from datetime import date
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
import intelligence_cycle_health as health


def test_quiet_but_collected_cycle_is_healthy(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "CACHE_PATH", tmp_path / "health.json")
    execution = {"refresh_status": "Success", "intelligence_cycle_statistics": {
        "daily_monitoring": {"sources_scanned": 12}}}
    result = health.assess(execution_report=execution, source_health={"sources": {}},
                           trace_audit={"status": "no_material_events"}, downstream={},
                           today=date(2026, 9, 14))
    assert result["status"] == "healthy"


def test_broken_trace_or_downstream_failure_is_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "CACHE_PATH", tmp_path / "health.json")
    execution = {"refresh_status": "Success", "intelligence_cycle_statistics": {
        "daily_monitoring": {"sources_scanned": 5}}}
    result = health.assess(execution_report=execution, source_health={"sources": {}},
                           trace_audit={"status": "degraded", "incomplete_traces": 1},
                           downstream={"safe_failed": 1}, today=date(2026, 9, 14))
    assert result["status"] == "failed"
    assert result["critical_alerts"] == 2


def test_zero_scan_and_priority_stale_source_are_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "CACHE_PATH", tmp_path / "health.json")
    execution = {"refresh_status": "Success", "intelligence_cycle_statistics": {
        "daily_monitoring": {"sources_scanned": 0}}}
    sources = {"sources": {"market_signals": {
        "tier": 3, "status": "stale", "days_unhealthy": 2, "reason": "old"}}}
    result = health.assess(execution_report=execution, source_health=sources,
                           trace_audit={"status": "no_material_events"}, downstream={},
                           today=date(2026, 9, 14))
    assert result["status"] == "degraded"
    assert result["warnings"] == 2
    assert result["priority_source_blind_spots"][0]["source"] == "market_signals"


def test_known_gp_manual_only_feeds_do_not_raise_false_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "CACHE_PATH", tmp_path / "health.json")
    execution = {"refresh_status": "Success", "intelligence_cycle_statistics": {
        "daily_monitoring": {"sources_scanned": 5}}}
    sources = {"sources": {"email:global-payments": {
        "tier": 1, "status": "skipped_no_raw_input", "days_unhealthy": 8}}}
    result = health.assess(execution_report=execution, source_health=sources,
                           trace_audit={"status": "no_material_events"}, downstream={},
                           today=date(2026, 9, 14))
    assert result["status"] == "healthy"
    assert result["accepted_manual_limitations"][0]["source"] == "email:global-payments"


def test_live_web_scanner_supersedes_stale_manual_market_inbox(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "CACHE_PATH", tmp_path / "health.json")
    execution = {"refresh_status": "Success", "intelligence_cycle_statistics": {
        "daily_monitoring": {"sources_scanned": 15}}}
    sources = {"sources": {
        "web_scanner": {"tier": 3, "status": "refreshed"},
        "market_signals": {"tier": 3, "status": "stale", "days_unhealthy": 2},
    }}
    result = health.assess(execution_report=execution, source_health=sources,
                           trace_audit={"status": "no_material_events"}, downstream={},
                           today=date(2026, 9, 14))
    assert result["status"] == "healthy"
    assert result["accepted_manual_limitations"][0]["mode"] == "superseded_by_live_web_scanner"
