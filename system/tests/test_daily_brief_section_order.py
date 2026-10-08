"""
test_daily_brief_section_order.py

RB-2026-08-25: Todd's explicit purpose statement for the Daily Brief -- it
is downstream of the Intelligence Brief (which already answers "what
happened and why it matters"). This document's job is (1) prep for the
day, (2) identify risks and opportunities, (3) connect the dots between
multiple intelligence signals across multiple days into insight the reader
wouldn't have seen alone -- "this is the report the CoS should speak
loudest." Section order used to be purely an artifact of upstream
computation order (Technology Radar/My Priorities first, Decision Queue
and GP/Genius Dot Connections buried near the bottom) with no relationship
to reader priority. render()'s section order now leads with Decision Queue
(what needs a decision today) and the two dot-connecting sections (Connect
the Dots, GP/Genius: Dot Connections), ahead of status/risk detail.

No test previously called render() end-to-end at all -- every existing
Daily Brief test exercises one _render_* function in isolation. This adds
minimal but real end-to-end coverage for the section ordering itself,
which unit tests on individual renderers can't verify.

RB-2026-10-08 (Group B restoration): the 08-25 consolidation excluded
Technology Radar and the CoS Bottom Line from the compact brief entirely,
alongside My Priorities/Loops & Obligations -- all four were "repeats and
inaccuracies" noise at the time. Reviewed after the self-audit closure
gate, refreshSources reliability fix, and review-queue backlog visibility
fix all landed (2026-10-08): Technology Radar (material risk/opportunity
signals) and CoS Bottom Line (closing synthesis -- the compact brief had
none at all) are directly decision-relevant and restored. My Priorities
and Loops & Obligations remain excluded -- genuinely routine/cadence
detail, not something the reconciliation work changes the case for.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_daily_brief as rdb  # noqa: E402


def _minimal_sections() -> dict:
    return {
        "decision_queue": [
            {"title": "Prep needed: Test Meeting", "disposition": "ask_todd",
             "recommended_action": "Do the thing.",
             "extras": {"requires_decision": True}},
        ],
        "connect_the_dots": [
            {"title": "Test Dot Connection", "why_it_matters": "Because reasons.",
             "disposition": "monitor", "confidence": "medium"},
        ],
        "watchlist_intelligence": [
            {"title": "Stripe — Escalation", "why_it_matters": "Escalated for real reasons.",
             "extras": {"entity_name": "Stripe", "watchlist_status": "Escalation", "status_changed": True}},
        ],
        "my_priorities": [{"title": "Some Priority Item"}],
        "communication_intelligence": [{"summary": "Loops overdue: 0."}],
    }


class TestDailyBriefSectionOrder(unittest.TestCase):
    def _render(self, sections: dict) -> str:
        tmp = Path(tempfile.mkdtemp())
        cache_path = tmp / "daily_brief.json"
        cache_path.write_text(json.dumps({
            "data": {
                "canonical_brief": {"sections": sections},
                "rendered_at": "2026-08-25T05:00:00",
            }
        }))
        with patch.object(rdb, "DAILY_BRIEF_CACHE", cache_path), \
             patch.object(rdb, "BRIEFS_DIR", tmp / "briefs"), \
             patch.object(rdb, "CONNECT_THE_DOTS_RENDERED_PATH", tmp / "ctd.json"), \
             patch.object(rdb, "GP_DOT_RENDERED_PATH", tmp / "gp.json"), \
             patch.object(rdb, "_load_weekly_plan", return_value=None), \
             patch.object(rdb, "_load_weekly_plan_draft", return_value=None):
            return rdb.render(date(2026, 8, 25), dry_run=True)

    def test_concise_contract_keeps_decisions_connections_and_group_b_sections(self):
        out = self._render(_minimal_sections())
        idx_decision_queue = out.find("## Decision Queue")
        idx_connect_dots = out.find("## Connect the Dots")
        self.assertGreater(idx_decision_queue, -1)
        self.assertGreater(idx_connect_dots, -1)
        self.assertLess(idx_decision_queue, idx_connect_dots,
                         "Decision Queue must lead Connect the Dots")
        # Group B restoration (2026-10-08): Technology Radar is back --
        # the fixture's watchlist_intelligence entry has status_changed,
        # so it has real content to show, not a placeholder.
        self.assertIn("## Technology Radar", out)
        # My Priorities and Loops & Obligations remain deliberately
        # excluded -- routine/cadence detail, not restored.
        self.assertNotIn("## My Priorities", out)
        self.assertNotIn("## Loops & Obligations", out)

    def test_cos_bottom_line_is_restored_as_the_final_section(self):
        out = self._render(_minimal_sections())
        self.assertIn("## CoS Bottom Line", out)
        # "Always last" per _render_cos_bottom_line's own contract.
        self.assertGreater(
            out.rfind("## CoS Bottom Line"),
            max(out.rfind("## Decision Queue"), out.rfind("## Technology Radar"), out.rfind("## Connect the Dots")),
        )


if __name__ == "__main__":
    unittest.main()
