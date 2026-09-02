import logging
import tempfile
from pathlib import Path

import pytest

from core.causal_repair_planner import CausalRepairPlanner
from core.context_manager import ContextManager
from core.novel_manager import NovelManager
from core.planning_impact_manager import PlanningImpactManager
from storage_utils import StorageManager


LOGGER = logging.getLogger("causal-repair-planner-test")


def _project(root: Path, storage: StorageManager):
    storage.atomic_write_json(root / "state.json", {"current_chapter": 2})
    storage.atomic_write_json(root / "outline" / "volumes.json", [{
        "title": "第一卷", "start_chapter": 1, "end_chapter": 2,
        "goal": "主角取得失踪案档案", "sections": [],
    }])
    storage.atomic_write_json(root / "summaries" / "000001.json", {"chapter": 1, "summary": "主角进入车站。"})
    storage.atomic_write_json(root / "summaries" / "000002.json", {"chapter": 2, "summary": "主角被迫撤离。"})
    storage.atomic_write_json(root / "outline" / "chapter_briefs.json", {
        "3": {"chapter": 3, "title": "原第三章", "synopsis": "主角整理失败线索，并决定寻找新的入口继续调查失踪案件。", "must_happen": ["保留原事件"]},
    })
    storage.atomic_write_json(root / "outline" / "chapter_plans.json", {"3": {"fingerprint": "old", "plan": {}}})
    storage.atomic_write_json(root / "outline" / "scene_outlines.json", {
        "3": {"chapter": 3, "status": "confirmed", "scenes": [{"title": "人工确认场景"}]},
    })


def test_causal_repair_proposal_is_isolated_and_preserves_confirmed_scene_on_apply():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        _project(root, storage)
        planner = CausalRepairPlanner(root, LOGGER, storage)
        proposal = planner.propose(3)
        assert proposal["patches"][0]["chapter"] == 3
        assert "人工确认的场景细纲将保留" in "；".join(proposal["patches"][0]["invalidations"])
        assert storage.safe_read_json(root / "outline" / "chapter_briefs.json", {})["3"]["must_happen"] == ["保留原事件"]
        applied = planner.apply(proposal["id"])
        brief = storage.safe_read_json(root / "outline" / "chapter_briefs.json", {})["3"]
        assert brief["title"] == "原第三章"
        assert brief["must_happen"][0] == "保留原事件"
        assert any("失踪案档案" in item for item in brief["must_happen"])
        assert "3" not in storage.safe_read_json(root / "outline" / "chapter_plans.json", {})
        scenes = storage.safe_read_json(root / "outline" / "scene_outlines.json", {})
        assert scenes["3"]["status"] == "confirmed"
        assert applied["planning_impact"]["protected_confirmed_scenes"] == [3]
        novel = NovelManager("因果修复书", root, LOGGER, storage)
        context = ContextManager(novel, LOGGER).build_context(max_tokens=5000)
        assert "当前章前提要（确认稿）" in context
        assert "失踪案档案" in context


def test_causal_repair_rejects_stale_proposal_and_rolls_back_partial_apply(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        _project(root, storage)
        planner = CausalRepairPlanner(root, LOGGER, storage)
        stale = planner.propose()
        briefs = storage.safe_read_json(root / "outline" / "chapter_briefs.json", {})
        briefs["3"]["title"] = "用户刚刚修改"
        storage.atomic_write_json(root / "outline" / "chapter_briefs.json", briefs)
        with pytest.raises(ValueError, match="提要已经变化"):
            planner.apply(stale["id"])

        fresh = planner.propose()
        before = storage.safe_read_json(root / "outline" / "chapter_briefs.json", {})
        monkeypatch.setattr(
            PlanningImpactManager, "record_changes",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("模拟影响记录失败")),
        )
        with pytest.raises(RuntimeError, match="模拟影响记录失败"):
            planner.apply(fresh["id"])
        assert storage.safe_read_json(root / "outline" / "chapter_briefs.json", {}) == before
        assert planner.get(fresh["id"])["status"] == "proposed"
