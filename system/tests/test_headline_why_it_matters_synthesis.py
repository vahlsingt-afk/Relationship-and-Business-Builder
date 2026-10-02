"""
test_headline_why_it_matters_synthesis.py

RB-2026-08-25 (Gap #10): Todd's 2026-08-24 guiding principles for the
Intelligence Brief ("The CoS commentary in the newspaper should be limited
to why a piece of news is important") were never implemented for Sections
A-D -- confirmed live in the 2026-08-25 brief, every World/National/
Restaurant Industry/Restaurant Technology item rendered with only the
outlet's own RSS/press blurb, zero business-climate synthesis anywhere on
the "front page." _synthesize_personal_why_batch (mirrors the existing
_team_synthesize_batch machinery, separate cache/role-summary since the
personal reader tracks business-climate relevance, not GP/Genius sales
positioning) now populates _personal_why_synth, and _fmt_headline renders
it as a second, distinct line below the existing factual summary -- for
sections world/national/restaurant/restaurant_tech only.
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


def _item(title: str, summary: str = "", section_hint: str = "") -> dict:
    return {
        "title": title,
        "summary": summary,
        "extras": {"source_url": "https://example.com/x", "source_name": "Test Source",
                   "pub_date": "2026-08-25"},
    }


class TestFmtHeadlineRendersSynthesizedWhy(unittest.TestCase):
    def setUp(self):
        rib._rendered_this_run = set()
        rib._personal_why_synth = {}

    def test_why_line_rendered_when_synthesis_available(self):
        title = "Treasury Secretary unveils new US economic sanctions on Iran"
        rib._personal_why_synth[rib._team_synth_key(title)] = (
            "New sanctions can push oil and shipping costs higher, a direct input cost for restaurants."
        )
        out = rib._fmt_headline(_item(title, "Some outlet summary."), date(2026, 8, 25), "world", {})
        self.assertIsNotNone(out)
        self.assertIn("*Some outlet summary.*", out)  # factual line still present
        self.assertIn("**Why it matters:** New sanctions can push oil and shipping costs higher", out)

    def test_no_why_line_when_synthesis_unavailable(self):
        # section="restaurant" avoids world/national's positive relevance
        # keyword gate, which is unrelated to what this test checks.
        title = "A restaurant story with no cached synthesis"
        out = rib._fmt_headline(_item(title, "Some outlet summary."), date(2026, 8, 25), "restaurant", {})
        self.assertIsNotNone(out)
        self.assertNotIn("Why it matters:", out)

    def test_no_implication_boilerplate_suppressed(self):
        title = "A restaurant story whose synthesis found no real implication"
        rib._personal_why_synth[rib._team_synth_key(title)] = "No clear business-climate implication."
        out = rib._fmt_headline(_item(title, "Some outlet summary."), date(2026, 8, 25), "restaurant", {})
        self.assertIsNotNone(out)
        self.assertNotIn("Why it matters:", out)

    def test_why_line_applies_to_all_four_front_page_sections(self):
        # World/national use a positive relevance keyword gate; restaurant/
        # restaurant_tech expect a badge-justified title from a real trade
        # source. Each title below is tailored to clear its own section's
        # gate -- what's under test is only whether the why-line renders
        # once a candidate genuinely reaches that point, not the gates
        # themselves (covered by their own dedicated test files).
        cases = {
            "world": ("New sanctions imposed amid economic standoff",
                      {"source_url": "https://example.com/w", "source_name": "BBC", "pub_date": "2026-08-25"}),
            "national": ("Federal Reserve raises interest rates amid inflation concerns",
                         {"source_url": "https://example.com/n", "source_name": "NPR", "pub_date": "2026-08-25"}),
            "restaurant": ("[👤 EXEC HIRE] Some Chain names new chief executive",
                           {"source_url": "https://example.com/r", "source_name": "NRN",
                            "pub_date": "2026-08-25", "signal_badge": "[👤 EXEC HIRE]"}),
            "restaurant_tech": ("[✅ CUSTOMER WIN] Toast expands POS integration to nearly 900 restaurants",
                                {"source_url": "https://restauranttechnologynews.com/x",
                                 "source_name": "Restaurant Technology News", "pub_date": "2026-08-25",
                                 "signal_badge": "[✅ CUSTOMER WIN]"}),
        }
        for section, (title, extras) in cases.items():
            rib._personal_why_synth = {rib._team_synth_key(title): "A specific, grounded business-climate reason."}
            item = {"title": title, "summary": "Outlet summary.", "extras": extras}
            out = rib._fmt_headline(item, date(2026, 8, 25), section, {})
            self.assertIsNotNone(out, f"expected a rendered item for section={section}")
            self.assertIn("Why it matters:", out, f"expected why-line for section={section}")

    def test_why_line_not_applied_outside_front_page_sections(self):
        """E/F/K have their own existing why-text paths and must not get a
        second, redundant synthesized line even if a cache entry exists."""
        title = "A corporate earnings story"
        rib._personal_why_synth[rib._team_synth_key(title)] = "A specific, grounded business-climate reason."
        out = rib._fmt_headline(_item(title, "Outlet summary."), date(2026, 8, 25), "some_other_section", {})
        self.assertNotIn("Why it matters:", out)


class TestWhyLineSuppressedWhenRedundantWithSummary(unittest.TestCase):
    """RB-2026-08-25: Todd's explicit standard -- "every word on the page
    has to earn its place." A synthesized why-line that just restates the
    outlet summary directly above it in different words is bloat, not new
    information, and must be suppressed the same way a why-text that merely
    repeats the title already is."""

    def setUp(self):
        rib._rendered_this_run = set()
        rib._personal_why_synth = {}

    def test_heavily_overlapping_why_line_suppressed(self):
        title = "Wendy's names Tariq Hassan chief marketing officer"
        summary = "Wendy's names Tariq Hassan as its new chief marketing and customer growth officer."
        # A near-pure restatement of the summary -- exactly the failure mode
        # this guards against, distinct from a why-line that legitimately
        # shares the same proper nouns while adding real judgment on top.
        redundant_why = "Wendy's has appointed Tariq Hassan to be its new chief marketing and customer growth officer."
        rib._personal_why_synth[rib._team_synth_key(title)] = redundant_why
        out = rib._fmt_headline(_item(title, summary), date(2026, 8, 25), "restaurant", {})
        self.assertIn(summary, out)
        self.assertNotIn("Why it matters:", out)

    def test_genuinely_distinct_why_line_still_renders(self):
        title = "Wendy's names Tariq Hassan chief marketing officer"
        summary = "Wendy's names Tariq Hassan as its new chief marketing and customer growth officer."
        distinct_why = "A new CMO from a direct competitor can signal a coming shift in loyalty and value-menu strategy."
        rib._personal_why_synth[rib._team_synth_key(title)] = distinct_why
        out = rib._fmt_headline(_item(title, summary), date(2026, 8, 25), "restaurant", {})
        self.assertIn(summary, out)
        self.assertIn("Why it matters:", out)
        self.assertIn(distinct_why, out)

    def test_short_why_line_not_penalized_by_heuristic(self):
        """A short, punchy why-line shouldn't be killed just because it
        doesn't have enough distinct words for the overlap heuristic to
        judge reliably -- the function must fail open (render) here, not
        fail closed."""
        title = "A story with a short why"
        summary = "A long factual outlet summary describing what actually happened in detail."
        short_why = "Cost pressure ahead."
        rib._personal_why_synth[rib._team_synth_key(title)] = short_why
        out = rib._fmt_headline(_item(title, summary), date(2026, 8, 25), "restaurant", {})
        self.assertIn("Why it matters:", out)


class TestRenderHeadlineSectionBatchesSynthesis(unittest.TestCase):
    def setUp(self):
        rib._rendered_this_run = set()
        rib._rendered_subjects_this_run = set()
        rib._personal_why_synth = {}

    def test_synthesis_batch_called_with_scope_filtered_titles_only(self):
        world_item = {"title": "A world story", "summary": "s1",
                      "extras": {"source_url": "https://example.com/1", "source_name": "BBC",
                                 "pub_date": "2026-08-25", "entities": []}}
        us_item = {"title": "A national story", "summary": "s2",
                   "extras": {"source_url": "https://example.com/2", "source_name": "NPR",
                              "pub_date": "2026-08-25", "entities": []}}
        with patch.object(rib.core, "classify_world_national_scope",
                           side_effect=lambda it: "world" if it is world_item else "us"), \
             patch.object(rib, "_synthesize_personal_why_batch") as mock_synth:
            rib._render_headline_section(
                [world_item, us_item], "A: World Headlines", "world",
                date(2026, 8, 25), {}, scope_filter="world")
        called_titles = {c["title"] for c in mock_synth.call_args[0][0]}
        self.assertEqual(called_titles, {"A world story"})  # us_item excluded by scope_filter

    def test_end_to_end_why_line_present_in_rendered_section_output(self):
        title = "A restaurant industry story about labor costs"
        rib._personal_why_synth[rib._team_synth_key(title)] = "Labor-cost pressure directly affects restaurant margins."
        item = {"title": title, "summary": "Factual outlet summary.",
                "extras": {"source_url": "https://example.com/labor", "source_name": "NRN",
                           "pub_date": "2026-08-25"}}
        with patch.object(rib, "_synthesize_personal_why_batch"):  # no-op, cache already seeded
            out = rib._render_headline_section(
                [item], "C: Restaurant Industry", "restaurant", date(2026, 8, 25), {})
        self.assertIn("Labor-cost pressure directly affects restaurant margins.", out)


if __name__ == "__main__":
    unittest.main()
