"""记录最终 Prompt，并支持基线差异回归。"""
from __future__ import annotations

import difflib
import hashlib
import re
from datetime import datetime
from pathlib import Path

from storage_utils import StorageManager


class PromptSnapshotManager:
    def __init__(self, storage_root: Path, logger=None):
        self.root = storage_root / "prompt_snapshots"
        self.storage = StorageManager(logger)

    def record(self, task_type: str, system: str, prompt: str, parameters: dict | None = None) -> dict:
        task = self._task_name(task_type)
        content = f"【SYSTEM】\n{system}\n\n【USER】\n{prompt}"
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        record = {
            "task_type": task, "hash": digest, "system": system, "prompt": prompt,
            "parameters": parameters or {}, "created_at": datetime.now().isoformat(),
        }
        self.storage.atomic_write_json(self.root / "latest" / f"{task}.json", record)
        return record

    def set_baseline(self, task_type: str) -> dict:
        task = self._task_name(task_type)
        latest = self.storage.safe_read_json(self.root / "latest" / f"{task}.json", None)
        if not latest:
            raise ValueError("该任务尚无 Prompt 快照")
        self.storage.atomic_write_json(self.root / "baselines" / f"{task}.json", latest)
        return latest

    def compare(self, task_type: str) -> dict:
        task = self._task_name(task_type)
        latest = self.storage.safe_read_json(self.root / "latest" / f"{task}.json", None)
        baseline = self.storage.safe_read_json(self.root / "baselines" / f"{task}.json", None)
        if not latest:
            raise ValueError("该任务尚无 Prompt 快照")
        if not baseline:
            return {"task_type": task, "status": "no_baseline", "latest": latest}
        old = (baseline.get("system", "") + "\n" + baseline.get("prompt", "")).splitlines()
        new = (latest.get("system", "") + "\n" + latest.get("prompt", "")).splitlines()
        diff = list(difflib.unified_diff(old, new, fromfile="baseline", tofile="latest", lineterm=""))
        return {"task_type": task, "status": "same" if not diff else "changed", "diff": diff[:1200], "latest_hash": latest["hash"], "baseline_hash": baseline["hash"]}

    def list_tasks(self) -> list[dict]:
        latest_dir = self.root / "latest"
        result = []
        for path in sorted(latest_dir.glob("*.json")) if latest_dir.exists() else []:
            data = self.storage.safe_read_json(path, {})
            result.append({"task_type": data.get("task_type", path.stem), "hash": data.get("hash", ""), "created_at": data.get("created_at", ""), "has_baseline": (self.root / "baselines" / path.name).exists()})
        return result

    def latest_reference(self, task_type: str) -> dict:
        task = self._task_name(task_type)
        data = self.storage.safe_read_json(self.root / "latest" / f"{task}.json", {})
        if not isinstance(data, dict):
            return {}
        return {
            "task_type": data.get("task_type", task),
            "prompt_hash": data.get("hash", ""),
            "parameters": data.get("parameters", {}),
            "created_at": data.get("created_at", ""),
        }

    @staticmethod
    def _task_name(task_type: str) -> str:
        task = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(task_type or "general"))
        task = task.strip(". _")[:80]
        return task or "general"
