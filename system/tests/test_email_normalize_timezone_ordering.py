"""
test_email_normalize_timezone_ordering.py

Regression coverage: normalize_email() picked the thread's "last_message_*"
via `max(msgs, key=lambda m: m.get("date") or "")` -- a lexicographic
STRING comparison of raw RFC 2822 Date headers. That only produces the
chronologically-last message when every message in the thread shares the
same UTC offset. The moment two messages differ (extremely common --
different email clients/timezones), the comparison breaks silently.

Confirmed live: a bridgepoint thread with Todd's reply at
"Mon, 13 Jul 2026 21:30:18 +0000" and Erika Till's genuinely later reply at
"Mon, 13 Jul 2026 17:39:12 -0400" (21:39:12 UTC -- nine minutes after
Todd's) sorted Todd's message as "last" purely because "21" > "17" as
characters, even though -0400 makes Erika's message the true latest. This
silently hid her reply from What Changed Today / the Communication Queue,
making it look like Todd never got a response.

Fixed by parsing each Date header to an aware datetime (email.utils.
parsedate_to_datetime) before comparing.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import fetch_via_session as fvs  # noqa: E402


def _msg(date: str, sender: str, labels: list[str], snippet: str = "") -> dict:
    return {
        "date": date,
        "sender": sender,
        "subject": "Re: Catch up & startup question",
        "labelIds": labels,
        "snippet": snippet,
        "toRecipients": [],
    }


class TestNormalizeEmailTimezoneOrdering(unittest.TestCase):
    def test_later_message_with_different_utc_offset_wins(self):
        """The exact live bug: a -0400 reply genuinely nine minutes after a
        +0000 message must still sort as 'last', even though '17' < '21' as
        raw characters."""
        raw = {"threads": [{
            "id": "t1",
            "messages": [
                _msg("Fri, 10 Jul 2026 17:04:11 -0400", "Erika Till <erika@riselightco.com>", ["INBOX"]),
                _msg("Mon, 13 Jul 2026 21:29:15 +0000", "todd@bridgepointops.com", ["DRAFT"]),
                _msg("Mon, 13 Jul 2026 21:30:18 +0000", "Todd Vahlsing <todd@bridgepointops.com>", ["SENT"]),
                _msg("Mon, 13 Jul 2026 17:39:12 -0400", "Erika Till <erika@riselightco.com>", ["IMPORTANT", "INBOX"],
                     "No worries at all on the delay..."),
            ],
        }]}
        out = fvs.normalize_email(raw)
        thread = out["threads"][0]
        self.assertEqual(thread["last_message_from"]["email"], "erika@riselightco.com")
        self.assertIn("No worries", thread["snippet"])

    def test_same_offset_thread_still_sorts_correctly(self):
        """Baseline: when every message shares the same UTC offset, the
        true latest message must still win (no regression on the common
        case)."""
        raw = {"threads": [{
            "id": "t2",
            "messages": [
                _msg("Mon, 13 Jul 2026 09:00:00 +0000", "a@example.com", ["INBOX"], "first"),
                _msg("Mon, 13 Jul 2026 15:00:00 +0000", "b@example.com", ["INBOX"], "second"),
                _msg("Mon, 13 Jul 2026 12:00:00 +0000", "c@example.com", ["INBOX"], "middle"),
            ],
        }]}
        out = fvs.normalize_email(raw)
        self.assertEqual(out["threads"][0]["snippet"], "second")

    def test_unparseable_date_does_not_crash_and_does_not_win(self):
        raw = {"threads": [{
            "id": "t3",
            "messages": [
                _msg("not a real date", "a@example.com", ["INBOX"], "garbage-date"),
                _msg("Mon, 13 Jul 2026 09:00:00 +0000", "b@example.com", ["INBOX"], "real"),
            ],
        }]}
        out = fvs.normalize_email(raw)
        self.assertEqual(out["threads"][0]["snippet"], "real")

    def test_naive_date_without_timezone_still_sorts(self):
        """Some clients omit a UTC offset entirely -- must not crash."""
        raw = {"threads": [{
            "id": "t4",
            "messages": [
                _msg("Mon, 13 Jul 2026 09:00:00", "a@example.com", ["INBOX"], "no-offset-earlier"),
                _msg("Mon, 13 Jul 2026 15:00:00", "b@example.com", ["INBOX"], "no-offset-later"),
            ],
        }]}
        out = fvs.normalize_email(raw)
        self.assertEqual(out["threads"][0]["snippet"], "no-offset-later")


if __name__ == "__main__":
    unittest.main()
