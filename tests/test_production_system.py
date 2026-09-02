import logging
import pytest
import tempfile
from pathlib import Path

from core.ai_action_registry import get_ai_action, list_ai_actions, validate_ai_action_registry
from core.ai_contracts import chapter_quality_metrics
from core.character_manager import CharacterManager
from core.chapter_manager import ChapterManager
from core.context_manager import ContextManager
from core.genre_pack_manager import GenrePackManager
from core.long_form_evaluator import LongFormEvaluator
from core.causal_repair_planner import CausalRepairPlanner
from core.novel_manager import NovelManager
from core.planning_impact_manager import PlanningImpactManager
from core.planning_review_manager import PlanningReviewManager
from core.planning_version_manager import PlanningVersionManager
from core.quality_tracker import QualityTracker
from core.scene_outline_manager import SceneOutlineManager
from core.state_card_manager import StateCardManager
from core.story_sandbox_manager import StorySandboxManager
from core.workflow_engine import list_workflows, workflow_payload, should_pause_for_commit
from core.chapter_generation_service import PIPELINE_STAGES
from storage_utils import StorageManager


LOGGER = logging.getLogger("production-system-test")


def novel_at(path: str) -> NovelManager:
    return NovelManager("Test", Path(path), LOGGER, StorageManager(LOGGER))


def test_ai_action_registry_is_complete():
    assert not validate_ai_action_registry()
    assert len(list_ai_actions()) >= 12
    assert get_ai_action("chapter_write")["writes"] == ["chapters"]


def test_deep_workflow_uses_shared_pipeline_stage_contract():
    workflow = next(item for item in list_workflows() if item["key"] == "deep_chapter")
    assert workflow["steps"][:len(PIPELINE_STAGES)] == list(PIPELINE_STAGES)


def test_chapter_commit_modes_have_distinct_pause_policy():
    assert should_pause_for_commit("review", "PASS") is True
    assert should_pause_for_commit("balanced", "PASS") is False
    assert should_pause_for_commit("balanced", "WARNING") is True
    assert should_pause_for_commit("automatic", "WARNING") is False
    assert should_pause_for_commit("automatic", "FAIL") is True
    assert should_pause_for_commit("review", "FAIL", approved=True) is False


def test_quality_report_tolerates_corrupt_debt_rows():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        storage.atomic_write_json(root / "quality" / "debt.json", {
            "items": [None, "损坏", {"id": "ok", "chapter": "损坏", "severity": "中"}],
        })
        tracker = QualityTracker(root, LOGGER, storage)
        report = tracker.get_report()
        assert report["total_debts"] == 1
        assert report["pending"] == 1
        assert report["by_chapter"] == {"0": 1}


def test_scene_outline_is_normalized_and_rendered():
    with tempfile.TemporaryDirectory() as tmp:
        manager = SceneOutlineManager(Path(tmp), LOGGER)
        item = manager.save(3, {"opening_hook": "接上警报", "scenes": [
            {"title": "下楼", "summary": "两人进入地下层", "goal": "查明警报", "conflict": "门被锁死", "target_words": 900},
            {"title": "发现", "summary": "找到损坏终端", "target_words": 1100},
        ], "status": "confirmed"})
        assert item["total_target_words"] == 2000
        assert item["scenes"][0]["obstacle"] == "门被锁死"
        assert "第3章已确认场景细纲" in manager.render(3)


def test_confirmed_scene_outline_controls_plan_and_invalidates_old_generation(tmp_path):
    storage = StorageManager(LOGGER)
    manager = SceneOutlineManager(tmp_path, LOGGER, storage)
    storage.atomic_write_json(tmp_path / "outline" / "chapter_plans.json", {
        "3": {"fingerprint": "old", "plan": {"beats": ["旧计划"]}},
    })
    storage.atomic_write_json(tmp_path / "turns" / "index.json", {
        "schema_version": 1,
        "items": [{"id": "active", "chapter": 3, "status": "ready"}],
    })
    item = manager.save(3, {
        "opening_hook": "承接警报", "ending_hook": "发现内鬼",
        "scenes": [{
            "title": "地下室", "summary": "调查终端", "goal": "取得记录",
            "obstacle": "终端断电", "turn": "备用电源启动", "outcome": "获得名单",
            "target_words": 1800,
        }], "status": "confirmed",
    })
    plan = manager.confirmed_plan(3)
    assert item["status"] == "confirmed"
    assert plan["scenes"][0]["name"] == "地下室"
    assert plan["scenes"][0]["word_budget"] == 1800
    assert plan["ending_hook"] == "发现内鬼"
    assert "3" not in storage.safe_read_json(tmp_path / "outline" / "chapter_plans.json", {})
    turn = storage.safe_read_json(tmp_path / "turns" / "index.json", {})["items"][0]
    assert turn["planning_stale"] is True
    assert storage.safe_read_json(tmp_path / "planning" / "epoch.json", {})["chapters"] == [3]


