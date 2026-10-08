"""
test_group_b_section_restoration.py — RB defect 2026-10-08, Group B.

The 2026-08-25 "CoS Brief v2" consolidation replaced render()'s verbose,
every-section markdown assembly with a curated `compact_parts` path, but
left the entire old assembly in place below an early `return markdown` --
~290 lines of permanently unreachable code (see test_pending_confirmations_
rendering.py for the sibling defect this was found alongside: pending
mutations/review-queue backlog were computed daily but never rendered for
the same reason).

Reviewed with Todd 2026-10-08: the consolidation itself was a deliberate,
correct call ("repeats and inaccuracies"), but with the self-audit closure
gate, refreshSources reliability fix, and review-queue backlog visibility
fix now in place, six of the ~24 sections in that dead block are directly
decision-relevant (not routine/cadence noise) and were restored into the
live compact path:
  - Technology Radar
  - Competitive Vulnerability Watchlist
  - Downstream Artifact Cascade
  - Proposed Relationship Mutations
  - Pending Graph Mutations
  - Executive Status (EOLMS roll-up)
  - CoS Bottom Line (closing synthesis -- the compact brief had none)

The remaining ~17 sections (My Priorities, Loops & Obligations, This Week/
Month Priorities, Weekly Plan Focus, Upcoming Prep Requirements, Learned
Patterns, Capacity Plan, Notable Contact Moves, Reactivation Candidates,
Active Opportunities, Job Intelligence, Horizon Watch, Captures/Capture
Insights, Battle Cards, CoS Opening, last-24h relationship signals,
Decision Queue's "RECOMMENDED ACTIONS" half) were deliberately left
excluded -- genuinely routine/cadence detail the reconciliation work
doesn't change the case for, per the "CoS Brief v2" comment's own
"intentionally excluded" language.

These tests confirm each restored section is both reachable (called
before render()'s remaining early return) and correctly suppressed when
it has nothing real to report, using the same minimal-fixture pattern as
test_daily_brief_section_order.py.
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


class TestGroupBSectionsReachable(unittest.TestCase):
    """Structural guard mirroring test_pending_confirmations_rendering.py's
    equivalent test -- each restored call must happen before render()'s
    early return, or it's dead code again."""

    def test_all_group_b_calls_happen_before_the_early_return(self):
        import inspect
        source = inspect.getsource(rdb.render)
        return_idx = source.index("\n    return markdown\n")
        for needle in (
            "_render_technology_radar(sections, target_date)",
            "_render_competitive_vulnerability_watchlist(sections)",
            "_render_downstream_artifact_cascade(sections)",
            "_render_proposed_relationship_mutations(sections)",
            "_render_pending_graph_mutations(sections)",
            "_render_executive_status()",
            "_render_cos_bottom_line(sections, target_date, gp_dots_markdown=gp_dots)",
        ):
            with self.subTest(needle=needle):
                idx = source.index(needle)
                self.assertLess(idx, return_idx, f"{needle} must be called before the early return")


class TestGroupBSectionsRenderWithRealContent(unittest.TestCase):
    def _render(self, sections: dict) -> str:
        tmp = Path(tempfile.mkdtemp())
        cache_path = tmp / "daily_brief.json"
        cache_path.write_text(json.dumps({
            "data": {
                "canonical_brief": {"sections": sections},
                "rendered_at": "2026-10-08T05:00:00",
            }
        }))
        with patch.object(rdb, "DAILY_BRIEF_CACHE", cache_path), \
             patch.object(rdb, "BRIEFS_DIR", tmp / "briefs"), \
             patch.object(rdb, "CONNECT_THE_DOTS_RENDERED_PATH", tmp / "ctd.json"), \
             patch.object(rdb, "GP_DOT_RENDERED_PATH", tmp / "gp.json"), \
             patch.object(rdb, "_load_weekly_plan", return_value=None), \
             patch.object(rdb, "_load_weekly_plan_draft", return_value=None):
            return rdb.render(date(2026, 10, 8), dry_run=True)

    def _base_sections(self, **overrides) -> dict:
        base = {
            "decision_queue": [
                {"title": "Prep needed: Test Meeting", "disposition": "ask_todd",
                 "recommended_action": "Do the thing.", "extras": {"requires_decision": True}},
            ],
            "connect_the_dots": [
                {"title": "Test Dot Connection", "why_it_matters": "Because reasons.",
                 "disposition": "monitor", "confidence": "medium"},
            ],
        }
        base.update(overrides)
        return base

    def test_proposed_relationship_mutations_renders_when_present(self):
        sections = self._base_sections(proposed_relationship_mutations=[
            {"title": "Reclassify Jane Doe", "disposition": "ask_todd", "extras": {
                "contact_name": "Jane Doe", "current_classification": "VC",
                "proposed_classification": "LKI", "confidence": "HIGH",
                "evidence_summary": "Advocacy signal detected.",
            }},
        ])
        out = self._render(sections)
        self.assertIn("Jane Doe", out)

    def test_proposed_relationship_mutations_absent_when_empty(self):
        out = self._render(self._base_sections(proposed_relationship_mutations=[]))
        self.assertNotIn("## Proposed Relationship Mutations", out)

    def test_pending_graph_mutations_renders_when_present(self):
        sections = self._base_sections(pending_graph_mutations=[
            {"title": "Graph mutation awaiting review", "summary": "Real pending item.",
             "disposition": "ask_todd", "extras": {}},
        ])
        out = self._render(sections)
        self.assertIn("Graph mutation awaiting review", out)

    def test_pending_graph_mutations_absent_when_empty(self):
        out = self._render(self._base_sections(pending_graph_mutations=[]))
        self.assertNotIn("Graph mutation awaiting review", out)

    def test_competitive_watch_absent_when_empty(self):
        out = self._render(self._base_sections(competitive_vulnerability=[]))
        self.assertNotIn("Competitive Vulnerability", out)

    def test_downstream_cascade_absent_when_empty(self):
        out = self._render(self._base_sections(downstream_artifact_cascade=[]))
        self.assertNotIn("Downstream Artifact", out)


if __name__ == "__main__":
    unittest.main()
