"""novel-workspace-mcp MCP Server v2.2"""
import json, asyncio, threading
from contextvars import ContextVar
import mcp.types as types
from mcp.server.lowlevel import Server; from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

import config
from config import ensure_dirs, setup_logging, SERVER_NAME, SERVER_VERSION, estimate_tokens
from storage_utils import StorageManager
from core.workspace_manager import WorkspaceManager
from core.chapter_manager import ChapterManager
from core.chapter_turn_engine import ChapterTurnEngine
from core.context_manager import ContextManager
from core.character_manager import CharacterManager
from core.timeline_manager import TimelineManager
from core.consistency_manager import ConsistencyManager
from core.writing_analyzer import WritingAnalyzer
from core.savepoint_manager import SavepointManager
from core.plugin_manager import EventBus, PluginManager
from core.quality_tracker import QualityTracker
from core.character_evolution import CharacterEvolutionTracker
from core.style_preset import StylePresetManager
from core.fact_manager import FactManager
from core.foreshadow_manager import ForeshadowManager
from core.change_review_manager import ChangeReviewManager
from core.export_manager import ExportManager
from core.entity_ledger import EntityLedger
from core.story_logic_manager import StoryLogicManager
from core.causal_graph_manager import CausalGraphManager
from core.causal_repair_planner import CausalRepairPlanner
from core.planning_review_manager import PlanningReviewManager
from core.ai_action_registry import list_ai_actions as registered_ai_actions
from core.ai_contracts import BASE_SYSTEM, parse_object, scene_revision_prompts
from core.genre_pack_manager import GenrePackManager
from core.long_form_evaluator import LongFormEvaluator
from core.planning_impact_manager import PlanningImpactManager
from core.scene_outline_manager import SceneOutlineManager
from core.state_card_manager import StateCardManager
from core.canonical_state_manager import CanonicalStateManager
from core.canonical_lock_manager import CanonicalLockManager
from core.story_clock_manager import StoryClockManager
from core.review_queue_manager import ReviewQueueManager
from core.author_preference_manager import AuthorPreferenceManager
from core.prompt_snapshot_manager import PromptSnapshotManager
from core.import_rebuilder import ImportRebuilder
from core.history_revision_manager import HistoryRevisionManager
from core.story_sandbox_manager import StorySandboxManager
from core.workflow_engine import list_workflows as registered_workflows

ensure_dirs(); logger = setup_logging()
logger.info('novel-workspace-mcp v2.2 starting')

workspace = WorkspaceManager(logger); storage_mgr = StorageManager(logger)
event_bus = EventBus(logger); plugin_mgr = PluginManager(config.PROJECT_ROOT / 'plugins', event_bus, logger)
writing_analyzer = WritingAnalyzer(logger)
_llm = None; _vs = None
_llm_lock = threading.Lock()
_vs_lock = threading.Lock()
_bound_novel = ContextVar("mcp_bound_novel", default=None)
_bound_novel_info = ContextVar("mcp_bound_novel_info", default=None)

def get_llm():
    global _llm
    if _llm is not None:
        return _llm
    with _llm_lock:
        if _llm is not None:
            return _llm
        client = None
        try:
            from llm_client import LMStudioClient
            client = LMStudioClient()
            if client.is_available():
                _llm = client
                if _vs is not None:
                    _vs.embed_func = client.embed
                    _vs._semantic_disabled = False
            else:
                client.close()
        except Exception as _ex:
            if client is not None:
                client.close()
            logger.debug('LLM init: %s', _ex)
    return _llm

def get_vs():
    global _vs
    if _vs is not None:
        return _vs
    with _vs_lock:
        if _vs is not None:
            return _vs
        try:
            from vector_store import VectorStore
            def unavailable_embedding(_text):
                raise RuntimeError("当前没有可用嵌入接口")
            _vs = VectorStore(logger, _llm.embed if _llm is not None else unavailable_embedding)
        except Exception as _vex:
            logger.debug('VS init: %s', _vex)
    return _vs

def nm():
    return _bound_novel.get() or workspace.get_novel_manager()


def current_novel_info():
    return _bound_novel_info.get() or workspace.get_current_novel()


chm = lambda: ChapterManager(nm(), logger, get_llm())
def turne(manager=None):
    def emit_after_save(chapter_number, content, result):
        event_bus.emit(
            "on_after_chapter_save", chapter_number=chapter_number,
            content=content, summary=result,
        )
    return ChapterTurnEngine(nm(), logger, manager or chm(), storage_mgr, [emit_after_save])
crm = lambda: CharacterManager(nm().path, logger)
tlm = lambda: TimelineManager(nm().path, logger)
csm = lambda: ConsistencyManager(nm(), logger)
spm = lambda: SavepointManager(nm().path, logger, storage_mgr)
qt_ = lambda: QualityTracker(nm().path, logger, storage_mgr)
ctxm = lambda: ContextManager(nm(), logger, get_vs(), get_llm())
cev_ = lambda: CharacterEvolutionTracker(nm().path, logger, storage_mgr)
stm_ = lambda: StylePresetManager(nm().path, logger, storage_mgr)
facts_ = lambda: FactManager(nm().path, logger, storage_mgr)
fsh_ = lambda: ForeshadowManager(nm().path, logger, storage_mgr)
reviews_ = lambda: ChangeReviewManager(nm().path, logger, storage_mgr)
scenes_ = lambda: SceneOutlineManager(nm().path, logger, storage_mgr)
states_ = lambda: StateCardManager(nm().path, logger, storage_mgr)
canonical_ = lambda: CanonicalStateManager(nm().path, logger, storage_mgr)
genres_ = lambda: GenrePackManager(nm().path, logger, storage_mgr)
sandboxes_ = lambda: StorySandboxManager(nm().path, logger, storage_mgr)

app = Server(SERVER_NAME)

