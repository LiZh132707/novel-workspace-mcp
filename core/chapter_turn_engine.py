"""章节回合引擎：草稿、检查、提交与废弃统一状态机。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Callable

from filelock import FileLock

from core.ai_contracts import chapter_quality_gate, chapter_source_hash
from core.chapter_manager import ChapterManager, count_chapter_words
from core.chapter_change_preview import ChapterChangePreview
from core.fact_manager import FactManager
from core.canonical_lock_manager import CanonicalLockManager
from core.character_decision_validator import CharacterDecisionValidator
from core.story_clock_manager import StoryClockManager
from storage_utils import StorageManager


class ChapterTurnEngine:
    OPEN_STATUSES = {"drafting", "ready", "blocked"}
    FINAL_STATUSES = {"committed", "discarded", "superseded"}

    def __init__(self, novel_manager, logger=None, chapter_manager: ChapterManager | None = None,
                 storage: StorageManager | None = None, post_commit_hooks: list[Callable] | None = None):
        self.nm = novel_manager
        self.root = novel_manager.path / "turns"
        self.index_path = self.root / "index.json"
        self.draft_dir = self.root / "drafts"
        self.commit_lock_path = self.root / ".commit.lock"
        self.storage = storage or novel_manager.storage or StorageManager(logger)
        self.chapter_manager = chapter_manager or ChapterManager(novel_manager, logger)
        self.logger = logger
        self.post_commit_hooks = list(post_commit_hooks or [])

    def save_draft(
        self, chapter: int, content: str, target_words: int = 5000,
        source: str = "manual", metadata: dict | None = None, reuse_open: bool = True,
    ) -> dict:
        chapter = int(chapter)
        if chapter < 1:
            raise ValueError("章节号必须为正整数")
        if not content or not content.strip():
            raise ValueError("草稿正文不能为空")
        target_words = max(500, min(20000, int(target_words or 5000)))
        with FileLock(str(self.index_path) + ".lock", timeout=30):
            data = self._load_index()
            turn = next((
                item for item in reversed(data["items"])
                if item.get("chapter") == chapter and item.get("status") in self.OPEN_STATUSES
            ), None) if reuse_open else None
            now = datetime.now().isoformat()
            if turn is None:
                turn = {
                    "id": uuid.uuid4().hex, "chapter": chapter, "status": "drafting",
                    "source": str(source or "manual")[:40], "created_at": now,
                }
                data["items"].append(turn)
            previous_hash = str(turn.get("content_hash", ""))
            draft_path = self.draft_dir / f"{turn['id']}.txt"
            self.storage.atomic_write_text(draft_path, content)
            quality = chapter_quality_gate(content, target_words)
            content_hash = chapter_source_hash(content)
            if previous_hash and previous_hash != content_hash:
                for key in (
                    "preview", "preview_summary", "previewed_at", "planning_stale",
                    "planning_impact_id", "planning_stale_at",
                ):
                    turn.pop(key, None)
            turn.update({
                "status": "blocked" if quality["status"] == "FAIL" else "ready",
                "source": str(source or turn.get("source") or "manual")[:40],
                "target_words": target_words, "words": count_chapter_words(content),
                "content_hash": content_hash, "quality": quality,
                "metadata": metadata or turn.get("metadata", {}), "updated_at": now,
            })
            effective_metadata = turn.get("metadata", {}) if isinstance(turn.get("metadata"), dict) else {}
            if "planning_stale" in effective_metadata and not effective_metadata.get("planning_stale"):
                for key in ("planning_stale", "planning_impact_id", "planning_stale_at"):
                    turn.pop(key, None)
            if effective_metadata.get("planning_stale"):
                turn.update({
                    "planning_stale": True,
                    "planning_impact_id": str(effective_metadata.get("planning_fingerprint", "runtime-change")),
                    "planning_stale_at": now,
                })
            if "planning_epoch" in effective_metadata:
                epoch_data = self.storage.safe_read_json(self.nm.path / "planning" / "epoch.json", {})
                current_epoch = str(epoch_data.get("id", "")) if isinstance(epoch_data, dict) else ""
                if str(effective_metadata.get("planning_epoch", "")) != current_epoch:
                    turn.update({
                        "planning_stale": True, "planning_impact_id": current_epoch,
                        "planning_stale_at": now,
                    })
            self._save_index(data)
            return dict(turn)

    def inspect(self, turn_id: str) -> dict:
        turn = self.get(turn_id)
        content = self.read_draft(turn_id)
        quality = chapter_quality_gate(content, int(turn.get("target_words", 5000)))
        current = int(self.nm.get_current_chapter())
        issues = []
        if int(turn["chapter"]) > current + 1:
            issues.append({"severity": "高", "message": f"正史当前到第{current}章，不能跳到第{turn['chapter']}章提交"})
        if turn.get("planning_stale"):
            issues.append({"severity": "高", "message": "该草稿生成后上游规划已经变化，需重新生成或明确确认沿用旧草稿"})
        if isinstance(turn.get("preview_summary"), dict) and turn["preview_summary"].get("analysis_degraded"):
            issues.append({"severity": "高", "message": "结构化摘要生成失败，人物、事实和时空检查不完整；建议重新预览"})
        existing = self.chapter_manager.read_chapter(int(turn["chapter"]))
        if existing and chapter_source_hash(existing) != chapter_source_hash(content):
            issues.append({"severity": "中", "message": "该章节已有正史正文，提交会创建覆盖前版本并重建后续状态"})
        preview = turn.get("preview", {}) if isinstance(turn.get("preview"), dict) else {}
        for conflict in preview.get("fact_conflicts", []) if isinstance(preview.get("fact_conflicts"), list) else []:
            issues.append({"severity": "中", "message": "硬事实变化待确认：" + str(conflict.get("message", ""))})
        for conflict in preview.get("state_change_conflicts", []) if isinstance(preview.get("state_change_conflicts"), list) else []:
            issues.append({"severity": "中", "message": "高风险状态变化待确认：" + str(conflict.get("message", ""))})
        for conflict in preview.get("canonical_lock_conflicts", []) if isinstance(preview.get("canonical_lock_conflicts"), list) else []:
            issues.append({"severity": "高", "message": "权威设定锁冲突：" + str(conflict.get("message", ""))})
        for issue in preview.get("story_clock_issues", []) if isinstance(preview.get("story_clock_issues"), list) else []:
            issues.append({"severity": str(issue.get("severity", "中")), "message": "故事时空：" + str(issue.get("message", ""))})
        for issue in preview.get("character_decision_issues", []) if isinstance(preview.get("character_decision_issues"), list) else []:
            issues.append({"severity": str(issue.get("severity", "中")), "message": "人物决策：" + str(issue.get("message", ""))})
        fact_conflicts = preview.get("fact_conflicts", []) if isinstance(preview.get("fact_conflicts"), list) else []
        state_change_conflicts = preview.get("state_change_conflicts", []) if isinstance(preview.get("state_change_conflicts"), list) else []
        lock_conflicts = preview.get("canonical_lock_conflicts", []) if isinstance(preview.get("canonical_lock_conflicts"), list) else []
        clock_blocking = any(item.get("blocking") for item in preview.get("story_clock_issues", []) if isinstance(item, dict))
        decision_blocking = any(item.get("blocking") for item in preview.get("character_decision_issues", []) if isinstance(item, dict))
        return {
            "turn": turn, "quality": quality, "issues": issues,
            "committable": not any(item["severity"] == "高" for item in issues),
            "requires_fact_confirmation": bool(fact_conflicts or state_change_conflicts),
            "requires_lock_confirmation": bool(lock_conflicts),
            "requires_clock_confirmation": clock_blocking,
            "requires_decision_confirmation": decision_blocking,
            "requires_plan_confirmation": bool(turn.get("planning_stale")),
            "requires_summary_confirmation": bool(
                isinstance(turn.get("preview_summary"), dict) and turn["preview_summary"].get("analysis_degraded")
            ),
        }

    def preview_changes(self, turn_id: str) -> dict:
        turn = self.get(turn_id)
        content = self.read_draft(turn_id)
        chapter = int(turn["chapter"])
        summary = self.chapter_manager.summary_mgr._llm_summary(chapter, content) if self.chapter_manager.summary_mgr.llm else self.chapter_manager.summary_mgr._basic_summary(chapter, content)
        known_characters = [
            path.stem for path in (self.nm.path / "characters").glob("*.json")
            if path.stem in content
        ]
        preview = {
            "chapter": chapter,
            "operation": "overwrite" if self.chapter_manager.read_chapter(chapter) else "create",
            "summary": summary.get("summary", ""),
            "characters_mentioned": known_characters,
            "character_changes": summary.get("characters_changed", []),
            "new_characters": summary.get("new_characters", []),
            "facts": summary.get("facts", []),
            "foreshadowing": summary.get("foreshadowing", []),
            "knowledge_changes": summary.get("knowledge_changes", []),
            "locations": summary.get("locations", []),
            "factions": summary.get("factions", []),
            "items": summary.get("items", []),
            "relationship_changes": summary.get("relationship_changes", []),
            "character_decisions": summary.get("character_decisions", []),
            "world_rule_changes": summary.get("world_rule_changes", []),
            "next_goal": summary.get("next_goal", ""),
            "source_hash": summary.get("source_hash", ""),
            "analysis_degraded": bool(summary.get("analysis_degraded")),
            "analysis_error": str(summary.get("analysis_error", "")),
        }
        preview["fact_conflicts"] = FactManager(
            self.nm.path, self.logger, self.storage,
        ).preview_conflicts(chapter, preview["facts"])
        preview["state_diff"] = ChapterChangePreview(
            self.nm.path, self.logger, self.storage,
        ).build(chapter, summary)
        preview["canonical_lock_conflicts"] = CanonicalLockManager(
            self.nm.path, self.logger, self.storage,
        ).conflicts(summary)
        preview["state_change_conflicts"] = self._high_risk_state_changes(
            preview["state_diff"], preview["canonical_lock_conflicts"],
        )
        preview["story_clock_issues"] = StoryClockManager(
            self.nm.path, self.logger, self.storage,
        ).preview(chapter, summary)
        preview["character_decision_issues"] = CharacterDecisionValidator(
            self.nm.path, self.logger, self.storage,
        ).inspect(preview["character_decisions"])
        with FileLock(str(self.index_path) + ".lock", timeout=30):
            data = self._load_index()
            stored = self._find(data, turn_id)
            stored.update({"preview": preview, "preview_summary": summary, "previewed_at": datetime.now().isoformat()})
            self._save_index(data)
        return preview

    def _refresh_deterministic_preview(self, turn: dict, summary: dict) -> dict:
        preview = dict(turn.get("preview", {})) if isinstance(turn.get("preview"), dict) else {}
        chapter = int(turn["chapter"])
        preview["fact_conflicts"] = FactManager(
            self.nm.path, self.logger, self.storage,
        ).preview_conflicts(chapter, summary.get("facts", []))
        preview["state_diff"] = ChapterChangePreview(
            self.nm.path, self.logger, self.storage,
        ).build(chapter, summary)
        preview["canonical_lock_conflicts"] = CanonicalLockManager(
            self.nm.path, self.logger, self.storage,
        ).conflicts(summary)
        preview["state_change_conflicts"] = self._high_risk_state_changes(
            preview["state_diff"], preview["canonical_lock_conflicts"],
        )
        preview["story_clock_issues"] = StoryClockManager(
            self.nm.path, self.logger, self.storage,
        ).preview(chapter, summary)
        preview["character_decisions"] = summary.get("character_decisions", [])
        preview["character_decision_issues"] = CharacterDecisionValidator(
            self.nm.path, self.logger, self.storage,
        ).inspect(preview["character_decisions"])
        return preview

    @staticmethod
    def _high_risk_state_changes(state_diff: dict, lock_conflicts: list[dict] | None = None) -> list[dict]:
        locked_keys = {
            (item.get("kind"), item.get("name"), item.get("field"))
            for item in (lock_conflicts or []) if isinstance(item, dict)
        }
        conflicts = []
        for key in ("state_changes", "foreshadow_changes", "knowledge_changes"):
            for item in state_diff.get(key, []) if isinstance(state_diff.get(key), list) else []:
                item_key = (item.get("kind"), item.get("name"), item.get("field")) if isinstance(item, dict) else ()
                if isinstance(item, dict) and item.get("risk") == "high" and item_key not in locked_keys:
                    conflicts.append({
                        **item,
                        "message": f"{item.get('kind_label', '状态')}“{item.get('name', '')}”的{item.get('field', '状态')}将从“{item.get('before', '未记录')}”改为“{item.get('after', '')}”",
                    })
        return conflicts

    def commit(self, turn_id: str, index_callback: Callable[[int, str], None] | None = None,
               allow_quality_failure: bool = False, allow_fact_conflicts: bool = False,
               allow_stale_planning: bool = False, allow_locked_changes: bool = False,
               allow_story_clock_conflicts: bool = False, allow_character_decision_conflicts: bool = False,
               allow_degraded_summary: bool = False) -> dict:
        with FileLock(str(self.commit_lock_path), timeout=600):
            with FileLock(str(self.nm.path / ".novel_mutation.lock"), timeout=600):
                response = self._commit_locked(
                    turn_id, index_callback, allow_quality_failure,
                    allow_fact_conflicts, allow_stale_planning, allow_locked_changes,
                    allow_story_clock_conflicts, allow_character_decision_conflicts, allow_degraded_summary,
                )
            self._run_extension_hooks(turn_id, response)
            return response

    def _commit_locked(
        self, turn_id: str, index_callback: Callable[[int, str], None] | None = None,
        allow_quality_failure: bool = False, allow_fact_conflicts: bool = False,
        allow_stale_planning: bool = False, allow_locked_changes: bool = False,
        allow_story_clock_conflicts: bool = False, allow_character_decision_conflicts: bool = False,
        allow_degraded_summary: bool = False,
    ) -> dict:
        initial = self.get(turn_id)
        if initial.get("status") == "superseded":
            raise ValueError("该回合已被更新版本取代，不能再次提交")
        if initial.get("status") not in {"committed", "discarded", "committing"} and not isinstance(initial.get("preview_summary"), dict):
            self.preview_changes(turn_id)
        recovered = False
        with FileLock(str(self.index_path) + ".lock", timeout=30):
            data = self._load_index()
            turn = self._find(data, turn_id)
            if turn.get("status") == "committed":
                if not turn.get("post_commit_pending"):
                    return {"turn": dict(turn), "result": turn.get("commit_result", {}), "unchanged": True}
                recovered = True
            if turn.get("status") == "discarded":
                raise ValueError("已废弃的回合不能提交")
            if turn.get("status") == "committing":
                content = self.read_draft(turn_id)
                canonical = self.chapter_manager.read_chapter(int(turn["chapter"])) or ""
                if canonical == content and self.chapter_manager.commits.is_committed(int(turn["chapter"]), canonical):
                    turn.update({
                        "status": "committed", "committed_at": datetime.now().isoformat(),
                        "post_commit_pending": True,
                    })
                    self._save_index(data)
                    recovered = True
                else:
                    try:
                        started = datetime.fromisoformat(str(turn.get("commit_started_at", "")))
                        age_seconds = (datetime.now() - started).total_seconds()
                    except ValueError:
                        age_seconds = 601
                    if age_seconds <= 600:
                        raise RuntimeError("该章节回合正在提交，请勿重复操作")
                    turn["status"] = "ready"
            content = self.read_draft(turn_id)
            preview_summary = turn.get("preview_summary") if isinstance(turn.get("preview_summary"), dict) else None
            if preview_summary is not None:
                if preview_summary.get("source_hash") != chapter_source_hash(content):
                    raise ValueError("章节变化预览与当前草稿不匹配，请重新生成预览后再提交")
                turn["preview"] = self._refresh_deterministic_preview(turn, preview_summary)
                turn["previewed_at"] = datetime.now().isoformat()
                self._save_index(data)
            inspection = self.inspect(turn_id)
            blocking = []
            for item in inspection["issues"]:
                if item["severity"] != "高":
                    continue
                message = item["message"]
                allowed = (
                    inspection["requires_plan_confirmation"] and "上游规划已经变化" in message and allow_stale_planning
                ) or (inspection["requires_lock_confirmation"] and "权威设定锁冲突" in message and allow_locked_changes) or (
                    inspection["requires_clock_confirmation"] and "故事时空" in message and allow_story_clock_conflicts
                ) or (inspection["requires_decision_confirmation"] and "人物决策" in message and allow_character_decision_conflicts)
                allowed = allowed or (
                    inspection["requires_summary_confirmation"] and "结构化摘要生成失败" in message and allow_degraded_summary
                )
                if not allowed:
                    blocking.append(message)
            if not recovered and blocking:
                raise ValueError("；".join(blocking))
            if not recovered and inspection["quality"]["status"] == "FAIL" and not allow_quality_failure:
                raise ValueError("章节质量检查未通过，请修改草稿或明确允许提交")
            if not recovered and inspection["requires_fact_confirmation"] and not allow_fact_conflicts:
                raise ValueError("章节会改变已确认的硬事实或高风险状态，请先查看变化预览并明确确认事实改写或状态改写")
            if not recovered:
                turn.update({
                    "status": "committing", "commit_started_at": datetime.now().isoformat(),
                    "commit_approvals": {
                        "quality_failure": bool(allow_quality_failure and inspection["quality"]["status"] == "FAIL"),
                        "fact_conflicts": bool(allow_fact_conflicts and inspection["requires_fact_confirmation"]),
                        "stale_planning": bool(allow_stale_planning and inspection["requires_plan_confirmation"]),
                        "locked_changes": bool(allow_locked_changes and inspection["requires_lock_confirmation"]),
                        "story_clock_conflicts": bool(allow_story_clock_conflicts and inspection["requires_clock_confirmation"]),
                        "character_decision_conflicts": bool(allow_character_decision_conflicts and inspection["requires_decision_confirmation"]),
                        "degraded_summary": bool(allow_degraded_summary and inspection["requires_summary_confirmation"]),
                    },
                })
                self._save_index(data)
            commit_approvals = turn.get("commit_approvals", {}) if isinstance(turn.get("commit_approvals"), dict) else {}
        if recovered:
            result = dict(turn.get("commit_result", {}))
            result.setdefault("chapter", int(turn["chapter"]))
            result.setdefault("words", int(turn.get("words", 0)))
            result["summary"] = self.chapter_manager.summary_mgr.get_summary(int(turn["chapter"])) or {}
        else:
            try:
                result = self.chapter_manager._save_chapter(
                    int(turn["chapter"]), content, turn.get("source") == "manual",
                    turn.get("preview_summary") if isinstance(turn.get("preview_summary"), dict) else None,
                )
            except Exception as exc:
                with FileLock(str(self.index_path) + ".lock", timeout=30):
                    data = self._load_index()
                    latest = self._find(data, turn_id)
                    latest.update({
                        "status": "blocked" if inspection["quality"]["status"] == "FAIL" else "ready",
                        "commit_error": str(exc), "updated_at": datetime.now().isoformat(),
                    })
                    self._save_index(data)
                raise
        warnings = []
        if index_callback:
            try:
                index_callback(int(turn["chapter"]), content)
            except Exception as exc:
                warnings.append(f"章节已提交，但索引更新失败：{exc}")
        with FileLock(str(self.index_path) + ".lock", timeout=30):
            data = self._load_index()
            turn = self._find(data, turn_id)
            for item in data["items"]:
                if (
                    item is not turn and item.get("status") == "committed"
                    and int(item.get("chapter", 0)) == int(turn["chapter"])
                ):
                    item.update({
                        "status": "superseded", "superseded_by": turn_id,
                        "superseded_at": datetime.now().isoformat(),
                    })
            turn.update({
                "status": "committed", "committed_at": datetime.now().isoformat(),
                "commit_result": {"words": result.get("words", 0), "commit": result.get("commit", {})},
                "governance_overrides": [key for key, value in commit_approvals.items() if value],
                "post_commit_warnings": warnings,
                "post_commit_pending": True,
            })
            self._save_index(data)
        post_commit = {}
        post_commit_succeeded = False
        try:
            from core.chapter_post_commit import ChapterPostCommitProcessor
            post_commit = ChapterPostCommitProcessor(self.nm, self.logger, self.storage).run(
                int(turn["chapter"]), content, result,
            )
            post_commit_succeeded = True
        except Exception as exc:
            warnings.append(f"章节已提交，但派生后处理失败：{exc}")
        patrol = {}
        try:
            from core.patrol_manager import PatrolManager
            patrol = PatrolManager(self.nm, self.logger, self.storage).after_commit(int(turn["chapter"]))
        except Exception as exc:
            warnings.append(f"章节已提交，但周期巡检失败：{exc}")
        with FileLock(str(self.index_path) + ".lock", timeout=30):
            data = self._load_index()
            turn = self._find(data, turn_id)
            turn.update({
                "post_commit_warnings": warnings,
                "post_commit": post_commit,
                "post_commit_pending": not post_commit_succeeded,
                "patrol": patrol,
            })
            if post_commit_succeeded:
                turn["post_commit_completed_at"] = datetime.now().isoformat()
            else:
                turn.pop("post_commit_completed_at", None)
            self._save_index(data)
            response = {"turn": dict(turn), "result": result, "inspection": inspection}
            if recovered:
                response["recovered"] = True
            return response

    def _run_extension_hooks(self, turn_id: str, response: dict):
        if not self.post_commit_hooks:
            return
        turn = response.get("turn", {})
        chapter = int(turn.get("chapter", 0))
        content = self.read_draft(turn_id)
        result = response.get("result", {})
        hook_warnings = []
        for hook in self.post_commit_hooks:
            try:
                hook(chapter, content, result)
            except Exception as exc:
                hook_warnings.append(f"章节已提交，但扩展钩子失败：{exc}")
        if not hook_warnings:
            return
        with FileLock(str(self.index_path) + ".lock", timeout=30):
            data = self._load_index()
            stored = self._find(data, turn_id)
            warnings = list(stored.get("post_commit_warnings", []))
            warnings.extend(hook_warnings)
            stored["post_commit_warnings"] = warnings
            self._save_index(data)
            response["turn"] = dict(stored)

    def commit_manual(
        self, chapter: int, content: str, target_words: int = 5000,
        index_callback: Callable[[int, str], None] | None = None,
        allow_quality_failure: bool = True, allow_fact_conflicts: bool = False,
        allow_stale_planning: bool = False, allow_locked_changes: bool = False,
        allow_story_clock_conflicts: bool = False, allow_character_decision_conflicts: bool = False,
        allow_degraded_summary: bool = False,
    ) -> dict:
        turn = self.save_draft(chapter, content, target_words, "manual")
        return self.commit(
            turn["id"], index_callback, allow_quality_failure, allow_fact_conflicts, allow_stale_planning,
            allow_locked_changes, allow_story_clock_conflicts, allow_character_decision_conflicts,
            allow_degraded_summary,
        )

    def discard(self, turn_id: str) -> dict:
        with FileLock(str(self.index_path) + ".lock", timeout=30):
            data = self._load_index()
            turn = self._find(data, turn_id)
            if turn.get("status") in {"committed", "superseded"}:
                raise ValueError("已进入正史历史的回合不能废弃")
            turn.update({"status": "discarded", "discarded_at": datetime.now().isoformat()})
            self._save_index(data)
            return dict(turn)

    def get(self, turn_id: str) -> dict:
        return dict(self._find(self._load_index(), turn_id))

    def list(self, chapter: int | None = None, limit: int = 50) -> list[dict]:
        items = self._load_index()["items"]
        if chapter is not None:
            items = [item for item in items if int(item.get("chapter", 0)) == int(chapter)]
        return [dict(item) for item in reversed(items[-max(1, min(200, limit)):])]

    def read_draft(self, turn_id: str) -> str:
        self._validate_id(turn_id)
        path = self.draft_dir / f"{turn_id}.txt"
        if not path.exists():
            raise ValueError("回合草稿不存在")
        return path.read_text("utf-8", errors="replace")

    def _load_index(self) -> dict:
        data = self.storage.safe_read_json(self.index_path, {"schema_version": 1, "items": []})
        if not isinstance(data, dict):
            data = {}
        items = data.get("items", [])
        return {"schema_version": 1, "items": [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []}

    def _save_index(self, data: dict):
        items = data.get("items", [])
        protected_statuses = {"drafting", "ready", "blocked", "committing"}
        removable = [
            item for item in items
            if item.get("status") not in protected_statuses
            and not (item.get("status") == "committed" and item.get("post_commit_pending"))
        ]
        excess = max(0, len(removable) - 500)
        dropped = removable[:excess]
        dropped_objects = {id(item) for item in dropped}
        kept = [item for item in items if id(item) not in dropped_objects]
        data["items"] = kept
        self.storage.atomic_write_json(self.index_path, data)
        for item in dropped:
            turn_id = str(item.get("id", "")) if isinstance(item, dict) else ""
            if turn_id.isalnum():
                (self.draft_dir / f"{turn_id}.txt").unlink(missing_ok=True)

    @staticmethod
    def _find(data: dict, turn_id: str) -> dict:
        ChapterTurnEngine._validate_id(turn_id)
        item = next((item for item in data["items"] if item.get("id") == turn_id), None)
        if not item:
            raise ValueError("章节回合不存在")
        return item

    @staticmethod
    def _validate_id(turn_id: str):
        if not turn_id or len(turn_id) > 64 or not turn_id.isalnum():
            raise ValueError("无效的章节回合ID")
