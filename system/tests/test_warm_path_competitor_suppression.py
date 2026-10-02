"""
test_warm_path_competitor_suppression.py

Regression coverage: "Warm path opportunity: Toast" was recommended from a
stale active_threads entry predating the user's Global Payments employment.
Toast is now a direct Genius/Worldpay competitor -- recommending outreach
to a competitor's contacts as a sales opportunity doesn't make sense.
_compute_my_priorities now filters warm_path_candidates_for_threads()
results through rb_core.is_gp_competitor() before taking the top candidate.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402
import rb_core as core  # noqa: E402


class TestIsGpCompetitor(unittest.TestCase):
    def test_toast_is_a_competitor(self):
        self.assertTrue(core.is_gp_competitor("Toast"))

    def test_case_insensitive(self):
        self.assertTrue(core.is_gp_competitor("TOAST"))

    def test_non_competitor_company_not_flagged(self):
        self.assertFalse(core.is_gp_competitor("Perfect Hire"))


_TOAST_WARM_PATH = {
    "thread_id": "t-toast", "thread_title": "Toast enterprise role watch",
    "boost_for_brief": "high", "company": "Toast",
    "candidates": ["Bob Gibson"], "cluster_size": 2,
}
_NON_COMPETITOR_WARM_PATH = {
    "thread_id": "t-ph", "thread_title": "Perfect Hire equity opportunity",
    "boost_for_brief": "medium", "company": "Perfect Hire",
    "candidates": ["Jane Smith"], "cluster_size": 2,
}


class TestWarmPathCompetitorSuppression(unittest.TestCase):
    def test_competitor_warm_path_suppressed_when_alternative_exists(self):
        with patch.object(core, "load_active_threads", return_value=[]), \
             patch.object(core, "load_baseline", return_value=[]), \
             patch.object(core, "warm_path_candidates_for_threads",
                          return_value=[_TOAST_WARM_PATH, _NON_COMPETITOR_WARM_PATH]):
            items = db._compute_my_priorities({}, {})
        warm_path_items = [i for i in items if "Warm path opportunity" in (i.get("title") or "")]
        self.assertEqual(len(warm_path_items), 1)
        self.assertIn("Perfect Hire", warm_path_items[0]["title"])
        self.assertNotIn("Toast", warm_path_items[0]["title"])

    def test_competitor_only_warm_path_yields_no_recommendation(self):
        with patch.object(core, "load_active_threads", return_value=[]), \
             patch.object(core, "load_baseline", return_value=[]), \
             patch.object(core, "warm_path_candidates_for_threads",
                          return_value=[_TOAST_WARM_PATH]):
            items = db._compute_my_priorities({}, {})
        warm_path_items = [i for i in items if "Warm path opportunity" in (i.get("title") or "")]
        self.assertEqual(warm_path_items, [])


if __name__ == "__main__":
    unittest.main()
