"""记录章节正文及全部派生状态已经完成提交。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core.ai_contracts import chapter_source_hash
from storage_utils import StorageManager


class ChapterCommitManager:
    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.root = novel_path
        self.path = novel_path / "tracking" / "chapter_commits.json"
        self.storage = storage or StorageManager(logger)

    def is_committed(self, chapter: int, content: str) -> bool:
        item = self.get(chapter)
        content_hash = chapter_source_hash(content)
        if item.get("content_hash") != content_hash or item.get("status") != "committed":
            return False
        summary = self.storage.safe_read_json(self.root / "summaries" / f"{int(chapter):06d}.json", {})
        return bool(
            isinstance(summary, dict)
            and summary.get("source_hash") == content_hash
            and item.get("summary_hash") == content_hash
        )

    def get(self, chapter: int) -> dict:
        item = self._load().get(str(int(chapter)), {})
        return item if isinstance(item, dict) else {}

    def mark(self, chapter: int, content: str, summary: dict) -> dict:
        data = self._load()
        item = {
            "chapter": int(chapter), "status": "committed", "content_hash": chapter_source_hash(content),
            "summary_hash": summary.get("source_hash", ""), "committed_at": datetime.now().isoformat(),
        }
        data[str(int(chapter))] = item
        self.storage.atomic_write_json(self.path, data)
        return item

    def invalidate_from(self, chapter: int):
        data = self._load()
        changed = False
        for key in list(data):
            if str(key).isdigit() and int(key) >= int(chapter):
                data.pop(key, None)
                changed = True
        if changed:
            self.storage.atomic_write_json(self.path, data)

    def invalidate(self, chapters: list[int]):
        targets = {str(int(chapter)) for chapter in chapters}
        data = self._load()
        if any(key in data for key in targets):
            for key in targets:
                data.pop(key, None)
            self.storage.atomic_write_json(self.path, data)

    def _load(self) -> dict:
        data = self.storage.safe_read_json(self.path, {})
        return data if isinstance(data, dict) else {}
