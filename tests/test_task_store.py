import tempfile
from pathlib import Path

from core.task_store import TaskStore
from core.task_runner import PersistentTaskRunner
import logging
import time
import sqlite3
import threading


def test_task_lifecycle_and_events():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        task_id = store.create("测试小说", "chapter", "生成第一章", {"words": 1000})
        store.event(task_id, "开始规划", 10, stage="planning")
        store.event(task_id, "开始写作", 30, stage="writing")
        task = store.get(task_id)
        assert task["progress"] == 30
        assert len(task["events"]) == 2
        store.finish(task_id, {"words": 987})
        assert store.get(task_id)["status"] == "completed"


def test_running_tasks_become_interrupted_after_restart():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        task_id = store.create("测试小说", "planning", "开书策划")
        store.mark_interrupted()
        assert store.get(task_id)["status"] == "interrupted"


def test_serial_background_runner_completes_queued_task():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        runner = PersistentTaskRunner(store, logging.getLogger("test"), poll_interval=0.02)
        runner.register("demo", lambda task: {"value": task["input"]["value"] + 1})
        task_id = store.create("测试", "demo", "测试后台任务", {"value": 2}, status="queued")
        runner.start()
        runner.notify()
        for _ in range(100):
            if store.get(task_id)["status"] == "completed":
                break
            time.sleep(0.02)
        runner.stop()
        task = store.get(task_id)
        assert task["status"] == "completed"
        assert task["result"]["value"] == 3


def test_runner_reports_task_as_executing_until_handler_exits():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        runner = PersistentTaskRunner(store, logging.getLogger("test-active"), poll_interval=0.01)
        entered = threading.Event()
        release = threading.Event()

        def handler(_task):
            entered.set()
            release.wait(2)
            return {"ok": True}

        runner.register("demo", handler)
        task_id = store.create("测试", "demo", "活动任务", status="queued")
        runner.start()
        runner.notify()
        assert entered.wait(1)
        assert runner.is_executing(task_id) is True
        release.set()
        for _ in range(100):
            if not runner.is_executing(task_id):
                break
            time.sleep(0.01)
        runner.stop()
        assert runner.is_executing(task_id) is False


def test_runner_stop_reports_when_handler_is_still_active():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        runner = PersistentTaskRunner(store, logging.getLogger("test-stop"), poll_interval=0.01)
        entered = threading.Event()
        release = threading.Event()

        def handler(_task):
            entered.set()
            release.wait(2)
            return {}

        runner.register("demo", handler)
        task_id = store.create("测试", "demo", "关机中的任务", status="queued")
        runner.start()
        runner.notify()
        assert entered.wait(1)
        assert runner.stop(timeout=0.01) is False
        store.mark_interrupted()
        assert store.get(task_id)["status"] == "interrupted"
        release.set()
        for _ in range(100):
            if not runner.is_executing(task_id):
                break
            time.sleep(0.01)
        assert runner.stop(timeout=1) is True


def test_task_input_checkpoint_survives_reopen():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "tasks.db"
        store = TaskStore(database)
        task_id = store.create("测试", "batch", "批量", {"completed_chapters": []}, status="queued")
        store.patch_input(task_id, {"completed_chapters": [1, 2]})
        reopened = TaskStore(database)
        assert reopened.get(task_id)["input"]["completed_chapters"] == [1, 2]


