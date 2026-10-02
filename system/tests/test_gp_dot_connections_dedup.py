"""
test_gp_dot_connections_dedup.py

Regression coverage: GP/Genius: Dot Connections deduplicated bullets by
keying on bullet[:80] -- the first 80 characters of the rendered markdown
line. Since a bullet's markdown is "**[Title](URL)** — dot", the key is
quickly dominated by the URL once the title is short. The same headline
picked up from two different source pools (the general headline scan and
the newsletter-article scan) can carry two different URLs for the same
story, so both survived as "different" bullets -- confirmed live: "Adyen
promotes executives" rendered twice in one brief.

Fixed by deduping on the normalized story identity (bare title / ticker)
captured at each append site, not a truncated rendering of the final
markdown line.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_daily_brief as rdb  # noqa: E402

# RB-2026-07-17: _render_gp_dot_connections now attempts an LLM synthesis
# call (brief_synthesis.py) for any bullet with no deterministic
# role-context match -- every test below exercises the deterministic
# fallback path and must not depend on (or make a live call using)
# whatever OPENAI_API_KEY happens to be set in the runner's environment.
_no_openai_key = patch.dict(os.environ, {}, clear=True)


def _headline_item(title: str, url: str) -> dict:
    return {
        "title": title,
        "why_it_matters": "",
        "extras": {"source_url": url, "source_name": "Payments Dive", "signal_type": ""},
    }


def _newsletter_item(source: str, articles: list[tuple[str, str]]) -> dict:
    return {
        "title": source,
        "extras": {
            "source_name": source,
            "articles": [{"title": t, "url": u} for t, u in articles],
        },
    }


@_no_openai_key
class TestGpDotConnectionsDedup(unittest.TestCase):
    def test_same_headline_two_different_urls_renders_once(self):
        """The exact live bug: same story via two source pools with two
        different (both long, tracking-parameter-laden) URLs."""
        sections = {
            "world_national_headlines": [
                _headline_item(
                    "Adyen promotes executives",
                    "https://link.paymentsdive.com/click/46478659.54669/aHR0cHM6Ly93d3cucGF5bWVudHNkaXZlLmNvbS9uZXdzL2FkeWVuLW5hbWVzLWludGVyaW0tY2ZvLWxlYWRlcnNoaXAtdXBkYXRlLXBheW1lbnRzLzgyNDQxNC8/6a1ad91dd5d74dcaa907d226B6cd98b01",
                ),
            ],
            "restaurant_industry_headlines": [],
            "newsletter_intelligence": [
                _newsletter_item("Payments Dive", [
                    ("Adyen promotes executives",
                     "https://link.paymentsdive.com/click/99999999.11111/DIFFERENTBASE64PAYLOADHERE1234567890/6a1ad91dd5d74dcaa907d226Bdifferenthash"),
                ]),
            ],
        }
        out = rdb._render_gp_dot_connections(sections, date(2026, 7, 9))
        self.assertEqual(out.count("Adyen promotes executives"), 1)

    def test_genuinely_different_stories_both_render(self):
        sections = {
            "world_national_headlines": [
                _headline_item("Adyen promotes executives", "https://paymentsdive.com/adyen-execs"),
                _headline_item("Fiserv president exits", "https://paymentsdive.com/fiserv-exit"),
            ],
            "restaurant_industry_headlines": [],
            "newsletter_intelligence": [],
        }
        out = rdb._render_gp_dot_connections(sections, date(2026, 7, 9))
        self.assertIn("Adyen promotes executives", out)
        self.assertIn("Fiserv president exits", out)
        self.assertEqual(out.count("- **["), 2)


@_no_openai_key
class TestGpDotConnectionsCrossDayDedup(unittest.TestCase):
    """RB-DEFECT-2026-07-14: this section had zero cross-day dedup at all --
    a story stays in its upstream feed's freshness window for several days,
    so the same bullets ("Fiserv president exits", "Fiserv debit network
    sale skepticism abounds") rendered verbatim for 3+ consecutive daily
    briefs. Now tracks first-shown date per dedup key (GP_DOT_RENDERED_PATH)
    and permanently drops a story once it's past GP_DOT_DEDUP_WINDOW_DAYS
    old, mirroring render_intelligence_brief.py's first_rendered-anchored
    fix for the identical class of bug (RB-DEFECT-2026-07-13)."""

    def _sections(self):
        return {
            "world_national_headlines": [
                _headline_item("Fiserv president exits", "https://paymentsdive.com/fiserv-exit"),
            ],
            "restaurant_industry_headlines": [],
            "newsletter_intelligence": [],
        }

    def test_first_occurrence_renders_and_registers(self):
        rendered: dict = {}
        out = rdb._render_gp_dot_connections(self._sections(), date(2026, 7, 12), rendered=rendered)
        self.assertIn("Fiserv president exits", out)
        self.assertIn("fiserv president exits", rendered)
        self.assertEqual(rendered["fiserv president exits"]["first_rendered"], "2026-07-12")

    def test_still_within_window_keeps_rendering(self):
        rendered = {"fiserv president exits": {"first_rendered": "2026-07-12"}}
        out = rdb._render_gp_dot_connections(self._sections(), date(2026, 7, 13), rendered=rendered)
        self.assertIn("Fiserv president exits", out)
        # first_rendered must not reset just because it rendered again
        self.assertEqual(rendered["fiserv president exits"]["first_rendered"], "2026-07-12")

    def test_past_window_drops_permanently(self):
        rendered = {"fiserv president exits": {"first_rendered": "2026-07-11"}}
        out = rdb._render_gp_dot_connections(self._sections(), date(2026, 7, 14), rendered=rendered)
        self.assertNotIn("Fiserv president exits", out)

    def test_no_rendered_registry_defaults_to_fresh(self):
        """Calling without a rendered dict (e.g. every existing test in this
        file) must not crash and must render normally."""
        out = rdb._render_gp_dot_connections(self._sections(), date(2026, 7, 12))
        self.assertIn("Fiserv president exits", out)


class TestGpDotConnectionsSynthesis(unittest.TestCase):
    """RB-2026-07-17: headlines with no deterministic role-context match
    (the common case -- most headlines don't touch anything on Todd's
    active board) get one batched LLM synthesis call instead of the fixed
    boilerplate ("Competitive or market signal relevant to your Genius/
    Worldpay territory -- assess customer impact."). Any failure must fall
    back to that exact boilerplate, unchanged."""

    def _sections(self):
        return {
            "world_national_headlines": [
                _headline_item("Gold Star Chili Expands Partnership with PAR Technology",
                                "https://example.com/gold-star-chili"),
            ],
            "restaurant_industry_headlines": [],
            "newsletter_intelligence": [],
        }

    def test_synthesis_unavailable_falls_back_to_current_boilerplate(self):
        with patch("brief_synthesis.synthesize_signals", return_value=None):
            out = rdb._render_gp_dot_connections(self._sections(), date(2026, 7, 17), rendered={})
        self.assertIn(rdb.GP_DOT_GENERIC_FALLBACK, out)

    def test_synthesis_success_replaces_boilerplate(self):
        def fake_synth(items, role_summary):
            return {items[0]["key"]: {
                "why": "PAR Technology winning multi-unit accounts signals share gains against POS incumbents.",
                "action": "Flag in next Genius territory review.",
            }}
        with patch("brief_synthesis.synthesize_signals", side_effect=fake_synth):
            out = rdb._render_gp_dot_connections(self._sections(), date(2026, 7, 17), rendered={})
        self.assertNotIn(rdb.GP_DOT_GENERIC_FALLBACK, out)
        self.assertIn("PAR Technology winning multi-unit accounts", out)
        self.assertIn("Flag in next Genius territory review.", out)

    def test_synthesis_result_is_cached_in_rendered_registry(self):
        """A second render call within the dedup window must reuse the
        cached synthesized text instead of calling the LLM again."""
        rendered: dict = {}
        def fake_synth(items, role_summary):
            return {items[0]["key"]: {"why": "Cached synthesis text.", "action": ""}}
        with patch("brief_synthesis.synthesize_signals", side_effect=fake_synth) as mock_synth:
            rdb._render_gp_dot_connections(self._sections(), date(2026, 7, 17), rendered=rendered)
            self.assertEqual(mock_synth.call_count, 1)
            out2 = rdb._render_gp_dot_connections(self._sections(), date(2026, 7, 17), rendered=rendered)
            self.assertEqual(mock_synth.call_count, 1)  # not called again
        self.assertIn("Cached synthesis text.", out2)

    def test_role_context_match_never_triggers_synthesis(self):
        """A signal that already has a deterministic role-context match must
        not be sent for synthesis at all -- the free/instant path always wins."""
        sections = self._sections()
        sections["opportunity_board"] = [{"extras": {"companies": "PAR Technology"}}]
        with patch("brief_synthesis.synthesize_signals") as mock_synth:
            out = rdb._render_gp_dot_connections(sections, date(2026, 7, 17), rendered={})
        mock_synth.assert_not_called()
        self.assertIn("is one of your active opportunities", out)


if __name__ == "__main__":
    unittest.main()
