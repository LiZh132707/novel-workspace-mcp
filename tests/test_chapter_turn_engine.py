import logging
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from core.chapter_manager import ChapterManager
from core.chapter_change_preview import ChapterChangePreview
from core.chapter_turn_engine import ChapterTurnEngine
from core.character_manager import CharacterManager
from core.novel_manager import NovelManager
from storage_utils import StorageManager


LOGGER = logging.getLogger("chapter-turn-engine-test")


def _engine(root: Path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("回合书", root, LOGGER, storage)
    manager = ChapterManager(novel, LOGGER)
    return novel, ChapterTurnEngine(novel, LOGGER, manager, storage)


def test_draft_is_isolated_until_turn_commit():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        novel, engine = _engine(root)
        content = "林舟沿着封闭站台寻找出口。" * 45
        turn = engine.save_draft(1, content, 500, "generated")
        assert turn["status"] == "ready"
        assert novel.get_current_chapter() == 0
        assert not (root / "chapters" / "000001.txt").exists()
        committed = engine.commit(turn["id"])
        assert committed["turn"]["status"] == "committed"
        assert novel.get_current_chapter() == 1
        assert (root / "chapters" / "000001.txt").read_text("utf-8") == content


def test_turn_blocks_chapter_gap_and_quality_failure():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        gap = engine.save_draft(2, "跨章草稿" * 200, 500)
        with pytest.raises(ValueError, match="不能跳到"):
            engine.commit(gap["id"], allow_quality_failure=True)
        short = engine.save_draft(1, "太短了", 500)
        assert short["status"] == "blocked"
        with pytest.raises(ValueError, match="质量检查未通过"):
            engine.commit(short["id"])


def test_turn_commit_survives_noncritical_index_failure_and_recovers_marker():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        content = "苏遥核对每一道门锁留下的痕迹。" * 45
        turn = engine.save_draft(1, content, 500)
        result = engine.commit(
            turn["id"], index_callback=lambda _chapter, _content: (_ for _ in ()).throw(RuntimeError("索引离线")),
        )
        assert result["turn"]["status"] == "committed"
        assert "索引更新失败" in result["turn"]["post_commit_warnings"][0]
        data = engine._load_index()
        data["items"][0].update({"status": "committing", "post_commit_pending": True})
        engine._save_index(data)
        recovered = engine.commit(turn["id"])
        assert recovered["recovered"] is True
        assert recovered["turn"]["status"] == "committed"
        assert recovered["turn"]["post_commit_pending"] is False


def test_recent_committing_turn_rejects_duplicate_submit():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        turn = engine.save_draft(1, "正在提交的正文。" * 60, 500)
        data = engine._load_index()
        data["items"][0].update({"status": "committing", "commit_started_at": datetime.now().isoformat()})
        engine._save_index(data)
        with pytest.raises(RuntimeError, match="正在提交"):
            engine.commit(turn["id"], allow_quality_failure=True)


def test_turn_commit_waits_for_whole_commit_lock():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        turn = engine.save_draft(1, "提交锁保护正文。" * 80, 500)
        started = threading.Event()
        finished = threading.Event()
        error = []

        def commit():
            started.set()
            try:
                engine.commit(turn["id"])
            except Exception as exc:
                error.append(exc)
            finally:
                finished.set()

        from filelock import FileLock
        with FileLock(str(engine.commit_lock_path), timeout=30):
            worker = threading.Thread(target=commit)
            worker.start()
            assert started.wait(1)
            time.sleep(0.05)
            assert not finished.is_set()
        worker.join(timeout=5)
        assert finished.is_set()
        assert error == []
        assert engine.get(turn["id"])["status"] == "committed"


def test_turn_commit_waits_for_novel_mutation_lock():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        turn = engine.save_draft(1, "小说事务锁保护正文。" * 80, 500)
        finished = threading.Event()

        def commit():
            engine.commit(turn["id"])
            finished.set()

        from filelock import FileLock
        with FileLock(str(root / ".novel_mutation.lock"), timeout=30):
            worker = threading.Thread(target=commit)
            worker.start()
            time.sleep(0.05)
            assert not finished.is_set()
        worker.join(timeout=5)
        assert finished.is_set()


def test_independent_turn_does_not_destroy_existing_candidate_draft():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        first = engine.save_draft(1, "候选版本甲。" * 80, 500, "generated")
        second = engine.save_draft(1, "候选版本乙。" * 80, 500, "review", {}, False)
        assert first["id"] != second["id"]
        assert "候选版本甲" in engine.read_draft(first["id"])
        assert "候选版本乙" in engine.read_draft(second["id"])


def test_new_commit_supersedes_previous_canonical_turn_and_prevents_replay():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        first = engine.save_draft(1, "第一版正史内容。" * 80, 500, "generated", {}, False)
        engine.commit(first["id"])
        second = engine.save_draft(1, "第二版正史内容已经改写。" * 80, 500, "review", {}, False)
        engine.commit(second["id"])
        assert engine.get(first["id"])["status"] == "superseded"
        assert engine.get(second["id"])["status"] == "committed"
        with pytest.raises(ValueError, match="更新版本取代"):
            engine.commit(first["id"], allow_quality_failure=True, allow_fact_conflicts=True)


def test_planning_stale_draft_requires_confirmation_and_editing_clears_marker():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        first = engine.save_draft(1, "旧规划生成的章节草稿。" * 80, 500, "generated")
        data = engine._load_index()
        data["items"][0]["planning_stale"] = True
        data["items"][0]["planning_impact_id"] = "impact1"
        engine._save_index(data)
        with pytest.raises(ValueError, match="上游规划已经变化"):
            engine.commit(first["id"])
        committed = engine.commit(first["id"], allow_stale_planning=True)
        assert committed["turn"]["status"] == "committed"

        second = engine.save_draft(1, "另一个旧规划草稿。" * 80, 500, "generated", {}, False)
        data = engine._load_index()
        stored = next(item for item in data["items"] if item["id"] == second["id"])
        stored["planning_stale"] = True
        engine._save_index(data)
        engine.save_draft(1, "作者根据新规划修改后的草稿。" * 80, 500, "manual")
        assert engine.get(second["id"]).get("planning_stale") is None


def test_same_content_regenerated_against_current_plan_clears_stale_marker():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        content = "固定种子生成的正文内容。" * 60
        first = engine.save_draft(1, content, 500, "batch", {
            "planning_stale": True, "planning_fingerprint": "old",
        })
        assert engine.get(first["id"])["planning_stale"] is True
        refreshed = engine.save_draft(1, content, 500, "batch", {
            "planning_stale": False, "planning_fingerprint": "current",
        })
        assert refreshed["id"] == first["id"]
        assert engine.get(first["id"]).get("planning_stale") is None


def test_draft_detects_planning_epoch_change_during_generation():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        engine.storage.atomic_write_json(root / "planning" / "epoch.json", {"id": "newplan"})
        turn = engine.save_draft(
            1, "模型根据旧规划生成的长篇草稿。" * 80, 500, "generated",
            {"planning_epoch": "oldplan"}, False,
        )
        stored = engine.get(turn["id"])
        assert stored["planning_stale"] is True
        assert stored["planning_impact_id"] == "newplan"
        with pytest.raises(ValueError, match="上游规划已经变化"):
            engine.commit(turn["id"])


def test_draft_accepts_explicit_runtime_stale_marker():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        turn = engine.save_draft(
            1, "模型生成期间用户修改了场景细纲。" * 80, 500, "generated",
            {"planning_stale": True, "planning_fingerprint": "old-fingerprint"}, False,
        )
        stored = engine.get(turn["id"])
        assert stored["planning_stale"] is True
        assert stored["planning_impact_id"] == "old-fingerprint"
        with pytest.raises(ValueError, match="上游规划已经变化"):
            engine.commit(turn["id"])


def test_change_preview_does_not_touch_canonical_state_and_is_reused_on_commit():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        novel, engine = _engine(root)
        content = "林舟在封锁线前确认了新的行动目标。" * 50
        turn = engine.save_draft(1, content, 500)
        preview = engine.preview_changes(turn["id"])
        assert preview["operation"] == "create"
        assert not (root / "summaries" / "000001.json").exists()
        assert novel.get_current_chapter() == 0
        engine.commit(turn["id"])
        assert (root / "summaries" / "000001.json").exists()
        assert novel.get_current_chapter() == 1


def test_editing_draft_invalidates_stale_change_preview():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        turn = engine.save_draft(1, "第一版草稿。" * 80, 500)
        engine.preview_changes(turn["id"])
        assert engine.get(turn["id"]).get("preview_summary")
        engine.save_draft(1, "第二版已经改变。" * 80, 500)
        assert "preview_summary" not in engine.get(turn["id"])


def test_change_preview_describes_state_foreshadow_and_knowledge_differences():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        turn = engine.save_draft(1, "林舟确认旧伤恶化，并终于理解钥匙的用途。" * 50, 500)
        summary = engine.chapter_manager.summary_mgr._basic_summary(1, engine.read_draft(turn["id"]))
        summary.update({
            "characters_changed": [{
                "name": "林舟", "field": "current_status", "new_value": "重伤失踪", "evidence": "旧伤恶化后失联",
            }],
            "foreshadowing": [{"action": "resolve", "text": "不存在的钥匙伏笔"}],
            "knowledge_changes": [{"name": "林舟", "fact": "钥匙用途", "status": "known", "source": "亲自验证"}],
        })
        engine.chapter_manager.summary_mgr.llm = None
        original = engine.chapter_manager.summary_mgr._basic_summary
        engine.chapter_manager.summary_mgr._basic_summary = lambda _chapter, _content: summary
        try:
            diff = engine.preview_changes(turn["id"])["state_diff"]
        finally:
            engine.chapter_manager.summary_mgr._basic_summary = original
        assert diff["totals"]["high_risk"] == 2
        assert diff["state_changes"][0]["before"] == ""
        assert diff["state_changes"][0]["after"] == "重伤失踪"
        assert diff["foreshadow_changes"][0]["risk"] == "high"
        assert diff["knowledge_changes"][0]["after"] == "known"


def test_change_preview_only_marks_destructive_status_fields_as_high_risk():
    assert ChapterChangePreview._risk(
        "location", "description", "", "死亡纪念馆旁的旧车站", "正文证据",
    ) != "high"
    assert ChapterChangePreview._risk(
        "character", "current_status", "存活", "死亡", "正文证据",
    ) == "high"
    assert ChapterChangePreview._risk(
        "item", "status", "由林舟持有", "已经遗失", "正文证据",
    ) == "high"


def test_high_risk_state_change_requires_explicit_commit_confirmation():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        content = "林舟在爆炸后确认密钥已经彻底摧毁。" * 50
        turn = engine.save_draft(1, content, 500)
        summary = engine.chapter_manager.summary_mgr._basic_summary(1, content)
        summary["items"] = [{
            "name": "核心密钥", "status": "彻底摧毁",
            "evidence": "密钥已经彻底摧毁", "evidence_verified": True,
        }]
        original = engine.chapter_manager.summary_mgr._basic_summary
        engine.chapter_manager.summary_mgr.llm = None
        engine.chapter_manager.summary_mgr._basic_summary = lambda _chapter, _content: summary
        try:
            preview = engine.preview_changes(turn["id"])
            inspection = engine.inspect(turn["id"])
            assert preview["state_change_conflicts"]
            assert inspection["requires_fact_confirmation"] is True
            assert any("高风险状态变化待确认" in item["message"] for item in inspection["issues"])
            with pytest.raises(ValueError, match="高风险状态"):
                engine.commit(turn["id"])
            committed = engine.commit(turn["id"], allow_fact_conflicts=True)
        finally:
            engine.chapter_manager.summary_mgr._basic_summary = original
        assert committed["turn"]["status"] == "committed"
        assert committed["turn"]["commit_approvals"]["fact_conflicts"] is True


def test_commit_requires_preview_and_explicit_hard_fact_confirmation():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        engine.storage.atomic_write_json(root / "facts.json", {
            "facts": [{"subject": "林舟", "predicate": "身份", "object": "调查员", "chapter": 2}],
            "conflicts": [],
        })
        turn = engine.save_draft(1, "林舟公开承认自己其实是医生。" * 50, 500)
        summary = engine.chapter_manager.summary_mgr._basic_summary(1, engine.read_draft(turn["id"]))
        summary["facts"] = [{"subject": "林舟", "predicate": "身份", "object": "医生"}]
        original = engine.chapter_manager.summary_mgr._basic_summary
        engine.chapter_manager.summary_mgr.llm = None
        engine.chapter_manager.summary_mgr._basic_summary = lambda _chapter, _content: summary
        try:
            with pytest.raises(ValueError, match="明确确认事实改写"):
                engine.commit(turn["id"])
            assert engine.get(turn["id"]).get("preview_summary")
            committed = engine.commit(turn["id"], allow_fact_conflicts=True)
        finally:
            engine.chapter_manager.summary_mgr._basic_summary = original
        assert committed["turn"]["status"] == "committed"


def test_commit_runs_shared_character_and_timeline_post_processing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        characters = CharacterManager(root, LOGGER)
        characters.create_character("林舟")
        content = "林舟进入档案馆，确认封锁线已经失效。" * 45
        turn = engine.save_draft(1, content, 500)
        summary = engine.chapter_manager.summary_mgr._basic_summary(1, content)
        summary.update({
            "summary": "林舟进入档案馆并确认封锁线失效。",
            "handoff": {"final_scene": {
                "location": "档案馆", "story_time": "深夜",
                "active_characters": ["林舟"], "last_action": "确认封锁线失效",
            }},
        })
        data = engine._load_index()
        data["items"][0]["preview_summary"] = summary
        engine._save_index(data)
        committed = engine.commit(turn["id"])
        assert characters.get_character("林舟")["last_chapter"] == 1
        assert committed["turn"]["post_commit"]["timeline_event"]["location"] == "档案馆"
        assert (root / "characters" / ".evolution" / "林舟.json").exists()


def test_extension_hook_failure_is_non_fatal():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("回合书", root, LOGGER, storage)
        manager = ChapterManager(novel, LOGGER)
        engine = ChapterTurnEngine(
            novel, LOGGER, manager, storage,
            [lambda *_args: (_ for _ in ()).throw(RuntimeError("插件离线"))],
        )
        turn = engine.save_draft(1, "林舟继续向前。" * 80, 500)
        committed = engine.commit(turn["id"])
        assert committed["turn"]["status"] == "committed"
        assert any("扩展钩子失败" in item for item in committed["turn"]["post_commit_warnings"])


def test_extension_hook_runs_after_novel_transaction_lock_is_released():
    from filelock import FileLock

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("回合书", root, LOGGER, storage)
        manager = ChapterManager(novel, LOGGER)
        hook_acquired_lock = []

        def hook(*_args):
            with FileLock(str(root / ".novel_mutation.lock"), timeout=0.2):
                hook_acquired_lock.append(True)

        engine = ChapterTurnEngine(novel, LOGGER, manager, storage, [hook])
        turn = engine.save_draft(1, "插件在事务之后运行。" * 70, 500)
        engine.commit(turn["id"])
        assert hook_acquired_lock == [True]


def test_builtin_post_processing_failure_stays_pending_and_can_retry(monkeypatch):
    from core.chapter_post_commit import ChapterPostCommitProcessor
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        original = ChapterPostCommitProcessor.run
        monkeypatch.setattr(
            ChapterPostCommitProcessor, "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("时间线磁盘暂不可写")),
        )
        turn = engine.save_draft(1, "林舟检查每一道门锁。" * 60, 500)
        failed = engine.commit(turn["id"])
        assert failed["turn"]["status"] == "committed"
        assert failed["turn"]["post_commit_pending"] is True
        monkeypatch.setattr(ChapterPostCommitProcessor, "run", original)
        recovered = engine.commit(turn["id"])
        assert recovered["recovered"] is True
        assert recovered["turn"]["post_commit_pending"] is False


def test_turn_index_pruning_removes_dropped_draft_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        items = []
        for index in range(501):
            turn_id = f"turn{index}"
            items.append({"id": turn_id, "chapter": index + 1, "status": "discarded"})
            engine.storage.atomic_write_text(engine.draft_dir / f"{turn_id}.txt", "草稿")
        engine._save_index({"schema_version": 1, "items": items})
        assert len(engine._load_index()["items"]) == 500
        assert not (engine.draft_dir / "turn0.txt").exists()
        assert (engine.draft_dir / "turn500.txt").exists()


def test_turn_index_pruning_never_drops_active_or_recoverable_turns():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _novel, engine = _engine(root)
        protected = [
            {"id": "activeReady", "chapter": 1, "status": "ready"},
            {
                "id": "pendingCommit",
                "chapter": 2,
                "status": "committed",
                "post_commit_pending": True,
            },
        ]
        terminal = [
            {"id": f"done{index}", "chapter": index + 3, "status": "discarded"}
            for index in range(501)
        ]
        for item in protected + terminal:
            engine.storage.atomic_write_text(
                engine.draft_dir / f"{item['id']}.txt", "不可误删的草稿"
            )

        engine._save_index({"schema_version": 1, "items": protected + terminal})

        saved = engine._load_index()["items"]
        assert {item["id"] for item in saved} >= {"activeReady", "pendingCommit"}
        assert len(saved) == 502
        assert (engine.draft_dir / "activeReady.txt").exists()
        assert (engine.draft_dir / "pendingCommit.txt").exists()
        assert not (engine.draft_dir / "done0.txt").exists()
