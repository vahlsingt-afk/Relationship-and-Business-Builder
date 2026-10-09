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


def _franchisee_row(name, *, strategic_value, gap_count):
    return {
        "target_key": f"franchisee:{name.lower()}", "display_name": name,
        "entity_type": "franchisee_organization", "strategic_value": strategic_value,
        "gap_count": gap_count, "gap_fields": [], "suggested_playbook": "franchisee_organization_profile",
        "context": {"confidence_tier": None, "total_identified_units": None},
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


class TestBoundedCoverageAllocation(unittest.TestCase):
    """2026-10-03 (CLAUDE_HANDOFF_RB_HUNTER_GATHERER_END_TO_END_DEFECTS,
    Defect 4): confirmed live -- one global sort put the first competitor
    at rank 16, behind every brand and franchisee. These guarantee a
    competitor/franchisee lands inside the reserved window even when many
    brands outrank every one of them, while leaving a small/balanced pool
    (see TestBuildMergeAndSort above, still green) sorted purely by
    strategic_value with nothing artificially reordered."""

    def _build(self, brands, competitors, franchisees, limit=100):
        with patch.object(hpq, "_rank_brands", return_value=brands), \
             patch.object(hpq, "_rank_competitors", return_value=competitors), \
             patch.object(hpq, "_rank_franchisees", return_value=franchisees), \
             patch.object(hpq.ei, "_read_graph", return_value={}), \
             patch.object(hpq.ei, "_index_by_id", return_value={}):
            return hpq.build(limit=limit)

    def test_a_competitor_lands_within_the_reserved_window_despite_many_higher_value_brands(self):
        brands = [_brand_row(f"Brand{i}", strategic_value=100 - i, gap_count=1) for i in range(15)]
        franchisees = [_franchisee_row(f"Org{i}", strategic_value=90 - i, gap_count=1) for i in range(10)]
        competitors = [_competitor_row("WeakCompetitor", strategic_value=5, gap_count=1)]
        report = self._build(brands, competitors, franchisees)
        ranks = {row["display_name"]: row["rank"] for row in report["queue"]}
        self.assertLessEqual(ranks["WeakCompetitor"], 9)  # was rank 26 (15+10+1) under pure global sort

    def test_small_balanced_pool_is_unaffected_sorts_purely_by_strategic_value(self):
        # Exactly the TestBuildMergeAndSort fixture shape -- confirms the
        # allocation layer is a no-op when there's nothing to guarantee.
        brands = [_brand_row("LowValue", strategic_value=10, gap_count=5)]
        competitors = [_competitor_row("HighValue", strategic_value=90, gap_count=1)]
        report = self._build(brands, competitors, [])
        self.assertEqual(report["queue"][0]["display_name"], "HighValue")

    def test_allocation_policy_block_is_present_and_explicit(self):
        report = self._build([_brand_row("A", strategic_value=1, gap_count=1)], [], [])
        policy = report["allocation_policy"]
        self.assertEqual(policy["reserved_window_per_bucket"], 3)
        self.assertEqual(set(policy["buckets"]), {"restaurant_brand", "restaurant_technology_company", "franchisee"})

    def test_every_row_is_tagged_with_its_coverage_bucket_and_allocation(self):
        report = self._build(
            [_brand_row("A", strategic_value=50, gap_count=1)],
            [_competitor_row("B", strategic_value=50, gap_count=1)],
            [_franchisee_row("C", strategic_value=50, gap_count=1)],
        )
        by_name = {row["display_name"]: row for row in report["queue"]}
        self.assertEqual(by_name["A"]["coverage_bucket"], "restaurant_brand")
        self.assertEqual(by_name["B"]["coverage_bucket"], "restaurant_technology_company")
        self.assertEqual(by_name["C"]["coverage_bucket"], "franchisee")
        self.assertIn(by_name["A"]["coverage_allocation"], ("reserved", "ranked"))

    def test_franchisee_discovery_playbook_groups_with_franchisee_bucket_not_brand(self):
        """A franchisee-discovery row's own entity_type is restaurant_brand
        (the target IS the brand) but the handoff doc groups it with
        franchisee research, not brand research -- bucketing must key off
        suggested_playbook, not raw entity_type."""
        discovery_row = _brand_row("DiscoveryTarget", strategic_value=50, gap_count=1)
        discovery_row["suggested_playbook"] = "franchisee_discovery"
        report = self._build([], [], [discovery_row])
        row = report["queue"][0]
        self.assertEqual(row["coverage_bucket"], "franchisee")


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

    def test_plain_int_unit_count_is_the_real_current_schema_and_does_not_crash(self):
        # franchisee_finder_common.py's own schema default is a plain int (not
        # {"value": ...}), and every real organization.json on disk uses this
        # shape -- this must score exactly like the equivalent dict form.
        low = hpq._franchisee_org_strategic_value({"total_identified_units": 15})
        high = hpq._franchisee_org_strategic_value({"total_identified_units": 600})
        self.assertLess(low, high)
        self.assertEqual(
            hpq._franchisee_org_strategic_value({"total_identified_units": 50}),
            hpq._franchisee_org_strategic_value({"total_identified_units": {"value": "50"}}),
        )

    def test_zero_plain_int_unit_count_does_not_crash(self):
        self.assertEqual(hpq._franchisee_org_strategic_value({"total_identified_units": 0}), 0)


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
