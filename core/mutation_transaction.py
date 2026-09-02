"""跨文件小说结构操作的临时事务快照。"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path


class NovelMutationTransaction:
    DIRECTORIES = ("summaries", "tracking", "reviews", "timeline", "characters", "outline", "planning")
    FILES = ("state.json", "facts.json", "foreshadowing.json")

    def __init__(
        self, root: Path, chapters: list[int],
        directories: tuple[str, ...] | None = None,
        files: tuple[str, ...] | None = None,
    ):
        self.root = root
        self.chapters = sorted(set(int(chapter) for chapter in chapters if int(chapter) > 0))
        self.directories = self.DIRECTORIES if directories is None else directories
        self.files = self.FILES if files is None else files
        self.backup = root / ".transactions" / uuid.uuid4().hex
        self.existing_dirs: set[str] = set()
        self.existing_files: set[str] = set()
        self.existing_chapters: set[int] = set()

    def __enter__(self):
        self.backup.mkdir(parents=True, exist_ok=False)
        ignore = shutil.ignore_patterns("*.lock", "*.tmp", ".backups")
        for name in self.directories:
            source = self.root / name
            if source.exists():
                self.existing_dirs.add(name)
                shutil.copytree(source, self.backup / name, ignore=ignore)
        for name in self.files:
            source = self.root / name
            if source.exists():
                self.existing_files.add(name)
                destination = self.backup / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        for chapter in self.chapters:
            source = self.root / "chapters" / f"{chapter:06d}.txt"
            if source.exists():
                self.existing_chapters.add(chapter)
                destination = self.backup / "chapters" / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        return self

    def __exit__(self, exc_type, exc, _traceback):
        cleanup_backup = exc_type is None
        try:
            if exc_type is not None:
                try:
                    self.restore()
                    cleanup_backup = True
                except Exception as restore_exc:
                    raise RuntimeError(
                        f"事务回滚失败，备份已保留在 {self.backup}；原始错误: {exc}"
                    ) from restore_exc
        finally:
            if cleanup_backup:
                shutil.rmtree(self.backup, ignore_errors=True)
                parent = self.backup.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
        return False

    def restore(self):
        for name in self.directories:
            current = self.root / name
            if current.exists():
                shutil.rmtree(current)
            if name in self.existing_dirs:
                shutil.copytree(self.backup / name, current)
        for name in self.files:
            current = self.root / name
            if current.exists():
                current.unlink()
            if name in self.existing_files:
                current.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.backup / name, current)
        chapters_dir = self.root / "chapters"
        chapters_dir.mkdir(parents=True, exist_ok=True)
        for chapter in self.chapters:
            current = chapters_dir / f"{chapter:06d}.txt"
            current.unlink(missing_ok=True)
            if chapter in self.existing_chapters:
                shutil.copy2(self.backup / "chapters" / current.name, current)