@app.list_tools()
async def list_tools():
    return [
        types.Tool(name="list_novels", description="list_novels: 列出所有小说", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="create_novel", description="create_novel: 创建新小说项目", inputSchema={"type":"object","properties":{"name": {"type": "string"}, "genre": {"type": "string"}, "style": {"type": "string"}, "description": {"type": "string"}},"required":["name"]}),
        types.Tool(name="open_novel", description="open_novel: 切换到指定小说", inputSchema={"type":"object","properties":{"name": {"type": "string"}, },"required":["name"]}),
        types.Tool(name="get_novel_status", description="get_novel_status: 查看小说状态", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="continue_story", description="continue_story: 生成续写上下文", inputSchema={"type":"object","properties":{"query": {"type": "string"}, "target_words": {"type": "integer"}, "focus_character": {"type": "string"}},"required":[]}),
        types.Tool(name="save_chapter", description="save_chapter: 保存章节并自动摘要；治理冲突默认阻止", inputSchema={"type":"object","properties":{"chapter_number":{"type":"integer"},"content":{"type":"string"},"allow_fact_conflicts":{"type":"boolean"},"allow_locked_changes":{"type":"boolean"},"allow_story_clock_conflicts":{"type":"boolean"},"allow_character_decision_conflicts":{"type":"boolean"},"allow_degraded_summary":{"type":"boolean"}},"required":["chapter_number","content"]}),
        types.Tool(name="append_chapter", description="append_chapter: 追加内容；治理冲突默认阻止", inputSchema={"type":"object","properties":{"chapter_number":{"type":"integer"},"content":{"type":"string"},"allow_fact_conflicts":{"type":"boolean"},"allow_locked_changes":{"type":"boolean"},"allow_story_clock_conflicts":{"type":"boolean"},"allow_character_decision_conflicts":{"type":"boolean"},"allow_degraded_summary":{"type":"boolean"}},"required":["chapter_number","content"]}),
        types.Tool(name="read_chapter", description="read_chapter: 读取章节", inputSchema={"type":"object","properties":{"chapter_number": {"type": "integer"}},"required":["chapter_number"]}),
        types.Tool(name="get_context", description="get_context: 构建写作上下文", inputSchema={"type":"object","properties":{"max_tokens": {"type": "integer"}, "query": {"type": "string"}, "focus_character": {"type": "string"}},"required":[]}),
        types.Tool(name="update_next_goal", description="update_next_goal: 更新写作目标", inputSchema={"type":"object","properties":{"goal": {"type": "string"}},"required":["goal"]}),
        types.Tool(name="update_novel_status", description="update_novel_status: 更新小说状态", inputSchema={"type":"object","properties":{"status": {"type": "string"}},"required":["status"]}),
        types.Tool(name="index_chapter_to_vector", description="index_chapter_to_vector: 章节向量化", inputSchema={"type":"object","properties":{"chapter_number": {"type": "integer"}},"required":["chapter_number"]}),
        types.Tool(name="create_character", description="create_character: 创建人物", inputSchema={"type":"object","properties":{"name": {"type": "string"}, "personality": {"type": "string"}, "background": {"type": "string"}, "abilities": {"type": "string"}, "ability_level": {"type": "string"}, "relationships": {"type": "string"}, "status": {"type": "string"}},"required":["name"]}),
        types.Tool(name="update_character", description="update_character: 更新人物", inputSchema={"type":"object","properties":{"name": {"type": "string"}, },"required":["name"]}),
        types.Tool(name="get_character", description="get_character: 获取人物详情", inputSchema={"type":"object","properties":{"name": {"type": "string"}, },"required":["name"]}),
        types.Tool(name="list_characters", description="list_characters: 列出所有人物", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="get_character_network", description="get_character_network: 人物关系图谱", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="add_event", description="add_event: 添加时间线事件", inputSchema={"type":"object","properties":{"chapter": {"type": "integer"}, "time": {"type": "string"}, "location": {"type": "string"}, "event": {"type": "string"}, "characters": {"type": "string"}},"required":["chapter", "time", "location", "event"]}),
        types.Tool(name="query_timeline", description="query_timeline: 查询时间线", inputSchema={"type":"object","properties":{"character": {"type": "string"}, "chapter": {"type": "integer"}, "keyword": {"type": "string"}, "limit": {"type": "integer"}},"required":[]}),
        types.Tool(name="check_consistency", description="check_consistency: 深度一致性检查", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="search_memory", description="search_memory: 语义检索", inputSchema={"type":"object","properties":{"query": {"type": "string"}},"required":["query"]}),
        types.Tool(name="analyze_chapter", description="analyze_chapter: 分析章节", inputSchema={"type":"object","properties":{"chapter_number": {"type": "integer"}},"required":["chapter_number"]}),
        types.Tool(name="detect_writing_patterns", description="detect_writing_patterns: AI指纹检测", inputSchema={"type":"object","properties":{"text": {"type": "string"}},"required":["text"]}),
        types.Tool(name="analyze_text_pacing", description="analyze_text_pacing: 文本节奏分析", inputSchema={"type":"object","properties":{"text": {"type": "string"}},"required":["text"]}),
        types.Tool(name="create_savepoint", description="create_savepoint: 创建快照", inputSchema={"type":"object","properties":{"chapter_number": {"type": "integer"}, "label": {"type": "string"}},"required":["chapter_number"]}),
        types.Tool(name="list_savepoints", description="list_savepoints: 列出快照", inputSchema={"type":"object","properties":{"chapter_number": {"type": "integer"}, "limit": {"type": "integer"}},"required":[]}),
        types.Tool(name="restore_savepoint", description="restore_savepoint: 恢复快照", inputSchema={"type":"object","properties":{"chapter_number": {"type": "integer"}, "savepoint_id": {"type": "string"}},"required":["chapter_number", "savepoint_id"]}),
        types.Tool(name="diff_savepoints", description="diff_savepoints: 比较快照差异", inputSchema={"type":"object","properties":{"chapter_number": {"type": "integer"}, "savepoint_a": {"type": "string"}, "savepoint_b": {"type": "string"}},"required":["chapter_number", "savepoint_a"]}),
        types.Tool(name="list_plugins", description="list_plugins: 列出插件", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="reload_plugins", description="reload_plugins: 热重载插件", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="toggle_plugin", description="toggle_plugin: 切换插件状态", inputSchema={"type":"object","properties":{"name": {"type": "string"}, "enabled": {"type": "boolean"}},"required":["name", "enabled"]}),
        types.Tool(name="report_quality_issue", description="report_quality_issue: 报告质量问题", inputSchema={"type":"object","properties":{"chapter": {"type": "integer"}, "issue_type": {"type": "string"}, "severity": {"type": "string"}, "description": {"type": "string"}, "suggestion": {"type": "string"}},"required":["chapter", "issue_type", "severity", "description"]}),
        types.Tool(name="get_quality_report", description="get_quality_report: 质量总报告", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="get_pending_issues", description="get_pending_issues: 待处理问题", inputSchema={"type":"object","properties":{"chapter": {"type": "integer"}},"required":[]}),
        types.Tool(name="scan_character_evolution", description="scan_character_evolution: 扫描人物演变", inputSchema={"type":"object","properties":{"chapter_number": {"type": "integer"}},"required":["chapter_number"]}),
        types.Tool(name="get_character_evolution", description="get_character_evolution: 人物演变报告", inputSchema={"type":"object","properties":{"name": {"type": "string"}, },"required":["name"]}),
        types.Tool(name="list_style_presets", description="list_style_presets: 列出风格预设", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="get_style_preset", description="get_style_preset: 获取风格预设", inputSchema={"type":"object","properties":{"name": {"type": "string"}, },"required":["name"]}),
        types.Tool(name="save_style_preset", description="save_style_preset: 保存风格预设", inputSchema={"type":"object","properties":{"name": {"type": "string"}, "description": {"type": "string"}, "traits": {"type": "array", "items": {"type": "string"}}, "avoid": {"type": "array", "items": {"type": "string"}}},"required":["name", "description", "traits"]}),
        types.Tool(name="extract_style_from_text", description="extract_style_from_text: 从文本提取风格", inputSchema={"type":"object","properties":{"name": {"type": "string"}, "text": {"type": "string"}},"required":["name", "text"]}),
        types.Tool(name="list_facts", description="list_facts: 查看最近事实与硬冲突", inputSchema={"type":"object","properties":{"limit":{"type":"integer"}},"required":[]}),
        types.Tool(name="list_foreshadowing", description="list_foreshadowing: 查看伏笔生命周期", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="get_story_logic", description="get_story_logic: 查看叙事承诺、因果和人物信息权限", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="get_causal_graph", description="get_causal_graph: 查看规划目标、正史证据、迟到兑现和因果环", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="propose_causal_repairs", description="propose_causal_repairs: 将到期缺证目标生成未来章节修复提案，不直接修改规划", inputSchema={"type":"object","properties":{"window":{"type":"integer"}},"required":[]}),
        types.Tool(name="apply_causal_repairs", description="apply_causal_repairs: 原子应用已预览的因果修复提案", inputSchema={"type":"object","properties":{"proposal_id":{"type":"string"}},"required":["proposal_id"]}),
        types.Tool(name="list_entities", description="list_entities: 查看地点、势力、物品和关系变化", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="get_planning_reviews", description="get_planning_reviews: 查看偏航、节末复盘和章节模式", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="get_chapter_briefs", description="get_chapter_briefs: 查看章前提要", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="rewrite_scene", description="rewrite_scene: 局部重写章节场景", inputSchema={"type":"object","properties":{"chapter":{"type":"integer"},"scene":{"type":"string"},"instruction":{"type":"string"},"target_words":{"type":"integer"}},"required":["chapter","scene"]}),
        types.Tool(name="list_character_changes", description="list_character_changes: 查看待确认人物变化", inputSchema={"type":"object","properties":{"status":{"type":"string"}},"required":[]}),
        types.Tool(name="decide_character_change", description="decide_character_change: 接受或拒绝人物变化", inputSchema={"type":"object","properties":{"change_id":{"type":"string"},"accept":{"type":"boolean"}},"required":["change_id","accept"]}),
        types.Tool(name="export_novel", description="export_novel: 导出txt/md/docx/epub/zip", inputSchema={"type":"object","properties":{"format_name":{"type":"string"}},"required":["format_name"]}),
        types.Tool(name="where_was_i", description="where_was_i: 查看上次进度", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="get_model_config", description="get_model_config: 查看模型配置", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="resume_session", description="resume_session: 新会话恢复包", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="list_ai_actions", description="list_ai_actions: 查看统一AI动作读写契约", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="list_workflows", description="list_workflows: 查看可恢复工作流定义", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="get_scene_outlines", description="get_scene_outlines: 查看场景级细纲", inputSchema={"type":"object","properties":{"chapter":{"type":"integer"}},"required":[]}),
        types.Tool(name="save_scene_outline", description="save_scene_outline: 保存并确认场景细纲", inputSchema={"type":"object","properties":{"chapter":{"type":"integer"},"outline":{"type":"object"}},"required":["chapter","outline"]}),
        types.Tool(name="get_state_cards", description="get_state_cards: 查看人物地点物品势力动态状态", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="upsert_state_card", description="upsert_state_card: 人工更新动态状态卡", inputSchema={"type":"object","properties":{"kind":{"type":"string"},"name":{"type":"string"},"chapter":{"type":"integer"},"fields":{"type":"object"},"evidence":{"type":"string"}},"required":["kind","name","chapter","fields"]}),
        types.Tool(name="list_genre_packs", description="list_genre_packs: 查看题材方法包", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="apply_genre_pack", description="apply_genre_pack: 应用题材结构与反模式规则", inputSchema={"type":"object","properties":{"key":{"type":"string"}},"required":["key"]}),
        types.Tool(name="generate_story_sandbox", description="generate_story_sandbox: 隔离生成三个剧情候选方向", inputSchema={"type":"object","properties":{"question":{"type":"string"}},"required":["question"]}),
        types.Tool(name="list_story_sandboxes", description="list_story_sandboxes: 查看剧情沙盒", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="adopt_story_sandbox", description="adopt_story_sandbox: 采纳候选并写入下一章目标", inputSchema={"type":"object","properties":{"sandbox_id":{"type":"string"},"variant_id":{"type":"string"}},"required":["sandbox_id","variant_id"]}),
        types.Tool(name="evaluate_long_form", description="evaluate_long_form: 运行无额外模型调用的长篇一致性评测", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="get_planning_impacts", description="get_planning_impacts: 查看规划修改影响的未来章节", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="review_chapter_memory", description="review_chapter_memory: 确认忽略或编辑章节交接", inputSchema={"type":"object","properties":{"chapter":{"type":"integer"},"status":{"type":"string"},"edits":{"type":"object"}},"required":["chapter","status"]}),
        types.Tool(name="list_state_proposals", description="list_state_proposals: 查看待裁决权威状态变化", inputSchema={"type":"object","properties":{"status":{"type":"string"}},"required":[]}),
        types.Tool(name="decide_state_proposal", description="decide_state_proposal: 接受或拒绝权威状态变化", inputSchema={"type":"object","properties":{"proposal_id":{"type":"string"},"accept":{"type":"boolean"}},"required":["proposal_id","accept"]}),
        types.Tool(name="get_review_queue", description="get_review_queue: 查看统一审核队列", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="list_canonical_locks", description="list_canonical_locks: 查看权威设定锁", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="upsert_canonical_lock", description="upsert_canonical_lock: 添加或更新权威设定锁", inputSchema={"type":"object","properties":{"kind":{"type":"string"},"name":{"type":"string"},"field":{"type":"string"},"value":{"type":"string"},"reason":{"type":"string"}},"required":["kind","name","field","value"]}),
        types.Tool(name="remove_canonical_lock", description="remove_canonical_lock: 解除权威设定锁", inputSchema={"type":"object","properties":{"lock_id":{"type":"string"}},"required":["lock_id"]}),
        types.Tool(name="get_story_clock", description="get_story_clock: 查看故事时钟和移动规则", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="set_travel_rule", description="set_travel_rule: 设置地点间最短移动分钟", inputSchema={"type":"object","properties":{"origin":{"type":"string"},"destination":{"type":"string"},"minutes":{"type":"integer"}},"required":["origin","destination","minutes"]}),
        types.Tool(name="remove_travel_rule", description="remove_travel_rule: 删除地点移动规则", inputSchema={"type":"object","properties":{"origin":{"type":"string"},"destination":{"type":"string"}},"required":["origin","destination"]}),
        types.Tool(name="get_author_preferences", description="get_author_preferences: 查看从人工修改学习的抽象写作偏好", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="list_prompt_snapshots", description="list_prompt_snapshots: 查看最终Prompt快照与基线", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="compare_prompt_snapshot", description="compare_prompt_snapshot: 对比任务Prompt与基线", inputSchema={"type":"object","properties":{"task_type":{"type":"string"}},"required":["task_type"]}),
        types.Tool(name="set_prompt_baseline", description="set_prompt_baseline: 将当前最终Prompt设为回归基线", inputSchema={"type":"object","properties":{"task_type":{"type":"string"}},"required":["task_type"]}),
        types.Tool(name="evaluate_rag", description="evaluate_rag: 使用黄金问题评测语义检索", inputSchema={"type":"object","properties":{"cases":{"type":"array","items":{"type":"object"}},"top_k":{"type":"integer"}},"required":["cases"]}),
        types.Tool(name="rebuild_imported_novel", description="rebuild_imported_novel: 从当前已有正文重建人物总纲状态和记忆", inputSchema={"type":"object","properties":{"batch_size":{"type":"integer"}},"required":[]}),
        types.Tool(name="revise_history", description="revise_history: 分析并事务式修改已经发生的剧情事实", inputSchema={"type":"object","properties":{"source_chapter":{"type":"integer"},"old_fact":{"type":"string"},"new_fact":{"type":"string"},"instruction":{"type":"string"},"mode":{"type":"string"},"auto_commit":{"type":"boolean"}},"required":["source_chapter","old_fact","new_fact"]}),
        types.Tool(name="list_history_revisions", description="list_history_revisions: 查看历史剧情修改分支", inputSchema={"type":"object","properties":{},"required":[]}),
        types.Tool(name="commit_history_revision", description="commit_history_revision: 提交已验证历史修改分支", inputSchema={"type":"object","properties":{"revision_id":{"type":"string"}},"required":["revision_id"]}),
        types.Tool(name="abort_history_revision", description="abort_history_revision: 废弃未提交历史修改分支", inputSchema={"type":"object","properties":{"revision_id":{"type":"string"}},"required":["revision_id"]}),
    ]

