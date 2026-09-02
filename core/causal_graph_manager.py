"""规划目标与正史因果证据的确定性图谱。"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from storage_utils import StorageManager


class CausalGraphManager:
    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.root = novel_path
        self.storage = storage or StorageManager(logger)

    def build(self, current_chapter: int | None = None) -> dict:
        summaries = self._summaries()
        current = max((item["chapter"] for item in summaries), default=0) if current_chapter is None else max(0, int(current_chapter))
        nodes: dict[str, dict] = {}
        edges = []
        actual_texts = []
        for item in summaries:
            actual_texts.extend(self._evidence_records(item["chapter"], item["summary"], f"summary:{item['chapter']}"))
        actual_texts.extend(self._chapter_records())
        logic = self.storage.safe_read_json(self.root / "tracking" / "story_logic.json", {})
        causal_links = logic.get("causal_links", []) if isinstance(logic, dict) and isinstance(logic.get("causal_links"), list) else []
        adjacency: dict[str, set[str]] = {}
        for link in causal_links[-200:]:
            if not isinstance(link, dict):
                continue
            cause, effect = str(link.get("cause", "")).strip(), str(link.get("effect", "")).strip()
            if not cause or not effect:
                continue
            chapter = self._int(link.get("chapter"))
            cause_id, effect_id = self._id("event", cause), self._id("event", effect)
            nodes.setdefault(cause_id, {"id": cause_id, "type": "canonical_event", "label": cause, "chapter": chapter})
            nodes.setdefault(effect_id, {"id": effect_id, "type": "canonical_event", "label": effect, "chapter": chapter})
            edges.append({
                "id": self._id("edge", f"{cause_id}|{effect_id}|{chapter}"),
                "source": cause_id, "target": effect_id, "type": "causes",
                "chapter": chapter, "actor": str(link.get("actor", "")),
            })
            adjacency.setdefault(cause_id, set()).add(effect_id)
            actual_texts.extend(self._evidence_records(chapter, cause, cause_id))
            actual_texts.extend(self._evidence_records(chapter, effect, effect_id))
        planned = self._planned_outcomes(current, actual_texts, nodes, edges)
        cycles = self._cycles(adjacency)
        gaps = [item for item in planned if item["status"] == "due_missing"]
        return {
            "current_chapter": current, "nodes": list(nodes.values()), "edges": edges,
            "planned_outcomes": planned, "gaps": gaps, "cycles": cycles,
            "stats": {
                "nodes": len(nodes), "causal_edges": len([item for item in edges if item["type"] == "causes"]),
                "planned": len(planned), "evidenced": len([item for item in planned if item["status"] in {"evidenced", "evidenced_late"}]),
                "evidenced_late": len([item for item in planned if item["status"] == "evidenced_late"]),
                "due_missing": len(gaps), "cycles": len(cycles),
            },
        }

    def _planned_outcomes(self, current: int, actual_texts: list[tuple[int, str, str, set[str], bool]], nodes: dict, edges: list) -> list[dict]:
        volumes = self.storage.safe_read_json(self.root / "outline" / "volumes.json", [])
        result = []
        for volume_index, volume in enumerate(volumes if isinstance(volumes, list) else []):
            if not isinstance(volume, dict):
                continue
            deadline = self._int(volume.get("end_chapter"))
            self._append_planned(
                result, nodes, edges, "volume_goal", str(volume.get("goal", "")).strip(),
                str(volume.get("title", "未命名卷")), deadline, current, actual_texts, f"v{volume_index}",
            )
            sections = volume.get("sections", []) if isinstance(volume.get("sections"), list) else []
            for section_index, section in enumerate(sections):
                if not isinstance(section, dict):
                    continue
                section_deadline = self._int(section.get("end_chapter"), deadline)
                scope = str(section.get("title", "未命名节"))
                required = section.get("required_outcomes", [])
                if isinstance(required, list):
                    for outcome_index, value in enumerate(required):
                        self._append_planned(
                            result, nodes, edges, "section_outcome", str(value).strip(), scope,
                            section_deadline, current, actual_texts, f"v{volume_index}s{section_index}o{outcome_index}",
                        )
                self._append_planned(
                    result, nodes, edges, "section_result", str(section.get("outcome", "")).strip(), scope,
                    section_deadline, current, actual_texts, f"v{volume_index}s{section_index}r",
                )
        return result

    def _append_planned(
        self, result: list, nodes: dict, edges: list, kind: str, text: str, scope: str,
        deadline: int, current: int, actual_texts: list[tuple[int, str, str, set[str], bool]], key: str,
    ):
        if not text:
            return
        if deadline < 1:
            return
        target_terms = self._terms(text)
        target_negative = self._has_negative(text)
        best = {"overlap": 0.0, "chapter": 0, "node_id": "", "text": ""}
        best_on_time = {"overlap": 0.0, "chapter": 0, "node_id": "", "text": ""}
        for chapter, excerpt, node_id, actual_terms, actual_negative in actual_texts:
            if actual_negative and not target_negative:
                continue
            overlap = len(target_terms & actual_terms) / max(1, len(target_terms)) if target_terms else 0.0
            if overlap > best["overlap"]:
                best = {"overlap": overlap, "chapter": chapter, "node_id": node_id, "text": excerpt}
            if chapter <= deadline and overlap > best_on_time["overlap"]:
                best_on_time = {"overlap": overlap, "chapter": chapter, "node_id": node_id, "text": excerpt}
        if best_on_time["overlap"] >= 0.30:
            status = "evidenced"
            best = best_on_time
        elif best["overlap"] >= 0.30 and best["chapter"] > deadline:
            status = "evidenced_late"
        else:
            status = "future" if deadline > current else "due_missing"
        plan_id = self._id("plan", f"{key}|{deadline}|{text}")
        nodes[plan_id] = {"id": plan_id, "type": "planned_outcome", "label": text, "deadline": deadline, "status": status, "scope": scope}
        if status in {"evidenced", "evidenced_late"}:
            evidence_id = best["node_id"]
            if evidence_id.startswith(("summary:", "chapter:")):
                node_type = "chapter_summary" if evidence_id.startswith("summary:") else "canonical_chapter_evidence"
                nodes.setdefault(evidence_id, {"id": evidence_id, "type": node_type, "label": best["text"][:500], "chapter": best["chapter"]})
            edges.append({
                "id": self._id("evidence", f"{evidence_id}|{plan_id}"), "source": evidence_id,
                "target": plan_id, "type": "supports", "chapter": best["chapter"],
            })
        result.append({
            "id": plan_id, "kind": kind, "scope": scope, "text": text, "deadline": deadline,
            "status": status, "evidence_overlap": round(best["overlap"], 3),
            "evidence_chapter": best["chapter"] if status in {"evidenced", "evidenced_late"} else 0,
        })

    def _summaries(self) -> list[dict]:
        result = []
        files = sorted(
            (path for path in (self.root / "summaries").glob("*.json") if path.stem.isdigit()),
            key=lambda item: int(item.stem),
        )
        for path in files:
            data = self.storage.safe_read_json(path, {})
            if isinstance(data, dict) and str(data.get("summary", "")).strip():
                result.append({"chapter": self._int(data.get("chapter"), int(path.stem)), "summary": str(data["summary"])})
        return result

    def _chapter_records(self) -> list[tuple[int, str, str, set[str], bool]]:
        result = []
        files = sorted(
            (path for path in (self.root / "chapters").glob("*.txt") if path.stem.isdigit()),
            key=lambda item: int(item.stem),
        )
        for path in files:
            text = path.read_text("utf-8", errors="replace").strip()
            if text:
                chapter = int(path.stem)
                result.extend(self._evidence_records(chapter, text, f"chapter:{chapter}"))
        return result

    @staticmethod
    def _cycles(adjacency: dict[str, set[str]]) -> list[list[str]]:
        cycles, visiting, visited = [], [], set()

        def walk(node: str):
            if node in visiting:
                cycle = visiting[visiting.index(node):] + [node]
                if cycle not in cycles:
                    cycles.append(cycle)
                return
            if node in visited:
                return
            visiting.append(node)
            for target in adjacency.get(node, set()):
                walk(target)
            visiting.pop()
            visited.add(node)

        for node in adjacency:
            walk(node)
        return cycles[:20]

    @staticmethod
    def _terms(text: str) -> set[str]:
        cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text or "")
        return {cleaned[index:index + 2] for index in range(max(0, len(cleaned) - 1))}

    @classmethod
    def _evidence_records(cls, chapter: int, text: str, node_id: str) -> list[tuple[int, str, str, set[str], bool]]:
        result = []
        for segment in re.split(r"[。！？；\n]+", text):
            segment = segment.strip()
            if not segment:
                continue
            result.append((chapter, segment, node_id, cls._terms(segment), cls._has_negative(segment)))
        return result

    @staticmethod
    def _has_negative(text: str) -> bool:
        return any(marker in text for marker in ("尚未", "仍未", "没有", "并未", "未能", "无法", "失败", "尚不"))

    @staticmethod
    def _id(kind: str, value: str) -> str:
        return f"{kind}:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"

    @staticmethod
    def _int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
