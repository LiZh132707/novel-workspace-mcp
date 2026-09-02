import logging

from core.chapter_generation_service import ChapterGenerationService
from core.chapter_manager import ChapterManager
from core.chapter_turn_engine import ChapterTurnEngine
from core.generation_provenance_manager import GenerationProvenanceManager
from core.novel_manager import NovelManager
from storage_utils import StorageManager


LOGGER = logging.getLogger("generation-provenance-test")


def test_generation_provenance_exposes_reproducibility_without_prompt_body(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("凭证书", tmp_path, LOGGER, storage)
    manager = ChapterManager(novel, LOGGER)
    engine = ChapterTurnEngine(novel, LOGGER, manager, storage)
    content = "林舟进入车站，确认封锁仍在继续。" * 40
    metadata = ChapterGenerationService.turn_metadata(
        "task-1", {"tokens_per_second": 51.8, "completion_tokens": 1200, "seed": 42},
        "", "fingerprint-1", False,
        {"task_type": "prose", "prompt_hash": "prompt-hash", "path": "latest/prose.json", "prompt": "不得暴露"},
        generation_profile={"model_name": "local-model", "context_window": 131072, "seed": 42},
    )
    turn = engine.save_draft(1, content, 500, "batch", metadata, False)
    engine.commit(turn["id"], allow_quality_failure=True, allow_fact_conflicts=True)
    item = GenerationProvenanceManager(novel, LOGGER, storage).get(1)
    assert item["canonical_committed"] is True
    assert item["metrics"]["tokens_per_second"] == 51.8
    assert item["generation_profile"]["context_window"] == 131072
    assert item["pipeline"]["checkpoint"] == "draft_ready"
    assert item["planning"]["fingerprint"] == "fingerprint-1"
    assert item["prompt"]["prompt_hash"] == "prompt-hash"
    assert "prompt" not in item["prompt"]


def test_generation_provenance_handles_manual_legacy_chapter(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("手工书", tmp_path, LOGGER, storage)
    ChapterManager(novel, LOGGER).save_chapter(1, "作者手工写入的章节。" * 30)
    item = GenerationProvenanceManager(novel, LOGGER, storage).get(1)
    assert item["canonical_committed"] is True
    assert item["trace_available"] is False
    assert item["source"] == "unknown"


def test_old_turn_cannot_claim_new_manual_content(tmp_path):
    storage = StorageManager(LOGGER)
    novel = NovelManager("覆盖书", tmp_path, LOGGER, storage)
    manager = ChapterManager(novel, LOGGER)
    engine = ChapterTurnEngine(novel, LOGGER, manager, storage)
    old = "模型生成的旧正文。" * 40
    turn = engine.save_draft(1, old, 500, "batch", {}, False)
    engine.commit(turn["id"], allow_quality_failure=True, allow_fact_conflicts=True)
    manager.save_chapter(1, "作者完全重写后的新正文。" * 40)
    item = GenerationProvenanceManager(novel, LOGGER, storage).get(1)
    assert item["canonical_committed"] is True
    assert item["trace_available"] is False
    assert item["turn_id"] == ""
