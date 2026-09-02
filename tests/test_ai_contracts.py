import json
import pytest

from core.ai_contracts import (
    chapter_completion_prompts, chapter_quality_gate, inspect_chapter, merge_chapter_continuation,
    parse_object, planning_prompts, selection_edit_prompts,
    detect_style_reference_leaks, staged_planning_prompts, style_analysis_prompts, validate_character_extraction,
    validate_plan, validate_planning_stage, validate_style_analysis, validate_summary,
    volume_sections_are_valid,
    build_fallback_volumes,
)
from core.fact_manager import FactManager
import logging
import tempfile
from pathlib import Path


def test_parse_repairs_common_json_errors():
    data = parse_object('结果如下：```json\n{"world":"甲" "outline":"乙", "first_goal":"丙"}\n```')
    assert data["world"] == "甲"
    assert data["outline"] == "乙"


def test_plan_normalizes_characters_and_warns():
    plan, warnings = validate_plan({
        "world": "世界" * 100,
        "rules": "规则" * 70,
        "style": "风格" * 50,
        "outline": "剧情" * 180,
        "first_goal": "主角抵达车站并发现追踪者，通过一次具体冲突得到失踪案的关键线索，同时暴露自己的调查目的，结尾在人群中看见本应已经死亡的证人。",
        "characters": [
            {"name": "林舟", "role": "主角", "personality": "谨慎", "background": "记者"},
            {"name": "林舟", "role": "重复"},
            {"name": "苏遥", "role": "对手", "personality": "冷静", "background": "调查员"},
        ],
    })
    assert len(plan["characters"]) == 2
    assert not warnings


def test_summary_and_chapter_quality():
    summary = validate_summary({"summary": "主角进入旧城并找到线索。", "facts": []}, 3)
    assert summary["chapter"] == 3
    assert inspect_chapter("以下是第一章内容。", 3000)
    assert not inspect_chapter("风吹过长街。" * 300, 1000)
    repeated = "这是一个足够长且完全相同的重复段落，用于检查生成内容。\n" * 3
    assert "正文存在完全重复的段落" in inspect_chapter(repeated, 1000)


def test_summary_handoff_only_keeps_exact_evidence():
    content = "林舟推开仓库铁门。苏遥留在雨中警戒。警报声突然从地下传来。"
    summary = validate_summary({
        "summary": "两人抵达仓库并听见警报。",
        "handoff": {
            "final_scene": {"location": "仓库", "active_characters": ["林舟", "苏遥"], "last_action": "两人准备下楼"},
            "open_loops": ["地下警报来源未知"],
            "evidence_quotes": ["警报声突然从地下传来。", "正文中不存在的证据"],
        },
        "plan_reconciliation": {
            "next_chapter_impacts": ["必须调查地下警报"],
            "evidence_quotes": ["苏遥留在雨中警戒。", "伪造引文"],
        },
    }, 3, content)
    assert summary["handoff"]["evidence_quotes"] == ["警报声突然从地下传来。"]
    assert summary["plan_reconciliation"]["evidence_quotes"] == ["苏遥留在雨中警戒。"]
    assert len(summary["source_hash"]) == 64


def test_summary_normalizes_common_model_aliases_before_governance():
    content = "林舟在港口确认自己已经受伤，苏遥仍守在门外。"
    summary = validate_summary({
        "summary": "林舟受伤后抵达港口。",
        "人物变化": [{"姓名": "林舟", "字段": "状态", "新值": "受伤", "证据": "林舟在港口确认自己已经受伤"}],
        "交接": {"结尾场景": {
            "地点": "港口", "time": "第2天 09:30", "characters": "林舟、苏遥", "最后动作": "苏遥守门",
        }},
    }, 4, content)
    change = summary["characters_changed"][0]
    assert change["name"] == "林舟"
    assert change["field"] == "current_status"
    assert change["new_value"] == "受伤"
    assert change["evidence_verified"] is True
    assert summary["handoff"]["final_scene"] == {
        "location": "港口", "story_time": "第2天 09:30",
        "active_characters": ["林舟", "苏遥"], "last_action": "苏遥守门",
    }


def test_summary_infers_character_field_from_direct_alias_value():
    summary = validate_summary({
        "summary": "人物状态发生变化。",
        "characters_changed": [{"character": "林舟", "status": "失踪"}],
    }, 2)
    assert summary["characters_changed"][0]["field"] == "current_status"
    assert summary["characters_changed"][0]["new_value"] == "失踪"


