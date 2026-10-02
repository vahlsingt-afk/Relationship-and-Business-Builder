"""
test_what_rb_found_cross_signal_synthesis.py — RB-2026-09-19.

_what_rb_found_v1_additional_sources() used to append three independent
items (best post-earnings hit, watchlist escalations, fresh strategic-
signal evidence) with no cross-referencing -- an entity independently
surfaced by two or three of those sources on the same day was listed once
per source with nothing indicating the sources agreed. Both the
2026-08-28 and 2026-09-19 strategic assessments flagged this specific
section (named for "connecting dots") as the clearest case of the
"synthesis is thin" gap still being true.

Fix: candidates are grouped by entity before being added; an entity
qualifying from >=2 distinct sources is folded into one
"[CONNECTED] <entity> — <sources>" item instead of being listed per source.
Deliberately scoped to only these three sources -- not a duplicate of
connect_the_dots's separate, broader IntelligenceDB convergence engine.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402


def _earnings_item(company: str, title: str = "Q3 earnings beat") -> dict:
    return db._canonical_item(
        title=title,
        summary=f"{company} reported {title}.",
        disposition="monitor",
        source_refs=["earnings_monitor"],
        extras={
            "earnings_type": "post_earnings_signal",
            "company": company,
            "strategic_relevance": "high",
        },
    )


def _watchlist_item(entity_name: str, title: str = "Escalation: leadership change") -> dict:
    return db._canonical_item(
        title=title,
        summary=f"{entity_name} watchlist status escalated.",
        disposition="act_today",
        source_refs=["watchlist_intelligence"],
        extras={"entity_name": entity_name},
    )


def _strategic_item(companies: list[str], title: str = "Multiple-source convergence: AI ordering") -> dict:
    return db._canonical_item(
        title=title,
        summary="Cross-source convergence on a tracked theme.",
        disposition="monitor",
        source_refs=["strategic_events"],
        extras={
            "entities": {"companies": companies, "people": []},
            "days_since_evidence": 0,
        },
    )


def _base_sections(**overrides) -> dict:
    sections = {
        "what_rb_found_without_you_telling_it": [],
        "earnings_intelligence": [],
        "watchlist_intelligence": [],
        "strategic_industry_signals": [],
    }
    sections.update(overrides)
    return sections


class TestSingleSourceUnchanged(unittest.TestCase):
    """An entity qualifying from only ONE source must behave exactly as
    before -- added as-is, no synthesis, no [CONNECTED] wrapping."""

    def test_single_watchlist_item_added_unchanged(self):
        sections = _base_sections(watchlist_intelligence=[_watchlist_item("Chipotle")])
        db._what_rb_found_v1_additional_sources(sections)
        target = sections["what_rb_found_without_you_telling_it"]
        self.assertEqual(len(target), 1)
        self.assertNotIn("[CONNECTED]", target[0]["title"])
        self.assertEqual(target[0]["extras"]["entity_name"], "Chipotle")

    def test_single_earnings_item_added_unchanged(self):
        sections = _base_sections(earnings_intelligence=[_earnings_item("Wingstop")])
        db._what_rb_found_v1_additional_sources(sections)
        target = sections["what_rb_found_without_you_telling_it"]
        self.assertEqual(len(target), 1)
        self.assertNotIn("[CONNECTED]", target[0]["title"])


class TestCrossSourceSynthesis(unittest.TestCase):
    """The actual fix: an entity hit by >=2 distinct sources gets ONE
    combined item, not one per source."""

    def test_entity_in_two_sources_produces_one_connected_item(self):
        sections = _base_sections(
            watchlist_intelligence=[_watchlist_item("Chipotle")],
            strategic_industry_signals=[_strategic_item(["Chipotle"])],
        )
        db._what_rb_found_v1_additional_sources(sections)
        target = sections["what_rb_found_without_you_telling_it"]
        self.assertEqual(len(target), 1, f"expected exactly one folded item, got {target}")
        item = target[0]
        self.assertIn("[CONNECTED]", item["title"])
        self.assertIn("Chipotle", item["title"])
        self.assertEqual(set(item["extras"]["connected_sources"]), {"watchlist", "strategic"})

    def test_entity_in_all_three_sources_has_high_confidence(self):
        sections = _base_sections(
            earnings_intelligence=[_earnings_item("Chipotle")],
            watchlist_intelligence=[_watchlist_item("Chipotle")],
            strategic_industry_signals=[_strategic_item(["Chipotle"])],
        )
        db._what_rb_found_v1_additional_sources(sections)
        target = sections["what_rb_found_without_you_telling_it"]
        self.assertEqual(len(target), 1)
        item = target[0]
        self.assertEqual(set(item["extras"]["connected_sources"]), {"earnings", "watchlist", "strategic"})
        self.assertEqual(item["confidence"], "high")
        self.assertEqual(item["disposition"], "act_today")

    def test_entity_name_matching_is_case_insensitive(self):
        sections = _base_sections(
            watchlist_intelligence=[_watchlist_item("Chipotle")],
            strategic_industry_signals=[_strategic_item(["CHIPOTLE"])],
        )
        db._what_rb_found_v1_additional_sources(sections)
        target = sections["what_rb_found_without_you_telling_it"]
        self.assertEqual(len(target), 1)
        self.assertIn("[CONNECTED]", target[0]["title"])

    def test_two_different_entities_do_not_cross_contaminate(self):
        """Chipotle qualifies from 2 sources; Wingstop qualifies from only
        1 -- must produce one connected item (Chipotle) and one unchanged
        item (Wingstop), never merge the two entities together."""
        sections = _base_sections(
            watchlist_intelligence=[_watchlist_item("Chipotle"), _watchlist_item("Wingstop", title="Escalation: pricing")],
            strategic_industry_signals=[_strategic_item(["Chipotle"])],
        )
        db._what_rb_found_v1_additional_sources(sections)
        target = sections["what_rb_found_without_you_telling_it"]
        self.assertEqual(len(target), 2)
        connected = [i for i in target if "[CONNECTED]" in i["title"]]
        unchanged = [i for i in target if "[CONNECTED]" not in i["title"]]
        self.assertEqual(len(connected), 1)
        self.assertIn("Chipotle", connected[0]["title"])
        self.assertEqual(len(unchanged), 1)
        self.assertEqual(unchanged[0]["extras"]["entity_name"], "Wingstop")

    def test_unrelated_entities_across_sources_are_not_folded(self):
        """No shared entity at all -- both items must be added independently,
        exactly as before this fix."""
        sections = _base_sections(
            watchlist_intelligence=[_watchlist_item("Chipotle")],
            strategic_industry_signals=[_strategic_item(["Sweetgreen"])],
        )
        db._what_rb_found_v1_additional_sources(sections)
        target = sections["what_rb_found_without_you_telling_it"]
        self.assertEqual(len(target), 2)
        self.assertFalse(any("[CONNECTED]" in i["title"] for i in target))

    def test_connected_item_source_refs_combine_both_sources(self):
        sections = _base_sections(
            watchlist_intelligence=[_watchlist_item("Chipotle")],
            strategic_industry_signals=[_strategic_item(["Chipotle"])],
        )
        db._what_rb_found_v1_additional_sources(sections)
        refs = sections["what_rb_found_without_you_telling_it"][0]["source_refs"]
        self.assertIn("watchlist_intelligence", refs)
        self.assertIn("strategic_events", refs)


class TestExistingFilteringUnaffected(unittest.TestCase):
    """The pre-existing per-source filters (act_today-only, loop-exclusion,
    fresh-evidence-only) must still apply before an item is even considered
    for cross-referencing."""

    def test_watchlist_item_without_act_today_is_excluded(self):
        item = _watchlist_item("Chipotle")
        item["disposition"] = "monitor"
        sections = _base_sections(
            watchlist_intelligence=[item],
            strategic_industry_signals=[_strategic_item(["Chipotle"])],
        )
        db._what_rb_found_v1_additional_sources(sections)
        target = sections["what_rb_found_without_you_telling_it"]
        # Only the strategic item qualifies -- no cross-source match possible.
        self.assertEqual(len(target), 1)
        self.assertNotIn("[CONNECTED]", target[0]["title"])

    def test_overdue_loop_watchlist_item_still_excluded_even_with_match(self):
        item = _watchlist_item("Chipotle")
        item["extras"]["loop_id"] = "L-2026-01-01-001"
        sections = _base_sections(
            watchlist_intelligence=[item],
            strategic_industry_signals=[_strategic_item(["Chipotle"])],
        )
        db._what_rb_found_v1_additional_sources(sections)
        target = sections["what_rb_found_without_you_telling_it"]
        self.assertEqual(len(target), 1)
        self.assertNotIn("[CONNECTED]", target[0]["title"])

    def test_stale_strategic_signal_still_excluded_even_with_match(self):
        item = _strategic_item(["Chipotle"])
        item["extras"]["days_since_evidence"] = 5
        sections = _base_sections(
            watchlist_intelligence=[_watchlist_item("Chipotle")],
            strategic_industry_signals=[item],
        )
        db._what_rb_found_v1_additional_sources(sections)
        target = sections["what_rb_found_without_you_telling_it"]
        self.assertEqual(len(target), 1)
        self.assertNotIn("[CONNECTED]", target[0]["title"])


if __name__ == "__main__":
    unittest.main()
