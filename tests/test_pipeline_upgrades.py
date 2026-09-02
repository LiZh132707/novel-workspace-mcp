import logging
import pytest
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.author_preference_manager import AuthorPreferenceManager
from core.ai_contracts import chapter_source_hash
from core.canonical_state_manager import CanonicalStateManager
from core.import_rebuilder import ImportRebuilder
from core.history_revision_manager import HistoryRevisionManager
from core.novel_manager import NovelManager
from core.derived_state_rebuilder import DerivedStateRebuilder
from core.entity_ledger import EntityLedger
from core.chapter_commit_manager import ChapterCommitManager
from core.chapter_manager import ChapterManager
from core.change_review_manager import ChangeReviewManager
from core.summary_manager import SummaryManager
from core.state_card_manager import StateCardManager
from core.creative_assets import CreativeAssetManager
from core.foreshadow_manager import ForeshadowManager
from core.quality_tracker import QualityTracker
from core.performance_manager import PerformanceManager
from core.savepoint_manager import SavepointManager
from core.task_runner import PersistentTaskRunner
from core.task_store import TaskStore
from core.timeline_manager import TimelineManager
from core.prompt_snapshot_manager import PromptSnapshotManager
from core.mutation_transaction import NovelMutationTransaction
from core.planning_version_manager import PlanningVersionManager
from storage_utils import StorageManager
from vector_store import VectorStore


LOGGER = logging.getLogger("pipeline-upgrades-test")


def test_vector_split_indexes_the_end_of_long_chapter():
    text = "\n\n".join(f"第{index}段" + "甲" * 500 for index in range(10)) + "\n\n结尾关键证据"
    chunks = VectorStore.split_text(text, chunk_size=900, overlap=100)
    assert len(chunks) > 4
    assert "结尾关键证据" in chunks[-1]["text"]
    assert chunks[-1]["end"] >= len(text) - 20
    assert "关键" in VectorStore._query_terms("结尾关键证据")


def test_local_retrieval_works_without_embedding_endpoint():
    with tempfile.TemporaryDirectory() as tmp:
        store = VectorStore.__new__(VectorStore)
        store.logger = LOGGER
        store._lock = threading.Lock()
        store.lexical_path = Path(tmp) / "lexical.db"
        store._semantic_disabled = True
        store._initialize_lexical()
        store.add_document("测试书", 7, "林舟把黑色钥匙藏进旧车站的储物柜。\n\n苏遥并不知道钥匙的位置。")
        hits = store.search("黑色钥匙 储物柜", "测试书", 5)
        assert hits and hits[0]["chapter"] == 7
        assert "本地全文命中" in hits[0]["reason"]
        store.delete_document("测试书", 7)
        assert store.search("黑色钥匙 储物柜", "测试书", 5) == []


def test_embedding_failure_removes_stale_semantic_entry_and_keeps_new_lexical_text():
    class Collection:
        def __init__(self):
            self.deleted = []
        def delete(self, where):
            self.deleted.append(where)
        def add(self, **_kwargs):
            raise AssertionError("嵌入失败后不应写入语义索引")
    with tempfile.TemporaryDirectory() as tmp:
        store = VectorStore.__new__(VectorStore)
        store.logger = LOGGER
        store._lock = threading.Lock()
        store.lexical_path = Path(tmp) / "lexical.db"
        store._semantic_disabled = False
        store._collection = Collection()
        store.embed_func = lambda _text: (_ for _ in ()).throw(RuntimeError("模拟嵌入失败"))
        store._initialize_lexical()
        store.add_document("测试书", 3, "新的车站线索")
        assert store._collection.deleted
        assert store.search("车站线索", "测试书", 5)[0]["chapter"] == 3


def test_empty_document_replacement_deletes_old_local_index():
    with tempfile.TemporaryDirectory() as tmp:
        store = VectorStore.__new__(VectorStore)
        store.logger = LOGGER
        store._lock = threading.Lock()
        store.lexical_path = Path(tmp) / "lexical.db"
        store._semantic_disabled = True
        store._initialize_lexical()
        store.add_document("测试书", 1, "旧剧情仍在这里")
        store.add_document("测试书", 1, "   ")
        assert store.search("旧剧情", "测试书", 5) == []


def test_bootstrap_prunes_indexes_for_missing_chapters(monkeypatch):
    class Collection:
        def __init__(self):
            self.deleted = []
        def delete(self, where):
            self.deleted.append(where)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        novels = root / "novels"
        (novels / "测试书" / "chapters").mkdir(parents=True)
        monkeypatch.setattr("vector_store.NOVELS_ROOT", novels)
        store = VectorStore.__new__(VectorStore)
        store.logger = LOGGER
        store._lock = threading.Lock()
        store.lexical_path = root / "lexical.db"
        store._collection = Collection()
        store._initialize_lexical()
        store._replace_lexical("测试书", 9, store.split_text("已经删除的第九章"))
        store._bootstrap_lexical()
        connection = store._connect_lexical()
        try:
            assert connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
        finally:
            connection.close()
        assert store._collection.deleted


def test_canonical_state_auto_commits_evidenced_low_risk_and_holds_conflict():
    with tempfile.TemporaryDirectory() as tmp:
        manager = CanonicalStateManager(Path(tmp), LOGGER)
        first = manager.propose_from_summary(1, {"items": [{
            "name": "钥匙", "owner": "林舟", "evidence": "林舟收起钥匙", "evidence_verified": True,
        }]})
        assert first["committed"] == 1
        assert manager.cards.get()["item"]["钥匙"]["fields"]["owner"] == "林舟"
        second = manager.propose_from_summary(2, {"items": [{
            "name": "钥匙", "owner": "苏遥", "evidence": "苏遥拿到钥匙", "evidence_verified": True,
        }]})
        assert second["pending"] == 1
        pending = manager.list("pending")
        assert pending[0]["previous"] == "林舟"
        manager.decide(pending[0]["id"], True)
        assert manager.cards.get()["item"]["钥匙"]["fields"]["owner"] == "苏遥"


