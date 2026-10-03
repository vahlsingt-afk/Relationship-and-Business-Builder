"""test_hunter_research_priority_queue.py — 2026-10-02.

Covers the one genuinely new piece of logic in hunter_research_priority_
queue.py: merging brand and competitor candidates into a single stack-
ranked order (strategic_value desc, then gap_count desc, then real
top-10-category placement count desc, then name) and capping to --limit.

_rank_brands()/_rank_competitors() themselves compose hunter_gap_
manifest.build_manifest(), hunter_competitor_category_targets.
top_competitors_by_category(), and baseline_research_gate._strategic_
value() -- each already covered by its own test file -- so this file
monkeypatches them directly rather than re-deriving a synthetic
ecosystem graph just to exercise the merge/sort/limit logic in isolation.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import hunter_research_priority_queue as hpq  # noqa: E402


def _brand_row(name, *, strategic_value, gap_count):
    return {
        "target_key": f"company:brand-{name.lower()}", "display_name": name,
        "entity_type": "restaurant_brand", "strategic_value": strategic_value,
        "gap_count": gap_count, "gap_fields": [], "suggested_playbook": "enterprise_account_profile",
        "context": {"unit_count": None},
    }


def _competitor_row(name, *, strategic_value, gap_count, top10_category_count=0, top10_best_rank=None):
    return {
        "target_key": f"competitor:{name.lower()}", "display_name": name,
        "entity_type": "restaurant_technology_company", "strategic_value": strategic_value,
        "gap_count": gap_count, "gap_fields": [], "suggested_playbook": "competitive_positioning",
        "context": {"top10_category_count": top10_category_count, "top10_best_rank": top10_best_rank},
    }


class TestBuildMergeAndSort(unittest.TestCase):
    def test_higher_strategic_value_ranks_first_across_types(self):
        brands = [_brand_row("LowValue", strategic_value=10, gap_count=5)]
        competitors = [_competitor_row("HighValue", strategic_value=90, gap_count=1)]
        with patch.object(hpq, "_rank_brands", return_value=brands), \
             patch.object(hpq, "_rank_competitors", return_value=competitors), \
             patch.object(hpq, "_rank_franchisees", return_value=[]), \
             patch.object(hpq.ei, "_read_graph", return_value={}), \
             patch.object(hpq.ei, "_index_by_id", return_value={}):
            report = hpq.build(limit=100)
        self.assertEqual(report["queue"][0]["display_name"], "HighValue")
        self.assertEqual(report["queue"][0]["rank"], 1)
        self.assertEqual(report["queue"][1]["display_name"], "LowValue")

    def test_gap_count_breaks_ties_within_equal_strategic_value(self):
        brands = [_brand_row("FewGaps", strategic_value=45, gap_count=2)]
        competitors = [_competitor_row("ManyGaps", strategic_value=45, gap_count=14)]
        with patch.object(hpq, "_rank_brands", return_value=brands), \
             patch.object(hpq, "_rank_competitors", return_value=competitors), \
             patch.object(hpq, "_rank_franchisees", return_value=[]), \
             patch.object(hpq.ei, "_read_graph", return_value={}), \
             patch.object(hpq.ei, "_index_by_id", return_value={}):
            report = hpq.build(limit=100)
        self.assertEqual(report["queue"][0]["display_name"], "ManyGaps")

    def test_top10_category_count_is_the_final_tiebreak_not_folded_into_score(self):
        a = _competitor_row("A", strategic_value=45, gap_count=10, top10_category_count=1)
        b = _competitor_row("B", strategic_value=45, gap_count=10, top10_category_count=5)
        with patch.object(hpq, "_rank_brands", return_value=[]), \
             patch.object(hpq, "_rank_competitors", return_value=[a, b]), \
             patch.object(hpq, "_rank_franchisees", return_value=[]), \
             patch.object(hpq.ei, "_read_graph", return_value={}), \
             patch.object(hpq.ei, "_index_by_id", return_value={}):
            report = hpq.build(limit=100)
        self.assertEqual(report["queue"][0]["display_name"], "B")

    def test_limit_caps_queue_but_reports_full_candidate_pool_count(self):
        brands = [_brand_row(f"Brand{i}", strategic_value=50 - i, gap_count=1) for i in range(10)]
        with patch.object(hpq, "_rank_brands", return_value=brands), \
             patch.object(hpq, "_rank_competitors", return_value=[]), \
             patch.object(hpq, "_rank_franchisees", return_value=[]), \
             patch.object(hpq.ei, "_read_graph", return_value={}), \
             patch.object(hpq.ei, "_index_by_id", return_value={}):
            report = hpq.build(limit=3)
        self.assertEqual(len(report["queue"]), 3)
        self.assertEqual(report["candidate_pool_count"], 10)
        self.assertEqual([row["rank"] for row in report["queue"]], [1, 2, 3])


class TestFranchiseeOrgStrategicValue(unittest.TestCase):
    def test_higher_unit_count_scores_higher(self):
        low = hpq._franchisee_org_strategic_value({"total_identified_units": {"value": "15"}})
        high = hpq._franchisee_org_strategic_value({"total_identified_units": {"value": "600"}})
        self.assertLess(low, high)

    def test_multi_brand_group_gets_a_bonus_over_identical_unit_count(self):
        single = hpq._franchisee_org_strategic_value({"total_identified_units": {"value": "50"}, "hierarchy_level": "multi_unit_single_brand"})
        multi = hpq._franchisee_org_strategic_value({"total_identified_units": {"value": "50"}, "hierarchy_level": "multi_brand_franchisee_group"})
        self.assertGreater(multi, single)

    def test_missing_unit_count_does_not_crash(self):
        self.assertEqual(hpq._franchisee_org_strategic_value({}), 0)

    def test_malformed_unit_count_does_not_crash(self):
        self.assertEqual(hpq._franchisee_org_strategic_value({"total_identified_units": {"value": "not a number"}}), 0)


class TestBrandPlaybookSelection(unittest.TestCase):
    def test_pure_technology_stack_gaps_select_tech_stack_reconstruction(self):
        gaps = [{"field": "technology_stack"}]
        self.assertEqual(hpq._brand_playbook(gaps), "technology_stack_reconstruction")

    def test_mixed_gaps_select_enterprise_account_profile(self):
        gaps = [{"field": "technology_stack"}, {"field": "leadership"}]
        self.assertEqual(hpq._brand_playbook(gaps), "enterprise_account_profile")

    def test_no_gaps_defaults_to_enterprise_account_profile(self):
        self.assertEqual(hpq._brand_playbook([]), "enterprise_account_profile")


if __name__ == "__main__":
    unittest.main()
