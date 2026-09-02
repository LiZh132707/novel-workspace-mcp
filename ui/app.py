"""novel-workspace-mcp 统一 Web UI — 单页 + 全自动工作流"""
# ruff: noqa: E402
import asyncio
import hashlib
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from filelock import FileLock
import uvicorn

import config
from llm_client import LMStudioClient
from config import ensure_dirs, setup_logging, MODEL_CONFIG, estimate_tokens
from storage_utils import StorageManager
from core.workspace_manager import WorkspaceManager
from core.novel_manager import NovelManager
from core.chapter_manager import ChapterManager
from core.chapter_turn_engine import ChapterTurnEngine
from core.project_health_manager import ProjectHealthManager
from core.context_manager import ContextManager
from core.character_manager import CharacterManager, ABILITY_TIERS
from core.timeline_manager import TimelineManager
from core.consistency_manager import ConsistencyManager
from core.writing_analyzer import WritingAnalyzer
from core.ai_contracts import (
    chapter_completion_prompts, chapter_plan_prompts, chapter_prompts, chapter_quality_gate,
    merge_chapter_continuation, parse_object,
    render_chapter_plan, validate_chapter_plan,
    selection_edit_prompts,
    title_prompts,
    BASE_SYSTEM,
    staged_planning_prompts,
    style_analysis_prompts,
    validate_style_analysis,
    detect_style_reference_leaks,
    validate_planning_stage,
    chapter_brief_prompts,
    render_chapter_brief,
    validate_chapter_brief,
    volume_sections_are_valid,
    volume_sections_prompts,
    normalize_volume_ranges,
    normalize_section_ranges,
    normalize_opening_chapters,
    duplicate_opening_chapters,
    repair_duplicate_opening_chapters,
    opening_character_identity_conflicts,
    repair_opening_character_identity_conflicts,
    repair_opening_protagonist_omissions,
    build_fallback_volumes,
    scene_revision_prompts,
    chapter_source_hash,
    PlanningArtifactError,
    validate_chapter_artifact,
)
from core.fact_manager import FactManager
from core.task_store import TaskStore
from core.task_runner import PersistentTaskRunner
from core.savepoint_manager import SavepointManager
from core.export_manager import ExportManager
from core.data_portability import ProjectZipRestorer, TextNovelImporter, TrashManager
from core.change_review_manager import ChangeReviewManager
from core.foreshadow_manager import ForeshadowManager
from core.settings_manager import SettingsManager
from core.prompt_settings import PromptSettingsManager
from core.quality_tracker import QualityTracker
from core.story_logic_manager import StoryLogicManager
from core.entity_ledger import EntityLedger
from core.backup_manager import BackupScheduler
from core.performance_manager import PerformanceManager
from core.planning_version_manager import PlanningVersionManager
from core.creative_assets import CreativeAssetManager, ASSET_TYPES
from core.ai_action_registry import list_ai_actions, get_ai_action, validate_ai_action_registry
from core.genre_pack_manager import GenrePackManager
from core.long_form_evaluator import LongFormEvaluator
from core.planning_impact_manager import PlanningImpactManager
from core.scene_outline_manager import SceneOutlineManager
from core.state_card_manager import StateCardManager
from core.canonical_state_manager import CanonicalStateManager
from core.canonical_lock_manager import CanonicalLockManager
from core.story_clock_manager import StoryClockManager
from core.review_queue_manager import ReviewQueueManager
from core.prompt_snapshot_manager import PromptSnapshotManager
from core.author_preference_manager import AuthorPreferenceManager
from core.import_rebuilder import ImportRebuilder
from core.history_revision_manager import HistoryRevisionManager
from core.story_sandbox_manager import StorySandboxManager
from core.workflow_engine import list_workflows, workflow_payload, should_pause_for_commit
from core.mutation_transaction import NovelMutationTransaction
from core.planning_application import protect_committed_opening
from core.chapter_generation_service import (
    ChapterGenerationService, StalePlanningError,
    aggregate_generation_metrics as shared_aggregate_generation_metrics,
)
from core.release_readiness_manager import ReleaseReadinessManager
from core.generation_provenance_manager import GenerationProvenanceManager
from core.production_control_manager import ProductionControlManager
from ui.routes.causal import create_router as create_causal_router

ensure_dirs()
logger = setup_logging()
logger.info("UI Server v3.0 starting on port 8765")

storage_mgr = StorageManager(logger)
workspace = WorkspaceManager(logger)
writing_analyzer = WritingAnalyzer(logger)
task_store = TaskStore(config.STORAGE_ROOT / "tasks.db")
task_runner = PersistentTaskRunner(task_store, logger)
trash_manager = TrashManager(config.STORAGE_ROOT)
settings_manager = SettingsManager(config.STORAGE_ROOT / "settings.json", logger)
prompt_settings_manager = PromptSettingsManager(config.STORAGE_ROOT, logger)
backup_scheduler = BackupScheduler(config.NOVELS_ROOT, config.STORAGE_ROOT, logger)
performance_manager = PerformanceManager(config.STORAGE_ROOT / "performance.json", logger)
RESUMABLE_TASK_KINDS = {"batch_chapters", "workflow", "import_rebuild", "history_revision"}


def _aggregate_generation_metrics(items: list[dict]) -> dict:
    return shared_aggregate_generation_metrics(items)


def _governance_preview_warnings(preview: dict) -> list[str]:
    warnings = []
    if preview.get("analysis_degraded"):
        warnings.append("结构化摘要失败：人物、事实和时空检查不完整")
    for item in preview.get("canonical_lock_conflicts", []) if isinstance(preview.get("canonical_lock_conflicts"), list) else []:
        warnings.append("权威设定锁冲突：" + str(item.get("message", "")))
    for item in preview.get("state_change_conflicts", []) if isinstance(preview.get("state_change_conflicts"), list) else []:
        warnings.append("高风险状态变化：" + str(item.get("message", "")))
    for key, label in (("story_clock_issues", "故事时空冲突"), ("character_decision_issues", "人物决策冲突")):
        for item in preview.get(key, []) if isinstance(preview.get(key), list) else []:
            if item.get("blocking"):
                warnings.append(label + "：" + str(item.get("message", "")))
    return warnings


def _parse_personality_profile(value: str | None) -> dict:
    if not str(value or "").strip():
        return {}
    parsed = json.loads(str(value))
    if not isinstance(parsed, dict):
        raise ValueError("人格指纹必须是 JSON 对象")
    return parsed


_STREAM_END = object()


