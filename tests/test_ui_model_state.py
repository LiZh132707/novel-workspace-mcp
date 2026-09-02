import asyncio
import json
import logging
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ui import app as web
from core.novel_manager import NovelManager
from core.scene_outline_manager import SceneOutlineManager
from storage_utils import StorageManager
from core.task_store import TaskStore
from core.character_manager import CharacterManager
from core.production_control_manager import ProductionControlManager


class FakeClient:
    def __init__(self, fail=False, busy=False):
        self.fail = fail
        self.generation_busy = busy
        self.calls = 0

    def unload_all(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("卸载失败")

    def reload(self):
        if self.fail:
            raise RuntimeError("重载失败")
        return True


def test_blocking_model_stream_does_not_block_event_loop():
    worker_threads = []

    def blocking_stream():
        for value in ("甲", "乙", "丙"):
            worker_threads.append(threading.get_ident())
            time.sleep(0.02)
            yield value

    async def run():
        ticks = 0
        finished = False

        async def ticker():
            nonlocal ticks
            while not finished:
                ticks += 1
                await asyncio.sleep(0.005)

        async def consume():
            nonlocal finished
            values = []
            async for value in web._iterate_blocking_stream(blocking_stream()):
                values.append(value)
            finished = True
            return values

        ticker_task = asyncio.create_task(ticker())
        values = await consume()
        await ticker_task
        return values, ticks

    values, ticks = asyncio.run(run())
    assert values == ["甲", "乙", "丙"]
    assert ticks >= 3
    assert all(thread_id != threading.get_ident() for thread_id in worker_threads)


def test_model_unload_clears_cached_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(web, "_llm", client)
    response = asyncio.run(web.api_model_unload())
    assert response.status_code == 200
    assert client.calls == 1
    assert web._llm is None


def test_model_unload_failure_keeps_client_for_retry(monkeypatch):
    client = FakeClient(fail=True)
    monkeypatch.setattr(web, "_llm", client)
    response = asyncio.run(web.api_model_unload())
    assert response.status_code == 409
    assert web._llm is client


def test_model_unload_rejects_busy_generation(monkeypatch):
    client = FakeClient(busy=True)
    monkeypatch.setattr(web, "_llm", client)
    response = asyncio.run(web.api_model_unload())
    assert response.status_code == 409
    assert client.calls == 0
    assert web._llm is client


def test_model_reload_failure_clears_stale_client(monkeypatch):
    client = FakeClient(fail=True)
    monkeypatch.setattr(web, "_llm", client)
    response = asyncio.run(web.api_model_reload())
    assert response.status_code == 500
    assert web._llm is None


def test_resume_rejects_until_running_worker_reaches_pause_checkpoint(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "tasks.db")
    task_id = store.create("测试", "batch_chapters", "暂停竞态", status="running")
    assert store.pause(task_id) is True

    class ActiveRunner:
        @staticmethod
        def is_executing(candidate):
            return candidate == task_id

    monkeypatch.setattr(web, "task_store", store)
    monkeypatch.setattr(web, "task_runner", ActiveRunner())
    response = asyncio.run(web.api_resume_task(task_id))
    payload = json.loads(response.body)
    assert response.status_code == 409
    assert "安全暂停点" in payload["error"]
    assert store.get(task_id)["status"] == "paused"


def test_cancel_api_does_not_report_success_for_completed_task(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "tasks.db")
    task_id = store.create("测试", "batch_chapters", "已完成任务", status="running")
    store.finish(task_id, {"ok": True})
    monkeypatch.setattr(web, "task_store", store)
    response = asyncio.run(web.api_cancel_task(task_id))
    assert response.status_code == 409
    assert store.get(task_id)["status"] == "completed"


def test_repair_task_changes_plan_fingerprint_without_overwriting_confirmed_scene_outline():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        logger = logging.getLogger("ui-plan-cache-test")
        storage = StorageManager(logger)
        novel = NovelManager("缓存书", root, logger, storage)
        scenes = SceneOutlineManager(root, logger, storage)
        scenes.save(2, {
            "status": "confirmed", "scenes": [{"title": "人工确认场景", "target_words": 900}],
        })
        before = web._chapter_plan_fingerprint(novel, 2, {"chapter": 2}, 5000, False)
        storage.atomic_write_json(root / "reviews" / "planning_reviews.json", {
            "volume_reviews": [{"repair_tasks": [{"id": "repair", "status": "pending"}]}],
        })
        after = web._chapter_plan_fingerprint(novel, 2, {"chapter": 2}, 5000, False)
        assert before != after
        web._save_cached_chapter_plan(novel, 2, after, {
            "scenes": [{"title": "AI重算场景", "target_words": 1200}],
        })
        assert scenes.get(2)["scenes"][0]["title"] == "人工确认场景"
        assert scenes.get(2)["status"] == "confirmed"


def test_plan_fingerprint_tracks_all_context_ledgers(tmp_path):
    logger = logging.getLogger("plan-fingerprint-ledgers-test")
    storage = StorageManager(logger)
    novel = NovelManager("指纹书", tmp_path, logger, storage)
    brief = {"chapter": 2, "title": "下一章"}
    previous = web._chapter_plan_fingerprint(novel, 2, brief, 5000, False)
    changes = [
        ("tracking/entities.json", {"locations": {"车站": {"status": "封锁"}}}),
        ("bible/author_preferences.json", {"profile": {"preferred_sentence_length": 18}}),
        ("bible/genre_pack.json", {"key": "suspense"}),
        ("summaries/000001.json", {"chapter": 1, "summary": "新的上章结果"}),
        ("timeline/000001_event.json", {"chapter": 1, "event": "警报响起"}),
        ("outline/scene_outlines.json", {"2": {"status": "confirmed"}}),
    ]
    for relative, payload in changes:
        storage.atomic_write_json(tmp_path / relative, payload)
        current = web._chapter_plan_fingerprint(novel, 2, brief, 5000, False)
        assert current != previous, relative
        previous = current


def test_plan_fingerprint_tracks_global_prompt_settings(tmp_path, monkeypatch):
    logger = logging.getLogger("plan-fingerprint-prompt-test")
    storage = StorageManager(logger)
    novel = NovelManager("提示词书", tmp_path, logger, storage)
    monkeypatch.setattr(web.prompt_settings_manager, "get", lambda: {"章节创作": {"chapter_write": {"instruction": "版本一"}}})
    before = web._chapter_plan_fingerprint(novel, 1, {"chapter": 1}, 5000, False)
    monkeypatch.setattr(web.prompt_settings_manager, "get", lambda: {"章节创作": {"chapter_write": {"instruction": "版本二"}}})
    after = web._chapter_plan_fingerprint(novel, 1, {"chapter": 1}, 5000, False)
    assert before != after


def test_plan_fingerprint_ignores_generated_scene_but_tracks_confirmed_scene(tmp_path):
    logger = logging.getLogger("plan-fingerprint-scene-status-test")
    storage = StorageManager(logger)
    novel = NovelManager("场景指纹书", tmp_path, logger, storage)
    brief = {"chapter": 1, "title": "入口"}
    before = web._chapter_plan_fingerprint(novel, 1, brief, 5000, False)
    storage.atomic_write_json(tmp_path / "outline" / "scene_outlines.json", {
        "1": {"chapter": 1, "status": "draft", "scenes": [{"title": "AI草稿"}]},
    })
    draft = web._chapter_plan_fingerprint(novel, 1, brief, 5000, False)
    assert draft == before
    storage.atomic_write_json(tmp_path / "outline" / "scene_outlines.json", {
        "1": {"chapter": 1, "status": "confirmed", "scenes": [{"title": "人工确认"}]},
    })
    confirmed = web._chapter_plan_fingerprint(novel, 1, brief, 5000, False)
    assert confirmed != before


def test_chapter_plan_cache_concurrent_saves_keep_every_chapter(tmp_path):
    logger = logging.getLogger("plan-cache-concurrency-test")
    storage = StorageManager(logger)
    novel = NovelManager("并发缓存书", tmp_path, logger, storage)
    plan = web.validate_chapter_plan({
        "beats": ["进入现场", "取得线索"],
        "scenes": [{"name": "现场", "goal": "调查", "word_budget": 800}],
    })
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(
            lambda chapter: web._save_cached_chapter_plan(novel, chapter, f"fp-{chapter}", plan),
            range(1, 21),
        ))
    cached = storage.safe_read_json(tmp_path / "outline" / "chapter_plans.json", {})
    assert set(cached) == {str(chapter) for chapter in range(1, 21)}


