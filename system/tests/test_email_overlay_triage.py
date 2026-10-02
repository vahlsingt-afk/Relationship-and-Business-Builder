"""
test_email_overlay_triage.py — RB 9.88 (RB-DEFECT-046 Slice 1: universal router).

core.email_overlay() now triages subject+snippet for from_baseline and
active_thread_company_hits threads via intelligence_triage.triage_overlay_text(),
appending non-noise results to a new additive `triage_signals` field.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import rb_core as core  # noqa: E402


FAKE_BASELINE = [
    {"id": "p-jane", "name": "Jane Doe", "email": "jane@example.org",
     "signal_class": "rc", "rc_tier": "outer"},
]

FAKE_ACTIVE_THREADS = [
    {"id": "AT-acme", "status": "open", "title": "Acme opportunity",
     "companies": ["Acme"], "people": []},
]


class TestEmailOverlayTriageSignals(unittest.TestCase):
    def setUp(self):
        self._orig_load_email = core.load_email
        self._orig_load_baseline = core.load_baseline
        self._orig_load_active_threads = core.load_active_threads
        self._orig_self_emails = core.self_emails
        self._orig_is_stale = core.is_overlay_stale
        core.load_baseline = lambda: FAKE_BASELINE  # type: ignore
        core.load_active_threads = lambda: FAKE_ACTIVE_THREADS  # type: ignore
        core.self_emails = lambda: {"todd@example.com"}  # type: ignore
        core.is_overlay_stale = lambda payload: False  # type: ignore

    def tearDown(self):
        core.load_email = self._orig_load_email
        core.load_baseline = self._orig_load_baseline
        core.load_active_threads = self._orig_load_active_threads
        core.self_emails = self._orig_self_emails
        core.is_overlay_stale = self._orig_is_stale

    def test_noise_thread_produces_no_triage_signal(self):
        core.load_email = lambda: {  # type: ignore
            "fetched_at": "2026-06-15T08:00:00Z",
            "accounts_seen": ["primary"],
            "threads": [
                {
                    "thread_id": "T-noise",
                    "subject": "Re: lunch tomorrow?",
                    "last_message_at": "2026-06-15T07:00:00Z",
                    "last_message_from": {"name": "Jane Doe", "email": "jane@example.org"},
                    "snippet": "Sounds good, see you then.",
                    "account_id": "primary",
                    "account_label": "Primary",
                },
            ],
        }
        overlay = core.email_overlay()
        self.assertIn("triage_signals", overlay)
        self.assertEqual(overlay["triage_signals"], [])
        self.assertEqual(len(overlay["from_baseline"]), 1)

    def test_intelligence_bearing_thread_produces_triage_signal(self):
        core.load_email = lambda: {  # type: ignore
            "fetched_at": "2026-06-15T08:00:00Z",
            "accounts_seen": ["primary"],
            "threads": [
                {
                    "thread_id": "T-intel",
                    "subject": "PAR Technology Q3 earnings",
                    "last_message_at": "2026-06-15T07:00:00Z",
                    "last_message_from": {"name": "Jane Doe", "email": "jane@example.org"},
                    "snippet": (
                        "PAR Technology Q3 earnings: comp sales +4%, "
                        "1,200 operator customers."
                    ),
                    "account_id": "primary",
                    "account_label": "Primary",
                },
            ],
        }
        overlay = core.email_overlay()
        self.assertEqual(len(overlay["triage_signals"]), 1)
        sig = overlay["triage_signals"][0]
        self.assertEqual(sig["thread_id"], "T-intel")
        self.assertFalse(sig["triage"]["noise_only"])


if __name__ == "__main__":
    unittest.main()
