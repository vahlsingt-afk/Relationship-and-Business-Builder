"""
test_daily_brief_loop_cross_section_dedup.py

RB-QUALITY-2026-09-04b: Todd's own read on the Daily Brief "getting longer,
not more useful" -- confirmed live in the real 2026-09-04 brief, the same 2
overdue loops (L-2026-08-28-002 Ryan Hildebrand, L-2026-08-28-003 RB
self-audit) were fully restated across 8 sections, 13 mentions total.
Asked Todd which consolidation shape he wanted; he chose "one canonical
section (Loops & Obligations), rest become pointers" and separately flagged
Top Decisions Today specifically as a section he skims for exactly this
reason.

Covers all 5 sections touched across both passes: Decision Queue (both the
DECISIONS REQUIRED and RECOMMENDED ACTIONS branches), Top Decisions Today,
My Priorities, Connect the Dots, and the Loops & Obligations rendering fix
that makes the whole design honest (see TestLoopsAndObligationsIsActually
Canonical below). A loop-linked item (title carries an L-YYYY-MM-DD-NNN id)
gets a short pointer to Loops & Obligations instead of its full why/
recommended-action text; a non-loop item is completely untouched -- these
sections carry genuinely distinct content (waiting-on-others items,
non-loop decisions, Connect the Dots' "new evidence" payload) that must
not be swept up by the same trim.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_daily_brief as rdb  # noqa: E402


class TestLoopIdHelpers(unittest.TestCase):
    def test_loop_ids_in_finds_a_real_id(self):
        self.assertEqual(rdb._loop_ids_in("Overdue loop L-2026-08-28-002 — Ryan Hildebrand"),
                          ["L-2026-08-28-002"])

    def test_loop_ids_in_returns_empty_for_no_match(self):
        self.assertEqual(rdb._loop_ids_in("Decision #1: Noelle Labrie"), [])

    def test_loop_pointer_note_names_loops_and_points_at_canonical_section(self):
        note = rdb._loop_pointer_note(["L-2026-08-28-002"])
        self.assertIn("L-2026-08-28-002", note)
        self.assertIn("Loops & Obligations", note)


class TestTopDecisionsTodayLoopPointer(unittest.TestCase):
    def test_loop_linked_decision_is_pointed_not_restated(self):
        sections = {"decision_layer": [{
            "title": "Decision #2: Overdue loop L-2026-08-28-002 — Ryan Hildebrand",
            "why_it_matters": "Past target obligations are action debt and should outrank ambient context.",
            "recommended_action": "Close, update, or explicitly defer loop L-2026-08-28-002 (Ryan Hildebrand) today.",
        }]}
        out = rdb._render_decision_layer(sections)
        self.assertIn("Loops & Obligations", out)
        self.assertIn("L-2026-08-28-002", out)
        self.assertNotIn("action debt", out)
        self.assertNotIn("Close, update, or explicitly defer", out)

    def test_non_loop_decision_is_untouched(self):
        sections = {"decision_layer": [{
            "title": "Decision #1: Noelle Labrie",
            "why_it_matters": "Signal class: RC; match quality high. Source: linkedin_messaging.",
            "recommended_action": "Reply via LinkedIn or pull the thread into email.",
        }]}
        out = rdb._render_decision_layer(sections)
        self.assertIn("Signal class: RC", out)
        self.assertIn("Reply via LinkedIn", out)
        self.assertNotIn("Loops & Obligations", out)

    def test_two_loop_decisions_both_pointed_no_duplicate_boilerplate(self):
        """Regression guard: the pre-existing same-day fix already dedupes
        an identical why-sentence repeated verbatim across items in this
        section. This fix goes further for loop items specifically -- both
        get pointed, neither shows the boilerplate sentence at all."""
        sections = {"decision_layer": [
            {"title": "Decision #2: Overdue loop L-2026-08-28-002 — Ryan Hildebrand",
             "why_it_matters": "Past target obligations are action debt and should outrank ambient context.",
             "recommended_action": "Close, update, or explicitly defer loop L-2026-08-28-002 (Ryan Hildebrand) today."},
            {"title": "Decision #3: Overdue loop L-2026-08-28-003 — RB self-audit",
             "why_it_matters": "Past target obligations are action debt and should outrank ambient context.",
             "recommended_action": "Close, update, or explicitly defer loop L-2026-08-28-003 (RB self-audit) today."},
        ]}
        out = rdb._render_decision_layer(sections)
        self.assertNotIn("action debt", out)
        self.assertIn("L-2026-08-28-002", out)
        self.assertIn("L-2026-08-28-003", out)


class TestLoopsAndObligationsIsActuallyCanonical(unittest.TestCase):
    """Foundational: every other fix in this file points at Loops &
    Obligations for "full detail." Confirmed live that promise was FALSE
    before this -- the section only ever rendered a generic templated
    recommended_action ("Close or re-date L-...", "Prepare next step for
    L-..."), never the loop's own real description (summary). Pointing
    other sections here while this rendered nothing richer would have been
    a real information loss, not a consolidation."""

    def test_renders_the_loops_real_description_not_the_generic_action(self):
        items = [{
            "title": "Overdue: L-2026-08-28-002 — Ryan Hildebrand",
            "summary": "Ryan owes the standard site-survey pricing figure "
                       "(POS/DMB/drive-thru) from the price sheet.",
            "recommended_action": "Close or re-date L-2026-08-28-002.",
            "extras": {"loop_bucket": "overdue"},
        }]
        out = rdb._render_loops_and_obligations({"loops_and_obligations": items})
        self.assertIn("Ryan owes the standard site-survey pricing figure", out)

    def test_falls_back_to_recommended_action_when_no_summary(self):
        items = [{
            "title": "Overdue: L-2026-09-01-001 — No description",
            "summary": "",
            "recommended_action": "Close or re-date L-2026-09-01-001.",
            "extras": {"loop_bucket": "overdue"},
        }]
        out = rdb._render_loops_and_obligations({"loops_and_obligations": items})
        self.assertIn("Close or re-date L-2026-09-01-001.", out)


class TestDecisionQueueLoopPointer(unittest.TestCase):
    def test_recommended_actions_loop_item_is_pointed(self):
        sections = {"decision_queue": [{
            "title": "Possible closure: L-2026-08-28-002 — Ryan Hildebrand",
            "extras": {"time_horizon": "today"},
            "recommended_action": "Review evidence for loop L-2026-08-28-002 and decide whether to close, re-date, or keep open.",
        }]}
        out = rdb._render_decision_queue(sections)
        self.assertIn("Loops & Obligations", out)
        self.assertNotIn("Review evidence for loop", out)

    def test_recommended_actions_non_loop_item_is_untouched(self):
        sections = {"decision_queue": [{
            "title": "Open waiting loop — todd.vahlsing@globalpayments.com",
            "extras": {"time_horizon": "today"},
            "recommended_action": "Open loop for todd.vahlsing@globalpayments.com (waiting) due 2026-09-14.",
        }]}
        out = rdb._render_decision_queue(sections)
        self.assertIn("Open loop for todd.vahlsing@globalpayments.com", out)
        self.assertNotIn("Loops & Obligations", out)

    def test_decisions_required_loop_item_is_pointed(self):
        sections = {"decision_queue": [{
            "title": "Overdue loop L-2026-08-28-002 — Ryan Hildebrand",
            "why_it_matters": "Past target obligations are action debt and should outrank ambient context.",
            "extras": {"requires_decision": True},
        }]}
        out = rdb._render_decision_queue(sections)
        self.assertIn("DECISIONS REQUIRED", out)
        self.assertIn("Loops & Obligations", out)
        self.assertNotIn("action debt", out)

    def test_decisions_required_non_loop_item_keeps_its_why(self):
        sections = {"decision_queue": [{
            "title": "Confirm Q3 renewal terms with legal",
            "why_it_matters": "Renewal window closes in 5 days.",
            "extras": {"requires_decision": True},
        }]}
        out = rdb._render_decision_queue(sections)
        self.assertIn("Renewal window closes in 5 days.", out)
        self.assertNotIn("Loops & Obligations", out)

    def test_ask_rb_hint_still_present_alongside_pointer(self):
        """The 'ask RB \"what's the latest on loop...\"' hint is genuinely
        distinct utility (an exact chat command), not redundant with Loops &
        Obligations -- it must survive the trim even though the detail text
        below it doesn't."""
        sections = {"decision_queue": [{
            "title": "Possible closure: L-2026-08-28-002 — Ryan Hildebrand",
            "extras": {"time_horizon": "today"},
            "recommended_action": "Review evidence for loop L-2026-08-28-002 and decide whether to close, re-date, or keep open.",
        }]}
        out = rdb._render_decision_queue(sections)
        self.assertIn('ask RB "what\'s the latest on loop L-2026-08-28-002?"', out)


class TestMyPrioritiesLoopPointer(unittest.TestCase):
    def test_loop_item_is_pointed_not_restated(self):
        """_compute_my_priorities() pulls loop items via dict(li) -- a
        literal copy of the loops_and_obligations item, same full summary
        text. extras.priority_category=='loop' is set explicitly at
        assembly time for exactly these items."""
        items = [{
            "title": "Due today: L-2026-08-10-003 — Little Caesars / Avery Churchwell",
            "summary": "**Follow up with Avery Friday, 2026-08-14.** Confirm whether Avery delivered the material.",
            "disposition": "act_today",
            "extras": {"priority_category": "loop", "urgency": "today"},
        }]
        out = rdb._render_my_priorities({"my_priorities": items})
        self.assertIn("Loops & Obligations", out)
        self.assertIn("L-2026-08-10-003", out)
        self.assertNotIn("Follow up with Avery", out)

    def test_non_loop_item_is_untouched(self):
        items = [{
            "title": "This week's outcome: Get Pollo Campero RFP response/pricing to a send-ready state",
            "summary": "",
            "disposition": "monitor",
            "extras": {"priority_category": "goal"},
        }]
        out = rdb._render_my_priorities({"my_priorities": items})
        self.assertNotIn("Loops & Obligations", out)
        self.assertIn("This week's outcome", out)

    def test_email_priority_item_with_loop_shaped_title_but_no_loop_category_is_untouched(self):
        """priority_category, not a title-text scan, decides this -- an
        item that happens to mention something loop-shaped in its title but
        isn't actually sourced from loops_and_obligations must not be
        trimmed."""
        items = [{
            "title": "[EMAIL] Awaiting response: Some subject",
            "summary": "Sent 3d ago. Category: direct followup sent. Last activity: 2026-09-01.",
            "disposition": "act_today",
            "extras": {"priority_category": "email", "urgency": "today"},
        }]
        out = rdb._render_my_priorities({"my_priorities": items})
        self.assertIn("Sent 3d ago", out)
        self.assertNotIn("Loops & Obligations", out)


class TestConnectTheDotsLoopTailTrim(unittest.TestCase):
    def test_decision_momentum_item_keeps_new_evidence_drops_restated_loop(self):
        """The recommended_action's LEAD (which new headlines triggered
        this) is genuinely new-today content Connect the Dots exists to
        surface -- must survive. The TAIL restating the decision's own
        title a second time is what gets trimmed."""
        from datetime import date
        items = [{
            "title": "[DECISION SIGNAL] New evidence for pending decision: Possible closure: L-2026-08-28-002 — Ryan Hildebrand",
            "why_it_matters": "Pending decisions become more expensive the longer they sit.",
            "recommended_action": (
                "Review the new evidence ([Payments Dive] Stripe taps new stablecoin executive), "
                "then make the call on: Possible closure: L-2026-08-28-002 — Ryan Hildebrand. "
                "Do not defer again without a specific new reason."
            ),
            "disposition": "act_today", "confidence": "medium",
            "extras": {"convergence_type": "decision_momentum"},
        }]
        out = rdb._render_connect_the_dots({"connect_the_dots": items}, date(2026, 9, 4))
        self.assertIn("Stripe taps new stablecoin executive", out)
        self.assertIn("Loops & Obligations", out)
        self.assertNotIn("then make the call on", out)
        self.assertNotIn("Do not defer again", out)

    def test_non_decision_momentum_item_untouched(self):
        from datetime import date
        items = [{
            "title": "[RELATIONSHIP ACTIVATION] Noelle Labrie",
            "why_it_matters": "High match quality signal.",
            "recommended_action": "Reply via LinkedIn.",
            "disposition": "act_today", "confidence": "high",
            "extras": {"convergence_type": "relationship_activation", "contact": "Noelle Labrie"},
        }]
        out = rdb._render_connect_the_dots({"connect_the_dots": items}, date(2026, 9, 4))
        self.assertIn("Reply via LinkedIn", out)
        self.assertNotIn("Loops & Obligations", out)

    def test_decision_momentum_item_with_no_loop_id_untouched(self):
        """A decision_momentum item whose underlying decision isn't
        loop-linked at all (e.g. a named-contact decision) must not have
        its action text altered -- there's no loop for the pointer to name."""
        from datetime import date
        items = [{
            "title": "[DECISION SIGNAL] New evidence for pending decision: Confirm Q3 renewal terms",
            "why_it_matters": "New evidence changes the calculus.",
            "recommended_action": (
                "Review the new evidence (Vendor pricing update), then make the call on: "
                "Confirm Q3 renewal terms. Do not defer again without a specific new reason."
            ),
            "disposition": "act_today", "confidence": "medium",
            "extras": {"convergence_type": "decision_momentum"},
        }]
        out = rdb._render_connect_the_dots({"connect_the_dots": items}, date(2026, 9, 4))
        self.assertIn("then make the call on: Confirm Q3 renewal terms", out)
        self.assertNotIn("Loops & Obligations", out)


if __name__ == "__main__":
    unittest.main()
