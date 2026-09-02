import logging
from datetime import datetime, timedelta

from core.chapter_manager import ChapterManager
from core.novel_manager import NovelManager
from core.production_control_manager import ProductionControlManager
from core.task_store import TaskStore
from core.quality_tracker import QualityTracker
from storage_utils import StorageManager


LOGGER = logging.getLogger("production-control-test")


def test_serial_policy_is_bounded_and_persistent(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("自动连载书", tmp_path, LOGGER, storage)
    manager = ProductionControlManager(novel, LOGGER, storage)
    policy = manager.update_policy({
        "enabled": True, "target_chapter": 5000, "batch_size": 99,
        "target_words": 99999, "max_retries": 99, "cooldown_seconds": 99999,
        "commit_mode": "automatic", "scene_mode": True,
    })
    assert policy["target_chapter"] == 1000
    assert policy["batch_size"] == 10
    assert policy["target_words"] == 20000
    assert policy["max_retries"] == 5
    assert policy["cooldown_seconds"] == 3600
    assert manager.policy()["enabled"] is True


def test_task_store_does_not_claim_scheduled_task_early(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    future = (datetime.now() + timedelta(minutes=5)).isoformat()
    task_id = store.create("书", "batch", "定时任务", status="queued", not_before=future)
    assert store.claim_next({"batch"}) is None
    assert store.get(task_id)["status"] == "queued"


def test_planning_tree_edit_marks_future_and_preserves_committed_chapters(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("规划树书", tmp_path, LOGGER, storage)
    ChapterManager(novel, LOGGER).save_chapter(1, "已经提交的第一章正文。" * 30)
    novel.save_state({"target_chapters": 6})
    manager = ProductionControlManager(novel, LOGGER, storage)
    result = manager.update_tree_node("chapter:2", {
        "title": "人工第二章", "chapter_mode": "character", "synopsis": "人工提要",
    })
    assert result["tree"]["chapters"][0]["chapter"] == 2
    assert result["impact"]["chapters"] == [2]
    assert (tmp_path / "chapters" / "000001.txt").exists()


def test_rhythm_budget_issues_and_manuscript_are_model_free(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("生产总控书", tmp_path, LOGGER, storage)
    storage.atomic_write_json(tmp_path / "outline" / "chapter_briefs.json", {
        "1": {"chapter_mode": "main_progress"},
        "2": {"chapter_mode": "complication"},
        "3": {"chapter_mode": "main_progress"},
    })
    storage.atomic_write_json(tmp_path / "outline" / "volumes.json", [{
        "title": "第一卷", "start_chapter": 1, "end_chapter": 10,
    }])
    ChapterManager(novel, LOGGER).save_chapter(1, "第一章正文。" * 40)
    manager = ProductionControlManager(novel, LOGGER, storage)
    assert manager.rhythm()["recommended"] in {"character", "aftermath"}
    assert manager.budget(10, 5000, 50)["estimated_calls"] == 40
    assert manager.issues()["total"] >= 0
    manuscript = manager.manuscript()
    assert manuscript["chapter_count"] == 1
    assert manuscript["volumes"][0]["chapters"][0]["chapter"] == 1


def test_serial_time_window_and_circuit_breakers(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("熔断书", tmp_path, LOGGER, storage)
    manager = ProductionControlManager(novel, LOGGER, storage)
    manager.update_policy({
        "enabled": True, "allowed_start_hour": 22, "allowed_end_hour": 6,
        "breaker_failure_limit": 2, "breaker_short_chapter_limit": 2,
        "minimum_tokens_per_second": 20,
    })
    assert manager.next_allowed_time(datetime(2026, 7, 14, 23, 0)) is None
    assert manager.next_allowed_time(datetime(2026, 7, 14, 12, 0)).hour == 22
    assert manager.record_failure("第一次")["state"] == "running"
    failed = manager.record_failure("第二次")
    assert failed["state"] == "tripped"
    assert manager.policy()["enabled"] is False
    manager.update_policy({"enabled": True})
    assert manager.record_chapter_result(300, 500, 50)["state"] == "running"
    short = manager.record_chapter_result(300, 500, 50)
    assert short["state"] == "tripped"


def test_issue_center_can_resolve_quality_debt(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("问题书", tmp_path, LOGGER, storage)
    tracker = QualityTracker(tmp_path, LOGGER, storage)
    debt = tracker.add_debt(1, "generation_quality", "中", "段落重复", "重写")
    manager = ProductionControlManager(novel, LOGGER, storage)
    issue = manager.issues()["groups"]["quality_debts"][0]
    assert issue["issue_id"] == f"quality:{debt['id']}"
    result = manager.resolve_issue(issue["issue_id"], "resolve", {"resolution": "已经人工重写"})
    assert result["issues"]["groups"]["quality_debts"] == []


def test_production_statistics_aggregates_committed_turn_metrics(tmp_path):
    from core.chapter_turn_engine import ChapterTurnEngine

    storage = StorageManager(LOGGER)
    novel = NovelManager("统计书", tmp_path, LOGGER, storage)
    manager = ChapterManager(novel, LOGGER)
    engine = ChapterTurnEngine(novel, LOGGER, manager, storage)
    turn = engine.save_draft(1, "用于统计的正文。" * 80, 500, "batch", {
        "metrics": {"tokens_per_second": 50, "elapsed_seconds": 20, "calls": 3},
    }, False)
    engine.commit(turn["id"], allow_quality_failure=True, allow_fact_conflicts=True)
    stats = ProductionControlManager(novel, LOGGER, storage).statistics()
    assert stats["committed_turns"] == 1
    assert stats["average_tokens_per_second"] == 50
    assert stats["model_calls"] == 3
