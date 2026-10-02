#!/usr/bin/env python3
"""
test_account_plan.py — RB-2026-09-07.

Isolated against a disposable customers_prospects_common.ROOT AND a
disposable artifact_vault_common.VAULT_ROOT (both patched to real tmp
directories, not just one of them) -- account_plan.py writes through both
modules, and this session already found live-data pollution once from a
test that patched only one of two path constants a module actually uses
(see test_blue_sheet_impact_review.py's real incident, 2026-09-07). Never
touches real project state.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import account_plan as ap  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index as ix  # noqa: E402

TEST_SLUG = "test-fixture-account-plan"


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)

        self._orig_cpc_root = cpc.ROOT
        self._orig_vault_root = avc.VAULT_ROOT
        self._orig_index_path = ix.INDEX_PATH
        self._orig_log_path = ix.UPDATE_LOG_PATH
        cpc.ROOT = tmp_root / "customers_prospects"
        avc.VAULT_ROOT = tmp_root / "artifact_vault"
        ix.INDEX_PATH = tmp_root / "intelligence_index.json"
        ix.UPDATE_LOG_PATH = tmp_root / "intelligence_index_updates.jsonl"

        self.account_dir_path = cpc.ROOT / "accounts" / TEST_SLUG
        self.account_dir_path.mkdir(parents=True)
        cpc.save_json(self.account_dir_path / "account.json", {
            "account_id": f"acct-{TEST_SLUG}", "account_slug": TEST_SLUG,
            "display_name": "Test Fixture Co", "aliases": [],
            "engagement_tier": "pre_engagement",
            "portfolio_status": {"value": "research", "status": "confirmed", "evidence_ids": [],
                                  "confidence": "high", "as_of": "2026-09-07", "scope": "account",
                                  "last_reviewed_by": "human:test"},
            "owners": [], "executive_summary": "", "current_business_situation": "",
            "leadership": {
                "confirmed": [{"name": "Jane Confirmed", "title": "CEO"}],
                "reported_unverified": [],
            },
            "technology_stack": [{
                "layer": "POS", "vendor": "RealVendor", "status": "Verify", "confidence": "medium",
                "current_state": "seed", "evidence_ids": [], "as_of": "2026-08-01",
                "scope": "account", "last_reviewed_by": "human:test",
            }],
            "commercial_models": [], "opportunities": [],
            "qualification": {
                "criteria": [
                    {"criterion": "Sufficient budget exists", "answer": "U", "weight": 20, "points": 0,
                     "current_read": "Unknown.", "next_step": "Confirm budget.", "status": "unknown"},
                    {"criterion": "At least one Coach", "answer": "N", "weight": 20, "points": 0,
                     "current_read": "No coach identified.", "next_step": "Find a coach.", "status": "unknown"},
                ],
                "qualification_score": {"value": 0, "max": 100, "formula": "SUM(criteria.points)"},
            },
            "buying_influences": [{
                "person_id": "person-test", "name": "Test Buyer", "title": "COO",
                "role_etuc": {"value": "E?", "status": "hypothesis"},
                "influence": "High", "current_read": "Public leader.", "next_step": "Confirm authority.",
            }],
            "strategic_position": {}, "bottom_line": "", "latest_review": {},
            "template_version": "test", "updated_at": "2026-09-07T00:00:00Z",
        })
        cpc.save_json(self.account_dir_path / "brand_profile.json", {
            "account_id": f"acct-{TEST_SLUG}",
            "founded": "1999", "headquarters": "Testville, TX", "segment": "LSR",
        })
        cpc.save_json(self.account_dir_path / "contradictions.json", {"account_id": f"acct-{TEST_SLUG}", "contradictions": []})
        cpc.save_json(self.account_dir_path / "source_index.json", {"account_id": f"acct-{TEST_SLUG}", "sources": []})
        cpc.save_json(self.account_dir_path / "discovery_questions.json", {
            "account_id": f"acct-{TEST_SLUG}",
            "questions": [
                {"question_id": "dq-1", "topic": "Payments", "question": "Who processes payments today?",
                 "gap_type": "missing", "importance": "high", "status": "open", "as_of": "2026-09-07"},
                {"question_id": "dq-2", "topic": "Payments", "question": "What POS is deployed?",
                 "gap_type": "missing", "importance": "high", "status": "answered", "as_of": "2026-09-07"},
            ],
        })
        (self.account_dir_path / "evidence.jsonl").write_text("", encoding="utf-8")

    def tearDown(self):
        cpc.ROOT = self._orig_cpc_root
        avc.VAULT_ROOT = self._orig_vault_root
        ix.INDEX_PATH = self._orig_index_path
        ix.UPDATE_LOG_PATH = self._orig_log_path
        self._tmpdir.cleanup()


class TestRenderSections(_IsolatedFixtureMixin):
    def test_qualification_section_renders_real_criteria(self):
        account = cpc.load_json(self.account_dir_path / "account.json")
        lines = ap.render_qualification_section(account)
        text = "\n".join(lines)
        self.assertIn("Sufficient budget exists", text)
        self.assertIn("At least one Coach", text)
        self.assertIn("| U |", text)

    def test_qualification_section_handles_no_criteria(self):
        lines = ap.render_qualification_section({"qualification": {"criteria": []}})
        self.assertIn("No qualification criteria recorded yet", "\n".join(lines))

    def test_discovery_status_shows_answered_count_and_open_questions(self):
        questions = json.loads((self.account_dir_path / "discovery_questions.json").read_text())["questions"]
        lines = ap.render_discovery_status_section(questions)
        text = "\n".join(lines)
        self.assertIn("1 of 2 discovery questions answered", text)
        self.assertIn("Who processes payments today?", text)  # open question shown
        self.assertNotIn("What POS is deployed?", text)  # answered question not in "Still Open"

    def test_buying_influences_section_renders_real_data(self):
        account = cpc.load_json(self.account_dir_path / "account.json")
        lines = ap.render_buying_influences_section(account)
        text = "\n".join(lines)
        self.assertIn("Test Buyer", text)
        self.assertIn("E?", text)  # role_etuc.value extracted from the field-object


class TestGenerateAccountPlan(_IsolatedFixtureMixin):
    def test_rejects_invalid_go_no_go(self):
        with self.assertRaises(ValueError):
            ap.generate_account_plan(TEST_SLUG, account_strategy="Real strategy text.", go_no_go="maybe")

    def test_rejects_empty_strategy(self):
        with self.assertRaises(ValueError):
            ap.generate_account_plan(TEST_SLUG, account_strategy="   ", go_no_go="go")

    def test_generates_and_persists_judgment_fields_onto_account_json(self):
        result = ap.generate_account_plan(
            TEST_SLUG, account_strategy="Lead with loyalty modernization angle.",
            go_no_go="go", generated_for="Todd Vahlsing",
        )
        self.assertIn("Lead with loyalty modernization angle.", result["markdown"])
        self.assertIn("Test Fixture Co Account Plan", result["markdown"])
        self.assertEqual(result["version"]["version"], 1)

        account = cpc.load_json(self.account_dir_path / "account.json")
        self.assertEqual(account["account_strategy"], "Lead with loyalty modernization angle.")
        self.assertEqual(account["account_plan_go_no_go"], "go")
        self.assertIn("account_plan_as_of", account)

    def test_internal_plan_uses_author_voice_and_no_system_provenance(self):
        rendered = ap.render_account_plan(
            TEST_SLUG,
            account_strategy="Ask Maggie to introduce Todd and coordinate with Todd.",
            go_no_go="go",
            prepared_for="Todd Vahlsing",
        )
        self.assertIn("Prepared by: Todd Vahlsing", rendered)
        self.assertIn("introduce me", rendered)
        self.assertIn("coordinate with me", rendered)
        self.assertNotIn("Prepared for: Todd Vahlsing", rendered)
        self.assertNotIn("persisted RBB", rendered)
        self.assertNotIn("Generated ", rendered)

    def test_reuses_shared_section_renderers_from_background_brief(self):
        """Confirms real reuse, not a parallel re-derivation -- brand
        profile and leadership content (owned by account_background_brief
        .py) must appear in the Account Plan output."""
        result = ap.generate_account_plan(TEST_SLUG, account_strategy="Strategy.", go_no_go="pending")
        md = result["markdown"]
        self.assertIn("Founded: 1999", md)  # render_brand_profile_section
        self.assertIn("Jane Confirmed", md)  # render_leadership_section
        self.assertIn("RealVendor", md)  # render_technology_environment_section

    def test_regenerating_creates_new_version_and_archives_prior(self):
        r1 = ap.generate_account_plan(TEST_SLUG, account_strategy="First strategy.", go_no_go="pending")
        r2 = ap.generate_account_plan(TEST_SLUG, account_strategy="Revised strategy.", go_no_go="go")
        self.assertEqual(r1["version"]["version"], 1)
        self.assertEqual(r2["version"]["version"], 2)
        self.assertIn("Revised strategy.", r2["markdown"])

        current = ap.get_current_account_plan(TEST_SLUG, include_content=True)
        self.assertEqual(current["version"], 2)
        self.assertIn("Revised strategy.", current["content"])

        history_dir = avc.instance_dir(ap.ARTIFACT_TYPE_DIR, TEST_SLUG) / "history"
        self.assertEqual(len(list(history_dir.iterdir())), 1)

    def test_unknown_account_raises(self):
        with self.assertRaises(FileNotFoundError):
            ap.generate_account_plan("does-not-exist-xyz", account_strategy="x", go_no_go="go")

    def test_registers_in_intelligence_index_on_first_version(self):
        ap.generate_account_plan(TEST_SLUG, account_strategy="Strategy.", go_no_go="go")
        matches = ix.find("Test Fixture Co")
        account_plan_matches = [m for m in matches if m.get("resource_type") == "account_plan"]
        self.assertEqual(len(account_plan_matches), 1)

    def test_regeneration_logs_update_not_a_second_index_entry(self):
        ap.generate_account_plan(TEST_SLUG, account_strategy="First.", go_no_go="pending")
        ap.generate_account_plan(TEST_SLUG, account_strategy="Second.", go_no_go="go")
        matches = ix.find("Test Fixture Co")
        account_plan_matches = [m for m in matches if m.get("resource_type") == "account_plan"]
        self.assertEqual(len(account_plan_matches), 1, "regeneration must log an update, not add a new index entry")
        log_lines = ix.UPDATE_LOG_PATH.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(log_lines), 1)


if __name__ == "__main__":
    unittest.main()
