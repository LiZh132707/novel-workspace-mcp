"""故事设定、人物与规划的轻量版本快照。"""
from datetime import datetime
from pathlib import Path
import difflib
import json
import shutil
import uuid
from filelock import FileLock


class PlanningVersionManager:
    TRACKED_DIRS = ("bible", "outline", "characters", "tracking", "reviews")
    TRACKED_FILES = ("facts.json", "foreshadowing.json")
    PLANNING_STATE_KEYS = (
        "next_goal", "target_chapters", "ending_direction", "planning_completed",
        "genre", "style", "description",
    )

    def __init__(self, novel_path: Path):
        self.novel_path = novel_path
        self.root = novel_path / "versions" / "planning"

    def snapshot(self, label: str = "手动保存前") -> dict:
        with FileLock(str(self.novel_path / ".novel_mutation.lock"), timeout=600):
            return self._snapshot(label)

    def _snapshot(self, label: str) -> dict:
        version_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
        target = self.root / version_id
        target.mkdir(parents=True, exist_ok=True)
        for name in self.TRACKED_DIRS:
            source = self.novel_path / name
            if source.exists():
                shutil.copytree(source, target / name, dirs_exist_ok=True)
        for name in self.TRACKED_FILES:
            source = self.novel_path / name
            if source.exists():
                shutil.copy2(source, target / name)
        state_path = self.novel_path / "state.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text("utf-8"))
            except Exception:
                state = {}
            if isinstance(state, dict):
                planning_state = {key: state[key] for key in self.PLANNING_STATE_KEYS if key in state}
                (target / "planning_state.json").write_text(json.dumps(planning_state, ensure_ascii=False, indent=2), "utf-8")
        meta = {"id": version_id, "label": label[:80], "created_at": datetime.now().isoformat(timespec="seconds")}
        (target / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
        return meta

    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        result = []
        for path in sorted(self.root.iterdir(), reverse=True):
            try:
                result.append(json.loads((path / "meta.json").read_text("utf-8")))
            except Exception:
                pass
        return result[:30]

    def restore(self, version_id: str) -> dict:
        with FileLock(str(self.novel_path / ".novel_mutation.lock"), timeout=600):
            if not self._valid_id(version_id):
                raise ValueError("无效的规划版本ID")
            source = self.root / version_id
            if not source.exists():
                raise ValueError("规划版本不存在")
            try:
                meta = json.loads((source / "meta.json").read_text("utf-8"))
            except Exception as exc:
                raise ValueError("规划版本元数据损坏") from exc
            if not isinstance(meta, dict):
                raise ValueError("规划版本元数据损坏")
            backup = self._snapshot("恢复版本前自动备份")
            try:
                self._apply_snapshot(source)
            except Exception:
                self._apply_snapshot(self.root / backup["id"])
                raise
            return meta

    def _apply_snapshot(self, source: Path):
        for name in self.TRACKED_DIRS:
            saved = source / name
            current = self.novel_path / name
            if current.exists():
                shutil.rmtree(current)
            if saved.exists():
                shutil.copytree(saved, current)
        for name in self.TRACKED_FILES:
            saved = source / name
            current = self.novel_path / name
            current.unlink(missing_ok=True)
            if saved.exists():
                shutil.copy2(saved, current)
        planning_state_path = source / "planning_state.json"
        if planning_state_path.exists():
            planning_state = json.loads(planning_state_path.read_text("utf-8"))
            if not isinstance(planning_state, dict):
                raise ValueError("规划状态快照损坏")
            state_path = self.novel_path / "state.json"
            try:
                state = json.loads(state_path.read_text("utf-8")) if state_path.exists() else {}
            except Exception:
                state = {}
            state = state if isinstance(state, dict) else {}
            for key in self.PLANNING_STATE_KEYS:
                state.pop(key, None)
            state.update({key: value for key, value in planning_state.items() if key in self.PLANNING_STATE_KEYS})
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")

    def diff(self, version_id: str) -> str:
        if not self._valid_id(version_id):
            raise ValueError("无效的规划版本ID")
        source = self.root / Path(version_id).name
        chunks = []
        for relative in (
            "bible/world.md", "bible/rules.md", "bible/style.md", "outline/main.md", "outline/volumes.json",
            "outline/chapter_briefs.json", "outline/scene_outlines.json",
        ):
            old_path, new_path = source / relative, self.novel_path / relative
            old = old_path.read_text("utf-8").splitlines() if old_path.exists() else []
            new = new_path.read_text("utf-8").splitlines() if new_path.exists() else []
            if old != new:
                chunks.extend(difflib.unified_diff(old, new, fromfile=f"版本/{relative}", tofile=f"当前/{relative}", lineterm=""))
        planning_state_path = source / "planning_state.json"
        if planning_state_path.exists():
            try:
                old_state = json.loads(planning_state_path.read_text("utf-8"))
                current_state = json.loads((self.novel_path / "state.json").read_text("utf-8"))
            except Exception:
                old_state, current_state = {}, {}
            old_state = old_state if isinstance(old_state, dict) else {}
            current_state = current_state if isinstance(current_state, dict) else {}
            current_planning = {key: current_state[key] for key in self.PLANNING_STATE_KEYS if key in current_state}
            old_lines = json.dumps(old_state, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
            new_lines = json.dumps(current_planning, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
            if old_lines != new_lines:
                chunks.extend(difflib.unified_diff(old_lines, new_lines, fromfile="版本/规划状态", tofile="当前/规划状态", lineterm=""))
        return "\n".join(chunks)[:50000] or "没有差异"

    @staticmethod
    def _valid_id(value: str) -> bool:
        return bool(
            value and len(value) <= 100
            and all(character.isalnum() or character in "_-" for character in value)
        )
