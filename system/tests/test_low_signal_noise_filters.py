"""
test_low_signal_noise_filters.py

RB-DEFECT-2026-07-20 (Phase 1.4): two low-signal noise categories reached
the Intelligence Brief with no CoS-relevant content:

1. "What Changed Today"'s email-activity line showed subject/sender pairs
   with zero business signal -- "12th straight Red Sox win might be
   wildest yet — MLB Morning Lineup" and "Complimentary August LinkedIn
   Masterclass-update — Success Champions" -- neither sender was covered by
   the existing _MUTED_EMAIL_SENDERS list (built for devotionals, retail
   deals, and job alerts, not sports digests or webinar marketing).
2. "D+: Newsletter Inbox" rendered a LinkedIn "invitations to connect"
   digest as if it were a real industry newsletter. It has zero article
   links, but the connection-request profile bios it lists are often packed
   with restaurant-tech buzzwords ("QSR & Restaurant Technology | Digital
   Ordering..."), which satisfied the relevance-keyword gate even though
   the email itself carries no industry news.

Fixed (Phase 1): extended _MUTED_EMAIL_SENDERS with generalizable keyword
patterns ("morning lineup", "masterclass", "webinar invit") for (1), and
added _is_low_signal_newsletter() -- checked before the relevance gate --
for (2).

Phase 3 (2026-07-20): layered llm_assist.is_low_signal() on top of both
keyword checks for the ambiguous middle ground a fixed list can't reach.
Every test in this file explicitly mocks llm_assist -- OPENAI_API_KEY
happening to be unset in the test environment is not something to rely on
for determinism (see TestLlmRelevanceLayer for the cases that actually
exercise the mocked LLM path).
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402


def _email_activity_item(summary: str) -> dict:
    return {
        "title": "Email activity",
        "summary": summary,
        "extras": {"delta_source": "email"},
    }


class TestWhatChangedTodayMutesLowSignalSenders(unittest.TestCase):
    """llm_assist mocked to None throughout -- these lock in the keyword-
    only behavior regardless of whether the LLM layer is configured."""

    def setUp(self):
        self._patch = patch.object(rib.llm_assist, "is_low_signal", return_value=None)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_sports_digest_muted(self):
        sections = {"personal_intelligence_delta": [
            _email_activity_item(
                "12th straight Red Sox win might be wildest yet — MLB Morning Lineup"
            ),
        ]}
        out = rib._render_what_changed(sections, date(2026, 7, 20))
        self.assertNotIn("Red Sox", out)

    def test_webinar_masterclass_marketing_muted(self):
        sections = {"personal_intelligence_delta": [
            _email_activity_item(
                "Complimentary August LinkedIn Masterclass-update — Success Champions"
            ),
        ]}
        out = rib._render_what_changed(sections, date(2026, 7, 20))
        self.assertNotIn("Masterclass", out)

    def test_genuine_business_email_still_shown(self):
        sections = {"personal_intelligence_delta": [
            _email_activity_item(
                "Q3 renewal terms — Ryan Hildebrand"
            ),
        ]}
        out = rib._render_what_changed(sections, date(2026, 7, 20))
        self.assertIn("Ryan Hildebrand", out)

    def test_mixed_summary_keeps_business_drops_noise(self):
        sections = {"personal_intelligence_delta": [
            _email_activity_item(
                "12th straight Red Sox win might be wildest yet — MLB Morning Lineup; "
                "Q3 renewal terms — Ryan Hildebrand"
            ),
        ]}
        out = rib._render_what_changed(sections, date(2026, 7, 20))
        self.assertIn("Ryan Hildebrand", out)
        self.assertNotIn("Red Sox", out)


class TestLowSignalNewsletterFilter(unittest.TestCase):
    def setUp(self):
        self._patch = patch.object(rib.llm_assist, "is_low_signal", return_value=None)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_linkedin_connection_digest_rejected(self):
        item = {
            "title": "LinkedIn",
            "extras": {"body_summary": (
                "Here's a summary of your latest invitations to connect. "
                "Robert Delmont Client Success Leader | QSR & Restaurant Technology | "
                "Digital Ordering & Franchise Operations | PAR Technology 48 mutual connections"
            )},
        }
        self.assertTrue(rib._is_low_signal_newsletter(item, item["extras"]["body_summary"]))

    def test_genuine_industry_newsletter_not_rejected(self):
        item = {
            "title": "Restaurant Dive",
            "extras": {"body_summary": "Taco Bell pulls ingredients as outbreak grows."},
        }
        self.assertFalse(rib._is_low_signal_newsletter(item, item["extras"]["body_summary"]))

    def test_connection_digest_excluded_from_rendered_output(self):
        sections = {"newsletter_intelligence": [{
            "title": "LinkedIn",
            "extras": {
                "source_name": "LinkedIn",
                "pub_date": "2026-07-19",
                "articles": [],
                "body_summary": (
                    "Here's a summary of your latest invitations to connect. "
                    "Temidayo Oke Direct Booking Website Designer | Helping Airbnb & "
                    "Vacation Rental Hosts Increase Direct Bookings"
                ),
            },
        }]}
        out = rib._render_newsletter_inbox(sections)
        self.assertNotIn("invitations to connect", out)
        self.assertEqual(out, "")


class TestLlmRelevanceLayer(unittest.TestCase):
    """RB-DEFECT-2026-07-20 Phase 3: llm_assist.is_low_signal() layered on
    top of both keyword checks for the ambiguous middle ground a fixed
    pattern list can't reach -- a subject/newsletter that isn't from a
    known-muted sender or known chrome phrase, but is still pure noise."""

    def test_email_activity_llm_confirmed_noise_dropped(self):
        sections = {"personal_intelligence_delta": [
            _email_activity_item("Big exciting update from a sender we've never seen before")
        ]}
        with patch.object(rib.llm_assist, "is_low_signal", return_value=True) as mock_ls:
            out = rib._render_what_changed(sections, date(2026, 7, 20))
        mock_ls.assert_called_once()
        self.assertIn("no notable subjects in preview window", out)

    def test_email_activity_llm_confirmed_business_kept(self):
        sections = {"personal_intelligence_delta": [
            _email_activity_item("Territory sync notes — a sender we've never seen before")
        ]}
        with patch.object(rib.llm_assist, "is_low_signal", return_value=False) as mock_ls:
            out = rib._render_what_changed(sections, date(2026, 7, 20))
        mock_ls.assert_called_once()
        self.assertIn("Territory sync notes", out)

    def test_email_activity_llm_unavailable_keeps_keyword_only_behavior(self):
        sections = {"personal_intelligence_delta": [
            _email_activity_item("Territory sync notes — a sender we've never seen before")
        ]}
        with patch.object(rib.llm_assist, "is_low_signal", return_value=None):
            out = rib._render_what_changed(sections, date(2026, 7, 20))
        self.assertIn("Territory sync notes", out)

    def test_newsletter_llm_confirmed_noise_rejected(self):
        item = {"title": "Some Newsletter", "extras": {"body_summary": "Ambiguous filler content."}}
        with patch.object(rib.llm_assist, "is_low_signal", return_value=True) as mock_ls:
            result = rib._is_low_signal_newsletter(item, item["extras"]["body_summary"])
        mock_ls.assert_called_once()
        self.assertTrue(result)

    def test_newsletter_llm_confirmed_relevant_kept(self):
        item = {"title": "Some Newsletter", "extras": {"body_summary": "Ambiguous filler content."}}
        with patch.object(rib.llm_assist, "is_low_signal", return_value=False) as mock_ls:
            result = rib._is_low_signal_newsletter(item, item["extras"]["body_summary"])
        mock_ls.assert_called_once()
        self.assertFalse(result)

    def test_newsletter_keyword_match_short_circuits_before_llm_call(self):
        """A keyword-list hit must not spend an API call at all."""
        item = {
            "title": "LinkedIn",
            "extras": {"body_summary": "Here's a summary of your latest invitations to connect."},
        }
        with patch.object(rib.llm_assist, "is_low_signal") as mock_ls:
            result = rib._is_low_signal_newsletter(item, item["extras"]["body_summary"])
        self.assertTrue(result)
        mock_ls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
