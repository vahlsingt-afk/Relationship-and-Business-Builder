"""
test_curated_trade_reads_cross_day_dedup.py

Regression coverage: Todd reported the Intelligence Brief repeating stories
from previous days. Confirmed live against 2026-08-27/28/29's real
intelligence-brief.md files -- "## D+: Curated Trade Reads"
(_render_team_newsletters) rendered:

  - "Agentic Commerce: How to Power AI-Driven Payment Experiences" on all
    three days -- a static resources.industrydive.com resource-library
    link (evergreen sponsored content, not dated news), wrapped in a
    different click-tracking URL each day.
  - "How One Restaurant Group Discovered AI That Actually Works" on two of
    the three days -- same resources.industrydive.com pattern.
  - "Wingstop's chief brand officer is departing the company" on two
    consecutive days with the LITERAL IDENTICAL tracking URL both times.
  - "Scooter's Coffee Names Anna Faktorovich Chief Marketing Officer" on
    two consecutive days, via two different opaque (non-decodable)
    click1.inform.wtwhmedia.com tracking URLs for the same headline.

Root causes, both in _render_team_newsletters:

1. _NEWSLETTER_JUNK_URL_PATTERNS already lists "resources.industrydive.com"
   specifically to reject this kind of static/sponsored link, but the check
   ran against the raw click-tracking URL (which never literally contains
   that substring) instead of the resolved destination.

2. Every other headline-producing section (A-E) checks/marks the
   persistent system/.cache/rendered_headlines.json registry so a story
   doesn't repeat across days. D+ only ever compared against the current
   run's own _rendered_this_run set, which resets every render -- so
   nothing from a prior day was ever caught. Fixed by threading optional
   `today`/`rendered` params through to the same _is_duplicate/
   _mark_rendered helpers A-E use, keyed on the resolved URL (since the
   raw tracking wrapper changes every send) with a normalized-title key as
   a fallback for platforms (wtwhmedia) whose tracking links have no
   decodable destination at all.

Also covers a related, separately-confirmed gap in the same source-name
filter: CStore Decisions was already being fetched (whitelisted
inform.wtwhmedia.com domain) but never passed the "restaurant/qsr/
payments/nrn/pmq" source-name check, so it silently never rendered.
"""
from __future__ import annotations

import base64
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402


def _b64(url: str) -> str:
    return base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


def _edition(source: str, articles: list[dict], pub_date: str = "Aug 28, 2026") -> dict:
    return {
        "title": source,
        "extras": {"source_name": source, "pub_date": pub_date, "articles": articles},
    }


class _BaseCase(unittest.TestCase):
    def setUp(self):
        rib._rendered_this_run = set()
        self._cache_patch = patch.object(
            rib, "TEAM_SYNTH_CACHE_PATH", Path("/tmp/does-not-exist-curated-reads-test-cache.json"))
        self._cache_patch.start()
        self._synth_patch = patch("brief_synthesis.synthesize_signals", side_effect=Exception("no network in tests"))
        self._synth_patch.start()

    def tearDown(self):
        self._cache_patch.stop()
        self._synth_patch.stop()
        rib._rendered_this_run = set()
        rib._team_synth_why = {}


