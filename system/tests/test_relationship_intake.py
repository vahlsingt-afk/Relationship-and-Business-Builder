"""
test_relationship_intake.py — Relationship intelligence intake tests.

Covers DEFECT-011: executive relationship intelligence not autonomously persisted.

Test groups:
  R1: Core invariants (required fields, guardrails, persistence_status, empty/missing entity)
  R2: Jenswold thread — full signal classification, trust, ecosystem, WMN, mutation proposals
  R3: Confirmation and rejection lifecycle (record_interaction)
  R4: Who Matters Now scoring and query_interactions retrieval
  R5: Entity resolution (explicit params override extraction; ecosystem tag merge)
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import relationship_intake
import mutations

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Simulates a positive unsolicited follow-up email from an executive contact.
# Signature block allows entity extraction: name, role, org.
JENSWOLD_FOLLOWUP = (
    "Hi Todd — I just wanted to follow up and say how much I enjoyed our conversation. "
    "Your perspective on operations and technology was genuinely impressive. "
    "I'd love to stay connected and explore how we might collaborate. "
    "Best regards, Elizabeth Jenswold, President, Bridgepoint Consulting"
)

# Variant that mentions Hospitality Table directly for ecosystem detection test.
JENSWOLD_WITH_HT = (
    "Hi Todd — I just wanted to follow up after our Hospitality Table conversation. "
    "Your perspective on operations and technology was genuinely impressive. "
    "I'd love to stay connected and explore how we might collaborate. "
    "Best regards, Elizabeth Jenswold, President, Bridgepoint Consulting"
)

# Referral signal — highest trust delta
REFERRAL_TEXT = (
    "I referred you to Maria Santos at Acme Corp — she's looking for exactly what you do. "
    "Maria Santos, Managing Director, Acme Corp"
)

# Generic encouragement — should still persist if entity provided explicitly
GENERIC_WITH_ENTITY = "Things look great — keep up the momentum. Looking forward to talking soon."

# Entirely empty
EMPTY_TEXT = "   "

# No named entity detectable
NO_ENTITY_TEXT = "Thanks so much for your help! Really appreciated it."


# ---------------------------------------------------------------------------
# R1: Core invariants
# ---------------------------------------------------------------------------


class TestCoreInvariants(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "interaction_ledger.json"

    def test_R1a_returns_interactions_list(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store
        )
        self.assertIn("interactions", result)
        self.assertIsInstance(result["interactions"], list)

    def test_R1b_required_fields_on_interaction(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store
        )
        self.assertTrue(len(result["interactions"]) >= 1)
        interaction = result["interactions"][0]
        required = {
            "id", "contact_id", "signal_type", "trust_delta",
            "persistence_status", "relationship_state_proposed",
            "strategic_classification", "recommended_posture",
            "ecosystem_tags", "who_matters_now_score",
            "mutation_proposals" if False else "id",  # mutation_proposals on result, not interaction
        }
        for field in {"id", "contact_id", "signal_type", "trust_delta",
                      "persistence_status", "relationship_state_proposed",
                      "strategic_classification", "recommended_posture",
                      "ecosystem_tags", "who_matters_now_score"}:
            self.assertIn(field, interaction, f"Missing field: {field}")

    def test_R1c_all_mutations_require_confirmation(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store
        )
        for proposal in result["mutation_proposals"]:
            self.assertTrue(
                proposal.get("requires_confirmation"),
                f"Mutation missing requires_confirmation: {proposal.get('mutation_type')}",
            )

    def test_R1d_interactions_start_as_proposed(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store
        )
        for interaction in result["interactions"]:
            self.assertEqual(interaction["claim_status"], "proposed")
            self.assertEqual(interaction["persistence_status"], "pending confirmation")
            self.assertIsNone(interaction["confirmed_at"])

    def test_R1e_empty_input_graceful(self):
        result = relationship_intake.process_relationship_thread(
            EMPTY_TEXT, store_path=self.store
        )
        self.assertEqual(result["persistence_status"], "RB did not persist")
        self.assertEqual(result["interactions"], [])
        self.assertIn("note", result)

    def test_R1f_no_entity_graceful(self):
        result = relationship_intake.process_relationship_thread(
            NO_ENTITY_TEXT, store_path=self.store
        )
        self.assertEqual(result["persistence_status"], "RB did not persist")
        self.assertEqual(result["interactions"], [])
        self.assertIn("note", result)
        self.assertIn("entity_name", result["note"])

    def test_R1g_persistence_status_never_none(self):
        for text in [JENSWOLD_FOLLOWUP, EMPTY_TEXT, NO_ENTITY_TEXT]:
            result = relationship_intake.process_relationship_thread(
                text, store_path=self.store
            )
            self.assertIsNotNone(result["persistence_status"])
            self.assertIn(
                result["persistence_status"],
                relationship_intake.PERSISTENCE_STATUSES,
            )


# ---------------------------------------------------------------------------
# R2: Jenswold thread — signal classification, trust, ecosystem, WMN, mutations
# ---------------------------------------------------------------------------


class TestJenswoldThread(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "interaction_ledger.json"
        self.result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store
        )
        self.interaction = self.result["interactions"][0]
        self.cos = self.result["cos_surface"]

    def test_R2a_classified_as_unsolicited_positive_followup(self):
        self.assertEqual(
            self.interaction["signal_type"], "unsolicited_positive_followup"
        )

    def test_R2b_trust_delta_is_2(self):
        self.assertEqual(self.interaction["trust_delta"], 2)

    def test_R2c_relationship_state_is_warm(self):
        self.assertEqual(self.interaction["relationship_state_proposed"], "warm")

    def test_R2d_entity_name_extracted(self):
        self.assertEqual(self.interaction["entity"]["name"], "Elizabeth Jenswold")

    def test_R2e_entity_org_extracted(self):
        self.assertEqual(self.interaction["entity"]["org"], "Bridgepoint Consulting")

    def test_R2f_entity_role_extracted(self):
        self.assertEqual(self.interaction["entity"]["role"], "President")

    def test_R2g_contact_id_derived_correctly(self):
        self.assertEqual(self.interaction["contact_id"], "elizabeth-jenswold")

    def test_R2h_executive_weight_positive(self):
        # President → exec_weight 2
        self.assertGreaterEqual(self.interaction["executive_weight"], 1)

    def test_R2i_who_matters_now_score_positive(self):
        self.assertGreater(self.interaction["who_matters_now_score"], 0)

    def test_R2j_strategic_classification_set(self):
        self.assertIsNotNone(self.interaction["strategic_classification"])
        self.assertGreater(len(self.interaction["strategic_classification"]), 0)

    def test_R2k_recommended_posture_set(self):
        self.assertIsNotNone(self.interaction["recommended_posture"])
        self.assertGreater(len(self.interaction["recommended_posture"]), 0)

    def test_R2l_hr_consulting_ecosystem_tag_detected(self):
        # "Bridgepoint" in text → hr_consulting
        self.assertIn("hr_consulting", self.interaction["ecosystem_tags"])

    def test_R2m_executive_network_ecosystem_tag_detected(self):
        # "President" in text → executive_network
        self.assertIn("executive_network", self.interaction["ecosystem_tags"])

    def test_R2n_interaction_persisted_to_ledger(self):
        ledger = json.loads(self.store.read_text())
        ids = [i["id"] for i in ledger["interactions"]]
        self.assertIn(self.interaction["id"], ids)

    def test_R2o_mutation_proposals_present(self):
        self.assertGreaterEqual(len(self.result["mutation_proposals"]), 2)

    def test_R2p_contact_upsert_proposal_present(self):
        types = [p["mutation_type"] for p in self.result["mutation_proposals"]]
        self.assertIn("contact_upsert", types)

    def test_R2q_interaction_event_proposal_present(self):
        types = [p["mutation_type"] for p in self.result["mutation_proposals"]]
        self.assertIn("interaction_event", types)

    def test_R2r_contact_upsert_data_complete(self):
        upsert = next(
            p for p in self.result["mutation_proposals"]
            if p["mutation_type"] == "contact_upsert"
        )
        cd = upsert["contact_data"]
        self.assertEqual(cd["id"], "elizabeth-jenswold")
        self.assertEqual(cd["name"], "Elizabeth Jenswold")
        self.assertIsNotNone(cd["last_touch"])
        self.assertIn("signal_class", cd)

    def test_R2s_cos_surface_populated(self):
        self.assertIsNotNone(self.cos)
        self.assertEqual(self.cos["entity"]["name"], "Elizabeth Jenswold")
        self.assertEqual(self.cos["entity"]["contact_id"], "elizabeth-jenswold")
        self.assertIn("strategic_classification", self.cos)
        self.assertIn("relationship_state_proposed", self.cos)
        self.assertIn("recommended_posture", self.cos)

    def test_R2t_explicit_ecosystem_tag_merged(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP,
            ecosystem_tags=["hospitality_table"],
            store_path=Path(self.tmp) / "ledger2.json",
        )
        tags = result["interactions"][0]["ecosystem_tags"]
        self.assertIn("hospitality_table", tags)

    def test_R2u_hospitality_table_detected_from_text(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_WITH_HT, store_path=Path(self.tmp) / "ledger3.json"
        )
        tags = result["interactions"][0]["ecosystem_tags"]
        self.assertIn("hospitality_table", tags)


# ---------------------------------------------------------------------------
# R3: Confirmation and rejection lifecycle
# ---------------------------------------------------------------------------


class TestConfirmationLifecycle(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "interaction_ledger.json"

    def test_R3a_confirm_sets_rb_recorded(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store
        )
        iid = result["interactions"][0]["id"]
        # RB-2026-08-28: confirming now also attempts to create a baseline
        # entry for a not-yet-known contact_id (see TestConfirmationCreatesBaselineContact
        # below) -- mock cmd_contact_add here so this pre-existing test of
        # the ledger-status half doesn't write a fake "Elizabeth Jenswold"
        # into whatever real baseline_index.json this process happens to see.
        with patch.object(mutations, "cmd_contact_add", return_value=0):
            updated = relationship_intake.record_interaction(iid, confirmed=True, store_path=self.store)
        self.assertEqual(updated["persistence_status"], "RB recorded")
        self.assertEqual(updated["claim_status"], "confirmed")
        self.assertIsNotNone(updated["confirmed_at"])

    def test_R3b_reject_sets_rb_skipped(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store
        )
        iid = result["interactions"][0]["id"]
        updated = relationship_intake.record_interaction(iid, confirmed=False, store_path=self.store)
        self.assertEqual(updated["persistence_status"], "RB skipped")
        self.assertEqual(updated["claim_status"], "rejected")

    def test_R3c_unknown_id_returns_error(self):
        result = relationship_intake.record_interaction(
            "ri-nonexistent-000000", confirmed=True, store_path=self.store
        )
        self.assertIn("error", result)

    def test_R3d_confirmation_persisted_in_ledger(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store
        )
        iid = result["interactions"][0]["id"]
        with patch.object(mutations, "cmd_contact_add", return_value=0):
            relationship_intake.record_interaction(iid, confirmed=True, store_path=self.store)
        ledger = json.loads(self.store.read_text())
        entry = next(i for i in ledger["interactions"] if i["id"] == iid)
        self.assertEqual(entry["persistence_status"], "RB recorded")


class TestConfirmationCreatesBaselineContact(unittest.TestCase):
    """RB-2026-08-28: confirming an interaction for a contact_id NOT
    already in baseline_index.json used to do nothing to baseline at all
    -- record_interaction() only ever updated interaction_ledger.json.
    The contact_upsert proposal process_relationship_thread() built was
    real but nothing ever applied it, through any path -- not apply=True
    (deliberately withheld by design for a new contact) and not the
    "proper" reviewed confirm step this behavior was supposedly deferred
    to. This is the fix: record_interaction() now creates the baseline
    entry itself when confirming an interaction for a not-yet-known
    contact_id."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "interaction_ledger.json"
        self.baseline_path = Path(self.tmp) / "baseline_index.json"
        self._orig_baseline_path = relationship_intake.BASELINE_INDEX_PATH
        relationship_intake.BASELINE_INDEX_PATH = self.baseline_path

    def tearDown(self):
        relationship_intake.BASELINE_INDEX_PATH = self._orig_baseline_path

    def _seed_baseline(self, entries):
        self.baseline_path.write_text(json.dumps(entries), encoding="utf-8")

    def test_confirming_new_contact_creates_baseline_entry(self):
        self._seed_baseline([])
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store,
        )
        iid = result["interactions"][0]["id"]

        with patch.object(mutations, "cmd_contact_add", return_value=0) as mocked_add:
            updated = relationship_intake.record_interaction(iid, confirmed=True, store_path=self.store)

        self.assertEqual(updated["baseline_contact_status"], "created")
        mocked_add.assert_called_once()
        ns = mocked_add.call_args.args[0]
        self.assertEqual(ns.id, "elizabeth-jenswold")
        self.assertEqual(ns.name, "Elizabeth Jenswold")
        self.assertEqual(ns.company, "Bridgepoint Consulting")
        self.assertEqual(ns.signal_class, "LKI")
        self.assertFalse(ns.dry_run)

    def test_confirming_already_known_contact_does_not_recreate_it(self):
        """The is_known_contact case is already handled by the separate
        touch/tags apply=True path -- record_interaction() must not also
        try to (re-)create an existing contact."""
        self._seed_baseline([
            {"id": "elizabeth-jenswold", "name": "Elizabeth Jenswold",
             "current_company": "Bridgepoint Consulting", "email": None, "notes": ""},
        ])
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store,
        )
        iid = result["interactions"][0]["id"]

        with patch.object(mutations, "cmd_contact_add") as mocked_add:
            updated = relationship_intake.record_interaction(iid, confirmed=True, store_path=self.store)

        mocked_add.assert_not_called()
        self.assertNotIn("baseline_contact_status", updated)

    def test_cmd_contact_add_failure_does_not_block_interaction_confirmation(self):
        self._seed_baseline([])
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store,
        )
        iid = result["interactions"][0]["id"]

        with patch.object(mutations, "cmd_contact_add", side_effect=RuntimeError("baseline validation failed")):
            updated = relationship_intake.record_interaction(iid, confirmed=True, store_path=self.store)

        self.assertEqual(updated["persistence_status"], "RB recorded")
        self.assertEqual(updated["claim_status"], "confirmed")
        self.assertTrue(updated["baseline_contact_status"].startswith("failed:"))

    def test_cmd_contact_add_nonzero_return_reported_as_failed(self):
        self._seed_baseline([])
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store,
        )
        iid = result["interactions"][0]["id"]

        with patch.object(mutations, "cmd_contact_add", return_value=1):
            updated = relationship_intake.record_interaction(iid, confirmed=True, store_path=self.store)

        self.assertTrue(updated["baseline_contact_status"].startswith("failed:"))

    def test_reject_never_touches_baseline(self):
        self._seed_baseline([])
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store,
        )
        iid = result["interactions"][0]["id"]

        with patch.object(mutations, "cmd_contact_add") as mocked_add:
            relationship_intake.record_interaction(iid, confirmed=False, store_path=self.store)

        mocked_add.assert_not_called()

    def test_created_contact_persisted_in_ledger_entry(self):
        self._seed_baseline([])
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store,
        )
        iid = result["interactions"][0]["id"]

        with patch.object(mutations, "cmd_contact_add", return_value=0):
            relationship_intake.record_interaction(iid, confirmed=True, store_path=self.store)

        ledger = json.loads(self.store.read_text())
        entry = next(i for i in ledger["interactions"] if i["id"] == iid)
        self.assertEqual(entry["baseline_contact_status"], "created")


