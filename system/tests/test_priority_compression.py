"""
test_priority_compression.py — Sprint L: Priority Compression tests.

Tests:
  PC1 — _compute_five_things returns at most 5 priority items (+ optional overflow)
  PC2 — top-5 items receive extras.five_things_rank tags on source items
  PC3 — overflow act_today items are tagged extras.five_things_overflow=True
  PC4 — overflow summary item has extras.is_overflow_summary=True
  PC5 — overflow summary item has correct overflow_count
  PC6 — overflow summary item disposition is monitor (not act_today)
  PC7 — overflow summary NOT added when ≤5 act_today items exist
  PC8 — rank tags are 1-indexed and sequential (1, 2, 3, 4, 5)
  PC9 — monitor-disposition items are not tagged five_things_overflow
  PC10 — _compute_five_things returns empty list for empty sections
  PC11 — overflow_titles in extras contains compressed item titles
  PC12 — fewer than 5 items returns all items, no overflow
"""
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from daily_brief import _compute_five_things


def _item(title: str, disposition: str = "act_today",
          grounding: str = "system_detected",
          freshness: str = "fresh",
          confidence: str = "high") -> dict:
    return {
        "title": title,
        "summary": f"Summary of {title}",
        "why_it_matters": f"Why {title} matters",
        "recommended_action": f"Do something about {title}",
        "disposition": disposition,
        "grounding": grounding,
        "freshness": freshness,
        "confidence": confidence,
        "extras": {},
    }


def _sections_with_n_act_today(n: int, section: str = "opportunity_board") -> dict:
    return {section: [_item(f"Priority item {i+1}") for i in range(n)]}


def _mixed_sections(n_act: int, n_monitor: int) -> dict:
    return {
        "opportunity_board": [_item(f"Act {i+1}", "act_today") for i in range(n_act)],
        "watchlist_intelligence": [_item(f"Monitor {i+1}", "monitor") for i in range(n_monitor)],
    }


class PC1HardCapFive(unittest.TestCase):
    def test_PC1_exactly_five_priorities_when_more_available(self):
        sections = _sections_with_n_act_today(10)
        result = _compute_five_things(sections, {})
        # At most 5 priority items + 1 optional overflow summary
        priority_items = [r for r in result if not (r.get("extras") or {}).get("is_overflow_summary")]
        self.assertLessEqual(len(priority_items), 5)

    def test_PC1_total_result_at_most_six(self):
        sections = _sections_with_n_act_today(10)
        result = _compute_five_things(sections, {})
        self.assertLessEqual(len(result), 6)

    def test_PC1_five_items_when_exactly_five_inputs(self):
        sections = _sections_with_n_act_today(5)
        result = _compute_five_things(sections, {})
        priority_items = [r for r in result if not (r.get("extras") or {}).get("is_overflow_summary")]
        self.assertEqual(len(priority_items), 5)


class PC2RankTagsOnSourceItems(unittest.TestCase):
    def test_PC2_source_items_tagged_with_five_things_rank(self):
        source_items = [_item(f"Item {i+1}") for i in range(5)]
        sections = {"opportunity_board": source_items}
        _compute_five_things(sections, {})
        ranked = [it for it in source_items if "five_things_rank" in it.get("extras", {})]
        self.assertEqual(len(ranked), 5)

    def test_PC2_rank_values_are_positive_integers(self):
        source_items = [_item(f"Item {i+1}") for i in range(5)]
        sections = {"opportunity_board": source_items}
        _compute_five_things(sections, {})
        ranks = [it["extras"]["five_things_rank"] for it in source_items]
        for r in ranks:
            self.assertIsInstance(r, int)
            self.assertGreater(r, 0)


class PC3OverflowTagging(unittest.TestCase):
    def test_PC3_overflow_act_today_items_tagged(self):
        source_items = [_item(f"Item {i+1}") for i in range(8)]
        sections = {"opportunity_board": source_items}
        _compute_five_things(sections, {})
        overflow_tagged = [it for it in source_items
                           if it.get("extras", {}).get("five_things_overflow")]
        # 8 items − 5 top = 3 overflow
        self.assertEqual(len(overflow_tagged), 3)

    def test_PC3_top_5_items_not_tagged_overflow(self):
        source_items = [_item(f"Item {i+1}") for i in range(8)]
        sections = {"opportunity_board": source_items}
        _compute_five_things(sections, {})
        top5 = [it for it in source_items if it.get("extras", {}).get("five_things_rank")]
        for it in top5:
            self.assertNotIn("five_things_overflow", it.get("extras", {}))


class PC4OverflowSummaryItem(unittest.TestCase):
    def test_PC4_overflow_summary_present_when_overflow_exists(self):
        sections = _sections_with_n_act_today(8)
        result = _compute_five_things(sections, {})
        overflow_items = [r for r in result if (r.get("extras") or {}).get("is_overflow_summary")]
        self.assertEqual(len(overflow_items), 1)

    def test_PC4_overflow_summary_not_present_when_5_or_fewer(self):
        sections = _sections_with_n_act_today(5)
        result = _compute_five_things(sections, {})
        overflow_items = [r for r in result if (r.get("extras") or {}).get("is_overflow_summary")]
        self.assertEqual(len(overflow_items), 0)


