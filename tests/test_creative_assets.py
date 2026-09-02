import logging
import tempfile
from pathlib import Path

from core.creative_assets import CreativeAssetManager
from core.foreshadow_manager import ForeshadowManager


def test_creative_assets_crud():
    with tempfile.TemporaryDirectory() as tmp:
        manager = CreativeAssetManager(Path(tmp), logging.getLogger("test"))
        item = manager.save("scenes", {"name": "雨夜车站", "description": "主角发现线索"})
        assert manager.list("scenes")[0]["name"] == "雨夜车站"
        manager.save("scenes", {**item, "description": "主角发现伪造线索"})
        assert manager.list("scenes")[0]["description"] == "主角发现伪造线索"
        assert manager.delete("scenes", item["id"])


def test_foreshadow_can_be_rescheduled_and_cancelled():
    with tempfile.TemporaryDirectory() as tmp:
        manager = ForeshadowManager(Path(tmp), logging.getLogger("test"))
        manager.ingest(1, [{"text": "红色车票", "target_chapter": 8}])
        item = manager.list()[0]
        updated = manager.update(item["id"], target_chapter=12, status="cancelled")
        assert updated["target_chapter"] == 12
        assert updated["status"] == "cancelled"
