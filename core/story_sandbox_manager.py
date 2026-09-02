"""剧情分支沙盒：候选方向只有采纳后才进入正式规划。"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from filelock import FileLock

from storage_utils import StorageManager


class StorySandboxManager:
    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.path = novel_path / "planning" / "sandboxes.json"
        self.storage = storage or StorageManager(logger)

    def list(self) -> list[dict]:
        return [dict(item) for item in self._load()["items"]]

    def save_variants(self, origin_chapter: int, question: str, variants: list[dict]) -> dict:
        clean = []
        for index, item in enumerate(variants[:5]):
            if not isinstance(item, dict):
                continue
            clean.append({
                "id": uuid.uuid4().hex[:12],
                "title": str(item.get("title", f"方向{index + 1}"))[:120],
                "direction": str(item.get("direction") or item.get("summary", ""))[:3000],
                "benefits": [str(value)[:500] for value in item.get("benefits", [])][:8],
                "risks": [str(value)[:500] for value in item.get("risks", [])][:8],
                "required_setup": [str(value)[:500] for value in item.get("required_setup", [])][:8],
                "status": "candidate",
            })
        if len(clean) < 2:
            raise ValueError("剧情沙盒至少需要两个有效方向")
        record = {"id": uuid.uuid4().hex, "origin_chapter": int(origin_chapter), "question": str(question)[:1000], "variants": clean, "status": "open", "created_at": datetime.now().isoformat()}
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            data = self._load()
            data["items"].append(record)
            data["items"] = self._prune_items(data["items"])
            self.storage.atomic_write_json(self.path, data)
        return record

    def adopt(self, sandbox_id: str, variant_id: str) -> dict:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            data = self._load()
            record = next((item for item in data["items"] if item.get("id") == sandbox_id), None)
            if not record:
                raise ValueError("剧情沙盒不存在")
            variant = next((item for item in record.get("variants", []) if item.get("id") == variant_id), None)
            if not variant:
                raise ValueError("候选方向不存在")
            for item in record["variants"]:
                item["status"] = "adopted" if item["id"] == variant_id else "rejected"
            record["status"] = "adopted"
            record["adopted_at"] = datetime.now().isoformat()
            self.storage.atomic_write_json(self.path, data)
            return variant

    @staticmethod
    def _prune_items(items: list[dict], terminal_limit: int = 60) -> list[dict]:
        terminal = [item for item in items if item.get("status") != "open"]
        dropped = {id(item) for item in terminal[:-terminal_limit]} if len(terminal) > terminal_limit else set()
        return [item for item in items if id(item) not in dropped]

    def _load(self) -> dict:
        data = self.storage.safe_read_json(self.path, {"items": []})
        items = data.get("items", []) if isinstance(data, dict) else []
        return {"items": [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []}