async def list_novels():
    n = workspace.list_novels()
    return "\n".join(f"{i}. {x['name']} [{x['status']}] {x['chapter_count']}ch" for i,x in enumerate(n,1)) if n else "Empty"

async def create_novel(name, genre="", style="", description=""):
    workspace.create_novel(name, genre, style, description)
    return f"Created: {name}"

async def open_novel(name):
    i = workspace.open_novel(name)
    return f"Switched to: {i['name']} (ch:{i['current_chapter']})"

async def get_novel_status():
    i = current_novel_info()
    parts = [f"{i['name']} [{i['status']}] ch:{i['current_chapter']} words:{i['total_words']}"]
    c = crm().list_characters()
    if c: parts.append("Chars: " + ", ".join(x['name'] for x in c[:5]))
    s = chm().get_recent_summaries(2)
    for x in reversed(s): parts.append(f"ch{x['chapter']}: {str(x.get('summary',''))[:80]}")
    g = i.get("next_goal","")
    if g: parts.append("Goal: " + g)
    return "\n".join(parts)

async def continue_story(query="", target_words=3000, focus_character=""):
    ctx = await asyncio.to_thread(
        lambda: ctxm().get_continue_context(
            query=query or None, focus_character=focus_character or None,
        )
    )
    from config import estimate_target_tokens
    ctx["target_words"] = target_words
    ctx["gen_estimate"] = estimate_target_tokens(target_words)
    return json.dumps(ctx, ensure_ascii=False, indent=2)

