"""
test_genius_capabilities.py — RB-2026-09-25.

Coverage for genius_capabilities.py, the Genius Capability Library:
Todd's own curated, structured "what Genius offers per product line"
content -- the reusable baseline the Value Wedge draws from. Isolated
against a disposable GENIUS_CAPABILITIES_PATH; never touches real
production data.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import genius_capabilities as gc  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_path = gc.GENIUS_CAPABILITIES_PATH
        self._orig_evidence_path = gc.GENIUS_EVIDENCE_PATH
        gc.GENIUS_CAPABILITIES_PATH = Path(self._tmpdir.name) / "genius_capabilities.json"
        gc.GENIUS_EVIDENCE_PATH = Path(self._tmpdir.name) / "genius_own_evidence.jsonl"

    def tearDown(self):
        gc.GENIUS_CAPABILITIES_PATH = self._orig_path
        gc.GENIUS_EVIDENCE_PATH = self._orig_evidence_path
        self._tmpdir.cleanup()


class TestAddCapability(_IsolatedFixtureMixin):
    def test_add_and_list_roundtrip(self):
        result = gc.add_capability("pos", "Real-time inventory sync across 40k locations", why_it_matters="eliminates manual reconciliation")
        self.assertTrue(result["ok"])
        points = gc.list_capabilities("pos")
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["point"], "Real-time inventory sync across 40k locations")
        self.assertEqual(points[0]["why_it_matters"], "eliminates manual reconciliation")

    def test_why_it_matters_and_evidence_id_are_optional(self):
        result = gc.add_capability("payments", "PCI Level 1 compliant end to end")
        self.assertIsNone(result["capability"]["why_it_matters"])
        self.assertIsNone(result["capability"]["evidence_id"])

    def test_unknown_category_rejected_on_add(self):
        with self.assertRaises(ValueError):
            gc.add_capability("not_a_real_category", "some point")

    def test_unknown_category_rejected_on_list(self):
        with self.assertRaises(ValueError):
            gc.list_capabilities("not_a_real_category")

    def test_empty_point_rejected(self):
        with self.assertRaises(ValueError):
            gc.add_capability("pos", "   ")

    def test_multiple_points_accumulate_never_overwritten(self):
        gc.add_capability("pos", "Point one")
        gc.add_capability("pos", "Point two")
        gc.add_capability("pos", "Point three")
        points = gc.list_capabilities("pos")
        self.assertEqual([p["point"] for p in points], ["Point one", "Point two", "Point three"])

    def test_categories_stay_independent(self):
        gc.add_capability("pos", "POS-specific point")
        gc.add_capability("payments", "Payments-specific point")
        self.assertEqual(len(gc.list_capabilities("pos")), 1)
        self.assertEqual(len(gc.list_capabilities("payments")), 1)


class TestListAllCapabilities(_IsolatedFixtureMixin):
    def test_returns_all_seven_product_lines_even_when_empty(self):
        result = gc.list_all_capabilities()
        self.assertEqual(set(result.keys()), set(cic.GENIUS_PRODUCT_LINES))

    def test_honest_blank_categories_are_empty_lists_not_missing(self):
        gc.add_capability("pos", "Only POS has a point so far")
        result = gc.list_all_capabilities()
        self.assertEqual(result["pos"], gc.list_capabilities("pos"))
        self.assertEqual(result["payments"], [])
        self.assertIn("back_office", result)

    def test_missing_file_returns_all_empty_categories(self):
        """No add_capability() call at all -- the store file never gets
        created -- list_all_capabilities() must still return every
        category, not raise or return {}."""
        result = gc.list_all_capabilities()
        self.assertEqual(len(result), 7)
        self.assertTrue(all(v == [] for v in result.values()))


class TestAddEvidence(_IsolatedFixtureMixin):
    def test_add_and_list_roundtrip(self):
        result = gc.add_evidence("pos", "Bookings +25% sequentially in Q2 2026", category="market_share",
                                  source="GPN Q2 2026 earnings call", confidence="critical")
        self.assertTrue(result["ok"])
        records = gc.list_evidence("pos")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["summary"], "Bookings +25% sequentially in Q2 2026")
        self.assertEqual(records[0]["confidence"], "critical")
        self.assertEqual(records[0]["source"], "GPN Q2 2026 earnings call")

    def test_parent_and_adjacent_scopes_are_valid(self):
        gc.add_evidence("parent", "Closed Worldpay acquisition Jan 12, 2026")
        gc.add_evidence("adjacent", "Kitchen Management line exists for context")
        self.assertEqual(len(gc.list_evidence("parent")), 1)
        self.assertEqual(len(gc.list_evidence("adjacent")), 1)

    def test_unknown_scope_rejected(self):
        with self.assertRaises(ValueError):
            gc.add_evidence("not_a_real_scope", "some note")
        with self.assertRaises(ValueError):
            gc.list_evidence("not_a_real_scope")

    def test_unknown_category_rejected(self):
        with self.assertRaises(ValueError):
            gc.add_evidence("pos", "some note", category="not_a_real_category")

    def test_empty_note_rejected(self):
        with self.assertRaises(ValueError):
            gc.add_evidence("pos", "   ")

    def test_evidence_ids_scoped_per_line_not_global(self):
        gc.add_evidence("pos", "POS fact one")
        gc.add_evidence("payments", "Payments fact one")
        gc.add_evidence("pos", "POS fact two")
        pos_records = gc.list_evidence("pos")
        self.assertEqual(pos_records[0]["evidence_id"], "genius-pos-0001")
        self.assertEqual(pos_records[1]["evidence_id"], "genius-pos-0002")
        self.assertEqual(gc.list_evidence("payments")[0]["evidence_id"], "genius-payments-0001")

    def test_capability_and_evidence_stores_are_independent(self):
        gc.add_capability("pos", "A capability point")
        gc.add_evidence("pos", "An evidence note")
        self.assertEqual(len(gc.list_capabilities("pos")), 1)
        self.assertEqual(len(gc.list_evidence("pos")), 1)


class TestListAllEvidence(_IsolatedFixtureMixin):
    def test_returns_every_valid_scope_even_when_empty(self):
        result = gc.list_all_evidence()
        self.assertEqual(set(result.keys()), gc.GENIUS_EVIDENCE_SCOPES)
        self.assertTrue(all(v == [] for v in result.values()))

    def test_honest_blank_scopes_stay_separate(self):
        gc.add_evidence("pos", "Only POS has evidence so far")
        result = gc.list_all_evidence()
        self.assertEqual(len(result["pos"]), 1)
        self.assertEqual(result["payments"], [])
        self.assertIn("parent", result)
        self.assertIn("adjacent", result)


if __name__ == "__main__":
    unittest.main()
