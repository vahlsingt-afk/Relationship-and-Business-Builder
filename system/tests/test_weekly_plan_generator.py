"""RB-DEFECT-021 — Monday plan-generation orchestrator tests.

Covers weekly_plan_generator.py: candidate derivation, allocation math,
draft construction, and the propose -> confirm lifecycle (including the
week-mismatch guard that prevents confirming a stale draft).
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import weekly_planning as wp
import weekly_plan_generator as gen


MONDAY = date(2026, 6, 8)  # matches the live system's current week-of in this session


def test_allocate_sums_to_100_and_weights_high_boost():
    candidates = [
        {"_boost": "high", "_source": "active_threads"},
        {"_boost": "low", "_source": "active_threads"},
        {"_boost": None, "_source": "loop_ledger"},
    ]
    pcts = gen._allocate(candidates)
    assert sum(pcts) == 100
    # high-boost and loop_ledger candidates get double weight vs. plain ones
    assert pcts[0] > pcts[1]
    assert pcts[2] > pcts[1]


def test_allocate_handles_empty():
    assert gen._allocate([]) == []


def test_candidate_from_thread_prefers_next_step_over_status_token():
    thread_with_next_step = {"id": "t1", "name": "Acme Co", "next_step": "Send proposal by Friday"}
    c = gen._candidate_from_thread(thread_with_next_step, 0)
    assert c["success_criteria"] == "Send proposal by Friday"

    thread_with_bare_status = {"id": "t2", "name": "Acme Co", "status": "open"}
    c2 = gen._candidate_from_thread(thread_with_bare_status, 1)
    # "open" is a state token, not guidance — must NOT leak into success_criteria
    assert c2["success_criteria"] != "open"
    assert "Acme Co" in c2["success_criteria"]

    thread_with_sentence_status = {
        "id": "t3", "name": "Acme Co",
        "status": "Awaiting signed contract from legal review",
    }
    c3 = gen._candidate_from_thread(thread_with_sentence_status, 2)
    assert c3["success_criteria"] == "Awaiting signed contract from legal review"


def test_candidate_from_thread_requires_a_name():
    assert gen._candidate_from_thread({"id": "t1"}, 0) is None


def test_candidate_from_overdue_loops_returns_none_when_clear():
    assert gen._candidate_from_overdue_loops({"overdue": [], "due_today": []}) is None


def test_candidate_from_overdue_loops_surfaces_count_and_ids():
    class FakeLoop:
        def __init__(self, loop_id):
            self.id = loop_id

    buckets = {"overdue": [FakeLoop("L-001")], "due_today": [FakeLoop("L-002")]}
    c = gen._candidate_from_overdue_loops(buckets)
    assert c is not None
    assert c["_boost"] == "high"
    assert set(c["linked_loop_ids"]) == {"L-001", "L-002"}
    assert "2 overdue/due-today" in c["success_criteria"]


def test_build_draft_plan_caps_outcomes_and_sums_allocation():
    with patch.object(gen, "_load_carryover_candidates", return_value=[]):
        plan = gen.build_draft_plan(MONDAY)
    assert plan["status"] == "draft_pending_confirmation"
    assert len(plan["outcomes"]) <= gen.MAX_CANDIDATE_OUTCOMES
    if plan["outcomes"]:
        total = sum(o["portfolio_allocation_pct"] for o in plan["outcomes"])
        assert total == 100
    # provenance must be present and aligned 1:1 with outcomes for transparency
    assert len(plan["generation_provenance"]) == len(plan["outcomes"])
    assert plan["week_of"] == wp._week_of(MONDAY)


def test_render_proposal_is_human_readable_and_labeled_as_draft():
    with patch.object(gen, "_load_carryover_candidates", return_value=[]):
        plan = gen.build_draft_plan(MONDAY)
    text = gen.render_proposal(plan)
    assert "DRAFT weekly plan" in text
    assert "pending confirmation" in text
    assert "STARTING PROPOSAL" in text


def test_confirm_promotes_draft_to_live_plan(tmp_path):
    draft_path = tmp_path / "draft.json"
    live_path = tmp_path / "live.json"

    plan = gen.build_draft_plan(MONDAY)
    draft_path.write_text(json.dumps(plan), encoding="utf-8")

    draft = json.loads(draft_path.read_text())
    assert wp.is_current(draft, MONDAY)
    draft["status"] = "active"
    draft.pop("generation_provenance", None)
    wp.save_plan(draft, path=live_path)

    loaded = wp.load_plan(live_path)
    assert loaded is not None
    assert loaded["status"] == "active"
    assert "generation_provenance" not in loaded
    assert wp.is_current(loaded, MONDAY)
    assert len(wp.active_outcomes(loaded)) == len(plan["outcomes"])


def test_confirm_rejects_stale_week_draft(tmp_path):
    """A draft generated for a prior week must not silently become this
    week's live plan — the CLI's --confirm path checks wp.is_current()
    before promoting. This test exercises the same guard logic directly."""
    stale_monday = date(2026, 6, 1)
    plan = gen.build_draft_plan(stale_monday)
    assert not wp.is_current(plan, MONDAY)


def test_confirm_draft_clears_pending_draft_state(tmp_path):
    """RB-DEFECT-067 regression: confirm_draft() promotes the draft's
    content to the LIVE plan (wp.save_plan(), which defaults to
    WEEKLY_PLAN_PATH — not draft_path) but used to never touch the on-disk
    draft file itself. weekly_plan_draft.json kept reporting
    status="draft_pending_confirmation" for a week that was already
    confirmed and active — confirmed live: two days after a real --confirm
    run, weekly_plan.json correctly showed status=active/week_of=2026-08-17
    while weekly_plan_draft.json still showed draft_pending_confirmation
    for that same week, so _render_weekly_plan_draft_alert() kept firing
    indefinitely. save_plan() is mocked here (its default target is the
    real production weekly_plan.json, not something this test may write
    to) — the assertion that matters is what confirm_draft() does to
    draft_path, which IS fully under this test's control."""
    draft_path = tmp_path / "draft.json"
    with patch.object(gen, "_load_carryover_candidates", return_value=[]):
        plan = gen.build_draft_plan(MONDAY)
    draft_path.write_text(json.dumps(plan), encoding="utf-8")

    # CARRYOVER_PATH patched to a tmp location: confirm_draft()'s post-promotion
    # cleanup step touches this path, and the real production one must never
    # be read or deleted just from running this test.
    with patch.object(gen.wp, "save_plan") as mock_save, \
         patch.object(gen, "CARRYOVER_PATH", tmp_path / "carryover.json"):
        result = gen.confirm_draft(draft_path=draft_path, today=MONDAY)

    assert "error" not in result
    mock_save.assert_called_once()
    promoted = mock_save.call_args[0][0]
    assert promoted["status"] == "active"  # the live plan got the promotion

    on_disk_draft = json.loads(draft_path.read_text())
    assert on_disk_draft["status"] != "draft_pending_confirmation", (
        "draft file must not still claim to be pending after a successful confirm"
    )
    assert on_disk_draft["status"] == "confirmed"
    assert on_disk_draft.get("confirmed_at")


