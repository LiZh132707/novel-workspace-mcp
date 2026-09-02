"""小说事实账本：记录可验证事实并发现同一属性的冲突。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from storage_utils import StorageManager


class FactManager:
    IMMUTABLE_PREDICATES = ("身份", "本名", "出生", "父亲", "母亲", "种族", "性别", "死亡日期", "世界规则")
    def __init__(self, novel_path: Path, logger, storage: StorageManager | None = None):
        self.path = novel_path / "facts.json"
        self.logger = logger
        self.storage = storage or StorageManager(logger)

    def load(self) -> dict:
        data = self.storage.safe_read_json(self.path, {"facts": [], "conflicts": []})
        if not isinstance(data, dict):
            data = {}
        facts = data.get("facts", [])
        conflicts = data.get("conflicts", [])
        return {
            "facts": [item for item in facts if isinstance(item, dict)] if isinstance(facts, list) else [],
            "conflicts": [item for item in conflicts if isinstance(item, dict)] if isinstance(conflicts, list) else [],
        }

    def add_from_summary(self, chapter: int, facts: list[dict]) -> dict:
        data = self.load()
        added = 0
        for item in facts:
            if not isinstance(item, dict):
                continue
            if item.get("evidence_verified") is False:
                continue
            subject = str(item.get("subject", "")).strip()
            predicate = str(item.get("predicate", "")).strip()
            value = str(item.get("object", "")).strip()
            if not subject or not predicate or not value:
                continue
            confidence = item.get("confidence", 0.8)
            try:
                confidence = max(0.0, min(1.0, float(confidence)))
            except (TypeError, ValueError):
                confidence = 0.8
            previous = [
                fact for fact in data["facts"]
                if fact.get("subject") == subject and fact.get("predicate") == predicate
            ]
            if any(fact.get("object") == value and self._chapter_number(fact.get("chapter")) == chapter for fact in previous):
                continue
            record = {
                "subject": subject, "predicate": predicate, "object": value,
                "chapter": chapter, "confidence": confidence,
                "evidence": str(item.get("evidence", ""))[:500],
                "created_at": datetime.now().isoformat(),
            }
            is_immutable = any(keyword in predicate for keyword in self.IMMUTABLE_PREDICATES)
            if is_immutable and previous and str(previous[-1].get("object", "")) != value:
                data["conflicts"].append({"chapter": chapter, "new": record, "previous": previous[-1], "resolved": False})
            data["facts"].append(record)
            added += 1
        self.storage.atomic_write_json(self.path, data)
        self.logger.info("事实账本更新: 第%d章新增%d条", chapter, added)
        return {"added": added, "conflicts": len([item for item in data["conflicts"] if not item.get("resolved")])}

    def preview_conflicts(self, chapter: int, facts: list[dict]) -> list[dict]:
        existing = self.load()["facts"]
        conflicts = []
        for item in facts if isinstance(facts, list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("evidence_verified") is False:
                continue
            subject = str(item.get("subject", "")).strip()
            predicate = str(item.get("predicate", "")).strip()
            value = str(item.get("object", "")).strip()
            if not subject or not predicate or not value:
                continue
            life_conflict = self._character_life_conflict(subject, predicate, value)
            if life_conflict:
                conflicts.append({
                    "subject": subject, "predicate": predicate,
                    "previous": life_conflict[0], "proposed": value,
                    "previous_chapter": 0,
                    "message": f"{subject}在权威人物名册中为“{life_conflict[0]}”，草稿拟改为“{value}”；生死变化必须明确确认",
                })
                continue
            if not any(keyword in predicate for keyword in self.IMMUTABLE_PREDICATES):
                continue
            previous = [
                fact for fact in existing
                if fact.get("subject") == subject and fact.get("predicate") == predicate
                and self._chapter_number(fact.get("chapter")) != int(chapter)
            ]
            if previous and str(previous[-1].get("object", "")) != value:
                conflicts.append({
                    "subject": subject, "predicate": predicate,
                    "previous": str(previous[-1].get("object", "")), "proposed": value,
                    "previous_chapter": self._chapter_number(previous[-1].get("chapter")),
                    "message": f"{subject}的{predicate}已在第{previous[-1].get('chapter')}章确认为“{previous[-1].get('object')}”，草稿拟改为“{value}”",
                })
        return conflicts

    def _character_life_conflict(self, subject: str, predicate: str, value: str) -> tuple[str, str] | None:
        if not re.fullmatch(r"[\w\u4e00-\u9fff]+", subject):
            return None
        if not any(keyword in predicate for keyword in ("状态", "生死", "存活", "死亡")):
            return None
        character = self.storage.safe_read_json(self.path.parent / "characters" / f"{subject}.json", None)
        if not isinstance(character, dict):
            return None
        previous = str(character.get("current_status", "")).strip()
        previous_class = self._life_state(previous)
        proposed_class = self._life_state(value)
        if previous_class and proposed_class and previous_class != proposed_class:
            return previous, value
        return None

    @staticmethod
    def _life_state(value: str) -> str:
        if re.search(r"死亡|已死|死去|身亡|尸体|死亡确认", value or ""):
            return "dead"
        if re.search(r"存活|活着|生还|仍活", value or ""):
            return "alive"
        return ""

    def recent(self, count: int = 30) -> list[dict]:
        return self.load()["facts"][-count:]

    def unresolved_conflicts(self) -> list[dict]:
        return [item for item in self.load()["conflicts"] if not item.get("resolved")]

    @staticmethod
    def _chapter_number(value) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0
