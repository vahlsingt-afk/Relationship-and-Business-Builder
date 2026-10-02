#!/usr/bin/env python3
"""
test_win_plan.py — RB-2026-09-07.

Isolated against a disposable customers_prospects_common.ROOT AND a
disposable artifact_vault_common.VAULT_ROOT (see test_account_plan.py's
own docstring for why both, not just one, must be patched). Never touches
real project state.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import win_plan as wp  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index as ix  # noqa: E402

TEST_SLUG = "test-fixture-win-plan"


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
            "engagement_tier": "active_engagement",
            "portfolio_status": {"value": "active", "status": "confirmed", "evidence_ids": [],
                                  "confidence": "high", "as_of": "2026-09-07", "scope": "account",
                                  "last_reviewed_by": "human:test"},
            "owners": [], "executive_summary": "", "current_business_situation": "",
            "leadership": {"confirmed": [], "reported_unverified": []},
            "technology_stack": [], "commercial_models": [], "opportunities": [],
            "qualification": {
                "criteria": [
                    {"criterion": "Sufficient budget exists", "answer": "Y", "weight": 20, "points": 20,
                     "current_read": "Confirmed.", "next_step": "None.", "status": "confirmed"},
                ],
                "qualification_score": {"value": 20, "max": 100, "formula": "SUM(criteria.points)"},
            },
            "buying_influences": [
                {"person_id": "person-diego", "name": "Diego Haro", "title": "Senior Accountant",
                 "role_etuc": {"value": "E?"}, "rating": {"value": 2}, "personal_win": {"value": "Budget control."},
                 "competitive_preference": {"value": "Favorable"}},
            ],
            "strategic_position": {
                "euphoria_panic": {
                    "current_state": {"value": "Confidence"},
                    "reason": {"value": "Strong technical fit."},
                    "timing": {"value": "Active modernization."},
                },
                "competition": {"value": "Incumbent is legacy vendor X."},
                "position": {
                    "place_in_funnel": "Close", "customer_priority": "High",
                    "position_vs_competition": {"value": "Leading"},
                    "critical_test": "Who signs?", "immediate_move": "Confirm signer.",
                },
                "strengths": [
                    {"value": "Strong technical fit.", "best_action_plan": "Emphasize reliability.",
                     "owner": "Todd", "target": "Now"},
                ],
                "red_flags": [
                    {"value": "Budget not yet confirmed.", "best_action_plan": "Escalate to CFO.",
                     "owner": "Todd", "target": "Open", "status": "hypothesis"},
                    {"value": "Contradictory statements on timeline.", "best_action_plan": "Reconcile.",
                     "owner": "Todd", "target": "Open", "status": "contradicted"},
                ],
            },
            "bottom_line": "", "latest_review": {},
            "template_version": "test", "updated_at": "2026-09-07T00:00:00Z",
        })
        cpc.save_json(self.account_dir_path / "brand_profile.json", {"account_id": f"acct-{TEST_SLUG}"})
        cpc.save_json(self.account_dir_path / "contradictions.json", {"account_id": f"acct-{TEST_SLUG}", "contradictions": []})
        cpc.save_json(self.account_dir_path / "source_index.json", {"account_id": f"acct-{TEST_SLUG}", "sources": []})
        cpc.save_json(self.account_dir_path / "discovery_questions.json", {"account_id": f"acct-{TEST_SLUG}", "questions": []})
        (self.account_dir_path / "evidence.jsonl").write_text("", encoding="utf-8")

    def tearDown(self):
        cpc.ROOT = self._orig_cpc_root
        avc.VAULT_ROOT = self._orig_vault_root
        ix.INDEX_PATH = self._orig_index_path
        ix.UPDATE_LOG_PATH = self._orig_log_path
        self._tmpdir.cleanup()


class TestRenderSections(_IsolatedFixtureMixin):
    def test_buying_influences_with_ratings_section(self):
        account = cpc.load_json(self.account_dir_path / "account.json")
        lines = wp.render_buying_influences_with_ratings_section(account)
        text = "\n".join(lines)
        self.assertIn("Diego Haro", text)
        self.assertIn("2", text)  # rating
        self.assertIn("Budget control.", text)  # personal_win
        self.assertIn("Favorable", text)  # competitive_preference

    def test_strategic_position_renders_euphoria_panic(self):
        account = cpc.load_json(self.account_dir_path / "account.json")
        lines = wp.render_strategic_position_section(account["strategic_position"])
        text = "\n".join(lines)
        self.assertIn("Confidence", text)
        self.assertIn("Strong technical fit.", text)

    def test_strategic_position_renders_strengths_and_red_flags(self):
        account = cpc.load_json(self.account_dir_path / "account.json")
        lines = wp.render_strategic_position_section(account["strategic_position"])
        text = "\n".join(lines)
        self.assertIn("Strong technical fit.", text)
        self.assertIn("Budget not yet confirmed.", text)

    def test_contradicted_red_flag_marked(self):
        account = cpc.load_json(self.account_dir_path / "account.json")
        lines = wp.render_strategic_position_section(account["strategic_position"])
        text = "\n".join(lines)
        self.assertIn("Contradictory statements on timeline.** *(contradicted)*", text)

    def test_handles_empty_strategic_position(self):
        lines = wp.render_strategic_position_section({})
        self.assertIn("No strategic position recorded yet", "\n".join(lines))


class TestGenerateWinPlan(_IsolatedFixtureMixin):
    def test_rejects_empty_close_plan(self):
        with self.assertRaises(ValueError):
            wp.generate_win_plan(TEST_SLUG, close_plan="   ")

    def test_generates_and_never_mutates_account_json(self):
        before = cpc.load_json(self.account_dir_path / "account.json")
        result = wp.generate_win_plan(TEST_SLUG, close_plan="Lead with reliability, target signature by EOQ.")
        after = cpc.load_json(self.account_dir_path / "account.json")
        self.assertEqual(before, after)
        self.assertIn("Lead with reliability, target signature by EOQ.", result["markdown"])

    def test_reuses_account_plan_qualification_renderer(self):
        """Confirms real reuse, not a parallel re-derivation of the
        qualification scorecard."""
        result = wp.generate_win_plan(TEST_SLUG, close_plan="Plan.")
        self.assertIn("Sufficient budget exists", result["markdown"])
        self.assertIn("Score: 20 / 100", result["markdown"])

    def test_regenerating_creates_new_version(self):
        r1 = wp.generate_win_plan(TEST_SLUG, close_plan="First plan.")
        r2 = wp.generate_win_plan(TEST_SLUG, close_plan="Revised plan.")
        self.assertEqual(r1["version"]["version"], 1)
        self.assertEqual(r2["version"]["version"], 2)
        current = wp.get_current_win_plan(TEST_SLUG, include_content=True)
        self.assertIn("Revised plan.", current["content"])

    def test_unknown_account_raises(self):
        with self.assertRaises(FileNotFoundError):
            wp.generate_win_plan("does-not-exist-xyz", close_plan="x")

    def test_registers_in_intelligence_index_on_first_version(self):
        wp.generate_win_plan(TEST_SLUG, close_plan="Plan.")
        matches = ix.find("Test Fixture Co")
        win_plan_matches = [m for m in matches if m.get("resource_type") == "win_plan"]
        self.assertEqual(len(win_plan_matches), 1)


if __name__ == "__main__":
    unittest.main()
