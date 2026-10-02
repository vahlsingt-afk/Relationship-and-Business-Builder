from datetime import date
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
import downstream_impact_queue as q


def _paths(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(q, "MUTATION_LEDGER_PATH", tmp_path / "mutations.jsonl")
    monkeypatch.setattr(q, "EXECUTION_LEDGER_PATH", tmp_path / "executions.jsonl")


def test_ramification_creates_review_item_not_automatic_mutation(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    ram = {"ramifications": [{"ramification_id": "ram-1", "entity_name": "Brand A",
        "evidence_summary": "New CEO", "source_refs": ["src-1"],
        "affected_artifacts": [{"artifact": "account_plan_or_blue_sheet", "action": "review_if_active"}]}]}
    result = q.run(ram, today=date(2026, 9, 14))
    assert result["review_items_added"] == 1
    assert result["safe_applied"] == 0
    assert q.list_items()[0]["status"] == "pending_review"


def test_approved_evidence_regenerates_background_brief(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    q.MUTATION_LEDGER_PATH.write_text(json.dumps({
        "mutation_id": "mut-1", "status": "canonical_evidence_appended",
        "entity_name": "Brand A",
        "canonical_target": "system/account_research/accounts/brand-a/evidence.jsonl"}) + "\n")
    monkeypatch.setattr(q.abb, "generate_brief", lambda slug, **kwargs: {
        "version": "v2", "path": "briefs/v2.md"})
    result = q.run({"ramifications": []}, today=date(2026, 9, 14))
    assert result["safe_applied"] == 1
    assert q.list_items(status="applied")[0]["execution_receipt"]["brief_version"] == "v2"


def test_review_resolution_never_executes_judgment_edit(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    q.QUEUE_PATH.write_text(json.dumps({"items": [{"impact_id": "impact-1", "status": "pending_review",
        "target_artifact": "blue_sheet", "review_required": True}]}))
    result = q.resolve("impact-1", decision="approve_manual_action", resolution="Review Blue Sheet strategy.")
    assert result["status"] == "approved_manual_action"
    assert "separate explicit operation" in result["note"]