async def save_chapter(chapter_number, content, allow_fact_conflicts=False, allow_locked_changes=False,
                       allow_story_clock_conflicts=False, allow_character_decision_conflicts=False,
                       allow_degraded_summary=False):
    def save():
        info = current_novel_info()
        manager = chm()
        engine = turne(manager)
        turn = engine.save_draft(chapter_number, content, 5000, "mcp", {}, False)

        def index_chapter(number, text):
            vector_store = get_vs()
            if vector_store:
                vector_store.add_document(info["name"], number, text)

        return engine.commit(
            turn["id"], index_chapter, True, bool(allow_fact_conflicts), False,
            bool(allow_locked_changes), bool(allow_story_clock_conflicts),
            bool(allow_character_decision_conflicts),
            bool(allow_degraded_summary),
        )["result"]

    result = await asyncio.to_thread(save)
    return f"ch{chapter_number} saved ({result['words']} words)"

async def append_chapter(chapter_number, content, allow_fact_conflicts=False, allow_locked_changes=False,
                         allow_story_clock_conflicts=False, allow_character_decision_conflicts=False,
                         allow_degraded_summary=False):
    def append():
        manager = chm()
        chapter_path = manager.path / config.CHAPTER_FILE_PATTERN.format(int(chapter_number))
        existing = chapter_path.read_text("utf-8", errors="replace") if chapter_path.exists() else ""
        full_content = existing.rstrip("\r\n") + "\n" + content if existing else content
        engine = turne(manager)
        turn = engine.save_draft(
            chapter_number, full_content, max(500, len(full_content)), "mcp_append", {}, False,
        )

        def index_chapter(number, text):
            vector_store = get_vs()
            if vector_store:
                vector_store.add_document(current_novel_info()["name"], number, text)

        engine.commit(
            turn["id"], index_chapter, True, bool(allow_fact_conflicts), False,
            bool(allow_locked_changes), bool(allow_story_clock_conflicts),
            bool(allow_character_decision_conflicts),
            bool(allow_degraded_summary),
        )

    await asyncio.to_thread(append)
    return f"ch{chapter_number} appended"