# ---------------------------------------------------------------------------
# auto_adopt_if_monday_eod — RB-2026-08-24, Todd's explicit decision
# ---------------------------------------------------------------------------

def test_auto_adopt_promotes_pending_draft_on_monday(tmp_path):
    draft_path = tmp_path / "draft.json"
    with patch.object(gen, "_load_carryover_candidates", return_value=[]):
        plan = gen.build_draft_plan(MONDAY)
    plan["status"] = "draft_pending_confirmation"
    draft_path.write_text(json.dumps(plan), encoding="utf-8")

    with patch.object(gen.wp, "save_plan") as mock_save, \
         patch.object(gen, "CARRYOVER_PATH", tmp_path / "carryover.json"):
        result = gen.auto_adopt_if_monday_eod(draft_path=draft_path, today=MONDAY)

    assert result["status"] == "auto_adopted"
    mock_save.assert_called_once()
    on_disk_draft = json.loads(draft_path.read_text())
    assert on_disk_draft["status"] == "confirmed"


def test_auto_adopt_skips_on_non_monday(tmp_path):
    draft_path = tmp_path / "draft.json"
    plan = gen.build_draft_plan(MONDAY)
    plan["status"] = "draft_pending_confirmation"
    draft_path.write_text(json.dumps(plan), encoding="utf-8")

    tuesday = date(2026, 6, 9)
    with patch.object(gen.wp, "save_plan") as mock_save:
        result = gen.auto_adopt_if_monday_eod(draft_path=draft_path, today=tuesday)

    assert result == {"status": "skipped", "reason": "not Monday"}
    mock_save.assert_not_called()


