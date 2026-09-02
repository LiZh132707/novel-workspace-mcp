import logging

import pytest

from core.ai_contracts import chapter_source_hash, validate_summary
from core.canonical_lock_manager import CanonicalLockManager
from core.chapter_manager import ChapterManager
from core.chapter_turn_engine import ChapterTurnEngine
from core.character_decision_validator import CharacterDecisionValidator
from core.character_manager import CharacterManager
from core.change_review_manager import ChangeReviewManager
from core.context_manager import ContextManager
from core.derived_state_rebuilder import DerivedStateRebuilder
from core.novel_manager import NovelManager
from core.quality_tracker import QualityTracker
from core.review_queue_manager import ReviewQueueManager
from core.story_clock_manager import StoryClockManager
from core.history_impact_analyzer import HistoryImpactAnalyzer
from core.summary_manager import SummaryManager
from storage_utils import StorageManager


LOGGER = logging.getLogger("story-governance-test")


def test_canonical_lock_detects_exact_field_change_and_can_be_removed(tmp_path):
    manager = CanonicalLockManager(tmp_path, LOGGER)
    lock = manager.upsert("character", "林舟", "current_status", "存活", "主角不能被静默写死")
    conflicts = manager.conflicts({"characters_changed": [{
        "name": "林舟", "field": "current_status", "new_value": "死亡", "evidence": "林舟停止了呼吸。",
    }]})
    assert conflicts[0]["lock_id"] == lock["id"]
    assert "存活" in conflicts[0]["message"] and "死亡" in conflicts[0]["message"]
    assert manager.remove(lock["id"]) is True
    assert manager.list() == []


