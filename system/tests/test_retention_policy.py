#!/usr/bin/env python3
"""
Tests for RB 9.13 — retention_policy.py

Test IDs and coverage:

RB-RETAIN-001: all seven retention classes are defined
RB-RETAIN-002: ephemeral_raw window is 24 hours
RB-RETAIN-003: source_cache window is 72 hours
RB-RETAIN-004: derived_intelligence window is 90 days
RB-RETAIN-005: durable_memory is indefinite
RB-RETAIN-006: audit_log window is 365 days
RB-RETAIN-007: user_exportable is indefinite
RB-RETAIN-008: forgettable is indefinite (per-request)

RB-EXPIRY-001: old ephemeral_raw item is expired
RB-EXPIRY-002: old source_cache item is expired
RB-EXPIRY-003: old derived_intelligence item is expired
RB-EXPIRY-004: durable_memory never expires
RB-EXPIRY-005: user_exportable never expires
RB-EXPIRY-006: future-dated item is not expired
RB-EXPIRY-007: expiry_at returns correct datetime
RB-EXPIRY-008: expiry_at returns None for indefinite classes

RB-DELETE-001: ephemeral_raw is deletable
RB-DELETE-002: source_cache is deletable
RB-DELETE-003: derived_intelligence is deletable
RB-DELETE-004: durable_memory is deletable
RB-DELETE-005: audit_log is NOT deletable
RB-DELETE-006: user_exportable is deletable
RB-DELETE-007: forgettable is deletable
RB-DELETE-008: audit_log is append-only

RB-FORGET-001: forgettable requires forget flow
RB-FORGET-002: durable_memory requires forget flow
RB-FORGET-003: user_exportable requires forget flow
RB-FORGET-004: ephemeral_raw does not require forget flow
RB-FORGET-005: unconfirmed forget request is invalid
RB-FORGET-006: forget request for audit_log items is blocked
RB-FORGET-007: confirmed forget request for deletable items is valid
RB-FORGET-008: tombstone does not contain raw content fields
RB-FORGET-009: tombstone contains deletion metadata

RB-ASSIGN-001: assign_retention_class respects explicit retention_class field
RB-ASSIGN-002: assign_retention_class falls back to data_class default
RB-ASSIGN-003: assign_retention_class uses ephemeral_raw for unknown data_class
RB-ASSIGN-004: data class raw_source defaults to ephemeral_raw
RB-ASSIGN-005: data class memory defaults to durable_memory
RB-ASSIGN-006: data class audit defaults to audit_log
RB-ASSIGN-007: data class intelligence defaults to derived_intelligence
RB-ASSIGN-008: data class normalized defaults to source_cache
RB-ASSIGN-009: data class summaries defaults to user_exportable

RB-PREVIEW-001: forget preview returns item_summary for each item
RB-PREVIEW-002: forget preview marks audit_log items as non-deletable
RB-PREVIEW-003: forget preview includes retention_class for each item
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import retention_policy as rp  # noqa: E402


# ---------------------------------------------------------------------------
# RB-RETAIN: Retention class definitions
# ---------------------------------------------------------------------------

def test_all_seven_classes_defined():
    """RB-RETAIN-001: all seven retention classes are defined."""
    expected = {
        "ephemeral_raw",
        "source_cache",
        "derived_intelligence",
        "durable_memory",
        "audit_log",
        "user_exportable",
        "forgettable",
    }
    assert expected == rp.ALL_CLASS_NAMES, (
        f"Missing classes: {expected - rp.ALL_CLASS_NAMES}; "
        f"Extra classes: {rp.ALL_CLASS_NAMES - expected}"
    )


def test_ephemeral_raw_window():
    """RB-RETAIN-002: ephemeral_raw window is 24 hours."""
    assert rp.RETENTION_CLASSES["ephemeral_raw"].window_hours == 24


def test_source_cache_window():
    """RB-RETAIN-003: source_cache window is 72 hours."""
    assert rp.RETENTION_CLASSES["source_cache"].window_hours == 72


def test_derived_intelligence_window():
    """RB-RETAIN-004: derived_intelligence window is 90 days."""
    assert rp.RETENTION_CLASSES["derived_intelligence"].window_hours == 90 * 24


def test_durable_memory_indefinite():
    """RB-RETAIN-005: durable_memory is indefinite."""
    assert rp.RETENTION_CLASSES["durable_memory"].window_hours is None


def test_audit_log_window():
    """RB-RETAIN-006: audit_log window is 365 days."""
    assert rp.RETENTION_CLASSES["audit_log"].window_hours == 365 * 24


def test_user_exportable_indefinite():
    """RB-RETAIN-007: user_exportable is indefinite."""
    assert rp.RETENTION_CLASSES["user_exportable"].window_hours is None


def test_forgettable_indefinite():
    """RB-RETAIN-008: forgettable is indefinite (deleted on request, not by timer)."""
    assert rp.RETENTION_CLASSES["forgettable"].window_hours is None


# ---------------------------------------------------------------------------
# RB-EXPIRY: Expiry detection
# ---------------------------------------------------------------------------

OLD_TS = "2020-01-01T00:00:00+00:00"
FUTURE_TS = "2099-01-01T00:00:00+00:00"


def test_expired_ephemeral_raw():
    """RB-EXPIRY-001: very old ephemeral_raw item is expired."""
    assert rp.is_expired("ephemeral_raw", OLD_TS)


def test_expired_source_cache():
    """RB-EXPIRY-002: very old source_cache item is expired."""
    assert rp.is_expired("source_cache", OLD_TS)


def test_expired_derived_intelligence():
    """RB-EXPIRY-003: very old derived_intelligence item is expired."""
    assert rp.is_expired("derived_intelligence", OLD_TS)


def test_not_expired_durable_memory():
    """RB-EXPIRY-004: durable_memory never expires."""
    assert not rp.is_expired("durable_memory", OLD_TS)
    assert not rp.is_expired("durable_memory", FUTURE_TS)


def test_not_expired_user_exportable():
    """RB-EXPIRY-005: user_exportable never expires."""
    assert not rp.is_expired("user_exportable", OLD_TS)


def test_future_not_expired():
    """RB-EXPIRY-006: future-dated ephemeral_raw item is not expired."""
    assert not rp.is_expired("ephemeral_raw", FUTURE_TS)


def test_expiry_at_returns_datetime():
    """RB-EXPIRY-007: expiry_at returns correct datetime for windowed classes."""
    ts = "2026-05-27T10:00:00+00:00"
    exp = rp.expiry_at("ephemeral_raw", ts)
    assert exp is not None
    stored_dt = datetime.fromisoformat(ts)
    expected_exp = stored_dt + timedelta(hours=24)
    assert exp == expected_exp


def test_expiry_at_indefinite():
    """RB-EXPIRY-008: expiry_at returns None for indefinite classes."""
    assert rp.expiry_at("durable_memory", OLD_TS) is None
    assert rp.expiry_at("user_exportable", OLD_TS) is None
    assert rp.expiry_at("forgettable", OLD_TS) is None


# ---------------------------------------------------------------------------
# RB-DELETE: Deletability
# ---------------------------------------------------------------------------

def test_ephemeral_raw_deletable():
    """RB-DELETE-001: ephemeral_raw is deletable."""
    assert rp.can_delete("ephemeral_raw")


def test_source_cache_deletable():
    """RB-DELETE-002: source_cache is deletable."""
    assert rp.can_delete("source_cache")


def test_derived_intelligence_deletable():
    """RB-DELETE-003: derived_intelligence is deletable."""
    assert rp.can_delete("derived_intelligence")


def test_durable_memory_deletable():
    """RB-DELETE-004: durable_memory is deletable (via forget flow)."""
    assert rp.can_delete("durable_memory")


def test_audit_log_not_deletable():
    """RB-DELETE-005: audit_log is NOT deletable."""
    assert not rp.can_delete("audit_log")


def test_user_exportable_deletable():
    """RB-DELETE-006: user_exportable is deletable."""
    assert rp.can_delete("user_exportable")


def test_forgettable_deletable():
    """RB-DELETE-007: forgettable is deletable."""
    assert rp.can_delete("forgettable")


def test_audit_log_append_only():
    """RB-DELETE-008: audit_log is append-only."""
    assert rp.is_append_only("audit_log")


def test_other_classes_not_append_only():
    """RB-DELETE-008b: other classes are not append-only."""
    for name in rp.ALL_CLASS_NAMES:
        if name != "audit_log":
            assert not rp.is_append_only(name), f"{name} should not be append-only"


# ---------------------------------------------------------------------------
# RB-FORGET: Forget flow
# ---------------------------------------------------------------------------

def test_forgettable_requires_forget_flow():
    """RB-FORGET-001: forgettable requires forget flow."""
    assert rp.requires_forget_flow("forgettable")


def test_durable_memory_requires_forget_flow():
    """RB-FORGET-002: durable_memory requires forget flow."""
    assert rp.requires_forget_flow("durable_memory")


def test_user_exportable_requires_forget_flow():
    """RB-FORGET-003: user_exportable requires forget flow."""
    assert rp.requires_forget_flow("user_exportable")


def test_ephemeral_raw_no_forget_flow():
    """RB-FORGET-004: ephemeral_raw does not require forget flow (auto-purged)."""
    assert not rp.requires_forget_flow("ephemeral_raw")


def test_unconfirmed_forget_request_invalid():
    """RB-FORGET-005: unconfirmed forget request is invalid."""
    items = [{"data_class": "memory", "item_summary": "contact fact"}]
    req = rp.ForgetRequest(scope="contact", scope_value="Olivia Nielsen", confirmed=False)
    valid, errors = rp.validate_forget_request(req, items)
    assert not valid
    assert any("confirmed" in e.lower() for e in errors)


def test_forget_request_for_audit_log_blocked():
    """RB-FORGET-006: forget request for audit_log items is blocked."""
    items = [{"data_class": "audit", "item_summary": "access event"}]
    req = rp.ForgetRequest(scope="all", scope_value="*", confirmed=True)
    valid, errors = rp.validate_forget_request(req, items)
    assert not valid
    assert any("append-only" in e.lower() for e in errors)


def test_confirmed_forget_request_valid():
    """RB-FORGET-007: confirmed forget request for deletable items is valid."""
    items = [
        {"data_class": "memory", "item_summary": "contact fact"},
        {"data_class": "intelligence", "item_summary": "opportunity signal"},
    ]
    req = rp.ForgetRequest(scope="contact", scope_value="Olivia Nielsen", confirmed=True)
    valid, errors = rp.validate_forget_request(req, items)
    assert valid, f"Expected valid, got errors: {errors}"


def test_tombstone_no_raw_content():
    """RB-FORGET-008: tombstone does not contain raw content fields."""
    item = {
        "data_class": "memory",
        "item_summary": "Olivia Nielsen / PerfectHire opportunity",
        "full_text": "This is raw transcript content",  # should NOT appear in tombstone
        "source": "ri_events",
        "event_id": "RIE-12345",
    }
    tombstone = rp.make_tombstone(item)
    assert "full_text" not in tombstone, "Tombstone must not contain raw content fields"
    assert "item_summary" not in tombstone, "Tombstone must not contain item_summary"
    assert tombstone["tombstone"] is True


def test_tombstone_contains_metadata():
    """RB-FORGET-009: tombstone contains deletion metadata."""
    item = {
        "data_class": "memory",
        "source": "ri_events",
        "event_id": "RIE-12345",
    }
    tombstone = rp.make_tombstone(item, reason="user_forget_request")
    assert tombstone["tombstone"] is True
    assert tombstone["reason"] == "user_forget_request"
    assert tombstone["original_data_class"] == "memory"
    assert "deleted_at" in tombstone


# ---------------------------------------------------------------------------
# RB-ASSIGN: Retention class assignment
# ---------------------------------------------------------------------------

def test_assign_respects_explicit_field():
    """RB-ASSIGN-001: assign_retention_class respects explicit retention_class field."""
    item = {"retention_class": "forgettable", "data_class": "raw_source"}
    assert rp.assign_retention_class(item) == "forgettable"


def test_assign_falls_back_to_data_class():
    """RB-ASSIGN-002: assign_retention_class falls back to data_class default."""
    item = {"data_class": "intelligence"}
    assert rp.assign_retention_class(item) == "derived_intelligence"


def test_assign_unknown_data_class_is_ephemeral():
    """RB-ASSIGN-003: assign_retention_class uses ephemeral_raw for unknown data_class."""
    item = {"data_class": "unknown"}
    assert rp.assign_retention_class(item) == "ephemeral_raw"


def test_assign_raw_source_default():
    """RB-ASSIGN-004: data class raw_source defaults to ephemeral_raw."""
    item = {"data_class": "raw_source"}
    assert rp.assign_retention_class(item) == "ephemeral_raw"


def test_assign_memory_default():
    """RB-ASSIGN-005: data class memory defaults to durable_memory."""
    item = {"data_class": "memory"}
    assert rp.assign_retention_class(item) == "durable_memory"


def test_assign_audit_default():
    """RB-ASSIGN-006: data class audit defaults to audit_log."""
    item = {"data_class": "audit"}
    assert rp.assign_retention_class(item) == "audit_log"


def test_assign_intelligence_default():
    """RB-ASSIGN-007: data class intelligence defaults to derived_intelligence."""
    item = {"data_class": "intelligence"}
    assert rp.assign_retention_class(item) == "derived_intelligence"


def test_assign_normalized_default():
    """RB-ASSIGN-008: data class normalized defaults to source_cache."""
    item = {"data_class": "normalized"}
    assert rp.assign_retention_class(item) == "source_cache"


def test_assign_summaries_default():
    """RB-ASSIGN-009: data class summaries defaults to user_exportable."""
    item = {"data_class": "summaries"}
    assert rp.assign_retention_class(item) == "user_exportable"


# ---------------------------------------------------------------------------
# RB-PREVIEW: Forget preview
# ---------------------------------------------------------------------------

def test_forget_preview_has_item_summaries():
    """RB-PREVIEW-001: forget preview returns item_summary for each item."""
    items = [
        {"item_summary": "Contact fact about Olivia", "data_class": "memory"},
        {"item_summary": "Signal from meeting", "data_class": "intelligence"},
    ]
    preview = rp.build_forget_preview(items)
    assert len(preview) == 2
    for p in preview:
        assert "item_summary" in p


def test_forget_preview_marks_audit_not_deletable():
    """RB-PREVIEW-002: forget preview marks audit_log items as non-deletable."""
    items = [
        {"item_summary": "Audit event", "data_class": "audit"},
    ]
    preview = rp.build_forget_preview(items)
    assert len(preview) == 1
    assert not preview[0]["deletable"]


def test_forget_preview_includes_retention_class():
    """RB-PREVIEW-003: forget preview includes retention_class for each item."""
    items = [
        {"item_summary": "Memory item", "data_class": "memory"},
        {"item_summary": "Intelligence item", "data_class": "intelligence"},
    ]
    preview = rp.build_forget_preview(items)
    for p in preview:
        assert "retention_class" in p
    assert preview[0]["retention_class"] == "durable_memory"
    assert preview[1]["retention_class"] == "derived_intelligence"


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback
    passed = 0
    failed = 0

    test_fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in test_fns:
        try:
            fn()
            print(f"  PASS: {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {fn.__name__} — {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {fn.__name__} — {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
