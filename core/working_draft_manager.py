"""批量生成工作草稿的可验证持久化。"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from core.ai_contracts import chapter_source_hash
from storage_utils import StorageManager


class WorkingDraftManager:
    def __init__(self, novel_path: Path, storage: StorageManager):
        self.root = novel_path / "drafts"
        self.storage = storage

    def load(self, chapter: int, expected: dict) -> str | None:
        text_path, metadata_path = self._paths(chapter)
        with self.storage._get_lock(text_path):
            if not text_path.exists():
                return None
            content = text_path.read_text("utf-8", errors="replace")
            metadata = self.storage.safe_read_json(metadata_path, {})
            valid = (
                len(content.strip()) > 200
                and isinstance(metadata, dict)
                and metadata.get("content_hash") == chapter_source_hash(content)
                and all(metadata.get(key) == value for key, value in expected.items())
            )
            if valid:
                return content
            self._archive_locked(chapter, text_path, metadata_path)
            return None

    def save(self, chapter: int, content: str, metadata: dict):
        text_path, metadata_path = self._paths(chapter)
        with self.storage._get_lock(text_path):
            self.storage.atomic_write_text(text_path, content)
            payload = dict(metadata)
            payload.update({
                "chapter": int(chapter),
                "content_hash": chapter_source_hash(content),
                "updated_at": datetime.now().isoformat(),
            })
            self.storage.atomic_write_json(metadata_path, payload)

    def clear(self, chapter: int):
        text_path, metadata_path = self._paths(chapter)
        with self.storage._get_lock(text_path):
            text_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)

    def _paths(self, chapter: int) -> tuple[Path, Path]:
        self.root.mkdir(parents=True, exist_ok=True)
        stem = f"{int(chapter):06d}_working"
        return self.root / f"{stem}.txt", self.root / f"{stem}.json"

    def _archive_locked(self, chapter: int, text_path: Path, metadata_path: Path):
        recovery = self.root / "recovery"
        recovery.mkdir(parents=True, exist_ok=True)
        suffix = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:6]
        if text_path.exists():
            text_path.replace(recovery / f"{int(chapter):06d}_{suffix}.txt")
        if metadata_path.exists():
            metadata_path.replace(recovery / f"{int(chapter):06d}_{suffix}.json")
