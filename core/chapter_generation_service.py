"""统一章节生成流水线，不负责正史提交与 UI 审批。"""
from __future__ import annotations

import re
from collections.abc import Callable

from core.ai_contracts import (
    chapter_completion_prompts,
    chapter_plan_prompts,
    chapter_prompts,
    chapter_quality_gate,
    merge_chapter_continuation,
    parse_object,
    render_chapter_brief,
    render_chapter_plan,
    revision_prompts,
    scene_write_prompts,
    validate_chapter_plan,
    validate_chapter_artifact,
)
from core.working_draft_manager import WorkingDraftManager
from core.scene_checkpoint_manager import SceneCheckpointManager


class StalePlanningError(RuntimeError):
    pass


class GenerationInterrupted(RuntimeError):
    pass


PIPELINE_STAGES = (
    "context", "brief", "planning", "writing", "completion",
    "revision", "quality", "draft_ready",
)


def aggregate_generation_metrics(items: list[dict]) -> dict:
    valid = [item for item in items if isinstance(item, dict) and item]
    if not valid:
        return {}
    completion_tokens = sum(int(item.get("completion_tokens", 0) or 0) for item in valid)
    prompt_tokens = sum(int(item.get("prompt_tokens", 0) or 0) for item in valid)
    cached_prompt_tokens = sum(int(item.get("cached_prompt_tokens", 0) or 0) for item in valid)
    prompt_eval_tokens = sum(int(item.get("prompt_eval_tokens", 0) or 0) for item in valid)
    elapsed = sum(float(item.get("elapsed_seconds", 0) or 0) for item in valid)
    generation_seconds = sum(
        int(item.get("completion_tokens", 0) or 0)
        / max(0.01, float(item.get("tokens_per_second", 0) or 0.01))
        for item in valid
    )
    return {
        "prompt_tokens": prompt_tokens,
        "cached_prompt_tokens": cached_prompt_tokens,
        "prompt_eval_tokens": prompt_eval_tokens,
        "prompt_tokens_per_second": round(sum(
            float(item.get("prompt_tokens_per_second", 0) or 0) for item in valid
        ) / max(1, len(valid)), 2),
        "completion_tokens": completion_tokens,
        "elapsed_seconds": round(elapsed, 3),
        "time_to_first_token": valid[0].get("time_to_first_token", 0),
        "tokens_per_second": round(completion_tokens / max(0.001, generation_seconds), 2),
        "end_to_end_tokens_per_second": round(completion_tokens / max(0.001, elapsed), 2),
        "seed": valid[0].get("seed"),
        "calls": len(valid),
    }


