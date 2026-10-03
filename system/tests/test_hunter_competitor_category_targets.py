"""test_hunter_competitor_category_targets.py — 2026-10-02.

Isolated against a hand-built graph + temp competitor registry, never
real production data (same _IsolatedRootMixin pattern as
test_competitor_intelligence.py).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import competitor_intelligence_common as cic  # noqa: E402
import hunter_competitor_category_targets as hcc  # noqa: E402


def _brand(id_, entity_type="brand"):
    return {"id": id_, "entity_type": entity_type, "name": id_}


def _vendor(id_, name):
    return {"id": id_, "entity_type": "vendor", "name": name}


def _rel(brand_id, vendor_id, category):
    return {
        "relationship_type": "uses_vendor_for_category",
        "from_entity_id": brand_id, "to_entity_id": vendor_id,
        "category": category, "status": "active",
    }


def _graph():
    """pos: vendor-acme (3 brands) ranked #1, vendor-other (1 brand) #2.
    payments_gateway: vendor-acme also ranked #1 there (2 brands) -- real
    reason to collapse into one target with 2 category placements."""
    entities = [
        _vendor("vendor-acme", "Acme POS"), _vendor("vendor-other", "Other POS"),
        _brand("brand-1"), _brand("brand-2"), _brand("brand-3"), _brand("brand-4"),
    ]
    relationships = [
        _rel("brand-1", "vendor-acme", "pos"), _rel("brand-2", "vendor-acme", "pos"),
        _rel("brand-3", "vendor-acme", "pos"), _rel("brand-4", "vendor-other", "pos"),
        _rel("brand-1", "vendor-acme", "payments_gateway"), _rel("brand-2", "vendor-acme", "payments_gateway"),
    ]
    return {"entities": entities, "relationships": relationships}


class _IsolatedRootMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_root = cic.ROOT
        cic.ROOT = Path(self._tmpdir.name)

    def tearDown(self):
        cic.ROOT = self._orig_root
        self._tmpdir.cleanup()


class TestTopCompetitorsByCategory(_IsolatedRootMixin):
    def test_dedupes_vendor_appearing_in_multiple_categories(self):
        placements = hcc.top_competitors_by_category(_graph(), top=10)
        self.assertIn("vendor-acme", placements)
        cats = sorted(p["category"] for p in placements["vendor-acme"]["placements"])
        self.assertIn("pos", cats)
        self.assertIn("payments_gateway", cats)

    def test_top_n_cutoff_excludes_lower_ranked_vendor_when_n_is_1(self):
        placements = hcc.top_competitors_by_category(_graph(), top=1)
        self.assertIn("vendor-acme", placements)
        self.assertNotIn("vendor-other", placements)


class TestBuildManifest(_IsolatedRootMixin):
    def setUp(self):
        super().setUp()
        cic.create_competitor_shell("acme-pos", "Acme POS", "vendor-acme")
        cic.create_competitor_shell("other-pos", "Other POS", "vendor-other")
        # _competitor_vendor_index() joins through the registry, not
        # just competitor.json on disk -- create_competitor_shell alone
        # doesn't register (only a subsequent writer call does).
        cic.register_competitor("acme-pos", "Acme POS")
        cic.register_competitor("other-pos", "Other POS")

    def test_fully_empty_competitor_has_all_four_focus_gaps(self):
        manifest = hcc.build_manifest(top=10, graph=_graph())
        acme = next(t for t in manifest["targets"] if t["target_key"] == "competitor:acme-pos")
        self.assertEqual(acme["category_count"], 2)
        self.assertEqual(acme["best_rank"], 1)
        gap_fields = sorted(g["field"] for g in acme["gaps"])
        self.assertEqual(gap_fields, sorted(hcc._FOCUS_FIELDS))

    def test_populated_focus_fields_close_their_gaps(self):
        import competitor_intelligence as ci
        ci.add_extended_profile_finding("acme-pos", "features", "Real-time kitchen display routing")
        ci.add_extended_profile_finding("acme-pos", "customer_feedback_testimonials", "G2 reviewer: cut ticket times")
        ci.add_extended_profile_finding("acme-pos", "value_statement", "The fastest line at every register.")
        ci.add_competitive_note("acme-pos", "Positions on speed and simplicity", category="pov")  # unrelated field, no-op here
        comp = cic.load_competitor("acme-pos")["competitor"]
        comp["positioning_summary"] = "Positions as the fast, simple POS for growing multi-unit brands"
        cic.save_json(cic.competitor_dir("acme-pos") / "competitor.json", comp)

        manifest = hcc.build_manifest(top=10, graph=_graph())
        acme = next(t for t in manifest["targets"] if t["target_key"] == "competitor:acme-pos")
        self.assertEqual(acme["gaps"], [])

    def test_vendor_with_no_competitor_profile_is_skipped_not_fabricated(self):
        graph = _graph()
        graph["entities"].append(_vendor("vendor-untracked", "Untracked Vendor"))
        graph["relationships"].append(_rel("brand-1", "vendor-untracked", "pos"))
        manifest = hcc.build_manifest(top=10, graph=graph)
        keys = [t["target_key"] for t in manifest["targets"]]
        self.assertNotIn("competitor:vendor-untracked", keys)
        self.assertFalse(any("untracked" in k for k in keys))

    def test_ranking_prefers_most_categories_then_most_gaps(self):
        manifest = hcc.build_manifest(top=10, graph=_graph())
        ordered_keys = [t["target_key"] for t in manifest["targets"]]
        self.assertEqual(ordered_keys[0], "competitor:acme-pos")  # 2 categories vs. 1


if __name__ == "__main__":
    unittest.main()
