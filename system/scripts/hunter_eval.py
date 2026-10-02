#!/usr/bin/env python3
"""Score Hunter guard results against the maintained evaluation corpus."""
from __future__ import annotations

import json
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent.parent
CORPUS_PATH = SYSTEM_DIR / "research" / "hunter_evaluation_corpus.json"


def score(results: dict) -> dict:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    observed = results.get("cases") or {}
    rows = []
    for case in corpus["cases"]:
        guard = case["expected_guard"]
        passed = guard in (observed.get(case["case_id"]) or [])
        rows.append({**case, "passed": passed})
    passed = sum(row["passed"] for row in rows)
    return {"schema": "rb.hunter_evaluation_score.v1", "passed": passed, "total": len(rows),
            "score_pct": round(100 * passed / len(rows), 1) if rows else 100.0, "cases": rows}
