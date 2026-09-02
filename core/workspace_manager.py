"""工作空间管理器：多小说项目管理，带备份与锁。"""
import shutil
from datetime import datetime
from typing import Optional

from config import NOVELS_ROOT, WORKSPACE_FILE, NOVEL_DIRS, BIBLE_FILES, OUTLINE_FILES
from storage_utils import StorageManager
from core.novel_manager import NovelManager
from core.project_schema import ProjectSchemaManager
from filelock import FileLock


class WorkspaceManager:
    def __init__(self, logger):
        self.logger = logger
        self.storage = StorageManager(logger)
        self._current_novel: Optional[str] = None
        self._load_workspace()

    def _load_workspace(self):
        loaded = self.storage.safe_read_json(WORKSPACE_FILE, {"novels": {}, "current": None})
        if not isinstance(loaded, dict):
            loaded = {}
        novels = loaded.get("novels", {})
        if not isinstance(novels, dict):
            novels = {}
        current = loaded.get("current")
        current = current if isinstance(current, str) and current in novels else None
        self.data = {**loaded, "novels": novels, "current": current}
        self._current_novel = self.data.get("current")

    def _save(self):
        self.storage.atomic_write_json(WORKSPACE_FILE, self.data)

    def list_novels(self) -> list[dict]:
        results = []
        # 拷贝键列表防止迭代期间字典被修改
        for name, info in list(self.data["novels"].items()):
            novel_path = NOVELS_ROOT / name
            chapters_dir = novel_path / "chapters"
            chapter_count = 0
            if chapters_dir.exists():
                try:
                    chapter_count = len([f for f in chapters_dir.iterdir() if f.suffix == ".txt" and f.stem.isdigit()])
                except (FileNotFoundError, PermissionError, NotADirectoryError) as _e:
                    pass
            results.append({
                "name": name,
                "status": info.get("status", "unknown"),
                "chapter_count": chapter_count,
                "created_at": info.get("created_at", ""),
                "updated_at": info.get("updated_at", ""),
                "genre": info.get("genre", ""),
            })
        return results

    def create_novel(self, name: str, genre: str = "", style: str = "",
                     description: str = "") -> dict:
        import re as _re
        if not _re.match(r"^[\w\u4e00-\u9fff\-]+$", name):
            raise ValueError(f"小说名 \"{name}\" 包含非法字符，只允许中英文、数字、下划线和连字符")
        # 双检锁：先检查一次，加锁后再检查一次，防止并发竞态
        if name in self.data["novels"]:
            raise ValueError(f"小说 '{name}' 已存在")
        from filelock import FileLock
        ws_lock = FileLock(str(WORKSPACE_FILE) + ".lock", timeout=30)
        with ws_lock:
            # 重新加载工作区数据，在锁内二次检查
            self._load_workspace()
            if name in self.data["novels"]:
                raise ValueError(f"小说 '{name}' 已存在")
            novel_path = NOVELS_ROOT / name
            if novel_path.exists():
                raise ValueError(f"小说目录 '{name}' 已存在但未登记，请先手动确认或导入，系统不会自动覆盖")
            for d in NOVEL_DIRS:
                (novel_path / d).mkdir(parents=True, exist_ok=True)
            try:
                for fname, content in BIBLE_FILES.items():
                    self.storage.atomic_write_text(novel_path / "bible" / fname, content)
                for fname, content in OUTLINE_FILES.items():
                    self.storage.atomic_write_text(novel_path / "outline" / fname, content)
                state = {
                    "current_chapter": 0,
                    "total_words": 0,
                    "status": "创作中",
                    "genre": genre,
                    "style": style,
                    "description": description,
                    "next_goal": "",
                    "last_summary": "",
                }
                self.storage.atomic_write_json(novel_path / "state.json", state)
                ProjectSchemaManager(novel_path, self.storage).initialize(name)
                now = datetime.now().isoformat()
                self.data["novels"][name] = {
                    "status": "创作中", "created_at": now, "updated_at": now, "genre": genre,
                }
                self.data["current"] = name
                self._current_novel = name
                self._save()
            except Exception:
                shutil.rmtree(novel_path, ignore_errors=True)
                raise
        self.logger.info("创建小说: %s", name)
        return {**state, "name": name}

    def open_novel(self, name: str) -> dict:
        with FileLock(str(WORKSPACE_FILE) + ".lock", timeout=30):
            self._load_workspace()
            if name not in self.data["novels"]:
                raise ValueError(f"小说 '{name}' 不存在")
            self.data["current"] = name
            self._current_novel = name
            self._save()
        self.logger.info("打开小说: %s", name)
        return self.get_current_novel()

    def get_current_novel(self) -> dict:
        name = self._current_novel or self.data.get("current")
        if not name:
            raise ValueError("未选择任何小说")
        if name not in self.data["novels"]:
            raise ValueError(f"小说 '{name}' 不存在")
        novel_path = NOVELS_ROOT / name
        if not novel_path.exists():
            raise ValueError(f"小说目录 '{name}' 不存在（文件已被删除）")
        nm = NovelManager(name, novel_path, self.logger, self.storage)
        info = self.data["novels"][name]
        state = nm.get_state()
        return {
            "name": name,
            "status": info.get("status", "unknown"),
            "current_chapter": state.get("current_chapter", 0),
            "total_words": state.get("total_words", 0),
            "genre": info.get("genre", ""),
            "style": state.get("style", ""),
            "description": state.get("description", ""),
            "next_goal": state.get("next_goal", ""),
            "last_summary": state.get("last_summary", ""),
        }

    def get_novel_manager(self) -> NovelManager:
        name = self._current_novel or self.data.get("current")
        if not name:
            raise ValueError("未选择任何小说")
        if name not in self.data["novels"]:
            raise ValueError(f"小说 '{name}' 不存在")
        novel_path = NOVELS_ROOT / name
        if not novel_path.exists():
            raise ValueError(f"小说目录 '{name}' 不存在（文件已被删除）")
        return NovelManager(name, novel_path, self.logger, self.storage)

    def capture_current(self) -> tuple[NovelManager, dict]:
        """在同一工作区锁内捕获当前小说管理器与状态，供长任务稳定绑定。"""
        with FileLock(str(WORKSPACE_FILE) + ".lock", timeout=30):
            self._load_workspace()
            manager = self.get_novel_manager()
            info = self.get_current_novel()
            return manager, info

    def update_status(self, name: str, status: str):
        with FileLock(str(WORKSPACE_FILE) + ".lock", timeout=30):
            self._load_workspace()
            if name in self.data["novels"]:
                self.data["novels"][name]["status"] = status
                self.data["novels"][name]["updated_at"] = datetime.now().isoformat()
                self._save()
            else:
                raise ValueError(f"小说 \"{name}\" 不存在")

    def remove_novel(self, name: str):
        with FileLock(str(WORKSPACE_FILE) + ".lock", timeout=30):
            self._load_workspace()
            if name not in self.data["novels"]:
                raise ValueError("小说不存在")
            self.data["novels"].pop(name)
            if self.data.get("current") == name:
                self.data["current"] = None
                self._current_novel = None
            self._save()

    def register_restored(self, name: str, metadata: dict):
        with FileLock(str(WORKSPACE_FILE) + ".lock", timeout=30):
            self._load_workspace()
            if name in self.data["novels"]:
                raise ValueError(f"小说《{name}》已经登记")
            self.data["novels"][name] = metadata or {}
            self.data["current"] = name
            self._current_novel = name
            self._save()

    def update_registration(self, name: str, metadata: dict, make_current: bool = True):
        with FileLock(str(WORKSPACE_FILE) + ".lock", timeout=30):
            self._load_workspace()
            if name not in self.data["novels"]:
                raise ValueError(f"小说 '{name}' 不存在")
            self.data["novels"][name].update(metadata or {})
            self.data["novels"][name]["updated_at"] = datetime.now().isoformat()
            if make_current:
                self.data["current"] = name
                self._current_novel = name
            self._save()

    def rollback_created(self, name: str, previous_current: str | None = None):
        with FileLock(str(WORKSPACE_FILE) + ".lock", timeout=30):
            self._load_workspace()
            self.data["novels"].pop(name, None)
            if self.data.get("current") == name:
                restored = previous_current if previous_current in self.data["novels"] else None
                self.data["current"] = restored
                self._current_novel = restored
            else:
                self._current_novel = self.data.get("current")
            self._save()