def test_canonical_lock_survives_tiny_context_budget(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("短上下文锁", tmp_path, LOGGER, storage)
    storage.atomic_write_text(tmp_path / "bible" / "style.md", "冗长文风要求。" * 500)
    storage.atomic_write_text(tmp_path / "bible" / "world.md", "冗长世界资料。" * 500)
    CanonicalLockManager(tmp_path, LOGGER, storage).upsert(
        "character", "林舟", "current_status", "存活", "不得静默写死",
    )
    context = ContextManager(novel, LOGGER).build_context(max_tokens=120)
    assert "用户权威设定锁" in context
    assert "林舟/current_status = 存活" in context


def test_story_clock_detects_time_reversal_and_insufficient_travel(tmp_path):
    manager = StoryClockManager(tmp_path, LOGGER)
    manager.set_travel_rule("旧城", "港口", 120)
    manager.record(1, {"handoff": {"final_scene": {
        "location": "旧城", "story_time": "第1天 10:00", "active_characters": ["林舟"],
    }}})
    travel_issues = manager.preview(2, {"handoff": {"final_scene": {
        "location": "港口", "story_time": "第1天 10:30", "active_characters": ["林舟"],
    }}})
    assert travel_issues[0]["blocking"] is True
    assert "至少需120分钟" in travel_issues[0]["message"]
    reversal = manager.preview(2, {"handoff": {"final_scene": {
        "location": "旧城", "story_time": "第1天 09:00", "active_characters": ["林舟"],
    }}})
    assert "倒退" in reversal[0]["message"]
    mixed_format = manager.preview(2, {"handoff": {"final_scene": {
        "location": "港口", "story_time": "2026-07-14 10:30", "active_characters": ["林舟"],
    }}})
    assert len(mixed_format) == 1
    assert mixed_format[0]["blocking"] is False
    assert "无法可靠计算" in mixed_format[0]["message"]
    assert manager.remove_travel_rule("旧城", "港口") is True
    assert manager.get()["travel_rules"] == []


def test_story_clock_uses_shortest_multi_hop_route(tmp_path):
    manager = StoryClockManager(tmp_path, LOGGER)
    manager.set_travel_rule("旧城", "车站", 30)
    manager.set_travel_rule("车站", "港口", 40)
    manager.set_travel_rule("旧城", "港口", 100)
    assert manager._travel_minutes(manager.get()["travel_rules"], "旧城", "港口") == 70
    manager.record(1, {"handoff": {"final_scene": {
        "location": "旧城", "story_time": "第1天 10:00", "active_characters": ["林舟"],
    }}})
    issues = manager.preview(2, {"handoff": {"final_scene": {
        "location": "港口", "story_time": "第1天 10:50", "active_characters": ["林舟"],
    }}})
    assert "至少需70分钟" in issues[0]["message"]


def test_character_decision_requires_evidence_and_transition_reason(tmp_path):
    CharacterManager(tmp_path, LOGGER).create_character("林舟", personality_profile={
        "desire": "保护妹妹", "principle": "不牺牲无辜者", "decision_style": "先确认事实再行动",
    })
    validator = CharacterDecisionValidator(tmp_path, LOGGER)
    missing_evidence = validator.inspect([{"name": "林舟", "action": "交出妹妹", "evidence_verified": False}])
    assert missing_evidence[0]["blocking"] is True
    unexplained = validator.inspect([{
        "name": "林舟", "action": "主动牺牲无辜者", "motive": "尽快脱身",
        "conflicts_with": "principle", "exception_reason": "", "evidence_verified": True,
    }])
    assert any(item["blocking"] and "没有转变诱因" in item["message"] for item in unexplained)


def test_turn_blocks_locked_change_until_separately_authorized(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("治理回合书", tmp_path, LOGGER, storage)
    chapter_manager = ChapterManager(novel, LOGGER)
    engine = ChapterTurnEngine(novel, LOGGER, chapter_manager, storage)
    CanonicalLockManager(tmp_path, LOGGER, storage).upsert("character", "林舟", "current_status", "存活")
    content = "林舟在废墟中停止了呼吸，苏遥确认他已经死亡。" * 35
    summary = validate_summary({
        "summary": "林舟在废墟中死亡。",
        "characters_changed": [{
            "name": "林舟", "field": "current_status", "new_value": "死亡",
            "change": "林舟死亡", "evidence": "林舟在废墟中停止了呼吸",
        }],
    }, 1, content)
    summary["source_hash"] = chapter_source_hash(content)
    chapter_manager.summary_mgr._basic_summary = lambda _chapter, _content: summary
    turn = engine.save_draft(1, content, 500)
    preview = engine.preview_changes(turn["id"])
    assert preview["canonical_lock_conflicts"]
    with pytest.raises(ValueError, match="权威设定锁冲突"):
        engine.commit(turn["id"], allow_quality_failure=True)
    committed = engine.commit(turn["id"], allow_quality_failure=True, allow_locked_changes=True)
    assert committed["turn"]["status"] == "committed"
    assert committed["turn"]["commit_approvals"]["locked_changes"] is True
    assert "locked_changes" in committed["turn"]["governance_overrides"]


def test_turn_blocks_unconfirmed_death_against_character_roster(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("生死事实回合书", tmp_path, LOGGER, storage)
    chapter_manager = ChapterManager(novel, LOGGER)
    engine = ChapterTurnEngine(novel, LOGGER, chapter_manager, storage)
    CharacterManager(tmp_path, LOGGER).create_character("沈川", status="存活")
    content = "沈川已经死亡，苏遥在现场确认了他的身份。" * 35
    summary = validate_summary({
        "summary": "沈川在现场死亡。",
        "facts": [{
            "subject": "沈川", "predicate": "状态", "object": "已死亡",
            "evidence": "沈川已经死亡",
        }],
    }, 1, content)
    summary["source_hash"] = chapter_source_hash(content)
    chapter_manager.summary_mgr._basic_summary = lambda _chapter, _content: summary
    turn = engine.save_draft(1, content, 500)
    preview = engine.preview_changes(turn["id"])
    assert preview["fact_conflicts"]
    with pytest.raises(ValueError, match="硬事实"):
        engine.commit(turn["id"], allow_quality_failure=True)
    committed = engine.commit(
        turn["id"], allow_quality_failure=True, allow_fact_conflicts=True,
    )
    assert "fact_conflicts" in committed["turn"]["governance_overrides"]


def test_commit_rechecks_locks_added_after_preview_without_second_summary_call(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("锁竞态书", tmp_path, LOGGER, storage)
    chapter_manager = ChapterManager(novel, LOGGER)
    engine = ChapterTurnEngine(novel, LOGGER, chapter_manager, storage)
    content = "林舟在废墟中停止了呼吸，苏遥确认他已经死亡。" * 35
    summary = validate_summary({
        "summary": "林舟死亡。", "characters_changed": [{
            "name": "林舟", "field": "current_status", "new_value": "死亡",
            "evidence": "林舟在废墟中停止了呼吸",
        }],
    }, 1, content)
    calls = []
    chapter_manager.summary_mgr._basic_summary = lambda _chapter, _content: calls.append(1) or summary
    turn = engine.save_draft(1, content, 500)
    assert engine.preview_changes(turn["id"])["canonical_lock_conflicts"] == []
    CanonicalLockManager(tmp_path, LOGGER, storage).upsert("character", "林舟", "current_status", "存活")
    with pytest.raises(ValueError, match="权威设定锁冲突"):
        engine.commit(turn["id"], allow_quality_failure=True)
    assert len(calls) == 1


def test_multiple_governance_conflicts_require_independent_approvals(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("多重治理书", tmp_path, LOGGER, storage)
    novel.save_state({"current_chapter": 1})
    CharacterManager(tmp_path, LOGGER).create_character("林舟", personality_profile={
        "principle": "不牺牲无辜者", "desire": "保护同伴", "decision_style": "先确认事实", "flaw": "过度谨慎",
    })
    CanonicalLockManager(tmp_path, LOGGER, storage).upsert("character", "林舟", "current_status", "存活")
    clock = StoryClockManager(tmp_path, LOGGER, storage)
    clock.set_travel_rule("旧城", "港口", 120)
    clock.record(1, {"handoff": {"final_scene": {
        "location": "旧城", "story_time": "第1天 10:00", "active_characters": ["林舟"],
    }}})
    chapter_manager = ChapterManager(novel, LOGGER)
    engine = ChapterTurnEngine(novel, LOGGER, chapter_manager, storage)
    evidence = "林舟决定牺牲无辜者，并在港口停止了呼吸"
    content = (evidence + "。苏遥确认了这个结果。") * 35
    summary = validate_summary({
        "summary": "林舟抵达港口后作出反常决定并死亡。",
        "characters_changed": [{"name": "林舟", "field": "current_status", "new_value": "死亡", "evidence": evidence}],
        "character_decisions": [{
            "name": "林舟", "action": "牺牲无辜者", "motive": "尽快脱身",
            "personality_basis": "", "conflicts_with": "principle", "exception_reason": "", "evidence": evidence,
        }],
        "handoff": {"final_scene": {
            "location": "港口", "story_time": "第1天 10:30", "active_characters": ["林舟"],
        }},
    }, 2, content)
    chapter_manager.summary_mgr._basic_summary = lambda _chapter, _content: summary
    turn = engine.save_draft(2, content, 500)
    engine.preview_changes(turn["id"])
    with pytest.raises(ValueError, match="故事时空"):
        engine.commit(turn["id"], allow_quality_failure=True, allow_locked_changes=True)
    with pytest.raises(ValueError, match="人物决策"):
        engine.commit(
            turn["id"], allow_quality_failure=True, allow_locked_changes=True,
            allow_story_clock_conflicts=True,
        )
    committed = engine.commit(
        turn["id"], allow_quality_failure=True, allow_locked_changes=True,
        allow_story_clock_conflicts=True, allow_character_decision_conflicts=True,
    )
    approvals = committed["turn"]["commit_approvals"]
    assert approvals["locked_changes"] is True
    assert approvals["story_clock_conflicts"] is True
    assert approvals["character_decision_conflicts"] is True


def test_degraded_structured_summary_blocks_commit_until_explicitly_accepted(tmp_path):
    class BrokenSummaryLLM:
        @staticmethod
        def chat(*_args, **_kwargs):
            return "这不是JSON"

    storage = StorageManager(LOGGER)
    novel = NovelManager("摘要降级书", tmp_path, LOGGER, storage)
    chapter_manager = ChapterManager(novel, LOGGER, BrokenSummaryLLM())
    engine = ChapterTurnEngine(novel, LOGGER, chapter_manager, storage)
    content = "林舟沿着封闭站台检查线索，苏遥记录每一次决定。" * 35
    turn = engine.save_draft(1, content, 500)
    preview = engine.preview_changes(turn["id"])
    assert preview["analysis_degraded"] is True
    assert engine.inspect(turn["id"])["requires_summary_confirmation"] is True
    with pytest.raises(ValueError, match="结构化摘要生成失败"):
        engine.commit(turn["id"], allow_quality_failure=True)
    committed = engine.commit(turn["id"], allow_quality_failure=True, allow_degraded_summary=True)
    assert committed["turn"]["commit_approvals"]["degraded_summary"] is True


def test_summary_model_receives_existing_personality_fingerprint(tmp_path):
    captured = {}

    class SummaryLLM:
        @staticmethod
        def chat(system, prompt, **_kwargs):
            captured["prompt"] = prompt
            return '{"summary":"林舟作出决定。"}'

    storage = StorageManager(LOGGER)
    novel = NovelManager("摘要人格书", tmp_path, LOGGER, storage)
    CharacterManager(tmp_path, LOGGER).create_character("林舟", personality_profile={
        "desire": "保护妹妹", "principle": "不牺牲无辜者", "decision_style": "先核实证据", "flaw": "拒绝求助",
    })
    result = SummaryManager(novel, LOGGER, SummaryLLM())._llm_summary(1, "林舟决定先检查证据，再进入仓库。" * 8)
    assert result["analysis_degraded"] is False
    assert "已有人格指纹" in captured["prompt"]
    assert "不牺牲无辜者" in captured["prompt"]


def test_summary_model_receives_pending_character_personality_as_provisional(tmp_path):
    captured = {}

    class SummaryLLM:
        @staticmethod
        def chat(system, prompt, **_kwargs):
            captured["prompt"] = prompt
            return '{"summary":"苏遥决定保护证据。"}'

    storage = StorageManager(LOGGER)
    novel = NovelManager("临时人物摘要书", tmp_path, LOGGER, storage)
    storage.atomic_write_json(tmp_path / "reviews" / "character_changes.json", {"items": ["损坏记录", {
        "id": "pending-su", "chapter": 1, "name": "苏遥", "field": "new_character", "status": "pending",
        "details": {
            "personality": "危机时优先保护证据",
            "relationships": "暂时协助林舟",
            "personality_profile": {"principle": "证据不能被污染", "decision_style": "先封存再行动"},
        },
    }]})
    result = SummaryManager(novel, LOGGER, SummaryLLM())._llm_summary(2, "苏遥先封存证据，然后才去救人。" * 8)
    assert result["analysis_degraded"] is False
    assert "证据不能被污染" in captured["prompt"]
    assert '"provisional": true' in captured["prompt"]


def test_review_queue_aggregates_without_model_calls(tmp_path):
    QualityTracker(tmp_path, LOGGER).add_debt(3, "logic", "高", "人物在同一时间出现在两个地点")
    queue = ReviewQueueManager(tmp_path, LOGGER).build()
    assert queue["total"] == 1
    assert queue["blocking"] == 1
    assert queue["items"][0]["type"] == "quality_debt"


def test_review_queue_skips_explicitly_approved_clock_exception(tmp_path):
    storage = StorageManager(LOGGER)
    storage.atomic_write_json(tmp_path / "tracking" / "story_clock.json", {"travel_rules": [], "events": [{
        "chapter": 2, "story_time": "第1天 10:00", "location": "港口", "characters": ["林舟"],
        "issues": [{"severity": "高", "blocking": True, "message": "移动耗时不足"}],
    }]})
    storage.atomic_write_json(tmp_path / "turns" / "index.json", {"items": [{
        "id": "turn-2", "chapter": 2, "status": "committed",
        "commit_approvals": {"story_clock_conflicts": True},
    }]})
    assert ReviewQueueManager(tmp_path, LOGGER, storage).build()["items"] == []


def test_review_queue_includes_degraded_summary_turn(tmp_path):
    storage = StorageManager(LOGGER)
    storage.atomic_write_json(tmp_path / "turns" / "index.json", {"items": [{
        "id": "turn-1", "chapter": 1, "status": "ready",
        "preview": {"analysis_degraded": True, "analysis_error": "模型未返回JSON"},
    }]})
    queue = ReviewQueueManager(tmp_path, LOGGER, storage).build()
    assert queue["items"][0]["type"] == "degraded_summary"
    assert queue["blocking"] == 1


def test_governance_readers_skip_corrupt_clock_records(tmp_path):
    storage = StorageManager(LOGGER)
    storage.atomic_write_json(tmp_path / "tracking" / "story_clock.json", {
        "travel_rules": [{"from": "旧城", "to": "港口", "minutes": "坏数据"}, {"from": "甲", "to": "乙", "minutes": 30}],
        "events": [{"chapter": "坏数据"}, {"chapter": 1, "story_time": "第1天", "location": "甲", "characters": []}],
    })
    clock = StoryClockManager(tmp_path, LOGGER, storage).get()
    assert clock["travel_rules"] == [{"from": "甲", "to": "乙", "minutes": 30}]
    assert [item["chapter"] for item in clock["events"]] == [1]


def test_history_impact_includes_locks_and_story_clock(tmp_path):
    storage = StorageManager(LOGGER)
    CanonicalLockManager(tmp_path, LOGGER, storage).upsert("character", "林舟", "current_status", "存活")
    storage.atomic_write_json(tmp_path / "tracking" / "story_clock.json", {"travel_rules": [], "events": [{
        "chapter": 2, "story_time": "第1天 10:00", "location": "旧城", "characters": ["林舟"],
    }]})
    result = HistoryImpactAnalyzer(tmp_path, storage).analyze("林舟存活", "林舟死亡", ["林舟", "存活", "死亡"])
    assert "canonical_locks" in result["categories"]
    assert "story_clock" in result["categories"]
    assert result["risk_level"] == "高"


def test_generation_context_contains_locks_and_story_clock(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("治理上下文书", tmp_path, LOGGER, storage)
    CanonicalLockManager(tmp_path, LOGGER, storage).upsert("world_rule", "记忆读取", "limit", "每人只能读取一次")
    clock = StoryClockManager(tmp_path, LOGGER, storage)
    clock.set_travel_rule("旧城", "港口", 120)
    clock.record(1, {"handoff": {"final_scene": {
        "location": "旧城", "story_time": "第1天 10:00", "active_characters": ["林舟"],
    }}})
    context = ContextManager(novel, LOGGER).build_context(max_tokens=5000)
    assert "用户权威设定锁" in context
    assert "每人只能读取一次" in context
    assert "故事时钟与行程约束" in context
    assert "旧城 ↔ 港口" in context


def test_pending_new_character_profile_is_available_to_next_chapter_logic(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("临时人物上下文书", tmp_path, LOGGER, storage)
    ChangeReviewManager(tmp_path, LOGGER, storage).add_new_characters(2, [{
        "name": "苏遥", "personality": "先核实证据再行动", "evidence": "苏遥递出证件",
        "personality_profile": {
            "desire": "查清真相", "principle": "证据优先", "decision_style": "先核实证据再行动",
            "stress_response": "封存证据",
        },
    }])
    context = ContextManager(novel, LOGGER).build_context(max_tokens=5000)
    assert "待确认的新人物临时档案" in context
    assert "查清真相" in context
    issues = CharacterDecisionValidator(tmp_path, LOGGER, storage).inspect([{
        "name": "苏遥", "action": "继续调查", "motive": "查清真相",
        "personality_basis": "证据优先", "evidence_verified": True,
    }])
    assert issues == []


def test_derived_rebuild_preserves_manual_travel_rules_and_replays_clock(tmp_path):
    storage = StorageManager(LOGGER)
    clock = StoryClockManager(tmp_path, LOGGER, storage)
    clock.set_travel_rule("旧城", "港口", 120)
    for chapter, location, story_time in ((1, "旧城", "第1天 10:00"), (2, "港口", "第1天 10:30")):
        storage.atomic_write_json(tmp_path / "summaries" / f"{chapter:06d}.json", {
            "chapter": chapter, "summary": "测试摘要", "handoff": {"final_scene": {
                "location": location, "story_time": story_time, "active_characters": ["林舟"],
            }},
        })
    result = DerivedStateRebuilder(tmp_path, LOGGER, storage).rebuild(2)
    rebuilt = clock.get()
    assert rebuilt["travel_rules"][0]["minutes"] == 120
    assert len(rebuilt["events"]) == 2
    assert result["story_clock_issues"] == 1
