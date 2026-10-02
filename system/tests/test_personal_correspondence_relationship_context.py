"""
test_personal_correspondence_relationship_context.py

Regression coverage for two RB-DEFECT-2026-07-08 issues in "What Changed
Today"'s personal-correspondence delta:

1. Gmail's CATEGORY_PERSONAL label means "landed in the Primary inbox tab"
   -- it does NOT mean "personal in the human sense." A startup CEO pitching
   a funding round (Saverio Ferraro, ZagOps) and a colleague at the user's
   own employer (Ryan Hildebrand, Global Payments) were both rendered as
   "Personal correspondence" / "genuine 1:1 correspondence, not bulk mail,"
   which misrepresents both the content and the relationship. The sender is
   now looked up against the baseline and relabeled by actual relationship
   context: colleague at the user's employer, business/networking contact
   (has a current_company on file), or genuinely personal (no match).

2. A calendar/meeting-invite forward ("You are invited to a Microsoft Teams
   meeting... click here to join") carries zero informational content, but
   one from a personal contact still landed in CATEGORY_PERSONAL + IMPORTANT
   and got surfaced as "genuine 1:1 correspondence" worth reviewing. It's
   calendar logistics, not correspondence -- excluded outright now.
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

_FAKE_BASELINE = [
    {"id": "saverio-ferraro", "name": "Saverio Ferraro", "current_company": "ZagOps",
     "current_role": "Chief Executive Officer", "email": None},
    {"id": "ryan-hildebrand", "name": "Ryan Hildebrand", "current_company": "Global Payments Inc.",
     "current_role": "Senior Director Account Executives", "email": "ryan.hildebrand1@yahoo.com"},
    # RB-DEFECT-2026-07-23: a real HubSpot CRM contact (Ed Garner,
    # ed.garner@gomaps.com) ingested with current_company left null.
    {"id": "ed-garner", "name": "Ed Garner", "current_company": None,
     "current_role": None, "email": "ed.garner@gomaps.com", "signal_class": "VC"},
]

_FAKE_EMPLOYER_PROFILE = {"company": "Global Payments Inc. / Genius"}


def _thread(subject: str, snippet: str, sender_name: str, sender_email: str, recent) -> dict:
    return {
        "subject": subject,
        "snippet": snippet,
        "last_message_at": recent,
        "last_message_from": {"name": sender_name, "email": sender_email},
        "labels": ["CATEGORY_PERSONAL", "IMPORTANT", "INBOX"],
    }


class TestPersonalCorrespondenceRelationshipContext(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.inbox_dir = Path(self.tmpdir.name)
        self._patches = [
            patch.object(core, "INBOX_DIR", self.inbox_dir),
            patch.object(db.core, "load_baseline", return_value=_FAKE_BASELINE),
        ]
        if db.compliance_engine is not None:
            self._patches.append(
                patch.object(db.compliance_engine, "active_primary_employer_id", return_value="global-payments")
            )
            self._patches.append(
                patch.object(db.compliance_engine, "load_employer_profile", return_value=_FAKE_EMPLOYER_PROFILE)
            )
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmpdir.cleanup()

    def _write_threads(self, threads: list[dict]) -> None:
        (self.inbox_dir / "email.personal.json").write_text(json.dumps({"threads": threads}))
        (self.inbox_dir / "email.bridgepoint.json").write_text(json.dumps({"threads": []}))

    def _correspondence_items(self) -> list[dict]:
        items = db._compute_personal_intelligence_delta({}, {})
        return [i for i in items if (i.get("extras") or {}).get("delta_source") == "personal_correspondence"]

    def test_networking_contact_labeled_business_not_personal(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        self._write_threads([_thread(
            "ZagOps growth round now open!",
            "Hi Todd, opening up our growth round.",
            "Saverio Ferraro", "savi@zagops.com", recent,
        )])
        items = self._correspondence_items()
        self.assertEqual(len(items), 1)
        self.assertIn("Business correspondence", items[0]["title"])
        self.assertNotIn("Personal correspondence", items[0]["title"])
        self.assertIn("ZagOps", items[0]["why_it_matters"])

    def test_employer_colleague_labeled_colleague_not_personal(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        self._write_threads([_thread(
            "Territory planning follow-up",
            "Let's sync on the territory plan this week.",
            "Ryan Hildebrand", "ryan.hildebrand1@yahoo.com", recent,
        )])
        items = self._correspondence_items()
        self.assertEqual(len(items), 1)
        self.assertIn("Colleague correspondence", items[0]["title"])
        self.assertIn("Global Payments", items[0]["why_it_matters"])

    def test_baseline_match_with_no_company_labeled_business_not_personal(self):
        """RB-DEFECT-2026-07-23: Ed Garner is a real, active baseline
        contact (2 weeks of HubSpot CRM exports) with no current_company on
        file. He used to fall through to "Personal correspondence," which
        render_intelligence_brief.py drops from the brief entirely -- so
        his "New Time Proposed: Maps <> Bridgepoint" follow-up email never
        surfaced despite being a genuine, ongoing business thread."""
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        self._write_threads([_thread(
            "New Time Proposed: Maps <> Bridgepoint",
            "Hey, Todd. I am having shoulder surgery on 8/12 and will be out for recovery on 8/14.",
            "Ed Garner", "ed.garner@gomaps.com", recent,
        )])
        items = self._correspondence_items()
        self.assertEqual(len(items), 1)
        self.assertIn("Business correspondence", items[0]["title"])
        self.assertNotIn("Personal correspondence", items[0]["title"])

    def test_unmatched_sender_still_labeled_personal(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        self._write_threads([_thread(
            "Dinner this weekend?",
            "Want to grab dinner Saturday?",
            "Mary Vahlsing", "mary@example.com", recent,
        )])
        items = self._correspondence_items()
        self.assertEqual(len(items), 1)
        self.assertIn("Personal correspondence", items[0]["title"])

    def test_meeting_invite_boilerplate_excluded_entirely(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        self._write_threads([_thread(
            "Todd/Ryan",
            "You are invited to a Microsoft Teams meeting. To join the meeting click here: "
            "https://us-1506.join.gong.io/global-payments-us/ryan.hildebrand/123 "
            "NOTICE: This email message is for the sole use of the intended recipient.",
            "Ryan Hildebrand", "ryan.hildebrand1@yahoo.com", recent,
        )])
        items = self._correspondence_items()
        self.assertEqual(items, [])

    def test_meeting_invite_excluded_even_when_subject_is_clean(self):
        """The boilerplate can be entirely in the snippet even if the subject
        line itself looks like a normal name-only subject."""
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        self._write_threads([_thread(
            "Todd/Ryan",
            "You have been invited to a Zoom meeting. Click here to join.",
            "Ryan Hildebrand", "ryan.hildebrand1@yahoo.com", recent,
        )])
        items = self._correspondence_items()
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
