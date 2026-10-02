"""
test_data_tier_smoke.py

Regression coverage for system/scripts/data_tier_smoke.py -- the scanner
that flags Tier 1 (canonical public) stores for private-judgment content
that shouldn't be there. Real motivating cases (both found by hand, not
by any automated check, on 2026-09-30): a live sales-opportunity note in
ecosystem_intelligence.json's entities[].notes (brand-little-caesars),
and a franchisee-complaint pain-point entry served live to teammates
(brand-burger-king) -- this test file locks in that the scanner would
have caught both, and that a properly-marked-private entry is correctly
left alone.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import data_tier_smoke as dts  # noqa: E402
import rb_core as core  # noqa: E402


class TestScanEcosystemNotes(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.graph_path = Path(self.tmpdir.name) / "ecosystem_intelligence.json"
        self._patch = patch.object(core, "ECOSYSTEM_INTELLIGENCE_PATH", self.graph_path)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _write(self, entities):
        self.graph_path.write_text(json.dumps({"entities": entities}), encoding="utf-8")

    def test_flags_a_populated_notes_field(self):
        self._write([{"id": "brand-little-caesars", "notes": "Active opportunity note"}])
        findings = dts.scan_ecosystem_notes()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["entity_id"], "brand-little-caesars")

    def test_does_not_flag_an_entity_with_no_notes_key(self):
        self._write([{"id": "brand-clean"}])
        self.assertEqual(dts.scan_ecosystem_notes(), [])

    def test_does_not_flag_an_empty_notes_string(self):
        self._write([{"id": "brand-clean", "notes": ""}])
        self.assertEqual(dts.scan_ecosystem_notes(), [])

    def test_missing_graph_file_returns_empty_not_an_error(self):
        self.assertEqual(dts.scan_ecosystem_notes(), [])


class TestScanBrandProfiles(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.profiles_dir = Path(self.tmpdir.name)
        self._patch = patch.object(dts, "BRAND_PROFILES_DIR", self.profiles_dir)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _write_profile(self, filename, pain_points=None, recent_signals=None, brand_id="brand-test"):
        (self.profiles_dir / filename).write_text(json.dumps({
            "brand_id": brand_id,
            "pain_points": pain_points or [],
            "recent_signals": recent_signals or [],
        }), encoding="utf-8")

    def test_flags_a_human_reviewed_pain_point_with_no_visibility_key(self):
        """The exact real Burger King shape: last_reviewed_by starts with
        "team:", visibility key never set at all (pre-2026-09-29 entry)."""
        self._write_profile("brand-burger-king.json", pain_points=[
            {"value": "Franchisees complaining about kiosk downtime",
             "last_reviewed_by": "team:todd"},
        ])
        findings = dts.scan_brand_profiles()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["field"], "pain_points")

    def test_does_not_flag_an_entry_already_marked_private(self):
        self._write_profile("brand-burger-king.json", pain_points=[
            {"value": "Franchisees complaining about kiosk downtime",
             "last_reviewed_by": "team:todd", "visibility": "private"},
        ])
        self.assertEqual(dts.scan_brand_profiles(), [])

    def test_does_not_flag_a_system_sourced_entry(self):
        self._write_profile("brand-clean.json", recent_signals=[
            {"value": "Public earnings note", "last_reviewed_by": "system:earnings_monitor_bridge"},
        ])
        self.assertEqual(dts.scan_brand_profiles(), [])

    def test_flags_human_reviewed_recent_signals_too(self):
        self._write_profile("brand-test.json", recent_signals=[
            {"value": "Some note", "last_reviewed_by": "human:todd"},
        ])
        findings = dts.scan_brand_profiles()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["field"], "recent_signals")

    def test_missing_profiles_dir_returns_empty_not_an_error(self):
        self._patch.stop()
        self._patch = patch.object(dts, "BRAND_PROFILES_DIR", self.profiles_dir / "does-not-exist")
        self._patch.start()
        self.assertEqual(dts.scan_brand_profiles(), [])


class TestRunScanExitCode(unittest.TestCase):
    def test_main_returns_nonzero_when_anything_is_flagged(self):
        with patch.object(dts, "scan_ecosystem_notes", return_value=[{"entity_id": "x"}]), \
             patch.object(dts, "scan_brand_profiles", return_value=[]), \
             patch("sys.argv", ["data_tier_smoke.py", "--json"]):
            self.assertEqual(dts.main(), 1)

    def test_main_returns_zero_when_clean(self):
        with patch.object(dts, "scan_ecosystem_notes", return_value=[]), \
             patch.object(dts, "scan_brand_profiles", return_value=[]), \
             patch("sys.argv", ["data_tier_smoke.py", "--json"]):
            self.assertEqual(dts.main(), 0)


if __name__ == "__main__":
    unittest.main()
