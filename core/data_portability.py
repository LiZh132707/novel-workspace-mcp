"""回收站、TXT章节识别与安全ZIP恢复。"""
from __future__ import annotations

import json
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path


class TrashManager:
    def __init__(self, storage_root: Path):
        self.root = storage_root / ".trash"
        self.root.mkdir(parents=True, exist_ok=True)

    def move(self, name: str, novel_path: Path, metadata: dict) -> dict:
        if not novel_path.exists():
            raise ValueError("小说目录不存在")
        trash_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{name}"
        destination = self.root / trash_id
        shutil.move(str(novel_path), str(destination))
        record = {"id": trash_id, "name": name, "deleted_at": datetime.now().isoformat(), "metadata": metadata}
        (destination / ".trash.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), "utf-8")
        return record

    def list(self) -> list[dict]:
        records = []
        for directory in sorted(self.root.iterdir(), reverse=True):
            if not directory.is_dir():
                continue
            try:
                record = json.loads((directory / ".trash.json").read_text("utf-8"))
                records.append(record)
            except Exception:
                continue
        return records

    def restore(self, trash_id: str, novels_root: Path) -> dict:
        self._validate_id(trash_id)
        source = self.root / trash_id
        if not source.is_dir():
            raise ValueError("回收站项目不存在")
        record = json.loads((source / ".trash.json").read_text("utf-8"))
        name = record["name"]
        destination = novels_root / name
        if destination.exists():
            raise ValueError(f"小说《{name}》已经存在，不能恢复")
        (source / ".trash.json").unlink(missing_ok=True)
        shutil.move(str(source), str(destination))
        return record

    def undo_restore(self, record: dict, novels_root: Path):
        trash_id = str(record.get("id", ""))
        self._validate_id(trash_id)
        name = str(record.get("name", ""))
        source = novels_root / name
        destination = self.root / trash_id
        if not source.is_dir() or destination.exists():
            raise RuntimeError("回收站恢复补偿失败，目录状态已变化")
        shutil.move(str(source), str(destination))
        (destination / ".trash.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), "utf-8",
        )

    def purge(self, trash_id: str) -> dict:
        self._validate_id(trash_id)
        source = (self.root / trash_id).resolve()
        root = self.root.resolve()
        if source == root or root not in source.parents or not source.is_dir():
            raise ValueError("回收站项目不存在或路径不安全")
        record_file = source / ".trash.json"
        record = json.loads(record_file.read_text("utf-8")) if record_file.exists() else {"id": trash_id}
        shutil.rmtree(source)
        return record

    @staticmethod
    def _validate_id(value: str):
        if not value or ".." in value or "/" in value or "\\" in value:
            raise ValueError("无效的回收站ID")


class TextNovelImporter:
    HEADING = re.compile(r"(?m)^\s*(第[0-9零〇一二三四五六七八九十百千万两]+[章节回卷][^\r\n]{0,80})\s*$")

    @classmethod
    def split(cls, text: str) -> list[dict]:
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        matches = list(cls.HEADING.finditer(text))
        if not matches:
            return [{"title": "第1章", "content": text}] if text else []
        chapters = []
        preface = text[:matches[0].start()].strip()
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            content = text[match.end():end].strip()
            if index == 0 and preface:
                content = preface + "\n\n" + content
            if content:
                chapters.append({"title": match.group(1).strip(), "content": content})
        return chapters

    @staticmethod
    def decode(data: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")


class ProjectZipRestorer:
    MAX_FILES = 10000
    MAX_UNCOMPRESSED = 1_000_000_000

    @classmethod
    def extract(cls, archive_path: Path, destination: Path):
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if len(members) > cls.MAX_FILES:
                raise ValueError("ZIP文件数量过多")
            if sum(item.file_size for item in members) > cls.MAX_UNCOMPRESSED:
                raise ValueError("ZIP解压后超过1GB限制")
            root = destination.resolve()
            for item in members:
                target = (destination / item.filename).resolve()
                if target != root and root not in target.parents:
                    raise ValueError("ZIP包含不安全路径")
            archive.extractall(destination)
        if not (destination / "state.json").exists():
            raise ValueError("ZIP不是有效的小说项目：缺少state.json")