async def read_chapter(chapter_number):
    r = chm().read_chapter(chapter_number)
    return r or f"ch{chapter_number} not found"

async def get_context(max_tokens=None, query="", focus_character=""):
    return await asyncio.to_thread(
        lambda: ctxm().build_context(
            max_tokens=max_tokens, query=query or None,
            focus_character=focus_character or None,
        )
    )

async def update_next_goal(goal):
    nm().update_next_goal(goal)
    return "Goal updated"

async def update_novel_status(status):
    i = current_novel_info()
    workspace.update_status(i["name"], status)
    return "Status updated"

async def index_chapter_to_vector(chapter_number):
    vs = get_vs()
    if not vs: return "Vector store unavailable"
    c = chm().read_chapter(chapter_number)
    if not c: return f"ch{chapter_number} not found"
    vs.add_document(current_novel_info()["name"], chapter_number, c)
    return "Indexed"

async def create_character(name, personality="", background="", abilities="", ability_level="凡人", relationships="", status="存活"):
    crm().create_character(name, personality, background, abilities, ability_level, relationships, status)
    return f"Character {name} created [{ability_level}]"

async def update_character(name, **kw):
    crm().update_character(name, **{k:v for k,v in kw.items() if v is not None})
    return f"{name} updated"

async def get_character(name):
    d = crm().get_character(name)
    return json.dumps(d, ensure_ascii=False, indent=2) if d else f"Not found: {name}"

async def list_characters():
    c = crm().list_characters()
    return "\n".join(f"{i}. {x['name']} [{x['status']}] [{x['ability_level']}] ch{x['last_chapter']}" for i,x in enumerate(c,1)) if c else "Empty"

async def get_character_network():
    return json.dumps(crm().get_character_network(), ensure_ascii=False, indent=2)

async def add_event(chapter, time, location, event, characters=""):
    cl = [c.strip() for c in characters.split(",") if c.strip()] if characters else []
    tlm().add_event(chapter, time, location, event, cl)
    return f"Event added: ch{chapter} {location}"

async def query_timeline(character="", chapter=None, keyword="", limit=20):
    r = tlm().query_timeline(character=character or None, chapter=chapter, keyword=keyword or None, limit=limit)
    return json.dumps(r, ensure_ascii=False, indent=2) if r else "No matches"

async def check_consistency():
    issues = csm().check_all()
    if not issues: return "PASS"
    return "\n".join(f"{i}. [{x['severity']}] {x['detail'][:80]}" for i,x in enumerate(issues,1))

async def search_memory(query):
    try:
        nm().get_state()
    except ValueError as _e:
        return f"Error: {_e}"
    vs = get_vs()
    if vs:
        try:
            r = vs.search(query, novel=current_novel_info()["name"], top_k=5)
            if r: return "\n".join(f"ch{x['chapter']}: {x['snippet'][:100]}" for x in r)
        except Exception as _se: logger.debug('search failed: %s', _se)
    ch_dir = nm().path / "chapters"
    res = []
    for f in sorted((path for path in ch_dir.glob("*.txt") if path.stem.isdigit()), key=lambda path: int(path.stem)):
        try:
            c = f.read_text("utf-8", errors="replace")
        except Exception:
            continue
        if query in c:
            idx = c.find(query)
            res.append(f"ch{int(f.stem)}: ...{c[max(0,idx-30):idx+len(query)+30]}...")
    return "\n".join(res[:10]) if res else f"Not found: {query}"

async def analyze_chapter(chapter_number):
    c = chm().read_chapter(chapter_number)
    return json.dumps(writing_analyzer.analyze_chapter(chapter_number, c), ensure_ascii=False, indent=2) if c else f"ch{chapter_number} not found"

async def detect_writing_patterns(text):
    return json.dumps(writing_analyzer.detect_patterns(text), ensure_ascii=False, indent=2)

async def analyze_text_pacing(text):
    return json.dumps(writing_analyzer.analyze_pacing(text), ensure_ascii=False, indent=2)

async def create_savepoint(chapter_number, label=""):
    c = chm().read_chapter(chapter_number)
    return json.dumps(spm().create(chapter_number, c, label), ensure_ascii=False, indent=2) if c else f"ch{chapter_number} not found"

async def list_savepoints(chapter_number=None, limit=20):
    sps = spm().list_savepoints(chapter_number=chapter_number or None, limit=limit)
    return "\n".join(f"{s['id']}: ch{s['chapter']} {s.get('label','')}" for s in sps) if sps else "Empty"

async def restore_savepoint(chapter_number, savepoint_id):
    r = spm().restore(chapter_number, savepoint_id)
    return r or "Not found"

async def diff_savepoints(chapter_number, savepoint_a, savepoint_b=None):
    return spm().diff(chapter_number, savepoint_a, savepoint_b)

