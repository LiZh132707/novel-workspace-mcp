"""AI 动作单一事实源：统一声明读取、写回、提示词与输出契约。"""
from __future__ import annotations

from copy import deepcopy


AI_ACTIONS = {
    "title": {"label": "小说命名", "category": "创建", "reads": ["source"], "writes": ["novel.name"], "prompt_key": "title", "profile": "brief", "output": "text", "review": True},
    "source_summary": {"label": "源数据归纳", "category": "创建", "reads": ["source"], "writes": ["story_seed"], "prompt_key": "planning", "profile": "brief", "output": "story_seed", "review": True},
    "foundation": {"label": "故事基础设定", "category": "策划", "reads": ["story_seed"], "writes": ["bible.world", "bible.rules"], "prompt_key": "foundation", "profile": "planning", "output": "foundation", "review": True},
    "characters": {"label": "主要人物", "category": "策划", "reads": ["story_seed", "world"], "writes": ["characters"], "prompt_key": "characters", "profile": "planning", "output": "characters", "review": True},
    "structure": {"label": "总纲与分卷", "category": "策划", "reads": ["story_seed", "world", "characters"], "writes": ["outline.main", "outline.volumes"], "prompt_key": "structure", "profile": "planning", "output": "volumes", "review": True},
    "chapter_brief": {"label": "章前提要", "category": "章节", "reads": ["outline", "continuity", "state_cards"], "writes": ["outline.chapter_briefs"], "prompt_key": "chapter_brief", "profile": "brief", "output": "chapter_brief", "review": True},
    "chapter_plan": {"label": "场景细纲", "category": "章节", "reads": ["chapter_brief", "continuity", "state_cards"], "writes": ["outline.scene_outlines"], "prompt_key": "chapter_plan", "profile": "planning", "output": "chapter_plan", "review": True},
    "chapter_write": {"label": "章节正文", "category": "章节", "reads": ["scene_outline", "continuity", "world", "characters", "facts"], "writes": ["chapters"], "prompt_key": "chapter_write", "profile": "prose", "output": "prose", "review": False},
    "summary": {"label": "章后记忆", "category": "章后", "reads": ["chapter", "chapter_plan"], "writes": ["summaries", "handoff", "state_cards", "facts"], "prompt_key": "summary", "profile": "brief", "output": "chapter_memory", "review": True},
    "revision": {"label": "定向修订", "category": "质量", "reads": ["chapter", "issues", "continuity"], "writes": ["chapters"], "prompt_key": "revision", "profile": "prose", "output": "prose", "review": True},
    "scene_revision": {"label": "局部场景重写", "category": "质量", "reads": ["chapter", "selection", "continuity"], "writes": ["chapter.selection"], "prompt_key": "scene_revision", "profile": "prose", "output": "prose", "review": True},
    "sandbox_variants": {"label": "剧情分支沙盒", "category": "探索", "reads": ["continuity", "outline", "state_cards"], "writes": ["sandbox"], "prompt_key": "sandbox", "profile": "planning", "output": "variants", "review": True},
}


def list_ai_actions() -> list[dict]:
    return [{"key": key, **deepcopy(value)} for key, value in AI_ACTIONS.items()]


def get_ai_action(key: str) -> dict:
    if key not in AI_ACTIONS:
        raise ValueError(f"未知 AI 动作: {key}")
    return {"key": key, **deepcopy(AI_ACTIONS[key])}


def validate_ai_action_registry() -> list[str]:
    errors = []
    required = {"label", "category", "reads", "writes", "prompt_key", "profile", "output", "review"}
    for key, action in AI_ACTIONS.items():
        missing = required - set(action)
        if missing:
            errors.append(f"{key} 缺少字段: {','.join(sorted(missing))}")
        if not action.get("reads") or not action.get("writes"):
            errors.append(f"{key} 必须声明 reads 与 writes")
    return errors
