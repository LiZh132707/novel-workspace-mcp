"""章前/章后偏航检测、章节模式统计和节末复盘。"""
import re
from pathlib import Path
from datetime import datetime

from filelock import FileLock

from storage_utils import StorageManager
from core.volume_acceptance_manager import VolumeAcceptanceManager


class PlanningReviewManager:
    def __init__(self, novel_path: Path, logger, storage: StorageManager | None = None):
        self.novel_path = novel_path
        self.path = novel_path / "reviews" / "planning_reviews.json"
        self.storage = storage or StorageManager(logger)

    @staticmethod
    def _terms(text: str) -> set[str]:
        cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text or "")
        return {cleaned[index:index + 2] for index in range(max(0, len(cleaned) - 1))}

    def review_chapter(self, chapter: int, summary: dict) -> dict:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._review_chapter(chapter, summary)

    def _review_chapter(self, chapter: int, summary: dict) -> dict:
        briefs = self.storage.safe_read_json(self.novel_path / "outline" / "chapter_briefs.json", {})
        briefs = briefs if isinstance(briefs, dict) else {}
        brief = briefs.get(str(chapter), {})
        brief = brief if isinstance(brief, dict) else {}
        expected = " ".join(str(brief.get(key, "")) for key in ("synopsis", "structural_purpose", "exit_state"))
        actual = str(summary.get("summary", ""))
        expected_terms, actual_terms = self._terms(expected), self._terms(actual)
        overlap = len(expected_terms & actual_terms) / max(1, len(expected_terms)) if expected_terms else 1.0
        level = "aligned" if overlap >= 0.22 else "minor" if overlap >= 0.10 else "major"
        item = {
            "chapter": chapter, "level": level, "overlap": round(overlap, 3),
            "expected": expected[:1200], "actual": actual[:1200],
            "suggestion": "保持现有规划" if level == "aligned" else "检查正文产生的新发展，确认是否调整后续提要",
        }
        data = self.storage.safe_read_json(self.path, {"chapters": [], "section_reviews": [], "volume_reviews": []})
        data = data if isinstance(data, dict) else {}
        for key in ("chapters", "section_reviews", "volume_reviews"):
            data[key] = data.get(key, []) if isinstance(data.get(key), list) else []
        auto_resolved = self._verify_repair_tasks(data, chapter, summary)
        data["chapters"] = [entry for entry in data["chapters"] if entry.get("chapter") != chapter]
        data["chapters"].append(item)
        section_review = self._section_review(chapter, summary)
        if section_review:
            data["section_reviews"] = [entry for entry in data["section_reviews"] if entry.get("end_chapter") != chapter]
            data["section_reviews"].append(section_review)
        volume_review = VolumeAcceptanceManager(self.novel_path, storage=self.storage).review_if_due(chapter)
        if volume_review:
            previous = next((entry for entry in data["volume_reviews"] if entry.get("end_chapter") == chapter), None)
            decisions = {
                task.get("id"): {key: task.get(key) for key in ("status", "note", "decided_at") if task.get(key)}
                for task in previous.get("repair_tasks", []) if isinstance(task, dict) and task.get("id")
            } if isinstance(previous, dict) else {}
            for task in volume_review.get("repair_tasks", []):
                if task.get("id") in decisions:
                    task.update(decisions[task["id"]])
            if volume_review.get("repair_tasks") and not any(task.get("status") == "pending" for task in volume_review["repair_tasks"]):
                volume_review["status"] = "accepted_after_review"
            data["volume_reviews"] = [entry for entry in data["volume_reviews"] if entry.get("end_chapter") != chapter]
            data["volume_reviews"].append(volume_review)
        self.storage.atomic_write_json(self.path, data)
        return {
            "chapter_review": item, "section_review": section_review,
            "volume_review": volume_review, "auto_resolved_repairs": auto_resolved,
        }

    def _section_review(self, chapter: int, summary: dict) -> dict | None:
        volumes = self.storage.safe_read_json(self.novel_path / "outline" / "volumes.json", [])
        for volume in volumes if isinstance(volumes, list) else []:
            if not isinstance(volume, dict):
                continue
            for section in volume.get("sections", []) if isinstance(volume.get("sections"), list) else []:
                if not isinstance(section, dict) or self._int(section.get("end_chapter")) != chapter:
                    continue
                outcomes = " ".join(str(value) for value in section.get("required_outcomes", [])) + " " + str(section.get("outcome", ""))
                corpus = self._summary_corpus(self._int(section.get("start_chapter"), chapter), chapter)
                corpus = f"{corpus} {summary.get('summary', '')}"
                overlap = self._positive_evidence_match(outcomes, corpus)[0] if self._terms(outcomes) else 1.0
                return {
                    "volume": volume.get("title", ""), "section": section.get("title", ""),
                    "end_chapter": chapter, "completion_hint": round(overlap, 3),
                    "status": "needs_review" if overlap < 0.18 else "likely_complete",
                    "required_outcomes": section.get("required_outcomes", []),
                    "actual_summary": summary.get("summary", ""),
                }
        return None

    def _summary_corpus(self, start: int, end: int) -> str:
        parts = []
        for number in range(max(1, start), end + 1):
            data = self.storage.safe_read_json(self.novel_path / "summaries" / f"{number:06d}.json", {})
            if isinstance(data, dict):
                parts.append(str(data.get("summary", "")))
            chapter_path = self.novel_path / "chapters" / f"{number:06d}.txt"
            if chapter_path.exists():
                parts.append(chapter_path.read_text("utf-8", errors="replace"))
        return " ".join(parts)

    def report(self) -> dict:
        data = self.storage.safe_read_json(self.path, {"chapters": [], "section_reviews": [], "volume_reviews": []})
        briefs = self.storage.safe_read_json(self.novel_path / "outline" / "chapter_briefs.json", {})
        modes = {}
        for brief in briefs.values() if isinstance(briefs, dict) else []:
            if not isinstance(brief, dict):
                continue
            mode = brief.get("chapter_mode", "unknown")
            modes[mode] = modes.get(mode, 0) + 1
        data = data if isinstance(data, dict) else {}
        for key in ("chapters", "section_reviews", "volume_reviews"):
            data[key] = data.get(key, []) if isinstance(data.get(key), list) else []
        data["chapter_modes"] = modes
        return data

    def pending_volume_repairs(self, next_chapter: int, limit: int = 12) -> list[dict]:
        reviews = self.report().get("volume_reviews", [])
        result = []
        for review in sorted(reviews, key=lambda item: self._int(item.get("end_chapter")) if isinstance(item, dict) else 0, reverse=True):
            if not isinstance(review, dict) or self._int(review.get("end_chapter")) >= int(next_chapter):
                continue
            for task in review.get("repair_tasks", []) if isinstance(review.get("repair_tasks"), list) else []:
                if isinstance(task, dict) and task.get("status", "pending") in {"pending", "deferred"}:
                    result.append({"volume": review.get("volume", ""), "end_chapter": review.get("end_chapter", 0)} | dict(task))
                    if len(result) >= max(1, int(limit)):
                        return result
        return result

    def decide_volume_task(
        self, task_id: str, status: str, note: str = "",
        evidence_chapter: int = 0, evidence_quote: str = "", waive: bool = False,
    ) -> dict:
        if status not in {"pending", "resolved", "deferred"}:
            raise ValueError("卷末修复任务状态无效")
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            data = self.storage.safe_read_json(self.path, {"chapters": [], "section_reviews": [], "volume_reviews": []})
            if not isinstance(data, dict) or not isinstance(data.get("volume_reviews"), list):
                raise ValueError("卷末验收记录损坏")
            for review in data["volume_reviews"]:
                if not isinstance(review, dict):
                    continue
                tasks = review.get("repair_tasks", [])
                if not isinstance(tasks, list):
                    continue
                task = next((item for item in tasks if isinstance(item, dict) and item.get("id") == task_id), None)
                if not task:
                    continue
                decision = {"status": status, "note": str(note)[:500], "decided_at": datetime.now().isoformat()}
                if status == "resolved":
                    if waive:
                        if not str(note).strip():
                            raise ValueError("无正文证据时必须填写人工豁免理由")
                        decision["resolution_mode"] = "waived"
                    else:
                        chapter = self._int(evidence_chapter)
                        quote = str(evidence_quote).strip()
                        if chapter < 1 or not quote:
                            raise ValueError("完成修复任务必须提供正文证据章节和原文；否则请选择人工豁免")
                        content_path = self.novel_path / "chapters" / f"{chapter:06d}.txt"
                        content = content_path.read_text("utf-8", errors="replace") if content_path.exists() else ""
                        if quote not in content:
                            raise ValueError("提供的证据原文不在指定章节中")
                        decision.update({
                            "resolution_mode": "manual_evidence", "evidence_chapter": chapter,
                            "evidence_quote": quote[:500],
                        })
                else:
                    decision.update({"resolution_mode": "", "evidence_chapter": 0, "evidence_quote": ""})
                task.update(decision)
                pending = any(isinstance(item, dict) and item.get("status", "pending") == "pending" for item in tasks)
                review["status"] = "needs_review" if pending else "accepted_after_review"
                self.storage.atomic_write_json(self.path, data)
                return {"task": task, "volume_review": review}
            raise ValueError("卷末修复任务不存在")

    def _verify_repair_tasks(self, data: dict, chapter: int, summary: dict) -> list[dict]:
        evidence = self._summary_evidence(summary)
        chapter_path = self.novel_path / "chapters" / f"{int(chapter):06d}.txt"
        if chapter_path.exists():
            evidence += " " + chapter_path.read_text("utf-8", errors="replace")
        if not self._terms(evidence):
            return []
        resolved = []
        for review in data.get("volume_reviews", []):
            if not isinstance(review, dict) or self._int(review.get("end_chapter")) >= int(chapter):
                continue
            tasks = review.get("repair_tasks", [])
            if not isinstance(tasks, list):
                continue
            for task in tasks:
                if not isinstance(task, dict) or task.get("status", "pending") not in {"pending", "deferred"}:
                    continue
                description = str(task.get("description", ""))
                terms = self._terms(description)
                overlap, excerpt = self._positive_evidence_match(description, evidence)
                if len(terms) < 3 or overlap < 0.22:
                    continue
                task.update({
                    "status": "resolved", "resolution_mode": "automatic_evidence",
                    "evidence_chapter": int(chapter), "evidence_quote": excerpt[:500],
                    "evidence_overlap": round(overlap, 3), "decided_at": datetime.now().isoformat(),
                    "note": "章节摘要与修复目标匹配，已自动找到完成证据",
                })
                resolved.append(dict(task))
            pending = any(
                isinstance(task, dict) and task.get("status", "pending") == "pending"
                for task in tasks
            )
            if tasks and not pending:
                review["status"] = "accepted_after_review"
        return resolved

    @classmethod
    def _positive_evidence_match(cls, target: str, evidence: str) -> tuple[float, str]:
        target_terms = cls._terms(target)
        if not target_terms:
            return 0.0, ""
        negative = ("尚未", "仍未", "没有", "并未", "未能", "无法", "失败", "尚不")
        best, excerpt = 0.0, ""
        for segment in re.split(r"[。！？；\n]+", evidence):
            if any(marker in segment for marker in negative) and not any(marker in target for marker in negative):
                continue
            overlap = len(target_terms & cls._terms(segment)) / max(1, len(target_terms))
            if overlap > best:
                best, excerpt = overlap, segment.strip()
        return best, excerpt

    @staticmethod
    def _summary_evidence(summary: dict) -> str:
        if not isinstance(summary, dict):
            return ""
        parts = [str(summary.get("summary", ""))]
        for key in ("characters_changed", "foreshadowing", "facts", "narrative_promises", "causal_links"):
            for item in summary.get(key, []) if isinstance(summary.get(key), list) else []:
                if isinstance(item, dict):
                    parts.append(" ".join(str(value) for value in item.values() if value not in (None, "", [])))
                else:
                    parts.append(str(item))
        return " ".join(parts)

    @staticmethod
    def _int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
