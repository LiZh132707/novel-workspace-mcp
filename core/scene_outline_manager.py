"""章节场景细纲管理。"""
from __future__ import annotations

from pathlib import Path

from filelock import FileLock

from storage_utils import StorageManager


class SceneOutlineManager:
    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.root = novel_path
        self.path = novel_path / "outline" / "scene_outlines.json"
        self.logger = logger
        self.storage = storage or StorageManager(logger)

    @staticmethod
    def normalize_scene(scene: dict, index: int) -> dict:
        try:
            target_words = int(scene.get("target_words", 800) or 800)
        except (TypeError, ValueError):
            target_words = 800
        return {
            "index": index,
            "title": str(scene.get("title", f"场景{index}"))[:120],
            "summary": str(scene.get("summary") or scene.get("description", ""))[:2000],
            "characters": [str(item)[:60] for item in scene.get("characters", []) if str(item).strip()][:12],
            "location": str(scene.get("location", ""))[:160],
            "goal": str(scene.get("goal") or scene.get("purpose", ""))[:1000],
            "obstacle": str(scene.get("obstacle") or scene.get("conflict", ""))[:1000],
            "turn": str(scene.get("turn") or scene.get("turning_point", ""))[:1000],
            "outcome": str(scene.get("outcome") or scene.get("result", ""))[:1000],
            "emotion": str(scene.get("emotion") or scene.get("emotional_shift", ""))[:500],
            "target_words": max(100, min(5000, target_words)),
        }

    def save(self, chapter: int, payload: dict) -> dict:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            previous = self.get(chapter)
            item = self._save(chapter, payload)
        if previous != item:
            self._invalidate_cached_plan(int(chapter))
            from core.planning_impact_manager import PlanningImpactManager
            PlanningImpactManager(self.root, self.logger, self.storage).touch_chapters(
                {int(chapter)}, "人工场景细纲已修改，本章旧规划与生成中草稿需要重新确认",
            )
        return item

    def _save(self, chapter: int, payload: dict) -> dict:
        chapter = int(chapter)
        if chapter < 1:
            raise ValueError("场景细纲章节号必须为正整数")
        scenes = payload.get("scenes", []) if isinstance(payload, dict) else []
        normalized = [self.normalize_scene(scene, index + 1) for index, scene in enumerate(scenes) if isinstance(scene, dict)][:12]
        if not normalized:
            raise ValueError("场景细纲至少需要一个场景")
        item = {
            "chapter": chapter,
            "opening_hook": str(payload.get("opening_hook", ""))[:1000],
            "ending_hook": str(payload.get("ending_hook") or payload.get("ending_cliffhanger", ""))[:1000],
            "scenes": normalized,
            "total_target_words": sum(scene["target_words"] for scene in normalized),
            "status": str(payload.get("status", "draft")) if str(payload.get("status", "draft")) in {"draft", "confirmed"} else "draft",
        }
        data = self.storage.safe_read_json(self.path, {})
        data = data if isinstance(data, dict) else {}
        data[str(chapter)] = item
        self.storage.atomic_write_json(self.path, data)
        return item

    def seed_from_plan(self, chapter: int, plan: dict) -> dict:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            existing = self.get(chapter)
            if existing and existing.get("status") == "confirmed":
                return existing
            scenes = plan.get("scenes", []) if isinstance(plan, dict) else []
            payload = {
                "opening_hook": plan.get("opening_hook", ""),
                "ending_hook": plan.get("ending_hook", ""),
                "scenes": scenes,
            }
            return self._save(chapter, payload)

    def get(self, chapter: int) -> dict | None:
        data = self.storage.safe_read_json(self.path, {})
        item = data.get(str(chapter)) if isinstance(data, dict) else None
        return item if isinstance(item, dict) else None

    def list(self) -> list[dict]:
        data = self.storage.safe_read_json(self.path, {})
        if not isinstance(data, dict):
            return []
        keys = sorted((key for key in data if str(key).isdigit()), key=lambda value: int(value))
        return [data[key] for key in keys if isinstance(data[key], dict)]

    def render(self, chapter: int) -> str:
        item = self.get(chapter)
        if not item:
            return ""
        lines = [f"【第{chapter}章已确认场景细纲】"]
        if item.get("opening_hook"):
            lines.append("开场承接：" + item["opening_hook"])
        for scene in item.get("scenes", []):
            parts = [scene.get("summary", ""), f"目标：{scene.get('goal', '')}", f"阻力：{scene.get('obstacle', '')}", f"转折：{scene.get('turn', '')}", f"结果：{scene.get('outcome', '')}", f"字数：{scene.get('target_words', 0)}"]
            lines.append(f"场景{scene.get('index')} {scene.get('title')}｜" + "｜".join(part for part in parts if part and not part.endswith("：")))
        if item.get("ending_hook"):
            lines.append("结尾牵引：" + item["ending_hook"])
        return "\n".join(lines)

    def confirmed_plan(self, chapter: int) -> dict | None:
        item = self.get(chapter)
        if not item or item.get("status") != "confirmed":
            return None
        scenes = []
        for scene in item.get("scenes", []) if isinstance(item.get("scenes"), list) else []:
            if not isinstance(scene, dict):
                continue
            scenes.append({
                "name": scene.get("title", ""), "summary": scene.get("summary", ""),
                "goal": scene.get("goal", ""), "obstacle": scene.get("obstacle", ""),
                "turn": scene.get("turn", ""), "exit_state": scene.get("outcome", ""),
                "word_budget": scene.get("target_words", 800),
                "characters": scene.get("characters", []), "location": scene.get("location", ""),
                "emotion": scene.get("emotion", ""),
            })
        if not scenes:
            return None
        return {
            "beats": [
                str(scene.get("goal") or scene.get("summary") or scene.get("name", "")).strip()
                for scene in scenes
                if str(scene.get("goal") or scene.get("summary") or scene.get("name", "")).strip()
            ],
            "opening_hook": item.get("opening_hook", ""),
            "ending_hook": item.get("ending_hook", ""),
            "scenes": scenes,
        }

    def _invalidate_cached_plan(self, chapter: int):
        path = self.root / "outline" / "chapter_plans.json"
        with FileLock(str(path) + ".transaction.lock", timeout=30):
            data = self.storage.safe_read_json(path, {})
            if isinstance(data, dict) and str(chapter) in data:
                data.pop(str(chapter), None)
                self.storage.atomic_write_json(path, data)
