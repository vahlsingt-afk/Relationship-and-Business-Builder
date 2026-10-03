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


def _packet(tmp_path, title="Brand A deploys", signal="deployment"):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({"entities": [{"name": "Brand A", "status": "active"}]}))
    web = {"industry_technology": [_item(title, "2026-10-02", signal=signal, entities=["Brand A"])]}
    return gatherer.build_packet(web, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem)


def test_write_packet_is_atomic_via_tmp_rename(tmp_path):
    path = tmp_path / "packet.json"
    packet = _packet(tmp_path)
    gatherer.write_packet(packet, path)
    assert path.exists()
    assert not path.with_suffix(".json.tmp").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["packet_id"] == packet["packet_id"]


def test_write_history_appends_one_line_per_run(tmp_path):
    history_path = tmp_path / "gatherer_history.jsonl"
    packet1 = _packet(tmp_path, title="Brand A deploys")
    packet2 = _packet(tmp_path, title="Brand A expands")
    gatherer.write_history(packet1, history_path)
    gatherer.write_history(packet2, history_path)
    records = gatherer.read_history(history_path)
    assert len(records) == 2
    assert records[0]["packet_id"] == packet1["packet_id"]
    assert records[1]["packet_id"] == packet2["packet_id"]


def test_read_history_skips_malformed_lines_and_respects_limit(tmp_path):
    history_path = tmp_path / "gatherer_history.jsonl"
    history_path.write_text("not json\n" + json.dumps({"packet_id": "a"}) + "\n" + json.dumps({"packet_id": "b"}) + "\n")
    assert gatherer.read_history(history_path) == [{"packet_id": "a"}, {"packet_id": "b"}]
    assert gatherer.read_history(history_path, limit=1) == [{"packet_id": "b"}]


def test_read_history_missing_file_returns_empty(tmp_path):
    assert gatherer.read_history(tmp_path / "nope.jsonl") == []


