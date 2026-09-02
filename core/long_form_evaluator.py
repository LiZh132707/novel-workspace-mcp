"""无需模型调用的长篇一致性基线评测。"""
from __future__ import annotations

from pathlib import Path

from core.ai_contracts import chapter_source_hash
from core.causal_graph_manager import CausalGraphManager
from storage_utils import StorageManager


class LongFormEvaluator:
    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.novel_path = novel_path
        self.storage = storage or StorageManager(logger)

    def run(self) -> dict:
        chapter_files = sorted(
            (path for path in (self.novel_path / "chapters").glob("*.txt") if path.stem.isdigit()),
            key=lambda path: int(path.stem),
        )
        total = len(chapter_files)
        verified_memory = 0
        verified_commits = 0
        repeated_openings = 0
        openings = set()
        commit_data = self.storage.safe_read_json(self.novel_path / "tracking" / "chapter_commits.json", {})
        for path in chapter_files:
            chapter = int(path.stem)
            content = path.read_text("utf-8", errors="replace")
            source_hash = chapter_source_hash(content)
            summary = self.storage.safe_read_json(self.novel_path / "summaries" / f"{chapter:06d}.json", {})
            if summary.get("source_hash") == source_hash and summary.get("handoff"):
                verified_memory += 1
            commit = commit_data.get(str(chapter), {})
            if commit.get("status") == "committed" and commit.get("content_hash") == source_hash:
                verified_commits += 1
            opening = "".join(content.strip().split())[:80]
            if opening and opening in openings:
                repeated_openings += 1
            openings.add(opening)
        planning = self.storage.safe_read_json(self.novel_path / "reviews" / "planning_reviews.json", {"chapters": []})
        planning = planning if isinstance(planning, dict) else {}
        reviewed = planning.get("chapters", []) if isinstance(planning.get("chapters"), list) else []
        reviewed = [item for item in reviewed if isinstance(item, dict)]
        aligned = sum(1 for item in reviewed if item.get("level") == "aligned")
        volume_reviews = planning.get("volume_reviews", []) if isinstance(planning.get("volume_reviews"), list) else []
        volume_reviews = [item for item in volume_reviews if isinstance(item, dict)]
        incomplete_volumes = [item for item in volume_reviews if item.get("status") == "needs_review"]
        state_cards = self.storage.safe_read_json(self.novel_path / "tracking" / "state_cards.json", {})
        state_count = sum(len(value) for value in state_cards.values() if isinstance(value, dict))
        proposals_data = self.storage.safe_read_json(self.novel_path / "tracking" / "state_proposals.json", {"items": []})
        proposals = proposals_data.get("items", []) if isinstance(proposals_data, dict) and isinstance(proposals_data.get("items"), list) else []
        proposals = [item for item in proposals if isinstance(item, dict)]
        pending_high_risk = sum(1 for item in proposals if item.get("status") == "pending" and item.get("risk") == "high")
        facts = self.storage.safe_read_json(self.novel_path / "facts.json", {"conflicts": []})
        conflicts = facts.get("conflicts", []) if isinstance(facts, dict) and isinstance(facts.get("conflicts"), list) else []
        unresolved_fact_conflicts = sum(1 for item in conflicts if isinstance(item, dict) and not item.get("resolved"))
        current_chapter = max((int(path.stem) for path in chapter_files), default=0)
        foreshadow_data = self.storage.safe_read_json(self.novel_path / "foreshadowing.json", {"items": []})
        foreshadows = foreshadow_data.get("items", []) if isinstance(foreshadow_data, dict) and isinstance(foreshadow_data.get("items"), list) else []
        overdue_foreshadows = sum(
            1 for item in foreshadows
            if isinstance(item, dict) and item.get("status") == "open"
            and current_chapter > self._safe_int(item.get("target_chapter"), current_chapter + 1)
        )
        chapter_numbers = {int(path.stem) for path in chapter_files}
        chapter_gaps = [chapter for chapter in range(1, current_chapter + 1) if chapter not in chapter_numbers]
        stuck_revisions = self._stuck_revisions()
        causal_graph = CausalGraphManager(self.novel_path, storage=self.storage).build(current_chapter)
        causal_gaps = causal_graph.get("gaps", [])
        causal_cycles = causal_graph.get("cycles", [])
        causal_late = int(causal_graph.get("stats", {}).get("evidenced_late", 0))
        memory_ratio = verified_memory / max(1, total)
        commit_ratio = verified_commits / max(1, total)
        alignment_ratio = aligned / len(reviewed) if reviewed else 1.0
        state_target = max(3, min(12, total * 2))
        base_score = (
            memory_ratio * 30 + commit_ratio * 20 + alignment_ratio * 20
            + min(1, state_count / state_target) * 10 + 20
        )
        penalty = min(20, unresolved_fact_conflicts * 5) + min(15, pending_high_risk * 3)
        penalty += min(10, overdue_foreshadows * 2) + min(15, len(chapter_gaps) * 3) + min(20, stuck_revisions * 10)
        penalty += min(15, len(incomplete_volumes) * 5)
        penalty += min(15, len(causal_gaps) * 3) + min(6, len(causal_cycles) * 2)
        penalty += min(10, causal_late * 2)
        score = round(max(0, min(100, base_score - penalty))) if total else 0
        issues = []
        if not total:
            issues.append("尚无可评估的数字章节正文")
        if total and memory_ratio < 0.9:
            issues.append(f"仅 {verified_memory}/{total} 章拥有有效连续性交接")
        if total and commit_ratio < 1:
            issues.append(f"仅 {verified_commits}/{total} 章完成正文与派生状态的完整提交")
        if reviewed and alignment_ratio < 0.6:
            issues.append("多数章节与章前提要偏离，需要确认实际进展或调整后续规划")
        if state_count < 5 and total >= 3:
            issues.append("动态状态卡覆盖不足")
        if repeated_openings:
            issues.append(f"发现 {repeated_openings} 个完全重复的章节开头")
        if pending_high_risk:
            issues.append(f"有 {pending_high_risk} 个高风险状态变更等待裁决")
        if unresolved_fact_conflicts:
            issues.append(f"有 {unresolved_fact_conflicts} 个权威事实冲突尚未解决")
        if overdue_foreshadows:
            issues.append(f"有 {overdue_foreshadows} 个伏笔超过目标章节仍未处理")
        if chapter_gaps:
            preview = "、".join(str(chapter) for chapter in chapter_gaps[:12])
            issues.append(f"章节序列存在缺口: {preview}")
        if stuck_revisions:
            issues.append(f"有 {stuck_revisions} 个历史修改事务停留在提交中，需要恢复或重试")
        if incomplete_volumes:
            names = "、".join(str(item.get("volume", "未命名卷")) for item in incomplete_volumes[:6])
            issues.append(f"有 {len(incomplete_volumes)} 卷未通过卷末验收：{names}")
        if causal_gaps:
            issues.append(f"有 {len(causal_gaps)} 个已到期规划结果缺少正史证据")
        if causal_cycles:
            issues.append(f"检测到 {len(causal_cycles)} 个因果环，需要确认是否为合理反馈循环")
        if causal_late:
            issues.append(f"有 {causal_late} 个规划结果在截止章节之后才获得正史证据")
        return {
            "score": score,
            "chapters": total,
            "memory_coverage": round(memory_ratio, 3),
            "commit_coverage": round(commit_ratio, 3),
            "planning_alignment": round(alignment_ratio, 3),
            "state_cards": state_count,
            "repeated_openings": repeated_openings,
            "pending_high_risk_states": pending_high_risk,
            "unresolved_fact_conflicts": unresolved_fact_conflicts,
            "overdue_foreshadows": overdue_foreshadows,
            "chapter_gaps": chapter_gaps,
            "stuck_history_revisions": stuck_revisions,
            "incomplete_volume_reviews": len(incomplete_volumes),
            "causal_gaps": len(causal_gaps),
            "causal_cycles": len(causal_cycles),
            "causal_late_evidence": causal_late,
            "issues": issues,
        }

    def _stuck_revisions(self) -> int:
        count = 0
        revisions = self.novel_path / "history_revisions"
        for path in revisions.glob("*/manifest.json") if revisions.exists() else []:
            manifest = self.storage.safe_read_json(path, {})
            if manifest.get("status") == "committing":
                count += 1
        return count

    @staticmethod
    def _safe_int(value, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
