"""
test_entity_alerts_press_release_headline_match.py

RB-DEFECT-2026-07-10: Section F (Watchlist) attributed "Amazon Web Services
-- Press release: Sofinnova Partners Announces Myricx Bio Agrees to Be
Acquired by Novartis" -- a totally unrelated biotech M&A press release with
zero real AWS relevance. Google News's exact-phrase search for an entity name
matches anywhere in a press release's indexed content, including boilerplate
"About [Company]" sections that mention an unrelated cloud/infra partner in
passing. Fixed by requiring the entity name to actually appear in the
headline -- a release genuinely about the entity will name it there.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import entity_alerts as ea  # noqa: E402


def _rss(items: list[tuple[str, str]]) -> bytes:
    entries = "".join(
        f"<item><title>{title}</title><link>{url}</link>"
        f"<pubDate>Fri, 10 Jul 2026 09:00:00 GMT</pubDate></item>"
        for title, url in items
    )
    return f"<?xml version='1.0'?><rss><channel>{entries}</channel></rss>".encode()


class TestPressReleaseHeadlineMatch(unittest.TestCase):
    def test_incidental_mention_not_in_headline_excluded(self):
        xml = _rss([
            ("Sofinnova Partners Announces Myricx Bio Agrees to Be Acquired by Novartis - Business Wire",
             "https://businesswire.com/myricx-novartis"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Amazon Web Services")
        self.assertEqual(out, [])

    def test_genuine_press_release_with_entity_in_headline_kept(self):
        xml = _rss([
            ("Amazon Web Services Announces New Restaurant Industry Partnership - PR Newswire",
             "https://prnewswire.com/aws-restaurant-partnership"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Amazon Web Services")
        self.assertEqual(len(out), 1)
        self.assertIn("Amazon Web Services", out[0]["title"])

    def test_case_insensitive_headline_match(self):
        xml = _rss([
            ("pizza ranch selects ncr voyix to power next-generation restaurant operations",
             "https://businesswire.com/pizza-ranch-ncr"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("NCR Voyix")
        self.assertEqual(len(out), 1)


class TestAmbiguousCommonWordEntityNames(unittest.TestCase):
    """RB-DEFECT-2026-07-10b: "Fourth" and "Square" (real watchlist entities)
    are also common English words. The headline-match fix alone still let
    "Sandisk to Report Fiscal Fourth Quarter and Fiscal Year 2026 Results"
    through for entity "Fourth" -- a coincidental ordinal-word match, not
    the POS company. Reject the specific idioms that dominate false
    positives for each name."""

    def test_fiscal_fourth_quarter_rejected_for_fourth(self):
        xml = _rss([
            ("Sandisk to Report Fiscal Fourth Quarter and Fiscal Year 2026 Results on August 5, 2026 - GlobeNewswire",
             "https://globenewswire.com/sandisk-q4"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Fourth")
        self.assertEqual(out, [])

    def test_q4_shorthand_rejected_for_fourth(self):
        xml = _rss([
            ("Acme Corp Reports Q4 Results - PR Newswire", "https://prnewswire.com/acme-q4"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Fourth")
        self.assertEqual(out, [])

    def test_genuine_fourth_press_release_still_kept(self):
        xml = _rss([
            ("Fourth Announces New POS Integration for Restaurant Operators - Business Wire",
             "https://businesswire.com/fourth-pos"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Fourth")
        self.assertEqual(len(out), 1)

    def test_town_square_rejected_for_square(self):
        xml = _rss([
            ("New Restaurant Opens on Main Street Near Town Square - PR Newswire",
             "https://prnewswire.com/town-square-restaurant"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Square")
        self.assertEqual(out, [])

    def test_square_footage_rejected_for_square(self):
        xml = _rss([
            ("Chain Expands With New 5,000 Square Feet Flagship Location - Business Wire",
             "https://businesswire.com/flagship-square-feet"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Square")
        self.assertEqual(out, [])

    def test_genuine_square_press_release_still_kept(self):
        xml = _rss([
            ("Square Announces New Restaurant POS Partnership - PR Newswire",
             "https://prnewswire.com/square-pos-partnership"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Square")
        self.assertEqual(len(out), 1)


class TestSquareProperNounCollisionRejected(unittest.TestCase):
    """RB-DEFECT-2026-07-16: "Longacre Square Partners" (an unrelated PE/
    real-estate firm) was surfaced in the Watchlist as a "Square" (the
    payments company) press release -- the idiom list only covered generic
    place-name phrases ("town square"), not other proper nouns that happen
    to contain "Square", and firm-name suffixes ("Partners", "Capital",
    "Ventures") are a strong tell it's a different company's name."""

    def test_longacre_square_partners_rejected_for_square(self):
        xml = _rss([
            ("Longacre Square Partners Announces New Leadership Appointments to Support Ongoing Growth - Business Wire",
             "https://businesswire.com/longacre-square-partners"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Square")
        self.assertEqual(out, [])

    def test_union_square_ventures_rejected_for_square(self):
        xml = _rss([
            ("Startup Raises Funding From Union Square Ventures - Tech Funding News",
             "https://businesswire.com/union-square-ventures-raise"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Square")
        self.assertEqual(out, [])

    def test_hyphenated_square_footage_rejected_for_square(self):
        xml = _rss([
            ("Chain Expands With New 5,000-Square-Foot Flagship Location - Business Wire",
             "https://businesswire.com/flagship-square-foot-hyphenated"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Square")
        self.assertEqual(out, [])

    def test_genuine_square_press_release_still_kept_alongside_new_guards(self):
        xml = _rss([
            ("Square Names New Chief Product Officer - Business Wire",
             "https://businesswire.com/square-cpo"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Square")
        self.assertEqual(len(out), 1)


class TestLegionBrandCollisionRejected(unittest.TestCase):
    """RB-DEFECT-2026-07-17: watchlist entity "Legion" (restaurant workforce-
    management software) matched "Lenovo Launches Legion R9000P Featuring
    World's First Inkjet-printed OLED Display" -- Lenovo's "Legion" gaming
    laptop line shares the brand name but has nothing to do with the actual
    company. Same collision class as Fourth/Square: reject the specific
    idiom that dominates false positives for this name."""

    def test_lenovo_legion_laptop_rejected_for_legion(self):
        xml = _rss([
            ("Lenovo Launches Legion R9000P Featuring World's First Inkjet-printed OLED Display from TCL CSOT - PR Newswire",
             "https://prnewswire.com/lenovo-legion-r9000p"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Legion")
        self.assertEqual(out, [])

    def test_legion_5i_gaming_laptop_rejected_for_legion(self):
        xml = _rss([
            ("Lenovo Legion 5i Gaming Laptop Now Available Worldwide - Business Wire",
             "https://businesswire.com/legion-5i-launch"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Legion")
        self.assertEqual(out, [])

    def test_genuine_legion_press_release_still_kept(self):
        xml = _rss([
            ("Legion Technologies Unveils Spring 2026 Release With 90+ New Innovations Across Workforce Management - Business Wire",
             "https://businesswire.com/legion-workforce-release"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Legion")
        self.assertEqual(len(out), 1)

    def test_royal_canadian_legion_veterans_charity_rejected_for_legion(self):
        """RB-DEFECT-2026-07-22: same collision class as Lenovo, a different
        real-world "Legion" -- the Royal Canadian Legion veterans' charity --
        surfaced under the workforce-management company's watchlist entry."""
        xml = _rss([
            ("Royal Canadian Legion Launches Annual Poppy Campaign to Support Veterans - PR Newswire",
             "https://prnewswire.com/royal-canadian-legion-poppy"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Legion")
        self.assertEqual(out, [])

    def test_american_legion_veterans_org_rejected_for_legion(self):
        xml = _rss([
            ("American Legion Post 142 Hosts Community Fundraiser - Business Wire",
             "https://businesswire.com/american-legion-post-142"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Legion")
        self.assertEqual(out, [])

    def test_legion_nationals_youth_sports_rejected_for_legion(self):
        """RB-DEFECT-2026-07-23: slipped past the "royal canadian legion"
        idiom -- this is the Royal Canadian Legion's sponsored youth sports
        championship, named without "Royal Canadian" in front of it."""
        xml = _rss([
            ("2028-29 Legion Nationals host city revealed: Nanaimo is next - GlobeNewswire",
             "https://globenewswire.com/legion-nationals-nanaimo"),
        ])
        with patch.object(ea, "_fetch_with_ua", return_value=xml):
            out = ea._fetch_press_releases("Legion")
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
