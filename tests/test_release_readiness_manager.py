import logging

from core.chapter_manager import ChapterManager
from core.character_manager import CharacterManager
from core.novel_manager import NovelManager
from core.release_readiness_manager import ReleaseReadinessManager
from core.quality_tracker import QualityTracker
from storage_utils import StorageManager


LOGGER = logging.getLogger("release-readiness-test")


def test_readiness_reports_missing_foundation_as_blocking(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("空项目", tmp_path, LOGGER, storage)
    report = ReleaseReadinessManager(novel, LOGGER, storage).run()
    assert report["status"] == "blocked"
    assert {item["key"] for item in report["checks"] if item["status"] == "fail"} >= {
        "world", "rules", "outline", "structure", "characters",
    }


def test_readiness_accepts_complete_committed_project(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("可用项目", tmp_path, LOGGER, storage)
    storage.atomic_write_text(tmp_path / "bible" / "world.md", "这是一个具有明确社会结构、地点和核心矛盾的近未来世界。")
    storage.atomic_write_text(tmp_path / "bible" / "rules.md", "人物行动遵守时间连续性、信息权限和物品状态约束。")
    storage.atomic_write_text(tmp_path / "outline" / "main.md", "主角调查失踪事件，逐步发现幕后组织并在结局作出选择。")
    storage.atomic_write_json(tmp_path / "outline" / "volumes.json", [{
        "title": "第一卷", "start_chapter": 1, "end_chapter": 10,
        "sections": [{"title": "调查开端", "start_chapter": 1, "end_chapter": 10}],
    }])
    storage.atomic_write_json(tmp_path / "outline" / "chapter_briefs.json", {"2": {"chapter": 2}})
    storage.atomic_write_json(tmp_path / "outline" / "scene_outlines.json", {"2": {"chapter": 2, "scenes": [{}]}})
    CharacterManager(tmp_path, LOGGER).create_character("林舟", role_tier="主角")
    CharacterManager(tmp_path, LOGGER).create_character("苏遥", role_tier="重要配角")
    ChapterManager(novel, LOGGER).save_chapter(1, "林舟和苏遥进入封锁现场，确认新的调查方向。" * 20)
    novel.save_state({"target_chapters": 10})
    report = ReleaseReadinessManager(novel, LOGGER, storage).run()
    assert report["status"] == "ready"
    assert report["score"] == 100


def test_readiness_blocks_discontinuous_volume_or_missing_section_coverage(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("坏结构", tmp_path, LOGGER, storage)
    storage.atomic_write_json(tmp_path / "outline" / "volumes.json", [{
        "title": "错误卷", "start_chapter": 5, "end_chapter": 10,
        "sections": [{"start_chapter": 5, "end_chapter": 8}],
    }])
    novel.save_state({"target_chapters": 10})
    check = ReleaseReadinessManager(novel, LOGGER, storage)._structure_check(10)
    assert check["status"] == "fail"
    assert "不连续" in check["detail"]


def test_readiness_blocks_existing_governance_debt(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("治理阻断项目", tmp_path, LOGGER, storage)
    storage.atomic_write_text(tmp_path / "bible" / "world.md", "足够完整的世界观与社会结构设定。")
    storage.atomic_write_text(tmp_path / "bible" / "rules.md", "明确且不可绕过的世界规则。")
    storage.atomic_write_text(tmp_path / "outline" / "main.md", "主角逐步调查事件并承担选择结果。")
    storage.atomic_write_json(tmp_path / "outline" / "volumes.json", [{"start_chapter": 1, "end_chapter": 10}])
    CharacterManager(tmp_path, LOGGER).create_character("林舟")
    CharacterManager(tmp_path, LOGGER).create_character("苏遥")
    QualityTracker(tmp_path, LOGGER, storage).add_debt(1, "logic", "高", "人物同时出现在两个地点")
    report = ReleaseReadinessManager(novel, LOGGER, storage).run()
    governance = next(item for item in report["checks"] if item["key"] == "governance")
    assert governance["status"] == "fail"
    assert report["status"] == "blocked"
