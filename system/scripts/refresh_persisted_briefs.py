#!/usr/bin/env python3
"""
refresh_persisted_briefs.py — routine regeneration of the four persisted,
versioned documents Team Portal's owner-only buttons produce: Canonical
Background Briefs (customers_prospects accounts), Competitive Briefs,
Battle Cards (2026-09-25), and Value Wedges (2026-09-25).

Todd's direct instruction: since Team Portal never calls an LLM and can
only ever GENERATE these documents through the owner path (a teammate
only ever reads whatever was last generated -- see team_tech_stack.py's
get_canonical_background_brief/get_competitive_brief_view/
get_battle_card_view/get_value_wedge_view), a document nobody has manually
clicked "generate" on stays stale or missing indefinitely. This closes
that gap by running the same generate_*() functions those buttons call,
for every real subject, once a day as part of morning_pipeline.py -- so
whatever the daily intelligence-gathering cycle (exec-move/ownership/
vulnerability scans, tech-stack promotion) or a deep-research packet
promoted since the last run shows up here automatically, without anyone
touching Team Portal.

Why this is safe to run unconditionally, every day, for every subject,
rather than needing its own "did anything actually change" logic: all
four generate_*() functions are cheap, deterministic template renders
(no LLM call anywhere in this path -- confirmed by reading every module
they touch), and artifact_vault_common.register_version() / account_
background_brief.register_brief_version() both gained a no-op guard
the same day this script was built specifically to support it -- a
day with no real change produces byte-identical markdown, which is
recognized and skipped rather than creating a new version, so this can
run daily forever without ever bloating history/ with duplicate versions.

A fixed generated_for="system:daily_refresh" is used on every call
(never a wall-clock-varying string) -- account_background_brief.py's
render embeds `generated_for` literally as "Prepared by: ..." in the
document body, so a varying value here would defeat the no-op guard by
making every day's render differ even when nothing else changed.

Usage:
    python3 system/scripts/refresh_persisted_briefs.py
    python3 system/scripts/refresh_persisted_briefs.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import account_background_brief as abb  # noqa: E402
import competitive_brief as cbrief  # noqa: E402
import battle_card as bcard  # noqa: E402
import value_wedge as vwedge  # noqa: E402
import competitive_landscape as cland  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402

GENERATED_FOR = "system:daily_refresh"


def _classify(before_version: int, result_version: dict) -> str:
    return "generated" if result_version["version"] > before_version else "unchanged"


def refresh_background_briefs() -> dict:
    """One per customers_prospects account -- the full canonical pipeline
    (abb.generate_brief), not the team-facing allowlisted structural-only
    version. Deliberately scoped to accounts Todd already has real
    engagement with (customers_prospects/), not all ~1,663 brands -- a
    full strategic brief for a brand with zero engagement would be
    meaningless busywork, unlike the Company Profile fields (identity/
    trajectory/footprint), which are appropriately ecosystem-wide."""
    generated, unchanged, failed = [], [], []
    for entry in cpc.load_registry().get("registry", []):
        slug = (entry.get("account_id") or "").removeprefix("acct-")
        if not slug:
            continue
        try:
            before = abb.get_current_brief_version(slug)
            before_version = before["version"] if before else 0
            result = abb.generate_brief(slug, generated_for=GENERATED_FOR)
            (generated if _classify(before_version, result["version"]) == "generated" else unchanged).append(slug)
        except Exception as exc:  # noqa: BLE001 — one bad account must never abort the whole run
            failed.append({"slug": slug, "error": str(exc)})
    return {"generated": generated, "unchanged": unchanged, "failed": failed}


def refresh_competitive_briefs() -> dict:
    """One per tracked competitor (competitor_intelligence's registry --
    all of them, not just the 9 priority vendors; the render is cheap and
    an honest sparse brief for a low-priority competitor is still better
    than none on file when a teammate looks one up)."""
    generated, unchanged, failed = [], [], []
    for entry in cic.load_registry().get("registry", []):
        slug = entry.get("competitor_slug")
        if not slug:
            continue
        try:
            before = cbrief.get_current_competitive_brief(slug)
            before_version = before["version"] if before else 0
            result = cbrief.generate_competitive_brief(slug, generated_for=GENERATED_FOR)
            (generated if _classify(before_version, result["version"]) == "generated" else unchanged).append(slug)
        except Exception as exc:  # noqa: BLE001
            failed.append({"slug": slug, "error": str(exc)})
    return {"generated": generated, "unchanged": unchanged, "failed": failed}


def refresh_battle_cards() -> dict:
    """One per valid tech-stack category (a small, fixed ~25-entry
    vocabulary) -- covers every category Team Portal's per-vendor Battle
    Card button can resolve to."""
    generated, unchanged, failed = [], [], []
    for category in cland.TECH_STACK_CATEGORIES:
        try:
            before = bcard.get_current_battle_card(category)
            before_version = before["version"] if before else 0
            result = bcard.generate_battle_card(category, generated_for=GENERATED_FOR)
            (generated if _classify(before_version, result["version"]) == "generated" else unchanged).append(category)
        except Exception as exc:  # noqa: BLE001
            failed.append({"category": category, "error": str(exc)})
    return {"generated": generated, "unchanged": unchanged, "failed": failed}


def refresh_value_wedges() -> dict:
    """One per tracked competitor, same universe as refresh_competitive_
    briefs() above (RB-2026-09-25) -- a competitor with no competes_on
    declared yet still gets a version registered (the honest-blank
    "declare product lines first" pointer), same "generate something real
    rather than leave it missing" reasoning as the other two."""
    generated, unchanged, failed = [], [], []
    for entry in cic.load_registry().get("registry", []):
        slug = entry.get("competitor_slug")
        if not slug:
            continue
        try:
            before = vwedge.get_current_value_wedge(slug)
            before_version = before["version"] if before else 0
            result = vwedge.generate_value_wedge(slug, generated_for=GENERATED_FOR)
            (generated if _classify(before_version, result["version"]) == "generated" else unchanged).append(slug)
        except Exception as exc:  # noqa: BLE001
            failed.append({"slug": slug, "error": str(exc)})
    return {"generated": generated, "unchanged": unchanged, "failed": failed}


def refresh_all() -> dict:
    return {
        "background_briefs": refresh_background_briefs(),
        "competitive_briefs": refresh_competitive_briefs(),
        "battle_cards": refresh_battle_cards(),
        "value_wedges": refresh_value_wedges(),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="Print the full machine-readable summary.")
    args = p.parse_args()

    result = refresh_all()
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    any_failed = False
    for label, key in (
        ("Canonical Background Briefs", "background_briefs"),
        ("Competitive Briefs", "competitive_briefs"),
        ("Battle Cards", "battle_cards"),
        ("Value Wedges", "value_wedges"),
    ):
        r = result[key]
        print(f"{label}: {len(r['generated'])} generated, {len(r['unchanged'])} unchanged, {len(r['failed'])} failed")
        for f in r["failed"]:
            any_failed = True
            print(f"  FAILED {f.get('slug') or f.get('category')}: {f['error']}")
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
