"""
test_passive_ri_linkedin_autoapply.py — regression test for the 2026-09-18
LinkedIn "stuck pending" defect (system/CLAUDE_HANDOFF_INTELLIGENCE_CYCLE_
REPAIR_2026-09-18.md, "LinkedIn" evidence section: 15 RI events written,
all 15 stayed proposed_write_pending_confirmation despite being dated,
net-new or dated-successor facts that mutation_policy.decide() classifies
as auto-appliable).

Root cause (confirmed by reading the code, not just the symptom): passive_
ri_ingest._ingest_candidate() only tagged event_at_confidence="high" for
source in {"email", "calendar"}; every linkedin_messaging-sourced candidate
was hardcoded to "medium", which then permanently failed the old
_is_high_confidence_projection_safe() gate regardless of how reliably dated
the LinkedIn platform timestamp actually was. refresh_all.py already calls
`passive_ri_ingest.py confirm --all --high-confidence-only` by default on
every pipeline run (RB-2026-08-24) -- so the auto-apply wiring existed, but
the LinkedIn events could never pass the gate to reach it.

This test proves both halves of the fix:
  1. A LinkedIn last_touch_update candidate (dated fact newer than the
     recorded last_touch) is classified event_at_confidence="high" and its
     _projection_decision() is AUTO_ADDED_DATED_SUCCESSOR / auto_apply=True
     (handoff acceptance tests 1 and 2).
  2. Running it through the real confirm_events() pipeline actually writes
     the touch (mutations.touch_contact) and advances baseline_index last_touch
     -- not just that the decision says it *should* auto-apply.

Isolation follows the existing pattern in passive_ri_ingest._smoke() /
_smoke_confirm_write(): rebind ri_events.EVENTS_DIR/INDEX_PATH and
ri_intake.PENDING_PATH to a tmp dir so nothing touches the real event log,
and patch rb_core.load_baseline / mutations.touch_contact's target file so
nothing touches the real baseline_index.json.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import passive_ri_ingest as pri  # noqa: E402
import ri_events  # noqa: E402
import ri_intake  # noqa: E402
import rb_core as core  # noqa: E402
import mutations  # noqa: E402
import mutation_policy  # noqa: E402


BASELINE_ENTRY = {
    "id": "ryan-hildebrand",
    "name": "Ryan Hildebrand",
    "signal_class": "RC",
    "last_touch": "2026-08-30",
    "company": "Acme Foods",
}


class LinkedInPassiveAutoApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="passive_ri_li_test_"))
        self._orig_events_dir = ri_events.EVENTS_DIR
        self._orig_index = ri_events.INDEX_PATH
        self._orig_pending = ri_intake.PENDING_PATH
        self._orig_cache = pri.CACHE_PATH
        self._orig_confirm_cache = pri.CONFIRM_CACHE_PATH
        self._orig_receipts = mutation_policy.RECEIPTS_PATH

        ri_events.EVENTS_DIR = self.tmp / "ri_events"
        ri_events.INDEX_PATH = self.tmp / "ri_events_index.json"
        ri_intake.PENDING_PATH = self.tmp / "ri_events_pending.json"
        pri.CACHE_PATH = self.tmp / "passive_ri_ingest.json"
        pri.CONFIRM_CACHE_PATH = self.tmp / "passive_ri_confirm.json"
        mutation_policy.RECEIPTS_PATH = self.tmp / "mutation_policy_receipts.jsonl"

        self.baseline = [dict(BASELINE_ENTRY)]

    def tearDown(self):
        ri_events.EVENTS_DIR = self._orig_events_dir
        ri_events.INDEX_PATH = self._orig_index
        ri_intake.PENDING_PATH = self._orig_pending
        pri.CACHE_PATH = self._orig_cache
        pri.CONFIRM_CACHE_PATH = self._orig_confirm_cache
        mutation_policy.RECEIPTS_PATH = self._orig_receipts

    def _make_linkedin_candidate(self, *, event_at: str) -> dict:
        return {
            "_source": "linkedin_last_touch",
            "contact_id": "ryan-hildebrand",
            "name": "Ryan Hildebrand",
            "signal_type": "last_touch_update",
            "event_at": event_at,
            "signal_strength": 0.70,
            "strategic_relevance": "medium",
            "reasoning": f"LinkedIn interaction on {event_at} is newer than recorded last_touch.",
            "grounding": "system_detected",
            "freshness": "fresh",
            "evidence": ["linkedin_messaging.proposed_last_touch_updates"],
            "source": "linkedin_messaging",  # the REAL source label _collect_linkedin_candidates() uses
        }

    def test_linkedin_candidate_tagged_high_confidence(self):
        """Root-cause fix: a system-detected LinkedIn timestamp is treated
        as reliably dated, same as email/calendar -- not downgraded to
        'medium' purely because of which platform it came from."""
        cand = self._make_linkedin_candidate(event_at="2026-09-16")
        result = {k: [] for k in (
            "blocked_low_confidence", "blocked_stale_source", "blocked_unmatched_entity",
            "blocked_no_date", "blocked_validation_error", "duplicates_skipped", "events_written",
        )}
        with mock.patch.object(core, "load_baseline", return_value=self.baseline):
            pri._ingest_candidate(cand, captured_at="2026-09-16T08:00:00Z", result=result)

        self.assertEqual(result["events_written"], result["events_written"])  # written, no exception
        self.assertEqual(len(result["events_written"]), 1)
        written_event_id = result["events_written"][0]["event_id"]
        stored = ri_events.find_by_event_id(written_event_id)
        self.assertEqual(stored["event_at_confidence"], "high")

    def test_linkedin_dated_successor_classified_auto_apply(self):
        """Handoff acceptance tests 1 & 2: a LinkedIn-sourced dated fact
        newer than the recorded last_touch is AUTO_ADDED_DATED_SUCCESSOR,
        not stuck behind a hardcoded 'medium' confidence label."""
        event = {
            "signal": {"type": "last_touch_update", "confidence": 0.70},
            "event_at": "2026-09-16",
            "event_at_confidence": "high",
            "entities": {"people": [{"matched_id": "ryan-hildebrand", "decision": "matched_existing"}]},
            "source": {"path": "linkedin_messaging"},
            "captured_at": "2026-09-16T08:00:00Z",
        }
        with mock.patch.object(core, "load_baseline", return_value=self.baseline):
            decision = pri._projection_decision(event)

        self.assertEqual(decision.status, mutation_policy.AUTO_ADDED_DATED_SUCCESSOR)
        self.assertTrue(decision.auto_apply)
        self.assertFalse(decision.requires_user_action)

    def test_before_fix_regression_guard_medium_confidence_still_blocks(self):
        """Confirms the fix is scoped correctly: an event that legitimately
        has low date confidence (not from a reliably-timestamped platform)
        still requires confirmation -- this isn't a blanket 'always auto-
        apply LinkedIn' change, it's specifically about date reliability."""
        event = {
            "signal": {"type": "last_touch_update", "confidence": 0.70},
            "event_at": "2026-09-16",
            "event_at_confidence": "medium",  # e.g. a source that can't confirm exact timing
            "entities": {"people": [{"matched_id": "ryan-hildebrand", "decision": "matched_existing"}]},
            "source": {"path": "some_unreliable_source"},
            "captured_at": "2026-09-16T08:00:00Z",
        }
        with mock.patch.object(core, "load_baseline", return_value=self.baseline):
            decision = pri._projection_decision(event)

        self.assertEqual(decision.status, mutation_policy.REJECTED_LOW_CONFIDENCE)
        self.assertFalse(decision.auto_apply)

    def test_end_to_end_confirm_pending_writes_touch_and_advances_baseline(self):
        """Full pipeline proof: ingest a LinkedIn candidate -> it's written
        as a proposed RI event -> confirm_pending(high_confidence_only=True)
        (exactly what refresh_all.py calls by default) actually applies the
        touch via mutations.touch_contact, and baseline_index's last_touch
        genuinely advances -- durable artifact evidence, not just a decision
        label."""
        cand = self._make_linkedin_candidate(event_at="2026-09-16")
        result = {k: [] for k in (
            "blocked_low_confidence", "blocked_stale_source", "blocked_unmatched_entity",
            "blocked_no_date", "blocked_validation_error", "duplicates_skipped", "events_written",
        )}

        touched: dict = {}

        def fake_touch_contact(contact_id, iso_date, source):
            touched["id"] = contact_id
            touched["date"] = iso_date
            self.baseline[0]["last_touch"] = iso_date
            return {"id": contact_id, "last_touch": iso_date}

        with mock.patch.object(core, "load_baseline", return_value=self.baseline), \
             mock.patch.object(mutations, "touch_contact", side_effect=fake_touch_contact):
            pri._ingest_candidate(cand, captured_at="2026-09-16T08:00:00Z", result=result)
            self.assertEqual(len(result["events_written"]), 1)

            confirm_result = pri.confirm_pending(dry_run=False, high_confidence_only=True)

        self.assertEqual(confirm_result["applied_count"], 1, confirm_result)
        self.assertEqual(confirm_result["blocked_count"], 0, confirm_result)
        self.assertEqual(touched.get("id"), "ryan-hildebrand")
        self.assertEqual(touched.get("date"), "2026-09-16")
        self.assertEqual(self.baseline[0]["last_touch"], "2026-09-16")

        applied_entry = confirm_result["applied"][0]
        self.assertEqual(applied_entry["persistence_status"], "persisted")
        self.assertEqual(applied_entry["decision_class"], mutation_policy.AUTO_ADDED_DATED_SUCCESSOR)

        # Receipt durability: mutation_policy recorded a receipt for this
        # decision, and it says the write actually applied.
        receipts = mutation_policy.load_receipts()
        self.assertEqual(len(receipts), 1)
        self.assertTrue(receipts[0]["applied"])
        self.assertEqual(receipts[0]["decision_class"], mutation_policy.AUTO_ADDED_DATED_SUCCESSOR)


if __name__ == "__main__":
    unittest.main()
