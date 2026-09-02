import sys
sys.path.insert(0, ".")
import logging
import tempfile
from pathlib import Path

import pytest

logging.disable(logging.CRITICAL)


def test_storage_manager():
    from storage_utils import StorageManager
    logger = logging.getLogger("test")
    logger.addHandler(logging.NullHandler())
    sm = StorageManager(logger, backup_count=3)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.json"
        sm.atomic_write_json(path, {"key": "value", "num": 42})
        recovered = sm.safe_read_json(path)
        assert recovered["key"] == "value"
        sm.atomic_write_json(path, {"i": 0})
        sm.atomic_write_json(path, {"i": 1})
        sm.atomic_write_json(path, {"i": 2})
        backups = list((path.parent / ".backups").glob("test.json.*"))
        assert len(backups) <= 3, f"Expected <=3 backups, got {len(backups)}"
        # recover from corrupt
        path.write_text("corrupt", encoding="utf-8")
        recovered = sm.safe_read_json(path, {"default": True})
        assert recovered is not None
    print("  [PASS] StorageManager")


def test_rapid_atomic_writes_keep_distinct_recovery_points(tmp_path):
    from storage_utils import StorageManager

    manager = StorageManager(logging.getLogger("rapid-backup-test"), backup_count=3)
    path = tmp_path / "state.json"
    for value in range(5):
        manager.atomic_write_json(path, {"value": value})
    backups = sorted((tmp_path / ".backups").glob("state.json.*.bak"))
    assert len(backups) == 3
    assert len({backup.name for backup in backups}) == 3
    assert [manager.safe_read_json(backup)["value"] for backup in backups] == [1, 2, 3]


def test_json_recovery_rechecks_main_file_under_lock(tmp_path):
    from storage_utils import StorageManager

    manager = StorageManager(logging.getLogger("locked-recovery-test"))
    path = tmp_path / "state.json"
    manager.atomic_write_json(path, {"value": "旧"})
    manager.atomic_write_json(path, {"value": "新"})
    path.write_text("{broken", encoding="utf-8")
    recovered = manager.safe_read_json(path)
    assert recovered == {"value": "旧"}
    assert manager.safe_read_json(path) == {"value": "旧"}
    assert not (tmp_path / ".state.json.recovery.tmp").exists()


def test_character_manager():
    from core.character_manager import CharacterManager
    logger = logging.getLogger("test")
    logger.addHandler(logging.NullHandler())
    with tempfile.TemporaryDirectory() as tmp:
        cm = CharacterManager(Path(tmp), logger)
        cm.create_character("Alice", "brave", "hero", "sword", "筑基", "friend: Bob", "存活")
        data = cm.get_character("Alice")
        assert data and data["ability_level"] == "筑基"
        chars = cm.list_characters()
        assert len(chars) == 1
        cm.update_character("Alice", ability_level="金丹", last_chapter=10)
        data = cm.get_character("Alice")
        assert data["ability_level"] == "金丹"
        assert len(data["ability_history"]) == 2
        # duplicate
        try:
            cm.create_character("Alice", "", "", "", "", "", "")
            assert False
        except ValueError:
            pass
        # network
        cm.create_character("Bob", "loyal", "", "", "练气", "Alice", "存活")
        network = cm.get_character_network()
        assert len(network["nodes"]) == 2
    print("  [PASS] CharacterManager")


def test_concurrent_character_creation_is_serialized(tmp_path, monkeypatch):
    import threading
    import time

    from core.character_manager import CharacterManager

    manager = CharacterManager(tmp_path, logging.getLogger("character-create-lock-test"))
    original = manager._create_character
    state = {"active": 0, "max_active": 0}
    state_lock = threading.Lock()

    def slow_create(*args, **kwargs):
        with state_lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        try:
            time.sleep(0.03)
            return original(*args, **kwargs)
        finally:
            with state_lock:
                state["active"] -= 1

    monkeypatch.setattr(manager, "_create_character", slow_create)
    successes = []
    failures = []

    def create():
        try:
            successes.append(manager.create_character("同名人物"))
        except ValueError as exc:
            failures.append(str(exc))

    workers = [threading.Thread(target=create) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=2)
    assert state["max_active"] == 1
    assert len(successes) == 1
    assert len(failures) == 1


