import tempfile
import zipfile
from pathlib import Path

from core.data_portability import ProjectZipRestorer, TextNovelImporter, TrashManager


def test_text_import_chapter_detection():
    text = "前言\n第1章 开始\n正文一\n第二章 继续\n正文二"
    chapters = TextNovelImporter.split(text)
    assert len(chapters) == 2
    assert "前言" in chapters[0]["content"]
    assert chapters[1]["title"].startswith("第二章")


def test_trash_move_and_restore():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        novels = root / "novels"
        source = novels / "测试"
        source.mkdir(parents=True)
        (source / "state.json").write_text("{}", "utf-8")
        trash = TrashManager(root)
        record = trash.move("测试", source, {"status": "创作中"})
        assert not source.exists() and trash.list()
        trash.restore(record["id"], novels)
        assert source.exists()


def test_failed_registration_can_compensate_trash_restore():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        novels = root / "novels"
        source = novels / "测试"
        source.mkdir(parents=True)
        (source / "state.json").write_text("{}", "utf-8")
        trash = TrashManager(root)
        record = trash.move("测试", source, {"status": "创作中"})
        restored = trash.restore(record["id"], novels)
        trash.undo_restore(restored, novels)
        assert not source.exists()
        assert trash.list()[0]["id"] == record["id"]


def test_trash_can_be_permanently_purged():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        novels = root / "novels"
        source = novels / "测试"
        source.mkdir(parents=True)
        trash = TrashManager(root)
        record = trash.move("测试", source, {})
        trash.purge(record["id"])
        assert trash.list() == []


def test_zip_restore_rejects_path_traversal():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        archive = root / "bad.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("../escape.txt", "bad")
            output.writestr("state.json", "{}")
        destination = root / "restore"
        destination.mkdir()
        try:
            ProjectZipRestorer.extract(archive, destination)
            assert False, "path traversal should fail"
        except ValueError:
            pass
