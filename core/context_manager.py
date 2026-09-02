"""上下文管理器：精准 Token 预算 + 会话恢复 + 优先级分档。"""
import json

from config import MODEL_CONFIG, RECENT_SUMMARIES_COUNT, estimate_tokens, trim_to_token_limit
from core.novel_manager import NovelManager
from core.chapter_manager import ChapterManager
from core.character_manager import CharacterManager
from core.timeline_manager import TimelineManager
from core.fact_manager import FactManager
from core.foreshadow_manager import ForeshadowManager
from core.story_logic_manager import StoryLogicManager
from core.entity_ledger import EntityLedger
from core.creative_assets import CreativeAssetManager
from core.ai_contracts import chapter_source_hash
from core.genre_pack_manager import GenrePackManager
from core.scene_outline_manager import SceneOutlineManager
from core.state_card_manager import StateCardManager
from core.author_preference_manager import AuthorPreferenceManager
from core.planning_review_manager import PlanningReviewManager
from core.change_review_manager import ChangeReviewManager
from core.canonical_lock_manager import CanonicalLockManager
from core.story_clock_manager import StoryClockManager


# 128K 上下文的分层预算。实际构建时会按 max_tokens 等比缩放，
# 因而短分析任务不会被长篇正文挤满，章节写作则能利用完整的近期叙事。
REFERENCE_CONTEXT_TOKENS = 96000
CONTEXT_BUDGET = {
    "authority_protocol": 900,
    "project_state": 900,
    "session_brief": 2500,
    "next_goal": 500,
    "chapter_blueprint": 8000,
    "repair_constraints": 2500,
    "continuity_handoff": 2500,
    "scene_outline": 5000,
    "state_cards": 5000,
    "genre_pack": 800,
    "global_outline": 7000,
    "style_contract": 4000,
    "recent_chapters": 30000,
    "recent_summaries": 9000,
    "characters": 7000,
    "canonical_characters": 4500,
    "creative_assets": 5000,
    "vector_retrieval": 4000,
    "timeline": 3000,
    "world_rules": 10000,
    "unresolved_foreshadow": 3000,
    "facts": 4000,
}

CONTEXT_AUTHORITY_PROTOCOL = """【上下文权威顺序（发生冲突时从上到下覆盖）】
1. 用户权威设定锁、已确认事实账本、权威人物名册、故事时钟与行程约束。
2. 上一章连续性交接、实时状态卡、人物认知与已经提交的正文结果。
3. 当前章前提要和场景细纲、当前节纲、当前卷纲、全书总纲。
4. 世界规则、叙事承诺、伏笔、创作资产、抽象文风规范与检索资料。
执行规则：不得把计划当成已发生事实；不得用旧提要覆盖较新的正文结果；不得串用人物、地点、物品或时间；
新增重大事实必须有当前任务明确要求或正文证据，信息不足时保守留白。"""


