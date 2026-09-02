"""风格预设系统：提取、保存、复用写作风格。"""
import json
import re
from pathlib import Path
from typing import Optional

from storage_utils import StorageManager


# 内置风格预设
BUILTIN_STYLES = {
    "硬核科幻": {
        "description": "技术细节丰富，逻辑严谨，术语准确",
        "traits": [
            "使用精确的技术术语",
            "注重逻辑推演",
            "对话包含专业讨论",
            "环境描写突出科技感",
        ],
        "avoid": ["魔法/超自然解释", "模糊的科技描述", "逻辑跳跃"],
    },
    "传统玄幻": {
        "description": "东方玄幻，修炼体系，境界分明",
        "traits": [
            "修炼境界体系完整",
            "战斗描写注重招式",
            "主角成长线清晰",
            "世界观有传统文化底蕴",
        ],
        "avoid": ["现代元素混杂", "过快升级", "体系混乱"],
    },
    "悬疑推理": {
        "description": "逻辑严密，伏笔精巧，节奏紧凑",
        "traits": [
            "层层递进的线索",
            "合理的误导",
            "对话包含信息差",
            "节奏张弛有度",
        ],
        "avoid": ["线索过于明显", "巧合解决谜题", "节奏拖沓"],
    },
    "轻快网文": {
        "description": "轻松愉快，节奏明快，爽点密集",
        "traits": [
            "短段落，快节奏",
            "对话风趣幽默",
            "每章有明确爽点",
            "金手指设定明确",
        ],
        "avoid": ["大段景物描写", "冗长的内心独白", "过度虐主"],
    },
}


class StylePresetManager:
    """风格预设管理。"""

    def __init__(self, novel_path: Path, logger, storage: StorageManager = None):
        self.path = novel_path / "bible" / "style_presets"
        self.logger = logger
        self.storage = storage or StorageManager(logger)
        self.path.mkdir(parents=True, exist_ok=True)

    def list_presets(self) -> list[dict]:
        results = []
        # 内置风格
        for name, data in BUILTIN_STYLES.items():
            results.append({
                "name": name, "builtin": True,
                "description": data["description"],
            })
        # 自定义风格
        for f in sorted(self.path.glob("*.json")):
            try:
                data = json.loads(f.read_text("utf-8"))
                results.append({
                    "name": data.get("name", f.stem),
                    "builtin": False,
                    "description": data.get("description", ""),
                })
            except Exception:
                pass
        return results

    def get_preset(self, name: str, prefer_custom: bool = False) -> Optional[dict]:
        """获取风格预设。优先返回自定义（即使与内置同名）。"""
        self._validate_name(name)
        preset_file = self.path / f"{name}.json"
        if preset_file.exists():
            try:
                data = json.loads(preset_file.read_text("utf-8"))
                data["builtin"] = False
                return data
            except Exception:
                pass
        if name in BUILTIN_STYLES:
            return {**BUILTIN_STYLES[name], "name": name, "builtin": True}
        return None

    def save_preset(self, name: str, description: str, traits: list[str],
                    avoid: list[str] = None) -> dict:
        self._validate_name(name)
        data = {
            "name": name,
            "description": str(description)[:1000],
            "traits": [str(item)[:500] for item in traits if str(item).strip()][:30],
            "avoid": [str(item)[:500] for item in (avoid or []) if str(item).strip()][:30],
            "created_at": __import__("datetime").datetime.now().isoformat(),
        }
        self.storage.atomic_write_json(self.path / f"{name}.json", data)
        self.logger.info("风格预设保存: %s", name)
        return data

    def extract_from_text(self, name: str, text: str) -> dict:
        """从文本中提取写作特征并生成风格预设。"""
        paras = [p for p in text.split("\n") if p.strip()]
        sents = re.split(r"[。！？\n]+", text)
        sents = [s.strip() for s in sents if s.strip()]

        avg_para_len = sum(len(p) for p in paras) / len(paras) if paras else 0
        avg_sent_len = sum(len(s) for s in sents) / len(sents) if sents else 0

        dialogue_lines = sum(1 for p in paras if "“" in p or "「" in p or '"' in p)
        dialogue_ratio = dialogue_lines / len(paras) if paras else 0

        traits = []
        if avg_sent_len < 20:
            traits.append("短句为主，节奏明快")
        elif avg_sent_len > 40:
            traits.append("长句为主，描写细腻")
        if dialogue_ratio > 0.4:
            traits.append("对话驱动，人物互动密集")
        elif dialogue_ratio < 0.2:
            traits.append("叙述为主，侧重描写")
        if avg_para_len < 80:
            traits.append("短段落，视觉节奏快")
        else:
            traits.append("长段落，沉浸式叙述")

        return self.save_preset(
            name=name,
            description=f"从文本提取的写作风格 - 平均句长{avg_sent_len:.0f}字，对话比例{dialogue_ratio:.0%}",
            traits=traits,
            avoid=[],
        )

    @staticmethod
    def _validate_name(name: str):
        if not isinstance(name, str) or not re.fullmatch(r"[\w\u4e00-\u9fff-]{1,64}", name):
            raise ValueError("风格预设名称只允许中英文、数字、下划线和连字符，长度1至64")
