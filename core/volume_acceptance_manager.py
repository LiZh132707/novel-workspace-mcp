"""卷末目标、人物弧与叙事义务的确定性验收。"""
from __future__ import annotations

import re
import hashlib
from pathlib import Path

from storage_utils import StorageManager


class VolumeAcceptanceManager:
    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.root = novel_path
        self.storage = storage or StorageManager(logger)

    def review_if_due(self, chapter: int) -> dict | None:
        volumes = self.storage.safe_read_json(self.root / "outline" / "volumes.json", [])
        volumes = volumes if isinstance(volumes, list) else []
        volume = next((
            item for item in volumes if isinstance(item, dict)
            and self._int(item.get("end_chapter")) == int(chapter)
        ), None)
        if not volume:
            return None
        start = max(1, self._int(volume.get("start_chapter"), 1))
        end = int(chapter)
        corpus = self._summary_corpus(start, end)
        outcomes = self._planned_outcomes(volume, corpus)
        overdue_foreshadows = self._overdue_foreshadows(start, end)
        overdue_promises = self._overdue_promises(start, end)
        goal_items = [item for item in outcomes if item["kind"] == "volume_goal"]
        required = [item for item in outcomes if item["kind"] in {"volume_goal", "character_change", "foreshadow"}]
        required_coverage = len([item for item in required if item["met"]]) / max(1, len(required))
        goal_met = all(item["met"] for item in goal_items) if goal_items else False
        repair_tasks = [
            {
                "kind": item["kind"], "priority": "高" if item["kind"] == "volume_goal" else "中",
                "description": f"下一卷开始前补齐：{item['text']}", "reason": "卷内摘要未找到足够完成证据",
            }
            for item in required if not item["met"]
        ]
        repair_tasks.extend({
            "kind": "overdue_foreshadow", "priority": "高",
            "description": f"处理已到期伏笔：{item.get('text', '')}",
            "reason": f"目标章节为第{item.get('target_chapter', end)}章，卷末仍开放",
        } for item in overdue_foreshadows)
        repair_tasks.extend({
            "kind": "overdue_promise", "priority": "高",
            "description": f"兑现、延期或取消叙事承诺：{item.get('text', '')}",
            "reason": f"卷内建立的承诺在第{end}章后仍开放",
        } for item in overdue_promises)
        for task in repair_tasks:
            raw = f"{end}|{task.get('kind', '')}|{task.get('description', '')}"
            task["id"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
            task["status"] = "pending"
        status = "likely_complete" if goal_met and required_coverage >= 0.6 and not overdue_foreshadows and not overdue_promises else "needs_review"
        return {
            "volume": str(volume.get("title") or "未命名卷"),
            "start_chapter": start, "end_chapter": end, "status": status,
            "goal_met": goal_met, "required_coverage": round(required_coverage, 3),
            "outcomes": outcomes, "overdue_foreshadows": overdue_foreshadows,
            "overdue_promises": overdue_promises, "repair_tasks": repair_tasks,
        }

    def _summary_corpus(self, start: int, end: int) -> str:
        parts = []
        for chapter in range(start, end + 1):
            summary = self.storage.safe_read_json(self.root / "summaries" / f"{chapter:06d}.json", {})
            if not isinstance(summary, dict):
                continue
            parts.append(str(summary.get("summary", "")))
            for key in ("characters_changed", "foreshadowing", "facts", "relationship_changes"):
                for item in summary.get(key, []) if isinstance(summary.get(key), list) else []:
                    if isinstance(item, dict):
                        parts.append(" ".join(str(value) for value in item.values() if value not in (None, "", [])))
                    else:
                        parts.append(str(item))
            chapter_path = self.root / "chapters" / f"{chapter:06d}.txt"
            if chapter_path.exists():
                parts.append(chapter_path.read_text("utf-8", errors="replace"))
        return " ".join(parts)

    def _planned_outcomes(self, volume: dict, corpus: str) -> list[dict]:
        candidates = [("volume_goal", str(volume.get("goal", "")).strip())]
        for kind, key in (("character_change", "character_changes"), ("foreshadow", "foreshadowing"), ("turning_point", "turning_points")):
            values = volume.get(key, [])
            if isinstance(values, list):
                candidates.extend((kind, str(value).strip()) for value in values if str(value).strip())
        result = []
        for kind, text in candidates:
            if not text:
                continue
            overlap = self._positive_overlap(text, corpus)
            result.append({"kind": kind, "text": text, "evidence_overlap": round(overlap, 3), "met": overlap >= 0.16})
        return result

    def _overdue_foreshadows(self, start: int, end: int) -> list[dict]:
        data = self.storage.safe_read_json(self.root / "foreshadowing.json", {"items": []})
        items = data.get("items", []) if isinstance(data, dict) and isinstance(data.get("items"), list) else []
        return [
            dict(item) for item in items if isinstance(item, dict) and item.get("status") == "open"
            and start <= self._int(item.get("introduced_chapter")) <= end
            and self._int(item.get("target_chapter"), end + 1) <= end
        ]

    def _overdue_promises(self, start: int, end: int) -> list[dict]:
        data = self.storage.safe_read_json(self.root / "tracking" / "story_logic.json", {"promises": []})
        items = data.get("promises", []) if isinstance(data, dict) and isinstance(data.get("promises"), list) else []
        return [
            dict(item) for item in items if isinstance(item, dict) and item.get("status", "open") == "open"
            and start <= self._int(item.get("introduced_chapter")) <= end
            and (not item.get("target_chapter") or self._int(item.get("target_chapter"), end) <= end)
        ]

    @staticmethod
    def _terms(text: str) -> set[str]:
        cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text or "")
        return {cleaned[index:index + 2] for index in range(max(0, len(cleaned) - 1))}

    @classmethod
    def _positive_overlap(cls, target: str, corpus: str) -> float:
        terms = cls._terms(target)
        if not terms:
            return 0.0
        negative = ("尚未", "仍未", "没有", "并未", "未能", "无法", "失败", "尚不")
        best = 0.0
        for segment in re.split(r"[。！？；\n]+", corpus):
            if any(marker in segment for marker in negative) and not any(marker in target for marker in negative):
                continue
            best = max(best, len(terms & cls._terms(segment)) / max(1, len(terms)))
        return best

    @staticmethod
    def _int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
