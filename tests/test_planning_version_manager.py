import tempfile
import json
import pytest
from pathlib import Path

from core.planning_version_manager import PlanningVersionManager


def test_planning_versions_snapshot_diff_and_restore():
    with tempfile.TemporaryDirectory() as tmp:
        novel = Path(tmp)
        (novel / "bible").mkdir()
        (novel / "outline").mkdir()
        (novel / "bible" / "world.md").write_text("旧世界", "utf-8")
        (novel / "outline" / "main.md").write_text("旧总纲", "utf-8")
        (novel / "state.json").write_text('{"current_chapter":8,"total_words":40000,"next_goal":"旧目标","target_chapters":100}', "utf-8")
        manager = PlanningVersionManager(novel)
        version = manager.snapshot("修改前")
        (novel / "bible" / "world.md").write_text("新世界", "utf-8")
        (novel / "facts.json").write_text('{"facts":[{"subject":"不应保留"}]}', "utf-8")
        (novel / "state.json").write_text('{"current_chapter":8,"total_words":40000,"next_goal":"新目标","target_chapters":200}', "utf-8")
        assert "新世界" in manager.diff(version["id"])
        manager.restore(version["id"])
        assert (novel / "bible" / "world.md").read_text("utf-8") == "旧世界"
        assert not (novel / "facts.json").exists()
        state = json.loads((novel / "state.json").read_text("utf-8"))
        assert state["current_chapter"] == 8 and state["total_words"] == 40000
        assert state["next_goal"] == "旧目标" and state["target_chapters"] == 100
        assert manager.list()


def test_planning_restore_failure_rolls_back_to_pre_restore_state(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        novel = Path(tmp)
        (novel / "bible").mkdir()
        (novel / "bible" / "world.md").write_text("快照世界", "utf-8")
        manager = PlanningVersionManager(novel)
        version = manager.snapshot("目标版本")
        (novel / "bible" / "world.md").write_text("恢复前当前世界", "utf-8")
        original_apply = manager._apply_snapshot
        calls = 0

        def fail_first_apply(source):
            nonlocal calls
            calls += 1
            if calls == 1:
                (novel / "bible" / "world.md").write_text("半恢复状态", "utf-8")
                raise RuntimeError("模拟复制失败")
            return original_apply(source)

        monkeypatch.setattr(manager, "_apply_snapshot", fail_first_apply)
        with pytest.raises(RuntimeError, match="模拟复制失败"):
            manager.restore(version["id"])
        assert (novel / "bible" / "world.md").read_text("utf-8") == "恢复前当前世界"