def test_summary_entity_contract_strips_model_metadata_and_normalizes_aliases():
    content = "林舟把钥匙收入外套内袋。"
    summary = validate_summary({
        "摘要": "林舟取得钥匙。",
        "items": [{
            "物品": "钥匙", "持有者": "林舟", "位置": "外套内袋", "状态": "完好",
            "证据": "林舟把钥匙收入外套内袋", "analysis": "这段模型解释不得进入状态账本",
        }],
        "下一章目标": "确认钥匙用途",
    }, 1, content)
    item = summary["items"][0]
    assert item["name"] == "钥匙" and item["owner"] == "林舟"
    assert item["location"] == "外套内袋" and item["status"] == "完好"
    assert item["evidence_verified"] is True
    assert "analysis" not in item
    assert summary["next_goal"] == "确认钥匙用途"


def test_summary_verifies_every_persistent_memory_record_against_chapter_text():
    content = "林舟看见红灯后相信出口已经封锁，因此决定返回大厅。"
    summary = validate_summary({
        "summary": "林舟因红灯折返。",
        "facts": [{"subject": "出口", "predicate": "状态", "object": "封锁", "evidence": "正文没有这句话"}],
        "foreshadowing": [{"action": "introduce", "text": "红灯来源", "evidence": "林舟看见红灯"}],
        "narrative_promises": [{"text": "调查红灯", "evidence": "正文不存在承诺"}],
        "causal_links": [{"cause": "看见红灯", "effect": "返回大厅", "actor": "林舟", "evidence": "因此决定返回大厅"}],
        "knowledge_changes": [{"name": "林舟", "fact": "出口已经封锁", "status": "believed", "evidence": "相信出口已经封锁"}],
    }, 2, content)
    assert summary["facts"][0]["evidence_verified"] is False
    assert summary["foreshadowing"][0]["evidence_verified"] is True
    assert summary["narrative_promises"][0]["evidence_verified"] is False
    assert summary["causal_links"][0]["evidence_verified"] is True
    assert summary["knowledge_changes"][0]["evidence_verified"] is True


def test_chapter_quality_gate_and_completion_merge():
    failed = chapter_quality_gate("短文" * 100, 1000)
    assert failed["status"] == "FAIL"
    passed = chapter_quality_gate("风吹过长街，人物继续向前。" * 100, 1000)
    assert passed["status"] == "PASS"
    system, prompt = chapter_completion_prompts("测试", "甲" * 700, 1000, "结尾找到线索")
    assert "约300至360字" in system
    assert "结尾找到线索" in prompt
    overlap = "他推开门后看见走廊尽头亮着一盏红灯。"
    assert merge_chapter_continuation("前文。" + overlap, overlap + "他继续向前。") == "前文。" + overlap + "\n\n他继续向前。"


def test_character_extraction_filters_known_and_duplicate_names():
    characters = validate_character_extraction({"new_characters": [
        {"name": "林舟"}, {"name": "苏遥", "evidence": "苏遥递出证件"}, {"name": "苏遥"},
    ]}, ["林舟"])
    assert [item["name"] for item in characters] == ["苏遥"]


def test_fact_ledger_tracks_history_and_hard_conflicts():
    with tempfile.TemporaryDirectory() as tmp:
        facts = FactManager(Path(tmp), logging.getLogger("test"))
        facts.add_from_summary(1, [{"subject": "林舟", "predicate": "位置", "object": "旧城"}])
        result = facts.add_from_summary(2, [{"subject": "林舟", "predicate": "位置", "object": "车站"}])
        assert result["conflicts"] == 0
        facts.add_from_summary(1, [{"subject": "林舟", "predicate": "本名", "object": "林舟"}])
        result = facts.add_from_summary(3, [{"subject": "林舟", "predicate": "本名", "object": "周林"}])
        assert result["conflicts"] == 1


def test_fact_ledger_compares_against_latest_value_not_any_historical_value():
    with tempfile.TemporaryDirectory() as tmp:
        manager = FactManager(Path(tmp), logging.getLogger("test"))
        manager.add_from_summary(1, [{"subject": "林舟", "predicate": "身份", "object": "记者"}])
        manager.add_from_summary(2, [{"subject": "林舟", "predicate": "身份", "object": "卧底"}])
        result = manager.add_from_summary(3, [{"subject": "林舟", "predicate": "身份", "object": "记者"}])
        assert result["conflicts"] == 2