def test_generated_brief_cannot_overwrite_manual_edit_made_during_generation(tmp_path):
    logger = logging.getLogger("brief-optimistic-merge-test")
    storage = StorageManager(logger)
    novel = NovelManager("提要并发书", tmp_path, logger, storage)
    manual = {
        "chapter": 2, "title": "人工标题", "chapter_mode": "character",
        "synopsis": "人工重新指定这一章应围绕人物选择、关系变化和后续代价展开，不能沿用模型此前看到的旧目标。",
        "side_value": "推进人物关系", "must_happen": ["人工事件"],
    }
    generated = {
        "chapter": 2, "title": "模型旧标题", "chapter_mode": "main_progress",
        "synopsis": "模型根据旧上下文生成的提要，不应该覆盖生成期间刚刚保存的人工版本。",
    }
    storage.atomic_write_json(tmp_path / "outline" / "chapter_briefs.json", {"2": manual})
    selected = web._store_generated_brief(novel, 2, generated, None)
    assert selected == manual
    assert storage.safe_read_json(tmp_path / "outline" / "chapter_briefs.json", {})["2"] == manual
    assert storage.safe_read_json(tmp_path / "outline" / "chapter_titles.json", {})["2"] == "人工标题"


def test_review_approval_is_bound_to_kind_content_and_plan():
    payload = {"approved_reviews": [{
        "kind": "quality", "chapter": 2, "content_hash": "hash-a",
        "planning_fingerprint": "plan-a",
    }]}
    assert web._review_approved(payload, "quality", 2, "hash-a", "plan-a") is True
    assert web._review_approved(payload, "consistency", 2, "hash-a", "plan-a") is False
    assert web._review_approved(payload, "quality", 2, "hash-b", "plan-a") is False
    assert web._review_approved(payload, "quality", 2, "hash-a", "plan-b") is False


