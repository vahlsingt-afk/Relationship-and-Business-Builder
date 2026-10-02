"""
test_watchlist_material_activity_surfaced.py

Regression coverage: Section F ("F: Watchlist") rendered only the "No
Change" bucket count -- "101 entities unchanged since last scan (no material
developments)." -- even on a brief whose own "Watchlist Delta Summary" line
(in What Changed Today) reported Escalated: 2 | New activity: 19 | Relevant
activity: 32. Those 53 entities with real activity were counted in the
summary line but never actually listed anywhere in the section body itself,
so "what's escalated, what's new" had to be reverse-engineered from a bare
count with no names attached.

_render_watchlist_rollup now calls _render_watchlist_material_activity to
render Escalated (full detail), New Activity (named bullets), and Relevant
Activity (named rollup) before the no-change count.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402

_NO_PRICE_DATA_DATE = date(2099, 1, 1)


def _wl_item(name: str, status: str, why: str = "", action: str = "", source_url: str | None = None) -> dict:
    return {
        "title": f"{name} — {status}",
        "why_it_matters": why,
        "recommended_action": action,
        "extras": {"entity_name": name, "watchlist_status": status, "source_url": source_url},
    }


class TestWatchlistMaterialActivitySurfaced(unittest.TestCase):
    def test_escalated_entities_rendered_with_full_detail(self):
        # RB-2026-08-28: why="Overdue loop" collided with a real, deliberate
        # exclusion filter in _render_watchlist_material_activity
        # (RB-DEFECT-2026-08-17's loop-grouping logic) that skips this path
        # for items whose why-text literally contains "overdue loop" -- those
        # are meant to be handled via core.group_watchlist_escalations'
        # loop_id-based grouping instead, to avoid double-rendering. This
        # test's own intent (a plain escalated entity, full detail, no loop
        # involved) needs why-text that doesn't collide with that filter.
        sections = {
            "watchlist_intelligence": [
                _wl_item("McDonald's", "Escalation", why="Sustained material signal, no resolution yet.", action="Follow up immediately."),
                _wl_item("Harri", "Escalation", why="Sustained material signal, no resolution yet.", action="Follow up immediately."),
            ]
        }
        out = rib._render_watchlist_rollup(sections, today=_NO_PRICE_DATA_DATE)
        self.assertIn("[ESCALATED] McDonald's", out)
        self.assertIn("[ESCALATED] Harri", out)
        self.assertIn("Sustained material signal, no resolution yet.", out)
        # RB-2026-08-28: NOT a bug -- per INTELLIGENCE_BRIEF_CANONICAL.md
        # ("Any recommendation... belongs in Daily Brief"), recommended_action
        # was deliberately removed from this (Intelligence Brief) render path
        # -- see the comment at render_intelligence_brief.py's escalated-item
        # block. Verified this time (unlike a similar case found the same
        # day for why_it_matters) that it genuinely does render elsewhere:
        # render_daily_brief.py has real, active recommended_action usage.
        self.assertNotIn("Follow up immediately.", out)

    def test_new_activity_entities_named_not_just_counted(self):
        sections = {
            "watchlist_intelligence": [
                _wl_item("Starbucks", "New Activity", why="Declared a dividend"),
                _wl_item("Domino's", "New Activity", why="Store expansion news"),
            ]
        }
        out = rib._render_watchlist_rollup(sections, today=_NO_PRICE_DATA_DATE)
        self.assertIn("New activity (2):", out)
        self.assertIn("Starbucks", out)
        self.assertIn("Domino's", out)

    def test_relevant_activity_not_enumerated(self):
        """RB-DEFECT-2026-07-27: "Relevant Activity" used to be rolled up
        and named here -- removed per explicit request: Todd doesn't need
        the scanned-companies roster in the brief, only escalations and
        genuinely new activity."""
        sections = {
            "watchlist_intelligence": [
                _wl_item("Chipotle", "Relevant Activity"),
                _wl_item("Wendy's", "Relevant Activity"),
            ]
        }
        out = rib._render_watchlist_rollup(sections, today=_NO_PRICE_DATA_DATE)
        self.assertNotIn("Relevant activity", out)
        self.assertNotIn("Chipotle", out)
        self.assertNotIn("Wendy's", out)
        self.assertIn("No escalations or new watchlist activity today.", out)

    def test_new_activity_entity_with_source_url_renders_as_link(self):
        """RB-DEFECT-2026-07-10e: press-release bullets rendered as plain
        text with no way to actually read them, unlike every news-story
        section (A-E) which links [title](url). daily_brief.py captures the
        real press-release/alert URL in extras.source_url; the renderer
        must turn the entity name into the link anchor when present."""
        sections = {
            "watchlist_intelligence": [
                _wl_item(
                    "Domino's", "New Activity",
                    why="Press release: News from Domino's Pizza (PR Newswire)",
                    source_url="https://prnewswire.com/dominos-news",
                ),
            ]
        }
        out = rib._render_watchlist_rollup(sections, today=_NO_PRICE_DATA_DATE)
        self.assertIn("[Domino's](https://prnewswire.com/dominos-news)", out)

    def test_new_activity_entity_without_source_url_renders_plain(self):
        """No URL available (e.g. web-scan-only evidence) -- must not
        produce a broken/empty link."""
        sections = {
            "watchlist_intelligence": [
                _wl_item("Chipotle", "New Activity", why="Some evidence with no link."),
            ]
        }
        out = rib._render_watchlist_rollup(sections, today=_NO_PRICE_DATA_DATE)
        self.assertIn("- Chipotle — Some evidence with no link.", out)
        self.assertNotIn("[Chipotle]()", out)

    def test_no_change_count_not_shown_alongside_material_activity(self):
        """RB-DEFECT-2026-07-27: the "N other entities unchanged since last
        scan" count was removed per explicit request, alongside the
        Relevant Activity name list."""
        sections = {
            "watchlist_intelligence": [
                _wl_item("McDonald's", "Escalation"),
                {"extras": {"watchlist_status": "No Change", "no_change_count": 101}},
            ]
        }
        out = rib._render_watchlist_rollup(sections, today=_NO_PRICE_DATA_DATE)
        self.assertIn("[ESCALATED] McDonald's", out)
        self.assertNotIn("other entities unchanged", out)


class TestEscalatedItemCrossSectionAndDecay(unittest.TestCase):
    """RB-DEFECT-2026-08-12: PAR/Olo/Fiserv were both (a) rendered in full
    in E: Earnings & Corporate, then AGAIN in full in F: Watchlist the same
    day, and (b) re-rendered with the identical full-length paragraph two
    days running once escalated, with no mechanism to acknowledge that
    Todd already saw it."""

    def setUp(self):
        self._orig = rib._rendered_this_run

    def tearDown(self):
        rib._rendered_this_run = self._orig

    def test_item_already_rendered_in_e_gets_pointer_not_full_repeat(self):
        rib._rendered_this_run = {"https://example.com/par-earnings"}
        long_why = "PAR Technology earnings release: " + ("details " * 30)
        sections = {"watchlist_intelligence": [
            {"title": "PAR Technology — Escalation", "why_it_matters": long_why,
             "recommended_action": "Review earnings.",
             "extras": {"entity_name": "PAR Technology", "watchlist_status": "Escalation",
                        "source_url": "https://example.com/par-earnings", "status_changed": True}},
        ]}
        out = rib._render_watchlist_rollup(sections, today=_NO_PRICE_DATA_DATE)
        self.assertIn("see E: Earnings & Corporate above", out)
        self.assertNotIn(long_why, out)

    def test_fresh_status_change_shows_full_detail(self):
        rib._rendered_this_run = set()
        why = "Genuinely new escalation reason, not previously reported."
        sections = {"watchlist_intelligence": [
            {"title": "McDonald's — Escalation", "why_it_matters": why, "recommended_action": "Follow up.",
             "extras": {"entity_name": "McDonald's", "watchlist_status": "Escalation", "status_changed": True}},
        ]}
        out = rib._render_watchlist_rollup(sections, today=_NO_PRICE_DATA_DATE)
        self.assertIn(why, out)

    def test_persisting_escalation_truncated_and_labeled(self):
        # RB-DEFECT-2026-08-19: "(Unchanged since prior brief)" was dropped
        # from the render -- Todd: "user doesn't care if the status changed
        # - tell me what the signal is." Truncation on a repeat is still the
        # behavior under test; the removed label is no longer part of the
        # contract.
        rib._rendered_this_run = set()
        long_why = "First earnings event on record for this company. " + ("No trend baseline yet. " * 20)
        sections = {"watchlist_intelligence": [
            {"title": "Fiserv — Escalation", "why_it_matters": long_why, "recommended_action": "Review.",
             "extras": {"entity_name": "Fiserv", "watchlist_status": "Escalation", "status_changed": False}},
        ]}
        out = rib._render_watchlist_rollup(sections, today=_NO_PRICE_DATA_DATE)
        self.assertIn("[ESCALATED] Fiserv", out)
        self.assertIn("First earnings event on record for this company.", out)
        self.assertNotIn(long_why, out)
        self.assertNotIn("Unchanged since prior brief", out)


class TestWatchlistAmbiguousNameRequiresContext(unittest.TestCase):
    """RB-2026-08-25: live in the 2026-08-25 Intelligence Brief -- "Revel"
    (the restaurant POS company on the watchlist) surfaced with a mining-
    industry press release ("Riverside Resources Executes Option Agreement
    for the Revel") that mentions the word "Revel" but is about an unrelated
    mining property/claim. The word genuinely appears in the text, so the
    existing own-entity-name check doesn't catch it -- this requires
    restaurant/payments context alongside the name for entities ambiguous
    enough to collide with an unrelated common word/place/claim name."""

    def test_revel_with_no_restaurant_context_dropped_as_noise(self):
        sections = {"watchlist_intelligence": [
            _wl_item("Revel", "New Activity",
                     why="Press release: Riverside Resources Executes Option "
                         "Agreement for the Revel - GlobeNewswire"),
        ]}
        out = rib._render_watchlist_rollup(sections, today=_NO_PRICE_DATA_DATE)
        self.assertIn("New activity (0):", out)
        self.assertNotIn("Riverside Resources", out)

    def test_revel_with_restaurant_context_still_renders(self):
        sections = {"watchlist_intelligence": [
            _wl_item("Revel", "New Activity",
                     why="Revel Systems announces new POS integration for quick-service restaurants."),
        ]}
        out = rib._render_watchlist_rollup(sections, today=_NO_PRICE_DATA_DATE)
        self.assertIn("New activity (1):", out)
        self.assertIn("Revel", out)


class TestWatchlistSuppressesExecHireAlreadyToldInEarlierSection(unittest.TestCase):
    """RB-2026-08-25: live in the 2026-08-25 Intelligence Brief -- the Dave &
    Buster's COO hire rendered in full in Section C (from an NRN headline)
    then rendered AGAIN in Section F's "New activity" (from a GlobeNewswire
    press-release URL for what is almost certainly the same announcement).
    Section C populates _rendered_exec_hire_entities_this_run when it renders
    an [EXEC HIRE]-badged item; Section F must consult it before repeating
    that entity's leadership news."""

    def setUp(self):
        rib._rendered_this_run = set()
        rib._rendered_exec_hire_entities_this_run = set()

    def test_leadership_news_suppressed_for_entity_already_told(self):
        rib._rendered_exec_hire_entities_this_run.add("dave & buster's")
        sections = {"watchlist_intelligence": [
            _wl_item("Dave & Buster's", "New Activity",
                     why="Press release: Dave & Buster's Strengthens Executive "
                         "Leadership Team, names Amanda Busby as new COO"),
        ]}
        out = rib._render_watchlist_rollup(sections, today=_NO_PRICE_DATA_DATE)
        self.assertIn("New activity (0):", out)
        self.assertNotIn("Amanda Busby", out)

    def test_non_leadership_news_for_same_entity_still_renders(self):
        """Only suppress when the watchlist item's own text also reads as a
        leadership announcement -- an entity already covered for an exec
        hire can still have genuinely distinct watchlist news the same day."""
        rib._rendered_exec_hire_entities_this_run.add("dave & buster's")
        sections = {"watchlist_intelligence": [
            _wl_item("Dave & Buster's", "New Activity",
                     why="Announced a new loyalty program partnership with Visa."),
        ]}
        out = rib._render_watchlist_rollup(sections, today=_NO_PRICE_DATA_DATE)
        self.assertIn("New activity (1):", out)
        self.assertIn("loyalty program partnership", out)

    def test_leadership_news_for_different_entity_still_renders(self):
        rib._rendered_exec_hire_entities_this_run.add("dave & buster's")
        sections = {"watchlist_intelligence": [
            _wl_item("Wendy's", "New Activity", why="Names Tariq Hassan chief marketing officer."),
        ]}
        out = rib._render_watchlist_rollup(sections, today=_NO_PRICE_DATA_DATE)
        self.assertIn("New activity (1):", out)
        self.assertIn("Wendy's", out)


if __name__ == "__main__":
    unittest.main()
