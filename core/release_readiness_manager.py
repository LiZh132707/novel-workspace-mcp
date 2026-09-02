"""小说项目进入自动连载前的确定性验收。"""
from __future__ import annotations

from core.chapter_commit_manager import ChapterCommitManager
from core.character_manager import CharacterManager
from core.ai_contracts import volume_sections_are_valid
from core.review_queue_manager import ReviewQueueManager
from storage_utils import StorageManager


class ReleaseReadinessManager:
    def __init__(self, novel_manager, logger=None, storage: StorageManager | None = None):
        self.nm = novel_manager
        self.root = novel_manager.path
        self.storage = storage or novel_manager.storage or StorageManager(logger)
        self.characters = CharacterManager(self.root, logger)
        self.commits = ChapterCommitManager(self.root, logger, self.storage)

    def run(self) -> dict:
        state = self.nm.get_state()
        current = max(0, self._int(state.get("current_chapter")))
        target = max(0, self._int(state.get("target_chapters")))
        checks = [
            self._file_check("world", "世界与故事前提", "bible/world.md", 20),
            self._file_check("rules", "世界规则", "bible/rules.md", 10),
            self._file_check("outline", "全书总纲", "outline/main.md", 20),
            self._structure_check(target),
            self._character_check(),
            self._canonical_check(current),
            self._stale_turn_check(),
            self._governance_check(),
            self._next_chapter_check(current, target),
        ]
        failures = [item for item in checks if item["status"] == "fail"]
        warnings = [item for item in checks if item["status"] == "warning"]
        score = round(sum(item["score"] for item in checks) / max(1, sum(item["weight"] for item in checks)) * 100)
        status = "blocked" if failures else "warning" if warnings else "ready"
        return {
            "status": status, "score": score, "checks": checks,
            "blocking": [item["detail"] for item in failures],
            "warnings": [item["detail"] for item in warnings],
            "current_chapter": current, "target_chapters": target,
        }

    def _file_check(self, key: str, label: str, relative: str, minimum: int) -> dict:
        path = self.root / relative
        content = path.read_text("utf-8", errors="replace").strip() if path.exists() else ""
        passed = len(content) >= minimum
        return self._check(key, label, "pass" if passed else "fail", 1, f"{label}{'已建立' if passed else '缺失或过短'}")

    def _structure_check(self, target: int) -> dict:
        volumes = self.storage.safe_read_json(self.root / "outline" / "volumes.json", [])
        valid = [item for item in volumes if isinstance(item, dict)] if isinstance(volumes, list) else []
        if not valid:
            return self._check("structure", "分卷结构", "fail", 1, "尚未建立有效分卷结构")
        expected_start = 1
        for index, item in enumerate(valid, 1):
            start = self._int(item.get("start_chapter"))
            end = self._int(item.get("end_chapter"))
            if start != expected_start or end < start:
                return self._check(
                    "structure", "分卷结构", "fail", 0,
                    f"第{index}卷章节范围不连续：应从第{expected_start}章开始，实际为第{start}—{end}章",
                )
            if not volume_sections_are_valid(item):
                return self._check(
                    "structure", "分卷结构", "fail", 0,
                    f"第{index}卷节纲缺失、重叠或未完整覆盖第{start}—{end}章",
                )
            expected_start = end + 1
        covered = expected_start - 1
        if target and covered != target:
            return self._check(
                "structure", "分卷结构", "fail", 0,
                f"分卷连续覆盖到第{covered}章，但目标为{target}章",
            )
        return self._check("structure", "分卷结构", "pass", 1, f"{len(valid)}卷，覆盖到第{covered}章")

    def _character_check(self) -> dict:
        count = len(self.characters.list_characters())
        status = "pass" if count >= 2 else "warning" if count == 1 else "fail"
        score = 1 if count >= 2 else 0.5 if count == 1 else 0
        return self._check("characters", "主要人物", status, score, f"已登记{count}个人物")

    def _canonical_check(self, current: int) -> dict:
        incomplete = []
        for chapter in range(1, current + 1):
            path = self.root / "chapters" / f"{chapter:06d}.txt"
            content = path.read_text("utf-8", errors="replace") if path.exists() else ""
            if not content or not self.commits.is_committed(chapter, content):
                incomplete.append(chapter)
        if incomplete:
            return self._check("canonical", "正史完整性", "fail", 0, f"第{'、'.join(map(str, incomplete[:12]))}章缺失或提交不完整")
        return self._check("canonical", "正史完整性", "pass", 1, f"前{current}章正文、摘要与提交标记一致")

    def _stale_turn_check(self) -> dict:
        data = self.storage.safe_read_json(self.root / "turns" / "index.json", {"items": []})
        items = data.get("items", []) if isinstance(data, dict) else []
        stale = [
            item for item in items if isinstance(item, dict)
            and item.get("status") in {"drafting", "ready", "blocked"}
            and item.get("planning_stale")
        ] if isinstance(items, list) else []
        status = "warning" if stale else "pass"
        return self._check("stale_turns", "陈旧生成回合", status, 0.5 if stale else 1, f"存在{len(stale)}个需按新规划处理的草稿")

    def _next_chapter_check(self, current: int, target: int) -> dict:
        if target and current >= target:
            return self._check("next_chapter", "下一章准备", "pass", 1, "已经达到目标章节数")
        chapter = current + 1
        briefs = self.storage.safe_read_json(self.root / "outline" / "chapter_briefs.json", {})
        scenes = self.storage.safe_read_json(self.root / "outline" / "scene_outlines.json", {})
        has_brief = isinstance(briefs, dict) and isinstance(briefs.get(str(chapter)), dict)
        has_scene = isinstance(scenes, dict) and isinstance(scenes.get(str(chapter)), dict)
        if has_brief and has_scene:
            return self._check("next_chapter", "下一章准备", "pass", 1, f"第{chapter}章提要和场景细纲均已就绪")
        if has_brief or has_scene:
            return self._check("next_chapter", "下一章准备", "warning", 0.5, f"第{chapter}章仅完成部分规划，生成时会自动补齐")
        return self._check("next_chapter", "下一章准备", "warning", 0.5, f"第{chapter}章尚无提要，生成时将先自动推演")

    def _governance_check(self) -> dict:
        queue = ReviewQueueManager(self.root, storage=self.storage).build()
        blocking = [item for item in queue["items"] if item.get("blocking")]
        if blocking:
            labels = "、".join(dict.fromkeys(str(item.get("title", "待处理事项")) for item in blocking[:6]))
            return self._check("governance", "治理审核", "fail", 0, f"存在{len(blocking)}个阻断项：{labels}")
        if queue["total"]:
            return self._check("governance", "治理审核", "warning", 0.5, f"存在{queue['total']}个非阻断审核项")
        return self._check("governance", "治理审核", "pass", 1, "没有阻断自动连载的审核项")

    @staticmethod
    def _check(key: str, label: str, status: str, ratio: float, detail: str) -> dict:
        weight = 1
        return {"key": key, "label": label, "status": status, "detail": detail, "weight": weight, "score": ratio * weight}

    @staticmethod
    def _int(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
