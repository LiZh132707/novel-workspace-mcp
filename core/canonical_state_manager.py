"""带证据、冲突裁决和版本记录的小说权威状态。"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from filelock import FileLock

from core.state_card_manager import StateCardManager
from storage_utils import StorageManager


class CanonicalStateManager:
    HIGH_RISK_WORDS = {"死亡", "复活", "失踪", "背叛", "决裂", "摧毁", "永久", "废除", "失忆"}

    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.root = novel_path
        self.path = novel_path / "tracking" / "state_proposals.json"
        self.version_path = novel_path / "tracking" / "canonical_versions.json"
        self.storage = storage or StorageManager(logger)
        self.cards = StateCardManager(novel_path, logger, self.storage)

    def list(self, status: str | None = None) -> list[dict]:
        items = self._load_proposals()["items"]
        return [item for item in items if not status or item.get("status") == status]

    def propose_from_summary(self, chapter: int, summary: dict) -> dict:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._propose_from_summary(chapter, summary)

    def _propose_from_summary(self, chapter: int, summary: dict) -> dict:
        candidates = []
        for item in summary.get("characters_changed", []):
            if isinstance(item, dict) and item.get("name"):
                candidates.append(("character", str(item["name"]), str(item.get("field") or "status"), item.get("new_value") or item.get("change", ""), item.get("evidence", ""), bool(item.get("evidence_verified", False))))
        for kind, key in (("location", "locations"), ("item", "items"), ("faction", "factions")):
            for item in summary.get(key, []):
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                for field, value in item.items():
                    if field not in {"name", "evidence", "evidence_verified"} and str(value).strip():
                        candidates.append((kind, str(item["name"]), str(field), value, item.get("evidence", ""), bool(item.get("evidence_verified", False))))
        for item in summary.get("relationship_changes", []):
            if isinstance(item, dict) and item.get("from") and item.get("to"):
                name = f"{item['from']}→{item['to']}"
                verified = bool(item.get("evidence_verified", False))
                candidates.append(("relationship", name, "type", item.get("type", ""), item.get("evidence", ""), verified))
                if str(item.get("strength", "")).strip():
                    candidates.append(("relationship", name, "strength", item.get("strength"), item.get("evidence", ""), verified))

        data = self._load_proposals()
        cards = self.cards.get()
        created = []
        for kind, name, field, value, evidence, evidence_verified in candidates:
            value = str(value).strip()[:1000]
            if not value:
                continue
            duplicate = next((item for item in data["items"] if item.get("chapter") == chapter and item.get("kind") == kind and item.get("name") == name and item.get("field") == field and item.get("value") == value), None)
            if duplicate:
                continue
            previous = str(cards.get(kind, {}).get(name, {}).get("fields", {}).get(field, ""))
            risk = self._risk(previous, value, evidence if evidence_verified else "")
            proposal = {
                "id": uuid.uuid4().hex, "chapter": int(chapter), "kind": kind, "name": name,
                "field": field, "previous": previous, "value": value, "evidence": str(evidence)[:500],
                "risk": risk, "status": "validated" if risk == "low" else "pending",
                "created_at": datetime.now().isoformat(),
            }
            data["items"].append(proposal)
            created.append(proposal)
        self.storage.atomic_write_json(self.path, data)
        committed = [self._commit(item) for item in created if item["status"] == "validated"]
        if committed:
            self._persist_statuses(committed)
            self.create_version(chapter, "章后低风险状态自动提交")
        return {
            "proposed": len(created), "committed": len(committed),
            "pending": len([item for item in created if item["status"] == "pending"]),
            "items": created,
        }

    def decide(self, proposal_id: str, accept: bool) -> dict:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._decide(proposal_id, accept)

    def _decide(self, proposal_id: str, accept: bool) -> dict:
        data = self._load_proposals()
        item = next((entry for entry in data["items"] if entry.get("id") == proposal_id), None)
        if not item:
            raise ValueError("状态变更提案不存在")
        if item.get("status") not in {"pending", "validated"}:
            return item
        if accept:
            self._commit(item)
            self.create_version(int(item.get("chapter", 0)), "人工确认状态提案")
        else:
            item["status"] = "rejected"
            item["decided_at"] = datetime.now().isoformat()
        self.storage.atomic_write_json(self.path, data)
        return item

    def create_version(self, chapter: int, reason: str) -> dict:
        with FileLock(str(self.version_path) + ".transaction.lock", timeout=30):
            snapshot = self.cards.get()
            raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            data = self._load_versions()
            previous = data["versions"][-1] if data["versions"] else None
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            if previous and previous.get("checksum") == digest:
                return previous
            record = {
                "version": len(data["versions"]) + 1, "chapter": int(chapter), "reason": reason,
                "checksum": digest, "snapshot": snapshot, "created_at": datetime.now().isoformat(),
            }
            data["versions"].append(record)
            data["versions"] = data["versions"][-200:]
            self.storage.atomic_write_json(self.version_path, data)
            return record

    def _commit(self, item: dict) -> dict:
        self.cards.upsert(item["kind"], item["name"], int(item["chapter"]), {item["field"]: item["value"]}, item.get("evidence", ""), "canonical")
        item["status"] = "committed"
        item["decided_at"] = datetime.now().isoformat()
        return item

    def _persist_statuses(self, committed: list[dict]):
        ids = {item["id"] for item in committed}
        data = self._load_proposals()
        by_id = {item["id"]: item for item in committed}
        data["items"] = [by_id[item["id"]] if item.get("id") in ids else item for item in data["items"]]
        self.storage.atomic_write_json(self.path, data)

    def _load_proposals(self) -> dict:
        data = self.storage.safe_read_json(self.path, {"items": []})
        items = data.get("items", []) if isinstance(data, dict) else []
        return {"items": [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []}

    def _load_versions(self) -> dict:
        data = self.storage.safe_read_json(self.version_path, {"versions": []})
        versions = data.get("versions", []) if isinstance(data, dict) else []
        return {"versions": [item for item in versions if isinstance(item, dict)] if isinstance(versions, list) else []}

    def _risk(self, previous: str, value: str, evidence: str) -> str:
        combined = previous + value
        if any(word in combined for word in self.HIGH_RISK_WORDS):
            return "high"
        if previous and previous != value:
            return "medium"
        if not str(evidence).strip():
            return "medium"
        return "low"
