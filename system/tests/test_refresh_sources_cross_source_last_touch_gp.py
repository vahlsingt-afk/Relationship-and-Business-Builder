"""
test_refresh_sources_cross_source_last_touch_gp.py

RB-DEFECT-2026-09-18 (system/CLAUDE_HANDOFF_INTELLIGENCE_CYCLE_REPAIR_2026-09-18.md,
"GP Outlook manual scan" evidence): refresh_sources.apply_cross_source_last_touch()
hardcoded its email/calendar source-file list to {personal, bridgepoint} only,
so a GP (global-payments) Outlook capture -- however fresh, however much real
interaction content it carried -- could never advance a contact's baseline
last_touch through cross-source reconciliation. This is distinct from
interaction_event_ledger.py, which globs INBOX_DIR for email*.json/
calendar*.json and therefore already picked GP files up correctly; this one
function is the one place that had the account name hardcoded.

Covers: a GP email participant with a date newer than the contact's current
last_touch now produces an applied cross-source update, exactly like the
existing personal/bridgepoint sources already did.
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

import refresh_sources as rs  # noqa: E402
import rb_core as core  # noqa: E402


class TestCrossSourceLastTouchGP(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="refresh_sources_gp_test_"))
        self.baseline = [{
            "id": "jane-procurement",
            "name": "Jane Procurement",
            "email": "jane.procurement@globalpayments.com",
            "signal_class": "RC",
            "last_touch": "2026-08-01",
            "notes": "",
        }]

    def _write_gp_email(self, *, msg_date: str) -> None:
        (self.tmp / "email.global-payments.json").write_text(json.dumps({
            "emails": [{
                "date": msg_date,
                "from": "todd@bridgepointops.org",
                "to": ["jane.procurement@globalpayments.com"],
            }]
        }), encoding="utf-8")

    def test_gp_email_source_included_in_cross_source_reconciliation(self):
        self._write_gp_email(msg_date="2026-09-16")
        with patch.object(core, "INBOX_DIR", self.tmp), \
             patch.object(core, "load_baseline", return_value=self.baseline), \
             patch.object(core, "BASELINE_PATH", self.tmp / "baseline_index.json"):
            result = rs.apply_cross_source_last_touch()

        self.assertEqual(result["status"], rs.STATUS_REFRESHED)
        self.assertEqual(result["applied"], 1, result)
        update = result["updates"][0]
        self.assertEqual(update["id"], "jane-procurement")
        self.assertEqual(update["old_last_touch"], "2026-08-01")
        self.assertEqual(update["new_last_touch"], "2026-09-16")
        self.assertIn("global-payments", update["source"])
        self.assertEqual(self.baseline[0]["last_touch"], "2026-09-16")

    def test_gp_email_older_than_current_last_touch_is_skipped(self):
        self._write_gp_email(msg_date="2026-07-01")  # older than 2026-08-01
        with patch.object(core, "INBOX_DIR", self.tmp), \
             patch.object(core, "load_baseline", return_value=self.baseline), \
             patch.object(core, "BASELINE_PATH", self.tmp / "baseline_index.json"):
            result = rs.apply_cross_source_last_touch()

        self.assertEqual(result["applied"], 0, result)
        self.assertEqual(self.baseline[0]["last_touch"], "2026-08-01")

    def test_operator_confirmed_contact_never_overwritten(self):
        """RB-DEFECT-2026-09-18: apply_cross_source_last_touch()'s own
        docstring has always promised 'Never overwrites operator-confirmed
        last_touch entries with lower-confidence sources' -- nothing
        actually enforced that until this fix. Reuses the exact same
        notes-text marker convention linkedin_ingest.py's
        _is_operator_confirmed() already uses for employment-state
        conflicts."""
        self.baseline[0]["notes"] = "2026-08-01: last_touch confirmed by Todd after a live call."
        self._write_gp_email(msg_date="2026-09-16")  # newer than current, would otherwise apply
        with patch.object(core, "INBOX_DIR", self.tmp), \
             patch.object(core, "load_baseline", return_value=self.baseline), \
             patch.object(core, "BASELINE_PATH", self.tmp / "baseline_index.json"):
            result = rs.apply_cross_source_last_touch()

        self.assertEqual(result["applied"], 0, result)
        self.assertEqual(self.baseline[0]["last_touch"], "2026-08-01")

    def test_operator_confirmed_contact_skipped_while_others_still_apply(self):
        confirmed_contact = dict(self.baseline[0])
        confirmed_contact["notes"] = "confirmed by operator"
        other_contact = {
            "id": "other-contact", "name": "Other Contact",
            "email": "other.contact@globalpayments.com",
            "signal_class": "RC", "last_touch": "2026-08-01", "notes": "",
        }
        self.baseline = [confirmed_contact, other_contact]
        (self.tmp / "email.global-payments.json").write_text(json.dumps({
            "emails": [
                {"date": "2026-09-16", "from": "todd@bridgepointops.org",
                 "to": ["jane.procurement@globalpayments.com"]},
                {"date": "2026-09-16", "from": "todd@bridgepointops.org",
                 "to": ["other.contact@globalpayments.com"]},
            ]
        }), encoding="utf-8")

        with patch.object(core, "INBOX_DIR", self.tmp), \
             patch.object(core, "load_baseline", return_value=self.baseline), \
             patch.object(core, "BASELINE_PATH", self.tmp / "baseline_index.json"):
            result = rs.apply_cross_source_last_touch()

        self.assertEqual(result["applied"], 1, result)
        self.assertEqual(confirmed_contact["last_touch"], "2026-08-01")
        self.assertEqual(other_contact["last_touch"], "2026-09-16")


if __name__ == "__main__":
    unittest.main()
