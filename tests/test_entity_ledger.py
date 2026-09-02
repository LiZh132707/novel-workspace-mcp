import logging
import tempfile
from pathlib import Path

from core.entity_ledger import EntityLedger
from storage_utils import StorageManager


def test_entity_ledger_tracks_item_and_relationship_history():
    with tempfile.TemporaryDirectory() as tmp:
        ledger = EntityLedger(Path(tmp), logging.getLogger("test"))
        ledger.ingest(3, {
            "items": [{"name": "铜钥匙", "owner": "林舟", "status": "完好"}],
            "locations": [{"name": "旧档案馆", "status": "封闭"}],
            "relationship_changes": [{"from": "林舟", "to": "苏遥", "type": "合作", "strength": 20}],
        })
        data = ledger.get()
        assert data["items"]["铜钥匙"]["owner"] == "林舟"
        assert data["relationships"][0]["type"] == "合作"


def test_entity_ledger_keeps_early_current_relationship_in_long_history():
    with tempfile.TemporaryDirectory() as tmp:
        ledger = EntityLedger(Path(tmp), logging.getLogger("long-relationship-test"))
        relationships = [
            {"from": "林舟", "to": "苏遥", "type": "长期盟友", "strength": 80},
            *[
                {"from": f"人物{index}", "to": f"对象{index}", "type": "交集", "strength": index % 100}
                for index in range(550)
            ],
        ]
        ledger.ingest(20, {"relationship_changes": relationships})
        data = ledger.get()
        context = ledger.compact_context()
        assert len(data["relationships"]) == 551
        assert any(
            item["from"] == "林舟" and item["to"] == "苏遥"
            for item in context["relationships"]
        )


def test_entity_ledger_recovers_from_wrong_json_shapes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        logger = logging.getLogger("damaged-entity-test")
        storage = StorageManager(logger)
        storage.atomic_write_json(root / "tracking" / "entities.json", {
            "locations": [], "factions": "损坏", "items": None, "relationships": {},
        })
        ledger = EntityLedger(root, logger, storage)
        assert ledger.get() == {"locations": {}, "factions": {}, "items": {}, "relationships": []}
        ledger.ingest(1, {"locations": [{"name": "车站", "status": "封锁"}]})
        assert ledger.get()["locations"]["车站"]["status"] == "封锁"
