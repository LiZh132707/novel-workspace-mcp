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
        if chapter is None:
            raise ValueError("Chapter is required when recording relationships")
        self._validate_chapter(chapter)
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
        changes = summary.get("relationship_changes", [])
        for item in changes if isinstance(changes, list) else []:
            relation = self._normalize_relationship({**item, "chapter": chapter}) if isinstance(item, dict) else None
            if relation and relation not in data["relationships"]:
                data["relationships"].append(relation)
        self.storage.atomic_write_json(self.path, data)
        return {key: len(value) for key, value in data.items()}

    @staticmethod
    def _strength(value) -> int:
        try:
            return max(-100, min(100, int(float(value))))
        except (TypeError, ValueError, OverflowError):
            labels = {"敌对": -80, "疏远": -30, "中立": 0, "友好": 40, "信任": 70, "亲密": 90}
            return labels.get(str(value).strip(), 0)

    @staticmethod
    def _validate_chapter(chapter):
        if chapter is not None and (isinstance(chapter, bool) or not isinstance(chapter, int) or chapter < 0):
            raise ValueError("Chapter must be a non-negative integer")

    @classmethod
    def _normalize_relationship(cls, item) -> dict | None:
        if not isinstance(item, dict) or item.get("evidence_verified") is False:
            return None
        source, target = item.get("from"), item.get("to")
        if not isinstance(source, str) or not isinstance(target, str):
            return None
        source, target = source.strip(), target.strip()
        if not source or not target or source == target:
            return None
        chapter = item.get("chapter", 0)
        if isinstance(chapter, str) and chapter.isascii() and chapter.isdigit():
            chapter = int(chapter)
        try:
            cls._validate_chapter(chapter)
        except ValueError:
            return None
        if chapter is None:
            return None
        relation = {
            "from": source, "to": target, "type": str(item.get("type") or "Unknown").strip() or "Unknown",
            "strength": cls._strength(item.get("strength", 0)), "chapter": chapter,
            "evidence": str(item.get("evidence") or ""),
        }
        if item.get("evidence_verified") is True:
            relation["evidence_verified"] = True
        return relation

    def relationship_snapshot(self, chapter: int | None = None, *, data: dict | None = None) -> dict:
        """Directed relationships as of a chapter, with chronological evidence history.

        Equal-chapter records use their persisted order as the tie breaker.
        This is an observation ledger, not a replacement for author review.
        """
        self._validate_chapter(chapter)
        data = self.get() if data is None else data
        history = []
        for raw in data["relationships"]:
            item = self._normalize_relationship(raw)
            if item and (chapter is None or item["chapter"] <= chapter):
                history.append(item)
        history.sort(key=lambda item: item["chapter"])
        current_relationships = {}
        for item in history:
            current_relationships[(item["from"], item["to"])] = item
        return {"relationships": list(current_relationships.values()), "history": history}

    def compact_context(self) -> dict:
        data = self.get()
        snapshot = self.relationship_snapshot(data=data)
        return {
            "locations": [{key: value for key, value in item.items() if key != "history"} for item in data["locations"].values()],
            "factions": [{key: value for key, value in item.items() if key != "history"} for item in data["factions"].values()],
            "items": [{key: value for key, value in item.items() if key != "history"} for item in data["items"].values()],
            "relationships": snapshot["relationships"],
            "recent_relationships": snapshot["history"][-30:],
        }
