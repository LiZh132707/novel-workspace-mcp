"""本地模型性能历史与硬件配置档案。"""
from datetime import datetime
from pathlib import Path
from filelock import FileLock

from storage_utils import StorageManager


class PerformanceManager:
    def __init__(self, path: Path, logger):
        self.path = path
        self.storage = StorageManager(logger)

    def get(self) -> dict:
        data = self.storage.safe_read_json(self.path, {})
        data = data if isinstance(data, dict) else {}
        history = data.get("history", [])
        profiles = data.get("profiles", {})
        return {
            "history": list(history)[-30:] if isinstance(history, list) else [],
            "profiles": profiles if isinstance(profiles, dict) else {},
            "active_profile": data.get("active_profile", "balanced"),
        }

    def record(self, metrics: dict, label: str = "generation") -> dict:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            data = self.get()
            item = {"at": datetime.now().isoformat(timespec="seconds"), "label": label, **metrics}
            data["history"] = (data["history"] + [item])[-30:]
            self.storage.atomic_write_json(self.path, data)
            return item

    def save_profile(self, name: str, values: dict) -> dict:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            data = self.get()
            safe_name = (name or "balanced").strip()[:32]
            data["profiles"][safe_name] = {
                "runtime": str(values.get("runtime", "Vulkan"))[:40],
                "cpu_experts": max(0, min(64, int(values.get("cpu_experts", 18)))),
                "context_length": max(4096, min(262144, int(values.get("context_length", 32768)))),
                "note": str(values.get("note", ""))[:300],
            }
            data["active_profile"] = safe_name
            self.storage.atomic_write_json(self.path, data)
            return data
