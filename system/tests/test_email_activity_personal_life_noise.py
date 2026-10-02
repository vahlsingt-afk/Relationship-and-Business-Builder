"""
test_email_activity_personal_life_noise.py

RB-DEFECT-2026-07-10: the "Email activity" bullet in "What Changed Today"
promoted personal/religious/health-care email subjects into its business-
relevant preview sample -- e.g. "Todd, confirm your visit... -- Aurora
Health Care" and "[Essential Rock Community Church] Service Reminder" --
because:

1. Gmail's CATEGORY_PERSONAL label added +3 to the business-relevance
   score, even though CATEGORY_PERSONAL means "landed in the Primary inbox
   tab," not "personally relevant to business" (same point already
   established for the personal_correspondence delta's _correspondence_label
   -- see test_personal_correspondence_relationship_context.py).
2. _BUSINESS_RELEVANCE_KEYWORDS included generic administrative words
   ("confirm", "urgent", "action required") that fire on any appointment
   reminder or admin email, with no negative signal for personal-life
   content the way _RETAIL_NOISE_SENDERS suppresses retail marketing.

Fixed by removing the CATEGORY_PERSONAL score bonus and adding
_PERSONAL_LIFE_NOISE_RE, which excludes church/religious and health-care
content from the sample outright (same suppression pattern as retail noise),
regardless of score.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402
import rb_core as core  # noqa: E402


def _thread(subject: str, sender_name: str, recent, *, unread: bool = False) -> dict:
    labels = ["INBOX", "CATEGORY_PERSONAL"]
    if unread:
        labels.append("UNREAD")
    return {
        "subject": subject,
        "snippet": "",
        "last_message_at": recent,
        "last_message_from": {"name": sender_name, "email": f"{sender_name.lower().replace(' ', '.')}@example.com"},
        "labels": labels,
    }


class TestEmailActivityPersonalLifeNoise(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.inbox_dir = Path(self.tmpdir.name)
        self._patches = [
            patch.object(core, "INBOX_DIR", self.inbox_dir),
            patch.object(db.core, "load_baseline", return_value=[]),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmpdir.cleanup()

    def _write_threads(self, threads: list[dict]) -> None:
        (self.inbox_dir / "email.personal.json").write_text(json.dumps({"threads": threads}))
        (self.inbox_dir / "email.bridgepoint.json").write_text(json.dumps({"threads": []}))

    def _email_activity_item(self) -> dict | None:
        items = db._compute_personal_intelligence_delta({}, {})
        matches = [i for i in items if (i.get("extras") or {}).get("delta_source") == "email"]
        return matches[0] if matches else None

    def test_health_care_appointment_excluded_from_sample(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        self._write_threads([
            _thread("Todd, confirm your visit on 7/10/2026", "Aurora Health Care", recent, unread=True),
        ])
        item = self._email_activity_item()
        self.assertIsNotNone(item)
        self.assertNotIn("Aurora Health Care", item["summary"])

    def test_church_service_reminder_excluded_from_sample(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        self._write_threads([
            _thread("[Essential Rock Community Church] Service Reminder", "Heather Jordan", recent, unread=True),
            _thread("Prayers and Praises", "Essential Rock Church", recent, unread=True),
        ])
        item = self._email_activity_item()
        self.assertIsNotNone(item)
        self.assertEqual(item["summary"], "(no business-relevant threads — remainder were promotional/personal/marketing)")

    def test_real_business_thread_still_promoted_over_personal_life_noise(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        self._write_threads([
            _thread("Prayers and Praises", "Essential Rock Church", recent, unread=True),
            _thread("Partnership proposal for Q3", "Jane Vendor", recent, unread=False),
        ])
        item = self._email_activity_item()
        self.assertIsNotNone(item)
        self.assertIn("Partnership proposal", item["summary"])
        self.assertNotIn("Essential Rock", item["summary"])

    def test_category_personal_label_alone_no_longer_wins_over_unlabeled_business_thread(self):
        """CATEGORY_PERSONAL used to add +3 score, letting a personal-tab
        thread with no business keywords outrank a genuine business thread
        that simply wasn't UNREAD. Neither should out-score the other on
        CATEGORY_PERSONAL alone now -- recency is the tiebreaker."""
        older = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        newer = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self._write_threads([
            _thread("Following up on our contract", "Business Partner", older, unread=False),
            _thread("Random personal note", "A Friend", newer, unread=False),
        ])
        item = self._email_activity_item()
        self.assertIsNotNone(item)
        self.assertIn("Following up on our contract", item["summary"])


if __name__ == "__main__":
    unittest.main()
