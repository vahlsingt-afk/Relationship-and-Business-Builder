"""
test_watchlist_promotion.py

RB-2026-09-05, watchlist auto-expansion scoping. Todd's two scoping
decisions, both tested here:
  - Brand promotion bar: repeated appearance over a window (90 days --
    not the naive 14, since technomic_watchlist_scan.py's Tier 2 rotation
    only scans ~1400 of the 1500 brands about once a week, making a
    14-day/3-appearance bar mathematically impossible for them).
  - Vendor discovery: mine ecosystem_intelligence.json's own vendor
    entities rather than sourcing a new external universe list.

Fully isolated -- tmp registry file, tmp candidate store, tmp history log,
tmp ecosystem graph. Never touches real production data.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import rb_core as core  # noqa: E402
import watchlist_promotion as wp  # noqa: E402
import watchlist_registry as wr  # noqa: E402
import technomic_watchlist_scan as tws  # noqa: E402


def _registry(brands=None, tech=None) -> dict:
    return {
        "schema_version": "1.0", "last_updated": "2026-01-01",
        "restaurant_brands": brands or ["McDonald's"],
        "restaurant_tech": tech or {"restaurant_tech_pos": ["Toast"]},
    }


def _vendor(id_, name, *, primary_category="pos"):
    return {
        "id": id_, "name": name, "entity_type": "vendor", "subtype": "restaurant_technology_vendor",
        "status": "active", "domains": ["restaurants"], "aliases": [],
        "attributes": {"primary_category": primary_category}, "sources": [], "confidence": {},
    }


def _graph(entities=None, relationships=None) -> dict:
    return {
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-05",
        "entities": entities or [], "relationships": relationships or [],
        "signals": [], "sources": [], "assessments": [], "user_relevance": [],
        "strategic_recommendations": [],
    }


class WatchlistPromotionTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)

        self.registry_path = tmp / "watchlist_registry.json"
        self.registry_path.write_text(json.dumps(_registry()), encoding="utf-8")
        self.store_path = tmp / "watchlist_promotion_candidates.json"
        self.history_path = tmp / "technomic_watchlist_history.jsonl"
        self.graph_path = tmp / "ecosystem_intelligence.json"

        self._patches = [
            patch.object(wr, "REGISTRY_PATH", self.registry_path),
            patch.object(wp, "STORE_PATH", self.store_path),
            patch.object(tws, "HISTORY_PATH", self.history_path),
            patch.object(core, "ECOSYSTEM_INTELLIGENCE_PATH", self.graph_path),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmpdir.cleanup()

    def _write_history(self, rows: list[dict]) -> None:
        self.history_path.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )

    def _write_graph(self, graph: dict) -> None:
        self.graph_path.write_text(json.dumps(graph), encoding="utf-8")

    def _all_candidates(self) -> list[dict]:
        """Confidence-Based Auto-Recording Phase 5 (2026-09-25): scanning
        now auto-applies a qualifying candidate immediately, so pending_
        candidates() (status=="proposed_pending_confirmation") is empty
        right after a scan -- tests that need to inspect what a scan just
        produced read the full candidate store instead."""
        return list(wp._load_store()["candidates"].values())


class TestBrandPromotionScan(WatchlistPromotionTestCase):
    def test_below_threshold_not_proposed(self):
        self._write_history([
            {"date": "2026-08-01", "name": "Sweetgreen", "tier": "tier2"},
            {"date": "2026-08-10", "name": "Sweetgreen", "tier": "tier2"},
        ])
        result = wp.scan_brand_promotions(date(2026, 9, 5))
        self.assertEqual(result["new_candidates"], 0)
        self.assertEqual(wp.pending_candidates(), [])

    def test_at_threshold_proposed(self):
        self._write_history([
            {"date": "2026-08-01", "name": "Sweetgreen", "tier": "tier2"},
            {"date": "2026-08-10", "name": "Sweetgreen", "tier": "tier2"},
            {"date": "2026-08-20", "name": "Sweetgreen", "tier": "tier2"},
        ])
        result = wp.scan_brand_promotions(date(2026, 9, 5))
        self.assertEqual(result["new_candidates"], 1)
        self.assertEqual(result["auto_applied"], 1)
        candidates = self._all_candidates()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["name"], "Sweetgreen")
        self.assertEqual(candidates[0]["category"], "brand")
        self.assertEqual(candidates[0]["status"], "confirmed")
        self.assertEqual(candidates[0]["confirmed_by"], "system:watchlist_promotion")
        self.assertEqual(len(candidates[0]["evidence"]["appearance_dates"]), 3)
        self.assertIn("Sweetgreen", wr.mandatory_all())

    def test_duplicate_same_day_appearance_not_double_counted(self):
        """Same-day tier1+tier2 double-runs are the normal case (see
        technomic_watchlist_scan.py's own comment) -- two history rows on
        the same date for the same brand must count as ONE appearance."""
        self._write_history([
            {"date": "2026-08-01", "name": "Sweetgreen", "tier": "tier1"},
            {"date": "2026-08-01", "name": "Sweetgreen", "tier": "tier2"},
            {"date": "2026-08-10", "name": "Sweetgreen", "tier": "tier2"},
            {"date": "2026-08-20", "name": "Sweetgreen", "tier": "tier2"},
        ])
        result = wp.scan_brand_promotions(date(2026, 9, 5))
        self.assertEqual(result["new_candidates"], 1)
        self.assertEqual(len(self._all_candidates()[0]["evidence"]["appearance_dates"]), 3)

    def test_appearances_outside_window_do_not_count(self):
        self._write_history([
            {"date": "2026-01-01", "name": "Sweetgreen", "tier": "tier2"},
            {"date": "2026-01-10", "name": "Sweetgreen", "tier": "tier2"},
            {"date": "2026-01-20", "name": "Sweetgreen", "tier": "tier2"},
        ])
        result = wp.scan_brand_promotions(date(2026, 9, 5))
        self.assertEqual(result["new_candidates"], 0)

    def test_already_mandatory_brand_never_proposed(self):
        self._write_history([
            {"date": "2026-08-01", "name": "McDonald's", "tier": "tier1"},
            {"date": "2026-08-10", "name": "McDonald's", "tier": "tier1"},
            {"date": "2026-08-20", "name": "McDonald's", "tier": "tier1"},
        ])
        result = wp.scan_brand_promotions(date(2026, 9, 5))
        self.assertEqual(result["new_candidates"], 0)

    def test_resolved_candidate_never_reproposed(self):
        """Confidence-Based Auto-Recording Phase 5: a candidate auto-
        confirms on first scan. Re-scanning must not create a second,
        duplicate candidate for the same brand."""
        self._write_history([
            {"date": "2026-08-01", "name": "Sweetgreen", "tier": "tier2"},
            {"date": "2026-08-10", "name": "Sweetgreen", "tier": "tier2"},
            {"date": "2026-08-20", "name": "Sweetgreen", "tier": "tier2"},
        ])
        wp.scan_brand_promotions(date(2026, 9, 5))
        self.assertEqual(self._all_candidates()[0]["status"], "confirmed")
        # Re-scan: must not come back as a fresh candidate.
        result = wp.scan_brand_promotions(date(2026, 9, 6))
        self.assertEqual(result["new_candidates"], 0)
        self.assertEqual(len(self._all_candidates()), 1)

    def test_missing_history_file_is_a_graceful_noop(self):
        result = wp.scan_brand_promotions(date(2026, 9, 5))
        self.assertEqual(result, {"scanned": 0, "new_candidates": 0, "auto_applied": 0})


class TestVendorDiscoveryScan(WatchlistPromotionTestCase):
    def test_thin_vendor_below_relationship_bar_not_proposed(self):
        self._write_graph(_graph(
            entities=[_vendor("vendor-x", "NewVendorCo")],
            relationships=[{"from_entity_id": "brand-a", "to_entity_id": "vendor-x", "status": "active"}],
        ))
        result = wp.scan_vendor_candidates()
        self.assertEqual(result["new_candidates"], 0)

    def test_vendor_with_enough_relationships_proposed(self):
        self._write_graph(_graph(
            entities=[_vendor("vendor-x", "SevenRooms")],
            relationships=[
                {"from_entity_id": "brand-a", "to_entity_id": "vendor-x", "status": "active"},
                {"from_entity_id": "brand-b", "to_entity_id": "vendor-x", "status": "active"},
            ],
        ))
        result = wp.scan_vendor_candidates()
        self.assertEqual(result["new_candidates"], 1)
        self.assertEqual(result["auto_applied"], 1)
        candidates = self._all_candidates()
        self.assertEqual(candidates[0]["name"], "SevenRooms")
        self.assertEqual(candidates[0]["category"], "vendor")
        self.assertEqual(candidates[0]["status"], "confirmed")
        self.assertEqual(candidates[0]["evidence"]["active_relationship_count"], 2)
        registry = wr.load_registry()
        self.assertIn("SevenRooms", registry["restaurant_tech"]["restaurant_tech_other"])

    def test_historical_not_active_relationships_do_not_count(self):
        self._write_graph(_graph(
            entities=[_vendor("vendor-x", "SevenRooms")],
            relationships=[
                {"from_entity_id": "brand-a", "to_entity_id": "vendor-x", "status": "historical"},
                {"from_entity_id": "brand-b", "to_entity_id": "vendor-x", "status": "historical"},
            ],
        ))
        result = wp.scan_vendor_candidates()
        self.assertEqual(result["new_candidates"], 0)

    def test_internal_placeholder_vendor_filtered_out(self):
        self._write_graph(_graph(
            entities=[_vendor("vendor-x", "Chick-fil-A internal"), _vendor("vendor-y", "custom_internal_pos")],
            relationships=[
                {"from_entity_id": "brand-a", "to_entity_id": "vendor-x", "status": "active"},
                {"from_entity_id": "brand-b", "to_entity_id": "vendor-x", "status": "active"},
                {"from_entity_id": "brand-a", "to_entity_id": "vendor-y", "status": "active"},
                {"from_entity_id": "brand-b", "to_entity_id": "vendor-y", "status": "active"},
            ],
        ))
        result = wp.scan_vendor_candidates()
        self.assertEqual(result["new_candidates"], 0)

    def test_name_variant_of_already_mandatory_vendor_filtered_out(self):
        """'NCR' is a real ecosystem entity but already-mandatory 'NCR Voyix'
        covers it -- the fuzzy token-subset match technomic_watchlist_scan.py
        already built for brands is reused here so this doesn't get
        proposed as if it were a genuinely new vendor."""
        self.registry_path.write_text(
            json.dumps(_registry(tech={"restaurant_tech_pos": ["Toast", "NCR Voyix"]})), encoding="utf-8",
        )
        self._write_graph(_graph(
            entities=[_vendor("vendor-ncr", "NCR")],
            relationships=[
                {"from_entity_id": "brand-a", "to_entity_id": "vendor-ncr", "status": "active"},
                {"from_entity_id": "brand-b", "to_entity_id": "vendor-ncr", "status": "active"},
            ],
        ))
        result = wp.scan_vendor_candidates()
        self.assertEqual(result["new_candidates"], 0)

    def test_already_mandatory_vendor_exact_match_filtered_out(self):
        self._write_graph(_graph(
            entities=[_vendor("vendor-toast", "Toast")],
            relationships=[
                {"from_entity_id": "brand-a", "to_entity_id": "vendor-toast", "status": "active"},
                {"from_entity_id": "brand-b", "to_entity_id": "vendor-toast", "status": "active"},
            ],
        ))
        result = wp.scan_vendor_candidates()
        self.assertEqual(result["new_candidates"], 0)

    def test_brand_entities_never_proposed_as_vendors(self):
        self._write_graph(_graph(entities=[
            {"id": "brand-x", "name": "Some Brand", "entity_type": "brand", "aliases": [],
             "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]},
        ]))
        result = wp.scan_vendor_candidates()
        self.assertEqual(result["scanned"], 0)


class TestRecordProposal(WatchlistPromotionTestCase):
    """record_proposal() itself, exercised directly against a manually-
    seeded pending candidate -- independent of scan_brand_promotions()/
    scan_vendor_candidates() now auto-applying (Phase 5, 2026-09-25), since
    a manual confirm/reject path must keep working for anything not
    reached by the two scan functions (e.g. a future kind, or a candidate
    seeded some other way)."""

    def _seed_pending(self, *, category: str, name: str, evidence: dict | None = None) -> str:
        store = wp._load_store()
        wp._add_candidate(store, category=category, name=name, reason="test", evidence=evidence or {})
        wp._save_store(store)
        return wp._candidate_id(category, name)

    def test_confirm_brand_writes_into_registry(self):
        cid = self._seed_pending(category="brand", name="Sweetgreen")
        result = wp.record_proposal(cid, confirmed=True)
        self.assertTrue(result["confirmed"])
        self.assertIn("Sweetgreen", wr.mandatory_all())

    def test_confirm_vendor_writes_into_registry_other_category(self):
        cid = self._seed_pending(category="vendor", name="SevenRooms")
        result = wp.record_proposal(cid, confirmed=True)
        self.assertTrue(result["confirmed"])
        registry = wr.load_registry()
        self.assertIn("SevenRooms", registry["restaurant_tech"]["restaurant_tech_other"])

    def test_reject_does_not_write_into_registry(self):
        cid = self._seed_pending(category="brand", name="Sweetgreen")
        result = wp.record_proposal(cid, confirmed=False)
        self.assertTrue(result["rejected"])
        self.assertNotIn("Sweetgreen", wr.mandatory_all())

    def test_unknown_candidate_id_returns_error(self):
        result = wp.record_proposal("brand::NoSuchThing", confirmed=True)
        self.assertIn("error", result)

    def test_already_resolved_candidate_cannot_be_resolved_again(self):
        cid = self._seed_pending(category="brand", name="Sweetgreen")
        wp.record_proposal(cid, confirmed=True)
        result = wp.record_proposal(cid, confirmed=False)
        self.assertIn("error", result)

    def test_confirmed_by_defaults_to_human(self):
        cid = self._seed_pending(category="brand", name="Sweetgreen")
        wp.record_proposal(cid, confirmed=True)
        self.assertEqual(self._all_candidates()[0]["confirmed_by"], "human")

    def test_confirmed_by_tags_system_provenance_when_passed(self):
        cid = self._seed_pending(category="brand", name="Sweetgreen")
        wp.record_proposal(cid, confirmed=True, confirmed_by="system:watchlist_promotion")
        self.assertEqual(self._all_candidates()[0]["confirmed_by"], "system:watchlist_promotion")


class TestScanAutoApplyIntegration(WatchlistPromotionTestCase):
    """scan_brand_promotions()/scan_vendor_candidates() end-to-end: a
    qualifying candidate is confirmed into the registry within the same
    scan call, no separate confirm step required."""

    def test_brand_scan_writes_registry_in_one_call(self):
        self._write_history([
            {"date": "2026-08-01", "name": "Sweetgreen", "tier": "tier2"},
            {"date": "2026-08-10", "name": "Sweetgreen", "tier": "tier2"},
            {"date": "2026-08-20", "name": "Sweetgreen", "tier": "tier2"},
        ])
        result = wp.scan_brand_promotions(date(2026, 9, 5))
        self.assertEqual(result, {"scanned": 1, "new_candidates": 1, "auto_applied": 1})
        self.assertIn("Sweetgreen", wr.mandatory_all())

    def test_dry_run_never_auto_applies(self):
        self._write_history([
            {"date": "2026-08-01", "name": "Sweetgreen", "tier": "tier2"},
            {"date": "2026-08-10", "name": "Sweetgreen", "tier": "tier2"},
            {"date": "2026-08-20", "name": "Sweetgreen", "tier": "tier2"},
        ])
        result = wp.scan_brand_promotions(date(2026, 9, 5), dry_run=True)
        self.assertEqual(result["new_candidates"], 1)
        self.assertEqual(result["auto_applied"], 0)
        self.assertNotIn("Sweetgreen", wr.mandatory_all())
        self.assertEqual(self._all_candidates(), [])


if __name__ == "__main__":
    unittest.main()
