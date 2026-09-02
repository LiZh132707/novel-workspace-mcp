"""章节草稿提交前的确定性状态差异预演。"""
from __future__ import annotations

from pathlib import Path

from core.foreshadow_manager import ForeshadowManager
from core.state_card_manager import StateCardManager
from core.story_logic_manager import StoryLogicManager
from storage_utils import StorageManager


class ChapterChangePreview:
    KIND_LABELS = {
        "character": "人物", "location": "地点", "item": "物品",
        "faction": "势力", "relationship": "关系",
    }

    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.root = novel_path
        self.storage = storage or StorageManager(logger)
        self.cards = StateCardManager(novel_path, logger, self.storage)
        self.foreshadows = ForeshadowManager(novel_path, logger, self.storage)
        self.logic = StoryLogicManager(novel_path, logger, self.storage)

    def build(self, chapter: int, summary: dict) -> dict:
        cards = self.cards.get()
        state_changes = self._state_changes(cards, summary)
        foreshadow_changes = self._foreshadow_changes(chapter, summary)
        knowledge_changes = self._knowledge_changes(summary)
        all_changes = state_changes + foreshadow_changes + knowledge_changes
        return {
            "state_changes": state_changes,
            "foreshadow_changes": foreshadow_changes,
            "knowledge_changes": knowledge_changes,
            "totals": {
                "all": len(all_changes),
                "new": len([item for item in all_changes if item.get("action") == "create"]),
                "changed": len([item for item in all_changes if item.get("action") == "change"]),
                "high_risk": len([item for item in all_changes if item.get("risk") == "high"]),
                "medium_risk": len([item for item in all_changes if item.get("risk") == "medium"]),
            },
        }

    def _state_changes(self, cards: dict, summary: dict) -> list[dict]:
        candidates = []
        for item in summary.get("characters_changed", []):
            if isinstance(item, dict) and str(item.get("name", "")).strip():
                candidates.append((
                    "character", str(item["name"]).strip(), str(item.get("field") or "status").strip(),
                    item.get("new_value") or item.get("change", ""), item.get("evidence", ""),
                ))
        for kind, key in (("location", "locations"), ("item", "items"), ("faction", "factions")):
            for item in summary.get(key, []):
                if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                    continue
                for field, value in item.items():
                    if field not in {"name", "evidence", "evidence_verified"} and str(value).strip():
                        candidates.append((kind, str(item["name"]).strip(), str(field), value, item.get("evidence", "")))
        for item in summary.get("relationship_changes", []):
            if not isinstance(item, dict) or not item.get("from") or not item.get("to"):
                continue
            name = f"{item['from']}→{item['to']}"
            candidates.append(("relationship", name, "type", item.get("type", ""), item.get("evidence", "")))
            if str(item.get("strength", "")).strip():
                candidates.append(("relationship", name, "strength", item["strength"], item.get("evidence", "")))
        result = []
        for kind, name, field, raw_after, evidence in candidates:
            after = str(raw_after).strip()[:1000]
            if not after:
                continue
            before = str(cards.get(kind, {}).get(name, {}).get("fields", {}).get(field, ""))
            if before == after:
                continue
            result.append({
                "category": "state", "kind": kind, "kind_label": self.KIND_LABELS[kind],
                "name": name, "field": field, "before": before, "after": after,
                "action": "change" if before else "create",
                "risk": self._risk(kind, field, before, after, str(evidence)), "evidence": str(evidence)[:500],
            })
        return result

    def _foreshadow_changes(self, chapter: int, summary: dict) -> list[dict]:
        open_items = [item for item in self.foreshadows.list(chapter) if item.get("status") == "open"]
        result = []
        for raw in summary.get("foreshadowing", []):
            item = raw if isinstance(raw, dict) else {"action": "introduce", "text": str(raw)}
            if item.get("evidence_verified") is False:
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            action = str(item.get("action") or "introduce")
            match = next((
                value for value in reversed(open_items)
                if text in str(value.get("text", "")) or str(value.get("text", "")) in text
            ), None)
            if action == "resolve":
                result.append({
                    "category": "foreshadow", "kind_label": "伏笔", "name": text,
                    "field": "status", "before": match.get("text", "未找到对应开放伏笔") if match else "未找到对应开放伏笔",
                    "after": "resolved", "action": "change", "risk": "low" if match else "high",
                    "matched_id": match.get("id", "") if match else "",
                })
            elif not match:
                result.append({
                    "category": "foreshadow", "kind_label": "伏笔", "name": text,
                    "field": "status", "before": "", "after": "open", "action": "create", "risk": "low",
                    "target_chapter": self._target_chapter(item.get("target_chapter"), chapter),
                })
        return result

    def _knowledge_changes(self, summary: dict) -> list[dict]:
        knowledge = self.logic.get().get("character_knowledge", {})
        result = []
        for item in summary.get("knowledge_changes", []):
            if not isinstance(item, dict) or not item.get("name") or not item.get("fact"):
                continue
            if item.get("evidence_verified") is False:
                continue
            name, fact = str(item["name"]), str(item["fact"])
            existing = next((
                value for value in knowledge.get(name, [])
                if isinstance(value, dict) and str(value.get("fact", "")) == fact
            ), None)
            after = StoryLogicManager.KNOWLEDGE_STATUS_ALIASES.get(
                str(item.get("status") or item.get("action") or "known").strip().lower(), "known",
            )
            before = str(existing.get("status", "")) if existing else ""
            if before == after:
                continue
            result.append({
                "category": "knowledge", "kind_label": "人物认知", "name": name,
                "field": fact, "before": before, "after": after,
                "action": "change" if before else "create",
                "risk": "medium" if before and before != after else "low",
                "source": str(item.get("source", ""))[:500],
            })
        return result

    @classmethod
    def _risk(cls, kind: str, field: str, before: str, after: str, evidence: str) -> str:
        high_risk_fields = {
            ("character", "current_status"): {"死亡", "复活", "失踪", "失忆", "永久"},
            ("location", "status"): {"摧毁", "永久", "封锁", "消失"},
            ("item", "status"): {"摧毁", "遗失", "消耗", "损坏", "永久"},
            ("faction", "status"): {"摧毁", "解散", "废除", "永久"},
            ("relationship", "type"): {"背叛", "决裂", "敌对", "永久"},
        }
        if any(word in before + after for word in high_risk_fields.get((kind, field), set())):
            return "high"
        if before and before != after:
            return "medium"
        return "low" if evidence.strip() else "medium"

    @staticmethod
    def _target_chapter(value, chapter: int) -> int:
        try:
            return max(chapter + 1, int(value or chapter + 10))
        except (TypeError, ValueError):
            return chapter + 10
