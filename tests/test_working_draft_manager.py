import logging

from core.quality_tracker import QualityTracker
from core.working_draft_manager import WorkingDraftManager
from storage_utils import StorageManager


def test_working_draft_only_recovers_for_exact_task_and_plan(tmp_path):
    storage = StorageManager(logging.getLogger("working-draft-test"))
    manager = WorkingDraftManager(tmp_path, storage)
    metadata = {
        "task_id": "task-a", "planning_fingerprint": "plan-a",
        "target_words": 5000, "scene_mode": False,
    }
    content = "正文" * 120
    manager.save(1, content, metadata)
    assert manager.load(1, metadata) == content

    manager.save(1, content, metadata)
    assert manager.load(1, metadata | {"planning_fingerprint": "plan-b"}) is None
    assert list((tmp_path / "drafts" / "recovery").glob("000001_*.txt"))


def test_working_draft_rejects_cross_task_and_tampered_content(tmp_path):
    storage = StorageManager(logging.getLogger("working-draft-tamper-test"))
    manager = WorkingDraftManager(tmp_path, storage)
    metadata = {
        "task_id": "task-a", "planning_fingerprint": "plan-a",
        "target_words": 5000, "scene_mode": False,
    }
    content = "可靠正文" * 80
    manager.save(2, content, metadata)
    assert manager.load(2, metadata | {"task_id": "task-b"}) is None

    manager.save(2, content, metadata)
    (tmp_path / "drafts" / "000002_working.txt").write_text("被外部改写" * 80, "utf-8")
    assert manager.load(2, metadata) is None


def test_working_draft_clear_removes_text_and_metadata(tmp_path):
    storage = StorageManager(logging.getLogger("working-draft-clear-test"))
    manager = WorkingDraftManager(tmp_path, storage)
    metadata = {"task_id": "task", "planning_fingerprint": "plan"}
    manager.save(3, "正文" * 120, metadata)
    manager.clear(3)
    assert not (tmp_path / "drafts" / "000003_working.txt").exists()
    assert not (tmp_path / "drafts" / "000003_working.json").exists()


def test_quality_debt_is_idempotent_during_recovery(tmp_path):
    tracker = QualityTracker(tmp_path, logging.getLogger("quality-debt-test"))
    first = tracker.add_debt(4, "consistency", "高", "人物状态冲突", "人工复核")
    second = tracker.add_debt(4, "consistency", "高", "人物状态冲突", "人工复核")
    assert first["id"] == second["id"]
    assert tracker.get_report()["total_debts"] == 1
