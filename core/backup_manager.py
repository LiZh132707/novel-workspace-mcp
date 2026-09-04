"""Automatic project backups and retention policy."""
from __future__ import annotations

import re
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from filelock import FileLock


_BACKUP_TIMESTAMP = re.compile(r"^\d{8}_\d{6}_\d{6}\.zip$")


class BackupScheduler:
    def __init__(
        self,
        novels_root: Path,
        storage_root: Path,
        logger,
        interval_seconds: int = 3600,
        keep_per_novel: int = 7,
        output_dir: Path | None = None,
    ):
        self.novels_root = novels_root
        self.output = Path(output_dir) if output_dir is not None else storage_root / "backups"
        self.logger = logger
        self.interval_seconds = interval_seconds
        self.keep_per_novel = keep_per_novel
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.output.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="novel-backup-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.backup_due()
            except Exception:
                self.logger.exception("Automatic backup check failed")
            self._stop.wait(self.interval_seconds)

    def backup_due(self) -> list[Path]:
        created = []
        cutoff = datetime.now() - timedelta(hours=24)
        for novel_path in self.novels_root.iterdir() if self.novels_root.exists() else []:
            if not novel_path.is_dir() or not (novel_path / "state.json").exists():
                continue
            backups = self._backups_for(novel_path.name)
            if backups and datetime.fromtimestamp(backups[0].stat().st_mtime) > cutoff:
                continue
            created.append(self.create(novel_path))
        return created

    def create(self, novel_path: Path) -> Path:
        novel_path = novel_path.resolve()
        novels_root = self.novels_root.resolve()
        if novel_path.parent != novels_root or not (novel_path / "state.json").is_file():
            raise ValueError("backup source must be a novel project directly under the novels directory")
        output = self.output.resolve()
        if output == novel_path or output.is_relative_to(novel_path):
            raise ValueError("backup output directory must be outside the novel project")
        self.output.mkdir(parents=True, exist_ok=True)
        target = self.output / f"{novel_path.name}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.zip"
        temporary = target.with_suffix(".zip.tmp")
        try:
            with FileLock(str(novel_path / ".novel_mutation.lock"), timeout=600):
                with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                    for file in novel_path.rglob("*"):
                        relative = file.relative_to(novel_path)
                        if (
                            file.is_file()
                            and not file.is_symlink()
                            and "exports" not in relative.parts
                            and not file.name.endswith((".lock", ".tmp"))
                        ):
                            archive.write(file, relative)
                with zipfile.ZipFile(temporary, "r") as archive:
                    damaged = archive.testzip()
                    if damaged is not None:
                        raise OSError(f"backup verification failed for {damaged}")
                temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        backups = self._backups_for(novel_path.name)
        for old in backups[self.keep_per_novel:]:
            old.unlink(missing_ok=True)
        self.logger.info("Backup completed: %s", target.name)
        return target

    def _backups_for(self, novel_name: str) -> list[Path]:
        """List backups literally, without treating a project name as a glob."""
        if not self.output.exists():
            return []
        prefix = f"{novel_name}_"
        return sorted(
            (
                path
                for path in self.output.iterdir()
                if (
                    path.is_file()
                    and path.name.startswith(prefix)
                    and _BACKUP_TIMESTAMP.fullmatch(path.name[len(prefix):]) is not None
                )
            ),
            reverse=True,
        )

    def status(self) -> dict:
        files = sorted(self.output.glob("*.zip"), reverse=True) if self.output.exists() else []
        return {"count": len(files), "latest": files[0].name if files else None, "directory": str(self.output), "keep_per_novel": self.keep_per_novel}
