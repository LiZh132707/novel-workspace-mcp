"""从章节摘要确定性重建全部可派生逻辑账本。"""
from __future__ import annotations

from pathlib import Path

from core.canonical_state_manager import CanonicalStateManager
from core.change_review_manager import ChangeReviewManager
from core.character_manager import CharacterManager
from core.entity_ledger import EntityLedger
from core.fact_manager import FactManager
from core.foreshadow_manager import ForeshadowManager
from core.planning_review_manager import PlanningReviewManager
from core.state_card_manager import StateCardManager
from core.story_logic_manager import StoryLogicManager
from core.story_clock_manager import StoryClockManager
from storage_utils import StorageManager


class DerivedStateRebuilder:
    DEFAULTS = {
        "facts.json": {"facts": [], "conflicts": []},
        "foreshadowing.json": {"items": []},
        "tracking/story_logic.json": {"promises": [], "causal_links": [], "character_knowledge": {}},
        "tracking/story_clock.json": {"travel_rules": [], "events": []},
        "tracking/entities.json": {"locations": {}, "factions": {}, "items": {}, "relationships": []},
        "tracking/state_cards.json": {kind: {} for kind in StateCardManager.TYPES},
        "tracking/state_proposals.json": {"items": []},
        "tracking/canonical_versions.json": {"versions": []},
        "reviews/character_changes.json": {"items": []},
        "reviews/planning_reviews.json": {"chapters": [], "section_reviews": [], "volume_reviews": []},
    }

    def __init__(self, novel_path: Path, logger=None, storage: StorageManager | None = None):
        self.root = novel_path
        self.logger = logger
        self.storage = storage or StorageManager(logger)

    def rebuild(self, current_chapter: int = 0, reason: str = "正文修改后重建") -> dict:
        manual_history = self._manual_state_history()
        prior_decisions = self._canonical_decisions()
        prior_accepted_reviews = self._accepted_character_reviews()
        prior_review_decisions = self._character_review_decisions()
        travel_rules = StoryClockManager(self.root, self.logger, self.storage).get()["travel_rules"]
        for relative, default in self.DEFAULTS.items():
            self.storage.atomic_write_json(self.root / relative, default)
        self.storage.atomic_write_json(
            self.root / "tracking" / "story_clock.json", {"travel_rules": travel_rules, "events": []},
        )
        cards = StateCardManager(self.root, self.logger, self.storage)
        canonical = CanonicalStateManager(self.root, self.logger, self.storage)
        manual_by_chapter = {}
        for kind, name, event in manual_history:
            manual_by_chapter.setdefault(int(event.get("chapter", 0)), []).append((kind, name, event))
        self._apply_manual_events(cards, manual_by_chapter.pop(0, []))
        facts = FactManager(self.root, self.logger, self.storage)
        foreshadows = ForeshadowManager(self.root, self.logger, self.storage)
        logic = StoryLogicManager(self.root, self.logger, self.storage)
        entities = EntityLedger(self.root, self.logger, self.storage)
        reviews = ChangeReviewManager(self.root, self.logger, self.storage)
        planning = PlanningReviewManager(self.root, self.logger, self.storage)
        story_clock = StoryClockManager(self.root, self.logger, self.storage)
        clock_issues = 0
        replayed = 0
        summary_files = (path for path in (self.root / "summaries").glob("*.json") if path.stem.isdigit())
        for path in sorted(summary_files, key=lambda item: int(item.stem)):
            summary = self.storage.safe_read_json(path, {})
            if not isinstance(summary, dict) or not summary:
                continue
            try:
                chapter = int(summary.get("chapter", int(path.stem)))
            except (TypeError, ValueError):
                chapter = int(path.stem)
            if chapter < 1:
                chapter = int(path.stem)
            facts.add_from_summary(chapter, summary.get("facts", []))
            foreshadows.ingest(chapter, summary.get("foreshadowing", []))
            logic.ingest(chapter, summary)
            entities.ingest(chapter, summary)
            proposal_result = canonical.propose_from_summary(chapter, summary)
            for proposal in proposal_result.get("items", []):
                if proposal.get("status") != "pending":
                    continue
                previous_decision = prior_decisions.get(self._proposal_signature(proposal))
                if previous_decision is None:
                    previous_decision = prior_decisions.get(self._proposal_signature(proposal, False))
                if previous_decision == "committed":
                    canonical.decide(proposal["id"], True)
                elif previous_decision == "rejected":
                    canonical.decide(proposal["id"], False)
            self._apply_manual_events(cards, manual_by_chapter.pop(chapter, []))
            reviews.add_from_summary(chapter, summary.get("characters_changed", []))
            reviews.add_new_characters(chapter, summary.get("new_characters", []))
            planning.review_chapter(chapter, summary)
            clock_issues += len(story_clock.record(chapter, summary).get("issues", []))
            replayed += 1
        for chapter in sorted(manual_by_chapter):
            self._apply_manual_events(cards, manual_by_chapter[chapter])
        review_decisions_replayed = self._restore_character_review_decisions(prior_review_decisions)
        character_profiles_reconciled = self._reconcile_character_profiles(prior_accepted_reviews)
        canonical.create_version(current_chapter, reason)
        return {
            "replayed_chapters": replayed,
            "manual_state_events": len(manual_history),
            "canonical_decisions_replayed": len(prior_decisions),
            "character_review_decisions_replayed": review_decisions_replayed,
            "character_profiles_reconciled": character_profiles_reconciled,
            "story_clock_issues": clock_issues,
        }

    @staticmethod
    def _apply_manual_events(cards: StateCardManager, events: list[tuple[str, str, dict]]):
        for kind, name, event in events:
            cards.upsert(kind, name, int(event.get("chapter", 0)), event.get("fields", {}), event.get("evidence", ""), event.get("source", "manual"))

    def _manual_state_history(self) -> list[tuple[str, str, dict]]:
        data = StateCardManager(self.root, self.logger, self.storage).get()
        result = []
        for kind, cards in data.items():
            for name, card in cards.items():
                for event in card.get("history", []):
                    if event.get("source") in {"manual", "bootstrap", "mcp", "import_rebuild"}:
                        result.append((kind, name, event))
        return result

    def _canonical_decisions(self) -> dict[tuple, str]:
        data = self.storage.safe_read_json(self.root / "tracking" / "state_proposals.json", {"items": []})
        result = {}
        for item in data.get("items", []) if isinstance(data, dict) else []:
            if isinstance(item, dict) and item.get("status") in {"committed", "rejected"}:
                result[self._proposal_signature(item)] = item["status"]
                result[self._proposal_signature(item, False)] = item["status"]
        return result

    def _character_review_decisions(self) -> dict[tuple, dict]:
        data = self.storage.safe_read_json(self.root / "reviews" / "character_changes.json", {"items": []})
        result = {}
        for item in data.get("items", []) if isinstance(data, dict) else []:
            if isinstance(item, dict) and item.get("status") in {"accepted", "rejected"}:
                result[self._review_signature(item)] = {
                    "status": item["status"], "decided_at": item.get("decided_at", ""),
                }
                result[self._review_signature(item, False)] = result[self._review_signature(item)]
        return result

    def _accepted_character_reviews(self) -> list[dict]:
        data = self.storage.safe_read_json(self.root / "reviews" / "character_changes.json", {"items": []})
        items = data.get("items", []) if isinstance(data, dict) and isinstance(data.get("items"), list) else []
        return [
            dict(item) for item in items if isinstance(item, dict)
            and item.get("status") == "accepted"
            and item.get("field") in {"current_status", "relationships", "ability_level", "location"}
        ]

    def _restore_character_review_decisions(self, decisions: dict[tuple, dict]) -> int:
        path = self.root / "reviews" / "character_changes.json"
        data = self.storage.safe_read_json(path, {"items": []})
        restored = 0
        for item in data.get("items", []):
            decision = decisions.get(self._review_signature(item))
            if decision is None:
                decision = decisions.get(self._review_signature(item, False))
            if not decision:
                continue
            item.update(decision)
            restored += 1
        if restored:
            self.storage.atomic_write_json(path, data)
        return restored

    def _reconcile_character_profiles(self, prior: list[dict]) -> int:
        if not prior:
            return 0
        current = [item for item in ChangeReviewManager(self.root, self.logger, self.storage).list(None) if item.get("status") == "accepted"]
        characters = CharacterManager(self.root, self.logger)
        reconciled = 0
        groups = {}
        for item in prior:
            groups.setdefault((str(item.get("name", "")), str(item.get("field", ""))), []).append(item)
        for (name, field), old_items in groups.items():
            detail = characters.get_character(name)
            if not detail:
                continue
            old_items.sort(key=lambda item: int(item.get("chapter", 0)))
            new_items = sorted(
                [item for item in current if item.get("name") == name and item.get("field") == field],
                key=lambda item: int(item.get("chapter", 0)),
            )
            prior_latest = str(old_items[-1].get("new_value", ""))
            baseline = next((str(item.get("old_value", "")) for item in old_items if str(item.get("old_value", ""))), "")
            final_value = str(new_items[-1].get("new_value", "")) if new_items else baseline
            if not final_value:
                continue
            if field in {"current_status", "relationships"}:
                profile_field = field
                if str(detail.get(profile_field, "")) != prior_latest:
                    continue
                characters.replace_review_derived_state(name, **{profile_field: final_value})
            elif field == "ability_level":
                if str(detail.get("ability_level", "")) != prior_latest:
                    continue
                old_pairs = {(int(item.get("chapter", 0)), str(item.get("new_value", ""))) for item in old_items}
                history = [
                    item for item in detail.get("ability_history", [])
                    if (int(item.get("chapter", 0)), str(item.get("level", ""))) not in old_pairs
                ]
                history.extend({"chapter": int(item.get("chapter", 0)), "level": str(item.get("new_value", ""))} for item in new_items)
                history.sort(key=lambda item: int(item.get("chapter", 0)))
                characters.replace_review_derived_state(name, ability_level=final_value, ability_history=history)
            elif field == "location":
                locations = detail.get("locations", []) if isinstance(detail.get("locations"), list) else []
                if locations and str(locations[-1].get("location", "")) != prior_latest:
                    continue
                old_pairs = {(int(item.get("chapter", 0)), str(item.get("new_value", ""))) for item in old_items}
                locations = [
                    item for item in locations
                    if (int(item.get("chapter", 0)), str(item.get("location", ""))) not in old_pairs
                ]
                if baseline and not locations:
                    locations.append({"chapter": 0, "location": baseline})
                locations.extend({"chapter": int(item.get("chapter", 0)), "location": str(item.get("new_value", ""))} for item in new_items)
                locations.sort(key=lambda item: int(item.get("chapter", 0)))
                characters.replace_review_derived_state(name, locations=locations)
            reconciled += 1
        return reconciled

    @staticmethod
    def _proposal_signature(item: dict, include_chapter: bool = True) -> tuple:
        return (
            int(item.get("chapter", 0)) if include_chapter else None,
            str(item.get("kind", "")), str(item.get("name", "")),
            str(item.get("field", "")), str(item.get("value", "")),
        )

    @staticmethod
    def _review_signature(item: dict, include_chapter: bool = True) -> tuple:
        return (
            int(item.get("chapter", 0)) if include_chapter else None,
            str(item.get("name", "")), str(item.get("field", "")),
            str(item.get("new_value", "")),
        )
