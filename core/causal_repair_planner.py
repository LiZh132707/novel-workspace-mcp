"""把已到期的因果缺口转换为可预览、可原子应用的未来章节提案。"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path

from filelock import FileLock

from core.causal_graph_manager import CausalGraphManager
from core.mutation_transaction import NovelMutationTransaction
from core.planning_impact_manager import PlanningImpactManager
from storage_utils import StorageManager


class CausalRepairPlanner:
    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.root = novel_path
        self.logger = logger
        self.storage = storage or StorageManager(logger)
        self.path = novel_path / "planning" / "causal_repairs.json"
        self.lock_path = novel_path / ".causal_repairs.lock"

    def propose(self, window: int = 3) -> dict:
        current = self._current_chapter()
        gaps = CausalGraphManager(self.root, self.logger, self.storage).build(current).get("gaps", [])
        unique_gaps = []
        seen = set()
        for gap in gaps:
            signature = (str(gap.get("text", "")).strip(), int(gap.get("deadline", 0) or 0))
            if not signature[0] or signature in seen:
                continue
            seen.add(signature)
            unique_gaps.append(gap)
        gaps = unique_gaps
        if not gaps:
            raise ValueError("当前没有已到期且缺少正史证据的规划结果")
        window = max(1, min(10, int(window or 3)))
        chapters = list(range(current + 1, current + window + 1))
        briefs = self._briefs()
        chapter_plans = self.storage.safe_read_json(self.root / "outline" / "chapter_plans.json", {})
        chapter_plans = chapter_plans if isinstance(chapter_plans, dict) else {}
        scene_outlines = self.storage.safe_read_json(self.root / "outline" / "scene_outlines.json", {})
        scene_outlines = scene_outlines if isinstance(scene_outlines, dict) else {}
        opening = self.storage.safe_read_json(self.root / "outline" / "opening_chapters.json", {})
        opening_chapters = {
            int(item.get("chapter", 0)) for item in opening.get("chapters", [])
            if isinstance(item, dict) and str(item.get("chapter", "")).isdigit()
        } if isinstance(opening, dict) and isinstance(opening.get("chapters"), list) else set()
        assignments: dict[int, list[dict]] = {chapter: [] for chapter in chapters}
        for index, gap in enumerate(gaps[:30]):
            assignments[chapters[index % len(chapters)]].append(dict(gap))
        patches = []
        for chapter, assigned in assignments.items():
            if not assigned:
                continue
            before = briefs.get(str(chapter), {})
            after = self._merge_brief(chapter, before, assigned)
            invalidations = []
            if str(chapter) in chapter_plans:
                invalidations.append("旧章节计划缓存将失效")
            scene = scene_outlines.get(str(chapter))
            if isinstance(scene, dict) and scene.get("status") == "confirmed":
                invalidations.append("人工确认的场景细纲将保留，但需复核是否容纳新约束")
            elif scene is not None:
                invalidations.append("未确认的场景细纲将失效")
            if chapter in opening_chapters:
                invalidations.append("该章开篇规划缓存将失效，以修复提要为准")
            patches.append({
                "chapter": chapter, "gap_ids": [item.get("id", "") for item in assigned],
                "gaps": assigned, "before": before, "after": after, "invalidations": invalidations,
            })
        proposal = {
            "id": uuid.uuid4().hex, "status": "proposed", "created_at": datetime.now().isoformat(),
            "source_current_chapter": current, "source_briefs_hash": self._hash(briefs),
            "window": window, "patches": patches,
        }
        with FileLock(str(self.lock_path), timeout=30):
            data = self._load()
            data["items"].append(proposal)
            data["items"] = self._prune_items(data["items"])
            self.storage.atomic_write_json(self.path, data)
        return proposal

    def apply(self, proposal_id: str) -> dict:
        self._validate_id(proposal_id)
        with FileLock(str(self.root / ".novel_mutation.lock"), timeout=600), NovelMutationTransaction(
            self.root, [], directories=("outline", "planning", "turns"), files=(),
        ):
            with FileLock(str(self.lock_path), timeout=30):
                data = self._load()
                proposal = next((item for item in data["items"] if item.get("id") == proposal_id), None)
                if not proposal:
                    raise ValueError("因果修复提案不存在")
                if proposal.get("status") == "applied":
                    return proposal
                if proposal.get("status") != "proposed":
                    raise ValueError("因果修复提案当前不能应用")
                current = self._current_chapter()
                if current != int(proposal.get("source_current_chapter", -1)):
                    raise ValueError("正史章节已经推进，请重新生成因果修复提案")
                old_briefs = self._briefs()
                if self._hash(old_briefs) != proposal.get("source_briefs_hash"):
                    raise ValueError("未来章节提要已经变化，请重新生成因果修复提案")
                new_briefs = dict(old_briefs)
                for patch in proposal.get("patches", []):
                    if not isinstance(patch, dict):
                        continue
                    chapter = int(patch.get("chapter", 0))
                    if chapter <= current:
                        raise ValueError("修复提案试图修改已发生章节")
                    new_briefs[str(chapter)] = dict(patch.get("after", {}))
                self.storage.atomic_write_json(self.root / "outline" / "chapter_briefs.json", new_briefs)
                impact = PlanningImpactManager(self.root, self.logger, self.storage).record_changes(
                    [], [], old_briefs, new_briefs, current,
                )
                proposal.update({
                    "status": "applied", "applied_at": datetime.now().isoformat(),
                    "planning_impact": impact,
                })
                self.storage.atomic_write_json(self.path, data)
                return proposal

    def get(self, proposal_id: str) -> dict:
        self._validate_id(proposal_id)
        item = next((value for value in self._load()["items"] if value.get("id") == proposal_id), None)
        if not item:
            raise ValueError("因果修复提案不存在")
        return item

    def _merge_brief(self, chapter: int, before, gaps: list[dict]) -> dict:
        existing = dict(before) if isinstance(before, dict) else {}
        labels = [str(item.get("text", "")).strip() for item in gaps if str(item.get("text", "")).strip()]
        constraints = [f"为规划结果“{label}”补充可验证的原因、行动与结果；若规划已不成立，必须明确产生调整依据" for label in labels]
        must_happen = [str(value) for value in existing.get("must_happen", []) if str(value).strip()] if isinstance(existing.get("must_happen"), list) else []
        for value in constraints:
            if value not in must_happen:
                must_happen.append(value)
        purpose = str(existing.get("structural_purpose", "")).strip()
        addition = "处理前序到期但缺少正史证据的规划结果：" + "；".join(labels)
        if addition not in purpose:
            purpose = (purpose + "；" + addition).strip("；")
        synopsis = str(existing.get("synopsis", "")).strip()
        if len(synopsis) < 30:
            synopsis = f"承接当前真实局势，通过具体行动、阻力和选择补足“{'；'.join(labels)}”的因果证据；不得强行倒叙补丁，结尾必须形成可验证的新结果。"
        return {
            **existing, "chapter": chapter, "title": str(existing.get("title") or "因果补强"),
            "chapter_mode": str(existing.get("chapter_mode") or "main_progress"),
            "synopsis": synopsis, "structural_purpose": purpose,
            "side_value": str(existing.get("side_value", "")),
            "entry_state": str(existing.get("entry_state", "")), "exit_state": str(existing.get("exit_state", "")),
            "must_happen": must_happen,
            "must_not_happen": existing.get("must_not_happen", []) if isinstance(existing.get("must_not_happen"), list) else [],
            "characters": existing.get("characters", []) if isinstance(existing.get("characters"), list) else [],
            "foreshadowing": existing.get("foreshadowing", []) if isinstance(existing.get("foreshadowing"), list) else [],
            "causal_repairs": [item.get("id", "") for item in gaps],
        }

    def _briefs(self) -> dict:
        data = self.storage.safe_read_json(self.root / "outline" / "chapter_briefs.json", {})
        return data if isinstance(data, dict) else {}

    def _current_chapter(self) -> int:
        state = self.storage.safe_read_json(self.root / "state.json", {})
        try:
            return max(0, int(state.get("current_chapter", 0))) if isinstance(state, dict) else 0
        except (TypeError, ValueError):
            return 0

    def _load(self) -> dict:
        data = self.storage.safe_read_json(self.path, {"items": []})
        items = data.get("items", []) if isinstance(data, dict) else []
        return {"items": [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []}

    @staticmethod
    def _prune_items(items: list[dict], terminal_limit: int = 50) -> list[dict]:
        terminal = [item for item in items if item.get("status") != "proposed"]
        dropped = {id(item) for item in terminal[:-terminal_limit]} if len(terminal) > terminal_limit else set()
        return [item for item in items if id(item) not in dropped]

    @staticmethod
    def _hash(value: dict) -> str:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _validate_id(value: str):
        if not value or len(value) > 64 or not value.isalnum():
            raise ValueError("因果修复提案ID无效")
