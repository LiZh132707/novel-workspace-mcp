"""小说工程健康检查与确定性修复。"""
from __future__ import annotations

from pathlib import Path

from filelock import FileLock

from core.chapter_commit_manager import ChapterCommitManager
from core.chapter_manager import count_chapter_words
from core.ai_contracts import chapter_source_hash
from core.derived_state_rebuilder import DerivedStateRebuilder
from core.mutation_transaction import NovelMutationTransaction
from core.summary_manager import SummaryManager
from core.project_schema import ProjectSchemaManager
from storage_utils import StorageManager


class ProjectHealthManager:
    def __init__(self, novel_manager, logger=None, storage: StorageManager | None = None):
        self.nm = novel_manager
        self.root = novel_manager.path
        self.logger = logger
        self.storage = storage or novel_manager.storage or StorageManager(logger)
        self.summaries = SummaryManager(novel_manager, logger, None)
        self.commits = ChapterCommitManager(self.root, logger, self.storage)

    def scan(self) -> dict:
        issues = []
        schema = ProjectSchemaManager(self.root, self.storage).validate()
        if not schema["valid"]:
            issues.append(self._issue("project_schema", "高", schema["reason"], True))
        chapter_files = self._chapter_files()
        chapters = [int(path.stem) for path in chapter_files]
        expected = list(range(1, max(chapters, default=0) + 1))
        missing_chapters = sorted(set(expected) - set(chapters))
        if missing_chapters:
            issues.append(self._issue("chapter_gap", "高", f"章节编号存在断档：{missing_chapters[:20]}", False, missing_chapters))
        total_words = 0
        for path in chapter_files:
            chapter = int(path.stem)
            content = path.read_text("utf-8", errors="replace")
            total_words += count_chapter_words(content)
            summary = self.summaries.get_summary(chapter)
            if not summary:
                issues.append(self._issue("missing_summary", "高", f"第{chapter}章缺少章节记忆", True, [chapter]))
            elif not self.commits.is_committed(chapter, content):
                issues.append(self._issue("incomplete_commit", "高", f"第{chapter}章提交标记与正文或摘要不一致", True, [chapter]))
        summary_dir = self.root / "summaries"
        orphan_summaries = sorted(
            int(path.stem) for path in summary_dir.glob("*.json")
            if path.stem.isdigit() and int(path.stem) not in set(chapters)
        ) if summary_dir.exists() else []
        if orphan_summaries:
            issues.append(self._issue("orphan_summary", "中", f"存在无正文的章节记忆：{orphan_summaries[:20]}", True, orphan_summaries))
        auto_timeline_chapters = set()
        invalid_timeline = []
        for path in (self.root / "timeline").glob("*.json") if (self.root / "timeline").exists() else []:
            event = self.storage.safe_read_json(path, {})
            chapter = self._positive_int(event.get("chapter")) if isinstance(event, dict) else None
            if not isinstance(event, dict) or chapter is None or not str(event.get("event", "")).strip():
                invalid_timeline.append(path.name)
                continue
            if event.get("source") == "chapter_summary":
                auto_timeline_chapters.add(chapter)
        if invalid_timeline:
            issues.append(self._issue(
                "invalid_timeline", "中",
                f"时间线事件损坏或字段无效：{invalid_timeline[:20]}", False,
            ))
        missing_timeline = [
            chapter for chapter in chapters
            if chapter not in auto_timeline_chapters
            and str((self.summaries.get_summary(chapter) or {}).get("summary", "")).strip()
        ]
        if missing_timeline:
            issues.append(self._issue(
                "missing_chapter_timeline", "中",
                f"章节级自动时间线缺失：{missing_timeline[:20]}", True, missing_timeline,
            ))
        state = self.nm.get_state()
        if int(state.get("current_chapter", 0) or 0) != max(chapters, default=0):
            issues.append(self._issue("state_chapter", "高", "state.json 当前章节与磁盘正文不一致", True))
        if int(state.get("total_words", 0) or 0) != total_words:
            issues.append(self._issue("state_words", "中", f"总字数应为 {total_words}", True))
        invalid_characters = []
        for path in (self.root / "characters").glob("*.json"):
            data = self.storage.safe_read_json(path, None)
            if not isinstance(data, dict) or not str(data.get("name", "")).strip():
                invalid_characters.append(path.name)
        if invalid_characters:
            issues.append(self._issue("invalid_character", "高", f"人物档案损坏：{invalid_characters[:20]}", False))
        broken_turns = []
        stranded_turns = []
        invalid_turns = []
        turns = self.storage.safe_read_json(self.root / "turns" / "index.json", {})
        indexed_turn_ids = {
            str(item.get("id", "")) for item in turns.get("items", [])
            if self._valid_turn(item)
        } if isinstance(turns, dict) and isinstance(turns.get("items"), list) else set()
        orphan_turn_drafts = [
            path.name for path in (self.root / "turns" / "drafts").glob("*.txt")
            if path.stem not in indexed_turn_ids
        ] if (self.root / "turns" / "drafts").exists() else []
        turn_items = turns.get("items", []) if isinstance(turns, dict) and isinstance(turns.get("items"), list) else []
        if not isinstance(turns, dict) or not isinstance(turns.get("items"), list):
            invalid_turns.append("index.json")
        for index, item in enumerate(turn_items):
            if not self._valid_turn(item):
                invalid_turns.append(f"记录{index + 1}")
                continue
            needs_post_commit = item.get("status") == "committed" and item.get("post_commit_pending")
            if item.get("status") in {"drafting", "ready", "blocked", "committing"} or needs_post_commit:
                draft_path = self.root / "turns" / "drafts" / f"{item.get('id')}.txt"
                if not draft_path.exists():
                    broken_turns.append(str(item.get("id", "")))
                elif item.get("status") == "committing" or needs_post_commit:
                    stranded_turns.append(str(item.get("id", "")))
        if broken_turns:
            issues.append(self._issue("missing_turn_draft", "中", f"存在缺少正文的中断回合：{len(broken_turns)}个", True))
        if invalid_turns:
            issues.append(self._issue("invalid_turn_record", "中", f"回合索引含无效记录：{invalid_turns[:20]}", True))
        if stranded_turns:
            issues.append(self._issue("stranded_turn_commit", "中", f"存在未完成终态确认的提交回合：{len(stranded_turns)}个", True))
        if orphan_turn_drafts:
            issues.append(self._issue("orphan_turn_draft", "低", f"存在未登记的孤立回合草稿：{len(orphan_turn_drafts)}个", True))
        transaction_dirs = list((self.root / ".transactions").iterdir()) if (self.root / ".transactions").exists() else []
        if transaction_dirs:
            issues.append(self._issue("stale_transaction", "低", f"存在 {len(transaction_dirs)} 个遗留事务目录，需人工确认后处理", False))
        severe = len([item for item in issues if item["severity"] == "高"])
        patrol_data = self.storage.safe_read_json(self.root / "planning" / "patrols.json", {"items": []})
        patrol_items = patrol_data.get("items", []) if isinstance(patrol_data, dict) else []
        return {
            "status": "healthy" if not issues else "attention" if severe == 0 else "unhealthy",
            "issues": issues, "issue_count": len(issues), "severe_count": severe,
            "chapters": len(chapters), "total_words": total_words,
            "repairable_count": len([item for item in issues if item["repairable"]]),
            "latest_patrol": patrol_items[-1:] if isinstance(patrol_items, list) else [],
        }

    def repair(self) -> dict:
        before = self.scan()
        with FileLock(str(self.root / ".novel_mutation.lock"), timeout=600), NovelMutationTransaction(
            self.root, [], directories=("summaries", "tracking", "reviews", "timeline", "characters", "planning", "turns"),
            files=("state.json", "facts.json", "foreshadowing.json", "project.json"),
        ):
            if not ProjectSchemaManager(self.root, self.storage).validate()["valid"]:
                ProjectSchemaManager(self.root, self.storage).initialize(self.nm.name)
            chapter_files = self._chapter_files()
            chapters = [int(path.stem) for path in chapter_files]
            for path in chapter_files:
                chapter = int(path.stem)
                content = path.read_text("utf-8", errors="replace")
                summary = self.summaries.get_summary(chapter)
                if not summary or summary.get("source_hash") != chapter_source_hash(content):
                    summary = self.summaries._basic_summary(chapter, content)
                    self.summaries.save_custom_summary(chapter, summary)
            for path in (self.root / "summaries").glob("*.json") if (self.root / "summaries").exists() else []:
                if path.stem.isdigit() and int(path.stem) not in set(chapters):
                    path.unlink()
            if chapters:
                DerivedStateRebuilder(self.root, self.logger, self.storage).rebuild(max(chapters), "工程健康修复")
            for path in chapter_files:
                chapter = int(path.stem)
                content = path.read_text("utf-8", errors="replace")
                summary = self.summaries.get_summary(chapter) or self.summaries._basic_summary(chapter, content)
                self.commits.mark(chapter, content, summary)
            from core.chapter_post_commit import ChapterPostCommitProcessor
            processor = ChapterPostCommitProcessor(self.nm, self.logger, self.storage)
            for path in chapter_files:
                chapter = int(path.stem)
                content = path.read_text("utf-8", errors="replace")
                processor.run(chapter, content, {"summary": self.summaries.get_summary(chapter) or {}})
            self._repair_turns()
            latest = max(chapters, default=0)
            latest_summary = self.summaries.get_summary(latest) if latest else {}
            self.nm.save_state({
                "current_chapter": latest,
                "total_words": sum(count_chapter_words(path.read_text("utf-8", errors="replace")) for path in chapter_files),
                "last_summary": (latest_summary or {}).get("summary", ""),
            })
        return {"before": before, "after": self.scan()}

    def _repair_turns(self):
        path = self.root / "turns" / "index.json"
        with FileLock(str(path) + ".lock", timeout=30):
            data = self.storage.safe_read_json(path, {})
            changed = not isinstance(data, dict) or not isinstance(data.get("items"), list)
            data = data if isinstance(data, dict) else {}
            data["schema_version"] = 1
            data["items"] = data.get("items", []) if isinstance(data.get("items"), list) else []
            valid_items = [item for item in data["items"] if self._valid_turn(item)]
            if len(valid_items) != len(data["items"]):
                data["items"] = valid_items
                changed = True
            for item in data["items"]:
                needs_post_commit = isinstance(item, dict) and item.get("status") == "committed" and item.get("post_commit_pending")
                if not isinstance(item, dict) or (item.get("status") not in {"drafting", "ready", "blocked", "committing"} and not needs_post_commit):
                    continue
                draft = self.root / "turns" / "drafts" / f"{item.get('id')}.txt"
                if not draft.exists() and not needs_post_commit:
                    item.update({"status": "discarded", "repair_reason": "草稿正文缺失"})
                    changed = True
                elif item.get("status") == "committing" or needs_post_commit:
                    chapter = int(item.get("chapter", 0))
                    content = draft.read_text("utf-8", errors="replace") if draft.exists() else ""
                    canonical = self.root / "chapters" / f"{chapter:06d}.txt"
                    canonical_text = canonical.read_text("utf-8", errors="replace") if canonical.exists() else ""
                    if (not content or canonical_text == content) and self.commits.is_committed(chapter, canonical_text):
                        try:
                            from core.chapter_post_commit import ChapterPostCommitProcessor
                            summary = self.summaries.get_summary(chapter) or {}
                            post_commit = ChapterPostCommitProcessor(self.nm, self.logger, self.storage).run(
                                chapter, canonical_text, {"summary": summary},
                            )
                            item.update({
                                "status": "committed", "post_commit_pending": False,
                                "post_commit": post_commit, "repair_reason": "正史已提交，补齐回合终态与派生后处理",
                            })
                        except Exception as exc:
                            item.update({
                                "status": "committed", "post_commit_pending": True,
                                "repair_reason": f"正史已提交，但派生后处理仍失败：{exc}",
                            })
                    else:
                        item.update({"status": "ready", "repair_reason": "提交未完成，已退回可重试状态"})
                    changed = True
            indexed = {str(item.get("id", "")) for item in data["items"] if isinstance(item, dict)}
            for draft in (self.root / "turns" / "drafts").glob("*.txt") if (self.root / "turns" / "drafts").exists() else []:
                if draft.stem not in indexed:
                    draft.unlink(missing_ok=True)
                    changed = True
            if changed:
                self.storage.atomic_write_json(path, data)

    def _chapter_files(self) -> list[Path]:
        return sorted(
            (path for path in (self.root / "chapters").glob("*.txt") if path.stem.isdigit()),
            key=lambda path: int(path.stem),
        )

    @staticmethod
    def _positive_int(value) -> int | None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @classmethod
    def _valid_turn(cls, item) -> bool:
        if not isinstance(item, dict):
            return False
        turn_id = str(item.get("id", ""))
        return bool(
            turn_id and len(turn_id) <= 64 and turn_id.isalnum()
            and cls._positive_int(item.get("chapter")) is not None
            and item.get("status") in {"drafting", "ready", "blocked", "committing", "committed", "discarded", "superseded"}
        )

    @staticmethod
    def _issue(kind: str, severity: str, message: str, repairable: bool, chapters: list[int] | None = None) -> dict:
        return {"kind": kind, "severity": severity, "message": message, "repairable": repairable, "chapters": chapters or []}