def test_selection_edit_prompt_preserves_story_facts():
    system, prompt = selection_edit_prompts("测试", "林舟走进车站。", "deai")
    assert "不能改变" in system
    assert "降低AI味" not in prompt
    assert "机械排比" in prompt


def test_style_reference_requests_abstract_traits_without_copying():
    _, prompt = planning_prompts("测试", "悬疑", "失踪案", "", "他推开门，雨声骤然逼近。")
    assert "文风参考文本" in prompt
    assert "不得复用参考文本的句子" in prompt


def test_staged_planning_never_receives_style_reference_plot():
    source = {
        "name": "测试", "description": "原创故事", "style_reference": "参考人物张三在月球盗窃王冠",
        "style_profile": {"style_instruction": "短句，有限视角，克制对白"},
    }
    _, prompt = staged_planning_prompts("foundation", source, {})
    assert "张三" not in prompt
    assert "月球盗窃王冠" not in prompt
    assert "短句，有限视角" in prompt


def test_staged_planning_receives_specific_story_engine_fields():
    source = {
        "name": "测试", "description": "失踪案", "protagonist": "负债记者",
        "external_goal": "七天内找到失踪证人", "internal_need": "承认自己需要他人帮助",
        "opposition": "掌控媒体的财团", "stakes": "失败会让证人死亡并使主角入狱",
        "inciting_incident": "本已死亡的证人寄来当天报纸", "world_rules": "记忆只能读取一次",
        "power_cost": "每次读取会永久丢失自己的一段记忆", "core_question": "证人为何被抹除",
    }
    system, prompt = staged_planning_prompts("foundation", source, {})
    assert "七天内找到失踪证人" in prompt
    assert "每次读取会永久丢失" in prompt
    assert "有明确内容的字段属于硬约束" in system


def test_confirmed_story_seed_replaces_verbose_source_in_downstream_prompt():
    source = {
        "name": "测试", "description": "冗长原始创意不应继续传递", "notes": "重复访谈原文",
        "protagonist": "原始主角访谈", "story_seed": {
            "logline": "记者在七天内寻找被系统抹除的证人",
            "protagonist_engine": "负债记者必须找到证人",
            "conflict_engine": "财团阻止调查",
            "world_contract": "记忆读取有永久代价",
            "ending_state": "证据公开但主角失去关键记忆",
            "must_keep": ["七天期限"], "must_avoid": ["万能能力"],
        },
    }
    _, prompt = staged_planning_prompts("foundation", source, {})
    assert "canonical_story_seed" in prompt
    assert "七天内寻找" in prompt
    assert "重复访谈原文" not in prompt


def test_style_analysis_is_plot_agnostic():
    system, _ = style_analysis_prompts("张三进入古堡")
    assert "不得作为新小说的剧情素材" in system
    assert "不要净化、弱化" in system
    result = validate_style_analysis({"style_instruction": "使用短句和有限视角。"})
    assert result["style_instruction"] == "使用短句和有限视角。"


def test_prompt_functions_include_editable_instruction_section():
    system, _ = style_analysis_prompts("一段参考文本")
    assert "用户可编辑提示词" in system


def test_style_reference_leak_detection_excludes_style_field():
    reference = "张三进入废弃月宫并偷走一顶黑色王冠"
    leaked = {"world": "传说张三进入废弃月宫并偷走一顶黑色王冠", "style": "短句"}
    assert detect_style_reference_leaks(reference, leaked)
    assert not detect_style_reference_leaks(reference, {"world": "原创海港城市", "style": reference})


def test_staged_structure_must_cover_target_chapters_continuously():
    source = {"name": "测试", "target_chapters": 50}
    _, prompt = staged_planning_prompts("structure", source, {"foundation": {"world": "设定"}})
    assert "恰好50章" in prompt
    valid = validate_planning_stage("structure", {"outline": "总纲", "volumes": [
        {"start_chapter": 1, "end_chapter": 20, "sections": [{"start_chapter": 1, "end_chapter": 20}]},
        {"start_chapter": 21, "end_chapter": 50, "sections": [{"start_chapter": 21, "end_chapter": 50}]},
    ]}, 50)
    assert valid["volumes"][-1]["end_chapter"] == 50
    try:
        validate_planning_stage("structure", {"outline": "总纲", "volumes": [{"start_chapter": 1, "end_chapter": 49, "sections": [{"start_chapter": 1, "end_chapter": 49}]}]}, 50)
        assert False, "incomplete chapter coverage should fail"
    except ValueError:
        pass