# ---------------------------------------------------------------------------
# R3b: apply=true (RB-DEFECT-2026-08-21 — API schema accepted apply but
# silently dropped it before it reached this function; the "settled fact,
# one-step" mode it advertised never actually existed).
# ---------------------------------------------------------------------------


class TestApplyParameter(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "interaction_ledger.json"
        self.baseline_path = Path(self.tmp) / "baseline_index.json"
        self._orig_baseline_path = relationship_intake.BASELINE_INDEX_PATH
        relationship_intake.BASELINE_INDEX_PATH = self.baseline_path

    def tearDown(self):
        relationship_intake.BASELINE_INDEX_PATH = self._orig_baseline_path

    def _seed_baseline(self, entries):
        self.baseline_path.write_text(json.dumps(entries), encoding="utf-8")

    def test_apply_true_with_no_baseline_match_stays_pending(self):
        """A brand-new, never-seen entity must never be auto-confirmed from
        freeform extracted text alone, regardless of apply -- same caution as
        identity_match_review.py's never-merge-on-name-alone rule."""
        self._seed_baseline([])
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, apply=True, store_path=self.store,
        )
        self.assertFalse(result["auto_confirmed"])
        self.assertEqual(result["persistence_status"], "pending confirmation")
        self.assertIn("apply_note", result)
        self.assertEqual(result["interactions"][0]["claim_status"], "proposed")

    def test_apply_true_with_confident_existing_match_auto_confirms(self):
        self._seed_baseline([
            {"id": "elizabeth-jenswold", "name": "Elizabeth Jenswold",
             "current_company": "Bridgepoint Consulting", "email": None, "notes": ""},
        ])
        with patch.object(mutations, "touch_contact") as mocked_touch, \
             patch.object(mutations, "cmd_contact_update") as mocked_update:
            result = relationship_intake.process_relationship_thread(
                JENSWOLD_FOLLOWUP, apply=True, store_path=self.store,
            )
        self.assertTrue(result["auto_confirmed"])
        self.assertEqual(result["persistence_status"], "RB recorded")
        self.assertEqual(result["interactions"][0]["claim_status"], "confirmed")
        self.assertNotIn("apply_note", result)

        # And it's really in the ledger, not just in the return value.
        ledger = json.loads(self.store.read_text())
        entry = ledger["interactions"][0]
        self.assertEqual(entry["claim_status"], "confirmed")
        self.assertEqual(entry["persistence_status"], "RB recorded")

    def test_apply_true_known_contact_touches_baseline(self):
        """RB-2026-08-24: confirming a known contact's interaction used to
        update only the interaction ledger, never baseline_index.json's
        last_touch -- staleness/decay tracking never reflected an apply:true
        confirmation. This is the fix: reuse the existing, already-tested
        touch_contact() primitive rather than a second baseline writer."""
        self._seed_baseline([
            {"id": "elizabeth-jenswold", "name": "Elizabeth Jenswold",
             "current_company": "Bridgepoint Consulting", "email": None, "notes": ""},
        ])
        with patch.object(mutations, "touch_contact") as mocked_touch, \
             patch.object(mutations, "cmd_contact_update") as mocked_update:
            result = relationship_intake.process_relationship_thread(
                JENSWOLD_FOLLOWUP, apply=True, store_path=self.store,
            )
        self.assertEqual(result["baseline_touch_status"], "applied")
        mocked_touch.assert_called_once()
        call_args = mocked_touch.call_args
        self.assertEqual(call_args.args[0], "elizabeth-jenswold")

    def test_apply_true_baseline_touch_failure_does_not_block_confirmation(self):
        """A touch_contact failure (e.g. a race where the contact vanished
        between the is_known_contact check and the write) must be surfaced,
        not silent, and must never undo the interaction confirmation that
        already succeeded."""
        self._seed_baseline([
            {"id": "elizabeth-jenswold", "name": "Elizabeth Jenswold",
             "current_company": "Bridgepoint Consulting", "email": None, "notes": ""},
        ])
        with patch.object(mutations, "touch_contact", side_effect=ValueError("no entry")), \
             patch.object(mutations, "cmd_contact_update") as mocked_update:
            result = relationship_intake.process_relationship_thread(
                JENSWOLD_FOLLOWUP, apply=True, store_path=self.store,
            )
        self.assertTrue(result["auto_confirmed"])
        self.assertEqual(result["persistence_status"], "RB recorded")
        self.assertTrue(result["baseline_touch_status"].startswith("failed:"))

    def test_apply_true_known_contact_applies_detected_tags(self):
        """RB-2026-08-24: extends the contact_upsert auto-apply beyond
        last_touch to the additive-only half of the proposal (tags) --
        current_company/current_role deliberately stay review-first,
        matching linkedin_ingest.py/contacts_ingest.py/hubspot_ingest.py's
        established never-auto-overwrite-company-or-role convention."""
        self._seed_baseline([
            {"id": "elizabeth-jenswold", "name": "Elizabeth Jenswold",
             "current_company": "Bridgepoint Consulting", "email": None, "notes": ""},
        ])
        with patch.object(mutations, "touch_contact"), \
             patch.object(mutations, "cmd_contact_update", return_value=0) as mocked_update:
            result = relationship_intake.process_relationship_thread(
                JENSWOLD_FOLLOWUP, apply=True, store_path=self.store,
            )
        self.assertEqual(result["baseline_tags_status"], "applied")
        mocked_update.assert_called_once()
        ns = mocked_update.call_args.args[0]
        self.assertEqual(ns.id, "elizabeth-jenswold")
        self.assertEqual(ns.tags_add, ["hr_consulting", "executive_network"])
        # The whole point: company/role must never be touched by this path.
        self.assertIsNone(ns.company)
        self.assertIsNone(ns.role)

    def test_apply_true_no_detected_tags_skips_tag_update_entirely(self):
        self._seed_baseline([
            {"id": "elizabeth-jenswold", "name": "Elizabeth Jenswold",
             "current_company": "Bridgepoint Consulting", "email": None, "notes": ""},
        ])
        with patch.object(mutations, "touch_contact"), \
             patch.object(mutations, "cmd_contact_update") as mocked_update:
            result = relationship_intake.process_relationship_thread(
                "Hi Todd, just checking in.", entity_name="Elizabeth Jenswold",
                apply=True, store_path=self.store,
            )
        mocked_update.assert_not_called()
        self.assertIsNone(result["baseline_tags_status"])

    def test_apply_true_tags_update_failure_does_not_block_confirmation(self):
        self._seed_baseline([
            {"id": "elizabeth-jenswold", "name": "Elizabeth Jenswold",
             "current_company": "Bridgepoint Consulting", "email": None, "notes": ""},
        ])
        with patch.object(mutations, "touch_contact"), \
             patch.object(mutations, "cmd_contact_update", side_effect=ValueError("boom")):
            result = relationship_intake.process_relationship_thread(
                JENSWOLD_FOLLOWUP, apply=True, store_path=self.store,
            )
        self.assertTrue(result["auto_confirmed"])
        self.assertEqual(result["persistence_status"], "RB recorded")
        self.assertTrue(result["baseline_tags_status"].startswith("failed:"))

    def test_apply_false_default_baseline_touch_status_is_none(self):
        self._seed_baseline([
            {"id": "elizabeth-jenswold", "name": "Elizabeth Jenswold",
             "current_company": "Bridgepoint Consulting", "email": None, "notes": ""},
        ])
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store,
        )
        self.assertIsNone(result["baseline_touch_status"])

    def test_apply_false_default_never_auto_confirms_even_with_match(self):
        self._seed_baseline([
            {"id": "elizabeth-jenswold", "name": "Elizabeth Jenswold",
             "current_company": "Bridgepoint Consulting", "email": None, "notes": ""},
        ])
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store,
        )
        self.assertFalse(result["auto_confirmed"])
        self.assertEqual(result["persistence_status"], "pending confirmation")


