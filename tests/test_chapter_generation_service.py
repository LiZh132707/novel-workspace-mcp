import json
import logging

from core.chapter_generation_service import ChapterGenerationService, GenerationInterrupted, PIPELINE_STAGES
import pytest
from core.chapter_manager import ChapterManager
from core.chapter_turn_engine import ChapterTurnEngine
from core.character_manager import CharacterManager
from core.novel_manager import NovelManager
from core.scene_outline_manager import SceneOutlineManager
from storage_utils import StorageManager


LOGGER = logging.getLogger("chapter-generation-service-test")


class FakeContextManager:
    def __init__(self):
        self.last_build_stats = {}

    def build_context(self, profile="balanced"):
        self.last_build_stats = {"planning_epoch": "", "profile": profile}
        return f"【{profile}上下文】世界规则、人物状态、当前卷纲与上一章交接。"


class ScriptedNovelLLM:
    def __init__(self):
        self.last_metrics = {}
        self.calls = []
        self.active = 0
        self.max_active = 0

    def chat(self, system, prompt, max_tokens=0, task_type="general", **_kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append(task_type)
        try:
            self.last_metrics = {
                "prompt_tokens": 100, "completion_tokens": 500,
                "elapsed_seconds": 10, "tokens_per_second": 50,
                "prompt_tokens_per_second": 300, "seed": 42,
            }
            if task_type == "planning":
                return json.dumps({
                    "beats": ["林舟进入现场确认目标", "苏遥带来线索并形成下一步选择"],
                    "scenes": [
                        {"name": "进入现场", "goal": "确认异常", "obstacle": "现场封锁", "turn": "找到暗门", "exit_state": "进入地下层", "word_budget": 900},
                        {"name": "地下调查", "goal": "取得记录", "obstacle": "设备断电", "turn": "备用电源启动", "exit_state": "获得嫌疑名单", "word_budget": 1100},
                    ],
                }, ensure_ascii=False)
            if task_type == "structured":
                return json.dumps({
                    "summary": "林舟与苏遥进入地下层调查并取得新的嫌疑名单。",
                    "handoff": {"final_scene": {
                        "location": "地下层", "story_time": f"第{len(self.calls)}天 10:00",
                        "active_characters": ["林舟", "苏遥"], "last_action": "两人核对嫌疑名单",
                    }},
                }, ensure_ascii=False)
            return self._prose()
        finally:
            self.active -= 1

    @staticmethod
    def _prose():
        paragraphs = []
        actions = ["检查门锁", "核对记录", "观察脚印", "询问守卫", "标记时间", "收起证物"]
        for index in range(70):
            action = actions[index % len(actions)]
            paragraphs.append(
                f"林舟在第{index + 1}处灯影下{action}，潮湿空气里混着金属气味。"
                f"苏遥低声说：“这条线索改变了我们原来的判断。”两人随即调整位置，继续向地下层推进。"
            )
        return "\n\n".join(paragraphs)


def _brief_writer(storage, root):
    def ensure(novel, _llm, _context, chapter):
        brief = {
            "chapter": chapter, "title": f"第{chapter}次推进", "chapter_mode": "main_progress",
            "synopsis": "林舟与苏遥根据上一章结果进入新的调查现场，在阻力中取得线索并形成下一章必须处理的新选择。",
            "structural_purpose": "推进当前节纲并保留后续调查空间", "side_value": "",
            "entry_state": "两人抵达现场", "exit_state": "取得新的嫌疑名单",
            "must_happen": ["获得线索"], "must_not_happen": ["提前解决全卷冲突"],
            "characters": ["林舟", "苏遥"], "foreshadowing": ["名单来源"],
        }
        path = root / "outline" / "chapter_briefs.json"
        data = storage.safe_read_json(path, {})
        data = data if isinstance(data, dict) else {}
        data[str(chapter)] = brief
        storage.atomic_write_json(path, data)
        return brief
    return ensure


def test_three_chapter_fake_model_release_acceptance(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("模拟验收书", tmp_path, LOGGER, storage)
    storage.atomic_write_text(tmp_path / "bible" / "world.md", "近未来封闭城市，信息传播受到严格限制。")
    storage.atomic_write_text(tmp_path / "bible" / "rules.md", "人物只能依据亲眼所见和明确转述行动。")
    storage.atomic_write_text(tmp_path / "outline" / "main.md", "第一卷调查失踪案，并在卷末确认幕后组织存在。")
    storage.atomic_write_json(tmp_path / "outline" / "volumes.json", [{
        "title": "调查卷", "start_chapter": 1, "end_chapter": 10,
        "goal": "确认幕后组织", "sections": [],
    }])
    characters = CharacterManager(tmp_path, LOGGER)
    characters.create_character("林舟", personality="谨慎", role_tier="主角")
    characters.create_character("苏遥", personality="敏锐", role_tier="重要配角")

    llm = ScriptedNovelLLM()
    context = FakeContextManager()
    plan_cache = {}

    def load_plan(_novel, chapter, fingerprint):
        item = plan_cache.get(chapter)
        return item["plan"] if item and item["fingerprint"] == fingerprint else None

    def save_plan(_novel, chapter, fingerprint, plan):
        plan_cache[chapter] = {"fingerprint": fingerprint, "plan": plan}
        SceneOutlineManager(tmp_path, LOGGER, storage).seed_from_plan(chapter, plan)

    service = ChapterGenerationService(
        novel, llm, context, storage,
        {"analysis_max_tokens": 1536, "max_output_tokens": 8192},
        _brief_writer(storage, tmp_path),
        lambda _novel, chapter, _brief, target, continuation: f"fp-{chapter}-{target}-{continuation}",
        load_plan, save_plan,
        lambda _novel, chapter: None,
    )
    chapter_manager = ChapterManager(novel, LOGGER, llm)
    engine = ChapterTurnEngine(novel, LOGGER, chapter_manager, storage)
    observed_stages = []

    for chapter in range(1, 4):
        generated = service.generate(
            chapter, 500, task_id=f"task-{chapter}",
            on_event=lambda stage, _message, _progress, _level: observed_stages.append(stage),
        )
        turn = engine.save_draft(
            chapter, generated["content"], 500, "acceptance",
            service.turn_metadata(
                f"task-{chapter}", generated["metrics"], generated["planning_epoch"],
                generated["planning_fingerprint"], generated["planning_stale"], {},
                generation_profile=generated["generation_profile"],
            ), False,
        )
        stored_turn = engine.get(turn["id"])
        assert stored_turn["metadata"]["pipeline"]["checkpoint"] == "draft_ready"
        assert stored_turn["metadata"]["generation_profile"]["seed"] == 42
        engine.commit(turn["id"], allow_quality_failure=True, allow_fact_conflicts=True)
        service.clear_working_draft(chapter)

    assert novel.get_current_chapter() == 3
    assert novel.get_state()["total_words"] > 1500
    assert all(chapter_manager.commits.is_committed(chapter, chapter_manager.read_chapter(chapter)) for chapter in range(1, 4))
    assert characters.get_character("林舟")["last_chapter"] == 3
    assert characters.get_character("苏遥")["last_chapter"] == 3
    assert len(list((tmp_path / "summaries").glob("*.json"))) == 3
    assert len(list((tmp_path / "timeline").glob("*.json"))) == 3
    assert set(PIPELINE_STAGES) >= set(observed_stages)
    assert {"brief", "planning", "writing", "quality"} <= set(observed_stages)
    assert llm.max_active == 1
    assert not list((tmp_path / "drafts").glob("*_working.*"))


def test_service_reuses_matching_working_draft_without_second_prose_call(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("草稿恢复书", tmp_path, LOGGER, storage)
    llm = ScriptedNovelLLM()
    context = FakeContextManager()
    cached = {}

    def load_plan(_novel, chapter, fingerprint):
        return cached.get((chapter, fingerprint))

    def save_plan(_novel, chapter, fingerprint, plan):
        cached[(chapter, fingerprint)] = plan

    service = ChapterGenerationService(
        novel, llm, context, storage,
        {"analysis_max_tokens": 1536, "max_output_tokens": 8192},
        _brief_writer(storage, tmp_path),
        lambda _novel, chapter, _brief, target, continuation: f"fp-{chapter}-{target}-{continuation}",
        load_plan, save_plan, lambda _novel, chapter: None,
    )
    first = service.generate(1, 500, task_id="same-task")
    prose_calls = llm.calls.count("prose")
    second = service.generate(1, 500, task_id="same-task")
    assert second["content"] == first["content"]
    assert llm.calls.count("prose") == prose_calls


def test_service_stops_between_model_calls_and_keeps_recoverable_working_draft(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("暂停恢复书", tmp_path, LOGGER, storage)
    llm = ScriptedNovelLLM()
    context = FakeContextManager()
    cached = {}
    service = ChapterGenerationService(
        novel, llm, context, storage,
        {"analysis_max_tokens": 1536, "max_output_tokens": 8192},
        _brief_writer(storage, tmp_path),
        lambda _novel, chapter, _brief, target, continuation: f"fp-{chapter}-{target}-{continuation}",
        lambda _novel, chapter, fingerprint: cached.get((chapter, fingerprint)),
        lambda _novel, chapter, fingerprint, plan: cached.__setitem__((chapter, fingerprint), plan),
        lambda _novel, chapter: None,
    )
    with pytest.raises(GenerationInterrupted):
        service.generate(
            1, 500, task_id="paused-task",
            should_stop=lambda: llm.calls.count("prose") >= 1,
        )
    assert (tmp_path / "drafts" / "000001_working.txt").exists()
    assert llm.calls.count("prose") == 1


def test_scene_mode_resumes_from_last_completed_scene(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("场景断点书", tmp_path, LOGGER, storage)
    llm = ScriptedNovelLLM()
    context = FakeContextManager()
    cached = {}
    service = ChapterGenerationService(
        novel, llm, context, storage,
        {"analysis_max_tokens": 1536, "max_output_tokens": 8192},
        _brief_writer(storage, tmp_path),
        lambda _novel, chapter, _brief, target, continuation: f"fp-{chapter}-{target}-{continuation}",
        lambda _novel, chapter, fingerprint: cached.get((chapter, fingerprint)),
        lambda _novel, chapter, fingerprint, plan: cached.__setitem__((chapter, fingerprint), plan),
        lambda _novel, chapter: None,
    )

    with pytest.raises(GenerationInterrupted):
        service.generate(
            1, 500, scene_mode=True, task_id="scene-task", auto_revision=False,
            should_stop=lambda: llm.calls.count("prose") >= 1,
        )
    checkpoint = tmp_path / "drafts" / "scene_checkpoints" / "000001.json"
    assert checkpoint.exists()
    assert storage.safe_read_json(checkpoint, {})["completed_scenes"] == 1

    prose_calls = llm.calls.count("prose")
    generated = service.generate(1, 500, scene_mode=True, task_id="scene-task", auto_revision=False)
    assert llm.calls.count("prose") == prose_calls + 1
    assert len(generated["content"]) > len(ScriptedNovelLLM._prose())
    service.clear_working_draft(1)
    assert not checkpoint.exists()