def test_task_resume_cannot_silently_approve_waiting_review(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "tasks.db")
    task_id = store.create(
        "测试书", "batch_chapters", "等待治理验收",
        {"waiting_review": {
            "kind": "preflight", "chapter": 2, "content_hash": "hash",
            "planning_fingerprint": "plan",
        }}, status="paused",
    )

    class Runner:
        @staticmethod
        def is_executing(_task_id):
            return False

        @staticmethod
        def notify():
            return None

    monkeypatch.setattr(web, "task_store", store)
    monkeypatch.setattr(web, "task_runner", Runner())
    blocked = asyncio.run(web.api_resume_task(task_id, approve_review=False))
    assert blocked.status_code == 409
    assert store.get(task_id)["status"] == "paused"
    assert store.get(task_id)["input"]["waiting_review"]["chapter"] == 2
    resumed = asyncio.run(web.api_resume_task(task_id, approve_review=True))
    assert resumed.status_code == 200
    assert store.get(task_id)["status"] == "queued"


def test_bounded_task_numbers_survive_corrupt_checkpoints():
    assert web._bounded_int("损坏", 3, 1, 10) == 3
    assert web._bounded_int(-50, 3, 1, 10) == 1
    assert web._bounded_int(500, 3, 1, 10) == 10


def test_batch_preflight_blocks_unready_project_without_creating_task(tmp_path, monkeypatch):
    logger = logging.getLogger("batch-preflight-test")
    storage = StorageManager(logger)
    novel = NovelManager("未准备书", tmp_path, logger, storage)
    monkeypatch.setattr(web, "get_novel_manager", lambda _name: novel)
    before = len(web.task_store.list("未准备书", 200))
    response = asyncio.run(web.api_batch_generate(
        "未准备书", count=3, target_words=5000,
        stop_on_warning=True, override_readiness=False,
    ))
    payload = json.loads(response.body)
    assert response.status_code == 409
    assert payload["readiness"]["status"] == "blocked"
    assert len(web.task_store.list("未准备书", 200)) == before