def test_staged_characters_sanitize_model_names():
    result = validate_planning_stage("characters", {"characters": [
        {"name": "林舟"}, {"name": "‘影子’乘客（未知）"}, {"name": "苏遥/调查员"},
    ]}, 10)
    assert [item["name"] for item in result["characters"]] == ["林舟", "影子乘客", "苏遥调查员"]
    assert len(result["personality_diversity"]["incomplete_profiles"]) == 3


def test_staged_characters_keep_personality_fingerprints_and_warn_on_clones():
    shared_profile = {
        "desire": "证明自己没有失败", "fear": "再次被同伴抛弃", "principle": "不牺牲无辜者",
        "flaw": "受质疑时独断", "stress_response": "沉默后独自行动", "decision_style": "先调查再冒险",
        "social_posture": "对陌生人戒备", "speech_habits": "短句反问", "contradiction": "渴望信任却拒绝求助",
    }
    result = validate_planning_stage("characters", {"characters": [
        {"name": "林舟", "personality_profile": shared_profile},
        {"name": "苏遥", "personality_profile": shared_profile},
        {"name": "周衡", "desire": "守住家族秘密", "fear": "真相公开"},
    ]}, 20)
    assert result["characters"][0]["personality_profile"]["contradiction"] == "渴望信任却拒绝求助"
    assert result["personality_diversity"]["status"] == "warning"
    assert result["personality_diversity"]["similar_pairs"][0]["left"] == "林舟"


def test_new_character_extraction_keeps_evidence_based_personality_profile():
    characters = validate_character_extraction({"new_characters": [{
        "name": "苏遥", "personality": "受压时仍会先保护证据",
        "personality_profile": {
            "desire": "查清失踪案", "stress_response": "先封存证据再救场",
            "decision_style": "先排除伪证", "speech_habits": "不用感叹句",
        },
        "evidence": "苏遥先将录音笔塞进防水袋。",
    }]}, [])
    profile = characters[0]["personality_profile"]
    assert profile["desire"] == "查清失踪案"
    assert profile["stress_response"] == "先封存证据再救场"
    assert profile["fear"] == ""


def test_summary_marks_hallucinated_new_character_evidence_as_unverified():
    content = "林舟独自进入仓库，没有遇见其他人。"
    summary = validate_summary({
        "summary": "林舟进入仓库。",
        "new_characters": [{"name": "苏遥", "evidence": "苏遥从门后走出来。"}],
    }, 1, content)
    assert summary["new_characters"][0]["evidence_verified"] is False


def test_summary_accepts_single_object_when_model_omits_array_wrapper():
    content = "苏遥递出钥匙。"
    summary = validate_summary({
        "summary": "苏遥交出钥匙。",
        "new_characters": {"name": "苏遥", "evidence": "苏遥递出钥匙"},
        "facts": {"subject": "钥匙", "predicate": "持有者", "object": "苏遥", "evidence": "苏遥递出钥匙"},
    }, 1, content)
    assert summary["new_characters"][0]["name"] == "苏遥"
    assert summary["facts"][0]["subject"] == "钥匙"
    assert summary["facts"][0]["evidence_verified"] is True


def test_volume_section_coverage_validation():
    volume = {"start_chapter": 1, "end_chapter": 10, "sections": [
        {"start_chapter": 1, "end_chapter": 5}, {"start_chapter": 6, "end_chapter": 10},
    ]}
    assert volume_sections_are_valid(volume)
    volume["sections"][1]["start_chapter"] = 7
    assert not volume_sections_are_valid(volume)


def test_volume_ranges_are_normalized_to_full_coverage():
    from core.ai_contracts import normalize_volume_ranges
    volumes = normalize_volume_ranges([
        {"start_chapter": 1, "end_chapter": 6, "sections": [{"start_chapter": 1, "end_chapter": 6}]},
        {"start_chapter": 6, "end_chapter": 9, "sections": [{"start_chapter": 6, "end_chapter": 9}]},
    ], 10)
    assert [(item["start_chapter"], item["end_chapter"]) for item in volumes] == [(1, 6), (7, 10)]
    assert volumes[1]["sections"] == []


