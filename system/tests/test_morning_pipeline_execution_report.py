from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import morning_pipeline


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_delivery_ok_true_when_no_delivery_steps_present() -> None:
    steps = [{"name": "publish_canonical_artifacts", "status": "pass"}]
    assert morning_pipeline._compute_delivery_ok(steps) is True


def test_delivery_ok_true_when_at_least_one_send_succeeds() -> None:
    steps = [
        {"name": "send_intelligence_brief_email", "status": "warn"},
        {"name": "send_daily_brief_email", "status": "pass"},
        {"name": "send_team_intelligence_brief_email", "status": "warn"},
    ]
    assert morning_pipeline._compute_delivery_ok(steps) is True


def test_delivery_ok_false_when_all_sends_fail() -> None:
    # This is the 2026-08-01 incident: brief built fine, SMTP DNS resolution
    # failed for every send step, and the pipeline still reported PASS
    # because email steps are required=False. Delivery must gate "ok".
    steps = [
        {"name": "publish_canonical_artifacts", "status": "pass"},
        {"name": "send_intelligence_brief_email", "status": "warn"},
        {"name": "send_daily_brief_email", "status": "warn"},
        {"name": "send_team_intelligence_brief_email", "status": "warn"},
    ]
    assert morning_pipeline._compute_delivery_ok(steps) is False


def test_execution_report_does_not_claim_historical_linkedin_deltas(
    tmp_path: Path, monkeypatch,
) -> None:
    system_dir = tmp_path / "system"
    cache_dir = system_dir / ".cache"
    published_dir = system_dir / "published" / "daily"
    monkeypatch.setattr(morning_pipeline, "SYSTEM_DIR", system_dir)
    monkeypatch.setattr(morning_pipeline, "PUBLISHED_DIR", published_dir)

    _write(cache_dir / "source_health.json", {
        "brief_trustworthiness": "partial",
        "sources": {"email:personal": {"status": "refreshed", "item_count": 4}},
    })
    _write(cache_dir / "intelligence_assessment.json", {
        "assessment_date": "2026-06-07",
        "trust_stats": {"items_fetched": 6, "mutation_proposals": 2},
    })
    _write(cache_dir / "linkedin_ingest_latest.json", {
        "ingest_date": "2026-05-29",
        "delta_intelligence": {
            "graph_mutations": {
                "baseline_entries_updated": 159,
                "relationship_strength_mutations": 55,
            },
            "opportunity_detection": {"suggested_outreach_queue": [{}, {}]},
        },
    })
    _write(published_dir / "latest_brief.json", {"trust_score": 80})

    report = morning_pipeline.build_execution_report({
        "generated_at": "2026-06-07T10:00:00+00:00",
        "date": "2026-06-07",
        "mode": "brief_only",
        "ok": True,
        "steps": [{"name": "publish_canonical_artifacts", "status": "pass"}],
        "intelligence_readiness": {"status": "READY"},
    })

    assert report["records_processed"] == 10
    assert report["records_changed"] == 0
    assert report["mutations_generated"] == 2
    assert report["opportunities_generated"] == 0
    assert report["relationship_changes"] == 0
    assert report["trust_score"] == 80
    assert report["brief_rebuilt"] is True
    assert report["cycle_proof"]["gather"]["items_fetched"] == 6
    assert report["cycle_proof"]["record"]["mutation_proposals"] == 2
    assert report["cycle_proof"]["report"]["brief_rebuilt"] is True
    assert report["cycle_type"] == "daily_intelligence_monitoring"
    assert report["routine_research_performed"] is False
    assert report["intelligence_cycle_statistics"]["daily_monitoring"]["sources_scanned"] == 0
    assert report["intelligence_cycle_statistics"]["daily_monitoring"]["items_fetched"] == 6


def test_execution_report_carries_pre_brief_scan_errors(
    tmp_path: Path, monkeypatch,
) -> None:
    system_dir = tmp_path / "system"
    published_dir = system_dir / "published" / "daily"
    monkeypatch.setattr(morning_pipeline, "SYSTEM_DIR", system_dir)
    monkeypatch.setattr(morning_pipeline, "PUBLISHED_DIR", published_dir)
    _write(system_dir / ".cache" / "source_health.json", {"sources": {}})

    report = morning_pipeline.build_execution_report({
        "generated_at": "2026-06-07T10:00:00+00:00",
        "date": "2026-06-07",
        "mode": "brief_only",
        "ok": True,
        "steps": [],
        "pre_brief_scan": {
            "steps": [{
                "name": "fetch_google_accounts",
                "status": "warn",
                "result": {"stderr_tail": "token expired"},
            }],
        },
    })

    assert report["refresh_status"] == "Partial"
    assert report["processing_errors"] == [{
        "step": "fetch_google_accounts",
        "status": "warn",
        "detail": "token expired",
    }]


def test_execution_report_includes_source_observability(
    tmp_path: Path, monkeypatch,
) -> None:
    system_dir = tmp_path / "system"
    cache_dir = system_dir / ".cache"
    published_dir = system_dir / "published" / "daily"
    monkeypatch.setattr(morning_pipeline, "SYSTEM_DIR", system_dir)
    monkeypatch.setattr(morning_pipeline, "PUBLISHED_DIR", published_dir)

    _write(cache_dir / "source_health.json", {
        "generated_at": "2026-06-08T12:00:00+00:00",
        "overall_health": "green",
        "sources": {
            "calendar:personal": {
                "status": "refreshed",
                "item_count": 8,
                "last_refreshed_at": "2026-06-08T11:59:00+00:00",
                "tier": 1,
            },
            "market_signals": {
                "status": "stale",
                "item_count": 10,
                "last_refreshed_at": "2026-05-27T10:56:00-05:00",
                "reason": "last refresh exceeds 72-hour threshold",
                "tier": 3,
            },
        },
    })

    report = morning_pipeline.build_execution_report({
        "generated_at": "2026-06-08T12:00:00+00:00",
        "date": "2026-06-08",
        "mode": "full",
        "ok": True,
        "steps": [],
    })

    dashboard = report["intelligence_health_dashboard"]
    rows = {row["source_key"]: row for row in dashboard["sources"]}
    assert rows["calendar:personal"]["status"] == "Healthy"
    assert rows["calendar:personal"]["records_processed"] == 8
    assert rows["market_signals"]["status"] == "Stale"
    assert rows["market_signals"]["failure_reason"] == "last refresh exceeds 72-hour threshold"
