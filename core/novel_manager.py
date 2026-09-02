"""小说管理器：管理单本小说的元数据与状态，使用事务性写入。"""
from pathlib import Path
from datetime import datetime
from typing import Optional

from storage_utils import StorageManager
from filelock import FileLock


class NovelManager:
    def __init__(self, name: str, novel_path: Path, logger,
                 storage: Optional[StorageManager] = None):
        self.name = name
        self.path = novel_path
        self.logger = logger
        self.storage = storage or StorageManager(logger)
        self._state_path = novel_path / "state.json"

    def get_state(self) -> dict:
        return self.storage.safe_read_json(self._state_path, {
            "current_chapter": 0, "total_words": 0,
            "status": "创作中", "next_goal": "", "last_summary": "",
        })

    def save_state(self, updates: dict):
        """使用读写锁保存状态，防止 TOCTOU 竞争。"""
        lock = FileLock(str(self._state_path) + ".lock", timeout=30)
        with lock:
            state = self.get_state()
            state.update(updates)
            state["updated_at"] = datetime.now().isoformat()
            self.storage.atomic_write_json(self._state_path, state)

    def get_current_chapter(self) -> int:
        return self.get_state().get("current_chapter", 0)

    def increment_chapter(self):
        """原子递增章节号（锁覆盖读取操作）。"""
        lock = FileLock(str(self._state_path) + ".lock", timeout=30)
        with lock:
            st = self.get_state()
            st["current_chapter"] = st.get("current_chapter", 0) + 1
            st["updated_at"] = datetime.now().isoformat()
            self.storage.atomic_write_json(self._state_path, st)
            return st["current_chapter"]

    def update_next_goal(self, goal: str):
        self.save_state({"next_goal": goal})

    def update_last_summary(self, summary: str):
        self.save_state({"last_summary": summary})

    def add_words(self, count: int):
        """原子增加字数（锁覆盖读取操作），支持负数表示减少。"""
        lock = FileLock(str(self._state_path) + ".lock", timeout=30)
        with lock:
            st = self.get_state()
            new_total = st.get("total_words", 0) + count
            if new_total < 0:
                current = st.get("total_words", 0)
                raise ValueError(f"字数不能为负数：当前{current} + 调整{count} = {new_total}")
            st["total_words"] = new_total
            st["updated_at"] = datetime.now().isoformat()
            self.storage.atomic_write_json(self._state_path, st)

    def get_status_report(self) -> dict:
        state = self.get_state()
        return {
            "name": self.name,
            "current_chapter": state.get("current_chapter", 0),
            "total_words": state.get("total_words", 0),
            "status": state.get("status", "创作中"),
            "next_goal": state.get("next_goal", ""),
            "last_summary": state.get("last_summary", ""),
        }
