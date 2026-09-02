import logging
import tempfile
from pathlib import Path

from core.story_logic_manager import StoryLogicManager


def test_story_logic_tracks_promises_causality_and_knowledge():
    with tempfile.TemporaryDirectory() as tmp:
        manager = StoryLogicManager(Path(tmp), logging.getLogger("test"))
        result = manager.ingest(2, {
            "narrative_promises": [{"text": "找出失踪者", "target_chapter": 20}],
            "causal_links": [{"cause": "拿到钥匙", "effect": "进入档案室", "actor": "林舟"}],
            "knowledge_changes": [{"name": "林舟", "fact": "馆长撒谎", "source": "档案"}],
        })
        assert result == {"promises": 1, "causal_links": 1, "knowledge_characters": 1}
        assert "馆长撒谎" in manager.context()


def test_story_logic_tracks_beliefs_corrections_and_unknown_boundaries():
    with tempfile.TemporaryDirectory() as tmp:
        manager = StoryLogicManager(Path(tmp), logging.getLogger("test"))
        manager.ingest(1, {"knowledge_changes": [
            {"name": "林舟", "fact": "馆长可信", "status": "believed", "source": "馆长自述", "source_reliability": "low"},
            {"name": "苏遥", "fact": "钥匙藏在钟楼", "status": "unknown"},
        ]})
        manager.ingest(3, {"knowledge_changes": [
            {"name": "林舟", "fact": "馆长可信", "status": "disproved", "source": "伪造档案"},
        ]})
        context = manager.context()
        assert '"disproved"' in context
        assert '"unknown"' in context
        entry = manager.get()["character_knowledge"]["林舟"][0]
        assert entry["status"] == "disproved"
        assert entry["history"][0]["status"] == "believed"


def test_story_logic_ingest_is_serialized(tmp_path, monkeypatch):
    import threading
    import time

    manager = StoryLogicManager(tmp_path, logging.getLogger("story-logic-lock-test"))
    original = manager._ingest
    state = {"active": 0, "max_active": 0}
    guard = threading.Lock()

    def slow_ingest(*args, **kwargs):
        with guard:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        try:
            time.sleep(0.02)
            return original(*args, **kwargs)
        finally:
            with guard:
                state["active"] -= 1

    monkeypatch.setattr(manager, "_ingest", slow_ingest)
    workers = [
        threading.Thread(
            target=manager.ingest,
            args=(chapter, {"narrative_promises": [{"text": f"承诺{chapter}"}]}),
        )
        for chapter in (1, 2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=2)
    assert state["max_active"] == 1
    assert {item["text"] for item in manager.get()["promises"]} == {"承诺1", "承诺2"}
