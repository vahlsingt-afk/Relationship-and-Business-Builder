"""
test_strategic_narratives.py — RB-DEFECT-046 Slice 2 (RB 9.89): persisted
Strategic Narrative schema.

Test groups:
  SN1: _extract_category_lifecycle_signals() unit tests
  SN2: update_strategic_narratives() confidence escalation + next_expected_signals
  SN3: End-to-end Hungry Howie's tech_stack_modernization scenario via run()
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import intelligence_mutation_engine as engine


def _empty_ecosystem() -> dict:
    return {"entities": [], "relationships": []}


class SN1ExtractCategoryLifecycleSignals(unittest.TestCase):
    def test_loyalty_sunset_detected(self):
        text = "Hungry Howie's confirmed that the Howie Rewards loyalty program will be sunset next quarter."
        signals = engine._extract_category_lifecycle_signals(text)
        self.assertEqual(len(signals), 1)
        sig = signals[0]
        self.assertEqual(sig["category"], "loyalty")
        self.assertEqual(sig["signal_type"], "sunset")
        self.assertEqual(sig["confidence"], 0.80)
        self.assertIn("Howie Rewards", sig["sentence_evidence"])

    def test_no_lifecycle_verb_no_signal(self):
        text = "Hungry Howie's loyalty program continues to grow in popularity."
        self.assertEqual(engine._extract_category_lifecycle_signals(text), [])

    def test_lifecycle_verb_without_known_category_no_signal(self):
        text = "The company will discontinue its legacy email newsletter."
        self.assertEqual(engine._extract_category_lifecycle_signals(text), [])

    def test_pos_replacement_detected(self):
        text = "The chain is replacing its aging point of sale system this year."
        signals = engine._extract_category_lifecycle_signals(text)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["category"], "pos")


class SN2UpdateStrategicNarratives(unittest.TestCase):
    def _brand(self) -> dict:
        return {"id": "brand-test", "name": "Test Brand"}

    def test_returns_none_for_untracked_category(self):
        brand = self._brand()
        result = engine.update_strategic_narratives(
            brand, category="marketing", signal_type="vendor_selected",
            description="x", source={"title": "t"}, now="2026-06-15T00:00:00+00:00",
        )
        self.assertIsNone(result)
        self.assertNotIn("strategic_narratives", brand)

    def test_first_signal_creates_low_confidence_narrative(self):
        brand = self._brand()
        narrative = engine.update_strategic_narratives(
            brand, category="pos", signal_type="vendor_selected",
            description="Test Brand confirmed as Toast customer (pos)",
            source={"title": "t"}, now="2026-06-15T00:00:00+00:00",
        )
        self.assertIsNotNone(narrative)
        self.assertEqual(narrative["narrative_type"], "tech_stack_modernization")
        self.assertEqual(narrative["confidence"], "low")
        self.assertEqual(len(narrative["supporting_signals"]), 1)
        # Only one category seen — no broader "what else changes" projection yet.
        self.assertEqual(narrative["next_expected_signals"], [])

    def test_second_category_escalates_to_medium_and_projects(self):
        brand = self._brand()
        engine.update_strategic_narratives(
            brand, category="pos", signal_type="vendor_selected",
            description="Test Brand confirmed as Toast customer (pos)",
            source={"title": "t1"}, now="2026-06-15T00:00:00+00:00",
        )
        narrative = engine.update_strategic_narratives(
            brand, category="back_office", signal_type="vendor_selected",
            description="Test Brand confirmed as Restaurant365 customer (back_office)",
            source={"title": "t2"}, now="2026-06-15T00:00:00+00:00",
        )
        self.assertEqual(narrative["confidence"], "medium")
        self.assertEqual(len(narrative["supporting_signals"]), 2)
        self.assertTrue(
            any("selection or change" in s for s in narrative["next_expected_signals"])
        )

    def test_third_category_escalates_to_high(self):
        brand = self._brand()
        engine.update_strategic_narratives(
            brand, category="pos", signal_type="vendor_selected",
            description="d1", source={"title": "t1"}, now="2026-06-15T00:00:00+00:00",
        )
        engine.update_strategic_narratives(
            brand, category="back_office", signal_type="vendor_selected",
            description="d2", source={"title": "t2"}, now="2026-06-15T00:00:00+00:00",
        )
        narrative = engine.update_strategic_narratives(
            brand, category="loyalty", signal_type="sunset",
            description="d3", source={"title": "t3"}, now="2026-06-15T00:00:00+00:00",
        )
        self.assertEqual(narrative["confidence"], "high")
        self.assertEqual(len(narrative["supporting_signals"]), 3)
        # Unresolved sunset for loyalty -> replacement-announcement expectation.
        self.assertTrue(
            any("loyalty platform replacement announcement" == s for s in narrative["next_expected_signals"])
        )


class SN3EndToEndHungryHowies(unittest.TestCase):
    def setUp(self):
        self.ecosystem = _empty_ecosystem()

    def test_two_step_narrative_accumulation(self):
        # Step 1: Toast POS selection.
        text1 = (
            "Hungry Howie's technology roadmap took a step forward this week. "
            "The chain has selected Toast as its new point-of-sale platform "
            "across all franchise locations."
        )
        result1 = engine.generate_mutations(
            text1, source_title="Article 1", source_url="https://example.com/1",
            ecosystem=self.ecosystem, baseline=[], strategic_memory={"signals": []},
        )
        # Note: apply_mutations() loads/writes real disk stores via core paths,
        # so for unit-level verification of narrative accumulation we instead
        # drive update_strategic_narratives() directly against the in-memory
        # ecosystem using the mutations generate_mutations() produced.
        self.assertTrue(
            any(m["type"] == "vendor_customer_relationship" for m in result1["mutations"])
        )
        rel_mut = next(
            m for m in result1["mutations"]
            if m["type"] == "vendor_customer_relationship" and m["category"] == "pos"
        )
        self.assertEqual(rel_mut["category"], "pos")
        self.assertIn("brand_name", rel_mut)

        brand_entity = engine._get_or_create_brand_entity(
            self.ecosystem, rel_mut["from_entity_id"], rel_mut["brand_name"], "2026-06-15T00:00:00+00:00",
        )
        engine.update_strategic_narratives(
            brand_entity, category=rel_mut["category"], signal_type="vendor_selected",
            description=rel_mut["description"], source=rel_mut["source"], now="2026-06-15T00:00:00+00:00",
        )
        narratives = brand_entity["strategic_narratives"]
        self.assertEqual(len(narratives), 1)
        self.assertEqual(narratives[0]["confidence"], "low")
        self.assertEqual(len(narratives[0]["supporting_signals"]), 1)

        # Step 2: Restaurant365 back-office selection + Howie Rewards loyalty sunset.
        text2 = (
            "Hungry Howie's franchise system continues its digital transformation. "
            "The chain has also rolled out Restaurant365 for back-office accounting "
            "across its franchise system. Separately, Hungry Howie's confirmed that "
            "the Howie Rewards loyalty program will be sunset later this year."
        )
        result2 = engine.generate_mutations(
            text2, source_title="Article 2", source_url="https://example.com/2",
            ecosystem=self.ecosystem, baseline=[], strategic_memory={"signals": []},
        )

        rel_mut2 = next(
            m for m in result2["mutations"]
            if m["type"] == "vendor_customer_relationship" and m["category"] == "back_office"
        )
        self.assertEqual(rel_mut2["category"], "back_office")
        brand_entity2 = engine._get_or_create_brand_entity(
            self.ecosystem, rel_mut2["from_entity_id"], rel_mut2["brand_name"], "2026-06-15T00:00:00+00:00",
        )
        # Should resolve to the same brand entity created in step 1.
        self.assertIs(brand_entity2, brand_entity)

        engine.update_strategic_narratives(
            brand_entity2, category=rel_mut2["category"], signal_type="vendor_selected",
            description=rel_mut2["description"], source=rel_mut2["source"], now="2026-06-15T00:00:00+00:00",
        )

        lifecycle_mut = next(
            m for m in result2["mutations"] if m["type"] == "category_lifecycle_signal"
        )
        self.assertEqual(lifecycle_mut["category"], "loyalty")
        self.assertEqual(lifecycle_mut["signal_type"], "sunset")
        brand_entity3 = engine._get_or_create_brand_entity(
            self.ecosystem, lifecycle_mut["from_entity_id"], lifecycle_mut["brand_name"], "2026-06-15T00:00:00+00:00",
        )
        self.assertIs(brand_entity3, brand_entity)

        narrative = engine.update_strategic_narratives(
            brand_entity3, category=lifecycle_mut["category"], signal_type=lifecycle_mut["signal_type"],
            description=lifecycle_mut["description"], source=lifecycle_mut["source"], now="2026-06-15T00:00:00+00:00",
        )

        narratives = brand_entity["strategic_narratives"]
        self.assertEqual(len(narratives), 1)
        self.assertEqual(narrative["confidence"], "high")
        categories = {s["category"] for s in narrative["supporting_signals"]}
        self.assertEqual(categories, {"pos", "back_office", "loyalty"})
        self.assertTrue(
            any("loyalty platform replacement announcement" == s for s in narrative["next_expected_signals"])
        )


if __name__ == "__main__":
    unittest.main()