def test_chapter_manager():
    from core.chapter_manager import ChapterManager
    from core.novel_manager import NovelManager
    from storage_utils import StorageManager
    logger = logging.getLogger("test")
    logger.addHandler(logging.NullHandler())
    with tempfile.TemporaryDirectory() as tmp:
        nm = NovelManager("Test", Path(tmp), logger, StorageManager(logger))
        cm = ChapterManager(nm, logger)
        result = cm.save_chapter(1, "第一章内容。" * 50)
        assert result["chapter"] == 1
        content = cm.read_chapter(1)
        assert content and "第一章内容" in content
        cm.append_chapter(1, "追加。" * 20)
        content2 = cm.read_chapter(1)
        assert "追加" in content2
        cm.save_chapter(2, "第二章。" * 30)
        assert cm.get_chapter_count() == 2
    print("  [PASS] ChapterManager")


def test_append_chapter_never_overwrites_when_existing_read_fails(tmp_path, monkeypatch):
    from core.chapter_manager import ChapterManager
    from core.novel_manager import NovelManager
    from storage_utils import StorageManager

    logger = logging.getLogger("append-read-failure-test")
    novel = NovelManager("Test", tmp_path, logger, StorageManager(logger))
    manager = ChapterManager(novel, logger)
    manager.save_chapter(1, "不可丢失的原正文")
    chapter_path = tmp_path / "chapters" / "000001.txt"
    original_bytes = chapter_path.read_bytes()
    original_read_text = Path.read_text

    def fail_target_read(path, *args, **kwargs):
        if path == chapter_path:
            raise OSError("模拟章节读取失败")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_target_read)
    with pytest.raises(OSError, match="模拟章节读取失败"):
        manager.append_chapter(1, "不应写入的追加内容")
    assert chapter_path.read_bytes() == original_bytes


def test_chapter_word_count_ignores_all_whitespace():
    from core.chapter_manager import count_chapter_words
    assert count_chapter_words("甲 乙\n丙\t丁") == 4


def test_timeline_manager():
    from core.timeline_manager import TimelineManager
    logger = logging.getLogger("test")
    logger.addHandler(logging.NullHandler())
    with tempfile.TemporaryDirectory() as tmp:
        tl = TimelineManager(Path(tmp), logger)
        tl.add_event(1, "Day 1", "Forest", "Hero arrives", ["Hero"])
        tl.add_event(1, "Night", "Cave", "Hero rests", ["Hero"])
        tl.add_event(2, "Day 2", "Castle", "Hero meets King", ["Hero", "King"])
        assert len(tl.query_timeline(character="Hero")) == 3
        assert len(tl.query_timeline(chapter=1)) == 2
        assert len(tl.query_timeline(keyword="Castle")) == 1
        assert len(tl.get_recent_events(2)) == 2
    print("  [PASS] TimelineManager")


def test_novel_manager():
    from core.novel_manager import NovelManager
    from storage_utils import StorageManager
    logger = logging.getLogger("test")
    logger.addHandler(logging.NullHandler())
    with tempfile.TemporaryDirectory() as tmp:
        nm = NovelManager("Test", Path(tmp), logger, StorageManager(logger))
        assert nm.get_state()["current_chapter"] == 0
        nm.increment_chapter()
        assert nm.get_current_chapter() == 1
        nm.add_words(5000)
        assert nm.get_state()["total_words"] == 5000
        nm.update_next_goal("突破")
        assert nm.get_state()["next_goal"] == "突破"
        r = nm.get_status_report()
        assert r["name"] == "Test" and r["current_chapter"] == 1
    print("  [PASS] NovelManager")


def test_consistency_manager():
    from core.consistency_manager import ConsistencyManager
    from core.novel_manager import NovelManager
    from core.chapter_manager import ChapterManager
    from core.character_manager import CharacterManager
    from storage_utils import StorageManager
    logger = logging.getLogger("test")
    logger.addHandler(logging.NullHandler())
    with tempfile.TemporaryDirectory() as tmp:
        nm = NovelManager("Test", Path(tmp), logger, StorageManager(logger))
        cm = ChapterManager(nm, logger)
        char_mgr = CharacterManager(Path(tmp), logger)
        char_mgr.create_character("Alice", "", "", "", "筑基", "", "存活")
        char_mgr.create_character("Bob", "", "", "", "练气", "", "存活")
        cm.save_chapter(1, "Alice starts.")
        cm.save_chapter(2, "Bob appears.")
        con = ConsistencyManager(nm, logger)
        issues = con.check_all()
        assert isinstance(issues, list)
        severe = [i for i in issues if i["severity"] == "高"]
        assert len(severe) == 0
    print("  [PASS] ConsistencyManager")


