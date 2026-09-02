"""用户可调整设置，硬件安全限制由服务端强制。"""
from pathlib import Path

from storage_utils import StorageManager


DEFAULT_SETTINGS = {
    "default_target_words": 5000,
    "default_batch_chapters": 3,
    "stop_batch_on_warning": True,
    "auto_save_drafts": True,
    "max_batch_chapters": 10,
    "model_concurrency": 1,
    "seed_mode": "random",
    "fixed_seed": 42,
    "auto_warmup": True,
    "speed_warning_ratio": 0.7,
    "creativity_mode": "balanced",
    "chapter_commit_mode": "balanced",
}


class SettingsManager:
    def __init__(self, path: Path, logger):
        self.path = path
        self.storage = StorageManager(logger)

    def get(self) -> dict:
        saved = self.storage.safe_read_json(self.path, {})
        saved = saved if isinstance(saved, dict) else {}
        return {**DEFAULT_SETTINGS, **saved, "model_concurrency": 1, "max_batch_chapters": 10}

    def update(self, values: dict) -> dict:
        current = self.get()
        if "default_target_words" in values:
            current["default_target_words"] = max(500, min(20000, int(values["default_target_words"])))
        if "default_batch_chapters" in values:
            current["default_batch_chapters"] = max(1, min(10, int(values["default_batch_chapters"])))
        for key in ("stop_batch_on_warning", "auto_save_drafts"):
            if key in values:
                current[key] = bool(values[key])
        if "auto_warmup" in values:
            current["auto_warmup"] = bool(values["auto_warmup"])
        if "speed_warning_ratio" in values:
            current["speed_warning_ratio"] = max(0.4, min(0.95, float(values["speed_warning_ratio"])))
        if "creativity_mode" in values:
            current["creativity_mode"] = values["creativity_mode"] if values["creativity_mode"] in {"stable", "balanced", "open"} else "balanced"
        if "chapter_commit_mode" in values:
            current["chapter_commit_mode"] = values["chapter_commit_mode"] if values["chapter_commit_mode"] in {"review", "balanced", "automatic"} else "balanced"
        if "seed_mode" in values:
            current["seed_mode"] = "fixed" if values["seed_mode"] == "fixed" else "random"
        if "fixed_seed" in values:
            current["fixed_seed"] = max(0, min(2147483647, int(values["fixed_seed"])))
        self.storage.atomic_write_json(self.path, current)
        return current
