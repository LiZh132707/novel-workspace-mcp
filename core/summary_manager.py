"""摘要管理器：支持 LLM 生成的结构化摘要。"""
import json
from datetime import datetime
from typing import Optional

from filelock import FileLock

from config import MODEL_CONFIG, SUMMARY_FILE_PATTERN
from storage_utils import StorageManager
from core.novel_manager import NovelManager
from core.character_manager import CharacterManager
from core.ai_contracts import (
    chapter_source_hash, parse_object, summary_prompts, validate_summary,
)


class SummaryManager:
    def __init__(self, novel_manager: NovelManager, logger, llm_client=None):
        self.nm = novel_manager
        self.path = novel_manager.path / "summaries"
        self.logger = logger
        self.llm = llm_client
        self.storage = StorageManager(logger)

    def generate_summary(self, chapter_number: int, content: str) -> dict:
        """生成章节摘要。如果有 LLM 客户端则调用 AI 生成，否则用截取。"""
        self.path.mkdir(parents=True, exist_ok=True)

        if self.llm and len(content) > 100:
            summary_data = self._llm_summary(chapter_number, content)
        else:
            summary_data = self._basic_summary(chapter_number, content)

        fname = SUMMARY_FILE_PATTERN.format(chapter_number)
        self.storage.atomic_write_json(self.path / fname, summary_data)
        self._update_long_term_memory()
        return summary_data

    def _update_long_term_memory(self):
        """每十章生成一次无额外模型调用的剧情弧压缩，控制长期上下文体积。"""
        files = sorted(
            (path for path in self.path.glob("*.json") if path.stem.isdigit()),
            key=lambda path: int(path.stem),
        )
        if len(files) < 10:
            return
        completed = len(files) // 10 * 10
        groups = []
        for start in range(0, completed, 10):
            items = [self.storage.safe_read_json(path, {}) for path in files[start:start + 10]]
            summaries = [str(item.get("summary", "")).strip()[:240] for item in items if item]
            if summaries:
                chapter_numbers = [int(item.get("chapter", int(files[start + offset].stem))) for offset, item in enumerate(items)]
                groups.append({"start_chapter": min(chapter_numbers), "end_chapter": max(chapter_numbers), "summary": "｜".join(summaries)[:2200]})
        self.storage.atomic_write_json(self.path / "long_term.json", {"arcs": groups})

    def _llm_summary(self, chapter_number: int, content: str) -> dict:
        """调用 LLM 生成结构化摘要。"""
        current_plan, next_plan = self._chapter_plans(chapter_number)
        character_profiles = self._character_profiles(content)
        system, prompt = summary_prompts(
            chapter_number, content, current_plan, next_plan, character_profiles,
        )
        try:
            result = self.llm.chat(
                system, prompt, max_tokens=int(MODEL_CONFIG.get("summary_max_tokens", 2400)),
                task_type="structured",
            )
            data = validate_summary(parse_object(result), chapter_number, content)
            data["created_at"] = datetime.now().isoformat()
            return data
        except Exception as e:
            self.logger.warning("LLM 摘要失败，使用基础摘要: %s", e)
            return self._basic_summary(chapter_number, content, degraded=True, error=str(e))

    def _chapter_plans(self, chapter_number: int) -> tuple[str, str]:
        briefs = self.storage.safe_read_json(self.nm.path / "outline" / "chapter_briefs.json", {})
        current = briefs.get(str(chapter_number), {})
        following = briefs.get(str(chapter_number + 1), {})
        return (
            json.dumps(current, ensure_ascii=False)[:7000] if current else "",
            json.dumps(following, ensure_ascii=False)[:3000] if following else "",
        )

    def _character_profiles(self, content: str) -> str:
        manager = CharacterManager(self.nm.path, self.logger)
        profiles = []
        included_names = set()
        for item in manager.list_characters():
            name = str(item.get("name", ""))
            if not name or name not in content:
                continue
            detail = manager.get_character(name) or {}
            profiles.append({
                "name": name,
                "personality": detail.get("personality", ""),
                "personality_profile": detail.get("personality_profile", {}),
                "current_status": detail.get("current_status", ""),
                "relationships": detail.get("relationships", ""),
                "provisional": False,
            })
            included_names.add(name)
            if len(profiles) >= 12:
                break
        reviews = self.storage.safe_read_json(
            self.nm.path / "reviews" / "character_changes.json", {"items": []},
        )
        review_items = reviews.get("items", []) if isinstance(reviews, dict) else []
        for item in review_items if isinstance(review_items, list) else []:
            if len(profiles) >= 12:
                break
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            details = item.get("details", {}) if isinstance(item.get("details"), dict) else {}
            if (
                item.get("status") != "pending" or item.get("field") != "new_character"
                or not name or name in included_names or name not in content
            ):
                continue
            profiles.append({
                "name": name,
                "personality": details.get("personality", ""),
                "personality_profile": details.get("personality_profile", {}),
                "current_status": "待确认",
                "relationships": details.get("relationships", ""),
                "provisional": True,
            })
            included_names.add(name)
        return json.dumps(profiles, ensure_ascii=False)[:8000] if profiles else ""

    def _basic_summary(self, chapter_number: int, content: str, degraded: bool = False, error: str = "") -> dict:
        """基础摘要（截取前200字）。"""
        tail = content.strip()[-240:]
        return {
            "chapter": chapter_number,
            "summary": content[:200] + "..." if len(content) > 200 else content,
            "characters_changed": [],
            "new_characters": [],
            "character_decisions": [],
            "world_rule_changes": [],
            "new_information": [],
            "foreshadowing": [],
            "facts": [],
            "narrative_promises": [],
            "causal_links": [],
            "knowledge_changes": [],
            "locations": [],
            "factions": [],
            "items": [],
            "relationship_changes": [],
            "source_hash": chapter_source_hash(content),
            "memory_schema_version": 1,
            "analysis_degraded": bool(degraded),
            "analysis_error": str(error)[:500] if degraded else "",
            "handoff": {
                "final_scene": {"location": "", "story_time": "", "active_characters": [], "last_action": tail},
                "state_changes": [], "knowledge_changes": [], "commitments": [], "open_loops": [],
                "immediate_next_intent": "", "evidence_quotes": [tail] if tail else [],
            },
            "plan_reconciliation": {
                "completed_goals": [], "unfinished_goals": [], "deviations": [], "new_constraints": [],
                "next_chapter_impacts": [], "evidence_quotes": [], "review_status": "unavailable",
            },
            "next_goal": "",
            "created_at": datetime.now().isoformat(),
        }

    def save_custom_summary(self, chapter_number: int, summary_data: dict):
        self.path.mkdir(parents=True, exist_ok=True)
        summary_data["chapter"] = chapter_number
        summary_data["created_at"] = datetime.now().isoformat()
        fname = SUMMARY_FILE_PATTERN.format(chapter_number)
        self.storage.atomic_write_json(self.path / fname, summary_data)
        self._update_long_term_memory()

    def get_summary(self, chapter_number: int) -> Optional[dict]:
        fname = SUMMARY_FILE_PATTERN.format(chapter_number)
        p = self.path / fname
        data = self.storage.safe_read_json(p, None)
        return data if isinstance(data, dict) else None

    def ensure_continuity_memory(self, chapter_number: int, content: str) -> dict:
        with FileLock(str(self.nm.path / ".novel_mutation.lock"), timeout=600):
            return self._ensure_continuity_memory(chapter_number, content)

    def _ensure_continuity_memory(self, chapter_number: int, content: str) -> dict:
        current = self.get_summary(chapter_number) or {}
        if current.get("source_hash") == chapter_source_hash(content) and current.get("handoff"):
            return current
        rebuilt = self._basic_summary(chapter_number, content)
        self.path.mkdir(parents=True, exist_ok=True)
        self.storage.atomic_write_json(self.path / SUMMARY_FILE_PATTERN.format(chapter_number), rebuilt)
        return rebuilt

    def review_memory(self, chapter_number: int, status: str, edits: dict | None = None) -> dict:
        with FileLock(str(self.nm.path / ".novel_mutation.lock"), timeout=600):
            return self._review_memory(chapter_number, status, edits)

    def _review_memory(self, chapter_number: int, status: str, edits: dict | None = None) -> dict:
        data = self.get_summary(chapter_number)
        if not data:
            raise ValueError("章节记忆不存在")
        if status not in {"confirmed", "dismissed", "pending"}:
            raise ValueError("未知审核状态")
        edits = edits or {}
        handoff = data.setdefault("handoff", {})
        reconciliation = data.setdefault("plan_reconciliation", {})
        for key in ("state_changes", "knowledge_changes", "commitments", "open_loops"):
            if isinstance(edits.get(key), list):
                handoff[key] = [str(item)[:500] for item in edits[key] if str(item).strip()][:20]
        if "immediate_next_intent" in edits:
            handoff["immediate_next_intent"] = str(edits["immediate_next_intent"])[:500]
        for key in ("completed_goals", "unfinished_goals", "deviations", "new_constraints", "next_chapter_impacts"):
            if isinstance(edits.get(key), list):
                reconciliation[key] = [str(item)[:500] for item in edits[key] if str(item).strip()][:16]
        reconciliation["review_status"] = status
        data["memory_review_status"] = status
        self.storage.atomic_write_json(self.path / SUMMARY_FILE_PATTERN.format(chapter_number), data)
        return data

    def get_recent_summaries(self, count: int = 5) -> list[dict]:
        if not self.path.exists():
            return []
        files = sorted(
            (path for path in self.path.glob("*.json") if path.stem.isdigit()),
            key=lambda path: int(path.stem), reverse=True,
        )
        result = []
        for f in files[:count]:
            try:
                item = json.loads(f.read_text("utf-8"))
                if isinstance(item, dict):
                    result.append(item)
            except Exception:
                pass
        return result
