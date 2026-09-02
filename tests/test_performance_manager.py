import logging
import tempfile
from pathlib import Path

from core.performance_manager import PerformanceManager


def test_performance_history_and_profiles_are_persisted():
    with tempfile.TemporaryDirectory() as tmp:
        manager = PerformanceManager(Path(tmp) / "performance.json", logging.getLogger("test"))
        manager.record({"tokens_per_second": 52.3, "completion_tokens": 700}, "benchmark")
        data = manager.save_profile("高速", {"runtime": "Vulkan", "cpu_experts": 18, "context_length": 32768})
        assert data["history"][0]["tokens_per_second"] == 52.3
        assert data["profiles"]["高速"]["cpu_experts"] == 18
        profile = manager.save_profile("128K", {"context_length": 131072})
        assert profile["profiles"]["128K"]["context_length"] == 131072


def test_performance_history_is_bounded():
    with tempfile.TemporaryDirectory() as tmp:
        manager = PerformanceManager(Path(tmp) / "performance.json", logging.getLogger("test"))
        for index in range(35):
            manager.record({"tokens_per_second": index}, "chapter")
        assert len(manager.get()["history"]) == 30


def test_performance_manager_recovers_from_wrong_json_shape(tmp_path):
    path = tmp_path / "performance.json"
    path.write_text("[]", "utf-8")
    manager = PerformanceManager(path, logging.getLogger("performance-shape-test"))
    assert manager.get()["history"] == []
    manager.record({"tokens_per_second": 50}, "benchmark")
    assert manager.get()["history"][0]["tokens_per_second"] == 50