def test_unverified_evidence_and_internal_metadata_never_auto_commit_as_state():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        summary = {"items": [{
            "name": "钥匙", "owner": "林舟", "evidence": "模型声称存在的句子",
            "evidence_verified": False,
        }]}
        result = CanonicalStateManager(root, LOGGER, storage).propose_from_summary(1, summary)
        assert result["committed"] == 0 and result["pending"] == 1
        assert {item["field"] for item in result["items"]} == {"owner"}
        EntityLedger(root, LOGGER, storage).ingest(1, summary)
        assert EntityLedger(root, LOGGER, storage).get()["items"] == {}


def test_derived_rebuild_skips_wrong_summary_shapes_and_uses_filename_chapter_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        storage.atomic_write_json(root / "summaries" / "000001.json", ["损坏结构"])
        storage.atomic_write_json(root / "summaries" / "000002.json", {
            "chapter": "损坏章号", "summary": "第二章仍可按文件名恢复。",
        })
        result = DerivedStateRebuilder(root, LOGGER, storage).rebuild(2)
        assert result["replayed_chapters"] == 1


def test_canonical_state_recovers_from_wrong_json_shapes(tmp_path):
    storage = StorageManager(LOGGER)
    manager = CanonicalStateManager(tmp_path, LOGGER, storage)
    storage.atomic_write_json(manager.path, [])
    storage.atomic_write_json(manager.version_path, {"versions": "损坏"})
    assert manager.list() == []
    version = manager.create_version(1, "恢复损坏版本索引")
    assert version["version"] == 1
    result = manager.propose_from_summary(1, {
        "characters_changed": [{
            "name": "林舟", "field": "status", "new_value": "清醒",
            "evidence": "林舟睁开眼睛", "evidence_verified": True,
        }],
    })
    assert result["proposed"] == 1


def test_prompt_snapshot_baseline_and_diff():
    with tempfile.TemporaryDirectory() as tmp:
        manager = PromptSnapshotManager(Path(tmp), LOGGER)
        manager.record("planning", "系统", "第一版")
        manager.set_baseline("planning")
        assert manager.compare("planning")["status"] == "same"
        manager.record("planning", "系统", "第二版")
        assert manager.compare("planning")["status"] == "changed"
        reference = manager.latest_reference("planning")
        assert reference["prompt_hash"]
        assert "prompt" not in reference and "system" not in reference


def test_prompt_snapshot_sanitizes_windows_filename_characters():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manager = PromptSnapshotManager(root, LOGGER)
        item = manager.record('../章节:规划*?"<>|', "系统", "内容")
        assert item["task_type"] == "章节_规划"
        latest_files = list((root / "prompt_snapshots" / "latest").glob("*.json"))
        assert len(latest_files) == 1
        assert latest_files[0].name == "章节_规划.json"


def test_file_backed_version_ids_reject_windows_invalid_characters():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("版本安全书", root, LOGGER, storage)
        with pytest.raises(ValueError, match="存档点 ID"):
            SavepointManager(root, LOGGER, storage).restore(1, "bad:*")
        with pytest.raises(ValueError, match="规划版本ID"):
            PlanningVersionManager(root).diff("bad:*")
        with pytest.raises(ValueError, match="历史修改ID"):
            HistoryRevisionManager(novel, LOGGER, None, storage).get("bad:*")


def test_memory_review_waits_for_novel_transaction_lock():
    from filelock import FileLock

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("交接锁书", root, LOGGER, storage)
        summaries = SummaryManager(novel, LOGGER, None)
        summaries.save_custom_summary(1, summaries._basic_summary(1, "第一章正文"))
        finished = threading.Event()

        def review():
            summaries.review_memory(1, "confirmed", {})
            finished.set()

        with FileLock(str(root / ".novel_mutation.lock"), timeout=30):
            worker = threading.Thread(target=review)
            worker.start()
            time.sleep(0.05)
            assert not finished.is_set()
        worker.join(timeout=3)
        assert finished.is_set()


def test_character_evolution_recovers_from_damaged_snapshot_shape():
    from core.character_evolution import CharacterEvolutionTracker
    from core.character_manager import CharacterManager

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        CharacterManager(root, LOGGER).create_character("林舟")
        storage = StorageManager(LOGGER)
        storage.atomic_write_json(root / "characters" / ".evolution" / "林舟.json", {
            "name": "林舟", "snapshots": ["损坏", {"chapter": 1}],
        })
        tracker = CharacterEvolutionTracker(root, LOGGER, storage)
        tracker.scan_chapter(2, "林舟重伤倒下。")
        evolution = tracker.get_evolution("林舟")
        assert [item["chapter"] for item in evolution["snapshots"]] == [1, 2]


def test_author_preference_learning_is_abstract_only():
    with tempfile.TemporaryDirectory() as tmp:
        manager = AuthorPreferenceManager(Path(tmp), LOGGER)
        data = manager.learn(3, "他说了一大段很长很长的话。", "他说：\n\n“走。”")
        assert data["profile"]["sample_count"] == 1
        context = manager.context()
        assert "对白字符占比" in context
        assert "他说" not in context


def test_planning_roles_map_to_character_context_priority():
    from core.character_manager import CharacterManager
    assert CharacterManager.role_tier_from_planning_role("主角/第一视角") == "主角"
    assert CharacterManager.role_tier_from_planning_role("主要对手") == "重要配角"
    assert CharacterManager.role_tier_from_planning_role("次要配角") == "次要角色"
    assert CharacterManager.role_tier_from_planning_role("NPC") == "NPC"