# ---------------------------------------------------------------------------
# R4: Who Matters Now and query_interactions
# ---------------------------------------------------------------------------


class TestWhoMattersNow(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "interaction_ledger.json"

    def test_R4a_jenswold_appears_in_wmn_after_process(self):
        relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store
        )
        wmn = relationship_intake.query_who_matters_now(store_path=self.store)
        contact_ids = [c["contact_id"] for c in wmn]
        self.assertIn("elizabeth-jenswold", contact_ids)

    def test_R4b_wmn_sorted_by_score_descending(self):
        # Add Jenswold (high score) and a low-signal contact
        relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store
        )
        relationship_intake.process_relationship_thread(
            GENERIC_WITH_ENTITY,
            entity_name="Plain Contact",
            entity_role=None,
            store_path=self.store,
        )
        wmn = relationship_intake.query_who_matters_now(store_path=self.store)
        scores = [c["score"] for c in wmn]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_R4c_jenswold_score_reflects_exec_weight(self):
        relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store
        )
        wmn = relationship_intake.query_who_matters_now(store_path=self.store)
        jenswold = next(c for c in wmn if c["contact_id"] == "elizabeth-jenswold")
        # trust_delta=2 + exec_weight=2 + recency=3 = 7 minimum
        self.assertGreaterEqual(jenswold["score"], 7)

    def test_R4d_rejected_interaction_excluded_from_wmn(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store
        )
        iid = result["interactions"][0]["id"]
        relationship_intake.record_interaction(iid, confirmed=False, store_path=self.store)
        wmn = relationship_intake.query_who_matters_now(store_path=self.store)
        contact_ids = [c["contact_id"] for c in wmn]
        self.assertNotIn("elizabeth-jenswold", contact_ids)

    def test_R4e_query_interactions_by_contact_id(self):
        relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store
        )
        results = relationship_intake.query_interactions(
            contact_id="elizabeth-jenswold", store_path=self.store
        )
        self.assertGreaterEqual(len(results), 1)
        for r in results:
            self.assertEqual(r["contact_id"], "elizabeth-jenswold")

    def test_R4f_query_interactions_by_signal_type(self):
        relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store
        )
        results = relationship_intake.query_interactions(
            signal_type="unsolicited_positive_followup", store_path=self.store
        )
        self.assertGreaterEqual(len(results), 1)
        for r in results:
            self.assertEqual(r["signal_type"], "unsolicited_positive_followup")

    def test_R4g_query_interactions_by_claim_status(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP, store_path=self.store
        )
        iid = result["interactions"][0]["id"]
        with patch.object(mutations, "cmd_contact_add", return_value=0):
            relationship_intake.record_interaction(iid, confirmed=True, store_path=self.store)
        confirmed = relationship_intake.query_interactions(
            claim_status="confirmed", store_path=self.store
        )
        self.assertGreaterEqual(len(confirmed), 1)
        for r in confirmed:
            self.assertEqual(r["claim_status"], "confirmed")

    def test_R4h_wmn_includes_ecosystem_tags(self):
        relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP,
            ecosystem_tags=["hospitality_table"],
            store_path=self.store,
        )
        wmn = relationship_intake.query_who_matters_now(store_path=self.store)
        jenswold = next(c for c in wmn if c["contact_id"] == "elizabeth-jenswold")
        self.assertIn("hospitality_table", jenswold["ecosystem_tags"])


