from __future__ import annotations

import json
from pathlib import Path

from system.scripts import entity_identity
from system.scripts import earnings_monitor
from system.scripts import web_scanner


ROOT = Path(__file__).resolve().parents[2]


def test_identity_terms_include_name_aliases_and_ticker_once():
    entity = {
        "name": "PAR Technology",
        "aliases": ["PAR", "ParTech", "par"],
        "ticker": "PAR",
    }
    assert entity_identity.identity_terms(entity) == [
        "PAR Technology",
        "PAR",
        "ParTech",
    ]


def test_attribution_terms_suppress_generic_category_alias():
    entity = {"name": "&pizza", "aliases": ["andpizza", "pizza"], "ticker": None}
    assert entity_identity.attribution_terms(entity) == ["&pizza", "andpizza"]


def test_web_scanner_does_not_attribute_generic_pizza_story_to_andpizza(monkeypatch):
    monkeypatch.setattr(
        web_scanner,
        "_graph_entity_identities",
        lambda: [("&pizza", ["&pizza", "andpizza"])],
    )
    assert web_scanner.detect_entities(
        "Mountain Mike's Pizza opens in Texas", "The pizza chain added one store."
    ) == []


def test_web_scanner_resolves_alias_to_canonical_entity(monkeypatch):
    monkeypatch.setattr(
        web_scanner,
        "_graph_entity_identities",
        lambda: [("PAR Technology", ["PAR Technology", "ParTech", "PAR"])],
    )
    matches = web_scanner.detect_entities(
        "ParTech expands its restaurant platform",
        "The company announced a new release.",
    )
    assert "PAR Technology" in matches
    assert "ParTech" not in matches


def test_search_queries_fan_out_over_full_identity_set():
    queries = entity_identity.search_queries(
        {
            "name": "PAR Technology",
            "aliases": ["ParTech", "Brink POS"],
            "ticker": "PAR",
        },
        "earnings",
    )
    assert queries == [
        '"PAR Technology" earnings',
        '"ParTech" earnings',
        '"Brink POS" earnings',
        '"PAR" earnings',
    ]


def test_private_graph_ticker_overrides_stale_calendar_ticker(tmp_path, monkeypatch):
    calendar = tmp_path / "earnings.yaml"
    calendar.write_text(
        "companies:\n"
        "  - name: Olo\n"
        "    ticker: OLO\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(earnings_monitor, "EARNINGS_CALENDAR_PATH", calendar)
    monkeypatch.setattr(
        earnings_monitor,
        "load_entities",
        lambda: [{"name": "Olo", "aliases": [], "ticker": None}],
    )
    company = earnings_monitor._load_calendar()[0]
    assert company["ticker"] is None
    assert company["search_terms"] == ["Olo"]


def test_ecosystem_entities_have_ticker_field_and_alias_backfill():
    graph = json.loads(
        (ROOT / "system" / "ecosystem_intelligence.json").read_text(encoding="utf-8")
    )
    entities = graph["entities"]
    # Not an exact count: the graph grows via ongoing vendor-evidence imports
    # (RB Vendor-First Baseline Project, 2026-08-03 onward), so a pinned
    # number breaks on every legitimate import. A floor still catches
    # catastrophic data loss (e.g. an accidental truncated write).
    assert len(entities) >= 1600
    assert all("ticker" in entity for entity in entities)
    par = next(entity for entity in entities if entity["name"] == "PAR Technology")
    assert par["ticker"] == "PAR"
    assert {"PAR", "ParTech", "Brink POS"}.issubset(set(par["aliases"]))
    olo = next(entity for entity in entities if entity["name"] == "Olo")
    assert olo["ticker"] is None