def test_seeding_generated_scene_does_not_create_false_user_impact(tmp_path):
    storage = StorageManager(LOGGER)
    manager = SceneOutlineManager(tmp_path, LOGGER, storage)
    manager.seed_from_plan(2, {
        "beats": ["进入现场"],
        "scenes": [{"name": "入口", "goal": "调查", "word_budget": 1000}],
    })
    assert manager.get(2)["status"] == "draft"
    assert not (tmp_path / "planning" / "epoch.json").exists()


def test_scene_outline_reader_tolerates_damage_and_invalid_word_budget():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        manager = SceneOutlineManager(root, LOGGER, storage)
        saved = manager.save(1, {"scenes": [{"title": "入口", "target_words": "损坏"}]})
        assert saved["scenes"][0]["target_words"] == 800
        storage.atomic_write_json(root / "outline" / "scene_outlines.json", {
            "bad": [], "2": "损坏", "3": {"chapter": 3},
        })
        assert manager.get(2) is None
        assert manager.list() == [{"chapter": 3}]
        with pytest.raises(ValueError, match="正整数"):
            manager.save(0, {"scenes": [{"title": "非法章"}]})


def test_state_cards_ingest_summary_and_keep_history():
    with tempfile.TemporaryDirectory() as tmp:
        manager = StateCardManager(Path(tmp), LOGGER)
        counts = manager.ingest_summary(4, {
            "characters_changed": [{"name": "林舟", "field": "location", "new_value": "地下室", "evidence": "林舟下楼"}],
            "items": [{"name": "黑盘", "owner": "林舟", "status": "完好"}],
            "relationship_changes": [{"from": "林舟", "to": "苏遥", "type": "合作", "strength": 40}],
        })
        assert counts["character"] == 1
        assert manager.get()["item"]["黑盘"]["fields"]["owner"] == "林舟"
        assert "动态状态卡" in manager.compact_context()


def test_planning_changes_invalidate_only_future_cache():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        cache = root / "outline" / "chapter_plans.json"
        storage.atomic_write_json(cache, {"2": {"plan": {}}, "5": {"plan": {}}, "6": {"plan": {}}})
        manager = PlanningImpactManager(root, LOGGER, storage)
        impact = manager.record_changes(
            [{"title": "第一卷", "start_chapter": 1, "end_chapter": 5}],
            [{"title": "第一卷", "start_chapter": 1, "end_chapter": 6, "goal": "新目标"}],
            {}, {}, 3,
        )
        assert impact["chapters"] == [4, 5, 6]
        remaining = storage.safe_read_json(cache, {})
        assert "2" in remaining and "5" not in remaining and "6" not in remaining


def test_planning_change_preserves_confirmed_scene_outline_but_invalidates_draft():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        storage.atomic_write_json(root / "outline" / "scene_outlines.json", {
            "4": {"chapter": 4, "status": "confirmed", "scenes": [{"title": "人工确认"}]},
            "5": {"chapter": 5, "status": "draft", "scenes": [{"title": "模型草稿"}]},
        })
        storage.atomic_write_json(root / "turns" / "index.json", {
            "schema_version": 1,
            "items": [{"id": "futureturn4", "chapter": 4, "status": "ready"}],
        })
        storage.atomic_write_text(root / "turns" / "drafts" / "futureturn4.txt", "旧规划生成的草稿")
        impact = PlanningImpactManager(root, LOGGER, storage).record_changes(
            [], [], {"4": {"title": "旧"}, "5": {"title": "旧"}},
            {"4": {"title": "新"}, "5": {"title": "新"}}, 3,
        )
        scenes = storage.safe_read_json(root / "outline" / "scene_outlines.json", {})
        assert "4" in scenes and "5" not in scenes
        assert impact["protected_confirmed_scenes"] == [4]
        turn = storage.safe_read_json(root / "turns" / "index.json", {})["items"][0]
        assert turn["planning_stale"] is True
        assert turn["planning_impact_id"] == impact["id"]
        assert storage.safe_read_json(root / "planning" / "epoch.json", {})["id"] == impact["id"]


