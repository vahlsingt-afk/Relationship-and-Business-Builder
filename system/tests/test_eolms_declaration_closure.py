"""
test_eolms_declaration_closure.py — RB-DEFECT-060

Regression coverage for evidence-backed EOLMS loop closure via the live
Custom GPT's executive-declaration pathway. Prior to this fix,
`ingestExecutiveDeclaration`'s `action_completed` event type only logged a
generic note to `interaction_ledger.json` and never actually closed a loop —
so "the well pump is working" had no path to auto-close anything.

Covers, isolated from real data (no test here reads or writes the live
system/eolms/loops.json):

  1. intelligence_triage.classify_executive_declaration() recognizing the new
     state_resolved / loop_advanced patterns (third-person resolution
     language and inbound-advance language that the original patterns,
     which require first-person "I have..." phrasing, don't catch).
  2. eolms.match_and_transition()'s confidence gate: a clear single match
     applies and transitions correctly; an ambiguous match (two
     similarly-scored candidates) does not apply anything; unrelated text
     returns no_match; a match that would reactivate a loop with an open
     dependency is still refused by the existing _apply_transition() gate.
  3. Integration: POST /ingest/executive_declaration via TestClient(server.app)
     end-to-end, asserting the response includes an eolms_loop_transition
     mutation entry.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

import rb_core as core  # noqa: E402
import eolms  # noqa: E402
import intelligence_triage as it  # noqa: E402


def _make_loop(**overrides) -> core.ELoop:
    today = date.today()
    defaults = dict(
        id="EL-TEST-001", title="Test loop", category="action", status="active",
        priority="medium", created_at=today, updated_at=today, last_activity=today,
        confidence="high",
    )
    defaults.update(overrides)
    return core.ELoop(**defaults)


class TestClassifyNewPatterns(unittest.TestCase):
    def test_state_resolved_third_person(self):
        stream = it.classify_executive_declaration("the well pump is working now")
        self.assertIsNotNone(stream)
        self.assertEqual(stream["event_type"], "state_resolved")
        self.assertEqual(stream["mutation_target"], "loop")

    def test_state_resolved_bare_resolved(self):
        stream = it.classify_executive_declaration("well pump resolved")
        self.assertEqual(stream["event_type"], "state_resolved")

    def test_loop_advanced_responded(self):
        stream = it.classify_executive_declaration("Ryan responded to my email")
        self.assertEqual(stream["event_type"], "loop_advanced")
        self.assertEqual(stream["mutation_target"], "loop")

    def test_loop_advanced_heard_back(self):
        stream = it.classify_executive_declaration("heard back from the vendor")
        self.assertEqual(stream["event_type"], "loop_advanced")

    def test_unrelated_text_does_not_match(self):
        self.assertIsNone(it.classify_executive_declaration("Let's grab lunch sometime this week."))

    def test_existing_action_completed_pattern_unaffected(self):
        stream = it.classify_executive_declaration("I sent the report")
        self.assertEqual(stream["event_type"], "action_completed")


class TestMatchAndTransition(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.loops_path = Path(self.tmpdir.name) / "loops.json"
        self.archive_dir = Path(self.tmpdir.name) / "archive"
        self._patches = [
            patch.object(core, "EOLMS_PATH", self.loops_path),
            patch.object(core, "EOLMS_ARCHIVE_DIR", self.archive_dir),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmpdir.cleanup()

    def _seed(self, loops: list[core.ELoop]) -> None:
        self.loops_path.write_text(json.dumps([l.to_dict() for l in loops], indent=2))

    def test_clear_match_applies_and_transitions(self):
        self._seed([_make_loop(
            id="EL-2026-01-01-001", title="Send commercials.",
            related_people=["Voosh"], next_action="Send commercials to Voosh.",
        )])
        result = eolms.match_and_transition("I sent the Voosh commercials this morning, that's done", apply=True)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["loop_id"], "EL-2026-01-01-001")
        self.assertEqual(result["to_status"], "completed")

        # Verify it was actually persisted, not just returned.
        reloaded = core.load_eloops(self.loops_path)
        self.assertEqual(reloaded[0].status, "completed")
        self.assertTrue(any(h["event"] == "intelligence_signal" for h in reloaded[0].history))

    def test_ambiguous_match_does_not_apply(self):
        # Two loops with identical titles/tags and distinguishing org names that the
        # declaration text doesn't mention — scores tie exactly, so neither should be
        # auto-picked (the declaration doesn't say which one it's about).
        self._seed([
            _make_loop(id="EL-2026-01-01-001", title="Home project",
                       tags=["home"], related_orgs=["VendorA"], category="project"),
            _make_loop(id="EL-2026-01-01-002", title="Home project",
                       tags=["home"], related_orgs=["VendorB"], category="project"),
        ])
        result = eolms.match_and_transition("the home project is finally done", apply=True)
        self.assertEqual(result["status"], "ambiguous")

        # Nothing should have been transitioned either way.
        reloaded = core.load_eloops(self.loops_path)
        for l in reloaded:
            self.assertEqual(l.status, "active")

    def test_unrelated_text_returns_no_match(self):
        self._seed([_make_loop(id="EL-2026-01-01-001", title="Send commercials.", related_people=["Voosh"])])
        result = eolms.match_and_transition("The weather is nice today.", apply=True)
        self.assertEqual(result["status"], "no_match")

    def test_dependency_gate_still_enforced_on_advance(self):
        # A waiting loop with an open (non-terminal) blocker should NOT be advanced to
        # active by a confident text match — the existing _apply_transition gate must
        # still refuse it, same as it does for manual/CLI transitions.
        self._seed([
            _make_loop(id="EL-2026-01-01-001", title="Blocker task", status="active",
                       related_people=["Acme"]),
            _make_loop(id="EL-2026-01-01-002", title="Dependent task", status="waiting",
                       related_people=["Acme"], waiting_on="Acme to finish the blocker task",
                       blocked_by=["EL-2026-01-01-001"]),
        ])
        result = eolms.match_and_transition("Acme confirmed the dependent task can move forward", apply=True)
        # Either it matched the dependent loop and was refused by the gate, or it matched
        # ambiguously between the two Acme-tagged loops — either way, nothing with an open
        # blocker should end up "active".
        reloaded = {l.id: l for l in core.load_eloops(self.loops_path)}
        self.assertNotEqual(reloaded["EL-2026-01-01-002"].status, "active")
        if result["status"] == "applied":
            self.assertNotEqual(result["loop_id"], "EL-2026-01-01-002")

    def test_dry_run_does_not_persist(self):
        self._seed([_make_loop(id="EL-2026-01-01-001", title="Send commercials.", related_people=["Voosh"])])
        result = eolms.match_and_transition("I sent the Voosh commercials, that's done", apply=False)
        self.assertEqual(result["status"], "dry_run")
        reloaded = core.load_eloops(self.loops_path)
        self.assertEqual(reloaded[0].status, "active")

    def test_requires_verification_routes_to_pending_verification(self):
        # RB-DEFECT-060 follow-up (EL-2026-07-02-021): a loop flagged as needing
        # independent verification lands in pending_verification, not completed,
        # on a confident "complete" match.
        self._seed([_make_loop(
            id="EL-2026-01-01-001", title="Fix widget bug",
            related_orgs=["WidgetCo"], requires_verification=True,
        )])
        result = eolms.match_and_transition("WidgetCo confirmed the widget bug is fixed", apply=True)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["to_status"], "pending_verification")
        reloaded = core.load_eloops(self.loops_path)
        self.assertEqual(reloaded[0].status, "pending_verification")

    def test_default_requires_verification_false_lands_in_completed(self):
        # Same scenario, but the (default) flag is off — behaves exactly as before.
        self._seed([_make_loop(id="EL-2026-01-01-001", title="Fix widget bug", related_orgs=["WidgetCo"])])
        result = eolms.match_and_transition("WidgetCo confirmed the widget bug is fixed", apply=True)
        self.assertEqual(result["to_status"], "completed")

    def test_tick_never_touches_pending_verification(self):
        old = date.today() - timedelta(days=120)
        self._seed([_make_loop(
            id="EL-2026-01-01-001", title="Fix widget bug", status="pending_verification",
            last_activity=old, updated_at=old, category="strategic_initiative",  # would be
            # long past any dormancy threshold if tick examined it
        )])
        loops = core.load_eloops(self.loops_path)
        changes = eolms._tick(loops)
        self.assertEqual(changes, [])
        self.assertEqual(loops[0].status, "pending_verification")

    def test_executive_summary_reports_pending_verification_counts(self):
        stale = date.today() - timedelta(days=10)
        fresh = date.today()
        loops = [
            _make_loop(id="EL-1", status="pending_verification", last_activity=stale, updated_at=stale),
            _make_loop(id="EL-2", status="pending_verification", last_activity=fresh, updated_at=fresh),
        ]
        counts = core.eloops_executive_summary(loops)["counts"]
        self.assertEqual(counts["pending_verification"], 2)
        self.assertEqual(counts["pending_verification_stale"], 1)


class TestIngestExecutiveDeclarationIntegration(unittest.TestCase):
    """End-to-end: POST /ingest/executive_declaration surfaces an EOLMS transition."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.loops_path = Path(self.tmpdir.name) / "loops.json"
        self.archive_dir = Path(self.tmpdir.name) / "archive"
        self.ledger_path = Path(self.tmpdir.name) / "interaction_ledger.json"
        self.ledger_path.write_text(json.dumps({"interactions": []}))

        import server  # noqa: E402  (imported lazily so path patches apply first)
        self.server = server
        # _execute_executive_declaration computes the interaction-ledger path inline as
        # SYSTEM_DIR / "interaction_ledger.json" (not a patchable module constant) — so
        # for an action_completed declaration, redirect server.SYSTEM_DIR to a tempdir
        # entirely rather than touching the real interaction_ledger.json. RB-DEFECT-042
        # was exactly this file getting polluted by test runs that didn't isolate it;
        # this test must never read or write the real one, even via backup/restore.
        self._patches = [
            patch.object(core, "EOLMS_PATH", self.loops_path),
            patch.object(core, "EOLMS_ARCHIVE_DIR", self.archive_dir),
            patch.object(server, "SYSTEM_DIR", Path(self.tmpdir.name)),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmpdir.cleanup()

    def test_declaration_triggers_eolms_transition(self):
        from fastapi.testclient import TestClient
        loops = [_make_loop(id="EL-2026-01-01-001", title="Send commercials.",
                             related_people=["Voosh"], next_action="Send commercials to Voosh.")]
        self.loops_path.write_text(json.dumps([l.to_dict() for l in loops], indent=2))

        client = TestClient(self.server.app, headers={"x-api-key": "test-key"})
        resp = client.post(
            "/ingest/executive_declaration",
            json={"text": "I sent the Voosh commercials this morning, that's done"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        eolms_mutations = [m for m in body["mutations_applied"] if m.get("mutation") == "eolms_loop_transition"]
        self.assertEqual(len(eolms_mutations), 1)
        self.assertEqual(eolms_mutations[0]["status"], "applied")
        self.assertEqual(eolms_mutations[0]["loop_id"], "EL-2026-01-01-001")

        reloaded = core.load_eloops(self.loops_path)
        self.assertEqual(reloaded[0].status, "completed")


if __name__ == "__main__":
    unittest.main()
