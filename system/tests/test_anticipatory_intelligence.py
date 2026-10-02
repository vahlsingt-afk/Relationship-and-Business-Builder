from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import anticipatory_signal_synthesis as synthesis
import advertiser_target_audit as advertiser
import distress_filing_monitor as distress
import newsletter_delivery_audit as newsletter
import public_artifact_monitor as artifacts
import render_intelligence_brief as renderer
import sitemap_signal_monitor as sitemap


def test_newsletter_configuration_does_not_claim_delivery(tmp_path, monkeypatch):
    config = tmp_path / "expected.json"
    cache = tmp_path / "observed.json"
    output = tmp_path / "audit.json"
    config.write_text(json.dumps({"newsletters": [{"name": "QSR AM Jolt", "aliases": ["AM Jolt"], "cadence_days": 1}]}))
    cache.write_text(json.dumps({"sources": []}))
    monkeypatch.setattr(newsletter, "CONFIG_PATH", config)
    monkeypatch.setattr(newsletter, "REGISTRY_PATH", cache)
    monkeypatch.setattr(newsletter, "RESULT_PATH", output)
    result = newsletter.run(today=date(2026, 9, 16))
    assert result["newsletters"][0]["status"] == "not_seen"


def test_sitemap_monitor_baselines_then_detects_new_material_url(tmp_path, monkeypatch):
    config, state, output = tmp_path / "config.json", tmp_path / "state.json", tmp_path / "result.json"
    config.write_text(json.dumps({"sitemaps": [{"entity": "Toast", "url": "https://example.com/sitemap.xml"}]}))
    monkeypatch.setattr(sitemap, "CONFIG_PATH", config)
    monkeypatch.setattr(sitemap, "STATE_PATH", state)
    monkeypatch.setattr(sitemap, "RESULT_PATH", output)
    first = b"<urlset><url><loc>https://example.com/news/old</loc></url></urlset>"
    second = b"<urlset><url><loc>https://example.com/news/old</loc></url><url><loc>https://example.com/partners/new</loc></url></urlset>"
    sitemap.run(today=date(2026, 9, 15), fetcher=lambda _url: first)
    result = sitemap.run(today=date(2026, 9, 16), fetcher=lambda _url: second)
    assert result["new_candidates"][0]["url"].endswith("/partners/new")


def test_public_artifact_monitor_archives_new_document_after_baseline(tmp_path, monkeypatch):
    config, state, output = tmp_path / "config.json", tmp_path / "state.json", tmp_path / "result.json"
    archive = tmp_path / "archive"
    config.write_text(json.dumps({"sources": [{"entity": "Airport", "url": "https://example.com/board", "source_category": "procurement_and_board_packets"}]}))
    monkeypatch.setattr(artifacts, "CONFIG_PATH", config)
    monkeypatch.setattr(artifacts, "STATE_PATH", state)
    monkeypatch.setattr(artifacts, "RESULT_PATH", output)
    monkeypatch.setattr(artifacts, "ARCHIVE_ROOT", archive)
    pages = {"https://example.com/board": b'<a href="old.pdf">Old agenda</a>'}
    artifacts.run(today=date(2026, 9, 15), fetcher=lambda url: pages[url])
    pages["https://example.com/board"] = b'<a href="old.pdf">Old agenda</a><a href="new.pdf">New RFP packet</a>'
    pages["https://example.com/new.pdf"] = b"%PDF-new"
    result = artifacts.run(today=date(2026, 9, 16), fetcher=lambda url: pages[url])
    assert result["new_candidates"][0]["status"] == "pending_review"
    assert list(archive.rglob("*new.pdf"))


def test_early_signal_renderer_shows_correlated_signal():
    payload = {"signals": [{"entity": "Brand A", "confidence": "medium", "signal_types": ["hiring", "pricing"], "expected_horizon": "weeks", "evidence_chain": [{"url": "https://example.com", "title": "Evidence"}], "what_confirms": "A named deployment."}]}
    text = renderer._render_early_signals(payload)
    assert "Before the Headlines" in text
    assert "Brand A" in text


def test_pipeline_wires_anticipatory_steps():
    text = (Path(__file__).parents[1] / "scripts" / "morning_pipeline.py").read_text()
    for name in ("sitemap_signal_monitor", "newsletter_delivery_audit", "public_artifact_monitor", "distress_filing_monitor", "advertiser_target_audit", "anticipatory_signal_synthesis"):
        assert name in text


def test_distress_monitor_baselines_then_matches_tracked_entity(tmp_path, monkeypatch):
    config, accounts, competitors = tmp_path / "config.json", tmp_path / "accounts.json", tmp_path / "competitors.json"
    state, output = tmp_path / "state.json", tmp_path / "result.json"
    config.write_text(json.dumps({"sources": [{"name": "Official WARN", "url": "https://example.test/warn", "source_type": "warn_notice"}]}))
    accounts.write_text(json.dumps({"registry": [{"aliases": ["Brand Alpha", "Alpha Grill"]}]}))
    competitors.write_text(json.dumps({"registry": []}))
    for name, value in (("CONFIG_PATH", config), ("ACCOUNT_REGISTRY", accounts), ("COMPETITOR_REGISTRY", competitors), ("STATE_PATH", state), ("RESULT_PATH", output)):
        monkeypatch.setattr(distress, name, value)
    distress.run(today=date(2026, 9, 15), fetcher=lambda _url: "Existing employer")
    result = distress.run(today=date(2026, 9, 16), fetcher=lambda _url: "Existing employer Brand Alpha closure 50 employees")
    assert result["new_candidates"][0]["entity"] == "Brand Alpha"
    assert result["new_candidates"][0]["status"] == "pending_review"


def test_advertiser_audit_does_not_overstate_keyword_search(tmp_path, monkeypatch):
    config, output = tmp_path / "ads.json", tmp_path / "coverage.json"
    config.write_text(json.dumps({"targets": [{"entity": "Toast", "domain": "toasttab.com"}]}))
    monkeypatch.setattr(advertiser, "CONFIG_PATH", config)
    monkeypatch.setattr(advertiser, "RESULT_PATH", output)
    result = advertiser.run(today=date(2026, 9, 16))
    assert result["summary"] == {"targets": 1, "verified": 0, "needs_identity": 1}
    assert result["targets"][0]["monitoring_verified"] is False
