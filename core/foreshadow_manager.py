"""伏笔生命周期管理。"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from filelock import FileLock

from storage_utils import StorageManager


class ForeshadowManager:
    def __init__(self, novel_path: Path, logger, storage: StorageManager | None = None):
        self.path = novel_path / "foreshadowing.json"
        self.logger = logger
        self.storage = storage or StorageManager(logger)

    def ingest(self, chapter: int, entries: list) -> dict:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._ingest(chapter, entries)

    def _ingest(self, chapter: int, entries: list) -> dict:
        data = self._load()
        introduced = resolved = 0
        for raw in entries:
            item = raw if isinstance(raw, dict) else {"action": "introduce", "text": str(raw)}
            if item.get("evidence_verified") is False:
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            action = item.get("action", "introduce")
            if action == "resolve":
                candidates = [
                    value for value in data["items"]
                    if value.get("status") == "open"
                    and (text in str(value.get("text", "")) or str(value.get("text", "")) in text)
                ]
                if candidates:
                    candidates[-1].update({"status": "resolved", "resolved_chapter": chapter, "resolved_at": datetime.now().isoformat()})
                    resolved += 1
                continue
            if any(value.get("status") == "open" and value.get("text") == text for value in data["items"]):
                continue
            try:
                target = int(item.get("target_chapter") or chapter + 10)
            except (TypeError, ValueError):
                target = chapter + 10
            data["items"].append({
                "id": uuid.uuid4().hex, "text": text, "introduced_chapter": chapter,
                "target_chapter": max(chapter + 1, target), "status": "open",
                "evidence": str(item.get("evidence", ""))[:500],
                "created_at": datetime.now().isoformat(),
            })
            introduced += 1
        self.storage.atomic_write_json(self.path, data)
        return {"introduced": introduced, "resolved": resolved}

    def list(self, current_chapter: int | None = None) -> list[dict]:
        items = self._load()["items"]
        for item in items:
            try:
                target_chapter = int(item.get("target_chapter", 0))
            except (TypeError, ValueError):
                target_chapter = 0
            item["overdue"] = bool(
                current_chapter is not None and item.get("status") == "open"
                and target_chapter > 0 and current_chapter > target_chapter
            )
        return items

    def open_items(self, current_chapter: int, limit: int = 20) -> list[dict]:
        items = [item for item in self.list(current_chapter) if item.get("status") == "open"]
        return sorted(items, key=lambda item: (
            not item.get("overdue", False), self._chapter_number(item.get("target_chapter")),
        ))[:limit]

    def update(self, item_id: str, **values) -> dict:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._update(item_id, **values)

    def _update(self, item_id: str, **values) -> dict:
        data = self._load()
        item = next((entry for entry in data["items"] if entry.get("id") == item_id), None)
        if not item:
            raise ValueError("伏笔不存在")
        if "text" in values and str(values["text"]).strip():
            item["text"] = str(values["text"]).strip()
        if "target_chapter" in values:
            item["target_chapter"] = max(item.get("introduced_chapter", 0) + 1, int(values["target_chapter"]))
        if values.get("status") in {"open", "resolved", "cancelled"}:
            item["status"] = values["status"]
        self.storage.atomic_write_json(self.path, data)
        return item

    def delete(self, item_id: str) -> bool:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._delete(item_id)

    def _delete(self, item_id: str) -> bool:
        data = self._load()
        before = len(data["items"])
        data["items"] = [item for item in data["items"] if item.get("id") != item_id]
        self.storage.atomic_write_json(self.path, data)
        return len(data["items"]) < before

    def _load(self) -> dict:
        data = self.storage.safe_read_json(self.path, {"items": []})
        items = data.get("items", []) if isinstance(data, dict) else []
        return {
            "items": [dict(item) for item in items if isinstance(item, dict)]
            if isinstance(items, list) else [],
        }

    @staticmethod
    def _chapter_number(value) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0