def test_planning_impact_tolerates_invalid_volume_ranges():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manager = PlanningImpactManager(root, LOGGER, StorageManager(LOGGER))
        impact = manager.record_changes(
            [{"title": "旧卷", "start_chapter": "损坏", "end_chapter": "损坏"}],
            [{"title": "新卷", "start_chapter": 4, "end_chapter": 5}],
            {}, {}, 3,
        )
        assert impact["chapters"] == [4, 5]


def test_upstream_setting_change_invalidates_future_plan_window():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        storage.atomic_write_json(root / "outline" / "chapter_plans.json", {
            "2": {"plan": {}}, "3": {"plan": {}}, "4": {"plan": {}}, "5": {"plan": {}},
        })
        storage.atomic_write_json(root / "outline" / "scene_outlines.json", {"3": {"scenes": []}, "4": {"scenes": []}})
        storage.atomic_write_json(root / "outline" / "chapter_briefs.json", {"3": {"title": "旧第三章"}, "4": {"title": "旧第四章"}})
        storage.atomic_write_json(root / "outline" / "chapter_titles.json", {"3": "旧第三章", "4": "旧第四章"})
        storage.atomic_write_json(root / "outline" / "opening_chapters.json", {"chapters": [{"chapter": 3}, {"chapter": 4}]})
        impact = PlanningImpactManager(root, LOGGER, storage).record_changes([], [], {}, {}, 2, True)
        assert impact["chapters"] == [3, 4, 5]
        remaining = storage.safe_read_json(root / "outline" / "chapter_plans.json", {})
        assert set(remaining) == {"2"}
        assert storage.safe_read_json(root / "outline" / "scene_outlines.json", {}) == {}
        assert storage.safe_read_json(root / "outline" / "chapter_briefs.json", {}) == {}
        assert storage.safe_read_json(root / "outline" / "chapter_titles.json", {}) == {}
        assert storage.safe_read_json(root / "outline" / "opening_chapters.json", {})["chapters"] == []


def test_planning_change_preserves_explicitly_edited_future_brief_and_duplicate_volume_titles():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        old = [{"title": "未命名", "start_chapter": 1, "end_chapter": 5}, {"title": "未命名", "start_chapter": 6, "end_chapter": 10}]
        new = [{"title": "未命名", "start_chapter": 1, "end_chapter": 6}, {"title": "未命名", "start_chapter": 7, "end_chapter": 12}]
        storage.atomic_write_json(root / "outline" / "chapter_briefs.json", {"8": {"title": "用户新提要"}, "9": {"title": "旧提要"}})
        storage.atomic_write_json(root / "outline" / "chapter_titles.json", {"8": "旧标题", "9": "旧提要"})
        impact = PlanningImpactManager(root, LOGGER, storage).record_changes(
            old, new, {"8": {"title": "旧提要"}, "9": {"title": "旧提要"}},
            {"8": {"title": "用户新提要"}, "9": {"title": "旧提要"}}, 5,
        )
        assert impact["chapters"] == list(range(6, 13))
        assert storage.safe_read_json(root / "outline" / "chapter_briefs.json", {}) == {"8": {"title": "用户新提要"}}
        assert storage.safe_read_json(root / "outline" / "chapter_titles.json", {}) == {"8": "用户新提要"}


