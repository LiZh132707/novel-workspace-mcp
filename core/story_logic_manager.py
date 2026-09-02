"""持久化叙事承诺、因果关系与人物信息权限。"""
from pathlib import Path

from filelock import FileLock

from storage_utils import StorageManager


class StoryLogicManager:
    KNOWLEDGE_STATUS_ALIASES = {
        "known": "known", "learned": "known", "知道": "known", "获知": "known",
        "believed": "believed", "believe": "believed", "误以为": "believed", "相信": "believed",
        "disproved": "disproved", "revoked": "disproved", "corrected": "disproved",
        "证伪": "disproved", "纠正": "disproved", "不再相信": "disproved",
        "unknown": "unknown", "forbidden": "unknown", "不知道": "unknown", "不应知道": "unknown",
    }

    def __init__(self, novel_path: Path, logger, storage: StorageManager | None = None):
        self.path = novel_path / "tracking" / "story_logic.json"
        self.storage = storage or StorageManager(logger)

    def get(self) -> dict:
        data = self.storage.safe_read_json(self.path, {
            "promises": [], "causal_links": [], "character_knowledge": {},
        })
        if not isinstance(data, dict):
            data = {}
        data.setdefault("promises", [])
        data.setdefault("causal_links", [])
        data.setdefault("character_knowledge", {})
        if not isinstance(data["promises"], list):
            data["promises"] = []
        if not isinstance(data["causal_links"], list):
            data["causal_links"] = []
        if not isinstance(data["character_knowledge"], dict):
            data["character_knowledge"] = {}
        return data

    def ingest(self, chapter: int, summary: dict) -> dict:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._ingest(chapter, summary)

    def _ingest(self, chapter: int, summary: dict) -> dict:
        data = self.get()
        for promise in summary.get("narrative_promises", []):
            if not isinstance(promise, dict) or not str(promise.get("text", "")).strip():
                continue
            if promise.get("evidence_verified") is False:
                continue
            text = str(promise["text"]).strip()
            normalized_promise = {
                key: promise[key] for key in ("text", "status", "target_chapter", "evidence")
                if key in promise and promise[key] not in (None, "")
            }
            existing = next((
                item for item in data["promises"]
                if item.get("text") == text or text in str(item.get("text", "")) or str(item.get("text", "")) in text
            ), None)
            if existing:
                existing.update(normalized_promise)
                existing["updated_chapter"] = chapter
            else:
                data["promises"].append({"text": text, "status": "open", "introduced_chapter": chapter} | normalized_promise)
        for link in summary.get("causal_links", []):
            if (
                isinstance(link, dict) and link.get("cause") and link.get("effect")
                and link.get("evidence_verified") is not False
            ):
                item = {
                    "chapter": chapter, "cause": str(link["cause"]), "effect": str(link["effect"]),
                    "actor": str(link.get("actor", "")), "evidence": str(link.get("evidence", ""))[:500],
                }
                if item not in data["causal_links"]:
                    data["causal_links"].append(item)
        knowledge = data["character_knowledge"]
        for item in summary.get("knowledge_changes", []):
            if not isinstance(item, dict) or not item.get("name") or not item.get("fact"):
                continue
            if item.get("evidence_verified") is False:
                continue
            entries = knowledge.setdefault(str(item["name"]), [])
            fact = str(item["fact"])
            raw_status = str(item.get("status") or item.get("action") or "known").strip().lower()
            status = self.KNOWLEDGE_STATUS_ALIASES.get(raw_status, "known")
            source = str(item.get("source", ""))
            reliability = self._reliability(item.get("source_reliability", item.get("reliability", "unknown")))
            existing = next((entry for entry in entries if entry.get("fact") == fact), None)
            if existing:
                old_status = existing.get("status", "known")
                if old_status != status:
                    existing.setdefault("history", []).append({
                        "status": old_status,
                        "chapter": existing.get("updated_chapter", existing.get("learned_chapter", chapter)),
                        "source": existing.get("source", ""),
                    })
                existing.update({
                    "status": status, "updated_chapter": chapter,
                    "source": source, "source_reliability": reliability,
                    "evidence": str(item.get("evidence", ""))[:500],
                })
                if status in {"known", "believed"} and not existing.get("learned_chapter"):
                    existing["learned_chapter"] = chapter
            else:
                entries.append({
                    "fact": fact, "status": status,
                    "learned_chapter": chapter if status in {"known", "believed"} else None,
                    "updated_chapter": chapter, "source": source,
                    "source_reliability": reliability,
                    "evidence": str(item.get("evidence", ""))[:500], "history": [],
                })
        self.storage.atomic_write_json(self.path, data)
        return {"promises": len(data["promises"]), "causal_links": len(data["causal_links"]), "knowledge_characters": len(knowledge)}

    @staticmethod
    def _reliability(value) -> str | float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            text = str(value or "unknown").strip().lower()
            return text if text in {"high", "medium", "low", "unknown"} else "unknown"

    def context(self, limit: int = 30) -> str:
        import json
        data = self.get()
        knowledge = {}
        for name, entries in data["character_knowledge"].items():
            grouped = {"known": [], "believed": [], "disproved": [], "unknown": []}
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict) or not entry.get("fact"):
                    continue
                status = self.KNOWLEDGE_STATUS_ALIASES.get(str(entry.get("status", "known")).lower(), "known")
                grouped[status].append({
                    "fact": entry["fact"], "source": entry.get("source", ""),
                    "source_reliability": entry.get("source_reliability", "unknown"),
                    "updated_chapter": entry.get("updated_chapter", entry.get("learned_chapter")),
                })
            knowledge[name] = {key: values[-limit:] for key, values in grouped.items() if values}
        compact = {
            "open_promises": [item for item in data["promises"] if item.get("status", "open") == "open"][-limit:],
            "recent_causal_links": data["causal_links"][-limit:],
            "character_knowledge": knowledge,
        }
        return json.dumps(compact, ensure_ascii=False)
