"""
test_intelligence_brief_section_e_g.py

Regression coverage: canonical Intelligence Brief spec (INTELLIGENCE_BRIEF_
CANONICAL.md) requires 13 sections in a fixed order, including E: Earnings &
Corporate and G: Opportunities. Assessment against the canonical spec found:

- Section E (_render_earnings) was already implemented and wired into
  render(), but returned "" (silently omitted, no heading at all) whenever
  there were no matching watchlist events. Canonical requires the section
  "present or one-sentence 'none this cycle'" — an absent section reads as
  "did RB even check?" instead of "confirmed nothing happened."
- Section G (_render_opportunities) was fully implemented but never called
  anywhere in render() — dead code. It also rendered every opportunity
  regardless of state/age, including ones closed 52 days ago (Patrick
  Nelson) and 49 days ago (Coates Group) — re-reporting old history as if
  it were today's intelligence, violating Part 1's "delta report, not a
  telemetry dump" rule.

Fixed: E now always renders with a quiet-cycle fallback; G is wired into
render() between F: Watchlist and H: Relationship Deltas, and only shows
active/waiting opportunities plus closures/rejections from the last 2 days.
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


class TestSectionEAlwaysRenders(unittest.TestCase):
    def test_empty_watchlist_still_shows_heading_and_quiet_line(self):
        out = rib._render_earnings({"watchlist_intelligence": []}, date(2026, 7, 7))
        self.assertIn("## E: Earnings & Corporate", out)
        self.assertIn("No earnings reports or material corporate events this cycle.", out)

    def test_real_earnings_event_still_renders_normally(self):
        sections = {
            "watchlist_intelligence": [{
                "title": "[EARNINGS] Toast reports Q2 results",
                "why_it_matters": "Beat on revenue.",
                "extras": {"watchlist_status": "Escalated", "source_url": "https://example.com/toast-q2"},
                "source_refs": [],
            }]
        }
        out = rib._render_earnings(sections, date(2026, 7, 7))
        self.assertIn("Toast reports Q2 results", out)
        self.assertNotIn("No earnings, funding", out)


class TestSectionEScansOtherFeedsForCorporateEvents(unittest.TestCase):
    """RB-DEFECT-2026-07-10: Section E only ever scanned watchlist_intelligence,
    so a funding/exec event that reached the brief via restaurant_industry_
    headlines or restaurant_technology_headlines (the same signal_badge
    classification Section D uses) never counted here — E said "no events"
    in the same document where C/D had already reported them."""

    def test_funding_badge_in_restaurant_industry_headlines_picked_up(self):
        sections = {
            "watchlist_intelligence": [],
            "restaurant_industry_headlines": [{
                "title": "[💰 FUNDING] Jersey Mike's IPO reflects growing investor appetite",
                "why_it_matters": "New capital raise.",
                "extras": {"signal_badge": "[💰 FUNDING]", "source_url": "https://example.com/jersey-mikes-ipo"},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 10))
        self.assertIn("Jersey Mike's IPO", out)
        self.assertNotIn("No earnings, funding", out)

    def test_exec_departure_in_restaurant_technology_headlines_picked_up(self):
        sections = {
            "watchlist_intelligence": [],
            "restaurant_technology_headlines": [{
                "title": "[👤 EXEC DEPARTURE] Fiserv president exits",
                "why_it_matters": "Leadership change.",
                "extras": {"signal_badge": "[👤 EXEC DEPARTURE]", "source_url": "https://example.com/fiserv-exit"},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 10))
        self.assertIn("Fiserv president exits", out)

    def test_non_corporate_badge_in_restaurant_technology_headlines_ignored(self):
        """A DEPLOYMENT badge is real Section D content, not an earnings/
        M&A/funding/exec/competitive-win event — must not spill into E."""
        sections = {
            "watchlist_intelligence": [],
            "restaurant_technology_headlines": [{
                "title": "[🚀 DEPLOYMENT] Pizza Ranch Rolls Out NCR Voyix Kiosks",
                "why_it_matters": "Kiosk rollout.",
                "extras": {"signal_badge": "[🚀 DEPLOYMENT]", "source_url": "https://example.com/pizza-ranch-ncr"},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 10))
        self.assertIn("No earnings reports", out)

    def test_customer_win_badge_in_restaurant_technology_headlines_surfaces_in_e(self):
        # RB-DEFECT-2026-08-19: Todd, on Section E: "Competitor wins should
        # surface in section E as well." A CUSTOMER WIN badge (a competitor
        # landing/renewing a major restaurant account) is exactly that --
        # unlike DEPLOYMENT above, it now belongs in E, not buried in D.
        sections = {
            "watchlist_intelligence": [],
            "restaurant_technology_headlines": [{
                "title": "[✅ CUSTOMER WIN] Pizza Ranch Selects NCR Voyix",
                "why_it_matters": "Customer adoption.",
                "extras": {"signal_badge": "[✅ CUSTOMER WIN]", "source_url": "https://example.com/pizza-ranch-ncr"},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 10))
        self.assertIn("Pizza Ranch Selects NCR Voyix", out)
        self.assertNotIn("No earnings reports", out)

    def test_same_url_not_double_counted_across_feeds(self):
        url = "https://example.com/same-story"
        sections = {
            "watchlist_intelligence": [{
                "title": "[FUNDING] Some Vendor raises $10M",
                "extras": {"watchlist_status": "New Activity", "source_url": url},
            }],
            "restaurant_industry_headlines": [{
                "title": "[💰 FUNDING] Some Vendor raises $10M",
                "extras": {"signal_badge": "[💰 FUNDING]", "source_url": url},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 10))
        self.assertEqual(out.count("Some Vendor raises"), 1)

    def test_unjustified_acquisition_badge_excluded(self):
        """RB-DEFECT-2026-07-27: this feed-scan loop read extras.signal_badge
        at face value without the _badge_is_justified() check _fmt_headline
        applies for C/D -- "Wingstop Expands National Wing Day into
        Five-Day Celebration" (a marketing promo) and "How the Jersey
        Mike's IPO will affect the M&A market" (an M&A-market analysis
        podcast) were both upstream-tagged [🏢 ACQUISITION] despite neither
        describing an actual acquisition. C correctly strips the badge for
        display; E must apply the same check rather than trusting the raw
        badge and re-surfacing stale marketing/analysis content as if it
        were a genuine M&A event."""
        sections = {
            "watchlist_intelligence": [],
            "restaurant_industry_headlines": [
                {
                    "title": "[🏢 ACQUISITION] Wingstop Expands National Wing Day into Five-Day Celebration",
                    "why_it_matters": "Marketing promotion.",
                    "extras": {"signal_badge": "[🏢 ACQUISITION]", "source_url": "https://example.com/wingstop-wing-day"},
                },
                {
                    "title": "[🏢 ACQUISITION] How the Jersey Mike's IPO will affect the M&A market",
                    "why_it_matters": "Podcast discussion of restaurant M&A trends.",
                    "extras": {"signal_badge": "[🏢 ACQUISITION]", "source_url": "https://example.com/jersey-mikes-ma-podcast"},
                },
            ],
        }
        out = rib._render_earnings(sections, date(2026, 7, 27))
        self.assertIn("No earnings reports or material corporate events this cycle.", out)
        self.assertNotIn("Wing Day", out)
        self.assertNotIn("M&A market", out)

    def test_justified_acquisition_badge_still_included(self):
        sections = {
            "watchlist_intelligence": [],
            "restaurant_industry_headlines": [{
                "title": "[🏢 ACQUISITION] Biscuit Belly acquires 35 Maple Street locations",
                "why_it_matters": "Real M&A event.",
                "extras": {"signal_badge": "[🏢 ACQUISITION]", "source_url": "https://example.com/biscuit-belly"},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 27))
        self.assertIn("Biscuit Belly acquires", out)


class TestSectionESkipsUrlsAlreadyRenderedInEarlierSections(unittest.TestCase):
    """RB-DEFECT-2026-07-10i: E's own seen_urls set only caught a duplicate
    between watchlist_intelligence and the two feed_key loops -- it never
    checked _rendered_this_run, so a badge-tagged item C/D had *already
    rendered* a few lines earlier in the same brief (a common case, since
    badges like [ACQUISITION]/[FUNDING] are exactly what makes a headline
    "material" enough for C/D to pick it) got shown a second time here
    verbatim: same title, same link, same source. Observed live: "Trending
    this week: Cotton Patch Cafe acquired...", "For new owner of Hot Dog on
    a Stick...", "Jersey Mike's IPO...", and others all appeared in both
    C and E."""

    def setUp(self):
        rib._rendered_this_run = set()
        rib._story_ledger = core.StoryLedger()
        rib._rendered_story_ids_this_run = set()

    def tearDown(self):
        rib._rendered_this_run = set()
        rib._story_ledger = core.StoryLedger()
        rib._rendered_story_ids_this_run = set()

    def test_url_already_rendered_in_c_or_d_is_skipped_in_e(self):
        url = "https://www.nrn.com/casual-dining/cotton-patch-cafe-acquired"
        rib._rendered_this_run.add(url)
        sections = {
            "watchlist_intelligence": [],
            "restaurant_industry_headlines": [{
                "title": "[🏢 ACQUISITION] Cotton Patch Cafe acquired by Local Favorite Restaurants",
                "extras": {"signal_badge": "[🏢 ACQUISITION]", "source_url": url},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 10))
        self.assertNotIn("Cotton Patch Cafe", out)
        self.assertIn("No earnings reports or material corporate events this cycle.", out)

    def test_url_not_seen_elsewhere_still_renders(self):
        url = "https://example.com/genuinely-only-in-e"
        sections = {
            "watchlist_intelligence": [],
            "restaurant_industry_headlines": [{
                "title": "[🏢 ACQUISITION] Some Other Chain acquired",
                "extras": {"signal_badge": "[🏢 ACQUISITION]", "source_url": url},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 10))
        self.assertIn("Some Other Chain acquired", out)

    def test_rendered_item_registers_into_rendered_this_run(self):
        url = "https://example.com/e-only-story"
        sections = {
            "watchlist_intelligence": [],
            "restaurant_industry_headlines": [{
                "title": "[🏢 ACQUISITION] E-Only Chain acquired",
                "extras": {"signal_badge": "[🏢 ACQUISITION]", "source_url": url},
            }],
        }
        rib._render_earnings(sections, date(2026, 7, 10))
        self.assertIn(url, rib._rendered_this_run)


class TestSectionERespectsCrossDayDedup(unittest.TestCase):
    """RB-DEFECT-2026-07-14: the same-run check above only ever caught a
    duplicate WITHIN today's brief -- it never checked whether an item had
    already aged out of C/D on a PRIOR day via RB-DEFECT-2026-07-13's fix.
    Since C/D suppress a stale corporate item via _is_duplicate before E
    ever runs, that item is no longer in _rendered_this_run when E scans --
    so E kept re-including it every day regardless of age. Confirmed live:
    "Pinkbox owner acquires Hot Dog on a Stick" (first shown 2026-07-09)
    was still rendering in E on 2026-07-14, five days past its own 4-day
    corporate grace period, having already correctly disappeared from C
    days earlier."""

    def setUp(self):
        rib._rendered_this_run = set()
        rib._story_ledger = core.StoryLedger()
        rib._rendered_story_ids_this_run = set()

    def tearDown(self):
        rib._rendered_this_run = set()
        rib._story_ledger = core.StoryLedger()
        rib._rendered_story_ids_this_run = set()

    def test_stale_item_past_grace_period_suppressed(self):
        url = "https://www.qsrweb.com/articles/pinkbox-owner-acquires-hot-dog-on-a-stick/"
        title = "[🏢 ACQUISITION] Pinkbox owner acquires Hot Dog on a Stick"
        # RB-DEFECT-2026-07-20 Phase 2: story identity now, not the legacy
        # URL registry -- seed the ledger with the equivalent "already
        # known 5 days ago" state.
        key = core.resolve_story_identity(title)
        rib._story_ledger.record(key, url, title, date(2026, 7, 9))
        sections = {
            "watchlist_intelligence": [],
            "restaurant_industry_headlines": [{
                "title": title,
                "extras": {"signal_badge": "[🏢 ACQUISITION]", "source_url": url},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 14))
        self.assertNotIn("Hot Dog on a Stick", out)
        self.assertIn("No earnings reports or material corporate events this cycle.", out)

    def test_item_1_day_old_is_now_suppressed_in_e(self):
        """RB-2026-09-25: this used to assert a 1-day-old item STILL
        renders (the old 4-day grace) -- Todd killed that grace on purpose,
        so E now suppresses a story the day after it first appeared, same
        as everywhere else."""
        url = "https://example.com/recent-acquisition"
        title = "[🏢 ACQUISITION] Recent Chain acquired"
        key = core.resolve_story_identity(title)
        rib._story_ledger.record(key, url, title, date(2026, 7, 13))
        sections = {
            "watchlist_intelligence": [],
            "restaurant_industry_headlines": [{
                "title": title,
                "extras": {"signal_badge": "[🏢 ACQUISITION]", "source_url": url},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 14))
        self.assertNotIn("Recent Chain acquired", out)

    def test_no_rendered_registry_defaults_to_no_cross_day_check(self):
        """Calling without a rendered dict (e.g. every existing test in this
        file) must not crash and must render normally."""
        url = "https://example.com/no-registry-passed"
        sections = {
            "watchlist_intelligence": [],
            "restaurant_industry_headlines": [{
                "title": "[🏢 ACQUISITION] No Registry Chain acquired",
                "extras": {"signal_badge": "[🏢 ACQUISITION]", "source_url": url},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 14))
        self.assertIn("No Registry Chain acquired", out)


class TestSectionERepublishedUrlDoesNotResetGraceClock(unittest.TestCase):
    """RB-DEFECT-2026-07-17: E has its own cross-day dedup check (separate
    from C/D's, see TestSectionERespectsCrossDayDedup above) that also only
    ever matched by exact URL -- a story republished under a new URL (see
    test_intelligence_brief_restaurant_tech_fmt.py's
    TestRepublishedUrlDoesNotResetGraceClock for the live example) got a
    fresh grace countdown in E too."""

    def setUp(self):
        rib._rendered_this_run = set()
        rib._story_ledger = core.StoryLedger()
        rib._rendered_story_ids_this_run = set()

    def test_republished_url_past_grace_suppressed_in_e(self):
        new_url = "https://www.nrn.com/regional-chains/wonder-acquires-mighty-quinn-s-bbq"
        old_url = "https://www.nrn.com/emerging-chains/wonder-acquires-mighty-quinn-s-bbq"
        title = "[🏢 ACQUISITION] Wonder acquires Mighty Quinn's BBQ"
        key = core.resolve_story_identity(title)
        rib._story_ledger.record(key, old_url, title, date(2026, 7, 10))
        sections = {
            "watchlist_intelligence": [],
            "restaurant_industry_headlines": [{
                "title": title,
                "extras": {"signal_badge": "[🏢 ACQUISITION]", "source_url": new_url},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 14))
        self.assertNotIn("Mighty Quinn", out)
        self.assertIn("No earnings reports or material corporate events this cycle.", out)


class TestSectionEFirstAppearanceFreshnessGate(unittest.TestCase):
    """RB-DEFECT-2026-07-20 (Phase 1.3): same first-appearance gap as C/D --
    a URL E has never seen before shouldn't "first appear" already days old
    just because corporate items get a long freshness ceiling elsewhere."""

    def setUp(self):
        rib._rendered_this_run = set()
        rib._story_ledger = core.StoryLedger()
        rib._rendered_story_ids_this_run = set()

    def test_never_seen_url_beyond_grace_on_arrival_is_dropped(self):
        sections = {
            "watchlist_intelligence": [],
            "restaurant_industry_headlines": [{
                "title": "[👤 EXEC HIRE] Pollo Campero Appoints Karla Patino VP of Marketing",
                "extras": {"signal_badge": "[👤 EXEC HIRE]",
                           "source_url": "https://example.com/pollo-campero-vp",
                           "pub_date": "2026-07-15"},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 20), rendered={})
        self.assertNotIn("Pollo Campero", out)
        self.assertIn("No earnings reports or material corporate events this cycle.", out)

    def test_never_seen_url_at_the_1_day_grace_boundary_still_renders(self):
        """RB-2026-09-25: the first-appearance gate compares with `>`, not
        `>=`, so a URL exactly 1 day old still passes on first sighting even
        with grace now 1 everywhere -- this used to test 2-days-old under
        the old 4-day window, which would now correctly be rejected."""
        sections = {
            "watchlist_intelligence": [],
            "restaurant_industry_headlines": [{
                "title": "[👤 EXEC HIRE] Pollo Campero Appoints Karla Patino VP of Marketing",
                "extras": {"signal_badge": "[👤 EXEC HIRE]",
                           "source_url": "https://example.com/pollo-campero-vp-fresh",
                           "pub_date": "2026-07-19"},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 20), rendered={})
        self.assertIn("Pollo Campero", out)

    # RB-2026-09-25: test_already_tracked_url_unaffected_by_first_appearance_gate
    # removed -- see the matching note in test_intelligence_brief_restaurant_tech_fmt.py's
    # TestFirstAppearanceFreshnessGate. A multi-day-old-but-still-tracked
    # story is no longer a scenario that exists once grace is 1.


class TestSectionESkipsSameEventDifferentOutlet(unittest.TestCase):
    """RB-DEFECT-2026-07-17: exact-URL dedup (above) only catches a byte-
    identical link repeat. Wonder's $650M Series D / $9B valuation was
    reported by three different outlets on the same day -- C rendered two
    of them (Restaurant Business Online + Fast Casual), then E's own
    feed_key scan added a third (Restaurant Dive) under yet another URL,
    since as far as E's exact-URL check could tell it was a fresh story.
    _corporate_subject_signature keys on the leading subject ("Wonder") so
    E can recognize the same real-world event under a different outlet's
    link and different headline wording."""

    def setUp(self):
        rib._rendered_this_run = set()
        rib._story_ledger = core.StoryLedger()
        rib._rendered_story_ids_this_run = set()
        rib._rendered_subjects_this_run = set()

    def tearDown(self):
        rib._rendered_this_run = set()
        rib._story_ledger = core.StoryLedger()
        rib._rendered_story_ids_this_run = set()
        rib._rendered_subjects_this_run = set()

    def test_same_company_event_already_rendered_in_c_or_d_skipped_in_e(self):
        rib._rendered_subjects_this_run.add("wonder")
        sections = {
            "watchlist_intelligence": [],
            "restaurant_technology_headlines": [{
                "title": "[💰 FUNDING] Wonder tops $9B valuation, raises $650M",
                "extras": {"signal_badge": "[💰 FUNDING]",
                           "source_url": "https://www.restaurantdive.com/news/wonder-valuation-9-billion/825417/"},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 17))
        self.assertNotIn("Wonder", out)
        self.assertIn("No earnings reports or material corporate events this cycle.", out)

    def test_different_company_event_still_renders(self):
        rib._rendered_subjects_this_run.add("wonder")
        sections = {
            "watchlist_intelligence": [],
            "restaurant_industry_headlines": [{
                "title": "[💰 FUNDING] Sweetgreen raises new funding round",
                "extras": {"signal_badge": "[💰 FUNDING]",
                           "source_url": "https://example.com/sweetgreen-funding"},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 17))
        self.assertIn("Sweetgreen raises new funding round", out)

    def test_two_feed_key_items_about_same_company_only_one_kept(self):
        sections = {
            "watchlist_intelligence": [],
            "restaurant_industry_headlines": [{
                "title": "[💰 FUNDING] Wonder is valued at more than $9B after latest fundraise",
                "extras": {"signal_badge": "[💰 FUNDING]",
                           "source_url": "https://restaurantbusinessonline.com/wonder-9b"},
            }],
            "restaurant_technology_headlines": [{
                "title": "[💰 FUNDING] Wonder tops $9B valuation, raises $650M",
                "extras": {"signal_badge": "[💰 FUNDING]",
                           "source_url": "https://www.restaurantdive.com/news/wonder-valuation-9-billion/825417/"},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 17))
        self.assertEqual(out.count("Wonder"), 1)

    def test_no_rendered_subjects_registry_state_leaks_between_calls(self):
        """Baseline sanity: with no prior subjects registered, a genuine
        event still renders (guards against over-suppression)."""
        sections = {
            "watchlist_intelligence": [],
            "restaurant_industry_headlines": [{
                "title": "[🏢 ACQUISITION] Some New Chain acquired",
                "extras": {"signal_badge": "[🏢 ACQUISITION]", "source_url": "https://example.com/new-chain"},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 7, 17))
        self.assertIn("Some New Chain acquired", out)


class TestSectionGOpportunities(unittest.TestCase):
    def test_no_data_shows_quiet_line(self):
        out = rib._render_opportunities({})
        self.assertIn("## G: Opportunities", out)
        self.assertIn("No verified active commercial opportunities.", out)

    def test_active_opportunity_shown_with_freshness_label(self):
        sections = {
            "opportunity_board": [{
                "title": "[ACTIVE] Foods Connected Commercial Director",
                "extras": {"opportunity_state": "ACTIVE", "evidence_age_days": 3},
            }]
        }
        out = rib._render_opportunities(sections)
        self.assertIn("Foods Connected Commercial Director", out)
        self.assertIn("UNCHANGED", out)
        self.assertNotIn("[ACTIVE]", out)  # bracket prefix stripped from title

    def test_old_closed_opportunity_not_rerendered_as_news(self):
        """Patrick Nelson (closed 52 days ago) must not repeat forever."""
        sections = {
            "opportunity_board": [{
                "title": "[CLOSED] Patrick Nelson / Matrix Software Solutions follow-up",
                "extras": {"opportunity_state": "CLOSED", "evidence_age_days": 52},
            }]
        }
        out = rib._render_opportunities(sections)
        self.assertNotIn("Patrick Nelson", out)
        self.assertIn("No verified active commercial opportunities.", out)

    def test_recently_closed_career_opportunity_is_not_shown(self):
        sections = {
            "opportunity_board": [{
                "title": "[CLOSED] Genius / Global Payments role conversation",
                "extras": {"opportunity_state": "CLOSED", "evidence_age_days": 1},
            }]
        }
        out = rib._render_opportunities(sections)
        self.assertNotIn("Genius / Global Payments role conversation", out)
        self.assertIn("No verified active commercial opportunities.", out)

    def test_strategic_account_loop_is_shown(self):
        sections = {
            "loops_and_obligations": [{
                "title": "Overdue: L-2026-07-27-001 — Pollo Campero / DMB RFI",
                "summary": "Internal RFI kickoff needs a confirmed owner and submission plan.",
            }]
        }
        out = rib._render_opportunities(sections)
        self.assertIn("Pollo Campero / DMB RFI", out)
        self.assertIn("confirmed owner", out)

    def test_stale_active_opportunity_flagged(self):
        sections = {
            "opportunity_board": [{
                "title": "Some Stale Deal",
                "extras": {"opportunity_state": "WAITING", "evidence_age_days": 20},
            }]
        }
        out = rib._render_opportunities(sections)
        self.assertIn("STALE⚠", out)


if __name__ == "__main__":
    unittest.main()
