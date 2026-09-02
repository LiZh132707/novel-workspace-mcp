"""章节生成凭证：模型参数、规划版本、Prompt引用与正史完整性。"""
from __future__ import annotations

from core.ai_contracts import chapter_source_hash
from core.chapter_commit_manager import ChapterCommitManager
from storage_utils import StorageManager


class GenerationProvenanceManager:
    def __init__(self, novel_manager, logger=None, storage: StorageManager | None = None):
        self.nm = novel_manager
        self.root = novel_manager.path
        self.storage = storage or novel_manager.storage or StorageManager(logger)
        self.commits = ChapterCommitManager(self.root, logger, self.storage)

    def list(self, limit: int = 100) -> list[dict]:
        chapters = sorted(
            (int(path.stem) for path in (self.root / "chapters").glob("*.txt") if path.stem.isdigit()),
            reverse=True,
        )[:max(1, min(500, int(limit)))]
        return [self.get(chapter) for chapter in chapters]

    def get(self, chapter: int) -> dict:
        chapter = int(chapter)
        if chapter < 1:
            raise ValueError("章节号必须为正整数")
        path = self.root / "chapters" / f"{chapter:06d}.txt"
        content = path.read_text("utf-8", errors="replace") if path.exists() else ""
        turns = self._turns(chapter)
        content_hash = chapter_source_hash(content) if content else ""
        canonical_turn = next((
            item for item in reversed(turns)
            if item.get("status") == "committed" and item.get("content_hash") == content_hash
        ), None)
        if canonical_turn is None:
            canonical_turn = next((
                item for item in reversed(turns)
                if item.get("status") == "superseded" and item.get("content_hash") == content_hash
            ), None)
        metadata = canonical_turn.get("metadata", {}) if isinstance(canonical_turn, dict) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        metrics = metadata.get("metrics", {}) if isinstance(metadata.get("metrics"), dict) else {}
        profile = metadata.get("generation_profile", {}) if isinstance(metadata.get("generation_profile"), dict) else {}
        pipeline = metadata.get("pipeline", {}) if isinstance(metadata.get("pipeline"), dict) else {}
        prompt = metadata.get("prompt", {}) if isinstance(metadata.get("prompt"), dict) else {}
        commit = self.commits.get(chapter)
        return {
            "chapter": chapter, "words": self._words(content),
            "content_hash": content_hash, "canonical_committed": bool(content) and self.commits.is_committed(chapter, content),
            "commit": commit, "turn_id": canonical_turn.get("id", "") if canonical_turn else "",
            "turn_status": canonical_turn.get("status", "untracked") if canonical_turn else "untracked",
            "source": canonical_turn.get("source", "unknown") if canonical_turn else "unknown",
            "created_at": canonical_turn.get("created_at", "") if canonical_turn else "",
            "committed_at": canonical_turn.get("committed_at", "") if canonical_turn else commit.get("committed_at", ""),
            "metrics": metrics, "generation_profile": profile, "pipeline": pipeline,
            "prompt": {
                key: prompt.get(key) for key in ("task_type", "prompt_hash", "created_at", "path")
                if prompt.get(key) not in (None, "")
            },
            "planning": {
                "epoch": metadata.get("planning_epoch", ""),
                "fingerprint": metadata.get("planning_fingerprint", ""),
                "stale": bool(metadata.get("planning_stale", False)),
            },
            "trace_available": canonical_turn is not None,
        }

    def _turns(self, chapter: int) -> list[dict]:
        data = self.storage.safe_read_json(self.root / "turns" / "index.json", {"items": []})
        items = data.get("items", []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            return []
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                number = int(item.get("chapter", 0))
            except (TypeError, ValueError):
                continue
            if number == chapter:
                result.append(item)
        return result

    @staticmethod
    def _words(content: str) -> int:
        return len("".join((content or "").split()))
