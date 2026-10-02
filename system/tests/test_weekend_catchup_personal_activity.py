"""
test_weekend_catchup_personal_activity.py

RB-DEFECT-2026-07-27: the Daily Brief's Monday "Weekend catch-up" block
(_load_weekend_intel_summary) used to scrape B: National Headlines and
I: Strategic Signals out of the past 2 days' Intelligence Briefs -- generic
world/macro news with no personal or business tie, already covered (and
better contextualized) there. Assessed for noise/overlap with the
Intelligence Brief: "Weekend catch-up" is a CoS conversation, not a second
news digest -- it should say what happened in Todd's world over the
weekend, not repeat headlines already sent. Replaced with a direct read of
weekend (Saturday+Sunday) email/SMS activity, the same underlying data the
Intelligence Brief's own "What Changed Today" line draws from.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_daily_brief as rdb  # noqa: E402
import rb_core as core  # noqa: E402

# A Monday -- the only weekday this function does anything.
_MONDAY = date(2026, 7, 27)

_FAKE_BASELINE = [
    {"id": "savi", "name": "Saverio Ferraro", "current_company": "ZagOps",
     "email": "savi@zagops.com"},
]


def _thread(sender_email: str, sender_name: str, when: datetime) -> dict:
    return {
        "thread_id": f"t-{sender_email}",
        "subject": "Hi",
        "last_message_at": when.isoformat(),
        "last_message_from": {"email": sender_email, "name": sender_name},
        "labels": [],
    }


class TestWeekendCatchupIsPersonalActivityNotNews(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.inbox_dir = Path(self.tmpdir.name)
        self._patches = [
            patch.object(core, "INBOX_DIR", self.inbox_dir),
            patch.object(core, "MESSAGES_PATH", self.inbox_dir / "messages.json"),
            patch.object(rdb.core, "load_baseline", return_value=_FAKE_BASELINE),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmpdir.cleanup()

    def _write_email(self, threads: list[dict]) -> None:
        (self.inbox_dir / "email.personal.json").write_text(json.dumps({"threads": threads}))
        (self.inbox_dir / "email.bridgepoint.json").write_text(json.dumps({"threads": []}))

    def _write_sms(self, events: list[dict]) -> None:
        (self.inbox_dir / "messages.json").write_text(json.dumps({"events": events}))

    def test_non_monday_returns_nothing(self):
        self._write_email([_thread("x@example.com", "X", datetime.now(timezone.utc))])
        out = rdb._load_weekend_intel_summary(date(2026, 7, 28))  # Tuesday
        self.assertEqual(out, [])

    def test_weekend_email_thread_count_surfaced(self):
        saturday = datetime(_MONDAY.year, _MONDAY.month, _MONDAY.day, 10, tzinfo=timezone.utc) - timedelta(days=2)
        self._write_email([
            _thread("a@example.com", "A", saturday),
            _thread("b@example.com", "B", saturday + timedelta(hours=2)),
        ])
        out = rdb._load_weekend_intel_summary(_MONDAY)
        self.assertTrue(any("2 threads over the weekend" in b for b in out))

    def test_weekday_email_before_weekend_window_excluded(self):
        """Friday's mail (before the Saturday/Sunday window) must not count
        as weekend activity."""
        friday = datetime(_MONDAY.year, _MONDAY.month, _MONDAY.day, 10, tzinfo=timezone.utc) - timedelta(days=3)
        self._write_email([_thread("a@example.com", "A", friday)])
        out = rdb._load_weekend_intel_summary(_MONDAY)
        self.assertEqual(out, [])

    def test_known_baseline_sender_named(self):
        saturday = datetime(_MONDAY.year, _MONDAY.month, _MONDAY.day, 10, tzinfo=timezone.utc) - timedelta(days=2)
        self._write_email([_thread("savi@zagops.com", "Saverio Ferraro", saturday)])
        out = rdb._load_weekend_intel_summary(_MONDAY)
        self.assertTrue(any("Saverio Ferraro" in b for b in out))

    def test_weekend_sms_contact_count_surfaced(self):
        saturday = datetime(_MONDAY.year, _MONDAY.month, _MONDAY.day, 10, tzinfo=timezone.utc) - timedelta(days=2)
        self._write_sms([
            {"handle": "+15551234567", "at": saturday.isoformat()},
            {"handle": "+15559876543", "at": (saturday + timedelta(hours=1)).isoformat()},
        ])
        out = rdb._load_weekend_intel_summary(_MONDAY)
        self.assertTrue(any("2 contacts active over the weekend" in b for b in out))

    def test_quiet_weekend_returns_no_bullets(self):
        out = rdb._load_weekend_intel_summary(_MONDAY)
        self.assertEqual(out, [])

    def test_no_generic_world_news_scraped_from_intelligence_briefs(self):
        """Regression guard for the actual defect: this must never again
        pull content out of past Intelligence Brief markdown files."""
        import inspect
        src = inspect.getsource(rdb._load_weekend_intel_summary)
        self.assertNotIn("BRIEFS_DIR", src)
        self.assertNotIn("intelligence-brief.md", src)


if __name__ == "__main__":
    unittest.main()
