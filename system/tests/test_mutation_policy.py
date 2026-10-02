"""
test_mutation_policy.py — unit tests for the shared write-decision policy
(system/scripts/mutation_policy.py), built for the 2026-09-18 intelligence-
cycle repair (system/CLAUDE_HANDOFF_INTELLIGENCE_CYCLE_REPAIR_2026-09-18.md).

Covers acceptance tests 1, 2, 3, 4, 5, 6, 11 from that handoff (numbering
below matches the handoff's "Acceptance tests" section):

  MP1 (handoff #1/#2) — a new dated fact automatically updates the tracked
       value and preserves provenance (net-new + dated successor).
  MP2 (handoff #2) — a later dated interaction automatically succeeds an
       older one without confirmation.
  MP3 (handoff #3) — an older historical fact is appended without
       regressing current state.
  MP4 (handoff #4) — a proposed replacement of an existing scalar requires
       confirmation.
  MP5 (handoff #5) — two conflicting undated values require confirmation.
  MP6 (handoff #6) — ambiguous identity does not mutate either candidate.
  MP7 — identical fact deduplicates without interrupting the operator.
  MP8 — low-confidence proposals are rejected, distinct from identity
       ambiguity.
  MP9 — set/list membership additions are always net-new regardless of
       existing scalar state.
  MP10 (handoff #11) — receipts reconcile: every decision produces exactly
       one receipt, and summarize_receipts()'s counts match what was
       decided.
  MP11 — "pending intelligence"/generic pending status never appears in the
       vocabulary this module hands back.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import mutation_policy as mp  # noqa: E402


class TestNetNewAndDatedSuccessor(unittest.TestCase):
    def test_mp1_missing_scalar_field_auto_fills(self):
        decision = mp.decide(
            source="linkedin_messaging",
            new_value="2026-09-16",
            existing_value=None,
            new_date="2026-09-16",
            existing_date=None,
            field_name="last_touch",
            entity_id="ryan-hildebrand",
        )
        self.assertEqual(decision.status, mp.AUTO_ADDED_NET_NEW)
        self.assertTrue(decision.auto_apply)
        self.assertFalse(decision.requires_user_action)
        self.assertEqual(decision.receipt["old_value"], None)
        self.assertEqual(decision.receipt["new_value"], "2026-09-16")
        self.assertEqual(decision.receipt["source"], "linkedin_messaging")

    def test_mp2_newer_dated_fact_auto_succeeds_older_without_confirmation(self):
        decision = mp.decide(
            source="linkedin_messaging",
            new_value="2026-09-16",
            existing_value="2026-08-30",
            new_date="2026-09-16",
            existing_date="2026-08-30",
            field_name="last_touch",
            entity_id="ryan-hildebrand",
        )
        self.assertEqual(decision.status, mp.AUTO_ADDED_DATED_SUCCESSOR)
        self.assertTrue(decision.auto_apply)
        self.assertFalse(decision.requires_user_action)

    def test_mp9_set_member_addition_is_always_net_new(self):
        # A new inbound-contact interaction event doesn't overwrite anything
        # -- it's a new member of the interaction history -- so it's net-new
        # even though the contact already has plenty of recorded history.
        decision = mp.decide(
            source="linkedin_messaging",
            new_value="2026-09-16",
            existing_value="2026-08-30",  # unrelated existing scalar; ignored for set members
            is_set_member=True,
            field_name="inbound_contact",
        )
        self.assertEqual(decision.status, mp.AUTO_ADDED_NET_NEW)
        self.assertTrue(decision.auto_apply)


class TestHistoricalAndConflicts(unittest.TestCase):
    def test_mp3_older_dated_fact_appends_without_regressing_current_state(self):
        decision = mp.decide(
            source="email",
            new_value="2026-07-01",
            existing_value="2026-08-30",
            new_date="2026-07-01",
            existing_date="2026-08-30",
            field_name="last_touch",
        )
        self.assertEqual(decision.status, mp.AUTO_ADDED_HISTORICAL_FACT)
        self.assertTrue(decision.auto_apply)
        self.assertFalse(decision.requires_user_action)
        # The receipt must carry both values so a caller can verify current
        # state was NOT overwritten by the older fact.
        self.assertEqual(decision.receipt["old_value"], "2026-08-30")
        self.assertEqual(decision.receipt["new_value"], "2026-07-01")

    def test_mp4_scalar_replacement_requires_confirmation(self):
        decision = mp.decide(
            source="manual_relationship_intake",
            new_value="VP Sales",
            existing_value="Director",
            is_replacement=True,
            field_name="role",
        )
        self.assertEqual(decision.status, mp.CONFIRMATION_REQUIRED_OVERWRITE)
        self.assertFalse(decision.auto_apply)
        self.assertTrue(decision.requires_user_action)

    def test_mp5_undated_conflict_requires_confirmation(self):
        decision = mp.decide(
            source="email",
            new_value="Acme Foods",
            existing_value="Acme Corp",
            field_name="company",
        )
        self.assertEqual(decision.status, mp.CONFIRMATION_REQUIRED_UNDATED_CONFLICT)
        self.assertFalse(decision.auto_apply)
        self.assertTrue(decision.requires_user_action)

    def test_same_date_different_value_requires_confirmation(self):
        decision = mp.decide(
            source="calendar",
            new_value="2026-09-16",
            existing_value="2026-09-16-different-event",
            new_date="2026-09-16",
            existing_date="2026-09-16",
        )
        self.assertEqual(decision.status, mp.CONFIRMATION_REQUIRED_UNDATED_CONFLICT)
        self.assertFalse(decision.auto_apply)


class TestIdentityAndConfidence(unittest.TestCase):
    def test_mp6_ambiguous_identity_never_mutates(self):
        decision = mp.decide(
            source="passive_ri_ingest",
            new_value="2026-09-16",
            existing_value=None,
            identity_ambiguous=True,
            identity_reason="no matched baseline contact",
        )
        self.assertEqual(decision.status, mp.REVIEW_REQUIRED_IDENTITY_AMBIGUITY)
        self.assertFalse(decision.auto_apply)
        self.assertTrue(decision.requires_user_action)

    def test_identity_ambiguity_wins_over_net_new(self):
        # Even when every other signal says "net-new, just write it," an
        # ambiguous identity match must still block the write (policy rule 5
        # takes precedence over rule 1).
        decision = mp.decide(
            source="passive_ri_ingest",
            new_value="2026-09-16",
            existing_value=None,
            is_set_member=True,
            identity_ambiguous=True,
        )
        self.assertEqual(decision.status, mp.REVIEW_REQUIRED_IDENTITY_AMBIGUITY)
        self.assertFalse(decision.auto_apply)

    def test_mp8_low_confidence_rejected_distinct_from_identity_ambiguity(self):
        decision = mp.decide(
            source="passive_ri_ingest",
            new_value="2026-09-16",
            existing_value=None,
            low_confidence=True,
            low_confidence_reason="event_at_confidence=medium",
        )
        self.assertEqual(decision.status, mp.REJECTED_LOW_CONFIDENCE)
        self.assertFalse(decision.auto_apply)
        self.assertFalse(decision.requires_user_action)

    def test_mp7_identical_fact_deduplicates_without_interruption(self):
        decision = mp.decide(
            source="email",
            new_value="2026-09-16",
            existing_value="2026-09-16",
        )
        self.assertEqual(decision.status, mp.REJECTED_DUPLICATE)
        self.assertFalse(decision.auto_apply)
        self.assertFalse(decision.requires_user_action)

    def test_identical_fact_case_insensitive(self):
        decision = mp.decide(
            source="email",
            new_value="Acme Corp",
            existing_value="acme corp",
        )
        self.assertEqual(decision.status, mp.REJECTED_DUPLICATE)


class TestVocabulary(unittest.TestCase):
    def test_mp11_no_generic_pending_status_in_vocabulary(self):
        for status in mp.ALL_STATUSES:
            self.assertNotIn(status, mp.GENERIC_PENDING_ALIASES_FORBIDDEN)

    def test_every_decision_status_is_valid(self):
        cases = [
            dict(source="s", new_value="a", existing_value=None),
            dict(source="s", new_value="a", existing_value="b", new_date="2026-01-01", existing_date="2026-01-02"),
            dict(source="s", new_value="a", existing_value="b", new_date="2026-01-02", existing_date="2026-01-01"),
            dict(source="s", new_value="a", existing_value="b", is_replacement=True),
            dict(source="s", new_value="a", existing_value="b"),
            dict(source="s", new_value="a", identity_ambiguous=True),
            dict(source="s", new_value="a", low_confidence=True),
            dict(source="s", new_value="a", existing_value="a"),
        ]
        for kwargs in cases:
            decision = mp.decide(**kwargs)
            self.assertTrue(decision.is_valid(), decision.status)


class TestReceiptsReconcile(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="mutation_policy_test_"))
        self.receipts_path = self.tmp / "receipts.jsonl"

    def test_mp10_receipts_reconcile_with_decisions(self):
        decisions = [
            mp.decide(source="s", new_value="a", existing_value=None),  # net-new
            mp.decide(source="s", new_value="a", existing_value="b",
                      new_date="2026-01-02", existing_date="2026-01-01"),  # dated successor
            mp.decide(source="s", new_value="a", existing_value="b", is_replacement=True),  # confirm
            mp.decide(source="s", new_value="a", existing_value="a"),  # duplicate
            mp.decide(source="s", new_value="a", identity_ambiguous=True),  # review
        ]
        applied_flags = [True, True, False, False, False]
        for decision, applied in zip(decisions, applied_flags):
            mp.record_receipt(
                decision,
                artifact="baseline_index.json" if applied else None,
                applied=applied,
                path=self.receipts_path,
            )

        receipts = mp.load_receipts(path=self.receipts_path)
        self.assertEqual(len(receipts), len(decisions))

        summary = mp.summarize_receipts(receipts)
        self.assertEqual(summary["mutations_generated"], 5)
        self.assertEqual(summary["automatically_applied"], 2)
        self.assertEqual(summary["records_changed"], 2)
        self.assertEqual(summary["confirmation_required"], 1)
        self.assertEqual(summary["review_required"], 1)
        self.assertEqual(summary["rejected"], 1)

    def test_receipt_without_write_still_records_reason(self):
        decision = mp.decide(source="s", new_value="a", existing_value="b", is_replacement=True)
        record = mp.record_receipt(decision, artifact=None, applied=False, path=self.receipts_path)
        self.assertFalse(record["applied"])
        self.assertIsNone(record["artifact"])
        self.assertTrue(record["reason"])


if __name__ == "__main__":
    unittest.main()
