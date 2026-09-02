"""人物人格指纹规范化与同质化检测。"""
from __future__ import annotations

import re


PROFILE_FIELDS = (
    "desire", "fear", "principle", "flaw", "stress_response",
    "decision_style", "social_posture", "speech_habits", "contradiction",
)


class PersonalityProfileManager:
    @staticmethod
    def normalize(character: dict) -> dict:
        source = character.get("personality_profile", {}) if isinstance(character.get("personality_profile"), dict) else {}
        result = {}
        for field in PROFILE_FIELDS:
            value = source.get(field) or character.get(field, "")
            result[field] = str(value).strip()[:500]
        if not any(result.values()) and character.get("personality"):
            result["social_posture"] = str(character.get("personality", ""))[:500]
        return result

    @classmethod
    def diversity_report(cls, characters: list[dict]) -> dict:
        profiles = [(str(item.get("name", "")), cls.normalize(item)) for item in characters if isinstance(item, dict)]
        similar = []
        incomplete = []
        for name, profile in profiles:
            filled = len([value for value in profile.values() if str(value).strip()])
            if filled < 4:
                incomplete.append({"name": name, "filled_fields": filled, "required_fields": 4})
        for index, (left_name, left) in enumerate(profiles):
            for right_name, right in profiles[index + 1:]:
                score = cls._similarity(left, right)
                if score >= 0.72:
                    similar.append({"left": left_name, "right": right_name, "similarity": round(score, 2)})
        return {
            "characters": len(profiles), "similar_pairs": similar,
            "incomplete_profiles": incomplete,
            "status": "warning" if similar or incomplete else "distinct",
        }

    @staticmethod
    def _similarity(left: dict, right: dict) -> float:
        left_terms = PersonalityProfileManager._terms(" ".join(left.values()))
        right_terms = PersonalityProfileManager._terms(" ".join(right.values()))
        return len(left_terms & right_terms) / max(1, len(left_terms | right_terms))

    @staticmethod
    def _terms(text: str) -> set[str]:
        cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text or "")
        return {cleaned[index:index + 2] for index in range(max(0, len(cleaned) - 1))}
