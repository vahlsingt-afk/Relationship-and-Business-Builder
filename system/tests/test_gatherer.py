import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import gatherer  # noqa: E402


def _item(title, date, *, domain="restaurant_technology", signal="deployment", entities=None, url="https://example.com/a"):
    return {"title": title, "summary": "Fresh change", "confidence": "medium", "source_refs": ["Example"],
            "extras": {"domain": domain, "entities": entities or [], "signal_type": signal,
                       "source_url": url, "pub_date": date, "source_name": "Example"}}


def test_gatherer_enforces_window_dedup_and_world_noise(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({"entities": [{"name": "Brand A", "status": "active", "aliases": ["A Brand"]}]}))
    web = {
        "industry_technology": [
            _item("Brand A deploys a new platform", "2026-10-02"),
            _item("Duplicate", "2026-10-02", url="https://example.com/a?utm_source=x"),
            _item("Old item", "2026-09-29", url="https://example.com/old"),
        ],
        "world_national": [_item("Unrelated politics", "2026-10-02", domain="world_national", signal="general", url="https://example.com/world")],
    }
    packet = gatherer.build_packet(web, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    assert packet["contract"] == gatherer.CONTRACT
    assert len(packet["changes"]) == 1
    assert packet["changes"][0]["entities"] == ["Brand A"]
    assert packet["changes"][0]["target_keys"] == []
    assert packet["changes"][0]["novelty"]["state"] == "new_in_window"
    assert "priority" in packet["changes"][0]["scores"]
    assert packet["stats"]["duplicates_removed"] == 1
    assert packet["stats"]["excluded_outside_window"] == 1
    assert packet["stats"]["excluded_world_noise"] == 1
    assert packet["hunter_escalations"][0]["recommended_playbook"] == "customer_deployment_validation"


def test_gatherer_marks_every_change_as_candidate(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text('{"entities": []}')
    packet = gatherer.build_packet({"industry_primary": [_item("Restaurant expansion", "2026-10-02", signal="expansion")]},
                                   now=datetime(2026, 10, 2, 18, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    assert packet["changes"][0]["verification_state"] == "candidate"
    assert "not proof" in packet["coverage"]["limitations"][0]


def test_gatherer_tracks_prior_novelty_and_corroboration(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({"entities": [{"id": "brand-a", "name": "Brand A", "entity_type": "brand", "status": "active"}]}))
    web = {"industry_technology": [
        _item("Brand A deploys platform", "2026-10-02", entities=["Brand A"], url="https://one.example/a"),
        _item("Brand A deploys platform", "2026-10-02", entities=["Brand A"], url="https://two.example/a"),
    ]}
    first = gatherer.build_packet(web, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    assert len(first["changes"]) == 1
    assert first["changes"][0]["corroboration"]["state"] == "multi_source"
    assert first["changes"][0]["target_keys"] == ["company:brand-a"]
    second = gatherer.build_packet(web, now=datetime(2026, 10, 2, 13, tzinfo=timezone.utc), ecosystem_path=ecosystem, prior_packet=first)
    assert second["changes"][0]["novelty"]["state"] == "seen_prior_day"
    assert second["hunter_escalations"][0]["verification_questions"]


def test_gatherer_compares_sources_with_canonical_rbb_state(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({
        "entities": [{"id": "brand-a", "name": "Brand A", "entity_type": "brand", "status": "active"}],
        "sources": [{"source_url": "https://example.com/a"}],
    }))
    packet = gatherer.build_packet(
        {"industry_technology": [_item("Brand A deploys platform", "2026-10-02", entities=["Brand A"])]},
        now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem,
    )
    assert packet["changes"][0]["rbb_comparison"]["state"] == "known_evidence_reobserved"
    assert packet["stats"]["known_evidence_reobserved"] == 1


def test_gatherer_clusters_related_headlines_into_one_event(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({"entities": [
        {"id": "brand-a", "name": "Brand A", "entity_type": "brand", "status": "active"}
    ]}))
    web = {"industry_technology": [
        _item("Brand A deploys Acme ordering platform", "2026-10-02", entities=["Brand A"], url="https://one.example/a"),
        _item("Acme platform deployment begins at Brand A", "2026-10-02", entities=["Brand A"], url="https://two.example/a"),
    ]}
    packet = gatherer.build_packet(web, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    assert len(packet["changes"]) == 1
    assert packet["changes"][0]["corroboration"]["source_count"] == 2
    assert packet["changes"][0]["clustered_headlines"]


def test_malformed_ecosystem_file_surfaces_an_explicit_error_not_a_silent_zero(tmp_path):
    """2026-10-03 (CLAUDE_HANDOFF_RB_HUNTER_GATHERER_END_TO_END_DEFECTS,
    Defect 5): _tracked_entities() used to swallow ANY read/parse
    exception into a bare tracked_entities_total: 0, indistinguishable
    from a legitimately empty graph."""
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text("this is not valid JSON {{{")
    packet = gatherer.build_packet({}, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    assert packet["coverage"]["tracked_entities_total"] == 0
    assert packet["coverage"]["tracked_entities_error"] is not None
    assert packet["run_receipt"]["canonical_state_compared"] is False


def test_successful_read_reports_no_tracked_entities_error(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({"entities": []}))
    packet = gatherer.build_packet({}, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    assert packet["coverage"]["tracked_entities_error"] is None
    assert packet["run_receipt"]["canonical_state_compared"] is True


def test_receipt_consistency_flags_the_real_observed_contradiction(tmp_path):
    """Reproduces the exact live incident: input_status 'ok', real input
    items present, but every configured source reported failed -- a
    schema-valid but materially misleading receipt."""
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({"entities": [
        {"id": "brand-a", "name": "Brand A", "entity_type": "brand", "status": "active"}
    ]}))
    web = {
        "status": "ok",
        "source_health": [{"source": "One", "status": "error"}, {"source": "Two", "status": "error"}],
        "industry_technology": [_item("Brand A deploys platform", "2026-10-02", entities=["Brand A"])],
    }
    packet = gatherer.build_packet(web, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    assert packet["stats"]["input_items"] == 1
    assert packet["coverage"]["source_checks"]["successful_sources"] == 0
    assert packet["run_receipt"]["receipt_consistent"] is False
    assert packet["run_receipt"]["receipt_consistency_reasons"]


def test_receipt_consistency_does_not_over_trigger_on_a_genuinely_empty_quiet_cycle(tmp_path):
    """A real quiet day (no collected items, no sources configured) is
    not an error and must not be flagged -- the check is narrow on
    purpose."""
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({"entities": []}))
    packet = gatherer.build_packet({"status": "ok"}, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    assert packet["stats"]["input_items"] == 0
    assert packet["coverage"]["tracked_entities_total"] == 0
    assert packet["run_receipt"]["receipt_consistent"] is True
    assert packet["run_receipt"]["receipt_consistency_reasons"] == []


def test_receipt_consistency_passes_when_failed_sources_report_no_input_items(tmp_path):
    """All sources failed AND nothing was collected -- an honest, fully
    failed cycle, not a contradictory one (no claim of real input to
    contradict the failure)."""
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({"entities": [
        {"id": "brand-a", "name": "Brand A", "entity_type": "brand", "status": "active"}
    ]}))
    web = {"status": "ok", "source_health": [{"source": "One", "status": "error"}]}
    packet = gatherer.build_packet(web, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    assert packet["stats"]["input_items"] == 0
    assert packet["run_receipt"]["receipt_consistent"] is True


def test_write_packet_validates_archives_and_queues_hunter_job(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({"entities": [
        {"id": "brand-a", "name": "Brand A", "entity_type": "brand", "status": "active"}
    ]}))
    web = {
        "status": "ok",
        "source_health": [{"source": "One", "status": "ok"}, {"source": "Two", "status": "error"}],
        "industry_technology": [_item("Brand A deploys platform", "2026-10-02", entities=["Brand A"])],
    }
    packet = gatherer.build_packet(web, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    output, history, queue = tmp_path / "packet.json", tmp_path / "history", tmp_path / "queue.jsonl"
    gatherer.write_packet(packet, output, history_dir=history, queue_path=queue)
    saved = json.loads(output.read_text())
    assert saved["run_receipt"]["schema_validation"] == "valid"
    assert saved["run_receipt"]["hunter_jobs_queued"] == 1
    assert saved["coverage"]["source_checks"]["coverage_pct"] == 50.0
    assert (history / f"{packet['packet_id']}.json").exists()
    queued = json.loads(queue.read_text().strip())
    assert queued["hunter_job"]["playbook"] == "customer_deployment_validation"