def test_import_rebuilder_without_llm_creates_resumable_structure():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("导入书", root, LOGGER, storage)
        chapters = root / "chapters"
        chapters.mkdir(parents=True)
        (chapters / "000001.txt").write_text("林舟进入车站，发现出口关闭。", "utf-8")
        (chapters / "000002.txt").write_text("他沿着隧道寻找备用出口。", "utf-8")
        result = ImportRebuilder(novel, LOGGER, None, storage).rebuild(batch_size=1)
        assert result["chapters"] == 2
        assert (root / "outline" / "main.md").exists()
        assert (root / "summaries" / "000002.json").exists()
        assert novel.get_current_chapter() == 2
        manager = ChapterManager(novel, LOGGER)
        assert manager.commits.is_committed(2, (chapters / "000002.txt").read_text("utf-8"))
        assert TimelineManager(root, LOGGER).get_events_by_chapter(2)[0]["source"] == "chapter_summary"


def test_import_rebuilder_invalidates_batch_cache_when_chapter_changes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("导入缓存", root, LOGGER, storage)
        chapter = novel.path / "chapters" / "000001.txt"
        chapter.parent.mkdir(parents=True, exist_ok=True)
        chapter.write_text("旧正文事实", "utf-8")
        rebuilder = ImportRebuilder(novel, LOGGER, None, storage)
        rebuilder.rebuild(batch_size=1)
        cache = novel.path / "planning" / "import_batches" / "0001.json"
        old_fingerprint = storage.safe_read_json(cache, {})["fingerprint"]
        storage.atomic_write_json(cache, [])
        chapter.write_text("完全不同的新正文事实", "utf-8")
        rebuilder.rebuild(batch_size=1)
        cached = storage.safe_read_json(cache, {})
        assert cached["fingerprint"] != old_fingerprint
        assert cached["data"]["chapters"][0]["summary"].startswith("完全不同")


def test_import_rebuilder_rolls_back_all_derived_files_on_persist_failure(monkeypatch):
    from core.chapter_post_commit import ChapterPostCommitProcessor
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("导入事务", root, LOGGER, storage)
        chapter = root / "chapters" / "000001.txt"
        storage.atomic_write_text(chapter, "林舟进入车站，发现出口关闭。")
        storage.atomic_write_text(root / "bible" / "world.md", "原世界观")
        novel.save_state({"current_chapter": 0, "total_words": 0})
        monkeypatch.setattr(
            ChapterPostCommitProcessor, "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("模拟派生写入失败")),
        )
        with pytest.raises(RuntimeError, match="模拟派生写入失败"):
            ImportRebuilder(novel, LOGGER, None, storage).rebuild(batch_size=1)
        assert (root / "bible" / "world.md").read_text("utf-8") == "原世界观"
        assert not (root / "summaries" / "000001.json").exists()
        assert novel.get_current_chapter() == 0


class RevisionFakeLLM:
    def chat(self, _system, prompt, max_tokens=0, task_type=""):
        if task_type == "revision":
            content = prompt.split("<chapter>\n", 1)[1].split("\n</chapter>", 1)[0]
            revised = content.replace("顾临川死亡", "顾临川重伤失踪")
            return revised if revised != content else content + "\n\n远处的风向随之改变。"
        return '{"summary":"历史修改后的章节结果","characters_changed":[],"new_characters":[],"new_information":[],"foreshadowing":[],"facts":[],"narrative_promises":[],"causal_links":[],"knowledge_changes":[],"locations":[],"factions":[],"items":[],"relationship_changes":[],"handoff":{"final_scene":{"location":"","story_time":"","active_characters":[],"last_action":"局势改变"},"state_changes":[],"knowledge_changes":[],"commitments":[],"open_loops":[],"immediate_next_intent":"","evidence_quotes":[]},"plan_reconciliation":{"completed_goals":[],"unfinished_goals":[],"deviations":[],"new_constraints":[],"next_chapter_impacts":[],"evidence_quotes":[]},"next_goal":"承接修改后的历史"}'


def test_history_revision_isolated_branch_and_atomic_commit():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("回合书", root, LOGGER, storage)
        chapters = root / "chapters"
        chapters.mkdir(parents=True)
        texts = {
            1: "有人预言顾临川死亡，这让林舟开始准备后路。",
            2: "爆炸吞没大厅。众人确认顾临川死亡，随后撤离。",
            3: "顾临川死亡后，苏遥接管了他留下的钥匙。",
        }
        for chapter, text in texts.items():
            (chapters / f"{chapter:06d}.txt").write_text(text, "utf-8")
        storage.atomic_write_json(root / "turns" / "index.json", {
            "schema_version": 1,
            "items": [{"id": "oldturn2", "chapter": 2, "status": "committed"}],
        })
        storage.atomic_write_text(root / "turns" / "drafts" / "oldturn2.txt", texts[2])
        storage.atomic_write_json(root / "planning" / "patrols.json", {
            "items": [{"chapter": 1, "score": 90}, {"chapter": 2, "score": 30}],
        })
        novel.save_state({"current_chapter": 3})
        manager = HistoryRevisionManager(novel, LOGGER, RevisionFakeLLM(), storage)
        item = manager.create(2, "顾临川死亡", "顾临川重伤失踪")
        assert item["impact"]["backward_count"] == 1
        assert item["impact"]["forward_count"] == 1
        branch = manager.run_branch(item["id"])
        assert branch["status"] == "validated", branch["validation"]
        assert "顾临川死亡" in (chapters / "000002.txt").read_text("utf-8")
        committed = manager.commit(item["id"])
        assert committed["status"] == "committed"
        assert committed["post_commit_warnings"] == []
        assert "顾临川重伤失踪" in (chapters / "000002.txt").read_text("utf-8")
        assert (root / "history_revisions" / item["id"] / "transaction_backup" / "chapters" / "000002.txt").exists()
        assert TimelineManager(root, LOGGER).get_events_by_chapter(2)[0]["source"] == "chapter_summary"
        turns = storage.safe_read_json(root / "turns" / "index.json", {})["items"]
        assert turns[0]["status"] == "superseded"
        assert turns[0]["superseded_by"] == f"history_revision:{item['id']}"
        patrols = storage.safe_read_json(root / "planning" / "patrols.json", {})["items"]
        assert patrols == []


