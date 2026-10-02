#!/usr/bin/env python3
"""
Tests for post_ingest_intelligence.py — Stage 6 intelligence generation from
freshly ingested structured data (RB-DEFECT-064 Phase 4).

Test IDs and coverage:

RB-POSTINGEST-001: null last_touch is flagged dormant
RB-POSTINGEST-002: last_touch older than dormant_days is flagged
RB-POSTINGEST-003: recent last_touch is not flagged
RB-POSTINGEST-004: untouched ids (not in baseline_by_id) are silently skipped, not errored
RB-POSTINGEST-005: a new contact's company with an existing inner-RC insider yields a broker candidate
RB-POSTINGEST-006: a new contact with no company never triggers a lookup
RB-POSTINGEST-007: a new contact whose company has no viable broker yields nothing for it
RB-POSTINGEST-008: repeated companies across multiple new contacts only look up once (cache reuse)
RB-POSTINGEST-009: distinct-company lookups are capped at `limit`
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import post_ingest_intelligence as pii  # noqa: E402

TODAY = date(2026, 7, 7)


def test_null_last_touch_is_dormant():
    """RB-POSTINGEST-001."""
    baseline_by_id = {"a": {"id": "a", "name": "A", "last_touch": None}}
    out = pii.dormant_relationships_resurfaced(["a"], baseline_by_id, today=TODAY)
    assert len(out) == 1
    assert out[0]["days_since_last_touch"] is None


def test_stale_last_touch_is_dormant():
    """RB-POSTINGEST-002."""
    baseline_by_id = {"a": {"id": "a", "name": "A", "last_touch": "2024-01-01"}}
    out = pii.dormant_relationships_resurfaced(["a"], baseline_by_id, today=TODAY)
    assert len(out) == 1
    assert out[0]["days_since_last_touch"] > pii.DORMANT_DAYS


def test_recent_last_touch_not_dormant():
    """RB-POSTINGEST-003."""
    baseline_by_id = {"a": {"id": "a", "name": "A", "last_touch": "2026-07-01"}}
    out = pii.dormant_relationships_resurfaced(["a"], baseline_by_id, today=TODAY)
    assert out == []


def test_unknown_ids_are_skipped_not_errored():
    """RB-POSTINGEST-004."""
    out = pii.dormant_relationships_resurfaced(["missing"], {}, today=TODAY)
    assert out == []


def _acme_baseline():
    return [
        {"id": "insider-1", "name": "Insider One", "current_company": "Acme Corp",
         "signal_class": "RC", "rc_tier": "inner", "circles": [], "tags": [], "notes": "",
         "last_touch": "2026-06-01"},
        {"id": "new-hire", "name": "New Hire", "current_company": "Acme Corp",
         "signal_class": "VC", "circles": [], "tags": [], "notes": "", "last_touch": None},
    ]


def test_warm_intro_candidate_found():
    """RB-POSTINGEST-005."""
    new_entries = [{"id": "new-hire", "name": "New Hire", "current_company": "Acme Corp"}]
    out = pii.warm_intro_candidates(new_entries, _acme_baseline(), today=TODAY, threads=[])
    assert len(out) == 1
    assert out[0]["broker_name"] == "Insider One"
    assert out[0]["new_contact"] == "New Hire"


def test_no_company_never_looks_up():
    """RB-POSTINGEST-006."""
    new_entries = [{"id": "x", "name": "No Company", "current_company": None}]
    out = pii.warm_intro_candidates(new_entries, _acme_baseline(), today=TODAY, threads=[])
    assert out == []


def test_no_viable_broker_yields_nothing():
    """RB-POSTINGEST-007."""
    baseline = [{"id": "lone-vc", "name": "Lone VC", "current_company": "Nobody Corp",
                 "signal_class": "VC", "circles": [], "tags": [], "notes": "", "last_touch": None}]
    new_entries = [{"id": "y", "name": "New Person", "current_company": "Nobody Corp"}]
    out = pii.warm_intro_candidates(new_entries, baseline, today=TODAY, threads=[])
    assert out == []


def test_repeated_company_only_looked_up_once(monkeypatch):
    """RB-POSTINGEST-008."""
    calls = []
    real_find = pii.core.find_intro_paths

    def counting_find(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("target"))
        return real_find(*args, **kwargs)

    monkeypatch.setattr(pii.core, "find_intro_paths", counting_find)

    new_entries = [
        {"id": "new-hire-1", "name": "New Hire One", "current_company": "Acme Corp"},
        {"id": "new-hire-2", "name": "New Hire Two", "current_company": "Acme Corp"},
    ]
    out = pii.warm_intro_candidates(new_entries, _acme_baseline(), today=TODAY, threads=[])
    assert len(out) == 2
    assert len(calls) == 1


def test_distinct_company_lookups_capped_at_limit():
    """RB-POSTINGEST-009."""
    baseline = [
        {"id": "insider-1", "name": "Insider One", "current_company": "Company A",
         "signal_class": "RC", "rc_tier": "inner", "circles": [], "tags": [], "notes": "",
         "last_touch": "2026-06-01"},
    ]
    new_entries = [
        {"id": "p1", "name": "Person One", "current_company": "Company A"},
        {"id": "p2", "name": "Person Two", "current_company": "Company B"},
    ]
    out = pii.warm_intro_candidates(new_entries, baseline, today=TODAY, threads=[], limit=1)
    # Only the first distinct company ("Company A") gets looked up under limit=1.
    assert {c["company"] for c in out} == {"Company A"}
