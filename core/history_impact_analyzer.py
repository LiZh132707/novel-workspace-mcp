"""历史事实修改的跨账本影响预览。"""
from __future__ import annotations

import json
from pathlib import Path

from storage_utils import StorageManager


class HistoryImpactAnalyzer:
    LABELS = {
        "characters": "人物档案", "facts": "事实账本", "timeline": "时间线",
        "foreshadowing": "伏笔", "character_reviews": "人物变化审核",
        "story_logic": "叙事承诺与认知", "entities": "地点势力物品",
        "state_cards": "动态状态卡", "planning": "未来规划",
        "canonical_locks": "权威设定锁", "story_clock": "故事时钟与行程",
    }

    def __init__(self, root: Path, storage: StorageManager | None = None):
        self.root = root
        self.storage = storage or StorageManager()

    def analyze(self, old_fact: str, new_fact: str, keywords: list[str]) -> dict:
        needles = [value.lower() for value in [old_fact, new_fact, *keywords] if str(value).strip()]
        generic = {
            "死亡", "身亡", "存活", "重伤", "轻伤", "失踪", "昏迷", "苏醒", "复活",
            "身份", "本名", "父亲", "母亲", "种族", "性别", "位于", "属于", "拥有",
            "被毁", "毁坏", "摧毁", "获得", "失去", "改变", "成为",
        }
        self._anchors = [
            str(value).lower() for value in keywords
            if 2 <= len(str(value).strip()) <= 6 and str(value).strip() not in generic
        ]
        categories = {key: {"label": label, "count": 0, "items": []} for key, label in self.LABELS.items()}
        self._scan_character_files(categories, needles)
        self._scan_list_file(categories, "facts", self.root / "facts.json", ("facts", "conflicts"), needles, "重建")
        self._scan_directory(categories, "timeline", self.root / "timeline", needles, "重建")
        self._scan_list_file(categories, "foreshadowing", self.root / "foreshadowing.json", ("items",), needles, "复核生命周期")
        self._scan_list_file(categories, "character_reviews", self.root / "reviews" / "character_changes.json", ("items",), needles, "重建并复核")
        self._scan_structured_file(categories, "story_logic", self.root / "tracking" / "story_logic.json", needles, "重建")
        self._scan_structured_file(categories, "entities", self.root / "tracking" / "entities.json", needles, "重建")
        self._scan_structured_file(categories, "state_cards", self.root / "tracking" / "state_cards.json", needles, "重建并保留人工覆盖")
        self._scan_structured_file(categories, "canonical_locks", self.root / "tracking" / "canonical_locks.json", needles, "必须人工复核锁定值")
        self._scan_structured_file(categories, "story_clock", self.root / "tracking" / "story_clock.json", needles, "重建并复核时空连续性")
        for filename in ("chapter_briefs.json", "chapter_plans.json", "scene_outlines.json", "opening_chapters.json", "volumes.json"):
            self._scan_structured_file(categories, "planning", self.root / "outline" / filename, needles, "失效并重新规划")
        categories = {key: value for key, value in categories.items() if value["count"]}
        affected = sorted({chapter for category in categories.values() for item in category["items"] for chapter in item["chapters"]})
        total = sum(item["count"] for item in categories.values())
        high_risk = any(key in categories for key in ("characters", "facts", "state_cards", "foreshadowing", "canonical_locks", "story_clock"))
        return {
            "categories": categories, "total_records": total, "affected_chapters": affected,
            "risk_level": "高" if high_risk else "中" if total else "低",
            "warnings": ["影响分析基于现有结构化账本和文本关键词；模型修订后仍会重新生成摘要并全量重建派生状态。"] if total else [],
        }

    def _scan_character_files(self, categories: dict, needles: list[str]):
        for path in (self.root / "characters").glob("*.json") if (self.root / "characters").exists() else []:
            self._consider(categories, "characters", path.name, self.storage.safe_read_json(path, {}), needles, "复核人物状态")

    def _scan_directory(self, categories: dict, category: str, directory: Path, needles: list[str], action: str):
        for path in directory.glob("*.json") if directory.exists() else []:
            self._consider(categories, category, path.name, self.storage.safe_read_json(path, {}), needles, action)

    def _scan_list_file(self, categories: dict, category: str, path: Path, keys: tuple[str, ...], needles: list[str], action: str):
        data = self.storage.safe_read_json(path, {})
        if not isinstance(data, dict):
            return
        for key in keys:
            for index, item in enumerate(data.get(key, []) if isinstance(data.get(key), list) else []):
                self._consider(categories, category, f"{path.name}:{key}[{index}]", item, needles, action)

    def _scan_structured_file(self, categories: dict, category: str, path: Path, needles: list[str], action: str):
        data = self.storage.safe_read_json(path, {})
        for source, item, hint in self._walk_records(data, path.name):
            self._consider(categories, category, source, item, needles, action, hint)

    def _walk_records(self, value, source: str, chapter_hint: int | None = None, depth: int = 0):
        if depth > 4:
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                yield from self._walk_records(item, f"{source}[{index}]", chapter_hint, depth + 1)
            return
        if not isinstance(value, dict):
            return
        hint = chapter_hint
        if source.rsplit(":", 1)[-1].isdigit():
            hint = int(source.rsplit(":", 1)[-1])
        has_scalar = any(not isinstance(item, (dict, list)) for item in value.values())
        if has_scalar:
            yield source, value, hint
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                child_hint = int(key) if str(key).isdigit() else hint
                yield from self._walk_records(item, f"{source}:{key}", child_hint, depth + 1)

    def _consider(self, categories: dict, category: str, source: str, item, needles: list[str], action: str, chapter_hint: int | None = None):
        text = (source + " " + json.dumps(item, ensure_ascii=False, sort_keys=True)).lower()
        if self._anchors and not any(anchor in text for anchor in self._anchors):
            return
        matched = []
        for needle in needles:
            if needle and needle in text and needle not in matched:
                matched.append(needle)
        if not matched:
            return
        chapters = self._chapters(item, chapter_hint)
        record = {
            "source": source, "chapters": chapters, "matched": matched[:10],
            "action": action, "snippet": text[:240],
        }
        bucket = categories[category]
        bucket["count"] += 1
        if len(bucket["items"]) < 30:
            bucket["items"].append(record)

    @staticmethod
    def _chapters(item, hint: int | None = None) -> list[int]:
        values = [hint] if hint else []
        if isinstance(item, dict):
            for key in ("chapter", "introduced_chapter", "target_chapter", "source_chapter", "start_chapter", "end_chapter"):
                value = item.get(key)
                if str(value).isdigit() and int(value) > 0:
                    values.append(int(value))
        return sorted(set(values))
