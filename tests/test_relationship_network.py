import logging
import asyncio
import json
import hashlib
from types import SimpleNamespace

import httpx
import pytest

from core.character_manager import CharacterManager
from core.entity_ledger import EntityLedger


@pytest.fixture
def relationship_world(tmp_path):
    logger = logging.getLogger("relationships")
    characters = CharacterManager(tmp_path, logger)
    characters.create_character("Alice", role_tier="主角", relationships="friend: Bob，Cara：guide；unresolved narrative")
    characters.create_character("Bob", role_tier="NPC", appearance_start=2, appearance_end=8)
    characters.create_character("Cara", role_tier="重要配角")
    return characters, EntityLedger(tmp_path, logger)


def test_backfilled_chapter_does_not_replace_newer_relationship(relationship_world):
    _, ledger = relationship_world
    ledger.ingest(10, {"relationship_changes": [{"from": "Alice", "to": "Cara", "type": "ally", "strength": 80}]})
    ledger.ingest(2, {"relationship_changes": [{"from": "Alice", "to": "Cara", "type": "rival", "strength": -60}]})
    current = ledger.compact_context()["relationships"]
    assert current[0]["type"] == "ally"


def test_rejected_evidence_does_not_enter_relationship_ledger(relationship_world):
    _, ledger = relationship_world
    ledger.ingest(1, {"relationship_changes": [{"from": "Alice", "to": "Cara", "type": "enemy", "evidence_verified": False}]})
    assert ledger.get()["relationships"] == []


def test_nonfinite_strength_does_not_crash():
    assert EntityLedger._strength(float("inf")) == 0
    assert EntityLedger._strength(float("-inf")) == 0


def test_snapshot_preserves_direction_and_uses_last_record_within_same_chapter(relationship_world):
    _, ledger = relationship_world
    ledger.ingest(3, {"relationship_changes": [
        {"from": "Alice", "to": "Bob", "type": "friendly"},
        {"from": "Bob", "to": "Alice", "type": "suspicious"},
    ]})
    ledger.ingest(8, {"relationship_changes": [{"from": "Alice", "to": "Bob", "type": "rival"}]})
    ledger.ingest(3, {"relationship_changes": [{"from": "Alice", "to": "Bob", "type": "trusted", "evidence": "shared secret"}]})
    snapshot = ledger.relationship_snapshot(3)
    states = {(e["from"], e["to"]): e["type"] for e in snapshot["relationships"]}
    assert states == {("Alice", "Bob"): "trusted", ("Bob", "Alice"): "suspicious"}
    assert all(item["chapter"] <= 3 for item in snapshot["history"])
    assert ledger.relationship_snapshot()["relationships"][0]["type"] == "rival"


def test_npc_filter_keeps_connected_characters_and_respects_appearance_range(relationship_world):
    characters, ledger = relationship_world
    ledger.ingest(3, {"relationship_changes": [
        {"from": "Alice", "to": "Bob", "type": "ally", "strength": 70},
        {"from": "Alice", "to": "Cara", "type": "mentor"},
    ]})
    view = characters.get_character_network(chapter=3, role_tier="NPC")
    assert {node["id"] for node in view["nodes"]} == {"Alice", "Bob"}
    assert len(view["edges"]) == 1
    assert view["edges"][0]["strength"] == 70
    assert characters.get_character_network(chapter=9, role_tier="NPC")["nodes"] == []
    assert characters.get_character_network(chapter=1)["edges"] == []


def test_legacy_prose_is_typed_deduplicated_and_never_invents_characters(relationship_world):
    characters, _ = relationship_world
    view = characters.get_character_network()
    assert {(e["to"], e["type"]) for e in view["edges"]} == {("Bob", "friend"), ("Cara", "guide")}
    assert {n["id"] for n in view["nodes"]} == {"Alice", "Bob", "Cara"}
    assert view["profile_notes"] == [{"character": "Alice", "text": "unresolved narrative"}]
    assert characters.get_character_network(chapter=20)["profile_notes"] == []


