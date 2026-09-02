"""用户声明的不可静默改写设定锁。"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from filelock import FileLock

from storage_utils import StorageManager


class CanonicalLockManager:
    KINDS = {"character", "location", "item", "faction", "relationship", "world_rule"}

    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.path = novel_path / "tracking" / "canonical_locks.json"
        self.storage = storage or StorageManager(logger)

    def list(self) -> list[dict]:
        return list(self._load()["items"])

    def upsert(self, kind: str, name: str, field: str, value: str, reason: str = "") -> dict:
        kind, name, field, value = map(lambda item: str(item).strip(), (kind, name, field, value))
        if kind not in self.KINDS:
            raise ValueError("不支持的设定锁类型")
        if not name or not field or not value:
            raise ValueError("设定锁的名称、字段和值不能为空")
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            data = self._load()
            item = next((entry for entry in data["items"] if entry.get("kind") == kind and entry.get("name") == name and entry.get("field") == field), None)
            now = datetime.now().isoformat()
            if item is None:
                item = {"id": uuid.uuid4().hex, "kind": kind, "name": name, "field": field, "created_at": now}
                data["items"].append(item)
            item.update({"value": value[:1000], "reason": str(reason)[:500], "updated_at": now})
            self.storage.atomic_write_json(self.path, data)
            return dict(item)

    def remove(self, lock_id: str) -> bool:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            data = self._load()
            kept = [item for item in data["items"] if item.get("id") != lock_id]
            if len(kept) == len(data["items"]):
                return False
            self.storage.atomic_write_json(self.path, {"items": kept})
            return True

    def conflicts(self, summary: dict) -> list[dict]:
        candidates = self._candidates(summary)
        conflicts = []
        for lock in self.list():
            key = (lock.get("kind"), lock.get("name"), lock.get("field"))
            for candidate in candidates.get(key, []):
                value = str(candidate.get("value", "")).strip()
                if value and value != str(lock.get("value", "")).strip():
                    conflicts.append({
                        "lock_id": lock.get("id"), "kind": key[0], "name": key[1], "field": key[2],
                        "locked_value": lock.get("value", ""), "proposed_value": value,
                        "evidence": candidate.get("evidence", ""), "reason": lock.get("reason", ""),
                        "message": f"{key[1]} / {key[2]} 已锁定为“{lock.get('value', '')}”，本章拟改为“{value}”",
                    })
        return conflicts

    def compact_context(self) -> str:
        items = self.list()
        if not items:
            return ""
        lines = ["【用户权威设定锁（最高优先级，正文不得静默改写）】"]
        labels = {"character": "人物", "location": "地点", "item": "物品", "faction": "势力", "relationship": "关系", "world_rule": "世界规则"}
        for item in items[:100]:
            reason = f"；原因：{item.get('reason')}" if item.get("reason") else ""
            lines.append(f"- {labels.get(item.get('kind'), item.get('kind'))}/{item.get('name')}/{item.get('field')} = {item.get('value')}{reason}")
        return "\n".join(lines)

    @staticmethod
    def _candidates(summary: dict) -> dict[tuple, list[dict]]:
        result: dict[tuple, list[dict]] = {}
        def add(kind, name, field, value, evidence=""):
            if str(name).strip() and str(field).strip() and str(value).strip():
                result.setdefault((kind, str(name).strip(), str(field).strip()), []).append({"value": value, "evidence": evidence})
        for item in summary.get("characters_changed", []):
            if isinstance(item, dict):
                add("character", item.get("name"), item.get("field") or "status", item.get("new_value") or item.get("change"), item.get("evidence"))
        for kind, source in (("location", "locations"), ("item", "items"), ("faction", "factions")):
            for item in summary.get(source, []):
                if isinstance(item, dict):
                    for field, value in item.items():
                        if field not in {"name", "evidence", "evidence_verified"}:
                            add(kind, item.get("name"), field, value, item.get("evidence"))
        for item in summary.get("relationship_changes", []):
            if isinstance(item, dict):
                name = f"{item.get('from', '')}→{item.get('to', '')}"
                add("relationship", name, "type", item.get("type"), item.get("evidence"))
                add("relationship", name, "strength", item.get("strength"), item.get("evidence"))
        for item in summary.get("world_rule_changes", []):
            if isinstance(item, dict):
                add("world_rule", item.get("name"), item.get("field") or "value", item.get("value"), item.get("evidence"))
        return result

    def _load(self) -> dict:
        data = self.storage.safe_read_json(self.path, {"items": []})
        items = data.get("items", []) if isinstance(data, dict) else []
        return {"items": [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []}