def test_history_revision_failed_commit_rolls_back_and_can_retry(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("回滚书", root, LOGGER, storage)
        chapters = root / "chapters"
        chapters.mkdir(parents=True)
        original = "爆炸吞没大厅，众人反复检查现场后确认顾临川死亡，随后带着钥匙离开并封锁入口。"
        (chapters / "000001.txt").write_text(original, "utf-8")
        novel.save_state({"current_chapter": 1, "total_words": len(original)})
        manager = HistoryRevisionManager(novel, LOGGER, RevisionFakeLLM(), storage)
        item = manager.create(1, "顾临川死亡", "顾临川重伤失踪")
        manager.run_branch(item["id"])
        original_generate = SummaryManager.generate_summary
        monkeypatch.setattr(SummaryManager, "generate_summary", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("模拟提交中断")))
        with pytest.raises(RuntimeError, match="模拟提交中断"):
            manager.commit(item["id"])
        assert (chapters / "000001.txt").read_text("utf-8") == original
        assert not (root / "summaries" / "000001.json").exists()
        assert manager.get(item["id"])["status"] == "commit_failed_rolled_back"
        monkeypatch.setattr(SummaryManager, "generate_summary", original_generate)
        assert manager.commit(item["id"])["status"] == "committed"


def test_history_revision_transaction_restores_character_and_timeline_directories():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("目录回滚", root, LOGGER, storage)
        from core.character_manager import CharacterManager
        characters = CharacterManager(root, LOGGER)
        characters.create_character("林舟")
        characters.update_character("林舟", current_status="死亡")
        timeline = TimelineManager(root, LOGGER)
        original_event = timeline.add_event(1, "午夜", "车站", "林舟倒下", ["林舟"])
        manager = HistoryRevisionManager(novel, LOGGER, RevisionFakeLLM(), storage)
        backup = root / "history_revisions" / "backup-test"
        manager._backup_transaction(backup, [])
        characters.update_character("林舟", current_status="存活")
        for path in (root / "timeline").glob("*.json"):
            path.unlink()
        timeline.add_event(2, "清晨", "医院", "林舟苏醒", ["林舟"])
        manager._restore_transaction(backup, [])
        assert characters.get_character("林舟")["current_status"] == "死亡"
        events = TimelineManager(root, LOGGER).get_recent_events(10)
        assert [item["id"] for item in events] == [original_event["id"]]


def test_history_revision_candidate_can_be_previewed_edited_and_revalidated():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("候选审阅", root, LOGGER, storage)
        storage.atomic_write_text(root / "chapters" / "000001.txt", "顾临川在爆炸中死亡。")
        novel.save_state({"current_chapter": 1})
        manager = HistoryRevisionManager(novel, LOGGER, None, storage)
        item = manager.create(1, "顾临川死亡", "顾临川重伤失踪")
        before = manager.preview_candidates(item["id"])
        assert before["items"][0]["changed"] is False
        updated = manager.update_candidate(item["id"], 1, "顾临川在爆炸中重伤失踪，其他人误以为他死亡。")
        assert updated["status"] == "validated"
        after = manager.preview_candidates(item["id"])
        assert after["items"][0]["changed"] is True
        assert "候选稿" in after["items"][0]["diff"]


def test_history_revision_invalidates_future_titles_and_opening_details():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("规划失效", root, LOGGER, storage)
        novel.save_state({"current_chapter": 3})
        storage.atomic_write_json(root / "outline" / "chapter_titles.json", {"2": "已发生", "4": "旧未来标题"})
        storage.atomic_write_json(root / "outline" / "chapter_briefs.json", {"4": {"title": "旧未来提要"}})
        storage.atomic_write_json(root / "outline" / "opening_chapters.json", {"chapters": [
            {"chapter": 2, "title": "已发生"}, {"chapter": 4, "title": "旧未来细纲"},
        ]})
        manager = HistoryRevisionManager(novel, LOGGER, None, storage)
        manager._invalidate_future_plans(1)
        assert storage.safe_read_json(root / "outline" / "chapter_titles.json", {}) == {"2": "已发生"}
        assert storage.safe_read_json(root / "outline" / "chapter_briefs.json", {}) == {}
        assert storage.safe_read_json(root / "outline" / "opening_chapters.json", {})["chapters"] == [{"chapter": 2, "title": "已发生"}]


def test_history_revision_plan_invalidation_tolerates_corrupt_shapes_and_chapter_numbers():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("损坏规划容错", root, LOGGER, storage)
        novel.save_state({"current_chapter": 3})
        storage.atomic_write_json(root / "outline" / "chapter_plans.json", ["损坏映射"])
        storage.atomic_write_json(root / "outline" / "opening_chapters.json", {"chapters": [
            {"chapter": "损坏", "title": "无法识别"}, {"chapter": 8, "title": "未来规划"},
        ]})
        manager = HistoryRevisionManager(novel, LOGGER, None, storage)
        manager._invalidate_future_plans(1)
        opening = storage.safe_read_json(root / "outline" / "opening_chapters.json", {})
        assert opening["chapters"] == [{"chapter": "损坏", "title": "无法识别"}]


