"""章节管理器：保存、追加、读取章节内容，事务性写入。"""
import re
from datetime import datetime
from contextlib import nullcontext
from typing import Optional
from filelock import FileLock

from config import CHAPTER_FILE_PATTERN, RECENT_SUMMARIES_COUNT
from core.novel_manager import NovelManager
from core.summary_manager import SummaryManager
from core.fact_manager import FactManager
from core.savepoint_manager import SavepointManager
from core.change_review_manager import ChangeReviewManager
from core.foreshadow_manager import ForeshadowManager
from core.story_logic_manager import StoryLogicManager
from core.planning_review_manager import PlanningReviewManager
from core.entity_ledger import EntityLedger
from core.state_card_manager import StateCardManager
from core.canonical_state_manager import CanonicalStateManager
from core.story_clock_manager import StoryClockManager
from core.author_preference_manager import AuthorPreferenceManager
from core.chapter_commit_manager import ChapterCommitManager
from core.derived_state_rebuilder import DerivedStateRebuilder
from core.ai_contracts import chapter_source_hash
from core.mutation_transaction import NovelMutationTransaction


def count_chapter_words(content: str) -> int:
    return len(re.sub(r"\s", "", content or ""))


class ChapterManager:
    def __init__(self, novel_manager: NovelManager, logger, llm_client=None):
        self.nm = novel_manager
        self.path = novel_manager.path / "chapters"
        self.storage = novel_manager.storage
        self.summary_mgr = SummaryManager(novel_manager, logger, llm_client)
        self.fact_mgr = FactManager(novel_manager.path, logger, self.storage)
        self.savepoints = SavepointManager(novel_manager.path, logger, self.storage)
        self.change_reviews = ChangeReviewManager(novel_manager.path, logger, self.storage)
        self.foreshadows = ForeshadowManager(novel_manager.path, logger, self.storage)
        self.story_logic = StoryLogicManager(novel_manager.path, logger, self.storage)
        self.planning_reviews = PlanningReviewManager(novel_manager.path, logger, self.storage)
        self.entities = EntityLedger(novel_manager.path, logger, self.storage)
        self.state_cards = StateCardManager(novel_manager.path, logger, self.storage)
        self.canonical_state = CanonicalStateManager(novel_manager.path, logger, self.storage)
        self.story_clock = StoryClockManager(novel_manager.path, logger, self.storage)
        self.author_preferences = AuthorPreferenceManager(novel_manager.path, logger, self.storage)
        self.commits = ChapterCommitManager(novel_manager.path, logger, self.storage)
        self.logger = logger

    def save_chapter(
        self, chapter_number: int, content: str, learn_author_preference: bool = False,
        summary_override: dict | None = None,
    ) -> dict:
        with FileLock(str(self.nm.path / ".novel_mutation.lock"), timeout=600):
            return self._save_chapter(chapter_number, content, learn_author_preference, summary_override)

    def _save_chapter(
        self, chapter_number: int, content: str, learn_author_preference: bool = False,
        summary_override: dict | None = None,
    ) -> dict:
        """保存章节（含输入验证）。"""
        if not isinstance(chapter_number, int) or chapter_number < 1:
            raise ValueError(f"章节号必须为正整数: {chapter_number}")
        if not content or not content.strip():
            raise ValueError("章节内容不能为空")
        self.path.mkdir(parents=True, exist_ok=True)
        fname = CHAPTER_FILE_PATTERN.format(chapter_number)
        fpath = self.path / fname
        old_words = 0
        old_text = ""
        content_changed = False
        needs_recovery = False
        if fpath.exists():
            old_text = fpath.read_text("utf-8", errors="replace")
            old_words = count_chapter_words(old_text)
            needs_recovery = not self.commits.is_committed(chapter_number, old_text)
            if old_text == content and not needs_recovery:
                summary_data = self.summary_mgr.get_summary(chapter_number) or {}
                self.logger.info("章节 %d 正文未变化，复用完整提交", chapter_number)
                return {
                    "chapter": chapter_number, "words": old_words, "summary": summary_data,
                    "facts": {"unchanged": True}, "pending_character_changes": 0,
                    "foreshadowing": {"unchanged": True}, "story_logic": {"unchanged": True},
                    "planning_review": {"unchanged": True}, "entities": {"unchanged": True},
                    "state_cards": {"unchanged": True}, "commit": self.commits.get(chapter_number),
                    "derived_rebuild": None, "unchanged": True,
                }
            if old_text.strip() and old_text != content:
                content_changed = True
                self.savepoints.create(chapter_number, old_text, "覆盖前自动版本", "system")
                if learn_author_preference:
                    try:
                        self.author_preferences.learn(chapter_number, old_text, content)
                    except Exception as exc:
                        self.logger.warning("作者偏好学习失败，不影响章节覆盖: %s", exc)
        self.storage.atomic_write_text(fpath, content)
        word_count = count_chapter_words(content)
        delta = word_count - old_words
        if delta != 0:
            self.nm.add_words(delta)
        if needs_recovery:
            self._recalculate_total_words()
        if isinstance(summary_override, dict) and summary_override.get("source_hash") == chapter_source_hash(content):
            summary_data = dict(summary_override)
            self.summary_mgr.save_custom_summary(chapter_number, summary_data)
        else:
            summary_data = self.summary_mgr.generate_summary(chapter_number, content)
        rebuild_result = None
        if content_changed or needs_recovery:
            reason = "未完整提交章节恢复重建" if needs_recovery and not content_changed else "章节正文覆盖后重建"
            rebuild_result = DerivedStateRebuilder(self.nm.path, self.logger, self.storage).rebuild(
                max(self.nm.get_current_chapter(), chapter_number), reason,
            )
            fact_result = {"rebuilt": True}
            review_count = len(self.change_reviews.list("pending"))
            foreshadow_result = {"rebuilt": True}
            logic_result = {"rebuilt": True}
            planning_review = self.planning_reviews.review_chapter(chapter_number, summary_data)
            entity_result = {"rebuilt": True}
            state_card_result = {"rebuilt": True}
            if content_changed:
                self._invalidate_future_planning(chapter_number)
        else:
            fact_result = self.fact_mgr.add_from_summary(chapter_number, summary_data.get("facts", []))
            review_count = self.change_reviews.add_from_summary(chapter_number, summary_data.get("characters_changed", []))
            review_count += self.change_reviews.add_new_characters(chapter_number, summary_data.get("new_characters", []))
            foreshadow_result = self.foreshadows.ingest(chapter_number, summary_data.get("foreshadowing", []))
            logic_result = self.story_logic.ingest(chapter_number, summary_data)
            planning_review = self.planning_reviews.review_chapter(chapter_number, summary_data)
            entity_result = self.entities.ingest(chapter_number, summary_data)
            state_card_result = self.canonical_state.propose_from_summary(chapter_number, summary_data)
        story_clock_result = self.story_clock.record(chapter_number, summary_data)
        if self.nm.get_current_chapter() < chapter_number:
            self.nm.save_state({"current_chapter": chapter_number})
        self.nm.update_last_summary(summary_data.get("summary", ""))
        commit = self.commits.mark(chapter_number, content, summary_data)
        self.logger.info("章节 %d 已保存 (%d 字)", chapter_number, word_count)
        return {
            "chapter": chapter_number, "words": word_count, "summary": summary_data,
            "facts": fact_result, "pending_character_changes": review_count,
            "foreshadowing": foreshadow_result,
            "story_logic": logic_result,
            "planning_review": planning_review,
            "entities": entity_result,
            "state_cards": state_card_result,
            "story_clock": story_clock_result,
            "commit": commit,
            "derived_rebuild": rebuild_result,
        }

    def _recalculate_total_words(self):
        total = 0
        for path in self.path.glob("*.txt"):
            if path.stem.isdigit():
                total += count_chapter_words(path.read_text("utf-8", errors="replace"))
        self.nm.save_state({"total_words": total})

    def merge_latest_chapters(self, chapter_number: int) -> dict:
        with FileLock(str(self.nm.path / ".novel_mutation.lock"), timeout=600):
            with NovelMutationTransaction(self.nm.path, [chapter_number, chapter_number + 1]):
                return self._merge_latest_chapters(chapter_number, True)

    def _merge_latest_chapters(self, chapter_number: int, already_locked: bool = False) -> dict:
        """合并最后两章，并清理被删除章节的全部派生状态。"""
        with nullcontext() if already_locked else FileLock(str(self.nm.path / ".novel_mutation.lock"), timeout=600):
            if chapter_number + 1 != self.nm.get_current_chapter():
                raise ValueError("为避免打乱后续编号，目前只允许合并最后两章")
            first = self.read_chapter(chapter_number)
            second = self.read_chapter(chapter_number + 1)
            if not first or not second:
                raise ValueError("相邻章节不存在")
            first_summary = self.summary_mgr.get_summary(chapter_number) or {}
            second_summary = self.summary_mgr.get_summary(chapter_number + 1) or {}
            combined = first.rstrip() + "\n\n" + second.lstrip()
            self.savepoints.create(chapter_number, first, "合并前第一章", "user")
            self.savepoints.create(chapter_number + 1, second, "合并前第二章", "user")
            self.storage.atomic_write_text(self.path / CHAPTER_FILE_PATTERN.format(chapter_number), combined)
            result = {}
            removed_chapter = chapter_number + 1
            (self.path / CHAPTER_FILE_PATTERN.format(removed_chapter)).unlink(missing_ok=True)
            (self.nm.path / "summaries" / f"{removed_chapter:06d}.json").unlink(missing_ok=True)
            self.commits.invalidate([removed_chapter])
            self._remap_chapter_references(removed_chapter, chapter_number)
            self._remove_planning_chapter(removed_chapter)
            self._invalidate_future_planning(chapter_number)
            merged_summary = self._merge_summaries(chapter_number, combined, first_summary, second_summary)
            self.summary_mgr.save_custom_summary(chapter_number, merged_summary)
            rebuild = DerivedStateRebuilder(self.nm.path, self.logger, self.storage).rebuild(
                chapter_number, "合并最后两章后重建",
            )
            self._recalculate_total_words()
            self.nm.save_state({"current_chapter": chapter_number, "last_summary": merged_summary.get("summary", "")})
            commit = self.commits.mark(chapter_number, combined, merged_summary)
            result.update({
                "chapter": chapter_number, "words": count_chapter_words(combined), "summary": merged_summary,
                "commit": commit, "derived_rebuild": rebuild, "merged_chapter": removed_chapter,
            })
            return result

    def split_latest_chapter(self, chapter_number: int, position: int) -> dict:
        with FileLock(str(self.nm.path / ".novel_mutation.lock"), timeout=600):
            with NovelMutationTransaction(self.nm.path, [chapter_number, chapter_number + 1]):
                return self._split_latest_chapter(chapter_number, position, True)

    def _split_latest_chapter(self, chapter_number: int, position: int, already_locked: bool = False) -> dict:
        """拆分最新章节，按正文证据迁移原有结构化记忆。"""
        with nullcontext() if already_locked else FileLock(str(self.nm.path / ".novel_mutation.lock"), timeout=600):
            if chapter_number != self.nm.get_current_chapter():
                raise ValueError("为避免打乱后续编号，目前只允许拆分最新章节")
            content = self.read_chapter(chapter_number) or ""
            if position < 100 or position > len(content) - 100:
                raise ValueError("拆分位置距离章节首尾过近")
            original_summary = self.summary_mgr.get_summary(chapter_number) or {}
            first_content = content[:position].rstrip()
            second_content = content[position:].lstrip()
            self.savepoints.create(chapter_number, content, "拆分前完整章节", "user")
            self.storage.atomic_write_text(self.path / CHAPTER_FILE_PATTERN.format(chapter_number), first_content)
            self.storage.atomic_write_text(self.path / CHAPTER_FILE_PATTERN.format(chapter_number + 1), second_content)
            self.commits.invalidate([chapter_number, chapter_number + 1])
            self._split_chapter_references(chapter_number, chapter_number + 1, first_content, second_content)
            first_summary = self.summary_mgr._basic_summary(chapter_number, first_content)
            second_summary = self.summary_mgr._basic_summary(chapter_number + 1, second_content)
            for key in (
                "characters_changed", "new_characters", "new_information", "foreshadowing", "facts",
                "narrative_promises", "causal_links", "knowledge_changes", "locations", "factions",
                "items", "relationship_changes",
            ):
                for item in original_summary.get(key, []) if isinstance(original_summary.get(key), list) else []:
                    target = first_summary if self._memory_in_text(item, first_content, second_content) else second_summary
                    target.setdefault(key, [])
                    if item not in target[key]:
                        target[key].append(item)
            if original_summary.get("handoff"):
                second_summary["handoff"] = original_summary["handoff"]
            if original_summary.get("plan_reconciliation"):
                second_summary["plan_reconciliation"] = original_summary["plan_reconciliation"]
            for chapter, chapter_content, summary in (
                (chapter_number, first_content, first_summary),
                (chapter_number + 1, second_content, second_summary),
            ):
                summary["chapter"] = chapter
                summary["source_hash"] = chapter_source_hash(chapter_content)
                summary["memory_review_status"] = "pending"
                self.summary_mgr.save_custom_summary(chapter, summary)
            rebuild = DerivedStateRebuilder(self.nm.path, self.logger, self.storage).rebuild(
                chapter_number + 1, "拆分最新章节后重建",
            )
            self._recalculate_total_words()
            self.nm.save_state({
                "current_chapter": chapter_number + 1,
                "last_summary": second_summary.get("summary", ""),
            })
            self.commits.mark(chapter_number, first_content, first_summary)
            self.commits.mark(chapter_number + 1, second_content, second_summary)
            return {"chapters": [chapter_number, chapter_number + 1], "derived_rebuild": rebuild}

    def _merge_summaries(self, chapter: int, content: str, first: dict, second: dict) -> dict:
        merged = self.summary_mgr._basic_summary(chapter, content)
        summary_parts = [str(item.get("summary", "")).strip() for item in (first, second) if item.get("summary")]
        merged["summary"] = "｜".join(summary_parts)[:1000] or merged["summary"]
        for key in (
            "characters_changed", "new_characters", "new_information", "foreshadowing", "facts",
            "narrative_promises", "causal_links", "knowledge_changes", "locations", "factions",
            "items", "relationship_changes",
        ):
            values = []
            for source in (first, second):
                for item in source.get(key, []) if isinstance(source.get(key), list) else []:
                    if item not in values:
                        values.append(item)
            merged[key] = values
        if second.get("handoff"):
            merged["handoff"] = second["handoff"]
        if second.get("plan_reconciliation"):
            merged["plan_reconciliation"] = second["plan_reconciliation"]
        merged["memory_review_status"] = "pending"
        return merged

    def batch_replace(self, old: str, new: str, chapters: list[int]) -> dict:
        chapter_numbers = sorted(set(int(value) for value in chapters if int(value) > 0))
        with FileLock(str(self.nm.path / ".novel_mutation.lock"), timeout=600):
            with NovelMutationTransaction(self.nm.path, chapter_numbers):
                return self._batch_replace(old, new, chapter_numbers, True)

    def _batch_replace(self, old: str, new: str, chapters: list[int], already_locked: bool = False) -> dict:
        """批量替换正文，同时更新结构化记忆、提交标记和派生账本。"""
        if not old or old == new:
            raise ValueError("请输入不同的查找与替换内容")
        with nullcontext() if already_locked else FileLock(str(self.nm.path / ".novel_mutation.lock"), timeout=600):
            changed = []
            replacements = 0
            for chapter in sorted(set(int(value) for value in chapters if int(value) > 0)):
                content = self.read_chapter(chapter)
                if not content or old not in content:
                    continue
                count = content.count(old)
                revised = content.replace(old, new)
                if not revised.strip():
                    raise ValueError(f"批量替换会清空第{chapter}章，操作已取消")
                self.savepoints.create(chapter, content, "批量替换前", "user")
                self.storage.atomic_write_text(self.path / CHAPTER_FILE_PATTERN.format(chapter), revised)
                summary = self.summary_mgr.get_summary(chapter) or self.summary_mgr._basic_summary(chapter, revised)
                summary = self._replace_in_memory(summary, old, new)
                summary["chapter"] = chapter
                summary["source_hash"] = chapter_source_hash(revised)
                summary["memory_review_status"] = "pending"
                self.summary_mgr.save_custom_summary(chapter, summary)
                changed.append(chapter)
                replacements += count
            if not changed:
                return {"changed_chapters": [], "replacements": 0, "derived_rebuild": None}
            self.commits.invalidate(changed)
            rebuild = DerivedStateRebuilder(self.nm.path, self.logger, self.storage).rebuild(
                self.nm.get_current_chapter(), "批量替换后重建",
            )
            self._recalculate_total_words()
            for chapter in changed:
                content = self.read_chapter(chapter) or ""
                summary = self.summary_mgr.get_summary(chapter) or {}
                self.commits.mark(chapter, content, summary)
            latest = self.summary_mgr.get_summary(self.nm.get_current_chapter()) or {}
            self.nm.save_state({"last_summary": latest.get("summary", "")})
            return {"changed_chapters": changed, "replacements": replacements, "derived_rebuild": rebuild}

    @classmethod
    def _replace_in_memory(cls, value, old: str, new: str):
        if isinstance(value, str):
            return value.replace(old, new)
        if isinstance(value, list):
            return [cls._replace_in_memory(item, old, new) for item in value]
        if isinstance(value, dict):
            return {key: cls._replace_in_memory(item, old, new) for key, item in value.items()}
        return value

    @staticmethod
    def _memory_in_text(item, first: str, second: str) -> bool:
        if isinstance(item, str):
            return bool(item and item in first and item not in second)
        if not isinstance(item, dict):
            return False
        evidence = str(item.get("evidence", "")).strip()
        if evidence:
            return evidence in first and evidence not in second
        clues = [
            str(item.get(key, "")).strip() for key in
            ("subject", "object", "name", "fact", "text", "new_value", "cause", "effect")
            if str(item.get(key, "")).strip()
        ]
        first_hits = sum(clue in first for clue in clues)
        second_hits = sum(clue in second for clue in clues)
        return first_hits > second_hits

    def _invalidate_future_planning(self, chapter: int):
        invalidated = []
        for relative in ("outline/chapter_plans.json", "outline/scene_outlines.json"):
            path = self.nm.path / relative
            data = self.storage.safe_read_json(path, {})
            removed = [key for key in data if str(key).isdigit() and int(key) > chapter]
            if removed:
                for key in removed:
                    data.pop(key, None)
                self.storage.atomic_write_json(path, data)
                invalidated.extend(int(key) for key in removed)
        if invalidated:
            self.storage.atomic_write_json(self.nm.path / "planning" / "manual_edit_replan.json", {
                "source_chapter": chapter, "invalidated_chapters": sorted(set(invalidated)),
                "created_at": datetime.now().isoformat(),
            })

    def _remap_chapter_references(self, source_chapter: int, target_chapter: int):
        timeline = self.nm.path / "timeline"
        for path in list(timeline.glob(f"{source_chapter:06d}_*.json")) if timeline.exists() else []:
            event = self.storage.safe_read_json(path, {})
            event["chapter"] = target_chapter
            event_id = str(event.get("id") or path.stem.split("_", 1)[-1])
            self.storage.atomic_write_json(timeline / f"{target_chapter:06d}_{event_id}.json", event)
            path.unlink(missing_ok=True)
        for path in (self.nm.path / "characters").glob("*.json"):
            data = self.storage.safe_read_json(path, {})
            changed = False
            if int(data.get("last_chapter", 0)) == source_chapter:
                data["last_chapter"] = target_chapter
                changed = True
            for key in ("locations", "ability_history"):
                for entry in data.get(key, []) if isinstance(data.get(key), list) else []:
                    if int(entry.get("chapter", 0)) == source_chapter:
                        entry["chapter"] = target_chapter
                        changed = True
            if changed:
                self.storage.atomic_write_json(path, data)
        evolution = self.nm.path / "characters" / ".evolution"
        for path in evolution.glob("*.json") if evolution.exists() else []:
            data = self.storage.safe_read_json(path, {})
            changed = False
            for entry in data.get("snapshots", []):
                if int(entry.get("chapter", 0)) == source_chapter:
                    entry["chapter"] = target_chapter
                    changed = True
            if changed:
                data["snapshots"] = self._deduplicate_chapter_entries(data.get("snapshots", []))
                self.storage.atomic_write_json(path, data)

    def _split_chapter_references(self, source_chapter: int, target_chapter: int, first: str, second: str):
        timeline = self.nm.path / "timeline"
        for path in list(timeline.glob(f"{source_chapter:06d}_*.json")) if timeline.exists() else []:
            event = self.storage.safe_read_json(path, {})
            clues = [str(event.get("event", "")).strip(), str(event.get("location", "")).strip()]
            first_hits = sum(bool(clue and clue in first) for clue in clues)
            second_hits = sum(bool(clue and clue in second) for clue in clues)
            if second_hits <= first_hits:
                continue
            event["chapter"] = target_chapter
            event_id = str(event.get("id") or path.stem.split("_", 1)[-1])
            self.storage.atomic_write_json(timeline / f"{target_chapter:06d}_{event_id}.json", event)
            path.unlink(missing_ok=True)
        for path in (self.nm.path / "characters").glob("*.json"):
            data = self.storage.safe_read_json(path, {})
            changed = False
            name = str(data.get("name", path.stem))
            if int(data.get("last_chapter", 0)) == source_chapter and name in second:
                data["last_chapter"] = target_chapter
                changed = True
            for key, field in (("locations", "location"), ("ability_history", "level")):
                for entry in data.get(key, []) if isinstance(data.get(key), list) else []:
                    clue = str(entry.get(field, "")).strip()
                    if int(entry.get("chapter", 0)) == source_chapter and clue and clue in second and clue not in first:
                        entry["chapter"] = target_chapter
                        changed = True
            if changed:
                self.storage.atomic_write_json(path, data)

    def _remove_planning_chapter(self, chapter: int):
        for relative in (
            "outline/chapter_titles.json", "outline/chapter_briefs.json",
            "outline/chapter_plans.json", "outline/scene_outlines.json",
        ):
            path = self.nm.path / relative
            data = self.storage.safe_read_json(path, {})
            if str(chapter) in data:
                data.pop(str(chapter), None)
                self.storage.atomic_write_json(path, data)

    @staticmethod
    def _deduplicate_chapter_entries(entries: list[dict]) -> list[dict]:
        by_chapter = {}
        for entry in entries:
            chapter = int(entry.get("chapter", 0))
            if chapter not in by_chapter:
                by_chapter[chapter] = entry
                continue
            current = by_chapter[chapter]
            for key, value in entry.items():
                if value not in (None, "", []):
                    current[key] = value
        return [by_chapter[key] for key in sorted(by_chapter)]

    def append_chapter(self, chapter_number: int, content: str) -> dict:
        """追加文本到已有章节（含输入验证）。"""
        with FileLock(str(self.nm.path / ".novel_mutation.lock"), timeout=600):
            return self._append_chapter(chapter_number, content)

    def _append_chapter(self, chapter_number: int, content: str) -> dict:
        if not isinstance(chapter_number, int) or chapter_number < 1:
            raise ValueError(f"章节号必须为正整数: {chapter_number}")
        if not content or not content.strip():
            raise ValueError("追加内容不能为空")
        self.path.mkdir(parents=True, exist_ok=True)
        fname = CHAPTER_FILE_PATTERN.format(chapter_number)
        fpath = self.path / fname
        existing = fpath.read_text("utf-8", errors="replace") if fpath.exists() else ""
        new_content = existing.rstrip("\n\r") + "\n" + content if existing else content
        result = self._save_chapter(chapter_number, new_content)
        result["appended"] = True
        result["appended_words"] = count_chapter_words(content)
        return result

    def read_chapter(self, chapter_number: int) -> Optional[str]:
        """读取指定章节（含输入验证）。"""
        if not isinstance(chapter_number, int) or chapter_number < 1:
            return None
        fname = CHAPTER_FILE_PATTERN.format(chapter_number)
        fpath = self.path / fname
        try:
            if fpath.exists():
                return fpath.read_text("utf-8", errors="replace")
        except Exception:
            pass
        return None

    def get_recent_chapters(self, count: int = 3) -> list[dict]:
        files = sorted(
            (path for path in self.path.glob("*.txt") if path.stem.isdigit()),
            key=lambda path: int(path.stem), reverse=True,
        )
        result = []
        for f in files[:count]:
            num = int(f.stem)
            preview = ""
            try:
                preview = f.read_text("utf-8", errors="replace")[:500]
            except Exception:
                pass
            result.append({
                "chapter": num,
                "content_preview": preview,
                "size": f.stat().st_size if f.exists() else 0,
            })
        return result

    def get_recent_summaries(self, count: int = None) -> list[dict]:
        return self.summary_mgr.get_recent_summaries(count or RECENT_SUMMARIES_COUNT)

    def get_chapter_count(self) -> int:
        return len(self.list_chapter_numbers())

    def list_chapter_numbers(self) -> list[int]:
        return sorted(int(path.stem) for path in self.path.glob("*.txt") if path.stem.isdigit())
