"""聚合所有需要人工关注的创作事项，不触发模型调用。"""
from __future__ import annotations

from pathlib import Path

from core.canonical_state_manager import CanonicalStateManager
from core.change_review_manager import ChangeReviewManager
from core.fact_manager import FactManager
from core.planning_impact_manager import PlanningImpactManager
from core.quality_tracker import QualityTracker
from storage_utils import StorageManager


class ReviewQueueManager:
    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.root = novel_path
        self.logger = logger
        self.storage = storage or StorageManager(logger)

    def build(self) -> dict:
        items = []
        for item in ChangeReviewManager(self.root, self.logger, self.storage).list("pending"):
            items.append(self._item("character_change", item.get("id"), item.get("chapter"), "人物变化待确认", item.get("change") or item.get("new_value"), "中", False, "characters"))
        for item in CanonicalStateManager(self.root, self.logger, self.storage).list("pending"):
            severity = "高" if item.get("risk") == "high" else "中"
            items.append(self._item("state_proposal", item.get("id"), item.get("chapter"), "权威状态待裁决", f"{item.get('name')} / {item.get('field')}：{item.get('previous')} → {item.get('value')}", severity, severity == "高", "dashboard"))
        for item in QualityTracker(self.root, self.logger, self.storage).get_pending_debts():
            severity = str(item.get("severity") or "中")
            items.append(self._item("quality_debt", item.get("id"), item.get("chapter"), "质量债务", item.get("description"), severity, severity in {"高", "严重"}, "dashboard"))
        for item in FactManager(self.root, self.logger, self.storage).unresolved_conflicts():
            previous = item.get("previous", {}) if isinstance(item.get("previous"), dict) else {}
            proposed = item.get("new", {}) if isinstance(item.get("new"), dict) else {}
            detail = item.get("message") or (
                f"{proposed.get('subject', previous.get('subject', ''))} / {proposed.get('predicate', previous.get('predicate', ''))}："
                f"{previous.get('object', '未知')} → {proposed.get('object', '未知')}"
            )
            items.append(self._item("fact_conflict", item.get("id") or f"fact-{len(items)}", item.get("chapter") or proposed.get("chapter"), "事实冲突", detail, "高", True, "timeline"))
        for item in PlanningImpactManager(self.root, self.logger, self.storage).list():
            if item.get("status") == "pending":
                chapters = [self._int(chapter) for chapter in item.get("chapters", []) if self._int(chapter) > 0] if isinstance(item.get("chapters"), list) else []
                items.append(self._item("planning_impact", item.get("id"), min(chapters) if chapters else 0, "规划传播影响", item.get("message"), "中", False, "dashboard"))
        revisions = []
        for path in sorted((self.root / "history_revisions").glob("*/manifest.json")) if (self.root / "history_revisions").exists() else []:
            item = self.storage.safe_read_json(path, {})
            if isinstance(item, dict):
                revisions.append(item)
        for item in revisions:
            if item.get("status") not in {"committed", "aborted"}:
                items.append(self._item("history_revision", item.get("id"), item.get("source_chapter"), "历史修改未完成", f"{item.get('old_fact')} → {item.get('new_fact')}", "高", True, "dashboard"))
        self._append_turns(items)
        self._append_clock(items)
        order = {"高": 0, "严重": 0, "中": 1, "低": 2}
        items.sort(key=lambda item: (order.get(item["severity"], 2), int(item.get("chapter", 0) or 0)))
        return {"items": items, "total": len(items), "blocking": sum(1 for item in items if item["blocking"]), "by_type": self._counts(items)}

    def _append_turns(self, items: list[dict]):
        data = self.storage.safe_read_json(self.root / "turns" / "index.json", {"items": []})
        turns = data.get("items", []) if isinstance(data, dict) else []
        for turn in turns if isinstance(turns, list) else []:
            if not isinstance(turn, dict) or turn.get("status") not in {"drafting", "ready", "blocked"}:
                continue
            chapter, preview = self._int(turn.get("chapter")), turn.get("preview", {}) if isinstance(turn.get("preview"), dict) else {}
            if turn.get("status") == "blocked":
                items.append(self._item("chapter_turn", turn.get("id"), chapter, "章节草稿被质量闸门阻止", "请修改正文或人工确认例外", "高", True, "write"))
            if turn.get("planning_stale"):
                items.append(self._item("stale_planning", turn.get("id"), chapter, "章节草稿规划已过期", "上游规划改变后尚未重新生成", "高", True, "write"))
            if preview.get("analysis_degraded"):
                items.append(self._item(
                    "degraded_summary", turn.get("id"), chapter, "结构化摘要失败",
                    preview.get("analysis_error") or "人物、事实和时空检查不完整", "高", True, "write",
                ))
            for kind, title in (("canonical_lock_conflicts", "设定锁冲突"), ("story_clock_issues", "故事时空冲突"), ("character_decision_issues", "人物决策异常")):
                for issue in preview.get(kind, []) if isinstance(preview.get(kind), list) else []:
                    items.append(self._item(kind, f"{turn.get('id')}:{len(items)}", chapter, title, issue.get("message"), issue.get("severity", "高"), bool(issue.get("blocking", True)), "write"))

    def _append_clock(self, items: list[dict]):
        data = self.storage.safe_read_json(self.root / "tracking" / "story_clock.json", {"events": []})
        turns_data = self.storage.safe_read_json(self.root / "turns" / "index.json", {"items": []})
        approved_chapters = {
            self._int(turn.get("chapter")) for turn in turns_data.get("items", [])
            if isinstance(turn, dict) and turn.get("status") == "committed"
            and isinstance(turn.get("commit_approvals"), dict)
            and turn["commit_approvals"].get("story_clock_conflicts")
        } if isinstance(turns_data, dict) and isinstance(turns_data.get("items"), list) else set()
        for event in data.get("events", []) if isinstance(data, dict) and isinstance(data.get("events"), list) else []:
            if self._int(event.get("chapter")) in approved_chapters:
                continue
            for issue in event.get("issues", []) if isinstance(event.get("issues"), list) else []:
                items.append(self._item("story_clock", f"clock-{event.get('chapter')}-{len(items)}", event.get("chapter"), "已提交章节存在时空警告", issue.get("message"), issue.get("severity", "中"), bool(issue.get("blocking")), "timeline"))

    @staticmethod
    def _item(kind, item_id, chapter, title, detail, severity, blocking, target):
        return {"type": kind, "id": str(item_id or ""), "chapter": ReviewQueueManager._int(chapter), "title": str(title), "detail": str(detail or ""), "severity": str(severity), "blocking": bool(blocking), "target": target}

    @staticmethod
    def _counts(items: list[dict]) -> dict:
        result = {}
        for item in items:
            result[item["type"]] = result.get(item["type"], 0) + 1
        return result

    @staticmethod
    def _int(value) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0
