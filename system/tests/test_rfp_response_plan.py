#!/usr/bin/env python3
"""
test_rfp_response_plan.py — RB-2026-09-07.

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

import rfp_response_plan as rrp  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index as ix  # noqa: E402

TEST_SLUG = "test-fixture-rfp-response-plan"


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
            "qualification": {"criteria": []}, "buying_influences": [],
            "strategic_position": {}, "bottom_line": "", "latest_review": {},
            "template_version": "test", "updated_at": "2026-09-07T00:00:00Z",
        })
        cpc.save_json(self.account_dir_path / "brand_profile.json", {"account_id": f"acct-{TEST_SLUG}"})
        cpc.save_json(self.account_dir_path / "contradictions.json", {"account_id": f"acct-{TEST_SLUG}", "contradictions": []})
        cpc.save_json(self.account_dir_path / "source_index.json", {"account_id": f"acct-{TEST_SLUG}", "sources": []})
        cpc.save_json(self.account_dir_path / "discovery_questions.json", {
            "account_id": f"acct-{TEST_SLUG}",
            "questions": [
                {"question_id": "dq-1", "topic": "Payments", "question": "Who processes payments today?",
                 "gap_type": "missing", "importance": "high", "status": "open", "as_of": "2026-09-07"},
                {"question_id": "dq-2", "topic": "Payments", "question": "Already answered.",
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


class TestRenderSections(unittest.TestCase):
    def test_open_loops_sorted_by_priority(self):
        loops = [
            {"priority": "P2", "loop": "Low priority item", "owner": "X", "completion_evidence": "Y", "status": "Open"},
            {"priority": "P0", "loop": "Urgent item", "owner": "X", "completion_evidence": "Y", "status": "Open"},
            {"priority": "P1", "loop": "Mid item", "owner": "X", "completion_evidence": "Y", "status": "Open"},
        ]
        lines = rrp.render_open_loops_section(loops)
        text = "\n".join(lines)
        self.assertLess(text.index("Urgent item"), text.index("Mid item"))
        self.assertLess(text.index("Mid item"), text.index("Low priority item"))

    def test_open_loops_handles_empty(self):
        lines = rrp.render_open_loops_section([])
        self.assertIn("No open loops recorded", "\n".join(lines))

    def test_clarification_questions_numbered(self):
        lines = rrp.render_clarification_questions_section(["First question?", "Second question?"])
        text = "\n".join(lines)
        self.assertIn("1. First question?", text)
        self.assertIn("2. Second question?", text)

    def test_submission_gate_matches_real_methodology(self):
        lines = rrp.render_submission_gate_section()
        text = "\n".join(lines)
        for requirement in rrp.SUBMISSION_GATE_REQUIREMENTS:
            self.assertIn(requirement, text)

    def test_related_discovery_questions_shows_only_open(self):
        questions = [
            {"question": "Open one?", "status": "open"},
            {"question": "Answered one?", "status": "answered"},
        ]
        lines = rrp.render_related_discovery_questions_section(questions)
        text = "\n".join(lines)
        self.assertIn("Open one?", text)
        self.assertNotIn("Answered one?", text)

    def test_related_discovery_questions_omits_section_when_none_open(self):
        lines = rrp.render_related_discovery_questions_section([{"question": "Answered.", "status": "answered"}])
        self.assertEqual(lines, [])


class TestGenerateRfpResponsePlan(_IsolatedFixtureMixin):
    def test_rejects_empty_deadline(self):
        with self.assertRaises(ValueError):
            rrp.generate_rfp_response_plan(TEST_SLUG, deadline="  ", open_loops=[{"priority": "P0", "loop": "x"}], clarification_questions=["q?"])

    def test_rejects_no_open_loops(self):
        with self.assertRaises(ValueError):
            rrp.generate_rfp_response_plan(TEST_SLUG, deadline="2026-09-04", open_loops=[], clarification_questions=["q?"])

    def test_rejects_no_clarification_questions(self):
        with self.assertRaises(ValueError):
            rrp.generate_rfp_response_plan(
                TEST_SLUG, deadline="2026-09-04",
                open_loops=[{"priority": "P0", "loop": "x", "owner": "y", "completion_evidence": "z", "status": "Open"}],
                clarification_questions=[],
            )

    def test_generates_and_never_mutates_account_json(self):
        before = cpc.load_json(self.account_dir_path / "account.json")
        result = rrp.generate_rfp_response_plan(
            TEST_SLUG, deadline="2026-09-04",
            open_loops=[{"priority": "P0", "loop": "Real open item", "owner": "Sales", "completion_evidence": "Sent.", "status": "Open"}],
            clarification_questions=["What is the deployment timeline?"],
        )
        after = cpc.load_json(self.account_dir_path / "account.json")
        self.assertEqual(before, after)
        self.assertIn("Real open item", result["markdown"])
        self.assertIn("What is the deployment timeline?", result["markdown"])

    def test_includes_related_open_discovery_questions(self):
        result = rrp.generate_rfp_response_plan(
            TEST_SLUG, deadline="2026-09-04",
            open_loops=[{"priority": "P0", "loop": "x", "owner": "y", "completion_evidence": "z", "status": "Open"}],
            clarification_questions=["q?"],
        )
        self.assertIn("Who processes payments today?", result["markdown"])
        self.assertNotIn("Already answered.", result["markdown"])

    def test_regenerating_creates_new_version(self):
        r1 = rrp.generate_rfp_response_plan(
            TEST_SLUG, deadline="2026-09-04",
            open_loops=[{"priority": "P0", "loop": "First.", "owner": "y", "completion_evidence": "z", "status": "Open"}],
            clarification_questions=["q1?"],
        )
        r2 = rrp.generate_rfp_response_plan(
            TEST_SLUG, deadline="2026-09-11",
            open_loops=[{"priority": "P0", "loop": "Revised.", "owner": "y", "completion_evidence": "z", "status": "Open"}],
            clarification_questions=["q2?"],
        )
        self.assertEqual(r1["version"]["version"], 1)
        self.assertEqual(r2["version"]["version"], 2)
        current = rrp.get_current_rfp_response_plan(TEST_SLUG, include_content=True)
        self.assertIn("Revised.", current["content"])

    def test_unknown_account_raises(self):
        with self.assertRaises(FileNotFoundError):
            rrp.generate_rfp_response_plan(
                "does-not-exist-xyz", deadline="2026-09-04",
                open_loops=[{"priority": "P0", "loop": "x", "owner": "y", "completion_evidence": "z", "status": "Open"}],
                clarification_questions=["q?"],
            )

    def test_registers_in_intelligence_index_on_first_version(self):
        rrp.generate_rfp_response_plan(
            TEST_SLUG, deadline="2026-09-04",
            open_loops=[{"priority": "P0", "loop": "x", "owner": "y", "completion_evidence": "z", "status": "Open"}],
            clarification_questions=["q?"],
        )
        matches = ix.find("Test Fixture Co")
        rfp_matches = [m for m in matches if m.get("resource_type") == "rfp_response_plan"]
        self.assertEqual(len(rfp_matches), 1)


if __name__ == "__main__":
    unittest.main()
