#!/usr/bin/env python3
"""
green_sheet.py — Green Sheet lifecycle (RB-2026-09-07).

Third artifact type in the customer-side sales-methodology suite, after
Background Brief and Account Plan (see system/CANONICAL_REGISTRY.yaml's
customers_prospects domain and the approved design in
~/.claude/plans/wiggly-kindling-willow.md). Todd's own framing: "single
call plan" -- the Miller Heiman Green Sheet, scoped to ONE specific call
with named attendees, not the whole account (that's the Account Plan) or
a whole active engagement (that's the Blue Sheet).

Deliberately lean and ungated, unlike Account Plan/Win Plan: a Green
Sheet is meant to be cheap to generate before any call, regenerated
freely, never a new commitment the way creating an Account Plan or Blue
Sheet is.

2026-09-28: extended with the real Miller Heiman Conceptual Selling
content the "Green Sheet" name actually refers to (source: Todd's own
canonical "Blank - Green Sheet v2.xltm" template) -- per-attendee Concept,
Valid Business Reason, Credibility, Perspective to Share, Unique
Strengths, Action Commitments, and Basic Issues. All of it follows the
same discipline as the original call_purpose/talking_points fields: real,
caller-supplied, call-specific judgment content, rendered fresh every
time and never persisted to account.json (the win_plan.py close_plan
pattern, not account_plan.py's persisted-strategy pattern -- these are
call-specific, not durable account facts). Buying-influence Role is the
one exception that stays sourced from account.json's existing role_etuc
field, since Role is genuinely durable, not call-specific. Everything
else is read directly from account.json/evidence.jsonl/discovery_
questions.json, filtered to the given attendees, and never written back
-- unlike Account Plan, a Green Sheet never mutates account.json at all;
it is a pure, disposable-by-design view.

Storage: system/artifact_vault/green_sheets/<slug>/ via
artifact_vault_common.py's shared versioning helper -- same current/,
history/, registry.json pattern as Account Plan.

CLI:
    python3 system/scripts/green_sheet.py generate <slug> --purpose "..." --attendees "Name One,Name Two"
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
import intelligence_index  # noqa: E402

ARTIFACT_TYPE_DIR = "green_sheets"
ARTIFACT_TYPE_TITLE = "Green Sheet"

# Fixed, paraphrased Miller Heiman question-mode reminders -- structural
# labels only, never the template's own KEY WORDS/WHEN USED text. Always
# rendered (not caller-supplied, not sourced from discovery_questions.json,
# which has no matching question-mode taxonomy) -- a live-call technique
# cue, same as the printed form's own always-present field labels.
_INFO_GATHERING_PROMPTS = [
    ("Confirmation Questions", "Verify what you already believe is true before building on it."),
    ("New Information Questions", "Surface facts, priorities, or concerns not yet on record."),
    ("Attitude Questions", "Gauge how this Buying Influence feels about the situation."),
]
_COMMITMENT_PROMPTS = [
    ("Commitment Questions", "Ask directly for the next real action."),
    ("Basic Issue Questions", "Surface personal-win concerns behind a stated position."),
]


def _name_matches(person_name: str, attendees: list[str]) -> bool:
    """Loose, case-insensitive match -- an attendee given as "Diego" or
    "Diego Haro" both correctly match a real evidence/buying_influences
    record for "Diego Haro". Never a fuzzy/edit-distance match -- a
    substring miss just means no relevant record found, never a wrong
    person guessed."""
    person_lower = person_name.lower()
    return any(a.lower() in person_lower or person_lower in a.lower() for a in attendees if a.strip())


def render_relevant_buying_influences_section(
    account: dict, attendees: list[str], concepts: Optional[list[dict]] = None,
) -> list[str]:
    """The '## On This Call' table -- only the buying_influences[] rows
    matching a named attendee, never the whole roster (that's Account
    Plan's job). Names an attendee has no on-file record for explicitly,
    rather than silently omitting them.

    concepts, if given, is a list of {"name": str, "concept": str} --
    what that Buying Influence is trying to Accomplish/Fix/Avoid on THIS
    call. Call-specific, so it's always caller-supplied here, never read
    from account.json (see module docstring). Matched onto a row using
    the same loose _name_matches discipline as attendee matching, and
    inserted right after Role -- matching the canonical template's own
    Name/Title -> Role -> Concept column order. A concept for a name that
    matches no on-file row is silently dropped (same risk class as an
    unmatched attendee)."""
    influences = account.get("buying_influences") or []
    concepts = concepts or []
    lines = ["\n## On This Call"]
    matched_names = set()
    rows = []
    for p in influences:
        name = p.get("name", "")
        if _name_matches(name, attendees):
            matched_names.add(name)
            role = p.get("role_etuc")
            role_val = role.get("value") if isinstance(role, dict) else role
            row = f"| {name} | {p.get('title', '')} | {role_val or 'Unknown'} |"
            if concepts:
                concept_val = next(
                    (c.get("concept", "") for c in concepts if _name_matches(name, [c.get("name", "")])), ""
                )
                row += f" {concept_val} |"
            row += f" {p.get('current_read', '')} | {p.get('next_step', '')} |"
            rows.append(row)
    if rows:
        if concepts:
            lines.append("\n| Name | Title | Role (ETUC) | Concept (This Call) | Current Read | Next Step |")
            lines.append("|---|---|---|---|---|---|")
        else:
            lines.append("\n| Name | Title | Role (ETUC) | Current Read | Next Step |")
            lines.append("|---|---|---|---|---|")
        lines.extend(rows)
    unmatched = [a for a in attendees if a.strip() and not any(_name_matches(a, [m]) for m in matched_names)]
    if unmatched:
        lines.append(f"\n*No on-file buying-influence record for: {', '.join(unmatched)}.*")
    return lines


def render_getting_information_section() -> list[str]:
    """The always-present 'Getting Information' question-mode reminder --
    fixed scaffolding, never caller-supplied (see _INFO_GATHERING_PROMPTS)."""
    lines = ["\n## Getting Information"]
    for label, blurb in _INFO_GATHERING_PROMPTS:
        lines.append(f"- **{label}**: {blurb}")
    return lines


def render_showing_value_section(
    valid_business_reason: str, credibility_if_established: str, credibility_if_not_established: str,
) -> list[str]:
    """'## Showing Value' -- omitted entirely (including the header) when
    none of these three real, caller-supplied fields are given."""
    if not (valid_business_reason.strip() or credibility_if_established.strip() or credibility_if_not_established.strip()):
        return []
    lines = ["\n## Showing Value"]
    if valid_business_reason.strip():
        lines.append(f"\n**Valid Business Reason**: {valid_business_reason}")
    if credibility_if_established.strip() or credibility_if_not_established.strip():
        lines.append("\n**Credibility**")
        if credibility_if_established.strip():
            lines.append(f"- If established: {credibility_if_established}")
        if credibility_if_not_established.strip():
            lines.append(f"- If not yet established: {credibility_if_not_established}")
    return lines


def render_giving_information_section(perspective_to_share: str, unique_strengths: Optional[list[dict]]) -> list[str]:
    """'## Giving Information' -- omitted entirely when neither field is
    given. unique_strengths is a list of {"so_what": str, "prove_it": str}."""
    unique_strengths = unique_strengths or []
    if not perspective_to_share.strip() and not unique_strengths:
        return []
    lines = ["\n## Giving Information"]
    if perspective_to_share.strip():
        lines.append(f"\n**Perspective to Share**: {perspective_to_share}")
    if unique_strengths:
        lines.append("\n| So What? | Prove It! |")
        lines.append("|---|---|")
        for s in unique_strengths:
            lines.append(f"| {s.get('so_what', '')} | {s.get('prove_it', '')} |")
    return lines


def render_getting_commitment_section(
    action_commitment_best: str, action_commitment_minimum: str, basic_issues: Optional[list[str]],
) -> list[str]:
    """'## Getting Commitment' -- the Commitment/Basic-Issue question-mode
    reminder always renders (fixed scaffolding, see _COMMITMENT_PROMPTS);
    Action Commitments and Basic Issues only render when real, caller-
    supplied content is given."""
    basic_issues = basic_issues or []
    lines = ["\n## Getting Commitment"]
    for label, blurb in _COMMITMENT_PROMPTS:
        lines.append(f"- **{label}**: {blurb}")
    if action_commitment_best.strip() or action_commitment_minimum.strip():
        lines.append("\n**Action Commitments**")
        if action_commitment_best.strip():
            lines.append(f"- Best: {action_commitment_best}")
        if action_commitment_minimum.strip():
            lines.append(f"- Minimum: {action_commitment_minimum}")
    if basic_issues:
        lines.append("\n**Basic Issues**")
        for issue in basic_issues:
            lines.append(f"- {issue}")
    return lines


def render_relevant_evidence_section(evidence: list[dict], attendees: list[str]) -> list[str]:
    """The '## Relevant Prior Evidence' section -- evidence.jsonl entries
    whose real participants[] overlaps a named attendee, most recent
    first, capped at 10 so this stays a call-prep sheet, not a full
    evidence dump (that's what evidence.jsonl itself is for)."""
    lines = ["\n## Relevant Prior Evidence"]
    matches = [e for e in evidence if _name_matches_any(e.get("participants") or [], attendees)]
    if not matches:
        lines.append("\n*No prior evidence on file involving these attendees.*")
        return lines
    matches.sort(key=lambda e: e.get("event_date") or "", reverse=True)
    for e in matches[:10]:
        lines.append(f"\n**{e.get('event_date', 'undated')}** — {e.get('excerpt', '')}")
        claims = e.get("extracted_claims") or []
        for c in claims:
            lines.append(f"- {c}")
    return lines


def _name_matches_any(names: list[str], attendees: list[str]) -> bool:
    return any(_name_matches(n, attendees) for n in names)


def render_green_sheet(
    slug: str, *, call_purpose: str, attendees: list[str], talking_points: str = "",
    prepared_for: str = "", purpose: str = "Single-call preparation",
    buying_influence_concepts: Optional[list[dict]] = None,
    valid_business_reason: str = "",
    credibility_if_established: str = "",
    credibility_if_not_established: str = "",
    perspective_to_share: str = "",
    unique_strengths: Optional[list[dict]] = None,
    action_commitment_best: str = "",
    action_commitment_minimum: str = "",
    basic_issues: Optional[list[str]] = None,
) -> str:
    """Pure rendering from persisted intelligence plus the caller-supplied
    call_purpose/talking_points and Conceptual Selling fields -- same
    'documents are outputs' discipline as render_background_brief()/
    render_account_plan(). Never writes anything back to account.json.

    The Conceptual Selling params (buying_influence_concepts through
    basic_issues) are all optional and strictly additive: omitting all of
    them renders the same document this function always has, plus the two
    fixed question-mode scaffolding sections (see _INFO_GATHERING_PROMPTS/
    _COMMITMENT_PROMPTS), which are always present."""
    intel = abb.retrieve_existing_intelligence(slug)
    account = intel["dossier"]["account"]
    display_name = account.get("display_name", slug)

    lines: list[str] = []
    lines.append(f"# {display_name} Green Sheet")
    if prepared_for:
        lines.append(f"\nPrepared for: {prepared_for}")
    lines.append(f"\nPurpose: {purpose}")
    lines.append(f"\nAttendees: {', '.join(attendees) if attendees else 'None specified'}")

    lines.append("\n## Call Objective")
    lines.append(call_purpose)

    single_sales_objective = (account.get("opportunities") or [{}])[0].get("single_sales_objective") if account.get("opportunities") else None
    if isinstance(single_sales_objective, dict):
        single_sales_objective = single_sales_objective.get("value")
    if single_sales_objective:
        lines.append("\n*Account-level Single Sales Objective (context): " + str(single_sales_objective) + "*")

    lines.extend(render_relevant_buying_influences_section(account, attendees, buying_influence_concepts))
    lines.extend(render_getting_information_section())
    lines.extend(render_relevant_evidence_section(intel["dossier"].get("evidence") or [], attendees))

    open_questions = [q for q in intel["discovery_questions"] if q.get("status") == "open"]
    if open_questions:
        lines.append("\n## Open Discovery Questions")
        for q in open_questions[:10]:
            lines.append(f"- {q['question']}")

    lines.extend(render_showing_value_section(valid_business_reason, credibility_if_established, credibility_if_not_established))
    lines.extend(render_giving_information_section(perspective_to_share, unique_strengths))
    lines.extend(render_getting_commitment_section(action_commitment_best, action_commitment_minimum, basic_issues))

    if talking_points.strip():
        lines.append("\n## Talking Points")
        lines.append(talking_points)

    # 2026-09-25: no live-timestamp footer here on purpose -- see battle_
    # card.py/competitor_intelligence.py's identical 2026-09-25 fix. A
    # "*Generated {now}*" line embedded in the body made two renders of
    # unchanged data byte-different every time, silently defeating
    # artifact_vault_common.register_version()'s no-op dedup guard and
    # creating a full new version on every regenerate click regardless of
    # real content change. generated_at already exists as real metadata
    # on the registered version entry -- no need to duplicate it here.
    lines.append("\n---\n*Sourced from persisted RBB account intelligence — not re-researched from scratch.*")
    return "\n".join(lines)


def get_current_green_sheet(slug: str, *, include_content: bool = False) -> Optional[dict]:
    return avc.get_current_version(ARTIFACT_TYPE_DIR, slug, include_content=include_content)


def generate_green_sheet(
    slug: str, *, call_purpose: str, attendees: list[str], talking_points: str = "",
    generated_for: str = "", purpose: str = "Single-call preparation",
    buying_influence_concepts: Optional[list[dict]] = None,
    valid_business_reason: str = "",
    credibility_if_established: str = "",
    credibility_if_not_established: str = "",
    perspective_to_share: str = "",
    unique_strengths: Optional[list[dict]] = None,
    action_commitment_best: str = "",
    action_commitment_minimum: str = "",
    basic_issues: Optional[list[str]] = None,
) -> dict:
    """The main entry point. Deliberately ungated (unlike Account Plan/Win
    Plan) -- a Green Sheet is cheap call prep, regenerated freely, never a
    new commitment. Requires the account to already exist; never mutates
    account.json -- including the Conceptual Selling fields below, all of
    which are real, caller-supplied, call-specific judgment content (see
    module docstring), never persisted."""
    if not call_purpose.strip():
        raise ValueError("call_purpose must be real, non-empty text")
    if not attendees:
        raise ValueError("attendees must name at least one real person on the call")

    account_dir = cpc.account_dir(slug)  # raises FileNotFoundError if unknown
    account = cpc.load_json(account_dir / "account.json")
    display_name = account.get("display_name", slug)

    markdown = render_green_sheet(
        slug, call_purpose=call_purpose, attendees=attendees, talking_points=talking_points,
        prepared_for=generated_for, purpose=purpose,
        buying_influence_concepts=buying_influence_concepts,
        valid_business_reason=valid_business_reason,
        credibility_if_established=credibility_if_established,
        credibility_if_not_established=credibility_if_not_established,
        perspective_to_share=perspective_to_share,
        unique_strengths=unique_strengths,
        action_commitment_best=action_commitment_best,
        action_commitment_minimum=action_commitment_minimum,
        basic_issues=basic_issues,
    )
    version = avc.register_version(
        ARTIFACT_TYPE_DIR, ARTIFACT_TYPE_TITLE, slug, display_name, markdown,
        generated_for=generated_for, purpose=purpose,
    )

    try:
        if version["version"] == 1:
            intelligence_index.register_document(
                display_name, "green_sheet", f"Green Sheet: {slug}",
                version["path"], source_system="green_sheet", created_at=avc.today(),
            )
        else:
            intelligence_index.log_update(
                display_name, version["path"], resource_type="green_sheet",
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
    p_gen.add_argument("--purpose", required=True, dest="call_purpose")
    p_gen.add_argument("--attendees", required=True, help="Comma-separated names")
    p_gen.add_argument("--talking-points", default="")
    p_gen.add_argument("--for", dest="generated_for", default="")
    p_gen.add_argument("--valid-business-reason", dest="valid_business_reason", default="")
    p_gen.add_argument("--credibility-if-established", dest="credibility_if_established", default="")
    p_gen.add_argument("--credibility-if-not-established", dest="credibility_if_not_established", default="")
    p_gen.add_argument("--perspective-to-share", dest="perspective_to_share", default="")
    p_gen.add_argument("--action-commitment-best", dest="action_commitment_best", default="")
    p_gen.add_argument("--action-commitment-minimum", dest="action_commitment_minimum", default="")
    p_gen.add_argument("--basic-issue", dest="basic_issues", action="append", default=[])
    args = parser.parse_args()

    if args.cmd == "generate":
        attendees = [a.strip() for a in args.attendees.split(",") if a.strip()]
        result = generate_green_sheet(
            args.slug, call_purpose=args.call_purpose, attendees=attendees,
            talking_points=args.talking_points, generated_for=args.generated_for,
            valid_business_reason=args.valid_business_reason,
            credibility_if_established=args.credibility_if_established,
            credibility_if_not_established=args.credibility_if_not_established,
            perspective_to_share=args.perspective_to_share,
            action_commitment_best=args.action_commitment_best,
            action_commitment_minimum=args.action_commitment_minimum,
            basic_issues=args.basic_issues,
        )
        print(result["markdown"])
        print(f"\n[registered as version {result['version']['version']}]", file=sys.stderr)


if __name__ == "__main__":
    main()
