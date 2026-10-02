import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
import routine_research_evidence_sweep as sweep


def test_routes_generic_evidence_to_review_without_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "QUEUE_PATH", tmp_path / "queue.jsonl")
    monkeypatch.setattr(sweep, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(sweep, "REVIEW_PATH", tmp_path / "review.json")
    monkeypatch.setattr(sweep, "LATEST_PATH", tmp_path / "latest.json")
    row = {
        "cycle_type": "routine_research", "entity_name": "NewCo", "entity_type": "brand",
        "dimension": "leadership", "finding_type": "new_baseline_fact",
        "evidence_text": "Jane Doe became CEO.", "source_url": "https://example.com/release",
        "source_title": "Leadership release", "source_published_date": "2026-09-01",
        "source_accessed_date": "2026-09-14", "confidence_pct": 95,
        "corroborating_source_count": 1, "limitations": "", "researched_at": "2026-09-14T10:00:00Z",
    }
    sweep.QUEUE_PATH.write_text(json.dumps(row) + "\n")
    first = sweep.sweep()
    second = sweep.sweep()
    assert first["new_lines"] == 1
    assert first["accepted_for_review"] == 1
    assert first["canonical_mutations"] == 0
    assert second["new_lines"] == 0
    review = json.loads(sweep.REVIEW_PATH.read_text())
    assert review["pending_reviews"][0]["requires_confirmation"] is True
