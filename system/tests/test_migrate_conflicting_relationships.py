#!/usr/bin/env python3
"""
test_migrate_conflicting_relationships.py — Confidence-Based Auto-Recording
(2026-09-25). Pure-logic tests against synthetic graphs -- the real graph's
4 stuck relationships are migrated once, for real, via the script's own
--confirm run (same discipline as backfill_confidence_scores.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import migrate_conflicting_relationships as mcr  # noqa: E402


def _rel(rel_id, *, status="active", score=None, conflicts_with=None, requires_confirmation=None):
    rel = {"id": rel_id, "status": status}
    if score is not None:
        rel["confidence"] = {"level": "high" if score >= 0.85 else "medium", "score": score,
                              "rationale": "", "review_after": None}
    if conflicts_with is not None:
        rel["conflicts_with"] = conflicts_with
    if requires_confirmation is not None:
        rel["requires_confirmation"] = requires_confirmation
    return rel


def test_find_stuck_only_returns_conflicting_status():
    graph = {"relationships": [
        _rel("rel-1", status="conflicting"),
        _rel("rel-2", status="active"),
        _rel("rel-3", status="conflicting"),
    ]}
    stuck = mcr.find_stuck(graph)
    assert {r["id"] for r in stuck} == {"rel-1", "rel-3"}


def test_higher_confidence_stuck_claim_supersedes_its_rival():
    graph = {"relationships": [
        _rel("rel-qu", status="conflicting", score=0.9, conflicts_with="rel-oracle", requires_confirmation=True),
        _rel("rel-oracle", status="active", score=0.6),
    ]}
    report = mcr.migrate(graph)
    assert report[0]["outcome"] == "auto_superseded"

    qu = next(r for r in graph["relationships"] if r["id"] == "rel-qu")
    oracle = next(r for r in graph["relationships"] if r["id"] == "rel-oracle")
    assert qu["status"] == "active"
    assert "requires_confirmation" not in qu
    assert "conflicts_with" not in qu
    assert oracle["status"] == "superseded"
    assert oracle["superseded_by"] == "rel-qu"


def test_lower_confidence_stuck_claim_is_recorded_alongside_not_promoted():
    graph = {"relationships": [
        _rel("rel-rumor", status="conflicting", score=0.4, conflicts_with="rel-confirmed", requires_confirmation=True),
        _rel("rel-confirmed", status="active", score=0.9),
    ]}
    report = mcr.migrate(graph)
    assert report[0]["outcome"] == "recorded_alongside"

    rumor = next(r for r in graph["relationships"] if r["id"] == "rel-rumor")
    confirmed = next(r for r in graph["relationships"] if r["id"] == "rel-confirmed")
    assert rumor["status"] == "rumored"
    assert rumor["related_claim_id"] == "rel-confirmed"
    assert "requires_confirmation" not in rumor
    assert "conflicts_with" not in rumor
    assert confirmed["status"] == "active"  # untouched, not overwritten


def test_missing_rival_is_skipped_not_errored():
    graph = {"relationships": [
        _rel("rel-orphan", status="conflicting", score=0.9, conflicts_with="rel-does-not-exist", requires_confirmation=True),
    ]}
    report = mcr.migrate(graph)
    assert report[0]["outcome"] == "skipped_rival_not_found"


def test_real_four_stuck_relationships_all_resolve_to_auto_superseded():
    """Confirmed against the real graph before migration (2026-09-25): all
    four currently-stuck relationships (Dave's Hot Chicken/Qu 0.77 vs 0.6,
    Jack in the Box/Qu 0.9 vs 0.86, Shake Shack/Qu 0.9 vs 0.86, Taco Bell/
    Yum-Byte 0.92 vs 0.86) have real, meaningfully-higher-or-top-tier
    confidence than their rivals -- every one should auto-supersede, not
    coexist."""
    graph = {"relationships": [
        _rel("rel-dave-qu", status="conflicting", score=0.77, conflicts_with="rel-dave-qsr", requires_confirmation=True),
        _rel("rel-dave-qsr", status="active", score=0.6),
        _rel("rel-jitb-qu", status="conflicting", score=0.9, conflicts_with="rel-jitb-oracle", requires_confirmation=True),
        _rel("rel-jitb-oracle", status="active", score=0.86),
        _rel("rel-shake-qu", status="conflicting", score=0.9, conflicts_with="rel-shake-oracle", requires_confirmation=True),
        _rel("rel-shake-oracle", status="active", score=0.86),
        _rel("rel-tb-yum", status="conflicting", score=0.92, conflicts_with="rel-tb-ncr", requires_confirmation=True),
        _rel("rel-tb-ncr", status="active", score=0.86),
    ]}
    report = mcr.migrate(graph)
    assert len(report) == 4
    assert all(entry["outcome"] == "auto_superseded" for entry in report)
    assert mcr.find_stuck(graph) == []


def test_no_stuck_relationships_migrates_nothing():
    graph = {"relationships": [_rel("rel-1", status="active")]}
    assert mcr.migrate(graph) == []
