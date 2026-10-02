#!/usr/bin/env python3
"""
inner_circle.py — Inner Circle roll-up (RB-2026-09-08).

Second relationship-side artifact. Genuinely new, fully computed content --
unlike relationship_card.py, there is no existing document to snapshot.
Confirmed with Todd directly: Inner Circle means a roll-up over the real
baseline_index.json contacts tagged rc_tier == "inner" (17 today) --
distinct from the pre-existing, differently-scoped system/circles/*.md
concept (goal-anchored network-activation circles, e.g. hospitality-table.md
-- not touched by this module).

For each inner-tier contact: name, current_company/current_role, last_touch,
relationship_health.drr_score (all from baseline_index.json), plus
trust_state/momentum read from that contact's system/cards/<id>.md
frontmatter (same regex approach getCard already uses in server.py -- no
new YAML dependency) and that card's own "## Open loops" section, extracted
verbatim (a real, already-authored section, never synthesized).

Two sections: Roster (everyone) and Needs Attention (only those already
flagged by RBB's own computed signals -- trust_state in
{strained, drifting, broken} or momentum == "negative" -- never an invented
staleness threshold on top).

Singleton artifact: one document total, not one per contact/account/
category like every other artifact in this suite. Uses a fixed SLUG so
artifact_vault_common.py's <artifact_type_dir>/<slug>/ layout still applies
-- the only singleton in the vault so far.

Fully computed, no caller-supplied free text -- no user_authorization_quote
gate, same reasoning as battle_card.py/vendor_engagement_analysis.py.

IMPORTANT: see relationship_card.py's own docstring for why every call
here uses core.load_baseline(core.BASELINE_PATH) explicitly, never the
bare default.

Storage: system/artifact_vault/inner_circle/master/ via
artifact_vault_common.py.

CLI:
    python3 system/scripts/inner_circle.py generate
    python3 system/scripts/inner_circle.py get
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index  # noqa: E402

ARTIFACT_TYPE_DIR = "inner_circle"
ARTIFACT_TYPE_TITLE = "Inner Circle"
SLUG = "master"

_NEEDS_ATTENTION_TRUST_STATES = {"strained", "drifting", "broken"}
_FRONTMATTER_RE = re.compile(r"^---\n(.*?\n)---\n", re.DOTALL)


def _card_field(frontmatter_text: str, field: str) -> Optional[str]:
    m = re.search(rf"^{field}:\s*(.*)$", frontmatter_text, flags=re.MULTILINE)
    if not m:
        return None
    value = m.group(1).strip()
    return value or None


def _card_section(body: str, heading: str) -> Optional[str]:
    """Verbatim text under a '## {heading}' markdown section, up to the
    next '## ' header or end of file. Matches the section names SCHEMAS.md
    already documents as first-class -- never synthesized."""
    m = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", body, flags=re.MULTILINE | re.DOTALL)
    if not m:
        return None
    text = m.group(1).strip()
    return text or None


def _read_card_details(contact_id: str) -> dict:
    """trust_state/momentum/open_loops for one contact's card, or all-None
    honestly when the card file doesn't exist -- never a placeholder."""
    card_path = core.CARDS_DIR / f"{contact_id}.md"
    if not card_path.exists():
        return {"trust_state": None, "momentum": None, "open_loops": None}
    text = card_path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    frontmatter_text = m.group(1) if m else ""
    body = text[m.end():] if m else text
    return {
        "trust_state": _card_field(frontmatter_text, "trust_state"),
        "momentum": _card_field(frontmatter_text, "momentum"),
        "open_loops": _card_section(body, "Open loops"),
    }


def _gather_inner_circle_rows() -> list[dict]:
    rows = []
    for entry in core.load_baseline(core.BASELINE_PATH):
        if entry.get("rc_tier") != "inner":
            continue
        details = _read_card_details(entry.get("id", ""))
        rows.append({
            "id": entry.get("id"),
            "name": entry.get("name"),
            "current_company": entry.get("current_company"),
            "current_role": entry.get("current_role"),
            "last_touch": entry.get("last_touch"),
            "drr_score": (entry.get("relationship_health") or {}).get("drr_score"),
            **details,
        })
    rows.sort(key=lambda r: (r.get("name") or "").lower())
    return rows


