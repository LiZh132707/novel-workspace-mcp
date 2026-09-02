import logging
import tempfile
from pathlib import Path

import pytest

from core.planning_review_manager import PlanningReviewManager
from storage_utils import StorageManager


def test_review_detects_section_end_and_modes():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        storage = StorageManager(logging.getLogger("test"))
        storage.atomic_write_json(path / "outline" / "chapter_briefs.json", {"2": {"chapter_mode": "character", "synopsis": "主角发现密室"}})
        storage.atomic_write_json(path / "outline" / "volumes.json", [{"title": "第一卷", "sections": [{"title": "密室", "end_chapter": 2, "required_outcomes": ["发现密室"]}]}])
        manager = PlanningReviewManager(path, logging.getLogger("test"), storage)
        result = manager.review_chapter(2, {"summary": "主角最终发现密室并进入其中"})
        assert result["section_review"]["status"] == "likely_complete"
        assert manager.report()["chapter_modes"] == {"character": 1}


def test_section_review_uses_whole_section_and_volume_review_creates_repair_tasks():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        storage = StorageManager(logging.getLogger("test-volume-review"))
        storage.atomic_write_json(path / "outline" / "chapter_briefs.json", {
            "1": {"chapter_mode": "main_progress", "synopsis": "发现密室"},
            "2": {"chapter_mode": "aftermath", "synopsis": "逃离追兵"},
        })
        storage.atomic_write_json(path / "outline" / "volumes.json", [{
            "title": "第一卷", "start_chapter": 1, "end_chapter": 2,
            "goal": "主角发现密室并取得档案", "character_changes": ["主角学会信任同伴"],
            "foreshadowing": ["建立钥匙来源线索"],
            "sections": [{
                "title": "密室", "start_chapter": 1, "end_chapter": 2,
                "required_outcomes": ["发现密室"],
            }],
        }])
        storage.atomic_write_json(path / "summaries" / "000001.json", {
            "chapter": 1, "summary": "主角发现密室并取得档案",
        })
        storage.atomic_write_json(path / "summaries" / "000002.json", {
            "chapter": 2, "summary": "主角独自逃离追兵",
        })
        storage.atomic_write_json(path / "foreshadowing.json", {"items": [{
            "id": "key", "text": "钥匙来源", "introduced_chapter": 1,
            "target_chapter": 2, "status": "open",
        }]})
        manager = PlanningReviewManager(path, logging.getLogger("test-volume-review"), storage)
        result = manager.review_chapter(2, {"summary": "主角独自逃离追兵"})
        assert result["section_review"]["status"] == "likely_complete"
        volume = result["volume_review"]
        assert volume["goal_met"] is True
        assert volume["status"] == "needs_review"
        assert any(item["kind"] == "character_change" for item in volume["repair_tasks"])
        assert any(item["kind"] == "overdue_foreshadow" for item in volume["repair_tasks"])
        assert all(item.get("id") and item.get("status") == "pending" for item in volume["repair_tasks"])
        assert manager.report()["volume_reviews"][0]["volume"] == "第一卷"
        task_id = volume["repair_tasks"][0]["id"]
        with pytest.raises(ValueError, match="正文证据"):
            manager.decide_volume_task(task_id, "resolved", "声称已经处理")
        decided = manager.decide_volume_task(task_id, "resolved", "作者确认不再需要该目标", waive=True)
        assert decided["task"]["status"] == "resolved"
        assert decided["task"]["resolution_mode"] == "waived"
        assert manager.pending_volume_repairs(3)
        for task in list(manager.pending_volume_repairs(3)):
            manager.decide_volume_task(task["id"], "deferred", "并入下一卷处理")
        assert manager.pending_volume_repairs(3)
        assert manager.report()["volume_reviews"][0]["status"] == "accepted_after_review"
        for task in list(manager.pending_volume_repairs(3)):
            manager.decide_volume_task(task["id"], "resolved", "作者确认取消", waive=True)
        assert manager.pending_volume_repairs(3) == []


def test_later_chapter_summary_automatically_resolves_matching_volume_repair():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        storage = StorageManager(logging.getLogger("test-auto-volume-evidence"))
        storage.atomic_write_json(path / "reviews" / "planning_reviews.json", {
            "chapters": [], "section_reviews": [], "volume_reviews": [{
                "volume": "第一卷", "end_chapter": 2, "status": "needs_review",
                "repair_tasks": [{
                    "id": "trust", "kind": "character_change", "status": "pending",
                    "description": "下一卷开始前补齐：主角学会信任同伴",
                }],
            }],
        })
        manager = PlanningReviewManager(path, logging.getLogger("test-auto-volume-evidence"), storage)
        result = manager.review_chapter(3, {"summary": "危机中主角终于学会信任同伴，并把后背交给队友。"})
        assert result["auto_resolved_repairs"][0]["id"] == "trust"
        task = manager.report()["volume_reviews"][0]["repair_tasks"][0]
        assert task["status"] == "resolved"
        assert task["resolution_mode"] == "automatic_evidence"
        assert task["evidence_chapter"] == 3


