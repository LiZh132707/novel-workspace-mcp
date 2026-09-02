import logging
import tempfile
from pathlib import Path

from core.change_review_manager import ChangeReviewManager
from core.character_manager import CharacterManager
from core.foreshadow_manager import ForeshadowManager
from core.fact_manager import FactManager
from core.entity_ledger import EntityLedger
from core.state_card_manager import StateCardManager
from core.story_logic_manager import StoryLogicManager
from core.timeline_manager import TimelineManager
from storage_utils import StorageManager


def test_tracking_readers_tolerate_partially_corrupt_records():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        logger = logging.getLogger("test-corrupt-tracking")
        storage = StorageManager(logger)
        storage.atomic_write_json(root / "facts.json", {
            "facts": [
                {"subject": "林舟", "predicate": "身份", "object": "调查员", "chapter": "损坏"},
                {"unexpected": True},
            ],
            "conflicts": [],
        })
        assert FactManager(root, logger, storage).preview_conflicts(2, [
            {"subject": "林舟", "predicate": "身份", "object": "医生"},
        ])[0]["previous_chapter"] == 0
        storage.atomic_write_json(root / "tracking" / "state_cards.json", {
            "character": {"林舟": {"fields": "损坏", "history": "损坏"}},
            "item": [],
        })
        cards = StateCardManager(root, logger, storage).get()
        assert cards["character"]["林舟"]["fields"] == {}
        assert cards["item"] == {}
        storage.atomic_write_json(root / "foreshadowing.json", {
            "items": [{"status": "open", "text": "钥匙", "target_chapter": "损坏"}, None],
        })
        assert ForeshadowManager(root, logger, storage).open_items(5)[0]["text"] == "钥匙"


def test_foreshadow_writer_recovers_from_wrong_file_shape():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        logger = logging.getLogger("foreshadow-write-recovery")
        storage = StorageManager(logger)
        storage.atomic_write_json(root / "foreshadowing.json", ["损坏结构"])
        manager = ForeshadowManager(root, logger, storage)
        result = manager.ingest(1, [{"text": "门后的红灯", "target_chapter": 5}])
        assert result["introduced"] == 1
        assert manager.list()[0]["text"] == "门后的红灯"


def test_character_change_requires_decision():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        logger = logging.getLogger("test")
        characters = CharacterManager(root, logger)
        characters.create_character("林舟")
        reviews = ChangeReviewManager(root, logger)
        reviews.add_from_summary(2, [{"name": "林舟", "field": "current_status", "new_value": "受伤", "evidence": "手臂流血"}])
        assert characters.get_character("林舟")["current_status"] == "存活"
        review = reviews.list()[0]
        reviews.decide(review["id"], True)
        assert characters.get_character("林舟")["current_status"] == "受伤"


def test_unverified_new_character_is_not_added_to_review_queue():
    with tempfile.TemporaryDirectory() as tmp:
        reviews = ChangeReviewManager(Path(tmp), logging.getLogger("unverified-character"))
        added = reviews.add_new_characters(2, [{
            "name": "幻觉人物", "evidence": "正文不存在", "evidence_verified": False,
        }])
        assert added == 0
        assert reviews.list() == []


def test_unverified_model_memory_does_not_enter_persistent_ledgers():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        logger = logging.getLogger("unverified-memory")
        storage = StorageManager(logger)
        unverified = {"evidence": "不存在的证据", "evidence_verified": False}
        facts = FactManager(root, logger, storage)
        assert facts.add_from_summary(1, [{
            "subject": "出口", "predicate": "状态", "object": "封锁", **unverified,
        }])["added"] == 0
        foreshadows = ForeshadowManager(root, logger, storage)
        assert foreshadows.ingest(1, [{
            "action": "introduce", "text": "虚构伏笔", **unverified,
        }])["introduced"] == 0
        logic = StoryLogicManager(root, logger, storage)
        logic.ingest(1, {
            "narrative_promises": [{"text": "虚构承诺", **unverified}],
            "causal_links": [{"cause": "虚构原因", "effect": "虚构结果", **unverified}],
            "knowledge_changes": [{"name": "林舟", "fact": "虚构认知", **unverified}],
        })
        assert logic.get() == {"promises": [], "causal_links": [], "character_knowledge": {}}
        entities = EntityLedger(root, logger, storage)
        entities.ingest(1, {"items": [{"name": "虚构钥匙", "owner": "林舟", **unverified}]})
        assert entities.get()["items"] == {}


def test_character_review_reader_ignores_corrupt_entries():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        logger = logging.getLogger("corrupt-character-review")
        storage = StorageManager(logger)
        storage.atomic_write_json(root / "reviews" / "character_changes.json", {
            "items": [
                None, "损坏",
                {"status": "accepted", "name": "林舟", "field": "current_status", "chapter": "损坏", "new_value": "存活"},
            ],
        })
        manager = ChangeReviewManager(root, logger, storage)
        assert len(manager.list(None)) == 1
        assert manager.character_status_at("林舟", 1) == "存活"


