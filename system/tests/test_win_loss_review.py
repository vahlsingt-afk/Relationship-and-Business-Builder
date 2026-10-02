#!/usr/bin/env python3
"""
test_win_loss_review.py — RB-2026-09-28.

Isolated against a disposable customers_prospects_common.ROOT, a disposable
competitor_intelligence_common.ROOT, a disposable ecosystem_intelligence.json
path, a disposable artifact_vault_common.VAULT_ROOT, a disposable
win_loss_review.INDEX_PATH, and disposable intelligence_index paths. Never
touches real project state. Combines test_win_plan.py's account fixture with
test_value_wedge.py's competitor fixture, since this is the first artifact
type that needs both.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import win_loss_review as wlr  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402
import competitor_intelligence as compintel  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index as ix  # noqa: E402

ACCOUNT_SLUG = "test-fixture-account"
COMPETITOR_SLUG = "test-fixture-competitor"
OPPORTUNITY_SLUG = "pos-refresh-2026-09"


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)

        self._orig_cpc_root = cpc.ROOT
        self._orig_cic_root = cic.ROOT
        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_vault_root = avc.VAULT_ROOT
        self._orig_index_path = ix.INDEX_PATH
        self._orig_log_path = ix.UPDATE_LOG_PATH
        self._orig_wlr_index_path = wlr.INDEX_PATH

        cpc.ROOT = tmp_root / "customers_prospects"
        cic.ROOT = tmp_root / "competitor_intelligence"
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = tmp_root / "ecosystem_intelligence.json"
        avc.VAULT_ROOT = tmp_root / "artifact_vault"
        ix.INDEX_PATH = tmp_root / "intelligence_index.json"
        ix.UPDATE_LOG_PATH = tmp_root / "intelligence_index_updates.jsonl"
        wlr.INDEX_PATH = tmp_root / "win_loss_reviews_index.json"

        self.account_dir_path = cpc.ROOT / "accounts" / ACCOUNT_SLUG
        self.account_dir_path.mkdir(parents=True)
        cpc.save_json(self.account_dir_path / "account.json", {
            "account_id": f"acct-{ACCOUNT_SLUG}", "account_slug": ACCOUNT_SLUG,
            "display_name": "Test Fixture Account", "aliases": [],
            "engagement_tier": "active_engagement",
            "portfolio_status": {"value": "active", "status": "confirmed", "evidence_ids": [],
                                  "confidence": "high", "as_of": "2026-09-28", "scope": "account",
                                  "last_reviewed_by": "human:test"},
            "owners": [], "executive_summary": "", "current_business_situation": "",
            "leadership": {"confirmed": [], "reported_unverified": []},
            "technology_stack": [], "commercial_models": [], "buying_influences": [],
            "opportunities": [], "qualification": {"criteria": []}, "strategic_position": {},
            "bottom_line": "", "latest_review": {},
            "template_version": "test", "updated_at": "2026-09-28T00:00:00Z",
        })
        cpc.save_json(self.account_dir_path / "brand_profile.json", {"account_id": f"acct-{ACCOUNT_SLUG}"})
        cpc.save_json(self.account_dir_path / "contradictions.json", {"account_id": f"acct-{ACCOUNT_SLUG}", "contradictions": []})
        cpc.save_json(self.account_dir_path / "source_index.json", {"account_id": f"acct-{ACCOUNT_SLUG}", "sources": []})
        (self.account_dir_path / "evidence.jsonl").write_text("", encoding="utf-8")

        (cic.ROOT / "_portfolio").mkdir(parents=True)
        (cic.ROOT / "_portfolio" / "competitor_registry.json").write_text(
            json.dumps({"registry": [{"competitor_slug": COMPETITOR_SLUG, "display_name": "Test Fixture Competitor"}]}),
            encoding="utf-8",
        )
        self.comp_dir = cic.ROOT / "competitors" / COMPETITOR_SLUG
        self.comp_dir.mkdir(parents=True)
        (self.comp_dir / "competitor.json").write_text(json.dumps({
            "competitor_id": f"comp-{COMPETITOR_SLUG}", "competitor_slug": COMPETITOR_SLUG,
            "display_name": "Test Fixture Competitor", "vendor_entity_id": None,
            "competes_on": [], "positioning_summary": "", "todds_pov": "",
            "vs_genius": {"genius_advantages": [], "competitor_advantages": []},
            "category_battle_cards": {}, "last_evidence_date": None,
        }), encoding="utf-8")
        (self.comp_dir / "evidence.jsonl").write_text("", encoding="utf-8")

        ei.core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(json.dumps({
            "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-28",
            "entities": [], "relationships": [], "signals": [], "sources": [],
            "assessments": [], "user_relevance": [], "strategic_recommendations": [],
        }), encoding="utf-8")

    def tearDown(self):
        cpc.ROOT = self._orig_cpc_root
        cic.ROOT = self._orig_cic_root
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        avc.VAULT_ROOT = self._orig_vault_root
        ix.INDEX_PATH = self._orig_index_path
        ix.UPDATE_LOG_PATH = self._orig_log_path
        wlr.INDEX_PATH = self._orig_wlr_index_path
        self._tmpdir.cleanup()

    def _generate(self, **overrides):
        kwargs = dict(
            outcome="loss", deal_size="$250k ARR", close_date="2026-09-15",
            decision_criteria="Price and implementation speed decided it.",
            competitive_dynamics="They positioned on price; our battle card held on integration depth.",
            what_we_did_well="Strong technical discovery.",
            what_we_would_change="Should have engaged the CFO earlier.",
            root_cause_or_key_driver="They underpriced the first-year contract by 30%.",
            competitors_in_deal=[COMPETITOR_SLUG],
        )
        kwargs.update(overrides)
        return wlr.generate_win_loss_review(ACCOUNT_SLUG, OPPORTUNITY_SLUG, **kwargs)


class TestGenerateWinLossReview(_IsolatedFixtureMixin):
    def test_unknown_account_raises(self):
        with self.assertRaises(FileNotFoundError):
            wlr.generate_win_loss_review(
                "does-not-exist-xyz", OPPORTUNITY_SLUG, outcome="loss", deal_size="", close_date="",
                decision_criteria="x", competitive_dynamics="", what_we_did_well="",
                what_we_would_change="", root_cause_or_key_driver="x",
            )

    def test_rejects_invalid_outcome(self):
        with self.assertRaises(ValueError):
            self._generate(outcome="tie")

    def test_rejects_empty_decision_criteria(self):
        with self.assertRaises(ValueError):
            self._generate(decision_criteria="   ")

    def test_rejects_empty_root_cause(self):
        with self.assertRaises(ValueError):
            self._generate(root_cause_or_key_driver="")

    def test_full_review_renders_all_core_sections(self):
        result = self._generate()
        md = result["markdown"]
        self.assertIn("## Deal Summary", md)
        self.assertIn("## Decision Criteria", md)
        self.assertIn("## Competitive Dynamics", md)
        self.assertIn("## What We Did Well", md)
        self.assertIn("## What We'd Change", md)
        self.assertIn("## Root Cause", md)
        self.assertIn("## Action Items for the Playbook", md)
        self.assertIn("They underpriced the first-year contract by 30%.", md)

    def test_win_renders_key_driver_heading_not_root_cause(self):
        result = self._generate(outcome="win")
        self.assertIn("## Key Driver", result["markdown"])
        self.assertNotIn("## Root Cause", result["markdown"])

    def test_customer_quote_only_renders_for_win_with_real_text(self):
        loss_result = self._generate(outcome="loss", customer_quote="Great partner.")
        self.assertNotIn("Customer Quote", loss_result["markdown"])

        win_no_quote = self._generate(outcome="win", customer_quote="   ")
        self.assertNotIn("Customer Quote", win_no_quote["markdown"])

        win_with_quote = self._generate(outcome="win", customer_quote="They loved the rollout speed.")
        self.assertIn("## Customer Quote / Reference", win_with_quote["markdown"])
        self.assertIn("They loved the rollout speed.", win_with_quote["markdown"])

    def test_competitor_evidence_note_written_with_correct_category(self):
        self._generate(outcome="loss")
        evidence = cic.load_jsonl(self.comp_dir / "evidence.jsonl")
        notes = [e for e in evidence if e.get("category") == "customer_loss"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["source"], "Todd Vahlsing (firsthand)")
        self.assertEqual(notes[0]["confidence"], "high")

    def test_win_writes_customer_win_category(self):
        self._generate(outcome="win", root_cause_or_key_driver="Fastest implementation timeline in the eval.")
        evidence = cic.load_jsonl(self.comp_dir / "evidence.jsonl")
        notes = [e for e in evidence if e.get("category") == "customer_win"]
        self.assertEqual(len(notes), 1)

    def test_exactly_one_gap_point_written_against_primary_competitor_only(self):
        self._generate(outcome="loss", competitors_in_deal=[COMPETITOR_SLUG])
        comp = cic.load_competitor(COMPETITOR_SLUG)["competitor"]
        self.assertEqual(len(comp["vs_genius"]["competitor_advantages"]), 1)
        self.assertEqual(
            comp["vs_genius"]["competitor_advantages"][0]["point"],
            "They underpriced the first-year contract by 30%.",
        )
        self.assertEqual(len(comp["vs_genius"]["genius_advantages"]), 0)

    def test_win_writes_gap_point_on_genius_side(self):
        self._generate(outcome="win", root_cause_or_key_driver="Fastest implementation timeline in the eval.")
        comp = cic.load_competitor(COMPETITOR_SLUG)["competitor"]
        self.assertEqual(len(comp["vs_genius"]["genius_advantages"]), 1)
        self.assertEqual(len(comp["vs_genius"]["competitor_advantages"]), 0)

    def test_no_competitors_named_skips_evidence_and_gap_point_gracefully(self):
        result = self._generate(competitors_in_deal=[])
        self.assertIn("None named", result["markdown"])
        evidence = cic.load_jsonl(self.comp_dir / "evidence.jsonl")
        self.assertEqual(evidence, [])

    def test_unknown_competitor_auto_creates_shell(self):
        self._generate(competitors_in_deal=["brand-new-competitor-xyz"])
        reg = cic.load_registry()
        slugs = [e["competitor_slug"] for e in reg["registry"]]
        self.assertIn("brand-new-competitor-xyz", slugs)

    def test_regenerating_unchanged_content_does_not_create_new_version(self):
        r1 = self._generate()
        r2 = self._generate()
        self.assertEqual(r1["version"]["version"], 1)
        self.assertEqual(r2["version"]["version"], 1)

    def test_regenerating_unchanged_content_does_not_duplicate_evidence_or_gap_points(self):
        """Real bug caught via a live-data smoke test: register_version()'s
        no-op dedup guard only protects the vault document -- without its
        own gate, add_competitive_note()/add_gap_point() would write a
        fresh duplicate on every regenerate call, since neither has an
        equivalent dedup guard."""
        self._generate()
        self._generate()
        self._generate()
        evidence = cic.load_jsonl(self.comp_dir / "evidence.jsonl")
        self.assertEqual(len(evidence), 1)
        comp = cic.load_competitor(COMPETITOR_SLUG)["competitor"]
        self.assertEqual(len(comp["vs_genius"]["competitor_advantages"]), 1)

    def test_changed_regeneration_does_not_relog_competitor_side(self):
        """Deliberate: a later regenerate (version > 1) is not re-logged to
        the competitor side, matching add_gap_point()'s own append-only,
        no-update precedent."""
        self._generate()
        self._generate(deal_size="$300k ARR")
        evidence = cic.load_jsonl(self.comp_dir / "evidence.jsonl")
        self.assertEqual(len(evidence), 1)

    def test_changed_regeneration_versions_and_updates_index_in_place(self):
        self._generate()
        self._generate(deal_size="$300k ARR")
        current = wlr.get_current_win_loss_review(ACCOUNT_SLUG, OPPORTUNITY_SLUG, include_content=True)
        self.assertEqual(current["version"], 2)
        reviews = wlr.list_win_loss_reviews(ACCOUNT_SLUG)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["deal_size"], "$300k ARR")

    def test_list_win_loss_reviews_returns_all_for_account_and_blank_for_none(self):
        self.assertEqual(wlr.list_win_loss_reviews(ACCOUNT_SLUG), [])
        self._generate()
        reviews = wlr.list_win_loss_reviews(ACCOUNT_SLUG)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["opportunity_slug"], OPPORTUNITY_SLUG)

    def test_registers_in_intelligence_index_on_first_version(self):
        self._generate()
        matches = ix.find("Test Fixture Account")
        wlr_matches = [m for m in matches if m.get("resource_type") == "win_loss_review"]
        self.assertEqual(len(wlr_matches), 1)


if __name__ == "__main__":
    unittest.main()