def test_planning_version_restores_tracking_and_fact_ledgers_together():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        storage.atomic_write_json(root / "tracking" / "state_cards.json", {"character": {"林舟": {"fields": {"status": "存活"}}}})
        storage.atomic_write_json(root / "facts.json", {"facts": [{"subject": "林舟", "predicate": "身份", "object": "记者"}], "conflicts": []})
        storage.atomic_write_json(root / "state.json", {"genre": "悬疑", "style": "冷峻", "description": "旧简介"})
        manager = PlanningVersionManager(root)
        version = manager.snapshot("一致快照")
        storage.atomic_write_json(root / "tracking" / "state_cards.json", {"character": {"林舟": {"fields": {"status": "死亡"}}}})
        storage.atomic_write_json(root / "facts.json", {"facts": [], "conflicts": []})
        storage.atomic_write_json(root / "state.json", {"genre": "玄幻", "style": "热血", "description": "新简介"})
        manager.restore(version["id"])
        assert storage.safe_read_json(root / "tracking" / "state_cards.json", {})["character"]["林舟"]["fields"]["status"] == "存活"
        assert storage.safe_read_json(root / "facts.json", {})["facts"][0]["object"] == "记者"
        state = storage.safe_read_json(root / "state.json", {})
        assert (state["genre"], state["style"], state["description"]) == ("悬疑", "冷峻", "旧简介")


def test_genre_pack_sandbox_and_workflow_catalog():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        genre = GenrePackManager(root, LOGGER)
        assert genre.apply("suspense")["label"] == "悬疑推理"
        assert "证据链" in genre.context()
        sandbox = StorySandboxManager(root, LOGGER)
        record = sandbox.save_variants(2, "下一步", [
            {"title": "追踪", "direction": "追踪信号"},
            {"title": "设伏", "direction": "利用假信号设伏"},
            {"title": "撤退", "direction": "先保护证人"},
        ])
        adopted = sandbox.adopt(record["id"], record["variants"][1]["id"])
        assert adopted["title"] == "设伏"
        assert len(list_workflows()) == 3
        assert workflow_payload("serial_chapters", {"count": 99, "target_words": 99999})["count"] == 10


def test_active_planning_records_survive_terminal_history_limits():
    pending_impacts = [{"id": "pending-impact", "status": "pending"}]
    resolved_impacts = [{"id": f"resolved-{index}", "status": "resolved"} for index in range(101)]
    impacts = PlanningImpactManager._prune_items(pending_impacts + resolved_impacts)
    assert any(item["id"] == "pending-impact" for item in impacts)
    assert len([item for item in impacts if item["status"] == "resolved"]) == 100

    proposed_repairs = [{"id": "open-repair", "status": "proposed"}]
    applied_repairs = [{"id": f"applied-{index}", "status": "applied"} for index in range(51)]
    repairs = CausalRepairPlanner._prune_items(proposed_repairs + applied_repairs)
    assert any(item["id"] == "open-repair" for item in repairs)
    assert len([item for item in repairs if item["status"] == "applied"]) == 50

    open_sandboxes = [{"id": "open-sandbox", "status": "open"}]
    adopted_sandboxes = [{"id": f"adopted-{index}", "status": "adopted"} for index in range(61)]
    sandboxes = StorySandboxManager._prune_items(open_sandboxes + adopted_sandboxes)
    assert any(item["id"] == "open-sandbox" for item in sandboxes)
    assert len([item for item in sandboxes if item["status"] == "adopted"]) == 60


def test_story_sandbox_recovers_from_wrong_json_shape():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        storage.atomic_write_json(root / "planning" / "sandboxes.json", ["损坏"])
        manager = StorySandboxManager(root, LOGGER, storage)
        assert manager.list() == []
        record = manager.save_variants(1, "下一步", [
            {"title": "调查", "direction": "核对证据"},
            {"title": "跟踪", "direction": "追踪嫌疑人"},
        ])
        assert manager.list()[0]["id"] == record["id"]


def test_character_appearance_range_filters_context_cast():
    with tempfile.TemporaryDirectory() as tmp:
        manager = CharacterManager(Path(tmp), LOGGER)
        manager.create_character("林舟", role_tier="主角", appearance_start=1)
        manager.create_character("证人", role_tier="NPC", appearance_start=5, appearance_end=8)
        assert [item["name"] for item in manager.list_characters(3)] == ["林舟"]
        assert {item["name"] for item in manager.list_characters(6)} == {"林舟", "证人"}
        assert [item["name"] for item in manager.list_characters(9)] == ["林舟"]


