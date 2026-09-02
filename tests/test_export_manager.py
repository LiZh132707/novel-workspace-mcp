import logging
import tempfile
import zipfile
from pathlib import Path

from core.export_manager import ExportManager


def test_all_export_formats():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        chapters = root / "chapters"
        chapters.mkdir()
        (chapters / "000001.txt").write_text("第一段。\n\n第二段。", "utf-8")
        manager = ExportManager("测试小说", root, logging.getLogger("test"))
        for format_name in ("txt", "md", "docx", "epub", "zip"):
            output = manager.export(format_name)
            assert output.exists() and output.stat().st_size > 0
        with zipfile.ZipFile(manager.export("zip")) as archive:
            assert "chapters/000001.txt" in archive.namelist()