def test_long_term_summary_uses_numeric_chapter_files_only():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        novel = NovelManager("摘要书", root, LOGGER, StorageManager(LOGGER))
        manager = SummaryManager(novel, LOGGER, None)
        for chapter in range(1, 11):
            manager.save_custom_summary(chapter, {"summary": f"第{chapter}章事件"})
        manager._update_long_term_memory()
        long_term = manager.storage.safe_read_json(root / "summaries" / "long_term.json", {})
        assert long_term["arcs"][0]["start_chapter"] == 1
        assert long_term["arcs"][0]["end_chapter"] == 10
        assert [item["chapter"] for item in manager.get_recent_summaries(3)] == [10, 9, 8]


def test_word_count_updates_are_atomic_under_threads():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        novel = NovelManager("并发书", root, LOGGER, StorageManager(LOGGER))
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _index: novel.add_words(1), range(80)))
        assert novel.get_state()["total_words"] == 80


def test_state_card_updates_do_not_lose_concurrent_writes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        def write_card(index):
            StateCardManager(root, LOGGER).upsert("item", f"物品{index}", index, {"owner": f"人物{index}"})
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write_card, range(24)))
        assert len(StateCardManager(root, LOGGER).get()["item"]) == 24


def test_auxiliary_ledgers_do_not_lose_concurrent_writes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        def write(index):
            CreativeAssetManager(root, LOGGER).save("questions", {"text": f"问题{index}"})
            ForeshadowManager(root, LOGGER).ingest(index + 1, [{"text": f"伏笔{index}"}])
            QualityTracker(root, LOGGER).add_debt(index + 1, "测试", "中", f"问题{index}")
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(24)))
        assert len(CreativeAssetManager(root, LOGGER).list("questions")) == 24
        assert len(ForeshadowManager(root, LOGGER).list()) == 24
        assert QualityTracker(root, LOGGER).get_report()["total_debts"] == 24


def test_performance_history_does_not_lose_concurrent_records():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "performance.json"
        def record(index):
            PerformanceManager(path, LOGGER).record({"tokens_per_second": index + 1}, "benchmark")
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(record, range(30)))
        history = PerformanceManager(path, LOGGER).get()["history"]
        assert len(history) == 30
        assert {item["tokens_per_second"] for item in history} == set(range(1, 31))


def test_savepoints_are_unique_and_indexed_under_concurrency():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        def create(index):
            return SavepointManager(root, LOGGER).create(1, f"版本正文{index}")
        with ThreadPoolExecutor(max_workers=8) as pool:
            records = list(pool.map(create, range(30)))
        assert len({item["id"] for item in records}) == 30
        assert len(SavepointManager(root, LOGGER).list_savepoints(1, 50)) == 30


def test_savepoint_index_rebuilds_from_surviving_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manager = SavepointManager(root, LOGGER)
        first = manager.create(1, "第一版正文", "第一版")
        index = root / ".savepoints" / "ch000001" / "_index.json"
        manager.storage.atomic_write_json(index, {"wrong": "shape"})
        recovered = manager.list_savepoints(1, 20)
        assert [item["id"] for item in recovered] == [first["id"]]
        second = manager.create(1, "第二版正文", "第二版")
        assert [item["id"] for item in manager.list_savepoints(1, 20)] == [second["id"], first["id"]]


def test_character_review_queue_does_not_lose_concurrent_new_characters():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        def propose(index):
            ChangeReviewManager(root, LOGGER).add_new_characters(index + 1, [{"name": f"人物{index}"}])
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(propose, range(20)))
        assert len(ChangeReviewManager(root, LOGGER).list()) == 20


def test_manual_state_override_replays_after_same_chapter_ai_state():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        summaries = root / "summaries"
        summaries.mkdir(parents=True)
        storage.atomic_write_json(summaries / "000001.json", {"chapter": 1, "summary": "获得钥匙", "items": [{"name": "钥匙", "owner": "林舟"}]})
        storage.atomic_write_json(summaries / "000002.json", {"chapter": 2, "summary": "钥匙转移", "items": [{"name": "钥匙", "owner": "苏遥"}]})
        from core.state_card_manager import StateCardManager
        cards = StateCardManager(root, LOGGER, storage)
        cards.upsert("item", "钥匙", 2, {"owner": "顾临川"}, "人工确认", "manual")
        DerivedStateRebuilder(root, LOGGER, storage).rebuild(2)
        assert cards.get()["item"]["钥匙"]["fields"]["owner"] == "顾临川"


def test_derived_rebuild_keeps_high_risk_state_pending_until_decided():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        summaries = root / "summaries"
        summaries.mkdir(parents=True)
        storage.atomic_write_json(summaries / "000001.json", {
            "chapter": 1, "summary": "顾临川倒下",
            "characters_changed": [{"name": "顾临川", "field": "current_status", "new_value": "死亡", "evidence": "顾临川停止呼吸"}],
        })
        DerivedStateRebuilder(root, LOGGER, storage).rebuild(1)
        canonical = CanonicalStateManager(root, LOGGER, storage)
        pending = canonical.list("pending")
        assert len(pending) == 1
        assert "顾临川" not in canonical.cards.get()["character"]
        canonical.decide(pending[0]["id"], True)
        DerivedStateRebuilder(root, LOGGER, storage).rebuild(1)
        assert canonical.cards.get()["character"]["顾临川"]["fields"]["current_status"] == "死亡"
        assert canonical.list("pending") == []


