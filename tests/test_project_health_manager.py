import logging
import tempfile
from pathlib import Path

from core.chapter_manager import ChapterManager
from core.chapter_turn_engine import ChapterTurnEngine
from core.novel_manager import NovelManager
from core.project_health_manager import ProjectHealthManager
from storage_utils import StorageManager


LOGGER = logging.getLogger("project-health-test")


def test_health_scan_and_repair_deterministic_project_damage():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("健康书", root, LOGGER, storage)
        manager = ChapterManager(novel, LOGGER)
        content = "林舟确认车站出口已经被封锁。" * 20
        manager.save_chapter(1, content)
        (root / "summaries" / "000001.json").unlink()
        storage.atomic_write_json(root / "summaries" / "000009.json", {"chapter": 9, "summary": "孤立记忆"})
        storage.atomic_write_json(root / "state.json", {"current_chapter": 7, "total_words": 1})
        storage.atomic_write_json(root / "turns" / "index.json", {
            "schema_version": 1,
            "items": [{"id": "abc123", "chapter": 2, "status": "ready"}],
        })
        storage.atomic_write_text(root / "turns" / "drafts" / "orphan.txt", "孤立草稿")
        health = ProjectHealthManager(novel, LOGGER, storage)
        before = health.scan()
        kinds = {item["kind"] for item in before["issues"]}
        assert {"missing_summary", "orphan_summary", "orphan_turn_draft", "state_chapter", "state_words", "missing_turn_draft"} <= kinds
        repaired = health.repair()
        assert repaired["after"]["status"] == "healthy"
        assert manager.commits.is_committed(1, content)
        assert novel.get_state()["current_chapter"] == 1
        assert novel.get_state()["total_words"] == len(content)
        assert not (root / "summaries" / "000009.json").exists()
        turns = storage.safe_read_json(root / "turns" / "index.json", {})["items"]
        assert turns[0]["status"] == "discarded"
        assert not (root / "turns" / "drafts" / "orphan.txt").exists()


def test_health_reports_unrepairable_chapter_gap_without_fabricating_content():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("断章书", root, LOGGER, storage)
        chapters = root / "chapters"
        chapters.mkdir(parents=True)
        (chapters / "000002.txt").write_text("第二章正文", "utf-8")
        report = ProjectHealthManager(novel, LOGGER, storage).scan()
        gap = next(item for item in report["issues"] if item["kind"] == "chapter_gap")
        assert gap["repairable"] is False
        assert gap["chapters"] == [1]


def test_health_repairs_committed_turn_with_pending_post_processing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("恢复书", root, LOGGER, storage)
        engine = ChapterTurnEngine(novel, LOGGER, ChapterManager(novel, LOGGER), storage)
        turn = engine.save_draft(1, "林舟进入档案馆并确认出口封闭。" * 40, 500)
        engine.commit(turn["id"])
        for path in (root / "timeline").glob("*.json"):
            path.unlink()
        data = engine._load_index()
        data["items"][0]["post_commit_pending"] = True
        engine._save_index(data)
        health = ProjectHealthManager(novel, LOGGER, storage)
        kinds = {item["kind"] for item in health.scan()["issues"]}
        assert {"stranded_turn_commit", "missing_chapter_timeline"} <= kinds
        repaired = health.repair()
        assert repaired["after"]["status"] == "healthy"
        assert engine.get(turn["id"])["post_commit_pending"] is False
        assert list((root / "timeline").glob("*.json"))


def test_health_scan_survives_invalid_timeline_and_repairs_invalid_turn_records():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        novel = NovelManager("损坏索引书", root, LOGGER, storage)
        storage.atomic_write_json(root / "timeline" / "broken.json", {
            "chapter": "不是数字", "event": "损坏事件", "source": "manual",
        })
        storage.atomic_write_json(root / "turns" / "index.json", {
            "schema_version": 1,
            "items": [
                {"id": "../../escape", "chapter": 1, "status": "ready"},
                {"id": "valid123", "chapter": 1, "status": "ready"},
            ],
        })
        storage.atomic_write_text(root / "turns" / "drafts" / "valid123.txt", "有效草稿")
        health = ProjectHealthManager(novel, LOGGER, storage)
        before = health.scan()
        kinds = {item["kind"] for item in before["issues"]}
        assert {"invalid_timeline", "invalid_turn_record"} <= kinds
        repaired = health.repair()
        assert any(item["kind"] == "invalid_timeline" for item in repaired["after"]["issues"])
        turns = storage.safe_read_json(root / "turns" / "index.json", {})["items"]
        assert [item["id"] for item in turns] == ["valid123"]
