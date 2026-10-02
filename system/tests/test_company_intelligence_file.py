"""
test_company_intelligence_file.py — RB-DEFECT-046 Slice 3 (RB 9.90):
Company Intelligence File + enrich-before-comment.

Test groups:
  CIF1: build_company_intelligence_file() unit tests
  CIF2: generate_mutations() "enrichment" field (enrich-before-comment)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import intelligence_mutation_engine as engine


def _empty_ecosystem() -> dict:
    return {"entities": [], "relationships": []}


class CIF1BuildCompanyIntelligenceFile(unittest.TestCase):
    def test_unknown_entity_returns_unavailable(self):
        result = engine.build_company_intelligence_file("Nonexistent Brand", _empty_ecosystem())
        self.assertFalse(result["available"])
        self.assertIn("reason", result)

    def test_known_brand_with_no_data_has_all_unknown_categories(self):
        ecosystem = _empty_ecosystem()
        ecosystem["entities"].append({
            "id": "brand-test", "name": "Test Brand", "entity_type": "brand",
        })
        result = engine.build_company_intelligence_file("Test Brand", ecosystem)
        self.assertTrue(result["available"])
        self.assertEqual(result["brand_name"], "Test Brand")
        for category in engine._TECH_STACK_CATEGORIES:
            self.assertEqual(result["tech_stack"][category]["status"], "unknown")
            self.assertIsNone(result["tech_stack"][category]["vendor"])
        self.assertEqual(result["strategic_narratives"], [])

    def test_active_relationship_populates_tech_stack(self):
        ecosystem = _empty_ecosystem()
        ecosystem["entities"].append({"id": "brand-test", "name": "Test Brand", "entity_type": "brand"})
        ecosystem["entities"].append({"id": "vendor-toast", "name": "Toast", "entity_type": "vendor"})
        ecosystem["relationships"].append({
            "id": "rel-1",
            "from_entity_id": "brand-test",
            "to_entity_id": "vendor-toast",
            "relationship_type": "uses_vendor_for_category",
            "category": "pos",
            "status": "active",
            "source": {"title": "Article"},
            "created_at": "2026-06-01T00:00:00+00:00",
        })
        result = engine.build_company_intelligence_file("Test Brand", ecosystem)
        self.assertEqual(result["tech_stack"]["pos"], {
            "vendor": "Toast", "status": "active", "source": {"title": "Article"},
        })
        self.assertEqual(result["last_verified"], "2026-06-01T00:00:00+00:00")

    def test_unresolved_sunset_signal_marks_category_sunset(self):
        ecosystem = _empty_ecosystem()
        brand_entity = {"id": "brand-test", "name": "Test Brand", "entity_type": "brand"}
        ecosystem["entities"].append(brand_entity)
        engine.update_strategic_narratives(
            brand_entity, category="loyalty", signal_type="sunset",
            description="Test Brand sunset of loyalty platform",
            source={"title": "Article"}, now="2026-06-15T00:00:00+00:00",
        )
        result = engine.build_company_intelligence_file("Test Brand", ecosystem)
        self.assertEqual(result["tech_stack"]["loyalty"], {
            "vendor": None, "status": "sunset", "source": None,
        })
        self.assertEqual(len(result["strategic_narratives"]), 1)
        self.assertEqual(result["last_verified"], "2026-06-15T00:00:00+00:00")

    def test_active_relationship_takes_precedence_over_sunset_signal(self):
        # Category replaced after sunset -> active relationship wins, no
        # leftover "sunset" entry for that category.
        ecosystem = _empty_ecosystem()
        brand_entity = {"id": "brand-test", "name": "Test Brand", "entity_type": "brand"}
        ecosystem["entities"].append(brand_entity)
        ecosystem["entities"].append({"id": "vendor-thanx", "name": "Thanx", "entity_type": "vendor"})
        ecosystem["relationships"].append({
            "id": "rel-1",
            "from_entity_id": "brand-test",
            "to_entity_id": "vendor-thanx",
            "relationship_type": "uses_vendor_for_category",
            "category": "loyalty",
            "status": "active",
            "source": {"title": "Article 2"},
            "created_at": "2026-06-20T00:00:00+00:00",
        })
        engine.update_strategic_narratives(
            brand_entity, category="loyalty", signal_type="sunset",
            description="Test Brand sunset of loyalty platform",
            source={"title": "Article"}, now="2026-06-15T00:00:00+00:00",
        )
        result = engine.build_company_intelligence_file("Test Brand", ecosystem)
        self.assertEqual(result["tech_stack"]["loyalty"]["status"], "active")
        self.assertEqual(result["tech_stack"]["loyalty"]["vendor"], "Thanx")


class CIF2EnrichmentField(unittest.TestCase):
    def test_enrichment_reflects_prior_state_before_this_articles_mutations(self):
        ecosystem = _empty_ecosystem()

        # Step 1: Toast POS selection establishes the brand entity.
        text1 = (
            "Hungry Howie's technology roadmap took a step forward this week. "
            "The chain has selected Toast as its new point-of-sale platform "
            "across all franchise locations."
        )
        result1 = engine.generate_mutations(
            text1, source_title="Article 1", source_url="https://example.com/1",
            ecosystem=ecosystem, baseline=[], strategic_memory={"signals": []},
        )
        rel_mut1 = next(
            m for m in result1["mutations"]
            if m["type"] == "vendor_customer_relationship" and m["category"] == "pos"
        )
        # No prior data existed before article 1.
        enrich1 = result1["enrichment"][rel_mut1["from_entity_id"]]
        self.assertFalse(enrich1["available"])

        # Apply article 1's relationship + narrative update so article 2 sees
        # prior context (mirrors apply_mutations()'s vendor_customer_relationship branch).
        ecosystem["relationships"].append({
            "id": rel_mut1["id"],
            "from_entity_id": rel_mut1["from_entity_id"],
            "to_entity_id": rel_mut1["to_entity_id"],
            "relationship_type": rel_mut1["relationship_type"],
            "category": rel_mut1["category"],
            "status": "active",
            "source": rel_mut1["source"],
            "created_at": "2026-06-15T00:00:00+00:00",
        })
        ecosystem["entities"].append({"id": rel_mut1["to_entity_id"], "name": "Toast", "entity_type": "vendor"})
        brand_entity = engine._get_or_create_brand_entity(
            ecosystem, rel_mut1["from_entity_id"], rel_mut1["brand_name"], "2026-06-15T00:00:00+00:00",
        )
        engine.update_strategic_narratives(
            brand_entity, category=rel_mut1["category"], signal_type="vendor_selected",
            description=rel_mut1["description"], source=rel_mut1["source"], now="2026-06-15T00:00:00+00:00",
        )

        # Step 2: a follow-up article about a loyalty sunset should see the
        # POS narrative established by article 1 as "prior context".
        text2 = (
            "Hungry Howie's franchise system continues its digital transformation. "
            "Separately, Hungry Howie's confirmed that the Howie Rewards loyalty "
            "program will be sunset later this year."
        )
        result2 = engine.generate_mutations(
            text2, source_title="Article 2", source_url="https://example.com/2",
            ecosystem=ecosystem, baseline=[], strategic_memory={"signals": []},
        )
        lifecycle_mut = next(
            m for m in result2["mutations"] if m["type"] == "category_lifecycle_signal"
        )
        enrich2 = result2["enrichment"][lifecycle_mut["from_entity_id"]]
        self.assertTrue(enrich2["available"])
        self.assertEqual(enrich2["tech_stack"]["pos"]["vendor"], "Toast")
        self.assertEqual(len(enrich2["strategic_narratives"]), 1)
        self.assertEqual(enrich2["strategic_narratives"][0]["confidence"], "low")


if __name__ == "__main__":
    unittest.main()
