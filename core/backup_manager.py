"""Automatic project backups and retention policy."""
from __future__ import annotations

import re
import hashlib
import json
import stat
import threading
import zipfile
import zlib
from datetime import datetime, timedelta
from pathlib import Path

from filelock import FileLock


_BACKUP_TIMESTAMP = re.compile(r"^\d{8}_\d{6}_\d{6}\.zip$")
_BACKUP_NAME = re.compile(r"(?P<novel>[\w-]+)_(?P<stamp>\d{8}_\d{6}_\d{6})\.zip")


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
        if isinstance(keep_per_novel, bool) or not isinstance(keep_per_novel, int) or keep_per_novel < 1:
            raise ValueError("Backup retention must be a positive integer")
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
                    self._inspect_archive(archive)
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
                    and not path.is_symlink()
                    and path.name.startswith(prefix)
                    and _BACKUP_TIMESTAMP.fullmatch(path.name[len(prefix):]) is not None
                )
            ),
            reverse=True,
        )

    def status(self) -> dict:
        files = self.list_backups()
        return {"count": len(files), "latest": files[0]["name"] if files else None, "directory": str(self.output), "keep_per_novel": self.keep_per_novel}

    def resolve_backup(self, filename: str, novel_name: str | None = None) -> Path:
        match = _BACKUP_NAME.fullmatch(filename) if isinstance(filename, str) else None
        if not match or (novel_name is not None and match["novel"] != novel_name):
            raise ValueError("Backup does not belong to the requested project")
        try:
            datetime.strptime(match["stamp"], "%Y%m%d_%H%M%S_%f")
        except ValueError as exc:
            raise ValueError("Invalid backup timestamp") from exc
        path = self.output / filename
        if path.is_symlink() or not path.is_file() or path.resolve().parent != self.output.resolve():
            raise ValueError("Backup not found in the configured backup directory")
        return path

    def list_backups(self, novel_name: str | None = None) -> list[dict]:
        """Return inexpensive inventory metadata; integrity is checked explicitly."""
        result = []
        for entry in self.output.iterdir() if self.output.exists() else []:
            try:
                path = self.resolve_backup(entry.name, novel_name)
                match = _BACKUP_NAME.fullmatch(path.name)
                result.append({
                    "name": path.name, "novel": match["novel"],
                    "created_at": datetime.strptime(match["stamp"], "%Y%m%d_%H%M%S_%f").isoformat(),
                    "size_bytes": path.stat().st_size,
                })
            except (ValueError, OSError):
                continue
        return sorted(result, key=lambda item: (item["created_at"], item["name"]), reverse=True)

    @staticmethod
    def _inspect_archive(archive: zipfile.ZipFile) -> dict:
        """Validate CRC and portable project structure without extracting files."""
        members = archive.infolist()
        total = sum(item.file_size for item in members)
        if len(members) > 10000 or total > 1_000_000_000:
            raise ValueError("Backup exceeds verification limits (10,000 entries / 1 GB expanded)")
        names = set()
        for item in members:
            parts = item.filename.rstrip("/").split("/")
            if (not all(parts) or any(part in (".", "..") or ":" in part for part in parts)
                    or "\\" in item.orig_filename or "\x00" in item.orig_filename
                    or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError("Backup contains an unsafe archive entry")
            identity = item.filename.rstrip("/").casefold()
            if identity in names:
                raise ValueError("Backup contains duplicate archive paths")
            names.add(identity)
        try:
            state = archive.getinfo("state.json")
        except KeyError as exc:
            raise ValueError("Backup is missing state.json") from exc
        if state.is_dir() or state.file_size > 2_000_000:
            raise ValueError("Invalid or oversized project state")
        damaged = archive.testzip()
        if damaged is not None:
            raise ValueError("Backup CRC verification failed")
        try:
            data = json.loads(archive.read(state).decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise ValueError("Backup state.json is not valid UTF-8 JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("Backup state.json must be a JSON object")
        return {"file_count": sum(not item.is_dir() for item in members), "uncompressed_bytes": total}

    def verify(self, filename: str, novel_name: str | None = None) -> dict:
        path = self.resolve_backup(filename, novel_name)
        report = {"name": filename, "status": "fail", "errors": []}
        try:
            with path.open("rb") as stream:
                digest = hashlib.sha256()
                size = 0
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                    size += len(block)
                report.update({"sha256": digest.hexdigest(), "size_bytes": size})
                stream.seek(0)
                with zipfile.ZipFile(stream) as archive:
                    report.update(self._inspect_archive(archive))
            report["status"] = "pass"
        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error) as exc:
            report["errors"].append(str(exc))
        return report