def test_volume_ranges_accept_model_chapter_labels():
    from core.ai_contracts import normalize_volume_ranges, normalize_section_ranges
    volumes = normalize_volume_ranges([
        {"start_chapter": "第1章", "end_chapter": "第10章"},
        {"start_chapter": "第11章", "end_chapter": "第20章"},
    ], 20)
    assert [(item["start_chapter"], item["end_chapter"]) for item in volumes] == [(1, 10), (11, 20)]
    volumes[0]["sections"] = [{"start_chapter": "第1章", "end_chapter": "第5章"}, {"start_chapter": "第6章", "end_chapter": "第10章"}]
    assert [(item["start_chapter"], item["end_chapter"]) for item in normalize_section_ranges(volumes[0])] == [(1, 5), (6, 10)]


def test_section_ranges_are_normalized_or_created():
    from core.ai_contracts import normalize_section_ranges
    volume = {"title": "第一卷", "start_chapter": 1, "end_chapter": 10, "sections": [
        {"title": "前段", "start_chapter": 1, "end_chapter": 6},
        {"title": "后段", "start_chapter": 6, "end_chapter": 9},
    ]}
    sections = normalize_section_ranges(volume)
    assert [(item["start_chapter"], item["end_chapter"]) for item in sections] == [(1, 6), (7, 10)]
    generated = normalize_section_ranges({"title": "短卷", "start_chapter": 1, "end_chapter": 7, "goal": "找到入口"})
    assert generated[0]["start_chapter"] == 1 and generated[-1]["end_chapter"] == 7


def test_opening_chapters_are_normalized_to_five():
    from core.ai_contracts import normalize_opening_chapters
    chapters = normalize_opening_chapters([{"chapter": 8, "title": "错误编号", "synopsis": "已有提要"}], 10, {"outline": "调查不存在的车站"})
    assert [item["chapter"] for item in chapters] == [1, 2, 3, 4, 5]
    assert all(item["synopsis"] for item in chapters)


def test_duplicate_opening_chapter_plans_are_repaired():
    from core.ai_contracts import duplicate_opening_chapters, repair_duplicate_opening_chapters
    repeated = {
        "opening": "同一个工作室开场",
        "beats": ["发现同一异常", "做出同一选择", "得到同一结果"],
        "ending_hook": "同一个结尾钩子导致完全重复的后果",
    }
    chapters = [
        {"chapter": 1, "synopsis": "第一章发现异常", "goal": "立案", **repeated},
        {"chapter": 2, "synopsis": "第二章核查证据", "goal": "取得离线日志", **repeated},
    ]
    assert duplicate_opening_chapters(chapters) == [2]
    repaired = repair_duplicate_opening_chapters(chapters)
    assert duplicate_opening_chapters(repaired) == []
    assert repaired[1]["opening"] != chapters[1]["opening"]
    assert "取得离线日志" in repaired[1]["beats"][0]


def test_opening_cannot_turn_living_planned_character_into_victim():
    from core.ai_contracts import opening_character_identity_conflicts
    characters = [{"name": "沈川", "background": "存活的证人与死者家属", "arc": "逐步重建信任"}]
    chapters = [{"chapter": 1, "characters": ["沈川（死者）"], "synopsis": "沈川已经死亡"}]
    conflicts = opening_character_identity_conflicts(chapters, characters)
    assert conflicts and conflicts[0]["name"] == "沈川"


def test_opening_identity_conflict_can_be_repaired_without_changing_roster():
    from core.ai_contracts import (
        opening_character_identity_conflicts, repair_opening_character_identity_conflicts,
    )
    characters = [{"name": "沈川", "background": "存活的证人与死者家属", "arc": "逐步重建信任"}]
    chapters = [{"chapter": 1, "characters": ["沈川（死者）"], "synopsis": "沈川死后的数据出现"}]
    repaired, replacements = repair_opening_character_identity_conflicts(chapters, characters)
    assert replacements["沈川"] != "沈川"
    assert opening_character_identity_conflicts(repaired, characters) == []
    assert characters[0]["name"] == "沈川"