def test_append_hunter_escalations_dedupes_across_runs(tmp_path):
    escalations_path = tmp_path / "escalations.jsonl"
    packet = _packet(tmp_path)
    first = gatherer.append_hunter_escalations(packet, escalations_path)
    assert len(first) == 1
    assert first[0]["change_id"] == packet["hunter_escalations"][0]["change_id"]
    second = gatherer.append_hunter_escalations(packet, escalations_path)
    assert second == []
    lines = [l for l in escalations_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1


def test_build_packet_status_ok_when_all_sources_succeed(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text('{"entities": []}')
    web = {
        "industry_primary": [_item("Restaurant expansion", "2026-10-02", signal="expansion")],
        "source_health": [{"source": "Nation's Restaurant News", "status": "ok", "items_returned": 3, "from_cache": False}],
        "status": "ok",
    }
    packet = gatherer.build_packet(web, now=datetime(2026, 10, 2, 18, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    assert packet["status"] == "ok"
    assert packet["source_health"]["expected_sources"] == 1
    assert packet["source_health"]["succeeded_sources"] == 1
    assert packet["source_health"]["failed_sources"] == 0
    assert not any("degraded" in l for l in packet["coverage"]["limitations"])


def test_build_packet_status_degraded_when_a_source_fails(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text('{"entities": []}')
    web = {
        "industry_primary": [],
        "source_health": [
            {"source": "Nation's Restaurant News", "status": "ok", "items_returned": 3, "from_cache": False},
            {"source": "QSR Magazine", "status": "fetch_error", "items_returned": 0, "from_cache": False, "error": "timeout"},
        ],
        "status": "ok",
    }
    packet = gatherer.build_packet(web, now=datetime(2026, 10, 2, 18, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    assert packet["status"] == "degraded"
    assert packet["source_health"]["failed_sources"] == 1
    assert packet["source_health"]["failed_source_names"] == ["QSR Magazine"]
    assert len(packet["changes"]) == 0
    assert any("degraded" in l for l in packet["coverage"]["limitations"])


def test_build_packet_status_degraded_on_upstream_error():
    web = {"status": "error", "source_health": []}
    packet = gatherer.build_packet(web, now=datetime(2026, 10, 2, 18, tzinfo=timezone.utc))
    assert packet["status"] == "degraded"
    assert packet["source_health"]["upstream_status"] == "error"


def test_build_packet_status_unknown_when_source_health_absent():
    packet = gatherer.build_packet({"industry_primary": []}, now=datetime(2026, 10, 2, 18, tzinfo=timezone.utc))
    assert packet["status"] == "unknown"
    assert packet["source_health"]["expected_sources"] == 0


def test_build_packet_clusters_same_event_across_urls(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({"entities": [{"name": "Brand A", "status": "active"}]}))
    web = {
        "industry_technology": [
            _item("Brand A deploys new kiosk platform", "2026-10-02", entities=["Brand A"],
                  url="https://outlet-one.example.com/story"),
            _item("Brand A rolls out kiosks nationwide", "2026-10-02", entities=["Brand A"],
                  url="https://outlet-two.example.com/story"),
        ],
    }
    packet = gatherer.build_packet(web, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    assert len(packet["changes"]) == 1
    primary = packet["changes"][0]
    assert len(primary["corroborating_sources"]) == 1
    assert primary["corroborating_sources"][0]["source_url"] == "https://outlet-two.example.com/story"
    assert packet["stats"]["clustered_as_same_event"] == 1


def test_build_packet_does_not_cluster_different_signal_types_same_day(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({"entities": [{"name": "Brand A", "status": "active"}]}))
    web = {
        "industry_technology": [
            _item("Brand A deploys new kiosk platform", "2026-10-02", signal="deployment", entities=["Brand A"],
                  url="https://outlet-one.example.com/story"),
            _item("Brand A announces new CTO", "2026-10-02", signal="executive_hire", entities=["Brand A"],
                  url="https://outlet-two.example.com/story"),
        ],
    }
    packet = gatherer.build_packet(web, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    assert len(packet["changes"]) == 2
    assert packet["stats"]["clustered_as_same_event"] == 0


def test_build_packet_does_not_cluster_entity_less_changes(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text('{"entities": []}')
    web = {
        "industry_primary": [
            _item("Restaurant industry expands", "2026-10-02", signal="expansion", url="https://a.example.com/x"),
            _item("Restaurant industry grows further", "2026-10-02", signal="expansion", url="https://b.example.com/y"),
        ],
    }
    packet = gatherer.build_packet(web, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    assert len(packet["changes"]) == 2
    assert all(c["corroborating_sources"] == [] for c in packet["changes"])
    assert packet["stats"]["clustered_as_same_event"] == 0


def test_novelty_unresolved_entity_when_no_entities_matched(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text('{"entities": []}')
    history_path = tmp_path / "history.jsonl"
    web = {"industry_primary": [_item("Restaurant industry expands", "2026-10-02", signal="expansion")]}
    packet = gatherer.build_packet(web, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc),
                                    ecosystem_path=ecosystem, history_path=history_path)
    assert packet["changes"][0]["novelty"] == gatherer.NOVELTY_UNRESOLVED_ENTITY
    assert packet["stats"]["novelty_counts"] == {gatherer.NOVELTY_UNRESOLVED_ENTITY: 1}


def test_novelty_new_to_rbb_when_no_prior_history_or_relationship(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({"entities": [{"id": "brand-a", "name": "Brand A", "status": "active"}],
                                      "relationships": []}))
    history_path = tmp_path / "history.jsonl"
    packet = gatherer.build_packet({"industry_technology": [_item("Brand A deploys", "2026-10-02", entities=["Brand A"])]},
                                    now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc),
                                    ecosystem_path=ecosystem, history_path=history_path)
    assert packet["changes"][0]["novelty"] == gatherer.NOVELTY_NEW_TO_RBB


def test_novelty_re_observed_when_same_url_in_prior_history(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({"entities": [{"id": "brand-a", "name": "Brand A", "status": "active"}]}))
    history_path = tmp_path / "history.jsonl"
    prior_packet = {"packet_id": "gatherer-prior", "generated_at": "2026-10-01T12:00:00Z",
                    "changes": [{"source_url": "https://example.com/a", "entities": ["Brand A"], "signal_type": "deployment"}]}
    gatherer.write_history(prior_packet, history_path)
    packet = gatherer.build_packet({"industry_technology": [_item("Brand A deploys", "2026-10-02", entities=["Brand A"])]},
                                    now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc),
                                    ecosystem_path=ecosystem, history_path=history_path)
    assert packet["changes"][0]["novelty"] == gatherer.NOVELTY_RE_OBSERVED


def test_novelty_repeated_monitoring_signal_same_entity_signal_different_url(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({"entities": [{"id": "brand-a", "name": "Brand A", "status": "active"}]}))
    history_path = tmp_path / "history.jsonl"
    prior_packet = {"packet_id": "gatherer-prior", "generated_at": "2026-10-01T12:00:00Z",
                    "changes": [{"source_url": "https://example.com/old-story", "entities": ["Brand A"], "signal_type": "deployment"}]}
    gatherer.write_history(prior_packet, history_path)
    packet = gatherer.build_packet(
        {"industry_technology": [_item("Brand A deploys again", "2026-10-02", entities=["Brand A"],
                                        url="https://example.com/new-story")]},
        now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem, history_path=history_path)
    assert packet["changes"][0]["novelty"] == gatherer.NOVELTY_REPEATED_MONITORING_SIGNAL


def test_novelty_possible_known_state_confirmation_when_active_relationship_exists(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({
        "entities": [{"id": "brand-a", "name": "Brand A", "status": "active"},
                     {"id": "vendor-x", "name": "Vendor X", "status": "active"}],
        "relationships": [{"from_entity_id": "brand-a", "to_entity_id": "vendor-x",
                            "relationship_type": "uses_vendor_for_category", "status": "active"}],
    }))
    history_path = tmp_path / "history.jsonl"
    packet = gatherer.build_packet(
        {"industry_technology": [_item("Brand A deploys", "2026-10-02", entities=["Brand A"])]},
        now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem, history_path=history_path)
    assert packet["changes"][0]["novelty"] == gatherer.NOVELTY_KNOWN_STATE_CONFIRMATION


def test_scores_present_with_four_dimensions(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text(json.dumps({"entities": [{"id": "brand-a", "name": "Brand A", "status": "active"}]}))
    history_path = tmp_path / "history.jsonl"
    packet = gatherer.build_packet(
        {"industry_technology": [_item("Brand A deploys", "2026-10-02", entities=["Brand A"])]},
        now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem, history_path=history_path)
    scores = packet["changes"][0]["scores"]
    assert set(scores) == {"impact", "ecosystem_relevance", "recency", "verification_need"}
    assert scores["impact"] == gatherer.MATERIAL_SIGNALS["deployment"]
    assert scores["ecosystem_relevance"] == 90
    assert scores["recency"] == 100


def test_scores_recency_decays_with_age_and_relevance_drops_without_match(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text('{"entities": []}')
    history_path = tmp_path / "history.jsonl"
    packet = gatherer.build_packet(
        {"industry_technology": [_item("Old-ish item", "2026-10-01T12:00:00", signal="deployment")]},
        now=datetime(2026, 10, 2, 0, tzinfo=timezone.utc), ecosystem_path=ecosystem, history_path=history_path)
    scores = packet["changes"][0]["scores"]
    assert scores["recency"] == 50
    assert scores["ecosystem_relevance"] == 55


def test_scores_verification_need_high_for_unresolved_entity_and_low_confidence(tmp_path):
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text('{"entities": []}')
    history_path = tmp_path / "history.jsonl"
    item = _item("Unresolved partnership story", "2026-10-02", signal="partnership")
    item["confidence"] = "low"
    packet = gatherer.build_packet({"industry_technology": [item]}, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc),
                                    ecosystem_path=ecosystem, history_path=history_path)
    assert packet["changes"][0]["scores"]["verification_need"] == 85


def test_append_hunter_escalations_no_candidates_is_noop(tmp_path):
    escalations_path = tmp_path / "escalations.jsonl"
    ecosystem = tmp_path / "ecosystem.json"
    ecosystem.write_text('{"entities": []}')
    packet = gatherer.build_packet({}, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), ecosystem_path=ecosystem)
    assert gatherer.append_hunter_escalations(packet, escalations_path) == []
    assert not escalations_path.exists()
