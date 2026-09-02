"""依据人物人格指纹和正文证据检查关键决定。"""
from __future__ import annotations

import re
from pathlib import Path

from storage_utils import StorageManager


class CharacterDecisionValidator:
    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.root = novel_path
        self.characters = novel_path / "characters"
        self.storage = storage or StorageManager(logger)

    def inspect(self, decisions: list[dict]) -> list[dict]:
        issues = []
        for decision in decisions if isinstance(decisions, list) else []:
            if not isinstance(decision, dict) or not str(decision.get("name", "")).strip():
                continue
            name = str(decision["name"]).strip()
            safe_name = name if re.fullmatch(r"[\w\u4e00-\u9fff]+", name) else ""
            profile = self.storage.safe_read_json(self.characters / f"{safe_name}.json", {}) if safe_name else {}
            if safe_name and not isinstance(profile, dict):
                profile = {}
            if safe_name and not profile:
                reviews = self.storage.safe_read_json(self.root / "reviews" / "character_changes.json", {"items": []})
                pending = next((
                    item for item in reviews.get("items", [])
                    if isinstance(item, dict) and item.get("status") == "pending"
                    and item.get("field") == "new_character" and item.get("name") == safe_name
                ), None) if isinstance(reviews, dict) and isinstance(reviews.get("items"), list) else None
                profile = pending.get("details", {}) if isinstance(pending, dict) and isinstance(pending.get("details"), dict) else {}
            fingerprint = profile.get("personality_profile", {}) if isinstance(profile, dict) else {}
            evidence_verified = bool(decision.get("evidence_verified"))
            if not evidence_verified:
                issues.append(self._issue(name, decision, "高", "关键决定缺少可在正文中定位的证据", True))
                continue
            motive = str(decision.get("motive", "")).strip()
            if not motive:
                issues.append(self._issue(name, decision, "中", "关键决定没有记录人物当下动机", False))
            conflict = str(decision.get("conflicts_with", "")).strip()
            exception = str(decision.get("exception_reason", "")).strip()
            if conflict and not exception:
                issues.append(self._issue(name, decision, "高", f"决定可能违背人格指纹“{conflict}”，但没有转变诱因", True))
            if isinstance(fingerprint, dict) and any(str(value).strip() for value in fingerprint.values()):
                basis = "".join((motive, str(decision.get("personality_basis", "")), str(decision.get("action", ""))))
                if self._similarity(basis, "".join(str(value) for value in fingerprint.values())) < 0.025 and not exception:
                    issues.append(self._issue(name, decision, "中", "该决定与已有人格指纹关联较弱，建议补充诱因或修正行为", False))
        return issues

    @staticmethod
    def _issue(name: str, decision: dict, severity: str, message: str, blocking: bool) -> dict:
        return {"name": name, "action": str(decision.get("action", ""))[:500], "severity": severity, "message": message, "blocking": blocking}

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        def terms(value):
            cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", value or "")
            return {cleaned[index:index + 2] for index in range(max(0, len(cleaned) - 1))}
        left_terms, right_terms = terms(left), terms(right)
        return len(left_terms & right_terms) / max(1, len(left_terms))
