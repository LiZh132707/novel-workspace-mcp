"""从作者对 AI 草稿的实际修改中学习抽象表达偏好。"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from storage_utils import StorageManager


class AuthorPreferenceManager:
    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.path = novel_path / "bible" / "author_preferences.json"
        self.storage = storage or StorageManager(logger)

    @staticmethod
    def metrics(text: str) -> dict:
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text or "") if item.strip()]
        sentences = [item for item in re.split(r"[。！？!?]+", text or "") if item.strip()]
        dialogue_chars = sum(len(item) for item in re.findall(r"[“\"]([^”\"]+)[”\"]", text or ""))
        total = max(1, len(re.sub(r"\s", "", text or "")))
        return {
            "average_paragraph_length": round(sum(map(len, paragraphs)) / max(1, len(paragraphs)), 1),
            "average_sentence_length": round(sum(map(len, sentences)) / max(1, len(sentences)), 1),
            "dialogue_ratio": round(dialogue_chars / total, 3),
            "paragraphs": len(paragraphs), "total_chars": total,
        }

    def learn(self, chapter: int, original: str, revised: str) -> dict:
        if not original.strip() or not revised.strip() or original == revised:
            return self.get()
        before, after = self.metrics(original), self.metrics(revised)
        data = self.get()
        sample = {"chapter": int(chapter), "before": before, "after": after, "created_at": datetime.now().isoformat()}
        data.setdefault("samples", []).append(sample)
        data["samples"] = data["samples"][-30:]
        recent = data["samples"]
        data["profile"] = {
            "preferred_paragraph_length": round(sum(item["after"]["average_paragraph_length"] for item in recent) / len(recent), 1),
            "preferred_sentence_length": round(sum(item["after"]["average_sentence_length"] for item in recent) / len(recent), 1),
            "preferred_dialogue_ratio": round(sum(item["after"]["dialogue_ratio"] for item in recent) / len(recent), 3),
            "sample_count": len(recent),
        }
        self.storage.atomic_write_json(self.path, data)
        return data

    def get(self) -> dict:
        data = self.storage.safe_read_json(self.path, {"profile": {}, "samples": []})
        data = data if isinstance(data, dict) else {}
        return {
            "profile": data.get("profile", {}) if isinstance(data.get("profile"), dict) else {},
            "samples": data.get("samples", []) if isinstance(data.get("samples"), list) else [],
        }

    def context(self) -> str:
        profile = self.get().get("profile", {})
        if not profile:
            return ""
        return "【作者修改形成的抽象偏好】\n" + "；".join((
            f"段落平均约{profile.get('preferred_paragraph_length')}字",
            f"句子平均约{profile.get('preferred_sentence_length')}字",
            f"对白字符占比约{round(float(profile.get('preferred_dialogue_ratio', 0)) * 100)}%",
            "只学习这些统计偏好，不复用历史章节的具体句子或剧情",
        ))
