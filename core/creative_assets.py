"""场景、名词、世界实体和剧情沙盒的统一资产库。"""
from datetime import datetime
from pathlib import Path
import uuid
from filelock import FileLock

from storage_utils import StorageManager


ASSET_TYPES = {
    "scenes", "locations", "factions", "items", "glossary", "sandboxes",
    "subplots", "secrets", "calendar", "routes", "resources", "conditions",
    "dependencies", "questions", "inspirations", "research",
    "inventory", "workflows", "publications", "visual_prompts",
}


class CreativeAssetManager:
    def __init__(self, novel_path: Path, logger):
        self.path = novel_path / "planning" / "creative_assets.json"
        self.storage = StorageManager(logger)

    def get(self) -> dict:
        data = self.storage.safe_read_json(self.path, {})
        data = data if isinstance(data, dict) else {}
        return {
            kind: list(data.get(kind, [])) if isinstance(data.get(kind), list) else []
            for kind in ASSET_TYPES
        }

    def list(self, kind: str) -> list[dict]:
        self._check(kind)
        return self.get()[kind]

    def save(self, kind: str, values: dict) -> dict:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._save(kind, values)

    def _save(self, kind: str, values: dict) -> dict:
        self._check(kind)
        data = self.get()
        asset_id = str(values.get("id", "")).strip()
        item = next((entry for entry in data[kind] if entry.get("id") == asset_id), None)
        now = datetime.now().isoformat(timespec="seconds")
        clean = {key: value for key, value in values.items() if key not in {"created_at", "updated_at"}}
        if item:
            item.update(clean)
            item["updated_at"] = now
        else:
            item = {**clean, "id": uuid.uuid4().hex, "created_at": now, "updated_at": now}
            data[kind].append(item)
        self.storage.atomic_write_json(self.path, data)
        return item

    def delete(self, kind: str, asset_id: str) -> bool:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._delete(kind, asset_id)

    def _delete(self, kind: str, asset_id: str) -> bool:
        self._check(kind)
        data = self.get()
        before = len(data[kind])
        data[kind] = [item for item in data[kind] if item.get("id") != asset_id]
        self.storage.atomic_write_json(self.path, data)
        return len(data[kind]) < before

    @staticmethod
    def _check(kind: str):
        if kind not in ASSET_TYPES:
            raise ValueError("不支持的创作资产类型")
