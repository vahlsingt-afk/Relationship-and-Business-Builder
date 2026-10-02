#!/usr/bin/env python3
"""
vendor_engagement_analysis.py — Vendor Engagement Analysis (RB-2026-09-07).

Third competitive-side artifact, after battle_card.py (category-wide market
share) and competitive_brief.py (competitor-wide profile). Neither answers
this artifact's question: for ONE specific account, which vendor(s) are
they actually using, how deep/entrenched is each engagement, and what's
RBB's real angle on each one. Genuinely new -- confirmed via grep, nothing
in this codebase computed "vendor engagement analysis" before this.

account.json's own technology_stack field (Todd's hand-curated rows) looks
like the obvious source but is thin (layer/vendor/current_state/status/
confidence only -- no schema, no switching_cost/incumbent/entrenchment
field anywhere). The real depth-of-engagement data lives on the ecosystem
graph side: uses_vendor_for_category relationships carry vendor_role,
status, deployment_status, risk, confidence, and strategic_note --
already reachable via account_background_brief.py's own
retrieve_existing_intelligence(), which every other artifact in this suite
already calls. This module is the first thing that actually reads those
richer fields instead of silently dropping them (the same fields
render_technology_environment_section() has always dropped -- see that
function's own docstring re: RB-DEFECT-2026-08-29 for the analogous gap on
the simpler view).

Merge precedence matches render_technology_environment_section() exactly:
account.technology_stack (hand-curated) wins for a layer/category both
sources cover; ecosystem_relationships fill every category not already
covered, rendered here with the richer fields render_technology_environment_
section drops. Relationships are then split by status: active under
"Current Vendor Engagements", everything else (historical/evaluating/
replacing/conflicting/superseded/rumored) under "Prior / Contested Claims"
-- never conflated with a live engagement.

For each ECOSYSTEM-sourced row (has a real vendor_entity_id, unlike
hand-curated rows' free-text vendor name -- deliberately not fuzzy-matched)
that resolves to a tracked competitor, appends a "Competitive Angle"
sub-section: positioning_summary, todds_pov, and this category's
category_battle_cards content when filled in -- the same real,
already-sourced content battle_card.py renders, just scoped to this one
vendor at this one account instead of the top-5 market-wide list.

Fully computed, no caller-supplied free text -- no user_authorization_quote
gate, same reasoning as battle_card.py/competitive_brief.py.

Storage: system/artifact_vault/vendor_engagement_analyses/<account_slug>/
via artifact_vault_common.py.

CLI:
    python3 system/scripts/vendor_engagement_analysis.py generate <account_slug>
    python3 system/scripts/vendor_engagement_analysis.py get <account_slug>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import customers_prospects_common as cpc  # noqa: E402
import account_background_brief as abb  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index  # noqa: E402

ARTIFACT_TYPE_DIR = "vendor_engagement_analyses"
ARTIFACT_TYPE_TITLE = "Vendor Engagement Analysis"

_OTHER_STATUS_LABELS = {
    "historical": "Historical",
    "evaluating": "Evaluating",
    "replacing": "Being Replaced",
    "conflicting": "Conflicting Claim",
    "superseded": "Superseded",
    "rumored": "Rumored",
}


def _competitor_vendor_index() -> dict[str, dict]:
    """vendor_entity_id -> real competitor_intelligence profile, for every
    tracked competitor that has resolved one. Deliberately mirrors
    competitive_landscape.py::_competitor_vendor_index() (same join, same
    small real registry) rather than reaching into that module's own
    private function."""
    index: dict[str, dict] = {}
    reg = cic.load_registry()
    for entry in reg.get("registry", []):
        slug = entry.get("competitor_slug", "")
        comp_path = cic.competitor_dir(slug) / "competitor.json"
        if not comp_path.exists():
            continue
        comp = cic.load_json(comp_path)
        vendor_entity_id = (comp or {}).get("vendor_entity_id")
        if vendor_entity_id:
            index[vendor_entity_id] = comp
    return index


def _split_ecosystem_rows(account: dict, ecosystem_relationships: list[dict]) -> tuple[list[dict], list[dict]]:
    """Ecosystem uses_vendor_for_category relationships not already covered
    by a hand-curated technology_stack row, split into (active, other) --
    same layer/category matching logic as render_technology_environment_
    section(). uses_vendor_for_category is directional (brand=from, vendor=to),
    so the vendor is always to_entity_id here."""
    covered = {str(row.get("layer", "")).strip().lower() for row in account.get("technology_stack", [])}
    active, other = [], []
    for rel in ecosystem_relationships:
        if rel.get("relationship_type") != "uses_vendor_for_category":
            continue
        category = rel.get("category")
        if not category or category.strip().lower() in covered:
            continue
        row = {
            "category": category,
            "vendor": rel.get("_other_entity_name", "Unknown"),
            "vendor_id": rel.get("to_entity_id"),
            "vendor_role": rel.get("vendor_role"),
            "status": rel.get("status"),
            "deployment_status": rel.get("deployment_status"),
            "confidence": (rel.get("confidence") or {}).get("level", "unknown"),
            "risk": rel.get("risk"),
            "strategic_note": rel.get("strategic_note"),
        }
        (active if rel.get("status") == "active" else other).append(row)
    return active, other


def _render_competitive_angle(vendor_id: Optional[str], category: str, comp_index: dict[str, dict]) -> list[str]:
    comp = comp_index.get(vendor_id) if vendor_id else None
    category_card = (comp.get("category_battle_cards") or {}).get(category) if comp else None
    has_content = comp and (comp.get("positioning_summary") or comp.get("todds_pov") or category_card)
    if not has_content:
        # Tracked-but-empty (a real competitor.json with nothing filled in
        # yet) reads the same as not-tracked-at-all here -- either way
        # there is honestly nothing to show, never a placeholder.
        return ["\n*No competitor profile content on file for this vendor yet.*"]
    lines = ["\n**Competitive Angle:**"]
    if comp.get("positioning_summary"):
        lines.append(f"- Positioning: {comp['positioning_summary']}")
    if comp.get("todds_pov"):
        lines.append(f"- Todd's POV: {comp['todds_pov']}")
    if category_card:
        if category_card.get("rm_plain_english_posture"):
            lines.append(f"- RM posture: {category_card['rm_plain_english_posture']}")
        for q in category_card.get("discovery_questions") or []:
            lines.append(f"- Discovery question: {q}")
        for rf in category_card.get("red_flags") or []:
            lines.append(f"- Red flag: {rf}")
    return lines


def _render_ecosystem_row(row: dict, comp_index: dict[str, dict]) -> list[str]:
    lines = [f"\n### {row['vendor']} — {row['category']}"]
    role = f" ({row['vendor_role']})" if row.get("vendor_role") and row["vendor_role"] != "unknown" else ""
    lines.append(f"\nStatus: {row.get('status')}{role} | Deployment: {row.get('deployment_status') or 'unknown'} | Confidence: {row.get('confidence')}")
    if row.get("risk") and row["risk"] != "unknown":
        lines.append(f"\nRisk: {row['risk']}")
    if row.get("strategic_note"):
        lines.append(f"\n{row['strategic_note']}")
    lines.extend(_render_competitive_angle(row.get("vendor_id"), row["category"], comp_index))
    return lines


def render_current_engagements_section(account: dict, active_ecosystem_rows: list[dict], comp_index: dict[str, dict]) -> list[str]:
    lines = ["\n## Current Vendor Engagements"]
    hand_curated = account.get("technology_stack", [])
    if not hand_curated and not active_ecosystem_rows:
        lines.append("\n*No vendor engagements recorded yet.*")
        return lines
    for row in hand_curated:
        lines.append(f"\n### {row.get('vendor', 'Unknown')} — {row.get('layer', '')}")
        lines.append(f"\nStatus: {row.get('status', '')} | Confidence: {row.get('confidence', '')}")
        if row.get("current_state"):
            lines.append(f"\n{row['current_state']}")
    for row in active_ecosystem_rows:
        lines.extend(_render_ecosystem_row(row, comp_index))
    return lines


def render_prior_claims_section(other_ecosystem_rows: list[dict], comp_index: dict[str, dict]) -> list[str]:
    if not other_ecosystem_rows:
        return []  # omit entirely rather than show an empty section
    lines = ["\n## Prior / Contested Claims", "\n*Not a current, active vendor engagement -- shown for context only.*"]
    for row in other_ecosystem_rows:
        label = _OTHER_STATUS_LABELS.get(row.get("status"), row.get("status"))
        lines.append(f"\n### {row['vendor']} — {row['category']} ({label})")
        if row.get("strategic_note"):
            lines.append(f"\n{row['strategic_note']}")
        lines.extend(_render_competitive_angle(row.get("vendor_id"), row["category"], comp_index))
    return lines


def render_vendor_engagement_analysis(slug: str) -> str:
    """Pure rendering from persisted intelligence -- same 'documents are
    outputs' discipline as every other artifact type in this suite. Never
    invents or updates account.json/ecosystem_intelligence.json data."""
    intel = abb.retrieve_existing_intelligence(slug)
    account = intel["dossier"]["account"]
    display_name = account.get("display_name", slug)

    active_rows, other_rows = _split_ecosystem_rows(account, intel.get("ecosystem_relationships") or [])
    comp_index = _competitor_vendor_index()

    lines: list[str] = [f"# {display_name} — Vendor Engagement Analysis"]
    lines.append("\nWhich vendor(s) this account actually uses, how entrenched each engagement is, and RBB's competitive angle on each.")

    lines.extend(render_current_engagements_section(account, active_rows, comp_index))
    lines.extend(render_prior_claims_section(other_rows, comp_index))

    # 2026-09-25: no live-timestamp footer -- see battle_card.py/
    # competitor_intelligence.py's identical fix; it silently defeated
    # register_version()'s no-op dedup guard on every regenerate.
    lines.append("\n---\n*Sourced from persisted RBB account + ecosystem intelligence — not re-researched from scratch.*")
    return "\n".join(lines)


def get_current_vendor_engagement_analysis(slug: str, *, include_content: bool = False) -> Optional[dict]:
    return avc.get_current_version(ARTIFACT_TYPE_DIR, slug, include_content=include_content)


def generate_vendor_engagement_analysis(slug: str, *, generated_for: str = "") -> dict:
    """The main entry point. Requires the account to already exist. Fully
    computed, freely regenerable -- no non-empty-content guard is needed,
    since there is no caller-supplied content to validate."""
    account_dir = cpc.account_dir(slug)  # raises FileNotFoundError if unknown
    account = cpc.load_json(account_dir / "account.json")
    display_name = account.get("display_name", slug)

    markdown = render_vendor_engagement_analysis(slug)
    version = avc.register_version(
        ARTIFACT_TYPE_DIR, ARTIFACT_TYPE_TITLE, slug, display_name, markdown,
        generated_for=generated_for, purpose="vendor engagement analysis",
    )

    try:
        if version["version"] == 1:
            intelligence_index.register_document(
                display_name, "vendor_engagement_analysis", f"Vendor Engagement Analysis: {slug}",
                version["path"], source_system="vendor_engagement_analysis", created_at=avc.today(),
            )
        else:
            intelligence_index.log_update(
                display_name, version["path"], resource_type="vendor_engagement_analysis",
                note=f"regenerated as v{version['version']}" + (f" for {generated_for}" if generated_for else ""),
            )
    except Exception:  # noqa: BLE001 — indexing is best-effort, never blocks a real artifact write
        pass

    return {"slug": slug, "markdown": markdown, "version": version}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_gen = sub.add_parser("generate")
    p_gen.add_argument("slug")
    p_gen.add_argument("--for", dest="generated_for", default="")
    p_get = sub.add_parser("get")
    p_get.add_argument("slug")
    args = parser.parse_args()

    if args.cmd == "generate":
        result = generate_vendor_engagement_analysis(args.slug, generated_for=args.generated_for)
        print(result["markdown"])
        print(f"\n[registered as version {result['version']['version']}]", file=sys.stderr)
    elif args.cmd == "get":
        current = get_current_vendor_engagement_analysis(args.slug, include_content=True)
        if current is None:
            print(f"No Vendor Engagement Analysis persisted for account '{args.slug}' yet.", file=sys.stderr)
            sys.exit(1)
        print(current["content"])


if __name__ == "__main__":
    main()
