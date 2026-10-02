from __future__ import annotations

import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import json


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import hunter  # noqa: E402
import hunter_gap_manifest as gaps  # noqa: E402


def _brand_snapshot():
    return {
        "brands": [{
            "id": "brand-example",
            "name": "Example Burgers",
            "rank": 25,
            "unit_count": 500,
            "coverage": "partial",
            "research_gaps": ["leadership", "technology_stack"],
            "known_leadership": [],
            "known_technology": {},
        }]
    }


def _competitor_snapshot():
    return {
        "competitors": [{
            "competitor_slug": "vendor-example",
            "display_name": "Vendor Example",
            "priority": "no_pack_yet",
            "missing_evidence_category_signal": ["positioning", "reference_customer"],
            "next_verification_needed": [],
        }]
    }


def _franchisee_snapshot():
    return {
        "organizations": [{
            "id": "flynn-group",
            "name": "Flynn Group",
            "linked_graph_entity_id": "operator-flynn-group",
            "total_identified_units": 2936,
            "brand_count": 6,
            "brands": ["Pizza Hut", "Applebee's"],
            "headquarters": None,
            "coverage": "none",
            "priority": "enterprise_primary",
            "research_gaps": ["headquarters", "ownership"],
        }]
    }


def _vendor_snapshot():
    return {
        "competitors": [{
            "competitor_slug": "vendor-example",
            "display_name": "Vendor Example",
            "coverage": "partial",
            "research_gaps": ["products", "key_customers"],
            "known_products": [],
        }]
    }


def _fdd_snapshot():
    return {
        "brands": [{
            "id": "brand-example-burgers",
            "name": "Example Burgers",
            "unit_count": 500.0,
            "segment": "LSR",
            "coverage": "none",
            "research_gaps": ["fdd_governance_economics"],
        }]
    }


def test_brand_snapshot_becomes_stable_gap_ids():
    target = gaps.normalize_brand_snapshot(_brand_snapshot())[0]
    assert target["target_key"] == "company:brand-example"
    assert target["priority"] == "enterprise_primary"
    assert [gap["gap_id"] for gap in target["gaps"]] == [
        "gap:company:brand-example:leadership",
        "gap:company:brand-example:technology-stack",
    ]


def test_franchisee_snapshot_becomes_stable_gap_ids():
    target = gaps.normalize_franchisee_snapshot(_franchisee_snapshot())[0]
    assert target["target_key"] == "franchisee:flynn-group"
    assert target["entity_type"] == "restaurant_operator"
    assert target["priority"] == "enterprise_primary"
    assert [gap["gap_id"] for gap in target["gaps"]] == [
        "gap:franchisee:flynn-group:headquarters",
        "gap:franchisee:flynn-group:ownership",
    ]


def test_franchisees_universe_included_in_manifest(monkeypatch):
    monkeypatch.setattr(gaps.franchisee_exporter, "export_franchisee_research_gaps", lambda **kwargs: _franchisee_snapshot())
    manifest = gaps.build_manifest(universe="franchisees")
    assert manifest["target_count"] == 1
    assert manifest["targets"][0]["target_key"] == "franchisee:flynn-group"
    assert gaps.FRANCHISEE_SOURCE in manifest["sources"]


def test_franchisees_excluded_from_brands_only_universe(monkeypatch):
    monkeypatch.setattr(gaps.brand_exporter, "export_research_gaps", lambda **kwargs: _brand_snapshot())
    monkeypatch.setattr(gaps.franchisee_exporter, "export_franchisee_research_gaps", lambda **kwargs: _franchisee_snapshot())
    manifest = gaps.build_manifest(universe="brands")
    target_keys = {t["target_key"] for t in manifest["targets"]}
    assert target_keys == {"company:brand-example"}


def test_franchisee_manifest_validates_against_schema(monkeypatch):
    monkeypatch.setattr(gaps.franchisee_exporter, "export_franchisee_research_gaps", lambda **kwargs: _franchisee_snapshot())
    manifest = gaps.build_manifest(universe="franchisees")
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "hunter_gap_manifest.schema.json"
    schema = json.loads(schema_path.read_text())
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest))
    assert errors == []


def test_fdd_snapshot_becomes_stable_gap_ids():
    target = gaps.normalize_fdd_snapshot(_fdd_snapshot())[0]
    assert target["target_key"] == "fdd:brand-example-burgers"
    assert target["entity_type"] == "restaurant_brand"
    assert target["priority"] == "enterprise_primary"
    assert [gap["gap_id"] for gap in target["gaps"]] == [
        "gap:fdd:brand-example-burgers:fdd-governance-economics",
    ]