def test_create_if_idle_is_atomic_across_store_instances():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "tasks.db"
        stores = [TaskStore(database), TaskStore(database)]
        barrier = threading.Barrier(2)
        results = []

        def create(index):
            barrier.wait()
            results.append(stores[index].create_if_idle("同一本书", "batch", f"任务{index}", status="queued"))

        threads = [threading.Thread(target=create, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sum(bool(item) for item in results) == 1
        assert len(stores[0].active_for_novel("同一本书")) == 1


def test_create_if_idle_allows_only_one_successor_of_running_task():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        predecessor = store.create("自动书", "batch", "当前回合", status="running")
        successor = store.create_if_idle(
            "自动书", "batch", "后继回合", status="queued",
            allowed_active_task_id=predecessor,
        )
        assert successor
        assert store.create_if_idle(
            "自动书", "batch", "重复后继", status="queued",
            allowed_active_task_id=predecessor,
        ) is None
        assert store.create_if_idle("自动书", "manual", "外部任务", status="queued") is None
        assert len(store.active_for_novel("自动书")) == 2


def test_task_can_pause_and_resume():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        task_id = store.create("测试", "batch", "批量", status="queued")
        assert store.pause(task_id) is True
        assert store.get(task_id)["status"] == "paused"
        assert store.resume(task_id) is True
        assert store.get(task_id)["status"] == "queued"


def test_resume_cannot_reintroduce_second_active_task_for_same_novel():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        failed = store.create("同书", "batch", "旧失败任务", status="running")
        store.fail(failed, "模拟失败")
        active = store.create_if_idle("同书", "batch", "新任务", status="queued")
        assert active
        assert store.resume(failed) is False
        assert store.get(failed)["status"] == "failed"
        assert len(store.active_for_novel("同书")) == 1


def test_requeue_cannot_reintroduce_second_active_task_for_same_novel():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        interrupted = store.create("同书", "batch", "旧中断任务", status="running")
        store.mark_interrupted()
        active = store.create_if_idle("同书", "batch", "新任务", status="queued")
        assert active
        assert store.requeue(interrupted) is False
        assert store.get(interrupted)["status"] == "interrupted"


def test_invalid_transitions_do_not_report_success_or_resurrect_final_task():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        task_id = store.create("测试", "batch", "终态", status="running")
        store.finish(task_id, {"ok": True})
        assert store.pause(task_id) is False
        assert store.resume(task_id) is False
        assert store.requeue(task_id) is False
        task = store.get(task_id)
        assert task["status"] == "completed"
        assert task["result"] == {"ok": True}


def test_late_event_does_not_overwrite_paused_stage_or_progress():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        task_id = store.create("测试", "batch", "暂停竞态", status="running")
        store.event(task_id, "模型生成中", 35, stage="writing")
        assert store.pause(task_id) is True
        store.event(task_id, "迟到的模型事件", 80, stage="late")
        task = store.get(task_id)
        assert task["status"] == "paused"
        assert task["stage"] == "用户暂停"
        assert task["progress"] == 35
        assert [event["message"] for event in task["events"]] == ["模型生成中"]


def test_paused_review_checkpoint_is_atomically_approved_before_resume():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        task_id = store.create(
            "测试", "batch_chapters", "质量暂停",
            {"waiting_review": {
                "kind": "quality", "chapter": 3, "content_hash": "正文哈希",
                "planning_fingerprint": "规划指纹",
            }, "approved_reviews": []},
            status="paused",
        )
        assert store.approve_waiting_review(task_id) is True
        task = store.get(task_id)
        assert task["input"]["waiting_review"] == {}
        assert task["input"]["approved_reviews"][0] | {"approved_at": "忽略"} == {
            "kind": "quality", "chapter": 3, "content_hash": "正文哈希",
            "planning_fingerprint": "规划指纹", "approved_at": "忽略",
        }
        assert store.resume(task_id) is True
        assert store.approve_waiting_review(task_id) is False


def test_preflight_governance_checkpoint_can_be_explicitly_approved():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        task_id = store.create(
            "测试", "batch_chapters", "治理检查暂停",
            {"waiting_review": {
                "kind": "preflight", "chapter": 4, "content_hash": "正文哈希",
                "planning_fingerprint": "规划指纹",
            }}, status="paused",
        )
        assert store.approve_waiting_review(task_id) is True
        approval = store.get(task_id)["input"]["approved_reviews"][0]
        assert approval["kind"] == "preflight"
        assert approval["chapter"] == 4


def test_corrupt_review_checkpoint_cannot_be_approved_or_bypass_resume_gate():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        task_id = store.create(
            "测试", "batch_chapters", "损坏验收点",
            {"waiting_review": {"kind": "volume_review", "chapter": "损坏"},
             "approved_reviews": [{"kind": "quality", "chapter": "错误", "content_hash": "x"}]},
            status="paused",
        )
        assert store.approve_waiting_review(task_id) is False
        task = store.get(task_id)
        assert task["status"] == "paused"
        assert task["input"]["waiting_review"]["chapter"] == "损坏"


def test_review_approval_requires_content_hash_and_known_kind():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        missing_hash = store.create(
            "测试", "batch_chapters", "缺哈希",
            {"waiting_review": {"kind": "quality", "chapter": 1}}, status="paused",
        )
        unknown_kind = store.create(
            "测试", "batch_chapters", "未知类型",
            {"waiting_review": {"kind": "other", "chapter": 1, "content_hash": "hash"}},
            status="paused",
        )
        assert store.approve_waiting_review(missing_hash) is False
        assert store.approve_waiting_review(unknown_kind) is False


def test_task_history_can_be_cleared_when_idle():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        task_id = store.create("测试", "demo", "历史", status="completed")
        store.event(task_id, "完成")
        store.clear_all()
        assert store.list() == []


def test_cancelled_task_cannot_be_overwritten_by_late_finish_or_failure():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        task_id = store.create("测试", "demo", "竞态", status="running")
        store.event(task_id, "生成中", 55, stage="writing")
        store.cancel(task_id)
        store.finish(task_id, {"should_not": "win"})
        store.fail(task_id, "迟到错误")
        store.event(task_id, "迟到进度", 99, stage="late")
        task = store.get(task_id)
        assert task["status"] == "cancelled"
        assert task["progress"] == 55
        assert task["stage"] == "writing"
        assert task["result"] == {}
        assert [event["message"] for event in task["events"]] == ["生成中"]


def test_completed_task_cannot_be_cancelled_after_completion():
    with tempfile.TemporaryDirectory() as tmp:
        store = TaskStore(Path(tmp) / "tasks.db")
        task_id = store.create("测试", "demo", "完成竞态", status="running")
        store.finish(task_id, {"ok": True})
        store.cancel(task_id)
        task = store.get(task_id)
        assert task["status"] == "completed" and task["result"] == {"ok": True}


def test_corrupt_task_payload_does_not_break_task_api_or_checkpoint_repair():
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "tasks.db"
        store = TaskStore(database)
        task_id = store.create("测试", "demo", "损坏载荷", status="running")
        connection = sqlite3.connect(database)
        try:
            connection.execute("UPDATE tasks SET input_json='{broken',result_json='[]' WHERE id=?", (task_id,))
            connection.commit()
        finally:
            connection.close()
        task = store.get(task_id)
        assert task["payload_corrupt"] is True and task["input"] == {} and task["result"] == {}
        store.patch_input(task_id, {"checkpoint": 3})
        assert store.get(task_id)["input"] == {"checkpoint": 3}
