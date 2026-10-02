"""
test_calendar_overlay_triage.py — RB 9.88 (RB-DEFECT-046 Slice 1: universal router).

core.calendar_overlay() now triages title+description for today/tomorrow
events via intelligence_triage.triage_overlay_text(), appending non-noise
results to a new additive `triage_signals` field.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import rb_core as core  # noqa: E402


FAKE_BASELINE = [
    {"id": "p-jane", "name": "Jane Doe", "email": "jane@example.org",
     "signal_class": "rc", "rc_tier": "outer"},
]

FAKE_ACTIVE_THREADS: list[dict] = []

TODAY = date(2026, 6, 15)


class TestCalendarOverlayTriageSignals(unittest.TestCase):
    def setUp(self):
        self._orig_load_calendar = core.load_calendar
        self._orig_load_baseline = core.load_baseline
        self._orig_load_active_threads = core.load_active_threads
        self._orig_self_emails = core.self_emails
        self._orig_is_stale = core.is_overlay_stale
        self._orig_source_readiness = core.source_readiness
        core.load_baseline = lambda: FAKE_BASELINE  # type: ignore
        core.load_active_threads = lambda: FAKE_ACTIVE_THREADS  # type: ignore
        core.self_emails = lambda: {"todd@example.com"}  # type: ignore
        core.is_overlay_stale = lambda payload: False  # type: ignore
        core.source_readiness = lambda **kw: {"status": "ok"}  # type: ignore

    def tearDown(self):
        core.load_calendar = self._orig_load_calendar
        core.load_baseline = self._orig_load_baseline
        core.load_active_threads = self._orig_load_active_threads
        core.self_emails = self._orig_self_emails
        core.is_overlay_stale = self._orig_is_stale
        core.source_readiness = self._orig_source_readiness

    def test_noise_event_produces_no_triage_signal(self):
        core.load_calendar = lambda: {  # type: ignore
            "fetched_at": "2026-06-15T08:00:00Z",
            "accounts_seen": ["primary"],
            "events": [
                {
                    "id": "E-noise",
                    "title": "Lunch with Jane",
                    "description": "Catching up over lunch.",
                    "start": "2026-06-15T12:00:00Z",
                    "end": "2026-06-15T13:00:00Z",
                    "attendees": [{"email": "jane@example.org", "name": "Jane Doe"}],
                    "account_id": "primary",
                    "account_label": "Primary",
                },
            ],
        }
        overlay = core.calendar_overlay(TODAY)
        self.assertIn("triage_signals", overlay)
        self.assertEqual(overlay["triage_signals"], [])
        self.assertEqual(len(overlay["today"]), 1)

    def test_intelligence_bearing_event_produces_triage_signal(self):
        core.load_calendar = lambda: {  # type: ignore
            "fetched_at": "2026-06-15T08:00:00Z",
            "accounts_seen": ["primary"],
            "events": [
                {
                    "id": "E-intel",
                    "title": "PAR Technology Q3 earnings review",
                    "description": (
                        "PAR Technology Q3 earnings: comp sales +4%, "
                        "1,200 operator customers."
                    ),
                    "start": "2026-06-15T15:00:00Z",
                    "end": "2026-06-15T16:00:00Z",
                    "attendees": [{"email": "jane@example.org", "name": "Jane Doe"}],
                    "account_id": "primary",
                    "account_label": "Primary",
                },
            ],
        }
        overlay = core.calendar_overlay(TODAY)
        self.assertEqual(len(overlay["triage_signals"]), 1)
        sig = overlay["triage_signals"][0]
        self.assertEqual(sig["event_id"], "E-intel")
        self.assertFalse(sig["triage"]["noise_only"])

    def test_event_declined_by_every_self_address_is_excluded(self):
        core.load_calendar = lambda: {  # type: ignore
            "fetched_at": "2026-06-15T08:00:00Z",
            "accounts_seen": ["primary"],
            "events": [{
                "id": "E-declined",
                "title": "SCN Guest Invitation Global Connections",
                "start": "2026-06-15T15:00:00Z",
                "end": "2026-06-15T16:00:00Z",
                "attendees": [
                    {"email": "todd@example.com", "self": True, "response": "declined"},
                    {"email": "jane@example.org", "response": "accepted"},
                ],
            }],
        }
        overlay = core.calendar_overlay(TODAY)
        self.assertEqual(overlay["today"], [])
        self.assertEqual(overlay["triage_signals"], [])
        self.assertEqual(overlay["attendees_not_in_baseline"], [])


if __name__ == "__main__":
    unittest.main()
