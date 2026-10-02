"""
test_daily_brief_technology_radar.py

Originally: canonical Daily Brief spec (DAILY_BRIEF_CANONICAL.md) required
Section 3 Technology Radar to give full dot-connected treatment (Signal /
Why it matters / Implication) to material watchlist entities, plus a
mandatory-coverage rollup naming every quiet entity in one sentence, with
Escalation getting full treatment, New Activity a condensed one-liner, and
Relevant Activity / No Change folded into a count-only rollup line.

RB-DEFECT-2026-07-27 superseded that: assessed against the Intelligence
Brief (which renders first in the same daily pipeline) for noise/overlap,
and New Activity + the quiet rollup were both found to be exact duplicates
of the Intelligence Brief's own Section F (Watchlist) -- including the "N
entities scanned" line Todd separately asked to remove from Section F.
Technology Radar is now escalations-only: the one tier that genuinely
earns a second, CoS-voiced mention.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_daily_brief as rdb  # noqa: E402


def _wl_item(entity: str, status: str, why: str = "", action: str = "", source_url: str | None = None,
             status_changed: bool = True) -> dict:
    return {
        "title": f"{entity} — {status}",
        "why_it_matters": why,
        "recommended_action": action,
        "extras": {"entity_name": entity, "watchlist_status": status, "source_url": source_url,
                   "status_changed": status_changed},
    }


class TestTechnologyRadar(unittest.TestCase):
    def test_empty_watchlist_returns_empty_string(self):
        self.assertEqual(rdb._render_technology_radar({}, date(2026, 7, 7)), "")

    def test_no_escalation_returns_empty_string(self):
        """Non-escalated activity (new/relevant/no-change) is already fully
        covered by the Intelligence Brief's Section F -- this section must
        stay silent rather than repeat it."""
        sections = {"watchlist_intelligence": [
            _wl_item("Domino's", "New Activity", why="Store expansion news."),
            _wl_item("Toast", "No Change"),
        ]}
        out = rdb._render_technology_radar(sections, date(2026, 7, 7))
        self.assertEqual(out, "")

    def test_escalated_entity_gets_full_treatment(self):
        """RB-DEFECT-2026-08-18 (see the matching comment in
        _render_technology_radar) intentionally stopped showing the bare
        "Follow up immediately." placeholder when real why-text is present
        -- it points at GP/Genius Dot Connections' actual analysis instead.
        This assertion was stale from before that change; updated to match
        the documented, intentional current behavior."""
        sections = {"watchlist_intelligence": [
            _wl_item("McDonald's", "Escalation", why="Overdue loop triggered escalation.",
                     action="Follow up immediately."),
        ]}
        out = rdb._render_technology_radar(sections, date(2026, 7, 7))
        self.assertIn("[ESCALATED] McDonald's", out)
        self.assertIn("Why it matters: Overdue loop triggered escalation.", out)
        self.assertIn("see GP/Genius Dot Connections below", out)

    def test_persisting_escalation_shown_compact_not_full_repeat(self):
        """RB-DEFECT-2026-08-12: PAR/Olo/Fiserv stayed in Escalation for
        multiple days with no mechanism to ever downgrade, and this section
        re-rendered the identical multi-hundred-word press-release
        paragraph every morning -- on top of the same paragraph already
        repeating in the Intelligence Brief's own Section F. A persisting
        (not newly status_changed) escalation must render a short,
        explicitly-labeled note instead of the full text.

        RB-2026-08-25: superseded the per-item "(unchanged since prior
        brief)" label with a single collapsed rollup line for ALL unchanged
        escalations -- confirmed live, a day where every escalation was
        unchanged rendered 7 full blocks restating "still open" seven
        different ways ("every word on the page has to earn its place").
        The persisting-escalation content must still never repeat the full
        why-text, just via a different (tighter) format."""
        long_why = "PAR Technology earnings release: " + ("details " * 30)
        sections = {"watchlist_intelligence": [
            _wl_item("PAR Technology", "Escalation", why=long_why,
                     action="Review earnings.", status_changed=False),
        ]}
        out = rdb._render_technology_radar(sections, date(2026, 7, 7))
        self.assertIn("unchanged since yesterday", out)
        self.assertIn("PAR Technology", out)
        self.assertNotIn(long_why, out)

    def test_fresh_and_stale_escalations_both_shown_at_appropriate_detail(self):
        """RB-2026-08-25: a mix of a freshly status_changed escalation and
        several unchanged ones -- the fresh one gets full block treatment,
        the unchanged ones collapse into one rollup line, not one block
        each."""
        sections = {"watchlist_intelligence": [
            _wl_item("Stripe", "Escalation", why="Stripe tips toward staying private.",
                     action="Follow up immediately.", status_changed=True),
            _wl_item("Toast", "Escalation", why="Overdue loop L-2026-01-01-001.",
                     action="Follow up.", status_changed=False),
            _wl_item("Square", "Escalation", why="Overdue loop L-2026-01-01-002.",
                     action="Follow up.", status_changed=False),
        ]}
        out = rdb._render_technology_radar(sections, date(2026, 7, 7))
        self.assertIn("[ESCALATED] Stripe", out)
        self.assertIn("Why it matters: Stripe tips toward staying private.", out)
        self.assertIn("2 other escalations unchanged since yesterday:", out)
        self.assertIn("Toast", out)
        self.assertIn("Square", out)
        # The unchanged entities must not ALSO get a full standalone block.
        self.assertNotIn("[ESCALATED] Toast", out)
        self.assertNotIn("[ESCALATED] Square", out)

    def test_escalation_shown_without_repeating_other_tiers(self):
        sections = {"watchlist_intelligence": [
            _wl_item("McDonald's", "Escalation", why="Overdue loop.", action="Follow up."),
            _wl_item("Domino's", "New Activity", why="Store expansion news."),
            _wl_item("Toast", "No Change"),
        ]}
        out = rdb._render_technology_radar(sections, date(2026, 7, 7))
        self.assertIn("[ESCALATED] McDonald's", out)
        self.assertNotIn("Domino's", out)
        self.assertNotIn("New activity this cycle:", out)
        self.assertNotIn("entities scanned", out)
        self.assertNotIn("entity scanned", out)


if __name__ == "__main__":
    unittest.main()
