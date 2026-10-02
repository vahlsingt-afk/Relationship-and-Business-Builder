"""
test_technology_lifecycle.py — Technology Lifecycle Phase 1 governed module.

Isolated from the real system/technology_lifecycle/*.jsonl files (own tmp
paths for all 5 stores) and from the real ecosystem_intelligence.json (a
small synthetic graph with just the entities these tests need).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import technology_lifecycle as tl  # noqa: E402

_GRAPH = {
    "entities": [
        {"id": "brand-burger-king", "name": "Burger King", "entity_type": "brand", "aliases": []},
        {"id": "brand-mcdonalds", "name": "McDonald's", "entity_type": "brand", "aliases": []},
        {"id": "vendor-par-technology", "name": "PAR Technology", "entity_type": "vendor", "aliases": []},
        {"id": "parent-restaurant-brands-international", "name": "Restaurant Brands International", "entity_type": "parent", "aliases": []},
    ],
    "relationships": [],
}


def _rel_key(**overrides) -> dict:
    base = {
        "parent_entity_id": None, "brand_entity_id": "brand-burger-king", "operator_entity_id": None,
        "technology_category": "pos", "vendor_entity_id": "vendor-par-technology", "product": "PAR Brink POS",
    }
    base.update(overrides)
    return base


class _IsolatedPathsMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self._orig = {
            "RELATIONSHIP_EVENTS_PATH": tl.RELATIONSHIP_EVENTS_PATH, "GOVERNANCE_PATH": tl.GOVERNANCE_PATH,
            "PENETRATION_PATH": tl.PENETRATION_PATH, "CHANGE_EVENTS_PATH": tl.CHANGE_EVENTS_PATH,
            "FORCING_SIGNALS_PATH": tl.FORCING_SIGNALS_PATH,
        }
        tl.RELATIONSHIP_EVENTS_PATH = tmp / "rel.jsonl"
        tl.GOVERNANCE_PATH = tmp / "gov.jsonl"
        tl.PENETRATION_PATH = tmp / "pen.jsonl"
        tl.CHANGE_EVENTS_PATH = tmp / "chg.jsonl"
        tl.FORCING_SIGNALS_PATH = tmp / "fs.jsonl"
        self._graph_patch = patch.object(tl, "_load_graph", lambda: _GRAPH)
        self._graph_patch.start()

    def tearDown(self):
        self._graph_patch.stop()
        for name, path in self._orig.items():
            setattr(tl, name, path)
        self._tmpdir.cleanup()


class TestRecordRelationshipEvent(_IsolatedPathsMixin, unittest.TestCase):
    def test_writes_and_reads_back(self):
        rec = tl.record_relationship_event(
            event_id="t1", relationship_key=_rel_key(), entity_level="brand", lifecycle_state="selected",
            state_date_or_range="2024", evidence="e", source_url="https://example.com",
            source_type="press release", confidence="high", evidence_type="independent_evidence",
        )
        self.assertEqual(rec["event_id"], "t1")
        loaded = tl.load_records(tl.RELATIONSHIP_EVENTS_PATH)
        self.assertEqual(len(loaded), 1)

    def test_unresolvable_vendor_entity_id_rejected(self):
        with self.assertRaises(tl.TechnologyLifecycleError):
            tl.record_relationship_event(
                event_id="t1", relationship_key=_rel_key(vendor_entity_id="vendor-fake"),
                entity_level="brand", lifecycle_state="selected", state_date_or_range="2024",
                evidence="e", source_url=None, source_type="press release",
                confidence="high", evidence_type="independent_evidence",
            )

    def test_invalid_lifecycle_state_rejected(self):
        with self.assertRaises(tl.TechnologyLifecycleError):
            tl.record_relationship_event(
                event_id="t1", relationship_key=_rel_key(), entity_level="brand",
                lifecycle_state="made_up_state", state_date_or_range="2024",
                evidence="e", source_url=None, source_type="press release",
                confidence="high", evidence_type="independent_evidence",
            )

    def test_invalid_technology_category_rejected(self):
        with self.assertRaises(tl.TechnologyLifecycleError):
            tl.record_relationship_event(
                event_id="t1", relationship_key=_rel_key(technology_category="made_up_category"),
                entity_level="brand", lifecycle_state="selected", state_date_or_range="2024",
                evidence="e", source_url=None, source_type="press release",
                confidence="high", evidence_type="independent_evidence",
            )

    def test_invalid_scope_type_rejected(self):
        with self.assertRaises(tl.TechnologyLifecycleError):
            tl.record_relationship_event(
                event_id="t1", relationship_key=_rel_key(), entity_level="brand", lifecycle_state="selected",
                state_date_or_range="2024", evidence="e", source_url=None, source_type="press release",
                confidence="high", evidence_type="independent_evidence",
                scope_observation={"scope_type": "made_up_scope"},
            )

    def test_null_optional_entity_ids_allowed(self):
        rec = tl.record_relationship_event(
            event_id="t1", relationship_key=_rel_key(parent_entity_id=None, operator_entity_id=None),
            entity_level="brand", lifecycle_state="selected", state_date_or_range="2024",
            evidence="e", source_url=None, source_type="press release",
            confidence="high", evidence_type="independent_evidence",
        )
        self.assertIsNone(rec["relationship_key"]["parent_entity_id"])


class TestCurrentStateDerivation(_IsolatedPathsMixin, unittest.TestCase):
    def test_most_recent_observed_at_wins(self):
        rk = _rel_key()
        tl.record_relationship_event(
            event_id="e1", relationship_key=rk, entity_level="brand", lifecycle_state="selected",
            state_date_or_range="2023", evidence="e", source_url=None, source_type="x",
            confidence="high", evidence_type="independent_evidence", observed_at="2023-01-01",
        )
        tl.record_relationship_event(
            event_id="e2", relationship_key=rk, entity_level="brand", lifecycle_state="rollout_active",
            state_date_or_range="2024", evidence="e", source_url=None, source_type="x",
            confidence="high", evidence_type="independent_evidence", observed_at="2024-06-01",
        )
        state = tl.current_state_for_relationship(rk)
        self.assertEqual(state["lifecycle_state"], "rollout_active")

    def test_superseded_line_excluded_from_current_state(self):
        rk = _rel_key()
        tl.record_relationship_event(
            event_id="e1", relationship_key=rk, entity_level="brand", lifecycle_state="selected",
            state_date_or_range="2023", evidence="e", source_url=None, source_type="x",
            confidence="high", evidence_type="independent_evidence", observed_at="2023-01-01",
        )
        tl.record_relationship_event(
            event_id="e2", relationship_key=rk, entity_level="brand", lifecycle_state="pilot_abandoned",
            state_date_or_range="2023", evidence="correction", source_url=None, source_type="x",
            confidence="high", evidence_type="independent_evidence", observed_at="2023-06-01",
            supersedes="e1",
        )
        # e1 is superseded by e2 -- e2 is correctly the latest AND the only
        # non-superseded line, so current state is e2's.
        state = tl.current_state_for_relationship(rk)
        self.assertEqual(state["lifecycle_state"], "pilot_abandoned")

    def test_no_events_returns_none(self):
        self.assertIsNone(tl.current_state_for_relationship(_rel_key()))

    def test_distinct_relationship_keys_never_mixed(self):
        """Burger King POS/PAR vs. a different vendor on the same brand must
        never be conflated into one current-state answer."""
        rk1 = _rel_key(vendor_entity_id="vendor-par-technology", product="PAR Brink POS")
        tl.record_relationship_event(
            event_id="e1", relationship_key=rk1, entity_level="brand", lifecycle_state="deployed",
            state_date_or_range="2024", evidence="e", source_url=None, source_type="x",
            confidence="high", evidence_type="independent_evidence",
        )
        rk2 = _rel_key(brand_entity_id="brand-mcdonalds")
        self.assertIsNone(tl.current_state_for_relationship(rk2))
        self.assertIsNotNone(tl.current_state_for_relationship(rk1))


class TestRecordGovernance(_IsolatedPathsMixin, unittest.TestCase):
    def test_writes_valid_record(self):
        rec = tl.record_governance(
            governance_id="g1", brand_entity_id="brand-burger-king", technology_category="pos",
            governance_state="approved_vendor_list", evidence="e", source_url=None,
            confidence="medium", evidence_type="vendor_claim", approved_vendors=["vendor-par-technology"],
        )
        self.assertEqual(rec["governance_state"], "approved_vendor_list")

    def test_invalid_governance_state_rejected(self):
        with self.assertRaises(tl.TechnologyLifecycleError):
            tl.record_governance(
                governance_id="g1", brand_entity_id="brand-burger-king", technology_category="pos",
                governance_state="made_up_state", evidence="e", source_url=None,
                confidence="medium", evidence_type="vendor_claim",
            )

    def test_unresolvable_approved_vendor_rejected(self):
        with self.assertRaises(tl.TechnologyLifecycleError):
            tl.record_governance(
                governance_id="g1", brand_entity_id="brand-burger-king", technology_category="pos",
                governance_state="approved_vendor_list", evidence="e", source_url=None,
                confidence="medium", evidence_type="vendor_claim", approved_vendors=["vendor-fake"],
            )


class TestRecordPenetration(_IsolatedPathsMixin, unittest.TestCase):
    def test_writes_with_partial_counts_only(self):
        rec = tl.record_penetration(
            observation_id="p1", relationship_key=_rel_key(), entity_level="brand",
            penetration_type="location_penetration", observation_date="2026-01-01",
            confidence="medium", evidence_type="independent_evidence", live_locations=400,
        )
        self.assertEqual(rec["live_locations"], 400)
        self.assertIsNone(rec["verified_locations"])  # never fabricated when absent

    def test_invalid_penetration_type_rejected(self):
        with self.assertRaises(tl.TechnologyLifecycleError):
            tl.record_penetration(
                observation_id="p1", relationship_key=_rel_key(), entity_level="brand",
                penetration_type="made_up_type", observation_date="2026-01-01",
                confidence="medium", evidence_type="independent_evidence",
            )


class TestRecordChangeEvent(_IsolatedPathsMixin, unittest.TestCase):
    def test_writes_with_passthrough_extras(self):
        rec = tl.record_change_event(
            event_id="c1", brand_entity_id="brand-burger-king", technology_category="pos",
            overall_confidence="medium", change_scope="broad_stack_replacement",
            push_factors=[{"factor": "contract expiration", "evidence": "e"}],
        )
        self.assertEqual(rec["change_scope"], "broad_stack_replacement")
        self.assertEqual(rec["push_factors"][0]["factor"], "contract expiration")

    def test_unresolvable_brand_rejected(self):
        with self.assertRaises(tl.TechnologyLifecycleError):
            tl.record_change_event(
                event_id="c1", brand_entity_id="brand-fake", technology_category="pos",
                overall_confidence="medium",
            )


class TestRecordForcingSignal(_IsolatedPathsMixin, unittest.TestCase):
    def test_writes_valid_signal(self):
        rec = tl.record_forcing_signal(
            signal_id="s1", brand_entity_id="brand-burger-king", entity_level="brand",
            technology_category="pos_hardware", forcing_event_type="os_eol", detail="d",
            evidence="e", source_url=None, confidence="medium", evidence_type="rbb_inference",
        )
        self.assertEqual(rec["forcing_event_type"], "os_eol")

    def test_invalid_forcing_event_type_rejected(self):
        with self.assertRaises(tl.TechnologyLifecycleError):
            tl.record_forcing_signal(
                signal_id="s1", brand_entity_id="brand-burger-king", entity_level="brand",
                technology_category="pos_hardware", forcing_event_type="made_up_type", detail="d",
                evidence="e", source_url=None, confidence="medium", evidence_type="rbb_inference",
            )


class TestGetEntityTechnologyProfile(_IsolatedPathsMixin, unittest.TestCase):
    def test_empty_profile_for_unresearched_brand(self):
        profile = tl.get_entity_technology_profile("brand-mcdonalds")
        self.assertEqual(profile["relationships"], [])
        self.assertEqual(profile["forcing_signals"], [])

    def test_aggregates_all_stores_for_brand(self):
        tl.record_relationship_event(
            event_id="e1", relationship_key=_rel_key(), entity_level="brand", lifecycle_state="selected",
            state_date_or_range="2024", evidence="e", source_url=None, source_type="x",
            confidence="high", evidence_type="independent_evidence",
        )
        tl.record_forcing_signal(
            signal_id="s1", brand_entity_id="brand-burger-king", entity_level="brand",
            technology_category="pos_hardware", forcing_event_type="os_eol", detail="d",
            evidence="e", source_url=None, confidence="medium", evidence_type="rbb_inference",
        )
        profile = tl.get_entity_technology_profile("brand-burger-king")
        self.assertEqual(len(profile["relationships"]), 1)
        self.assertEqual(len(profile["forcing_signals"]), 1)


if __name__ == "__main__":
    unittest.main()
