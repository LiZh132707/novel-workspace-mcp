"""持久串行后台工作器。模型任务始终只执行一个。"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

from core.task_store import TaskStore


TaskHandler = Callable[[dict], dict | None]


class PersistentTaskRunner:
    def __init__(self, store: TaskStore, logger, poll_interval: float = 0.5):
        self.store = store
        self.logger = logger
        self.poll_interval = poll_interval
        self.handlers: dict[str, TaskHandler] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._active_task_id: str | None = None

    def register(self, kind: str, handler: TaskHandler):
        self.handlers[kind] = handler

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="novel-task-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3) -> bool:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            return not self._thread.is_alive()
        return True

    def notify(self):
        self._wake.set()

    def is_executing(self, task_id: str) -> bool:
        with self._state_lock:
            return self._active_task_id == task_id

    def _set_active(self, task_id: str | None):
        with self._state_lock:
            self._active_task_id = task_id

    def _run(self):
        self.logger.info("持久任务工作器启动（并发=1）")
        while not self._stop.is_set():
            task = self.store.claim_next(set(self.handlers))
            if not task:
                self._wake.wait(self.poll_interval)
                self._wake.clear()
                continue
            handler = self.handlers.get(task["kind"])
            if not handler:
                self.store.fail(task["id"], f"没有任务处理器: {task['kind']}")
                continue
            self._set_active(task["id"])
            try:
                self.store.event(task["id"], "后台工作器开始执行", 1, stage="starting")
                latest = self.store.get(task["id"])
                if latest and latest["status"] in {"cancelled", "paused"}:
                    continue
                result = handler(task) or {}
                latest = self.store.get(task["id"])
                if latest and latest["status"] in {"cancelled", "paused"}:
                    continue
                self.store.finish(task["id"], result)
            except Exception as exc:
                latest = self.store.get(task["id"])
                if latest and latest["status"] in {"cancelled", "paused"}:
                    self.logger.info("后台任务已%s，保留可恢复状态 %s", latest["status"], task["id"])
                    continue
                self.logger.exception("后台任务失败 %s", task["id"])
                self.store.fail(task["id"], str(exc))
            finally:
                self._set_active(None)
            time.sleep(0.05)