def test_context_manager():
    from core.context_manager import ContextManager
    from core.novel_manager import NovelManager
    from core.chapter_manager import ChapterManager
    from core.character_manager import CharacterManager
    from storage_utils import StorageManager
    logger = logging.getLogger("test")
    logger.addHandler(logging.NullHandler())
    with tempfile.TemporaryDirectory() as tmp:
        nm = NovelManager("Test", Path(tmp), logger, StorageManager(logger))
        cm = ChapterManager(nm, logger)
        cm.save_chapter(1, "内容。" * 30)
        cm.save_chapter(2, "更多内容。" * 20)
        CharacterManager(nm.path, logger).create_character(
            "林舟", background="存活的调查员", role_tier="主角",
        )
        CharacterManager(nm.path, logger).create_character(
            "周岚", background="第五章才进入主线", appearance_start=5,
        )
        nm.update_next_goal("继续冒险")
        ctx = ContextManager(nm, logger).build_context(max_tokens=2000)
        assert len(ctx) > 0
        assert "【最近章节正文（按时间顺序）】" in ctx
        assert "【上一章连续性交接" in ctx
        assert "更多内容" in ctx
        assert "【上下文权威顺序" in ctx
        assert "【权威人物名册（最高优先级硬约束）】" in ctx
        assert '"role": "主角"' in ctx
        assert '"name": "周岚"' in ctx and '"availability_for_chapter": "future_reserved"' in ctx
        cont = ContextManager(nm, logger).get_continue_context()
        assert "context" in cont
        assert cont["novel_name"] == "Test"
    print("  [PASS] ContextManager")


def test_context_tolerates_invalid_state_and_outline_chapter_numbers():
    from core.context_manager import ContextManager
    from core.novel_manager import NovelManager
    from storage_utils import StorageManager
    logger = logging.getLogger("test-invalid-context")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(logger)
        nm = NovelManager("InvalidContext", root, logger, storage)
        storage.atomic_write_json(root / "state.json", {"current_chapter": "损坏", "total_words": 0})
        storage.atomic_write_json(root / "outline" / "opening_chapters.json", {
            "chapters": [{"chapter": "损坏", "title": "无效细纲"}],
        })
        storage.atomic_write_json(root / "outline" / "volumes.json", [{
            "title": "损坏卷", "start_chapter": "开始", "end_chapter": "结束",
            "sections": [{"start_chapter": "甲", "end_chapter": "乙"}],
        }])
        context = ContextManager(nm, logger).build_context(max_tokens=2000)
        assert "无效细纲" not in context
        assert "损坏卷" not in context


def test_context_tolerates_damaged_optional_context_ledgers(tmp_path):
    from core.context_manager import ContextManager
    from core.novel_manager import NovelManager
    from storage_utils import StorageManager

    logger = logging.getLogger("damaged-optional-context-test")
    storage = StorageManager(logger)
    novel = NovelManager("损坏可选账本", tmp_path, logger, storage)
    for relative in (
        "bible/author_preferences.json",
        "bible/genre_pack.json",
        "planning/creative_assets.json",
    ):
        storage.atomic_write_json(tmp_path / relative, ["损坏"])
    context = ContextManager(novel, logger).build_context(max_tokens=2000)
    assert "题材方法包：通用长篇" in context


def test_stale_chapter_handoff_is_rebuilt_from_current_text():
    from core.context_manager import ContextManager
    from core.novel_manager import NovelManager
    from core.chapter_manager import ChapterManager
    from storage_utils import StorageManager
    logger = logging.getLogger("test-stale-handoff")
    logger.addHandler(logging.NullHandler())
    with tempfile.TemporaryDirectory() as tmp:
        nm = NovelManager("StaleHandoff", Path(tmp), logger, StorageManager(logger))
        cm = ChapterManager(nm, logger)
        cm.save_chapter(1, "林舟站在仓库门前。警报从地下响起。")
        current = ContextManager(nm, logger).build_context(max_tokens=5000)
        assert "【上一章连续性交接" in current
        next((nm.path / "chapters").glob("*.txt")).write_text("这一章已经被作者手动彻底改写。", "utf-8")
        stale = ContextManager(nm, logger).build_context(max_tokens=5000)
        assert "【上一章连续性交接" in stale
        assert "这一章已经被作者手动彻底改写" in stale
        assert "警报从地下响起" not in stale


