"""
test_refresh_sources_sms_last_touch_operator_confirmed.py

RB-DEFECT-2026-09-18 follow-up: apply_cross_source_last_touch() was fixed to
honor operator-confirmed contacts (never silently overwrite a last_touch a
human already confirmed), but its sibling apply_sms_last_touch_updates()
had the exact same gap and was left unfixed at the time -- same missing
guarantee, same notes-text convention, just not yet applied to the SMS path.
Closed here using the now-shared refresh_sources._is_operator_confirmed()
helper (previously duplicated as a local closure inside
apply_cross_source_last_touch(), now promoted to module level so both
last-touch paths share the same check).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import refresh_sources as rs  # noqa: E402
import rb_core as core  # noqa: E402
import direct_comms_health as dch  # noqa: E402


def _sms_candidate(contact_id: str, *, days_ago: int) -> dict:
    event_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return {"contact_id": contact_id, "event_at": event_at}


class TestSmsLastTouchOperatorConfirmed(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="refresh_sources_sms_test_"))
        self.baseline_path = self.tmp / "baseline_index.json"

    def _run(self, baseline: list[dict], candidates: list[dict]):
        with patch.object(core, "load_baseline", return_value=baseline), \
             patch.object(core, "BASELINE_PATH", self.baseline_path), \
             patch.object(dch, "check_messages_readiness",
                           return_value={"state": "available_fresh", "ri_candidates": candidates}), \
             patch("mutations.snapshot", return_value="snap"):
            return rs.apply_sms_last_touch_updates(min_age_hours=24.0)

    def test_operator_confirmed_contact_not_overwritten_by_sms(self):
        baseline = [{
            "id": "jane-doe", "name": "Jane Doe", "signal_class": "RC",
            "last_touch": "2026-08-01",
            "notes": "confirmed by todd -- last_touch is accurate, do not auto-update.",
        }]
        result = self._run(baseline, [_sms_candidate("jane-doe", days_ago=2)])

        self.assertEqual(result["applied"], 0, result)
        self.assertEqual(baseline[0]["last_touch"], "2026-08-01")

    def test_operator_confirmed_contact_skipped_while_others_still_apply(self):
        confirmed = {
            "id": "jane-doe", "name": "Jane Doe", "signal_class": "RC",
            "last_touch": "2026-08-01",
            "notes": "confirmed by operator.",
        }
        other = {
            "id": "john-roe", "name": "John Roe", "signal_class": "RC",
            "last_touch": "2026-08-01", "notes": "",
        }
        baseline = [confirmed, other]
        candidates = [
            _sms_candidate("jane-doe", days_ago=2),
            _sms_candidate("john-roe", days_ago=2),
        ]
        result = self._run(baseline, candidates)

        self.assertEqual(result["applied"], 1, result)
        self.assertEqual(confirmed["last_touch"], "2026-08-01")
        self.assertNotEqual(other["last_touch"], "2026-08-01")
        # `updates` only lists applied entries; the confirmed contact's
        # absence from it, alongside its unmodified last_touch above, is
        # the proof it was skipped rather than silently overwritten.
        self.assertEqual(len(result["updates"]), 1)
        self.assertEqual(result["updates"][0]["contact_id"], "john-roe")


if __name__ == "__main__":
    unittest.main()
