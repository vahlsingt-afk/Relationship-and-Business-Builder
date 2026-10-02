#!/usr/bin/env python3
"""
relationship_plan.py — Relationship Plan (RB-2026-09-08).

Fourth and final relationship-side artifact, closing Todd's original
3-bucket taxonomy entirely (customer-side 6, competitive-side 3,
relationship-side 4 = 13 real artifacts). Unlike Relationship Card, Inner
Circle, and Referral Network -- each of which turned out to be existing
infrastructure needing only a governed persistence layer -- this is a
genuine green field. Confirmed via investigation: nothing in this
codebase lets Todd assert a forward-looking, per-person relationship goal
("get to know X better this quarter," "ask Y for an intro by end of
month") as a standing, trackable intent. Card sections (Why this matters,
Trust state, How to engage) are backward/descriptive; loops are
transactional/reactive. Neither is a plan.

Content shape borrows structurally from two real precedents, neither of
which is wired into or read by this module:
  - system/circles/*.md's goal / status / "## Next moves" shape, applied
    per-person instead of per-group.
  - win_plan.py's process shape (a gated, caller-supplied judgment
    document, versioned via artifact_vault_common.py, never mutating its
    source record) -- applied to a contact instead of an account.

relationship_goal and next_moves ARE real, caller-supplied content (never
invented/templated) -- the same "real, sourced content only" discipline
as rfp_response_plan.py's open_loops. Gated like Account Plan/Win Plan/
RFP Response Plan (user_authorization_quote required, non-empty-content
guard) -- setting a relationship goal is a real commitment, not a cheap
research artifact.

Confirmed with Todd directly (two real design decisions):
  1. Vault-only -- NEVER writes to baseline_index.json or system/cards/.
     identity_relationships is authority_status:
     operational_with_event_projection_transition with exactly 3 declared
     mutation_owner scripts (ri_events.py, mutations.py,
     intelligence_mutation_engine.py); this module deliberately does not
     become a fourth.
  2. NOT restricted to existing Relationship Cards (signal_class == "RC")
     -- any real baseline contact is eligible, since "deliberately build
     this LMI/LKI relationship toward RC" is the most valuable use case.

Read-only context (never mutated): the baseline entry itself, plus --
when a card exists -- its trust_state/momentum frontmatter and "## Why
this matters" section, via the same small regex helpers inner_circle.py
already uses (duplicated locally, not cross-imported, per this session's
per-module-helper convention).

Storage: system/artifact_vault/relationship_plans/<contact_id>/ via
artifact_vault_common.py.

CLI:
    python3 system/scripts/relationship_plan.py generate <contact_id> --goal "..." --next-moves-json '["..."]' --status active
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index  # noqa: E402

ARTIFACT_TYPE_DIR = "relationship_plans"
ARTIFACT_TYPE_TITLE = "Relationship Plan"
VALID_STATUSES = {"active", "paused", "achieved", "abandoned"}

_FRONTMATTER_RE = re.compile(r"^---\n(.*?\n)---\n", re.DOTALL)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _card_field(frontmatter_text: str, field: str) -> Optional[str]:
    m = re.search(rf"^{field}:\s*(.*)$", frontmatter_text, flags=re.MULTILINE)
    if not m:
        return None
    value = m.group(1).strip()
    return value or None


def _card_section(body: str, heading: str) -> Optional[str]:
    m = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", body, flags=re.MULTILINE | re.DOTALL)
    if not m:
        return None
    text = m.group(1).strip()
    return text or None


def _find_contact(contact_id: str) -> dict:
    """Real, existing baseline entry, any signal_class -- or raises.
    Deliberately no signal_class == 'RC' restriction (Todd's own choice:
    a plan may be exactly how an LMI/LKI contact gets built toward RC)."""
    for entry in core.load_baseline(core.BASELINE_PATH):
        if entry.get("id") == contact_id:
            return entry
    raise FileNotFoundError(f"No baseline_index.json entry for id '{contact_id}'")


def _read_card_context(contact_id: str) -> dict:
    """trust_state/momentum/why_this_matters for context, or all-None
    honestly when no card exists yet -- never a placeholder."""
    card_path = core.CARDS_DIR / f"{contact_id}.md"
    if not card_path.exists():
        return {"trust_state": None, "momentum": None, "why_this_matters": None}
    text = card_path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    frontmatter_text = m.group(1) if m else ""
    body = text[m.end():] if m else text
    return {
        "trust_state": _card_field(frontmatter_text, "trust_state"),
        "momentum": _card_field(frontmatter_text, "momentum"),
        "why_this_matters": _card_section(body, "Why this matters"),
    }


def render_relationship_plan(
    contact_id: str, *, relationship_goal: str, next_moves: list[str], status: str,
    target_cadence_days: Optional[int] = None, prepared_for: str = "", purpose: str = "Relationship planning",
) -> str:
    """Pure rendering from the caller-supplied real goal/next-moves plus
    read-only baseline/card context -- same 'documents are outputs'
    discipline as every other artifact type in this suite."""
    entry = _find_contact(contact_id)
    display_name = entry.get("name", contact_id)
    card_ctx = _read_card_context(contact_id)

    lines: list[str] = [f"# {display_name} — Relationship Plan"]
    if prepared_for:
        lines.append(f"\nPrepared for: {prepared_for}")
    lines.append(f"\nPurpose: {purpose}")

    lines.append("\n## Current Context")
    lines.append(
        f"\n{display_name} — {entry.get('current_role') or 'role unknown'} at "
        f"{entry.get('current_company') or 'company unknown'} | Signal class: {entry.get('signal_class')} | "
        f"Tier: {entry.get('rc_tier') or 'not yet a Relationship Card'}"
    )
    if card_ctx["trust_state"] or card_ctx["momentum"]:
        lines.append(f"\nTrust state: {card_ctx['trust_state'] or 'unknown'} | Momentum: {card_ctx['momentum'] or 'unknown'}")
    if card_ctx["why_this_matters"]:
        lines.append(f"\n**Why this matters (from Relationship Card):** {card_ctx['why_this_matters']}")

    lines.append("\n## Relationship Goal")
    lines.append(f"\n{relationship_goal}")

    lines.append("\n## Next Moves")
    for move in next_moves:
        lines.append(f"- {move}")

    lines.append(f"\n## Status: {status}")
    if target_cadence_days is not None:
        lines.append(f"\nTarget cadence: every {target_cadence_days} day(s).")

    lines.append(f"\n---\n*Generated {_now_iso()} from persisted RBB relationship intelligence — not re-researched from scratch.*")
    return "\n".join(lines)


def get_current_relationship_plan(contact_id: str, *, include_content: bool = False) -> Optional[dict]:
    return avc.get_current_version(ARTIFACT_TYPE_DIR, contact_id, include_content=include_content)


def generate_relationship_plan(
    contact_id: str, *, relationship_goal: str, next_moves: list[str], status: str,
    target_cadence_days: Optional[int] = None, generated_for: str = "", purpose: str = "Relationship planning",
) -> dict:
    """The main entry point. Requires the contact to already exist in
    baseline_index.json (any signal_class). Never mutates baseline_index.json
    or system/cards/ -- a relationship plan is versioned like every other
    artifact type in this suite, not a durable baseline fact."""
    if not relationship_goal.strip():
        raise ValueError("relationship_goal must be real, non-empty text")
    if not next_moves:
        raise ValueError("next_moves must contain at least one real move -- never an empty scaffold")
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status!r} (must be one of {sorted(VALID_STATUSES)})")

    entry = _find_contact(contact_id)  # raises FileNotFoundError if unknown
    display_name = entry.get("name", contact_id)

    markdown = render_relationship_plan(
        contact_id, relationship_goal=relationship_goal, next_moves=next_moves, status=status,
        target_cadence_days=target_cadence_days, prepared_for=generated_for, purpose=purpose,
    )
    version = avc.register_version(
        ARTIFACT_TYPE_DIR, ARTIFACT_TYPE_TITLE, contact_id, display_name, markdown,
        generated_for=generated_for, purpose=purpose,
    )

    try:
        if version["version"] == 1:
            intelligence_index.register_document(
                display_name, "relationship_plan", f"Relationship Plan: {contact_id}",
                version["path"], source_system="relationship_plan", created_at=avc.today(),
            )
        else:
            intelligence_index.log_update(
                display_name, version["path"], resource_type="relationship_plan",
                note=f"regenerated as v{version['version']}" + (f" for {generated_for}" if generated_for else ""),
            )
    except Exception:  # noqa: BLE001 — indexing is best-effort, never blocks a real artifact write
        pass

    return {"contact_id": contact_id, "markdown": markdown, "version": version}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_gen = sub.add_parser("generate")
    p_gen.add_argument("contact_id")
    p_gen.add_argument("--goal", required=True, dest="relationship_goal")
    p_gen.add_argument("--next-moves-json", required=True, help="JSON list of strings")
    p_gen.add_argument("--status", required=True, choices=sorted(VALID_STATUSES))
    p_gen.add_argument("--cadence-days", type=int, default=None, dest="target_cadence_days")
    p_gen.add_argument("--for", dest="generated_for", default="")
    p_get = sub.add_parser("get")
    p_get.add_argument("contact_id")
    args = parser.parse_args()

    if args.cmd == "generate":
        result = generate_relationship_plan(
            args.contact_id, relationship_goal=args.relationship_goal,
            next_moves=json.loads(args.next_moves_json), status=args.status,
            target_cadence_days=args.target_cadence_days, generated_for=args.generated_for,
        )
        print(result["markdown"])
        print(f"\n[registered as version {result['version']['version']}]", file=sys.stderr)
    elif args.cmd == "get":
        current = get_current_relationship_plan(args.contact_id, include_content=True)
        if current is None:
            print(f"No Relationship Plan published for '{args.contact_id}' yet.", file=sys.stderr)
            sys.exit(1)
        print(current["content"])


if __name__ == "__main__":
    main()
