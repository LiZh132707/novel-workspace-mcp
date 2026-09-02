"""存储工具：文件备份、写入锁、事务安全写入。"""
import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Any, Optional
from filelock import FileLock


class StorageManager:
    """带自动备份和写入锁的存储管理器。"""

    def __init__(self, logger, backup_count: int = 5):
        self.logger = logger
        self.backup_count = backup_count
        self._locks: dict[str, FileLock] = {}

    def _get_lock(self, path: Path) -> FileLock:
        key = str(path.absolute())
        if key not in self._locks:
            # Keep lock metadata out of the data directory.  This prevents
            # ``*_working.*`` globs and exports from treating lock files as
            # user content, while retaining per-file locking semantics.
            lock_dir = path.parent / ".locks"
            lock_dir.mkdir(parents=True, exist_ok=True)
            lock_path = lock_dir / f"{path.name}.lock"
            self._locks[key] = FileLock(str(lock_path), timeout=30)
        return self._locks[key]

    def backup_file(self, path: Path):
        """自动备份文件（保留最近 backup_count 份）。"""
        if not path.exists():
            return
        backup_dir = path.parent / ".backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = backup_dir / f"{path.name}.{timestamp}.bak"
        shutil.copy2(path, backup_path)
        self.logger.debug("备份: %s -> %s", path.name, backup_path.name)
        # 清理旧备份
        backups = sorted(backup_dir.glob(f"{path.name}.*.bak"))
        while len(backups) > self.backup_count:
            old = backups.pop(0)
            old.unlink(missing_ok=True)
            self.logger.debug("清理旧备份: %s", old.name)

    def atomic_write_json(self, path: Path, data: Any):
        """事务性 JSON 写入：先写临时文件再 rename，带备份和锁。"""
        lock = self._get_lock(path)
        with lock:
            self.backup_file(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.parent / f".{path.name}.tmp"
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            tmp.replace(path)
            self.logger.debug("事务写入: %s", path.name)

    def atomic_write_text(self, path: Path, text: str):
        """事务性文本写入（含自动备份）。"""
        lock = self._get_lock(path)
        with lock:
            self.backup_file(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.parent / f".{path.name}.tmp"
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
            self.logger.debug("事务文本写入: %s", path.name)

    def safe_read_json(self, path: Path, default: Any = None) -> Any:
        """安全读取 JSON，失败时返回默认值。"""
        try:
            if path.exists():
                return json.loads(path.read_text("utf-8"))
        except Exception as e:
            self.logger.warning("读取 %s 失败: %s，尝试备份恢复", path.name, e)
            recovered = self._try_recover(path)
            if recovered is not None:
                return recovered
        if default is not None:
            return default
        return None  # 文件不存在且无默认值时返回 None

    def _try_recover(self, path: Path) -> Optional[Any]:
        backup_dir = path.parent / ".backups"
        if not backup_dir.exists():
            return None
        with self._get_lock(path):
            if path.exists():
                try:
                    return json.loads(path.read_text("utf-8"))
                except Exception:
                    pass
            backups = sorted(backup_dir.glob(f"{path.name}.*.bak"), reverse=True)
            for backup in backups:
                try:
                    raw = backup.read_text("utf-8")
                    data = json.loads(raw)
                    self.logger.info("从备份恢复: %s", backup.name)
                    recovery = path.parent / f".{path.name}.recovery.tmp"
                    recovery.write_text(raw, encoding="utf-8")
                    recovery.replace(path)
                    return data
                except Exception:
                    continue
        return None