def test_overdue_recovery_adopts_current_week_draft_on_tuesday(tmp_path):
    draft_path = tmp_path / "draft.json"
    plan = gen.build_draft_plan(MONDAY)
    plan["status"] = "draft_pending_confirmation"
    draft_path.write_text(json.dumps(plan), encoding="utf-8")
    tuesday = date(2026, 6, 9)
    with patch.object(gen.wp, "save_plan") as mock_save, \
         patch.object(gen, "CARRYOVER_PATH", tmp_path / "carryover.json"):
        result = gen.auto_adopt_overdue_draft(draft_path=draft_path, today=tuesday)
    assert result["status"] == "auto_adopted"
    mock_save.assert_called_once()


def test_overdue_recovery_never_overrides_rejected_draft(tmp_path):
    draft_path = tmp_path / "draft.json"
    plan = gen.build_draft_plan(MONDAY)
    plan["status"] = "rejected"
    draft_path.write_text(json.dumps(plan), encoding="utf-8")
    with patch.object(gen.wp, "save_plan") as mock_save:
        result = gen.auto_adopt_overdue_draft(draft_path=draft_path, today=date(2026, 6, 9))
    assert result["status"] == "skipped"
    mock_save.assert_not_called()


def test_auto_adopt_never_overrides_an_explicit_rejection(tmp_path):
    """The whole point of this being a fallback, not a default: a draft
    Todd explicitly rejected must never get silently promoted just because
    Monday evening arrived."""
    draft_path = tmp_path / "draft.json"
    plan = gen.build_draft_plan(MONDAY)
    plan["status"] = "rejected"
    plan["rejected_at"] = "2026-06-08T09:00:00Z"
    draft_path.write_text(json.dumps(plan), encoding="utf-8")

    with patch.object(gen.wp, "save_plan") as mock_save:
        result = gen.auto_adopt_if_monday_eod(draft_path=draft_path, today=MONDAY)

    assert result["status"] == "skipped"
    mock_save.assert_not_called()
    on_disk_draft = json.loads(draft_path.read_text())
    assert on_disk_draft["status"] == "rejected"


def test_auto_adopt_is_a_noop_if_already_confirmed(tmp_path):
    draft_path = tmp_path / "draft.json"
    plan = gen.build_draft_plan(MONDAY)
    plan["status"] = "confirmed"
    plan["confirmed_at"] = "2026-06-08T09:00:00Z"
    draft_path.write_text(json.dumps(plan), encoding="utf-8")

    with patch.object(gen.wp, "save_plan") as mock_save:
        result = gen.auto_adopt_if_monday_eod(draft_path=draft_path, today=MONDAY)

    assert result["status"] == "skipped"
    mock_save.assert_not_called()


def test_auto_adopt_skips_when_no_draft_present(tmp_path):
    missing_path = tmp_path / "no_such_draft.json"
    result = gen.auto_adopt_if_monday_eod(draft_path=missing_path, today=MONDAY)
    assert result == {"status": "skipped", "reason": "no draft present"}