# ---------------------------------------------------------------------------
# R5: Entity resolution
# ---------------------------------------------------------------------------


class TestEntityResolution(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "interaction_ledger.json"

    def test_R5a_explicit_name_overrides_extraction(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP,
            entity_name="Override Name",
            store_path=self.store,
        )
        self.assertEqual(result["interactions"][0]["entity"]["name"], "Override Name")

    def test_R5b_explicit_org_overrides_extraction(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP,
            entity_name="Elizabeth Jenswold",
            entity_org="Override Org",
            store_path=self.store,
        )
        self.assertEqual(result["interactions"][0]["entity"]["org"], "Override Org")

    def test_R5c_explicit_role_overrides_extraction(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP,
            entity_name="Elizabeth Jenswold",
            entity_role="CEO",
            store_path=self.store,
        )
        self.assertEqual(result["interactions"][0]["entity"]["role"], "CEO")

    def test_R5d_explicit_ecosystem_tags_merged_with_detected(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP,
            ecosystem_tags=["hospitality_table"],
            store_path=self.store,
        )
        tags = result["interactions"][0]["ecosystem_tags"]
        self.assertIn("hospitality_table", tags)
        # Should also have auto-detected tags (hr_consulting, executive_network)
        self.assertGreater(len(tags), 1)

    def test_R5e_invalid_source_type_defaults_to_email(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP,
            source_type="carrier_pigeon",
            store_path=self.store,
        )
        self.assertEqual(result["interactions"][0]["source_type"], "email")

    def test_R5f_explicit_interaction_date_used(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP,
            interaction_date="2026-01-15",
            store_path=self.store,
        )
        self.assertEqual(result["interactions"][0]["interaction_date"], "2026-01-15")

    def test_R5g_referral_signal_has_highest_trust_delta(self):
        result = relationship_intake.process_relationship_thread(
            REFERRAL_TEXT,
            entity_name="Maria Santos",
            entity_role="Managing Director",
            entity_org="Acme Corp",
            store_path=self.store,
        )
        # referral_sent → trust_delta 3
        self.assertEqual(result["interactions"][0]["trust_delta"], 3)


# ---------------------------------------------------------------------------
# R6: Strategic Ecosystem Connector classification
# ---------------------------------------------------------------------------


class TestStrategicClassification(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "interaction_ledger.json"

    def test_R6a_president_with_two_ecosystem_tags_is_strategic_connector(self):
        # Jenswold with explicit hospitality_table → exec_weight=2, tags>=2
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP,
            ecosystem_tags=["hospitality_table"],
            store_path=self.store,
        )
        classification = result["interactions"][0]["strategic_classification"]
        self.assertEqual(classification, "Strategic Ecosystem Connector")

    def test_R6b_cos_surface_strategic_classification_matches_interaction(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP,
            ecosystem_tags=["hospitality_table"],
            store_path=self.store,
        )
        self.assertEqual(
            result["cos_surface"]["strategic_classification"],
            result["interactions"][0]["strategic_classification"],
        )

    def test_R6c_recommended_posture_present_for_strategic_connector(self):
        result = relationship_intake.process_relationship_thread(
            JENSWOLD_FOLLOWUP,
            ecosystem_tags=["hospitality_table"],
            store_path=self.store,
        )
        posture = result["interactions"][0]["recommended_posture"]
        self.assertIn("trust", posture.lower())


# ---------------------------------------------------------------------------
# R7: source_type preservation + call-note classification
# (RB-DEFECT-2026-09-18, handoff acceptance test 15: "source_type='call_note'
# remains a call through persistence and downstream reporting." Root cause:
# SOURCE_TYPES enforced a narrower vocabulary than the API's own documented
# contract for this field, so source_type="call_note" silently fell back to
# "email" with no error. Also covers the companion classification gap this
# incident exposed: a third-person call recap ("Phone call with X... During
# the call X said...") matched none of _SIGNAL_INDICATORS["meeting_completed"]
# and cascaded to signal_type=unknown -> cold -> Network Contact.)
# ---------------------------------------------------------------------------

SAL_NAZIR_CALL_TEXT = (
    "Phone call with Sal Nazir on 2026-09-18. Sal can be reached at 416-457-7269. "
    "Sal is the former General Manager of Payments at PAR Technology and has been "
    "away from PAR since May 2026. During the call Sal said DoorDash may be looking "
    "to acquire PAR Technology; treat as credible but uncorroborated human-source "
    "intelligence."
)


class TestR7_SourceTypePreservation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Path(self.tmp) / "interaction_ledger.json"

    def test_R7a_call_note_source_type_preserved_not_converted_to_email(self):
        result = relationship_intake.process_relationship_thread(
            SAL_NAZIR_CALL_TEXT, entity_name="Sal Nazir", entity_org="Formerly PAR Technology",
            source_type="call_note", store_path=self.store,
        )
        self.assertEqual(result["interactions"][0]["source_type"], "call_note")

    def test_R7b_other_documented_source_types_also_preserved(self):
        for source_type in ("transcript", "sms", "meeting_note", "recruiter_email"):
            result = relationship_intake.process_relationship_thread(
                SAL_NAZIR_CALL_TEXT, entity_name="Sal Nazir", source_type=source_type,
                store_path=self.store,
            )
            self.assertEqual(result["interactions"][0]["source_type"], source_type)

    def test_R7c_unrecognized_source_type_still_falls_back_to_email(self):
        """The fallback itself is correct behavior for a genuinely unknown
        value -- only the documented-but-unsupported vocabulary was the bug."""
        result = relationship_intake.process_relationship_thread(
            SAL_NAZIR_CALL_TEXT, entity_name="Sal Nazir", source_type="carrier_pigeon",
            store_path=self.store,
        )
        self.assertEqual(result["interactions"][0]["source_type"], "email")

    def test_R7d_third_person_call_recap_classifies_as_meeting_completed_not_unknown(self):
        result = relationship_intake.process_relationship_thread(
            SAL_NAZIR_CALL_TEXT, entity_name="Sal Nazir", entity_org="Formerly PAR Technology",
            source_type="call_note", store_path=self.store,
        )
        interaction = result["interactions"][0]
        self.assertEqual(interaction["signal_type"], "meeting_completed")
        self.assertNotEqual(interaction["relationship_state_proposed"], "cold")
        self.assertNotEqual(interaction["strategic_classification"], "Network Contact")


if __name__ == "__main__":
    unittest.main()
