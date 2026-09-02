"""新小说工程格式定义；当前不承担旧版本迁移。"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from storage_utils import StorageManager


PROJECT_SCHEMA_VERSION = 1
PROJECT_TYPE = "novel-turn-engine"


class ProjectSchemaManager:
    def __init__(self, novel_path: Path, storage: StorageManager | None = None):
        self.root = novel_path
        self.path = novel_path / "project.json"
        self.storage = storage or StorageManager(logging.getLogger("project-schema"))

    def initialize(self, name: str) -> dict:
        manifest = {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "project_type": PROJECT_TYPE,
            "name": name,
            "created_at": datetime.now().isoformat(),
            "canonical_content": "chapters",
            "draft_content": "turns/drafts",
            "turn_index": "turns/index.json",
        }
        self.storage.atomic_write_json(self.path, manifest)
        return manifest

    def validate(self) -> dict:
        data = self.storage.safe_read_json(self.path, None)
        if not isinstance(data, dict):
            return {"valid": False, "reason": "工程清单缺失或损坏"}
        if data.get("schema_version") != PROJECT_SCHEMA_VERSION:
            return {"valid": False, "reason": "工程 schema 与当前程序不一致"}
        if data.get("project_type") != PROJECT_TYPE:
            return {"valid": False, "reason": "不是章节回合制小说工程"}
        return {"valid": True, "manifest": data}
