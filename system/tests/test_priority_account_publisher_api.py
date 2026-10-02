"""
test_priority_account_publisher_api.py — RB-DEFECT-069, 2026-09-10.

API-layer coverage for the priority-account publisher coverage endpoints:
  GET  /priority-accounts/publisher-matches (getPriorityAccountPublisherMatches)
  POST /confirm kind="priority_account_publisher_match" (confirmProposal dispatch)

Same direct-function-call + monkeypatch isolation pattern as
test_tech_stack_relationship_api.py. The scan() step itself (which needs a
real/mocked network fetcher) isn't exposed via the API at all -- only
pending_candidates()/record_proposal() are -- so these tests seed a pending
candidate directly, matching how test_tech_stack_relationship_api.py's own
fixture seeds via the real propose function where one exists.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from system.api import server


@pytest.fixture()
def isolated_priority_accounts(tmp_path: Path, monkeypatch):
    graph_path = tmp_path / "ecosystem_intelligence.json"
    store_path = tmp_path / "priority_account_publisher_candidates.json"
    cpc_root = tmp_path / "customers_prospects"
    (cpc_root / "_portfolio").mkdir(parents=True)
    (cpc_root / "_portfolio" / "customers_prospects_registry.json").write_text(
        json.dumps({"registry": []}), encoding="utf-8",
    )
    acct_dir = cpc_root / "accounts" / "del-taco"
    acct_dir.mkdir(parents=True)
    (acct_dir / "account.json").write_text(json.dumps({"display_name": "Del Taco"}), encoding="utf-8")
    (acct_dir / "evidence.jsonl").write_text("", encoding="utf-8")

    graph_path.write_text(json.dumps({
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-10",
        "entities": [
            {"id": "brand-del-taco", "name": "Del Taco", "entity_type": "brand", "aliases": [],
             "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]},
        ],
        "relationships": [], "signals": [], "sources": [],
        "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }), encoding="utf-8")

    monkeypatch.setattr(server, "_auth", lambda *a, **k: None)
    pas = server.priority_account_publisher_scan
    monkeypatch.setattr(pas.ei.core, "ECOSYSTEM_INTELLIGENCE_PATH", graph_path)
    monkeypatch.setattr(pas, "STORE_PATH", store_path)
    monkeypatch.setattr(pas.cpc, "ROOT", cpc_root)
    return {"graph_path": graph_path, "store_path": store_path, "cpc_root": cpc_root}


def _seed_pending_candidate(pas, cid="brand-del-taco::abc123"):
    store = {"candidates": {cid: {
        "candidate_id": cid, "status": "proposed_pending_confirmation",
        "entity_id": "brand-del-taco", "account_name": "Del Taco",
        "evidence_target": {"kind": "customers_prospects", "slug": "del-taco"},
        "signal_class": "vendor_relationship_formed",
        "title": "Del Taco Selects New Payments Platform for Nationwide Rollout",
        "url": "https://www.restaurantnews.com/del-taco-payments-090226/",
        "pub_date": "2026-09-02", "source_name": "Industry Publisher Search",
        "detected_at": "2026-09-10T00:00:00Z", "resolved_at": None,
    }}}
    pas._save_store(store)
    return cid


def test_get_endpoint_lists_pending_candidate(isolated_priority_accounts):
    pas = server.priority_account_publisher_scan
    _seed_pending_candidate(pas)
    result = server.get_priority_account_publisher_matches(x_api_key=None)
    assert len(result["proposals"]) == 1
    assert result["proposals"][0]["account_name"] == "Del Taco"


def test_confirm_proposal_dispatches_priority_account_publisher_match_kind(isolated_priority_accounts):
    pas = server.priority_account_publisher_scan
    cid = _seed_pending_candidate(pas)

    result = server.confirm_proposal(
        server.ConfirmProposalBody(kind="priority_account_publisher_match", id=cid, confirmed=True),
        x_api_key=None,
    )
    assert result["confirmed"] is True

    evidence = pas.cpc.load_jsonl(isolated_priority_accounts["cpc_root"] / "accounts" / "del-taco" / "evidence.jsonl")
    assert len(evidence) == 1
    assert evidence[0]["event_date"] == "2026-09-02"
    assert evidence[0]["source_type"] == "priority_account_publisher_match"

    assert server.get_priority_account_publisher_matches(x_api_key=None)["proposals"] == []


def test_confirm_proposal_rejects_priority_account_publisher_match_kind(isolated_priority_accounts):
    pas = server.priority_account_publisher_scan
    cid = _seed_pending_candidate(pas)

    result = server.confirm_proposal(
        server.ConfirmProposalBody(kind="priority_account_publisher_match", id=cid, confirmed=False),
        x_api_key=None,
    )
    assert result["rejected"] is True
    assert server.get_priority_account_publisher_matches(x_api_key=None)["proposals"] == []


def test_confirm_proposal_unknown_id_returns_404(isolated_priority_accounts):
    with pytest.raises(HTTPException) as exc_info:
        server.confirm_proposal(
            server.ConfirmProposalBody(kind="priority_account_publisher_match", id="not-a-real-id", confirmed=True),
            x_api_key=None,
        )
    assert exc_info.value.status_code == 404
