import logging
import tempfile
from pathlib import Path

from core.settings_manager import SettingsManager


def test_settings_enforce_hardware_limits():
    with tempfile.TemporaryDirectory() as tmp:
        manager = SettingsManager(Path(tmp) / "settings.json", logging.getLogger("test"))
        values = manager.update({"default_target_words": 99999, "default_batch_chapters": 99, "model_concurrency": 9})
        assert values["default_target_words"] == 20000
        assert values["default_batch_chapters"] == 10
        assert values["model_concurrency"] == 1


def test_seed_mode_and_fixed_seed_are_persisted():
    with tempfile.TemporaryDirectory() as tmp:
        manager = SettingsManager(Path(tmp) / "settings.json", logging.getLogger("test"))
        settings = manager.update({"seed_mode": "fixed", "fixed_seed": 123456})
        assert settings["seed_mode"] == "fixed"
        assert settings["fixed_seed"] == 123456
        settings = manager.update({"seed_mode": "anything", "fixed_seed": -5})
        assert settings["seed_mode"] == "random"
        assert settings["fixed_seed"] == 0


def test_performance_and_creativity_settings_are_bounded():
    with tempfile.TemporaryDirectory() as tmp:
        manager = SettingsManager(Path(tmp) / "settings.json", logging.getLogger("test"))
        settings = manager.update({"auto_warmup": False, "speed_warning_ratio": 2, "creativity_mode": "open"})
        assert settings["auto_warmup"] is False
        assert settings["speed_warning_ratio"] == 0.95
        assert settings["creativity_mode"] == "open"


def test_settings_wrong_json_shape_degrades_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("[]", "utf-8")
    settings = SettingsManager(path, logging.getLogger("settings-shape-test")).get()
    assert settings["default_target_words"] == 5000
    assert settings["model_concurrency"] == 1
