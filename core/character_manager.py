"""人物管理器：支持能力等级、关系图谱、事件追踪。"""
import json
from pathlib import Path
from typing import Optional


# 标准能力等级体系（玄幻/修仙/科幻通用）
ABILITY_TIERS = [
    "凡人", "练气", "筑基", "金丹", "元婴", "化神",
    "炼虚", "合体", "大乘", "渡劫", "真仙", "金仙",
    "太乙", "大罗", "准圣", "圣人", "天道",
]


from storage_utils import StorageManager
from core.personality_profile_manager import PersonalityProfileManager


class CharacterManager:
    def __init__(self, novel_path: Path, logger):
        self.path = novel_path / "characters"
        self.logger = logger
        self.storage = StorageManager(logger)
        self.path.mkdir(parents=True, exist_ok=True)

    def create_character(self, name: str, personality: str = "", background: str = "",
                         abilities: str = "", ability_level: str = "凡人",
                         relationships: str = "", status: str = "存活",
                         role_tier: str = "重要配角", appearance_start: int = 1,
                         appearance_end: int = 0, personality_profile: dict | None = None) -> dict:
        from filelock import FileLock
        with FileLock(str(self.path / f"{name}.json") + ".lock", timeout=30):
            return self._create_character(
                name, personality, background, abilities, ability_level,
                relationships, status, role_tier, appearance_start, appearance_end,
                personality_profile,
            )

    def _create_character(self, name: str, personality: str = "", background: str = "",
                         abilities: str = "", ability_level: str = "凡人",
                         relationships: str = "", status: str = "存活",
                         role_tier: str = "重要配角", appearance_start: int = 1,
                         appearance_end: int = 0, personality_profile: dict | None = None) -> dict:
        import re as _re
        if not _re.match(r"^[\w\u4e00-\u9fff]+$", name):
            raise ValueError(f"角色名 \"{name}\" 包含非法字符")
        char_file = self.path / f"{name}.json"
        if char_file.exists():
            raise ValueError(f"人物 '{name}' 已存在")
        if ability_level not in ABILITY_TIERS:
            ability_level = self._normalize_tier(ability_level)
        data = {
            "name": name,
            "personality": personality,
            "personality_profile": PersonalityProfileManager.normalize({
                "personality": personality,
                "personality_profile": personality_profile if isinstance(personality_profile, dict) else {},
            }),
            "background": background,
            "abilities": abilities,
            "ability_level": ability_level,
            "ability_history": [{"chapter": 0, "level": ability_level}],
            "relationships": relationships,
            "important_events": [],
            "current_status": status,
            "last_chapter": 0,
            "locations": [],
            "role_tier": role_tier if role_tier in {"主角", "重要配角", "次要角色", "NPC", "路人"} else "重要配角",
            "appearance_start": max(1, int(appearance_start or 1)),
            "appearance_end": max(0, int(appearance_end or 0)),
        }
        self.storage.atomic_write_json(char_file, data)
        self.logger.info("创建人物: %s [%s]", name, ability_level)
        return data

    @staticmethod
    def role_tier_from_planning_role(role: str) -> str:
        value = str(role or "").strip().lower()
        if value in {"主角", "protagonist"} or "主人公" in value or "主角" in value:
            return "主角"
        if value in {"npc", "路人"}:
            return "NPC" if value == "npc" else "路人"
        if "次要" in value or "minor" in value:
            return "次要角色"
        return "重要配角"

    def update_character(self, name: str, **kwargs) -> dict:
        char_file = self.path / f"{name}.json"
        from filelock import FileLock
        lock = FileLock(str(char_file) + ".lock", timeout=30)
        with lock:
            if not char_file.exists():
                raise ValueError(f"人物 '{name}' 不存在")
            data = json.loads(char_file.read_text("utf-8"))
            prev_ability = data.get("ability_level", "")
            for k, v in kwargs.items():
                if v is not None:
                    data[k] = PersonalityProfileManager.normalize({"personality_profile": v}) if k == "personality_profile" else v
            if "ability_level" in kwargs:
                level = kwargs["ability_level"]
                if level not in ABILITY_TIERS:
                    level = self._normalize_tier(level)
                if level != prev_ability:
                    data["ability_level"] = level
                    if "ability_history" not in data:
                        data["ability_history"] = []
                    data["ability_history"].append({
                        "chapter": data.get("last_chapter", 0),
                        "level": level,
                    })
            if "location" in kwargs and kwargs["location"]:
                if "locations" not in data:
                    data["locations"] = []
                loc_entry = {
                    "chapter": data.get("last_chapter", 0),
                    "location": kwargs["location"],
                }
                if not data["locations"] or data["locations"][-1]["location"] != kwargs["location"]:
                    data["locations"].append(loc_entry)

            self.storage.atomic_write_json(char_file, data)
            self.logger.debug("更新人物: %s", name)
            return data

    def replace_review_derived_state(self, name: str, **fields) -> dict:
        """原子替换由人物审核记录派生的字段，不触发二次历史追加。"""
        allowed = {"current_status", "relationships", "ability_level", "ability_history", "locations"}
        values = {key: value for key, value in fields.items() if key in allowed}
        char_file = self.path / f"{name}.json"
        from filelock import FileLock
        with FileLock(str(char_file) + ".lock", timeout=30):
            data = self.storage.safe_read_json(char_file, None)
            if not isinstance(data, dict):
                raise ValueError(f"人物 '{name}' 不存在")
            data.update(values)
            self.storage.atomic_write_json(char_file, data)
            return data

    def get_character(self, name: str) -> Optional[dict]:
        """获取人物档案（事务安全读取）。"""
        char_file = self.path / f"{name}.json"
        data = self.storage.safe_read_json(char_file, None)
        return data if isinstance(data, dict) else None

    def list_characters(self, chapter: int | None = None) -> list[dict]:
        result = []
        for f in sorted(self.path.glob("*.json")):
            try:
                data = json.loads(f.read_text("utf-8"))
                start = max(1, int(data.get("appearance_start", 1) or 1))
                end = max(0, int(data.get("appearance_end", 0) or 0))
                if chapter is not None and (chapter < start or (end and chapter > end)):
                    continue
                result.append({
                    "name": data.get("name", f.stem),
                    "status": data.get("current_status", "未知"),
                    "ability_level": data.get("ability_level", "未知"),
                    "last_chapter": data.get("last_chapter", 0),
                    "role_tier": data.get("role_tier", "重要配角"),
                    "appearance_start": start,
                    "appearance_end": end,
                })
            except Exception:
                pass
        priority = {"主角": 0, "重要配角": 1, "次要角色": 2, "NPC": 3, "路人": 4}
        result.sort(key=lambda item: (
            priority.get(item.get("role_tier", "重要配角"), 1),
            -int(item.get("last_chapter", 0) or 0),
            str(item.get("name", "")),
        ))
        return result

    def canonical_roster(self, chapter: int | None = None) -> list[dict]:
        roster = []
        for item in self.list_characters(chapter):
            detail = self.get_character(item["name"])
            if isinstance(detail, dict):
                roster.append(detail)
        return roster

    def add_event_to_character(self, name: str, event: str):
        """添加重要事件到人物档案。"""
        char_file = self.path / f"{name}.json"
        if not char_file.exists():
            raise ValueError(f"\u4eba\u7269 \'{name}\' \u4e0d\u5b58\u5728")
        from filelock import FileLock
        if char_file.exists():
            from filelock import FileLock
            lock = FileLock(str(char_file) + ".lock", timeout=30)
            with lock:
                data = json.loads(char_file.read_text("utf-8"))
                if "important_events" not in data:
                    data["important_events"] = []
                data["important_events"].append(event)
                self.storage.atomic_write_json(char_file, data)

    def get_ability_tier_index(self, level: str) -> int:
        """获取能力等级索引，用于比较高低。"""
        level = self._normalize_tier(level)
        if level in ABILITY_TIERS:
            return ABILITY_TIERS.index(level)
        return -1

    @staticmethod
    def _normalize_tier(level: str) -> str:
        """模糊匹配能力等级（空字符返回"凡人"默认值）。"""
        level = level.strip()
        if not level:
            return "凡人"
        # 所有匹配的等级，按长度优先（长者优先）然后按强度排序（等级越高越优先）
        matches = []
        for i, tier in enumerate(ABILITY_TIERS):
            if tier in level:
                matches.append((len(tier), i, tier))
        if matches:
            matches.sort(key=lambda x: (-x[0], -x[1]))
            return matches[0][2]
        for i, tier in enumerate(ABILITY_TIERS):
            if level in tier:
                return tier
        return level

    def get_character_network(self) -> dict:
        """获取人物关系网络（简单版）。"""
        chars = self.list_characters()
        network = {"nodes": [], "edges": []}
        for c in chars:
            network["nodes"].append({"id": c["name"], "level": c["ability_level"]})
            data = self.get_character(c["name"])
            if data and data.get("relationships"):
                for rel in data["relationships"].split(","):
                    rel = rel.strip()
                    if rel:
                        network["edges"].append({
                            "from": c["name"],
                            "to": rel,
                            "type": "关系",
                        })
        return network
