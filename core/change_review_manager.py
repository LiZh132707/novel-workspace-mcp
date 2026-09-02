"""人物变更建议审核：模型只能提议，确认后才写入人物档案。"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from filelock import FileLock

from core.character_manager import CharacterManager
from storage_utils import StorageManager


class ChangeReviewManager:
    ALLOWED_FIELDS = {"current_status", "location", "ability_level", "relationships", "important_event"}

    def __init__(self, novel_path: Path, logger, storage: StorageManager | None = None):
        self.path = novel_path / "reviews" / "character_changes.json"
        self.logger = logger
        self.storage = storage or StorageManager(logger)
        self.characters = CharacterManager(novel_path, logger)

    def add_from_summary(self, chapter: int, changes: list[dict]) -> int:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._add_from_summary(chapter, changes)

    def _add_from_summary(self, chapter: int, changes: list[dict]) -> int:
        data = self._load()
        added = 0
        for change in changes:
            if not isinstance(change, dict):
                continue
            name = str(change.get("name", "")).strip()
            field = str(change.get("field", "")).strip()
            new_value = str(change.get("new_value", "")).strip()
            if not name or field not in self.ALLOWED_FIELDS or not self.characters.get_character(name):
                continue
            duplicate = any(
                item.get("chapter") == chapter and item.get("name") == name
                and item.get("field") == field and item.get("new_value") == new_value
                for item in data["items"]
            )
            if duplicate:
                continue
            data["items"].append({
                "id": uuid.uuid4().hex, "chapter": chapter, "name": name, "field": field,
                "old_value": str(change.get("old_value", "")), "new_value": new_value,
                "change": str(change.get("change", "")), "evidence": str(change.get("evidence", "")),
                "status": "pending", "created_at": datetime.now().isoformat(),
            })
            added += 1
        self.storage.atomic_write_json(self.path, data)
        return added

    def add_new_characters(self, chapter: int, characters: list[dict]) -> int:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._add_new_characters(chapter, characters)

    def _add_new_characters(self, chapter: int, characters: list[dict]) -> int:
        data = self._load()
        added = 0
        for character in characters:
            if not isinstance(character, dict):
                continue
            if character.get("evidence_verified") is False:
                continue
            name = str(character.get("name", "")).strip()
            if not name or self.characters.get_character(name):
                continue
            if any(
                item.get("status") == "pending" and item.get("name") == name
                and item.get("field") == "new_character" for item in data["items"]
            ):
                continue
            data["items"].append({
                "id": uuid.uuid4().hex, "chapter": chapter, "name": name,
                "field": "new_character", "old_value": "", "new_value": name,
                "change": "新增人物档案", "evidence": str(character.get("evidence", "")),
                "details": {
                    **{key: str(character.get(key, "")) for key in ("personality", "background", "abilities", "relationships")},
                    "personality_profile": character.get("personality_profile", {})
                    if isinstance(character.get("personality_profile"), dict) else {},
                },
                "status": "pending", "created_at": datetime.now().isoformat(),
            })
            added += 1
        self.storage.atomic_write_json(self.path, data)
        return added

    def list(self, status: str | None = "pending") -> list[dict]:
        return [dict(item) for item in self._load()["items"] if not status or item.get("status") == status]

    def decide(self, change_id: str, accept: bool) -> dict:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._decide(change_id, accept)

    def _decide(self, change_id: str, accept: bool) -> dict:
        data = self._load()
        item = next((value for value in data["items"] if value.get("id") == change_id), None)
        if not item:
            raise ValueError("人物变更建议不存在")
        if item.get("status") != "pending":
            return item
        if accept:
            field = item["field"]
            if field == "new_character":
                details = item.get("details", {})
                self.characters.create_character(
                    item["name"], details.get("personality", ""), details.get("background", ""),
                    details.get("abilities", ""), "凡人", details.get("relationships", ""), "存活",
                    personality_profile=details.get("personality_profile", {}),
                )
                self.characters.update_character(
                    item["name"], last_chapter=item["chapter"], appearance_start=item["chapter"],
                )
                item["status"] = "accepted"
                item["decided_at"] = datetime.now().isoformat()
                self.storage.atomic_write_json(self.path, data)
                return item
            if field not in self.ALLOWED_FIELDS:
                raise ValueError(f"不支持自动应用的字段: {field or '未结构化'}")
            if field == "important_event":
                self.characters.add_event_to_character(item["name"], item["new_value"] or item["change"])
            else:
                current = self.characters.get_character(item["name"]) or {}
                if not str(item.get("old_value", "")).strip():
                    current_field = "locations" if field == "location" else field
                    value = current.get(current_field, "")
                    if field == "location" and isinstance(value, list):
                        value = value[-1].get("location", "") if value else ""
                    item["old_value"] = str(value)
                kwargs = {field: item["new_value"], "last_chapter": item["chapter"]}
                self.characters.update_character(item["name"], **kwargs)
            item["status"] = "accepted"
        else:
            item["status"] = "rejected"
        item["decided_at"] = datetime.now().isoformat()
        self.storage.atomic_write_json(self.path, data)
        return item

    def character_status_at(self, name: str, chapter: int, fallback: str = "未知") -> str:
        changes = sorted(
            (
                item for item in self.list(None)
                if item.get("status") == "accepted" and item.get("name") == name
                and item.get("field") == "current_status"
            ),
            key=lambda item: self._chapter(item.get("chapter")),
        )
        applicable = [item for item in changes if self._chapter(item.get("chapter")) <= int(chapter)]
        if applicable:
            return str(applicable[-1].get("new_value") or fallback)
        if changes:
            return str(changes[0].get("old_value") or fallback)
        return fallback

    def _load(self) -> dict:
        data = self.storage.safe_read_json(self.path, {"items": []})
        items = data.get("items", []) if isinstance(data, dict) else []
        return {"items": [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []}

    @staticmethod
    def _chapter(value) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0
