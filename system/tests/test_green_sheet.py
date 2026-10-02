#!/usr/bin/env python3
"""
test_green_sheet.py — RB-2026-09-07.

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

import green_sheet as gs  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index as ix  # noqa: E402

TEST_SLUG = "test-fixture-green-sheet"


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
            "technology_stack": [],
            "commercial_models": [],
            "opportunities": [{
                "opportunity_id": "opp-test-1",
                "single_sales_objective": {"value": "Deploy DMB across all 200 locations by Q2.", "status": "confirmed"},
            }],
            "qualification": {"criteria": []},
            "buying_influences": [
                {"person_id": "person-diego", "name": "Diego Haro", "title": "Senior Accountant",
                 "role_etuc": {"value": "T / E?"}, "current_read": "Named discovery participant.",
                 "next_step": "Confirm budget authority."},
                {"person_id": "person-randy", "name": "Randy Cerrate", "title": "Purchasing Specialist",
                 "role_etuc": {"value": "T"}, "current_read": "Attended discovery call.",
                 "next_step": "Confirm evaluation role."},
            ],
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
            ],
        })
        with open(self.account_dir_path / "evidence.jsonl", "w", encoding="utf-8") as f:
            import json
            f.write(json.dumps({
                "evidence_id": "ev-1", "account_id": f"acct-{TEST_SLUG}", "opportunity_ids": [],
                "source_type": "meeting_notes", "durable_source_id": "x", "source_author": "Todd",
                "participants": ["Diego Haro", "Todd Vahlsing"], "event_date": "2026-09-01",
                "ingestion_date": "2026-09-01", "excerpt": "Discovery call with Diego on payments.",
                "extracted_claims": ["Diego confirmed his own budget involvement."],
                "evidence_class": "direct_customer_correspondence", "confidence": "high",
                "scope": "account", "limitations": "", "contradiction_links": [],
                "processing_version": "test",
            }) + "\n")
            f.write(json.dumps({
                "evidence_id": "ev-2", "account_id": f"acct-{TEST_SLUG}", "opportunity_ids": [],
                "source_type": "email", "durable_source_id": "y", "source_author": "Todd",
                "participants": ["Someone Else"], "event_date": "2026-08-01",
                "ingestion_date": "2026-08-01", "excerpt": "Unrelated email thread.",
                "extracted_claims": [], "evidence_class": "user_reported", "confidence": "medium",
                "scope": "account", "limitations": "", "contradiction_links": [],
                "processing_version": "test",
            }) + "\n")

    def tearDown(self):
        cpc.ROOT = self._orig_cpc_root
        avc.VAULT_ROOT = self._orig_vault_root
        ix.INDEX_PATH = self._orig_index_path
        ix.UPDATE_LOG_PATH = self._orig_log_path
        self._tmpdir.cleanup()


class TestNameMatching(unittest.TestCase):
    def test_matches_partial_name(self):
        self.assertTrue(gs._name_matches("Diego Haro", ["Diego"]))

    def test_does_not_match_unrelated_name(self):
        self.assertFalse(gs._name_matches("Diego Haro", ["Randy"]))

    def test_case_insensitive(self):
        self.assertTrue(gs._name_matches("Diego Haro", ["diego haro"]))

    def test_empty_attendee_list_matches_nothing(self):
        self.assertFalse(gs._name_matches("Diego Haro", []))


class TestRenderSections(_IsolatedFixtureMixin):
    def test_buying_influences_filtered_to_attendees(self):
        account = cpc.load_json(self.account_dir_path / "account.json")
        lines = gs.render_relevant_buying_influences_section(account, ["Diego"])
        text = "\n".join(lines)
        self.assertIn("Diego Haro", text)
        self.assertNotIn("Randy Cerrate", text)  # not on this call

    def test_unmatched_attendee_flagged_explicitly(self):
        account = cpc.load_json(self.account_dir_path / "account.json")
        lines = gs.render_relevant_buying_influences_section(account, ["Diego", "Nobody On File"])
        text = "\n".join(lines)
        self.assertIn("No on-file buying-influence record for: Nobody On File", text)

    def test_evidence_filtered_to_attendees(self):
        evidence = [
            {"participants": ["Diego Haro"], "event_date": "2026-09-01", "excerpt": "Discovery call.", "extracted_claims": []},
            {"participants": ["Someone Else"], "event_date": "2026-08-01", "excerpt": "Unrelated.", "extracted_claims": []},
        ]
        lines = gs.render_relevant_evidence_section(evidence, ["Diego"])
        text = "\n".join(lines)
        self.assertIn("Discovery call.", text)
        self.assertNotIn("Unrelated.", text)


class TestGenerateGreenSheet(_IsolatedFixtureMixin):
    def test_rejects_empty_call_purpose(self):
        with self.assertRaises(ValueError):
            gs.generate_green_sheet(TEST_SLUG, call_purpose="  ", attendees=["Diego Haro"])

    def test_rejects_no_attendees(self):
        with self.assertRaises(ValueError):
            gs.generate_green_sheet(TEST_SLUG, call_purpose="Confirm budget.", attendees=[])

    def test_generates_without_authorization_gate(self):
        """Deliberately ungated -- no user_authorization_quote parameter
        exists on this function at all, unlike Account Plan/Blue Sheet."""
        result = gs.generate_green_sheet(TEST_SLUG, call_purpose="Confirm budget authority.", attendees=["Diego Haro"])
        self.assertIn("Confirm budget authority.", result["markdown"])
        self.assertEqual(result["version"]["version"], 1)

    def test_never_mutates_account_json(self):
        before = cpc.load_json(self.account_dir_path / "account.json")
        gs.generate_green_sheet(TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"])
        after = cpc.load_json(self.account_dir_path / "account.json")
        self.assertEqual(before, after)

    def test_includes_relevant_evidence_and_influences_and_open_questions(self):
        result = gs.generate_green_sheet(TEST_SLUG, call_purpose="Discuss payments.", attendees=["Diego Haro"])
        md = result["markdown"]
        self.assertIn("Diego Haro", md)  # buying influence
        self.assertIn("Discovery call with Diego on payments.", md)  # evidence
        self.assertNotIn("Unrelated email thread.", md)  # not this attendee's evidence
        self.assertIn("Who processes payments today?", md)  # open discovery question

    def test_includes_account_level_single_sales_objective_as_context(self):
        result = gs.generate_green_sheet(TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"])
        self.assertIn("Deploy DMB across all 200 locations by Q2.", result["markdown"])

    def test_talking_points_included_when_given(self):
        result = gs.generate_green_sheet(
            TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"],
            talking_points="Lead with reliability, mention the Q2 timeline.",
        )
        self.assertIn("Lead with reliability, mention the Q2 timeline.", result["markdown"])

    def test_regenerating_creates_new_version_and_archives_prior(self):
        r1 = gs.generate_green_sheet(TEST_SLUG, call_purpose="First call.", attendees=["Diego Haro"])
        r2 = gs.generate_green_sheet(TEST_SLUG, call_purpose="Follow-up call.", attendees=["Randy Cerrate"])
        self.assertEqual(r1["version"]["version"], 1)
        self.assertEqual(r2["version"]["version"], 2)
        current = gs.get_current_green_sheet(TEST_SLUG, include_content=True)
        self.assertIn("Follow-up call.", current["content"])

    def test_unknown_account_raises(self):
        with self.assertRaises(FileNotFoundError):
            gs.generate_green_sheet("does-not-exist-xyz", call_purpose="x", attendees=["Someone"])

    def test_registers_in_intelligence_index_on_first_version(self):
        gs.generate_green_sheet(TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"])
        matches = ix.find("Test Fixture Co")
        green_sheet_matches = [m for m in matches if m.get("resource_type") == "green_sheet"]
        self.assertEqual(len(green_sheet_matches), 1)


class TestConceptualSellingScaffolding(_IsolatedFixtureMixin):
    """The Getting Information / Getting Commitment question-mode reminders
    are fixed, always-present scaffolding -- confirmed with Todd to render
    unconditionally, not gated behind any new field."""

    def test_getting_information_scaffolding_always_present(self):
        result = gs.generate_green_sheet(TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"])
        md = result["markdown"]
        self.assertIn("## Getting Information", md)
        self.assertIn("Confirmation Questions", md)

    def test_getting_commitment_scaffolding_always_present(self):
        result = gs.generate_green_sheet(TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"])
        md = result["markdown"]
        self.assertIn("## Getting Commitment", md)
        self.assertIn("Commitment Questions", md)
        self.assertIn("Basic Issue Questions", md)


class TestConceptColumn(_IsolatedFixtureMixin):
    def test_concept_column_omitted_when_not_supplied(self):
        result = gs.generate_green_sheet(TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"])
        md = result["markdown"]
        self.assertIn("| Name | Title | Role (ETUC) | Current Read | Next Step |", md)
        self.assertNotIn("Concept (This Call)", md)

    def test_concept_merges_into_on_this_call_table(self):
        result = gs.generate_green_sheet(
            TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"],
            buying_influence_concepts=[{"name": "Diego", "concept": "Avoid downtime during POS cutover."}],
        )
        md = result["markdown"]
        self.assertIn("Concept (This Call)", md)
        self.assertIn("Avoid downtime during POS cutover.", md)

    def test_concept_for_unmatched_name_is_dropped_not_erroring(self):
        result = gs.generate_green_sheet(
            TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"],
            buying_influence_concepts=[{"name": "Nobody On File", "concept": "Some concept."}],
        )
        self.assertNotIn("Some concept.", result["markdown"])


class TestShowingValueSection(_IsolatedFixtureMixin):
    def test_omitted_when_no_fields_supplied(self):
        result = gs.generate_green_sheet(TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"])
        self.assertNotIn("## Showing Value", result["markdown"])

    def test_valid_business_reason_renders(self):
        result = gs.generate_green_sheet(
            TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"],
            valid_business_reason="Confirm the POS cutover plan won't disrupt service.",
        )
        md = result["markdown"]
        self.assertIn("## Showing Value", md)
        self.assertIn("Confirm the POS cutover plan won't disrupt service.", md)

    def test_credibility_if_established_and_if_not_render_correctly(self):
        result = gs.generate_green_sheet(
            TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"],
            credibility_if_established="Reference the Cafe Rio rollout.",
            credibility_if_not_established="Lead with the reference customer list.",
        )
        md = result["markdown"]
        self.assertIn("If established: Reference the Cafe Rio rollout.", md)
        self.assertIn("If not yet established: Lead with the reference customer list.", md)


class TestGivingInformationSection(_IsolatedFixtureMixin):
    def test_omitted_when_no_fields_supplied(self):
        result = gs.generate_green_sheet(TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"])
        self.assertNotIn("## Giving Information", result["markdown"])

    def test_perspective_to_share_renders(self):
        result = gs.generate_green_sheet(
            TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"],
            perspective_to_share="Most cutovers fail on staff training, not the tech.",
        )
        md = result["markdown"]
        self.assertIn("## Giving Information", md)
        self.assertIn("Most cutovers fail on staff training, not the tech.", md)

    def test_unique_strengths_table_renders(self):
        result = gs.generate_green_sheet(
            TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"],
            unique_strengths=[{"so_what": "24/7 onsite support during cutover.", "prove_it": "Cafe Rio reference call."}],
        )
        md = result["markdown"]
        self.assertIn("So What?", md)
        self.assertIn("Prove It!", md)
        self.assertIn("24/7 onsite support during cutover.", md)
        self.assertIn("Cafe Rio reference call.", md)


class TestGettingCommitmentSubContent(_IsolatedFixtureMixin):
    def test_action_commitments_render_when_supplied(self):
        result = gs.generate_green_sheet(
            TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"],
            action_commitment_best="Sign the pilot agreement.",
            action_commitment_minimum="Agree to a follow-up demo.",
        )
        md = result["markdown"]
        self.assertIn("Best: Sign the pilot agreement.", md)
        self.assertIn("Minimum: Agree to a follow-up demo.", md)

    def test_action_commitments_subsection_omitted_when_not_supplied(self):
        result = gs.generate_green_sheet(TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"])
        md = result["markdown"]
        self.assertIn("## Getting Commitment", md)
        self.assertNotIn("**Action Commitments**", md)

    def test_basic_issues_bullets_render_when_supplied(self):
        result = gs.generate_green_sheet(
            TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"],
            basic_issues=["Worried about losing the current vendor relationship."],
        )
        self.assertIn("Worried about losing the current vendor relationship.", result["markdown"])


class TestNewFieldsNeverPersisted(_IsolatedFixtureMixin):
    def test_new_fields_never_persisted_to_account_json(self):
        before = cpc.load_json(self.account_dir_path / "account.json")
        gs.generate_green_sheet(
            TEST_SLUG, call_purpose="Test.", attendees=["Diego Haro"],
            buying_influence_concepts=[{"name": "Diego", "concept": "Avoid downtime."}],
            valid_business_reason="Reason.", credibility_if_established="Est.",
            credibility_if_not_established="Not est.", perspective_to_share="Perspective.",
            unique_strengths=[{"so_what": "So what.", "prove_it": "Prove it."}],
            action_commitment_best="Best.", action_commitment_minimum="Minimum.",
            basic_issues=["Issue."],
        )
        after = cpc.load_json(self.account_dir_path / "account.json")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
