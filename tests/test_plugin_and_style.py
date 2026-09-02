import logging
import tempfile
from pathlib import Path

import pytest

from core.plugin_manager import BasePlugin, EventBus
from core.style_preset import StylePresetManager


class DemoPlugin(BasePlugin):
    def transform(self, value):
        return value + "-插件"

    def observe_only(self, _value):
        return None


def test_disabled_plugin_is_skipped_by_emit_pipeline_and_collect():
    bus = EventBus(logging.getLogger("test"))
    plugin = DemoPlugin("demo", bus, logging.getLogger("test"))
    bus.register(plugin)
    assert bus.emit_pipeline("transform", "原文") == "原文-插件"
    plugin.enabled = False
    assert bus.emit_pipeline("transform", "原文") == "原文"
    assert bus.collect("transform", "原文") == []


def test_pipeline_none_result_keeps_previous_data():
    bus = EventBus(logging.getLogger("test"))
    plugin = DemoPlugin("demo", bus, logging.getLogger("test"))
    bus.register(plugin)
    assert bus.emit_pipeline("observe_only", "原文") == "原文"


def test_event_bus_uses_stable_snapshot_when_plugin_unregisters_during_emit():
    bus = EventBus(logging.getLogger("snapshot-test"))
    calls = []

    class RemovingPlugin(BasePlugin):
        def fire(self):
            calls.append("first")
            bus.unregister(second)

    class SecondPlugin(BasePlugin):
        def fire(self):
            calls.append("second")

    first = RemovingPlugin("first", bus, logging.getLogger("snapshot-test"))
    second = SecondPlugin("second", bus, logging.getLogger("snapshot-test"))
    bus.register(first)
    bus.register(second)
    bus.emit("fire")
    assert calls == ["first", "second"]
    calls.clear()
    bus.emit("fire")
    assert calls == ["first"]


def test_style_preset_rejects_path_traversal_and_limits_fields():
    with tempfile.TemporaryDirectory() as tmp:
        manager = StylePresetManager(Path(tmp), logging.getLogger("test"))
        with pytest.raises(ValueError):
            manager.save_preset("../越界", "描述", ["特征"])
        item = manager.save_preset("克制风格", "描述" * 1000, ["特征"] * 40, ["避免"] * 40)
        assert len(item["description"]) == 1000
        assert len(item["traits"]) == 30
        assert manager.get_preset("克制风格")["builtin"] is False
