"""质量债务跟踪器：记录章节质量问题并在后续自动修复。"""
from pathlib import Path
from datetime import datetime
from filelock import FileLock

from storage_utils import StorageManager


class QualityTracker:
    """跨章节质量问题跟踪。"""

    def __init__(self, novel_path: Path, logger, storage: StorageManager = None):
        self.path = novel_path / "quality"
        self.logger = logger
        self.storage = storage or StorageManager(logger)
        self.path.mkdir(parents=True, exist_ok=True)

    def add_debt(self, chapter: int, issue_type: str, severity: str,
                 description: str, suggestion: str = "") -> dict:
        """记录一条质量债务。"""
        debt_file = self.path / "debt.json"
        with FileLock(str(debt_file) + ".transaction.lock", timeout=30):
            debts = self._load(debt_file)
            existing = next((
                item for item in debts["items"]
                if not item.get("resolved", False)
                and self._chapter(item.get("chapter")) == self._chapter(chapter)
                and item.get("type") == issue_type
                and item.get("description") == description
            ), None)
            if existing:
                return existing
            entry = {
                "id": f"debt_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
                "chapter": chapter,
                "type": issue_type,
                "severity": severity,
                "description": description,
                "suggestion": suggestion,
                "resolved": False,
                "created_at": datetime.now().isoformat(),
            }
            debts["items"].append(entry)
            self.storage.atomic_write_json(debt_file, debts)
        self.logger.info("质量债务记录: 第%d章 [%s] %s", chapter, severity, description[:40])
        return entry

    def resolve_debt(self, debt_id: str, resolution: str = ""):
        """标记债务为已解决。"""
        debt_file = self.path / "debt.json"
        lock = FileLock(str(debt_file) + ".transaction.lock", timeout=30)
        with lock:
            debts = self._load(debt_file)
            for item in debts["items"]:
                if item.get("id") == debt_id:
                    item["resolved"] = True
                    item["resolved_at"] = datetime.now().isoformat()
                    item["resolution"] = resolution
                    break
            self.storage.atomic_write_json(debt_file, debts)

    def get_pending_debts(self, chapter: int = None) -> list[dict]:
        """获取未解决的质量债务。"""
        debt_file = self.path / "debt.json"
        debts = self._load(debt_file)
        pending = [d for d in debts["items"] if not d.get("resolved", False)]
        if chapter:
            pending = [d for d in pending if self._chapter(d.get("chapter")) <= chapter]
        return pending

    def get_report(self) -> dict:
        """获取完整质量报告。"""
        debt_file = self.path / "debt.json"
        debts = self._load(debt_file)
        items = debts["items"]
        total = len(items)
        resolved = sum(1 for d in items if d.get("resolved", False))
        pending = total - resolved
        by_severity = {}
        for d in items:
            s = str(d.get("severity") or "未知")
            by_severity[s] = by_severity.get(s, 0) + 1
        by_chapter = {}
        for d in items:
            c = str(self._chapter(d.get("chapter")))
            by_chapter[c] = by_chapter.get(c, 0) + 1
        return {
            "total_debts": total,
            "resolved": resolved,
            "pending": pending,
            "resolution_rate": round(resolved / total * 100, 1) if total else 100,
            "by_severity": by_severity,
            "by_chapter": by_chapter,
        }

    def _load(self, debt_file: Path) -> dict:
        data = self.storage.safe_read_json(debt_file, {"items": []})
        items = data.get("items", []) if isinstance(data, dict) else []
        return {"items": [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []}

    @staticmethod
    def _chapter(value) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0
