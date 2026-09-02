import json
import logging
import tempfile
from pathlib import Path

from core.consistency_manager import ConsistencyManager
from core.novel_manager import NovelManager
from core.timeline_manager import TimelineManager
from storage_utils import StorageManager


LOGGER = logging.getLogger("consistency-deep-test")


def test_consistency_detects_real_conflicts_but_ignores_death_references():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        (root / "chapters").mkdir(parents=True)
        (root / "characters").mkdir(parents=True)
        (root / "bible").mkdir(parents=True)
        (root / "chapters" / "000001.txt").write_text("顾临川倒下。林舟留在城门。", "utf-8")
        (root / "chapters" / "000002.txt").write_text("众人想起顾临川的遗像。林舟守在城门。", "utf-8")
        (root / "chapters" / "000003.txt").write_text("顾临川推门而入。祭司复活死人。林舟查看密室。", "utf-8")
        (root / "bible" / "rules.md").write_text("# 规则\n魔法不能复活死人\n", "utf-8")
        (root / "characters" / "顾临川.json").write_text(json.dumps({
            "name": "顾临川", "current_status": "死亡", "last_chapter": 1,
            "ability_history": [], "locations": [],
        }, ensure_ascii=False), "utf-8")
        (root / "characters" / "林舟.json").write_text(json.dumps({
            "name": "林舟", "current_status": "存活", "last_chapter": 3,
            "ability_history": [], "locations": [
                {"chapter": 2, "location": "城门"}, {"chapter": 3, "location": "密室"},
            ],
        }, ensure_ascii=False), "utf-8")
        timeline = TimelineManager(root, LOGGER)
        timeline.add_event(3, "午夜", "城门", "林舟巡查", ["林舟"])
        timeline.add_event(3, "午夜", "密室", "林舟开锁", ["林舟"])
        novel = NovelManager("测试", root, LOGGER, storage)
        novel.save_state({"current_chapter": 3})
        issues = ConsistencyManager(novel, LOGGER).check_all()
        assert not any(item["type"] == "人物已死亡但再次出现" and item["chapter"] == 2 for item in issues)
        assert any(item["type"] == "人物已死亡但再次出现" and item["chapter"] == 3 for item in issues)
        assert any(item["type"] == "可能违反世界规则" for item in issues)
        assert any(item["type"] == "时间地点冲突" for item in issues)
        assert any(item["type"] == "时空跳跃" and item["chapter"] == 3 for item in issues)


def test_chapter_count_uses_only_numeric_chapter_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        chapters = root / "chapters"
        chapters.mkdir(parents=True)
        (chapters / "000001.txt").write_text("第一章", "utf-8")
        (chapters / "notes.txt").write_text("不是章节", "utf-8")
        novel = NovelManager("测试", root, LOGGER, StorageManager(LOGGER))
        from core.chapter_manager import ChapterManager
        assert ChapterManager(novel, LOGGER).get_chapter_count() == 1


def test_dead_character_resurrection_wording_is_not_mistaken_for_a_reference():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "chapters").mkdir(parents=True)
        (root / "characters").mkdir(parents=True)
        (root / "chapters" / "000002.txt").write_text("死去的顾临川突然睁眼，缓缓站了起来。", "utf-8")
        (root / "characters" / "顾临川.json").write_text(json.dumps({
            "name": "顾临川", "current_status": "死亡", "last_chapter": 1,
            "ability_history": [], "locations": [],
        }, ensure_ascii=False), "utf-8")
        novel = NovelManager("测试", root, LOGGER, StorageManager(LOGGER))
        novel.save_state({"current_chapter": 2})
        issues = ConsistencyManager(novel, LOGGER).check_all()
        assert any(item["type"] == "人物已死亡但再次出现" for item in issues)


def test_death_check_uses_accepted_death_chapter_and_status_synonyms():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "chapters").mkdir(parents=True)
        for chapter, text in ((1, "顾临川仍在行动。"), (2, "顾临川在战斗中倒下。"), (3, "顾临川后来又推门出现。")):
            (root / "chapters" / f"{chapter:06d}.txt").write_text(text, "utf-8")
        from core.character_manager import CharacterManager
        from core.change_review_manager import ChangeReviewManager
        characters = CharacterManager(root, LOGGER)
        characters.create_character("顾临川")
        reviews = ChangeReviewManager(root, LOGGER)
        reviews.add_from_summary(2, [{"name": "顾临川", "field": "current_status", "new_value": "阵亡"}])
        reviews.decide(reviews.list()[0]["id"], True)
        characters.update_character("顾临川", relationships="遗属已安置", last_chapter=3)
        novel = NovelManager("测试", root, LOGGER, StorageManager(LOGGER))
        novel.save_state({"current_chapter": 3})
        issues = ConsistencyManager(novel, LOGGER).check_all()
        issue = next(item for item in issues if item["type"] == "人物已死亡但再次出现")
        assert "第2章死亡" in issue["detail"] and issue["chapter"] == 3
