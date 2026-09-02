"""章节提交后的分级巡检调度，不调用模型。"""
from __future__ import annotations

from datetime import datetime

from core.long_form_evaluator import LongFormEvaluator
from core.project_health_manager import ProjectHealthManager
from storage_utils import StorageManager


class PatrolManager:
    def __init__(self, novel_manager, logger=None, storage: StorageManager | None = None):
        self.nm = novel_manager
        self.logger = logger
        self.storage = storage or novel_manager.storage or StorageManager(logger)
        self.path = novel_manager.path / "planning" / "patrols.json"

    @staticmethod
    def due_for_chapter(chapter: int) -> dict:
        chapter = max(0, int(chapter))
        return {
            "health": chapter > 0 and chapter % 10 == 0,
            "long_form": chapter > 0 and chapter % 25 == 0,
        }

    def after_commit(self, chapter: int) -> dict:
        due = self.due_for_chapter(chapter)
        result = {"chapter": int(chapter), "due": due, "created_at": datetime.now().isoformat()}
        if due["health"]:
            result["health"] = ProjectHealthManager(self.nm, self.logger, self.storage).scan()
        if due["long_form"]:
            result["long_form"] = LongFormEvaluator(self.nm.path, self.logger, self.storage).run()
        if due["health"] or due["long_form"]:
            data = self.storage.safe_read_json(self.path, {"items": []})
            data = data if isinstance(data, dict) else {"items": []}
            items = data.get("items", []) if isinstance(data.get("items"), list) else []
            items.append(result)
            data["items"] = items[-100:]
            self.storage.atomic_write_json(self.path, data)
        return result

    def latest(self) -> dict:
        data = self.storage.safe_read_json(self.path, {"items": []})
        items = data.get("items", []) if isinstance(data, dict) else []
        return items[-1] if isinstance(items, list) and items else {}