async def list_plugins():
    pl = plugin_mgr.list_plugins()
    return "\n".join(f"{p['name']} {'ON' if p['enabled'] else 'OFF'}" for p in pl) if pl else "Empty"

async def reload_plugins():
    plugin_mgr.reload()
    return "Plugins reloaded"

async def toggle_plugin(name, enabled):
    (plugin_mgr.enable if enabled else plugin_mgr.disable)(name)
    return f"{name} toggled"

async def report_quality_issue(chapter, issue_type, severity, description, suggestion=''):
    return json.dumps(qt_().add_debt(chapter, issue_type, severity, description, suggestion), ensure_ascii=False, indent=2)

async def get_quality_report():
    return json.dumps(qt_().get_report(), ensure_ascii=False, indent=2)

async def get_pending_issues(chapter=None):
    issues = qt_().get_pending_debts(chapter=chapter or None)
    return "\n".join(f"[{i['severity']}] ch{i['chapter']}: {i['description'][:50]}" for i in issues) if issues else "None pending"

async def scan_character_evolution(chapter_number):
    c = chm().read_chapter(chapter_number)
    if not c: return f"ch{chapter_number} not found"
    cev_().scan_chapter(chapter_number, c)
    return f"ch{chapter_number} scanned"

async def get_character_evolution(name):
    return cev_().get_evolution_report(name)

async def list_style_presets():
    presets = stm_().list_presets()
    return "\n".join(f"{p['name']}: {p['description'][:50]}" for p in presets)

async def get_style_preset(name):
    d = stm_().get_preset(name)
    return json.dumps(d, ensure_ascii=False, indent=2) if d else f"Not found: {name}"

async def save_style_preset(name, description, traits, avoid=None):
    stm_().save_preset(name, description, traits, avoid or [])
    return f"Style {name} saved"

async def extract_style_from_text(name, text):
    return json.dumps(stm_().extract_from_text(name, text), ensure_ascii=False, indent=2)

async def list_facts(limit=50):
    return json.dumps({"facts": facts_().recent(limit), "conflicts": facts_().unresolved_conflicts()}, ensure_ascii=False, indent=2)

async def list_foreshadowing():
    return json.dumps(fsh_().list(nm().get_current_chapter()), ensure_ascii=False, indent=2)

async def get_story_logic():
    return json.dumps(StoryLogicManager(nm().path, logger).get(), ensure_ascii=False, indent=2)

async def get_causal_graph():
    return json.dumps(CausalGraphManager(nm().path, logger, storage_mgr).build(nm().get_current_chapter()), ensure_ascii=False, indent=2)

async def propose_causal_repairs(window=3):
    return json.dumps(CausalRepairPlanner(nm().path, logger, storage_mgr).propose(int(window or 3)), ensure_ascii=False, indent=2)

async def apply_causal_repairs(proposal_id):
    result = await asyncio.to_thread(
        CausalRepairPlanner(nm().path, logger, storage_mgr).apply, proposal_id,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)

async def list_entities():
    return json.dumps(EntityLedger(nm().path, logger).get(), ensure_ascii=False, indent=2)

async def get_planning_reviews():
    return json.dumps(PlanningReviewManager(nm().path, logger).report(), ensure_ascii=False, indent=2)

async def get_chapter_briefs():
    return json.dumps(storage_mgr.safe_read_json(nm().path / "outline" / "chapter_briefs.json", {}), ensure_ascii=False, indent=2)

async def rewrite_scene(chapter, scene, instruction="", target_words=1200):
    llm = await asyncio.to_thread(get_llm)
    if not llm:
        raise RuntimeError("模型服务未连接")
    full = await asyncio.to_thread(lambda: chm().read_chapter(int(chapter)) or scene)
    system, prompt = scene_revision_prompts(current_novel_info()["name"], full, scene, instruction, int(target_words))
    result = await asyncio.to_thread(
        llm.chat, system, prompt,
        min(5000, max(800, int(target_words / 1.8) + 500)), 0.58,
    )
    return result.strip()

async def list_character_changes(status="pending"):
    return json.dumps(reviews_().list(status or None), ensure_ascii=False, indent=2)

async def decide_character_change(change_id, accept):
    return json.dumps(reviews_().decide(change_id, accept), ensure_ascii=False, indent=2)

async def export_novel(format_name):
    current = current_novel_info()
    path = await asyncio.to_thread(
        ExportManager(current["name"], nm().path, logger).export, format_name,
    )
    return f"Exported: {path}"

async def where_was_i():
    s = nm().get_state()
    summaries = chm().get_recent_summaries(1)
    chars = crm().list_characters()
    last = summaries[0].get("summary","")[:200] if summaries else ""
    active = [c["name"] for c in chars[:5] if c["last_chapter"] >= s.get("current_chapter",0)-2] if chars else []
    lines = [f"ch{s.get('current_chapter',0)} | {s.get('total_words',0)} words | Goal: {s.get('next_goal','-')}", f"Last: {last}", f"Active: {', '.join(active) if active else '-'}"]
    if s.get("current_chapter",0) > 0:
        lines.append(f"Next: ch{s['current_chapter']+1} (continue_story target_words=3000)")
    else:
        lines.append("Next: create_character -> save_chapter 1")
    r = "\n".join(lines)
    return r + f"\n[~{estimate_tokens(r)} tokens]"

async def get_model_config():
    from config import get_model_config_report
    return json.dumps(get_model_config_report(), ensure_ascii=False, indent=2)

async def resume_session():
    return json.dumps(ctxm().resume_session(), ensure_ascii=False, indent=2)

async def list_ai_actions():
    return json.dumps(registered_ai_actions(), ensure_ascii=False, indent=2)

async def list_workflows():
    return json.dumps(registered_workflows(), ensure_ascii=False, indent=2)

async def get_scene_outlines(chapter=None):
    data = scenes_().get(int(chapter)) if chapter else scenes_().list()
    return json.dumps(data, ensure_ascii=False, indent=2)

async def save_scene_outline(chapter, outline):
    outline = dict(outline or {})
    outline["status"] = "confirmed"
    return json.dumps(scenes_().save(int(chapter), outline), ensure_ascii=False, indent=2)