class ChapterGenerationService:
    def __init__(
        self, novel_manager, llm, context_manager, storage, model_config: dict,
        ensure_brief: Callable, fingerprint: Callable, load_plan: Callable,
        save_plan: Callable, confirmed_plan: Callable,
    ):
        self.nm = novel_manager
        self.llm = llm
        self.context = context_manager
        self.storage = storage
        self.model_config = model_config
        self.ensure_brief = ensure_brief
        self.fingerprint = fingerprint
        self.load_plan = load_plan
        self.save_plan = save_plan
        self.confirmed_plan = confirmed_plan
        self.working = WorkingDraftManager(novel_manager.path, storage)
        self.scene_checkpoints = SceneCheckpointManager(novel_manager.path, storage)

    def resolve_plan(self, chapter: int, fingerprint: str) -> tuple[dict | None, str]:
        plan = self.confirmed_plan(self.nm, chapter)
        if plan is not None:
            return plan, "confirmed"
        plan = self.load_plan(self.nm, chapter, fingerprint)
        return (plan, "cache") if plan is not None else (None, "")

    def accept_generated_plan(self, chapter: int, brief: dict, target_words: int,
                              continuation: bool, fingerprint: str, plan: dict) -> dict:
        plan = validate_chapter_plan(plan)
        self.validate_plan_artifact(plan, brief)
        if self.is_planning_stale(chapter, brief, target_words, continuation, fingerprint):
            raise StalePlanningError(f"第{chapter}章规划生成期间上游设定已变化")
        self.save_plan(self.nm, chapter, fingerprint, plan)
        return plan

    def canonical_characters(self) -> list[dict]:
        character_manager = getattr(self.context, "char_mgr", None)
        return character_manager.canonical_roster() if character_manager is not None else []

    def validate_brief_artifact(self, brief: dict) -> dict:
        return validate_chapter_artifact(
            brief, self.canonical_characters(), label=f"第{brief.get('chapter', '?')}章提要",
            require_protagonist=brief.get("chapter_mode") in {"main_progress", "setup", "complication"},
            chapter=int(brief.get("chapter", 0) or 0),
        )

    def validate_plan_artifact(self, plan: dict, brief: dict) -> dict:
        return validate_chapter_artifact(
            plan, self.canonical_characters(), label=f"第{brief.get('chapter', '?')}章详细规划",
            require_protagonist=brief.get("chapter_mode") in {"main_progress", "setup", "complication"},
            chapter=int(brief.get("chapter", 0) or 0),
        )

    def is_planning_stale(self, chapter: int, brief: dict, target_words: int,
                          continuation: bool, fingerprint: str) -> bool:
        return self.fingerprint(self.nm, chapter, brief, target_words, continuation) != fingerprint

    @staticmethod
    def turn_metadata(task_id: str, metrics: dict, planning_epoch: str,
                      planning_fingerprint: str, planning_stale: bool, prompt: dict,
                      revised: bool = False, generation_profile: dict | None = None) -> dict:
        return {
            "task_id": task_id, "metrics": metrics, "prompt": prompt,
            "planning_epoch": planning_epoch, "planning_fingerprint": planning_fingerprint,
            "planning_stale": planning_stale, "revised": revised,
            "generation_profile": generation_profile or {},
            "pipeline": {"schema_version": 1, "stages": list(PIPELINE_STAGES), "checkpoint": "draft_ready"},
        }

    def generate(
        self, chapter: int, target_words: int, *, continuation: bool = False,
        scene_mode: bool = False, task_id: str = "", auto_revision: bool = True,
        on_event: Callable[[str, str, int, str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> dict:
        emit = on_event or (lambda _stage, _message, _progress, _level="info": None)
        emit("context", "准备章节上下文", 2, "info")
        brief_context = self.context.build_context(profile="brief")
        emit("brief", "生成或读取章前提要", 4, "info")
        brief = self.ensure_brief(self.nm, self.llm, brief_context, chapter)
        self.validate_brief_artifact(brief)
        fingerprint = self.fingerprint(self.nm, chapter, brief, target_words, continuation)

        plan, plan_source = self.resolve_plan(chapter, fingerprint)
        if plan is None:
            self._guard(should_stop)
            emit("planning", "规划本章剧情节拍", 7, "info")
            planning_context = self.context.build_context(profile="planning") + "\n\n" + render_chapter_brief(brief)
            system, prompt = chapter_plan_prompts(self.nm.name, planning_context, continuation, target_words)
            raw = self.llm.chat(
                system, prompt, self.model_config.get("analysis_max_tokens", 1536), task_type="planning",
            )
            plan = self.accept_generated_plan(
                chapter, brief, target_words, continuation, fingerprint, parse_object(raw),
            )
            plan_source = "generated"
        else:
            self.validate_plan_artifact(plan, brief)
            label = "人工确认场景细纲" if plan_source == "confirmed" else "已验证章节规划缓存"
            emit("planning", f"采用{label}", 7, "info")

        writing_context = (
            self.context.build_context(profile="prose")
            + "\n\n" + render_chapter_brief(brief)
            + "\n\n" + render_chapter_plan(plan)
        )
        planning_epoch = self.context.last_build_stats.get("planning_epoch", "")
        draft_metadata = {
            "task_id": task_id, "planning_fingerprint": fingerprint,
            "target_words": target_words, "scene_mode": scene_mode,
        }
        content = self.working.load(chapter, draft_metadata) if task_id else None
        generation_metrics = []
        if content is not None:
            emit("writing", "恢复上次已生成工作草稿", 55, "info")
        elif scene_mode and len(plan.get("scenes", [])) >= 2:
            scenes = plan["scenes"]
            emit("writing", f"启用逐场景长章模式（{len(scenes)}个场景）", 12, "info")
            scene_checkpoint = {
                "task_id": task_id, "planning_fingerprint": fingerprint,
                "scene_count": len(scenes), "target_words": target_words,
            }
            parts = self.scene_checkpoints.load(chapter, scene_checkpoint) if task_id else []
            if parts:
                emit("writing", f"恢复场景断点，已完成{len(parts)}/{len(scenes)}个场景", 12, "info")
            for index, scene in enumerate(scenes[len(parts):], len(parts) + 1):
                self._guard(should_stop)
                system, prompt = scene_write_prompts(
                    self.nm.name, writing_context, scene, parts[-1] if parts else "",
                )
                text = self.llm.chat(
                    system, prompt,
                    min(int(scene.get("word_budget", 800) / 1.8) + 700, 5000),
                    task_type="prose",
                )
                parts.append(text.strip())
                if task_id:
                    self.scene_checkpoints.save(chapter, scene_checkpoint, parts)
                generation_metrics.append(dict(self.llm.last_metrics))
                emit("writing", f"场景{index}/{len(scenes)}完成", 12 + int(index / len(scenes) * 40), "info")
            content = "\n\n".join(parts)
        else:
            self._guard(should_stop)
            emit("writing", "生成章节正文", 12, "info")
            system, prompt = chapter_prompts(self.nm.name, writing_context, target_words, continuation)
            content = self.llm.chat(
                system, prompt,
                min(int(target_words / 1.8) + 1000, self.model_config["max_output_tokens"]),
                task_type="prose",
            )
            generation_metrics.append(dict(self.llm.last_metrics))
        if task_id:
            self.working.save(chapter, content, draft_metadata)

        self._guard(should_stop)

        content, completion_metrics, completion_passes = self._complete(
            content, target_words, render_chapter_plan(plan), emit, should_stop,
        )
        generation_metrics.extend(completion_metrics)
        if task_id:
            self.working.save(chapter, content, draft_metadata)

        gate = chapter_quality_gate(content, target_words)
        repair_warnings = [warning for warning in gate["warnings"] if "短于目标90%" not in warning]
        revised = False
        if auto_revision and repair_warnings:
            self._guard(should_stop)
            emit("revision", "质量检查发现问题，执行一次定向修订", 60, "warning")
            system, prompt = revision_prompts(self.nm.name, content, repair_warnings, target_words)
            content = self.llm.chat(
                system, prompt,
                min(int(target_words / 1.8) + 1000, self.model_config["max_output_tokens"]),
                task_type="revision",
            )
            generation_metrics.append(dict(self.llm.last_metrics))
            gate = chapter_quality_gate(content, target_words)
            revised = True
            if task_id:
                self.working.save(chapter, content, draft_metadata)

        planning_stale = self.is_planning_stale(
            chapter, brief, target_words, continuation, fingerprint,
        )
        emit("quality", f"质量闸门：{gate['status']} · {gate['word_count']}/{target_words}字", 70,
             "warning" if gate["status"] != "PASS" else "info")
        generation_profile = {
            key: self.model_config.get(key) for key in (
                "model_name", "context_window", "max_output_tokens", "reasoning_effort",
            ) if self.model_config.get(key) is not None
        }
        metrics = aggregate_generation_metrics(generation_metrics)
        if metrics.get("seed") is not None:
            generation_profile["seed"] = metrics["seed"]
        return {
            "chapter": chapter, "brief": brief, "plan": plan, "plan_source": plan_source,
            "content": content, "gate": gate, "metrics": metrics,
            "completion_passes": completion_passes, "revised": revised,
            "planning_epoch": planning_epoch, "planning_fingerprint": fingerprint,
            "planning_stale": planning_stale, "draft_metadata": draft_metadata,
            "generation_profile": generation_profile,
        }

    def clear_working_draft(self, chapter: int):
        self.working.clear(chapter)
        self.scene_checkpoints.clear(chapter)

    def _complete(self, content: str, target_words: int, plan_context: str,
                  emit: Callable, should_stop: Callable[[], bool] | None) -> tuple[str, list[dict], int]:
        metrics = []
        passes = 0
        while len(re.sub(r"\s", "", content)) < int(target_words * 0.9) and passes < 2:
            self._guard(should_stop)
            passes += 1
            current_words = len(re.sub(r"\s", "", content))
            remaining = max(300, target_words - current_words)
            emit("completion", f"正文仅{current_words}字，第{passes}次补写约{remaining}字", 55 + passes * 4, "info")
            system, prompt = chapter_completion_prompts(self.nm.name, content, target_words, plan_context)
            addition = self.llm.chat(
                system, prompt,
                min(int(remaining / 1.8) + 900, self.model_config["max_output_tokens"]),
                task_type="prose",
            )
            metrics.append(dict(self.llm.last_metrics))
            merged = merge_chapter_continuation(content, addition)
            if len(merged) <= len(content) + 50:
                break
            content = merged
        return content, metrics, passes

    @staticmethod
    def _guard(should_stop: Callable[[], bool] | None):
        if should_stop and should_stop():
            raise GenerationInterrupted("章节生成已在安全检查点停止")