def test_batch_start_rejects_second_active_task_for_same_novel(tmp_path, monkeypatch):
    logger = logging.getLogger("batch-dedup-test")
    storage = StorageManager(logger)
    novel = NovelManager("批次去重书", tmp_path / "novel", logger, storage)
    store = TaskStore(tmp_path / "tasks.db")
    store.create("批次去重书", "batch_chapters", "已有任务", status="paused")
    monkeypatch.setattr(web, "get_novel_manager", lambda _name: novel)
    monkeypatch.setattr(web, "task_store", store)
    response = asyncio.run(web.api_batch_generate("批次去重书"))
    assert response.status_code == 409
    assert "已有运行、排队或暂停任务" in json.loads(response.body)["error"]
    assert len(store.list("批次去重书")) == 1


class JsonRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


def test_auto_serial_start_and_stop_only_manage_persistent_queue(tmp_path, monkeypatch):
    logger = logging.getLogger("serial-api-test")
    storage = StorageManager(logger)
    novel = NovelManager("自动队列书", tmp_path / "novel", logger, storage)
    storage.atomic_write_text(novel.path / "bible" / "world.md", "具有明确地点、社会结构和核心矛盾的近未来城市世界。")
    storage.atomic_write_text(novel.path / "bible" / "rules.md", "人物必须遵守时间连续性、信息权限和物品状态。")
    storage.atomic_write_text(novel.path / "outline" / "main.md", "主角调查失踪案并逐步确认幕后组织的长期总纲。")
    storage.atomic_write_json(novel.path / "outline" / "volumes.json", [{
        "title": "第一卷", "start_chapter": 1, "end_chapter": 10,
        "sections": [{"title": "第一节", "start_chapter": 1, "end_chapter": 10}],
    }])
    CharacterManager(novel.path, logger).create_character("林舟", role_tier="主角")
    CharacterManager(novel.path, logger).create_character("苏遥", role_tier="重要配角")
    store = TaskStore(tmp_path / "tasks.db")

    class Runner:
        @staticmethod
        def notify():
            return None

    monkeypatch.setattr(web, "get_novel_manager", lambda _name: novel)
    monkeypatch.setattr(web, "task_store", store)
    monkeypatch.setattr(web, "task_runner", Runner())
    ProductionControlManager(novel, logger, storage).update_policy({
        "target_chapter": 3, "batch_size": 1, "target_words": 5000,
    })
    started = asyncio.run(web.api_start_serial_control("自动队列书"))
    payload = json.loads(started.body)
    assert started.status_code == 200
    task = store.get(payload["task_id"])
    assert task["status"] == "queued"
    assert task["input"]["serial_controller"] is True
    assert task["input"]["count"] == 1
    stopped = asyncio.run(web.api_stop_serial_control("自动队列书"))
    assert stopped.status_code == 200
    assert store.get(payload["task_id"])["status"] == "cancelled"
    assert ProductionControlManager(novel, logger, storage).policy()["enabled"] is False


def test_candidate_draft_api_does_not_modify_canonical_chapter(tmp_path, monkeypatch):
    logger = logging.getLogger("candidate-api-test")
    storage = StorageManager(logger)
    novel = NovelManager("候选书", tmp_path, logger, storage)
    monkeypatch.setattr(web, "get_novel_manager", lambda _name: novel)
    response = asyncio.run(web.api_create_chapter_candidate(
        "候选书", JsonRequest({
            "chapter": 1, "content": "候选正文内容。" * 80,
            "label": "方向A", "target_words": 500,
        }),
    ))
    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["turn"]["source"] == "candidate"
    assert not (novel.path / "chapters" / "000001.txt").exists()
    listed = asyncio.run(web.api_chapter_candidates("候选书", 1))
    assert len(json.loads(listed.body)["items"]) == 1


def test_corrupt_chapter_plan_cache_is_replaced_safely(tmp_path):
    logger = logging.getLogger("corrupt-plan-cache-test")
    storage = StorageManager(logger)
    novel = NovelManager("损坏缓存书", tmp_path, logger, storage)
    storage.atomic_write_json(tmp_path / "outline" / "chapter_plans.json", [])
    assert web._load_cached_chapter_plan(novel, 1, "fingerprint") is None
    plan = web.validate_chapter_plan({
        "beats": ["进入现场", "发现异常"],
        "scenes": [{
            "name": "档案室", "goal": "找线索", "obstacle": "守卫",
            "turn": "停电", "exit_state": "拿到档案", "word_budget": 1400,
        }],
    })
    web._save_cached_chapter_plan(novel, 1, "fingerprint", plan)
    assert web._load_cached_chapter_plan(novel, 1, "fingerprint")["scenes"]
