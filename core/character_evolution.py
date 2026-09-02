"""角色演变追踪：自动扫描角色外貌/状态/能力变化。"""
import re
from pathlib import Path
from typing import Optional

from filelock import FileLock

from storage_utils import StorageManager
from core.character_manager import CharacterManager


class CharacterEvolutionTracker:
    """跨章节追踪角色变化。"""

    def __init__(self, novel_path: Path, logger, storage: StorageManager = None):
        self.char_mgr = CharacterManager(novel_path, logger)
        self.path = novel_path / "characters" / ".evolution"
        self.logger = logger
        self.storage = storage or StorageManager(logger)
        self.path.mkdir(parents=True, exist_ok=True)

    def scan_chapter(self, chapter: int, content: str):
        """扫描章节中所有角色的演变。"""
        for c in self.char_mgr.list_characters():
            name = c["name"]
            if name not in content:
                continue

            evo_file = self.path / f"{name}.json"
            with FileLock(str(evo_file) + ".lock", timeout=30):
                self._scan_character(chapter, content, name, evo_file)

    def _scan_character(self, chapter: int, content: str, name: str, evo_file: Path):
        evo = self.storage.safe_read_json(evo_file, {"name": name, "snapshots": []})
        evo = evo if isinstance(evo, dict) else {}
        snapshots = evo.get("snapshots", [])
        snapshots = [item for item in snapshots if isinstance(item, dict)] if isinstance(snapshots, list) else []
        evo = {**evo, "name": name, "snapshots": snapshots}

        appearance = self._extract_appearance(content, name)
        status_change = self._extract_status(content, name)
        ability_hint = self._extract_ability(content, name)
        snapshot = {
            "chapter": chapter,
            "appearance": appearance,
            "status_change": status_change,
            "ability_hint": ability_hint,
        }
        existing = [item for item in evo["snapshots"] if item.get("chapter") == chapter]
        if existing:
            evo["snapshots"][evo["snapshots"].index(existing[0])] = snapshot
        else:
            evo["snapshots"].append(snapshot)
        self.storage.atomic_write_json(evo_file, evo)

    def get_evolution(self, name: str) -> Optional[dict]:
        """获取角色演变历史。"""
        evo_file = self.path / f"{name}.json"
        return self.storage.safe_read_json(evo_file, None)

    def _extract_appearance(self, text: str, name: str) -> list[str]:
        """提取外貌描述（使用 re.escape 防止特殊字符破坏正则）。"""
        clues = []
        escaped = re.escape(name)
        patterns = [
            rf"{escaped}[^。]{{0,20}}(身穿|穿着|身着|戴|穿|披|一头|留着|长着|脸上|眼中|嘴角)[^。]{{0,20}}",
            rf"(身穿|穿着|身着)[^。]{{0,30}}{escaped}[^。]{{0,20}}",
        ]
        for p in patterns:
            for m in re.finditer(p, text):
                clues.append(m.group())
        return clues[:5]

    def _extract_status(self, text: str, name: str) -> Optional[str]:
        """提取状态变化（使用 re.escape 防止特殊字符破坏正则）。"""
        escaped = re.escape(name)
        patterns = [
            rf"{escaped}[^。]{{0,30}}(重伤|轻伤|濒死|死亡|昏迷|苏醒|突破|进阶|晋级|受伤|倒下)[^。]{{0,20}}",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                return m.group()
        return None

    def _extract_ability(self, text: str, name: str) -> Optional[str]:
        """提取能力变化线索（使用 re.escape 防止特殊字符破坏正则）。"""
        escaped = re.escape(name)
        patterns = [
            rf"{escaped}[^。]{{0,40}}(突破|晋级|进阶|领悟|学会|掌握|达到|进入)[^。]{{0,30}}(境界|层|阶|级|段)",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                return m.group()
        return None

    def get_evolution_report(self, name: str) -> str:
        """生成可读的角色演变报告。"""
        evo = self.get_evolution(name)
        if not evo or not evo.get("snapshots"):
            return f"「{name}」暂无演变数据。"
        parts = [f"【{name} 演变追踪】\n"]
        for s in sorted(evo["snapshots"], key=lambda x: x["chapter"]):
            parts.append(f"第{s['chapter']}章:")
            if s["appearance"]:
                parts.append(f"  外貌: {'; '.join(s['appearance'][:2])}")
            if s["status_change"]:
                parts.append(f"  状态: {s['status_change']}")
            if s["ability_hint"]:
                parts.append(f"  能力: {s['ability_hint']}")
            parts.append("")
        return "\n".join(parts)
