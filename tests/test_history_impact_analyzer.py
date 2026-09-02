import logging
import tempfile
from pathlib import Path

from core.history_impact_analyzer import HistoryImpactAnalyzer
from core.history_revision_manager import HistoryRevisionManager
from core.novel_manager import NovelManager
from storage_utils import StorageManager


LOGGER = logging.getLogger("history-impact-test")


def test_history_impact_analyzer_finds_cross_ledger_dependencies():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        storage.atomic_write_json(root / "characters" / "顾临川.json", {"name": "顾临川", "current_status": "死亡", "last_chapter": 20})
        storage.atomic_write_json(root / "facts.json", {"facts": [
            {"chapter": 20, "subject": "顾临川", "predicate": "状态", "object": "死亡"},
            {"chapter": 18, "subject": "林舟", "predicate": "状态", "object": "死亡"},
        ], "conflicts": []})
        storage.atomic_write_json(root / "foreshadowing.json", {"items": [{"introduced_chapter": 5, "target_chapter": 20, "text": "顾临川留下的遗书"}]})
        storage.atomic_write_json(root / "timeline" / "000020_event.json", {"chapter": 20, "event": "顾临川在爆炸中死亡"})
        storage.atomic_write_json(root / "tracking" / "story_logic.json", {"promises": [{"introduced_chapter": 3, "text": "顾临川必须活着交出证据"}]})
        storage.atomic_write_json(root / "outline" / "chapter_briefs.json", {"21": {"title": "死亡余波", "synopsis": "众人处理顾临川死亡的后果"}})
        result = HistoryImpactAnalyzer(root, storage).analyze(
            "顾临川死亡", "顾临川重伤失踪", ["顾临川", "死亡", "失踪"],
        )
        assert {"characters", "facts", "timeline", "foreshadowing", "story_logic", "planning"} <= set(result["categories"])
        assert {3, 5, 20, 21} <= set(result["affected_chapters"])
        assert result["risk_level"] == "高"
        assert result["categories"]["facts"]["count"] == 1


def test_history_impact_analyzer_does_not_modify_ledgers():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        path = root / "facts.json"
        original = {"facts": [{"chapter": 1, "subject": "林舟", "predicate": "身份", "object": "记者"}], "conflicts": []}
        storage.atomic_write_json(path, original)
        HistoryImpactAnalyzer(root, storage).analyze("林舟是记者", "林舟是警察", ["林舟", "记者", "警察"])
        assert storage.safe_read_json(path, {}) == original


def test_revision_manager_splits_fact_sentence_for_cross_ledger_matching():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("影响书", root, LOGGER, storage)
        storage.atomic_write_text(root / "chapters" / "000001.txt", "顾临川在爆炸后停止呼吸。")
        novel.save_state({"current_chapter": 1})
        storage.atomic_write_json(root / "characters" / "顾临川.json", {"name": "顾临川", "current_status": "死亡"})
        storage.atomic_write_json(root / "facts.json", {"facts": [{"chapter": 1, "subject": "顾临川", "predicate": "状态", "object": "死亡"}], "conflicts": []})
        impact = HistoryRevisionManager(novel, LOGGER, None, storage).analyze(1, "顾临川死亡", "顾临川重伤失踪")
        assert {"顾临川", "死亡", "失踪"} <= set(impact["keywords"])
        assert {"characters", "facts"} <= set(impact["ledger_impacts"]["categories"])


def test_structured_ledger_dependency_expands_revision_candidate_range():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("传播书", root, LOGGER, storage)
        for chapter in range(1, 6):
            storage.atomic_write_text(root / "chapters" / f"{chapter:06d}.txt", f"第{chapter}章普通正文。")
        storage.atomic_write_text(root / "chapters" / "000001.txt", "顾临川在爆炸中死亡。")
        novel.save_state({"current_chapter": 5})
        storage.atomic_write_json(root / "tracking" / "state_cards.json", {
            "character": {"顾临川": {"history": [{"chapter": 5, "fields": {"status": "死亡"}, "evidence": "旧状态传播"}]}}
        })
        impact = HistoryRevisionManager(novel, LOGGER, None, storage).analyze(1, "顾临川死亡", "顾临川重伤失踪")
        chapter5 = next(item for item in impact["chapters"] if item["chapter"] == 5)
        assert chapter5["dependency_types"] == ["动态状态卡"]
