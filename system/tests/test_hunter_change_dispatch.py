from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import hunter_change_dispatch as dispatch  # noqa: E402
from test_hunter_contract import _valid_packet  # noqa: E402


def _change_packet():
    packet = _valid_packet()
    packet["cycle"]["playbook"] = "enterprise_account_profile"
    packet["payload_schema"] = "rb.brand_company_profile.v1"
    packet["targets"][0].update({
        "target_key": "company:brand-example",
        "display_name": "Example Burgers",
        "entity_type": "restaurant_brand",
    })
    packet["findings"][0]["target_key"] = "company:brand-example"
    packet["change_events"] = [{
        "change_event_id": "chg-example-expansion",
        "target_key": "company:brand-example",
        "change_type": "expansion",
        "summary": "The brand announced a material expansion.",
        "prior_state": {"units": 100},
        "new_state": {"units": 150},
        "effective_at": "2026-09-01",
        "observed_at": "2026-10-02T12:02:00Z",
        "materiality": "high",
        "finding_ids": ["f-example-customer"],
        "source_ids": ["src-example"],
        "confidence_pct": 80,
    }]
    packet["mutation_proposals"] = [{
        "proposal_id": "mut-example-expansion",
        "target_key": "company:brand-example",
        "operation": "append_event",
        "field_path": "brand_profile.recent_signals",
        "existing_value": None,
        "new_value": {
            "signal_type": "expansion_or_contraction",
            "value": "The brand announced expansion from 100 to 150 units.",
            "status": "reported",
            "confidence": "medium",
            "as_of": "2026-09-01",
        },
        "effective_at": "2026-09-01",
        "confidence_pct": 80,
        "change_event_ids": ["chg-example-expansion"],
        "finding_ids": ["f-example-customer"],
        "source_ids": ["src-example"],
    }]
    packet["cos_handoffs"] = [{
        "handoff_id": "cos-example-expansion",
        "headline": "Example Burgers accelerates expansion",
        "connected_target_keys": ["company:brand-example"],
        "change_event_ids": ["chg-example-expansion"],
        "finding_ids": ["f-example-customer"],
        "connection": "Expansion increases the number of locations affected by technology standards.",
        "why_it_matters": "A larger footprint can increase implementation and support requirements.",
        "is_inference": True,
        "confidence_pct": 70,
        "time_horizon": "months",
    }]
    return packet


def test_dry_run_recognizes_safe_brand_signal_writer_without_mutating():
    result = dispatch.dispatch(_change_packet(), dry_run=True)
    assert result["ok"] is True
    assert result["mutation_results"][0]["handler_recognized"] is True
    assert result["mutation_results"][0]["applied"] is False
    assert result["cos_handoffs_recorded"] == 1


def test_unregistered_writer_is_queued_not_claimed_applied():
    packet = _change_packet()
    packet["mutation_proposals"][0]["field_path"] = "unknown.store.field"
    result = dispatch.dispatch(packet, dry_run=True)
    assert result["canonical_applied"] == 0
    assert result["queued_for_review_or_unhandled"] == 1


# ---- resolved_sources embedding (Todd's review feedback, 2026-10-10) -----
# A reviewer previously saw only source_ids on a queued proposal, with no
# way to see what those sources actually say without re-opening the
# archived packet. The packet is only in scope at dispatch time -- resolve
# it then, once, and embed it in the queued row.

def _queue_an_unregistered_proposal():
    tmp = tempfile.TemporaryDirectory()
    queue_path = Path(tmp.name) / "proposals.jsonl"
    packet = _change_packet()
    packet["mutation_proposals"][0]["field_path"] = "unknown.store.field"
    with patch.object(dispatch, "PROPOSAL_QUEUE_PATH", queue_path):
        dispatch.dispatch(packet, dry_run=False)
        rows = [json.loads(l) for l in queue_path.read_text().splitlines()]
    tmp.cleanup()
    return rows


def test_queued_proposal_carries_resolved_sources():
    rows = _queue_an_unregistered_proposal()
    assert len(rows) == 1
    sources = rows[0]["resolved_sources"]
    assert len(sources) == 1
    assert sources[0]["source_id"] == "src-example"
    assert sources[0]["title"] == "Case study"
    assert sources[0]["publisher"] == "Example"
    assert sources[0]["url"] == "https://example.com/case-study"


def test_resolve_sources_skips_an_unknown_id_instead_of_raising():
    packet = _change_packet()
    resolved = dispatch._resolve_sources(packet, ["src-example", "src-does-not-exist"])
    assert len(resolved) == 1
    assert resolved[0]["source_id"] == "src-example"


def test_resolve_sources_empty_list_returns_empty_list():
    packet = _change_packet()
    assert dispatch._resolve_sources(packet, []) == []
