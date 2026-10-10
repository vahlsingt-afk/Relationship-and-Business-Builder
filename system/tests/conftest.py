"""
conftest.py — test-session isolation for runtime ledger files.

RB-DEFECT-042 closed most ledger-pollution leaks, but running the full suite
still left several tracked runtime files modified:

  - system/api/request.log              (every TestClient request is logged)
  - system/behavioral_intelligence.json (macro_intelligence default store)
  - system/entity_intelligence.json     (macro_intelligence default store)
  - system/conversation_insights.json   (insight_intake default store)
  - system/strategic_events.json        (strategic_events default store)
  - system/audit/<YYYY-MM>.jsonl        (audit_log default partition dir)

Each of these is a module-level constant computed once at import time, and
several test files load the owning module multiple times under different
names via importlib (especially system/api/server.py). Monkeypatching the
already-bound constants after the fact can't reach every one of those copies.

Instead, the owning modules (macro_intelligence.py, insight_intake.py,
strategic_events.py, audit_log.py, system/api/server.py) compute these
constants from an `RB_*_PATH` / `RB_*_DIR` environment variable, falling back
to the real on-disk location when unset. Setting those variables here, at
conftest collection time — before any test module (and therefore before any
of those modules) is imported — redirects every copy to a shared per-session
temp directory.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_RB_TEST_RUNTIME_DIR = tempfile.mkdtemp(prefix="rb_test_runtime_")
_runtime = Path(_RB_TEST_RUNTIME_DIR)

os.environ.setdefault("RB_BEHAVIORAL_INTELLIGENCE_PATH", str(_runtime / "behavioral_intelligence.json"))
os.environ.setdefault("RB_ENTITY_INTELLIGENCE_PATH", str(_runtime / "entity_intelligence.json"))
os.environ.setdefault("RB_CONVERSATION_INSIGHTS_PATH", str(_runtime / "conversation_insights.json"))
os.environ.setdefault("RB_STRATEGIC_EVENTS_PATH", str(_runtime / "strategic_events.json"))
os.environ.setdefault("RB_AUDIT_DIR", str(_runtime / "audit"))
os.environ.setdefault("RB_AUDIT_INDEX_PATH", str(_runtime / "audit_log_index.json"))
os.environ.setdefault("RB_REQUEST_LOG_PATH", str(_runtime / "request.log"))
# RB-2026-09-30: team_portal_usage_log.py's _UsageLogger middleware is
# attached globally to team_portal_api.app (app.add_middleware(...) at
# module import time), so it fires for every request any test sends
# through TestClient(team_portal_api.app) -- including every existing
# test file that predates this feature and has no reason to know about
# it. Confirmed live: running the pre-existing Team Portal test suites
# after adding the middleware wrote 100+ real lines (member_id "jsmith"
# from unrelated auth fixtures, etc.) into the real system/team/
# usage_log/ directory. Same class of bug as RB_REQUEST_LOG_PATH above,
# same fix: redirect via the env var the module already reads at import
# time, before any test module (and therefore team_portal_usage_log.py)
# is imported.
os.environ.setdefault("RB_TEAM_PORTAL_USAGE_LOG_DIR", str(_runtime / "team_portal_usage_log"))
os.environ.setdefault("RB_EXTERNAL_BRIEF_DIR", str(_runtime / "external_brief"))
# RB-DEFECT-066 backfill (2026-08-10): earnings_monitor.OUTPUT_PATH now has
# real earnings_release rows in production, so any test that builds a brief
# without individually patching it (most do not) reads real production
# signals and, via record_earnings_history, makes real network fetches and
# writes real history records. Same isolation pattern as the paths above.
os.environ.setdefault("RB_EARNINGS_OUTPUT_PATH", str(_runtime / "market_signals_earnings.jsonl"))
os.environ.setdefault("RB_EARNINGS_HISTORY_PATH", str(_runtime / "earnings_calls.jsonl"))
# RB-2026-09-06: daily_brief.py's cross-day dedup ledger (sent-loop-
# verification repeats, feeder-signal repeats, decision-persistence
# annotations) had no env-var override -- same class of bug the paths
# above were already fixed for. Confirmed live: repeated test runs against
# the real path had contaminated system/.cache/daily_brief_item_ledger.json
# with fabricated first_seen dates and render counts in the hundreds.
os.environ.setdefault("RB_BRIEF_ITEM_LEDGER_PATH", str(_runtime / "daily_brief_item_ledger.json"))
# RB-2026-09-18 (intelligence-cycle repair): mutation_policy.py is the new
# shared write-decision receipt log, same module-level-constant-at-import-
# time pattern as everything else above -- without this, any test exercising
# a mutation_policy.record_receipt() call (passive RI auto-apply, watchlist
# auto-apply) would append real rows to system/.cache/mutation_policy_receipts.jsonl.
os.environ.setdefault("RB_MUTATION_POLICY_RECEIPTS_PATH", str(_runtime / "mutation_policy_receipts.jsonl"))
# RB-SECURITY-2026-09-03 (server.py's _auth()) made a missing RB_API_KEY
# fail *closed* (503 on every route) instead of the old fail-open behavior
# tests were unknowingly relying on. server.API_KEY is the same kind of
# module-level constant computed once at import time as the paths above --
# set it here, before any test module imports system/api/server.py, so
# TestClient calls exercise real per-route success/validation logic again
# instead of the "server misconfigured" branch. Tests that call
# `client.post(..., headers={"x-api-key": "test-key"})` now authenticate
# for real rather than by accident of fail-open.
#
# RB-DEFECT (2026-09-16): this was `setdefault`, unlike every other var
# above -- fine for those (test-only names, never set outside a test run)
# but wrong for this one, since RB_API_KEY is also a REAL secret that IS
# legitimately set in the pipeline's own environment. self_audit_sweep.py
# runs this exact suite as a subprocess of morning_pipeline.py, which
# inherits the real key from run_with_secrets.py -- so setdefault silently
# kept the real key, every hardcoded `headers={"x-api-key": "test-key"}`
# call stopped matching it, and ~210 API tests failed with 401 the first
# morning this ran unattended. Confirmed live: reproduced by running the
# suite with a real-looking RB_API_KEY already in the environment (mass
# failures, exact same shape); fixed by forcing the value unconditionally
# so the test session's server.API_KEY is always "test-key" regardless of
# what the launching process already set -- this only rebinds the
# in-process os.environ of this pytest subprocess, never the parent
# pipeline process's real key.
os.environ["RB_API_KEY"] = "test-key"

SCRIPTS_DIR = str(Path(__file__).resolve().parents[1] / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import rb_core as _rb_core  # noqa: E402

_RB_TEST_SNAPSHOTS_DIR = _runtime / "_snapshots"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "rb_real_snapshots_dir: opt this test out of the default SNAPSHOTS_DIR "
        "isolation below, for a test that genuinely needs to exercise the real "
        "system/_snapshots/ directory.",
    )


@pytest.fixture(autouse=True)
def _isolate_ecosystem_snapshots_dir(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """RB-DEFECT (2026-09-27): ecosystem_intelligence.py's _write_graph()
    reads core.ECOSYSTEM_INTELLIGENCE_PATH (which every test that needs graph
    isolation already patches, ad hoc, to a per-test tmp file) but copies the
    pre-write snapshot to core.SNAPSHOTS_DIR -- a SEPARATE module-level
    constant no test isolated. Result: a test correctly read/wrote its
    isolated tmp graph, but _write_graph() still copied the resulting
    snapshot into the REAL system/_snapshots/ directory -- confirmed live,
    1085 tiny single-entity test-fixture snapshots (some dated with today's
    timestamp but production-impossible content, e.g. a lone "brand-del-taco"
    entity) accumulated there this way, vs. 65 real multi-MB production
    snapshots. See test_ecosystem_snapshots_dir_isolation.py for the
    regression test and defects/ for the full writeup.

    Rather than requiring 29 affected test files to each grow a matching
    SNAPSHOTS_DIR patch, default every test to a shared per-session tmp
    snapshots dir here so this class of leak can't recur regardless of
    whether a test remembers to isolate it itself. A test that already
    patches core.SNAPSHOTS_DIR (or a module alias of it, e.g. em.SNAPSHOTS_DIR)
    in its own setUp/monkeypatch just overrides this baseline for its
    duration and restores back to it, never to the real path -- no rewrite
    needed. Opt out with @pytest.mark.rb_real_snapshots_dir for a test that
    genuinely needs the real directory (none currently do).
    """
    if request.node.get_closest_marker("rb_real_snapshots_dir"):
        yield
        return
    monkeypatch.setattr(_rb_core, "SNAPSHOTS_DIR", _RB_TEST_SNAPSHOTS_DIR)
    yield


@pytest.fixture(autouse=True)
def _isolate_ecosystem_conflict_queue(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """RB-DEFECT (2026-10-09): ecosystem_intelligence.py's
    _write_conflict_record() reads core.CONFLICT_QUEUE_PATH -- a SEPARATE
    module-level constant from core.ECOSYSTEM_INTELLIGENCE_PATH, which is
    the only one most tests remember to isolate (e.g.
    test_tech_stack_relationship_promotion.py's _IsolatedGraphMixin).
    Confirmed live: every pytest run through that fixture's
    resolve_and_upsert_relationship() calls wrote a synthetic
    brand-blaze-pizza/vendor-oracle "existing claim" conflict record into
    the REAL system/inbox/ecosystem/conflict_queue.jsonl (65 polluted
    entries accumulated this way before cleanup) -- the exact same shape
    of leak as the SNAPSHOTS_DIR defect above.

    Unlike SNAPSHOTS_DIR (where each snapshot gets a unique, timestamp-
    tagged filename, so sharing one directory across the whole session is
    harmless), conflict records all land in ONE shared JSONL file, and
    callers like intelligence_mutation_engine.build_mutation_brief_block()
    and conflict_pattern_monitor.py filter/count by date -- a per-SESSION
    shared file would let one test's write bleed into another test's
    "conflicts detected today" count (confirmed live: a sibling test
    running first inflated this test's count from 1 to 2). So this one is
    isolated per-TEST, via pytest's own function-scoped tmp_path, rather
    than reusing the shared _RB_TEST_* runtime directory.
    """
    monkeypatch.setattr(_rb_core, "CONFLICT_QUEUE_PATH", tmp_path / "conflict_queue.jsonl")
    yield


@pytest.fixture(autouse=True)
def _isolate_loop_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """RB-DEFECT-074: declaration/capture processing now reconciles legacy L-
    loops and writes system/loop_state.json. Tests that exercise those paths
    without their own fixture must never write the live structured-state file
    (same leak class as the conflict_queue.jsonl pollution)."""
    import rb_core as _rb_core
    monkeypatch.setattr(_rb_core, "LOOP_STATE_PATH", tmp_path / "loop_state.json")
    yield


@pytest.fixture(autouse=True)
def _reset_render_intelligence_brief_rendered_this_run():
    """RB-DEFECT-2026-07-08: render_intelligence_brief._rendered_this_run is a
    module-level set used to dedup a URL across sections within a single
    render() call -- but render() is the only caller that resets it. Any test
    that calls _render_headline_section/_fmt_headline/_render_newsletter_inbox
    directly (as most unit tests here do, to avoid exercising the full
    pipeline) leaves whatever URLs it used sitting in that global for the
    rest of the pytest session. Two placeholder URLs as generic as
    "https://example.com/1" collided across unrelated test files once D+
    started also consulting this registry (for its own, legitimate,
    same-run cross-section dedup), silently emptying newsletter output in
    tests that never touched each other's fixtures on purpose.
    """
    try:
        import render_intelligence_brief as _rib
        _rib._rendered_this_run = set()
    except Exception:
        pass
    yield
    try:
        import render_intelligence_brief as _rib
        _rib._rendered_this_run = set()
    except Exception:
        pass