class PC5OverflowCount(unittest.TestCase):
    def test_PC5_overflow_count_is_correct(self):
        sections = _sections_with_n_act_today(9)
        result = _compute_five_things(sections, {})
        overflow = next(r for r in result if (r.get("extras") or {}).get("is_overflow_summary"))
        self.assertEqual(overflow["extras"]["overflow_count"], 4)

    def test_PC5_overflow_count_only_counts_act_today(self):
        sections = _mixed_sections(n_act=8, n_monitor=10)
        result = _compute_five_things(sections, {})
        overflow = next((r for r in result if (r.get("extras") or {}).get("is_overflow_summary")), None)
        if overflow:
            # Only act_today overflow should be counted — monitor items are not overflow
            self.assertGreaterEqual(overflow["extras"]["overflow_count"], 1)
            # Max overflow should be act_today count - 5 = 8 - 5 = 3
            self.assertLessEqual(overflow["extras"]["overflow_count"], 3)


class PC6OverflowDisposition(unittest.TestCase):
    def test_PC6_overflow_summary_disposition_is_monitor(self):
        sections = _sections_with_n_act_today(8)
        result = _compute_five_things(sections, {})
        overflow = next(r for r in result if (r.get("extras") or {}).get("is_overflow_summary"))
        self.assertEqual(overflow["disposition"], "monitor")

    def test_PC6_overflow_summary_title_mentions_count(self):
        sections = _sections_with_n_act_today(8)
        result = _compute_five_things(sections, {})
        overflow = next(r for r in result if (r.get("extras") or {}).get("is_overflow_summary"))
        self.assertIn("3", overflow["title"])  # 8 - 5 = 3


class PC7NoOverflowWhenFiveOrFewer(unittest.TestCase):
    def test_PC7_no_overflow_with_exactly_five(self):
        sections = _sections_with_n_act_today(5)
        result = _compute_five_things(sections, {})
        self.assertFalse(any((r.get("extras") or {}).get("is_overflow_summary") for r in result))

    def test_PC7_no_overflow_with_fewer_than_five(self):
        sections = _sections_with_n_act_today(3)
        result = _compute_five_things(sections, {})
        self.assertFalse(any((r.get("extras") or {}).get("is_overflow_summary") for r in result))


class PC8RankSequential(unittest.TestCase):
    def test_PC8_ranks_are_1_to_5(self):
        sections = _sections_with_n_act_today(8)
        result = _compute_five_things(sections, {})
        priority = [r for r in result if not (r.get("extras") or {}).get("is_overflow_summary")]
        ranks = sorted(r["extras"]["rank"] for r in priority)
        self.assertEqual(ranks, list(range(1, len(priority) + 1)))

    def test_PC8_rank_1_highest_score(self):
        """Rank 1 should be the highest-scoring item."""
        source_items = [_item(f"Item {i+1}") for i in range(6)]
        # Give item index 2 a boost via title keyword
        source_items[2]["title"] = "CRITICAL overdue item"
        sections = {"opportunity_board": source_items}
        result = _compute_five_things(sections, {})
        rank1 = next(r for r in result if r.get("extras", {}).get("rank") == 1)
        self.assertIn("CRITICAL", rank1["title"])


class PC9MonitorNotTaggedOverflow(unittest.TestCase):
    def test_PC9_monitor_items_not_overflow_tagged(self):
        sections = _mixed_sections(n_act=3, n_monitor=10)
        _compute_five_things(sections, {})
        monitor_items = sections["watchlist_intelligence"]
        for it in monitor_items:
            self.assertNotIn("five_things_overflow", it.get("extras", {}))


class PC10EmptySections(unittest.TestCase):
    def test_PC10_empty_sections_returns_empty(self):
        result = _compute_five_things({}, {})
        self.assertEqual(result, [])

    def test_PC10_sections_with_only_ignore_returns_empty(self):
        sections = {"opportunity_board": [_item("Ignored", "ignore") for _ in range(5)]}
        result = _compute_five_things(sections, {})
        self.assertEqual(result, [])


class PC11OverflowTitles(unittest.TestCase):
    def test_PC11_overflow_titles_in_extras(self):
        sections = _sections_with_n_act_today(8)
        result = _compute_five_things(sections, {})
        overflow = next(r for r in result if (r.get("extras") or {}).get("is_overflow_summary"))
        self.assertIn("overflow_titles", overflow["extras"])
        self.assertIsInstance(overflow["extras"]["overflow_titles"], list)
        self.assertEqual(len(overflow["extras"]["overflow_titles"]), 3)

    def test_PC11_overflow_titles_are_strings(self):
        sections = _sections_with_n_act_today(7)
        result = _compute_five_things(sections, {})
        overflow = next(r for r in result if (r.get("extras") or {}).get("is_overflow_summary"))
        for t in overflow["extras"]["overflow_titles"]:
            self.assertIsInstance(t, str)


class PC12FewerThanFive(unittest.TestCase):
    def test_PC12_three_items_returns_three(self):
        sections = _sections_with_n_act_today(3)
        result = _compute_five_things(sections, {})
        self.assertEqual(len(result), 3)

    def test_PC12_one_item_returns_one(self):
        sections = _sections_with_n_act_today(1)
        result = _compute_five_things(sections, {})
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
