#!/usr/bin/env python3
"""
win_loss_review.py — Win/Loss Review (RB-2026-09-28).

First artifact type in this codebase scoped to a discrete, dated,
repeatable per-OPPORTUNITY event rather than a single evolving per-account
document. Research confirmed RB has no existing "sales opportunity" entity
anywhere: opportunity_pipeline.py tracks Todd's own career pipeline
(unrelated), sales_opportunity_radar.py is predictive-only, and
account.json's opportunities: [] field is untyped and unused. This module
is the first real consumer of a per-opportunity key.

Storage key is a compound slug: f"{account_slug}--{opportunity_slug}"
(double-hyphen join -- every real slug elsewhere in this codebase is
single-hyphen-only, so the two components always split back out
unambiguously). This compound key is used ONLY as artifact_vault_common
.py's own instance_dir() key -- it is NEVER passed through slug_safety
.assert_safe_slug() or customers_prospects_common.account_dir() (whose
regex ^[a-z0-9]+(-[a-z0-9]+)*$ would reject a double-hyphen). The account
itself is always looked up by the plain account_slug.

Unlike Account Plan/Win Plan (which render mostly from live account.json
fields plus one judgment field), a Win/Loss Review is a point-in-time
snapshot of a closed event -- all content is caller-supplied at generation
time; nothing is re-read from account.json except display_name.

Gated like Account Plan/Win Plan/Green Sheet: requires the account to
already exist, and root_cause_or_key_driver/decision_criteria must be
real, non-empty text (creating one is Todd committing a real account of
why a deal was won or lost, not a routine log entry).

Competitor feedback loop (Todd's confirmed design): every named competitor
in competitors_in_deal gets the full competitive_dynamics text appended to
their evidence log via competitor_intelligence.add_competitive_note(),
using category="customer_win"/"customer_loss" -- the first real use of
those two reserved-but-previously-idle categories for Todd's own firsthand
deal outcomes (source="Todd Vahlsing (firsthand)", confidence="high",
never "critical" -- critical stays reserved for the counterparty's own
official statements). Only root_cause_or_key_driver auto-promotes to a
structured vs_genius gap point, and only against the FIRST/primary named
competitor (competitors_in_deal[0]) -- the field is singular by design
("the single biggest factor"), so it is written once per review, not once
per competitor. Any other point can be promoted later with the existing
addCompetitorGapPoint tool; nothing is silently lost either way since the
full text is always in the evidence log.

No "list all slugs under an artifact_type_dir" helper exists in
artifact_vault_common.py, and a compound-slug vault key isn't discoverable
without knowing opportunity_slug in advance -- so this module keeps its
own small flat-JSON index (win_loss_reviews_index.json), same style as
genius_capabilities.py's flat store, so "list every Win/Loss Review for
this account" is a real, cheap query rather than a directory scan.

CLI:
    python3 system/scripts/win_loss_review.py generate <account_slug> <opportunity_slug> \\
        --outcome win --deal-size "$250k ARR" --close-date 2026-09-15 \\
        --decision-criteria "..." --competitive-dynamics "..." \\
        --what-we-did-well "..." --what-we-would-change "..." \\
        --root-cause "..." --competitor comp-slug-a --competitor comp-slug-b
    python3 system/scripts/win_loss_review.py get <account_slug> <opportunity_slug>
    python3 system/scripts/win_loss_review.py list <account_slug>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import customers_prospects_common as cpc  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import competitor_intelligence as compintel  # noqa: E402
import intelligence_index  # noqa: E402

ARTIFACT_TYPE_DIR = "win_loss_reviews"
ARTIFACT_TYPE_TITLE = "Win-Loss Review"

VALID_OUTCOMES = {"win", "loss"}

INDEX_PATH = SCRIPTS_DIR.parent / "win_loss_reviews_index.json"


def _vault_slug(account_slug: str, opportunity_slug: str) -> str:
    return f"{account_slug}--{opportunity_slug}"


# ---------------------------------------------------------------------------
# Index — flat JSON store, same style as genius_capabilities.py
# ---------------------------------------------------------------------------

def _load_index() -> dict:
    if not INDEX_PATH.exists():
        return {}
    try:
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_index(index: dict) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _upsert_index_entry(account_slug: str, entry: dict) -> None:
    """Append-only per account, but exactly one entry per opportunity_slug
    -- re-running generate for the same opportunity updates that entry in
    place (matching the vault's own versioning: one current review per
    opportunity, not unbounded history in the index)."""
    index = _load_index()
    entries = index.setdefault(account_slug, [])
    entries[:] = [e for e in entries if e.get("opportunity_slug") != entry["opportunity_slug"]]
    entries.append(entry)
    _save_index(index)


def list_win_loss_reviews(account_slug: str) -> list[dict]:
    """Honest-blank: [] for an account with no reviews on file."""
    return list(_load_index().get(account_slug, []))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_win_loss_review(
    *, display_name: str, opportunity_slug: str, outcome: str, deal_size: str, close_date: str,
    decision_criteria: str, competitive_dynamics: str, what_we_did_well: str,
    what_we_would_change: str, root_cause_or_key_driver: str,
    action_items: Optional[list[dict]] = None, customer_quote: str = "",
    competitors_in_deal: Optional[list[str]] = None, prepared_for: str = "",
) -> str:
    """Pure rendering from caller-supplied structured fields -- unlike
    render_win_plan()/render_account_plan(), nothing here is re-read from
    persisted account intelligence; a Win/Loss Review is a snapshot of a
    closed event, not a live view of current account state."""
    action_items = action_items or []
    competitors_in_deal = competitors_in_deal or []

    lines: list[str] = []
    lines.append(f"# {display_name} — Win/Loss Review: {opportunity_slug}")
    if prepared_for:
        lines.append(f"\nPrepared for: {prepared_for}")

    lines.append("\n## Deal Summary")
    lines.append(f"- **Outcome**: {outcome.capitalize()}")
    lines.append(f"- **Deal size**: {deal_size}")
    lines.append(f"- **Close date**: {close_date}")
    if competitors_in_deal:
        lines.append(f"- **Competitors in the deal**: {', '.join(competitors_in_deal)}")
    else:
        lines.append("- **Competitors in the deal**: *None named.*")

    lines.append("\n## Decision Criteria")
    lines.append(decision_criteria)

    lines.append("\n## Competitive Dynamics")
    lines.append(competitive_dynamics)

    lines.append("\n## What We Did Well")
    lines.append(what_we_did_well)

    lines.append("\n## What We'd Change")
    lines.append(what_we_would_change)

    lines.append(f"\n## {'Key Driver' if outcome == 'win' else 'Root Cause'}")
    lines.append(root_cause_or_key_driver)

    lines.append("\n## Action Items for the Playbook")
    if action_items:
        lines.append("\n| Action | Owner | Where it updates | Due date |")
        lines.append("|---|---|---|---|")
        for item in action_items:
            lines.append(
                f"| {item.get('action', '')} | {item.get('owner', '')} | "
                f"{item.get('where_it_updates', '')} | {item.get('due_date', '')} |"
            )
    else:
        lines.append("\n*No action items recorded yet.*")

    if outcome == "win" and customer_quote.strip():
        lines.append("\n## Customer Quote / Reference")
        lines.append(customer_quote.strip())

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Versioned artifact + registration
# ---------------------------------------------------------------------------

def get_current_win_loss_review(account_slug: str, opportunity_slug: str, *, include_content: bool = False) -> Optional[dict]:
    return avc.get_current_version(
        ARTIFACT_TYPE_DIR, _vault_slug(account_slug, opportunity_slug), include_content=include_content,
    )


def generate_win_loss_review(
    account_slug: str, opportunity_slug: str, *, outcome: str, deal_size: str, close_date: str,
    decision_criteria: str, competitive_dynamics: str, what_we_did_well: str,
    what_we_would_change: str, root_cause_or_key_driver: str,
    action_items: Optional[list[dict]] = None, customer_quote: str = "",
    competitors_in_deal: Optional[list[str]] = None, generated_for: str = "",
) -> dict:
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome!r} (must be one of {sorted(VALID_OUTCOMES)})")
    if not decision_criteria.strip():
        raise ValueError("decision_criteria must be real, non-empty text")
    if not root_cause_or_key_driver.strip():
        raise ValueError("root_cause_or_key_driver must be real, non-empty text")

    account_dir = cpc.account_dir(account_slug)  # raises FileNotFoundError if unknown
    account = cpc.load_json(account_dir / "account.json")
    display_name = account.get("display_name", account_slug)

    action_items = action_items or []
    competitors_in_deal = competitors_in_deal or []

    markdown = render_win_loss_review(
        display_name=display_name, opportunity_slug=opportunity_slug, outcome=outcome,
        deal_size=deal_size, close_date=close_date, decision_criteria=decision_criteria,
        competitive_dynamics=competitive_dynamics, what_we_did_well=what_we_did_well,
        what_we_would_change=what_we_would_change, root_cause_or_key_driver=root_cause_or_key_driver,
        action_items=action_items, customer_quote=customer_quote,
        competitors_in_deal=competitors_in_deal, prepared_for=generated_for,
    )
    slug = _vault_slug(account_slug, opportunity_slug)
    # Captured BEFORE register_version() -- register_version()'s own no-op
    # dedup guard means an unchanged regenerate call still reports back
    # "version 1" every time (it's returning the existing v1 entry, not
    # creating a new one), so version["version"] == 1 can NOT distinguish
    # "just created for real, right now" from "resolved back to the
    # existing v1 unchanged." Whether a persisted version already existed
    # BEFORE this call is the real signal.
    is_first_write_ever = avc.get_current_version(ARTIFACT_TYPE_DIR, slug) is None
    version = avc.register_version(
        ARTIFACT_TYPE_DIR, ARTIFACT_TYPE_TITLE, slug, f"{display_name} {opportunity_slug}", markdown,
        generated_for=generated_for, purpose="Win/Loss Review",
    )

    # Competitor feedback loop -- gated on is_first_write_ever, not run on
    # every generate() call. add_competitive_note()/add_gap_point() have no
    # dedup guard of their own (evidence.jsonl is append-only, add_gap_
    # point() has no update), so calling them unconditionally on every
    # generate() would write duplicate evidence notes and duplicate gap
    # points on every accidental or intentional rerun. Every named
    # competitor gets the firsthand evidence note; only the primary (first)
    # competitor gets root_cause_or_key_driver promoted to a structured gap
    # point. A genuine correction on a later regenerate is NOT re-logged to
    # the competitor side -- deliberate, matching add_gap_point()'s
    # existing append-only, no-update precedent; a real correction can
    # still be made by hand with the existing addCompetitorGapPoint tool.
    if is_first_write_ever:
        primary_evidence_id = None
        for i, comp_slug in enumerate(competitors_in_deal):
            compintel.ensure_competitor_by_slug(comp_slug)
            note_result = compintel.add_competitive_note(
                comp_slug, competitive_dynamics,
                category="customer_win" if outcome == "win" else "customer_loss",
                source="Todd Vahlsing (firsthand)", confidence="high",
            )
            if i == 0:
                primary_evidence_id = note_result["evidence_id"]

        if competitors_in_deal:
            primary_slug = competitors_in_deal[0]
            gap_side = "genius" if outcome == "win" else "competitor"
            compintel.add_gap_point(primary_slug, gap_side, root_cause_or_key_driver, evidence_id=primary_evidence_id)

    _upsert_index_entry(account_slug, {
        "opportunity_slug": opportunity_slug, "outcome": outcome, "deal_size": deal_size,
        "close_date": close_date, "competitors": competitors_in_deal,
        "root_cause_or_key_driver": root_cause_or_key_driver, "vault_slug": slug,
        "recorded_at": avc.today(),
    })

    try:
        if version["version"] == 1:
            intelligence_index.register_document(
                display_name, "win_loss_review", f"Win/Loss Review: {account_slug} / {opportunity_slug}",
                version["path"], source_system="win_loss_review", created_at=avc.today(),
            )
        else:
            intelligence_index.log_update(
                display_name, version["path"], resource_type="win_loss_review",
                note=f"regenerated as v{version['version']}" + (f" for {generated_for}" if generated_for else ""),
            )
    except Exception:  # noqa: BLE001 — indexing is best-effort, never blocks a real artifact write
        pass

    return {"account_slug": account_slug, "opportunity_slug": opportunity_slug, "markdown": markdown, "version": version}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_gen = sub.add_parser("generate")
    p_gen.add_argument("account_slug")
    p_gen.add_argument("opportunity_slug")
    p_gen.add_argument("--outcome", required=True, choices=sorted(VALID_OUTCOMES))
    p_gen.add_argument("--deal-size", dest="deal_size", default="")
    p_gen.add_argument("--close-date", dest="close_date", default="")
    p_gen.add_argument("--decision-criteria", dest="decision_criteria", required=True)
    p_gen.add_argument("--competitive-dynamics", dest="competitive_dynamics", default="")
    p_gen.add_argument("--what-we-did-well", dest="what_we_did_well", default="")
    p_gen.add_argument("--what-we-would-change", dest="what_we_would_change", default="")
    p_gen.add_argument("--root-cause", dest="root_cause_or_key_driver", required=True)
    p_gen.add_argument("--customer-quote", dest="customer_quote", default="")
    p_gen.add_argument("--competitor", dest="competitors_in_deal", action="append", default=[])
    p_gen.add_argument("--for", dest="generated_for", default="")

    p_get = sub.add_parser("get")
    p_get.add_argument("account_slug")
    p_get.add_argument("opportunity_slug")

    p_list = sub.add_parser("list")
    p_list.add_argument("account_slug")

    args = parser.parse_args()

    if args.cmd == "generate":
        result = generate_win_loss_review(
            args.account_slug, args.opportunity_slug, outcome=args.outcome, deal_size=args.deal_size,
            close_date=args.close_date, decision_criteria=args.decision_criteria,
            competitive_dynamics=args.competitive_dynamics, what_we_did_well=args.what_we_did_well,
            what_we_would_change=args.what_we_would_change, root_cause_or_key_driver=args.root_cause_or_key_driver,
            customer_quote=args.customer_quote, competitors_in_deal=args.competitors_in_deal,
            generated_for=args.generated_for,
        )
        print(result["markdown"])
        print(f"\n[registered as version {result['version']['version']}]", file=sys.stderr)
    elif args.cmd == "get":
        current = get_current_win_loss_review(args.account_slug, args.opportunity_slug, include_content=True)
        print(json.dumps(current, indent=2, ensure_ascii=False) if current else "null")
    elif args.cmd == "list":
        print(json.dumps(list_win_loss_reviews(args.account_slug), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