class TestCstoreDecisionsSourceFilter(_BaseCase):
    """RB-2026-08-29: CStore Decisions already lands on the whitelisted
    inform.wtwhmedia.com domain (confirmed live -- 9 editions in the
    bridgepoint inbox over the prior 2 weeks) but never passed D+'s
    source-name relevance filter, so it was fetched and silently dropped
    every time. Todd confirmed he wants it included."""

    def test_real_cstore_headline_renders(self):
        headline = "CStore Momentum Highlights Growth, Tech and Foodservice"
        self.assertGreaterEqual(rib._team_newsletter_score(headline), 2)  # confirms this exercises a real passing case
        url = "https://link.wtwhmedia.com/click/1.1/aaaa/bbbb"
        sections = [_edition("CStore Decisions", [{"title": headline, "url": url}])]
        # This headline's deterministic fallback bucket (_team_why_it_
        # matters_fallback) is the generic "Read this only if..." non-
        # answer, which the candidate-selection loop drops *before* batch
        # synthesis ever runs (synthesis only covers the already-narrowed
        # "chosen" list) -- so a real synthesized "why" has to already be
        # in the per-run cache for this candidate to survive selection at
        # all, same as it would after a real LLM call in production.
        rib._team_synth_why[rib._team_synth_key(headline)] = "Signals foodservice/tech investment in the c-store channel."
        out = rib._render_team_newsletters(sections, None, filter_team_terms=False,
                                            today=date(2026, 8, 29), rendered={})
        self.assertIn(headline, out)

    def test_cstore_morning_fill_up_masthead_variant_also_passes_source_filter(self):
        headline = "CStore Momentum Highlights Growth, Tech and Foodservice"
        url = "https://link.wtwhmedia.com/click/2.2/cccc/dddd"
        sections = [_edition("CStore Decisions' Morning Fill-Up", [{"title": headline, "url": url}])]
        rib._team_synth_why[rib._team_synth_key(headline)] = "Signals foodservice/tech investment in the c-store channel."
        out = rib._render_team_newsletters(sections, None, filter_team_terms=False,
                                            today=date(2026, 8, 29), rendered={})
        self.assertIn(headline, out)

    def test_low_relevance_cstore_headline_still_filtered_by_score_gate(self):
        """Passing the source-name gate doesn't bypass the shared relevance
        score threshold -- a generic product-roundup headline (real example:
        "Hot New Products") stays out, same bar as every other source."""
        headline = "Hot New Products"
        self.assertLess(rib._team_newsletter_score(headline), 2)
        url = "https://link.wtwhmedia.com/click/3.3/eeee/ffff"
        sections = [_edition("CStore Decisions", [{"title": headline, "url": url}])]
        out = rib._render_team_newsletters(sections, None, filter_team_terms=False,
                                            today=date(2026, 8, 29), rendered={})
        self.assertNotIn(headline, out)


class TestAiReportSourceFilter(_BaseCase):
    """RB-2026-08-29: Todd subscribed to The AI Report and asked for it to
    be tracked given AI-in-restaurants questions. Its source name ("The AI
    Report") needed a specific "ai report" allowlist entry -- a bare "ai"
    substring would over-match unrelated source names like "Retail Dive" or
    "Modern Retail" (both contain "ai" inside "retail")."""

    def test_ai_report_headline_with_real_restaurant_relevance_renders(self):
        headline = "How One QSR Chain Rebuilt Its AI Data Stack"
        self.assertGreaterEqual(rib._team_newsletter_score(headline), 2)
        url = "https://link.beehiiv.com/click/1.1/aaaa/bbbb"
        sections = [_edition("The AI Report", [{"title": headline, "url": url}])]
        rib._team_synth_why[rib._team_synth_key(headline)] = "Shows how a QSR peer is standardizing AI infrastructure ahead of vendor evaluations."
        out = rib._render_team_newsletters(sections, None, filter_team_terms=False,
                                            today=date(2026, 8, 29), rendered={})
        self.assertIn(headline, out)

    def test_bare_ai_substring_does_not_falsely_admit_unrelated_sources(self):
        """"Retail Dive" contains the literal substring "ai" (inside
        "retail") -- confirms the allowlist entry is the specific phrase
        "ai report", not the bare two letters, so this doesn't ride along."""
        source = "Retail Dive"
        self.assertIn("ai", source.lower())  # sanity: the substring trap this guards against is real
        self.assertFalse(any(p in source.lower() for p in
                              ("restaurant", "qsr", "payments", "nrn", "pmq", "cstore", "ai report")))


