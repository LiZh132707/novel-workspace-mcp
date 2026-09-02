"""规划修改影响传播与未来章节缓存失效。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from filelock import FileLock

from storage_utils import StorageManager


class PlanningImpactManager:
    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.novel_path = novel_path
        self.path = novel_path / "planning" / "impacts.json"
        self.storage = storage or StorageManager(logger)

    def record_changes(
        self, old_volumes: list, new_volumes: list, old_briefs: dict, new_briefs: dict,
        current_chapter: int, upstream_changed: bool = False,
    ) -> dict:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._record_changes(
                old_volumes, new_volumes, old_briefs, new_briefs, current_chapter, upstream_changed,
            )

    def touch_chapters(self, chapters: set[int] | list[int], message: str) -> dict:
        normalized = sorted({self._int(chapter) for chapter in chapters if self._int(chapter) > 0})
        if not normalized:
            return {"status": "unchanged", "chapters": []}
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            now = datetime.now()
            item = {
                "id": now.strftime("%Y%m%d%H%M%S%f"),
                "created_at": now.isoformat(), "status": "pending",
                "chapters": normalized, "message": str(message)[:500],
            }
            self.storage.atomic_write_json(self.novel_path / "planning" / "epoch.json", {
                "id": item["id"], "created_at": item["created_at"], "chapters": normalized,
            })
            self._mark_open_turns_stale(set(normalized), item["id"])
            data = self.storage.safe_read_json(self.path, {"items": []})
            data = data if isinstance(data, dict) else {}
            items = data.get("items", []) if isinstance(data.get("items"), list) else []
            items.append(item)
            data["items"] = self._prune_items(items)
            self.storage.atomic_write_json(self.path, data)
            return item

    def _record_changes(
        self, old_volumes: list, new_volumes: list, old_briefs: dict, new_briefs: dict,
        current_chapter: int, upstream_changed: bool = False,
    ) -> dict:
        changed = set()
        old_volumes = old_volumes if isinstance(old_volumes, list) else []
        new_volumes = new_volumes if isinstance(new_volumes, list) else []
        old_briefs = old_briefs if isinstance(old_briefs, dict) else {}
        new_briefs = new_briefs if isinstance(new_briefs, dict) else {}
        for index in range(max(len(old_volumes), len(new_volumes))):
            old_item = old_volumes[index] if index < len(old_volumes) and isinstance(old_volumes[index], dict) else {}
            new_item = new_volumes[index] if index < len(new_volumes) and isinstance(new_volumes[index], dict) else {}
            if json.dumps(old_item, ensure_ascii=False, sort_keys=True) == json.dumps(new_item, ensure_ascii=False, sort_keys=True):
                continue
            starts = [self._int(item.get("start_chapter"), current_chapter + 1) for item in (old_item, new_item) if item]
            ends = [self._int(item.get("end_chapter"), current_chapter + 1) for item in (old_item, new_item) if item]
            start = max(current_chapter + 1, min(starts or [current_chapter + 1]))
            end = max(start, max(ends or [start]))
            changed.update(range(start, end + 1))
        explicitly_changed_briefs = set()
        for key in set(old_briefs) | set(new_briefs):
            if old_briefs.get(key) != new_briefs.get(key) and str(key).isdigit() and int(key) > current_chapter:
                changed.add(int(key))
                explicitly_changed_briefs.add(int(key))
        if upstream_changed:
            candidates = [int(key) for key in set(old_briefs) | set(new_briefs) if str(key).isdigit()]
            candidates.extend(self._int(item.get("end_chapter")) for item in [*old_volumes, *new_volumes] if isinstance(item, dict))
            target = max([current_chapter + 3, *candidates])
            changed.update(range(current_chapter + 1, target + 1))
        data = self.storage.safe_read_json(self.path, {"items": []})
        data = data if isinstance(data, dict) else {"items": []}
        data["items"] = data.get("items", []) if isinstance(data.get("items"), list) else []
        if changed:
            item = {
                "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
                "created_at": datetime.now().isoformat(),
                "status": "pending",
                "chapters": sorted(changed),
                "message": "上游规划已修改，以下未来章节的提要或场景规划需要复核",
            }
            self.storage.atomic_write_json(self.novel_path / "planning" / "epoch.json", {
                "id": item["id"], "created_at": item["created_at"], "chapters": item["chapters"],
            })
            self._mark_open_turns_stale(changed, item["id"])
            data["items"].append(item)
            data["items"] = self._prune_items(data["items"])
            protected_scenes = self._invalidate_future_artifacts(changed, explicitly_changed_briefs, new_briefs)
            item["protected_confirmed_scenes"] = protected_scenes
            self.storage.atomic_write_json(self.path, data)
            return item
        return {"status": "unchanged", "chapters": []}

    def _invalidate_future_artifacts(self, chapters: set[int], preserved_briefs: set[int], new_briefs: dict):
        outline = self.novel_path / "outline"
        self._remove_mapping_keys(outline / "chapter_plans.json", chapters)
        protected_scenes = self._remove_scene_keys(outline / "scene_outlines.json", chapters)
        self._remove_mapping_keys(outline / "chapter_briefs.json", chapters - preserved_briefs)
        titles_path = outline / "chapter_titles.json"
        titles = self.storage.safe_read_json(titles_path, {})
        titles = titles if isinstance(titles, dict) else {}
        titles_changed = False
        for chapter in chapters:
            key = str(chapter)
            if chapter in preserved_briefs and isinstance(new_briefs.get(key), dict):
                title = str(new_briefs[key].get("title", ""))
                if titles.get(key) != title:
                    titles[key] = title
                    titles_changed = True
            elif key in titles:
                titles.pop(key, None)
                titles_changed = True
        if titles_changed:
            self.storage.atomic_write_json(titles_path, titles)
        opening_path = outline / "opening_chapters.json"
        opening = self.storage.safe_read_json(opening_path, {})
        if isinstance(opening, dict) and isinstance(opening.get("chapters"), list):
            kept = [
                item for item in opening["chapters"]
                if not isinstance(item, dict) or self._int(item.get("chapter")) not in chapters
            ]
            if len(kept) != len(opening["chapters"]):
                opening["chapters"] = kept
                self.storage.atomic_write_json(opening_path, opening)
        return protected_scenes

    def _remove_scene_keys(self, path: Path, chapters: set[int]) -> list[int]:
        data = self.storage.safe_read_json(path, {})
        if not isinstance(data, dict):
            return []
        changed = False
        protected = []
        for chapter in sorted(chapters):
            key = str(chapter)
            item = data.get(key)
            if isinstance(item, dict) and item.get("status") == "confirmed":
                protected.append(chapter)
            elif key in data:
                data.pop(key, None)
                changed = True
        if changed:
            self.storage.atomic_write_json(path, data)
        return protected

    def _mark_open_turns_stale(self, chapters: set[int], impact_id: str):
        path = self.novel_path / "turns" / "index.json"
        if not path.exists():
            return
        with FileLock(str(path) + ".lock", timeout=30):
            data = self.storage.safe_read_json(path, {"schema_version": 1, "items": []})
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                return
            changed = False
            for item in data["items"]:
                if not isinstance(item, dict) or item.get("status") not in {"drafting", "ready", "blocked"}:
                    continue
                if self._int(item.get("chapter")) not in chapters:
                    continue
                item.update({
                    "planning_stale": True, "planning_impact_id": impact_id,
                    "planning_stale_at": datetime.now().isoformat(),
                })
                changed = True
            if changed:
                self.storage.atomic_write_json(path, data)

    def _remove_mapping_keys(self, path: Path, chapters: set[int]):
        data = self.storage.safe_read_json(path, {})
        if not isinstance(data, dict):
            return
        changed = False
        for chapter in chapters:
            if str(chapter) in data:
                data.pop(str(chapter), None)
                changed = True
        if changed:
            self.storage.atomic_write_json(path, data)

    def list(self) -> list[dict]:
        data = self.storage.safe_read_json(self.path, {"items": []})
        items = data.get("items", []) if isinstance(data, dict) else []
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    @staticmethod
    def _prune_items(items: list[dict], terminal_limit: int = 100) -> list[dict]:
        terminal = [item for item in items if item.get("status") != "pending"]
        dropped = {id(item) for item in terminal[:-terminal_limit]} if len(terminal) > terminal_limit else set()
        return [item for item in items if id(item) not in dropped]

    def resolve(self, impact_id: str) -> dict:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            data = self.storage.safe_read_json(self.path, {"items": []})
            items = data.get("items", []) if isinstance(data, dict) and isinstance(data.get("items"), list) else []
            item = next((entry for entry in items if isinstance(entry, dict) and entry.get("id") == impact_id), None)
            if not item:
                raise ValueError("规划影响记录不存在")
            item["status"] = "resolved"
            item["resolved_at"] = datetime.now().isoformat()
            self.storage.atomic_write_json(self.path, {"items": items})
            return item

    @staticmethod
    def _int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
