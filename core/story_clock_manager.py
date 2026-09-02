"""故事内时间、地点与移动耗时账本。"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from filelock import FileLock

from storage_utils import StorageManager


class StoryClockManager:
    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.path = novel_path / "tracking" / "story_clock.json"
        self.storage = storage or StorageManager(logger)

    def get(self) -> dict:
        data = self.storage.safe_read_json(self.path, {"travel_rules": [], "events": []})
        if not isinstance(data, dict):
            data = {}
        travel_rules = []
        for item in data.get("travel_rules", []) if isinstance(data.get("travel_rules"), list) else []:
            if not isinstance(item, dict):
                continue
            try:
                minutes = max(1, int(item.get("minutes", 0)))
            except (TypeError, ValueError):
                continue
            origin, destination = str(item.get("from", "")).strip(), str(item.get("to", "")).strip()
            if not origin or not destination or origin == destination:
                continue
            travel_rules.append({"from": origin, "to": destination, "minutes": minutes})
        events = []
        for item in data.get("events", []) if isinstance(data.get("events"), list) else []:
            if not isinstance(item, dict):
                continue
            try:
                chapter = int(item.get("chapter", 0))
            except (TypeError, ValueError):
                continue
            if chapter < 1:
                continue
            events.append({
                "chapter": chapter, "story_time": str(item.get("story_time", "")),
                "location": str(item.get("location", "")),
                "characters": [str(name) for name in item.get("characters", []) if str(name).strip()] if isinstance(item.get("characters"), list) else [],
                "issues": [issue for issue in item.get("issues", []) if isinstance(issue, dict)] if isinstance(item.get("issues"), list) else [],
            })
        events.sort(key=lambda item: item["chapter"])
        return {"travel_rules": travel_rules, "events": events}

    def set_travel_rule(self, origin: str, destination: str, minutes: int) -> dict:
        origin, destination = str(origin).strip(), str(destination).strip()
        if not origin or not destination or origin == destination:
            raise ValueError("移动规则需要两个不同地点")
        minutes = max(1, int(minutes))
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            data = self.get()
            rule = next((item for item in data["travel_rules"] if {item.get("from"), item.get("to")} == {origin, destination}), None)
            if rule is None:
                rule = {"from": origin, "to": destination}
                data["travel_rules"].append(rule)
            rule["minutes"] = minutes
            self.storage.atomic_write_json(self.path, data)
            return dict(rule)

    def remove_travel_rule(self, origin: str, destination: str) -> bool:
        origin, destination = str(origin).strip(), str(destination).strip()
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            data = self.get()
            kept = [item for item in data["travel_rules"] if {item.get("from"), item.get("to")} != {origin, destination}]
            if len(kept) == len(data["travel_rules"]):
                return False
            data["travel_rules"] = kept
            self.storage.atomic_write_json(self.path, data)
            return True

    def preview(self, chapter: int, summary: dict) -> list[dict]:
        scene = summary.get("handoff", {}).get("final_scene", {}) if isinstance(summary.get("handoff"), dict) else {}
        location, story_time = str(scene.get("location", "")).strip(), str(scene.get("story_time", "")).strip()
        characters = scene.get("active_characters", []) if isinstance(scene.get("active_characters"), list) else []
        if not location or not story_time:
            return []
        current_point = self._time_value(story_time)
        data = self.get()
        issues = []
        for name in characters:
            previous = next((item for item in reversed(data["events"]) if int(item.get("chapter", 0)) < int(chapter) and name in item.get("characters", [])), None)
            if not previous:
                continue
            previous_point = self._time_value(str(previous.get("story_time", "")))
            comparable = current_point is not None and previous_point is not None and current_point[0] == previous_point[0]
            if current_point is not None and previous_point is not None and current_point[0] != previous_point[0]:
                issues.append(self._issue(name, "中", f"故事时间格式从“{previous.get('story_time')}”切换为“{story_time}”，无法可靠计算先后与行程", False))
            if comparable and current_point[1] < previous_point[1]:
                issues.append(self._issue(name, "高", f"故事时间从“{previous.get('story_time')}”倒退到“{story_time}”", True))
                continue
            old_location = str(previous.get("location", ""))
            if old_location and old_location != location and comparable:
                elapsed = current_point[1] - previous_point[1]
                required = self._travel_minutes(data["travel_rules"], old_location, location)
                if required is not None and elapsed < required:
                    issues.append(self._issue(name, "高", f"从“{old_location}”到“{location}”至少需{required}分钟，实际仅{max(0, int(elapsed))}分钟", True))
                elif elapsed == 0:
                    issues.append(self._issue(name, "中", f"同一故事时间从“{old_location}”出现在“{location}”，请确认是否瞬移", False))
        return issues

    def record(self, chapter: int, summary: dict) -> dict:
        scene = summary.get("handoff", {}).get("final_scene", {}) if isinstance(summary.get("handoff"), dict) else {}
        event = {
            "chapter": int(chapter), "story_time": str(scene.get("story_time", "")).strip(),
            "location": str(scene.get("location", "")).strip(),
            "characters": [str(item) for item in scene.get("active_characters", []) if str(item).strip()] if isinstance(scene.get("active_characters"), list) else [],
        }
        if not event["story_time"] and not event["location"]:
            return {"recorded": False, "issues": []}
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            data = self.get()
            issues = self.preview(chapter, summary)
            data["events"] = [item for item in data["events"] if int(item.get("chapter", 0)) != int(chapter)]
            event["issues"] = issues
            data["events"].append(event)
            data["events"].sort(key=lambda item: int(item.get("chapter", 0)))
            self.storage.atomic_write_json(self.path, data)
            return {"recorded": True, "event": event, "issues": issues}

    def compact_context(self, event_limit: int = 12) -> str:
        data = self.get()
        if not data["travel_rules"] and not data["events"]:
            return ""
        lines = ["【故事时钟与行程约束】"]
        for rule in data["travel_rules"][:50]:
            lines.append(f"- 移动：{rule.get('from')} ↔ {rule.get('to')}，至少{rule.get('minutes')}分钟")
        for event in data["events"][-max(1, event_limit):]:
            characters = "、".join(str(item) for item in event.get("characters", []))
            lines.append(f"- 第{event.get('chapter')}章末：{event.get('story_time') or '时间未标注'}｜{event.get('location') or '地点未标注'}｜{characters or '现场人物未标注'}")
        return "\n".join(lines)

    @staticmethod
    def _travel_minutes(rules: list[dict], origin: str, destination: str) -> int | None:
        if origin == destination:
            return 0
        graph: dict[str, list[tuple[str, int]]] = {}
        for item in rules:
            start, end = str(item.get("from", "")), str(item.get("to", ""))
            try:
                minutes = max(1, int(item.get("minutes", 0)))
            except (TypeError, ValueError):
                continue
            if not start or not end:
                continue
            graph.setdefault(start, []).append((end, minutes))
            graph.setdefault(end, []).append((start, minutes))
        distances = {origin: 0}
        visited = set()
        while True:
            current = min(
                ((distance, node) for node, distance in distances.items() if node not in visited),
                default=None,
            )
            if current is None:
                return None
            distance, node = current
            if node == destination:
                return distance
            visited.add(node)
            for neighbor, minutes in graph.get(node, []):
                candidate = distance + minutes
                if candidate < distances.get(neighbor, candidate + 1):
                    distances[neighbor] = candidate

    @staticmethod
    def _time_value(value: str) -> tuple[str, float] | None:
        text = str(value).strip()
        match = re.search(r"第\s*(\d+)\s*天(?:\s*(\d{1,2})[:：时](\d{1,2})?分?)?", text)
        if match:
            hour, minute = int(match.group(2) or 0), int(match.group(3) or 0)
            if hour > 23 or minute > 59:
                return None
            return "story_day", int(match.group(1)) * 1440 + hour * 60 + minute
        try:
            return "calendar", datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() / 60
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _issue(name: str, severity: str, message: str, blocking: bool) -> dict:
        return {"name": str(name), "severity": severity, "message": message, "blocking": blocking}
