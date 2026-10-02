"""
test_manual_relationship_intake_contact_enrichment.py

RB-DEFECT-2026-09-18 (system/CLAUDE_HANDOFF_INTELLIGENCE_CYCLE_REPAIR_2026-09-18.md,
"Added defect: relationship intake misroutes contact enrichment and
intelligence calls"). Observed 2026-09-18 recording a phone call with Sal
Nazir: manualRelationshipIntake(apply=true) did not project a caller-supplied
phone number into baseline_index.json, invented a "warm recruiting
re-engagement" narrative from a single bare operator_domain_overlap signal
(no ask/commitment/next-step present), and auto-created an opportunity
thread + monitoring loop for it. Before this fix, manual_relationship_
intake.py had ZERO test coverage in this suite at all -- confirmed by
grepping every test file for an import of the module.

Covers acceptance tests 13, 14, 16, 17, 18 from the handoff (13 pure-decision
tests plus one full apply=True integration test proving the durable write,
matching the rigor the rest of this repair's test suite already uses):

  13. An explicit phone number fills an empty canonical phone field
      automatically and appears in the durable contact record.
  14. A different phone number does not overwrite an existing phone without
      confirmation.
  16. A relationship call containing strategic intelligence but no ask/
      commitment does not create an opportunity, thread, loop, or recruiter
      narrative.
  17. A real grounded commitment in a call can create a loop, with the exact
      commitment and due date preserved.
  18. Dated former-employer information preserves employment history
      without treating the former employer as the contact's current company.

(Test 15 -- source_type='call_note' preserved through persistence -- lives in
test_relationship_intake.py::TestR7_SourceTypePreservation, since that's
relationship_intake.py's defect, not manual_relationship_intake.py's.)
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import manual_relationship_intake as mri  # noqa: E402
import mutations  # noqa: E402
import mutation_policy as mp  # noqa: E402
import rb_core as core  # noqa: E402


SAL_NAZIR_CALL_TEXT = (
    "Phone call with Sal Nazir on 2026-09-18. Sal can be reached at 416-457-7269. "
    "Sal is the former General Manager of Payments at PAR Technology and has been "
    "away from PAR since May 2026. During the call Sal said DoorDash may be looking "
    "to acquire PAR Technology; treat as credible but uncorroborated human-source "
    "intelligence."
)


def _sal_baseline(**overrides) -> list[dict]:
    entry = {
        "id": "sal-nazir", "name": "Sal Nazir",
        "current_company": "PAR Technology", "current_role": "General Manager, Payments",
        "email": None, "phone": None, "signal_class": "VC",
        "last_touch": None, "tags": [], "notes": "", "sources": [],
    }
    entry.update(overrides)
    return [entry]


class _IsolatedBaselineMixin:
    """Full apply=True integration harness: isolated baseline_index.json,
    mutation_policy receipts, and mutations.py's real snapshot/validate
    machinery stubbed out (already covered by mutations.py's own tests --
    these tests exercise manual_relationship_intake.py's decision logic and
    that the write actually lands, not the snapshot/validator itself)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mri_contact_enrichment_test_"))
        self.baseline_path = self.tmp / "baseline_index.json"
        self._orig_receipts_path = mp.RECEIPTS_PATH
        mp.RECEIPTS_PATH = self.tmp / "receipts.jsonl"
        # RB note: mri.core IS rb_core (same module object as `core` here --
        # `import rb_core as core` in both files binds the same sys.modules
        # entry), so patching core.load_baseline already covers mri.core.
        # load_baseline too. A prior version of this fixture patched both
        # separately -- redundant patches on the SAME (object, attribute)
        # pair, stopped in forward (not reverse) order, corrupt the final
        # restored value: the second patcher's "saved original" is actually
        # the first patcher's fake, so stopping second re-applies that fake
        # permanently. Confirmed live: this leaked core.load_baseline as a
        # bound method into every OTHER test file for the rest of the
        # process, breaking system/tests/test_relationship_reactivation_scan.py
        # (and, transitively, any other test importing rb_core fresh) when
        # run after this file in the same pytest session -- not reproducible
        # running either file alone. One patch per attribute, always.
        self._patches = [
            patch.object(core, "BASELINE_PATH", self.baseline_path),
            patch.object(core, "load_baseline", self._load_baseline),
            patch.object(mutations, "snapshot", lambda *a, **k: "snap"),
            patch.object(mutations, "_validate_baseline_or_rollback", lambda *a, **k: 0),
            patch.object(mri, "_write_interaction_brief", lambda path, **kw: {"status": "written"}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        mp.RECEIPTS_PATH = self._orig_receipts_path

    def _load_baseline(self, path=None):
        return json.loads(self.baseline_path.read_text())

    def _write_baseline(self, entries: list[dict]) -> None:
        self.baseline_path.write_text(json.dumps(entries))

    def _current_sal(self) -> dict:
        return next(e for e in json.loads(self.baseline_path.read_text()) if e["id"] == "sal-nazir")


class Test13_PhoneAutoFill(_IsolatedBaselineMixin, unittest.TestCase):
    def test_explicit_phone_fills_empty_canonical_field(self):
        self._write_baseline(_sal_baseline())
        report = mri.assess(
            text=SAL_NAZIR_CALL_TEXT, contact_id="sal-nazir", name="Sal Nazir",
            event_at="2026-09-18", captured_at="2026-09-18T16:30:00+00:00",
            organization="Formerly PAR Technology", apply=True,
        )
        self.assertEqual(report["persistence"]["status"], "persisted")
        applied_ops = {a["operation"] for a in report["persistence"]["applied"]}
        self.assertIn("updateContact", applied_ops)
        self.assertEqual(self._current_sal()["phone"], "416-457-7269")

        receipts = mp.load_receipts()
        phone_receipt = next(r for r in receipts if r["field_name"] == "phone")
        self.assertEqual(phone_receipt["decision_class"], mp.AUTO_ADDED_NET_NEW)
        self.assertTrue(phone_receipt["applied"])

    def test_review_first_proposal_marks_phone_auto_applicable(self):
        """Even without apply=True, the review-first proposal correctly
        classifies the phone write as auto-applicable (safe_to_write)."""
        self._write_baseline(_sal_baseline())
        report = mri.assess(
            text=SAL_NAZIR_CALL_TEXT, contact_id="sal-nazir", name="Sal Nazir",
            event_at="2026-09-18", captured_at="2026-09-18T16:30:00+00:00",
            organization="Formerly PAR Technology", apply=False,
        )
        phone_props = [m for m in report["proposed_mutations"]
                       if m["operation"] == "updateContact" and m["body"].get("phone")]
        self.assertEqual(len(phone_props), 1)
        self.assertTrue(phone_props[0]["safe_to_write"])


class Test14_PhoneOverwriteRequiresConfirmation(_IsolatedBaselineMixin, unittest.TestCase):
    def test_different_existing_phone_not_overwritten_without_confirmation(self):
        self._write_baseline(_sal_baseline(phone="647-000-0000"))
        report = mri.assess(
            text=SAL_NAZIR_CALL_TEXT, contact_id="sal-nazir", name="Sal Nazir",
            event_at="2026-09-18", captured_at="2026-09-18T16:30:00+00:00",
            organization="Formerly PAR Technology", apply=True,
        )
        # The auto-apply pass must not have touched phone at all.
        self.assertEqual(self._current_sal()["phone"], "647-000-0000")
        skipped_reasons = [s["operation"] for s in report["persistence"]["skipped"]]
        # Not-safe-to-write proposals are filtered before apply_mutations()
        # even runs -- confirm the proposal itself was correctly marked
        # confirmation-required, not silently dropped.
        phone_props = [m for m in report["proposed_mutations"]
                       if m["operation"] == "updateContact" and m["body"].get("phone")]
        self.assertEqual(len(phone_props), 1)
        self.assertFalse(phone_props[0]["safe_to_write"])
        self.assertEqual(phone_props[0]["decision_class"], mp.CONFIRMATION_REQUIRED_OVERWRITE)

    def test_identical_phone_deduplicates_no_proposal_at_all(self):
        self._write_baseline(_sal_baseline(phone="416-457-7269"))
        report = mri.assess(
            text=SAL_NAZIR_CALL_TEXT, contact_id="sal-nazir", name="Sal Nazir",
            event_at="2026-09-18", captured_at="2026-09-18T16:30:00+00:00",
            organization="Formerly PAR Technology", apply=False,
        )
        phone_props = [m for m in report["proposed_mutations"]
                       if m["operation"] == "updateContact" and "phone" in m["body"]]
        self.assertEqual(phone_props, [])


class Test16_NoGroundedAskNoOpportunity(_IsolatedBaselineMixin, unittest.TestCase):
    def test_operator_domain_overlap_alone_creates_no_opportunity_thread_or_loop(self):
        self._write_baseline(_sal_baseline())
        report = mri.assess(
            text=SAL_NAZIR_CALL_TEXT, contact_id="sal-nazir", name="Sal Nazir",
            event_at="2026-09-18", captured_at="2026-09-18T16:30:00+00:00",
            organization="Formerly PAR Technology", apply=False,
        )
        self.assertIsNone(report["opportunity_state"])
        ops = {m["operation"] for m in report["proposed_mutations"]}
        self.assertNotIn("openThread", ops)
        self.assertNotIn("loopAdd", ops)
        # The two ungrounded tags must not be proposed either.
        tag_props = [m for m in report["proposed_mutations"] if m["operation"] == "card_or_baseline_tag_update"]
        for t in tag_props:
            self.assertNotIn("franchise_governance_signal", t["body"]["tags"])
            self.assertNotIn("restaurant_operator_overlap", t["body"]["tags"])
        # next_expected_action must never be checked here since opp_state is
        # None -- confirming no recruiter narrative was invented at all.

    def test_bare_opportunity_signal_company_mention_alone_creates_no_opportunity(self):
        text = "Ran into Unisys at the conference and we talked shop for a bit."
        self.assertIsNone(mri._opportunity_state(mri._detect_signals(text), company="Unisys", opportunity=None))

    def test_operator_domain_overlap_with_reciprocal_peer_signal_still_qualifies(self):
        """A weak signal paired with a genuinely grounded one still counts --
        the fix only blocks the WEAK-ONLY case, confirmed via the existing
        strategic_peer_exploration path staying reachable."""
        text = (
            "Great chat about the restaurant operator space -- mutual respect, "
            "similar experience, happy to help each other out."
        )
        signals = mri._detect_signals(text)
        types = {s["type"] for s in signals}
        self.assertIn("operator_domain_overlap", types)
        self.assertIn("reciprocal_peer_signal", types)
        opp_state = mri._opportunity_state(signals, company=None, opportunity=None)
        self.assertIsNotNone(opp_state)
        self.assertEqual(opp_state["stage"], "strategic_peer_exploration")


class Test17_GroundedCommitmentCreatesLoop(_IsolatedBaselineMixin, unittest.TestCase):
    def test_grounded_followup_commitment_creates_loop_with_evidence_and_due_date(self):
        text = (
            "Caught up with Dana Whitfield today, a light relationship check-in -- "
            "I'll send her the conference deck next week."
        )
        self._write_baseline([{
            "id": "dana-whitfield", "name": "Dana Whitfield", "current_company": "Acme",
            "current_role": "VP", "email": None, "phone": None, "signal_class": "VC",
            "last_touch": None, "tags": [], "notes": "", "sources": [],
        }])
        report = mri.assess(
            text=text, contact_id="dana-whitfield", name="Dana Whitfield",
            event_at="2026-09-18", captured_at="2026-09-18T16:30:00+00:00",
            apply=False,
        )
        # No opportunity narrative -- this is a pure relationship-governance
        # follow-up, not a job/recruiting/business opportunity.
        loop_props = [m for m in report["proposed_mutations"] if m["operation"] == "loopAdd"]
        self.assertEqual(len(loop_props), 1, report["proposed_mutations"])
        loop = loop_props[0]
        self.assertTrue(loop["safe_to_write"])
        # The exact commitment phrase is preserved, not paraphrased away.
        self.assertIn("i'll send", loop["body"]["description"].lower())
        self.assertEqual(loop["body"]["target"], "2026-09-20")  # event_at + 2 days

    def test_followup_commitment_creates_loop_even_without_opportunity_state(self):
        """The exact bug this test closes: previously this loop was nested
        inside `if opp_state:`, so a call with ONLY a follow-up commitment
        (no recruiting/communication-risk/internal-circulation signal
        alongside it) never reached the loop-creation code at all."""
        text = "I'll send you the notes next week."
        signals = mri._detect_signals(text)
        types = {s["type"] for s in signals}
        self.assertIn("followup_loop_candidate", types)
        self.assertIsNone(mri._opportunity_state(signals, company=None, opportunity=None))
        proposed = mri._proposed_mutations(
            {"id": "x", "name": "X", "last_touch": None}, signals,
            event_at=__import__("datetime").date(2026, 9, 18),
            captured_at=__import__("datetime").date(2026, 9, 18),
            name="X", company=None, opportunity=None,
        )
        self.assertTrue(any(m["operation"] == "loopAdd" for m in proposed))


class Test18_DatedFormerEmployerPreservesHistory(_IsolatedBaselineMixin, unittest.TestCase):
    def test_dated_departure_clears_current_company_preserves_history(self):
        self._write_baseline(_sal_baseline())
        report = mri.assess(
            text=SAL_NAZIR_CALL_TEXT, contact_id="sal-nazir", name="Sal Nazir",
            event_at="2026-09-18", captured_at="2026-09-18T16:30:00+00:00",
            organization="Formerly PAR Technology", apply=True,
        )
        self.assertEqual(report["persistence"]["status"], "persisted")
        sal = self._current_sal()
        # Not treated as current employment any more...
        self.assertIsNone(sal["current_company"])
        self.assertIsNone(sal["current_role"])
        # ...but the fact itself is preserved, not erased.
        self.assertEqual(sal["last_known_company"], "PAR Technology")
        self.assertEqual(sal["last_known_role"], "General Manager, Payments")
        self.assertEqual(sal["employment_status"], "no_stated_current_role")
        self.assertEqual(sal["employment_status_source"], "operator_confirmed")
        self.assertEqual(sal["employment_end_date"], "2026-05-01")
        self.assertEqual(sal["employment_date_confidence"], "operator_confirmed")

    def test_former_employer_statement_about_unrelated_company_is_a_no_op(self):
        """A 'formerly at Y' statement about some OTHER employer than the
        one on file must not blank out an unrelated current role."""
        self._write_baseline(_sal_baseline(current_company="Some Other Company"))
        report = mri.assess(
            text=SAL_NAZIR_CALL_TEXT, contact_id="sal-nazir", name="Sal Nazir",
            event_at="2026-09-18", captured_at="2026-09-18T16:30:00+00:00",
            organization="Formerly PAR Technology", apply=True,
        )
        sal = self._current_sal()
        self.assertEqual(sal["current_company"], "Some Other Company")
        applied_ops = {a["operation"] for a in report["persistence"]["applied"]}
        self.assertNotIn("updateEmploymentState", applied_ops)

    def test_undated_former_employer_still_clears_current_without_end_date(self):
        """A 'formerly at X' statement with no parseable departure date still
        correctly stops treating X as current -- just without a precise end
        date, per employment_state.resolve_operator_confirmed_departure's
        graceful degradation."""
        self._write_baseline(_sal_baseline())
        text = "Caught up with Sal Nazir, formerly of PAR Technology."
        report = mri.assess(
            text=text, contact_id="sal-nazir", name="Sal Nazir",
            event_at="2026-09-18", captured_at="2026-09-18T16:30:00+00:00",
            organization="Formerly PAR Technology", apply=True,
        )
        sal = self._current_sal()
        self.assertIsNone(sal["current_company"])
        self.assertEqual(sal["last_known_company"], "PAR Technology")
        self.assertIsNone(sal.get("employment_end_date"))


if __name__ == "__main__":
    unittest.main()
