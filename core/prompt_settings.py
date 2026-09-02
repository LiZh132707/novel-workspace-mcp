"""可由设置中心编辑的分类提示词规则。"""
from copy import deepcopy
from pathlib import Path

from storage_utils import StorageManager


PROMPT_CATALOG = {
    "基础规则": {
        "base": {"label": "全局基础规则", "instruction": "保证因果、人物动机、时空连续性和信息一致性；不输出任务解释。"},
        "title": {"label": "AI起名", "instruction": "书名应易记、与核心冲突相关，避免模板化组合。"},
    },
    "创建与策划": {
        "planning": {"label": "整套开书策划", "instruction": "策划必须具体、可执行、彼此一致。"},
        "foundation": {"label": "故事基础设定", "instruction": "世界观和剧情只能来自用户源数据，不能来自文风参考的具体内容。"},
        "characters": {"label": "主要人物生成", "instruction": "人物欲望、恐惧、原则和缺点必须能制造长期冲突。"},
        "structure": {"label": "总纲与分卷", "instruction": "上级纲规定结果和边界，同时保留人物、支线和世界探索空间。"},
        "opening": {"label": "开篇滚动细纲", "instruction": "只规划未来少量章节，不把长期故事锁死。"},
        "volume_sections": {"label": "单卷节纲补全", "instruction": "节纲章节范围必须连续且完整覆盖所属卷。"},
        "import_rebuild": {"label": "旧小说结构重建", "instruction": "只恢复正文有证据支持的结构与状态，不把推测写成事实。"},
    },
    "文风": {
        "style_analysis": {"label": "参考文风分析", "instruction": "完整保留表达个性和尺度，只禁止搬运具体人物、地点、事件和设定。"},
    },
    "章节创作": {
        "chapter_brief": {"label": "章前提要", "instruction": "在主线、人物、支线、探索、余波和缓冲之间自然切换。"},
        "chapter_plan": {"label": "场景与节拍规划", "instruction": "每个场景都要产生信息、关系、风险或局势变化。"},
        "chapter_write": {"label": "正文生成", "instruction": "把规划扩展成有呼吸感的故事，不机械复述提纲。"},
        "scene_write": {"label": "复杂长章逐场景生成", "instruction": "只写当前场景，同时自然承接上一场景，不重复解释全章背景。"},
    },
    "章后整理": {
        "summary": {"label": "章后摘要与连续性抽取", "instruction": "只记录正文明确发生或能够直接推出的事实。"},
        "character_extract": {"label": "新人物补充识别", "instruction": "只登记有明确姓名且可能再次出场的人物。"},
    },
    "修改与质量": {
        "revision": {"label": "章节质量修订", "instruction": "只修复明确问题，不改变既定事件和人物动机。"},
        "scene_revision": {"label": "局部场景重写", "instruction": "保持前后事实、人物信息权限和物品状态一致。"},
        "history_revision": {"label": "历史剧情事务修改", "instruction": "同时修正前置铺垫、事实发生过程和后续结果；不受影响正文尽量保持。"},
        "selection_edit": {"label": "选区编辑", "instruction": "只修改选中文字，不新增会改变后续剧情的重大事实。"},
    },
    "探索与沙盒": {
        "sandbox": {"label": "剧情分支沙盒", "instruction": "候选方向必须因果成立、彼此明显不同，并明确收益、风险和所需铺垫。"},
    },
}


class PromptSettingsManager:
    def __init__(self, storage_root: Path, logger=None):
        self.path = storage_root / "prompt_settings.json"
        self.storage = StorageManager(logger)

    def get(self) -> dict:
        saved = self.storage.safe_read_json(self.path, {})
        result = deepcopy(PROMPT_CATALOG)
        for category, entries in saved.items():
            if category not in result or not isinstance(entries, dict):
                continue
            for key, value in entries.items():
                if key in result[category] and isinstance(value, dict):
                    result[category][key]["instruction"] = str(value.get("instruction", result[category][key]["instruction"]))[:8000]
        return result

    def save(self, data: dict) -> dict:
        result = deepcopy(PROMPT_CATALOG)
        for category, entries in result.items():
            submitted = data.get(category, {}) if isinstance(data.get(category), dict) else {}
            for key in entries:
                if isinstance(submitted.get(key), dict):
                    entries[key]["instruction"] = str(submitted[key].get("instruction", "")).strip()[:8000]
        self.storage.atomic_write_json(self.path, result)
        return result

    def reset(self) -> dict:
        self.storage.atomic_write_json(self.path, PROMPT_CATALOG)
        return deepcopy(PROMPT_CATALOG)

    def instruction(self, key: str) -> str:
        for entries in self.get().values():
            if key in entries:
                return entries[key]["instruction"]
        return ""