def test_opening_main_chapter_restores_confirmed_protagonist():
    from core.ai_contracts import repair_opening_protagonist_omissions
    characters = [
        {"name": "林砚", "role": "主角"},
        {"name": "沈川", "role": "重要配角"},
    ]
    chapters = [{
        "chapter": 1, "chapter_mode": "setup", "characters": ["沈川", "死者（无名）"],
        "synopsis": "沈川在修复室检查记忆，沈川决定保存证据。",
    }]
    repaired, numbers = repair_opening_protagonist_omissions(chapters, characters)
    assert numbers == [1]
    assert "林砚" in json.dumps(repaired[0], ensure_ascii=False)
    assert "沈川" not in json.dumps(repaired[0], ensure_ascii=False)


def test_evidence_verifier_accepts_multiple_exact_fragments_joined_by_ellipsis():
    from core.ai_contracts import evidence_in_content
    content = "林砚按下按钮，强行读取后续片段。随后她将异常数据保存在离线硬盘里。"
    assert evidence_in_content("林砚按下按钮...将异常数据保存在离线硬盘里", content)
    assert not evidence_in_content("林砚按下按钮...顾衡当场认罪", content)


def test_chapter_artifact_blocks_living_character_used_as_dead_victim():
    from core.ai_contracts import PlanningArtifactError, validate_chapter_artifact
    roster = [
        {"name": "林砚", "role_tier": "主角", "current_status": "存活"},
        {"name": "沈川", "role_tier": "重要配角", "current_status": "存活"},
    ]
    brief = {"chapter": 1, "chapter_mode": "setup", "characters": ["林砚", "沈川（死者）"]}
    with pytest.raises(PlanningArtifactError, match="沈川"):
        validate_chapter_artifact(brief, roster, label="章前提要", require_protagonist=True)


def test_chapter_artifact_requires_protagonist_only_for_main_chapter():
    from core.ai_contracts import PlanningArtifactError, validate_chapter_artifact
    roster = [{"name": "林砚", "role_tier": "主角", "current_status": "存活"}]
    artifact = {"characters": ["苏遥"], "beats": ["苏遥独自核查证据"]}
    with pytest.raises(PlanningArtifactError, match="缺少已确认主角"):
        validate_chapter_artifact(artifact, roster, label="详细规划", require_protagonist=True)
    assert validate_chapter_artifact(artifact, roster, label="支线规划") is artifact


def test_chapter_artifact_allows_new_distinct_victim_name():
    from core.ai_contracts import validate_chapter_artifact
    roster = [{"name": "林砚", "role_tier": "主角", "current_status": "存活"}]
    artifact = {"characters": ["林砚", "陈默（死者）"], "beats": ["林砚核查陈默死后的记录"]}
    assert validate_chapter_artifact(
        artifact, roster, label="详细规划", require_protagonist=True,
    ) is artifact


def test_chapter_artifact_blocks_future_character_early_appearance():
    from core.ai_contracts import PlanningArtifactError, validate_chapter_artifact
    roster = [
        {"name": "林砚", "role_tier": "主角", "current_status": "存活", "appearance_start": 1},
        {"name": "周岚", "role_tier": "重要配角", "current_status": "存活", "appearance_start": 5},
    ]
    artifact = {"chapter": 2, "characters": ["林砚", "周岚"], "beats": ["周岚交出证据"]}
    with pytest.raises(PlanningArtifactError, match="不得在第2章提前出现"):
        validate_chapter_artifact(
            artifact, roster, label="章前提要", require_protagonist=True, chapter=2,
        )


def test_chapter_plan_keeps_scene_word_budgets():
    from core.ai_contracts import validate_chapter_plan
    plan = validate_chapter_plan({
        "beats": ["进入现场", "发现异常"],
        "scenes": [{"name": "档案室", "goal": "找线索", "obstacle": "守卫", "turn": "停电", "exit_state": "拿到档案", "word_budget": 1400}],
    })
    assert plan["scenes"][0]["word_budget"] == 1400


def test_generation_loop_detector():
    from llm_client import detect_generation_loop
    unit = "他推开门后看见走廊尽头亮着一盏红灯。"
    assert detect_generation_loop(unit * 5)
    assert not detect_generation_loop("风穿过长街，林舟停下脚步。苏遥从另一侧走来，两人交换了刚得到的线索。" * 3)


def test_fallback_volumes_cover_every_chapter():
    volumes = build_fallback_volumes(100, "主角调查失踪案")
    assert volumes[0]["start_chapter"] == 1
    assert volumes[-1]["end_chapter"] == 100
    assert all(volume_sections_are_valid(volume) for volume in volumes)