def _next_stream_item(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return _STREAM_END


async def _iterate_blocking_stream(iterator):
    try:
        while True:
            item = await asyncio.to_thread(_next_stream_item, iterator)
            if item is _STREAM_END:
                return
            yield item
    finally:
        close = getattr(iterator, "close", None)
        if close:
            try:
                await asyncio.to_thread(close)
            except Exception as exc:
                logger.warning("关闭模型流失败: %s", exc)


def _complete_short_chapter(llm, name: str, content: str, target_words: int, plan_context: str,
                            on_pass=None, max_passes: int = 2) -> tuple[str, list[dict], int]:
    metrics = []
    passes = 0
    while len(re.sub(r"\s", "", content)) < int(target_words * 0.9) and passes < max_passes:
        passes += 1
        current_words = len(re.sub(r"\s", "", content))
        remaining = max(300, target_words - current_words)
        if on_pass:
            on_pass(passes, current_words, remaining)
        system, prompt = chapter_completion_prompts(name, content, target_words, plan_context)
        addition = llm.chat(
            system, prompt,
            max_tokens=min(int(remaining / 1.8) + 900, MODEL_CONFIG["max_output_tokens"]),
            task_type="prose",
        )
        metrics.append(dict(llm.last_metrics))
        merged = merge_chapter_continuation(content, addition)
        if len(merged) <= len(content) + 50:
            break
        content = merged
    return content, metrics, passes

GPU_PROTECTED_PROCESSES = {
    "system", "registry", "dwm", "csrss", "winlogon", "explorer", "sihost",
    "fontdrvhost", "audiodg", "radeonsoftware", "amdrssrcext", "atiesrxx",
    "atieclxx", "lm studio", "llama-server", "lms", "python", "pythonw",
    "chatgpt", "codex",
}


def _gpu_processes() -> list[dict]:
    script = r"""
$samples=(Get-Counter '\GPU Process Memory(*)\Dedicated Usage' -ErrorAction SilentlyContinue).CounterSamples
$usage=@{}
foreach($sample in $samples){if($sample.CookedValue -gt 1048576 -and $sample.InstanceName -match 'pid_(\d+)_'){$pidValue=[int]$Matches[1];$usage[$pidValue]=($usage[$pidValue]+$sample.CookedValue)}}
$rows=@()
foreach($pidValue in $usage.Keys){$process=Get-Process -Id $pidValue -ErrorAction SilentlyContinue;if($process){$rows += [pscustomobject]@{pid=$pidValue;name=$process.ProcessName;vram_mb=[math]::Round($usage[$pidValue]/1MB,1)}}}
$rows | Sort-Object vram_mb -Descending | ConvertTo-Json -Compress
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script], capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    data = json.loads(result.stdout)
    rows = data if isinstance(data, list) else [data]
    for item in rows:
        item["protected"] = item.get("name", "").lower() in GPU_PROTECTED_PROCESSES or int(item.get("pid", 0)) == os.getpid()
    return rows

_llm = None
_vs = None
_llm_init_lock = threading.Lock()
_vs_init_lock = threading.Lock()


def get_llm():
    global _llm
    if _llm is not None:
        return _llm
    client = None
    with _llm_init_lock:
        if _llm is not None:
            return _llm
        try:
            from llm_client import LMStudioClient
            client = LMStudioClient()
            logger.info("正在连接模型服务 [%s]: %s ...", client.provider, client.model_key)
            if client.start(wait_ready=True, max_wait=180):
                _llm = client
                if _vs is not None:
                    _vs.embed_func = client.embed
                    _vs._semantic_disabled = False
                logger.info("模型服务连接完成 [%s]", client.provider)
                if settings_manager.get().get("auto_warmup", True):
                    client.warmup()
                    performance_manager.record(client.last_metrics, "warmup")
            else:
                logger.warning("模型服务尚未就绪；请检查后端配置和模型是否可用")
                client.close()
        except Exception as ex:
            if client is not None:
                client.close()
            logger.warning("模型服务连接失败: %s", ex)
    return _llm


def _unload_model_locked():
    global _llm
    with _llm_init_lock:
        client = _llm or LMStudioClient()
        if client and getattr(client, "generation_busy", False):
            raise RuntimeError("模型正在生成，请先停止当前任务")
        if client:
            client.unload_all()
        _llm = None


def _reload_model_locked():
    global _llm
    with _llm_init_lock:
        client = _llm or LMStudioClient()
        if getattr(client, "generation_busy", False):
            raise RuntimeError("模型正在生成，不能重载")
        try:
            if not client.reload():
                raise RuntimeError("模型重载后未能就绪")
            _llm = client
            if settings_manager.get().get("auto_warmup", True):
                client.warmup()
                performance_manager.record(client.last_metrics, "warmup")
            return client
        except Exception:
            _llm = None
            raise


def _warmup_model_locked():
    with _llm_init_lock:
        client = _llm
        if not client:
            raise RuntimeError("模型未连接")
        metrics = client.warmup()
        performance_manager.record(metrics, "warmup")
        return metrics


def _benchmark_model_locked() -> list[dict]:
    with _llm_init_lock:
        client = _llm
        if not client:
            raise RuntimeError("模型未连接")
        client.warmup()
        prompt = "直接写约600字现代都市悬疑小说正文。不要标题、解释或提纲，结尾自然收束。"
        runs = []
        for index in range(3):
            client.chat(
                "你是中文小说性能测试器，只输出正文。", prompt,
                700, 0.8, 0.9, 30, 1.1, 0.0, 0.0, "none", 123456,
            )
            run = {"run": index + 1, **client.last_metrics}
            runs.append(run)
            performance_manager.record(run, "benchmark")
        return runs


def _model_status_locked() -> dict:
    with _llm_init_lock:
        client = _llm or LMStudioClient()
        loaded_models = client.loaded_models() if client else []
        if _llm is None and loaded_models:
            if client.provider == "local":
                client._sync_context_from_lms()
                running_port = client._discover_server_port()
                if running_port is not None:
                    client._set_server_port(running_port)
        return {
            "loaded": bool(client and client.is_available()),
            "busy": bool(client and client.generation_busy),
            "provider": getattr(client, "provider", "local"),
            "model": getattr(client, "model_key", MODEL_CONFIG.get("model_name", "")),
            "loaded_models": loaded_models,
            "last_metrics": client.last_metrics if client else {},
        }


def get_vs():
    global _vs
    if _vs is not None:
        return _vs
    with _vs_init_lock:
        if _vs is not None:
            return _vs
        try:
            from vector_store import VectorStore
            def unavailable_embedding(_text):
                raise RuntimeError("当前没有可用嵌入接口")
            _vs = VectorStore(logger, _llm.embed if _llm is not None else unavailable_embedding)
            logger.info("Vector store initialized")
        except Exception as ex:
            logger.debug("VS init skipped: %s", ex)
    return _vs


def _index_chapter(nm: NovelManager, chapter: int, content: str):
    try:
        store = get_vs()
        if store:
            store.add_document(nm.name, chapter, content)
    except Exception as exc:
        logger.warning("第%d章全文向量索引失败（不影响正文保存）: %s", chapter, exc)


def _delete_chapter_index(nm: NovelManager, chapter: int):
    try:
        store = get_vs()
        if store:
            store.delete_document(nm.name, chapter)
    except Exception as exc:
        logger.warning("第%d章旧索引删除失败: %s", chapter, exc)


def get_novel_manager(name: str) -> NovelManager:
    novel_path = config.NOVELS_ROOT / name
    if not novel_path.exists():
        raise HTTPException(404, f"小说目录 {name} 不存在")
    return NovelManager(name, novel_path, logger, storage_mgr)


def get_chapter_manager(nm: NovelManager) -> ChapterManager:
    # 浏览页面和读取章节时不应触发耗时的模型加载；生成接口会按需加载。
    return ChapterManager(nm, logger, _llm)


def get_turn_engine(nm: NovelManager, chapter_manager: ChapterManager | None = None) -> ChapterTurnEngine:
    return ChapterTurnEngine(nm, logger, chapter_manager or get_chapter_manager(nm), storage_mgr)


def get_context_manager(nm: NovelManager) -> ContextManager:
    return ContextManager(nm, logger, get_vs(), _llm)


def get_character_manager(nm: NovelManager) -> CharacterManager:
    return CharacterManager(nm.path, logger)


def get_timeline_manager(nm: NovelManager) -> TimelineManager:
    return TimelineManager(nm.path, logger)


def _ensure_chapter_brief(nm: NovelManager, llm, context: str, chapter: int) -> dict:
    briefs_file = nm.path / "outline" / "chapter_briefs.json"
    briefs = storage_mgr.safe_read_json(briefs_file, {})
    briefs = briefs if isinstance(briefs, dict) else {}
    baseline_brief = briefs.get(str(chapter))
    canonical_characters = get_character_manager(nm).canonical_roster()

    def enforce_authority(brief: dict) -> dict:
        return validate_chapter_artifact(
            brief, canonical_characters, label=f"第{chapter}章提要",
            require_protagonist=brief.get("chapter_mode") in {"main_progress", "setup", "complication"},
            chapter=chapter,
        )

    if isinstance(baseline_brief, dict):
        return enforce_authority(baseline_brief)
    recent_briefs = []
    for number in sorted((int(key) for key in briefs if str(key).isdigit()), reverse=True):
        if number < chapter:
            item = briefs[str(number)]
            if not isinstance(item, dict):
                continue
            recent_briefs.append({
                "chapter": number, "title": item.get("title", ""), "chapter_mode": item.get("chapter_mode", ""),
                "synopsis": item.get("synopsis", ""), "exit_state": item.get("exit_state", ""),
            })
        if len(recent_briefs) >= 4:
            break
    if recent_briefs:
        context += "\n\n【最近章前提要与模式，避免重复】\n" + json.dumps(list(reversed(recent_briefs)), ensure_ascii=False)
    opening = storage_mgr.safe_read_json(nm.path / "outline" / "opening_chapters.json", {})
    opening = opening if isinstance(opening, dict) else {}
    for item in opening.get("chapters", []) if isinstance(opening.get("chapters"), list) else []:
        if not isinstance(item, dict) or _bounded_int(item.get("chapter", 0), 0, 0, 1_000_000) != chapter:
            continue
        brief = {
            "chapter": chapter, "title": item.get("title", ""),
            "synopsis": item.get("synopsis") or item.get("goal", ""),
            "chapter_mode": item.get("chapter_mode", "main_progress"),
            "structural_purpose": item.get("goal", ""), "entry_state": item.get("opening", ""),
            "side_value": item.get("side_value", ""), "exit_state": item.get("ending_hook", ""), "must_happen": item.get("beats", []),
            "must_not_happen": [], "characters": item.get("characters", []), "foreshadowing": [],
        }
        allowed_modes = {"main_progress", "complication", "character", "subplot", "exploration", "aftermath", "breathing", "setup"}
        if brief["chapter_mode"] not in allowed_modes:
            brief["chapter_mode"] = "main_progress"
        if len(str(brief["synopsis"]).strip()) < 30:
            brief["synopsis"] = (str(brief["synopsis"]).strip() + "；承接当前卷纲与节纲，通过具体行动、阻力和选择形成新的局势，并留下下一章必须处理的后果。").strip("；")
        if brief["chapter_mode"] in {"character", "subplot", "exploration", "aftermath", "breathing", "setup"} and not brief["side_value"]:
            brief["side_value"] = "推进人物、关系、世界理解或后续条件，避免无功能停滞"
        brief = validate_chapter_brief(brief, chapter)
        try:
            brief = enforce_authority(brief)
            return _store_generated_brief(nm, chapter, brief, baseline_brief)
        except PlanningArtifactError as conflict:
            context += "\n\n【已确认开篇细纲冲突，必须重新规划本章】\n" + str(conflict)
    system, prompt = chapter_brief_prompts(nm.name, chapter, context)
    for attempt in range(2):
        raw = llm.chat(system, prompt, MODEL_CONFIG.get("analysis_max_tokens", 1536), task_type="planning")
        brief = validate_chapter_brief(parse_object(raw), chapter)
        try:
            brief = enforce_authority(brief)
            return _store_generated_brief(nm, chapter, brief, baseline_brief)
        except PlanningArtifactError as conflict:
            if attempt:
                raise
            prompt += (
                "\n\n上一次章前提要违反权威上下文：" + str(conflict)
                + "\n请完整重做，严格使用以下人物名册，不得串用身份：\n"
                + json.dumps(canonical_characters, ensure_ascii=False)
            )
    raise PlanningArtifactError(f"第{chapter}章提要未通过权威校验")


def _store_generated_brief(nm: NovelManager, chapter: int, generated: dict,
                           baseline: dict | None) -> dict:
    briefs_file = nm.path / "outline" / "chapter_briefs.json"
    with FileLock(str(briefs_file) + ".transaction.lock", timeout=30):
        latest = storage_mgr.safe_read_json(briefs_file, {})
        latest = latest if isinstance(latest, dict) else {}
        current = latest.get(str(chapter))
        if isinstance(current, dict) and current != baseline:
            selected = current
        else:
            latest[str(chapter)] = generated
            storage_mgr.atomic_write_json(briefs_file, latest)
            selected = generated
    titles_file = nm.path / "outline" / "chapter_titles.json"
    with FileLock(str(titles_file) + ".transaction.lock", timeout=30):
        titles = storage_mgr.safe_read_json(titles_file, {})
        titles = titles if isinstance(titles, dict) else {}
        titles[str(chapter)] = str(selected.get("title", ""))
        storage_mgr.atomic_write_json(titles_file, titles)
    return selected


def _chapter_plan_fingerprint(nm: NovelManager, chapter: int, brief: dict,
                              target_words: int, continuation: bool) -> str:
    source_digest = hashlib.sha256()
    for relative in (
        "bible/world.md", "bible/rules.md", "bible/style.md",
        "bible/author_preferences.json", "bible/genre_pack.json", "outline/main.md",
        "outline/volumes.json", "outline/narrative_policy.json",
        "summaries/long_term.json",
        "facts.json", "foreshadowing.json", "planning/creative_assets.json",
        "tracking/story_logic.json", "tracking/state_cards.json", "tracking/entities.json",
        "reviews/planning_reviews.json",
    ):
        path = nm.path / relative
        if path.exists():
            source_digest.update(relative.encode("utf-8"))
            source_digest.update(path.read_bytes())
    scene_outlines = storage_mgr.safe_read_json(nm.path / "outline" / "scene_outlines.json", {})
    confirmed_scenes = {
        str(key): value for key, value in scene_outlines.items()
        if isinstance(value, dict) and value.get("status") == "confirmed"
    } if isinstance(scene_outlines, dict) else {}
    source_digest.update(b"outline/scene_outlines.confirmed")
    source_digest.update(json.dumps(confirmed_scenes, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    for path in sorted((nm.path / "characters").glob("*.json")):
        source_digest.update(path.name.encode("utf-8"))
        source_digest.update(path.read_bytes())
    for directory, limit in (("summaries", 10), ("timeline", 20)):
        paths = sorted((nm.path / directory).glob("*.json"))[-limit:]
        for path in paths:
            source_digest.update(f"{directory}/{path.name}".encode("utf-8"))
            source_digest.update(path.read_bytes())
    canonical = storage_mgr.safe_read_json(nm.path / "tracking" / "canonical_versions.json", {"versions": []})
    canonical = canonical if isinstance(canonical, dict) else {}
    versions = canonical.get("versions", []) if isinstance(canonical.get("versions"), list) else []
    latest_canonical = versions[-1] if versions and isinstance(versions[-1], dict) else {}
    state = nm.get_state()
    payload = {
        "chapter": chapter,
        "brief": brief,
        "target_words": target_words,
        "continuation": continuation,
        "next_goal": state.get("next_goal", ""),
        "last_summary": state.get("last_summary", ""),
        "planning_sources": source_digest.hexdigest(),
        "canonical_state": latest_canonical.get("checksum", ""),
        "prompt_settings": prompt_settings_manager.get(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_cached_chapter_plan(nm: NovelManager, chapter: int, fingerprint: str) -> Optional[dict]:
    path = nm.path / "outline" / "chapter_plans.json"
    with FileLock(str(path) + ".transaction.lock", timeout=30):
        all_cached = storage_mgr.safe_read_json(path, {})
        all_cached = all_cached if isinstance(all_cached, dict) else {}
        cached = all_cached.get(str(chapter), {})
    cached = cached if isinstance(cached, dict) else {}
    if cached.get("fingerprint") != fingerprint:
        return None
    try:
        return validate_chapter_plan(cached.get("plan", {}))
    except Exception:
        return None


def _save_cached_chapter_plan(nm: NovelManager, chapter: int, fingerprint: str, plan: dict):
    path = nm.path / "outline" / "chapter_plans.json"
    with FileLock(str(path) + ".transaction.lock", timeout=30):
        cached = storage_mgr.safe_read_json(path, {})
        cached = cached if isinstance(cached, dict) else {}
        cached[str(chapter)] = {"fingerprint": fingerprint, "plan": plan}
        storage_mgr.atomic_write_json(path, cached)
    SceneOutlineManager(nm.path, logger, storage_mgr).seed_from_plan(chapter, plan)


def _confirmed_chapter_plan(nm: NovelManager, chapter: int) -> dict | None:
    plan = SceneOutlineManager(nm.path, logger, storage_mgr).confirmed_plan(chapter)
    return validate_chapter_plan(plan) if plan else None


def _positive_ints(values) -> list[int]:
    result = []
    for value in values if isinstance(values, list) else []:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in result:
            result.append(number)
    return result


def _bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _review_approved(payload: dict, kind: str, chapter: int, content_hash: str,
                     planning_fingerprint: str = "") -> bool:
    for item in payload.get("approved_reviews", []) if isinstance(payload.get("approved_reviews"), list) else []:
        if not isinstance(item, dict):
            continue
        try:
            approved_chapter = int(item.get("chapter", 0))
        except (TypeError, ValueError):
            continue
        if (
            item.get("kind") == kind and approved_chapter == chapter
            and item.get("content_hash") == content_hash
            and str(item.get("planning_fingerprint", "")) == planning_fingerprint
        ):
            return True
    return False


def _stored_planning_checkpoint(chapter_manager: ChapterManager, chapter: int) -> tuple[str, dict | None]:
    report = chapter_manager.planning_reviews.report()
    for kind, key in (("volume_review", "volume_reviews"), ("section_review", "section_reviews")):
        for item in report.get(key, []) if isinstance(report.get(key), list) else []:
            if not isinstance(item, dict):
                continue
            try:
                end_chapter = int(item.get("end_chapter", 0))
            except (TypeError, ValueError):
                continue
            if end_chapter == chapter:
                return kind, item
    return "", None


def _finish_batch_post_commit(task: dict, nm: NovelManager, chapter_manager: ChapterManager,
                              chapter: int, content: str, target_words: int,
                              stop_on_warning: bool, progress_at):
    latest = task_store.get(task["id"]) or task
    if latest.get("status") in {"cancelled", "paused"}:
        raise RuntimeError(f"任务已{latest.get('status')}，停止提交后检查")
    payload = latest.get("input", {}) if isinstance(latest.get("input"), dict) else {}
    content_hash = chapter_source_hash(content)
    task_store.event(task["id"], f"第{chapter}章：检查跨章节一致性", progress_at(88), stage="consistency")
    consistency_issues = ConsistencyManager(nm, logger).check_all()
    final_gate = chapter_quality_gate(content, target_words, consistency_issues)
    severe = [issue for issue in consistency_issues if issue.get("severity") == "高"]
    if severe:
        review_fingerprint = hashlib.sha256(json.dumps(
            severe, ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")).hexdigest()
        tracker = QualityTracker(nm.path, logger, storage_mgr)
        for issue in severe:
            tracker.add_debt(chapter, "consistency", "高", issue.get("message", str(issue)), "人工确认设定或恢复章节版本")
        approved = _review_approved(payload, "consistency", chapter, content_hash, review_fingerprint)
        if (stop_on_warning or final_gate["status"] == "FAIL") and not approved:
            task_store.patch_input(task["id"], {"waiting_review": {
                "kind": "consistency", "chapter": chapter, "content_hash": content_hash,
                "planning_fingerprint": review_fingerprint,
            }})
            task_store.pause(task["id"])
            raise RuntimeError(f"第{chapter}章保存后发现{len(severe)}个严重一致性问题，批次已暂停")
    review_kind, checkpoint = _stored_planning_checkpoint(chapter_manager, chapter)
    checkpoint_fingerprint = hashlib.sha256(json.dumps(
        checkpoint or {}, ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    if checkpoint and stop_on_warning and not _review_approved(
        payload, review_kind, chapter, content_hash, checkpoint_fingerprint,
    ):
        if review_kind == "volume_review":
            message = f"已到达“{checkpoint.get('volume', '')}”卷末，验收状态 {checkpoint.get('status', '')}，修复任务 {len(checkpoint.get('repair_tasks', []))} 项"
        else:
            message = f"已到达节纲“{checkpoint.get('section', '')}”末尾，等待节末复盘确认"
        task_store.event(task["id"], message, progress_at(90), stage=review_kind)
        task_store.patch_input(task["id"], {"waiting_review": {
            "kind": review_kind, "chapter": chapter, "content_hash": content_hash,
            "planning_fingerprint": checkpoint_fingerprint,
        }})
        task_store.pause(task["id"])
        raise RuntimeError("批次已在规划验收节点暂停，请检查复盘与修复任务后继续")


def _run_batch_task(task: dict) -> dict:
    """后台连续生成；每章的模型调用严格串行。"""
    payload = task.get("input", {})
    payload = payload if isinstance(payload, dict) else {}
    name = task["novel"]
    count = _bounded_int(payload.get("count", 3), 3, 1, 10)
    target_words = _bounded_int(payload.get("target_words", 5000), 5000, 500, 20000)
    stop_on_warning = bool(payload.get("stop_on_warning", True))
    scene_mode = bool(payload.get("scene_mode", False))
    commit_mode = str(payload.get("commit_mode") or settings_manager.get().get("chapter_commit_mode", "balanced"))
    if commit_mode not in {"review", "balanced", "automatic"}:
        commit_mode = "balanced"
    nm = get_novel_manager(name)
    llm = get_llm()
    if not llm:
        raise RuntimeError("模型服务未连接")
    context_manager = ContextManager(nm, logger, None, llm)
    chapter_manager = ChapterManager(nm, logger, llm)
    turn_engine = get_turn_engine(nm, chapter_manager)
    generation_service = ChapterGenerationService(
        nm, llm, context_manager, storage_mgr, MODEL_CONFIG,
        _ensure_chapter_brief, _chapter_plan_fingerprint,
        _load_cached_chapter_plan, _save_cached_chapter_plan, _confirmed_chapter_plan,
    )
    start_chapter = _bounded_int(
        payload.get("start_chapter") or nm.get_current_chapter() + 1,
        nm.get_current_chapter() + 1, 1, 1_000_000,
    )
    desired_chapters = list(range(start_chapter, start_chapter + count))
    completed = _positive_ints(payload.get("completed_chapters", []))
    post_commit_checked = _positive_ints(payload.get("post_commit_checked_chapters", []))
    completed = [chapter for chapter in completed if chapter in desired_chapters]
    post_commit_checked = [chapter for chapter in post_commit_checked if chapter in desired_chapters]
    for chapter in desired_chapters:
        chapter_index = chapter - start_chapter
        progress_at = lambda stage, chapter_index=chapter_index: min(
            100, int((chapter_index + max(0, min(100, stage)) / 100) / count * 100),
        )
        if chapter in completed and chapter in post_commit_checked:
            continue
        latest = task_store.get(task["id"])
        if latest and latest["status"] in {"cancelled", "paused"}:
            return {"completed_chapters": completed, "cancelled": True}
        chapter_file = nm.path / "chapters" / config.CHAPTER_FILE_PATTERN.format(chapter)
        if chapter_file.exists():
            existing_content = chapter_file.read_text("utf-8", errors="replace")
            if chapter_manager.commits.is_committed(chapter, existing_content):
                if chapter not in completed:
                    completed.append(chapter)
                    task_store.patch_input(task["id"], {"completed_chapters": completed})
                task_store.event(task["id"], f"第{chapter}章已有完整提交记录，正在补跑提交后检查", int(len(completed) / count * 100), stage="recovered")
                _finish_batch_post_commit(task, nm, chapter_manager, chapter, existing_content, target_words, stop_on_warning, progress_at)
                if chapter not in post_commit_checked:
                    post_commit_checked.append(chapter)
                task_store.patch_input(task["id"], {"post_commit_checked_chapters": post_commit_checked})
                generation_service.clear_working_draft(chapter)
                continue
            task_store.event(task["id"], f"第{chapter}章正文存在但章后状态未完成，正在补齐提交", int(len(completed) / count * 100), stage="recover_finalize")
            recovery_turn = turn_engine.save_draft(
                chapter, existing_content, target_words, "batch_recovery", {"task_id": task["id"]}, False,
            )
            turn_engine.commit(
                recovery_turn["id"], lambda number, text: _index_chapter(nm, number, text), True, True,
            )
            completed.append(chapter)
            _finish_batch_post_commit(task, nm, chapter_manager, chapter, existing_content, target_words, stop_on_warning, progress_at)
            post_commit_checked.append(chapter)
            task_store.patch_input(task["id"], {
                "completed_chapters": completed, "post_commit_checked_chapters": post_commit_checked,
            })
            generation_service.clear_working_draft(chapter)
            continue
        index = chapter_index
        generated = generation_service.generate(
            chapter, target_words, scene_mode=scene_mode, task_id=task["id"], auto_revision=True,
            on_event=lambda stage, message, progress, level, chapter=chapter, progress_at=progress_at: task_store.event(
                task["id"], f"第{chapter}章：{message}", progress_at(progress),
                level=level, stage=stage,
            ),
            should_stop=lambda: (task_store.get(task["id"]) or {}).get("status") in {"paused", "cancelled"},
        )
        content = generated["content"]
        gate = generated["gate"]
        warnings = list(gate["warnings"])
        fingerprint = generated["planning_fingerprint"]
        aggregate_metrics = generated["metrics"]
        completion_passes = generated["completion_passes"]
        if generated["planning_stale"]:
            warnings.append("生成期间上游规划发生变化，必须重新生成或人工确认沿用旧草稿")
            gate["warnings"] = list(dict.fromkeys(warnings))
            gate["status"] = "FAIL"
            task_store.event(
                task["id"], f"第{chapter}章生成期间规划发生变化，草稿已保留但禁止自动提交",
                progress_at(66), level="warning", stage="planning_stale",
            )
        task_store.event(task["id"], f"第{chapter}章正文生成完成 · {aggregate_metrics.get('tokens_per_second', 0)} token/s · {aggregate_metrics.get('completion_tokens', 0)} tokens · 自动补写{completion_passes}次", progress_at(65), stage="metrics")
        latest = task_store.get(task["id"])
        if latest and latest["status"] in {"cancelled", "paused"}:
            draft_dir = nm.path / "drafts"
            draft_dir.mkdir(parents=True, exist_ok=True)
            storage_mgr.atomic_write_text(draft_dir / f"{chapter:06d}_cancelled.txt", content)
            return {"completed_chapters": completed, "cancelled": True, "draft_chapter": chapter}
        prompt_type = "revision" if generated["revised"] else "prose"
        turn_metadata = generation_service.turn_metadata(
            task["id"], aggregate_metrics, generated["planning_epoch"], fingerprint,
            generated["planning_stale"],
            PromptSnapshotManager(config.STORAGE_ROOT).latest_reference(prompt_type),
            generated["revised"], generated["generation_profile"],
        )
        turn = turn_engine.save_draft(
            chapter, content, target_words, "batch",
            turn_metadata,
        )
        preview = turn_engine.preview_changes(turn["id"])
        logic_conflicts = preview.get("fact_conflicts", [])
        if logic_conflicts:
            warnings.extend("硬事实冲突：" + item["message"] for item in logic_conflicts)
            gate["warnings"] = list(dict.fromkeys(warnings))
            gate["status"] = "FAIL"
            task_store.event(
                task["id"], f"第{chapter}章提交前发现{len(logic_conflicts)}个硬事实变化，等待人工确认",
                progress_at(69), level="warning", stage="logic_preflight",
            )
        governance_warnings = _governance_preview_warnings(preview)
        if governance_warnings:
            warnings.extend(governance_warnings)
            gate["warnings"] = list(dict.fromkeys(warnings))
            gate["status"] = "FAIL"
            task_store.event(
                task["id"], f"第{chapter}章提交前发现{len(governance_warnings)}个设定、时空或人物决策阻断项，等待人工确认",
                progress_at(69), level="warning", stage="governance_preflight",
            )
        task_store.event(task["id"], f"第{chapter}章质量闸门：{gate['status']} · {gate['word_count']}/{target_words}字", progress_at(70), level="warning" if gate["status"] != "PASS" else "info", stage="quality")
        latest_payload = (task_store.get(task["id"]) or task).get("input", {})
        content_hash = chapter_source_hash(content)
        quality_approved = _review_approved(latest_payload, "quality", chapter, content_hash, fingerprint)
        preflight_approved = _review_approved(latest_payload, "preflight", chapter, content_hash, fingerprint)
        review_approved = quality_approved or preflight_approved
        preflight_needed = bool(logic_conflicts or governance_warnings or generated["planning_stale"])
        if should_pause_for_commit(commit_mode, gate["status"], review_approved):
            draft_dir = nm.path / "drafts"
            draft_dir.mkdir(parents=True, exist_ok=True)
            storage_mgr.atomic_write_text(draft_dir / f"{chapter:06d}.txt", content)
            message = f"第{chapter}章等待提交确认（{commit_mode} / {gate['status']}），草稿已隔离保存：{'；'.join(warnings) or '无质量警告'}"
            task_store.event(task["id"], message, progress_at(70), level="warning", stage="paused")
            task_store.patch_input(task["id"], {"waiting_review": {
                "kind": "preflight" if preflight_needed else "quality",
                "chapter": chapter, "turn_id": turn["id"],
                "content_hash": content_hash, "planning_fingerprint": fingerprint,
            }})
            task_store.pause(task["id"])
            raise RuntimeError("批次因质量检查暂停：" + "；".join(warnings))
        task_store.event(task["id"], f"第{chapter}章：整理摘要、人物变化与事实", progress_at(75), stage="analysis")
        committed = turn_engine.commit(
            turn["id"], lambda number, text: _index_chapter(nm, number, text),
            allow_quality_failure=gate["status"] == "FAIL" and review_approved,
            allow_fact_conflicts=quality_approved or preflight_approved,
            allow_locked_changes=preflight_approved,
            allow_story_clock_conflicts=preflight_approved,
            allow_character_decision_conflicts=preflight_approved,
            allow_degraded_summary=preflight_approved,
            allow_stale_planning=preflight_approved,
        )
        result = committed["result"]
        generation_service.clear_working_draft(chapter)
        if gate["status"] != "PASS":
            tracker = QualityTracker(nm.path, logger, storage_mgr)
            for warning in warnings:
                tracker.add_debt(chapter, "generation_quality", "中", warning, "批量任务已继续，请人工复核")
        next_goal = result.get("summary", {}).get("next_goal", "")
        if next_goal:
            nm.update_next_goal(next_goal)
        _finish_batch_post_commit(task, nm, chapter_manager, chapter, content, target_words, stop_on_warning, progress_at)
        completed.append(chapter)
        post_commit_checked.append(chapter)
        task_store.patch_input(task["id"], {
            "completed_chapters": completed, "post_commit_checked_chapters": post_commit_checked,
        })
        progress = int((index + 1) / count * 100)
        if payload.get("serial_controller"):
            runtime = ProductionControlManager(nm, logger, storage_mgr).record_chapter_result(
                int(result.get("words", 0)), target_words,
                float(aggregate_metrics.get("tokens_per_second", 0) or 0),
            )
            if runtime["state"] == "tripped":
                task_store.event(
                    task["id"], f"自动连载熔断：{runtime['last_stop_reason']}",
                    progress, level="warning", stage="circuit_breaker",
                )
                return {"completed_chapters": completed, "count": len(completed), "circuit_breaker": runtime}
        task_store.event(task["id"], f"第{chapter}章已保存，共{result['words']}字", progress, stage="saved")
    if payload.get("serial_controller"):
        policy = ProductionControlManager(nm, logger, storage_mgr).policy()
        if policy["enabled"] and nm.get_current_chapter() < policy["target_chapter"]:
            _enqueue_serial_batch(
                nm, policy, 0, datetime.now() + timedelta(seconds=policy["cooldown_seconds"]),
                predecessor_task_id=task["id"],
            )
    return {"completed_chapters": completed, "count": len(completed)}


def _enqueue_serial_batch(nm: NovelManager, policy: dict, attempt: int = 0,
                          not_before: datetime | None = None,
                          predecessor_task_id: str | None = None) -> str | None:
    remaining = max(0, int(policy["target_chapter"]) - nm.get_current_chapter())
    if remaining <= 0:
        raise ValueError("已经达到自动连载目标章节")
    count = min(int(policy["batch_size"]), remaining)
    schedule_base = not_before or datetime.now()
    allowed_time = ProductionControlManager(nm, logger, storage_mgr).next_allowed_time(schedule_base)
    scheduled_for = max(
        (value for value in (schedule_base if not_before else None, allowed_time) if value is not None),
        default=None,
    )
    task_id = task_store.create_if_idle(
        nm.name, "batch_chapters", f"自动连载 · 第{nm.get_current_chapter() + 1}章起 · {count}章",
        {
            "count": count, "target_words": int(policy["target_words"]),
            "stop_on_warning": bool(policy["stop_on_warning"]),
            "scene_mode": bool(policy["scene_mode"]), "commit_mode": policy["commit_mode"],
            "start_chapter": nm.get_current_chapter() + 1, "completed_chapters": [],
            "serial_controller": True, "serial_attempt": int(attempt),
        },
        status="queued", not_before=scheduled_for.isoformat() if scheduled_for else "",
        allowed_active_task_id=predecessor_task_id,
    )
    if not task_id:
        return None
    task_store.event(task_id, "自动连载回合已进入单模型串行队列", 0, stage="queued")
    task_runner.notify()
    return task_id


def _run_batch_task_entry(task: dict) -> dict:
    try:
        return _run_batch_task(task)
    except Exception:
        payload = task.get("input", {}) if isinstance(task.get("input"), dict) else {}
        latest = task_store.get(task["id"])
        if payload.get("serial_controller") and latest and latest.get("status") == "running":
            nm = get_novel_manager(task["novel"])
            controller = ProductionControlManager(nm, logger, storage_mgr)
            runtime = controller.record_failure(str(sys.exc_info()[1] or "未知失败"))
            policy = controller.policy()
            attempt = int(payload.get("serial_attempt", 0) or 0)
            if runtime["state"] != "tripped" and policy["enabled"] and attempt < policy["max_retries"]:
                _enqueue_serial_batch(
                    nm, policy, attempt + 1,
                    datetime.now() + timedelta(seconds=policy["cooldown_seconds"]),
                    predecessor_task_id=task["id"],
                )
        raise


def _run_workflow_task(task: dict) -> dict:
    workflow = task.get("input", {}).get("workflow")
    if workflow in {"deep_chapter", "serial_chapters"}:
        task_store.event(task["id"], "工作流已装配，进入章节生成流水线", 2, stage="workflow_prepare")
        result = _run_batch_task(task)
        latest = task_store.get(task["id"])
        if latest and latest["status"] in {"paused", "cancelled"}:
            return result
        task_store.patch_input(task["id"], {"workflow_completed": task.get("input", {}).get("workflow_steps", [])})
        result["evaluation"] = LongFormEvaluator(get_novel_manager(task["novel"]).path, logger, storage_mgr).run()
        return result
    if workflow == "quality_sweep":
        task_store.event(task["id"], "正在检查交接覆盖、规划贴合与动态状态", 35, stage="quality_sweep")
        result = LongFormEvaluator(get_novel_manager(task["novel"]).path, logger, storage_mgr).run()
        task_store.patch_input(task["id"], {"workflow_completed": task.get("input", {}).get("workflow_steps", [])})
        task_store.event(task["id"], f"全书质量基线 {result['score']} 分", 95, stage="report")
        return result
    raise ValueError("未知工作流")


def _run_import_rebuild_task(task: dict) -> dict:
    nm = get_novel_manager(task["novel"])
    llm = get_llm()
    if not llm:
        raise RuntimeError("本地模型未连接，无法重建导入小说")
    def progress(message: str, value: int, stage: str):
        latest = task_store.get(task["id"])
        if latest and latest["status"] in {"paused", "cancelled"}:
            raise RuntimeError(f"导入重建已{latest['status']}")
        task_store.event(task["id"], message, value, stage=stage)
    result = ImportRebuilder(nm, logger, llm, storage_mgr).rebuild(progress=progress)
    task_store.event(task["id"], "正在建立全文分块语义索引", 96, stage="import_index")
    for path in sorted(
        (path for path in (nm.path / "chapters").glob("*.txt") if path.stem.isdigit()),
        key=lambda path: int(path.stem),
    ):
        latest = task_store.get(task["id"])
        if latest and latest["status"] in {"paused", "cancelled"}:
            raise RuntimeError(f"导入索引已{latest['status']}")
        _index_chapter(nm, int(path.stem), path.read_text("utf-8", errors="replace"))
    return result


def _run_history_revision_task(task: dict) -> dict:
    payload = task.get("input", {})
    nm = get_novel_manager(task["novel"])
    llm = get_llm()
    if not llm:
        raise RuntimeError("本地模型未连接")
    manager = HistoryRevisionManager(nm, logger, llm, storage_mgr)
    revision_id = str(payload.get("revision_id", ""))
    action = payload.get("action", "rewrite")
    existing = manager.get(revision_id)
    if existing.get("status") in {"committed", "aborted"}:
        result = existing
    elif action == "commit":
        task_store.event(task["id"], "正在原子提交历史修改并重建全书逻辑账本", 20, stage="history_commit")
        result = manager.commit(revision_id)
    else:
        def progress(message: str, value: int, stage: str):
            latest = task_store.get(task["id"])
            if latest and latest["status"] in {"paused", "cancelled"}:
                raise RuntimeError(f"历史修改已{latest['status']}")
            task_store.event(task["id"], message, value, stage=stage)
        result = manager.run_branch(revision_id, progress)
        if payload.get("auto_commit") and result.get("status") == "validated":
            task_store.event(task["id"], "分支验证通过，正在原子提交并重建派生状态", 85, stage="history_commit")
            result = manager.commit(revision_id)
    if result.get("status") == "committed":
        for impact in result.get("impact", {}).get("chapters", []):
            chapter = int(impact["chapter"])
            content = (nm.path / "chapters" / config.CHAPTER_FILE_PATTERN.format(chapter)).read_text("utf-8", errors="replace")
            _index_chapter(nm, chapter, content)
    return result


# FastAPI 应用
@asynccontextmanager
async def lifespan(_app: FastAPI):
    task_store.mark_interrupted()
    task_runner.register("batch_chapters", _run_batch_task_entry)
    task_runner.register("workflow", _run_workflow_task)
    task_runner.register("import_rebuild", _run_import_rebuild_task)
    task_runner.register("history_revision", _run_history_revision_task)
    task_store.recover_interrupted(RESUMABLE_TASK_KINDS)
    task_runner.start()
    backup_scheduler.start()
    logger.info("UI 服务启动 v3.0")
    yield
    worker_stopped = task_runner.stop()
    if not worker_stopped:
        task_store.mark_interrupted()
        logger.warning("后台工作器未在关机期限内退出，运行中任务已标记为可恢复中断")
    backup_scheduler.stop()
    if _llm is not None:
        try:
            _llm.stop()
        except Exception:
            pass


app = FastAPI(title="novel-workspace-mcp UI", version="3.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

templates = Jinja2Templates(directory=str(PROJECT_ROOT / "ui" / "templates"))
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "ui" / "static")), name="static")

# 主页面
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    novels_raw = workspace.list_novels()
    novel_list = []
    for n in novels_raw:
        try:
            nm = get_novel_manager(n["name"])
            st = nm.get_state()
            n["total_words"] = st.get("total_words", 0)
            n["next_goal"] = st.get("next_goal", "")
            cm = get_chapter_manager(nm)
            n["summaries"] = cm.get_recent_summaries(3)
        except Exception:
            n["total_words"] = 0
            n["next_goal"] = ""
            n["summaries"] = []
        novel_list.append(n)

    current = workspace.data.get("current")
    current_novel = None
    current_state = {}
    current_chapters = []
    current_characters = []
    current_timeline = []
    if current and current in workspace.data["novels"]:
        try:
            nm = get_novel_manager(current)
            current_state = nm.get_status_report()
            cm = get_chapter_manager(nm)
            current_chapters = cm.get_recent_chapters(5)
            char_mgr = get_character_manager(nm)
            current_characters = char_mgr.list_characters()
            tl_mgr = get_timeline_manager(nm)
            current_timeline = tl_mgr.get_recent_events(5)
            current_novel = {
                "name": current,
                "state": current_state,
                "chapters": current_chapters,
                "characters": current_characters,
                "timeline": current_timeline,
            }
        except Exception as e:
            logger.warning("加载当前小说 %s 失败: %s", current, e)

    model_info = {
        "name": MODEL_CONFIG.get("model_name", "unknown")[:40],
        "ctx": MODEL_CONFIG["context_window"],
        "speed": MODEL_CONFIG["tokens_per_second"],
        "default_words": MODEL_CONFIG["default_target_words"],
    }

    return templates.TemplateResponse(request, "index.html", {
        "novels": novel_list,
        "current_novel": current_novel,
        "model": model_info,
        "target_words_options": [1000, 2000, 3000, 5000, 8000],
        "ability_tiers": ABILITY_TIERS,
    })


# ---- 小说 CRUD ----

@app.post("/api/novels/create")
async def api_create_novel(
    name: str = Form(...), genre: str = Form(""), style: str = Form(""),
    description: str = Form(""), world: str = Form(""), rules: str = Form(""),
):
    created = False
    previous_current = workspace.data.get("current")
    try:
        result = workspace.create_novel(name, genre, style, description)
        created = True
        bible_dir = config.NOVELS_ROOT / name / "bible"
        if world:
            storage_mgr.atomic_write_text(bible_dir / "world.md", world)
        if rules:
            storage_mgr.atomic_write_text(bible_dir / "rules.md", rules)
        if style:
            storage_mgr.atomic_write_text(bible_dir / "style.md", style)
        return JSONResponse({"success": True, "novel": result})
    except Exception as e:
        if created:
            shutil.rmtree(config.NOVELS_ROOT / name, ignore_errors=True)
            workspace.rollback_created(name, previous_current)
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.post("/api/novels/{name}/open")
async def api_open_novel(name: str):
    try:
        workspace.open_novel(name)
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.post("/api/novels/{name}/delete")
async def api_delete_novel(name: str):
    try:
        active_tasks = task_store.active_for_novel(name)
        if active_tasks:
            raise ValueError("小说仍有后台任务运行，请先停止任务后再删除")
        novel_path = config.NOVELS_ROOT / name
        metadata = workspace.data["novels"].get(name)
        if not metadata:
            raise ValueError("小说不存在")
        record = trash_manager.move(name, novel_path, metadata)
        try:
            workspace.remove_novel(name)
        except Exception:
            trash_manager.restore(record["id"], config.NOVELS_ROOT)
            raise
        try:
            store = get_vs()
            if store:
                store.delete_novel(name)
        except Exception as exc:
            logger.warning("小说已移入回收站，但旧向量索引清理失败：%s", exc)
        return JSONResponse({"success": True, "trash": record})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.get("/api/trash")
async def api_trash():
    return JSONResponse({"success": True, "items": trash_manager.list()})


@app.post("/api/trash/{trash_id}/restore")
async def api_restore_trash(trash_id: str):
    record = None
    try:
        record = trash_manager.restore(trash_id, config.NOVELS_ROOT)
        try:
            workspace.register_restored(record["name"], record.get("metadata", {}))
        except Exception:
            trash_manager.undo_restore(record, config.NOVELS_ROOT)
            raise
        nm = get_novel_manager(record["name"])
        for path in sorted(
            (path for path in (nm.path / "chapters").glob("*.txt") if path.stem.isdigit()),
            key=lambda path: int(path.stem),
        ):
            await asyncio.to_thread(_index_chapter, nm, int(path.stem), path.read_text("utf-8", errors="replace"))
        return JSONResponse({"success": True, "name": record["name"]})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/trash/{trash_id}/purge")
async def api_purge_trash(trash_id: str):
    try:
        record = trash_manager.purge(trash_id)
        return JSONResponse({"success": True, "item": record})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/import/txt")
async def api_import_txt(
    name: str = Form(...), genre: str = Form(""), file: UploadFile = File(...),
):
    created = False
    previous_current = workspace.data.get("current")
    try:
        data = await file.read(50_000_001)
        if len(data) > 50_000_000:
            raise ValueError("TXT文件不能超过50MB")
        chapters = await asyncio.to_thread(
            lambda: TextNovelImporter.split(TextNovelImporter.decode(data))
        )
        if not chapters:
            raise ValueError("TXT中没有可导入内容")
        def persist_import():
            nonlocal created
            workspace.create_novel(name, genre, "", f"从 {file.filename or 'TXT'} 导入")
            created = True
            novel_manager = get_novel_manager(name)
            manager = ChapterManager(novel_manager, logger, None)
            titles = {}
            for index, chapter in enumerate(chapters, 1):
                manager.save_chapter(index, chapter["content"])
                titles[str(index)] = chapter["title"]
            storage_mgr.atomic_write_json(
                novel_manager.path / "outline" / "chapter_titles.json", titles,
            )

        await asyncio.to_thread(persist_import)
        task_id = task_store.create_if_idle(
            name, "import_rebuild", f"重建《{name}》人物、总纲与状态",
            {"chapters": len(chapters)}, status="queued",
        )
        if not task_id:
            raise RuntimeError("该小说已有运行、排队或暂停任务，无法启动导入重建")
        task_store.event(task_id, "正文已导入，等待单模型串行重建", 0, stage="queued")
        task_runner.notify()
        return JSONResponse({"success": True, "name": name, "chapters": len(chapters), "task_id": task_id})
    except Exception as exc:
        if created:
            destination = config.NOVELS_ROOT / name
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            workspace.rollback_created(name, previous_current)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/import/project")
async def api_import_project(name: str = Form(...), file: UploadFile = File(...)):
    temp_root = config.STORAGE_ROOT / ".imports"
    temp_root.mkdir(parents=True, exist_ok=True)
    created = False
    previous_current = workspace.data.get("current")
    try:
        data = await file.read(500_000_001)
        if len(data) > 500_000_000:
            raise ValueError("项目ZIP不能超过500MB")
        def persist_project():
            nonlocal created
            with tempfile.TemporaryDirectory(dir=temp_root) as temporary:
                temporary_path = Path(temporary)
                archive = temporary_path / "project.zip"
                archive.write_bytes(data)
                extracted = temporary_path / "project"
                extracted.mkdir()
                ProjectZipRestorer.extract(archive, extracted)
                workspace.create_novel(name)
                created = True
                destination = config.NOVELS_ROOT / name
                shutil.rmtree(destination)
                shutil.copytree(extracted, destination)

        await asyncio.to_thread(persist_project)
        state = storage_mgr.safe_read_json(config.NOVELS_ROOT / name / "state.json", {})
        workspace.update_registration(name, {"genre": state.get("genre", ""), "status": state.get("status", "创作中")})
        return JSONResponse({"success": True, "name": name})
    except Exception as exc:
        if created:
            destination = config.NOVELS_ROOT / name
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            workspace.rollback_created(name, previous_current)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


# ---- 写作 API ----

@app.post("/api/novels/{name}/continue-story")
async def api_continue_story(name: str):
    try:
        nm = get_novel_manager(name)
        ctx_mgr = get_context_manager(nm)
        state = nm.get_status_report()
        context = await asyncio.to_thread(ctx_mgr.build_context, None, None, None, True)
        return JSONResponse({
            "success": True, "context": context, "state": state,
            "next_chapter": state["current_chapter"] + 1,
            "context_tokens": estimate_tokens(context),
            "available_tokens": MODEL_CONFIG["available_context"],
            "context_health": ctx_mgr.last_build_stats,
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.get("/api/novels/{name}/status")
async def api_novel_status(name: str):
    try:
        nm = get_novel_manager(name)
        st = nm.get_status_report()
        cm = get_chapter_manager(nm)
        char_mgr = get_character_manager(nm)
        tl_mgr = get_timeline_manager(nm)
        briefs = storage_mgr.safe_read_json(nm.path / "outline" / "chapter_briefs.json", {})
        next_brief = briefs.get(str(nm.get_current_chapter() + 1))
        return JSONResponse({
            "success": True, "status": st,
            "characters": char_mgr.list_characters(),
            "recent_chapters": cm.get_recent_chapters(3),
            "recent_summaries": cm.get_recent_summaries(3),
            "timeline": tl_mgr.get_recent_events(5),
            "next_brief": next_brief,
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.post("/api/novels/{name}/chapters/save")
async def api_save_chapter(
    name: str, chapter: int = Form(...), content: str = Form(...), target_words: int = Form(0),
    allow_fact_conflicts: bool = Form(False),
    allow_locked_changes: bool = Form(False), allow_story_clock_conflicts: bool = Form(False),
    allow_character_decision_conflicts: bool = Form(False),
    allow_degraded_summary: bool = Form(False),
):
    try:
        nm = get_novel_manager(name)
        cm = get_chapter_manager(nm)
        expected_words = max(500, min(20000, target_words or settings_manager.get()["default_target_words"]))
        committed = await asyncio.to_thread(
            get_turn_engine(nm, cm).commit_manual,
            chapter, content, expected_words,
            lambda number, text: _index_chapter(nm, number, text), True, allow_fact_conflicts,
            False, allow_locked_changes, allow_story_clock_conflicts, allow_character_decision_conflicts,
            allow_degraded_summary,
        )
        result = committed["result"]
        result["turn"] = committed["turn"]
        quality = chapter_quality_gate(content, expected_words)
        quality_warnings = quality["warnings"]
        tracker = QualityTracker(nm.path, logger, storage_mgr)
        for warning in quality_warnings:
            tracker.add_debt(chapter, "generation_quality", "中", warning, "检查后局部重写或补充正文")
        result["quality_warnings"] = quality_warnings
        result["quality"] = quality
        return JSONResponse({"success": True, "result": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.post("/api/novels/{name}/chapters/append")
async def api_append_chapter(
    name: str, chapter: int = Form(...), content: str = Form(...),
    allow_fact_conflicts: bool = Form(False),
    allow_locked_changes: bool = Form(False), allow_story_clock_conflicts: bool = Form(False),
    allow_character_decision_conflicts: bool = Form(False),
    allow_degraded_summary: bool = Form(False),
):
    try:
        nm = get_novel_manager(name)
        cm = get_chapter_manager(nm)
        chapter_path = cm.path / config.CHAPTER_FILE_PATTERN.format(chapter)
        existing = await asyncio.to_thread(
            lambda: chapter_path.read_text("utf-8", errors="replace") if chapter_path.exists() else ""
        )
        full_content = existing.rstrip("\r\n") + "\n" + content if existing else content
        engine = get_turn_engine(nm, cm)
        turn = await asyncio.to_thread(
            engine.save_draft, chapter, full_content, max(500, len(full_content)), "manual_append", {}, False,
        )
        committed = await asyncio.to_thread(
            engine.commit, turn["id"], lambda number, text: _index_chapter(nm, number, text), True,
            allow_fact_conflicts, False, allow_locked_changes,
            allow_story_clock_conflicts, allow_character_decision_conflicts,
            allow_degraded_summary,
        )
        result = committed["result"]
        return JSONResponse({"success": True, "result": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.get("/api/novels/{name}/chapters/{chapter}")
async def api_read_chapter(name: str, chapter: int):
    try:
        nm = get_novel_manager(name)
        cm = get_chapter_manager(nm)
        content = cm.read_chapter(chapter)
        if content is None:
            return JSONResponse({"success": False, "error": "章节不存在"}, status_code=404)
        return JSONResponse({"success": True, "content": content, "words": len(content.replace(" ", ""))})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.get("/api/novels/{name}/chapters")
async def api_list_chapters(name: str):
    try:
        nm = get_novel_manager(name)
        chapters_dir = nm.path / "chapters"
        if not chapters_dir.exists():
            return JSONResponse({"success": True, "chapters": []})
        files = sorted(chapters_dir.glob("*.txt"))
        titles = storage_mgr.safe_read_json(nm.path / "outline" / "chapter_titles.json", {})
        result = []
        for f in files:
            try:
                num = int(f.stem)
                words = len(f.read_text("utf-8", errors="replace").replace(" ", ""))
                result.append({"chapter": num, "title": titles.get(str(num), ""), "words": words})
            except Exception:
                pass
        return JSONResponse({"success": True, "chapters": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


# ---- 人物 API ----

@app.get("/api/novels/{name}/characters")
async def api_list_characters(name: str):
    try:
        nm = get_novel_manager(name)
        char_mgr = get_character_manager(nm)
        chars = char_mgr.list_characters()
        full = []
        for c in chars:
            d = char_mgr.get_character(c["name"])
            full.append(d or c)
        return JSONResponse({"success": True, "characters": full})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.post("/api/novels/{name}/characters/create")
async def api_create_character(
    name: str, char_name: str = Form(...), personality: str = Form(""),
    background: str = Form(""), abilities: str = Form(""),
    ability_level: str = Form("凡人"), relationships: str = Form(""),
    status: str = Form("存活"), role_tier: str = Form("重要配角"),
    appearance_start: int = Form(1), appearance_end: int = Form(0),
    personality_profile: str = Form(""),
):
    try:
        nm = get_novel_manager(name)
        char_mgr = get_character_manager(nm)
        result = char_mgr.create_character(
            char_name, personality, background, abilities,
            ability_level, relationships, status, role_tier, appearance_start, appearance_end,
            personality_profile=_parse_personality_profile(personality_profile),
        )
        return JSONResponse({"success": True, "character": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.post("/api/novels/{name}/characters/update")
async def api_update_character(
    name: str, char_name: str = Form(...), personality: Optional[str] = Form(None),
    background: Optional[str] = Form(None), abilities: Optional[str] = Form(None),
    ability_level: Optional[str] = Form(None), relationships: Optional[str] = Form(None),
    current_status: Optional[str] = Form(None), last_chapter: Optional[int] = Form(None),
    role_tier: Optional[str] = Form(None), appearance_start: Optional[int] = Form(None),
    appearance_end: Optional[int] = Form(None),
    personality_profile: Optional[str] = Form(None),
):
    try:
        nm = get_novel_manager(name)
        char_mgr = get_character_manager(nm)
        data = {
            key: value for key, value in {
                "personality": personality, "background": background, "abilities": abilities,
                "ability_level": ability_level, "relationships": relationships,
                "current_status": current_status, "last_chapter": last_chapter,
                "role_tier": role_tier, "appearance_start": appearance_start, "appearance_end": appearance_end,
                "personality_profile": _parse_personality_profile(personality_profile)
                if personality_profile is not None else None,
            }.items() if value is not None
        }
        result = char_mgr.update_character(char_name, **data)
        return JSONResponse({"success": True, "character": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


# ---- 时间线 API ----

@app.get("/api/novels/{name}/timeline")
async def api_timeline(name: str, chapter: int = None):
    try:
        nm = get_novel_manager(name)
        tl_mgr = get_timeline_manager(nm)
        if chapter:
            events = tl_mgr.get_events_by_chapter(chapter)
        else:
            events = tl_mgr.get_recent_events(50)
        return JSONResponse({"success": True, "events": events})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.post("/api/novels/{name}/timeline/add")
async def api_add_event(
    name: str, chapter: int = Form(...), time: str = Form(""),
    location: str = Form(""), event: str = Form(...), characters: str = Form(""),
):
    try:
        nm = get_novel_manager(name)
        tl_mgr = get_timeline_manager(nm)
        char_list = [c.strip() for c in characters.split(",") if c.strip()] if characters else []
        result = tl_mgr.add_event(chapter, time, location, event, char_list)
        return JSONResponse({"success": True, "event": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


# ---- 设定 API ----

@app.get("/api/novels/{name}/bible")
async def api_get_bible(name: str):
    try:
        bible_dir = config.NOVELS_ROOT / name / "bible"
        data = {}
        for fname in ["world.md", "rules.md", "style.md"]:
            fp = bible_dir / fname
            data[fname.replace(".md", "")] = fp.read_text("utf-8") if fp.exists() else ""
        outline_file = config.NOVELS_ROOT / name / "outline" / "main.md"
        data["outline"] = outline_file.read_text("utf-8") if outline_file.exists() else ""
        nm = get_novel_manager(name)
        st = nm.get_state()
        data["next_goal"] = st.get("next_goal", "")
        data["description"] = st.get("description", "")
        data["genre"] = st.get("genre", "")
        data["target_chapters"] = st.get("target_chapters", 0)
        data["volumes"] = storage_mgr.safe_read_json(config.NOVELS_ROOT / name / "outline" / "volumes.json", [])
        data["chapter_briefs"] = storage_mgr.safe_read_json(config.NOVELS_ROOT / name / "outline" / "chapter_briefs.json", {})
        data["opening"] = storage_mgr.safe_read_json(config.NOVELS_ROOT / name / "outline" / "opening_chapters.json", {})
        return JSONResponse({"success": True, "bible": data})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.post("/api/novels/{name}/bible/save")
async def api_save_bible(
    name: str, world: Optional[str] = Form(None), rules: Optional[str] = Form(None),
    style: Optional[str] = Form(None), outline: Optional[str] = Form(None), next_goal: Optional[str] = Form(None),
    target_chapters: int = Form(0),
    volumes: Optional[str] = Form(None), chapter_briefs: Optional[str] = Form(None),
):
    try:
        nm = get_novel_manager(name)
        parsed_volumes = None
        parsed_briefs = None
        if volumes is not None:
            parsed_volumes = json.loads(volumes) if volumes.strip() else []
            if not isinstance(parsed_volumes, list):
                raise ValueError("卷纲与节纲必须是 JSON 数组")
            if target_chapters > 0:
                parsed_volumes = normalize_volume_ranges(parsed_volumes, target_chapters)
                for volume in parsed_volumes:
                    volume["sections"] = normalize_section_ranges(volume)
        if chapter_briefs is not None:
            parsed_briefs = json.loads(chapter_briefs) if chapter_briefs.strip() else {}
            if not isinstance(parsed_briefs, dict):
                raise ValueError("章节提要必须是以章节号为键的 JSON 对象")
        with FileLock(str(nm.path / ".novel_mutation.lock"), timeout=600), NovelMutationTransaction(
            nm.path, [], directories=("bible", "outline", "planning"), files=("state.json",),
        ):
            impact = _persist_bible_changes(
                nm, world, rules, style, outline, next_goal, target_chapters,
                parsed_volumes, parsed_briefs,
            )
        return JSONResponse({"success": True, "planning_impact": impact})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.get("/api/novels/{name}/release-readiness")
async def api_release_readiness(name: str):
    try:
        report = await asyncio.to_thread(ReleaseReadinessManager(
            get_novel_manager(name), logger, storage_mgr,
        ).run)
        return JSONResponse({"success": True, "report": report})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/generation-provenance")
async def api_generation_provenance(name: str, limit: int = 100):
    try:
        items = await asyncio.to_thread(
            GenerationProvenanceManager(get_novel_manager(name), logger, storage_mgr).list, limit,
        )
        return JSONResponse({"success": True, "items": items})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/generation-provenance/{chapter}")
async def api_chapter_generation_provenance(name: str, chapter: int):
    try:
        item = await asyncio.to_thread(
            GenerationProvenanceManager(get_novel_manager(name), logger, storage_mgr).get, chapter,
        )
        return JSONResponse({"success": True, "item": item})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/production-control")
async def api_production_control(name: str):
    try:
        nm = get_novel_manager(name)
        manager = ProductionControlManager(nm, logger, storage_mgr)
        statistics = manager.statistics(task_store.list(name, 200))
        readiness, issues = await asyncio.gather(
            asyncio.to_thread(ReleaseReadinessManager(nm, logger, storage_mgr).run),
            asyncio.to_thread(manager.issues),
        )
        return JSONResponse({
            "success": True, "policy": manager.policy(), "rhythm": manager.rhythm(),
            "budget": manager.budget(10, manager.policy()["target_words"], MODEL_CONFIG.get("tokens_per_second", 50)),
            "manuscript": manager.manuscript(), "readiness": readiness, "issues": issues,
            "statistics": statistics, "runtime": manager.runtime(),
        })
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.put("/api/novels/{name}/production-control/policy")
async def api_update_serial_policy(name: str, request: Request):
    try:
        policy = ProductionControlManager(
            get_novel_manager(name), logger, storage_mgr,
        ).update_policy(await request.json())
        return JSONResponse({"success": True, "policy": policy})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/production-control/start")
async def api_start_serial_control(name: str):
    try:
        nm = get_novel_manager(name)
        manager = ProductionControlManager(nm, logger, storage_mgr)
        readiness = await asyncio.to_thread(ReleaseReadinessManager(nm, logger, storage_mgr).run)
        if readiness["status"] == "blocked":
            return JSONResponse({"success": False, "error": "项目可用性验收未通过", "readiness": readiness}, status_code=409)
        if task_store.active_for_novel(name):
            return JSONResponse({"success": False, "error": "该小说已有运行、排队或暂停任务"}, status_code=409)
        policy = manager.update_policy({"enabled": True})
        task_id = _enqueue_serial_batch(nm, policy)
        if not task_id:
            return JSONResponse({"success": False, "error": "该小说已有运行、排队或暂停任务"}, status_code=409)
        return JSONResponse({"success": True, "task_id": task_id, "policy": policy})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/production-control/stop")
async def api_stop_serial_control(name: str):
    try:
        manager = ProductionControlManager(get_novel_manager(name), logger, storage_mgr)
        policy = manager.update_policy({"enabled": False})
        cancelled = 0
        for item in task_store.list(name, 200):
            if item.get("status") != "queued":
                continue
            task = task_store.get(item["id"])
            if task and task.get("input", {}).get("serial_controller"):
                task_store.cancel(item["id"])
                cancelled += 1
        return JSONResponse({"success": True, "policy": policy, "cancelled_queued": cancelled})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/planning-tree")
async def api_planning_tree(name: str):
    return JSONResponse({
        "success": True,
        "tree": ProductionControlManager(get_novel_manager(name), logger, storage_mgr).planning_tree(),
    })


@app.patch("/api/novels/{name}/planning-tree/{node_id}")
async def api_update_planning_tree(name: str, node_id: str, request: Request):
    try:
        payload = await request.json()
        result = await asyncio.to_thread(
            ProductionControlManager(get_novel_manager(name), logger, storage_mgr).update_tree_node,
            node_id, payload.get("data") if isinstance(payload, dict) else payload,
        )
        return JSONResponse({"success": True, **result})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/production-issues")
async def api_production_issues(name: str):
    try:
        result = await asyncio.to_thread(
            ProductionControlManager(get_novel_manager(name), logger, storage_mgr).issues,
        )
        return JSONResponse({"success": True, **result})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/production-issues/{issue_id}/action")
async def api_resolve_production_issue(name: str, issue_id: str, request: Request):
    try:
        payload = await request.json()
        result = await asyncio.to_thread(
            ProductionControlManager(get_novel_manager(name), logger, storage_mgr).resolve_issue,
            issue_id, str(payload.get("action", "resolve")), payload,
        )
        return JSONResponse({"success": True, **result})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/production-statistics")
async def api_production_statistics(name: str):
    manager = ProductionControlManager(get_novel_manager(name), logger, storage_mgr)
    return JSONResponse({
        "success": True,
        "statistics": manager.statistics(task_store.list(name, 200)),
    })


@app.get("/api/novels/{name}/generation-budget")
async def api_generation_budget(name: str, chapters: int = 10, target_words: int = 5000):
    result = ProductionControlManager(get_novel_manager(name), logger, storage_mgr).budget(
        chapters, target_words, MODEL_CONFIG.get("tokens_per_second", 50),
    )
    return JSONResponse({"success": True, "budget": result})


@app.get("/api/novels/{name}/manuscript-plan")
async def api_manuscript_plan(name: str):
    return JSONResponse({
        "success": True,
        "manuscript": ProductionControlManager(get_novel_manager(name), logger, storage_mgr).manuscript(),
    })


@app.post("/api/novels/{name}/chapter-candidates")
async def api_create_chapter_candidate(name: str, request: Request):
    try:
        payload = await request.json()
        chapter = int(payload.get("chapter", 0))
        content = str(payload.get("content", ""))
        nm = get_novel_manager(name)
        turn = await asyncio.to_thread(
            get_turn_engine(nm).save_draft, chapter, content,
            max(500, min(20000, int(payload.get("target_words", 5000)))), "candidate",
            {"label": str(payload.get("label", "候选稿"))[:120], "parent_turn_id": str(payload.get("parent_turn_id", ""))},
            False,
        )
        return JSONResponse({"success": True, "turn": turn})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/chapter-candidates/{chapter}")
async def api_chapter_candidates(name: str, chapter: int):
    turns = get_turn_engine(get_novel_manager(name)).list(chapter, 200)
    items = [item for item in turns if item.get("source") == "candidate" and item.get("status") not in {"discarded", "superseded"}]
    return JSONResponse({"success": True, "items": items})


@app.get("/api/novels/{name}/chapter-candidate-comparisons/{left_id}/{right_id}")
async def api_compare_chapter_candidates(name: str, left_id: str, right_id: str):
    try:
        engine = get_turn_engine(get_novel_manager(name))
        left, right = engine.get(left_id), engine.get(right_id)
        if int(left["chapter"]) != int(right["chapter"]):
            raise ValueError("只能比较同一章的候选稿")
        left_text, right_text = engine.read_draft(left_id), engine.read_draft(right_id)
        diff = "".join(difflib.unified_diff(
            left_text.splitlines(keepends=True), right_text.splitlines(keepends=True),
            fromfile=left_id, tofile=right_id,
        ))
        return JSONResponse({"success": True, "chapter": left["chapter"], "diff": diff or "候选稿内容相同"})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/turns")
async def api_chapter_turns(name: str, chapter: Optional[int] = None, limit: int = 50):
    try:
        nm = get_novel_manager(name)
        return JSONResponse({"success": True, "turns": get_turn_engine(nm).list(chapter, limit)})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/turns/{turn_id}")
async def api_chapter_turn(name: str, turn_id: str):
    try:
        nm = get_novel_manager(name)
        engine = get_turn_engine(nm)
        return JSONResponse({
            "success": True, "inspection": engine.inspect(turn_id),
            "content": engine.read_draft(turn_id),
        })
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/turns/{turn_id}/preview")
async def api_preview_chapter_turn(name: str, turn_id: str):
    try:
        nm = get_novel_manager(name)
        preview = await asyncio.to_thread(get_turn_engine(nm).preview_changes, turn_id)
        return JSONResponse({"success": True, "preview": preview})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/turns/{turn_id}/commit")
async def api_commit_chapter_turn(name: str, turn_id: str, request: Request):
    try:
        payload = await request.json()
        nm = get_novel_manager(name)
        result = await asyncio.to_thread(
            get_turn_engine(nm).commit, turn_id,
            lambda number, text: _index_chapter(nm, number, text),
            bool(payload.get("allow_quality_failure", False)),
            bool(payload.get("allow_fact_conflicts", False)),
            bool(payload.get("allow_stale_planning", False)),
            bool(payload.get("allow_locked_changes", False)),
            bool(payload.get("allow_story_clock_conflicts", False)),
            bool(payload.get("allow_character_decision_conflicts", False)),
            bool(payload.get("allow_degraded_summary", False)),
        )
        return JSONResponse({"success": True, **result})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/turns/{turn_id}/discard")
async def api_discard_chapter_turn(name: str, turn_id: str):
    try:
        turn = get_turn_engine(get_novel_manager(name)).discard(turn_id)
        return JSONResponse({"success": True, "turn": turn})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/health")
async def api_project_health(name: str):
    try:
        nm = get_novel_manager(name)
        report = await asyncio.to_thread(ProjectHealthManager(nm, logger, storage_mgr).scan)
        return JSONResponse({"success": True, "report": report})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/health/repair")
async def api_repair_project_health(name: str):
    try:
        nm = get_novel_manager(name)
        result = await asyncio.to_thread(ProjectHealthManager(nm, logger, storage_mgr).repair)
        return JSONResponse({"success": True, **result})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


def _persist_bible_changes(
    nm: NovelManager, world: Optional[str], rules: Optional[str], style: Optional[str],
    outline: Optional[str], next_goal: Optional[str], target_chapters: int,
    parsed_volumes: list | None, parsed_briefs: dict | None,
) -> dict:
    tracked_paths = [
        nm.path / "bible" / "world.md", nm.path / "bible" / "rules.md",
        nm.path / "bible" / "style.md", nm.path / "outline" / "main.md",
    ]
    old_sources = {str(path): path.read_text("utf-8", errors="replace") if path.exists() else "" for path in tracked_paths}
    old_volumes = storage_mgr.safe_read_json(nm.path / "outline" / "volumes.json", [])
    old_briefs = storage_mgr.safe_read_json(nm.path / "outline" / "chapter_briefs.json", {})
    PlanningVersionManager(nm.path)._snapshot("保存设定前")
    bible_dir = nm.path / "bible"
    bible_dir.mkdir(parents=True, exist_ok=True)
    if world is not None:
        storage_mgr.atomic_write_text(bible_dir / "world.md", world)
    if rules is not None:
        storage_mgr.atomic_write_text(bible_dir / "rules.md", rules)
    if style is not None:
        storage_mgr.atomic_write_text(bible_dir / "style.md", style)
    outline_file = nm.path / "outline" / "main.md"
    outline_file.parent.mkdir(parents=True, exist_ok=True)
    if outline is not None:
        storage_mgr.atomic_write_text(outline_file, outline)
    if parsed_volumes is not None:
        storage_mgr.atomic_write_json(outline_file.parent / "volumes.json", parsed_volumes)
    if parsed_briefs is not None:
        storage_mgr.atomic_write_json(outline_file.parent / "chapter_briefs.json", parsed_briefs)
    if next_goal is not None:
        nm.update_next_goal(next_goal)
    if target_chapters:
        nm.save_state({"target_chapters": max(5, min(1000, target_chapters))})
    new_volumes = storage_mgr.safe_read_json(nm.path / "outline" / "volumes.json", [])
    new_briefs = storage_mgr.safe_read_json(nm.path / "outline" / "chapter_briefs.json", {})
    upstream_changed = any(
        (path.read_text("utf-8", errors="replace") if path.exists() else "") != old_sources[str(path)]
        for path in tracked_paths
    )
    return PlanningImpactManager(nm.path, logger, storage_mgr).record_changes(
        old_volumes, new_volumes, old_briefs, new_briefs, nm.get_current_chapter(), upstream_changed,
    )


# ---- 一致性检查 ----

@app.get("/api/novels/{name}/consistency")
async def api_consistency(name: str):
    try:
        nm = get_novel_manager(name)
        result = await asyncio.to_thread(ConsistencyManager(nm, logger).check_all)
        return JSONResponse({
            "success": True,
            "issues": result,
            "summary": f"完成深度一致性检查，发现 {len(result)} 个问题",
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.get("/api/novels/{name}/planning-versions")
async def api_planning_versions(name: str):
    return JSONResponse({"success": True, "versions": PlanningVersionManager(get_novel_manager(name).path).list()})


@app.get("/api/novels/{name}/planning-versions/{version_id}/diff")
async def api_planning_version_diff(name: str, version_id: str):
    try:
        return JSONResponse({"success": True, "diff": PlanningVersionManager(get_novel_manager(name).path).diff(version_id)})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/planning-versions/{version_id}/restore")
async def api_restore_planning_version(name: str, version_id: str):
    try:
        nm = get_novel_manager(name)
        item = await asyncio.to_thread(PlanningVersionManager(nm.path).restore, version_id)
        state = nm.get_state()
        workspace.update_registration(
            name, {"genre": state.get("genre", ""), "status": state.get("status", "创作中")}, False,
        )
        return JSONResponse({"success": True, "version": item})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/search")
async def api_search_novel(name: str, q: str):
    nm = get_novel_manager(name)
    query = q.strip().lower()
    if len(query) < 2:
        return JSONResponse({"success": True, "results": []})
    results = []
    for path in nm.path.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".json"} or "versions" in path.parts:
            continue
        try:
            text = path.read_text("utf-8")
        except Exception:
            continue
        lower = text.lower()
        cursor = 0
        while len(results) < 80:
            index = lower.find(query, cursor)
            if index < 0:
                break
            results.append({"file": str(path.relative_to(nm.path)), "snippet": text[max(0, index - 80):index + len(query) + 120].replace("\n", " "), "offset": index})
            cursor = index + len(query)
        if len(results) >= 80:
            break
    return JSONResponse({"success": True, "results": results})


@app.get("/api/novels/{name}/semantic-search")
async def api_semantic_search(name: str, q: str, top_k: int = 8):
    query = q.strip()
    if len(query) < 2:
        return JSONResponse({"success": True, "results": []})
    store = await asyncio.to_thread(get_vs)
    if not store:
        return JSONResponse({"success": False, "error": "向量模型不可用"}, status_code=503)
    results = await asyncio.to_thread(store.search, query, name, max(1, min(20, top_k)))
    return JSONResponse({"success": True, "results": results})


@app.post("/api/novels/{name}/rag-evaluate")
async def api_rag_evaluate(name: str, request: Request):
    payload = await request.json()
    cases = payload.get("cases", []) if isinstance(payload.get("cases"), list) else []
    store = await asyncio.to_thread(get_vs)
    if not store:
        return JSONResponse({"success": False, "error": "向量模型不可用"}, status_code=503)
    report = await asyncio.to_thread(store.evaluate, cases, name, int(payload.get("top_k", 5)))
    storage_mgr.atomic_write_json(get_novel_manager(name).path / "tracking" / "rag_evaluation.json", report)
    return JSONResponse({"success": True, "report": report})


@app.get("/api/novels/{name}/character-arcs")
async def api_character_arcs(name: str):
    nm = get_novel_manager(name)
    manager = CharacterManager(nm.path, logger)
    arcs = []
    for summary in manager.list_characters():
        data = manager.get_character(summary["name"]) or {}
        points = [{"chapter": item.get("chapter", 0), "type": "能力", "value": item.get("level", "")} for item in data.get("ability_history", [])]
        points += [{"chapter": item.get("chapter", 0), "type": "地点", "value": item.get("location", "")} for item in data.get("locations", [])]
        points += [{"chapter": data.get("last_chapter", 0), "type": "事件", "value": item} for item in data.get("important_events", [])[-10:]]
        arcs.append({"name": summary["name"], "status": data.get("current_status", ""), "points": sorted(points, key=lambda item: int(item.get("chapter", 0)))})
    return JSONResponse({"success": True, "arcs": arcs})


@app.post("/api/novels/{name}/chapters/{chapter}/review")
async def api_review_chapter(name: str, chapter: int):
    try:
        nm = get_novel_manager(name)
        content = get_chapter_manager(nm).read_chapter(chapter)
        if not content:
            raise ValueError("章节不存在")
        llm = await asyncio.to_thread(get_llm)
        system = BASE_SYSTEM + "\n你是小说审稿编辑。只输出JSON，定位具体问题段落，不重写整章。"
        prompt = f'''审查下面第{chapter}章，返回{{"score":0,"summary":"总体评价","issues":[{{"type":"逻辑/人物/重复/节奏/文风","quote":"问题原句或短段","reason":"原因","replacement":"只修改该问题段的建议稿"}}]}}。没有明确问题时issues为空。\n<chapter>\n{content[:16000]}\n</chapter>'''
        raw = await asyncio.to_thread(llm.chat, system, prompt, 2600, task_type="structured")
        report = parse_object(raw)
        return JSONResponse({"success": True, "report": report, "metrics": llm.last_metrics})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/chapters/{chapter}/apply-review")
async def api_apply_chapter_review(name: str, chapter: int, request: Request):
    try:
        payload = await request.json()
        quote = str(payload.get("quote", ""))
        replacement = str(payload.get("replacement", ""))
        if not quote or not replacement:
            raise ValueError("问题原文和替换稿不能为空")
        nm = get_novel_manager(name)
        manager = get_chapter_manager(nm)
        content = manager.read_chapter(chapter) or ""
        if quote not in content:
            raise ValueError("问题原文已变化，请重新审稿")
        revised = content.replace(quote, replacement, 1)
        engine = get_turn_engine(nm, manager)
        turn = await asyncio.to_thread(engine.save_draft, chapter, revised, max(500, len(revised)), "review", {}, False)
        committed = await asyncio.to_thread(
            engine.commit, turn["id"], lambda number, text: _index_chapter(nm, number, text), True,
            bool(payload.get("allow_fact_conflicts", False)),
        )
        result = committed["result"]
        return JSONResponse({"success": True, "result": result, "content": revised})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/dashboard")
async def api_dashboard(name: str):
    try:
        nm = get_novel_manager(name)
        state = nm.get_state()
        fact_manager = FactManager(nm.path, logger, storage_mgr)
        foreshadows = ForeshadowManager(nm.path, logger, storage_mgr).list(state.get("current_chapter", 0))
        reviews = ChangeReviewManager(nm.path, logger, storage_mgr).list("pending")
        tasks = task_store.list(name, 8)
        quality = QualityTracker(nm.path, logger, storage_mgr).get_report()
        planning_position = {"volume": "未规划", "section": "未规划", "section_progress": 0}
        current_chapter = state.get("current_chapter", 0)
        for volume in storage_mgr.safe_read_json(nm.path / "outline" / "volumes.json", []):
            if int(volume.get("start_chapter", 0)) <= current_chapter <= int(volume.get("end_chapter", 0)):
                planning_position["volume"] = volume.get("title", "未命名卷")
                for section in volume.get("sections", []):
                    start, end = int(section.get("start_chapter", 0)), int(section.get("end_chapter", 0))
                    if start <= current_chapter <= end:
                        planning_position["section"] = section.get("title", "未命名节")
                        planning_position["section_progress"] = round((current_chapter - start + 1) / max(1, end - start + 1) * 100)
                        break
                break
        return JSONResponse({"success": True, "dashboard": {
            "state": state,
            "character_count": len(get_character_manager(nm).list_characters()),
            "pending_character_changes": len(reviews),
            "fact_conflicts": len(fact_manager.unresolved_conflicts()),
            "open_foreshadows": len([item for item in foreshadows if item["status"] == "open"]),
            "overdue_foreshadows": len([item for item in foreshadows if item.get("overdue")]),
            "quality": quality,
            "planning_position": planning_position,
            "tasks": tasks,
        }})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/character-changes")
async def api_character_changes(name: str, status: Optional[str] = "pending"):
    try:
        nm = get_novel_manager(name)
        items = ChangeReviewManager(nm.path, logger, storage_mgr).list(status or None)
        return JSONResponse({"success": True, "items": items})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/review-queue")
async def api_review_queue(name: str):
    try:
        nm = get_novel_manager(name)
        result = ReviewQueueManager(nm.path, logger, storage_mgr).build()
        for task in task_store.list(name, 100):
            if task.get("status") != "paused":
                continue
            detail = task_store.get(task.get("id", "")) or {}
            waiting = detail.get("input", {}).get("waiting_review", {}) if isinstance(detail.get("input"), dict) else {}
            if not isinstance(waiting, dict) or not waiting.get("chapter"):
                continue
            chapters = _positive_ints([waiting.get("chapter")])
            if not chapters:
                continue
            result["items"].insert(0, {
                "type": "task_review", "id": str(task.get("id", "")),
                "chapter": chapters[0], "title": "后台任务等待人工验收",
                "detail": f"{task.get('title', '后台任务')} · {waiting.get('kind', 'review')}",
                "severity": "高", "blocking": True, "target": "dashboard",
            })
            result["total"] += 1
            result["blocking"] += 1
            result["by_type"]["task_review"] = result["by_type"].get("task_review", 0) + 1
        return JSONResponse({"success": True, **result})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/canonical-locks")
async def api_canonical_locks(name: str):
    try:
        nm = get_novel_manager(name)
        return JSONResponse({"success": True, "items": CanonicalLockManager(nm.path, logger, storage_mgr).list()})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/canonical-locks")
async def api_upsert_canonical_lock(name: str, request: Request):
    try:
        payload = await request.json()
        nm = get_novel_manager(name)
        item = CanonicalLockManager(nm.path, logger, storage_mgr).upsert(
            payload.get("kind", ""), payload.get("name", ""), payload.get("field", ""),
            payload.get("value", ""), payload.get("reason", ""),
        )
        return JSONResponse({"success": True, "item": item})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.delete("/api/novels/{name}/canonical-locks/{lock_id}")
async def api_remove_canonical_lock(name: str, lock_id: str):
    try:
        nm = get_novel_manager(name)
        removed = CanonicalLockManager(nm.path, logger, storage_mgr).remove(lock_id)
        return JSONResponse({"success": True, "removed": removed})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/story-clock")
async def api_story_clock(name: str):
    try:
        nm = get_novel_manager(name)
        return JSONResponse({"success": True, "clock": StoryClockManager(nm.path, logger, storage_mgr).get()})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/story-clock/travel-rules")
async def api_story_clock_travel_rule(name: str, request: Request):
    try:
        payload = await request.json()
        nm = get_novel_manager(name)
        rule = StoryClockManager(nm.path, logger, storage_mgr).set_travel_rule(
            payload.get("from", ""), payload.get("to", ""), payload.get("minutes", 0),
        )
        return JSONResponse({"success": True, "rule": rule})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.delete("/api/novels/{name}/story-clock/travel-rules")
async def api_remove_story_clock_travel_rule(name: str, origin: str, destination: str):
    try:
        nm = get_novel_manager(name)
        removed = StoryClockManager(nm.path, logger, storage_mgr).remove_travel_rule(origin, destination)
        return JSONResponse({"success": True, "removed": removed})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/character-changes/{change_id}/decide")
async def api_decide_character_change(name: str, change_id: str, accept: bool = Form(...)):
    try:
        nm = get_novel_manager(name)
        item = ChangeReviewManager(nm.path, logger, storage_mgr).decide(change_id, accept)
        return JSONResponse({"success": True, "item": item})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/versions")
async def api_versions(name: str, chapter: Optional[int] = None, limit: int = 50):
    try:
        nm = get_novel_manager(name)
        versions = SavepointManager(nm.path, logger, storage_mgr).list_savepoints(chapter, limit)
        return JSONResponse({"success": True, "versions": versions})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/versions/{chapter}/{version_id}/restore")
async def api_restore_version(name: str, chapter: int, version_id: str):
    try:
        nm = get_novel_manager(name)
        content = await asyncio.to_thread(
            SavepointManager(nm.path, logger, storage_mgr).restore, chapter, version_id,
        )
        if content is None:
            return JSONResponse({"success": False, "error": "版本不存在"}, status_code=404)
        def restore_version():
            manager = get_chapter_manager(nm)
            engine = get_turn_engine(nm, manager)
            turn = engine.save_draft(
                chapter, content, max(500, len(content)), "version_restore", {}, False,
            )
            return engine.commit(
                turn["id"], lambda number, text: _index_chapter(nm, number, text), True, True,
            )["result"]

        result = await asyncio.to_thread(restore_version)
        return JSONResponse({"success": True, "result": result})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/versions/{chapter}/{version_id}/diff")
async def api_diff_version(name: str, chapter: int, version_id: str):
    try:
        nm = get_novel_manager(name)
        diff = SavepointManager(nm.path, logger, storage_mgr).diff(chapter, version_id)
        return JSONResponse({"success": True, "diff": diff})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/export/{format_name}")
async def api_export_novel(name: str, format_name: str):
    try:
        nm = get_novel_manager(name)
        path = await asyncio.to_thread(ExportManager(name, nm.path, logger).export, format_name)
        return FileResponse(path, filename=path.name)
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/facts")
async def api_facts(name: str):
    try:
        nm = get_novel_manager(name)
        manager = FactManager(nm.path, logger, storage_mgr)
        return JSONResponse({
            "success": True,
            "facts": manager.recent(100),
            "conflicts": manager.unresolved_conflicts(),
        })
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/foreshadowing")
async def api_foreshadowing(name: str):
    try:
        nm = get_novel_manager(name)
        current = nm.get_current_chapter()
        items = ForeshadowManager(nm.path, logger, storage_mgr).list(current)
        return JSONResponse({"success": True, "items": items})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/story-logic")
async def api_story_logic(name: str):
    try:
        nm = get_novel_manager(name)
        logic = StoryLogicManager(nm.path, logger, storage_mgr).get()
        return JSONResponse({"success": True, "logic": logic})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


app.include_router(create_causal_router(get_novel_manager, logger, storage_mgr))


@app.get("/api/novels/{name}/chapter-memory")
async def api_chapter_memory(name: str):
    try:
        nm = get_novel_manager(name)
        chapter = nm.get_current_chapter()
        if chapter < 1:
            return JSONResponse({"success": True, "chapter": 0, "status": "empty", "handoff": {}, "plan_reconciliation": {}})
        manager = get_chapter_manager(nm)
        content = manager.read_chapter(chapter) or ""
        summary = manager.summary_mgr.ensure_continuity_memory(chapter, content) if content else {}
        current = bool(content) and summary.get("source_hash") == chapter_source_hash(content)
        return JSONResponse({
            "success": True,
            "chapter": chapter,
            "status": "verified" if current else "stale",
            "handoff": summary.get("handoff", {}),
            "plan_reconciliation": summary.get("plan_reconciliation", {}),
        })
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/chapter-memory/{chapter}/review")
async def api_review_chapter_memory(name: str, chapter: int, request: Request):
    try:
        payload = await request.json()
        nm = get_novel_manager(name)
        data = get_chapter_manager(nm).summary_mgr.review_memory(chapter, str(payload.get("status", "confirmed")), payload.get("edits", {}))
        return JSONResponse({"success": True, "memory": data})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/scene-outlines")
async def api_scene_outlines(name: str):
    manager = SceneOutlineManager(get_novel_manager(name).path, logger, storage_mgr)
    return JSONResponse({"success": True, "items": manager.list()})


@app.get("/api/novels/{name}/scene-outlines/{chapter}")
async def api_scene_outline(name: str, chapter: int):
    item = SceneOutlineManager(get_novel_manager(name).path, logger, storage_mgr).get(chapter)
    return JSONResponse({"success": True, "item": item})


@app.post("/api/novels/{name}/scene-outlines/{chapter}")
async def api_save_scene_outline(name: str, chapter: int, request: Request):
    try:
        item = SceneOutlineManager(get_novel_manager(name).path, logger, storage_mgr).save(chapter, await request.json())
        return JSONResponse({"success": True, "item": item})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/scene-outlines/{chapter}/generate")
async def api_generate_scene_outline(name: str, chapter: int, request: Request):
    try:
        payload = await request.json()
        target_words = max(500, min(20000, int(payload.get("target_words", 5000))))
        nm = get_novel_manager(name)
        existing = SceneOutlineManager(nm.path, logger, storage_mgr).get(chapter)
        if existing and existing.get("status") == "confirmed":
            return JSONResponse({"success": True, "item": existing, "preserved": True, "metrics": {}})
        llm = await asyncio.to_thread(get_llm)
        manager = ContextManager(nm, logger, get_vs(), llm)
        context = await asyncio.to_thread(manager.build_context, None, None, None, False, "planning")
        brief = await asyncio.to_thread(_ensure_chapter_brief, nm, llm, context, chapter)
        system, prompt = chapter_plan_prompts(name, context + "\n\n" + render_chapter_brief(brief), False, target_words)
        raw = await asyncio.to_thread(llm.chat, system, prompt, MODEL_CONFIG.get("analysis_max_tokens", 1536), task_type="planning")
        plan = validate_chapter_plan(parse_object(raw))
        fingerprint = _chapter_plan_fingerprint(nm, chapter, brief, target_words, False)
        _save_cached_chapter_plan(nm, chapter, fingerprint, plan)
        return JSONResponse({"success": True, "item": SceneOutlineManager(nm.path, logger, storage_mgr).get(chapter), "metrics": llm.last_metrics})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/state-cards")
async def api_state_cards(name: str):
    nm = get_novel_manager(name)
    manager = StateCardManager(nm.path, logger, storage_mgr)
    cards = manager.get()
    if not any(cards.get(kind) for kind in manager.TYPES):
        characters = CharacterManager(nm.path, logger)
        for summary in characters.list_characters():
            detail = characters.get_character(summary["name"]) or summary
            location = (detail.get("locations") or [{}])[-1].get("location", "") if isinstance(detail.get("locations"), list) else ""
            manager.upsert("character", summary["name"], int(detail.get("last_chapter", 0)), {
                "role_tier": detail.get("role_tier", "重要配角"), "status": detail.get("current_status", ""),
                "ability_level": detail.get("ability_level", ""), "location": location,
            }, source="bootstrap")
        cards = manager.get()
    return JSONResponse({"success": True, "cards": cards})


@app.get("/api/novels/{name}/state-proposals")
async def api_state_proposals(name: str, status: Optional[str] = "pending"):
    manager = CanonicalStateManager(get_novel_manager(name).path, logger, storage_mgr)
    return JSONResponse({"success": True, "items": manager.list(status or None)})


@app.post("/api/novels/{name}/state-proposals/{proposal_id}")
async def api_decide_state_proposal(name: str, proposal_id: str, request: Request):
    try:
        payload = await request.json()
        item = CanonicalStateManager(get_novel_manager(name).path, logger, storage_mgr).decide(proposal_id, bool(payload.get("accept")))
        return JSONResponse({"success": True, "item": item})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/history-revisions")
async def api_history_revisions(name: str):
    return JSONResponse({"success": True, "items": HistoryRevisionManager(get_novel_manager(name), logger, None, storage_mgr).list()})


@app.get("/api/novels/{name}/history-revisions/{revision_id}")
async def api_history_revision(name: str, revision_id: str):
    try:
        item = HistoryRevisionManager(get_novel_manager(name), logger, None, storage_mgr).get(revision_id)
        return JSONResponse({"success": True, "item": item})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/history-revisions/analyze")
async def api_analyze_history_revision(name: str, request: Request):
    try:
        payload = await request.json()
        manager = HistoryRevisionManager(get_novel_manager(name), logger, None, storage_mgr)
        source_chapter = int(payload.get("source_chapter", 0))
        old_fact = str(payload.get("old_fact", "")).strip()
        new_fact = str(payload.get("new_fact", "")).strip()
        if not old_fact or not new_fact or old_fact == new_fact:
            raise ValueError("旧事实和新事实必须非空且不同")
        if source_chapter < 1 or source_chapter > manager.nm.get_current_chapter():
            raise ValueError("事实发生章超出当前正文范围")
        impact = await asyncio.to_thread(manager.analyze, source_chapter, old_fact, new_fact)
        return JSONResponse({"success": True, "impact": impact})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/history-revisions/{revision_id}/preview")
async def api_preview_history_revision(name: str, revision_id: str):
    try:
        manager = HistoryRevisionManager(get_novel_manager(name), logger, None, storage_mgr)
        return JSONResponse({"success": True, "preview": manager.preview_candidates(revision_id)})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/history-revisions/{revision_id}/candidates/{chapter}")
async def api_update_history_candidate(name: str, revision_id: str, chapter: int, request: Request):
    try:
        payload = await request.json()
        manager = HistoryRevisionManager(get_novel_manager(name), logger, None, storage_mgr)
        item = await asyncio.to_thread(manager.update_candidate, revision_id, chapter, str(payload.get("content", "")))
        return JSONResponse({"success": True, "item": item})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/history-revisions")
async def api_create_history_revision(name: str, request: Request):
    try:
        payload = await request.json()
        nm = get_novel_manager(name)
        if task_store.active_for_novel(name):
            return JSONResponse({"success": False, "error": "该小说已有运行、排队或暂停任务，请先处理现有任务"}, status_code=409)
        manager = HistoryRevisionManager(nm, logger, None, storage_mgr)
        item = manager.create(
            int(payload.get("source_chapter", 0)), str(payload.get("old_fact", "")),
            str(payload.get("new_fact", "")), str(payload.get("instruction", "")),
            str(payload.get("mode", "minimal_patch")),
        )
        task_id = task_store.create_if_idle(
            name, "history_revision", f"历史修改：第{item['source_chapter']}章",
            {"revision_id": item["id"], "action": "rewrite", "auto_commit": bool(payload.get("auto_commit", True))},
            status="queued",
        )
        if not task_id:
            manager.abort(item["id"])
            return JSONResponse({"success": False, "error": "该小说已有运行、排队或暂停任务，请先处理现有任务"}, status_code=409)
        task_store.event(task_id, f"影响分析完成：前置{item['impact']['backward_count']}章，后续{item['impact']['forward_count']}章", 0, stage="history_analyzed")
        task_runner.notify()
        return JSONResponse({"success": True, "item": item, "task_id": task_id})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/history-revisions/{revision_id}/commit")
async def api_commit_history_revision(name: str, revision_id: str):
    try:
        manager = HistoryRevisionManager(get_novel_manager(name), logger, None, storage_mgr)
        item = manager.get(revision_id)
        if item.get("status") != "validated":
            raise ValueError("分支尚未验证通过")
        task_id = task_store.create_if_idle(
            name, "history_revision", f"提交历史修改：第{item['source_chapter']}章",
            {"revision_id": revision_id, "action": "commit"}, status="queued",
        )
        if not task_id:
            return JSONResponse({"success": False, "error": "该小说已有运行、排队或暂停任务，请先处理现有任务"}, status_code=409)
        task_runner.notify()
        return JSONResponse({"success": True, "task_id": task_id})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/history-revisions/{revision_id}/abort")
async def api_abort_history_revision(name: str, revision_id: str):
    try:
        item = HistoryRevisionManager(get_novel_manager(name), logger, None, storage_mgr).abort(revision_id)
        return JSONResponse({"success": True, "item": item})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/author-preferences")
async def api_author_preferences(name: str):
    return JSONResponse({"success": True, **AuthorPreferenceManager(get_novel_manager(name).path, logger, storage_mgr).get()})


@app.get("/api/prompt-snapshots")
async def api_prompt_snapshots():
    return JSONResponse({"success": True, "items": PromptSnapshotManager(config.STORAGE_ROOT, logger).list_tasks()})


@app.get("/api/prompt-snapshots/{task_type}/compare")
async def api_compare_prompt_snapshot(task_type: str):
    try:
        return JSONResponse({"success": True, "result": PromptSnapshotManager(config.STORAGE_ROOT, logger).compare(task_type)})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/prompt-snapshots/{task_type}/baseline")
async def api_set_prompt_baseline(task_type: str):
    try:
        return JSONResponse({"success": True, "baseline": PromptSnapshotManager(config.STORAGE_ROOT, logger).set_baseline(task_type)})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/state-cards/{kind}/{card_name}")
async def api_save_state_card(name: str, kind: str, card_name: str, request: Request):
    try:
        payload = await request.json()
        card = StateCardManager(get_novel_manager(name).path, logger, storage_mgr).upsert(kind, card_name, int(payload.get("chapter", 0)), payload.get("fields", {}), payload.get("evidence", ""), "manual")
        return JSONResponse({"success": True, "card": card})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/genre-packs")
async def api_genre_packs(name: str):
    return JSONResponse({"success": True, "packs": GenrePackManager(get_novel_manager(name).path, logger, storage_mgr).list()})


@app.post("/api/novels/{name}/genre-packs/{key}")
async def api_apply_genre_pack(name: str, key: str):
    try:
        return JSONResponse({"success": True, "pack": GenrePackManager(get_novel_manager(name).path, logger, storage_mgr).apply(key)})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/sandboxes")
async def api_sandboxes(name: str):
    return JSONResponse({"success": True, "items": StorySandboxManager(get_novel_manager(name).path, logger, storage_mgr).list()})


@app.post("/api/novels/{name}/sandboxes/generate")
async def api_generate_sandbox(name: str, request: Request):
    try:
        payload = await request.json()
        question = str(payload.get("question", "接下来有哪些合理且有创造力的推进方向？"))[:1000]
        nm = get_novel_manager(name)
        llm = await asyncio.to_thread(get_llm)
        context = await asyncio.to_thread(ContextManager(nm, logger, get_vs(), llm).build_context, None, question, None, False, "brief")
        system = BASE_SYSTEM + "\n你是剧情分支设计师。只输出JSON。三个方向必须因果成立、彼此明显不同，不能直接写入正式剧情。\n" + prompt_settings_manager.instruction("sandbox")
        prompt = f'''根据上下文回答问题：{question}\n返回{{"variants":[{{"title":"","direction":"","benefits":[""],"risks":[""],"required_setup":[""]}}]}}，必须正好三个候选。\n<context>\n{context}\n</context>'''
        raw = await asyncio.to_thread(llm.chat, system, prompt, 1800, task_type="planning")
        record = StorySandboxManager(nm.path, logger, storage_mgr).save_variants(nm.get_current_chapter(), question, parse_object(raw).get("variants", []))
        return JSONResponse({"success": True, "sandbox": record, "metrics": llm.last_metrics})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/sandboxes/{sandbox_id}/adopt/{variant_id}")
async def api_adopt_sandbox(name: str, sandbox_id: str, variant_id: str):
    try:
        nm = get_novel_manager(name)
        variant = StorySandboxManager(nm.path, logger, storage_mgr).adopt(sandbox_id, variant_id)
        nm.update_next_goal(variant["direction"])
        return JSONResponse({"success": True, "variant": variant})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/long-form-evaluation")
async def api_long_form_evaluation(name: str):
    manager = LongFormEvaluator(get_novel_manager(name).path, logger, storage_mgr)
    return JSONResponse({"success": True, "report": await asyncio.to_thread(manager.run)})


@app.get("/api/novels/{name}/planning-impacts")
async def api_planning_impacts(name: str):
    return JSONResponse({"success": True, "items": PlanningImpactManager(get_novel_manager(name).path, logger, storage_mgr).list()})


@app.post("/api/novels/{name}/planning-impacts/{impact_id}/resolve")
async def api_resolve_planning_impact(name: str, impact_id: str):
    try:
        item = PlanningImpactManager(get_novel_manager(name).path, logger, storage_mgr).resolve(impact_id)
        return JSONResponse({"success": True, "item": item})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/entities")
async def api_entities(name: str):
    try:
        nm = get_novel_manager(name)
        return JSONResponse({"success": True, "entities": EntityLedger(nm.path, logger, storage_mgr).get()})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/assets")
async def api_creative_assets(name: str):
    return JSONResponse({"success": True, "assets": CreativeAssetManager(get_novel_manager(name).path, logger).get()})


@app.post("/api/novels/{name}/assets/{kind}")
async def api_save_creative_asset(name: str, kind: str, request: Request):
    try:
        if kind not in ASSET_TYPES:
            raise ValueError("未知资产类型")
        item = CreativeAssetManager(get_novel_manager(name).path, logger).save(kind, await request.json())
        return JSONResponse({"success": True, "item": item})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.delete("/api/novels/{name}/assets/{kind}/{asset_id}")
async def api_delete_creative_asset(name: str, kind: str, asset_id: str):
    try:
        deleted = CreativeAssetManager(get_novel_manager(name).path, logger).delete(kind, asset_id)
        return JSONResponse({"success": True, "deleted": deleted})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/assets/research/upload")
async def api_upload_research(name: str, file: UploadFile = File(...)):
    try:
        raw = await file.read()
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError("研究资料最大5MB")
        if Path(file.filename or "").suffix.lower() not in {".txt", ".md"}:
            raise ValueError("研究资料目前支持TXT和Markdown")
        text = raw.decode("utf-8", errors="replace").strip()
        item = CreativeAssetManager(get_novel_manager(name).path, logger).save("research", {
            "name": Path(file.filename or "研究资料").stem, "description": text[:40000], "source_file": file.filename,
        })
        return JSONResponse({"success": True, "item": item})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/characters/{character_name}/voice")
async def api_character_voice(name: str, character_name: str, request: Request):
    try:
        payload = await request.json()
        voice = {key: str(payload.get(key, ""))[:1000] for key in ("sentence_style", "vocabulary", "addressing", "emotion", "forbidden")}
        character = CharacterManager(get_novel_manager(name).path, logger).update_character(character_name, voice_profile=voice)
        return JSONResponse({"success": True, "character": character})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/foreshadowing/{item_id}")
async def api_update_foreshadow(name: str, item_id: str, request: Request):
    try:
        item = ForeshadowManager(get_novel_manager(name).path, logger, storage_mgr).update(item_id, **(await request.json()))
        return JSONResponse({"success": True, "item": item})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.delete("/api/novels/{name}/foreshadowing/{item_id}")
async def api_delete_foreshadow(name: str, item_id: str):
    deleted = ForeshadowManager(get_novel_manager(name).path, logger, storage_mgr).delete(item_id)
    return JSONResponse({"success": True, "deleted": deleted})


@app.post("/api/novels/{name}/chapters/{chapter}/split")
async def api_split_latest_chapter(name: str, chapter: int, request: Request):
    try:
        nm = get_novel_manager(name)
        if chapter != nm.get_current_chapter():
            raise ValueError("为避免打乱后续编号，目前只允许拆分最新章节")
        payload = await request.json()
        content = get_chapter_manager(nm).read_chapter(chapter) or ""
        position = int(payload.get("position", len(content) // 2))
        manager = ChapterManager(nm, logger, None)
        result = await asyncio.to_thread(manager.split_latest_chapter, chapter, position)
        for changed in result["chapters"]:
            await asyncio.to_thread(_index_chapter, nm, changed, manager.read_chapter(changed) or "")
        return JSONResponse({"success": True, **result})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/chapters/{chapter}/merge-next")
async def api_merge_latest_chapters(name: str, chapter: int):
    try:
        nm = get_novel_manager(name)
        manager = ChapterManager(nm, logger, None)
        result = await asyncio.to_thread(manager.merge_latest_chapters, chapter)
        await asyncio.to_thread(_index_chapter, nm, chapter, manager.read_chapter(chapter) or "")
        await asyncio.to_thread(_delete_chapter_index, nm, chapter + 1)
        return JSONResponse({"success": True, "chapter": chapter, "result": result})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/chapters/{chapter}/dialogue")
async def api_extract_dialogue(name: str, chapter: int):
    content = get_chapter_manager(get_novel_manager(name)).read_chapter(chapter) or ""
    matches = re.findall(r"[“「『](.*?)[”」』]", content, re.S)
    return JSONResponse({"success": True, "dialogue": [item.strip() for item in matches if item.strip()], "count": len(matches)})


@app.post("/api/novels/{name}/chapters/batch-replace")
async def api_batch_replace(name: str, request: Request):
    try:
        payload = await request.json()
        old, new = str(payload.get("old", "")), str(payload.get("new", ""))
        if not old or old == new:
            raise ValueError("请输入不同的查找与替换内容")
        nm = get_novel_manager(name)
        manager = ChapterManager(nm, logger, None)
        chapters = payload.get("chapters") or list(range(1, nm.get_current_chapter() + 1))
        result = await asyncio.to_thread(manager.batch_replace, old, new, chapters)
        for chapter in result["changed_chapters"]:
            await asyncio.to_thread(_index_chapter, nm, chapter, manager.read_chapter(chapter) or "")
        return JSONResponse({"success": True, **result})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/cast-stats")
async def api_cast_stats(name: str):
    nm = get_novel_manager(name)
    characters = CharacterManager(nm.path, logger).list_characters()
    stats = {item["name"]: {"name": item["name"], "chapters": 0, "mentions": 0, "last_seen": 0} for item in characters}
    conflict_curve = []
    for chapter in range(1, nm.get_current_chapter() + 1):
        content = get_chapter_manager(nm).read_chapter(chapter) or ""
        for name_value, item in stats.items():
            count = content.count(name_value)
            if count:
                item["chapters"] += 1; item["mentions"] += count; item["last_seen"] = chapter
        compact = content.replace(" ", "")
        pressure = sum(compact.count(word) for word in ("危险", "死亡", "失败", "冲突", "追杀", "秘密", "真相", "代价", "失去"))
        dialogue_chars = sum(len(value) for value in re.findall(r"[“「『](.*?)[”」』]", content, re.S))
        conflict_curve.append({"chapter": chapter, "pressure": pressure, "dialogue_ratio": round(dialogue_chars / max(1, len(compact)), 3)})
    return JSONResponse({"success": True, "characters": list(stats.values()), "conflict_curve": conflict_curve})


@app.get("/api/novels/{name}/world-snapshot/{chapter}")
async def api_world_snapshot(name: str, chapter: int):
    nm = get_novel_manager(name)
    characters = []
    manager = CharacterManager(nm.path, logger)
    reviews = ChangeReviewManager(nm.path, logger, storage_mgr)
    for item in manager.list_characters():
        detail = manager.get_character(item["name"]) or {}
        locations = [entry for entry in detail.get("locations", []) if int(entry.get("chapter", 0)) <= chapter]
        abilities = [entry for entry in detail.get("ability_history", []) if int(entry.get("chapter", 0)) <= chapter]
        status = reviews.character_status_at(item["name"], chapter, detail.get("current_status", ""))
        characters.append({"name": item["name"], "status": status, "location": locations[-1]["location"] if locations else "未知", "ability": abilities[-1]["level"] if abilities else detail.get("ability_level", "")})
    facts = [item for item in FactManager(nm.path, logger, storage_mgr).recent(500) if int(item.get("chapter", 0)) <= chapter]
    timeline = [item for item in TimelineManager(nm.path, logger).get_recent_events(500) if int(item.get("chapter", 0)) <= chapter]
    timeline.sort(key=lambda item: (int(item.get("chapter", 0)), str(item.get("time", "")), str(item.get("id", ""))))
    assets = CreativeAssetManager(nm.path, logger).get()
    return JSONResponse({"success": True, "snapshot": {"chapter": chapter, "characters": characters, "facts": facts[-50:], "events": timeline[-30:], "locations": assets["locations"], "factions": assets["factions"], "items": assets["items"], "resources": assets["resources"], "conditions": assets["conditions"]}})


@app.post("/api/novels/{name}/ai-tool/{tool}")
async def api_novel_ai_tool(name: str, tool: str, request: Request):
    try:
        payload = await request.json()
        llm = await asyncio.to_thread(get_llm)
        if not llm:
            raise RuntimeError("模型未连接")
        nm = get_novel_manager(name)
        context = await asyncio.to_thread(
            ContextManager(nm, logger, None, llm).build_context, 9000,
        )
        instruction = str(payload.get("instruction", ""))[:5000]
        schemas = {
            "candidates": ('生成互不雷同的候选方案。只输出JSON。', '{"candidates":[{"title":"方案名","content":"候选正文或规划","strength":"优势","risk":"风险"}]}'),
            "rule-check": ('检查能力、技术、经济或行动是否违反已确认世界规则。只输出JSON。', '{"valid":true,"violations":[],"costs":[],"safe_revision":"可行修正"}'),
            "reverse-ending": ('从目标结局反向推导必要条件和章节节点。只输出JSON。', '{"ending_state":"","required_conditions":[],"reverse_milestones":[{"before_chapter":1,"condition":""}],"risks":[]}'),
            "titles": ('根据已有章节摘要生成统一风格标题。只输出JSON。', '{"titles":[{"chapter":1,"title":""}]}'),
            "promotion": ('生成适合小说发布的简介和宣传材料。只输出JSON。', '{"short_intro":"","long_intro":"","selling_points":[],"tags":[],"character_cards":[]}'),
            "action-director": ('规划动作场面的空间、参与者、动作顺序、伤势、资源消耗和位置连续性。只输出JSON。', '{"space":"","participants":[],"beats":[],"position_changes":[],"injuries":[],"resource_costs":[],"continuity_risks":[]}'),
            "chapter-mode": ('将指定章节方案转换为用户要求的调查、战斗、感情、日常、揭秘或高潮模式，不改变硬事实。只输出JSON。', '{"mode":"","kept_facts":[],"new_beats":[],"scene_plan":[],"ending_hook":""}'),
            "trim": ('定位重复解释、无效心理和冗长对白，提供局部精简，不重写无问题内容。只输出JSON。', '{"cuts":[{"quote":"","reason":"","replacement":""}],"estimated_reduction":0}'),
            "suspense": ('检查问题提出、部分回答、反转和完整揭露的分布。只输出JSON。', '{"open_questions":[],"reveals":[],"density_score":0,"gaps":[],"suggestions":[]}'),
            "climax": ('检查卷末高潮是否兑现本卷承诺、人物选择和代价。只输出JSON。', '{"score":0,"fulfilled":[],"missing":[],"new_unearned_elements":[],"repair_plan":[]}'),
            "viewpoint": ('从指定人物视角重构事件，保持客观事实和信息权限。只输出JSON。', '{"viewpoint":"","known_facts":[],"unknown_facts":[],"rewritten_scene":""}'),
            "exit-impact": ('分析人物死亡或退场对支线、伏笔、关系、资源和后续章节的影响。只输出JSON。', '{"affected_subplots":[],"affected_promises":[],"relationship_impacts":[],"broken_dependencies":[],"repair_options":[]}'),
            "goal-conflicts": ('比较主要人物当前欲望、恐惧和资源，找出可形成场景的冲突。只输出JSON。', '{"conflicts":[{"characters":[],"incompatible_goals":"","scene_opportunity":"","stakes":""}]}'),
            "reader-sim": ('分别模拟核心读者、普通读者和挑剔读者，评估期待、困惑和弃读风险。只输出JSON。', '{"personas":[{"reader":"","expectation":"","confusion":"","drop_risk":0,"feedback":""}],"next_click_score":0}'),
            "recap": ('生成不剧透未来的前情提要或卷首回顾。只输出JSON。', '{"short_recap":"","long_recap":"","key_names":[],"open_questions":[]}'),
            "understanding": ('测试读者能否理解规则、动机和当前局势，指出表达缺口。只输出JSON。', '{"questions":[{"question":"","expected_answer":"","evidence":""}],"likely_misunderstandings":[],"clarity_score":0}'),
            "audiobook": ('把内容整理为有声书脚本，区分旁白、人物、停顿和情绪。只输出JSON。', '{"segments":[{"speaker":"旁白","emotion":"","pause_ms":0,"text":""}]}'),
            "comic": ('把章节转换为漫画页与分镜，不增加硬事实。只输出JSON。', '{"pages":[{"page":1,"panels":[{"shot":"","visual":"","dialogue":"","caption":""}]}]}'),
            "translate": ('按目标语言翻译，严格保持名词词典和人物语气。只输出JSON。', '{"language":"","translated_text":"","term_map":[]}'),
            "visual": ('为封面、人物立绘或场景图生成可用于图像模型的提示词，不生成图片。只输出JSON。', '{"subject":"","positive_prompt":"","negative_prompt":"","composition":"","palette":""}'),
            "platform-format": ('根据目标平台生成标题、段落、简介和发布格式建议。只输出JSON。', '{"platform":"","chapter_title_format":"","paragraph_rules":[],"intro":"","tags":[]}'),
        }
        if tool not in schemas:
            raise ValueError("未知AI工具")
        role, schema = schemas[tool]
        raw = await asyncio.to_thread(llm.chat, BASE_SYSTEM + "\n" + role, f"<context>\n{context}\n</context>\n用户要求：{instruction}\n返回格式：{schema}", 3600, task_type="structured")
        return JSONResponse({"success": True, "result": parse_object(raw), "metrics": llm.last_metrics})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/batch-review")
async def api_batch_review(name: str, request: Request):
    try:
        payload = await request.json()
        chapters = [int(item) for item in payload.get("chapters", [])][:5]
        if not chapters:
            raise ValueError("请选择1至5章")
        nm = get_novel_manager(name); llm = await asyncio.to_thread(get_llm); reports = []
        for chapter in chapters:
            content = get_chapter_manager(nm).read_chapter(chapter) or ""
            if not content:
                continue
            raw = await asyncio.to_thread(llm.chat, BASE_SYSTEM + "\n你是小说审稿编辑，只输出JSON。", f'审查第{chapter}章：{{"chapter":{chapter},"score":0,"issues":[{{"type":"","quote":"","reason":"","replacement":""}}]}}\n<chapter>{content[:14000]}</chapter>', 2200, task_type="structured")
            reports.append(parse_object(raw))
        return JSONResponse({"success": True, "reports": reports, "metrics": llm.last_metrics})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


# ---- 流式生成 API ----

def _build_writing_prompt(name: str, context: str = "", target_words: int = 5000, continuation: bool = False) -> tuple:
    return chapter_prompts(name, context, target_words, continuation)


@app.post("/api/novels/{name}/generate")
async def api_generate_chapter(
    name: str, context: str = Form(""), target_words: int = Form(5000),
    temperature: float = Form(0.7), top_p: float = Form(0.8),
    top_k: int = Form(20), repeat_penalty: float = Form(1.08),
    presence_penalty: float = Form(0.0), min_p: float = Form(0.0),
    max_tokens: int = Form(0), continuation: bool = Form(False),
):
    if task_store.active_for_novel(name):
        return JSONResponse({"success": False, "error": "该小说已有运行、排队或暂停任务，请先处理现有任务"}, status_code=409)
    llm = await asyncio.to_thread(get_llm)
    if not llm:
        return JSONResponse({"success": False, "error": "模型服务未连接"}, status_code=503)
    nm = get_novel_manager(name)
    chapter_number = nm.get_current_chapter() + 1
    target_chapters = nm.get_state().get("target_chapters", 0)
    if target_chapters and chapter_number > target_chapters:
        return JSONResponse({"success": False, "error": f"已经达到目标章节数 {target_chapters}，请先在故事设定中调整目标"}, status_code=409)
    tokens = max_tokens if max_tokens > 0 else min(int(target_words / 1.8) + 1000, MODEL_CONFIG["max_output_tokens"])
    creativity = settings_manager.get().get("creativity_mode", "balanced")
    if creativity not in {"stable", "balanced", "open"}:
        creativity = "balanced"
    temperature = max(0.0, min(2.0, float(temperature)))
    top_p = max(0.05, min(1.0, float(top_p)))
    top_k = max(0, min(200, int(top_k)))
    repeat_penalty = max(0.8, min(2.0, float(repeat_penalty)))
    presence_penalty = max(-2.0, min(2.0, float(presence_penalty)))
    min_p = max(0.0, min(1.0, float(min_p)))
    creativity_parameters = {
        "stable": (0.58, 0.78, 1.14),
        "balanced": (temperature, top_p, repeat_penalty),
        "open": (max(0.82, temperature), max(0.9, top_p), min(1.1, repeat_penalty)),
    }
    temperature, top_p, repeat_penalty = creativity_parameters[creativity]
    creativity_instruction = {
        "stable": "【创意档位】稳健：严格完成节纲目标，不新增改变主线的重大设定。",
        "balanced": "【创意档位】均衡：约70%推进目标、20%人物生活与关系、10%铺垫或意外，但所有细节必须产生后续价值。",
        "open": "【创意档位】开放：允许自然支线、误会、小人物事件与延迟兑现，但不得破坏既有事实和卷纲终点。",
    }[creativity]
    context = context + "\n\n" + creativity_instruction
    system, prompt = _build_writing_prompt(name, context, target_words, continuation)
    task_id = task_store.create_if_idle(name, "chapter", f"生成第{chapter_number}章", {
        "target_words": target_words, "continuation": continuation,
    })
    if not task_id:
        return JSONResponse({"success": False, "error": "该小说已有运行、排队或暂停任务，请先处理现有任务"}, status_code=409)

    async def event_gen():
        try:
            context_manager = ContextManager(nm, logger, None, llm)
            generation_service = ChapterGenerationService(
                nm, llm, context_manager, storage_mgr, MODEL_CONFIG,
                _ensure_chapter_brief, _chapter_plan_fingerprint,
                _load_cached_chapter_plan, _save_cached_chapter_plan, _confirmed_chapter_plan,
            )
            task_store.event(task_id, "正在生成章前提要", 5, stage="brief")
            yield f"data: {json.dumps({'type': 'task', 'task_id': task_id}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'stage', 'message': '正在确认本章提要', 'progress': 5}, ensure_ascii=False)}\n\n"
            brief_context = await asyncio.to_thread(context_manager.build_context, None, None, None, False, "brief")
            brief = await asyncio.to_thread(_ensure_chapter_brief, nm, llm, brief_context, chapter_number)
            writing_base_context = context + "\n\n" + render_chapter_brief(brief)
            brief_event = {"type": "stage", "message": f"章前提要已确认：{brief.get('title') or '本章'}", "progress": 10, "brief": brief}
            yield f"data: {json.dumps(brief_event, ensure_ascii=False)}\n\n"
            task_store.event(task_id, "正在规划本章结构", 12, stage="planning")
            plan_fingerprint = _chapter_plan_fingerprint(nm, chapter_number, brief, target_words, continuation)
            chapter_plan, plan_source = generation_service.resolve_plan(chapter_number, plan_fingerprint)
            confirmed_plan = plan_source == "confirmed"
            try:
                if chapter_plan is None:
                    planning_context = await asyncio.to_thread(context_manager.build_context, None, None, None, False, "planning")
                    planning_context += "\n\n" + render_chapter_brief(brief)
                    plan_system, plan_prompt = chapter_plan_prompts(name, planning_context, continuation, target_words)
                    plan_task = asyncio.create_task(asyncio.to_thread(
                        llm.chat, plan_system, plan_prompt,
                        MODEL_CONFIG.get("analysis_max_tokens", 1536), task_type="planning",
                    ))
                    waited = 0
                    while not plan_task.done():
                        done, _ = await asyncio.wait({plan_task}, timeout=5)
                        if done:
                            break
                        waited += 5
                        yield f"data: {json.dumps({'type': 'heartbeat', 'message': f'章节规划中，已等待 {waited} 秒', 'progress': min(22, 8 + waited)}, ensure_ascii=False)}\n\n"
                    raw_plan = await plan_task
                    chapter_plan = generation_service.accept_generated_plan(
                        chapter_number, brief, target_words, continuation,
                        plan_fingerprint, parse_object(raw_plan),
                    )
                    plan_message = f"规划完成：{len(chapter_plan['beats'])} 个剧情节拍"
                elif confirmed_plan:
                    generation_service.validate_plan_artifact(chapter_plan, brief)
                    plan_message = f"采用人工确认场景细纲：{len(chapter_plan['scenes'])} 个场景"
                else:
                    generation_service.validate_plan_artifact(chapter_plan, brief)
                    plan_message = f"复用已验证规划：{len(chapter_plan['beats'])} 个剧情节拍"
                writing_context = writing_base_context + "\n\n" + render_chapter_plan(chapter_plan)
                beat_count = len(chapter_plan["beats"])
                plan_event = {"type": "stage", "message": plan_message, "progress": 24, "plan": chapter_plan, "cached": plan_message.startswith("复用")}
                yield f"data: {json.dumps(plan_event, ensure_ascii=False)}\n\n"
            except (StalePlanningError, PlanningArtifactError):
                raise
            except Exception as plan_error:
                writing_context = writing_base_context
                yield f"data: {json.dumps({'type': 'warning', 'message': f'章节规划不可用，将按原目标写作：{plan_error}', 'progress': 24}, ensure_ascii=False)}\n\n"
            system, prompt = _build_writing_prompt(name, writing_context, target_words, continuation)
            planning_epoch = context_manager.last_build_stats.get("planning_epoch", "")
            yield f"data: {json.dumps({'type': 'stage', 'message': '规划已交给正文模型，开始写作', 'progress': 26}, ensure_ascii=False)}\n\n"
            task_store.event(task_id, "规划完成，开始生成正文", 26, stage="writing")
            generated_parts = []
            generation_metrics = []
            stream = llm.chat_stream(
                system, prompt, max_tokens=tokens,
                temperature=temperature, top_p=top_p, top_k=top_k,
                repeat_penalty=repeat_penalty, presence_penalty=presence_penalty, min_p=min_p,
                task_type="prose",
            )
            async for chunk in _iterate_blocking_stream(stream):
                generated_parts.append(chunk)
                yield f"data: {json.dumps({'token': chunk})}\n\n"
            generation_metrics.append(dict(llm.last_metrics))
            content = "".join(generated_parts)
            completion_passes = 0
            while len(re.sub(r"\s", "", content)) < int(target_words * 0.9) and completion_passes < 2:
                completion_passes += 1
                current_words = len(re.sub(r"\s", "", content))
                remaining = max(300, target_words - current_words)
                message = f"正文仅{current_words}字，正在第{completion_passes}次补写约{remaining}字"
                task_store.event(task_id, message, 88 + completion_passes * 3, stage="completion")
                yield f"data: {json.dumps({'type': 'stage', 'message': message, 'progress': 88 + completion_passes * 3}, ensure_ascii=False)}\n\n"
                completion_system, completion_prompt = chapter_completion_prompts(
                    name, content, target_words, render_chapter_plan(chapter_plan) if 'chapter_plan' in locals() else writing_base_context,
                )
                addition_parts = []
                stream = llm.chat_stream(
                    completion_system, completion_prompt,
                    max_tokens=min(int(remaining / 1.8) + 900, MODEL_CONFIG["max_output_tokens"]),
                    temperature=temperature, top_p=top_p, top_k=top_k,
                    repeat_penalty=repeat_penalty, presence_penalty=presence_penalty, min_p=min_p,
                    task_type="prose",
                )
                async for chunk in _iterate_blocking_stream(stream):
                    addition_parts.append(chunk)
                    yield f"data: {json.dumps({'token': chunk})}\n\n"
                generation_metrics.append(dict(llm.last_metrics))
                previous = content
                content = merge_chapter_continuation(content, "".join(addition_parts))
                yield f"data: {json.dumps({'type': 'replace', 'content': content}, ensure_ascii=False)}\n\n"
                if len(content) <= len(previous) + 50:
                    break
            gate = chapter_quality_gate(content, target_words)
            warnings = gate["warnings"]
            word_count = gate["word_count"]
            task_store.event(task_id, "正文生成完成，正在执行质量检查", 96, stage="quality")
            gate["auto_continuations"] = completion_passes
            yield f"data: {json.dumps({'quality': gate}, ensure_ascii=False)}\n\n"
            aggregate_metrics = _aggregate_generation_metrics(generation_metrics)
            planning_is_stale = generation_service.is_planning_stale(
                chapter_number, brief, target_words, continuation, plan_fingerprint,
            )
            turn_metadata = generation_service.turn_metadata(
                task_id, aggregate_metrics, planning_epoch, plan_fingerprint, planning_is_stale,
                PromptSnapshotManager(config.STORAGE_ROOT).latest_reference("prose"),
                generation_profile={
                    "model_name": MODEL_CONFIG.get("model_name", ""),
                    "context_window": MODEL_CONFIG.get("context_window", 0),
                    "max_output_tokens": MODEL_CONFIG.get("max_output_tokens", 0),
                    "temperature": temperature, "top_p": top_p, "top_k": top_k,
                    "repeat_penalty": repeat_penalty, "presence_penalty": presence_penalty,
                    "min_p": min_p, "seed": aggregate_metrics.get("seed"),
                },
            )
            turn = await asyncio.to_thread(
                get_turn_engine(nm).save_draft, chapter_number, content, target_words,
                "stream", turn_metadata,
            )
            if planning_is_stale:
                message = "正文生成期间上游规划发生变化，草稿已保留但禁止自动提交"
                task_store.event(task_id, message, 97, level="warning", stage="planning_stale")
                yield f"data: {json.dumps({'type': 'warning', 'message': message, 'progress': 97}, ensure_ascii=False)}\n\n"
            preview = await asyncio.to_thread(get_turn_engine(nm).preview_changes, turn["id"])
            logic_conflicts = preview.get("fact_conflicts", [])
            if logic_conflicts:
                warnings.extend("硬事实冲突：" + item["message"] for item in logic_conflicts)
                gate["warnings"] = list(dict.fromkeys(warnings))
                gate["status"] = "FAIL"
                message = f"提交前发现{len(logic_conflicts)}个硬事实变化，已保留为草稿等待确认"
                task_store.event(task_id, message, 97, level="warning", stage="logic_preflight")
                yield f"data: {json.dumps({'type': 'warning', 'message': message, 'progress': 97, 'fact_conflicts': logic_conflicts}, ensure_ascii=False)}\n\n"
            governance_warnings = _governance_preview_warnings(preview)
            if governance_warnings:
                warnings.extend(governance_warnings)
                gate["warnings"] = list(dict.fromkeys(warnings))
                gate["status"] = "FAIL"
                message = f"提交前发现{len(governance_warnings)}个设定、时空或人物决策阻断项，已保留草稿等待确认"
                task_store.event(task_id, message, 97, level="warning", stage="governance_preflight")
                yield f"data: {json.dumps({'type': 'warning', 'message': message, 'progress': 97, 'governance_warnings': governance_warnings}, ensure_ascii=False)}\n\n"
            commit_mode = settings_manager.get().get("chapter_commit_mode", "balanced")
            auto_commit = not planning_is_stale and ((commit_mode == "balanced" and gate["status"] == "PASS") or (
                commit_mode == "automatic" and gate["status"] != "FAIL"
            ))
            committed = False
            if auto_commit:
                await asyncio.to_thread(
                    get_turn_engine(nm).commit, turn["id"],
                    lambda number, text: _index_chapter(nm, number, text), False, False,
                )
                committed = True
                yield f"data: {json.dumps({'type': 'committed', 'turn_id': turn['id'], 'message': f'章节已按{commit_mode}策略自动提交正史'}, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'draft', 'turn_id': turn['id'], 'status': turn['status'], 'message': '正文已进入草稿回合，尚未写入正史'}, ensure_ascii=False)}\n\n"
            task_store.finish(task_id, {"words": word_count, "quality": gate, "turn_id": turn["id"], "committed": committed})
            performance_manager.record(aggregate_metrics, "chapter")
            history = [item for item in performance_manager.get()["history"][:-1] if item.get("label") in {"chapter", "benchmark"} and item.get("tokens_per_second")]
            if history:
                baseline = sum(float(item["tokens_per_second"]) for item in history[-10:]) / min(10, len(history))
                threshold = float(settings_manager.get().get("speed_warning_ratio", 0.7))
                current_speed = float(aggregate_metrics.get("tokens_per_second", 0))
                if current_speed and current_speed < baseline * threshold:
                    yield f"data: {json.dumps({'type': 'warning', 'message': f'速度异常：当前 {current_speed:.2f} token/s，近期热态均速 {baseline:.2f} token/s；请检查后台占用或长上下文预填充'}, ensure_ascii=False)}\n\n"
            metrics_message = (
                f"性能 · {aggregate_metrics.get('calls', 1)}次调用 · 首Token {aggregate_metrics.get('time_to_first_token', 0)}秒 · "
                f"正文 {aggregate_metrics.get('tokens_per_second', 0)} token/s · "
                f"端到端 {aggregate_metrics.get('end_to_end_tokens_per_second', 0)} token/s · "
                f"预填充 {aggregate_metrics.get('prompt_tokens_per_second', 0)} token/s · "
                f"缓存 {aggregate_metrics.get('cached_prompt_tokens', 0)} · "
                f"输入 {aggregate_metrics.get('prompt_tokens', 0)} / 输出 {aggregate_metrics.get('completion_tokens', 0)} tokens"
            )
            yield f"data: {json.dumps({'type': 'metrics', 'message': metrics_message, 'metrics': aggregate_metrics, 'progress': 100}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except asyncio.CancelledError:
            task_store.cancel(task_id)
            raise
        except Exception as e:
            task_store.fail(task_id, str(e))
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/api/generate-stream")
async def api_generate_stream(
    system: str = Form(""), prompt: str = Form(...),
    max_tokens: int = Form(2048), temperature: float = Form(0.7),
    top_p: float = Form(0.8), top_k: int = Form(20),
    repeat_penalty: float = Form(1.12), presence_penalty: float = Form(1.5),
    min_p: float = Form(0.0),
):
    llm = await asyncio.to_thread(get_llm)
    if not llm:
        return JSONResponse({"success": False, "error": "模型服务未连接"}, status_code=503)

    async def event_gen():
        try:
            stream = llm.chat_stream(
                system or BASE_SYSTEM, prompt,
                max_tokens=max_tokens, temperature=temperature,
                top_p=top_p, top_k=top_k, repeat_penalty=repeat_penalty,
                presence_penalty=presence_penalty, min_p=min_p,
            )
            async for chunk in _iterate_blocking_stream(stream):
                yield f"data: {json.dumps({'token': chunk})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/api/ai-generate")
async def api_ai_generate(
    system: str = Form(""), prompt: str = Form(...),
    max_tokens: int = Form(1024), temperature: float = Form(0.7),
):
    llm = await asyncio.to_thread(get_llm)
    if not llm:
        return JSONResponse({"success": False, "error": "模型服务未连接"}, status_code=503)
    try:
        result = await asyncio.to_thread(
            llm.chat, system or BASE_SYSTEM, prompt, max_tokens, temperature
        )
        return JSONResponse({"success": True, "content": result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/assist/title")
async def api_assist_title(genre: str = Form(""), idea: str = Form("")):
    llm = await asyncio.to_thread(get_llm)
    if not llm:
        return JSONResponse({"success": False, "error": "模型服务未连接"}, status_code=503)
    try:
        system, prompt = title_prompts(genre, idea)
        content = await asyncio.to_thread(llm.chat, system, prompt, 80, task_type="planning")
        title = re.sub(r"[《》“”\"'`#*]", "", content).splitlines()[0].strip()
        if not title or len(title) > 20:
            raise ValueError("模型返回的书名不合格")
        return JSONResponse({"success": True, "content": title, "metrics": llm.last_metrics})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/assist/source-field/{field}")
async def api_assist_source_field(field: str, request: Request):
    specs = {
        "name": "2至10个汉字的小说书名，不加书名号",
        "genre": "明确的题材与可选子类型，例如都市悬疑/近未来社会派悬疑",
        "description": "100至300字核心创意，说明独特设定、核心冲突和吸引点",
        "notes": "尚未被结构化字段覆盖、但会影响创作的补充要求；没有则返回空字符串",
        "setting": "时代、主要舞台、社会环境和日常生活形态，80至200字",
        "viewpoint": "叙事人称、视角人物范围和叙述距离，30至100字",
        "protagonist": "主角开局身份、现实处境、已有能力、性格底色和关键缺陷，100至250字",
        "external_goal": "可以在故事中明确判断成败的具体外在目标，50至150字",
        "internal_need": "主角尚未承认、但必须改变的内在缺口，50至150字",
        "opposition": "主要对手或系统性阻力、其合理动机和压制手段，100至250字",
        "stakes": "主角失败在个人、关系和世界层面的具体损失，80至180字",
        "inciting_incident": "开篇发生、迫使主角离开原有生活且无法轻易退出的具体事件，100至220字",
        "world_rules": "世界、能力、技术或制度能做什么、不能做什么的硬规则，120至300字",
        "power_cost": "主角优势、资源来源、使用限制、代价与反制方式，100至250字",
        "core_question": "贯穿长篇、持续驱动读者阅读的核心问题，50至150字",
        "milestones": "3至6个必须经过的关键事件或状态变化，不展开完整大纲",
        "must_have": "根据现有创意推荐必须保留的体验、关系或元素，3至8项",
        "prohibited": "根据题材列出容易破坏本故事的套路或设定，3至8项",
        "audience": "目标读者画像和希望产生的主要情绪体验，30至100字",
        "pacing": "开篇、中段、卷末的节奏倾向，30至100字",
        "relationship_line": "感情线、友情、敌对关系和群像比重倾向，30至120字",
        "ending_preference": "主角、核心关系和世界在结局时达到的具体状态，80至180字",
        "target_chapters": "只返回一个5至1000的整数，依据题材复杂度和现有关键节点估算",
        "foundation_premise": "依据源数据写成150至350字明确故事前提，包含主角、触发事件、目标、阻力和代价",
        "foundation_theme": "核心主题、价值冲突及其在人物选择中的体现，80至180字",
        "foundation_world": "具体世界观：时代、空间、社会、势力和普通生活，300至800字",
        "foundation_rules": "力量、技术、制度的边界、代价、稀缺性和不可违反事项，250至600字",
        "foundation_style": "可直接执行的叙事人称、句法、节奏、对白和描写规范，200至500字",
        "foundation_ending": "主角、关系、核心矛盾和世界在结局时的状态，150至350字",
        "structure_outline": "依据已确认设定和人物写全书因果主线、关键转折、高潮和结局，600至1500字",
        "rolling_plan": "后续章节如何按卷纲滚动生成提要、处理偏航和更新账本的规则，150至350字",
    }
    try:
        if field not in specs:
            raise ValueError("不支持的源数据字段")
        payload = await request.json()
        context = {key: str(value)[:3000] for key, value in payload.items() if key != field and str(value).strip()}
        llm = await asyncio.to_thread(get_llm)
        if not llm:
            raise RuntimeError("模型未连接")
        system = BASE_SYSTEM + "\n你负责补全小说创作简报中的单个字段。只输出字段内容，不解释、不加标题、不输出JSON。已有字段是约束，不得改写其它字段。"
        prompt = f"已有创作简报：\n{json.dumps(context, ensure_ascii=False)}\n\n当前只补全字段 {field}。要求：{specs[field]}"
        value = (await asyncio.to_thread(llm.chat, system, prompt, 900, task_type="planning")).strip()
        if field == "target_chapters":
            match = re.search(r"\d+", value)
            value = str(max(5, min(1000, int(match.group()) if match else 100)))
        return JSONResponse({"success": True, "field": field, "value": value, "metrics": llm.last_metrics})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/assist/source-all")
async def api_assist_source_all(request: Request):
    try:
        existing = await request.json()
        existing = {key: str(value)[:4000] for key, value in existing.items() if str(value).strip()}
        llm = await asyncio.to_thread(get_llm)
        if not llm:
            raise RuntimeError("模型未连接")
        system = BASE_SYSTEM + """
你负责生成一份可直接驱动长篇小说策划的完整创作简报。只输出单个JSON对象，不使用Markdown。
用户已填写的字段是硬约束，必须原样保留其核心含义；只补全缺失字段并让各字段形成因果闭环。
不要用“神秘力量、巨大阴谋、命运齿轮”等空泛表达，每一项必须具体、可验证、能影响人物选择。"""
        schema = {
            "name": "2至10字书名", "genre": "题材与子类型", "target_chapters": 100,
            "description": "核心创意与独特吸引点", "notes": "其它必要补充",
            "setting": "时代、舞台、社会环境", "viewpoint": "人称、视角范围与距离",
            "protagonist": "主角开局身份、处境、能力与缺陷", "external_goal": "可验证成败的外在目标",
            "internal_need": "主角尚未承认的内在缺口", "opposition": "主要对手、动机与阻力手段",
            "stakes": "个人、关系和世界层面的失败代价", "inciting_incident": "不可轻易退出的开篇触发事件",
            "world_rules": "世界或能力硬规则与边界", "power_cost": "优势、限制、代价与反制",
            "core_question": "贯穿长篇的核心问题", "milestones": "3至6个必要关键节点",
            "must_have": "必须出现或保留的3至8项", "prohibited": "绝对禁止的3至8项",
            "audience": "目标读者与情绪体验", "pacing": "开篇、中段、卷末节奏",
            "relationship_line": "感情、友情、敌对和群像倾向", "ending_preference": "主角、关系与世界最终状态",
            "story_seed": {
                "logline": "一句话故事前提", "protagonist_engine": "主角开局、目标和内在缺口",
                "conflict_engine": "对手、阻力、触发事件和失败代价", "world_contract": "舞台、硬规则和能力代价",
                "long_question": "长期问题", "milestones": ["少量必要节点"],
                "experience_contract": "读者体验、节奏、关系线、视角", "ending_state": "最终状态",
                "must_keep": ["硬约束"], "must_avoid": ["禁止项"], "open_space": ["允许发挥项"],
            },
        }
        prompt = f"已有内容：\n{json.dumps(existing, ensure_ascii=False)}\n\n严格返回并完整填写以下JSON：\n{json.dumps(schema, ensure_ascii=False)}"
        raw = await asyncio.to_thread(llm.chat, system, prompt, 3600, task_type="planning")
        result = parse_object(raw)
        for key, value in existing.items():
            if key in schema:
                result[key] = value
        chapter_match = re.search(r"\d+", str(result.get("target_chapters", 100)))
        result["target_chapters"] = max(5, min(1000, int(chapter_match.group()) if chapter_match else 100))
        if not isinstance(result.get("story_seed"), dict):
            result["story_seed"] = {
                "logline": str(result.get("description", "")),
                "protagonist_engine": "；".join(filter(None, [str(result.get("protagonist", "")), str(result.get("external_goal", "")), str(result.get("internal_need", ""))])),
                "conflict_engine": "；".join(filter(None, [str(result.get("opposition", "")), str(result.get("inciting_incident", "")), str(result.get("stakes", ""))])),
                "world_contract": "；".join(filter(None, [str(result.get("setting", "")), str(result.get("world_rules", "")), str(result.get("power_cost", ""))])),
                "long_question": str(result.get("core_question", "")),
                "milestones": result.get("milestones", []),
                "experience_contract": "；".join(filter(None, [str(result.get("audience", "")), str(result.get("pacing", "")), str(result.get("relationship_line", "")), str(result.get("viewpoint", ""))])),
                "ending_state": str(result.get("ending_preference", "")),
                "must_keep": result.get("must_have", []), "must_avoid": result.get("prohibited", []),
                "open_space": ["未在故事种子中确定的次要人物、地点细节与具体场景过程"],
            }
        seed = result["story_seed"]
        optional_defaults = {
            "name": existing.get("name", "未命名故事"), "genre": existing.get("genre", "类型小说"),
            "description": seed.get("logline", "围绕主角目标、阻力与代价展开的长篇故事"),
            "setting": seed.get("world_contract", "与核心冲突直接相关的时代、社会与主要舞台"),
            "protagonist": seed.get("protagonist_engine", "拥有明确目标、缺陷与现实处境的主角"),
            "external_goal": seed.get("protagonist_engine", "完成可以明确判断成败的主线目标"),
            "opposition": seed.get("conflict_engine", "具有合理动机并持续制造阻力的对手"),
            "stakes": seed.get("conflict_engine", "失败会造成个人、关系与局势层面的具体损失"),
            "inciting_incident": seed.get("conflict_engine", "迫使主角行动且无法轻易退出的事件"),
            "notes": "无额外补充", "viewpoint": "第三人称有限视角，主要跟随主角",
            "internal_need": seed.get("protagonist_engine", "在行动中认识并修正自身缺口"),
            "world_rules": seed.get("world_contract", "世界规则必须存在边界与代价"),
            "power_cost": seed.get("world_contract", "优势必须有消耗、限制与反制方式"),
            "core_question": seed.get("long_question", "主角能否完成目标并承担代价"),
            "milestones": seed.get("milestones", ["建立目标", "遭遇重大代价", "完成最终选择"]),
            "must_have": seed.get("must_keep", ["主角目标与失败代价持续有效"]),
            "prohibited": seed.get("must_avoid", ["无代价逆转", "机械降神"]),
            "audience": "偏好长篇连续性、人物选择与因果推进的成年读者",
            "pacing": "开篇明确触发事件，中段交替推进与缓冲，卷末兑现阶段承诺",
            "relationship_line": "关系变化服务人物选择与主线，不强行增加感情线",
            "ending_preference": seed.get("ending_state", "主线矛盾得到明确结果，选择产生不可逆后果"),
        }
        for key, value in optional_defaults.items():
            if not str(result.get(key, "")).strip():
                result[key] = value
        required_keys = ("name", "genre", "description", "setting", "protagonist", "external_goal", "opposition", "stakes", "inciting_incident", "story_seed")
        missing = [key for key in required_keys if not str(result.get(key, "")).strip()]
        if missing:
            raise ValueError("完整简报仍缺少字段：" + "、".join(missing))
        return JSONResponse({"success": True, "source": result, "metrics": llm.last_metrics})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/assist/source-summary")
async def api_assist_source_summary(request: Request):
    """把详细访谈压缩为后续阶段唯一使用的、可编辑的故事种子。"""
    try:
        source = await request.json()
        safe_source = {key: value for key, value in source.items() if key not in {"style_reference", "accepted_plan", "story_seed"}}
        llm = await asyncio.to_thread(get_llm)
        if not llm:
            raise RuntimeError("模型未连接")
        system = BASE_SYSTEM + """
你是小说项目主编。把详细创作访谈压缩成一份无重复、无矛盾、可直接驱动后续策划的故事种子。只输出JSON。
不得增加用户没有暗示的新题材、新金手指或重大反转；冲突字段不一致时，优先保留更具体、可验证的描述。
硬约束必须逐项保留；允许发挥项必须明确标出，不能让后续模型把空白误认为确定设定。"""
        schema = {
            "logline": "一句话故事前提", "protagonist_engine": "主角开局、外在目标、内在缺口",
            "conflict_engine": "对手、持续阻力、触发事件和失败代价", "world_contract": "舞台、硬规则、能力代价",
            "long_question": "长期问题或核心矛盾", "milestones": ["少量必要节点"],
            "experience_contract": "读者、节奏、关系线、视角和文风方向", "ending_state": "最终状态",
            "must_keep": ["硬约束"], "must_avoid": ["禁止项"], "open_space": ["仍未确定、允许后续合理发挥的事项"],
        }
        raw = await asyncio.to_thread(llm.chat, system, f"详细源数据：\n{json.dumps(safe_source, ensure_ascii=False)}\n\n返回：{json.dumps(schema, ensure_ascii=False)}", 3000, task_type="structured")
        seed = parse_object(raw)
        required = ("logline", "protagonist_engine", "conflict_engine", "world_contract", "ending_state")
        missing = [key for key in required if not str(seed.get(key, "")).strip()]
        if missing:
            raise ValueError("故事种子缺少：" + "、".join(missing))
        return JSONResponse({"success": True, "story_seed": seed, "metrics": llm.last_metrics})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/planning/stage/{stage}")
async def api_planning_stage(stage: str, request: Request):
    try:
        payload = await request.json()
        source = payload.get("source", {})
        accepted = payload.get("accepted", {})
        target_chapters = max(5, min(1000, int(source.get("target_chapters", 100))))
        llm = await asyncio.to_thread(get_llm)
        if not llm:
            return JSONResponse({"success": False, "error": "模型服务未连接"}, status_code=503)
        style_profile = source.get("style_profile", {})
        if stage == "foundation" and source.get("style_reference") and not style_profile:
            style_system, style_prompt = style_analysis_prompts(str(source["style_reference"]))
            style_raw = await asyncio.to_thread(llm.chat, style_system, style_prompt, 2200, task_type="structured")
            style_profile = validate_style_analysis(parse_object(style_raw))
        safe_source = {key: value for key, value in source.items() if key != "style_reference"}
        safe_source["style_profile"] = style_profile
        system, prompt = staged_planning_prompts(stage, safe_source, accepted)
        max_tokens = 3200 if stage == "structure" else 3500 if stage == "opening" else 2600
        raw = await asyncio.to_thread(llm.chat, system, prompt, max_tokens, task_type="planning")
        try:
            parsed = parse_object(raw)
        except Exception:
            parsed = {}
        if stage == "foundation":
            required_foundation = ("premise", "world", "rules", "style", "ending_direction")
            if any(not str(parsed.get(key, "")).strip() for key in required_foundation):
                retry_prompt = prompt + "\n\n上一次基础设定有字段缺失。只输出完整JSON，必须同时包含premise、theme、world、rules、style、ending_direction，所有字段非空。"
                retry_raw = await asyncio.to_thread(llm.chat, system, retry_prompt, 3200, task_type="structured")
                try:
                    retry_data = parse_object(retry_raw)
                    for key, value in retry_data.items():
                        if value and not parsed.get(key):
                            parsed[key] = value
                except Exception:
                    pass
            seed = safe_source.get("story_seed", {}) if isinstance(safe_source.get("story_seed"), dict) else {}
            fallbacks = {
                "premise": seed.get("logline") or safe_source.get("description") or "围绕主角目标与阻力展开的故事",
                "theme": seed.get("long_question") or "人在代价与选择中如何保持自我",
                "world": seed.get("world_contract") or safe_source.get("setting") or "依据用户核心创意建立的具体社会与生活环境",
                "rules": seed.get("world_contract") or safe_source.get("world_rules") or "所有能力与资源均有边界、代价和可被利用的限制",
                "style": seed.get("experience_contract") or safe_source.get("viewpoint") or "采用有限视角，以具体行动和对白推进",
                "ending_direction": seed.get("ending_state") or safe_source.get("ending_preference") or "主线矛盾得到明确结果，人物选择产生不可逆后果",
            }
            for key, value in fallbacks.items():
                if not str(parsed.get(key, "")).strip():
                    parsed[key] = str(value)
            if style_profile and str(style_profile.get("style_instruction", "")).strip():
                parsed["style"] = str(style_profile["style_instruction"]).strip()
        if stage == "characters" and (not isinstance(parsed.get("characters"), list) or len(parsed.get("characters", [])) < 3):
            retry_prompt = prompt + "\n\n上一次有效人物少于3名。请完整重做人物JSON，必须包含4至6名姓名不同的人物：主角、主要对手、至少两名推动主线的重要配角。不要解释。"
            retry_raw = await asyncio.to_thread(llm.chat, system, retry_prompt, 3600, task_type="structured")
            try:
                parsed = parse_object(retry_raw)
            except Exception:
                parsed = {}
        if stage == "characters" and len(parsed.get("characters", []) if isinstance(parsed.get("characters"), list) else []) < 3:
            existing = [item for item in parsed.get("characters", []) if isinstance(item, dict)] if isinstance(parsed.get("characters"), list) else []
            used_names = {str(item.get("name", "")).strip() for item in existing}
            seed = safe_source.get("story_seed", {}) if isinstance(safe_source.get("story_seed"), dict) else {}
            templates = [
                ("沈砚", "主角", str(seed.get("protagonist_engine") or safe_source.get("protagonist") or "承担核心目标的人"), "完成外在目标", "失败代价成为现实"),
                ("顾临川", "主要对手", str(seed.get("conflict_engine") or safe_source.get("opposition") or "持续制造阻力的人"), "维护自己的秩序与利益", "失去控制权"),
                ("林澈", "重要配角", "能提供主角欠缺的资源或视角，但有独立利益", "推动真相同时保护自身", "被主角的选择牵连"),
                ("周岚", "重要配角", "与主角存在信任张力，负责把抽象代价转化为关系后果", "阻止局势失控", "再次遭到背叛"),
            ]
            for fallback_name, role, background, desire, fear in templates:
                if len(existing) >= 4:
                    break
                name_value = fallback_name
                suffix = 2
                while name_value in used_names:
                    name_value = fallback_name + str(suffix); suffix += 1
                used_names.add(name_value)
                existing.append({
                    "name": name_value, "role": role, "desire": desire, "fear": fear,
                    "principle": "以自身利益与底线做出可观察选择", "flaw": "关键压力下会采取有代价的错误策略",
                    "personality": "通过具体行动、说话方式和选择表现性格",
                    "background": background, "abilities": "拥有与角色功能匹配的资源，同时存在明确限制",
                    "arc": "在主线冲突中因选择与后果发生可追踪变化", "relationships": "根据用户确认后再细化",
                })
            parsed = {"characters": existing}
        if stage == "structure" and not parsed.get("volumes"):
            retry_prompt = prompt + "\n\n上一次返回缺少volumes或volumes为空。请重新输出完整JSON；必须包含连续覆盖全书的volumes数组，每卷包含必要字段。不要解释。"
            retry_raw = await asyncio.to_thread(llm.chat, system, retry_prompt, 5200, task_type="structured")
            try:
                parsed = parse_object(retry_raw)
            except Exception:
                parsed = {}
        if stage == "structure" and not parsed.get("volumes"):
            parsed["volumes"] = build_fallback_volumes(target_chapters, str(parsed.get("outline", "")))
            parsed.setdefault("outline", "根据已确认故事设定，按建立局势、升级冲突、重大转折与终局兑现逐步推进。")
        if stage == "structure" and isinstance(parsed.get("volumes"), list):
            parsed["volumes"] = normalize_volume_ranges(parsed["volumes"], target_chapters)
            upstream = {"source": safe_source, "foundation": accepted.get("foundation", {}), "characters": accepted.get("characters", {})}
            for volume in parsed["volumes"]:
                if not isinstance(volume, dict) or volume_sections_are_valid(volume):
                    continue
                try:
                    section_system, section_prompt = volume_sections_prompts(volume, upstream)
                    section_raw = await asyncio.to_thread(llm.chat, section_system, section_prompt, 2200, task_type="structured")
                    section_data = parse_object(section_raw)
                except Exception as section_error:
                    logger.warning("分卷 %s 节纲生成失败，使用确定性可编辑骨架: %s", volume.get("title", ""), section_error)
                    section_data = {"sections": []}
                volume["sections"] = section_data.get("sections", [])
                volume["sections"] = normalize_section_ranges(volume)
        if stage == "opening":
            parsed["chapters"] = normalize_opening_chapters(parsed.get("chapters", []), target_chapters, accepted.get("structure", {}))
            duplicates = duplicate_opening_chapters(parsed["chapters"])
            accepted_characters = accepted.get("characters", {}).get("characters", []) if isinstance(accepted.get("characters"), dict) else []
            identity_conflicts = opening_character_identity_conflicts(parsed["chapters"], accepted_characters)
            if duplicates or identity_conflicts:
                conflict_names = sorted({item["name"] for item in identity_conflicts})
                retry_prompt = prompt + (
                    "\n\n上一次结果存在以下错误：重复执行方案章节="
                    + ("、".join(str(number) for number in duplicates) or "无")
                    + "；被错误改写身份或生死状态的已确认人物=" + ("、".join(conflict_names) or "无") + "。"
                    "请完整重做前五章JSON；"
                    "每章必须承接上一章结果，并拥有不同的地点或当下问题、不同的因果节拍和不同结尾后果。"
                    "不得复制任何一章的opening、beats或ending_hook；不得把已确认人物改成死者、受害者或其他身份。"
                    "需要死者时必须另取不与已确认人物重名的新姓名。"
                )
                try:
                    retry_raw = await asyncio.to_thread(llm.chat, system, retry_prompt, 4200, task_type="structured")
                    retry_data = parse_object(retry_raw)
                    retry_chapters = normalize_opening_chapters(
                        retry_data.get("chapters", []), target_chapters, accepted.get("structure", {}),
                    )
                    retry_duplicates = duplicate_opening_chapters(retry_chapters)
                    retry_identity_conflicts = opening_character_identity_conflicts(retry_chapters, accepted_characters)
                    if len(retry_duplicates) + len(retry_identity_conflicts) < len(duplicates) + len(identity_conflicts):
                        parsed = retry_data
                        parsed["chapters"] = retry_chapters
                except Exception as retry_error:
                    logger.warning("开篇细纲去重重试失败，使用确定性修复: %s", retry_error)
            parsed["chapters"] = repair_duplicate_opening_chapters(parsed["chapters"])
            remaining_identity_conflicts = opening_character_identity_conflicts(parsed["chapters"], accepted_characters)
            if remaining_identity_conflicts:
                parsed["chapters"], replacements = repair_opening_character_identity_conflicts(
                    parsed["chapters"], accepted_characters,
                )
                logger.warning("开篇细纲人物身份冲突已确定性更名修复: %s", replacements)
            parsed["chapters"], protagonist_repairs = repair_opening_protagonist_omissions(
                parsed["chapters"], accepted_characters,
            )
            if protagonist_repairs:
                logger.warning("开篇主线章节主角缺失已修复: %s", protagonist_repairs)
        result = validate_planning_stage(stage, parsed, target_chapters)
        if stage == "foundation" and source.get("style_reference"):
            leaks = detect_style_reference_leaks(str(source["style_reference"]), result)
            if leaks:
                raise ValueError("检测到文风参考内容混入故事设定，请重新生成：" + "、".join(leaks))
        return JSONResponse({"success": True, "stage": stage, "result": result, "style_profile": style_profile, "metrics": llm.last_metrics})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/create-from-plan")
async def api_create_from_plan(request: Request):
    created = False
    name = ""
    previous_current = workspace.data.get("current")
    try:
        payload = await request.json()
        source = payload.get("source", {})
        plan = payload.get("plan", {})
        name = str(source.get("name", "")).strip()
        target_chapters = max(5, min(1000, int(source.get("target_chapters", 100))))
        foundation = validate_planning_stage("foundation", plan.get("foundation", {}), target_chapters)
        characters = validate_planning_stage("characters", plan.get("characters", {}), target_chapters)
        structure = validate_planning_stage("structure", plan.get("structure", {}), target_chapters)
        opening = validate_planning_stage("opening", plan.get("opening", {}), target_chapters)
        workspace.create_novel(name, str(source.get("genre", "")), foundation["style"], str(source.get("description", "")))
        created = True
        nm = get_novel_manager(name)
        world_text = f"# 故事前提\n\n{foundation['premise']}\n\n# 核心主题\n\n{foundation.get('theme', '')}\n\n# 世界观\n\n{foundation['world']}\n\n# 结局方向\n\n{foundation['ending_direction']}"
        (nm.path / "bible" / "world.md").write_text(world_text, "utf-8")
        (nm.path / "bible" / "rules.md").write_text(foundation["rules"], "utf-8")
        (nm.path / "bible" / "style.md").write_text(foundation["style"], "utf-8")
        volume_lines = ["# 全书总纲", "", structure["outline"], "", "# 分卷规划", ""]
        for volume in structure["volumes"]:
            volume_lines.extend([
                f"## {volume.get('title', '未命名卷')}（第{volume['start_chapter']}—{volume['end_chapter']}章）",
                "", f"目标：{volume.get('goal', '')}", f"冲突：{volume.get('conflict', '')}",
                "转折：" + "；".join(str(item) for item in volume.get("turning_points", [])), "",
            ])
            for section in volume.get("sections", []):
                volume_lines.extend([
                    f"### {section.get('title', '未命名节')}（第{section.get('start_chapter')}—{section.get('end_chapter')}章）",
                    f"作用：{section.get('purpose', '')}", f"冲突：{section.get('conflict', '')}",
                    f"结果：{section.get('outcome', '')}", "",
                ])
        (nm.path / "outline" / "main.md").write_text("\n".join(volume_lines), "utf-8")
        storage_mgr.atomic_write_json(nm.path / "outline" / "volumes.json", structure["volumes"])
        storage_mgr.atomic_write_json(nm.path / "outline" / "narrative_policy.json", structure.get("narrative_policy", {}))
        storage_mgr.atomic_write_json(nm.path / "outline" / "opening_chapters.json", opening)
        titles = {str(item.get("chapter")): item.get("title", "") for item in opening["chapters"]}
        storage_mgr.atomic_write_json(nm.path / "outline" / "chapter_titles.json", titles)
        char_mgr = get_character_manager(nm)
        for character in characters["characters"]:
            char_mgr.create_character(
                str(character.get("name", "")).strip(), str(character.get("personality", "")),
                str(character.get("background", "")), str(character.get("abilities", "")), "凡人",
                str(character.get("relationships", "")), "存活",
                role_tier=char_mgr.role_tier_from_planning_role(character.get("role", "")),
                personality_profile=character.get("personality_profile", {}),
            )
        first_goal = str(opening["chapters"][0].get("goal", "")) if opening["chapters"] else ""
        nm.save_state({
            "target_chapters": target_chapters, "planning_completed": True,
            "next_goal": first_goal, "ending_direction": foundation["ending_direction"],
        })
        safe_source = {key: value for key, value in source.items() if key != "style_reference"}
        (nm.path / "planning").mkdir(parents=True, exist_ok=True)
        storage_mgr.atomic_write_json(nm.path / "planning" / "workflow.json", {"source": safe_source, "plan": plan, "completed": True})
        return JSONResponse({"success": True, "name": name, "target_chapters": target_chapters})
    except Exception as exc:
        if created and name:
            destination = config.NOVELS_ROOT / name
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            workspace.rollback_created(name, previous_current)
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/apply-staged-plan")
async def api_apply_staged_plan(name: str, request: Request):
    """将逐阶段确认的策划事务化应用到当前小说。"""
    try:
        payload = await request.json()
        source = payload.get("source", {})
        plan = payload.get("plan", {})
        target_chapters = max(5, min(1000, int(source.get("target_chapters", 100))))
        foundation = validate_planning_stage("foundation", plan.get("foundation", {}), target_chapters)
        characters = validate_planning_stage("characters", plan.get("characters", {}), target_chapters)
        structure = validate_planning_stage("structure", plan.get("structure", {}), target_chapters)
        opening = validate_planning_stage("opening", plan.get("opening", {}), target_chapters)
        nm = get_novel_manager(name)
        current_chapter = nm.get_current_chapter()
        old_opening = storage_mgr.safe_read_json(nm.path / "outline" / "opening_chapters.json", {})
        opening = protect_committed_opening(old_opening, opening, current_chapter)
        world_text = (
            f"# 故事前提\n\n{foundation['premise']}\n\n# 核心主题\n\n{foundation.get('theme', '')}"
            f"\n\n# 世界观\n\n{foundation['world']}\n\n# 结局方向\n\n{foundation['ending_direction']}"
        )
        volume_lines = ["# 全书总纲", "", structure["outline"], "", "# 分卷规划", ""]
        for volume in structure["volumes"]:
            volume_lines.extend([
                f"## {volume.get('title', '未命名卷')}（第{volume['start_chapter']}—{volume['end_chapter']}章）",
                "", f"目标：{volume.get('goal', '')}", f"冲突：{volume.get('conflict', '')}",
                "转折：" + "；".join(str(item) for item in volume.get("turning_points", [])), "",
            ])
            for section in volume.get("sections", []):
                volume_lines.extend([
                    f"### {section.get('title', '未命名节')}（第{section.get('start_chapter')}—{section.get('end_chapter')}章）",
                    f"作用：{section.get('purpose', '')}", f"冲突：{section.get('conflict', '')}",
                    f"结果：{section.get('outcome', '')}", "",
                ])
        old_volumes = storage_mgr.safe_read_json(nm.path / "outline" / "volumes.json", [])
        old_briefs = storage_mgr.safe_read_json(nm.path / "outline" / "chapter_briefs.json", {})
        with FileLock(str(nm.path / ".novel_mutation.lock"), timeout=600), NovelMutationTransaction(
            nm.path, [], directories=("bible", "outline", "planning", "characters"), files=("state.json",),
        ):
            PlanningVersionManager(nm.path)._snapshot("分阶段重策划前")
            storage_mgr.atomic_write_text(nm.path / "bible" / "world.md", world_text)
            storage_mgr.atomic_write_text(nm.path / "bible" / "rules.md", foundation["rules"])
            storage_mgr.atomic_write_text(nm.path / "bible" / "style.md", foundation["style"])
            storage_mgr.atomic_write_text(nm.path / "outline" / "main.md", "\n".join(volume_lines))
            storage_mgr.atomic_write_json(nm.path / "outline" / "volumes.json", structure["volumes"])
            storage_mgr.atomic_write_json(nm.path / "outline" / "narrative_policy.json", structure.get("narrative_policy", {}))
            impact = PlanningImpactManager(nm.path, logger, storage_mgr).record_changes(
                old_volumes, structure["volumes"], old_briefs, {}, nm.get_current_chapter(), True,
            )
            storage_mgr.atomic_write_json(nm.path / "outline" / "opening_chapters.json", opening)
            titles = storage_mgr.safe_read_json(nm.path / "outline" / "chapter_titles.json", {})
            titles = titles if isinstance(titles, dict) else {}
            titles = {key: value for key, value in titles.items() if str(key).isdigit() and int(key) <= current_chapter}
            titles.update({str(item.get("chapter")): item.get("title", "") for item in opening["chapters"]})
            storage_mgr.atomic_write_json(nm.path / "outline" / "chapter_titles.json", titles)
            char_mgr = get_character_manager(nm)
            for character in characters["characters"]:
                char_name = str(character.get("name", "")).strip()
                values = {
                    "personality": str(character.get("personality", "")),
                    "personality_profile": character.get("personality_profile", {}),
                    "background": str(character.get("background", "")),
                    "abilities": str(character.get("abilities", "")),
                    "relationships": str(character.get("relationships", "")),
                    "role_tier": char_mgr.role_tier_from_planning_role(character.get("role", "")),
                }
                if char_mgr.get_character(char_name):
                    char_mgr.update_character(char_name, **values)
                else:
                    char_mgr.create_character(char_name, **values)
            next_opening = next(
                (item for item in opening["chapters"] if int(item.get("chapter", 0)) > current_chapter), None,
            )
            first_goal = str(next_opening.get("goal", "")) if next_opening else str(nm.get_state().get("next_goal", ""))
            nm.save_state({
                "genre": str(source.get("genre", "")), "description": str(source.get("description", "")),
                "style": foundation["style"],
                "target_chapters": target_chapters, "planning_completed": True,
                "next_goal": first_goal, "ending_direction": foundation["ending_direction"],
            })
            safe_source = {key: value for key, value in source.items() if key != "style_reference"}
            storage_mgr.atomic_write_json(
                nm.path / "planning" / "workflow.json",
                {"source": safe_source, "plan": plan, "completed": True},
            )
        registration_warning = ""
        try:
            workspace.update_registration(name, {"genre": str(source.get("genre", ""))}, False)
        except Exception as exc:
            registration_warning = f"策划已应用，但工作区题材标签同步失败：{exc}"
            logger.warning(registration_warning)
        return JSONResponse({
            "success": True, "name": name, "target_chapters": target_chapters,
            "planning_impact": impact, "protected_chapters": opening.get("protected_chapters", []),
            "warnings": [registration_warning] if registration_warning else [],
        })
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/edit-selection")
async def api_edit_selection(
    name: str, text: str = Form(...), operation: str = Form(...), instruction: str = Form(""),
):
    if not text.strip() or len(text) > 12000:
        return JSONResponse({"success": False, "error": "请选择1至12000字的正文"}, status_code=400)
    llm = await asyncio.to_thread(get_llm)
    if not llm:
        return JSONResponse({"success": False, "error": "模型服务未连接"}, status_code=503)
    try:
        system, prompt = selection_edit_prompts(name, text, operation, instruction)
        max_tokens = min(4096, max(1024, int(len(text) / 1.8 * (1.8 if operation == "expand" else 1.2))))
        result = await asyncio.to_thread(llm.chat, system, prompt, max_tokens, task_type="revision")
        if not result.strip():
            raise ValueError("模型没有返回修改结果")
        return JSONResponse({"success": True, "content": result.strip(), "metrics": llm.last_metrics})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/novels/{name}/rewrite-scene")
async def api_rewrite_scene(
    name: str, chapter: int = Form(...), scene: str = Form(...),
    instruction: str = Form(""), target_words: int = Form(1200),
):
    if not scene.strip() or len(scene) > 12000:
        return JSONResponse({"success": False, "error": "目标场景必须为1至12000字"}, status_code=400)
    try:
        nm = get_novel_manager(name)
        full_chapter = get_chapter_manager(nm).read_chapter(chapter) or scene
        llm = await asyncio.to_thread(get_llm)
        if not llm:
            return JSONResponse({"success": False, "error": "模型服务未连接"}, status_code=503)
        system, prompt = scene_revision_prompts(name, full_chapter, scene, instruction, target_words)
        max_tokens = min(5000, max(800, int(target_words / 1.8) + 500))
        content = await asyncio.to_thread(llm.chat, system, prompt, max_tokens, task_type="revision")
        return JSONResponse({"success": True, "content": content.strip(), "metrics": llm.last_metrics})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/model-config")
async def model_config_json():
    return JSONResponse(config.get_model_config_report())


@app.get("/api/model/status")
async def api_model_status():
    status = await asyncio.to_thread(_model_status_locked)
    return JSONResponse({
        **status,
        "queue_mode": "serial",
        "max_concurrency": MODEL_CONFIG.get("max_concurrent_generations", 1),
        "context_budget": MODEL_CONFIG.get("available_context"),
        "runtime": config.get_model_config_report()["runtime"],
        "reasoning_effort": MODEL_CONFIG.get("reasoning_effort", "none"),
        "performance": performance_manager.get(),
    })


@app.post("/api/model/unload")
async def api_model_unload():
    try:
        await asyncio.to_thread(_unload_model_locked)
        return JSONResponse({"success": True})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=409)


@app.post("/api/model/clear-vram")
async def api_model_clear_vram():
    """仅卸载模型占用；不强杀驱动、桌面或其他应用。"""
    try:
        await asyncio.to_thread(_unload_model_locked)
        await asyncio.sleep(1)
        return JSONResponse({"success": True, "message": "模型服务已断开；如需继续使用，请重新连接模型后端"})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=409)


@app.get("/api/model/gpu-processes")
async def api_gpu_processes():
    try:
        rows = await asyncio.to_thread(_gpu_processes)
        return JSONResponse({"success": True, "processes": rows, "total_mb": round(sum(float(item.get("vram_mb", 0)) for item in rows), 1)})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.get("/api/model/hardware-stats")
async def api_hardware_stats():
    """返回 Windows 可公开读取的实时 GPU 指标；驱动未暴露的温度/功耗标记为空。"""
    try:
        script = r"""
$engine=(Get-Counter '\GPU Engine(*)\Utilization Percentage' -ErrorAction SilentlyContinue).CounterSamples | Where-Object {$_.CookedValue -gt 0} | Measure-Object CookedValue -Sum
$memory=(Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage' -ErrorAction SilentlyContinue).CounterSamples | Where-Object {$_.CookedValue -gt 0} | Measure-Object CookedValue -Maximum
[pscustomobject]@{utilization=[math]::Round([math]::Min(100,$engine.Sum),1);dedicated_mb=[math]::Round($memory.Maximum/1MB,1)} | ConvertTo-Json -Compress
"""
        result = await asyncio.to_thread(subprocess.run, ["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        data = json.loads(result.stdout) if result.stdout.strip() else {}
        data.update({"temperature": None, "power_watts": None, "clock_mhz": None, "note": "温度、功耗和频率需 AMD 驱动接口支持；未伪造不可用数据"})
        return JSONResponse({"success": True, "stats": data})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.post("/api/model/gpu-processes/{pid}/close")
async def api_close_gpu_process(pid: int):
    try:
        rows = await asyncio.to_thread(_gpu_processes)
        item = next((row for row in rows if int(row.get("pid", 0)) == pid), None)
        if not item:
            return JSONResponse({"success": False, "error": "进程已结束或不再占用显存"}, status_code=404)
        if item.get("protected"):
            return JSONResponse({"success": False, "error": f"{item.get('name')} 是受保护进程，禁止从项目中关闭"}, status_code=403)
        result = await asyncio.to_thread(
            subprocess.run, ["taskkill", "/PID", str(pid)], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "进程拒绝退出")
        return JSONResponse({"success": True, "message": f"已请求关闭 {item.get('name')}"})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=409)


@app.post("/api/model/reload")
async def api_model_reload():
    try:
        await asyncio.to_thread(_reload_model_locked)
        return JSONResponse({"success": True})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@app.post("/api/model/clear-conversations")
async def api_clear_conversations():
    try:
        task_store.clear_all()
        return JSONResponse({"success": True, "message": "已清除本项目全部任务、事件和浏览器对话草稿"})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=409)


@app.post("/api/model/warmup")
async def api_model_warmup():
    try:
        await asyncio.to_thread(get_llm)
        metrics = await asyncio.to_thread(_warmup_model_locked)
        return JSONResponse({"success": True, "metrics": metrics})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=503)


@app.post("/api/model/benchmark")
async def api_model_benchmark():
    """固定提示词三轮热态测速，冷启动/预热不计入平均值。"""
    try:
        await asyncio.to_thread(get_llm)
        runs = await asyncio.to_thread(_benchmark_model_locked)
        average = round(sum(float(item.get("tokens_per_second", 0)) for item in runs) / len(runs), 2)
        return JSONResponse({"success": True, "runs": runs, "average_tokens_per_second": average})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=503)


@app.post("/api/model/profile")
async def api_model_profile(request: Request):
    try:
        payload = await request.json()
        data = performance_manager.save_profile(str(payload.get("name", "balanced")), payload)
        return JSONResponse({"success": True, "performance": data})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/settings")
async def api_settings():
    return JSONResponse({"success": True, "settings": settings_manager.get(), "model": config.get_model_config_report(), "backup": backup_scheduler.status()})


@app.post("/api/settings")
async def api_save_settings(request: Request):
    try:
        values = await request.json()
        return JSONResponse({"success": True, "settings": settings_manager.update(values)})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/settings/prompts")
async def api_prompt_settings():
    return JSONResponse({"success": True, "prompts": prompt_settings_manager.get()})


@app.post("/api/settings/prompts")
async def api_save_prompt_settings(request: Request):
    try:
        return JSONResponse({"success": True, "prompts": prompt_settings_manager.save(await request.json())})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.post("/api/settings/prompts/reset")
async def api_reset_prompt_settings():
    return JSONResponse({"success": True, "prompts": prompt_settings_manager.reset()})


@app.get("/api/ai-actions")
async def api_ai_actions():
    return JSONResponse({"success": True, "actions": list_ai_actions(), "errors": validate_ai_action_registry()})


@app.get("/api/workflows")
async def api_workflows():
    return JSONResponse({"success": True, "workflows": list_workflows()})


@app.post("/api/novels/{name}/workflows/{workflow_key}")
async def api_start_workflow(name: str, workflow_key: str, request: Request):
    try:
        values = await request.json()
        values = values if isinstance(values, dict) else {}
        nm = get_novel_manager(name)
        if task_store.active_for_novel(name):
            return JSONResponse({"success": False, "error": "该小说已有运行、排队或暂停任务，请先处理现有任务"}, status_code=409)
        if workflow_key in {"deep_chapter", "serial_chapters"}:
            readiness = await asyncio.to_thread(ReleaseReadinessManager(nm, logger, storage_mgr).run)
            if readiness["status"] == "blocked" and not bool(values.get("override_readiness", False)):
                return JSONResponse({
                    "success": False, "error": "项目可用性验收未通过，暂不启动自动生成",
                    "readiness": readiness,
                }, status_code=409)
        payload = workflow_payload(workflow_key, values)
        if workflow_key in {"deep_chapter", "serial_chapters"}:
            payload["readiness_score"] = readiness["score"]
        payload["commit_mode"] = settings_manager.get().get("chapter_commit_mode", "balanced")
        payload["start_chapter"] = nm.get_current_chapter() + 1
        task_id = task_store.create_if_idle(
            name, "workflow", next(item["label"] for item in list_workflows() if item["key"] == workflow_key),
            payload, status="queued",
        )
        if not task_id:
            return JSONResponse({"success": False, "error": "该小说已有运行、排队或暂停任务，请先处理现有任务"}, status_code=409)
        task_store.event(task_id, "工作流已进入单模型串行队列", 0, stage="queued")
        task_runner.notify()
        return JSONResponse({"success": True, "task_id": task_id})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/novels/{name}/prompt-preview/{action_key}")
async def api_prompt_preview(name: str, action_key: str, chapter: int = 0, target_words: int = 5000):
    try:
        action = get_ai_action(action_key)
        nm = get_novel_manager(name)
        context_manager = ContextManager(nm, logger, None, None)
        context = await asyncio.to_thread(
            context_manager.build_context, None, None, None, False, action["profile"],
        )
        system = user = ""
        if action_key == "chapter_write":
            chapter = chapter or nm.get_current_chapter() + 1
            briefs = storage_mgr.safe_read_json(nm.path / "outline" / "chapter_briefs.json", {})
            briefs = briefs if isinstance(briefs, dict) else {}
            brief = briefs.get(str(chapter), {})
            scene = SceneOutlineManager(nm.path, logger, storage_mgr).render(chapter)
            system, user = chapter_prompts(name, context + "\n\n" + render_chapter_brief(brief) + "\n\n" + scene, target_words, False)
        return JSONResponse({"success": True, "action": action, "system": system, "user": user, "context": context, "context_stats": context_manager.last_build_stats})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)


@app.get("/api/tasks")
async def api_tasks(novel: Optional[str] = None, limit: int = 30):
    return JSONResponse({"success": True, "tasks": task_store.list(novel, limit)})


@app.post("/api/novels/{name}/batch-generate")
async def api_batch_generate(
    name: str, count: int = Form(3), target_words: int = Form(5000),
    stop_on_warning: bool = Form(True), override_readiness: bool = Form(False),
):
    nm = get_novel_manager(name)
    if task_store.active_for_novel(name):
        return JSONResponse({"success": False, "error": "该小说已有运行、排队或暂停任务，请先处理现有任务"}, status_code=409)
    readiness = await asyncio.to_thread(ReleaseReadinessManager(nm, logger, storage_mgr).run)
    if readiness["status"] == "blocked" and not override_readiness:
        return JSONResponse({
            "success": False, "error": "项目可用性验收未通过，暂不启动连续生成",
            "readiness": readiness,
        }, status_code=409)
    count = max(1, min(10, count))
    target_total = nm.get_state().get("target_chapters", 0)
    if target_total:
        remaining = target_total - nm.get_current_chapter()
        if remaining <= 0:
            return JSONResponse({"success": False, "error": f"已经达到目标章节数 {target_total}"}, status_code=409)
        count = min(count, remaining)
    target_words = max(500, min(20000, target_words))
    task_id = task_store.create_if_idle(
        name, "batch_chapters", f"连续生成 {count} 章",
        {"count": count, "target_words": target_words, "stop_on_warning": stop_on_warning,
         "start_chapter": nm.get_current_chapter() + 1, "completed_chapters": [],
         "commit_mode": settings_manager.get().get("chapter_commit_mode", "balanced"),
         "readiness_score": readiness["score"]},
        status="queued",
    )
    if not task_id:
        return JSONResponse({"success": False, "error": "该小说已有运行、排队或暂停任务，请先处理现有任务"}, status_code=409)
    task_store.event(task_id, "任务已进入单模型串行队列", 0, stage="queued")
    task_runner.notify()
    return JSONResponse({"success": True, "task_id": task_id, "status": "queued"})


@app.get("/api/tasks/{task_id}")
async def api_task(task_id: str):
    task = task_store.get(task_id)
    if not task:
        return JSONResponse({"success": False, "error": "任务不存在"}, status_code=404)
    return JSONResponse({"success": True, "task": task})


@app.post("/api/tasks/{task_id}/cancel")
async def api_cancel_task(task_id: str):
    task = task_store.get(task_id)
    if not task:
        return JSONResponse({"success": False, "error": "任务不存在"}, status_code=404)
    if not task_store.cancel(task_id):
        return JSONResponse({"success": False, "error": f"任务当前状态为 {task['status']}，不能取消"}, status_code=409)
    return JSONResponse({"success": True})


@app.post("/api/tasks/{task_id}/pause")
async def api_pause_task(task_id: str):
    task = task_store.get(task_id)
    if not task:
        return JSONResponse({"success": False, "error": "任务不存在"}, status_code=404)
    if task["kind"] not in RESUMABLE_TASK_KINDS:
        return JSONResponse({"success": False, "error": "实时流式任务不支持暂停，请使用取消"}, status_code=409)
    if not task_store.pause(task_id):
        return JSONResponse({"success": False, "error": f"任务当前状态为 {task['status']}，不能暂停"}, status_code=409)
    return JSONResponse({"success": True, "message": "将在当前模型调用结束后暂停"})


@app.post("/api/tasks/{task_id}/resume")
async def api_resume_task(task_id: str, approve_review: bool = Form(False)):
    task = task_store.get(task_id)
    if not task:
        return JSONResponse({"success": False, "error": "任务不存在"}, status_code=404)
    if task["kind"] not in RESUMABLE_TASK_KINDS:
        return JSONResponse({"success": False, "error": "该任务没有可恢复检查点，请重新发起生成"}, status_code=409)
    if task_runner.is_executing(task_id):
        return JSONResponse({
            "success": False,
            "error": "当前模型调用尚未结束，工作器还没有进入安全暂停点，请稍后再恢复",
        }, status_code=409)
    waiting_review = task.get("input", {}).get("waiting_review", {})
    if isinstance(waiting_review, dict) and waiting_review.get("chapter"):
        if not approve_review:
            return JSONResponse({
                "success": False,
                "error": f"第{waiting_review.get('chapter')}章正在等待{waiting_review.get('kind', '人工')}验收；必须明确批准后才能恢复",
                "waiting_review": waiting_review,
            }, status_code=409)
        if not task_store.approve_waiting_review(task_id):
            return JSONResponse({"success": False, "error": "任务验收检查点损坏，不能绕过确认恢复"}, status_code=409)
    if not task_store.resume(task_id):
        return JSONResponse({"success": False, "error": f"任务当前状态为 {task['status']}，不能恢复"}, status_code=409)
    task_runner.notify()
    return JSONResponse({"success": True})


def main():
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
