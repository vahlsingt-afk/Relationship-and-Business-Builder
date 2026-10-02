#!/usr/bin/env python3
"""
test_vendor_engagement_analysis.py — RB-2026-09-07.

Isolated against a disposable customers_prospects_common.ROOT (the account
+ its technology_stack), a disposable ecosystem_intelligence.json path (via
ei.core.ECOSYSTEM_INTELLIGENCE_PATH — see test_battle_card.py's own
docstring for why this, not ei._read_graph, must be patched), a disposable
competitor_intelligence_common.ROOT (the competitive-angle cross-reference),
and disposable artifact_vault_common.VAULT_ROOT / intelligence_index paths.
Never touches real project state.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import vendor_engagement_analysis as vea  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index as ix  # noqa: E402

TEST_SLUG = "test-fixture-vea"


def _rel(rid, brand_id, vendor_id, category, *, status="active", **extra):
    row = {"id": rid, "from_entity_id": brand_id, "to_entity_id": vendor_id,
           "relationship_type": "uses_vendor_for_category", "category": category, "status": status}
    row.update(extra)
    return row


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)

        self._orig_cpc_root = cpc.ROOT
        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_cic_root = cic.ROOT
        self._orig_vault_root = avc.VAULT_ROOT
        self._orig_index_path = ix.INDEX_PATH
        self._orig_log_path = ix.UPDATE_LOG_PATH

        cpc.ROOT = tmp_root / "customers_prospects"
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = tmp_root / "ecosystem_intelligence.json"
        cic.ROOT = tmp_root / "competitor_intelligence"
        avc.VAULT_ROOT = tmp_root / "artifact_vault"
        ix.INDEX_PATH = tmp_root / "intelligence_index.json"
        ix.UPDATE_LOG_PATH = tmp_root / "intelligence_index_updates.jsonl"

        self.account_dir_path = cpc.ROOT / "accounts" / TEST_SLUG
        self.account_dir_path.mkdir(parents=True)
        cpc.save_json(self.account_dir_path / "account.json", {
            "account_id": f"acct-{TEST_SLUG}", "account_slug": TEST_SLUG,
            "display_name": "Test Fixture Co", "aliases": [],
            "engagement_tier": "active_engagement",
            "portfolio_status": {"value": "active", "status": "confirmed", "evidence_ids": [],
                                  "confidence": "high", "as_of": "2026-09-07", "scope": "account",
                                  "last_reviewed_by": "human:test"},
            "owners": [], "executive_summary": "", "current_business_situation": "",
            "leadership": {"confirmed": [], "reported_unverified": []},
            "technology_stack": [
                {"layer": "pos", "vendor": "Hand-Curated Vendor", "current_state": "Hand-curated row, should win.",
                 "status": "Confirmed", "confidence": "high"},
            ],
            "commercial_models": [], "opportunities": [],
            "qualification": {"criteria": []}, "buying_influences": [],
            "strategic_position": {}, "bottom_line": "", "latest_review": {},
            "template_version": "test", "updated_at": "2026-09-07T00:00:00Z",
        })
        cpc.save_json(self.account_dir_path / "brand_profile.json", {"account_id": f"acct-{TEST_SLUG}"})
        cpc.save_json(self.account_dir_path / "contradictions.json", {"account_id": f"acct-{TEST_SLUG}", "contradictions": []})
        cpc.save_json(self.account_dir_path / "source_index.json", {"account_id": f"acct-{TEST_SLUG}", "sources": []})
        cpc.save_json(self.account_dir_path / "discovery_questions.json", {"account_id": f"acct-{TEST_SLUG}", "questions": []})
        (self.account_dir_path / "evidence.jsonl").write_text("", encoding="utf-8")

        brand_id = "brand-test-fixture-co"
        graph = {
            "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-07",
            "entities": [
                {"id": brand_id, "name": "Test Fixture Co", "entity_type": "brand", "aliases": [],
                 "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]},
                {"id": "vendor-covered", "name": "Covered Vendor", "entity_type": "vendor", "aliases": [],
                 "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]},
                {"id": "vendor-tracked-rival", "name": "Tracked Rival", "entity_type": "vendor", "aliases": [],
                 "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]},
                {"id": "vendor-untracked", "name": "Untracked Vendor", "entity_type": "vendor", "aliases": [],
                 "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]},
            ],
            "relationships": [
                # Same category as the hand-curated row above -- must NOT
                # appear as a separate ecosystem row (hand-curated wins).
                _rel("rel-covered", brand_id, "vendor-covered", "pos", status="active"),
                # A different, uncovered category with a tracked-competitor
                # cross-reference and real strategic_note/risk content.
                _rel("rel-active", brand_id, "vendor-tracked-rival", "payments", status="active",
                     vendor_role="acquirer_or_payments", deployment_status="deployed_scope_not_publicly_disclosed",
                     confidence={"level": "high"}, risk="switching_friction",
                     strategic_note="Real strategic note about Tracked Rival at this account."),
                # A historical claim in an uncovered category -- must land in
                # Prior / Contested Claims, not Current Vendor Engagements.
                _rel("rel-historical", brand_id, "vendor-untracked", "loyalty", status="historical",
                     confidence={"level": "medium"}),
            ],
            "signals": [], "sources": [], "assessments": [], "user_relevance": [],
            "strategic_recommendations": [],
        }
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(json.dumps(graph), encoding="utf-8")

        (cic.ROOT / "_portfolio").mkdir(parents=True)
        (cic.ROOT / "_portfolio" / "competitor_registry.json").write_text(
            json.dumps({"registry": [{"competitor_slug": "tracked-rival", "display_name": "Tracked Rival"}]}),
            encoding="utf-8",
        )
        comp_dir = cic.ROOT / "competitors" / "tracked-rival"
        comp_dir.mkdir(parents=True)
        comp = {
            "competitor_id": "comp-tracked-rival", "competitor_slug": "tracked-rival",
            "display_name": "Tracked Rival", "vendor_entity_id": "vendor-tracked-rival",
            "competes_on": ["payments"],
            "positioning_summary": "Real positioning for Tracked Rival.",
            "todds_pov": "Real Todd's POV for Tracked Rival.",
            "vs_genius": {"genius_advantages": [], "competitor_advantages": []},
            "category_battle_cards": {}, "last_evidence_date": "2026-09-01",
        }
        (comp_dir / "competitor.json").write_text(json.dumps(comp), encoding="utf-8")
        (comp_dir / "evidence.jsonl").write_text("", encoding="utf-8")

    def tearDown(self):
        cpc.ROOT = self._orig_cpc_root
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        cic.ROOT = self._orig_cic_root
        avc.VAULT_ROOT = self._orig_vault_root
        ix.INDEX_PATH = self._orig_index_path
        ix.UPDATE_LOG_PATH = self._orig_log_path
        self._tmpdir.cleanup()


class TestRenderVendorEngagementAnalysis(_IsolatedFixtureMixin):
    def test_hand_curated_row_wins_over_covered_category(self):
        md = vea.render_vendor_engagement_analysis(TEST_SLUG)
        self.assertIn("Hand-Curated Vendor", md)
        self.assertNotIn("Covered Vendor", md)

    def test_uncovered_ecosystem_row_renders_with_rich_fields(self):
        md = vea.render_vendor_engagement_analysis(TEST_SLUG)
        self.assertIn("Tracked Rival", md)
        self.assertIn("Real strategic note about Tracked Rival at this account.", md)
        self.assertIn("switching_friction", md)

    def test_competitive_angle_present_for_tracked_competitor(self):
        md = vea.render_vendor_engagement_analysis(TEST_SLUG)
        self.assertIn("Real positioning for Tracked Rival.", md)
        self.assertIn("Real Todd's POV for Tracked Rival.", md)

    def test_no_competitive_angle_for_untracked_vendor(self):
        md = vea.render_vendor_engagement_analysis(TEST_SLUG)
        # The historical Untracked Vendor row should say so honestly.
        section = md.split("Untracked Vendor")[1]
        self.assertIn("No competitor profile content on file", section)

    def test_historical_claim_in_prior_claims_not_current(self):
        md = vea.render_vendor_engagement_analysis(TEST_SLUG)
        current_section = md.split("## Prior / Contested Claims")[0]
        prior_section = md.split("## Prior / Contested Claims")[1]
        self.assertNotIn("Untracked Vendor", current_section)
        self.assertIn("Untracked Vendor", prior_section)
        self.assertIn("Historical", prior_section)

    def test_omits_prior_claims_section_when_none(self):
        # Rebuild a graph with no non-active relationships at all.
        graph = json.loads(ei.core.ECOSYSTEM_INTELLIGENCE_PATH.read_text())
        graph["relationships"] = [r for r in graph["relationships"] if r.get("status") == "active"]
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(json.dumps(graph), encoding="utf-8")
        md = vea.render_vendor_engagement_analysis(TEST_SLUG)
        self.assertNotIn("Prior / Contested Claims", md)


class TestGenerateVendorEngagementAnalysis(_IsolatedFixtureMixin):
    def test_generates_and_persists(self):
        result = vea.generate_vendor_engagement_analysis(TEST_SLUG)
        self.assertEqual(result["version"]["version"], 1)
        current = vea.get_current_vendor_engagement_analysis(TEST_SLUG, include_content=True)
        self.assertIn("Tracked Rival", current["content"])

    def test_never_mutates_account_json(self):
        before = cpc.load_json(self.account_dir_path / "account.json")
        vea.generate_vendor_engagement_analysis(TEST_SLUG)
        after = cpc.load_json(self.account_dir_path / "account.json")
        self.assertEqual(before, after)

    def test_regenerating_unchanged_content_does_not_create_new_version(self):
        """artifact_vault_common.register_version()'s no-op guard
        (2026-09-25) -- unchanged content stays version 1 rather than
        bumping every time the daily refresh re-renders it. A real content
        change still versions correctly; see
        test_artifact_vault_common.py for that coverage directly."""
        r1 = vea.generate_vendor_engagement_analysis(TEST_SLUG)
        r2 = vea.generate_vendor_engagement_analysis(TEST_SLUG, generated_for="test-run")
        self.assertEqual(r1["version"]["version"], 1)
        self.assertEqual(r2["version"]["version"], 1)

    def test_unknown_account_raises(self):
        with self.assertRaises(FileNotFoundError):
            vea.generate_vendor_engagement_analysis("does-not-exist-xyz")

    def test_registers_in_intelligence_index_on_first_version(self):
        vea.generate_vendor_engagement_analysis(TEST_SLUG)
        matches = ix.find("Test Fixture Co")
        vea_matches = [m for m in matches if m.get("resource_type") == "vendor_engagement_analysis"]
        self.assertEqual(len(vea_matches), 1)


if __name__ == "__main__":
    unittest.main()