def test_auto_adopt_surfaces_stale_week_failure(tmp_path):
    """A draft that's technically pending but for the wrong week (e.g. a
    stale draft nobody regenerated) must be reported as failed, not
    silently promoted for the wrong week."""
    draft_path = tmp_path / "draft.json"
    stale_monday = date(2026, 6, 1)
    plan = gen.build_draft_plan(stale_monday)
    plan["status"] = "draft_pending_confirmation"
    draft_path.write_text(json.dumps(plan), encoding="utf-8")

    with patch.object(gen.wp, "save_plan") as mock_save:
        result = gen.auto_adopt_if_monday_eod(draft_path=draft_path, today=MONDAY)

    assert result["status"] == "failed"
    assert "error" in result
    mock_save.assert_not_called()


def test_confirm_draft_error_path_leaves_draft_file_untouched(tmp_path):
    """A rejected (stale-week) confirm attempt must not mutate the draft
    file at all — only a successful promotion should flip its status."""
    draft_path = tmp_path / "draft.json"
    stale_monday = date(2026, 6, 1)
    plan = gen.build_draft_plan(stale_monday)
    draft_path.write_text(json.dumps(plan), encoding="utf-8")
    before = draft_path.read_text()

    with patch.object(gen.wp, "save_plan") as mock_save:
        result = gen.confirm_draft(draft_path=draft_path, today=MONDAY)

    assert "error" in result
    mock_save.assert_not_called()
    assert draft_path.read_text() == before


def test_generate_candidates_excludes_closed_threads():
    """Regression: a thread marked status:closed (e.g. Patrick Nelson / Matrix
    Software Solutions, closed 2026-07-03) was still proposed as an "Advance
    <name>" candidate outcome days after closure, since generate_candidates()
    only filtered opp_threads by type, never by status. A freshly-generated
    draft on 2026-07-06 still carried "Advance Patrick Nelson / Matrix
    Software Solutions follow-up" as a candidate, wasting a portfolio
    allocation slot on already-finished work."""
    threads = [
        {"id": "t-open", "name": "Open Deal Co", "type": "opportunity", "status": "open"},
        {"id": "t-closed", "name": "Patrick Nelson / Matrix Software Solutions", "type": "partnership", "status": "closed"},
        {"id": "t-done", "name": "Wrapped Up Inc", "type": "opportunity", "status": "done"},
    ]
    with patch.object(gen, "_load_threads", return_value=threads), \
         patch.object(gen, "_load_carryover_candidates", return_value=[]), \
         patch.object(gen.core, "parse_loop_ledger", return_value=[]), \
         patch.object(gen.core, "loops_by_status", return_value={}):
        candidates = gen.generate_candidates(MONDAY)
    titles = [c["title"] for c in candidates]
    assert any("Open Deal Co" in t for t in titles)
    assert not any("Patrick Nelson" in t for t in titles)
    assert not any("Wrapped Up Inc" in t for t in titles)


# ---------------------------------------------------------------------------
# Carryover — friday_eow_routine.py's still-open-outcome handoff into next
# week's draft generation
# ---------------------------------------------------------------------------

def _write_carryover(path, outcomes):
    path.write_text(json.dumps({
        "from_week_of": "2026-06-01", "generated_at": "2026-06-05T20:00:00Z",
        "carryover_outcomes": outcomes,
    }), encoding="utf-8")


def test_load_carryover_candidates_missing_file_returns_empty(tmp_path):
    with patch.object(gen, "CARRYOVER_PATH", tmp_path / "no_such_file.json"):
        assert gen._load_carryover_candidates() == []


def test_load_carryover_candidates_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "carryover.json"
    path.write_text("{not valid json", encoding="utf-8")
    with patch.object(gen, "CARRYOVER_PATH", path):
        assert gen._load_carryover_candidates() == []


