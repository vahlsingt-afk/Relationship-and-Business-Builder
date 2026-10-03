from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import hunter  # noqa: E402
import hunter_citation_verify  # noqa: E402
import hunter_cos_synthesis  # noqa: E402
import hunter_cycle  # noqa: E402
import hunter_eval  # noqa: E402
import hunter_feedback  # noqa: E402
import hunter_payload_validate  # noqa: E402
import hunter_snapshot  # noqa: E402
from test_hunter_contract import _valid_packet  # noqa: E402


def test_payload_registry_rejects_wrong_shape():
    errors = hunter_payload_validate.validate("rb.brand_company_profile.v1", {"wrong": []})
    assert errors[0]["code"] == "payload_collection_missing"


def test_snapshot_detects_no_delta():
    directive = {
        "prepared_at": "2026-10-02T10:00:00Z",
        "packet_requirements": {"prior_state_as_of": "2026-10-02T10:00:00Z"},
        "gap_manifest": {"targets": [{"target_key": "competitor:example", "current_state": {"x": 1}, "gaps": []}]},
    }
    snapshot = hunter_snapshot.from_directive(directive)
    packet = _valid_packet()
    packet["change_events"] = [{"change_event_id": "chg-same", "target_key": "competitor:example", "prior_state": 1, "new_state": 1}]
    assert hunter_snapshot.compare(snapshot, packet)["errors"][0]["code"] == "change_has_no_delta"


def test_citation_integrity_flags_productive_inaccessible_source():
    packet = _valid_packet()
    packet["source_ledger"][0]["access_status"] = "blocked"
    report = hunter_citation_verify.verify(packet)
    assert report["errors"][0]["code"] == "productive_source_inaccessible"


def test_cos_synthesis_preserves_evidence_links():
    result = hunter_cos_synthesis.synthesize([{"handoff": {
        "handoff_id": "cos-one", "headline": "Change", "connected_target_keys": ["company:a"],
        "finding_ids": ["f-one"], "change_event_ids": ["chg-one"], "confidence_pct": 75,
    }}])
    assert result["target_threads"]["company:a"][0]["finding_ids"] == ["f-one"]


def test_feedback_is_attributed_to_source():
    result = hunter_feedback.attribute(_valid_packet(), [{"finding_id": "f-example-customer", "outcome": "accepted"}])
    assert result["sources"][0]["accepted"] == 1


def test_eval_score_uses_maintained_corpus():
    result = hunter_eval.score({"cases": {"logo-wall-customer": ["discovery_source_overpromoted"]}})
    assert result["passed"] == 1
    assert result["total"] >= 10


def test_cycle_finalize_defaults_to_dry_run():
    packet = _valid_packet()
    job = {"before_snapshot": {"targets": {"competitor:example": {"current_state": {}}}, "state_hash": "x"}}
    receipt = hunter_cycle.finalize(job, packet)
    assert receipt["ok"] is True
    assert receipt["dispatch"]["dry_run"] is True


def test_theme_playbook_is_registered():
    result = hunter.plan("restaurant_ai_pilots")
    assert result["payload_schema"] == "rb.hunter_industry_theme.v1"


def test_gatherer_escalation_becomes_hunter_job(monkeypatch, tmp_path):
    queue = tmp_path / "queue.jsonl"
    queue.write_text(json.dumps({
        "status": "ready_for_hunter_preparation",
        "change_id": "gchg-one",
        "verification_questions": ["Verify it"],
        "hunter_job": {
            "playbook": "change_monitor", "depth": "monitor",
            "target_keys": ["company:brand-a"], "objective": "Verify change",
            "known_source_urls": ["https://example.com/a"], "prior_state_hint": {"state": "new_to_rbb_candidate"},
        },
    }) + "\n")
    monkeypatch.setattr(hunter_cycle, "prepare", lambda *args, **kwargs: {
        "directive": {"packet_requirements": {"target_keys": ["company:brand-a"]}}
    })
    job = hunter_cycle.prepare_gatherer_escalation(queue_path=queue)
    assert job["directive"]["gatherer_context"]["change_id"] == "gchg-one"
    assert job["gatherer_escalation"]["hunter_job"]["playbook"] == "change_monitor"
