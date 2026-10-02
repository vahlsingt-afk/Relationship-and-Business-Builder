"""
test_intelligence_calibration.py — RB-2026-09-15 (Codex handoff item #5,
outcome-correlation half).

Coverage for intelligence_calibration.py: disposition counts by item_type,
and outcome correlation for buying_window_hypothesis items against
sales_opportunity_radar_state.json's real active_pursuit signal. All state
paths isolated from real production caches -- same discipline as
test_intelligence_action_queue.py's _IsolatedQueueMixin.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
import intelligence_action_queue as iaq  # noqa: E402
import intelligence_calibration as calib  # noqa: E402
import sales_opportunity_radar as radar  # noqa: E402


class _IsolatedCalibrationMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        tmp = Path(self._tmpdir)

        self._orig_iaq_state = iaq.STATE_PATH
        self._orig_radar_state = radar.STATE_PATH
        self._orig_cache = calib.CACHE_PATH
        iaq.STATE_PATH = tmp / "intelligence_action_queue_state.json"
        radar.STATE_PATH = tmp / "sales_opportunity_radar_state.json"
        calib.CACHE_PATH = tmp / "intelligence_calibration.json"

    def tearDown(self):
        iaq.STATE_PATH = self._orig_iaq_state
        radar.STATE_PATH = self._orig_radar_state
        calib.CACHE_PATH = self._orig_cache
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_resolutions(self, resolutions: dict) -> None:
        iaq.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        iaq.STATE_PATH.write_text(json.dumps({"resolutions": resolutions}), encoding="utf-8")

    def _write_radar_state(self, entities: dict) -> None:
        radar.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        radar.STATE_PATH.write_text(json.dumps({"entities": entities}), encoding="utf-8")


class TestEmptyState(_IsolatedCalibrationMixin):
    def test_no_resolutions_produces_honest_empty_report(self):
        report = calib.build()
        self.assertEqual(report["total_resolutions"], 0)
        self.assertEqual(report["disposition_counts_by_item_type"], {})
        self.assertEqual(report["outcome_correlation"]["accepted_with_known_outcome"], 0)
        self.assertEqual(report["outcome_correlation"]["accepted_confirmed_active_pursuit"], 0)
        self.assertEqual(report["outcome_correlation"]["rows"], [])


class TestDispositionCounts(_IsolatedCalibrationMixin):
    def test_counts_grouped_by_item_type_and_disposition(self):
        self._write_resolutions({
            "iaq-1": {"queue_id": "iaq-1", "item_type": "buying_window_hypothesis",
                      "entity": "Toast", "entity_id": "vendor-toast", "disposition": "accepted"},
            "iaq-2": {"queue_id": "iaq-2", "item_type": "buying_window_hypothesis",
                      "entity": "Olo", "entity_id": "vendor-olo", "disposition": "rejected"},
            "iaq-3": {"queue_id": "iaq-3", "item_type": "competitor_review",
                      "entity": "ncr", "entity_id": None, "disposition": "deferred"},
        })
        report = calib.build()
        self.assertEqual(report["total_resolutions"], 3)
        self.assertEqual(report["disposition_counts_by_item_type"]["buying_window_hypothesis"],
                          {"accepted": 1, "rejected": 1, "deferred": 0})
        self.assertEqual(report["disposition_counts_by_item_type"]["competitor_review"],
                          {"accepted": 0, "rejected": 0, "deferred": 1})

    def test_unknown_disposition_counted_in_total_but_not_in_any_bucket(self):
        """Defensive: a malformed/legacy resolution record must not crash
        the report or silently inflate a real bucket."""
        self._write_resolutions({
            "iaq-1": {"queue_id": "iaq-1", "item_type": "buying_window_hypothesis",
                      "entity": "Toast", "entity_id": "vendor-toast", "disposition": "maybe"},
        })
        report = calib.build()
        self.assertEqual(report["total_resolutions"], 1)
        self.assertEqual(report["disposition_counts_by_item_type"]["buying_window_hypothesis"],
                          {"accepted": 0, "rejected": 0, "deferred": 0})


class TestOutcomeCorrelation(_IsolatedCalibrationMixin):
    def test_accepted_buying_window_hypothesis_confirmed_active_pursuit(self):
        self._write_resolutions({
            "iaq-1": {"queue_id": "iaq-1", "item_type": "buying_window_hypothesis",
                      "entity": "Toast", "entity_id": "vendor-toast", "disposition": "accepted",
                      "resolved_at": "2026-09-15T10:00:00Z"},
        })
        self._write_radar_state({"vendor-toast": {"outcome": "active_pursuit"}})
        report = calib.build()
        rows = report["outcome_correlation"]["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["real_outcome"], "active_pursuit")
        self.assertEqual(report["outcome_correlation"]["accepted_with_known_outcome"], 1)
        self.assertEqual(report["outcome_correlation"]["accepted_confirmed_active_pursuit"], 1)

    def test_accepted_but_still_unresolved_not_counted_as_confirmed(self):
        self._write_resolutions({
            "iaq-1": {"queue_id": "iaq-1", "item_type": "buying_window_hypothesis",
                      "entity": "Toast", "entity_id": "vendor-toast", "disposition": "accepted"},
        })
        self._write_radar_state({"vendor-toast": {"outcome": "unresolved"}})
        report = calib.build()
        self.assertEqual(report["outcome_correlation"]["accepted_with_known_outcome"], 1)
        self.assertEqual(report["outcome_correlation"]["accepted_confirmed_active_pursuit"], 0)

    def test_entity_id_none_falls_back_to_entity_name_lookup(self):
        """Live incident this exists for: Yum Brands' real radar hypothesis
        carries entity_id=None, and sales_opportunity_radar._calibrate()
        keys its own state dict by entity NAME in that case. Looking up
        only by entity_id would silently miss this and every case like it."""
        self._write_resolutions({
            "iaq-1": {"queue_id": "iaq-1", "item_type": "buying_window_hypothesis",
                      "entity": "Yum Brands", "entity_id": None, "disposition": "accepted"},
        })
        self._write_radar_state({"Yum Brands": {"outcome": "active_pursuit"}})
        report = calib.build()
        self.assertEqual(report["outcome_correlation"]["accepted_confirmed_active_pursuit"], 1)

    def test_rejected_items_excluded_from_confirmed_count(self):
        self._write_resolutions({
            "iaq-1": {"queue_id": "iaq-1", "item_type": "buying_window_hypothesis",
                      "entity": "Toast", "entity_id": "vendor-toast", "disposition": "rejected"},
        })
        self._write_radar_state({"vendor-toast": {"outcome": "active_pursuit"}})
        report = calib.build()
        self.assertEqual(report["outcome_correlation"]["accepted_with_known_outcome"], 0)
        self.assertEqual(report["outcome_correlation"]["accepted_confirmed_active_pursuit"], 0)

    def test_non_correlatable_item_type_never_appears_in_outcome_rows(self):
        self._write_resolutions({
            "iaq-1": {"queue_id": "iaq-1", "item_type": "competitor_review",
                      "entity": "ncr", "entity_id": "vendor-ncr-voyix", "disposition": "accepted"},
            "iaq-2": {"queue_id": "iaq-2", "item_type": "first_party_page_change",
                      "entity": "PAR Technology", "entity_id": "vendor-par-technology", "disposition": "accepted"},
        })
        self._write_radar_state({
            "vendor-ncr-voyix": {"outcome": "active_pursuit"},
            "vendor-par-technology": {"outcome": "active_pursuit"},
        })
        report = calib.build()
        self.assertEqual(report["outcome_correlation"]["rows"], [])
        self.assertEqual(report["outcome_correlation"]["accepted_with_known_outcome"], 0)

    def test_entity_never_seen_by_radar_reports_none_not_a_crash(self):
        self._write_resolutions({
            "iaq-1": {"queue_id": "iaq-1", "item_type": "buying_window_hypothesis",
                      "entity": "Some New Brand", "entity_id": "brand-some-new-brand", "disposition": "accepted"},
        })
        self._write_radar_state({})
        report = calib.build()
        self.assertEqual(report["outcome_correlation"]["rows"][0]["real_outcome"], None)
        self.assertEqual(report["outcome_correlation"]["accepted_confirmed_active_pursuit"], 0)


if __name__ == "__main__":
    unittest.main()
