"""人物、地点、物品和势力的统一动态状态卡。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from filelock import FileLock

from storage_utils import StorageManager


class StateCardManager:
    TYPES = {"character", "location", "item", "faction", "relationship"}

    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.path = novel_path / "tracking" / "state_cards.json"
        self.storage = storage or StorageManager(logger)

    def get(self) -> dict:
        raw = self.storage.safe_read_json(self.path, {kind: {} for kind in self.TYPES})
        raw = raw if isinstance(raw, dict) else {}
        data = {}
        for kind in self.TYPES:
            cards = raw.get(kind, {})
            if not isinstance(cards, dict):
                data[kind] = {}
                continue
            normalized = {}
            for name, card in cards.items():
                if not isinstance(card, dict):
                    continue
                item = dict(card)
                item["name"] = str(item.get("name") or name)
                item["type"] = kind
                item["fields"] = item.get("fields", {}) if isinstance(item.get("fields"), dict) else {}
                item["history"] = item.get("history", []) if isinstance(item.get("history"), list) else []
                normalized[str(name)] = item
            data[kind] = normalized
        return data

    def upsert(self, kind: str, name: str, chapter: int, fields: dict, evidence: str = "", source: str = "manual") -> dict:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._upsert(kind, name, chapter, fields, evidence, source)

    def _upsert(self, kind: str, name: str, chapter: int, fields: dict, evidence: str = "", source: str = "manual") -> dict:
        if kind not in self.TYPES:
            raise ValueError("未知状态卡类型")
        name = str(name).strip()[:120]
        if not name:
            raise ValueError("状态卡名称不能为空")
        data = self.get()
        cards = data.setdefault(kind, {})
        current = cards.get(name, {"name": name, "type": kind, "fields": {}, "history": []})
        clean_fields = {str(key)[:80]: str(value)[:1000] for key, value in fields.items() if str(value).strip()}
        current["fields"].update(clean_fields)
        current["last_chapter"] = int(chapter)
        current["updated_at"] = datetime.now().isoformat()
        current["history"].append({"chapter": int(chapter), "fields": clean_fields, "evidence": str(evidence)[:500], "source": source})
        current["history"] = current["history"][-80:]
        cards[name] = current
        self.storage.atomic_write_json(self.path, data)
        return current

    def ingest_summary(self, chapter: int, summary: dict) -> dict:
        counts = {kind: 0 for kind in self.TYPES}
        for item in summary.get("characters_changed", []):
            if not isinstance(item, dict) or not item.get("name"):
                continue
            field = str(item.get("field") or "status")
            self.upsert("character", item["name"], chapter, {field: item.get("new_value") or item.get("change", "")}, item.get("evidence", ""), "summary")
            counts["character"] += 1
        for kind, source in (("location", "locations"), ("item", "items"), ("faction", "factions")):
            for item in summary.get(source, []):
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                fields = {key: value for key, value in item.items() if key not in {"name", "evidence"}}
                self.upsert(kind, item["name"], chapter, fields, item.get("evidence", ""), "summary")
                counts[kind] += 1
        for item in summary.get("relationship_changes", []):
            if not isinstance(item, dict) or not item.get("from") or not item.get("to"):
                continue
            name = f"{item['from']}→{item['to']}"
            self.upsert("relationship", name, chapter, {"type": item.get("type", ""), "strength": item.get("strength", "")}, item.get("evidence", ""), "summary")
            counts["relationship"] += 1
        return counts

    def compact_context(self, limit_per_type: int = 30) -> str:
        data = self.get()
        lines = ["【动态状态卡（以最新章节为准）】"]
        labels = {"character": "人物", "location": "地点", "item": "物品", "faction": "势力", "relationship": "关系"}
        for kind in ("character", "location", "item", "faction", "relationship"):
            cards = sorted(data.get(kind, {}).values(), key=lambda item: int(item.get("last_chapter", 0)), reverse=True)[:limit_per_type]
            for card in cards:
                fields = "｜".join(f"{key}:{value}" for key, value in card.get("fields", {}).items())
                lines.append(f"- {labels[kind]}/{card.get('name')}（第{card.get('last_chapter', 0)}章）：{fields}")
        return "\n".join(lines) if len(lines) > 1 else ""
