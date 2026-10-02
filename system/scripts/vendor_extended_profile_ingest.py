#!/usr/bin/env python3
"""
vendor_extended_profile_ingest.py — RB-2026-09-28.

Ingests a ChatGPT-researched dataset (see system/research/
vendor_research_instructions.md, produced against export_vendor_
extended_profile_gaps.py's gap list) into competitor_intelligence's
EXTENDED_PROFILE_FIELDS via competitor_intelligence.
add_extended_profile_finding() -- the writer that closes the gap Team
Portal's Competitor Snapshot card was showing honest-empty for every
tracked vendor.

Competitor identity resolution is exact slug/display-name/alias match
against the existing competitor registry only -- a name that doesn't
resolve is skipped and reported, never guessed at or auto-created (this
module never creates a new competitor; export_vendor_extended_profile_
gaps.py only ever lists vendors that already exist).

CLI:
    python3 vendor_extended_profile_ingest.py ingest --file PATH [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import competitor_intelligence as ci  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402

_FIELD_GROUPS = ("products", "strengths", "key_customers", "recent_news", "trends")


def _resolve_slug(name: str, registry: list[dict]) -> str | None:
    name_lower = name.strip().lower()
    for entry in registry:
        slug = entry.get("competitor_slug", "")
        if slug.lower() == name_lower or entry.get("display_name", "").lower() == name_lower:
            return slug
    for entry in registry:
        slug = entry.get("competitor_slug", "")
        try:
            comp = cic.load_competitor(slug)["competitor"]
        except FileNotFoundError:
            continue
        if name_lower in [a.lower() for a in (comp.get("aliases") or [])]:
            return slug
    return None


def ingest(dataset_path: Path, *, dry_run: bool = False) -> dict:
    data = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    dataset = data.get("dataset") or {}
    records = dataset.get("records") or []
    registry = cic.load_registry().get("registry", [])

    counts = {
        "competitors_total": len(records), "competitors_resolved": 0, "competitors_unresolved": 0,
        "findings_applied": 0, "findings_deduped": 0, "findings_invalid": 0,
    }
    unresolved: list[str] = []
    errors: list[dict] = []

    for rec in records:
        vendor_name = rec.get("vendor_name") or rec.get("competitor_slug")
        if not vendor_name:
            continue
        slug = _resolve_slug(vendor_name, registry)
        if not slug:
            counts["competitors_unresolved"] += 1
            unresolved.append(vendor_name)
            continue
        counts["competitors_resolved"] += 1

        findings = rec.get("findings") or {}
        for field_group, items in findings.items():
            if field_group not in _FIELD_GROUPS or not isinstance(items, list):
                continue
            for item in items:
                value = (item or {}).get("value")
                if not value:
                    continue
                if dry_run:
                    counts["findings_applied"] += 1
                    continue
                try:
                    result = ci.add_extended_profile_finding(
                        slug, field_group, str(value),
                        confidence=item.get("confidence", "medium"),
                        source_url=item.get("source_url"), as_of=item.get("as_of"),
                    )
                except ValueError as exc:
                    counts["findings_invalid"] += 1
                    errors.append({"vendor": vendor_name, "field": field_group, "error": str(exc)})
                    continue
                if result.get("deduped"):
                    counts["findings_deduped"] += 1
                else:
                    counts["findings_applied"] += 1

    return {"counts": counts, "unresolved_competitors": unresolved, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_ingest = sub.add_parser("ingest")
    p_ingest.add_argument("--file", required=True)
    p_ingest.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.cmd == "ingest":
        result = ingest(Path(args.file), dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
