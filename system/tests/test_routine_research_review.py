from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
import routine_research_review as review


def _seed(path: Path):
    path.write_text(json.dumps({"pending_reviews": [{
        "evidence_id": "rre-1", "status": "pending_review", "entity_name": "Test Brand",
        "entity_type": "brand", "dimension": "leadership", "finding_type": "new_baseline_fact",
        "evidence_text": "Jane Doe became CEO.", "source_url": "https://example.com/release",
        "source_title": "Company release", "source_published_date": "2026-09-01",
        "confidence_pct": 95, "limitations": "",
    }]}))


def test_approve_appends_canonical_evidence_and_ledger(tmp_path, monkeypatch):
    review_path = tmp_path / "reviews.json"
    ledger_path = tmp_path / "ledger.jsonl"
    evidence_path = tmp_path / "evidence.jsonl"
    _seed(review_path)
    monkeypatch.setattr(review, "REVIEW_PATH", review_path)
    monkeypatch.setattr(review, "MUTATION_LEDGER_PATH", ledger_path)
    monkeypatch.setattr(review.abb, "resolve_account", lambda name: ("test-brand", True))
    monkeypatch.setattr(review.cpc, "account_dir", lambda slug: tmp_path)
    monkeypatch.setattr(review.cpc, "load_jsonl", lambda path: [])
    monkeypatch.setattr(review.cpc, "next_evidence_id", lambda slug, rows: "ev-test-0001")
    monkeypatch.setattr(review.cpc, "append_jsonl", lambda path, row: evidence_path.write_text(json.dumps(row) + "\n"))
    monkeypatch.setattr(review.cpc, "flag_needs_refresh", lambda slug, reason: True)
    monkeypatch.setattr(review.cpc, "today", lambda: "2026-09-14")
    monkeypatch.setattr(review.core, "PROJECT_DIR", tmp_path)
    result = review.resolve("rre-1", decision="approve_evidence", resolution="Source verified; retain.")
    assert result["status"] == "canonical_evidence_appended"
    assert json.loads(evidence_path.read_text())["extracted_claims"] == ["Jane Doe became CEO."]
    assert json.loads(ledger_path.read_text())["decision"] == "approve_evidence"
    assert review.list_reviews() == []


def test_reject_writes_decision_but_no_canonical_target(tmp_path, monkeypatch):
    review_path = tmp_path / "reviews.json"
    _seed(review_path)
    monkeypatch.setattr(review, "REVIEW_PATH", review_path)
    monkeypatch.setattr(review, "MUTATION_LEDGER_PATH", tmp_path / "ledger.jsonl")
    result = review.resolve("rre-1", decision="reject", resolution="Source is not authoritative.")
    assert result["status"] == "rejected"
    assert result["canonical_target"] is None
