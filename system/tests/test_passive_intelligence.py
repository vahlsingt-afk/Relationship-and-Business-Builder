"""
RB passive intelligence tests.

These cover the defect where uploaded/vendor/social content was treated as
conversation instead of confidence-weighted intelligence.
"""
import importlib.util
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "system" / "scripts" / "passive_intelligence.py"
DAILY_BRIEF_SCRIPT = ROOT / "system" / "scripts" / "daily_brief.py"

spec = importlib.util.spec_from_file_location("passive_intelligence", SCRIPT)
pi = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(pi)

db_spec = importlib.util.spec_from_file_location("daily_brief", DAILY_BRIEF_SCRIPT)
daily_brief = importlib.util.module_from_spec(db_spec)
assert db_spec.loader is not None
db_spec.loader.exec_module(daily_brief)


class PassiveIntelligenceTests(unittest.TestCase):
    def test_extract_claims_isolates_factual_assertions(self):
        text = (
            "AI coding is entering a new phase. "
            "Uber exceeded its AI coding budget in four months. "
            "AI-generated PRs contain 1.7x more defects."
        )
        claims = pi.extract_claims(text)
        self.assertGreaterEqual(len(claims), 2)
        claim_text = " ".join(c["claim_text"] for c in claims)
        self.assertIn("Uber exceeded", claim_text)
        self.assertIn("1.7x more defects", claim_text)

    def test_linkedin_vendor_claim_without_corroboration_is_not_canonical(self):
        result = pi.evaluate_passive_intelligence(
            "Microsoft cancelled internal Claude licenses. Uber exceeded AI coding budget in four months.",
            {"source_type": "linkedin_vendor_post", "platform": "linkedin", "title": "Vendor post"},
            graph={"sources": [], "signals": [], "relationships": []},
            today=date(2026, 5, 27),
        )
        self.assertEqual(result["contract"], "rb_passive_intelligence_evaluation_v1")
        self.assertGreaterEqual(result["claim_count"], 1)
        for claim in result["claims"]:
            self.assertIn(claim["claim_status"], {"plausible_but_unverified", "insufficient_evidence"})
            self.assertNotIn(claim["graph_mutation_eligibility"], {"canonical_fact", "corroborated_intelligence"})
            self.assertTrue(claim["uncertainty_preserved"])

    def test_existing_rb_evidence_can_corroborate_claim(self):
        graph = {
            "sources": [
                {
                    "id": "src-credible-report-ai-defects",
                    "title": "Credible report found AI generated PRs have 1.7x more defects",
                    "notes": "Independent engineering study.",
                }
            ],
            "signals": [],
            "relationships": [],
        }
        result = pi.evaluate_passive_intelligence(
            "AI-generated PRs contain 1.7x more defects.",
            {"source_type": "credible_reporting", "platform": "article", "title": "Engineering report"},
            graph=graph,
            today=date(2026, 5, 27),
        )
        claim = result["claims"][0]
        self.assertGreaterEqual(claim["corroboration_count"], 1)
        self.assertIn(claim["claim_status"], {"verified", "likely_true"})
        self.assertIn(claim["graph_mutation_eligibility"], {"canonical_fact", "corroborated_intelligence"})

    def test_signal_metadata_compresses_evaluation_for_graph_signal(self):
        result = pi.evaluate_passive_intelligence(
            "NVIDIA acknowledged compute costs exceeded labor costs.",
            {"source_type": "linkedin_vendor_post", "platform": "linkedin"},
            graph={"sources": [], "signals": [], "relationships": []},
            today=date(2026, 5, 27),
        )
        metadata = pi.signal_metadata_from_evaluation(result)
        required = {
            "source_type",
            "source_quality",
            "confidence_score",
            "corroboration_count",
            "claim_status",
            "verification_timestamp",
            "narrative_classification",
            "graph_mutation_eligibility",
            "strategic_relevance_score",
        }
        self.assertTrue(required.issubset(metadata.keys()))

    def test_uncorroborated_claim_includes_external_search_plan(self):
        result = pi.evaluate_passive_intelligence(
            "Uber exceeded AI coding budget in four months.",
            {"source_type": "linkedin_vendor_post", "platform": "linkedin"},
            graph={"sources": [], "signals": [], "relationships": []},
            today=date(2026, 5, 27),
        )
        claim = result["claims"][0]
        plan = claim["corroboration_search_plan"]
        self.assertEqual(plan["status"], "needed")
        self.assertGreaterEqual(plan["minimum_new_corroborating_sources"], 2)
        self.assertIn("earnings_call", plan["preferred_source_types"])
        self.assertTrue(plan["search_queries"])
        self.assertTrue(result["summary"]["corroboration_search_required"])
        self.assertEqual(result["summary"]["corroboration_search_queue"][0]["claim_id"], claim["claim_id"])

    def test_daily_brief_preserves_zero_corroboration_for_passive_signal(self):
        report = {
            "ecosystem_intelligence": {
                "watch_list_signals": [{
                    "entity_id": "brand-uber",
                    "entity_name": "Uber",
                    "signal_class": "strategic_weak_signal",
                    "recommended_action": "monitor",
                    "summary": "Uber exceeded AI coding budget in four months.",
                    "confidence": "medium",
                    "confidence_score": 0.42,
                    "claim_status": "plausible_but_unverified",
                    "corroboration_count": 0,
                    "corroboration_sources": [],
                    "source_quality": "medium",
                    "graph_mutation_eligibility": "weak_signal",
                }]
            }
        }
        item = daily_brief._ecosystem_intelligence_section(report)[0]
        self.assertIn("corroboration: 0", item["summary"])
        self.assertEqual(item["provenance"]["corroboration_count"], 0)
        self.assertEqual(item["extras"]["graph_mutation_eligibility"], "weak_signal")


if __name__ == "__main__":
    unittest.main()
