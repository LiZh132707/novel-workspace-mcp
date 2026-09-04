import logging
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from core.backup_manager import BackupScheduler


def test_backup_creation_and_retention():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        novels = root / "novels"
        novel = novels / "测试小说"
        novel.mkdir(parents=True)
        (novel / "state.json").write_text("{}", "utf-8")
        (novel / "chapter.txt").write_text("正文", "utf-8")
        scheduler = BackupScheduler(novels, root, logging.getLogger("test"), keep_per_novel=2)
        for _ in range(3):
            scheduler.create(novel)
        assert scheduler.status()["count"] <= 2


def test_backup_waits_for_novel_transaction_and_publishes_atomically(tmp_path):
    from filelock import FileLock

    novels = tmp_path / "novels"
    novel = novels / "锁测试"
    novel.mkdir(parents=True)
    (novel / "state.json").write_text("{}", "utf-8")
    scheduler = BackupScheduler(novels, tmp_path, logging.getLogger("backup-lock-test"))
    finished = threading.Event()
    result = []

    def create():
        result.append(scheduler.create(novel))
        finished.set()

    with FileLock(str(novel / ".novel_mutation.lock"), timeout=30):
        worker = threading.Thread(target=create)
        worker.start()
        time.sleep(0.05)
        assert not finished.is_set()
        assert list((tmp_path / "backups").glob("*.zip")) == []
    worker.join(timeout=5)
    assert finished.is_set()
    assert result[0].exists()
    assert list((tmp_path / "backups").glob("*.tmp")) == []


def test_backup_retention_treats_project_name_as_literal_text(tmp_path):
    novels = tmp_path / "novels"
    novel = novels / "Story[1]"
    novel.mkdir(parents=True)
    (novel / "state.json").write_text("{}", "utf-8")
    scheduler = BackupScheduler(novels, tmp_path, logging.getLogger("backup-literal"), keep_per_novel=2)
    for _ in range(3):
        scheduler.create(novel)
    assert len(scheduler._backups_for("Story[1]")) == 2


def test_backup_retention_does_not_mix_names_with_shared_prefix(tmp_path):
    novels = tmp_path / "novels"
    short = novels / "Story"
    long = novels / "Story_extra"
    for novel in (short, long):
        novel.mkdir(parents=True)
        (novel / "state.json").write_text("{}", "utf-8")
    scheduler = BackupScheduler(novels, tmp_path, logging.getLogger("backup-prefix"), keep_per_novel=1)
    scheduler.create(long)
    scheduler.create(short)
    scheduler.create(short)
    assert len(scheduler._backups_for("Story")) == 1
    assert len(scheduler._backups_for("Story_extra")) == 1


def test_backup_exclusions_are_relative_and_archive_is_readable(tmp_path):
    root = tmp_path / "exports" / "runtime"
    novels = root / "novels"
    novel = novels / "Portable"
    (novel / "exports").mkdir(parents=True)
    (novel / "state.json").write_text("{}", "utf-8")
    (novel / "chapter.txt").write_text("chapter", "utf-8")
    (novel / "exports" / "draft.docx").write_text("generated", "utf-8")
    scheduler = BackupScheduler(novels, root, logging.getLogger("backup-relative"))
    archive_path = scheduler.create(novel)
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        assert "chapter.txt" in archive.namelist()
        assert "exports/draft.docx" not in archive.namelist()


def test_backup_rejects_output_directory_inside_project(tmp_path):
    novels = tmp_path / "novels"
    novel = novels / "UnsafeOutput"
    novel.mkdir(parents=True)
    (novel / "state.json").write_text("{}", "utf-8")
    scheduler = BackupScheduler(
        novels,
        tmp_path,
        logging.getLogger("backup-output"),
        output_dir=novel / "backups",
    )
    try:
        scheduler.create(novel)
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("an in-project backup output should be rejected")
