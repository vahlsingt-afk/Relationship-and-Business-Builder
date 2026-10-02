"""
test_week_month_capacity_renderers.py

RB-2026-07-20: daily_brief.py computes this_week_priorities,
this_month_priorities, and capacity_plan every day, unconditionally --
but no Python renderer for any of the three existed in
render_daily_brief.py, so none of it ever reached the Daily Brief Todd
reads. Adding _render_this_week_priorities/_render_this_month_priorities/
_render_capacity_plan surfaces content that was already being computed
and silently discarded, directly answering "help schedule the
day/week/month."
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_daily_brief as rdb  # noqa: E402


class TestThisWeekPriorities(unittest.TestCase):
    def test_empty_section_shows_negative_confirmation_with_header(self):
        out = rdb._render_this_week_priorities({"this_week_priorities": []})
        self.assertIn("## This Week", out)
        self.assertIn("No near-term priorities detected.", out)

    def test_missing_section_shows_negative_confirmation_with_header(self):
        out = rdb._render_this_week_priorities({})
        self.assertIn("## This Week", out)
        self.assertIn("No near-term priorities detected.", out)

    def test_ignore_disposition_sentinel_treated_as_negative_confirmation(self):
        sections = {"this_week_priorities": [{
            "title": "No near-term (this week) priorities detected",
            "disposition": "ignore",
            "summary": "No deliverable-shaped meetings...",
        }]}
        out = rdb._render_this_week_priorities(sections)
        self.assertIn("No near-term priorities detected.", out)

    def test_real_item_renders_title_and_detail(self):
        sections = {"this_week_priorities": [{
            "title": "This week: L-2026-07-01 — Jeff Coffland",
            "disposition": "monitor",
            "why_it_matters": "Loop target: 2026-07-24.",
            "summary": "Follow up on intro.",
        }]}
        out = rdb._render_this_week_priorities(sections)
        self.assertIn("L-2026-07-01 — Jeff Coffland", out)
        self.assertIn("Loop target: 2026-07-24.", out)
        # "This week: " label prefix is stripped from the rendered title
        self.assertNotIn("This week: L-2026-07-01", out)

    def test_caps_at_eight_items(self):
        sections = {"this_week_priorities": [
            {"title": f"This week: Item {i}", "disposition": "monitor", "why_it_matters": f"Detail {i}"}
            for i in range(20)
        ]}
        out = rdb._render_this_week_priorities(sections)
        self.assertEqual(out.count("- **Item"), 8)

    def test_multiple_earnings_calls_consolidated_not_repeated_verbatim(self):
        """RB-DEFECT-2026-07-27: each pre-earnings item carries its own full
        why_it_matters boilerplate ("Earnings calls surface the most candid
        executive language about...") -- with several companies reporting
        in the same window this repeated near-verbatim per company. Must
        collapse into one consolidated line naming all companies instead."""
        def _earnings_item(company: str) -> dict:
            return {
                "title": f"This week: {company} earnings call",
                "disposition": "monitor",
                "extras": {
                    "earnings_type": "pre_earnings_alert",
                    "company": company,
                    "watch_dimensions": "ARR growth, NRR, customer count, technology roadmap, margin",
                },
            }
        sections = {"this_week_priorities": [
            _earnings_item("PAR Technology"), _earnings_item("Olo"), _earnings_item("Toast"),
        ]}
        out = rdb._render_this_week_priorities(sections)
        self.assertEqual(out.count("surface the most candid"), 0)
        self.assertEqual(out.count("Earnings this week:"), 1)
        self.assertIn("PAR Technology, Olo, Toast", out)

    def test_compute_this_week_priorities_no_longer_generates_loop_items(self):
        """Regression guard for the actual defect: this must never again
        independently generate a "this_week" bucket loop item -- that's
        My Priorities' job now (via loops_and_obligations)."""
        import daily_brief as db
        report = {
            "today": "2026-07-27",
            "loops": {"this_week": [
                {"id": "L-2026-07-23-006", "party": "Jeff Caplin", "target": date(2026, 7, 28),
                 "description": "Follow up on Church's."},
            ]},
        }
        items = db._compute_this_week_priorities(report, {})
        titles = " ".join((i.get("title") or "") for i in items)
        self.assertNotIn("L-2026-07-23-006", titles)


class TestThisMonthPriorities(unittest.TestCase):
    def test_empty_section_shows_negative_confirmation_with_header(self):
        out = rdb._render_this_month_priorities({"this_month_priorities": []})
        self.assertIn("## This Month", out)
        self.assertIn("No strategic priorities detected.", out)

    def test_real_item_renders_title_and_detail(self):
        sections = {"this_month_priorities": [{
            "title": "This month: Global Payments earnings call",
            "disposition": "monitor",
            "why_it_matters": "Report date approaching.",
        }]}
        out = rdb._render_this_month_priorities(sections)
        self.assertIn("Global Payments earnings call", out)
        self.assertIn("Report date approaching.", out)

    def test_multiple_earnings_calls_consolidated_by_watch_dimension(self):
        """RB-DEFECT-2026-07-27: same fix as This Week -- PAR Technology,
        Olo, Toast, and Yum Brands all reporting "this month" used to
        render as 4 near-identical boilerplate paragraphs."""
        def _earnings_item(company: str, watch_for: str) -> dict:
            return {
                "title": f"This month: {company} earnings call",
                "disposition": "monitor",
                "extras": {
                    "earnings_type": "pre_earnings_alert",
                    "company": company,
                    "watch_dimensions": watch_for,
                },
            }
        sections = {"this_month_priorities": [
            _earnings_item("PAR Technology", "tech spend and platform direction"),
            _earnings_item("Olo", "tech spend and platform direction"),
            _earnings_item("Toast", "tech spend and platform direction"),
            _earnings_item("Yum Brands", "operator tech investment and traffic trends"),
        ]}
        out = rdb._render_this_month_priorities(sections)
        self.assertEqual(out.count("Earnings this month:"), 2)
        self.assertIn("PAR Technology, Olo, Toast", out)
        self.assertIn("Yum Brands", out)


class TestCapacityPlan(unittest.TestCase):
    def test_empty_list_suppresses_entirely(self):
        """The compute layer returns [] when the calendar's already full
        enough that no plan is needed -- must render nothing, not a header
        with no content."""
        out = rdb._render_capacity_plan({"capacity_plan": []})
        self.assertEqual(out, "")

    def test_missing_section_suppresses_entirely(self):
        out = rdb._render_capacity_plan({})
        self.assertEqual(out, "")

    def test_real_plan_renders_hours_and_blocks(self):
        sections = {"capacity_plan": [{
            "title": "Capacity Available",
            "why_it_matters": "5 hours available today.",
            "extras": {
                "available_hours": 5,
                "allocation_blocks": [
                    {"label": "Relationship execution", "time": "1 hour",
                     "actions": ["Jeff Coffland: follow up", "Channing Smith: reconnect"]},
                ],
            },
        }]}
        out = rdb._render_capacity_plan(sections)
        self.assertIn("## Capacity Plan — 5h unscheduled", out)
        self.assertIn("**Relationship execution** (1 hour)", out)
        self.assertIn("Jeff Coffland: follow up", out)

    def test_no_allocation_blocks_falls_back_to_why_it_matters(self):
        sections = {"capacity_plan": [{
            "title": "Capacity Available",
            "why_it_matters": "Some hours available.",
            "extras": {},
        }]}
        out = rdb._render_capacity_plan(sections)
        self.assertIn("## Capacity Plan", out)
        self.assertIn("Some hours available.", out)


if __name__ == "__main__":
    unittest.main()
