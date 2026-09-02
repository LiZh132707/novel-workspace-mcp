"""章节提交后的确定性派生处理。"""
from __future__ import annotations

from core.character_evolution import CharacterEvolutionTracker
from core.character_manager import CharacterManager
from core.timeline_manager import TimelineManager
from storage_utils import StorageManager


class ChapterPostCommitProcessor:
    """统一维护人物出场、人物演变与章节级时间线。"""

    def __init__(self, novel_manager, logger=None, storage: StorageManager | None = None):
        self.nm = novel_manager
        self.logger = logger
        self.storage = storage or novel_manager.storage or StorageManager(logger)
        self.characters = CharacterManager(novel_manager.path, logger)
        self.evolution = CharacterEvolutionTracker(novel_manager.path, logger, self.storage)
        self.timeline = TimelineManager(novel_manager.path, logger)

    def run(self, chapter: int, content: str, result: dict) -> dict:
        mentioned = []
        for item in self.characters.list_characters():
            name = str(item.get("name", "")).strip()
            if name and name in content:
                self.characters.update_character(name, last_chapter=chapter)
                mentioned.append(name)
        self.evolution.scan_chapter(chapter, content)
        event = self._record_timeline(chapter, result.get("summary", {}), mentioned)
        return {"mentioned_characters": mentioned, "timeline_event": event}

    def _record_timeline(self, chapter: int, summary: dict, mentioned: list[str]) -> dict | None:
        self.timeline.remove_auto_events(chapter)
        if not isinstance(summary, dict):
            return None
        handoff = summary.get("handoff", {}) if isinstance(summary.get("handoff"), dict) else {}
        final_scene = handoff.get("final_scene", {}) if isinstance(handoff.get("final_scene"), dict) else {}
        event_text = str(summary.get("summary", "")).strip()
        if not event_text:
            event_text = str(final_scene.get("last_action", "")).strip()
        if not event_text:
            return None
        event_text = event_text[:600]
        location = str(final_scene.get("location", "")).strip() or self._first_location(summary) or "未知地点"
        story_time = str(final_scene.get("story_time", "")).strip() or f"第{chapter}章"
        active = final_scene.get("active_characters", [])
        characters = [str(value).strip() for value in active if str(value).strip()] if isinstance(active, list) else []
        if not characters:
            characters = mentioned
        return self.timeline.add_event(
            chapter, story_time, location, event_text, characters, source="chapter_summary",
        )

    @staticmethod
    def _first_location(summary: dict) -> str:
        locations = summary.get("locations", [])
        if not isinstance(locations, list):
            return ""
        for item in locations:
            if isinstance(item, dict) and str(item.get("name", "")).strip():
                return str(item["name"]).strip()
        return ""