async def get_state_cards():
    return json.dumps(states_().get(), ensure_ascii=False, indent=2)

async def upsert_state_card(kind, name, chapter, fields, evidence=""):
    return json.dumps(states_().upsert(kind, name, int(chapter), dict(fields or {}), evidence, "mcp"), ensure_ascii=False, indent=2)

async def list_genre_packs():
    return json.dumps(genres_().list(), ensure_ascii=False, indent=2)

async def apply_genre_pack(key):
    return json.dumps(genres_().apply(key), ensure_ascii=False, indent=2)

async def generate_story_sandbox(question):
    llm = await asyncio.to_thread(get_llm)
    if not llm:
        raise RuntimeError("模型服务未连接")
    context = await asyncio.to_thread(lambda: ctxm().build_context(query=question, profile="brief"))
    system = BASE_SYSTEM + "\n你是剧情分支设计师。只输出JSON。生成三个因果成立、彼此不同且不直接写入正式规划的方向。"
    prompt = f'问题：{question}\n返回{{"variants":[{{"title":"","direction":"","benefits":[""],"risks":[""],"required_setup":[""]}}]}}。\n<context>\n{context}\n</context>'
    raw = await asyncio.to_thread(llm.chat, system, prompt, 1800, task_type="planning")
    record = await asyncio.to_thread(
        sandboxes_().save_variants,
        nm().get_current_chapter(), question, parse_object(raw).get("variants", []),
    )
    return json.dumps(record, ensure_ascii=False, indent=2)

async def list_story_sandboxes():
    return json.dumps(sandboxes_().list(), ensure_ascii=False, indent=2)

async def adopt_story_sandbox(sandbox_id, variant_id):
    variant = sandboxes_().adopt(sandbox_id, variant_id)
    nm().update_next_goal(variant["direction"])
    return json.dumps(variant, ensure_ascii=False, indent=2)

async def evaluate_long_form():
    result = await asyncio.to_thread(LongFormEvaluator(nm().path, logger, storage_mgr).run)
    return json.dumps(result, ensure_ascii=False, indent=2)

async def get_planning_impacts():
    return json.dumps(PlanningImpactManager(nm().path, logger, storage_mgr).list(), ensure_ascii=False, indent=2)

async def review_chapter_memory(chapter, status, edits=None):
    return json.dumps(chm().summary_mgr.review_memory(int(chapter), status, dict(edits or {})), ensure_ascii=False, indent=2)

async def list_state_proposals(status="pending"):
    return json.dumps(canonical_().list(status or None), ensure_ascii=False, indent=2)

async def decide_state_proposal(proposal_id, accept):
    return json.dumps(canonical_().decide(proposal_id, bool(accept)), ensure_ascii=False, indent=2)

async def get_review_queue():
    return json.dumps(ReviewQueueManager(nm().path, logger, storage_mgr).build(), ensure_ascii=False, indent=2)

async def list_canonical_locks():
    return json.dumps(CanonicalLockManager(nm().path, logger, storage_mgr).list(), ensure_ascii=False, indent=2)

async def upsert_canonical_lock(kind, name, field, value, reason=""):
    item = CanonicalLockManager(nm().path, logger, storage_mgr).upsert(kind, name, field, value, reason)
    return json.dumps(item, ensure_ascii=False, indent=2)

async def remove_canonical_lock(lock_id):
    return json.dumps({"removed": CanonicalLockManager(nm().path, logger, storage_mgr).remove(lock_id)}, ensure_ascii=False)

async def get_story_clock():
    return json.dumps(StoryClockManager(nm().path, logger, storage_mgr).get(), ensure_ascii=False, indent=2)

async def set_travel_rule(origin, destination, minutes):
    item = StoryClockManager(nm().path, logger, storage_mgr).set_travel_rule(origin, destination, int(minutes))
    return json.dumps(item, ensure_ascii=False, indent=2)

async def remove_travel_rule(origin, destination):
    removed = StoryClockManager(nm().path, logger, storage_mgr).remove_travel_rule(origin, destination)
    return json.dumps({"removed": removed}, ensure_ascii=False)

async def get_author_preferences():
    return json.dumps(AuthorPreferenceManager(nm().path, logger, storage_mgr).get(), ensure_ascii=False, indent=2)

async def list_prompt_snapshots():
    return json.dumps(PromptSnapshotManager(config.STORAGE_ROOT, logger).list_tasks(), ensure_ascii=False, indent=2)

async def compare_prompt_snapshot(task_type):
    return json.dumps(PromptSnapshotManager(config.STORAGE_ROOT, logger).compare(task_type), ensure_ascii=False, indent=2)

async def set_prompt_baseline(task_type):
    return json.dumps(PromptSnapshotManager(config.STORAGE_ROOT, logger).set_baseline(task_type), ensure_ascii=False, indent=2)

async def evaluate_rag(cases, top_k=5):
    store = get_vs()
    if not store:
        raise RuntimeError("向量模型不可用")
    return json.dumps(store.evaluate(cases, current_novel_info()["name"], int(top_k)), ensure_ascii=False, indent=2)

async def rebuild_imported_novel(batch_size=4):
    client = await asyncio.to_thread(get_llm)
    if not client:
        raise RuntimeError("本地模型未连接")
    result = await asyncio.to_thread(
        ImportRebuilder(nm(), logger, client, storage_mgr).rebuild,
        batch_size=max(1, min(8, int(batch_size))),
    )
    return json.dumps(result, ensure_ascii=False, indent=2)

async def revise_history(source_chapter, old_fact, new_fact, instruction="", mode="minimal_patch", auto_commit=True):
    client = await asyncio.to_thread(get_llm)
    if not client:
        raise RuntimeError("本地模型未连接")
    manager = HistoryRevisionManager(nm(), logger, client, storage_mgr)
    item = await asyncio.to_thread(manager.create, int(source_chapter), old_fact, new_fact, instruction, mode)
    item = await asyncio.to_thread(manager.run_branch, item["id"])
    if auto_commit and item.get("status") == "validated":
        item = await asyncio.to_thread(manager.commit, item["id"])
    return json.dumps(item, ensure_ascii=False, indent=2)

