#!/usr/bin/env python3
"""
test_backfill_confidence_scores.py — Confidence-Based Auto-Recording
(2026-09-25). Pure-logic tests against synthetic graphs -- never touches
the real ecosystem_intelligence.json (that's exercised once, deliberately,
via the script's own --confirm run, snapshotted and schema-validated by
ecosystem_intelligence._write_graph() same as any other real graph write).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import backfill_confidence_scores as bfc  # noqa: E402


def _rel(rel_id, *, level=None, score=None, has_confidence=True):
    rel = {"id": rel_id}
    if has_confidence:
        rel["confidence"] = {"level": level, "score": score, "rationale": "", "review_after": None}
    return rel


def test_find_missing_only_returns_null_score_relationships():
    graph = {"relationships": [
        _rel("rel-1", level="high", score=None),
        _rel("rel-2", level="high", score=0.85),
        _rel("rel-3", level="medium", score=None),
    ]}
    missing = bfc.find_missing(graph)
    assert {r["id"] for r in missing} == {"rel-1", "rel-3"}


def test_relationship_with_no_confidence_block_at_all_counts_as_missing():
    graph = {"relationships": [_rel("rel-1", has_confidence=False)]}
    assert len(bfc.find_missing(graph)) == 1


def test_backfill_derives_score_from_existing_level():
    graph = {"relationships": [
        _rel("rel-high", level="high", score=None),
        _rel("rel-medium", level="medium", score=None),
        _rel("rel-low", level="low", score=None),
        _rel("rel-critical", level="critical", score=None),
    ]}
    changed = bfc.backfill(graph)
    assert len(changed) == 4
    by_id = {r["id"]: r["confidence"]["score"] for r in graph["relationships"]}
    assert by_id["rel-high"] == 0.85
    assert by_id["rel-medium"] == 0.5
    assert by_id["rel-low"] == 0.2
    assert by_id["rel-critical"] == 0.95


def test_backfill_never_touches_a_relationship_that_already_has_a_score():
    graph = {"relationships": [_rel("rel-1", level="high", score=0.72)]}
    changed = bfc.backfill(graph)
    assert changed == []
    assert graph["relationships"][0]["confidence"]["score"] == 0.72


def test_backfill_defaults_unknown_or_missing_level_to_medium_band():
    graph = {"relationships": [
        _rel("rel-1", level=None, score=None),
        _rel("rel-2", level="not_a_real_level", score=None),
    ]}
    changed = bfc.backfill(graph)
    assert len(changed) == 2
    for rel in graph["relationships"]:
        assert rel["confidence"]["score"] == 0.5


def test_backfill_creates_missing_confidence_block_rather_than_erroring():
    graph = {"relationships": [_rel("rel-1", has_confidence=False)]}
    changed = bfc.backfill(graph)
    assert len(changed) == 1
    assert graph["relationships"][0]["confidence"]["score"] == 0.5


def test_backfill_report_records_before_and_after():
    graph = {"relationships": [_rel("rel-1", level="high", score=None)]}
    changed = bfc.backfill(graph)
    assert changed[0]["relationship_id"] == "rel-1"
    assert changed[0]["old_score"] is None
    assert changed[0]["new_score"] == 0.85
    assert changed[0]["level"] == "high"


def test_empty_graph_backfills_nothing():
    graph = {"relationships": []}
    assert bfc.backfill(graph) == []
