"""
test_connect_the_dots_rendering.py

Regression coverage: daily_brief.py computes a rich connect_the_dots
section -- relationship-activation windows, cross-company convergence
validation, decision-relevant new evidence, cross-time entity tracking,
entity co-occurrence, and industry+opportunity/relationship linkage -- but
neither render script ever displayed it. This was very likely the actual
gap behind the user's "GP/Genius Dot Connections is just more articles,
zero synthesis" complaint: genuine dot-connecting synthesis already
existed, it just wasn't rendered anywhere.

_render_connect_the_dots surfaces it in the Daily Brief, ranked by
disposition/confidence/importance, capped at a reasonable count.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_daily_brief as rdb  # noqa: E402

_TEST_DATE = date(2026, 7, 16)


def _dot_item(title: str, disposition: str = "monitor", confidence: str = "medium",
              importance: str | None = None) -> dict:
    return {
        "title": title,
        "why_it_matters": f"Why {title} matters.",
        "recommended_action": f"Act on {title}.",
        "disposition": disposition,
        "confidence": confidence,
        "extras": {"importance": importance} if importance else {},
    }


class TestConnectTheDotsRendering(unittest.TestCase):
    def test_empty_section_renders_nothing(self):
        out = rdb._render_connect_the_dots({"connect_the_dots": []}, _TEST_DATE)
        self.assertEqual(out, "")

    def test_missing_section_renders_nothing(self):
        out = rdb._render_connect_the_dots({}, _TEST_DATE)
        self.assertEqual(out, "")

    def test_items_render_with_why_and_action(self):
        sections = {"connect_the_dots": [
            _dot_item("[CONVERGENCE] Independent validation of X", disposition="act_today", confidence="high"),
        ]}
        out = rdb._render_connect_the_dots(sections, _TEST_DATE)
        self.assertIn("[CONVERGENCE] Independent validation of X", out)
        self.assertIn("Why [CONVERGENCE] Independent validation of X matters.", out)
        self.assertIn("Act on [CONVERGENCE] Independent validation of X.", out)

    def test_act_today_high_confidence_ranked_above_monitor_low_confidence(self):
        sections = {"connect_the_dots": [
            _dot_item("[ENTITY PAIR] Low priority item", disposition="monitor", confidence="low"),
            _dot_item("[DECISION SIGNAL] High priority item", disposition="act_today", confidence="high"),
        ]}
        out = rdb._render_connect_the_dots(sections, _TEST_DATE)
        self.assertLess(
            out.index("High priority item"),
            out.index("Low priority item"),
        )

    def test_caps_at_max_items(self):
        sections = {"connect_the_dots": [_dot_item(f"[ENTITY PAIR] Item {i}") for i in range(20)]}
        out = rdb._render_connect_the_dots(sections, _TEST_DATE)
        rendered_count = sum(1 for i in range(20) if f"Item {i}" in out)
        self.assertLessEqual(rendered_count, rdb._CONNECT_THE_DOTS_MAX)


if __name__ == "__main__":
    unittest.main()