async def list_history_revisions():
    return json.dumps(HistoryRevisionManager(nm(), logger, None, storage_mgr).list(), ensure_ascii=False, indent=2)

async def commit_history_revision(revision_id):
    client = await asyncio.to_thread(get_llm)
    result = await asyncio.to_thread(
        HistoryRevisionManager(nm(), logger, client, storage_mgr).commit, revision_id,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)

async def abort_history_revision(revision_id):
    return json.dumps(HistoryRevisionManager(nm(), logger, None, storage_mgr).abort(revision_id), ensure_ascii=False, indent=2)

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list:
    """MCP tool call dispatch handler."""
    manager_token = None
    info_token = None
    try:
        if name not in HANDLERS:
            raise ValueError(f"Unknown tool: {name}")
        if name not in GLOBAL_TOOLS:
            manager, info = workspace.capture_current()
            manager_token = _bound_novel.set(manager)
            info_token = _bound_novel_info.set(info)
        handler = HANDLERS[name]
        if arguments:
            result = await handler(**arguments)
        else:
            result = await handler()
        if isinstance(result, str):
            return [types.TextContent(type="text", text=result)]
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
    except Exception as e:
        logger.error("Tool %s failed: %s", name, e)
        return [types.TextContent(type="text", text=f"Error: {e}")]
    finally:
        if info_token is not None:
            _bound_novel_info.reset(info_token)
        if manager_token is not None:
            _bound_novel.reset(manager_token)

async def main():
    logger.info('FastMCP waiting...')
    async with stdio_server() as (r, w):
        await app.run(r, w, InitializationOptions(
            server_name=SERVER_NAME, server_version=SERVER_VERSION,
            capabilities=app.get_capabilities(notification_options=None, experimental_capabilities=None),
        ))

def run(): asyncio.run(main())

HANDLERS = {
    'list_novels': list_novels,
    'create_novel': create_novel,
    'open_novel': open_novel,
    'get_novel_status': get_novel_status,
    'continue_story': continue_story,
    'save_chapter': save_chapter,
    'append_chapter': append_chapter,
    'read_chapter': read_chapter,
    'get_context': get_context,
    'update_next_goal': update_next_goal,
    'update_novel_status': update_novel_status,
    'index_chapter_to_vector': index_chapter_to_vector,
    'create_character': create_character,
    'update_character': update_character,
    'get_character': get_character,
    'list_characters': list_characters,
    'get_character_network': get_character_network,
    'add_event': add_event,
    'query_timeline': query_timeline,
    'check_consistency': check_consistency,
    'search_memory': search_memory,
    'analyze_chapter': analyze_chapter,
    'detect_writing_patterns': detect_writing_patterns,
    'analyze_text_pacing': analyze_text_pacing,
    'create_savepoint': create_savepoint,
    'list_savepoints': list_savepoints,
    'restore_savepoint': restore_savepoint,
    'diff_savepoints': diff_savepoints,
    'list_plugins': list_plugins,
    'reload_plugins': reload_plugins,
    'toggle_plugin': toggle_plugin,
    'report_quality_issue': report_quality_issue,
    'get_quality_report': get_quality_report,
    'get_pending_issues': get_pending_issues,
    'scan_character_evolution': scan_character_evolution,
    'get_character_evolution': get_character_evolution,
    'list_style_presets': list_style_presets,
    'get_style_preset': get_style_preset,
    'save_style_preset': save_style_preset,
    'extract_style_from_text': extract_style_from_text,
    'list_facts': list_facts,
    'list_foreshadowing': list_foreshadowing,
    'get_story_logic': get_story_logic,
    'get_causal_graph': get_causal_graph,
    'propose_causal_repairs': propose_causal_repairs,
    'apply_causal_repairs': apply_causal_repairs,
    'list_entities': list_entities,
    'get_planning_reviews': get_planning_reviews,
    'get_chapter_briefs': get_chapter_briefs,
    'rewrite_scene': rewrite_scene,
    'list_character_changes': list_character_changes,
    'decide_character_change': decide_character_change,
    'export_novel': export_novel,
    'where_was_i': where_was_i,
    'get_model_config': get_model_config,
    'resume_session': resume_session,
    'list_ai_actions': list_ai_actions,
    'list_workflows': list_workflows,
    'get_scene_outlines': get_scene_outlines,
    'save_scene_outline': save_scene_outline,
    'get_state_cards': get_state_cards,
    'upsert_state_card': upsert_state_card,
    'list_genre_packs': list_genre_packs,
    'apply_genre_pack': apply_genre_pack,
    'generate_story_sandbox': generate_story_sandbox,
    'list_story_sandboxes': list_story_sandboxes,
    'adopt_story_sandbox': adopt_story_sandbox,
    'evaluate_long_form': evaluate_long_form,
    'get_planning_impacts': get_planning_impacts,
    'review_chapter_memory': review_chapter_memory,
    'list_state_proposals': list_state_proposals,
    'decide_state_proposal': decide_state_proposal,
    'get_review_queue': get_review_queue,
    'list_canonical_locks': list_canonical_locks,
    'upsert_canonical_lock': upsert_canonical_lock,
    'remove_canonical_lock': remove_canonical_lock,
    'get_story_clock': get_story_clock,
    'set_travel_rule': set_travel_rule,
    'remove_travel_rule': remove_travel_rule,
    'get_author_preferences': get_author_preferences,
    'list_prompt_snapshots': list_prompt_snapshots,
    'compare_prompt_snapshot': compare_prompt_snapshot,
    'set_prompt_baseline': set_prompt_baseline,
    'evaluate_rag': evaluate_rag,
    'rebuild_imported_novel': rebuild_imported_novel,
    'revise_history': revise_history,
    'list_history_revisions': list_history_revisions,
    'commit_history_revision': commit_history_revision,
    'abort_history_revision': abort_history_revision,
}

GLOBAL_TOOLS = {
    "list_novels", "create_novel", "open_novel",
    "list_plugins", "reload_plugins", "toggle_plugin",
    "detect_writing_patterns", "analyze_text_pacing",
    "get_model_config", "list_ai_actions", "list_workflows",
    "list_prompt_snapshots", "compare_prompt_snapshot", "set_prompt_baseline",
}

if __name__ == '__main__':
    run()
