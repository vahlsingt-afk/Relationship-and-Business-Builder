#!/usr/bin/env python3
"""
migrate_todd_profile_pov.py — one-time User POV Registry Phase 1 seed
migration.

Per system/POV_REGISTRY_FEATURE_BRIEF_2026-10-01.md's "Notes for whoever
picks this up": system/00_TODD_PROFILE.md's existing content should be
the seed migration source -- "read it in full and propose a first-pass
set of atomic entries from it as part of scoping this, rather than
starting from zero." This is that proposal, scoped to the two sections
that are genuinely POV-shaped (strategic beliefs and hard operating
boundaries), not the whole file -- biography, communication style, and
relationship-capital taxonomy are identity/configuration, not claims
about the world or operating rules, so they're deliberately left out of
this migration (see the brief's own framing: "biography, preferences,
strategic theses, and hard operating boundaries" names these as four
distinct kinds of content, only two of which belong in a POV registry).

Selection is a human-judgment curation step, not a mechanical parse --
each entry below is a verbatim or near-verbatim statement from
00_TODD_PROFILE.md, with type/scope/conviction assigned by reading it in
context. All are authorship="user_authored" (Todd's own already-written
words, not RBB inference), so none need needs_review per user_pov.py's
write discipline.

Idempotent: checks for an existing active entry with the same statement
and source_document before adding, so re-running doesn't create
duplicates.

CLI:
    python3 migrate_todd_profile_pov.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import user_pov as up  # noqa: E402

SOURCE_DOCUMENT = "system/00_TODD_PROFILE.md"

# (statement, type, scope, conviction, source_section)
_SEED_ENTRIES: list[tuple[str, str, str, str, str]] = [
    (
        "Enterprise buyers prefer best-of-breed; platform plays weaken at enterprise scale.",
        "principle", "enterprise_sales", "strong_conviction", "Strategic theses",
    ),
    (
        "Payments-tied ecosystems create resistance.",
        "principle", "restaurant_technology", "informed_belief", "Strategic theses",
    ),
    (
        "Integration failures kill deployments.",
        "principle", "restaurant_technology", "strong_conviction", "Strategic theses",
    ),
    (
        "AI is a tool, not magic. Most AI companies overestimate readiness.",
        "principle", "ai", "strong_conviction", "Strategic theses",
    ),
    (
        "Operational execution matters more than demos.",
        "principle", "enterprise_sales", "strong_conviction", "Strategic theses",
    ),
    (
        "Most operational failures are execution failures; leadership visibility into floor reality is weak.",
        "principle", "restaurant_technology", "informed_belief", "Strategic theses",
    ),
    (
        "Enterprise sales is a clarity problem, not an activity problem.",
        "principle", "enterprise_sales", "foundational_principle", "Strategic theses",
    ),
    (
        "Friday-night operational reality matters.",
        "evaluative_lens", "restaurant_technology", "strong_conviction", "Strategic theses",
    ),
    (
        "Too many restaurant-tech vendors are chasing too few customers; consolidation is likely. "
        "Enterprise-friendly differentiated platforms win -- generalist platform plays don't survive "
        "enterprise scrutiny. The market is overcrowded at the small/mid-market end and underbuilt at "
        "the operator-grade-enterprise end.",
        "hypothesis", "restaurant_technology", "informed_belief", "Restaurant-tech market thesis",
    ),
    (
        "BridgePoint Ops will not be positioned as a free strategy resource, intro broker, or "
        "commission-only sales channel.",
        "hard_boundary", "business_development", "foundational_principle", "BridgePoint Ops engagement boundaries",
    ),
    (
        "No unpaid thinking. No introductions without conviction. No advisory without payment. "
        "Relationship capital is not free inventory.",
        "hard_boundary", "business_development", "foundational_principle", "BridgePoint Ops engagement boundaries",
    ),
]


def run(dry_run: bool = False) -> dict:
    existing = up.list_pov_entries()
    existing_keys = {(e["statement"], e.get("source_document")) for e in existing}

    summary = {"created": 0, "skipped_existing": 0, "entries": []}
    for statement, entry_type, scope, conviction, source_section in _SEED_ENTRIES:
        key = (statement, SOURCE_DOCUMENT)
        if key in existing_keys:
            summary["skipped_existing"] += 1
            continue
        summary["entries"].append({"statement": statement, "type": entry_type, "scope": scope})
        if dry_run:
            continue
        entry = up.add_pov_entry(
            statement, entry_type, scope, conviction=conviction, authorship="user_authored",
            source_document=SOURCE_DOCUMENT, source_section=source_section,
        )
        summary["created"] += 1
        existing_keys.add(key)
        summary["entries"][-1]["pov_id"] = entry["pov_id"]

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing anything.")
    args = parser.parse_args()
    summary = run(dry_run=args.dry_run)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
