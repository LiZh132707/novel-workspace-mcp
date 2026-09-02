"""逐场景长章的细粒度恢复检查点。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from filelock import FileLock

from storage_utils import StorageManager


class SceneCheckpointManager:
    def __init__(self, novel_path: Path, storage: StorageManager):
        self.root = novel_path / "drafts" / "scene_checkpoints"
        self.storage = storage

    def load(self, chapter: int, expected: dict) -> list[str]:
        path = self._path(chapter)
        with FileLock(str(path) + ".lock", timeout=30):
            data = self.storage.safe_read_json(path, {})
            if not isinstance(data, dict) or any(data.get(key) != value for key, value in expected.items()):
                return []
            parts = data.get("parts", [])
            return [str(part) for part in parts if str(part).strip()] if isinstance(parts, list) else []

    def save(self, chapter: int, expected: dict, parts: list[str]):
        path = self._path(chapter)
        with FileLock(str(path) + ".lock", timeout=30):
            self.storage.atomic_write_json(path, {
                **expected, "chapter": int(chapter), "completed_scenes": len(parts),
                "parts": list(parts), "updated_at": datetime.now().isoformat(),
            })

    def clear(self, chapter: int):
        path = self._path(chapter)
        with FileLock(str(path) + ".lock", timeout=30):
            path.unlink(missing_ok=True)

    def _path(self, chapter: int) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root / f"{int(chapter):06d}.json"
