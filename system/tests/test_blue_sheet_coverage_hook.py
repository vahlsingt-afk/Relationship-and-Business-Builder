"""
test_blue_sheet_coverage_hook.py

RB-2026-08-23: CANONICAL_REGISTRY.yaml's blue_sheets.mutation_owner said
"whenever a canonical-registry mutation touches an account_id, check
blue_sheet_registry.json... this still needs to be written." This is that
wiring -- scoped to coverage detection/logging only, not the field-level
impact_review.process_event() auto-apply, after finding during
implementation that technology_stack[].vendor (free text) doesn't reliably
match ecosystem_intelligence.json's product/vendor_role fields, and
technology_stack[].status uses a different vocabulary than
impact_review.ProposedChange expects -- auto-applying on an unreliable name
match risked writing wrong data into a real account document.

Covers: the crosswalk (entity_id_to_slug), coverage logging for both
activated and unactivated accounts, non-vendor entities correctly no-op,
and that promote_posture/promote_confidence actually call the hook with the
relationship's from_entity_id (not to_entity_id -- the brand side, not the
vendor side).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "blue_sheets" / "_engine"))

import ecosystem_intelligence as ei  # noqa: E402
import common as blue_sheet_common  # noqa: E402


# ---------------------------------------------------------------------------
# Crosswalk + coverage logging (blue_sheets/_engine/common.py)
# ---------------------------------------------------------------------------

def test_entity_id_to_slug_strips_brand_prefix():
    assert blue_sheet_common.entity_id_to_slug("brand-pollo-campero") == "pollo-campero"


def test_entity_id_to_slug_returns_none_for_non_brand_entities():
    """Blue Sheets exist for restaurant-brand accounts, not vendor entities --
    a vendor-side entity_id must never resolve to a slug."""
    assert blue_sheet_common.entity_id_to_slug("vendor-newpos") is None
    assert blue_sheet_common.entity_id_to_slug("") is None
    assert blue_sheet_common.entity_id_to_slug(None) is None


def test_log_coverage_event_writes_expected_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(blue_sheet_common, "ROOT", tmp_path)
    blue_sheet_common.log_coverage_event(
        entity_id="brand-pollo-campero", slug="pollo-campero", activated=True,
        mutation_type="promote_posture", detail="rel-x: provisional -> substantiated",
    )
    log_path = tmp_path / "_portfolio" / "coverage_log.jsonl"
    assert log_path.exists()
    record = json.loads(log_path.read_text().strip())
    assert record["entity_id"] == "brand-pollo-campero"
    assert record["slug"] == "pollo-campero"
    assert record["activated"] is True
    assert record["mutation_type"] == "promote_posture"
    assert "logged_at" in record


# ---------------------------------------------------------------------------
# The hook itself, as wired into ecosystem_intelligence.py
# ---------------------------------------------------------------------------

def _fake_blue_sheet_common(*, activated: bool):
    fake = MagicMock()
    fake.entity_id_to_slug.side_effect = blue_sheet_common.entity_id_to_slug
    fake.is_activated.return_value = activated
    return fake


def test_hook_no_ops_silently_when_blue_sheet_module_unavailable(monkeypatch):
    """RB-2026-08-23 design requirement: a Blue Sheet logging failure must
    never block or fail the ecosystem-intelligence mutation it observes."""
    monkeypatch.setattr(ei, "_blue_sheet_common", None)
    ei._log_blue_sheet_coverage("brand-pollo-campero", mutation_type="promote_posture", detail="x")
    # No exception raised is the assertion.


def test_hook_no_ops_for_non_brand_entity(monkeypatch):
    fake = _fake_blue_sheet_common(activated=True)
    monkeypatch.setattr(ei, "_blue_sheet_common", fake)
    ei._log_blue_sheet_coverage("vendor-newpos", mutation_type="promote_posture", detail="x")
    fake.log_coverage_event.assert_not_called()


def test_hook_logs_coverage_for_activated_account(monkeypatch):
    fake = _fake_blue_sheet_common(activated=True)
    monkeypatch.setattr(ei, "_blue_sheet_common", fake)
    ei._log_blue_sheet_coverage("brand-pollo-campero", mutation_type="promote_posture", detail="rel-x")
    fake.log_coverage_event.assert_called_once_with(
        entity_id="brand-pollo-campero", slug="pollo-campero", activated=True,
        mutation_type="promote_posture", detail="rel-x",
    )


def test_hook_logs_coverage_for_unactivated_account_too():
    """An account with NO Blue Sheet must still be logged -- that absence is
    exactly the fact a future coverage check needs to find."""
    fake = _fake_blue_sheet_common(activated=False)
    import ecosystem_intelligence as ei2
    ei2._blue_sheet_common = fake
    try:
        ei2._log_blue_sheet_coverage("brand-five-guys", mutation_type="promote_confidence", detail="rel-y")
    finally:
        ei2._blue_sheet_common = blue_sheet_common
    fake.log_coverage_event.assert_called_once_with(
        entity_id="brand-five-guys", slug="five-guys", activated=False,
        mutation_type="promote_confidence", detail="rel-y",
    )


def test_hook_swallows_exceptions_from_the_blue_sheet_module(monkeypatch):
    fake = MagicMock()
    fake.entity_id_to_slug.side_effect = RuntimeError("boom")
    monkeypatch.setattr(ei, "_blue_sheet_common", fake)
    ei._log_blue_sheet_coverage("brand-pollo-campero", mutation_type="promote_posture", detail="x")
    # No exception raised is the assertion.


# ---------------------------------------------------------------------------
# Integration: promote_posture/promote_confidence actually call the hook
# with the relationship's from_entity_id (the brand), not to_entity_id
# (the vendor).
# ---------------------------------------------------------------------------

def _empty_graph():
    return {"schema_version": "2.0", "entities": [], "relationships": [], "sources": []}


def _rel(rel_id="rel-brand-pollo-campero-pos-vendor-toast"):
    return {
        "id": rel_id,
        "from_entity_id": "brand-pollo-campero", "to_entity_id": "vendor-toast",
        "relationship_type": "uses_vendor_for_category", "status": "active",
        "domains": ["restaurants"], "category": "pos", "vendor_role": "unknown",
        "product": None, "deployment": {"stage": "unknown", "scope": "unknown", "evidence": "test"},
        "evidence_posture": "provisional",
        "interpretation_scope": "test", "risk": "unknown", "sources": ["src-test"],
        "confidence": {"level": "low"}, "strategic_note": "",
        "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
    }


def test_promote_posture_calls_hook_with_brand_side_entity_id(monkeypatch):
    graph = _empty_graph()
    rel = _rel()
    graph["relationships"].append(rel)

    monkeypatch.setattr(ei, "_read_graph", lambda *a, **kw: graph)
    monkeypatch.setattr(ei, "_write_graph", lambda g, **kw: None)
    monkeypatch.setattr(ei.audit_log, "append_event", lambda **kw: None)
    hook_calls = []
    monkeypatch.setattr(ei, "_log_blue_sheet_coverage",
                         lambda entity_id, **kw: hook_calls.append((entity_id, kw)))
    # RB-2026-08-24: promote_posture/promote_confidence now also call
    # _sync_blue_sheet_technology_stack, which -- unlike _log_blue_sheet_
    # coverage above -- was NOT mocked here before, and this fixture's
    # from_entity_id is the real, activated brand-pollo-campero account.
    # It happened to be a safe no-op only because category="pos" is
    # genuinely ambiguous in the real account.json (matches two rows) --
    # accidental safety, not real isolation. Mock it explicitly so this
    # test can never touch the real dossier regardless of fixture category.
    monkeypatch.setattr(ei, "_sync_blue_sheet_technology_stack", lambda *a, **kw: None)

    class FakeArgs:
        relationship_id = rel["id"]
        new_posture = "partially_substantiated"
        source_title = "Trade magazine confirmation"
        source_type = "credible_trade_reporting"
        source_url = None
        dry_run = False

    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        rc = ei.promote_posture(FakeArgs())

    assert rc == 0
    assert len(hook_calls) == 1
    entity_id, kwargs = hook_calls[0]
    assert entity_id == "brand-pollo-campero"  # the brand, not vendor-toast
    assert kwargs["mutation_type"] == "promote_posture"


def test_promote_confidence_calls_hook_with_brand_side_entity_id(monkeypatch):
    graph = _empty_graph()
    rel = _rel()
    graph["relationships"].append(rel)

    monkeypatch.setattr(ei, "_read_graph", lambda *a, **kw: graph)
    monkeypatch.setattr(ei, "_write_graph", lambda g, **kw: None)
    monkeypatch.setattr(ei.audit_log, "append_event", lambda **kw: None)
    hook_calls = []
    monkeypatch.setattr(ei, "_log_blue_sheet_coverage",
                         lambda entity_id, **kw: hook_calls.append((entity_id, kw)))
    # RB-2026-08-24: promote_posture/promote_confidence now also call
    # _sync_blue_sheet_technology_stack, which -- unlike _log_blue_sheet_
    # coverage above -- was NOT mocked here before, and this fixture's
    # from_entity_id is the real, activated brand-pollo-campero account.
    # It happened to be a safe no-op only because category="pos" is
    # genuinely ambiguous in the real account.json (matches two rows) --
    # accidental safety, not real isolation. Mock it explicitly so this
    # test can never touch the real dossier regardless of fixture category.
    monkeypatch.setattr(ei, "_sync_blue_sheet_technology_stack", lambda *a, **kw: None)

    class FakeArgs:
        relationship_id = rel["id"]
        new_posture = "partially_substantiated"
        source_title = "Trade magazine confirmation"
        source_type = "credible_trade_reporting"
        source_url = None
        dry_run = False

    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        rc = ei.promote_confidence(FakeArgs())

    assert rc == 0
    assert len(hook_calls) == 1
    entity_id, kwargs = hook_calls[0]
    assert entity_id == "brand-pollo-campero"
    assert kwargs["mutation_type"] == "promote_confidence"


# ---------------------------------------------------------------------------
# RB-2026-08-24: match_technology_stack_row / POSTURE_TO_STATUS (common.py)
# ---------------------------------------------------------------------------

def _account_with_layers(*layers: str) -> dict:
    return {"technology_stack": [{"layer": name, "status": "Open"} for name in layers]}


def test_match_technology_stack_row_unambiguous_match():
    account = _account_with_layers("Online ordering", "Loyalty / app")
    assert blue_sheet_common.match_technology_stack_row(account, "online_ordering") == 0
    assert blue_sheet_common.match_technology_stack_row(account, "loyalty") == 1


def test_match_technology_stack_row_ambiguous_category_returns_none():
    """Real Pollo Campero data: 'pos' matches both 'POS software' and 'POS
    hardware / workstations'. The matcher must count actual candidate rows,
    never trust the keyword map alone, and refuse to guess."""
    account = _account_with_layers("POS software", "POS hardware / workstations")
    assert blue_sheet_common.match_technology_stack_row(account, "pos") is None


def test_match_technology_stack_row_ambiguous_payments_returns_none():
    account = _account_with_layers("Above-store digital gateway / payments", "Payment terminal")
    assert blue_sheet_common.match_technology_stack_row(account, "payments") is None


def test_match_technology_stack_row_unmapped_category_returns_none():
    account = _account_with_layers("Online ordering")
    assert blue_sheet_common.match_technology_stack_row(account, "franchise_management") is None


def test_match_technology_stack_row_no_layers_returns_none():
    assert blue_sheet_common.match_technology_stack_row({}, "loyalty") is None


def test_posture_to_status_never_maps_to_active_or_reconcile():
    """Active/Reconcile carry operational meaning beyond evidence confidence
    -- this mapping must never assert either one automatically."""
    mapped_values = set(blue_sheet_common.POSTURE_TO_STATUS.values())
    assert "Active" not in mapped_values
    assert "Reconcile" not in mapped_values


def test_posture_to_status_known_values():
    assert blue_sheet_common.POSTURE_TO_STATUS["provisional"] == "Verify"
    assert blue_sheet_common.POSTURE_TO_STATUS["partially_substantiated"] == "Verify"
    assert blue_sheet_common.POSTURE_TO_STATUS["substantiated"] == "Confirmed"


# ---------------------------------------------------------------------------
# RB-2026-08-24: _sync_blue_sheet_technology_stack (ecosystem_intelligence.py)
# ---------------------------------------------------------------------------

def test_sync_calls_process_event_on_unambiguous_match(monkeypatch):
    fake_common = MagicMock()
    fake_common.entity_id_to_slug.return_value = "pollo-campero"
    fake_common.is_activated.return_value = True
    fake_common.POSTURE_TO_STATUS = {"substantiated": "Confirmed"}
    fake_common.load_json.return_value = {"account_id": "acct-pollo-campero", "technology_stack": []}
    fake_common.match_technology_stack_row.return_value = 2
    fake_common.load_jsonl.return_value = []
    fake_common.next_evidence_id.return_value = "ev-pollo-campero-0099"
    fake_common.today.return_value = "2026-08-24"

    import sys
    real_impact_review = sys.modules.get("impact_review") or __import__("impact_review")
    fake_impact_review = MagicMock()
    fake_impact_review.ProposedChange = real_impact_review.ProposedChange

    monkeypatch.setattr(ei, "_blue_sheet_common", fake_common)
    monkeypatch.setattr(ei, "_blue_sheet_impact_review", fake_impact_review)

    ei._sync_blue_sheet_technology_stack(
        "brand-pollo-campero", category="online_ordering", new_posture="substantiated",
        source_title="Test source", source_url=None,
    )

    fake_impact_review.process_event.assert_called_once()
    call_args = fake_impact_review.process_event.call_args
    slug, changes, evidence = call_args.args[0], call_args.args[1], call_args.args[2]
    assert slug == "pollo-campero"
    assert call_args.kwargs.get("apply") is True or (len(call_args.args) > 3 and call_args.args[3] is True)
    assert len(changes) == 1
    assert changes[0].json_path == "technology_stack[2].status"
    assert changes[0].new_value == "Confirmed"


def test_sync_no_ops_when_no_unambiguous_match(monkeypatch):
    fake_common = MagicMock()
    fake_common.entity_id_to_slug.return_value = "pollo-campero"
    fake_common.is_activated.return_value = True
    fake_common.POSTURE_TO_STATUS = {"substantiated": "Confirmed"}
    fake_common.load_json.return_value = {"account_id": "acct-pollo-campero", "technology_stack": []}
    fake_common.match_technology_stack_row.return_value = None  # ambiguous or unmapped

    fake_impact_review = MagicMock()
    monkeypatch.setattr(ei, "_blue_sheet_common", fake_common)
    monkeypatch.setattr(ei, "_blue_sheet_impact_review", fake_impact_review)

    ei._sync_blue_sheet_technology_stack(
        "brand-pollo-campero", category="pos", new_posture="substantiated",
        source_title="Test source", source_url=None,
    )

    fake_impact_review.process_event.assert_not_called()


def test_sync_no_ops_when_posture_not_in_translation_table(monkeypatch):
    """e.g. 'unknown' or 'conflicting' postures have no status mapping --
    must fall through cleanly, never attempt a lookup with a missing key."""
    fake_common = MagicMock()
    fake_common.POSTURE_TO_STATUS = {"substantiated": "Confirmed"}
    fake_impact_review = MagicMock()
    monkeypatch.setattr(ei, "_blue_sheet_common", fake_common)
    monkeypatch.setattr(ei, "_blue_sheet_impact_review", fake_impact_review)

    ei._sync_blue_sheet_technology_stack(
        "brand-pollo-campero", category="pos", new_posture="provisional",
        source_title="Test source", source_url=None,
    )

    fake_common.is_activated.assert_not_called()  # short-circuited before even checking activation
    fake_impact_review.process_event.assert_not_called()


def test_sync_no_ops_for_unactivated_account(monkeypatch):
    fake_common = MagicMock()
    fake_common.entity_id_to_slug.return_value = "five-guys"
    fake_common.is_activated.return_value = False
    fake_common.POSTURE_TO_STATUS = {"substantiated": "Confirmed"}
    fake_impact_review = MagicMock()
    monkeypatch.setattr(ei, "_blue_sheet_common", fake_common)
    monkeypatch.setattr(ei, "_blue_sheet_impact_review", fake_impact_review)

    ei._sync_blue_sheet_technology_stack(
        "brand-five-guys", category="pos", new_posture="substantiated",
        source_title="Test source", source_url=None,
    )

    fake_impact_review.process_event.assert_not_called()


def test_sync_swallows_exceptions_and_never_raises(monkeypatch):
    fake_common = MagicMock()
    fake_common.entity_id_to_slug.side_effect = RuntimeError("boom")
    fake_common.POSTURE_TO_STATUS = {"substantiated": "Confirmed"}
    fake_impact_review = MagicMock()
    monkeypatch.setattr(ei, "_blue_sheet_common", fake_common)
    monkeypatch.setattr(ei, "_blue_sheet_impact_review", fake_impact_review)

    # No exception raised is the assertion.
    ei._sync_blue_sheet_technology_stack(
        "brand-pollo-campero", category="pos", new_posture="substantiated",
        source_title="Test source", source_url=None,
    )
    fake_impact_review.process_event.assert_not_called()


def test_sync_no_ops_when_blue_sheet_modules_unavailable(monkeypatch):
    monkeypatch.setattr(ei, "_blue_sheet_common", None)
    monkeypatch.setattr(ei, "_blue_sheet_impact_review", None)
    # No exception raised is the assertion.
    ei._sync_blue_sheet_technology_stack(
        "brand-pollo-campero", category="pos", new_posture="substantiated",
        source_title="Test source", source_url=None,
    )
