import logging
import tempfile
from pathlib import Path

from core.causal_graph_manager import CausalGraphManager
from core.long_form_evaluator import LongFormEvaluator
from storage_utils import StorageManager


LOGGER = logging.getLogger("causal-graph-test")


def test_causal_graph_links_planned_outcomes_to_canonical_evidence_and_finds_gaps():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        storage.atomic_write_json(root / "outline" / "volumes.json", [{
            "title": "第一卷", "start_chapter": 1, "end_chapter": 2,
            "goal": "主角发现密室并取得档案",
            "sections": [{
                "title": "追查", "start_chapter": 1, "end_chapter": 2,
                "required_outcomes": ["找到幕后凶手"],
            }],
        }])
        storage.atomic_write_json(root / "summaries" / "000001.json", {
            "chapter": 1, "summary": "主角终于发现密室并取得档案。",
        })
        storage.atomic_write_json(root / "summaries" / "000002.json", {
            "chapter": 2, "summary": "众人撤离现场，但凶手身份仍然未知。",
        })
        storage.atomic_write_json(root / "tracking" / "story_logic.json", {
            "promises": [], "character_knowledge": {},
            "causal_links": [
                {"chapter": 1, "cause": "门锁被破解", "effect": "密室入口打开"},
                {"chapter": 2, "cause": "密室入口打开", "effect": "门锁被破解"},
            ],
        })
        graph = CausalGraphManager(root, LOGGER, storage).build(2)
        volume_goal = next(item for item in graph["planned_outcomes"] if item["kind"] == "volume_goal")
        assert volume_goal["status"] == "evidenced"
        assert volume_goal["evidence_chapter"] == 1
        assert any(item["text"] == "找到幕后凶手" for item in graph["gaps"])
        assert graph["cycles"]
        assert graph["stats"]["causal_edges"] == 2
        storage.atomic_write_json(root / "summaries" / "000003.json", {
            "chapter": 3, "summary": "主角终于找到幕后凶手并取得口供。",
        })
        late = CausalGraphManager(root, LOGGER, storage).build(3)
        outcome = next(item for item in late["planned_outcomes"] if item["text"] == "找到幕后凶手")
        assert outcome["status"] == "evidenced_late"
        assert outcome["evidence_chapter"] == 3
        assert late["stats"]["evidenced_late"] == 1
        evaluation = LongFormEvaluator(root, LOGGER, storage).run()
        assert evaluation["causal_cycles"] == 1
        assert evaluation["causal_late_evidence"] == 1


def test_causal_graph_finds_evidence_in_chapter_ending_when_basic_summary_omits_it():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        storage = StorageManager(LOGGER)
        storage.atomic_write_json(root / "outline" / "volumes.json", [{
            "title": "第一卷", "start_chapter": 1, "end_chapter": 1,
            "goal": "主角取得核心证物", "sections": [],
        }])
        storage.atomic_write_json(root / "summaries" / "000001.json", {
            "chapter": 1, "summary": "主角进入仓库，与守卫周旋。",
        })
        storage.atomic_write_text(
            root / "chapters" / "000001.txt",
            "主角进入仓库，与守卫周旋。" * 40 + "最终主角取得核心证物，并立即封存。",
        )
        graph = CausalGraphManager(root, LOGGER, storage).build(1)
        outcome = graph["planned_outcomes"][0]
        assert outcome["status"] == "evidenced"
        assert outcome["evidence_chapter"] == 1
        evidence_nodes = [item for item in graph["nodes"] if item["type"] == "canonical_chapter_evidence"]
        assert "取得核心证物" in evidence_nodes[0]["label"]
