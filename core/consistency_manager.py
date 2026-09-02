"""一致性检查器：能力等级矛盾、世界规则违反、时空连续性。"""
import re

from core.novel_manager import NovelManager
from core.character_manager import CharacterManager
from core.timeline_manager import TimelineManager
from core.chapter_manager import ChapterManager
from core.change_review_manager import ChangeReviewManager


class ConsistencyManager:
    DEATH_REFERENCE_MARKERS = (
        "回忆", "想起", "记得", "梦见", "梦到", "提到", "提及", "谈起", "说起",
        "曾经", "生前", "已故", "故人", "死去", "死讯", "尸体", "遗体", "遗像",
        "墓碑", "坟墓", "灵位", "葬礼", "照片", "画像", "日记", "遗书", "录音",
    )
    NON_LIVE_FRAMING_MARKERS = (
        "回忆", "想起", "记得", "梦见", "梦到", "提到", "提及", "谈起", "说起",
        "遗像", "墓碑", "坟墓", "灵位", "照片", "画像", "日记", "遗书", "录音",
    )
    LIVE_APPEARANCE_MARKERS = (
        "突然", "睁眼", "醒来", "站起", "起身", "推门", "走来", "走进", "出现",
        "回来", "复活", "开口", "回答", "说道", "攻击", "抓住", "冲向",
    )
    MOVEMENT_MARKERS = (
        "前往", "赶往", "来到", "抵达", "到达", "进入", "离开", "返回", "回到",
        "出发", "赶到", "转移", "撤离", "穿过", "走进", "走出", "跑到", "飞往",
        "传送", "瞬移", "跃迁", "乘车", "乘船", "登机", "下车", "下船",
    )
    PROHIBITION_MARKERS = ("禁止", "不能", "不可", "不得", "不允许", "严禁")
    DEATH_STATUS_MARKERS = ("死亡", "阵亡", "身亡", "已故", "去世")

    def __init__(self, novel_manager: NovelManager, logger):
        self.nm = novel_manager
        self.char_mgr = CharacterManager(novel_manager.path, logger)
        self.timeline_mgr = TimelineManager(novel_manager.path, logger)
        self.chapter_mgr = ChapterManager(novel_manager, logger)
        self.change_reviews = ChangeReviewManager(novel_manager.path, logger, novel_manager.storage)
        self.logger = logger
        # 缓存世界规则
        self._world_rules: list[str] = []
        self._load_world_rules()

    def _load_world_rules(self):
        rules_path = self.nm.path / "bible" / "rules.md"
        if rules_path.exists():
            text = rules_path.read_text("utf-8")
            for line in text.split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("!"):
                    self._world_rules.append(line)

    def check_all(self) -> list[dict]:
        issues = []
        issues.extend(self._check_characters())
        issues.extend(self._check_ability_levels())
        issues.extend(self._check_timeline())
        issues.extend(self._check_chapter_gaps())
        issues.extend(self._check_world_rules())
        issues.extend(self._check_spatiotemporal())
        self.logger.info("深度一致性检查完成: %d 个问题", len(issues))
        return issues

    def _check_characters(self) -> list[dict]:
        issues = []
        dead_chars = []
        for c in self.char_mgr.list_characters():
            data = self.char_mgr.get_character(c["name"])
            if not data:
                continue
            status = data.get("current_status", "")
            last_ch = data.get("last_chapter", 0)
            if any(marker in str(status) for marker in self.DEATH_STATUS_MARKERS):
                death_changes = [
                    item for item in self.change_reviews.list(None)
                    if item.get("status") == "accepted" and item.get("name") == data["name"]
                    and item.get("field") == "current_status"
                    and any(marker in str(item.get("new_value", "")) for marker in self.DEATH_STATUS_MARKERS)
                ]
                death_chapter = max((int(item.get("chapter", 0)) for item in death_changes), default=int(last_ch or 0))
                dead_chars.append((data["name"], death_chapter))
        if not dead_chars:
            return issues
        # 读取每个章节一次，检查所有已死亡角色
        for ch in self.chapter_mgr.list_chapter_numbers():
            ch_content = self.chapter_mgr.read_chapter(ch)
            if not ch_content:
                continue
            for name, death_ch in dead_chars:
                if ch > death_ch and name in ch_content:
                    if not self._has_live_appearance(ch_content, name):
                        continue
                    issues.append({
                        "type": "人物已死亡但再次出现",
                        "severity": "高",
                        "detail": f"{name} 在第{death_ch}章死亡，但第{ch}章仍出现",
                        "chapter": ch,
                    })
        return issues

    def _has_live_appearance(self, content: str, name: str) -> bool:
        """排除回忆、遗物、梦境等纯引用，仅保留疑似现实再登场。"""
        start = 0
        while True:
            idx = content.find(name, start)
            if idx < 0:
                return False
            context = content[max(0, idx - 18):idx + len(name) + 18]
            has_reference = any(marker in context for marker in self.DEATH_REFERENCE_MARKERS)
            if not has_reference:
                return True
            if any(marker in context for marker in self.NON_LIVE_FRAMING_MARKERS):
                start = idx + len(name)
                continue
            if any(marker in context for marker in self.LIVE_APPEARANCE_MARKERS):
                return True
            start = idx + len(name)

    def _check_ability_levels(self) -> list[dict]:
        """检查能力等级矛盾（等级跳跃不能超过2级/章，不能逆向突破）。"""
        issues = []
        for c in self.char_mgr.list_characters():
            data = self.char_mgr.get_character(c["name"])
            if not data:
                continue
            history = data.get("ability_history", [])
            for i in range(1, len(history)):
                prev = history[i - 1]
                curr = history[i]
                prev_idx = self.char_mgr.get_ability_tier_index(prev["level"])
                curr_idx = self.char_mgr.get_ability_tier_index(curr["level"])
                if prev_idx >= 0 and curr_idx >= 0:
                    jump = curr_idx - prev_idx
                    if jump > 2:
                        issues.append({
                            "type": "能力等级跳跃过大",
                            "severity": "中",
                            "detail": (f"{data['name']} 在第{prev['chapter']}章"
                                       f"还是{prev['level']}，第{curr['chapter']}章"
                                       f"直接跳到{curr['level']}（跳跃{jump}级）"),
                            "chapter": curr["chapter"],
                        })
                    elif jump < 0:
                        issues.append({
                            "type": "能力等级逆向突破",
                            "severity": "高",
                            "detail": (f"{data['name']} 从{prev['level']}"
                                       f"退回到{curr['level']}（第{curr['chapter']}章）"),
                            "chapter": curr["chapter"],
                        })
        return issues

    def _check_timeline(self) -> list[dict]:
        """同章同一时刻，只有同一人物出现在不同地点才构成确定性冲突。"""
        issues = []
        events = self.timeline_mgr.get_recent_events(500)
        appearances = {}
        for e in events:
            chapter = e.get("chapter")
            story_time = str(e.get("time", "")).strip()
            location = str(e.get("location", "")).strip()
            if not isinstance(chapter, int) or not story_time or not location:
                continue
            for character in e.get("characters", []) if isinstance(e.get("characters"), list) else []:
                name = str(character).strip()
                if name:
                    appearances.setdefault((chapter, story_time, name), set()).add(location)
        for (chapter, story_time, name), locations in appearances.items():
            if len(locations) > 1:
                issues.append({
                    "type": "时间地点冲突",
                    "severity": "高",
                    "detail": f"第{chapter}章“{story_time}”，{name}同时出现在不同地点: {sorted(locations)}",
                    "chapter": chapter,
                })
        return issues

    def _check_chapter_gaps(self) -> list[dict]:
        issues = []
        chapter_numbers = self.chapter_mgr.list_chapter_numbers()
        if len(chapter_numbers) < 2:
            return issues
        existing = set(chapter_numbers)
        if existing:
            max_ch = max(existing)
            for i in range(1, max_ch + 1):
                if i not in existing:
                    issues.append({
                        "type": "章节缺失",
                        "severity": "低",
                        "detail": f"第{i}章缺失",
                        "chapter": i,
                    })
        return issues

    def _check_world_rules(self) -> list[dict]:
        """保守检查明确禁令中的动作，不把规则原句误判为违规。"""
        issues = []
        if not self._world_rules:
            return issues
        forbidden_actions = [
            (rule, action) for rule in self._world_rules
            if (action := self._forbidden_action(rule))
        ]
        if not forbidden_actions:
            return issues
        for ch in self.chapter_mgr.list_chapter_numbers():
            content = self.chapter_mgr.read_chapter(ch)
            if not content:
                continue
            for rule, action in forbidden_actions:
                idx = content.find(action)
                while idx != -1:
                    prefix = content[max(0, idx - 16):idx]
                    if not any(marker in prefix for marker in self.PROHIBITION_MARKERS):
                        issues.append({
                            "type": "可能违反世界规则",
                            "severity": "低",
                            "detail": f"第{ch}章疑似执行“{action}”，可能违反规则「{rule[:80]}」",
                            "chapter": ch,
                        })
                        break
                    idx = content.find(action, idx + len(action))
        return issues

    def _forbidden_action(self, rule: str) -> str:
        parts = re.split(r"禁止|不能|不可|不得|不允许|严禁", rule, maxsplit=1)
        if len(parts) != 2:
            return ""
        action = re.split(r"[。；;，,：:\n]", parts[1], maxsplit=1)[0].strip()
        action = re.sub(r"^(任何人|所有人|任何角色|所有角色|人物|角色|任何生物)", "", action).strip()
        return action if 2 <= len(action) <= 40 else ""

    def _check_spatiotemporal(self) -> list[dict]:
        """检查时空连续性（人物位置跳跃）。"""
        issues = []
        for c in self.char_mgr.list_characters():
            data = self.char_mgr.get_character(c["name"])
            if not data:
                continue
            locations = data.get("locations", [])
            for i in range(1, len(locations)):
                prev = locations[i - 1]
                curr = locations[i]
                if prev["location"] != curr["location"]:
                    ch_diff = curr["chapter"] - prev["chapter"]
                    severity = ""
                    reason = ""
                    if ch_diff < 0:
                        severity = "高"
                        reason = "位置记录章节倒序"
                    elif ch_diff == 0 and not self._has_movement_evidence(data["name"], prev, curr):
                        severity = "高"
                        reason = "同章切换地点但正文没有移动证据"
                    elif ch_diff == 1 and not self._has_movement_evidence(data["name"], prev, curr):
                        severity = "中"
                        reason = "相邻章切换地点但没有移动或转场证据"
                    if severity:
                        issues.append({
                            "type": "时空跳跃",
                            "severity": severity,
                            "detail": (f"{data['name']} 在{prev['location']}"
                                       f"（第{prev['chapter']}章）之后未交代移动，"
                                       f"就出现在{curr['location']}（第{curr['chapter']}章）；{reason}"),
                            "chapter": curr["chapter"],
                        })
        return issues

    def _has_movement_evidence(self, name: str, previous: dict, current: dict) -> bool:
        start = max(1, min(int(previous.get("chapter", 1)), int(current.get("chapter", 1))))
        end = max(start, max(int(previous.get("chapter", 1)), int(current.get("chapter", 1))))
        for chapter in range(start, end + 1):
            content = self.chapter_mgr.read_chapter(chapter) or ""
            offset = 0
            while True:
                idx = content.find(name, offset)
                if idx < 0:
                    break
                context = content[max(0, idx - 80):idx + len(name) + 80]
                if any(marker in context for marker in self.MOVEMENT_MARKERS):
                    return True
                offset = idx + len(name)
        return False
