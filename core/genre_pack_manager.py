"""题材方法包：提供结构、节奏与反模式，不模仿具体作者。"""
from __future__ import annotations

from pathlib import Path

from storage_utils import StorageManager


GENRE_PACKS = {
    "general": {"label": "通用长篇", "rules": ["每章至少产生一种可追踪变化", "支线必须服务人物、世界或主线条件"], "avoid": ["机械复述提纲", "连续解释设定"], "modes": ["main_progress", "character", "subplot", "aftermath"]},
    "suspense": {"label": "悬疑推理", "rules": ["控制信息差并保存证据链", "答案出现前必须存在可回溯线索", "每章至少推进调查或改变嫌疑结构"], "avoid": ["无铺垫反转", "侦探依靠作者全知破案"], "modes": ["setup", "exploration", "complication", "main_progress"]},
    "xianxia": {"label": "仙侠修真", "rules": ["力量提升必须有资源、风险或心境代价", "境界影响选择而不只是数值", "宗门和因果关系持续产生后果"], "avoid": ["无代价升级", "敌人排队送经验"], "modes": ["exploration", "main_progress", "character", "aftermath"]},
    "urban": {"label": "都市现实", "rules": ["冲突落在工作、关系、金钱与身份压力", "用行动和日常细节呈现人物", "重大选择产生现实后果"], "avoid": ["所有配角只围绕主角", "用巧合解决核心困难"], "modes": ["character", "subplot", "complication", "aftermath"]},
    "historical": {"label": "历史题材", "rules": ["官职、交通、生产力和称谓符合时代边界", "架空改动必须声明连锁影响", "人物选择受到制度和信息条件限制"], "avoid": ["现代概念直接套古代", "忽略时间和路程成本"], "modes": ["setup", "main_progress", "exploration", "aftermath"]},
    "romance": {"label": "言情关系", "rules": ["关系推进依靠选择、误解澄清和共同经历", "双方都有独立目标与边界", "情绪变化必须有可见触发"], "avoid": ["用强制行为替代情感建立", "配角只作为恋爱工具"], "modes": ["character", "complication", "subplot", "breathing"]},
}


class GenrePackManager:
    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.path = novel_path / "bible" / "genre_pack.json"
        self.storage = storage or StorageManager(logger)

    def list(self) -> list[dict]:
        active = self.get().get("key", "general")
        return [{"key": key, **value, "active": key == active} for key, value in GENRE_PACKS.items()]

    def get(self) -> dict:
        saved = self.storage.safe_read_json(self.path, {})
        saved = saved if isinstance(saved, dict) else {}
        key = saved.get("key", "general")
        return {"key": key, **GENRE_PACKS.get(key, GENRE_PACKS["general"])}

    def apply(self, key: str) -> dict:
        if key not in GENRE_PACKS:
            raise ValueError("未知题材方法包")
        data = {"key": key, **GENRE_PACKS[key]}
        self.storage.atomic_write_json(self.path, data)
        return data

    def context(self) -> str:
        pack = self.get()
        return "\n".join([
            f"【题材方法包：{pack['label']}】",
            "执行策略：" + "；".join(pack["rules"]),
            "避免模式：" + "；".join(pack["avoid"]),
            "章节模式建议：" + "、".join(pack["modes"]),
        ])
