#!/usr/bin/env python3
"""
win_plan.py — Win Plan lifecycle (RB-2026-09-07).

Fourth artifact type in the customer-side sales-methodology suite (after
Background Brief, Account Plan, and Blue Sheet -- see system/CANONICAL_
REGISTRY.yaml's customers_prospects domain and the approved design in
~/.claude/plans/wiggly-kindling-willow.md). Todd's own framing: "closing
strategy" -- the late-funnel document that pulls together qualification
status, buying-influence ratings, and competitive position into one real
plan for how this specific opportunity actually gets won.

Reuses account_plan.py's qualification-scorecard renderer directly (same
underlying data, same table shape -- a Win Plan doesn't re-qualify the
account, it reads where qualification currently stands). Two sections are
new here because they read fields Account Plan's simpler views don't
surface: buying influences WITH their rating/personal-win/competitive-
preference field-objects (closing-strategy-specific, not needed for
top-of-funnel planning), and strategic_position (euphoria-panic,
competitive landscape, strengths/red-flags -- all real, already-modeled
account.json content this is the first artifact type to actually render).

The only new judgment content is close_plan -- Todd's own real, specific
plan for closing this opportunity, never a generic template. Gated like
Account Plan/Blue Sheet creation: requires an explicit, real
user_authorization_quote -- creating a Win Plan represents Todd committing
to a specific closing strategy, a real decision moment.

Storage: system/artifact_vault/win_plans/<slug>/ via
artifact_vault_common.py's shared versioning helper.

CLI:
    python3 system/scripts/win_plan.py generate <slug> --close-plan "..." --authorized-by "Todd Vahlsing"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import customers_prospects_common as cpc  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import account_background_brief as abb  # noqa: E402
import account_plan as acct_plan  # noqa: E402
import intelligence_index  # noqa: E402

ARTIFACT_TYPE_DIR = "win_plans"
ARTIFACT_TYPE_TITLE = "Win Plan"


def _field_value(node) -> str:
    """Extracts .value from a field-object, or returns the node itself if
    it's already a plain value. field_dictionary.md's universal shape."""
    if isinstance(node, dict):
        return str(node.get("value", ""))
    return str(node) if node is not None else ""


# ---------------------------------------------------------------------------
# Sections unique to Win Plan
# ---------------------------------------------------------------------------

def render_buying_influences_with_ratings_section(account: dict) -> list[str]:
    """The '## Buying Influences — Closing Read' table -- adds rating,
    personal_win, and competitive_preference on top of account_plan.py's
    simpler table, since closing strategy specifically needs to know who's
    for/against and why, not just who's identified. Governance-gated
    field-objects (field_dictionary.md) -- displays current .value only,
    never writes to them."""
    influences = account.get("buying_influences") or []
    lines = ["\n## Buying Influences — Closing Read"]
    if not influences:
        lines.append("\n*No buying influences identified yet.*")
        return lines
    lines.append("\n| Name | Title | Role (ETUC) | Rating | Personal Win | Competitive Preference |")
    lines.append("|---|---|---|---|---|---|")
    for p in influences:
        role = _field_value(p.get("role_etuc")) or "Unknown"
        rating = _field_value(p.get("rating")) or "Unrated"
        personal_win = _field_value(p.get("personal_win")) or "Unknown"
        comp_pref = _field_value(p.get("competitive_preference")) or "Unknown"
        lines.append(f"| {p.get('name', '')} | {p.get('title', '')} | {role} | {rating} | {personal_win} | {comp_pref} |")
    return lines


def render_strategic_position_section(strategic_position: dict) -> list[str]:
    """The '## Competitive Position' section -- strategic_position's real,
    already-modeled euphoria-panic read, competitive landscape, funnel
    position, and strengths/red-flags. First artifact type to actually
    render this field."""
    lines = ["\n## Competitive Position"]
    if not strategic_position:
        lines.append("\n*No strategic position recorded yet.*")
        return lines

    eup = strategic_position.get("euphoria_panic") or {}
    if eup:
        lines.append("\n### Euphoria / Panic Read")
        state = _field_value(eup.get("current_state"))
        if state:
            lines.append(f"- **State**: {state}")
        reason = _field_value(eup.get("reason"))
        if reason:
            lines.append(f"- **Reason**: {reason}")
        timing = _field_value(eup.get("timing"))
        if timing:
            lines.append(f"- **Timing**: {timing}")

    competition = _field_value(strategic_position.get("competition"))
    if competition:
        lines.append("\n### Competitive Landscape")
        lines.append(competition)

    position = strategic_position.get("position") or {}
    if position:
        lines.append("\n### Funnel Position")
        if position.get("place_in_funnel"):
            lines.append(f"- **Stage**: {position['place_in_funnel']}")
        if position.get("customer_priority"):
            lines.append(f"- **Customer priority**: {position['customer_priority']}")
        pos_vs_comp = _field_value(position.get("position_vs_competition"))
        if pos_vs_comp:
            lines.append(f"- **Position vs. competition**: {pos_vs_comp}")
        if position.get("critical_test"):
            lines.append(f"- **Critical test**: {position['critical_test']}")
        if position.get("immediate_move"):
            lines.append(f"- **Immediate move**: {position['immediate_move']}")

    strengths = strategic_position.get("strengths") or []
    if strengths:
        lines.append("\n### Strengths")
        for s in strengths:
            val = s.get("value", "") if isinstance(s, dict) else str(s)
            lines.append(f"\n- **{val}**")
            if isinstance(s, dict):
                if s.get("best_action_plan"):
                    lines.append(f"  - Action: {s['best_action_plan']}")
                if s.get("owner"):
                    lines.append(f"  - Owner: {s['owner']} (target: {s.get('target', 'unset')})")

    red_flags = strategic_position.get("red_flags") or []
    if red_flags:
        lines.append("\n### Red Flags")
        for r in red_flags:
            val = r.get("value", "") if isinstance(r, dict) else str(r)
            flag_status = r.get("status", "") if isinstance(r, dict) else ""
            marker = " *(contradicted)*" if flag_status == "contradicted" else ""
            lines.append(f"\n- **{val}**{marker}")
            if isinstance(r, dict):
                if r.get("best_action_plan"):
                    lines.append(f"  - Action: {r['best_action_plan']}")
                if r.get("owner"):
                    lines.append(f"  - Owner: {r['owner']} (target: {r.get('target', 'unset')})")

    return lines


def render_win_plan(
    slug: str, *, close_plan: str,
    prepared_for: str = "", purpose: str = "Closing strategy",
) -> str:
    """Pure rendering from persisted intelligence plus the caller-supplied
    close_plan -- same 'documents are outputs' discipline as
    render_background_brief()/render_account_plan()."""
    intel = abb.retrieve_existing_intelligence(slug)
    account = intel["dossier"]["account"]

    display_name = account.get("display_name", slug)
    posture = account.get("portfolio_status", {})
    posture_str = abb._fmt_field(posture, "active pursuit") if isinstance(posture, dict) else str(posture)

    lines: list[str] = []
    lines.append(f"# {display_name} Win Plan")
    if prepared_for:
        lines.append(f"\nPrepared for: {prepared_for}")
    lines.append(f"\nPurpose: {purpose}")
    lines.append(f"\nCurrent posture: {posture_str}")

    lines.extend(acct_plan.render_qualification_section(account))
    lines.extend(render_buying_influences_with_ratings_section(account))
    lines.extend(render_strategic_position_section(account.get("strategic_position") or {}))

    lines.append("\n## Close Plan")
    lines.append(close_plan)

    # 2026-09-25: no live-timestamp footer -- see battle_card.py/
    # competitor_intelligence.py's identical fix; it silently defeated
    # register_version()'s no-op dedup guard on every regenerate.
    lines.append("\n---\n*Sourced from persisted RBB account intelligence — not re-researched from scratch.*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Versioned artifact + registration
# ---------------------------------------------------------------------------

def get_current_win_plan(slug: str, *, include_content: bool = False) -> Optional[dict]:
    return avc.get_current_version(ARTIFACT_TYPE_DIR, slug, include_content=include_content)


def generate_win_plan(
    slug: str, *, close_plan: str,
    generated_for: str = "", purpose: str = "Closing strategy",
) -> dict:
    """The main entry point. Requires the account to already exist. Never
    mutates account.json -- unlike Account Plan, a Win Plan's close_plan
    is not persisted as an account.json field (it's a point-in-time
    closing strategy, not a durable account-record fact); regenerating
    with a revised close_plan versions the document, same as every other
    artifact type in this suite."""
    if not close_plan.strip():
        raise ValueError("close_plan must be real, non-empty text")

    account_dir = cpc.account_dir(slug)  # raises FileNotFoundError if unknown
    account = cpc.load_json(account_dir / "account.json")
    display_name = account.get("display_name", slug)

    markdown = render_win_plan(slug, close_plan=close_plan, prepared_for=generated_for, purpose=purpose)
    version = avc.register_version(
        ARTIFACT_TYPE_DIR, ARTIFACT_TYPE_TITLE, slug, display_name, markdown,
        generated_for=generated_for, purpose=purpose,
    )

    try:
        if version["version"] == 1:
            intelligence_index.register_document(
                display_name, "win_plan", f"Win Plan: {slug}",
                version["path"], source_system="win_plan", created_at=avc.today(),
            )
        else:
            intelligence_index.log_update(
                display_name, version["path"], resource_type="win_plan",
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
    p_gen.add_argument("--close-plan", required=True, dest="close_plan")
    p_gen.add_argument("--for", dest="generated_for", default="")
    args = parser.parse_args()

    if args.cmd == "generate":
        result = generate_win_plan(args.slug, close_plan=args.close_plan, generated_for=args.generated_for)
        print(result["markdown"])
        print(f"\n[registered as version {result['version']['version']}]", file=sys.stderr)


if __name__ == "__main__":
    main()
