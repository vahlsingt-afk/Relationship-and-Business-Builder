"""
test_leadership_ownership_same_day_brief.py — RB-2026-09-18, next-sprint
Workstream 1.

_compute_leadership_ownership_same_day() reads executive_move_promotion.py's
and ownership_promotion.py's same-day promoted-candidate manifests and
surfaces them into daily_brief.py's pending_mutations section -- purely
additive VISIBILITY (mirrors technomic_watchlist_scan.py's proven same-day
pattern). Before this, a candidate detected by either scanner's daily
scan() sat invisible in its own candidate store unless Todd ran `... pending`
himself. record_proposal()'s confirm-before-mutate gate is untouched and out
of scope for this test file (covered in test_executive_move_promotion.py /
test_ownership_promotion.py).
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402
import executive_move_promotion as emp  # noqa: E402
import ownership_promotion as ownp  # noqa: E402


class TestLeadershipOwnershipSameDayBrief(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)
        self._exec_promoted = tmp / "executive_move_promoted.json"
        self._owner_promoted = tmp / "ownership_promotion_promoted.json"
        self._orig_exec_path = emp.PROMOTED_PATH
        self._orig_owner_path = ownp.PROMOTED_PATH
        emp.PROMOTED_PATH = self._exec_promoted
        ownp.PROMOTED_PATH = self._owner_promoted
        self._orig_has_flag = db._HAS_LEADERSHIP_OWNERSHIP_PROMOTION
        db._HAS_LEADERSHIP_OWNERSHIP_PROMOTION = True
        db._emp = emp
        db._ownp = ownp

    def tearDown(self):
        emp.PROMOTED_PATH = self._orig_exec_path
        ownp.PROMOTED_PATH = self._orig_owner_path
        db._HAS_LEADERSHIP_OWNERSHIP_PROMOTION = self._orig_has_flag
        self.tmpdir.cleanup()

    def _write_exec_manifest(self, scan_date: str, candidates: list[dict]) -> None:
        self._exec_promoted.write_text(json.dumps({"_scan_date": scan_date, "candidates": candidates}))

    def _write_owner_manifest(self, scan_date: str, candidates: list[dict]) -> None:
        self._owner_promoted.write_text(json.dumps({"_scan_date": scan_date, "candidates": candidates}))

    def test_no_manifests_produces_no_items(self):
        items = db._compute_leadership_ownership_same_day({}, {})
        self.assertEqual(items, [])

    def test_feature_flag_off_produces_no_items(self):
        db._HAS_LEADERSHIP_OWNERSHIP_PROMOTION = False
        self._write_exec_manifest(date.today().isoformat(), [
            {"candidate_id": "x", "entity_name": "Dairy Queen", "proposed_name": "Phil Crawford",
             "proposed_title": "CTO"},
        ])
        items = db._compute_leadership_ownership_same_day({}, {})
        self.assertEqual(items, [])

    def test_todays_executive_move_candidate_is_surfaced(self):
        self._write_exec_manifest(date.today().isoformat(), [
            {"candidate_id": "brand-dairy-queen::sig-1", "entity_name": "Dairy Queen",
             "proposed_name": "Phil Crawford", "proposed_title": "CTO"},
        ])
        items = db._compute_leadership_ownership_same_day({}, {})
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIn("Leadership change", item["title"])
        self.assertIn("Phil Crawford", item["summary"])
        self.assertIn("Dairy Queen", item["summary"])
        self.assertEqual(item["disposition"], "act_today")
        # Must never assert this as confirmed fact -- review-first discipline.
        self.assertIn("not verified facts", item["why_it_matters"])

    def test_yesterdays_executive_move_manifest_is_not_surfaced(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self._write_exec_manifest(yesterday, [
            {"candidate_id": "x", "entity_name": "Dairy Queen", "proposed_name": "Phil Crawford",
             "proposed_title": "CTO"},
        ])
        items = db._compute_leadership_ownership_same_day({}, {})
        self.assertEqual(items, [])

    def test_todays_ownership_candidate_is_surfaced(self):
        self._write_owner_manifest(date.today().isoformat(), [
            {"candidate_id": "brand-del-taco::sig-1", "entity_name": "Del Taco",
             "proposed_owner_name": "Yadav Enterprises"},
        ])
        items = db._compute_leadership_ownership_same_day({}, {})
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIn("Ownership change", item["title"])
        self.assertIn("Yadav Enterprises", item["summary"])
        self.assertIn("Del Taco", item["summary"])
        self.assertEqual(item["disposition"], "act_today")

    def test_both_same_day_manifests_produce_two_items(self):
        today = date.today().isoformat()
        self._write_exec_manifest(today, [
            {"candidate_id": "x", "entity_name": "Dairy Queen", "proposed_name": "Phil Crawford",
             "proposed_title": "CTO"},
        ])
        self._write_owner_manifest(today, [
            {"candidate_id": "y", "entity_name": "Del Taco", "proposed_owner_name": "Yadav Enterprises"},
        ])
        items = db._compute_leadership_ownership_same_day({}, {})
        self.assertEqual(len(items), 2)

    def test_missing_name_still_surfaces_with_placeholder(self):
        """confidence='unknown' candidates (no name extracted) are still
        eligible for same-day surfacing -- the detection itself ('something
        changed at this company') is useful signal even before a name is
        known. See executive_move_promotion.PROMOTED_PATH's own rationale."""
        self._write_exec_manifest(date.today().isoformat(), [
            {"candidate_id": "x", "entity_name": "Golden Corral", "proposed_name": None,
             "proposed_title": None},
        ])
        items = db._compute_leadership_ownership_same_day({}, {})
        self.assertEqual(len(items), 1)
        self.assertIn("name not yet extracted", items[0]["summary"])

    def test_wired_into_full_pending_mutations_assembly(self):
        """End-to-end: the same function this test file exercises directly
        is also what daily_brief's build path calls into
        sections['pending_mutations'] alongside _compute_pending_mutations()
        -- proves the two don't collide when both run."""
        self._write_exec_manifest(date.today().isoformat(), [
            {"candidate_id": "x", "entity_name": "Dairy Queen", "proposed_name": "Phil Crawford",
             "proposed_title": "CTO"},
        ])
        (Path(self.tmpdir.name) / "interaction_ledger.json").write_text(
            json.dumps({"_schema_version": "1.0", "interactions": []})
        )
        sections = {"pending_mutations": []}
        with patch.object(db.core, "SYSTEM_DIR", Path(self.tmpdir.name)):
            sections["pending_mutations"].extend(db._compute_pending_mutations({}, sections))
            sections["pending_mutations"].extend(db._compute_leadership_ownership_same_day({}, sections))
        titles = [i["title"] for i in sections["pending_mutations"]]
        self.assertTrue(any("none overdue" in t for t in titles))
        self.assertTrue(any("Leadership change" in t for t in titles))


if __name__ == "__main__":
    main()
