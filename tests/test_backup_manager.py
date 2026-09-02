import logging
import tempfile
import threading
import time
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
