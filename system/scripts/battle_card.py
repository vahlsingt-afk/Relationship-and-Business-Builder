#!/usr/bin/env python3
"""
battle_card.py — Battle Card persistence (RB-2026-09-07).

First of two competitive-side artifacts closing the persistence gap the
2026-09-07 artifact-inventory audit found and system/CANONICAL_REGISTRY.yaml
already documents under competitive_landscape's known_gap: market-share/
battle-card views are computed fresh at query time from
industry_ecosystem_intelligence and have no store of their own.

This module does NOT recompute or reinterpret anything --
competitive_landscape.py::battle_card_for_category() is already the real,
correct, fully-deterministic battle card (market share + real
competitor_intelligence positioning/POV/advantages + earnings-derived
financial health, joined at call time from ecosystem_intelligence.json and
competitor_intelligence/competitors/*/competitor.json). This module only
renders that dict to markdown and persists it via artifact_vault_common.py's
shared versioning helper, the same pattern proven by the customer-side
sales-artifact suite (account_plan.py, green_sheet.py, win_plan.py,
rfp_response_plan.py).

Category-scoped, not account- or competitor-scoped -- one battle card per
tech-stack category (e.g. "pos", "payments_gateway"), each covering Genius's
own position plus up to 5 competitors in that category.
competitive_landscape.TECH_STACK_CATEGORIES is the valid category vocabulary
(the same set getCategoryMarketShare already validates against).

Fully computed, no caller-supplied free text -- unlike RFP Response Plan,
there is nothing to validate as "real content the caller must supply." No
user_authorization_quote gate: this is internal RM-facing intelligence, not
a customer-facing commitment (same reasoning that left Green Sheet ungated
within the customer-side suite).

Storage: system/artifact_vault/battle_cards/<category>/ via
artifact_vault_common.py.

CLI:
    python3 system/scripts/battle_card.py generate <category>
    python3 system/scripts/battle_card.py get <category>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import ecosystem_intelligence as ei  # noqa: E402
import competitive_landscape as cland  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index  # noqa: E402

ARTIFACT_TYPE_DIR = "battle_cards"
ARTIFACT_TYPE_TITLE = "Battle Card"

# Moved to competitor_intelligence_common.py (2026-09-25) so
# competitor_intelligence.py's Competitive Brief and value_wedge.py's
# Value Wedge share the exact same category display formatting. Kept as
# a module-level alias here so this module's own call sites and tests
# don't need to change.
_category_display_name = cic.category_display_name


def _advantage_text(item) -> str:
    """vs_genius advantage entries are {point, evidence_id, added_at} dicts
    in the real data (competitor_intelligence.py's add_gap_point()), not
    plain strings -- render just the point, never the raw dict repr."""
    if isinstance(item, dict):
        return item.get("point", "")
    return str(item)


def _render_competitor_card(card: dict) -> list[str]:
    name = card.get("vendor_name") or card["vendor_id"]
    lines = [f"\n### {name}"]
    lines.append(f"\n{card['delta_line']}")
    if card.get("positioning_summary"):
        lines.append(f"\n**Positioning:** {card['positioning_summary']}")
    if card.get("todds_pov"):
        lines.append(f"\n**Todd's POV:** {card['todds_pov']}")
    if card.get("genius_advantages"):
        lines.append("\n**Genius advantages:**")
        lines.extend(f"- {_advantage_text(a)}" for a in card["genius_advantages"])
    if card.get("competitor_advantages"):
        lines.append(f"\n**{name} advantages:**")
        lines.extend(f"- {_advantage_text(a)}" for a in card["competitor_advantages"])
    if card.get("has_category_battle_card"):
        lines.extend(cic.render_category_battle_card_body(card))
    else:
        lines.append("\n*No category-specific battle card content on file yet.*")
    fh = card.get("recent_financial_health")
    if fh:
        lines.append(f"\n**Recent financial health ({fh['earnings_company']}):** {fh.get('answer') or 'see financial_health_signals'}")
    if not card.get("has_competitor_profile"):
        lines.append("\n*No competitor_intelligence profile on file for this vendor yet.*")
    return lines


def _artifact_slug(category: str, feature_vendor_id: Optional[str]) -> str:
    """Persistence key: plain `category` when no vendor is featured (back-
    compat / category-only callers), else `category--<vendor_id>` so each
    featured vendor gets its own independently-versioned artifact instead
    of every vendor in a category sharing one "current" document that
    whoever generated it last happened to feature (2026-09-28 fix, see
    battle_card_for_category's docstring)."""
    return category if feature_vendor_id is None else f"{category}--{feature_vendor_id}"


def render_battle_card(category: str, *, feature_vendor_id: Optional[str] = None) -> str:
    """Pure rendering from competitive_landscape.battle_card_for_category()'s
    already-computed, already-correct dict -- same 'documents are outputs'
    discipline as every other artifact type in this suite. Never
    re-derives or re-interprets the underlying numbers.

    Deliberately does NOT embed a live generation timestamp in the
    returned text (2026-09-25 fix -- it originally did, via a trailing
    "*Generated {_now_iso()}...*" footer). That timestamp already exists
    as real metadata on the registered version entry (register_version()'s
    own `generated_at`); embedding it a second time, inside the document
    body itself, meant every single render produced different bytes even
    when nothing about the underlying data had changed -- which silently
    defeated register_version()'s no-op dedup guard for this artifact type
    specifically, exactly the problem that guard exists to prevent. A
    routine daily regeneration (refresh_persisted_briefs.py) would have
    created 365 near-identical versions a year despite the guard, purely
    because of this one embedded timestamp."""
    if category not in cland.TECH_STACK_CATEGORIES:
        raise ValueError(f"unknown category '{category}' -- must be one of competitive_landscape.TECH_STACK_CATEGORIES")

    graph = ei._read_graph()
    data = cland.battle_card_for_category(graph, category, feature_vendor_id=feature_vendor_id)
    display_category = _category_display_name(category)

    lines: list[str] = [f"# {display_category} — Battle Card"]
    lines.append(
        f"\nGenius: {data['genius_brand_count']} brands "
        f"({data['genius_share_of_known_pct'] or 0}% of known) out of "
        f"{data['brands_with_known_vendor']}/{data['total_brands_tracked']} brands tracked with a known vendor in this category."
    )

    if not data["battle_cards"]:
        lines.append("\n*No competitors with a current relationship in this category.*")
    else:
        lines.append("\n## Competitors")
        for card in data["battle_cards"]:
            lines.extend(_render_competitor_card(card))

    lines.append("\n---\n*Sourced from ecosystem_intelligence.json + competitor_intelligence — not re-researched from scratch.*")
    return "\n".join(lines)


def get_current_battle_card(
    category: str, *, include_content: bool = False, feature_vendor_id: Optional[str] = None,
) -> Optional[dict]:
    return avc.get_current_version(
        ARTIFACT_TYPE_DIR, _artifact_slug(category, feature_vendor_id), include_content=include_content,
    )


def generate_battle_card(category: str, *, generated_for: str = "", feature_vendor_id: Optional[str] = None) -> dict:
    """The main entry point. Fully computed, freely regenerable -- no
    non-empty-content guard is needed (unlike RFP Response Plan), since
    there is no caller-supplied content to validate."""
    markdown = render_battle_card(category, feature_vendor_id=feature_vendor_id)  # raises ValueError for an unknown category
    slug = _artifact_slug(category, feature_vendor_id)
    display_name = _category_display_name(category)

    version = avc.register_version(
        ARTIFACT_TYPE_DIR, ARTIFACT_TYPE_TITLE, slug, display_name, markdown,
        generated_for=generated_for, purpose="competitive battle card",
    )

    try:
        if version["version"] == 1:
            intelligence_index.register_document(
                display_name, "battle_card", f"Battle Card: {display_name}",
                version["path"], source_system="battle_card", created_at=avc.today(),
            )
        else:
            intelligence_index.log_update(
                display_name, version["path"], resource_type="battle_card",
                note=f"regenerated as v{version['version']}" + (f" for {generated_for}" if generated_for else ""),
            )
    except Exception:  # noqa: BLE001 — indexing is best-effort, never blocks a real artifact write
        pass

    return {"category": category, "markdown": markdown, "version": version}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_gen = sub.add_parser("generate")
    p_gen.add_argument("category")
    p_gen.add_argument("--for", dest="generated_for", default="")
    p_get = sub.add_parser("get")
    p_get.add_argument("category")
    args = parser.parse_args()

    if args.cmd == "generate":
        result = generate_battle_card(args.category, generated_for=args.generated_for)
        print(result["markdown"])
        print(f"\n[registered as version {result['version']['version']}]", file=sys.stderr)
    elif args.cmd == "get":
        current = get_current_battle_card(args.category, include_content=True)
        if current is None:
            print(f"No battle card persisted for category '{args.category}' yet.", file=sys.stderr)
            sys.exit(1)
        print(current["content"])


if __name__ == "__main__":
    main()
