"""
test_executive_status_named.py

Regression coverage: Executive Status rendered bare counts only
("Active Strategic Initiatives: 4") with no way to tell what those 4
things actually are -- user feedback: "this is a meaningless section right
now." The underlying ELoop objects always had real titles; _render_
executive_status() now names them instead of just counting.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_daily_brief as rdb  # noqa: E402
import rb_core as core  # noqa: E402


def _loop(id_, title, category, status, **kw) -> core.ELoop:
    return core.ELoop(id=id_, title=title, category=category, status=status, **kw)


class TestExecutiveStatusNamed(unittest.TestCase):
    def test_active_strategic_initiatives_named_not_just_counted(self):
        loops = [
            _loop("e1", "RB — Intelligence Platform", "strategic_initiative", "active"),
            _loop("e2", "RB — Capture Platform", "strategic_initiative", "active"),
        ]
        with patch.object(core, "load_eloops", return_value=loops):
            out = rdb._render_executive_status()
        self.assertIn("Active Strategic Initiatives: 2", out)
        self.assertIn("RB — Intelligence Platform", out)
        self.assertIn("RB — Capture Platform", out)

    def test_blocked_items_named(self):
        loops = [
            _loop("b1", "Global Payments — Enterprise Capture Strategy", "project", "blocked"),
        ]
        with patch.object(core, "load_eloops", return_value=loops):
            out = rdb._render_executive_status()
        self.assertIn("Blocked: 1", out)
        self.assertIn("Global Payments — Enterprise Capture Strategy", out)

    def test_zero_count_category_has_no_name_list(self):
        loops = [
            _loop("p1", "Some Project", "project", "active"),
        ]
        with patch.object(core, "load_eloops", return_value=loops):
            out = rdb._render_executive_status()
        self.assertIn("Active Strategic Initiatives: 0", out)
        # No dash-separated name list should follow a zero count.
        self.assertNotIn("Active Strategic Initiatives: 0 —", out)

    def test_zero_dormant_line_suppressed_entirely(self):
        """RB-2026-08-25: "Dormant: 0" is a guaranteed-empty line -- it
        names nothing and implies no action, unlike every other line here.
        Every other zero-value counter in this section is already
        conditionally shown; this one now is too."""
        loops = [
            _loop("p1", "Some Project", "project", "active"),
        ]
        with patch.object(core, "load_eloops", return_value=loops):
            out = rdb._render_executive_status()
        self.assertNotIn("Dormant", out)

    def test_nonzero_dormant_line_still_shown(self):
        loops = [
            _loop("d1", "Some Dormant Thing", "project", "dormant"),
        ]
        with patch.object(core, "load_eloops", return_value=loops):
            out = rdb._render_executive_status()
        self.assertIn("Dormant: 1", out)


if __name__ == "__main__":
    unittest.main()
