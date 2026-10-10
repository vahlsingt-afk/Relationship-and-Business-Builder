"""
test_loop_state_reconciliation.py -- RB-DEFECT-074 acceptance coverage.

Fixtures only (temp ledger / state / weekly plan); nothing here reads or writes
the live loop_ledger.md, loop_state.json or weekly_plan.json.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import rb_core as core  # noqa: E402
import loop_state as ls  # noqa: E402
import weekly_planning as wp  # noqa: E402

TODAY = date(2026, 10, 10)

ROWS = [
    ("L-2026-08-31-001", "2026-08-31", "Josh Wesolowski / McDonald's",
     "LinkedIn reply: Josh is open to meeting. Next: send collateral and schedule a late-September meeting.",
     "2026-09-22", "open"),
    ("L-2026-08-28-001", "2026-08-28", "Jeff Caplin (Church's Texas Chicken)",
     "Jeff Caplin plans to stop by FSTEC to talk with Todd; schedule time.", "2026-09-22", "open"),
    ("L-2026-08-11-001", "2026-08-11", "Jeff Coffland",
     "Receive Jeff Coffland's introduction to Justine Power and McDonald's routing feedback. "
     "Waiting on Jeff Coffland since the product map was sent.", "2026-09-30", "open"),
    ("L-2026-08-10-002", "2026-08-10", "Worldpay metered pricing initiative / Five Guys",
     "**Internal initiative approval gate before any customer approach.** Metered transaction based "
     "pricing proposal for Five Guys; push Ryan for internal review.", "2026-09-22", "open"),
    ("L-2026-09-21-001", "2026-09-21", "RB self-audit",
     "Self-audit findings: 1 write op(s) receiving traffic with zero confirmed mutations", "2026-10-01", "open"),
]


def _write_ledger(path: Path, rows=ROWS) -> None:
    lines = ["# Loop Ledger", "", "| ID | Opened | Person/Company | Loop | Closure target | Status |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {a} | {b} | {c} | {d} | {e} | {f} |" for a, b, c, d, e, f in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.ledger, self.state = t / "loop_ledger.md", t / "loop_state.json"
        _write_ledger(self.ledger)
        self.plan = t / "weekly_plan.json"
        self.patches = [
            patch.object(core, "LOOP_LEDGER_PATH", self.ledger),
            patch.object(core, "LOOP_STATE_PATH", self.state),
            patch.object(wp, "WEEKLY_PLAN_PATH", self.plan),
            # snapshots go to the real _snapshots dir otherwise
            patch("mutations.snapshot", lambda *a, **k: None),
            patch("mutations.eolms.close_by_ledger_id", lambda *a, **k: None),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def status(self, loop_id):
        return next(l for l in core.parse_loop_ledger() if l.id == loop_id)


class TestMeetingEvidence(Base):
    def test_processed_meeting_closes_only_josh_scheduling_loop_and_is_idempotent(self):
        cap = {"file_id": "cap-1", "queued_at": "2026-10-07T15:45:02Z",
               "transcript": "Okay yeah awesome I appreciate it Josh and in a couple of weeks we'll "
                             "get something set up. Michael and Ryan both say hello."}
        res = ls.reconcile_capture(cap)
        self.assertTrue(res["mutation_applied"], res)
        self.assertEqual([a["loop_id"] for a in res["applied"] if a["transition"] == "closed"],
                         ["L-2026-08-31-001"])
        self.assertTrue(self.status("L-2026-08-31-001").closed)
        # Others untouched (Coffland introduction, approval gate, self-audit).
        for lid in ("L-2026-08-11-001", "L-2026-08-10-002", "L-2026-09-21-001", "L-2026-08-28-001"):
            self.assertFalse(self.status(lid).closed, lid)
        # Provenance recorded in the ledger close reason.
        self.assertIn("capture:cap-1", self.ledger.read_text())
        # Idempotent replay: no further mutation.
        again = ls.reconcile_capture(cap)
        self.assertFalse(again["mutation_applied"])

    def test_josh_and_coffland_are_not_conflated_on_first_name(self):
        res = ls.reconcile_text("Jeff mentioned the plan; thank-you sent.", evidence_id="e1")
        # 'Jeff' is shared by Caplin and Coffland, so a first name alone matches neither.
        self.assertFalse(res["mutation_applied"], res)


class TestDeclarations(Base):
    def test_coffland_touch_does_not_close_unfulfilled_introduction(self):
        res = ls.reconcile_text("I met with Jeff Coffland this week and emailed him.",
                                evidence_id="e-coff", event_date="2026-10-10")
        self.assertFalse(self.status("L-2026-08-11-001").closed)
        st = ls.read_state("L-2026-08-11-001")
        self.assertEqual(st["last_interaction"], "2026-10-10")
        self.assertNotIn("closed", st.get("state", ""))
        self.assertEqual(res["status"], "recorded")

    def test_coffland_introduction_closes_only_with_receipt_language(self):
        ls.reconcile_text("Jeff Coffland introduced me to Justin Powers; done.", evidence_id="e-int")
        self.assertTrue(self.status("L-2026-08-11-001").closed)

    def test_mtbp_progress_never_approves_or_closes(self):
        res = ls.reconcile_text(
            "Floated metered transaction pricing to Ryan; Ryan requested material for Michael "
            "Estabrooks and I sent it. Proforma started.", evidence_id="e-mtbp")
        self.assertFalse(self.status("L-2026-08-10-002").closed)
        self.assertTrue(any(i["loop_id"] == "L-2026-08-10-002" for i in res["review_items"]), res)

    def test_nonresponse_suppresses_chase_and_proposes_disposition(self):
        res = ls.reconcile_text(
            "Sent two emails and one LinkedIn response to Jeff Caplin, no response.",
            evidence_id="e-cap", event_date="2026-10-10")
        st = ls.read_state("L-2026-08-28-001")
        self.assertEqual(st["state"], "awaiting_response")
        self.assertGreaterEqual(st["outreach_attempts"], 2)
        self.assertTrue(st["strategy"]["suppress_chase"])
        self.assertTrue(st["strategy"]["review_first"])
        kinds = {i["kind"] for i in res["review_items"]}
        self.assertIn("disposition_proposal", kinds)
        self.assertFalse(self.status("L-2026-08-28-001").closed)
        # Bucketed as waiting, not an overdue chase.
        b = core.loops_by_status(core.parse_loop_ledger(), TODAY)
        self.assertIn("L-2026-08-28-001", [l.id for l in b["waiting"]])
        self.assertNotIn("L-2026-08-28-001", [l.id for l in b["overdue"]])

    def test_no_match_is_not_a_mutation(self):
        res = ls.reconcile_text("The weather is nice.", evidence_id="e-x")
        self.assertEqual(res["status"], "no_match")
        self.assertFalse(res["mutation_applied"])
        self.assertFalse(self.state.exists())

    def test_self_audit_loop_never_declaration_closed(self):
        ls.reconcile_text("RB self audit findings resolved and done.", evidence_id="e-sa")
        self.assertFalse(self.status("L-2026-09-21-001").closed)

    def test_metadata_only_evidence_never_closes(self):
        res = ls.reconcile_text("Thanks Josh Wesolowski — thank-you sent", evidence_id="outlook:1",
                                metadata_only=True)
        self.assertFalse(self.status("L-2026-08-31-001").closed)
        self.assertFalse(any(a["transition"] == "closed" for a in res["applied"]))


class TestUpdateLoopState(Base):
    def test_requires_evidence_and_valid_state(self):
        with self.assertRaises(ValueError):
            ls.update_loop_state("L-2026-08-10-002", state="awaiting_internal_review", source_evidence=[])
        with self.assertRaises(ValueError):
            ls.update_loop_state("L-2026-08-10-002", state="bogus", source_evidence=["todd:2026-10-10"])
        with self.assertRaises(ValueError):
            ls.update_loop_state("L-9999-01-01-001", state="waiting", source_evidence=["x"])

    def test_unknown_checkpoint_is_not_invented_and_update_is_idempotent(self):
        kw = dict(current_action="Waiting for next internal meeting; proforma P&L in progress",
                  state="awaiting_internal_review", waiting_on="Ryan / Michael Estabrooks",
                  next_checkpoint=None, source_evidence=["todd:2026-10-10"])
        r1 = ls.update_loop_state("L-2026-08-10-002", **kw)
        self.assertEqual(r1["status"], "updated")
        self.assertIn("state", r1["changed_fields"])
        self.assertIsNone(r1["state"]["next_checkpoint"])
        r2 = ls.update_loop_state("L-2026-08-10-002", **kw)
        self.assertEqual(r2["status"], "no_change")
        self.assertFalse(r2["mutation_applied"])
        b = core.loops_by_status(core.parse_loop_ledger(), TODAY)
        self.assertIn("L-2026-08-10-002", [l.id for l in b["waiting"]])
        # Ledger prose and target date untouched.
        self.assertEqual(self.status("L-2026-08-10-002").target, date(2026, 9, 22))

    def test_rejects_closed_loop(self):
        ls.reconcile_text("Josh Wesolowski meeting occurred, thank-you sent", evidence_id="c1",
                          evidence_kind="meeting_occurred")
        with self.assertRaises(ValueError):
            ls.update_loop_state("L-2026-08-31-001", state="waiting", source_evidence=["x"])


class TestWeeklyPlan(Base):
    def _plan(self, week_of="2026-09-28"):
        plan = wp.new_plan(date.fromisoformat(week_of))
        plan["week_of"] = week_of
        plan["outcomes"] = [
            wp.make_outcome("o1", "Close out loops", "done", linked_loop_ids=["L-2026-08-31-001"]),
            wp.make_outcome("o1", "Close out loops", "done",
                            linked_loop_ids=["L-2026-08-28-001", "L-2026-09-21-001"]),
        ]
        wp.save_plan(plan, self.plan)

    def test_closed_linked_loop_reaches_plan_and_stale_week_is_exposed(self):
        self._plan()
        ls.reconcile_text("Josh Wesolowski meeting occurred, thank-you sent", evidence_id="c1",
                          evidence_kind="meeting_occurred")
        res = ls.reconcile_weekly_plan(self.plan, today=TODAY)
        self.assertEqual(res["status"], "reconciled")
        saved = wp.load_plan(self.plan)
        self.assertEqual(saved["outcomes"][0]["status"], "closed")
        self.assertEqual(saved["outcomes"][0]["loop_progress"]["closed_ids"], ["L-2026-08-31-001"])
        self.assertEqual(saved["outcomes"][1]["status"], "active")
        h = res["health"]
        self.assertTrue(h["stale"])
        self.assertEqual(h["current_week_of"], wp._week_of(TODAY))
        self.assertEqual(h["duplicate_outcome_ids"], ["o1"])


class TestApi(Base):
    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(ROOT / "system" / "api"))
        import server
        from fastapi.testclient import TestClient
        self.server = server
        self.sys_patch = patch.object(server, "SYSTEM_DIR", Path(self.tmp.name))
        self.sys_patch.start()
        (Path(self.tmp.name) / "interaction_ledger.json").write_text(json.dumps({"interactions": []}))
        self.client = TestClient(server.app, headers={"x-api-key": "test-key"})

    def tearDown(self):
        self.sys_patch.stop()
        super().tearDown()

    def test_update_loop_state_endpoint_and_getloops_readback(self):
        r = self.client.post("/loops/state", json={
            "id": "L-2026-08-10-002", "state": "awaiting_internal_review",
            "waiting_on": "Ryan / Michael Estabrooks", "clear_checkpoint": True,
            "current_action": "Await next internal meeting; proforma P&L started",
            "source_evidence": ["todd:2026-10-10"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("state", r.json()["changed_fields"])
        got = self.client.get("/loops").json()
        waiting = {l["id"]: l for l in got["buckets"]["waiting"]}
        self.assertEqual(waiting["L-2026-08-10-002"]["state"]["waiting_on"], "Ryan / Michael Estabrooks")
        self.assertIn("plan_health", got)

    def test_update_loop_state_rejects_missing_evidence(self):
        r = self.client.post("/loops/state", json={"id": "L-2026-08-10-002", "state": "waiting",
                                                   "source_evidence": []})
        self.assertEqual(r.status_code, 422)

    def test_declaration_no_match_is_not_reported_as_transition(self):
        r = self.client.post("/ingest/executive_declaration",
                             json={"text": "I completed the unrelated gutter cleaning today."})
        self.assertEqual(r.status_code, 200, r.text)
        lt = r.json().get("loop_transitions")
        if lt is not None:
            self.assertFalse(lt["transition_applied"])

    def test_declaration_reaches_ledger_loop(self):
        r = self.client.post("/ingest/executive_declaration", json={
            "text": "I met with Josh Wesolowski; the meeting occurred Wednesday and the thank-you sent.",
            "event_at": "2026-10-10"})
        self.assertEqual(r.status_code, 200, r.text)
        lt = r.json()["loop_transitions"]
        self.assertTrue(lt["transition_applied"], r.json())
        self.assertTrue(self.status("L-2026-08-31-001").closed)


if __name__ == "__main__":
    unittest.main()
