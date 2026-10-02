"""
test_job_postings_promotion_api.py — RB-2026-09-18, wired 2026-09-25
(Confidence-Based Auto-Recording Phase 7).

API-layer coverage for the job-postings endpoints:
  GET  /job-postings/proposals    (getJobPostingCandidates)
  POST /confirm kind="job_posting" (confirmProposal dispatch)

Same direct-function-call + monkeypatch isolation pattern as
test_priority_account_publisher_api.py. scan() itself (which needs a real/
mocked network fetcher) isn't exposed via the API at all -- only
pending_candidates()/record_proposal() are -- so these tests seed a
pending candidate directly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from system.api import server


@pytest.fixture()
def isolated_job_postings(tmp_path: Path, monkeypatch):
    graph_path = tmp_path / "ecosystem_intelligence.json"
    store_path = tmp_path / "job_postings_candidates.json"
    cpc_root = tmp_path / "customers_prospects"
    (cpc_root / "_portfolio").mkdir(parents=True)
    (cpc_root / "_portfolio" / "customers_prospects_registry.json").write_text(
        json.dumps({"registry": []}), encoding="utf-8",
    )
    acct_dir = cpc_root / "accounts" / "wendys"
    acct_dir.mkdir(parents=True)
    (acct_dir / "account.json").write_text(json.dumps({"display_name": "Wendy's"}), encoding="utf-8")
    (acct_dir / "evidence.jsonl").write_text("", encoding="utf-8")

    graph_path.write_text(json.dumps({
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-18",
        "entities": [
            {"id": "brand-wendys", "name": "Wendy's", "entity_type": "brand", "aliases": [],
             "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]},
        ],
        "relationships": [], "signals": [], "sources": [],
        "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }), encoding="utf-8")

    monkeypatch.setattr(server, "_auth", lambda *a, **k: None)
    jp = server.job_postings_promotion
    monkeypatch.setattr(jp.ei.core, "ECOSYSTEM_INTELLIGENCE_PATH", graph_path)
    monkeypatch.setattr(jp, "STORE_PATH", store_path)
    monkeypatch.setattr(jp.cpc, "ROOT", cpc_root)
    return {"graph_path": graph_path, "store_path": store_path, "cpc_root": cpc_root}


def _seed_pending_candidate(jp, cid="brand-wendys::abc123"):
    store = {"candidates": {cid: {
        "candidate_id": cid, "status": "proposed_pending_confirmation",
        "entity_id": "brand-wendys", "account_name": "Wendy's", "account_slug": "wendys",
        "vulnerability_category": "tech_hiring",
        "role_title": "Director of Restaurant Technology - Wendy's",
        "url": "https://boards.greenhouse.io/wendys/jobs/12345",
        "source_name": "DuckDuckGo Job Board Search",
        "detected_at": "2026-09-18T00:00:00Z", "resolved_at": None,
    }}}
    jp._save_store(store)
    return cid


def test_get_endpoint_lists_pending_candidate(isolated_job_postings):
    jp = server.job_postings_promotion
    _seed_pending_candidate(jp)
    result = server.get_job_posting_candidates(x_api_key=None)
    assert len(result["proposals"]) == 1
    assert result["proposals"][0]["account_name"] == "Wendy's"


def test_confirm_proposal_dispatches_job_posting_kind(isolated_job_postings):
    jp = server.job_postings_promotion
    cid = _seed_pending_candidate(jp)

    result = server.confirm_proposal(
        server.ConfirmProposalBody(kind="job_posting", id=cid, confirmed=True),
        x_api_key=None,
    )
    assert result["confirmed"] is True

    evidence = jp.cpc.load_jsonl(isolated_job_postings["cpc_root"] / "accounts" / "wendys" / "evidence.jsonl")
    assert len(evidence) == 1
    assert evidence[0]["source_type"] == "job_postings_promotion_match"
    assert evidence[0]["extracted_claims"] == []

    assert server.get_job_posting_candidates(x_api_key=None)["proposals"] == []


def test_confirm_proposal_rejects_job_posting_kind(isolated_job_postings):
    jp = server.job_postings_promotion
    cid = _seed_pending_candidate(jp)

    result = server.confirm_proposal(
        server.ConfirmProposalBody(kind="job_posting", id=cid, confirmed=False),
        x_api_key=None,
    )
    assert result["rejected"] is True
    assert server.get_job_posting_candidates(x_api_key=None)["proposals"] == []


def test_confirm_proposal_unknown_id_returns_404(isolated_job_postings):
    with pytest.raises(HTTPException) as exc_info:
        server.confirm_proposal(
            server.ConfirmProposalBody(kind="job_posting", id="not-a-real-id", confirmed=True),
            x_api_key=None,
        )
    assert exc_info.value.status_code == 404
