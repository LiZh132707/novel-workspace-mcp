"""历史剧情修改：双向影响分析、隔离分支、验证与原子提交。"""
from __future__ import annotations

import json
import difflib
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from filelock import FileLock

import config
from core.ai_contracts import BASE_SYSTEM, inspect_chapter
from core.chapter_commit_manager import ChapterCommitManager
from core.prompt_settings import PromptSettingsManager
from core.state_card_manager import StateCardManager
from core.summary_manager import SummaryManager
from core.derived_state_rebuilder import DerivedStateRebuilder
from core.history_impact_analyzer import HistoryImpactAnalyzer
from storage_utils import StorageManager


class HistoryRevisionManager:
    DERIVED_DIRECTORIES = ("characters", "timeline", "turns")
    DERIVED_DEFAULTS = {
        "facts.json": {"facts": [], "conflicts": []},
        "foreshadowing.json": {"items": []},
        "tracking/story_logic.json": {"promises": [], "causal_links": [], "character_knowledge": {}},
        "tracking/story_clock.json": {"travel_rules": [], "events": []},
        "tracking/entities.json": {"locations": {}, "factions": {}, "items": {}, "relationships": []},
        "tracking/state_cards.json": {kind: {} for kind in StateCardManager.TYPES},
        "tracking/state_proposals.json": {"items": []},
        "tracking/canonical_versions.json": {"versions": []},
        "reviews/character_changes.json": {"items": []},
        "reviews/planning_reviews.json": {"chapters": [], "section_reviews": [], "volume_reviews": []},
    }
    PLAN_DEFAULTS = {
        "outline/chapter_plans.json": {}, "outline/chapter_briefs.json": {},
        "outline/scene_outlines.json": {}, "planning/history_revision_replan.json": {},
        "outline/chapter_titles.json": {}, "outline/opening_chapters.json": {},
        "tracking/chapter_commits.json": {},
        "summaries/long_term.json": {"arcs": []},
        "planning/patrols.json": {"items": []},
        "state.json": {},
    }

    def __init__(self, novel_manager, logger=None, llm=None, storage: StorageManager | None = None):
        self.nm = novel_manager
        self.root = novel_manager.path
        self.logger = logger
        self.llm = llm
        self.storage = storage or StorageManager(logger)
        self.revisions = self.root / "history_revisions"

    def create(self, source_chapter: int, old_fact: str, new_fact: str, instruction: str = "", mode: str = "minimal_patch") -> dict:
        current = self.nm.get_current_chapter()
        source_chapter = int(source_chapter)
        if source_chapter < 1 or source_chapter > current:
            raise ValueError(f"修改章节必须位于1至{current}章")
        old_fact, new_fact = old_fact.strip(), new_fact.strip()
        if not old_fact or not new_fact or old_fact == new_fact:
            raise ValueError("旧事实和新事实必须非空且不同")
        if mode not in {"minimal_patch", "range_rewrite", "replan_forward"}:
            raise ValueError("未知历史修改模式")
        revision_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        impact = self.analyze(source_chapter, old_fact, new_fact)
        branch = self.revisions / revision_id
        for item in impact["chapters"]:
            chapter = int(item["chapter"])
            source = self.root / "chapters" / config.CHAPTER_FILE_PATTERN.format(chapter)
            if source.exists():
                text = source.read_text("utf-8", errors="replace")
                self.storage.atomic_write_text(branch / "original" / source.name, text)
                self.storage.atomic_write_text(branch / "candidates" / source.name, text)
        manifest = {
            "id": revision_id, "status": "analyzed", "source_chapter": source_chapter,
            "old_fact": old_fact, "new_fact": new_fact, "instruction": instruction.strip()[:4000],
            "mode": mode, "impact": impact, "validation": {}, "created_at": datetime.now().isoformat(),
        }
        self.storage.atomic_write_json(branch / "manifest.json", manifest)
        return manifest

    def analyze(self, source_chapter: int, old_fact: str, new_fact: str) -> dict:
        keywords = self._keywords(old_fact + " " + new_fact)
        current = self.nm.get_current_chapter()
        summaries = self.root / "summaries"
        chapters = []
        edges = []
        for chapter in range(1, current + 1):
            path = self.root / "chapters" / config.CHAPTER_FILE_PATTERN.format(chapter)
            if not path.exists():
                continue
            content = path.read_text("utf-8", errors="replace")
            summary = self.storage.safe_read_json(summaries / config.SUMMARY_FILE_PATTERN.format(chapter), {})
            haystack = (content + "\n" + json.dumps(summary, ensure_ascii=False)).lower()
            matched = [keyword for keyword in keywords if keyword.lower() in haystack]
            exact = old_fact.lower() in haystack or new_fact.lower() in haystack
            include = chapter == source_chapter or exact or len(matched) >= 2
            if not include and source_chapter < chapter <= min(current, source_chapter + 3):
                include = True
                matched.append("状态传播窗口")
            if not include:
                continue
            direction = "修改点" if chapter == source_chapter else "前置铺垫" if chapter < source_chapter else "后续结果"
            dependency_types = self._dependency_types(summary, keywords)
            chapters.append({
                "chapter": chapter, "direction": direction, "matched": matched[:12],
                "reason": f"{direction}命中修改事实或其人物、物品、地点与结果依赖",
                "dependency_types": dependency_types,
                "recommended_action": "rewrite" if chapter == source_chapter else "patch",
            })
            if chapter != source_chapter:
                edges.append({
                    "from_chapter": chapter if chapter < source_chapter else source_chapter,
                    "to_chapter": source_chapter if chapter < source_chapter else chapter,
                    "direction": "supports" if chapter < source_chapter else "causes",
                    "types": dependency_types or ["文本关联"],
                })
        if not any(item["chapter"] == source_chapter for item in chapters):
            chapters.append({"chapter": source_chapter, "direction": "修改点", "matched": [], "reason": "用户指定的历史修改发生章", "recommended_action": "rewrite"})
        chapters.sort(key=lambda item: item["chapter"])
        ledger_impacts = HistoryImpactAnalyzer(self.root, self.storage).analyze(old_fact, new_fact, keywords)
        existing_chapters = {int(item["chapter"]) for item in chapters}
        rewrite_categories = {
            "facts", "timeline", "foreshadowing", "character_reviews",
            "story_logic", "entities", "state_cards",
            "story_clock",
        }
        for category, bucket in ledger_impacts.get("categories", {}).items():
            if category not in rewrite_categories:
                continue
            for record in bucket.get("items", []):
                for chapter in record.get("chapters", []):
                    chapter = int(chapter)
                    path = self.root / "chapters" / config.CHAPTER_FILE_PATTERN.format(chapter)
                    if chapter in existing_chapters or chapter < 1 or chapter > current or not path.exists():
                        continue
                    direction = "前置铺垫" if chapter < source_chapter else "后续结果"
                    chapters.append({
                        "chapter": chapter, "direction": direction,
                        "matched": record.get("matched", [])[:12],
                        "reason": f"{bucket.get('label', category)}中存在结构化依赖，需要复核正文证据",
                        "dependency_types": [bucket.get("label", category)],
                        "recommended_action": "patch",
                    })
                    edges.append({
                        "from_chapter": chapter if chapter < source_chapter else source_chapter,
                        "to_chapter": source_chapter if chapter < source_chapter else chapter,
                        "direction": "supports" if chapter < source_chapter else "causes",
                        "types": [bucket.get("label", category)],
                    })
                    existing_chapters.add(chapter)
        chapters.sort(key=lambda item: item["chapter"])
        return {
            "keywords": keywords, "chapters": chapters, "edges": edges,
            "earliest_chapter": chapters[0]["chapter"], "latest_chapter": chapters[-1]["chapter"],
            "backward_count": len([item for item in chapters if item["chapter"] < source_chapter]),
            "forward_count": len([item for item in chapters if item["chapter"] > source_chapter]),
            "ledger_impacts": ledger_impacts,
        }

    def run_branch(self, revision_id: str, progress=None) -> dict:
        manifest = self.get(revision_id)
        if manifest["status"] in {"committed", "aborted"}:
            raise ValueError("该历史修改已经结束")
        if not self.llm:
            raise RuntimeError("本地模型未连接")
        branch = self.revisions / revision_id
        items = manifest["impact"]["chapters"]
        completed = set(manifest.get("completed_chapters", []))
        for index, impact in enumerate(items):
            if self.get(revision_id).get("status") == "aborted":
                return self.get(revision_id)
            chapter = int(impact["chapter"])
            if chapter in completed:
                continue
            path = branch / "candidates" / config.CHAPTER_FILE_PATTERN.format(chapter)
            content = path.read_text("utf-8", errors="replace")
            system, prompt = self._revision_prompt(manifest, impact, content)
            revised = self.llm.chat(system, prompt, min(config.MODEL_CONFIG["max_output_tokens"], max(1800, int(len(content) / 1.5) + 1200)), task_type="revision")
            revised = revised.strip()
            if len(revised) < max(20, int(len(content) * 0.55)):
                raise RuntimeError(f"第{chapter}章修补结果异常偏短，分支已保留")
            self.storage.atomic_write_text(path, revised)
            completed.add(chapter)
            manifest["completed_chapters"] = sorted(completed)
            manifest["status"] = "rewriting"
            self.storage.atomic_write_json(branch / "manifest.json", manifest)
            if progress:
                progress(f"历史修改分支：第{chapter}章修补完成", 10 + int((index + 1) / len(items) * 70), "history_rewrite")
        manifest["validation"] = self.validate(revision_id)
        manifest["status"] = "validated" if manifest["validation"]["passed"] else "needs_review"
        manifest["updated_at"] = datetime.now().isoformat()
        self.storage.atomic_write_json(branch / "manifest.json", manifest)
        return manifest

    def validate(self, revision_id: str) -> dict:
        manifest = self.get(revision_id)
        branch = self.revisions / revision_id
        issues = []
        for impact in manifest["impact"]["chapters"]:
            chapter = int(impact["chapter"])
            original = (branch / "original" / config.CHAPTER_FILE_PATTERN.format(chapter)).read_text("utf-8", errors="replace")
            candidate = (branch / "candidates" / config.CHAPTER_FILE_PATTERN.format(chapter)).read_text("utf-8", errors="replace")
            if candidate == original:
                issues.append({"chapter": chapter, "severity": "high", "message": "候选正文没有产生变化"})
            target = max(1, len(re.sub(r"\s", "", original)))
            warnings = inspect_chapter(candidate, target) if target >= 300 else []
            for warning in warnings:
                if "短于目标90%" in warning or "完全重复" in warning or "模型说明" in warning:
                    issues.append({"chapter": chapter, "severity": "high", "message": warning})
            if chapter == int(manifest["source_chapter"]) and manifest["old_fact"] in candidate and manifest["new_fact"] not in candidate:
                issues.append({"chapter": chapter, "severity": "high", "message": "修改点仍保留旧事实且未体现新事实"})
        return {"passed": not any(item["severity"] == "high" for item in issues), "issues": issues, "checked_chapters": len(manifest["impact"]["chapters"])}

    def preview_candidates(self, revision_id: str) -> dict:
        manifest = self.get(revision_id)
        branch = self.revisions / revision_id
        items = []
        for impact in manifest.get("impact", {}).get("chapters", []):
            chapter = int(impact["chapter"])
            name = config.CHAPTER_FILE_PATTERN.format(chapter)
            original_path = branch / "original" / name
            candidate_path = branch / "candidates" / name
            if not original_path.exists() or not candidate_path.exists():
                continue
            original = original_path.read_text("utf-8", errors="replace")
            candidate = candidate_path.read_text("utf-8", errors="replace")
            diff = "".join(difflib.unified_diff(
                original.splitlines(keepends=True), candidate.splitlines(keepends=True),
                fromfile=f"第{chapter}章 原文", tofile=f"第{chapter}章 候选稿", n=3,
            ))
            items.append({
                "chapter": chapter, "direction": impact.get("direction", ""),
                "changed": original != candidate, "original_chars": len(original),
                "candidate_chars": len(candidate), "candidate": candidate,
                "diff": diff[:60000],
            })
        return {"id": revision_id, "status": manifest.get("status"), "validation": manifest.get("validation", {}), "items": items}

    def update_candidate(self, revision_id: str, chapter: int, content: str) -> dict:
        manifest = self.get(revision_id)
        if manifest.get("status") in {"committed", "aborted", "committing"}:
            raise ValueError("当前历史修改状态不能编辑候选稿")
        chapter = int(chapter)
        affected = {int(item["chapter"]) for item in manifest.get("impact", {}).get("chapters", [])}
        if chapter not in affected:
            raise ValueError("该章节不在历史修改影响范围内")
        if not content or not content.strip():
            raise ValueError("候选正文不能为空")
        path = self.revisions / revision_id / "candidates" / config.CHAPTER_FILE_PATTERN.format(chapter)
        if not path.exists():
            raise ValueError("候选章节不存在")
        self.storage.atomic_write_text(path, content.strip())
        manifest["validation"] = self.validate(revision_id)
        manifest["status"] = "validated" if manifest["validation"]["passed"] else "needs_review"
        manifest["updated_at"] = datetime.now().isoformat()
        self.storage.atomic_write_json(self.revisions / revision_id / "manifest.json", manifest)
        return manifest

    def commit(self, revision_id: str) -> dict:
        with FileLock(str(self.root / ".novel_mutation.lock"), timeout=600):
            return self._commit(revision_id)

    def _commit(self, revision_id: str) -> dict:
        manifest = self.get(revision_id)
        retryable_rollback = manifest.get("status") == "commit_failed_rolled_back" and manifest.get("commit_state") == "rolled_back"
        recovering_commit = manifest.get("status") == "committing" and manifest.get("commit_state") in {"backed_up", "applying"}
        if (manifest.get("status") != "validated" and not recovering_commit and not retryable_rollback) or not manifest.get("validation", {}).get("passed"):
            raise ValueError("历史修改分支尚未通过验证")
        branch = self.revisions / revision_id
        backup = branch / "transaction_backup"
        affected = [int(item["chapter"]) for item in manifest["impact"]["chapters"]]
        backup_complete = (backup / "backup_manifest.json").exists()
        if manifest.get("commit_state") in {"backed_up", "applying"} and backup_complete:
                self._restore_transaction(backup, affected)
                manifest["commit_state"] = "rolled_back_for_retry"
                self.storage.atomic_write_json(branch / "manifest.json", manifest)
        if backup.exists() and not backup_complete:
            shutil.rmtree(backup)
        if not (backup / "backup_manifest.json").exists():
            self._backup_transaction(backup, affected)
        manifest["status"] = "committing"
        manifest["commit_state"] = "backed_up"
        self.storage.atomic_write_json(branch / "manifest.json", manifest)
        try:
            manifest["commit_state"] = "applying"
            self.storage.atomic_write_json(branch / "manifest.json", manifest)
            for chapter in affected:
                name = config.CHAPTER_FILE_PATTERN.format(chapter)
                candidate = (branch / "candidates" / name).read_text("utf-8", errors="replace")
                self.storage.atomic_write_text(self.root / "chapters" / name, candidate)
            summaries = SummaryManager(self.nm, self.logger, self.llm)
            commits = ChapterCommitManager(self.root, self.logger, self.storage)
            commits.invalidate(affected)
            for chapter in affected:
                content = (self.root / "chapters" / config.CHAPTER_FILE_PATTERN.format(chapter)).read_text("utf-8", errors="replace")
                summary = summaries.generate_summary(chapter, content)
                if summary.get("analysis_degraded"):
                    raise RuntimeError(f"第{chapter}章结构化摘要失败，历史修改已回滚，请修复模型输出后重试")
                commits.mark(chapter, content, summary)
            self.rebuild_derived_state()
            chapter_files = [path for path in (self.root / "chapters").glob("*.txt") if path.stem.isdigit()]
            total_words = sum(len(re.sub(r"\s", "", path.read_text("utf-8", errors="replace"))) for path in chapter_files)
            latest_chapter = max((int(path.stem) for path in chapter_files), default=0)
            latest_summary = self.storage.safe_read_json(self.root / "summaries" / config.SUMMARY_FILE_PATTERN.format(latest_chapter), {})
            latest_summary = latest_summary if isinstance(latest_summary, dict) else {}
            self.nm.save_state({
                "current_chapter": latest_chapter,
                "total_words": total_words,
                "last_summary": latest_summary.get("summary", ""),
            })
            post_commit_warnings = []
            try:
                from core.chapter_post_commit import ChapterPostCommitProcessor
                processor = ChapterPostCommitProcessor(self.nm, self.logger, self.storage)
                for chapter in affected:
                    content = (self.root / "chapters" / config.CHAPTER_FILE_PATTERN.format(chapter)).read_text("utf-8", errors="replace")
                    summary = self.storage.safe_read_json(
                        self.root / "summaries" / config.SUMMARY_FILE_PATTERN.format(chapter), {},
                    )
                    processor.run(chapter, content, {"summary": summary})
            except Exception as exc:
                post_commit_warnings.append(f"历史正文已提交，但章节后处理失败：{exc}")
            self._invalidate_future_plans(min(affected))
            self._supersede_affected_turns(affected, revision_id)
            manifest["status"] = "committed"
            manifest["commit_state"] = "committed"
            manifest["committed_at"] = datetime.now().isoformat()
            manifest["post_commit_warnings"] = post_commit_warnings
            self.storage.atomic_write_json(branch / "manifest.json", manifest)
            return manifest
        except Exception:
            self._restore_transaction(backup, affected)
            manifest["status"] = "commit_failed_rolled_back"
            manifest["commit_state"] = "rolled_back"
            manifest["updated_at"] = datetime.now().isoformat()
            self.storage.atomic_write_json(branch / "manifest.json", manifest)
            raise

    def rebuild_derived_state(self):
        return DerivedStateRebuilder(self.root, self.logger, self.storage).rebuild(self.nm.get_current_chapter(), "历史修改提交后全量重建")

    def get(self, revision_id: str) -> dict:
        self._validate_id(revision_id)
        data = self.storage.safe_read_json(self.revisions / revision_id / "manifest.json", None)
        if not data:
            raise ValueError("历史修改分支不存在")
        return data

    def list(self) -> list[dict]:
        result = []
        for path in sorted(self.revisions.glob("*/manifest.json"), reverse=True) if self.revisions.exists() else []:
            data = self.storage.safe_read_json(path, {})
            if not isinstance(data, dict):
                continue
            result.append({key: data.get(key) for key in ("id", "status", "source_chapter", "old_fact", "new_fact", "mode", "created_at", "committed_at")})
        return result

    def abort(self, revision_id: str) -> dict:
        manifest = self.get(revision_id)
        if manifest.get("status") == "committed":
            raise ValueError("已提交的历史修改不能直接废弃，请创建反向修改")
        if manifest.get("status") == "committing":
            raise ValueError("历史修改正在原子提交，不能中途废弃")
        manifest["status"] = "aborted"
        manifest["updated_at"] = datetime.now().isoformat()
        self.storage.atomic_write_json(self.revisions / revision_id / "manifest.json", manifest)
        return manifest

    def _revision_prompt(self, manifest: dict, impact: dict, content: str) -> tuple[str, str]:
        custom = PromptSettingsManager(config.STORAGE_ROOT).instruction("history_revision")
        system = BASE_SYSTEM + """
你是小说历史连续性修订器。只输出修订后的完整章节正文，不输出标题、解释、修改清单或Markdown。
修改必须服从新的历史事实，同时尽量保留不受影响的情节、人物声音和文风。""" + (f"\n用户可编辑规则：{custom}" if custom else "")
        direction = impact["direction"]
        role = "调整此前铺垫，使新事实在发生章成立，但不能让事件提前发生" if direction == "前置铺垫" else "准确改写事实发生过程" if direction == "修改点" else "修正人物认知、动机、资源、关系和事件后果"
        mode_rule = {
            "minimal_patch": "采用最小改动，保留所有仍然成立的段落。",
            "range_rewrite": "允许重组本章场景，但必须保持未受影响的人物目标和有效事件。",
            "replan_forward": "修正文内后果，并为后续重新规划留下清晰、可执行的章末状态。",
        }.get(manifest.get("mode"), "采用最小必要改动。")
        prompt = f"""历史修改：把“{manifest['old_fact']}”改为“{manifest['new_fact']}”。
本章作用：{direction}；{role}。
修订方式：{mode_rule}
额外要求：{manifest.get('instruction') or '无'}
命中依赖：{'、'.join(impact.get('matched', [])) or '修改传播窗口'}

规则：
1. 只修改因历史变化而必须调整的内容，其余正文尽量保持。
2. 人物只能依据当时已经知道的信息行动。
3. 物品、位置、伤势、时间和关系变化必须有可见原因。
4. 不得为了迁就新事实新增机械降神或无铺垫巧合。

<chapter>
{content[:30000]}
</chapter>"""
        return system, prompt

    def _backup_transaction(self, backup: Path, affected: list[int]):
        existing = []
        existing_directories = []
        for chapter in affected:
            name = config.CHAPTER_FILE_PATTERN.format(chapter)
            for folder in ("chapters", "summaries"):
                source = self.root / folder / name
                if folder == "summaries":
                    source = source.with_suffix(".json")
                if source.exists():
                    existing.append(str(source.relative_to(self.root)).replace("\\", "/"))
                    destination = backup / folder / source.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
        for relative in self.DERIVED_DEFAULTS:
            source = self.root / relative
            if source.exists():
                existing.append(str(source.relative_to(self.root)).replace("\\", "/"))
                destination = backup / "derived" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        for relative in self.PLAN_DEFAULTS:
            source = self.root / relative
            if source.exists():
                existing.append(str(source.relative_to(self.root)).replace("\\", "/"))
                destination = backup / "plans" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        for name in self.DERIVED_DIRECTORIES:
            source = self.root / name
            if source.exists():
                existing_directories.append(name)
                shutil.copytree(
                    source, backup / "directories" / name,
                    ignore=shutil.ignore_patterns("*.lock", "*.tmp"), dirs_exist_ok=True,
                )
        self.storage.atomic_write_json(backup / "backup_manifest.json", {
            "existing": sorted(set(existing)),
            "existing_directories": sorted(set(existing_directories)),
        })

    def _restore_transaction(self, backup: Path, affected: list[int]):
        metadata = self.storage.safe_read_json(backup / "backup_manifest.json", {"existing": []})
        existing = set(metadata.get("existing", []))
        existing_directories = set(metadata.get("existing_directories", []))
        for chapter in affected:
            for folder, suffix in (("chapters", ".txt"), ("summaries", ".json")):
                source = backup / folder / f"{chapter:06d}{suffix}"
                destination = self.root / folder / source.name
                if source.exists():
                    self.storage.atomic_write_text(destination, source.read_text("utf-8", errors="replace"))
                elif str(destination.relative_to(self.root)).replace("\\", "/") not in existing and destination.exists():
                    destination.unlink()
        for relative, default in self.DERIVED_DEFAULTS.items():
            source = backup / "derived" / relative
            destination = self.root / relative
            if source.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            elif str(destination.relative_to(self.root)).replace("\\", "/") not in existing and destination.exists():
                destination.unlink()
        for relative, default in self.PLAN_DEFAULTS.items():
            source = backup / "plans" / relative
            destination = self.root / relative
            if source.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            elif str(destination.relative_to(self.root)).replace("\\", "/") not in existing and destination.exists():
                destination.unlink()
        for name in self.DERIVED_DIRECTORIES:
            source = backup / "directories" / name
            destination = self.root / name
            if destination.exists():
                shutil.rmtree(destination)
            if name in existing_directories and source.exists():
                shutil.copytree(source, destination)

    def _invalidate_future_plans(self, earliest: int):
        current = self.nm.get_current_chapter()
        for relative in (
            "outline/chapter_plans.json", "outline/chapter_briefs.json",
            "outline/scene_outlines.json", "outline/chapter_titles.json",
        ):
            path = self.root / relative
            data = self.storage.safe_read_json(path, {})
            if not isinstance(data, dict):
                continue
            changed = False
            for key in list(data):
                if str(key).isdigit() and int(key) > current:
                    data.pop(key, None)
                    changed = True
            if changed:
                self.storage.atomic_write_json(path, data)
        opening_path = self.root / "outline" / "opening_chapters.json"
        opening = self.storage.safe_read_json(opening_path, {})
        if isinstance(opening, dict) and isinstance(opening.get("chapters"), list):
            kept = [
                item for item in opening["chapters"]
                if not isinstance(item, dict) or self._safe_int(item.get("chapter")) <= current
            ]
            if len(kept) != len(opening["chapters"]):
                opening["chapters"] = kept
                self.storage.atomic_write_json(opening_path, opening)
        patrol_path = self.root / "planning" / "patrols.json"
        patrols = self.storage.safe_read_json(patrol_path, {"items": []})
        if isinstance(patrols, dict) and isinstance(patrols.get("items"), list):
            kept_patrols = [
                item for item in patrols["items"]
                if not isinstance(item, dict) or self._safe_int(item.get("chapter")) < earliest
            ]
            if len(kept_patrols) != len(patrols["items"]):
                patrols["items"] = kept_patrols
                self.storage.atomic_write_json(patrol_path, patrols)
        self.storage.atomic_write_json(self.root / "planning" / "history_revision_replan.json", {"earliest_changed": earliest, "next_planning_window": [current + 1, current + 3], "created_at": datetime.now().isoformat()})

    def _supersede_affected_turns(self, chapters: list[int], revision_id: str):
        path = self.root / "turns" / "index.json"
        if not path.exists():
            return
        targets = {int(chapter) for chapter in chapters}
        with FileLock(str(path) + ".lock", timeout=30):
            data = self.storage.safe_read_json(path, {"schema_version": 1, "items": []})
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                return
            changed = False
            for item in data["items"]:
                if not isinstance(item, dict) or item.get("status") != "committed":
                    continue
                try:
                    chapter = int(item.get("chapter", 0))
                except (TypeError, ValueError):
                    continue
                if chapter not in targets:
                    continue
                item.update({
                    "status": "superseded", "superseded_by": f"history_revision:{revision_id}",
                    "superseded_at": datetime.now().isoformat(),
                })
                changed = True
            if changed:
                self.storage.atomic_write_json(path, data)

    @staticmethod
    def _safe_int(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _keywords(text: str) -> list[str]:
        candidates = re.findall(r"[A-Za-z0-9_]{3,}|[\u4e00-\u9fff]{2,16}", text)
        stop = {"修改", "改为", "事实", "发生", "人物", "剧情", "以后", "之前", "这个", "一个", "仍然", "变成"}
        markers = (
            "死亡", "身亡", "存活", "重伤", "轻伤", "失踪", "昏迷", "苏醒", "复活",
            "身份", "本名", "父亲", "母亲", "种族", "性别", "位于", "属于", "拥有",
            "被毁", "毁坏", "摧毁", "获得", "失去",
        )
        result = []
        for word in candidates:
            parts = [word]
            for separator in ("成为", "改成", "改为", "其实是", "仍是", "不是", "是", "为"):
                if separator in word:
                    parts.extend(value for value in word.split(separator) if len(value) >= 2)
            for marker in markers:
                if marker in word:
                    parts.append(marker)
                    prefix = word.split(marker, 1)[0]
                    if 2 <= len(prefix) <= 6:
                        parts.append(prefix)
            for part in parts:
                if part not in stop and part not in result:
                    result.append(part)
        return result[:24]

    @staticmethod
    def _dependency_types(summary: dict, keywords: list[str]) -> list[str]:
        fields = {
            "事实": summary.get("facts", []), "人物状态": summary.get("characters_changed", []),
            "人物认知": summary.get("knowledge_changes", []), "因果": summary.get("causal_links", []),
            "伏笔承诺": summary.get("foreshadowing", []) + summary.get("narrative_promises", []),
            "资源地点关系": summary.get("items", []) + summary.get("locations", []) + summary.get("relationship_changes", []),
            "章节交接": summary.get("handoff", {}),
        }
        result = []
        for label, value in fields.items():
            text = json.dumps(value, ensure_ascii=False)
            if any(keyword in text for keyword in keywords):
                result.append(label)
        return result

    @staticmethod
    def _validate_id(value: str):
        if (
            not value or len(value) > 100
            or any(not (character.isalnum() or character in "_-") for character in value)
        ):
            raise ValueError("无效的历史修改ID")
