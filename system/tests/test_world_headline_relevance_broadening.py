"""
test_world_headline_relevance_broadening.py

Regression coverage: World/National Headlines were narrowly business-
focused because _WORLD_RELEVANCE_KEYWORDS had no oil/energy-price or
labor-market vocabulary at all, and only matched "department of justice"
spelled out (not "DOJ"). Real BBC/NPR/Axios headlines that should read as
world/national news failed the gate on that basis alone:
  - "Diesel sees biggest monthly fall in 26 years. What's happening to fuel
    prices?" — no oil/fuel keyword existed.
  - "U.S. job market slows in June" / "Jobs report gives labor market a
    yellow card" — no labor-market keyword existed.
  - "Egg producers settle with DOJ..." — only the spelled-out phrase matched.
  - "Trump says Netanyahu 'knows who the boss is'..." — no leader-name
    keyword existed for a real Middle East diplomacy story.

Broadened _WORLD_RELEVANCE_KEYWORDS to cover politics, wars, oil prices, and
consumer spending/labor-market data per user feedback, without loosening
the noise filters (_WORLD_NOISE_TITLE_PATTERNS, _ANALYST_NOTE_PATTERN) that
keep lifestyle/analyst-note content out.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402


def _passes_relevance_gate(title: str) -> bool:
    tl = title.lower()
    bare_tl = re.sub(r"^\[[^\]]+\]\s*", "", tl)
    if any(p in tl for p in rib._WORLD_NOISE_TITLE_PATTERNS):
        return False
    if rib._ANALYST_NOTE_PATTERN.search(tl):
        return False
    return any(k in bare_tl for k in rib._WORLD_RELEVANCE_KEYWORDS)


class TestWorldHeadlineRelevanceBroadening(unittest.TestCase):
    def test_oil_and_fuel_price_story_now_passes(self):
        self.assertTrue(_passes_relevance_gate(
            "Diesel sees biggest monthly fall in 26 years. What's happening to fuel prices?"
        ))

    def test_labor_market_story_now_passes(self):
        self.assertTrue(_passes_relevance_gate("U.S. job market slows in June"))
        self.assertTrue(_passes_relevance_gate("Jobs report gives labor market a yellow card"))

    def test_doj_abbreviation_now_passes(self):
        self.assertTrue(_passes_relevance_gate(
            "Egg producers settle with DOJ, states over price-fixing complaint"
        ))

    def test_world_leader_name_now_passes(self):
        self.assertTrue(_passes_relevance_gate(
            'Trump says Netanyahu "knows who the boss is" ahead of possible WH visit'
        ))

    def test_lifestyle_noise_still_excluded(self):
        self.assertFalse(_passes_relevance_gate(
            "How to eat out without spending a fortune"
        ))
        self.assertFalse(_passes_relevance_gate(
            "No-gift policy for Taylor Swift, but how much should you give at a wedding?"
        ))

    def test_analyst_note_still_excluded(self):
        self.assertFalse(_passes_relevance_gate(
            "Piper Sandler Initiates Visa Inc. (V) With Overweight Rating on Payments Strength"
        ))

    def test_real_war_coverage_still_passes(self):
        self.assertTrue(_passes_relevance_gate("Ukraine proves it can hit Russia almost anywhere"))

    def test_trump_named_alone_now_passes(self):
        """RB-DEFECT-2026-07-11: "netanyahu"/"putin"/"zelensky" were listed
        as world-leader keywords but "trump" wasn't, so a genuine US policy
        story naming only him and no other qualifying term failed the gate."""
        self.assertTrue(_passes_relevance_gate("Will Trump Accounts deliver for American children?"))


class TestWorldNationalBadgeTrustBypass(unittest.TestCase):
    """RB-DEFECT-2026-07-11: an item already badge-classified as
    ACQUISITION/FUNDING/DEPLOYMENT by daily_brief.py's upstream classifier
    was still re-filtered against _WORLD_RELEVANCE_KEYWORDS here, which
    doesn't cover every synonym the badge-specific check
    (_badge_is_justified / _BADGE_TITLE_KEYWORDS) already does. Observed
    live: "[ACQUISITION] EasyJet agrees to surprise takeover bid" was
    correctly badged but dropped since "takeover" isn't in
    _WORLD_RELEVANCE_KEYWORDS -- even though it IS in
    _BADGE_TITLE_KEYWORDS["[ACQUISITION]"] and would have passed
    _badge_is_justified a few lines later, on this same call, if the
    broader keyword gate hadn't already rejected it first. This resulted
    in Section A/B going to "0 material headlines" on a day where most of
    the pool was otherwise a legitimate repeat of yesterday's brief."""

    def _item(self, title: str) -> dict:
        return {
            "title": title,
            "summary": "",
            "extras": {"source_name": "Test Source",
                       "source_url": "https://example.com/story",
                       "pub_date": "2026-07-11"},
        }

    def test_allowed_badge_with_no_broad_keyword_still_renders(self):
        from datetime import date
        item = self._item("[🏢 ACQUISITION] EasyJet agrees to surprise takeover bid as rival US firm swoops in")
        out = rib._fmt_headline(item, date(2026, 7, 11), "world", {})
        self.assertIsNotNone(out)
        self.assertIn("EasyJet", out)

    def test_disallowed_badge_with_no_broad_keyword_still_dropped(self):
        """Only ACQUISITION/FUNDING/DEPLOYMENT bypass the gate -- an
        EXEC HIRE badge (not in _WORLD_NATIONAL_ALLOWED_BADGES) with no
        qualifying keyword must still be excluded, same as before."""
        from datetime import date
        item = self._item("[👤 EXEC HIRE] Some Local Bakery Names New Regional Manager")
        out = rib._fmt_headline(item, date(2026, 7, 11), "world", {})
        self.assertIsNone(out)

    def test_unbadged_item_with_no_keyword_still_dropped(self):
        from datetime import date
        item = self._item("Man nearly sucked out of window mid-air on Ryanair plane, passengers say")
        out = rib._fmt_headline(item, date(2026, 7, 11), "world", {})
        self.assertIsNone(out)

    def test_badge_bypass_still_subject_to_badge_justification_check(self):
        """A badge that doesn't survive _badge_is_justified (misclassified)
        must still be stripped/rejected as a badge, even though it skips
        the broad keyword gate -- the badge-trust bypass isn't a blanket
        pass-through for anything wearing a bracket label."""
        from datetime import date
        # "ACQUISITION" badge on a title with none of _BADGE_TITLE_KEYWORDS'
        # acquisition synonyms -- the badge itself is a misfire.
        item = self._item("[🏢 ACQUISITION] Local diner celebrates its 50th anniversary this weekend")
        out = rib._fmt_headline(item, date(2026, 7, 11), "world", {})
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
