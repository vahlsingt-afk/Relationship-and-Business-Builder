#!/usr/bin/env python3
"""
Tests for relationship_health_aggregate.py — persisting metadata-derived
relationship health onto baseline_index.json (Metadata-First Connector,
RB Phase 1).

Test IDs and coverage:

RB-RELHEALTH-001: overdue thread → outstanding_follow_ups_count >= 1, timestamp recorded
RB-RELHEALTH-002: awaiting thread → outstanding_follow_ups_count >= 1, awaiting timestamp recorded
RB-RELHEALTH-003: drr_score in block matches calling rb_core.drr_score() directly
RB-RELHEALTH-004: contact with no signals gets a zeroed, non-null block
RB-RELHEALTH-005: RFC 2822 sent_at timestamps are parsed correctly (not naive ISO slicing)
RB-RELHEALTH-006: running compute twice is idempotent (only computed_at differs)
RB-RELHEALTH-007: no value in any block exceeds privacy_guard.MAX_EXCERPT_LEN (no leaked content)
RB-RELHEALTH-008: apply_to_baseline() writes relationship_health and snapshots first (tmp paths only)
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import relationship_health_aggregate as rha  # noqa: E402
import privacy_guard as pg  # noqa: E402


def _baseline():
    return [
        {
            "id": "jane-doe",
            "name": "Jane Doe",
            "current_company": "Acme Inc",
            "signal_class": "RC",
            "rc_state": "ACTIVE",
            "rc_tier": "inner",
            "last_touch": "2026-07-01",
            "circles": ["circle-a"],
            "email": "jane@acme.com",
            "phone": None,
            "sources": ["manual"],
        },
        {
            "id": "john-silent",
            "name": "John Silent",
            "current_company": "Beta Co",
            "signal_class": "LKI",
            "rc_state": None,
            "rc_tier": None,
            "last_touch": "2025-01-01",
            "circles": [],
            "email": "john@beta.com",
            "phone": None,
            "sources": [],
        },
    ]


def test_overdue_thread_recorded():
    """RB-RELHEALTH-001."""
    today = date(2026, 7, 6)
    overlay = {
        "sent_followups": [
            {
                "response_status": "response_overdue",
                "sent_at": "2026-06-25T00:00:00+00:00",
                "matched_contacts": [{"id": "jane-doe"}],
            },
        ],
        "from_baseline": [],
    }
    result = rha.compute_relationship_health(
        _baseline(), today=today, threads=[], email_overlay_data=overlay,
        interaction_overlay_data={"matched_contacts": []},
    )
    assert result["jane-doe"]["outstanding_follow_ups_count"] == 1
    assert result["jane-doe"]["last_response_overdue_at"] == "2026-06-25T00:00:00+00:00"
    assert result["jane-doe"]["last_awaiting_response_at"] is None


def test_awaiting_thread_recorded():
    """RB-RELHEALTH-002."""
    today = date(2026, 7, 6)
    overlay = {
        "sent_followups": [
            {
                "response_status": "awaiting_response",
                "sent_at": "2026-07-02T00:00:00+00:00",
                "matched_contacts": [{"id": "jane-doe"}],
            },
        ],
        "from_baseline": [],
    }
    result = rha.compute_relationship_health(
        _baseline(), today=today, threads=[], email_overlay_data=overlay,
        interaction_overlay_data={"matched_contacts": []},
    )
    assert result["jane-doe"]["outstanding_follow_ups_count"] == 1
    assert result["jane-doe"]["last_awaiting_response_at"] == "2026-07-02T00:00:00+00:00"
    assert result["jane-doe"]["last_response_overdue_at"] is None


def test_drr_score_matches_direct_call():
    """RB-RELHEALTH-003."""
    import rb_core as core

    today = date(2026, 7, 6)
    baseline = _baseline()
    result = rha.compute_relationship_health(
        baseline, today=today, threads=[],
        email_overlay_data={"sent_followups": [], "from_baseline": []},
        interaction_overlay_data={"matched_contacts": []},
    )
    direct = core.drr_score(baseline[0], today, threads=[])
    assert result["jane-doe"]["drr_score"] == direct["score"]
    assert result["jane-doe"]["drr_components"] == direct["components"]


def test_contact_with_no_signals_gets_zeroed_block():
    """RB-RELHEALTH-004."""
    today = date(2026, 7, 6)
    result = rha.compute_relationship_health(
        _baseline(), today=today, threads=[],
        email_overlay_data={"sent_followups": [], "from_baseline": []},
        interaction_overlay_data={"matched_contacts": []},
    )
    block = result["john-silent"]
    assert block["outstanding_follow_ups_count"] == 0
    assert block["last_response_overdue_at"] is None
    assert block["last_awaiting_response_at"] is None
    assert block["communication_frequency_90d"] == 0.0
    assert block["source"] == "relationship_health_aggregate.py"


def test_rfc2822_sent_at_parsed_correctly():
    """RB-RELHEALTH-005: Gmail thread-list headers arrive as RFC 2822
    ("Wed, 10 Jun 2026 05:19:36 -0500"), not ISO 8601. String-slicing the
    first 10 chars (a naive ISO assumption) would silently drop these."""
    today = date(2026, 7, 6)
    overlay = {
        "sent_followups": [
            {
                "response_status": "response_overdue",
                "sent_at": "Wed, 10 Jun 2026 05:19:36 -0500",
                "matched_contacts": [{"id": "jane-doe"}],
            },
        ],
        "from_baseline": [],
    }
    result = rha.compute_relationship_health(
        _baseline(), today=today, threads=[], email_overlay_data=overlay,
        interaction_overlay_data={"matched_contacts": []},
    )
    assert result["jane-doe"]["outstanding_follow_ups_count"] == 1
    # Persisted timestamp should be normalized to ISO 8601, not the raw RFC 2822 string.
    assert result["jane-doe"]["last_response_overdue_at"] == "2026-06-10T10:19:36+00:00"


def test_later_iso_compares_chronologically_not_lexically():
    """RB-RELHEALTH-005b: 'Fri, 26 Jun 2026' is chronologically later than
    'Wed, 10 Jun 2026' but alphabetically earlier ('F' < 'W') — the latest-
    timestamp tracking must not use raw string comparison."""
    today = date(2026, 7, 6)
    overlay = {
        "sent_followups": [
            {
                "response_status": "response_overdue",
                "sent_at": "Wed, 10 Jun 2026 05:19:36 -0500",
                "matched_contacts": [{"id": "jane-doe"}],
            },
            {
                "response_status": "response_overdue",
                "sent_at": "Fri, 26 Jun 2026 13:10:12 -0500",
                "matched_contacts": [{"id": "jane-doe"}],
            },
        ],
        "from_baseline": [],
    }
    result = rha.compute_relationship_health(
        _baseline(), today=today, threads=[], email_overlay_data=overlay,
        interaction_overlay_data={"matched_contacts": []},
    )
    assert result["jane-doe"]["last_response_overdue_at"] == "2026-06-26T18:10:12+00:00"


def test_idempotent_across_runs():
    """RB-RELHEALTH-006."""
    today = date(2026, 7, 6)
    overlay = {
        "sent_followups": [
            {
                "response_status": "response_overdue",
                "sent_at": "2026-06-25T00:00:00+00:00",
                "matched_contacts": [{"id": "jane-doe"}],
            },
        ],
        "from_baseline": [{"match": {"id": "jane-doe"}, "last_message_at": "2026-07-01T00:00:00+00:00"}],
    }
    interaction = {
        "matched_contacts": [
            {"id": "jane-doe", "messages_in": 2, "messages_out": 1, "calls_in": 0, "calls_out": 0, "calls_missed": 0},
        ],
    }
    baseline = _baseline()
    r1 = rha.compute_relationship_health(baseline, today=today, threads=[], email_overlay_data=overlay, interaction_overlay_data=interaction)
    r2 = rha.compute_relationship_health(baseline, today=today, threads=[], email_overlay_data=overlay, interaction_overlay_data=interaction)
    for cid in ("jane-doe", "john-silent"):
        a, b = dict(r1[cid]), dict(r2[cid])
        a.pop("computed_at", None)
        b.pop("computed_at", None)
        assert a == b, f"non-idempotent output for {cid}"


def test_no_leaked_content_in_any_block():
    """RB-RELHEALTH-007."""
    today = date(2026, 7, 6)
    overlay = {
        "sent_followups": [
            {
                "response_status": "response_overdue",
                "sent_at": "2026-06-25T00:00:00+00:00",
                "matched_contacts": [{"id": "jane-doe"}],
            },
        ],
        "from_baseline": [],
    }
    result = rha.compute_relationship_health(
        _baseline(), today=today, threads=[], email_overlay_data=overlay,
        interaction_overlay_data={"matched_contacts": []},
    )
    for cid, block in result.items():
        for v in block.values():
            if isinstance(v, str):
                assert len(v) <= pg.MAX_EXCERPT_LEN, f"{cid} block value exceeds MAX_EXCERPT_LEN: {v!r}"
            assert not isinstance(v, dict) or all(
                isinstance(vv, (int, float)) for vv in v.values()
            ), f"{cid} drr_components should only contain numeric values"


def test_apply_to_baseline_writes_and_snapshots(monkeypatch, tmp_path):
    """RB-RELHEALTH-008: exercises the real write path, but entirely against
    tmp_path — must never touch the real baseline_index.json or system/_snapshots/."""
    import rb_core as core
    import mutations

    baseline_path = tmp_path / "baseline_index.json"
    baseline_path.write_text(json.dumps(_baseline()))
    snapshots_dir = tmp_path / "_snapshots"

    monkeypatch.setattr(core, "SNAPSHOTS_DIR", snapshots_dir)
    monkeypatch.setattr(mutations.core, "SNAPSHOTS_DIR", snapshots_dir)
    monkeypatch.setattr(
        rha.core, "email_overlay",
        lambda baseline=None, threads=None: {"sent_followups": [], "from_baseline": []},
    )
    monkeypatch.setattr(
        rha.core, "interaction_overlay",
        lambda baseline=None, today=None, recent_days=90: {"matched_contacts": []},
    )
    monkeypatch.setattr(rha.core, "load_active_threads", lambda: [])

    result = rha.apply_to_baseline(baseline_path=baseline_path)

    assert result["entries_updated"] == 2
    written = json.loads(baseline_path.read_text())
    assert all("relationship_health" in entry for entry in written)
    assert snapshots_dir.exists()
    assert list(snapshots_dir.glob("*pre-relationship-health-aggregate*"))
