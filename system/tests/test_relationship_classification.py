import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "system" / "scripts" / "relationship_classification.py"


spec = importlib.util.spec_from_file_location("relationship_classification", SCRIPT)
rc = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(rc)


def _rel(**overrides) -> dict:
    base = {
        "id": "rel-test",
        "relationship_type": "uses_vendor_for_category",
        "deployment_claim_type": None,
        "vendor_role": None,
        "deployment": {},
    }
    base.update(overrides)
    return base


class RelationshipClassificationTest(unittest.TestCase):
    def test_pilot(self):
        rel = _rel(deployment_claim_type="pilot")
        result = rc.classify_relationship(rel)
        self.assertEqual(result["level"], 1)
        self.assertEqual(result["level_name"], "pilot")
        self.assertEqual(result["confidence"]["level"], "low")

    def test_single_franchisee(self):
        rel = _rel(
            deployment_claim_type="limited_operator_deployment",
            customer_operator="Acme Group LLC",
            deployment={"scope_unit_count": 8},
        )
        result = rc.classify_relationship(rel, brand_attrs={"unit_count": 600})
        self.assertEqual(result["level"], 2)
        self.assertEqual(result["level_name"], "single_franchisee")

    def test_multi_franchisee_adoption_via_franchise_groups(self):
        rel = _rel(
            deployment_claim_type="franchisee_deployment",
            deployment={"scope_unit_count": 125, "estimated_franchise_groups": 7},
        )
        result = rc.classify_relationship(rel, brand_attrs={"unit_count": 360})
        self.assertEqual(result["level"], 3)
        self.assertEqual(result["level_name"], "multi_franchisee_adoption")

    def test_preferred_vendor(self):
        rel = _rel(deployment_claim_type="approved_vendor", vendor_role="approved_hardware_vendor")
        result = rc.classify_relationship(rel)
        self.assertEqual(result["level"], 4)
        self.assertEqual(result["level_name"], "preferred_vendor")

    def test_standardized_platform_high_penetration(self):
        rel = _rel(
            deployment_claim_type="systemwide_deployment",
            deployment={"stage": "full_deployment", "deployed_units": 13500, "penetration_pct": None},
        )
        result = rc.classify_relationship(rel, brand_attrs={"unit_count": 13500})
        self.assertEqual(result["level"], 5)
        self.assertEqual(result["level_name"], "standardized_platform")

    def test_systemwide_claim_with_low_penetration_downgrades_to_multi_franchisee(self):
        rel = _rel(
            deployment_claim_type="systemwide_deployment",
            deployment={"stage": "active_rollout", "deployed_units": 3500, "penetration_pct": 50.0},
        )
        result = rc.classify_relationship(rel, brand_attrs={"unit_count": 7000})
        self.assertEqual(result["level"], 3)

    def test_logo_only_is_unclassified(self):
        rel = _rel(deployment_claim_type="logo_or_customer_page")
        self.assertIsNone(rc.classify_relationship(rel))

    def test_reference_only_is_unclassified(self):
        rel = _rel(deployment_claim_type="reference_only")
        self.assertIsNone(rc.classify_relationship(rel))

    def test_unknown_claim_type_is_unclassified(self):
        rel = _rel(deployment_claim_type="unknown")
        self.assertIsNone(rc.classify_relationship(rel))

    def test_heuristic_never_assigns_level_six(self):
        # Even with maximal evidence, the heuristic should top out at level 5.
        rel = _rel(
            deployment_claim_type="systemwide_deployment",
            vendor_role="system_of_record_pos",
            deployment={
                "stage": "full_deployment",
                "deployed_units": 14000,
                "penetration_pct": 100.0,
                "corporate_vs_franchise": "corporate_mandated",
            },
        )
        result = rc.classify_relationship(rel, brand_attrs={"unit_count": 14000})
        self.assertEqual(result["level"], 5)

    def test_should_overwrite_respects_manual_level_six(self):
        existing = {"level": 6, "level_name": "strategic_platform", "confidence": {"level": "critical", "score": 0.95}}
        new = {"level": 5, "level_name": "standardized_platform", "confidence": {"level": "high", "score": 0.85}}
        self.assertFalse(rc._should_overwrite(existing, new, force=False))
        self.assertTrue(rc._should_overwrite(existing, new, force=True))

    def test_should_overwrite_does_not_downgrade_higher_confidence(self):
        existing = {"level": 5, "level_name": "standardized_platform", "confidence": {"level": "high", "score": 0.85}}
        new = {"level": 2, "level_name": "single_franchisee", "confidence": {"level": "low", "score": 0.4}}
        self.assertFalse(rc._should_overwrite(existing, new, force=False))

    def test_should_overwrite_allows_when_no_existing(self):
        new = {"level": 2, "level_name": "single_franchisee", "confidence": {"level": "low", "score": 0.4}}
        self.assertTrue(rc._should_overwrite(None, new, force=False))

    def test_estimate_penetration_pct(self):
        self.assertEqual(rc.estimate_penetration_pct(125, 250), 50.0)
        self.assertIsNone(rc.estimate_penetration_pct(None, 250))
        self.assertIsNone(rc.estimate_penetration_pct(125, None))
        self.assertIsNone(rc.estimate_penetration_pct(125, 0))


if __name__ == "__main__":
    unittest.main()
