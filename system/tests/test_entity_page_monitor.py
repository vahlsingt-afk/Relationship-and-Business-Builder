from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
import entity_page_monitor as monitor


def test_page_monitor_baselines_then_detects_material_change(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(monitor, "RESULT_PATH", tmp_path / "result.json")
    monkeypatch.setattr(monitor.ei, "_read_graph", lambda: {"entities": [], "relationships": []})
    monkeypatch.setattr(monitor, "page_registry", lambda graph: [{"entity": "Brand A", "entity_id": "brand-a", "url": "https://a.example/tech"}])
    first = monitor.run(today=date(2026, 9, 14), fetcher=lambda url: "Technology partners")
    second = monitor.run(today=date(2026, 9, 15), fetcher=lambda url: "Technology partners request for proposal")
    assert first["baselined"] == 1
    assert len(second["changes"]) == 1
    assert second["changes"][0]["material_keywords_detected"] is True


def test_registry_excludes_press_release_article(monkeypatch):
    monkeypatch.setattr(monitor, "CONFIG_PATH", Path("/nonexistent"))
    monkeypatch.setattr(monitor, "STRATEGIC_CONFIG_PATH", Path("/nonexistent"))
    monkeypatch.setattr(monitor, "HIGH_VALUE_CONFIG_PATH", Path("/nonexistent"))
    monkeypatch.setattr(monitor, "EARLIEST_SIGNAL_CONFIG_PATH", Path("/nonexistent"))
    graph = {
        "entities": [{"id": "brand-a", "name": "Brand A", "entity_type": "brand"}],
        "relationships": [{"from_entity_id": "brand-a", "source_assertions": [{
            "source_type": "primary_operator_statement",
            "url": "https://www.businesswire.com/news/home/123/example",
        }]}],
    }
    assert monitor.page_registry(graph) == []


def test_registry_does_not_assign_vendor_announcement_page_to_brand(monkeypatch):
    monkeypatch.setattr(monitor, "CONFIG_PATH", Path("/nonexistent"))
    monkeypatch.setattr(monitor, "STRATEGIC_CONFIG_PATH", Path("/nonexistent"))
    monkeypatch.setattr(monitor, "HIGH_VALUE_CONFIG_PATH", Path("/nonexistent"))
    monkeypatch.setattr(monitor, "EARLIEST_SIGNAL_CONFIG_PATH", Path("/nonexistent"))
    graph = {
        "entities": [{"id": "brand-a", "name": "Brand A", "entity_type": "brand"}],
        "relationships": [{"from_entity_id": "brand-a", "source_assertions": [{
            "source_type": "primary_vendor_announcement", "url": "https://vendor.example/",
        }]}],
    }
    assert monitor.page_registry(graph) == []


def test_word_diff_suppresses_listing_count_noise():
    # Reconstruction of the 2026-09-15 vendor-toast careers/jobs-search excerpt:
    # a dynamic listing page whose only delta is facet item counts churning,
    # which previously leaked through as an unreadable "-(46 -items) -46 ..." string.
    old_text = "Search jobs (46 items) 46 (20 items) 20 (12 items) 12 Total 59 results"
    new_text = "Search jobs (44 items) 44 (18 items) 18 (11 items) 11 Total 57 results"
    excerpt, changed_text, is_noise = monitor._word_diff(old_text, new_text)
    assert is_noise is True
    assert not monitor.MATERIAL_RE.search(changed_text)


def test_word_diff_produces_readable_excerpt_for_real_change():
    old_text = "Toast technology team focuses on point of sale integration"
    new_text = "Toast technology team focuses on point of sale integration and payments platform modernization"
    excerpt, changed_text, is_noise = monitor._word_diff(old_text, new_text)
    assert is_noise is False
    assert excerpt == "Added: and payments platform modernization"


def test_page_monitor_routes_listing_count_noise_to_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(monitor, "RESULT_PATH", tmp_path / "result.json")
    monkeypatch.setattr(monitor.ei, "_read_graph", lambda: {"entities": [], "relationships": []})
    monkeypatch.setattr(monitor, "page_registry", lambda graph: [{
        "entity": "Toast", "entity_id": "vendor-toast",
        "url": "https://careers.toasttab.com/jobs/search/search-page-r-d",
    }])
    texts = iter([
        "Search jobs (46 items) 46 (20 items) 20 (12 items) 12 Total 59 results",
        "Search jobs (44 items) 44 (18 items) 18 (11 items) 11 Total 57 results",
    ])
    fetcher = lambda url: next(texts)
    monitor.run(today=date(2026, 9, 14), fetcher=fetcher)
    second = monitor.run(today=date(2026, 9, 15), fetcher=fetcher)
    assert second["changes"] == []
    assert second["unchanged"] == [{
        "entity": "Toast", "url": "https://careers.toasttab.com/jobs/search/search-page-r-d",
        "finding": "checked_listing_count_only",
    }]


def test_strategic_registry_covers_every_approved_public_source_category():
    rows = monitor._load(monitor.STRATEGIC_CONFIG_PATH)["sources"]
    categories = {row["source_category"] for row in rows}
    assert categories == {
        "company_job_boards", "vendor_status", "product_release_notes",
        "investor_relations", "app_stores", "partner_marketplaces", "patents",
        "official_fdd_portals", "consumer_demand", "payment_economics",
        "conference_signals", "procurement_and_permits",
    }
    assert len(rows) <= monitor.MAX_PAGES_PER_RUN


def test_high_value_registry_covers_every_approved_gap_category():
    rows = monitor._load(monitor.HIGH_VALUE_CONFIG_PATH)["sources"]
    categories = {row["source_category"] for row in rows}
    assert categories == {
        "major_franchisees", "location_and_menu_deltas", "financial_distress",
        "concession_procurement", "trademarks", "vendor_customer_proof",
        "cyber_trust", "franchisee_sentiment",
    }
    combined = (
        len(monitor._load(monitor.CONFIG_PATH).get("pages") or [])
        + len(monitor._load(monitor.STRATEGIC_CONFIG_PATH).get("sources") or [])
        + len(rows)
    )
    assert combined <= monitor.MAX_PAGES_PER_RUN


def test_earliest_signal_registry_covers_every_approved_category():
    rows = monitor._load(monitor.EARLIEST_SIGNAL_CONFIG_PATH)["sources"]
    categories = {row["source_category"] for row in rows}
    assert categories == {
        "private_capital", "domain_certificate_activity", "developer_ecosystem",
        "pricing_and_terms", "technical_labor_filings", "hardware_authorizations",
        "mobile_app_technical", "development_and_permits", "competitor_sales_motion",
        "leadership_network",
    }
    combined = sum(len(monitor._load(path).get(key) or []) for path, key in (
        (monitor.CONFIG_PATH, "pages"),
        (monitor.STRATEGIC_CONFIG_PATH, "sources"),
        (monitor.HIGH_VALUE_CONFIG_PATH, "sources"),
        (monitor.EARLIEST_SIGNAL_CONFIG_PATH, "sources"),
    ))
    assert combined <= monitor.MAX_PAGES_PER_RUN