def test_fdd_universe_included_in_manifest(monkeypatch):
    monkeypatch.setattr(gaps.fdd_exporter, "export_fdd_target_population", lambda **kwargs: _fdd_snapshot())
    manifest = gaps.build_manifest(universe="fdd")
    assert manifest["target_count"] == 1
    assert manifest["targets"][0]["target_key"] == "fdd:brand-example-burgers"
    assert gaps.FDD_SOURCE in manifest["sources"]


def test_fdd_excluded_from_brands_only_universe(monkeypatch):
    monkeypatch.setattr(gaps.brand_exporter, "export_research_gaps", lambda **kwargs: _brand_snapshot())
    monkeypatch.setattr(gaps.fdd_exporter, "export_fdd_target_population", lambda **kwargs: _fdd_snapshot())
    manifest = gaps.build_manifest(universe="brands")
    target_keys = {t["target_key"] for t in manifest["targets"]}
    assert target_keys == {"company:brand-example"}


def test_fdd_manifest_validates_against_schema(monkeypatch):
    monkeypatch.setattr(gaps.fdd_exporter, "export_fdd_target_population", lambda **kwargs: _fdd_snapshot())
    manifest = gaps.build_manifest(universe="fdd")
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "hunter_gap_manifest.schema.json"
    schema = json.loads(schema_path.read_text())
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest))
    assert errors == []


def test_competitor_exporters_merge_without_duplicate_target():
    targets = gaps.normalize_competitor_snapshots(_competitor_snapshot(), _vendor_snapshot())
    assert len(targets) == 1
    fields = {gap["field"] for gap in targets[0]["gaps"]}
    assert {"research_pack_baseline", "positioning", "reference_customer", "products", "key_customers"} <= fields
    assert "research_pack" in targets[0]["current_state"]
    assert "extended_profile" in targets[0]["current_state"]


def test_manifest_validates_and_counts(monkeypatch):
    monkeypatch.setattr(gaps.brand_exporter, "export_research_gaps", lambda **kwargs: _brand_snapshot())
    manifest = gaps.build_manifest(universe="brands")
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "hunter_gap_manifest.schema.json"
    schema = json.loads(schema_path.read_text())
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest))
    assert errors == []
    assert manifest["target_count"] == 1
    assert manifest["gap_count"] == 2


def test_prepare_cycle_combines_plan_manifest_and_context(monkeypatch):
    manifest = {
        "schema": "rb.hunter_gap_manifest.v1",
        "generated_at": "2026-10-02T12:00:00Z",
        "sources": [gaps.BRAND_SOURCE],
        "target_count": 1,
        "gap_count": 1,
        "targets": [{
            "target_key": "company:brand-example",
            "display_name": "Example Burgers",
            "entity_type": "restaurant_brand",
            "priority": "enterprise_primary",
            "coverage": "partial",
            "current_state": {},
            "gaps": [{"gap_id": "gap:company:brand-example:leadership"}],
            "discovery_domains": ["technology stack", "leadership changes"],
        }],
    }
    monkeypatch.setattr(gaps, "build_manifest", lambda **kwargs: manifest)
    monkeypatch.setattr(hunter, "build_context", lambda keys, modules=None: {"keys": keys})
    directive = hunter.prepare_cycle(
        "enterprise_account_profile", universe="brands", limit=1,
        five_hour_used_pct=20, weekly_used_pct=25, hours_to_weekly_reset=72,
    )
    assert directive["packet_requirements"]["known_gap_ids"] == ["gap:company:brand-example:leadership"]
    assert directive["packet_requirements"]["target_keys"] == ["company:brand-example"]
    assert directive["prior_context"] == {"keys": ["company:brand-example"]}
    assert directive["resource_plan"]["status"] == "authorized"
    assert directive["resource_plan"]["preferred_execution_tier"] == "chatgpt_deep_research_economy"