def _needs_attention(row: dict) -> bool:
    return row.get("trust_state") in _NEEDS_ATTENTION_TRUST_STATES or row.get("momentum") == "negative"


def _render_row(row: dict) -> list[str]:
    lines = [f"\n### {row['name']}"]
    role = f" — {row['current_role']} at {row['current_company']}" if row.get("current_role") or row.get("current_company") else ""
    lines.append(f"\n{row['name']}{role}")
    lines.append(
        f"\nLast touch: {row.get('last_touch') or 'unknown'} | "
        f"DRR score: {row.get('drr_score') if row.get('drr_score') is not None else 'unknown'} | "
        f"Trust state: {row.get('trust_state') or 'unknown'} | Momentum: {row.get('momentum') or 'unknown'}"
    )
    if row.get("open_loops"):
        lines.append(f"\n**Open loops:** {row['open_loops']}")
    return lines


def render_inner_circle() -> str:
    """Pure rendering from persisted baseline + card data -- same
    'documents are outputs' discipline as every other artifact in this
    suite. Never invents or updates baseline_index.json/card data."""
    rows = _gather_inner_circle_rows()
    lines: list[str] = ["# Inner Circle"]
    lines.append(f"\n{len(rows)} contact(s) currently tagged rc_tier == \"inner\".")

    lines.append("\n## Inner Circle Roster")
    if not rows:
        lines.append("\n*No contacts currently tagged rc_tier == \"inner\".*")
    for row in rows:
        lines.extend(_render_row(row))

    attention_rows = [r for r in rows if _needs_attention(r)]
    lines.append("\n## Needs Attention")
    if not attention_rows:
        lines.append("\n*No inner-circle contact currently flagged strained/drifting/broken trust or negative momentum.*")
    else:
        for row in attention_rows:
            lines.extend(_render_row(row))

    # 2026-09-25: no live-timestamp footer -- see battle_card.py/
    # competitor_intelligence.py's identical fix; it silently defeated
    # register_version()'s no-op dedup guard on every regenerate.
    lines.append("\n---\n*Sourced from persisted RBB relationship intelligence — not re-researched from scratch.*")
    return "\n".join(lines)


def get_current_inner_circle(*, include_content: bool = False) -> Optional[dict]:
    return avc.get_current_version(ARTIFACT_TYPE_DIR, SLUG, include_content=include_content)


def generate_inner_circle(*, generated_for: str = "") -> dict:
    """The main entry point. Fully computed, freely regenerable -- no
    non-empty-content guard is needed, since there is no caller-supplied
    content to validate."""
    markdown = render_inner_circle()
    version = avc.register_version(
        ARTIFACT_TYPE_DIR, ARTIFACT_TYPE_TITLE, SLUG, "Inner Circle", markdown,
        generated_for=generated_for, purpose="inner circle roll-up",
    )

    try:
        if version["version"] == 1:
            intelligence_index.register_document(
                "Inner Circle", "inner_circle", "Inner Circle",
                version["path"], source_system="inner_circle", created_at=avc.today(),
            )
        else:
            intelligence_index.log_update(
                "Inner Circle", version["path"], resource_type="inner_circle",
                note=f"regenerated as v{version['version']}" + (f" for {generated_for}" if generated_for else ""),
            )
    except Exception:  # noqa: BLE001 — indexing is best-effort, never blocks a real artifact write
        pass

    return {"markdown": markdown, "version": version}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_gen = sub.add_parser("generate")
    p_gen.add_argument("--for", dest="generated_for", default="")
    sub.add_parser("get")
    args = parser.parse_args()

    if args.cmd == "generate":
        result = generate_inner_circle(generated_for=args.generated_for)
        print(result["markdown"])
        print(f"\n[registered as version {result['version']['version']}]", file=sys.stderr)
    elif args.cmd == "get":
        current = get_current_inner_circle(include_content=True)
        if current is None:
            print("No Inner Circle persisted yet.", file=sys.stderr)
            sys.exit(1)
        print(current["content"])


if __name__ == "__main__":
    main()
