#!/usr/bin/env python3
"""Build Hunter's authoritative gap manifest from RBB's live gap exporters."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import export_research_gaps as brand_exporter  # noqa: E402
import export_competitor_research_gaps as competitor_exporter  # noqa: E402
import export_vendor_extended_profile_gaps as vendor_exporter  # noqa: E402


SCHEMA = "rb.hunter_gap_manifest.v1"
BRAND_SOURCE = "system/scripts/export_research_gaps.py"
COMPETITOR_SOURCE = "system/scripts/export_competitor_research_gaps.py"
VENDOR_SOURCE = "system/scripts/export_vendor_extended_profile_gaps.py"

_FIELD_QUESTIONS = {
    "leadership": "Who currently leads the company and the functions relevant to restaurant technology decisions?",
    "scale": "What is the current enterprise footprint, using a dated and clearly defined unit measure?",
    "technology_stack": "Which restaurant-technology products and vendors are currently selected, piloted, rolling out, or live, at what scope?",
    "franchise_disclosure": "What do current public franchise disclosures say about required technology, approved vendors, fees, replacement obligations, and governance?",
    "general_evidence": "What material public facts improve RBB's understanding of this restaurant brand?",
    "products": "What products does the company currently sell to restaurants, and how are they positioned?",
    "strengths": "Which strengths are supported by customer-controlled or independent evidence?",
    "key_customers": "Which named restaurant customers are publicly supported, at what deployment scope and currentness?",
    "recent_news": "What material company, product, customer, ownership, funding, or operating changes are recent?",
    "trends": "Which dated market or company trends materially affect this vendor?",
    "positioning": "How is the company positioned in restaurant technology, separating its claims from independent evidence?",
    "weakness": "What independently supported weaknesses or implementation risks are public?",
    "pricing": "What public pricing, fees, or commercial-model evidence exists?",
    "reference_customer": "Which named restaurant references are verified and current?",
    "market_share": "What credible public evidence exists about market presence or installed footprint?",
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "gap"


def _importance(field: str) -> str:
    if field in {"technology_stack", "key_customers", "reference_customer", "franchise_disclosure"}:
        return "high"
    if field in {"leadership", "scale", "products", "strengths", "positioning", "recent_news"}:
        return "medium"
    return "low"


def _gap(target_key: str, field: str, gap_type: str, source: str, question: str | None = None) -> dict:
    return {
        "gap_id": f"gap:{target_key}:{_slug(field)}",
        "field": field,
        "gap_type": gap_type,
        "question": question or _FIELD_QUESTIONS.get(field) or f"What current public evidence resolves: {field}?",
        "importance": _importance(field),
        "source_exporter": source,
    }


def normalize_brand_snapshot(snapshot: dict) -> list[dict]:
    out = []
    for row in snapshot.get("brands") or []:
        target_key = f"company:{row['id']}"
        units = row.get("unit_count") or 0
        priority = "enterprise_primary" if units and units > 100 else "secondary"
        gaps = [_gap(target_key, field, "missing", BRAND_SOURCE) for field in row.get("research_gaps") or []]
        out.append({
            "target_key": target_key,
            "display_name": row.get("name") or row["id"],
            "entity_type": "restaurant_brand",
            "priority": priority,
            "coverage": row.get("coverage") or "unknown",
            "current_state": {key: value for key, value in row.items() if key not in {"research_gaps", "coverage"}},
            "gaps": gaps,
            "discovery_domains": ["ownership and leadership changes", "restaurant footprint and financial health", "technology stack and lifecycle", "franchise governance", "new restaurant-industry signals"],
        })
    return out


def normalize_competitor_snapshots(research_snapshot: dict, vendor_snapshot: dict) -> list[dict]:
    combined: dict[str, dict] = {}
    for row in research_snapshot.get("competitors") or []:
        slug = row.get("competitor_slug")
        if not slug:
            continue
        target_key = f"competitor:{slug}"
        gaps = []
        if row.get("priority") == "no_pack_yet":
            gaps.append(_gap(target_key, "research_pack_baseline", "missing", COMPETITOR_SOURCE,
                             "What complete current public baseline should RBB hold for this restaurant-technology company?"))
        for field in row.get("missing_evidence_category_signal") or []:
            gaps.append(_gap(target_key, field, "missing", COMPETITOR_SOURCE))
        for index, question in enumerate(row.get("next_verification_needed") or []):
            gaps.append(_gap(target_key, f"verification-{index + 1}-{question}", "verification_needed", COMPETITOR_SOURCE, str(question)))
        combined[target_key] = {
            "target_key": target_key,
            "display_name": row.get("display_name") or slug,
            "entity_type": "restaurant_technology_company",
            "priority": "explicit",
            "coverage": row.get("priority") or "unknown",
            "current_state": {"research_pack": row},
            "gaps": gaps,
            "discovery_domains": ["products and capabilities", "restaurant customers and deployment scope", "ownership funding and M&A", "enterprise readiness", "competitive and market signals"],
        }
    for row in vendor_snapshot.get("competitors") or []:
        slug = row.get("competitor_slug")
        if not slug:
            continue
        target_key = f"competitor:{slug}"
        target = combined.setdefault(target_key, {
            "target_key": target_key,
            "display_name": row.get("display_name") or slug,
            "entity_type": "restaurant_technology_company",
            "priority": "explicit",
            "coverage": row.get("coverage") or "unknown",
            "current_state": {},
            "gaps": [],
            "discovery_domains": ["products and capabilities", "restaurant customers and deployment scope", "ownership funding and M&A", "enterprise readiness", "competitive and market signals"],
        })
        target["current_state"]["extended_profile"] = row
        for field in row.get("research_gaps") or []:
            new_gap = _gap(target_key, field, "missing", VENDOR_SOURCE)
            if new_gap["gap_id"] not in {g["gap_id"] for g in target["gaps"]}:
                target["gaps"].append(new_gap)
        if target.get("coverage") == "cycle_covered":
            target["coverage"] = row.get("coverage") or target["coverage"]
    return list(combined.values())


def build_manifest(*, universe: str = "all", target_keys: list[str] | None = None, limit: int | None = None) -> dict:
    targets = []
    sources = []
    if universe in {"all", "brands"}:
        targets.extend(normalize_brand_snapshot(brand_exporter.export_research_gaps(only_gaps=True)))
        sources.append(BRAND_SOURCE)
    if universe in {"all", "competitors"}:
        targets.extend(normalize_competitor_snapshots(
            competitor_exporter.export_competitor_research_gaps(only_gaps=True),
            vendor_exporter.export_vendor_extended_profile_gaps(only_gaps=True),
        ))
        sources.extend([COMPETITOR_SOURCE, VENDOR_SOURCE])
    if target_keys:
        wanted = set(target_keys)
        targets = [target for target in targets if target["target_key"] in wanted]
    targets.sort(key=lambda row: (0 if row["priority"] in {"enterprise_primary", "emerging_primary"} else 1, -len(row["gaps"]), row["display_name"]))
    if limit is not None:
        targets = targets[:max(0, limit)]
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": sources,
        "target_count": len(targets),
        "gap_count": sum(len(target["gaps"]) for target in targets),
        "targets": targets,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Hunter gap manifest from live RBB state")
    parser.add_argument("--universe", choices=["all", "brands", "competitors"], default="all")
    parser.add_argument("--target", action="append", dest="targets")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output")
    args = parser.parse_args()
    manifest = build_manifest(universe=args.universe, target_keys=args.targets, limit=args.limit)
    rendered = json.dumps(manifest, indent=2, default=str) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
