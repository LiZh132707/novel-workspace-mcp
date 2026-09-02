"""版本存档点管理器：Git风格章节版本控制。"""
import json
import difflib
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional

from storage_utils import StorageManager
from filelock import FileLock


def _validate_path_id(id_str: str) -> None:
    """Validate that a savepoint ID doesn't contain path traversal characters."""
    if (
        not id_str or len(id_str) > 100
        or any(not (character.isalnum() or character in "_-") for character in id_str)
    ):
        raise ValueError(f"无效的存档点 ID: {id_str}")


class SavepointManager:
    """Git 风格的存档点系统。"""

    def __init__(self, novel_path: Path, logger, storage: StorageManager = None):
        self.path = novel_path / ".savepoints"
        self.logger = logger
        self.storage = storage or StorageManager(logger)
        self.path.mkdir(parents=True, exist_ok=True)

    def create(self, chapter_number: int, content: str,
               label: str = "", author: str = "AI") -> dict:
        """创建存档点。"""
        if not isinstance(chapter_number, int) or chapter_number < 1:
            raise ValueError(f"存档点章节号必须为正整数: {chapter_number}")
        if not content:
            raise ValueError("存档点内容不能为空")
        sp_dir = self.path / f"ch{chapter_number:06d}"
        with FileLock(str(sp_dir) + ".transaction.lock", timeout=30):
            return self._create_locked(chapter_number, content, label, author, sp_dir)

    def _create_locked(self, chapter_number: int, content: str, label: str, author: str, sp_dir: Path) -> dict:
        now = datetime.now()
        sp_id = now.strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:6]
        sp_dir.mkdir(parents=True, exist_ok=True)

        # 保存内容
        content_path = sp_dir / f"{sp_id}.txt"
        self.storage.atomic_write_text(content_path, content)

        # 保存元数据
        meta = {
            "id": sp_id,
            "chapter": chapter_number,
            "label": label or f"第{chapter_number}章 存档 {sp_id}",
            "author": author,
            "created_at": now.isoformat(),
            "content_size": len(content),
            "content_file": str(content_path),
        }
        meta_path = sp_dir / f"{sp_id}.json"
        self.storage.atomic_write_json(meta_path, meta)

        # 更新索引
        index = self._get_index(chapter_number)
        index = [item for item in index if str(item.get("id", "")) != sp_id]
        index.insert(0, meta)
        self.storage.atomic_write_json(sp_dir / "_index.json", index)

        self.logger.info("存档点创建: 第%d章 %s", chapter_number, sp_id)
        return meta

    def list_savepoints(self, chapter_number: int = None,
                        limit: int = 20) -> list[dict]:
        """列出存档点。"""
        if chapter_number:
            return self._get_index(chapter_number)[:limit]

        # 跨章节列出
        all_sps = []
        for idx_file in sorted(self.path.glob("ch*/_index.json"), reverse=True):
            try:
                sps = json.loads(idx_file.read_text("utf-8"))
                all_sps.extend(sps)
            except Exception:
                pass
        all_sps.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return all_sps[:limit]

    def restore(self, chapter_number: int, savepoint_id: str) -> Optional[str]:
        """恢复存档点内容。"""
        _validate_path_id(savepoint_id)
        sp_dir = self.path / f"ch{chapter_number:06d}"
        content_file = sp_dir / f"{savepoint_id}.txt"
        try:
            if content_file.exists():
                self.logger.info("恢复存档: 第%d章 %s", chapter_number, savepoint_id)
                return content_file.read_text("utf-8", errors="replace")
        except Exception:
            pass
        return None

    def diff(self, chapter_number: int, sp_id_a: str,
             sp_id_b: str = None) -> str:
        """比较两个存档点的差异。"""
        _validate_path_id(sp_id_a)
        if sp_id_b:
            _validate_path_id(sp_id_b)
        sp_dir = self.path / f"ch{chapter_number:06d}"

        try:
            content_a = (sp_dir / f"{sp_id_a}.txt").read_text("utf-8", errors="replace").splitlines()
        except Exception:
            content_a = []

        if sp_id_b:
            try:
                content_b = (sp_dir / f"{sp_id_b}.txt").read_text("utf-8", errors="replace").splitlines()
            except Exception:
                content_b = []
        else:
            # 对比当前文件
            from config import CHAPTER_FILE_PATTERN
            current_file = Path(sp_dir.parent.parent / "chapters"
                                / CHAPTER_FILE_PATTERN.format(chapter_number))
            content_b = []
            if current_file.exists():
                try:
                    content_b = current_file.read_text("utf-8", errors="replace").splitlines()
                except Exception:
                    pass

        diff = difflib.unified_diff(
            content_a, content_b,
            fromfile=f"savepoint:{sp_id_a}",
            tofile=f"current:{sp_id_b or 'latest'}",
            lineterm="",
        )
        return "\n".join(diff)

    def _get_index(self, chapter_number: int) -> list[dict]:
        """获取章节的存档索引，索引损坏时尝试备份恢复。"""
        sp_dir = self.path / f"ch{chapter_number:06d}"
        idx_file = sp_dir / "_index.json"
        if idx_file.exists():
            try:
                data = json.loads(idx_file.read_text("utf-8"))
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)]
                self.logger.warning("存档索引结构错误，正在从元数据重建")
            except Exception as e:
                self.logger.warning("索引文件损坏，尝试从备份恢复: %s", e)
                try:
                    recovered = self.storage._try_recover(idx_file)
                    if recovered is not None and isinstance(recovered, list):
                        return [item for item in recovered if isinstance(item, dict)]
                except Exception:
                    pass
        rebuilt = self._rebuild_index(sp_dir)
        if rebuilt:
            self.logger.warning("已从现存元数据恢复 %d 个存档点", len(rebuilt))
        return rebuilt

    def _rebuild_index(self, sp_dir: Path) -> list[dict]:
        by_id = {}
        for meta_path in sp_dir.glob("*.json") if sp_dir.exists() else []:
            if meta_path.name == "_index.json":
                continue
            data = self.storage.safe_read_json(meta_path, {})
            savepoint_id = str(data.get("id", "")) if isinstance(data, dict) else ""
            if not savepoint_id or not (sp_dir / f"{savepoint_id}.txt").exists():
                continue
            by_id[savepoint_id] = data
        items = list(by_id.values())
        items.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return items