def test_prepare_without_codex_usage_still_authorizes_chat_research(monkeypatch):
    manifest = {
        "schema": "rb.hunter_gap_manifest.v1",
        "generated_at": "2026-10-02T12:00:00Z",
        "sources": [gaps.BRAND_SOURCE],
        "target_count": 1,
        "gap_count": 0,
        "targets": [{
            "target_key": "company:brand-example",
            "display_name": "Example Burgers",
            "entity_type": "restaurant_brand",
            "priority": "enterprise_primary",
            "coverage": "partial",
            "current_state": {},
            "gaps": [],
            "discovery_domains": ["technology stack"],
        }],
    }
    monkeypatch.setattr(gaps, "build_manifest", lambda **kwargs: manifest)
    monkeypatch.setattr(hunter, "build_context", lambda keys, modules=None: {"keys": keys})
    directive = hunter.prepare_cycle("enterprise_account_profile", universe="brands", limit=1)
    assert directive["resource_plan"]["status"] == "authorized"
    assert directive["resource_plan"]["chat_research_status"] == "authorized"
    assert directive["resource_plan"]["codex_work_status"] == "blocked"
    assert directive["packet_requirements"]["research_authorized"] is True
    assert directive["packet_requirements"]["target_keys"] == ["company:brand-example"]


def _gatherer_packet():
    return {
        "contract": "rb.gatherer_daily_change_packet.v1",
        "packet_id": "gatherer-20261002T120000Z",
        "generated_at": "2026-10-02T12:00:00Z",
        "changes": [
            {"change_id": "gchg-aaa", "title": "Brand A deploys new platform", "source_url": "https://example.com/a",
             "novelty": "new_to_rbb", "materiality": 95, "scores": {"impact": 85}, "corroborating_sources": []},
            {"change_id": "gchg-bbb", "title": "Brand B names new CTO", "source_url": "https://example.com/b",
             "novelty": "new_to_rbb", "materiality": 75, "scores": {"impact": 75}, "corroborating_sources": []},
        ],
        "hunter_escalations": [
            {"change_id": "gchg-aaa", "target_names": ["Brand A"], "recommended_playbook": "customer_deployment_validation",
             "reason": "Material 24-hour change signal requires source verification before canonical use.", "priority": "high"},
            {"change_id": "gchg-bbb", "target_names": ["Brand B"], "recommended_playbook": "leadership_decision_map",
             "reason": "Material 24-hour change signal requires source verification before canonical use.", "priority": "medium"},
        ],
    }


def test_prepare_gatherer_cycle_filters_by_playbook_and_builds_directive(monkeypatch):
    monkeypatch.setattr(hunter, "build_context", lambda keys, modules=None: {"keys": keys})
    directive = hunter.prepare_gatherer_cycle(
        "customer_deployment_validation", packet=_gatherer_packet(),
        five_hour_used_pct=20, weekly_used_pct=25, hours_to_weekly_reset=72,
    )
    assert directive["source"] == "gatherer"
    assert directive["packet_id"] == "gatherer-20261002T120000Z"
    targets = directive["gap_manifest"]["targets"]
    assert len(targets) == 1
    assert targets[0]["target_key"] == "gatherer:gchg-aaa"
    assert targets[0]["display_name"] == "Brand A"
    assert targets[0]["current_state"]["materiality"] == 95
    assert directive["packet_requirements"]["target_keys"] == ["gatherer:gchg-aaa"]
    assert directive["packet_requirements"]["known_gap_ids"] == ["gap:gatherer:gchg-aaa:verification"]
    assert directive["packet_requirements"]["discovery_domains"] == ["https://example.com/a"]
    assert directive["prior_context"] == {"keys": ["gatherer:gchg-aaa"]}
    assert directive["resource_plan"]["status"] == "authorized"


def test_prepare_gatherer_cycle_respects_limit(monkeypatch):
    monkeypatch.setattr(hunter, "build_context", lambda keys, modules=None: {"keys": keys})
    packet = _gatherer_packet()
    packet["hunter_escalations"].append(
        {"change_id": "gchg-ccc", "target_names": ["Brand C"], "recommended_playbook": "customer_deployment_validation",
         "reason": "r", "priority": "medium"})
    directive = hunter.prepare_gatherer_cycle("customer_deployment_validation", packet=packet, limit=1)
    assert len(directive["gap_manifest"]["targets"]) == 1


def test_prepare_gatherer_cycle_empty_when_no_escalations_match_playbook(monkeypatch):
    monkeypatch.setattr(hunter, "build_context", lambda keys, modules=None: {"keys": keys})
    directive = hunter.prepare_gatherer_cycle("ownership_funding_ma", packet=_gatherer_packet())
    assert directive["gap_manifest"]["targets"] == []
    assert directive["packet_requirements"]["target_keys"] == []