def test_character_list_prioritizes_role_then_recent_activity():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manager = CharacterManager(root, LOGGER)
        manager.create_character("阿路人", role_tier="路人")
        manager.create_character("最后的主角", role_tier="主角")
        manager.create_character("重要旧友", role_tier="重要配角")
        manager.create_character("重要新友", role_tier="重要配角")
        manager.update_character("重要新友", last_chapter=8)
        assert [item["name"] for item in manager.list_characters()] == [
            "最后的主角", "重要新友", "重要旧友", "阿路人",
        ]


def test_memory_constraints_enter_context_only_after_confirmation():
    with tempfile.TemporaryDirectory() as tmp:
        nm = novel_at(tmp)
        chapters = ChapterManager(nm, LOGGER)
        chapters.save_chapter(1, "林舟进入地下室。警报仍在持续，他决定寻找总闸。")
        summary = chapters.summary_mgr.get_summary(1)
        summary["plan_reconciliation"] = {
            "completed_goals": [], "unfinished_goals": [], "deviations": [],
            "new_constraints": ["地下室断电前无法离开"], "next_chapter_impacts": ["先寻找总闸"],
            "evidence_quotes": [], "review_status": "pending",
        }
        chapters.summary_mgr.save_custom_summary(1, summary)
        pending = ContextManager(nm, LOGGER).build_context(max_tokens=5000)
        assert "地下室断电前无法离开" not in pending
        chapters.summary_mgr.review_memory(1, "confirmed")
        confirmed = ContextManager(nm, LOGGER).build_context(max_tokens=5000)
        assert "地下室断电前无法离开" in confirmed


def test_pending_volume_repairs_enter_next_chapter_context_until_decided():
    with tempfile.TemporaryDirectory() as tmp:
        nm = novel_at(tmp)
        nm.save_state({"current_chapter": 2})
        storage = StorageManager(LOGGER)
        storage.atomic_write_json(nm.path / "reviews" / "planning_reviews.json", {
            "chapters": [], "section_reviews": [],
            "volume_reviews": [{
                "volume": "第一卷", "start_chapter": 1, "end_chapter": 2, "status": "needs_review",
                "repair_tasks": [{
                    "id": "repairkey", "kind": "foreshadow", "priority": "中",
                    "description": "补齐钥匙来源线索", "status": "pending",
                }],
            }],
        })
        pending = ContextManager(nm, LOGGER).build_context(max_tokens=5000)
        assert "上一卷遗留修复约束" in pending
        assert "补齐钥匙来源线索" in pending
        PlanningReviewManager(nm.path, LOGGER, storage).decide_volume_task(
            "repairkey", "resolved", "作者决定取消该线索", waive=True,
        )
        resolved = ContextManager(nm, LOGGER).build_context(max_tokens=5000)
        assert "补齐钥匙来源线索" not in resolved


def test_long_form_evaluation_and_local_quality_metrics():
    with tempfile.TemporaryDirectory() as tmp:
        nm = novel_at(tmp)
        chapters = ChapterManager(nm, LOGGER)
        chapters.save_chapter(1, "林舟推开门。\n\n苏遥问：“你听见了吗？”\n\n警报突然响起，他决定立刻下楼。")
        StateCardManager(nm.path, LOGGER).upsert("character", "林舟", 1, {"location": "楼梯口"})
        report = LongFormEvaluator(nm.path, LOGGER).run()
        assert report["memory_coverage"] == 1
        assert report["commit_coverage"] == 1
        metrics = chapter_quality_metrics("短句。" * 20 + "“我们走。”" + "警报突然响起，他必须做出选择！")
        assert 0 <= metrics["reader_pull"] <= 100
        assert 0 <= metrics["human_texture"] <= 100


def test_direct_model_waits_for_lmstudio_port_release(monkeypatch):
    from llm_client import LMStudioClient
    client = LMStudioClient()
    states = iter([True, False])
    monkeypatch.setattr(client, "_port_in_use", lambda port=1234: next(states))
    monkeypatch.setattr("llm_client.time.sleep", lambda _seconds: None)
    client._wait_port_released(max_wait=1)
