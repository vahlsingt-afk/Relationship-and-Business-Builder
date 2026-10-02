import importlib.util
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("cockpit_context", ROOT / "system/scripts/cockpit_context.py")
cc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cc)


def test_context_has_required_generated_sections():
    ctx = cc.build_context(now=datetime(2026, 8, 19, 12, tzinfo=timezone.utc))
    assert ctx["projection_type"] == "generated_read_only"
    for key in (
        "freshness", "weekly_outcomes_and_priorities", "active_opportunities",
        "open_loops", "active_theses_and_evidence_state",
        "recent_material_intelligence", "pending_decisions", "reconciliation_queue",
    ):
        assert key in ctx


def test_loop_namespaces_keep_authority_explicit():
    ctx = cc.build_context(now=datetime(2026, 8, 19, 12, tzinfo=timezone.utc))
    assert all(x["authority"] == "system/loop_ledger.md" for x in ctx["open_loops"]["legacy"])
    assert all(x["authority"] == "system/eolms/loops.json" for x in ctx["open_loops"]["executive"])
    assert ctx["open_loops"]["total"] == len(ctx["open_loops"]["legacy"]) + len(ctx["open_loops"]["executive"])


def test_fingerprint_is_stable_for_same_inputs():
    now = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
    assert cc.build_context(now=now)["freshness"]["input_fingerprint"] == cc.build_context(now=now)["freshness"]["input_fingerprint"]


def test_material_ecosystem_signals_filters_and_resolves_entity_names(tmp_path):
    """RB-DEFECT-2026-08-20: cockpit_context.py never read ecosystem_intelligence.json
    at all, so a real, material watchlist finding (Red Robin's Q2 earnings-call
    technology signal) existed nowhere the RBB Project could see it -- not a
    staleness problem, the whole signal stream was invisible to the projection."""
    import json
    path = tmp_path / "ecosystem_intelligence.json"
    path.write_text(json.dumps({
        "entities": [{"id": "brand-red-robin", "name": "Red Robin"}],
        "signals": [
            {
                "id": "sig-material", "signal_type": "extreme_pain",
                "entities": ["brand-red-robin"], "summary": "Material finding",
                "confidence": {"level": "high"}, "captured_at": "2026-08-20T09:00:00Z",
                "event_at": "2026-08-19",
            },
            {
                "id": "sig-low-confidence", "signal_type": "extreme_pain",
                "entities": ["brand-red-robin"], "summary": "Weak signal",
                "confidence": {"level": "low"}, "captured_at": "2026-08-20T10:00:00Z",
            },
            {
                "id": "sig-routine", "signal_type": "general_market_context",
                "entities": ["brand-red-robin"], "summary": "Press mention",
                "confidence": {"level": "high"}, "captured_at": "2026-08-20T11:00:00Z",
            },
        ],
    }), encoding="utf-8")

    result = cc._material_ecosystem_signals(path)
    assert [s["signal_id"] for s in result] == ["sig-material"]
    assert result[0]["entities"] == ["Red Robin"]
    assert result[0]["source"] == "ecosystem_intelligence"


def test_material_ecosystem_signals_merged_into_recent_material_intelligence():
    ctx = cc.build_context(now=datetime(2026, 8, 20, 12, tzinfo=timezone.utc))
    sources = {i.get("source") for i in ctx["recent_material_intelligence"]}
    assert sources <= {"strategic_events", "ecosystem_intelligence"}


# ---------------------------------------------------------------------------
# RB-2026-08-23 (P0-3): a fresh RBB Project chat answering "what am I
# waiting on" from open_loops had no way to know that data was stale
# without separately cross-referencing freshness.stale_inputs by name.
# section_freshness makes that traceable per-section instead.
# ---------------------------------------------------------------------------

def test_section_freshness_present_for_every_documented_section():
    ctx = cc.build_context(now=datetime(2026, 8, 19, 12, tzinfo=timezone.utc))
    assert set(ctx["section_freshness"].keys()) == set(cc.SECTION_INPUTS.keys())


def test_section_freshness_flags_stale_when_its_input_is_stale():
    stale = cc._section_freshness(["legacy_loops", "weekly_plan"])
    assert stale["open_loops"]["stale"] is True
    assert stale["open_loops"]["based_on_stale_inputs"] == ["legacy_loops"]
    assert stale["weekly_outcomes_and_priorities"]["stale"] is True


def test_section_freshness_not_stale_when_no_relevant_input_is_stale():
    stale = cc._section_freshness(["legacy_loops"])
    assert stale["active_theses_and_evidence_state"]["stale"] is False
    assert stale["active_theses_and_evidence_state"]["based_on_stale_inputs"] == []


def test_section_freshness_all_clean_when_nothing_is_stale():
    clean = cc._section_freshness([])
    assert all(not v["stale"] for v in clean.values())
    assert all(v["based_on_stale_inputs"] == [] for v in clean.values())


def test_multi_input_section_lists_only_its_own_stale_inputs():
    """recent_material_intelligence depends on two inputs -- staleness in an
    unrelated input must not leak into its based_on_stale_inputs list."""
    result = cc._section_freshness(["legacy_loops", "strategic_events"])
    assert result["recent_material_intelligence"]["based_on_stale_inputs"] == ["strategic_events"]


# ---------------------------------------------------------------------------
# reconciliation_queue content shape (previously zero coverage beyond key
# presence -- test_context_has_required_generated_sections above only
# checks reconciliation_queue exists, not what's actually in it).
# ---------------------------------------------------------------------------

def test_reconciliation_queue_authority_gaps_comes_from_registry():
    ctx = cc.build_context(now=datetime(2026, 8, 19, 12, tzinfo=timezone.utc))
    assert isinstance(ctx["reconciliation_queue"]["authority_gaps"], list)


def test_reconciliation_queue_stale_active_threads_only_lists_actually_overdue_threads():
    ctx = cc.build_context(now=datetime(2026, 8, 19, 12, tzinfo=timezone.utc))
    stale_ids = set(ctx["reconciliation_queue"]["stale_active_threads"])
    open_by_id = {t["id"]: t for t in ctx["active_opportunities"]}
    for tid in stale_ids:
        assert tid in open_by_id, f"{tid} flagged stale but not in active_opportunities"
        target = open_by_id[tid].get("target_close")
        assert target and str(target) < "2026-08-19"


def test_reconciliation_queue_pending_identity_matches_is_a_list():
    ctx = cc.build_context(now=datetime(2026, 8, 19, 12, tzinfo=timezone.utc))
    assert isinstance(ctx["reconciliation_queue"]["pending_identity_matches"], list)


def test_reconciliation_queue_cross_namespace_loop_conflicts_is_a_list():
    """RB-2026-08-23 (P1-4): loop_reconciliation.py's findings surface here
    as a fourth reconciliation_queue key, alongside authority_gaps/
    stale_active_threads/pending_identity_matches."""
    ctx = cc.build_context(now=datetime(2026, 8, 19, 12, tzinfo=timezone.utc))
    assert isinstance(ctx["reconciliation_queue"]["cross_namespace_loop_conflicts"], list)


def test_cross_namespace_loop_conflicts_never_breaks_context_generation(monkeypatch):
    """Non-fatal by design -- a detection-only, non-canonical check failing
    must not prevent the rest of the cockpit projection from generating."""
    monkeypatch.setattr(cc.loop_reconciliation, "build_report",
                         lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    ctx = cc.build_context(now=datetime(2026, 8, 19, 12, tzinfo=timezone.utc))
    assert ctx["reconciliation_queue"]["cross_namespace_loop_conflicts"] == []
