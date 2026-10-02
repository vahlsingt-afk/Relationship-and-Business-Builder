"""
test_intelligence_brief_restaurant_tech_fmt.py

RB-DEFECT-2026-07-10: Section D (Restaurant Technology) rendered "0 material
headlines this cycle" despite 10 genuinely good, fresh candidate items being
available. Root-caused to two compounding bugs in _fmt_headline():

1. _RTN_PR_PATTERN (vendor-advertorial detection for Restaurant Technology
   News items) matched on generic verb+"restaurant(s)" phrasing alone,
   dropping real events the upstream classifier had already vetted with a
   signal_badge -- e.g. "[CUSTOMER WIN] Taco Bell Expands Drive-Thru Voice AI
   to Nearly 900 Restaurants" matches "Expands ... Restaurants" exactly like
   vendor puffery would, but is genuine customer-adoption news. Fixed by
   exempting badged items from this check (mirrors the same exemption
   principle _select_restaurant_tech_items already applies).
2. _is_duplicate's corporate-event dedup used a hardcoded 1-day grace period
   ("shown once, never again") copied from world/national's fast-cycling-news
   assumption. restaurant_tech leans on 1-2 low-daily-volume feeds, so every
   badged item vanished permanently the day after first appearing, and on any
   day with no brand-new badged story the section went fully empty even
   though several still-fresh badged items existed. Fixed with a longer,
   section-aware grace period (CORPORATE_DEDUP_GRACE_DAYS) for restaurant_tech/
   restaurant_industry, while world/national keeps the original 1-day cutoff.

RB-2026-09-25 SUPERSEDED: Todd's explicit call -- kill the extended grace
entirely. In practice it meant the exact same headline and "why it matters"
text repeating verbatim for up to 4 days (confirmed live twice: a Digital
Transactions acquisition brief unchanged 2026-09-20 through 09-22; a funding
item unchanged 2026-09-23/09-24), which read as stale/repetitive. Preferred
now: a genuinely thin news day renders thin (or empty) rather than recycling
old content. CORPORATE_DEDUP_GRACE_DAYS is 1 everywhere now -- the tests
below that specifically exercised the old multi-day window were updated to
match (or removed, where the scenario they tested no longer exists under a
1-day grace); tests exercising mechanics that are still real regardless of
the grace value (republished-URL identity inheritance, first_rendered vs.
last_rendered, same-day re-render) are unchanged. Kept as module history,
not because the fixed bug isn't real -- the underlying "0 material
headlines" risk this originally traded away is now accepted on purpose.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402
import rb_core as core  # noqa: E402


def _badged_item(title: str, *, source: str = "Restaurant Technology News",
                  pub_date: str = "2026-07-09", badge: str = "[✅ CUSTOMER WIN]") -> dict:
    # Real items carry the badge as a literal prefix on the title string
    # (e.g. "[✅ CUSTOMER WIN] Taco Bell Expands...") in addition to the
    # extras.signal_badge field -- _fmt_headline's badge-justification check
    # reads the title prefix, not just the extras field.
    full_title = f"{badge} {title}" if badge else title
    return {
        "title": full_title,
        "why_it_matters": "Customer adoption signal.",
        "extras": {
            "source_url": f"https://restauranttechnologynews.com/{abs(hash(title))}",
            "source_name": source,
            "pub_date": pub_date,
            "signal_badge": badge,
        },
    }


class TestCorporateGraceKilled(unittest.TestCase):
    """RB-2026-09-25: Todd's explicit product decision, pinned directly so
    a future change can't silently reintroduce a multi-day grace without a
    test noticing. 'If a section is truly empty because of no news, leave
    it empty' -- not a bug fix, a deliberate choice to prefer an honest
    thin section over recycled content."""

    def test_grace_constant_is_1_everywhere(self):
        self.assertEqual(rib.CORPORATE_DEDUP_GRACE_DAYS, 1)


class TestVendorPrPatternExemptsBadgedItems(unittest.TestCase):
    def setUp(self):
        rib._rendered_this_run = set()
        rib._story_ledger = core.StoryLedger()
        rib._rendered_story_ids_this_run = set()

    def test_badged_item_matching_vendor_pattern_still_renders(self):
        item = _badged_item(
            "Taco Bell Expands Drive-Thru Voice AI to Nearly 900 Restaurants Across the U.S.",
        )
        out = rib._fmt_headline(item, date(2026, 7, 10), "restaurant_tech", {})
        self.assertIsNotNone(out)
        self.assertIn("Taco Bell Expands", out)

    def test_unbadged_item_matching_vendor_pattern_still_dropped(self):
        item = _badged_item(
            "Squirrel Brings Cloud POS, Integrated Payments and Kitchen Automation to Restaurants",
            badge="",
        )
        out = rib._fmt_headline(item, date(2026, 7, 10), "restaurant_tech", {})
        self.assertIsNone(out)

    def test_gp_vendor_pr_still_kept_even_unbadged(self):
        item = _badged_item(
            "Genius Strengthens Restaurant Operations With Connected Commerce Platform",
            badge="",
        )
        out = rib._fmt_headline(item, date(2026, 7, 10), "restaurant_tech", {})
        self.assertIsNotNone(out)


class TestCorporateDedupGracePeriod(unittest.TestCase):
    def setUp(self):
        rib._rendered_this_run = set()
        rib._story_ledger = core.StoryLedger()
        rib._rendered_story_ids_this_run = set()

    def test_badged_item_rendered_2_days_ago_is_now_suppressed_in_restaurant_tech(self):
        """RB-2026-09-25: this used to assert the OPPOSITE (the old 4-day
        grace let a 2-day-old story keep re-rendering) -- Todd killed that
        grace on purpose, so restaurant_tech now behaves exactly like
        world/national: shown once, suppressed from the next day on."""
        item = _badged_item(
            "Dishio Acquires Rival Kitchen-Ops Platform", pub_date="2026-07-05",
            badge="[🏢 ACQUISITION]",
        )
        url = item["extras"]["source_url"]
        key = core.resolve_story_identity(item["title"])
        rib._story_ledger.record(key, url, item["title"], date(2026, 7, 8))
        out = rib._fmt_headline(item, date(2026, 7, 10), "restaurant_tech", {})
        self.assertIsNone(out)

    def test_badged_item_rendered_9_days_ago_still_suppressed_in_restaurant_tech(self):
        item = _badged_item("GoTab Acquires Fishbowl", pub_date="2026-07-01", badge="[🏢 ACQUISITION]")
        url = item["extras"]["source_url"]
        rendered = {url: {"last_rendered": "2026-07-01"}}  # 9 days before today
        out = rib._fmt_headline(item, date(2026, 7, 10), "restaurant_tech", rendered)
        self.assertIsNone(out)

    def test_repeated_rerenders_do_not_reset_the_grace_clock(self):
        """RB-DEFECT-2026-07-13: _mark_rendered bumps last_rendered to today
        every time an item successfully re-renders -- so an item that keeps
        getting picked up daily (or is manually re-rendered) never actually
        aged past ~1 day under the old last_rendered-based check, and never
        hit the grace threshold. Confirmed live: a Cotton Patch Cafe
        acquisition story first shown 2026-07-09 was still rendering
        verbatim on 2026-07-13 (render_count: 20) -- a genuinely 4-day-old
        story well past CORPORATE_DEDUP_GRACE_DAYS (4) that should have
        expired days earlier. The grace period must be measured from
        first_rendered (when the story first appeared), not last_rendered
        (which resets every render)."""
        item = _badged_item(
            "Cotton Patch Cafe acquired by Local Favorite Restaurants",
            pub_date="2026-07-08", badge="[🏢 ACQUISITION]",
        )
        url = item["extras"]["source_url"]
        # first_rendered is 4 days before today -- at CORPORATE_DEDUP_GRACE_DAYS,
        # this must now expire even though last_rendered looks brand new
        # (as it would after being re-rendered every day in between).
        rendered = {url: {"first_rendered": "2026-07-09", "last_rendered": "2026-07-12"}}
        out = rib._fmt_headline(item, date(2026, 7, 13), "restaurant_tech", rendered)
        self.assertIsNone(out)

    def test_missing_first_rendered_falls_back_to_last_rendered(self):
        """Older persisted entries from before this fix have no
        first_rendered field at all -- must not crash, and should fall back
        to the pre-fix last_rendered-based behavior rather than treating
        the item as brand new."""
        item = _badged_item("GoTab Acquires Fishbowl", pub_date="2026-07-01", badge="[🏢 ACQUISITION]")
        url = item["extras"]["source_url"]
        rendered = {url: {"last_rendered": "2026-07-01"}}  # no first_rendered key
        out = rib._fmt_headline(item, date(2026, 7, 10), "restaurant_tech", rendered)
        self.assertIsNone(out)

    def test_world_national_keeps_1_day_grace(self):
        item = {
            "title": "Some Corp Announces Acquisition of Rival Corp",
            "why_it_matters": "M&A event.",
            "extras": {
                "source_url": "https://example.com/some-corp-acquires",
                "source_name": "BBC World News",
                "pub_date": "2026-07-08",
                "signal_badge": "[🏢 ACQUISITION]",
            },
        }
        url = item["extras"]["source_url"]
        rendered = {url: {"last_rendered": "2026-07-09"}}  # 1 day before today
        out = rib._fmt_headline(item, date(2026, 7, 10), "world", rendered)
        self.assertIsNone(out)

    def test_same_day_rerender_does_not_bypass_corporate_grace_expiry(self):
        """RB-DEFECT-2026-07-22: the non-corporate same-day escape hatch
        (age_days == 0 against last_rendered, meant to support --force
        same-day re-renders) used to run BEFORE the corporate branch --
        so on any day the brief rendered more than once (a resend, a
        retry, a manual force-regenerate), last_rendered was already
        "today" by the second evaluation, and the escape returned "not a
        duplicate" without ever reaching the first_rendered check.
        Confirmed live: "How P. Terry's Burger Stand..." (first_rendered
        2026-07-18) was still rendering on 2026-07-22, a full day past its
        4-day corporate grace, because last_rendered had already been
        bumped to today from an earlier same-day render. Tests
        _is_duplicate directly -- this specific title happens to resolve a
        clean story identity (Phase 2's separate path), so routing through
        _fmt_headline wouldn't exercise the fallback branch this bug
        actually lived in; the underlying function is what needs pinning
        down regardless of which path currently reaches it."""
        url = "https://x.com/p-terrys-style-story"
        rendered = {url: {"first_rendered": "2026-07-18", "last_rendered": "2026-07-22"}}
        result = rib._is_duplicate(url, rendered, date(2026, 7, 22), is_corporate=True, corporate_grace_days=4)
        self.assertTrue(result)

    def test_same_day_rerender_still_shows_genuinely_new_same_day_story(self):
        """A story genuinely first rendered today (first_rendered == today)
        must still show on a same-day re-render -- the corporate branch
        doesn't need the escape hatch since age 0 already passes on its own."""
        url = "https://x.com/brand-new-today"
        rendered = {url: {"first_rendered": "2026-07-22", "last_rendered": "2026-07-22"}}
        result = rib._is_duplicate(url, rendered, date(2026, 7, 22), is_corporate=True, corporate_grace_days=4)
        self.assertFalse(result)


class TestRepublishedUrlDoesNotResetGraceClock(unittest.TestCase):
    """RB-DEFECT-2026-07-17: some trade-press CMSes republish the exact same
    story under a new URL path (NRN moved "Wonder acquires Mighty Quinn's
    BBQ" from /emerging-chains/ to /regional-chains/ on day 5). Since dedup
    used to be keyed by exact URL, the republished link was treated as a
    brand-new story and got a fresh CORPORATE_DEDUP_GRACE_DAYS countdown --
    the identical headline rendered five days straight (2026-07-10 through
    2026-07-14) instead of the intended four.

    RB-DEFECT-2026-07-20 Phase 2: this exact scenario is now handled by
    story identity (rb_core.StoryLedger) rather than the anchor-noun/URL-
    registry patch these tests originally exercised -- both titles resolve
    to the same (entity="wonder", event="acquisition") key regardless of
    which URL carries them, so a republished link finds the SAME story in
    the ledger and inherits its first_seen instead of starting fresh."""

    def setUp(self):
        rib._rendered_this_run = set()
        rib._story_ledger = core.StoryLedger()
        rib._rendered_story_ids_this_run = set()

    def _seed_original_story(self, old_url: str, first_seen: date) -> None:
        key = core.resolve_story_identity("[🏢 ACQUISITION] Wonder acquires Mighty Quinn's BBQ")
        rib._story_ledger.record(key, old_url, "[🏢 ACQUISITION] Wonder acquires Mighty Quinn's BBQ", first_seen)

    def test_republished_url_past_grace_is_suppressed(self):
        item = _badged_item(
            "Wonder acquires Mighty Quinn's BBQ",
            pub_date="2026-07-14", badge="[🏢 ACQUISITION]",
        )
        old_url = "https://www.nrn.com/emerging-chains/wonder-acquires-mighty-quinn-s-bbq"
        # today (07-14) - original first_seen (07-10) = 4 days, well past the
        # 1-day grace (RB-2026-09-25) -- any offset >= 1 would suppress now.
        self._seed_original_story(old_url, date(2026, 7, 10))
        out = rib._fmt_headline(item, date(2026, 7, 14), "restaurant_industry", {})
        self.assertIsNone(out)

    def test_republished_url_same_day_still_renders_and_inherits_date(self):
        """RB-2026-09-25: with CORPORATE_DEDUP_GRACE_DAYS now 1 everywhere,
        the only day a republished URL can still legitimately render is the
        same day the original story first appeared (age 0) -- this used to
        test day 2 of a since-killed 4-day window; the still-real behavior
        being pinned here is identity inheritance (a republish must find the
        SAME story and keep its real first_seen), not the grace length."""
        item = _badged_item(
            "Wonder acquires Mighty Quinn's BBQ",
            pub_date="2026-07-10", badge="[🏢 ACQUISITION]",
        )
        new_url = item["extras"]["source_url"]
        old_url = "https://www.nrn.com/emerging-chains/wonder-acquires-mighty-quinn-s-bbq"
        self._seed_original_story(old_url, date(2026, 7, 10))
        out = rib._fmt_headline(item, date(2026, 7, 10), "restaurant_industry", {})
        self.assertIsNotNone(out)
        # New URL must resolve to the SAME story and inherit its first_seen,
        # not today -- otherwise it just buys another fresh countdown.
        story = rib._story_ledger.url_to_story(new_url)
        self.assertIsNotNone(story)
        self.assertEqual(story["first_seen"], "2026-07-10")
        self.assertIn(old_url, story["urls"])

    def test_unrelated_story_about_different_company_not_affected(self):
        item = _badged_item(
            "GoTab Acquires Fishbowl", pub_date="2026-07-14", badge="[🏢 ACQUISITION]",
        )
        old_url = "https://www.nrn.com/emerging-chains/wonder-acquires-mighty-quinn-s-bbq"
        self._seed_original_story(old_url, date(2026, 7, 10))
        out = rib._fmt_headline(item, date(2026, 7, 14), "restaurant_industry", {})
        self.assertIsNotNone(out)


class TestFirstAppearanceFreshnessGate(unittest.TestCase):
    """RB-DEFECT-2026-07-20 (Phase 1.3): a corporate item's up-to-21-day
    freshness ceiling is meant to keep an already-tracked story visible
    through a slow news day, not to let a URL never seen before "first
    appear" days late as if it were breaking news. Confirmed live: QSR
    Magazine's feed was dead 2026-06-03 through 2026-07-17; once
    reactivated, its first scan surfaced several exec-hire items already 4
    days old, which rendered as fresh "new activity" simply because nothing
    had ever registered their URLs before."""

    def setUp(self):
        rib._rendered_this_run = set()
        rib._story_ledger = core.StoryLedger()
        rib._rendered_story_ids_this_run = set()

    def test_never_seen_url_beyond_grace_on_arrival_is_dropped(self):
        item = _badged_item(
            "PrimoHoagies Names Madalyn Weintraub VP of Marketing",
            pub_date="2026-07-16", badge="[👤 EXEC HIRE]",
        )
        # today (07-20) - pub_date (07-15) = 5 days, well beyond the 1-day
        # grace (RB-2026-09-25) -- unambiguous either way.
        item["extras"]["pub_date"] = "2026-07-15"
        out = rib._fmt_headline(item, date(2026, 7, 20), "restaurant_industry", {})
        self.assertIsNone(out)

    def test_never_seen_url_at_the_1_day_grace_boundary_still_renders(self):
        """RB-2026-09-25: with CORPORATE_DEDUP_GRACE_DAYS now 1 everywhere,
        the first-appearance gate (`> corporate_grace`, not `>=`) still lets
        a URL exactly 1 day old through on its first sighting -- pins that
        boundary is still `>`, not accidentally tightened to `>=` when the
        constant dropped from 4 to 1. This used to test 2-days-old under the
        old 4-day window; that offset would now correctly be rejected."""
        item = _badged_item(
            "PrimoHoagies Names Madalyn Weintraub VP of Marketing",
            pub_date="2026-07-19", badge="[👤 EXEC HIRE]",
        )
        out = rib._fmt_headline(item, date(2026, 7, 20), "restaurant_industry", {})
        self.assertIsNotNone(out)

    # RB-2026-09-25: test_already_tracked_url_unaffected_by_first_appearance_gate
    # removed -- it asserted a multi-day-old-but-still-in-grace tracked story
    # still renders, a scenario that no longer exists once grace is 1 (an
    # already-tracked story is only ever still eligible on the exact same
    # day it was first rendered, which is the same-day-rerender case already
    # covered by TestCorporateDedupGracePeriod's same-day tests above).

    def test_world_national_first_appearance_uses_tighter_1_day_grace(self):
        item = {
            "title": "[🏢 ACQUISITION] Some Corp Announces Acquisition of Rival Corp",
            "why_it_matters": "M&A event.",
            "extras": {
                "source_url": "https://example.com/some-corp-old-first-appearance",
                "source_name": "BBC World News",
                "pub_date": "2026-07-17",
                "signal_badge": "[🏢 ACQUISITION]",
            },
        }
        out = rib._fmt_headline(item, date(2026, 7, 20), "world", {})
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
