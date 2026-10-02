"""
test_my_priorities_redundancy_fixes.py

RB-2026-08-25: live in the 2026-08-25 Daily Brief, three separate ways My
Priorities rendered content that didn't earn its place:

1. Every [PREP]-badged meeting item rendered a redundant "Event: {same
   title}" detail line -- the existing title-vs-detail redundancy check
   compared full strings including the [PREP] badge, which the detail text
   (built as "Event: {bare title}") never carries, so the check never
   caught it.
2. "This week's outcome: Close out overdue and due-today loops" rendered
   with an "Allocation: 100.0% -- items aligned to this outcome are
   boosted; unaligned items are dampened." detail line -- internal
   scoring-engine language leaking into reader text, the same class of
   leak already banned from Decision Queue/Bottom Line.
3. Jeff Coffland and Jeff Weaver each appeared twice: once as a plain
   "Relationship at risk: {name}" item and once as a richer "↘ {name}"
   relationship-decay-alert item -- two different upstream generators
   flagging the same person under different title text, so the existing
   exact-title dedup never caught it.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_daily_brief as rdb  # noqa: E402


class TestEventDuplicateLineSuppressed(unittest.TestCase):
    def test_badged_meeting_prep_item_does_not_repeat_as_event_line(self):
        sections = {"my_priorities": [
            {"title": "[PREP] [GP] Connect to Thrive: Turning barriers into opportunities",
             "summary": "Event: [GP] Connect to Thrive: Turning barriers into opportunities"},
        ]}
        out = rdb._render_my_priorities(sections)
        self.assertIn("[PREP] [GP] Connect to Thrive: Turning barriers into opportunities", out)
        self.assertNotIn("Event: [GP] Connect to Thrive", out)

    def test_genuinely_additive_detail_still_renders(self):
        sections = {"my_priorities": [
            {"title": "[PREP] [GP] Genius Restaurant Team - Bookings Review",
             "summary": "3 attendees confirmed; agenda not yet shared."},
        ]}
        out = rdb._render_my_priorities(sections)
        self.assertIn("3 attendees confirmed", out)


class TestInternalScoringJargonSuppressed(unittest.TestCase):
    def test_allocation_boilerplate_suppressed_title_kept(self):
        sections = {"my_priorities": [
            {"title": "This week's outcome: Close out overdue and due-today loops",
             "summary": "Allocation: 100.0% — items aligned to this outcome are boosted; "
                         "unaligned items are dampened."},
        ]}
        out = rdb._render_my_priorities(sections)
        self.assertIn("This week's outcome: Close out overdue and due-today loops", out)
        self.assertNotIn("boosted", out)
        self.assertNotIn("dampened", out)
        self.assertNotIn("Allocation:", out)


class TestRelationshipAtRiskDedupedByName(unittest.TestCase):
    def test_generic_and_decay_alert_variants_for_same_person_collapse_to_one(self):
        sections = {"my_priorities": [
            {"title": "Relationship at risk: Jeff Coffland",
             "summary": "Jeff Coffland (RC) — recency index 0.14. Re-engage this week before the relationship goes cold."},
            {"title": "Relationship decay alert: ↘ Jeff Coffland",
             "extras": {"days_since_last_contact": 40}},
        ]}
        out = rdb._render_my_priorities(sections)
        # One bullet line for Jeff Coffland, not two.
        bullet_lines = [ln for ln in out.splitlines() if ln.startswith("-") and "Jeff Coffland" in ln]
        self.assertEqual(len(bullet_lines), 1)
        self.assertIn("↘", out)  # the richer decay-alert format is the one kept

    def test_person_flagged_only_generically_still_renders(self):
        sections = {"my_priorities": [
            {"title": "Relationship at risk: David Kaun",
             "summary": "David Kaun (LKI) — recency index 0.30. Re-engage this week."},
        ]}
        out = rdb._render_my_priorities(sections)
        self.assertIn("David Kaun", out)

    def test_order_independent_generic_first_still_dedupes(self):
        """Richer format must win regardless of which order the two
        upstream items happen to appear in."""
        sections = {"my_priorities": [
            {"title": "Relationship at risk: Jeff Weaver",
             "summary": "Jeff Weaver (LKI) — recency index 0.20."},
            {"title": "↘ Jeff Weaver", "extras": {"days_since_last_contact": 55}},
        ]}
        out = rdb._render_my_priorities(sections)
        bullet_lines = [ln for ln in out.splitlines() if ln.startswith("-") and "Jeff Weaver" in ln]
        self.assertEqual(len(bullet_lines), 1)


if __name__ == "__main__":
    unittest.main()
