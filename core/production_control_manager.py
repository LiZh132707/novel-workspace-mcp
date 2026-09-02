"""自动连载策略、规划树、问题中心、节奏、预算与成稿编排。"""
from __future__ import annotations

from datetime import datetime
from filelock import FileLock

from core.consistency_manager import ConsistencyManager
from core.fact_manager import FactManager
from core.foreshadow_manager import ForeshadowManager
from core.planning_impact_manager import PlanningImpactManager
from core.quality_tracker import QualityTracker
from core.story_logic_manager import StoryLogicManager
from storage_utils import StorageManager


DEFAULT_SERIAL_POLICY = {
    "enabled": False, "target_chapter": 100, "batch_size": 1, "target_words": 5000,
    "commit_mode": "balanced", "stop_on_warning": True, "max_retries": 2,
    "cooldown_seconds": 30, "scene_mode": False,
    "allowed_start_hour": 0, "allowed_end_hour": 24, "notification_enabled": True,
    "breaker_failure_limit": 3, "breaker_short_chapter_limit": 2,
    "minimum_tokens_per_second": 15.0,
}


class ProductionControlManager:
    MODES = ("main_progress", "complication", "character", "subplot", "exploration", "aftermath", "breathing", "setup")

    def __init__(self, novel_manager, logger=None, storage: StorageManager | None = None):
        self.nm = novel_manager
        self.root = novel_manager.path
        self.logger = logger
        self.storage = storage or novel_manager.storage or StorageManager(logger)
        self.policy_path = self.root / "automation" / "serial_policy.json"
        self.runtime_path = self.root / "automation" / "serial_runtime.json"

    def policy(self) -> dict:
        saved = self.storage.safe_read_json(self.policy_path, {})
        saved = saved if isinstance(saved, dict) else {}
        return self._normalize_policy({**DEFAULT_SERIAL_POLICY, **saved})

    def update_policy(self, values: dict) -> dict:
        policy = self._normalize_policy({**self.policy(), **(values if isinstance(values, dict) else {})})
        policy["updated_at"] = datetime.now().isoformat()
        self.storage.atomic_write_json(self.policy_path, policy)
        return policy

    def runtime(self) -> dict:
        data = self.storage.safe_read_json(self.runtime_path, {})
        data = data if isinstance(data, dict) else {}
        return {
            "consecutive_failures": max(0, self._int(data.get("consecutive_failures"))),
            "consecutive_short_chapters": max(0, self._int(data.get("consecutive_short_chapters"))),
            "last_speed": max(0.0, self._float(data.get("last_speed"))),
            "state": str(data.get("state", "idle")),
            "last_stop_reason": str(data.get("last_stop_reason", "")),
            "updated_at": str(data.get("updated_at", "")),
        }

    def record_chapter_result(self, words: int, target_words: int, speed: float) -> dict:
        policy, runtime = self.policy(), self.runtime()
        runtime["consecutive_failures"] = 0
        runtime["consecutive_short_chapters"] = (
            runtime["consecutive_short_chapters"] + 1
            if int(words) < int(target_words) * 0.8 else 0
        )
        runtime["last_speed"] = max(0.0, float(speed or 0))
        reason = ""
        if runtime["consecutive_short_chapters"] >= policy["breaker_short_chapter_limit"]:
            reason = f"连续{runtime['consecutive_short_chapters']}章短于目标80%"
        elif 0 < runtime["last_speed"] < policy["minimum_tokens_per_second"]:
            reason = f"生成速度降至{runtime['last_speed']} token/s，低于阈值{policy['minimum_tokens_per_second']}"
        return self._save_runtime(runtime, reason)

    def record_failure(self, error: str) -> dict:
        policy, runtime = self.policy(), self.runtime()
        runtime["consecutive_failures"] += 1
        reason = (
            f"连续失败{runtime['consecutive_failures']}次：{str(error)[:240]}"
            if runtime["consecutive_failures"] >= policy["breaker_failure_limit"] else ""
        )
        return self._save_runtime(runtime, reason)

    def next_allowed_time(self, now: datetime | None = None) -> datetime | None:
        now = now or datetime.now()
        policy = self.policy()
        start, end = policy["allowed_start_hour"], policy["allowed_end_hour"]
        if start == 0 and end == 24:
            return None
        hour = now.hour
        allowed = start <= hour < end if start < end else hour >= start or hour < end
        if allowed:
            return None
        candidate = now.replace(hour=start % 24, minute=0, second=0, microsecond=0)
        if candidate <= now:
            from datetime import timedelta
            candidate += timedelta(days=1)
        return candidate

    def planning_tree(self) -> dict:
        volumes = self.storage.safe_read_json(self.root / "outline" / "volumes.json", [])
        briefs = self.storage.safe_read_json(self.root / "outline" / "chapter_briefs.json", {})
        scenes = self.storage.safe_read_json(self.root / "outline" / "scene_outlines.json", {})
        volumes = volumes if isinstance(volumes, list) else []
        briefs = briefs if isinstance(briefs, dict) else {}
        scenes = scenes if isinstance(scenes, dict) else {}
        volume_nodes = []
        for index, volume in enumerate(volumes):
            if not isinstance(volume, dict):
                continue
            sections = []
            for section_index, section in enumerate(volume.get("sections", []) if isinstance(volume.get("sections"), list) else []):
                if isinstance(section, dict):
                    sections.append({"id": f"section:{index}:{section_index}", "type": "section", "data": section})
            volume_nodes.append({"id": f"volume:{index}", "type": "volume", "data": volume, "children": sections})
        chapter_nodes = []
        for key in sorted({*briefs, *scenes}, key=lambda value: int(value) if str(value).isdigit() else 10**9):
            if not str(key).isdigit():
                continue
            chapter_nodes.append({
                "id": f"chapter:{key}", "type": "chapter", "chapter": int(key),
                "brief": briefs.get(key), "scene_outline": scenes.get(key),
                "locked": isinstance(scenes.get(key), dict) and scenes[key].get("status") == "confirmed",
            })
        return {
            "sources": [
                self._text_node("world", "世界观", "bible/world.md"),
                self._text_node("rules", "世界规则", "bible/rules.md"),
                self._text_node("outline", "全书总纲", "outline/main.md"),
            ],
            "volumes": volume_nodes, "chapters": chapter_nodes,
        }

    def update_tree_node(self, node_id: str, data) -> dict:
        node_id = str(node_id)
        with FileLock(str(self.root / "planning" / ".tree.lock"), timeout=30):
            if node_id in {"world", "rules", "outline"}:
                relative = {"world": "bible/world.md", "rules": "bible/rules.md", "outline": "outline/main.md"}[node_id]
                self.storage.atomic_write_text(self.root / relative, str(data)[:200000])
                chapters = self._future_window()
            elif node_id.startswith("chapter:"):
                chapter = self._positive(node_id.split(":", 1)[1])
                if not isinstance(data, dict):
                    raise ValueError("章节提要必须为JSON对象")
                path = self.root / "outline" / "chapter_briefs.json"
                mapping = self.storage.safe_read_json(path, {})
                mapping = mapping if isinstance(mapping, dict) else {}
                mapping[str(chapter)] = {**data, "chapter": chapter}
                self.storage.atomic_write_json(path, mapping)
                titles_path = self.root / "outline" / "chapter_titles.json"
                titles = self.storage.safe_read_json(titles_path, {})
                titles = titles if isinstance(titles, dict) else {}
                titles[str(chapter)] = str(data.get("title", ""))[:120]
                self.storage.atomic_write_json(titles_path, titles)
                chapters = {chapter}
            elif node_id == "volumes":
                if not isinstance(data, list):
                    raise ValueError("分卷结构必须为JSON数组")
                self.storage.atomic_write_json(self.root / "outline" / "volumes.json", data)
                chapters = self._future_window()
            else:
                raise ValueError("不支持的规划节点")
            impact = PlanningImpactManager(self.root, self.logger, self.storage).touch_chapters(
                chapters, f"规划树节点 {node_id} 已人工修改",
            )
        return {"node_id": node_id, "impact": impact, "tree": self.planning_tree()}

    def rhythm(self) -> dict:
        briefs = self.storage.safe_read_json(self.root / "outline" / "chapter_briefs.json", {})
        briefs = briefs if isinstance(briefs, dict) else {}
        recent = []
        for key in sorted((key for key in briefs if str(key).isdigit()), key=int)[-12:]:
            item = briefs[key]
            if isinstance(item, dict):
                recent.append(str(item.get("chapter_mode", "main_progress")))
        counts = {mode: recent.count(mode) for mode in self.MODES}
        last = recent[-1] if recent else ""
        main_run = 0
        for mode in reversed(recent):
            if mode not in {"main_progress", "complication"}:
                break
            main_run += 1
        if main_run >= 3:
            recommended = "aftermath" if last == "complication" else "character"
            reason = "连续主线/复杂化章节过多，建议安排人物反应、余波或关系变化"
        elif last in {"breathing", "aftermath", "setup"}:
            recommended, reason = "main_progress", "上一章已完成缓冲或铺垫，建议恢复主线推进"
        elif counts["exploration"] == 0 and len(recent) >= 6:
            recommended, reason = "exploration", "最近章节缺少世界探索与新信息来源"
        else:
            recommended, reason = "complication", "保持推进，但通过新阻力或代价避免直线完成节纲"
        return {"recent_modes": recent, "counts": counts, "recommended": recommended, "reason": reason}

    def issues(self) -> dict:
        consistency = ConsistencyManager(self.nm, self.logger).check_all()
        debts = QualityTracker(self.root, self.logger, self.storage).get_pending_debts()
        facts = FactManager(self.root, self.logger, self.storage).unresolved_conflicts()
        foreshadows = [
            item for item in ForeshadowManager(self.root, self.logger, self.storage).list(self.nm.get_current_chapter())
            if item.get("overdue")
        ]
        logic = StoryLogicManager(self.root, self.logger, self.storage).get()
        promises = [item for item in logic.get("promises", []) if item.get("status", "open") == "open"]
        impacts_data = self.storage.safe_read_json(self.root / "planning" / "impacts.json", {"items": []})
        impacts = impacts_data.get("items", []) if isinstance(impacts_data, dict) else []
        pending_impacts = [item for item in impacts if isinstance(item, dict) and item.get("status") == "pending"]
        groups = {
            "consistency": [dict(item, issue_id=f"consistency:{index}") for index, item in enumerate(consistency)],
            "quality_debts": [dict(item, issue_id=f"quality:{item.get('id', index)}") for index, item in enumerate(debts)],
            "fact_conflicts": [dict(item, issue_id=f"fact:{index}") for index, item in enumerate(facts)],
            "overdue_foreshadowing": [dict(item, issue_id=f"foreshadow:{item.get('id', index)}") for index, item in enumerate(foreshadows)],
            "open_promises": [dict(item, issue_id=f"promise:{index}") for index, item in enumerate(promises)],
            "planning_impacts": [dict(item, issue_id=f"impact:{item.get('id', index)}") for index, item in enumerate(pending_impacts)],
        }
        return {"total": sum(len(value) for value in groups.values()), "groups": groups}

    def resolve_issue(self, issue_id: str, action: str, values: dict | None = None) -> dict:
        values = values if isinstance(values, dict) else {}
        kind, _, raw_id = str(issue_id).partition(":")
        if kind == "quality":
            QualityTracker(self.root, self.logger, self.storage).resolve_debt(raw_id, str(values.get("resolution", action)))
        elif kind == "foreshadow":
            status = "resolved" if action == "resolve" else "cancelled" if action == "cancel" else "open"
            ForeshadowManager(self.root, self.logger, self.storage).update(
                raw_id, status=status, **({"target_chapter": values["target_chapter"]} if values.get("target_chapter") else {}),
            )
        elif kind == "impact":
            PlanningImpactManager(self.root, self.logger, self.storage).resolve(raw_id)
        elif kind == "fact":
            self._resolve_fact(self._int(raw_id), str(values.get("resolution", action)))
        elif kind == "promise":
            self._resolve_promise(self._int(raw_id), action)
        else:
            raise ValueError("该问题需要跳转到对应章节或规划节点人工修复")
        return {"resolved": issue_id, "issues": self.issues()}

    def statistics(self, tasks: list[dict] | None = None) -> dict:
        data = self.storage.safe_read_json(self.root / "turns" / "index.json", {"items": []})
        items = data.get("items", []) if isinstance(data, dict) else []
        committed = [item for item in items if isinstance(item, dict) and item.get("status") == "committed"] if isinstance(items, list) else []
        speeds, durations, calls, warnings, manual, trend = [], [], 0, 0, 0, []
        for item in committed:
            metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
            metrics = metadata.get("metrics", {}) if isinstance(metadata.get("metrics"), dict) else {}
            if self._float(metrics.get("tokens_per_second")) > 0:
                speeds.append(self._float(metrics["tokens_per_second"]))
            durations.append(self._float(metrics.get("elapsed_seconds")))
            calls += self._int(metrics.get("calls"))
            warnings += len(item.get("post_commit_warnings", [])) if isinstance(item.get("post_commit_warnings"), list) else 0
            manual += int(item.get("source") in {"manual", "candidate", "review"})
            trend.append({
                "chapter": self._int(item.get("chapter")),
                "tokens_per_second": self._float(metrics.get("tokens_per_second")),
                "elapsed_seconds": self._float(metrics.get("elapsed_seconds")),
                "calls": self._int(metrics.get("calls")),
                "quality": (item.get("quality", {}) if isinstance(item.get("quality"), dict) else {}).get("status", ""),
                "revised": bool(metadata.get("revised", False)),
            })
        generated_tasks = [
            item for item in (tasks or [])
            if isinstance(item, dict) and item.get("kind") in {"batch_chapters", "workflow", "chapter"}
        ]
        failed_tasks = [item for item in generated_tasks if item.get("status") == "failed"]
        paused_tasks = [item for item in generated_tasks if item.get("status") == "paused"]
        return {
            "committed_turns": len(committed),
            "average_tokens_per_second": round(sum(speeds) / len(speeds), 2) if speeds else 0,
            "average_elapsed_seconds": round(sum(durations) / len(durations), 2) if durations else 0,
            "model_calls": calls, "post_commit_warnings": warnings,
            "manual_intervention_rate": round(manual / len(committed) * 100, 1) if committed else 0,
            "task_count": len(generated_tasks),
            "task_failure_rate": round(len(failed_tasks) / len(generated_tasks) * 100, 1) if generated_tasks else 0,
            "task_pause_rate": round(len(paused_tasks) / len(generated_tasks) * 100, 1) if generated_tasks else 0,
            "trend": sorted(trend, key=lambda item: item["chapter"])[-50:],
            "runtime": self.runtime(),
        }

    def budget(self, chapters: int, target_words: int, speed: float = 50) -> dict:
        chapters = max(1, min(1000, int(chapters)))
        target_words = max(500, min(20000, int(target_words)))
        speed = max(1.0, float(speed or 50))
        prose_tokens = int(target_words / 1.8) + 1000
        calls_per_chapter = 4
        output_tokens = chapters * (prose_tokens + 4200)
        seconds = round(output_tokens / speed + chapters * 35)
        return {
            "chapters": chapters, "target_words": target_words,
            "estimated_calls": chapters * calls_per_chapter,
            "estimated_output_tokens": output_tokens,
            "estimated_seconds": seconds, "estimated_hours": round(seconds / 3600, 2),
            "context_window": 131072, "concurrency": 1,
        }

    def manuscript(self) -> dict:
        volumes = self.storage.safe_read_json(self.root / "outline" / "volumes.json", [])
        volumes = volumes if isinstance(volumes, list) else []
        chapters = {}
        titles = self.storage.safe_read_json(self.root / "outline" / "chapter_titles.json", {})
        titles = titles if isinstance(titles, dict) else {}
        for path in (self.root / "chapters").glob("*.txt"):
            if path.stem.isdigit():
                text = path.read_text("utf-8", errors="replace")
                chapters[int(path.stem)] = {"title": titles.get(path.stem, ""), "words": len("".join(text.split()))}
        groups = []
        assigned = set()
        for volume in volumes:
            if not isinstance(volume, dict):
                continue
            start, end = self._int(volume.get("start_chapter")), self._int(volume.get("end_chapter"))
            items = [{"chapter": number, **chapters[number]} for number in range(start, end + 1) if number in chapters]
            assigned.update(item["chapter"] for item in items)
            groups.append({"title": volume.get("title", "未命名卷"), "start": start, "end": end, "words": sum(item["words"] for item in items), "chapters": items})
        unassigned = [{"chapter": number, **item} for number, item in sorted(chapters.items()) if number not in assigned]
        return {"volumes": groups, "unassigned": unassigned, "total_words": sum(item["words"] for item in chapters.values()), "chapter_count": len(chapters)}

    def _text_node(self, key: str, label: str, relative: str) -> dict:
        path = self.root / relative
        return {"id": key, "type": "text", "label": label, "content": path.read_text("utf-8", errors="replace") if path.exists() else ""}

    def _future_window(self) -> set[int]:
        current = self.nm.get_current_chapter()
        target = self._int(self.nm.get_state().get("target_chapters")) or current + 20
        return set(range(current + 1, min(target, current + 200) + 1))

    @staticmethod
    def _normalize_policy(values: dict) -> dict:
        return {
            "enabled": bool(values.get("enabled", False)),
            "target_chapter": max(1, min(1000, ProductionControlManager._int(values.get("target_chapter")) or 100)),
            "batch_size": max(1, min(10, ProductionControlManager._int(values.get("batch_size")) or 1)),
            "target_words": max(500, min(20000, ProductionControlManager._int(values.get("target_words")) or 5000)),
            "commit_mode": values.get("commit_mode") if values.get("commit_mode") in {"review", "balanced", "automatic"} else "balanced",
            "stop_on_warning": bool(values.get("stop_on_warning", True)),
            "max_retries": max(0, min(5, ProductionControlManager._int(values.get("max_retries")))),
            "cooldown_seconds": max(0, min(3600, ProductionControlManager._int(values.get("cooldown_seconds")))),
            "scene_mode": bool(values.get("scene_mode", False)),
            "allowed_start_hour": max(0, min(23, ProductionControlManager._int(values.get("allowed_start_hour")))),
            "allowed_end_hour": max(1, min(24, ProductionControlManager._int(values.get("allowed_end_hour")) or 24)),
            "notification_enabled": bool(values.get("notification_enabled", True)),
            "breaker_failure_limit": max(1, min(10, ProductionControlManager._int(values.get("breaker_failure_limit")) or 3)),
            "breaker_short_chapter_limit": max(1, min(10, ProductionControlManager._int(values.get("breaker_short_chapter_limit")) or 2)),
            "minimum_tokens_per_second": max(0.0, min(500.0, ProductionControlManager._float(values.get("minimum_tokens_per_second")) or 15.0)),
        }

    def _save_runtime(self, runtime: dict, reason: str) -> dict:
        runtime["state"] = "tripped" if reason else "running"
        runtime["last_stop_reason"] = reason
        runtime["updated_at"] = datetime.now().isoformat()
        self.storage.atomic_write_json(self.runtime_path, runtime)
        if reason:
            self.update_policy({"enabled": False})
        return runtime

    def _resolve_fact(self, index: int, resolution: str):
        path = self.root / "facts.json"
        with FileLock(str(path) + ".transaction.lock", timeout=30):
            data = self.storage.safe_read_json(path, {"facts": [], "conflicts": []})
            conflicts = data.get("conflicts", []) if isinstance(data, dict) else []
            unresolved = [item for item in conflicts if isinstance(item, dict) and not item.get("resolved")]
            if index < 0 or index >= len(unresolved):
                raise ValueError("事实冲突不存在")
            unresolved[index].update({"resolved": True, "resolution": resolution[:500], "resolved_at": datetime.now().isoformat()})
            self.storage.atomic_write_json(path, data)

    def _resolve_promise(self, index: int, action: str):
        manager = StoryLogicManager(self.root, self.logger, self.storage)
        with FileLock(str(manager.path) + ".transaction.lock", timeout=30):
            data = manager.get()
            open_items = [item for item in data["promises"] if item.get("status", "open") == "open"]
            if index < 0 or index >= len(open_items):
                raise ValueError("叙事承诺不存在")
            open_items[index]["status"] = "resolved" if action == "resolve" else "cancelled"
            open_items[index]["resolved_at"] = datetime.now().isoformat()
            self.storage.atomic_write_json(manager.path, data)

    @staticmethod
    def _positive(value) -> int:
        result = ProductionControlManager._int(value)
        if result < 1:
            raise ValueError("章节号必须为正整数")
        return result

    @staticmethod
    def _int(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _float(value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
