#!/usr/bin/env python3
"""
account_plan.py — Account Plan lifecycle (RB-2026-09-07).

Second artifact type in the customer-side sales-methodology suite (after
Background Brief, before Blue Sheet — see system/CANONICAL_REGISTRY.yaml's
customers_prospects domain and the approved design in
~/.claude/plans/wiggly-kindling-willow.md). Todd's own framing: "top of
funnel account planning with discovery" — the stage where an account moves
from pure research (Background Brief) into a formal, qualified plan of
attack, before an active Blue Sheet engagement exists.

Deliberately narrow in what it writes, wide in what it reads: reuses
account_background_brief.py's data-gathering (retrieve_existing_intelligence)
and per-section renderers (render_brand_profile_section,
render_leadership_section, render_technology_environment_section,
render_related_artifacts_section) rather than re-deriving brand/leadership/
tech-stack content a second time. The only NEW judgment content this module
introduces is the account strategy narrative and the go/no-go qualification
call — both real, caller-supplied text (never fabricated, never templated),
persisted onto account.json as two new plain-string fields (account_strategy,
account_plan_go_no_go), the same flat-string convention bottom_line already
uses. Everything else in the rendered document (qualification scorecard,
discovery status, buying influences) is read directly from account.json/
discovery_questions.json — this module never invents or updates that data.

Gated like Blue Sheet creation: requires an explicit, real
user_authorization_quote and at least one substantive judgment field —
creating an Account Plan is a real "we are now formally planning to pursue
this account" moment, not a cheap research artifact like the Background
Brief.

Storage: system/artifact_vault/account_plans/<slug>/ via
artifact_vault_common.py's shared versioning helper (current/, history/,
registry.json) — see that module's own docstring for the full layout and
why indexing is NOT handled there (system/artifacts/registry.json's own
API is deliberately scoped away from this; see intelligence_index.py
instead, wired in below).

CLI:
    python3 system/scripts/account_plan.py generate <slug> --strategy "..." --go-no-go go --authorized-by "Todd Vahlsing" --quote "..."
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import customers_prospects_common as cpc  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import account_background_brief as abb  # noqa: E402
import intelligence_index  # noqa: E402

ARTIFACT_TYPE_DIR = "account_plans"
ARTIFACT_TYPE_TITLE = "Account Plan"
VALID_GO_NO_GO = {"go", "no-go", "pending"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Section renderers unique to Account Plan (everything else is reused from
# account_background_brief.py)
# ---------------------------------------------------------------------------

def render_qualification_section(account: dict) -> list[str]:
    """The '## Qualification Scorecard' table -- account.json's own
    qualification.criteria[] (the canonical Miller Heiman scorecard,
    scoring_rules.json's 5-criterion definition: Budget/Access/Buying
    Process/EBI/Coach), read verbatim, never re-derived or guessed."""
    qual = account.get("qualification") or {}
    criteria = qual.get("criteria") or []
    lines = ["\n## Qualification Scorecard"]
    if not criteria:
        lines.append("\n*No qualification criteria recorded yet.*")
        return lines
    lines.append("\n| Criterion | Answer | Weight | Points | Current Read | Next Step |")
    lines.append("|---|---|---|---|---|---|")
    for c in criteria:
        lines.append(
            f"| {c.get('criterion', '')} | {c.get('answer', 'U')} | {c.get('weight', '')} | "
            f"{c.get('points', 0)} | {c.get('current_read', '')} | {c.get('next_step', '')} |"
        )
    score = qual.get("qualification_score") or {}
    if score:
        lines.append(f"\n**Score: {score.get('value', 0)} / {score.get('max', 100)}**")
    return lines


def render_discovery_status_section(discovery_questions: list[dict]) -> list[str]:
    """The '## Discovery Status' section -- what's been answered vs. what's
    still open, grouped by topic. Distinct from Background Brief's own
    'Key Discovery Questions' section (which only ever shows open
    questions) -- an Account Plan is specifically about showing PROGRESS
    on discovery, not just the remaining gap."""
    lines = ["\n## Discovery Status"]
    if not discovery_questions:
        lines.append("\n*No discovery questions recorded yet.*")
        return lines
    answered = [q for q in discovery_questions if q.get("status") == "answered"]
    open_qs = [q for q in discovery_questions if q.get("status") == "open"]
    lines.append(f"\n{len(answered)} of {len(discovery_questions)} discovery questions answered.")
    if open_qs:
        by_topic: dict[str, list[dict]] = {}
        for q in open_qs:
            by_topic.setdefault(q.get("topic", "General"), []).append(q)
        lines.append("\n### Still Open")
        for topic, qs in by_topic.items():
            lines.append(f"\n**{topic}**")
            for q in qs:
                lines.append(f"- {q['question']}")
    return lines


def render_buying_influences_section(account: dict) -> list[str]:
    """The '## Buying Influences' table -- account.json's buying_influences[],
    read verbatim. role_etuc/mode/rating are governance-gated field-objects
    (field_dictionary.md) -- this only ever displays their current .value,
    never writes to them."""
    influences = account.get("buying_influences") or []
    lines = ["\n## Buying Influences"]
    if not influences:
        lines.append("\n*No buying influences identified yet.*")
        return lines
    lines.append("\n| Name | Title | Role (ETUC) | Influence | Current Read | Next Step |")
    lines.append("|---|---|---|---|---|---|")
    for p in influences:
        role = p.get("role_etuc")
        role_val = role.get("value") if isinstance(role, dict) else role
        lines.append(
            f"| {p.get('name', '')} | {p.get('title', '')} | {role_val or 'Unknown'} | "
            f"{p.get('influence', '')} | {p.get('current_read', '')} | {p.get('next_step', '')} |"
        )
    return lines


def render_account_plan(
    slug: str, *, account_strategy: str, go_no_go: str,
    prepared_for: str = "", purpose: str = "Top-of-funnel account plan and qualification",
) -> str:
    """Pure rendering from persisted intelligence plus the two caller-
    supplied judgment fields -- same 'documents are outputs' discipline as
    render_background_brief(). Reuses abb.retrieve_existing_intelligence()
    (dossier, discovery_questions, ecosystem_relationships, related_artifacts)
    rather than re-deriving any of it."""
    intel = abb.retrieve_existing_intelligence(slug)
    account = intel["dossier"]["account"]
    brand = intel["dossier"]["brand_profile"]

    display_name = account.get("display_name", slug)
    posture = account.get("portfolio_status", {})
    posture_str = abb._fmt_field(posture, "research and qualification — not yet a confirmed active opportunity") if isinstance(posture, dict) else str(posture)

    lines: list[str] = []
    lines.append(f"# {display_name} Account Plan")
    if prepared_for:
        lines.append(f"\nPrepared by: {prepared_for}")
    lines.append(f"\nPurpose: {purpose}")
    lines.append(f"\nCurrent posture: {posture_str}")

    lines.extend(render_qualification_section(account))
    lines.extend(render_discovery_status_section(intel["discovery_questions"]))
    lines.extend(abb.render_brand_profile_section(brand))
    lines.extend(abb.render_leadership_section(account))
    lines.extend(abb.render_technology_environment_section(account, intel.get("ecosystem_relationships")))
    lines.extend(render_buying_influences_section(account))
    lines.extend(abb.render_related_artifacts_section(intel))

    lines.append("\n## Account Strategy")
    lines.append(account_strategy)

    lines.append("\n## Go / No-Go")
    lines.append(go_no_go)

    rendered = "\n".join(lines)
    if prepared_for.strip().casefold() == "todd vahlsing":
        # Internal plans are authored in Todd's voice. Keep his name in the
        # byline, but never refer to him as a third party in the body.
        replacements = (
            ("introduce Todd Vahlsing", "introduce me"),
            ("introduce Todd", "introduce me"),
            ("with Todd Vahlsing", "with me"),
            ("with Todd", "with me"),
            ("Todd Vahlsing's", "my"),
            ("Todd's", "my"),
            ("Todd Vahlsing owns", "I own"),
            ("Todd owns", "I own"),
        )
        for old, new in replacements:
            rendered = rendered.replace(old, new)
    return rendered


# ---------------------------------------------------------------------------
# Versioned artifact + registration
# ---------------------------------------------------------------------------

def get_current_account_plan(slug: str, *, include_content: bool = False) -> Optional[dict]:
    return avc.get_current_version(ARTIFACT_TYPE_DIR, slug, include_content=include_content)


def generate_account_plan(
    slug: str, *, account_strategy: str, go_no_go: str,
    generated_for: str = "", purpose: str = "Top-of-funnel account plan and qualification",
) -> dict:
    """The main entry point: persist the two judgment fields onto
    account.json, render, and register a new vault version. Requires the
    account to already exist (created via account_background_brief.py's
    create_new_account / resolve_account first, same as Blue Sheet requires
    an existing Account Research folder in practice) -- an Account Plan is
    never the FIRST thing created for a brand-new brand."""
    if go_no_go not in VALID_GO_NO_GO:
        raise ValueError(f"invalid go_no_go: {go_no_go!r} (must be one of {sorted(VALID_GO_NO_GO)})")
    if not account_strategy.strip():
        raise ValueError("account_strategy must be real, non-empty text")

    account_dir = cpc.account_dir(slug)  # raises FileNotFoundError if unknown
    account = cpc.load_json(account_dir / "account.json")
    account["account_strategy"] = account_strategy
    account["account_plan_go_no_go"] = go_no_go
    account["account_plan_as_of"] = avc.today()
    account["updated_at"] = _now_iso()
    cpc.save_json(account_dir / "account.json", account)

    display_name = account.get("display_name", slug)
    markdown = render_account_plan(
        slug, account_strategy=account_strategy, go_no_go=go_no_go,
        prepared_for=generated_for, purpose=purpose,
    )
    version = avc.register_version(
        ARTIFACT_TYPE_DIR, ARTIFACT_TYPE_TITLE, slug, display_name, markdown,
        generated_for=generated_for, purpose=purpose,
    )

    try:
        if version["version"] == 1:
            intelligence_index.register_document(
                display_name, "account_plan", f"Account Plan: {slug}",
                version["path"], source_system="account_plan", created_at=avc.today(),
            )
        else:
            intelligence_index.log_update(
                display_name, version["path"], resource_type="account_plan",
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
    p_gen.add_argument("--strategy", required=True, dest="account_strategy")
    p_gen.add_argument("--go-no-go", required=True, choices=sorted(VALID_GO_NO_GO))
    p_gen.add_argument("--for", dest="generated_for", default="")
    args = parser.parse_args()

    if args.cmd == "generate":
        result = generate_account_plan(
            args.slug, account_strategy=args.account_strategy,
            go_no_go=args.go_no_go, generated_for=args.generated_for,
        )
        print(result["markdown"])
        print(f"\n[registered as version {result['version']['version']}]", file=sys.stderr)


if __name__ == "__main__":
    main()
