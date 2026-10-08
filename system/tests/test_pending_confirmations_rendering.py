"""
test_pending_confirmations_rendering.py — RB defect 2026-10-08.

_render_pending_confirmations() in render_daily_brief.py used to read only
sections["pending_mutations"][0] -- but three separate daily_brief.py
functions append to that SAME list (_compute_pending_mutations,
_compute_leadership_ownership_same_day, _compute_review_queue_backlog), and
_compute_pending_mutations always appends exactly one item first (a real
finding or its own "none overdue" placeholder). So items[1:] -- same-day
executive-move/ownership candidates, and the strategic-assessment Finding 2
review-queue backlog across all 6 promotion-scanner queues -- were computed
correctly every day but could never render, no matter how large or stale.
Confirmed live: a real 334-item backlog, oldest 33 days, silently never
reached the actual brief Todd reads.

Separately, the function was never even CALLED from the live "compact"
render path render()'s markdown is actually built from (compact_parts) --
only from ~300 lines of dead code after an early `return markdown` left
over from the pre-"CoS Brief v2" format. Both are fixed: the function now
renders every non-"ignore"-disposition item, and render()'s compact path
now calls it.

These tests exercise _render_pending_confirmations() directly (pure
function of `sections`, no file I/O) -- the dead-code/wiring half is a
one-line addition in render(), not independently testable without the
heavy render() fixture machinery other tests in this suite already use.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_daily_brief as rdb  # noqa: E402


def _item(**overrides) -> dict:
    base = {
        "title": "Pending mutations — none overdue",
        "summary": "",
        "recommended_action": "",
        "disposition": "ignore",
        "source_refs": ["interaction_ledger.json"],
        "extras": {},
    }
    base.update(overrides)
    return base


class TestRenderPendingConfirmations(unittest.TestCase):
    def test_empty_list_renders_nothing(self):
        self.assertEqual(rdb._render_pending_confirmations({"pending_mutations": []}), "")

    def test_missing_key_renders_nothing(self):
        self.assertEqual(rdb._render_pending_confirmations({}), "")

    def test_single_clean_placeholder_renders_the_all_clear_message(self):
        sections = {"pending_mutations": [_item()]}
        result = rdb._render_pending_confirmations(sections)
        self.assertIn("## Pending Confirmations", result)
        self.assertIn("No relationship-intelligence proposals or review-queue items pending", result)

    def test_real_interaction_ledger_item_renders_contacts(self):
        sections = {"pending_mutations": [_item(
            title="Pending mutations — 1 unconfirmed >12h",
            disposition="act_today",
            source_refs=["interaction_ledger.json"],
            extras={"pending_count": 1, "pending_contacts": ["Morgan"], "oldest_created_at": "2026-10-07T00:00:00+00:00"},
        )]}
        result = rdb._render_pending_confirmations(sections)
        self.assertIn("Morgan", result)
        self.assertIn('ask RB "confirm my interaction with Morgan"', result)

    def test_review_queue_backlog_item_now_renders_past_index_zero(self):
        """The core regression: a real, non-ignore item in position 1+ of
        the list must render, not just whatever is at position 0."""
        sections = {"pending_mutations": [
            _item(),  # clean placeholder at index 0, as _compute_pending_mutations always produces
            _item(
                title="Review-queue backlog — 334 item(s) across 3 queue(s)",
                summary="334 candidate(s) awaiting confirm/reject: 156 tech-stack relationship, "
                        "146 ownership change, 32 executive move. Oldest is a tech-stack relationship "
                        "candidate, detected 33 day(s) ago.",
                recommended_action="Review and confirm/reject via each script's own `... pending`/`confirm`/`reject` CLI.",
                disposition="act_today",
                source_refs=["review_queue_backlog"],
            ),
        ]}
        result = rdb._render_pending_confirmations(sections)
        self.assertIn("Review-queue backlog — 334 item(s)", result)
        self.assertIn("156 tech-stack relationship", result)
        # The clean placeholder at index 0 must NOT also print its own
        # "nothing pending" text alongside a real finding.
        self.assertNotIn("No relationship-intelligence proposals or review-queue items pending", result)

    def test_leadership_ownership_same_day_item_renders(self):
        sections = {"pending_mutations": [
            _item(),
            _item(
                title="Leadership change candidates detected today — 2 unconfirmed",
                summary="2 possible executive-move signal(s) detected today, pending review: Acme Corp: Jane Doe (CEO).",
                recommended_action="Review via `python3 executive_move_promotion.py pending`, then confirm or reject each.",
                disposition="act_today",
                source_refs=["executive_move_promotion"],
            ),
        ]}
        result = rdb._render_pending_confirmations(sections)
        self.assertIn("Leadership change candidates detected today", result)
        self.assertIn("Jane Doe", result)

    def test_multiple_real_items_all_render_together(self):
        sections = {"pending_mutations": [
            _item(
                title="Pending mutations — 1 unconfirmed >12h",
                disposition="act_today",
                source_refs=["interaction_ledger.json"],
                extras={"pending_count": 1, "pending_contacts": ["Morgan"], "oldest_created_at": "2026-10-07"},
            ),
            _item(
                title="Leadership change candidates detected today — 1 unconfirmed",
                summary="1 possible executive-move signal(s) detected today.",
                disposition="act_today",
                source_refs=["executive_move_promotion"],
            ),
            _item(
                title="Review-queue backlog — 334 item(s) across 3 queue(s)",
                summary="334 candidate(s) awaiting confirm/reject.",
                disposition="act_today",
                source_refs=["review_queue_backlog"],
            ),
        ]}
        result = rdb._render_pending_confirmations(sections)
        self.assertIn("Morgan", result)
        self.assertIn("Leadership change candidates detected today", result)
        self.assertIn("Review-queue backlog — 334 item(s)", result)

    def test_all_clean_items_render_only_one_all_clear_message(self):
        sections = {"pending_mutations": [
            _item(title="Pending mutations — none overdue", source_refs=["interaction_ledger.json"]),
            _item(title="Review-queue backlog — nothing pending", source_refs=["review_queue_backlog"]),
        ]}
        result = rdb._render_pending_confirmations(sections)
        self.assertEqual(result.count("No relationship-intelligence proposals or review-queue items pending"), 1)


class TestRenderCompactPathCallsPendingConfirmations(unittest.TestCase):
    """Confirms _render_pending_confirmations is actually wired into the
    live compact-brief path, not left in the dead code tail after render()'s
    early `return markdown` -- the second half of this same defect."""

    def test_render_function_source_calls_it_before_the_early_return(self):
        # Searches for the real statement line, not any substring match --
        # a prose comment mentioning "return markdown" (as this very fix's
        # own commit does, to explain why the line matters) would otherwise
        # produce a false-positive match earlier in the source than the
        # actual statement.
        import inspect
        source = inspect.getsource(rdb.render)
        call_idx = source.index("_render_pending_confirmations(sections)")
        return_idx = source.index("\n    return markdown\n")
        self.assertLess(
            call_idx, return_idx,
            "_render_pending_confirmations must be called before render()'s "
            "early return, or it's dead code again.",
        )


if __name__ == "__main__":
    unittest.main()