def test_load_carryover_candidates_skips_outcomes_with_no_open_loops(tmp_path):
    """An outcome only carries forward if it still has an actually-open
    loop -- one whose loop(s) all closed since Friday must not keep getting
    proposed as if nothing happened."""
    path = tmp_path / "carryover.json"
    _write_carryover(path, [
        {"id": "outcome-a", "title": "A", "success_criteria": "sc-a",
         "linked_opportunity_ids": ["opp-a"], "linked_loop_ids": ["L-1"],
         "still_open_loop_ids": ["L-1"]},
        {"id": "outcome-b", "title": "B", "success_criteria": "sc-b",
         "linked_opportunity_ids": ["opp-b"], "linked_loop_ids": ["L-2"],
         "still_open_loop_ids": []},
    ])
    with patch.object(gen, "CARRYOVER_PATH", path):
        candidates = gen._load_carryover_candidates()
    ids = [c["id"] for c in candidates]
    assert ids == ["outcome-a"]
    assert candidates[0]["_source"] == "carryover"
    assert candidates[0]["_boost"] == "high"
    assert candidates[0]["linked_loop_ids"] == ["L-1"]
    assert "week of 2026-06-01" in candidates[0]["success_criteria"]


def test_generate_candidates_ranks_carryover_first_and_dedupes(tmp_path):
    """A carried-forward outcome must rank ahead of a fresh thread candidate,
    and must not ALSO be proposed a second time as a generic thread advance
    or folded into the generic overdue-loop bucket."""
    carryover_path = tmp_path / "carryover.json"
    _write_carryover(carryover_path, [
        {"id": "outcome-pollo", "title": "Get Pollo Campero RFP send-ready",
         "success_criteria": "sc", "linked_opportunity_ids": ["pollo-campero"],
         "linked_loop_ids": ["L-old-1"], "still_open_loop_ids": ["L-old-1"]},
    ])
    threads = [
        {"id": "pollo-campero", "name": "Pollo Campero", "type": "opportunity", "status": "open"},
        {"id": "t-fresh", "name": "Fresh Deal Co", "type": "opportunity", "status": "open"},
    ]

    class FakeLoop:
        def __init__(self, loop_id):
            self.id = loop_id

    buckets = {"overdue": [FakeLoop("L-old-1"), FakeLoop("L-other")], "due_today": []}

    with patch.object(gen, "CARRYOVER_PATH", carryover_path), \
         patch.object(gen, "_load_threads", return_value=threads), \
         patch.object(gen.core, "parse_loop_ledger", return_value=[]), \
         patch.object(gen.core, "loops_by_status", return_value=buckets):
        candidates = gen.generate_candidates(MONDAY)

    # Carryover ranks first.
    assert candidates[0]["id"] == "outcome-pollo"
    # The same real-world thing isn't proposed a second time as a fresh
    # "Advance Pollo Campero" thread candidate.
    assert not any(c["id"] != "outcome-pollo" and "Pollo Campero" in c.get("title", "") for c in candidates)
    # L-old-1 isn't double-counted into the generic overdue bucket, but the
    # unrelated L-other still is.
    loop_bucket = next((c for c in candidates if c["id"] == "outcome-loop-stability"), None)
    assert loop_bucket is not None
    assert "L-old-1" not in loop_bucket["linked_loop_ids"]
    assert "L-other" in loop_bucket["linked_loop_ids"]
    # The unrelated fresh thread is still proposed.
    assert any("Fresh Deal Co" in c.get("title", "") for c in candidates)


def test_confirm_draft_clears_carryover_after_successful_promotion(tmp_path):
    draft_path = tmp_path / "draft.json"
    carryover_path = tmp_path / "carryover.json"
    _write_carryover(carryover_path, [
        {"id": "outcome-a", "title": "A", "success_criteria": "sc",
         "linked_opportunity_ids": [], "linked_loop_ids": ["L-1"], "still_open_loop_ids": ["L-1"]},
    ])
    with patch.object(gen, "CARRYOVER_PATH", carryover_path), \
         patch.object(gen, "_load_carryover_candidates", return_value=[]):
        plan = gen.build_draft_plan(MONDAY)
    draft_path.write_text(json.dumps(plan), encoding="utf-8")

    with patch.object(gen.wp, "save_plan"), \
         patch.object(gen, "CARRYOVER_PATH", carryover_path):
        result = gen.confirm_draft(draft_path=draft_path, today=MONDAY)

    assert "error" not in result
    assert not carryover_path.exists()