def test_character_status_history_does_not_leak_future_death():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        logger = logging.getLogger("test")
        characters = CharacterManager(root, logger)
        characters.create_character("林舟")
        reviews = ChangeReviewManager(root, logger)
        reviews.add_from_summary(5, [{"name": "林舟", "field": "current_status", "new_value": "死亡", "evidence": "停止呼吸"}])
        reviews.decide(reviews.list()[0]["id"], True)
        assert reviews.character_status_at("林舟", 4, "死亡") == "存活"
        assert reviews.character_status_at("林舟", 5, "存活") == "死亡"


def test_new_character_requires_acceptance():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        logger = logging.getLogger("test")
        reviews = ChangeReviewManager(root, logger)
        reviews.add_new_characters(3, [{
            "name": "苏遥", "personality": "冷静", "evidence": "苏遥递出证件",
            "personality_profile": {
                "desire": "查明真相", "flaw": "不愿解释自己的判断",
                "stress_response": "越危险越简短地下命令",
            },
        }])
        assert CharacterManager(root, logger).get_character("苏遥") is None
        review = reviews.list()[0]
        reviews.decide(review["id"], True)
        assert CharacterManager(root, logger).get_character("苏遥")["personality"] == "冷静"
        profile = CharacterManager(root, logger).get_character("苏遥")["personality_profile"]
        assert profile["desire"] == "查明真相"
        assert profile["stress_response"] == "越危险越简短地下命令"


def test_unsupported_character_change_does_not_create_unresolvable_review():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        logger = logging.getLogger("test")
        CharacterManager(root, logger).create_character("林舟")
        reviews = ChangeReviewManager(root, logger)
        assert reviews.add_from_summary(2, [{"name": "林舟", "field": "模型乱写字段", "new_value": "值"}]) == 0
        assert reviews.list() == []


def test_foreshadow_lifecycle_and_overdue():
    with tempfile.TemporaryDirectory() as tmp:
        manager = ForeshadowManager(Path(tmp), logging.getLogger("test"))
        manager.ingest(1, [{"action": "introduce", "text": "红色钥匙的来历", "target_chapter": 3}])
        assert manager.list(4)[0]["overdue"] is True
        manager.ingest(5, [{"action": "resolve", "text": "红色钥匙的来历"}])
        assert manager.list(5)[0]["status"] == "resolved"


def test_timeline_add_is_idempotent_for_same_event():
    with tempfile.TemporaryDirectory() as tmp:
        manager = TimelineManager(Path(tmp), logging.getLogger("test"))
        first = manager.add_event(2, "午夜", "钟楼", "林舟发现钥匙", ["苏遥", "林舟"])
        second = manager.add_event(2, "午夜", "钟楼", "林舟发现钥匙", ["林舟", "苏遥"])
        assert first["id"] == second["id"]
        assert len(manager.get_events_by_chapter(2)) == 1


def test_replacing_automatic_timeline_keeps_manual_events():
    with tempfile.TemporaryDirectory() as tmp:
        manager = TimelineManager(Path(tmp), logging.getLogger("test"))
        manual = manager.add_event(2, "午夜", "钟楼", "手动记录的重要事件", ["林舟"])
        manager.add_event(2, "午夜", "钟楼", "旧章节摘要", ["林舟"], source="chapter_summary")
        assert manager.remove_auto_events(2) == 1
        manager.add_event(2, "凌晨", "广场", "新章节摘要", ["林舟"], source="chapter_summary")
        events = manager.get_events_by_chapter(2)
        assert {item["id"] for item in events if item["source"] == "manual"} == {manual["id"]}
        assert [item["event"] for item in events if item["source"] == "chapter_summary"] == ["新章节摘要"]


def test_fact_conflict_preview_does_not_write_and_ignores_mutable_changes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manager = FactManager(root, logging.getLogger("test"))
        manager.add_from_summary(1, [
            {"subject": "林舟", "predicate": "身份", "object": "记者"},
            {"subject": "林舟", "predicate": "所在地", "object": "车站"},
        ])
        before = manager.load()
        conflicts = manager.preview_conflicts(2, [
            {"subject": "林舟", "predicate": "身份", "object": "警察"},
            {"subject": "林舟", "predicate": "所在地", "object": "医院"},
        ])
        assert len(conflicts) == 1 and conflicts[0]["predicate"] == "身份"
        assert manager.load() == before


def test_fact_preview_blocks_unconfirmed_character_death_against_roster():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        logger = logging.getLogger("life-state-conflict")
        CharacterManager(root, logger).create_character("沈川", status="存活")
        manager = FactManager(root, logger)
        conflicts = manager.preview_conflicts(1, [{
            "subject": "沈川", "predicate": "状态", "object": "已死亡",
            "evidence": "沈川已经死亡", "evidence_verified": True,
        }])
        assert len(conflicts) == 1
        assert "生死变化必须明确确认" in conflicts[0]["message"]
        assert manager.load()["facts"] == []