def test_negative_summary_does_not_falsely_resolve_volume_repair():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        storage = StorageManager(logging.getLogger("test-negative-evidence"))
        storage.atomic_write_json(path / "reviews" / "planning_reviews.json", {
            "chapters": [], "section_reviews": [], "volume_reviews": [{
                "volume": "第一卷", "end_chapter": 2, "status": "needs_review",
                "repair_tasks": [{
                    "id": "trust", "status": "pending",
                    "description": "下一卷开始前补齐：主角学会信任同伴",
                }],
            }],
        })
        manager = PlanningReviewManager(path, logging.getLogger("test-negative-evidence"), storage)
        result = manager.review_chapter(3, {"summary": "主角仍未学会信任同伴，因此拒绝合作。"})
        assert result["auto_resolved_repairs"] == []
        assert manager.report()["volume_reviews"][0]["repair_tasks"][0]["status"] == "pending"


def test_chapter_ending_can_resolve_repair_when_summary_omits_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        storage = StorageManager(logging.getLogger("test-ending-evidence"))
        storage.atomic_write_json(path / "reviews" / "planning_reviews.json", {
            "chapters": [], "section_reviews": [], "volume_reviews": [{
                "volume": "第一卷", "end_chapter": 2, "status": "needs_review",
                "repair_tasks": [{
                    "id": "evidence", "status": "pending",
                    "description": "下一卷开始前补齐：主角取得核心证物",
                }],
            }],
        })
        storage.atomic_write_text(
            path / "chapters" / "000003.txt",
            "本章前半段持续追逐。" * 50 + "最终主角取得核心证物，并交给可信同伴保管。",
        )
        manager = PlanningReviewManager(path, logging.getLogger("test-ending-evidence"), storage)
        result = manager.review_chapter(3, {"summary": "主角继续追逐敌人。"})
        task = result["auto_resolved_repairs"][0]
        assert task["status"] == "resolved"
        assert "取得核心证物" in task["evidence_quote"]


def test_section_and_volume_reviews_use_canonical_chapter_endings_not_only_summaries():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        storage = StorageManager(logging.getLogger("test-canonical-review-evidence"))
        storage.atomic_write_json(path / "outline" / "volumes.json", [{
            "title": "第一卷", "start_chapter": 1, "end_chapter": 2,
            "goal": "主角取得核心证物", "sections": [{
                "title": "证物争夺", "start_chapter": 1, "end_chapter": 2,
                "required_outcomes": ["主角取得核心证物"],
            }],
        }])
        storage.atomic_write_json(path / "summaries" / "000001.json", {"chapter": 1, "summary": "主角进入仓库。"})
        storage.atomic_write_json(path / "summaries" / "000002.json", {"chapter": 2, "summary": "众人撤离。"})
        storage.atomic_write_text(
            path / "chapters" / "000001.txt",
            "主角进入仓库。" * 40 + "最终主角取得核心证物，并完成封存。",
        )
        storage.atomic_write_text(path / "chapters" / "000002.txt", "众人安全撤离现场。")
        manager = PlanningReviewManager(path, logging.getLogger("test-canonical-review-evidence"), storage)
        result = manager.review_chapter(2, {"summary": "众人撤离。"})
        assert result["section_review"]["status"] == "likely_complete"
        assert result["volume_review"]["goal_met"] is True


def test_negative_canonical_text_does_not_pass_section_or_volume_acceptance():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        storage = StorageManager(logging.getLogger("test-negative-canonical-review"))
        storage.atomic_write_json(path / "outline" / "volumes.json", [{
            "title": "第一卷", "start_chapter": 1, "end_chapter": 1,
            "goal": "主角取得核心证物", "sections": [{
                "title": "证物争夺", "start_chapter": 1, "end_chapter": 1,
                "required_outcomes": ["主角取得核心证物"],
            }],
        }])
        storage.atomic_write_json(path / "summaries" / "000001.json", {"chapter": 1, "summary": "主角仍未取得核心证物。"})
        storage.atomic_write_text(path / "chapters" / "000001.txt", "战斗结束，但主角仍未取得核心证物。")
        result = PlanningReviewManager(path, logging.getLogger("test-negative-canonical-review"), storage).review_chapter(
            1, {"summary": "主角仍未取得核心证物。"},
        )
        assert result["section_review"]["status"] == "needs_review"
        assert result["volume_review"]["goal_met"] is False