def test_derived_rebuild_preserves_character_review_decisions():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        from core.character_manager import CharacterManager
        CharacterManager(root, LOGGER).create_character("林舟")
        summaries = root / "summaries"
        summaries.mkdir(parents=True)
        storage.atomic_write_json(summaries / "000001.json", {
            "chapter": 1, "summary": "林舟受伤",
            "characters_changed": [{"name": "林舟", "field": "current_status", "new_value": "受伤", "evidence": "手臂流血"}],
        })
        reviews = ChangeReviewManager(root, LOGGER, storage)
        reviews.add_from_summary(1, storage.safe_read_json(summaries / "000001.json", {})["characters_changed"])
        reviews.decide(reviews.list()[0]["id"], False)
        DerivedStateRebuilder(root, LOGGER, storage).rebuild(1)
        assert reviews.list(None)[0]["status"] == "rejected"


def test_derived_rebuild_reverts_character_state_when_accepted_history_disappears():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        from core.character_manager import CharacterManager
        characters = CharacterManager(root, LOGGER)
        characters.create_character("林舟")
        summary_path = root / "summaries" / "000001.json"
        storage.atomic_write_json(summary_path, {
            "chapter": 1, "summary": "林舟死亡",
            "characters_changed": [{"name": "林舟", "field": "current_status", "new_value": "死亡", "evidence": "停止呼吸"}],
        })
        reviews = ChangeReviewManager(root, LOGGER, storage)
        reviews.add_from_summary(1, storage.safe_read_json(summary_path, {})["characters_changed"])
        reviews.decide(reviews.list()[0]["id"], True)
        assert characters.get_character("林舟")["current_status"] == "死亡"
        storage.atomic_write_json(summary_path, {"chapter": 1, "summary": "林舟幸存", "characters_changed": []})
        result = DerivedStateRebuilder(root, LOGGER, storage).rebuild(1)
        assert result["character_profiles_reconciled"] == 1
        assert characters.get_character("林舟")["current_status"] == "存活"


def test_derived_rebuild_does_not_overwrite_later_manual_character_override():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        from core.character_manager import CharacterManager
        characters = CharacterManager(root, LOGGER)
        characters.create_character("林舟")
        summary_path = root / "summaries" / "000001.json"
        storage.atomic_write_json(summary_path, {
            "chapter": 1, "summary": "林舟受伤",
            "characters_changed": [{"name": "林舟", "field": "current_status", "new_value": "受伤", "evidence": "手臂流血"}],
        })
        reviews = ChangeReviewManager(root, LOGGER, storage)
        reviews.add_from_summary(1, storage.safe_read_json(summary_path, {})["characters_changed"])
        reviews.decide(reviews.list()[0]["id"], True)
        characters.update_character("林舟", current_status="失踪")
        storage.atomic_write_json(summary_path, {"chapter": 1, "summary": "林舟离开", "characters_changed": []})
        DerivedStateRebuilder(root, LOGGER, storage).rebuild(1)
        assert characters.get_character("林舟")["current_status"] == "失踪"


def test_paused_task_is_not_overwritten_as_failed():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        runner = PersistentTaskRunner(store, LOGGER, poll_interval=0.01)
        task_id = store.create("测试", "pause_test", "暂停测试", status="queued")
        def handler(task):
            store.event(task["id"], "需要确认", 42)
            store.pause(task["id"])
            raise RuntimeError("等待确认")
        runner.register("pause_test", handler)
        runner.start()
        runner.notify()
        for _index in range(100):
            if store.get(task_id)["status"] == "paused":
                break
            time.sleep(0.01)
        runner.stop()
        task = store.get(task_id)
        assert task["status"] == "paused"
        assert task["progress"] == 42


def test_chapter_commit_marker_detects_partial_save():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        manager = ChapterCommitManager(root, LOGGER, storage)
        assert not manager.is_committed(1, "正文")
        summary = {"source_hash": chapter_source_hash("正文")}
        manager.mark(1, "正文", summary)
        assert not manager.is_committed(1, "正文")
        storage.atomic_write_json(root / "summaries" / "000001.json", summary)
        assert manager.is_committed(1, "正文")
        assert not manager.is_committed(1, "修改后的正文")


