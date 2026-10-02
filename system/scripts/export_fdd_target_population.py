#!/usr/bin/env python3
"""
export_fdd_target_population.py — 2026-10-02.

Exports a compact, hand-to-ChatGPT-Deep-Research JSON snapshot of the FDD
Technology Governance & Economics program's canonical research population
(system/technology_lifecycle/FDD_GOVERNANCE_ECONOMICS_BRIEF.md, §20
"Incoming Research Program": "every canonical RBB restaurant brand with
more than 90 locations, processed in batches of 25... RBB should provide
the canonical population and IDs").

Real gap this closes: a live Codex/ChatGPT-Project Hunter task running the
first FDD research cycle had no local source for this population and
reached for the live Trusted Chat API to get it, hitting the same DNS
failure (that hostname only resolves when Todd's Mac is running the
tunnel) already fixed once for Franchisee Finder's equivalent gap
(export_franchisee_research_gaps.py, 2026-10-02). Same fix, same reason:
this export is 100% local (reads only system/ecosystem_intelligence.json
and system/technology_lifecycle/fdd_sources.jsonl), matching
hunter_cycle.py's documented network-free prepare workflow.

Population: every ecosystem_intelligence.json entity_type:"brand" whose
attributes.unit_count exceeds --unit-threshold (default 90, the same
">90-location target queue" criterion named in both this incident and the
franchisee one). Coverage is computed against fdd_sources.jsonl -- a
brand with at least one non-superseded, non-"not_located" FDD source
record on file is "has_fdd_source"; everything else is "none" (the
honest, correct answer for the program's actual first live cycle, where
fdd_sources.jsonl is still empty for the whole population).

Batches of 25, sorted by unit_count descending (largest, most
enterprise-significant brands first) -- matches brief §20's fixed batch
size exactly, so `batches[0]` is ready to hand to the first real research
cycle without any further slicing.

Usage:
    python3 export_fdd_target_population.py --output PATH [--only-gaps] [--unit-threshold 90]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import technology_lifecycle as tl  # noqa: E402

DEFAULT_UNIT_THRESHOLD = 90
BATCH_SIZE = 25

_NOT_COVERING_STATUSES = {"not_located", "superseded"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _brand_unit_count(entity: dict) -> float:
    return (entity.get("attributes") or {}).get("unit_count") or 0


def _fdd_covered_brand_ids() -> set[str]:
    """Brand entity ids with at least one current/usable FDD source record
    on file -- a record whose document_status isn't itself a non-coverage
    marker (not_located: nothing was ever found; superseded: a newer
    document replaced it and is what should be cited instead)."""
    sources = tl.load_records(tl.FDD_SOURCES_PATH) if tl.FDD_SOURCES_PATH.exists() else []
    covered = set()
    for rec in sources:
        if rec.get("document_status") in _NOT_COVERING_STATUSES:
            continue
        brand_id = rec.get("brand_id")
        if brand_id:
            covered.add(brand_id)
    return covered


def _brand_record(entity: dict, *, covered_ids: set[str]) -> dict:
    unit_count = _brand_unit_count(entity)
    brand_id = entity["id"]
    has_source = brand_id in covered_ids
    return {
        "id": brand_id,
        "name": entity.get("name") or brand_id,
        "unit_count": unit_count,
        "segment": (entity.get("attributes") or {}).get("segment"),
        "coverage": "has_fdd_source" if has_source else "none",
        "research_gaps": [] if has_source else ["fdd_governance_economics"],
    }


_MISSION = (
    "This file is RBB's canonical FDD Technology Governance & Economics research population -- every "
    "restaurant brand RBB tracks with more than {threshold} system locations (the enterprise franchise "
    "population this program covers), each with a coverage flag. Your mission, per "
    "system/technology_lifecycle/FDD_GOVERNANCE_ECONOMICS_BRIEF.md: for entries with coverage \"none\", "
    "locate and review that brand's current (and where available, prior-year) FDD, and extract technology "
    "governance, economics, franchisor change authority, and permanent brand facts per the brief's §2-§12. "
    "Process in fixed batches of 25 (see `batches` below) -- do not mix brands across batches. Cite a "
    "source_url and effective/accessed date for every finding; preserve disclosed ranges rather than "
    "inventing point estimates; do not treat the newest document found as current without checking its "
    "effective date."
)


def export_fdd_target_population(*, only_gaps: bool = False, unit_threshold: int = DEFAULT_UNIT_THRESHOLD) -> dict:
    graph = tl._load_graph()
    covered_ids = _fdd_covered_brand_ids()
    population = [
        e for e in graph.get("entities") or []
        if e.get("entity_type") == "brand" and _brand_unit_count(e) > unit_threshold
    ]
    all_records = [_brand_record(e, covered_ids=covered_ids) for e in population]
    all_records.sort(key=lambda r: -r["unit_count"])

    coverage_summary = {
        "total_brands": len(all_records),
        "has_fdd_source": sum(1 for r in all_records if r["coverage"] == "has_fdd_source"),
        "none": sum(1 for r in all_records if r["coverage"] == "none"),
    }
    records = [r for r in all_records if r["coverage"] != "has_fdd_source"] if only_gaps else all_records
    batches = [records[i:i + BATCH_SIZE] for i in range(0, len(records), BATCH_SIZE)]

    return {
        "generated_at": _now_iso(),
        "unit_threshold": unit_threshold,
        "batch_size": BATCH_SIZE,
        "mission": _MISSION.format(threshold=unit_threshold),
        "coverage_summary": coverage_summary,
        "brands": records,
        "batches": batches,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--only-gaps", action="store_true")
    parser.add_argument("--unit-threshold", type=int, default=DEFAULT_UNIT_THRESHOLD)
    args = parser.parse_args()

    data = export_fdd_target_population(only_gaps=args.only_gaps, unit_threshold=args.unit_threshold)
    Path(args.output).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {len(data['brands'])} FDD-population brand records ({len(data['batches'])} batches of {BATCH_SIZE}) to {args.output}")
    print(json.dumps(data["coverage_summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
