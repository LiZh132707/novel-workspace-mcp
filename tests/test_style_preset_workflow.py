import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

import novel_server
from core.style_preset import StylePresetManager
from ui import app as web


@pytest.fixture
def manager(tmp_path):
    return StylePresetManager(tmp_path, logging.getLogger("preset-test"))


def test_explicit_source_never_falls_back_and_builtin_is_a_copy(manager):
    assert manager.get_preset("悬疑推理", source="custom") is None
    manager.save_preset("MyStyle", "Custom", ["Short sentences"])
    assert manager.get_preset("MyStyle", source="builtin") is None
    assert manager.get_preset("MyStyle", source="custom")["name"] == "MyStyle"
    builtin = manager.get_preset("悬疑推理", source="builtin")
    builtin["traits"].clear()
    assert manager.get_preset("悬疑推理")["traits"]
    with pytest.raises(ValueError):
        manager.get_preset("MyStyle", source="other")


@pytest.mark.parametrize("data", [[], None, {"description": []}, {"traits": "text"}, {"avoid": [1]}])
def test_malformed_custom_presets_are_skipped(manager, data):
    (manager.path / "Broken.json").write_text(json.dumps(data), encoding="utf-8")
    assert manager.get_preset("Broken", source="custom") is None
    assert not any(p["name"] == "Broken" for p in manager.list_presets())


def test_stored_name_does_not_override_file_identity(manager):
    (manager.path / "Valid.json").write_text('{"name":"../wrong","traits":[]}', encoding="utf-8")
    assert manager.get_preset("Valid")["name"] == "Valid"
    assert any(p["name"] == "Valid" for p in manager.list_presets())


def test_web_preview_matches_mcp_and_never_changes_story_bible(manager, tmp_path, monkeypatch):
    manager.save_preset("悬疑推理", "Custom prose", ["Short sentences"], ["Repetition"])
    style = tmp_path / "bible" / "style.md"
    style.write_text("Original style", encoding="utf-8")
    monkeypatch.setattr(web, "get_novel_manager", lambda name: SimpleNamespace(path=tmp_path))
    monkeypatch.setattr(novel_server, "stm_", lambda: manager)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url="http://test") as client:
            listing = (await client.get("/api/novels/Test/style-presets")).json()
            assert len([p for p in listing["presets"] if p["name"] == "悬疑推理"]) == 2
            preview = await client.get("/api/novels/Test/style-presets/preview", params={"preset": "悬疑推理", "source": "custom"})
            assert preview.status_code == 200
            data = preview.json()
            mcp = json.loads(await novel_server.get_style_preset("悬疑推理", source="custom", include_rendered=True))
            assert data["style_text"] == mcp["style_text"]
            assert "Custom prose" in data["style_text"] and "## Avoid" in data["style_text"]
            assert (await client.get("/api/novels/Test/style-presets/preview", params={"preset": "Absent"})).status_code == 404
            for params in ({"preset": "../wrong"}, {"preset": "悬疑推理", "source": "wrong"}):
                assert (await client.get("/api/novels/Test/style-presets/preview", params=params)).status_code == 400
    asyncio.run(run())
    assert style.read_text("utf-8") == "Original style"


def test_preset_routes_require_registered_project(monkeypatch):
    from fastapi import HTTPException

    def missing(name):
        raise HTTPException(404, "Project not found")

    monkeypatch.setattr(web, "get_novel_manager", missing)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url="http://test") as client:
            assert (await client.get("/api/novels/Unknown/style-presets")).status_code == 404
            assert (await client.get("/api/novels/Unknown/style-presets/preview?preset=Missing")).status_code == 404
    asyncio.run(run())


def test_mcp_style_schema_exposes_selection_and_rendering():
    tool = next(t for t in asyncio.run(novel_server.list_tools()) if t.name == "get_style_preset")
    assert tool.inputSchema["properties"]["source"]["enum"] == ["auto", "builtin", "custom"]
    assert tool.inputSchema["properties"]["prefer_custom"]["type"] == "boolean"
    assert tool.inputSchema["properties"]["include_rendered"]["type"] == "boolean"