def test_confirm_draft_missing_carryover_does_not_error(tmp_path):
    """No carryover file at all (the common case, most weeks) must not
    make a successful confirm fail."""
    draft_path = tmp_path / "draft.json"
    with patch.object(gen, "_load_carryover_candidates", return_value=[]):
        plan = gen.build_draft_plan(MONDAY)
    draft_path.write_text(json.dumps(plan), encoding="utf-8")

    with patch.object(gen.wp, "save_plan"), \
         patch.object(gen, "CARRYOVER_PATH", tmp_path / "no_such_carryover.json"):
        result = gen.confirm_draft(draft_path=draft_path, today=MONDAY)

    assert "error" not in result


def test_generate_risks_excludes_closed_threads():
    """Same bug class as generate_candidates, one function over: a closed
    thread (Coates Group, closed) still generated an "Opportunity momentum
    decay" risk in the 2026-07-06 draft even though the opportunity was
    already closed, not merely quiet."""
    threads = [
        {"id": "t-stale-open", "name": "Stale Open Co", "type": "opportunity",
         "status": "open", "boost_for_brief": "high", "opened": "2026-05-01"},
        {"id": "t-stale-closed", "name": "Coates Group", "type": "opportunity",
         "status": "closed", "boost_for_brief": "high", "opened": "2026-05-01"},
    ]
    with patch.object(gen, "_load_threads", return_value=threads):
        risks = gen._generate_risks(MONDAY, [])
    titles = [r["title"] for r in risks]
    assert any("Stale Open Co" in t for t in titles)
    assert not any("Coates Group" in t for t in titles)


def test_generate_risks_no_longer_produces_relationship_decay_risk(tmp_path):
    """RB-2026-08-25: this risk type was removed as a source-level dedup
    fix -- daily_brief.py's own DRR decay alert block (_compute_my_
    priorities section 6a) reads the exact same drr_score.json and already
    produces a strictly better version of the same signal (daily cadence,
    dual threshold, opportunity-exclusion, cooldown). Confirmed live: the
    same person (Jeff Coffland) was flagged twice in My Priorities --
    "Relationship at risk: Jeff Coffland" from this generator and "↘ Jeff
    Coffland" from daily_brief.py's, under two title formats that didn't
    dedup against each other. Even with a real drr_score.json present and
    a row that would have qualified under the old recency<0.4 threshold,
    no "Relationship at risk:" item should be generated here."""
    drr_path = tmp_path / "drr_score.json"
    drr_path.write_text(json.dumps({
        "data": {"rows": [
            {"id": "c1", "name": "Jeff Coffland", "signal_class": "RC",
             "components": {"recency": 0.14}},
        ]}
    }))
    with patch.object(gen, "_load_threads", return_value=[]), \
         patch.object(gen.core, "CACHE_DIR", tmp_path):
        risks = gen._generate_risks(MONDAY, [])
    titles = [r["title"] for r in risks]
    assert not any("Relationship at risk" in t for t in titles)
    assert not any("Jeff Coffland" in t for t in titles)


def test_generate_forcing_functions_excludes_closed_threads():
    """Same bug class again: a closed thread still fired a "21d stall
    threshold" forcing function."""
    threads = [
        {"id": "t-stall-open", "name": "Stalled Open Co", "type": "opportunity",
         "status": "open", "boost_for_brief": "high", "opened": "2026-05-01"},
        {"id": "t-stall-closed", "name": "Coates Group", "type": "opportunity",
         "status": "closed", "boost_for_brief": "high", "opened": "2026-05-01"},
    ]
    with patch.object(gen, "_load_threads", return_value=threads), \
         patch.object(gen.core, "parse_loop_ledger", return_value=[]), \
         patch.object(gen.core, "loops_by_status", return_value={}):
        ffs = gen._generate_forcing_functions(MONDAY, [])
    titles = [f["title"] for f in ffs]
    assert any("Stalled Open Co" in t for t in titles)
    assert not any("Coates Group" in t for t in titles)
