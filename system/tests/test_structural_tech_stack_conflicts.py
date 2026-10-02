"""
test_structural_tech_stack_conflicts.py — RB-2026-09-19.

Finding 4 of system/RBB_STRATEGIC_ASSESSMENT_2026-09-19.md: every account's
contradictions.json is permanently seeded empty (customers_prospects_
common.py) with nothing anywhere writing to it, so account_background_
brief.py's "Conflicting information requiring reconciliation" section could
never render anything real, no matter how genuine the underlying conflict.

_find_structural_tech_stack_conflicts() closes the minimal, highest-
confidence case: read the EXPLICIT requires_confirmation / status ==
"conflicting" / conflicts_with fields ecosystem_intelligence.py's
check_relationship_conflict() already writes onto a relationship the
moment it detects a real rival claim for the same brand+category -- not a
re-derived heuristic (an earlier draft re-counted rival "live-status"
claims per category instead, which both false-positived on two
legitimately-competing "evaluating" candidates AND missed real flagged
conflicts, since a flagged relationship's own status becomes the literal
string "conflicting", not a LIVE_CLAIM_STATUSES member).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import account_background_brief as abb  # noqa: E402


def _rel(**overrides) -> dict:
    base = {
        "id": "rel-test-1",
        "relationship_type": "uses_vendor_for_category",
        "from_entity_id": "brand-test",
        "to_entity_id": "vendor-a",
        "category": "pos",
        "status": "active",
        "_other_entity_name": "Vendor A",
    }
    base.update(overrides)
    return base


class TestFindStructuralTechStackConflicts(unittest.TestCase):
    def test_no_relationships_returns_empty(self):
        self.assertEqual(abb._find_structural_tech_stack_conflicts([]), [])

    def test_single_clean_relationship_no_conflict(self):
        rels = [_rel()]
        self.assertEqual(abb._find_structural_tech_stack_conflicts(rels), [])

    def test_flagged_conflicting_relationship_is_surfaced(self):
        """The real, live production shape: an incoming claim gets
        status="conflicting" + requires_confirmation=True + conflicts_with
        pointing at the rival's id, per ecosystem_intelligence.py's own
        check_relationship_conflict() write path."""
        incoming = _rel(
            id="rel-incoming", to_entity_id="vendor-b", _other_entity_name="Vendor B",
            status="conflicting", requires_confirmation=True, conflicts_with="rel-existing",
        )
        existing = _rel(id="rel-existing", to_entity_id="vendor-a", _other_entity_name="Vendor A", status="active")
        conflicts = abb._find_structural_tech_stack_conflicts([existing, incoming])
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0]
        self.assertEqual(c["field"], "technology_stack.pos")
        self.assertEqual(c["claim_a"]["value"], "Vendor B")
        self.assertEqual(c["claim_a"]["status"], "conflicting")
        self.assertEqual(c["claim_b"]["value"], "Vendor A")
        self.assertEqual(c["claim_b"]["status"], "active")

    def test_requires_confirmation_true_without_status_string_still_caught(self):
        """Defensive: read either signal independently, not just the exact
        status string, in case a future write path sets one without the
        other."""
        incoming = _rel(
            id="rel-incoming", to_entity_id="vendor-b", _other_entity_name="Vendor B",
            status="active", requires_confirmation=True, conflicts_with="rel-existing",
        )
        existing = _rel(id="rel-existing", to_entity_id="vendor-a", _other_entity_name="Vendor A")
        conflicts = abb._find_structural_tech_stack_conflicts([existing, incoming])
        self.assertEqual(len(conflicts), 1)

    def test_two_evaluating_candidates_is_not_a_false_positive(self):
        """The exact case check_relationship_conflict()'s own docstring
        says is NOT a conflict: two open/tentative claims for the same
        category may legitimately compete. Neither carries
        requires_confirmation or status=="conflicting" because
        check_relationship_conflict() never flagged them -- this function
        must not invent a conflict a re-derived heuristic would have."""
        rels = [
            _rel(id="rel-a", to_entity_id="vendor-a", _other_entity_name="Vendor A", status="evaluating"),
            _rel(id="rel-b", to_entity_id="vendor-b", _other_entity_name="Vendor B", status="evaluating"),
        ]
        self.assertEqual(abb._find_structural_tech_stack_conflicts(rels), [])

    def test_auto_superseded_relationship_is_not_a_conflict(self):
        """The other check_relationship_conflict() outcome: a superseded
        claim is a resolved supersession, not an open contradiction."""
        rels = [
            _rel(id="rel-old", to_entity_id="vendor-a", _other_entity_name="Vendor A",
                 status="superseded", superseded_by="rel-new"),
            _rel(id="rel-new", to_entity_id="vendor-b", _other_entity_name="Vendor B", status="active"),
        ]
        self.assertEqual(abb._find_structural_tech_stack_conflicts(rels), [])

    def test_non_tech_stack_relationship_type_ignored(self):
        rel = _rel(relationship_type="employs", status="conflicting", requires_confirmation=True)
        self.assertEqual(abb._find_structural_tech_stack_conflicts([rel]), [])

    def test_rival_lookup_missing_degrades_gracefully(self):
        """conflicts_with points at an id not present in the relationships
        list handed in (e.g. the rival belongs to a different brand slice
        that wasn't fetched) -- must not raise, must still surface the
        flagged claim with an honest 'unknown' rival rather than crashing."""
        incoming = _rel(
            id="rel-incoming", to_entity_id="vendor-b", _other_entity_name="Vendor B",
            status="conflicting", requires_confirmation=True, conflicts_with="rel-not-present",
        )
        conflicts = abb._find_structural_tech_stack_conflicts([incoming])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["claim_b"]["value"], "rel-not-present")
        self.assertIsNone(conflicts[0]["claim_b"]["status"])

    def test_real_production_shape_dave_s_hot_chicken(self):
        """Regression fixture matching the real live-data case confirmed
        against system/ecosystem_intelligence.json while building this fix
        (brand-dave-s-hot-chicken / kds_kitchen_ops / vendor-qu conflicting
        with an active vendor-qsr-automations claim)."""
        incoming = _rel(
            id="rel-brand-dave-s-hot-chicken-kds-kitchen-ops-unknown-vendor-qsr-automations",
            from_entity_id="brand-dave-s-hot-chicken", to_entity_id="vendor-qu",
            _other_entity_name="Qu", category="kds_kitchen_ops",
            status="conflicting", requires_confirmation=True,
            conflicts_with="rel-brand-dave-s-hot-chicken-kds-kitchen-ops-vendor-qsr-automations",
        )
        existing = _rel(
            id="rel-brand-dave-s-hot-chicken-kds-kitchen-ops-vendor-qsr-automations",
            from_entity_id="brand-dave-s-hot-chicken", to_entity_id="vendor-qsr-automations",
            _other_entity_name="QSR Automations", category="kds_kitchen_ops", status="active",
        )
        conflicts = abb._find_structural_tech_stack_conflicts([existing, incoming])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["field"], "technology_stack.kds_kitchen_ops")
        self.assertEqual(conflicts[0]["claim_a"]["value"], "Qu")
        self.assertEqual(conflicts[0]["claim_b"]["value"], "QSR Automations")


class TestAssessFreshnessMergesStructuralConflicts(unittest.TestCase):
    """assess_freshness() must combine contradictions.json's (still empty
    in practice) stored entries with live-detected structural conflicts --
    and must never let a structural-detection failure break the freshness
    assessment as a whole."""

    def _intelligence(self, ecosystem_relationships):
        return {
            "dossier": {
                "account": {}, "brand_profile": {},
                "contradictions": {"contradictions": []},
            },
            "ecosystem_relationships": ecosystem_relationships,
        }

    def test_no_ecosystem_relationships_no_conflicts(self):
        freshness = abb.assess_freshness(self._intelligence([]))
        self.assertEqual(freshness["conflicting"], [])

    def test_structural_conflict_appears_in_conflicting_bucket(self):
        incoming = _rel(
            id="rel-incoming", to_entity_id="vendor-b", _other_entity_name="Vendor B",
            status="conflicting", requires_confirmation=True, conflicts_with="rel-existing",
        )
        existing = _rel(id="rel-existing", to_entity_id="vendor-a", _other_entity_name="Vendor A")
        freshness = abb.assess_freshness(self._intelligence([existing, incoming]))
        self.assertEqual(len(freshness["conflicting"]), 1)
        self.assertEqual(freshness["conflicting"][0]["field"], "technology_stack.pos")

    def test_stored_and_structural_conflicts_both_included(self):
        intel = self._intelligence([
            _rel(id="rel-incoming", to_entity_id="vendor-b", _other_entity_name="Vendor B",
                 status="conflicting", requires_confirmation=True, conflicts_with="rel-existing"),
            _rel(id="rel-existing", to_entity_id="vendor-a", _other_entity_name="Vendor A"),
        ])
        intel["dossier"]["contradictions"]["contradictions"] = [
            {"field": "brand_profile.ownership", "claim_a": {"value": "X"}, "claim_b": {"value": "Y"}, "note": "manual"},
        ]
        freshness = abb.assess_freshness(intel)
        self.assertEqual(len(freshness["conflicting"]), 2)
        fields = {c["field"] for c in freshness["conflicting"]}
        self.assertEqual(fields, {"technology_stack.pos", "brand_profile.ownership"})

    def test_missing_ecosystem_relationships_key_degrades_gracefully(self):
        intel = self._intelligence([])
        del intel["ecosystem_relationships"]
        freshness = abb.assess_freshness(intel)
        self.assertEqual(freshness["conflicting"], [])


if __name__ == "__main__":
    unittest.main()