class ContextManager:
    def __init__(self, novel_manager: NovelManager, logger,
                 vector_store=None, llm_client=None):
        self.nm = novel_manager
        self.path = novel_manager.path
        self.logger = logger
        self.chapter_mgr = ChapterManager(novel_manager, logger, llm_client)
        self.char_mgr = CharacterManager(novel_manager.path, logger)
        self.timeline_mgr = TimelineManager(novel_manager.path, logger)
        self.fact_mgr = FactManager(novel_manager.path, logger)
        self.foreshadow_mgr = ForeshadowManager(novel_manager.path, logger)
        self.story_logic_mgr = StoryLogicManager(novel_manager.path, logger)
        self.entity_ledger = EntityLedger(novel_manager.path, logger)
        self.creative_assets = CreativeAssetManager(novel_manager.path, logger)
        self.vector_store = vector_store
        self.llm = llm_client
        self.last_build_stats = {}

    def build_context(self, max_tokens: int = None, query: str = None,
                      focus_character: str = None,
                      for_new_session: bool = False,
                      profile: str = "prose") -> str:
        """构建写作上下文，按优先级分配 token。

        Args:
            max_tokens: 最大 token 预算，默认使用 MODEL_CONFIG.available_context
            query: 向量检索关键词
            for_new_session: 是否为全新会话（包含更全面的简报）
        """
        if max_tokens is None:
            profiles = MODEL_CONFIG.get("context_profiles", {})
            max_tokens = int(profiles.get(profile, MODEL_CONFIG["available_context"]))
            max_tokens = min(max_tokens, MODEL_CONFIG["available_context"])
        max_tokens = max(0, int(max_tokens))
        if max_tokens == 0:
            self.last_build_stats = {
                "tokens": 0, "budget": 0, "usage_percent": 0.0, "health": "green",
                "recent_full_chapters": 0, "continuity_handoff": False, "segments": [],
                "semantic_query": query or "", "profile": profile, "hard_truncated": False,
                "planning_epoch": self._planning_epoch(),
            }
            return ""
        parts = []
        remaining = max_tokens

        scale = min(1.0, max_tokens / REFERENCE_CONTEXT_TOKENS)

        def budget(name: str) -> int:
            return min(remaining, max(32, int(CONTEXT_BUDGET[name] * scale)))

        state = self.nm.get_state()
        if not query:
            query = str(state.get("next_goal", "")).strip() or None

        # 用户权威锁必须先于任何可裁剪的创作资料，避免短上下文任务遗漏硬约束。
        lock_context = CanonicalLockManager(self.path, self.logger, self.nm.storage).compact_context()
        if lock_context and remaining > 0:
            block = trim_to_token_limit(lock_context, min(2500, remaining))
            parts.append(block)
            remaining -= estimate_tokens(block)

        if max_tokens >= 1000 and remaining > 0:
            protocol_budget = min(remaining, max(256, int(CONTEXT_BUDGET["authority_protocol"] * scale)))
            block = trim_to_token_limit(CONTEXT_AUTHORITY_PROTOCOL, protocol_budget)
            parts.append(block)
            remaining -= estimate_tokens(block)

        if remaining > 0:
            project_state = {
                "novel": self.nm.name,
                "current_chapter": self._int(state.get("current_chapter")),
                "target_chapters": self._int(state.get("target_chapters")),
                "status": state.get("status", "创作中"),
                "ending_direction": state.get("ending_direction", ""),
                "planning_completed": bool(state.get("planning_completed")),
            }
            block = trim_to_token_limit(
                "【项目全局状态（权威）】\n" + json.dumps(project_state, ensure_ascii=False),
                min(remaining, max(128, int(CONTEXT_BUDGET["project_state"] * scale))),
            )
            parts.append(block)
            remaining -= estimate_tokens(block)

        # 人物名册是全局权威状态，必须早于章纲、近期正文与检索资料进入上下文。
        canonical_characters = []
        next_chapter_for_context = self._int(state.get("current_chapter")) + 1
        for item in self.char_mgr.list_characters()[:16]:
            detail = self.char_mgr.get_character(item["name"]) or {}
            start = self._int(detail.get("appearance_start", item.get("appearance_start", 1)), 1)
            end = self._int(detail.get("appearance_end", item.get("appearance_end", 0)), 0)
            availability = "future_reserved" if start > next_chapter_for_context else "inactive" if end and next_chapter_for_context > end else "active"
            canonical_characters.append({
                "name": item["name"],
                "role": detail.get("role_tier") or item.get("role_tier") or "未分级",
                "current_status": detail.get("current_status") or item.get("status") or "未知",
                "background": detail.get("background", ""),
                "relationships": detail.get("relationships", ""),
                "appearance_start": start,
                "appearance_end": end,
                "availability_for_chapter": availability,
            })
        if canonical_characters and remaining > 0:
            roster_text = (
                "【权威人物名册（最高优先级硬约束）】\n"
                "姓名是唯一身份标识。不得串用人物身份、职业、经历、生死状态、关系或行动；"
                "需要新死者、受害者或路人时必须另取不重名的新姓名。"
                "future_reserved人物只用于防止重名和身份串用，在到达appearance_start前不得出场、行动或被当前人物认识。\n"
                + json.dumps(canonical_characters, ensure_ascii=False)
            )
            roster_budget = min(remaining, max(512, int(CONTEXT_BUDGET["canonical_characters"] * scale)))
            block = trim_to_token_limit(roster_text, roster_budget)
            parts.append(block)
            remaining -= estimate_tokens(block)

        # 稳定前缀必须位于最前：跨章节不变的内容可被 llama.cpp 前缀缓存复用。
        style_file = self.path / "bible" / "style.md"
        if style_file.exists() and remaining > 0:
            style = style_file.read_text("utf-8", errors="replace").strip()
            if style:
                block = trim_to_token_limit("【抽象文风执行规范】\n" + style, budget("style_contract"))
                parts.append(block)
                remaining -= estimate_tokens(block)

        author_preferences = AuthorPreferenceManager(self.path, self.logger).context()
        if author_preferences and remaining > 0:
            preference_budget = min(remaining, max(32, int(800 * scale)))
            block = trim_to_token_limit(author_preferences, preference_budget)
            parts.append(block)
            remaining -= estimate_tokens(block)

        genre_context = GenrePackManager(self.path, self.logger).context()
        if genre_context and remaining > 0:
            block = trim_to_token_limit(genre_context, budget("genre_pack"))
            parts.append(block)
            remaining -= estimate_tokens(block)

        world_budget = budget("world_rules")
        bible_dir = self.path / "bible"
        bible_labels = {
            "rules.md": "【世界硬规则（权威约束）】",
            "world.md": "【故事世界、主题与结局方向（权威设定）】",
        }
        for fname in ["rules.md", "world.md"]:
            fp = bible_dir / fname
            if not fp.exists() or remaining <= 0 or world_budget <= 0:
                continue
            text = fp.read_text("utf-8", errors="replace").strip()
            if not text:
                continue
            block = trim_to_token_limit(bible_labels[fname] + "\n" + text, min(world_budget, remaining))
            block_tokens = estimate_tokens(block)
            parts.append(block)
            world_budget -= block_tokens
            remaining -= block_tokens

        outline_file = self.path / "outline" / "main.md"
        if outline_file.exists() and remaining > 0:
            outline = outline_file.read_text("utf-8", errors="replace").strip()
            if outline:
                block = trim_to_token_limit("【全书总纲（计划目标，不等于已发生事实）】\n" + outline, budget("global_outline"))
                parts.append(block)
                remaining -= estimate_tokens(block)

        # 1. 新会话简报（仅新对话）
        if for_new_session:
            brief = self._build_session_brief(state)
            brief_tokens = estimate_tokens(brief)
            if brief_tokens <= budget("session_brief"):
                parts.append(brief)
                remaining -= brief_tokens

        # 2. 当前写作目标（高优先级）
        next_goal = state.get("next_goal", "")
        if next_goal:
            goal_text = "【当前写作目标】\n" + next_goal + "\n"
            goal_tokens = estimate_tokens(goal_text)
            if goal_tokens <= budget("next_goal"):
                parts.append(goal_text)
                remaining -= goal_tokens

        facts = self.fact_mgr.recent(25)
        if facts and remaining > 0:
            fact_budget = budget("facts")
            fact_lines = ["【已确认事实账本（硬约束）】"]
            used = estimate_tokens(fact_lines[0])
            for fact in facts:
                line = f"  第{fact.get('chapter', '?')}章: {fact.get('subject', '')} · {fact.get('predicate', '')} = {fact.get('object', '')}"
                tokens = estimate_tokens(line)
                if used + tokens > fact_budget:
                    break
                fact_lines.append(line)
                used += tokens
            if len(fact_lines) > 1:
                parts.append("\n".join(fact_lines))
                remaining -= used

        clock_context = StoryClockManager(self.path, self.logger, self.nm.storage).compact_context()
        if clock_context and remaining > 0:
            block = trim_to_token_limit(clock_context, min(2500, remaining))
            parts.append(block)
            remaining -= estimate_tokens(block)

        # 2.1 用户在创建向导中确认的章节细纲与当前分卷目标
        next_chapter = self._int(state.get("current_chapter")) + 1
        blueprint_parts = []
        briefs = self.nm.storage.safe_read_json(self.path / "outline" / "chapter_briefs.json", {})
        current_brief = briefs.get(str(next_chapter)) if isinstance(briefs, dict) else None
        if isinstance(current_brief, dict) and current_brief:
            blueprint_parts.append("【当前章前提要（确认稿）】\n" + json.dumps(current_brief, ensure_ascii=False))
        repair_tasks = PlanningReviewManager(self.path, self.logger, self.nm.storage).pending_volume_repairs(next_chapter)
        if repair_tasks:
            blueprint_parts.append(
                "【上一卷遗留修复约束（pending优先在下一卷前3章内分配；deferred按延期说明处理，不要求全部塞入本章）】\n"
                + json.dumps(repair_tasks, ensure_ascii=False)
            )
        opening_file = self.path / "outline" / "opening_chapters.json"
        if opening_file.exists():
            opening = self.nm.storage.safe_read_json(opening_file, {})
            opening_chapters = opening.get("chapters", []) if isinstance(opening, dict) else []
            for item in opening_chapters:
                if not isinstance(item, dict):
                    continue
                if self._int(item.get("chapter")) == next_chapter:
                    blueprint_parts.append("【已确认的本章细纲】\n" + json.dumps(item, ensure_ascii=False))
                    break
        volumes_file = self.path / "outline" / "volumes.json"
        policy_file = self.path / "outline" / "narrative_policy.json"
        if policy_file.exists():
            policy = self.nm.storage.safe_read_json(policy_file, {})
            if isinstance(policy, dict) and policy:
                blueprint_parts.append("【叙事配比与自由度】\n" + json.dumps(policy, ensure_ascii=False))
        if volumes_file.exists():
            volumes = self.nm.storage.safe_read_json(volumes_file, [])
            for volume in volumes if isinstance(volumes, list) else []:
                if not isinstance(volume, dict):
                    continue
                if self._int(volume.get("start_chapter")) <= next_chapter <= self._int(volume.get("end_chapter")):
                    volume_summary = {key: value for key, value in volume.items() if key != "sections"}
                    blueprint_parts.append("【当前卷纲（计划目标）】\n" + json.dumps(volume_summary, ensure_ascii=False))
                    for section in volume.get("sections", []) if isinstance(volume.get("sections"), list) else []:
                        if not isinstance(section, dict):
                            continue
                        if self._int(section.get("start_chapter")) <= next_chapter <= self._int(section.get("end_chapter")):
                            blueprint_parts.append("【当前节纲（计划目标）】\n" + json.dumps(section, ensure_ascii=False))
                            break
                    break
        logic_context = self.story_logic_mgr.context(20)
        if logic_context and logic_context != '{"open_promises": [], "recent_causal_links": [], "character_knowledge": {}}':
            blueprint_parts.append("【叙事承诺、因果与人物信息权限】\n" + logic_context)
        entity_context = self.entity_ledger.compact_context()
        if any(entity_context.values()):
            blueprint_parts.append("【地点、势力、物品与人物关系】\n" + json.dumps(entity_context, ensure_ascii=False))
        pending_character_reviews = ChangeReviewManager(
            self.path, self.logger, self.nm.storage,
        ).list("pending")
        provisional_characters = [
            {"name": item.get("name"), **item.get("details", {}), "introduced_chapter": item.get("chapter")}
            for item in pending_character_reviews
            if isinstance(item, dict) and item.get("field") == "new_character"
            and isinstance(item.get("details"), dict)
        ]
        if provisional_characters:
            blueprint_parts.append(
                "【待确认的新人物临时档案（可用于保持连续性，但不得擅自扩写未确认背景）】\n"
                + json.dumps(provisional_characters[-12:], ensure_ascii=False)
            )
        if blueprint_parts:
            blueprint = "\n\n".join(blueprint_parts)
            blueprint = trim_to_token_limit(
                blueprint, min(remaining, budget("chapter_blueprint") + budget("repair_constraints")),
            )
            parts.append(blueprint)
            remaining -= estimate_tokens(blueprint)

        scene_outline = SceneOutlineManager(self.path, self.logger).render(next_chapter)
        if scene_outline and remaining > 0:
            block = trim_to_token_limit(scene_outline, budget("scene_outline"))
            parts.append(block)
            remaining -= estimate_tokens(block)

        continuity_included = False
        previous_chapter = next_chapter - 1
        if previous_chapter > 0 and remaining > 0:
            previous_content = self.chapter_mgr.read_chapter(previous_chapter) or ""
            previous_summary = self.chapter_mgr.summary_mgr.ensure_continuity_memory(previous_chapter, previous_content) if previous_content else {}
            handoff = previous_summary.get("handoff") if isinstance(previous_summary.get("handoff"), dict) else {}
            source_current = bool(previous_content) and previous_summary.get("source_hash") == chapter_source_hash(previous_content)
            if handoff and source_current:
                final_scene = handoff.get("final_scene") if isinstance(handoff.get("final_scene"), dict) else {}
                def join_values(value) -> str:
                    if isinstance(value, list):
                        return "；".join(str(item) for item in value if str(item).strip())
                    return str(value or "")
                lines = [f"【上一章连续性交接（第{previous_chapter}章，强约束）】"]
                for label, value in (
                    ("结尾地点", final_scene.get("location")),
                    ("结尾时间", final_scene.get("story_time")),
                    ("现场人物", join_values(final_scene.get("active_characters", []))),
                    ("最后动作", final_scene.get("last_action")),
                    ("状态变化", join_values(handoff.get("state_changes", []))),
                    ("认知变化", join_values(handoff.get("knowledge_changes", []))),
                    ("承诺与硬约束", join_values(handoff.get("commitments", []))),
                    ("未闭环", join_values(handoff.get("open_loops", []))),
                    ("紧接意图", handoff.get("immediate_next_intent")),
                ):
                    if value:
                        lines.append(f"{label}：{value}")
                quotes = handoff.get("evidence_quotes", [])
                if isinstance(quotes, list) and quotes:
                    lines.append("正文证据：" + "；".join(f"“{item}”" for item in quotes[:6]))
                reconciliation = previous_summary.get("plan_reconciliation", {})
                if isinstance(reconciliation, dict) and reconciliation.get("review_status") == "confirmed":
                    for label, key in (
                        ("前章未完成", "unfinished_goals"),
                        ("实际偏移", "deviations"),
                        ("新增约束", "new_constraints"),
                        ("下一章影响", "next_chapter_impacts"),
                    ):
                        values = reconciliation.get(key, [])
                        if values:
                            lines.append(f"{label}：" + "；".join(str(item) for item in values[:8]))
                if len(lines) > 1:
                    block = trim_to_token_limit("\n".join(lines), budget("continuity_handoff"))
                    parts.append(block)
                    remaining -= estimate_tokens(block)
                    continuity_included = True

        state_card_context = StateCardManager(self.path, self.logger).compact_context()
        if state_card_context and remaining > 0:
            block = trim_to_token_limit(state_card_context, budget("state_cards"))
            parts.append(block)
            remaining -= estimate_tokens(block)

        # 最近章节正文是长上下文最有价值的动态部分：保留场景语气、对话和连续动作。
        recent_chapter_budget = budget("recent_chapters")
        chapter_blocks = []
        used_chapter_tokens = 0
        chapter_files = sorted(
            (path for path in self.chapter_mgr.path.glob("*.txt") if path.stem.isdigit()),
            key=lambda path: int(path.stem), reverse=True,
        )[:6]
        for chapter_file in chapter_files:
            try:
                chapter_number = int(chapter_file.stem)
                content = chapter_file.read_text("utf-8", errors="replace").strip()
            except (OSError, ValueError):
                continue
            if not content:
                continue
            header = f"【第{chapter_number}章正文】\n"
            available = recent_chapter_budget - used_chapter_tokens - estimate_tokens(header)
            if available <= 32:
                break
            content_tokens = estimate_tokens(content)
            if content_tokens > available:
                content = self._trim_tail_to_token_limit(content, available)
            block = header + content
            block_tokens = estimate_tokens(block)
            if used_chapter_tokens + block_tokens > recent_chapter_budget:
                break
            chapter_blocks.append((chapter_number, block))
            used_chapter_tokens += block_tokens
        if chapter_blocks:
            chapter_blocks.sort(key=lambda item: item[0])
            parts.append("【最近章节正文（按时间顺序）】\n\n" + "\n\n".join(item[1] for item in chapter_blocks))
            remaining -= used_chapter_tokens

        # 5. 最近章节摘要与长期剧情弧用于补齐正文窗口之前的历史。
        long_term_file = self.path / "summaries" / "long_term.json"
        if long_term_file.exists():
            long_term = self.nm.storage.safe_read_json(long_term_file, {})
            arcs = long_term.get("arcs", []) if isinstance(long_term, dict) else []
            arcs = arcs if isinstance(arcs, list) else []
            if arcs:
                history = "【长期剧情弧摘要】\n" + "\n".join(
                    f"第{item.get('start_chapter')}—{item.get('end_chapter')}章：{item.get('summary', '')}"
                    for item in arcs[-4:] if isinstance(item, dict)
                )
                history = trim_to_token_limit(history, min(1800, remaining))
                parts.append(history)
                remaining -= estimate_tokens(history)

        recent = self.chapter_mgr.get_recent_summaries(RECENT_SUMMARIES_COUNT)
        if recent:
            summary_parts = ["【最近章节摘要】"]
            used = estimate_tokens("\n".join(summary_parts))
            summary_budget = budget("recent_summaries")
            for s in reversed(recent):
                line = "第{}章: {}".format(s.get("chapter", "?"), s.get("summary", "")[:300])
                line_tokens = estimate_tokens(line)
                if used + line_tokens > summary_budget:
                    break
                summary_parts.append(line)
                used += line_tokens
                # 附带伏笔信息（如果摘要里有关键内容）
                f_list = s.get("foreshadowing", [])
                if isinstance(f_list, list) and len(f_list) > 0:
                    f_text = "  伏笔: " + " | ".join(str(f)[:60] for f in f_list[:2])
                    f_tokens = estimate_tokens(f_text)
                    if used + f_tokens <= summary_budget:
                        summary_parts.append(f_text)
                        used += f_tokens
            summary_parts.append("")
            parts.append("\n".join(summary_parts))
            remaining -= used

        # 6. 主要人物档案与实时状态
        chars = self.char_mgr.list_characters(next_chapter)
        # If focus_character specified, show only that character
        if focus_character:
            chars = [c for c in chars if c['name'] == focus_character]
        if chars:
            char_parts = ["【主要人物状态】"]
            char_budget = budget("characters")
            used = estimate_tokens("\n".join(char_parts))
            for c in chars:
                detail = self.char_mgr.get_character(c["name"]) or {}
                voice = detail.get("voice_profile", {})
                line = "  {} [{}] [{}] 最后出场: 第{}章".format(
                    c["name"], c["status"], c["ability_level"], c["last_chapter"]
                )
                line_tokens = estimate_tokens(line)
                if used + line_tokens > char_budget:
                    break
                char_parts.append(line)
                used += line_tokens
                character_profile = {
                    key: detail.get(key)
                    for key in ("personality", "personality_profile", "background", "abilities", "relationships", "current_status", "locations")
                    if detail.get(key)
                }
                events = detail.get("important_events", [])
                if events:
                    character_profile["recent_important_events"] = events[-6:]
                if character_profile and used < char_budget:
                    profile_line = "    档案: " + json.dumps(character_profile, ensure_ascii=False)
                    profile_tokens = estimate_tokens(profile_line)
                    if used + profile_tokens <= char_budget:
                        char_parts.append(profile_line)
                        used += profile_tokens
                if voice and used < char_budget:
                    voice_line = "    语气规范: " + json.dumps(voice, ensure_ascii=False)
                    voice_tokens = estimate_tokens(voice_line)
                    if used + voice_tokens <= char_budget:
                        char_parts.append(voice_line)
                        used += voice_tokens
            parts.append("\n".join(char_parts))
            remaining -= used

        # 7. 用户维护的创作资产与语义检索
        assets = self.creative_assets.get()
        def compact_assets(kind: str, limit: int, text_limit: int = 360) -> list[dict]:
            result = []
            for item in assets.get(kind, [])[-limit:]:
                if not isinstance(item, dict):
                    continue
                result.append({
                    key: (value[:text_limit] if isinstance(value, str) else value)
                    for key, value in item.items()
                    if key not in {"id", "created_at", "updated_at", "source_file"}
                })
            return result
        active_assets = {
            "名词词典": compact_assets("glossary", 30, 180),
            "伤势状态": compact_assets("conditions", 15), "资源账本": compact_assets("resources", 15),
            "章节依赖": compact_assets("dependencies", 15), "场景计划": compact_assets("scenes", 8),
            "人物秘密": compact_assets("secrets", 12), "悬念问题": compact_assets("questions", 15),
            "支线": compact_assets("subplots", 12), "世界日历": compact_assets("calendar", 15),
            "地点": compact_assets("locations", 12), "旅行路线": compact_assets("routes", 12),
            "势力": compact_assets("factions", 10), "物品": compact_assets("items", 12),
            "研究资料摘要": compact_assets("research", 5, 240),
        }
        if any(active_assets.values()):
            asset_text = "【用户维护的创作资产】\n" + json.dumps(active_assets, ensure_ascii=False)
            asset_text = trim_to_token_limit(asset_text, budget("creative_assets"))
            parts.append(asset_text)
            remaining -= estimate_tokens(asset_text)

        if query and self.vector_store:
            vs_budget = budget("vector_retrieval")
            try:
                vs_parts = ["【相关记忆检索】"]
                vs_used = estimate_tokens("\n".join(vs_parts))
                results = self.vector_store.search(query, novel=self.nm.name, top_k=3)
                for r in results:
                    line = "  第{}章 (相关度:{:.2f}): {}".format(
                        r["chapter"], r["score"], r["snippet"][:150]
                    )
                    lt = estimate_tokens(line)
                    if vs_used + lt > vs_budget:
                        break
                    vs_parts.append(line)
                    vs_used += lt
                if len(vs_parts) > 1:
                    parts.append("\n".join(vs_parts))
                    remaining -= vs_used
            except Exception as e:
                self.logger.debug("向量检索失败: %s", e)

        # 8. 最近时间线事件
        events = self.timeline_mgr.get_recent_events(5)
        if events:
            tl_parts = ["【最近事件】"]
            tl_budget = budget("timeline")
            used = estimate_tokens("\n".join(tl_parts))
            for e in reversed(events):
                line = "  第{}章 {} {}: {}".format(
                    e.get("chapter", "?"), e.get("time", ""),
                    e.get("location", ""), e.get("event", "")[:100]
                )
                lt = estimate_tokens(line)
                if used + lt > tl_budget:
                    break
                tl_parts.append(line)
                used += lt
            parts.append("\n".join(tl_parts))
            remaining -= used

        open_foreshadows = self.foreshadow_mgr.open_items(state.get("current_chapter", 0), 12)
        if open_foreshadows:
            foreshadow_budget = budget("unresolved_foreshadow")
            lines = ["【待回收伏笔】"]
            used = estimate_tokens(lines[0])
            for item in open_foreshadows:
                marker = "[已超期]" if item.get("overdue") else f"[建议第{item['target_chapter']}章前]"
                line = f"  {marker} {item['text']}"
                tokens = estimate_tokens(line)
                if used + tokens > foreshadow_budget:
                    break
                lines.append(line)
                used += tokens
            parts.append("\n".join(lines))
            remaining -= used

        context = "\n\n".join(parts)
        hard_truncated = estimate_tokens(context) > max_tokens
        if hard_truncated:
            context = trim_to_token_limit(context, max_tokens)
        actual_tokens = estimate_tokens(context)
        usage_ratio = actual_tokens / max(1, max_tokens)
        segments = []
        for part in parts if not hard_truncated else [context]:
            first_line = part.splitlines()[0].strip() if part else "未命名上下文"
            label = first_line.strip("【】 ")[:40] or "未命名上下文"
            tokens = estimate_tokens(part)
            segments.append({
                "label": label,
                "tokens": tokens,
                "percent": round(tokens / max(1, actual_tokens) * 100, 1),
            })
        segments.sort(key=lambda item: item["tokens"], reverse=True)
        self.last_build_stats = {
            "tokens": actual_tokens,
            "budget": max_tokens,
            "usage_percent": round(usage_ratio * 100, 1),
            "health": "green" if usage_ratio < 0.70 else "yellow" if usage_ratio < 0.85 else "red",
            "recent_full_chapters": len(chapter_blocks),
            "continuity_handoff": continuity_included,
            "segments": segments,
            "semantic_query": query or "",
            "profile": profile,
            "hard_truncated": hard_truncated,
            "planning_epoch": self._planning_epoch(),
        }
        self.logger.info("上下文构建: %d tokens / %d 预算 (target: %d)",
                         actual_tokens, max_tokens - remaining, max_tokens)
        return context

    @staticmethod
    def _trim_tail_to_token_limit(text: str, max_tokens: int) -> str:
        if not text or max_tokens <= 0:
            return ""
        if estimate_tokens(text) <= max_tokens:
            return text
        marker = "[前文已按预算省略，保留本章后段]\n"
        content_budget = max(1, max_tokens - estimate_tokens(marker))
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if estimate_tokens(text[-mid:]) <= content_budget:
                lo = mid
            else:
                hi = mid - 1
        return marker + text[-lo:] if lo else marker.rstrip()

    @staticmethod
    def _int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _planning_epoch(self) -> str:
        data = self.nm.storage.safe_read_json(self.path / "planning" / "epoch.json", {})
        return str(data.get("id", "")) if isinstance(data, dict) else ""

    def _build_session_brief(self, state: dict) -> str:
        """构建新会话简报——LLM 冷启动时需要的全景概览。"""
        total_ch = state.get("current_chapter", 0)
        total_words = state.get("total_words", 0)
        chars = self.char_mgr.list_characters()[:10]

        brief = ["=== 小说状态简报（新会话） ==="]
        brief.append(f"当前进度: 第{total_ch}章 / 共{total_words}字")
        brief.append(f"活跃人物: {len(chars)} 人")
        if chars:
            brief.append("主要人物: " + ", ".join(
                f"{c['name']}[{c['ability_level']}]" for c in chars[:8]
            ))
        goal = state.get("next_goal", "")
        if goal:
            brief.append(f"写作目标: {goal}")
        last_summary = state.get("last_summary", "")
        if last_summary:
            brief.append(f"上章提要: {last_summary[:200]}")
        brief.append("(以上为恢复会话所需的最小上下文)")
        return "\n".join(brief)

    def get_continue_context(self, query: str = None, focus_character: str = None) -> dict:
        """准备续写上下文。"""
        state = self.nm.get_state()
        current = state.get("current_chapter", 0)
        context_text = self.build_context(query=query, focus_character=focus_character)
        return {
            "novel_name": self.nm.name,
            "current_chapter": current + 1,
            "total_words": state.get("total_words", 0),
            "total_chapters": current,
            "next_goal": state.get("next_goal", ""),
            "context": context_text,
            "context_tokens": estimate_tokens(context_text),
            "model_context_window": MODEL_CONFIG["context_window"],
            "reserved_for_output": MODEL_CONFIG["max_output_tokens"],
        }

    def resume_session(self) -> dict:
        """为全新对话生成会话恢复包。包含精简的全局概览。"""
        state = self.nm.get_state()
        context = self.build_context(for_new_session=True)
        chars = self.char_mgr.list_characters()
        events = self.timeline_mgr.get_recent_events(5)

        return {
            "action": "session_resume",
            "novel_name": self.nm.name,
            "status": state.get("status", "创作中"),
            "current_chapter": state.get("current_chapter", 0),
            "total_words": state.get("total_words", 0),
            "next_goal": state.get("next_goal", ""),
            "last_summary": state.get("last_summary", "")[:300],
            "character_count": len(chars),
            "recent_event_count": len(events),
            "context": context,
            "context_tokens": estimate_tokens(context),
            "available_tokens": MODEL_CONFIG["available_context"],
            "recommended_first_step": f"使用 get_context 获取完整上下文后继续第{state.get('current_chapter',0)+1}章",
        }
