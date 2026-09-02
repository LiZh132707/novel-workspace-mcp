"""地点、势力、物品及人物关系的连续性账本。"""
from pathlib import Path

from filelock import FileLock

from storage_utils import StorageManager


class EntityLedger:
    def __init__(self, novel_path: Path, logger, storage: StorageManager | None = None):
        self.path = novel_path / "tracking" / "entities.json"
        self.storage = storage or StorageManager(logger)

    def get(self) -> dict:
        data = self.storage.safe_read_json(
            self.path,
            {"locations": {}, "factions": {}, "items": {}, "relationships": []},
        )
        data = data if isinstance(data, dict) else {}
        return {
            "locations": data.get("locations", {}) if isinstance(data.get("locations"), dict) else {},
            "factions": data.get("factions", {}) if isinstance(data.get("factions"), dict) else {},
            "items": data.get("items", {}) if isinstance(data.get("items"), dict) else {},
            "relationships": data.get("relationships", []) if isinstance(data.get("relationships"), list) else [],
        }

    def ingest(self, chapter: int, summary: dict) -> dict:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._ingest(chapter, summary)

    def _ingest(self, chapter: int, summary: dict) -> dict:
        data = self.get()
        for kind in ("locations", "factions", "items"):
            for item in summary.get(kind, []):
                if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                    continue
                if item.get("evidence_verified") is False:
                    continue
                name = str(item["name"]).strip()
                current = data[kind].setdefault(name, {"name": name, "first_chapter": chapter, "history": []})
                state = {
                    key: value for key, value in item.items()
                    if key not in {"name", "evidence", "evidence_verified"} and value not in (None, "", [])
                }
                if state:
                    current.update(state)
                    event = {"chapter": chapter} | state
                    if event not in current["history"]:
                        current["history"].append(event)
                current["last_chapter"] = chapter
        for item in summary.get("relationship_changes", []):
            if not isinstance(item, dict) or not item.get("from") or not item.get("to"):
                continue
            relation = {
                "from": str(item["from"]), "to": str(item["to"]), "type": str(item.get("type", "未知")),
                "strength": self._strength(item.get("strength", 0)),
                "chapter": chapter, "evidence": str(item.get("evidence", "")),
            }
            if relation not in data["relationships"]:
                data["relationships"].append(relation)
        self.storage.atomic_write_json(self.path, data)
        return {key: len(value) for key, value in data.items()}

    @staticmethod
    def _strength(value) -> int:
        try:
            return max(-100, min(100, int(float(value))))
        except (TypeError, ValueError):
            labels = {"敌对": -80, "疏远": -30, "中立": 0, "友好": 40, "信任": 70, "亲密": 90}
            return labels.get(str(value).strip(), 0)

    def compact_context(self) -> dict:
        data = self.get()
        current_relationships = {}
        for item in data["relationships"]:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("from", "")), str(item.get("to", "")))
            if all(key):
                current_relationships[key] = item
        return {
            "locations": [{key: value for key, value in item.items() if key != "history"} for item in data["locations"].values()],
            "factions": [{key: value for key, value in item.items() if key != "history"} for item in data["factions"].values()],
            "items": [{key: value for key, value in item.items() if key != "history"} for item in data["items"].values()],
            "relationships": list(current_relationships.values()),
            "recent_relationships": data["relationships"][-30:],
        }
