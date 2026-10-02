from __future__ import annotations

import sys
from pathlib import Path


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