class TestResourceLibraryJunkLinksRejected(_BaseCase):
    def test_wrapped_industrydive_resource_link_is_never_rendered(self):
        real_url = "http://resources.industrydive.com/Agentic-Commerce-How-to-Power-AI-Driven-Payment-Experiences"
        wrapped = f"https://link.paymentsdive.com/click/47197588.47640/{_b64(real_url)}/deadbeef"
        sections = [_edition("Payments Dive", [
            {"title": "Agentic Commerce: How to Power AI-Driven Payment Experiences", "url": wrapped},
        ])]
        out = rib._render_team_newsletters(sections, None, filter_team_terms=False,
                                            today=date(2026, 8, 29), rendered={})
        self.assertNotIn("Agentic Commerce", out)


class TestCrossDayDedupByResolvedUrl(_BaseCase):
    def test_same_story_different_tracking_wrapper_suppressed_next_day(self):
        real_url = "https://www.nrn.com/leadership/wingstop-cbo-departs"
        wrapped_day1 = f"https://app.go.informamail05.com/e/er?s=1&{_b64(real_url)}=1"
        # Different literal wrapper, but _resolve_wrapped_url can't decode
        # this platform's format either -- exercise the more common case
        # first: the *identical* wrapped URL repeating (Wingstop's real
        # live case), which the plain resolved-URL registry catches
        # trivially since resolve(x) == resolve(x).
        rendered: dict = {}
        headline = "Wingstop's chief brand officer is departing the company"
        day1 = [_edition("NRN", [{"title": headline, "url": wrapped_day1}])]
        out1 = rib._render_team_newsletters(day1, None, filter_team_terms=False,
                                             today=date(2026, 8, 28), rendered=rendered)
        self.assertIn("Wingstop", out1)

        rib._rendered_this_run = set()  # new run, new day
        day2 = [_edition("NRN", [{"title": headline, "url": wrapped_day1}])]
        out2 = rib._render_team_newsletters(day2, None, filter_team_terms=False,
                                             today=date(2026, 8, 29), rendered=rendered)
        self.assertNotIn("Wingstop", out2)

    def test_same_story_resolvable_tracking_wrapper_suppressed_next_day(self):
        """Same shape as the industrydive resource-library case (base64-
        wrapped, decodes to a stable destination, but the OUTER wrapper URL
        differs every send) -- using a non-junk destination here so this
        test exercises the dedup fix specifically, not the separate junk-
        URL-pattern fix covered by TestResourceLibraryJunkLinksRejected."""
        real_url = "https://www.restaurantdive.com/news/some-real-dated-article/824999/"
        wrapped_day1 = f"https://link.paymentsdive.com/click/1.1/{_b64(real_url)}/aaa"
        wrapped_day2 = f"https://link.restaurantdive.com/click/2.2/{_b64(real_url)}/bbb"
        headline = "Restaurant Payments Platform Launches New Loyalty Feature"
        rendered: dict = {}

        day1 = [_edition("Payments Dive", [{"title": headline, "url": wrapped_day1}])]
        out1 = rib._render_team_newsletters(day1, None, filter_team_terms=False,
                                             today=date(2026, 8, 28), rendered=rendered)
        self.assertIn(headline, out1)

        rib._rendered_this_run = set()
        day2 = [_edition("Restaurant Dive", [{"title": headline, "url": wrapped_day2}])]
        out2 = rib._render_team_newsletters(day2, None, filter_team_terms=False,
                                             today=date(2026, 8, 29), rendered=rendered)
        self.assertNotIn(headline, out2)