def test_uncommitted_chapter_retry_repairs_word_count_and_derived_state(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("恢复书", root, LOGGER, storage)
        manager = ChapterManager(novel, LOGGER)
        original_add_words = novel.add_words
        monkeypatch.setattr(novel, "add_words", lambda _count: (_ for _ in ()).throw(RuntimeError("模拟字数更新中断")))
        with pytest.raises(RuntimeError, match="模拟字数更新中断"):
            manager.save_chapter(1, "林舟进入车站并锁上身后的门。")
        assert (root / "chapters" / "000001.txt").exists()
        assert novel.get_state()["total_words"] == 0
        monkeypatch.setattr(novel, "add_words", original_add_words)
        result = manager.save_chapter(1, "林舟进入车站并锁上身后的门。")
        assert result["derived_rebuild"]["replayed_chapters"] == 1
        assert novel.get_state()["total_words"] == len("林舟进入车站并锁上身后的门。")
        assert manager.commits.is_committed(1, "林舟进入车站并锁上身后的门。")


def test_saving_identical_committed_chapter_is_model_free_and_idempotent(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        novel = NovelManager("幂等书", root, LOGGER, StorageManager(LOGGER))
        manager = ChapterManager(novel, LOGGER)
        content = "林舟进入车站并锁上身后的门。"
        calls = 0
        original_generate = manager.summary_mgr.generate_summary
        def counted_generate(chapter, text):
            nonlocal calls
            calls += 1
            return original_generate(chapter, text)
        monkeypatch.setattr(manager.summary_mgr, "generate_summary", counted_generate)
        manager.save_chapter(1, content)
        second = manager.save_chapter(1, content)
        assert calls == 1
        assert second["unchanged"] is True
        assert novel.get_state()["total_words"] == len(content)


def test_chapter_overwrite_aborts_when_preoverwrite_savepoint_fails(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        novel = NovelManager("备份保护书", root, LOGGER, StorageManager(LOGGER))
        manager = ChapterManager(novel, LOGGER)
        original = "不可丢失的原始正文。"
        manager.save_chapter(1, original)
        monkeypatch.setattr(
            manager.savepoints, "create",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("模拟存档磁盘故障")),
        )
        with pytest.raises(RuntimeError, match="模拟存档磁盘故障"):
            manager.save_chapter(1, "准备覆盖的新正文。")
        assert manager.read_chapter(1) == original
        assert manager.commits.is_committed(1, original)
        assert novel.get_state()["total_words"] == len(original)


def test_chapter_commit_ledger_recovers_from_wrong_json_shape():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        manager = ChapterCommitManager(root, LOGGER, storage)
        storage.atomic_write_json(root / "tracking" / "chapter_commits.json", [])
        assert manager.is_committed(1, "正文") is False
        summary = {"source_hash": chapter_source_hash("正文")}
        storage.atomic_write_json(root / "summaries" / "000001.json", summary)
        manager.mark(1, "正文", summary)
        assert manager.is_committed(1, "正文") is True


def test_selective_planning_transaction_rolls_back_partial_save():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        storage.atomic_write_text(root / "bible" / "world.md", "旧世界")
        storage.atomic_write_json(root / "outline" / "volumes.json", [{"title": "旧卷"}])
        storage.atomic_write_json(root / "state.json", {"next_goal": "旧目标", "current_chapter": 3})
        with pytest.raises(RuntimeError, match="模拟规划保存中断"):
            with NovelMutationTransaction(
                root, [], directories=("bible", "outline", "planning"), files=("state.json",),
            ):
                storage.atomic_write_text(root / "bible" / "world.md", "新世界")
                storage.atomic_write_json(root / "outline" / "volumes.json", [{"title": "新卷"}])
                storage.atomic_write_json(root / "planning" / "impacts.json", {"items": [1]})
                storage.atomic_write_json(root / "state.json", {"next_goal": "新目标", "current_chapter": 3})
                raise RuntimeError("模拟规划保存中断")
        assert (root / "bible" / "world.md").read_text("utf-8") == "旧世界"
        assert storage.safe_read_json(root / "outline" / "volumes.json", []) == [{"title": "旧卷"}]
        assert storage.safe_read_json(root / "state.json", {})["next_goal"] == "旧目标"
        assert not (root / "planning").exists()


def test_failed_transaction_restore_preserves_recovery_backup(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        storage.atomic_write_text(root / "bible" / "world.md", "唯一可恢复旧数据")
        transaction = NovelMutationTransaction(root, [], directories=("bible",), files=())

        with pytest.raises(RuntimeError, match="事务回滚失败"):
            with transaction:
                storage.atomic_write_text(root / "bible" / "world.md", "未完成的新数据")
                monkeypatch.setattr(
                    "core.mutation_transaction.shutil.copytree",
                    lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("模拟回滚磁盘故障")),
                )
                raise RuntimeError("触发事务回滚")

        assert transaction.backup.exists()
        assert (transaction.backup / "bible" / "world.md").read_text("utf-8") == "唯一可恢复旧数据"


def test_identical_chapter_rebuilds_when_committed_summary_is_missing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        novel = NovelManager("摘要恢复书", root, LOGGER, StorageManager(LOGGER))
        manager = ChapterManager(novel, LOGGER)
        content = "林舟在雨夜抵达车站。"
        manager.save_chapter(1, content)
        (root / "summaries" / "000001.json").unlink()
        assert manager.commits.is_committed(1, content) is False
        result = manager.save_chapter(1, content)
        assert result["derived_rebuild"]["replayed_chapters"] == 1
        assert manager.commits.is_committed(1, content) is True


def test_merge_latest_chapters_removes_ghost_memory_and_rebuilds_totals():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        novel = NovelManager("合并书", root, LOGGER, StorageManager(LOGGER))
        manager = ChapterManager(novel, LOGGER)
        first = "林舟取得钥匙。"
        second = "林舟用钥匙打开密室。"
        manager.save_chapter(1, first)
        manager.save_chapter(2, second)
        second_summary = manager.summary_mgr.get_summary(2)
        second_summary["facts"] = [{"subject": "密室", "predicate": "状态", "object": "已打开"}]
        manager.summary_mgr.save_custom_summary(2, second_summary)
        from core.timeline_manager import TimelineManager
        TimelineManager(root, LOGGER).add_event(2, "午夜", "密室", "机关开启", ["林舟"])
        from core.character_manager import CharacterManager
        CharacterManager(root, LOGGER).create_character("林舟")
        CharacterManager(root, LOGGER).update_character("林舟", last_chapter=2, location="密室")
        manager.storage.atomic_write_json(root / "outline" / "chapter_titles.json", {"1": "钥匙", "2": "密室"})
        manager.storage.atomic_write_json(root / "outline" / "chapter_briefs.json", {"2": {"synopsis": "打开密室"}})
        result = manager.merge_latest_chapters(1)
        assert result["merged_chapter"] == 2
        assert not (root / "chapters" / "000002.txt").exists()
        assert not (root / "summaries" / "000002.json").exists()
        assert manager.commits.get(2) == {}
        assert novel.get_current_chapter() == 1
        assert novel.get_state()["total_words"] == len(first + second)
        assert manager.fact_mgr.recent()[-1]["object"] == "已打开"
        assert TimelineManager(root, LOGGER).get_events_by_chapter(2) == []
        assert TimelineManager(root, LOGGER).get_events_by_chapter(1)[0]["event"] == "机关开启"
        detail = CharacterManager(root, LOGGER).get_character("林舟")
        assert detail["last_chapter"] == 1 and detail["locations"][-1]["chapter"] == 1
        assert "2" not in manager.storage.safe_read_json(root / "outline" / "chapter_titles.json", {})
        assert "2" not in manager.storage.safe_read_json(root / "outline" / "chapter_briefs.json", {})


def test_batch_replace_updates_structured_memory_commits_and_totals():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        novel = NovelManager("替换书", root, LOGGER, StorageManager(LOGGER))
        manager = ChapterManager(novel, LOGGER)
        manager.save_chapter(1, "林舟取得钥匙。")
        summary = manager.summary_mgr.get_summary(1)
        summary["facts"] = [{"subject": "林舟", "predicate": "持有", "object": "钥匙"}]
        manager.summary_mgr.save_custom_summary(1, summary)
        result = manager.batch_replace("林舟", "林川", [1])
        content = manager.read_chapter(1)
        assert result["changed_chapters"] == [1]
        assert "林川" in content and "林舟" not in content
        assert manager.fact_mgr.recent()[-1]["subject"] == "林川"
        assert manager.commits.is_committed(1, content)
        assert novel.get_state()["total_words"] == len(content)


def test_batch_replace_cannot_empty_a_chapter_and_rolls_back():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        novel = NovelManager("空章保护书", root, LOGGER, StorageManager(LOGGER))
        manager = ChapterManager(novel, LOGGER)
        manager.save_chapter(1, "唯一正文")
        before = novel.get_state()
        with pytest.raises(ValueError, match="清空第1章"):
            manager.batch_replace("唯一正文", "", [1])
        assert manager.read_chapter(1) == "唯一正文"
        assert novel.get_state() == before


def test_split_latest_chapter_preserves_structured_memory_by_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        novel = NovelManager("拆分书", root, LOGGER, StorageManager(LOGGER))
        manager = ChapterManager(novel, LOGGER)
        first = "林舟在大厅取得钥匙。" * 12
        second = "苏遥在密室打开机关。" * 12
        manager.save_chapter(1, first + second)
        summary = manager.summary_mgr.get_summary(1)
        summary["characters_changed"] = [{"name": "林舟", "field": "location", "new_value": "大厅", "evidence": "林舟在大厅取得钥匙"}]
        summary["facts"] = [{"subject": "机关", "predicate": "状态", "object": "已打开"}]
        manager.summary_mgr.save_custom_summary(1, summary)
        from core.timeline_manager import TimelineManager
        TimelineManager(root, LOGGER).add_event(1, "深夜", "密室", "苏遥在密室打开机关", ["苏遥"])
        manager.split_latest_chapter(1, len(first))
        assert manager.summary_mgr.get_summary(1)["characters_changed"][0]["name"] == "林舟"
        assert manager.summary_mgr.get_summary(2)["facts"][0]["subject"] == "机关"
        assert manager.commits.is_committed(1, manager.read_chapter(1))
        assert manager.commits.is_committed(2, manager.read_chapter(2))
        assert TimelineManager(root, LOGGER).get_events_by_chapter(1) == []
        assert TimelineManager(root, LOGGER).get_events_by_chapter(2)[0]["location"] == "密室"


def test_split_chapter_replays_accepted_state_decision_at_new_chapter_number():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("裁决迁移书", root, LOGGER, storage)
        manager = ChapterManager(novel, LOGGER)
        first = "大厅中的调查仍在继续。" * 12
        second = "顾临川停止呼吸，众人确认他已经死亡。" * 12
        manager.save_chapter(1, first + second)
        summary = manager.summary_mgr.get_summary(1)
        summary["characters_changed"] = [{
            "name": "顾临川", "field": "current_status", "new_value": "死亡",
            "evidence": "顾临川停止呼吸",
        }]
        manager.summary_mgr.save_custom_summary(1, summary)
        canonical = CanonicalStateManager(root, LOGGER, storage)
        proposal = canonical.propose_from_summary(1, summary)["items"][0]
        canonical.decide(proposal["id"], True)
        manager.split_latest_chapter(1, len(first))
        assert canonical.list("pending") == []
        committed = canonical.list("committed")
        assert committed and committed[0]["chapter"] == 2


def test_merge_failure_rolls_back_all_structural_files(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        novel = NovelManager("事务回滚书", root, LOGGER, StorageManager(LOGGER))
        manager = ChapterManager(novel, LOGGER)
        first = "第一章保持原样。"
        second = "第二章也必须恢复。"
        manager.save_chapter(1, first)
        manager.save_chapter(2, second)
        before_state = novel.get_state()
        monkeypatch.setattr("core.chapter_manager.DerivedStateRebuilder.rebuild", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("模拟结构重建失败")))
        with pytest.raises(RuntimeError, match="模拟结构重建失败"):
            manager.merge_latest_chapters(1)
        assert manager.read_chapter(1) == first
        assert manager.read_chapter(2) == second
        assert manager.summary_mgr.get_summary(2) is not None
        assert novel.get_state() == before_state


def test_historical_edit_invalidates_future_scene_plan_cache():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("改史书", root, LOGGER, storage)
        manager = ChapterManager(novel, LOGGER)
        manager.save_chapter(1, "第一章原文。")
        manager.save_chapter(2, "第二章原文。")
        storage.atomic_write_json(root / "outline" / "chapter_plans.json", {"2": {"plan": {}}, "3": {"plan": {}}})
        storage.atomic_write_json(root / "outline" / "scene_outlines.json", {"2": {"scenes": []}, "3": {"scenes": []}})
        manager.save_chapter(1, "第一章修改后的正文。")
        assert storage.safe_read_json(root / "outline" / "chapter_plans.json", {}) == {}
        assert storage.safe_read_json(root / "outline" / "scene_outlines.json", {}) == {}