def test_long_context_keeps_recent_prose_and_full_style():
    from config import MODEL_CONFIG, estimate_tokens
    from core.context_manager import ContextManager
    from core.novel_manager import NovelManager
    from core.chapter_manager import ChapterManager
    from storage_utils import StorageManager
    logger = logging.getLogger("test-long-context")
    logger.addHandler(logging.NullHandler())
    with tempfile.TemporaryDirectory() as tmp:
        nm = NovelManager("LongContext", Path(tmp), logger, StorageManager(logger))
        cm = ChapterManager(nm, logger)
        for chapter in range(1, 8):
            cm.save_chapter(chapter, (f"第{chapter}章场景细节。" * 180) + f"结尾标记{chapter}")
        (nm.path / "bible").mkdir(parents=True, exist_ok=True)
        (nm.path / "outline").mkdir(parents=True, exist_ok=True)
        style_lines = [f"风格规则{i}：保持具体动作与人物感官。" for i in range(30)]
        (nm.path / "bible" / "style.md").write_text("\n".join(style_lines), "utf-8")
        (nm.path / "outline" / "main.md").write_text("全书因果主线与终局约束。" * 80, "utf-8")

        manager = ContextManager(nm, logger)
        manager.fact_mgr.add_from_summary(7, [{"subject": "林舟", "predicate": "身份", "object": "调查员"}])
        context = manager.build_context(max_tokens=12000)

        assert "结尾标记7" in context
        assert "结尾标记6" in context
        assert "结尾标记1" not in context
        assert "风格规则29" in context
        assert "【全书总纲（计划目标，不等于已发生事实）】" in context
        assert "【已确认事实账本（硬约束）】" in context
        assert context.index("【已确认事实账本（硬约束）】") < context.index("【最近章节正文（按时间顺序）】")
        assert context.index("【抽象文风执行规范】") < context.index("【最近章节正文（按时间顺序）】")
        assert estimate_tokens(context) <= 12200

        manager.build_context(profile="brief")
        assert manager.last_build_stats["profile"] == "brief"
        assert manager.last_build_stats["budget"] == min(24000, MODEL_CONFIG["available_context"])
        manager.build_context(profile="planning")
        assert manager.last_build_stats["budget"] == min(48000, MODEL_CONFIG["available_context"])
        manager.build_context(profile="prose")
        assert manager.last_build_stats["budget"] == min(96000, MODEL_CONFIG["available_context"])
        assert manager.last_build_stats["segments"]
        assert manager.last_build_stats["segments"][0]["tokens"] > 0


def test_context_never_exceeds_tiny_budget_and_keeps_single_line_rules():
    from config import estimate_tokens, trim_to_token_limit
    from core.context_manager import ContextManager
    from core.novel_manager import NovelManager
    from core.chapter_manager import ChapterManager
    from storage_utils import StorageManager
    logger = logging.getLogger("test-tiny-context")
    with tempfile.TemporaryDirectory() as tmp:
        nm = NovelManager("TinyContext", Path(tmp), logger, StorageManager(logger))
        ChapterManager(nm, logger).save_chapter(1, "结尾现场。" * 300)
        (nm.path / "bible").mkdir(parents=True, exist_ok=True)
        (nm.path / "bible" / "rules.md").write_text("能力每次使用都会永久失去一段记忆", "utf-8")
        manager = ContextManager(nm, logger)
        context = manager.build_context(max_tokens=120)
        assert estimate_tokens(context) <= 120
        assert "永久失去一段记忆" in context
        assert manager.last_build_stats["tokens"] <= 120
        assert manager.build_context(max_tokens=0) == ""
        trimmed = trim_to_token_limit("很长的内容" * 500, 20)
        assert estimate_tokens(trimmed) <= 20


if __name__ == "__main__":
    print("Running unit tests...\n")
    test_storage_manager()
    test_character_manager()
    test_chapter_manager()
    test_timeline_manager()
    test_novel_manager()
    test_consistency_manager()
    test_context_manager()
    print("\nALL TESTS PASSED")