def test_ledger_takes_precedence_over_profile_and_retains_old_durable_edges(relationship_world):
    characters, ledger = relationship_world
    ledger.ingest(1, {"relationship_changes": [{"from": "Alice", "to": "Bob", "type": "rival"}]})
    for number in range(2, 40):
        ledger.ingest(number, {"relationship_changes": [{"from": "Cara", "to": f"Visitor{number}", "type": "met"}]})
    view = characters.get_character_network(character="Bob")
    assert len(view["edges"]) == 1
    assert view["edges"][0]["type"] == "rival"
    visitor = characters.get_character_network(character="Visitor39")
    assert next(n for n in visitor["nodes"] if n["id"] == "Visitor39")["registered"] is False


def test_dirty_records_and_character_metadata_do_not_crash_or_rewrite_files(relationship_world):
    characters, ledger = relationship_world
    ledger.storage.atomic_write_json(ledger.path, {"relationships": [
        None, [], {"from": "Alice", "to": "Alice", "chapter": 1},
        {"from": "Alice", "to": "Cara", "chapter": "broken"},
        {"from": "Alice", "to": "Cara", "chapter": 2, "evidence_verified": False},
        {"from": " Alice ", "to": "Cara ", "chapter": "4", "strength": "信任"},
    ]})
    path = characters.path / "Alice.json"
    data = json.loads(path.read_text("utf-8"))
    data.update({"name": "../WrongName", "last_chapter": "broken", "role_tier": {}})
    characters.storage.atomic_write_json(path, data)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (path, ledger.path)}
    view = characters.get_character_network(chapter=4)
    assert view["edges"] == [{"from": "Alice", "to": "Cara", "chapter": 4, "strength": 70, "type": "Unknown", "evidence": "", "source": "ledger"}]
    assert characters.get_character("Alice")["name"] == "Alice"
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in before.items())


@pytest.mark.parametrize("kwargs", [{"chapter": -1}, {"chapter": True}, {"chapter": 2.5}, {"role_tier": "wrong"}, {"character": "missing"}])
def test_invalid_network_filters_are_rejected(relationship_world, kwargs):
    with pytest.raises(ValueError):
        relationship_world[0].get_character_network(**kwargs)


def test_network_api_and_mcp_share_the_same_result(relationship_world, monkeypatch):
    from ui import app as web
    import novel_server

    characters, ledger = relationship_world
    ledger.ingest(3, {"relationship_changes": [{"from": "Alice", "to": "Bob", "type": "ally", "evidence": "They agreed to cooperate."}]})
    monkeypatch.setattr(web, "get_novel_manager", lambda name: SimpleNamespace(path=characters.path.parent))
    monkeypatch.setattr(web, "get_character_manager", lambda novel: characters)
    monkeypatch.setattr(novel_server, "crm", lambda: characters)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url="http://test") as client:
            response = await client.get("/api/novels/Test/character-network", params={"chapter": 3, "role_tier": "NPC"})
            assert response.status_code == 200
            assert response.json()["network"] == json.loads(await novel_server.get_character_network(chapter=3, role_tier="NPC"))
            assert (await client.get("/api/novels/Test/character-network?chapter=-1")).status_code == 400
            assert (await client.get("/api/novels/Test/character-network?chapter=abc")).status_code == 422
            schema = next(t for t in await novel_server.list_tools() if t.name == "get_character_network").inputSchema
            assert "NPC" in schema["properties"]["role_tier"]["enum"]
    asyncio.run(run())


def test_network_api_requires_registered_project(monkeypatch):
    from fastapi import HTTPException
    from ui import app as web

    def missing(name):
        raise HTTPException(404, "Project not found")
    monkeypatch.setattr(web, "get_novel_manager", missing)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url="http://test") as client:
            assert (await client.get("/api/novels/Unknown/character-network")).status_code == 404
    asyncio.run(run())
