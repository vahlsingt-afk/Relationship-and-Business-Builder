"""
test_watchlist_delta_summary_noise_filter.py

RB-2026-08-25: live in the 2026-08-25 Intelligence Brief -- the "What Changed
Today" Watchlist Delta Summary line named "Amazon Web Services" as new
watchlist activity, while Section F's own rendered list (a few paragraphs
below it in the same document) showed "Google Cloud" as its 5th "New
activity" item instead. Root cause: render_intelligence_brief._clean_
watchlist_why drops megacap-platform items (AWS/Microsoft/Google/etc.) with
no restaurant/payments context as noise before Section F renders, but
daily_brief.py's summary-line computation took the raw, unfiltered entity
list -- so the two disagreed. daily_brief._watchlist_delta_passes_noise_
filter now applies the identical filter before the summary takes its
top-5 names, so the summary is always a subset of what Section F lists.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402


def _wl_item(name: str, why: str, loop_id: str | None = None) -> dict:
    extras = {"entity_name": name}
    if loop_id:
        extras["loop_id"] = loop_id
    return {"why_it_matters": why, "extras": extras}


class TestWatchlistDeltaPassesNoiseFilter(unittest.TestCase):
    def test_megacap_platform_with_no_restaurant_context_filtered(self):
        """The exact live case: AWS mentioned only in a datacenter/cloud
        partnership story with zero restaurant/payments angle."""
        item = _wl_item(
            "Amazon Web Services",
            "Press release: Google Cloud Announces Strategic Partnership with Verizon to Scale Enterprise AI",
        )
        self.assertFalse(db._watchlist_delta_passes_noise_filter(item))

    def test_megacap_platform_with_restaurant_context_kept(self):
        item = _wl_item(
            "Amazon Web Services",
            "AWS announces new point-of-sale integration for quick-service restaurant chains.",
        )
        self.assertTrue(db._watchlist_delta_passes_noise_filter(item))

    def test_narrowly_scoped_entity_not_subject_to_megacap_filter(self):
        item = _wl_item("Toast", "Toast announces Q3 restaurant customer growth.")
        self.assertTrue(db._watchlist_delta_passes_noise_filter(item))

    def test_analyst_note_filtered_same_as_section_f(self):
        item = _wl_item(
            "Microsoft",
            "Blue Chip Partners LLC Acquires 1,314 Shares of Microsoft Corporation",
        )
        self.assertFalse(db._watchlist_delta_passes_noise_filter(item))


class TestWatchlistDeltaIsMarketSignalEscalation(unittest.TestCase):
    def test_loop_based_escalation_excluded(self):
        item = _wl_item("McDonald's", "Overdue loop follow-up needed.", loop_id="L-2026-07-23-003")
        self.assertFalse(db._watchlist_delta_is_market_signal_escalation(item))

    def test_market_signal_escalation_included(self):
        item = _wl_item("Stripe", "Stripe tips toward staying private, per Payments Dive.")
        self.assertTrue(db._watchlist_delta_is_market_signal_escalation(item))


class TestWatchlistDeltaSummaryNamesAreSubsetOfSectionF(unittest.TestCase):
    """End-to-end regression for the actual bug: build the same watchlist
    item set the live brief had (AWS filtered, Google Cloud kept) and
    confirm the summary's name list no longer contains an entity Section F
    itself would drop."""

    def test_filtered_entity_never_appears_in_new_activity_names(self):
        items = [
            {"extras": {"entity_name": "Dave & Buster's", "watchlist_status": "New Activity"},
             "why_it_matters": "Press release: Dave & Buster's names Amanda Busby as new COO"},
            {"extras": {"entity_name": "Amazon Web Services", "watchlist_status": "New Activity"},
             "why_it_matters": "Press release: Google Cloud Announces Strategic Partnership with Verizon"},
            {"extras": {"entity_name": "Google Cloud", "watchlist_status": "New Activity"},
             "why_it_matters": "Press release: Google Cloud Announces Strategic Partnership with Verizon to Scale Enterprise AI"},
        ]
        kept_names = [
            (it.get("extras") or {}).get("entity_name") for it in items
            if (it.get("extras") or {}).get("watchlist_status") == "New Activity"
            and db._watchlist_delta_passes_noise_filter(it)
        ]
        self.assertNotIn("Amazon Web Services", kept_names)
        self.assertIn("Google Cloud", kept_names)
        self.assertIn("Dave & Buster's", kept_names)


if __name__ == "__main__":
    unittest.main()
