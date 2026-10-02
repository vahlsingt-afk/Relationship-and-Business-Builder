"""
test_account_background_brief_fdd_governance.py — FDD Technology
Governance & Economics Account Background Brief enrichment
(render_fdd_governance_economics_section, brief §13).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import account_background_brief as abb  # noqa: E402
import technology_lifecycle as tl  # noqa: E402

_GRAPH = {
    "entities": [
        {"id": "brand-burger-king", "name": "Burger King", "entity_type": "brand", "aliases": []},
        {"id": "brand-mcdonalds", "name": "McDonald's", "entity_type": "brand", "aliases": []},
        {"id": "vendor-par-technology", "name": "PAR Technology", "entity_type": "vendor", "aliases": []},
    ],
    "relationships": [],
}


class _IsolatedPathsMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self._orig = {
            name: getattr(tl, name) for name in (
                "RELATIONSHIP_EVENTS_PATH", "GOVERNANCE_PATH", "PENETRATION_PATH", "CHANGE_EVENTS_PATH",
                "FORCING_SIGNALS_PATH", "FDD_SOURCES_PATH", "TECHNOLOGY_ECONOMICS_PATH",
                "GOVERNANCE_CHANGE_EVENTS_PATH", "PENETRATION_RECONCILIATION_PATH", "FDD_RESEARCH_GAPS_PATH",
                "ENTITY_RESOLUTION_REVIEW_PATH",
            )
        }
        for name in self._orig:
            setattr(tl, name, tmp / f"{name}.jsonl")
        self._orig_load_graph = tl._load_graph
        tl._load_graph = lambda: _GRAPH

    def tearDown(self):
        for name, path in self._orig.items():
            setattr(tl, name, path)
        tl._load_graph = self._orig_load_graph
        self._tmpdir.cleanup()


class TestRenderFddGovernanceEconomicsSection(_IsolatedPathsMixin, unittest.TestCase):
    def test_none_brand_entity_id_renders_nothing(self):
        self.assertEqual(abb.render_fdd_governance_economics_section(None), [])

    def test_untracked_brand_renders_nothing(self):
        self.assertEqual(abb.render_fdd_governance_economics_section("brand-mcdonalds"), [])

    def test_fdd_source_renders_bullet(self):
        tl.record_fdd_source(
            fdd_id="fdd-bk-2026", brand_id="brand-burger-king", fdd_year="2026",
            document_status="current", evidence_type="independent_evidence", confidence="high",
            source_url="https://example.com/fdd",
        )
        lines = abb.render_fdd_governance_economics_section("brand-burger-king")
        text = "\n".join(lines)
        self.assertIn("FDD Technology Governance & Economics", text)
        self.assertIn("2026 FDD", text)
        self.assertIn("status: current", text)

    def test_governance_with_fdd_fields_renders_table_row(self):
        tl.record_governance(
            governance_id="gov-1", brand_entity_id="brand-burger-king", technology_category="pos",
            governance_state="mandated", evidence="e", source_url=None, confidence="high",
            evidence_type="independent_evidence",
            fdd_sourced_fields={
                "contractual_authority": "franchisor may designate required technology systems",
                "current_requirement": "must use Vendor X POS",
                "grandfathering_status": "grandfathering_created",
                "conversion_deadline": "2028-01-01",
            },
        )
        lines = abb.render_fdd_governance_economics_section("brand-burger-king")
        text = "\n".join(lines)
        self.assertIn("must use Vendor X POS", text)
        self.assertIn("2028-01-01", text)

    def test_governance_without_fdd_fields_does_not_render_table_row(self):
        """A plain Technology Lifecycle governance record (no
        fdd_sourced_fields) is NOT FDD-specific content -- it already
        renders via render_technology_lifecycle_section's own governance
        handling (if any); this section must not duplicate it."""
        tl.record_governance(
            governance_id="gov-1", brand_entity_id="brand-burger-king", technology_category="pos",
            governance_state="mandated", evidence="e", source_url=None, confidence="high",
            evidence_type="independent_evidence",
        )
        self.assertEqual(abb.render_fdd_governance_economics_section("brand-burger-king"), [])

    def test_economics_renders_disclosed_range_not_point_estimate(self):
        tl.record_economics_observation(
            observation_id="eco-1", brand_id="brand-burger-king", technology_category="pos",
            evidence_type="independent_evidence", confidence="high",
            cost_range_low=100, cost_range_high=500, cost_unit="usd_per_location",
        )
        lines = abb.render_fdd_governance_economics_section("brand-burger-king")
        text = "\n".join(lines)
        self.assertIn("100–500", text)

    def test_governance_change_event_renders_bullet(self):
        tl.record_governance_change_event(
            change_event_id="gce-1", brand_id="brand-burger-king", technology_category="pos",
            change_type="optional_to_mandated", evidence="e", evidence_type="independent_evidence",
            confidence="high", from_value="approved_vendor_list", to_value="mandated",
        )
        lines = abb.render_fdd_governance_economics_section("brand-burger-king")
        text = "\n".join(lines)
        self.assertIn("optional_to_mandated", text)
        self.assertIn("approved_vendor_list", text)

    def test_penetration_reconciliation_renders_bullet(self):
        tl.record_penetration_reconciliation(
            reconciliation_id="rec-1",
            relationship_key={
                "brand_entity_id": "brand-burger-king", "technology_category": "pos",
                "vendor_entity_id": "vendor-par-technology",
            },
            reconciliation_status="migration_in_progress", evidence="legacy system still installed",
            evidence_type="independent_evidence", confidence="medium",
        )
        lines = abb.render_fdd_governance_economics_section("brand-burger-king")
        text = "\n".join(lines)
        self.assertIn("migration_in_progress", text)
        self.assertIn("legacy system still installed", text)

    def test_open_research_gap_renders_bullet(self):
        tl.record_fdd_research_gap(
            fdd_gap_id="gap-1", brand_id="brand-burger-king", gap_type="grandfathering_unknown",
            detail="unclear how many locations qualify",
        )
        lines = abb.render_fdd_governance_economics_section("brand-burger-king")
        text = "\n".join(lines)
        self.assertIn("grandfathering_unknown", text)
        self.assertIn("unclear how many locations qualify", text)

    def test_resolved_gap_does_not_render(self):
        tl.record_fdd_research_gap(
            fdd_gap_id="gap-1", brand_id="brand-burger-king", gap_type="grandfathering_unknown",
            detail="unclear how many locations qualify",
        )
        tl.record_fdd_research_gap(
            fdd_gap_id="gap-1-resolved", brand_id="brand-burger-king", gap_type="grandfathering_unknown",
            detail="resolved via case study", status="resolved", supersedes="gap-1",
        )
        self.assertEqual(abb.render_fdd_governance_economics_section("brand-burger-king"), [])


if __name__ == "__main__":
    unittest.main()
