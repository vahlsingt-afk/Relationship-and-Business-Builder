from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import hunter_source_registry as registry  # noqa: E402


def test_observed_domains_are_merged_into_growing_registry():
    seed = {
        "sources": {
            "sec.gov": {
                "name": "SEC", "source_class": "government_or_regulator",
                "authority_score": 100, "topics": [], "preferred_for": ["ownership"],
            }
        }
    }
    coverage = {
        "sources": {
            "www.vendor.example": {"checked": 8, "productive": 6, "unproductive": 2},
            "sec.gov": {"checked": 2, "productive": 2, "unproductive": 0},
        }
    }
    result = registry.build_ranked_registry(seed, coverage)
    by_domain = {row["domain"]: row for row in result["ranked_sources"]}
    assert result["source_count"] == 2
    assert by_domain["vendor.example"]["checked"] == 8
    assert by_domain["vendor.example"]["source_class"] == "unclassified"


def test_authority_prevents_productive_low_grade_source_from_outranking_regulator():
    regulator = registry.score({
        "source_class": "government_or_regulator", "authority_score": 100,
        "checked": 2, "productive": 2,
    })
    forum = registry.score({
        "source_class": "public_review_or_directory", "authority_score": 30,
        "checked": 100, "productive": 100,
    })
    assert regulator["quality_score"] > forum["quality_score"]


def test_productivity_uses_smoothing_and_reports_sample_confidence():
    one_hit = registry.score({"source_class": "vendor", "checked": 1, "productive": 1})
    proven = registry.score({"source_class": "vendor", "checked": 25, "productive": 20})
    assert one_hit["productivity_score"] < 100
    assert one_hit["score_confidence"] == "low"
    assert proven["score_confidence"] in {"medium", "high"}


def test_route_prioritizes_sources_matching_research_module():
    ranked = {
        "ranked_sources": [
            {"domain": "general.example", "preferred_for": [], "quality_score": 99, "access_tier": "free_public"},
            {"domain": "filing.example", "preferred_for": ["ownership"], "quality_score": 80, "access_tier": "free_public"},
        ]
    }
    routed = registry.research_route(ranked, ["ownership"], limit=2)
    assert routed[0]["domain"] == "filing.example"


def test_route_excludes_paid_and_unknown_sources():
    ranked = {"ranked_sources": [
        {"domain": "paid.example", "preferred_for": ["ownership"], "access_tier": "paid_subscription"},
        {"domain": "unknown.example", "preferred_for": ["ownership"], "access_tier": "unknown"},
        {"domain": "free.example", "preferred_for": [], "access_tier": "free_public"},
    ]}
    assert [row["domain"] for row in registry.research_route(ranked, ["ownership"])] == ["free.example"]


def test_recursive_url_discovery_finds_nested_sources():
    urls = list(registry._urls({"records": [{"sources": ["https://example.com/a"]}]}))
    assert urls == ["https://example.com/a"]
