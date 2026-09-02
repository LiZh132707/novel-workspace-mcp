
"""时间线管理器：记录和查询小说中的事件。"""
import json
import uuid
from pathlib import Path
from filelock import FileLock

from storage_utils import StorageManager


class TimelineManager:
    def __init__(self, novel_path: Path, logger):
        self.path = novel_path / "timeline"
        self.logger = logger
        self.storage = StorageManager(logger)
        self.path.mkdir(parents=True, exist_ok=True)

    def add_event(self, chapter: int, time: str, location: str, event: str,
                  characters: list[str] = None, source: str = "manual") -> dict:
        if not isinstance(chapter, int) or chapter < 1:
            raise ValueError(f"事件章节号必须为正整数: {chapter}")
        normalized_characters = sorted({str(value).strip() for value in (characters or []) if str(value).strip()})
        with FileLock(str(self.path / ".timeline.transaction.lock"), timeout=30):
            for path in self.path.glob(f"{chapter:06d}_*.json"):
                existing = self.storage.safe_read_json(path, {})
                if (
                    str(existing.get("time", "")) == str(time)
                    and str(existing.get("location", "")) == str(location)
                    and str(existing.get("event", "")) == str(event)
                    and sorted(existing.get("characters", [])) == normalized_characters
                    and str(existing.get("source", "manual")) == str(source or "manual")
                ):
                    return existing
            evt_id = str(uuid.uuid4())[:8]
            data = {
                "id": evt_id,
                "chapter": chapter,
                "time": time,
                "location": location,
                "event": event,
                "characters": normalized_characters,
                "source": str(source or "manual")[:40],
            }
            fname = f"{data['chapter']:06d}_{evt_id}.json"
            self.storage.atomic_write_json(self.path / fname, data)
            self.logger.info("添加事件: 第%d章 %s", chapter, event[:30])
            return data

    def remove_auto_events(self, chapter: int) -> int:
        removed = 0
        with FileLock(str(self.path / ".timeline.transaction.lock"), timeout=30):
            for path in self.path.glob(f"{int(chapter):06d}_*.json"):
                data = self.storage.safe_read_json(path, {})
                if data.get("source") == "chapter_summary":
                    path.unlink(missing_ok=True)
                    removed += 1
        return removed

    def query_timeline(self, character: str = None, chapter: int = None,
                       keyword: str = None, limit: int = 20) -> list[dict]:
        results = []
        files = sorted(self.path.glob("*.json"))
        for f in files:
            try:
                data = json.loads(f.read_text("utf-8"))
            except Exception:
                continue
            if character and character not in data.get("characters", []):
                continue
            if chapter is not None and data.get("chapter") != chapter:
                continue
            if keyword and keyword not in data.get("event", "") and keyword not in data.get("location", ""):
                continue
            results.append(data)
            if len(results) >= limit:
                break
        return results

    def get_recent_events(self, count: int = 10) -> list[dict]:
        files = sorted(self.path.glob("*.json"), reverse=True)
        result = []
        for f in files[:count]:
            try:
                result.append(json.loads(f.read_text("utf-8")))
            except Exception:
                pass
        return result

    def get_events_by_chapter(self, chapter: int) -> list[dict]:
        return self.query_timeline(chapter=chapter)