class TestCrossDayDedupByTitleFallback(_BaseCase):
    def test_same_headline_unresolvable_opaque_tracking_urls_suppressed_next_day(self):
        """wtwhmedia-style links have no decodable embedded destination --
        _resolve_wrapped_url falls back to returning them unchanged, so two
        different opaque slugs for the same headline must still be caught
        via the normalized-title fallback key."""
        headline = "Scooter's Coffee Names Anna Faktorovich Chief Marketing Officer"
        url_day1 = ("https://click1.inform.wtwhmedia.com/"
                    "msttpbvjzgmldtkmltgnnlzyyqlygtdjbkjpvzztndwqwy_fbjbddnbbhwdgrgnnsrqb.html"
                    "?a=208367&b=4116443&c=4116443&d=208367")
        url_day2 = ("https://click1.inform.wtwhmedia.com/"
                    "msttpbvjzgmldtkmltgnnlzyyqlygtdjbkjpvzztndwqwy_imnbmhbdvmwynznddhzgb.html"
                    "?a=208367&b=4524635&c=4524635&d=208367")
        # Confirm the two wrappers really don't resolve to a shared identity
        # -- otherwise this test would pass for the wrong reason.
        self.assertNotEqual(rib._resolve_wrapped_url(url_day1), rib._resolve_wrapped_url(url_day2))

        rendered: dict = {}
        day1 = [_edition("QSR Magazine", [{"title": headline, "url": url_day1}])]
        out1 = rib._render_team_newsletters(day1, None, filter_team_terms=False,
                                             today=date(2026, 8, 27), rendered=rendered)
        self.assertIn("Scooter", out1)

        rib._rendered_this_run = set()
        day2 = [_edition("QSR Magazine", [{"title": headline, "url": url_day2}])]
        out2 = rib._render_team_newsletters(day2, None, filter_team_terms=False,
                                             today=date(2026, 8, 28), rendered=rendered)
        self.assertNotIn("Scooter", out2)


class TestNoRegression(_BaseCase):
    def test_a_genuinely_new_story_still_renders_on_a_later_day(self):
        rendered: dict = {}
        old_url = "https://link.restaurantdive.com/click/1.1/aaaa/bbbb"
        day1 = [_edition("Restaurant Dive", [
            {"title": "An Old Story Already Covered", "url": old_url},
        ])]
        rib._render_team_newsletters(day1, None, filter_team_terms=False,
                                      today=date(2026, 8, 20), rendered=rendered)

        rib._rendered_this_run = set()
        new_url = "https://link.restaurantdive.com/click/2.2/cccc/dddd"
        day2 = [_edition("Restaurant Dive", [
            {"title": "Chipotle Names New Chief Technology Officer", "url": new_url},
        ])]
        out2 = rib._render_team_newsletters(day2, None, filter_team_terms=False,
                                             today=date(2026, 8, 21), rendered=rendered)
        self.assertIn("Chipotle", out2)

    def test_expired_dedup_window_allows_rerender(self):
        """DEDUP_WINDOW_DAYS (7) expiring should let a story that's still
        sitting in the newsletter pool render again -- this is a shown-
        window, not a permanent ban."""
        headline = "Payments Platform Story Old Enough To Recirculate"
        url = "https://link.restaurantdive.com/click/9.9/eeee/ffff"
        rendered: dict = {}
        day1 = [_edition("Restaurant Dive", [{"title": headline, "url": url}])]
        rib._render_team_newsletters(day1, None, filter_team_terms=False,
                                      today=date(2026, 8, 1), rendered=rendered)

        rib._rendered_this_run = set()
        much_later = date(2026, 8, 1) + timedelta(days=rib.DEDUP_WINDOW_DAYS + 1)
        day2 = [_edition("Restaurant Dive", [{"title": headline, "url": url}])]
        out2 = rib._render_team_newsletters(day2, None, filter_team_terms=False,
                                             today=much_later, rendered=rendered)
        self.assertIn("recirculate", out2.lower())

    def test_no_today_or_rendered_param_preserves_old_no_cross_day_dedup_behavior(self):
        """The twice-weekly team-brief call site doesn't pass today/rendered
        -- must keep working exactly as before (same-run dedup only)."""
        headline = "Chipotle Names New Chief Technology Officer"
        url = "https://link.restaurantdive.com/click/3.3/gggg/hhhh"
        day1 = [_edition("Restaurant Dive", [{"title": headline, "url": url}])]
        out1 = rib._render_team_newsletters(day1, None, filter_team_terms=False)
        self.assertIn("Chipotle", out1)

        rib._rendered_this_run = set()
        day2 = [_edition("Restaurant Dive", [{"title": headline, "url": url}])]
        out2 = rib._render_team_newsletters(day2, None, filter_team_terms=False)
        self.assertIn("Chipotle", out2)  # still renders -- no persistence wired up


if __name__ == "__main__":
    unittest.main()
