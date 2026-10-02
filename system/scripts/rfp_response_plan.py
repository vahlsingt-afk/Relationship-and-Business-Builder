#!/usr/bin/env python3
"""
rfp_response_plan.py — RFP Response Plan lifecycle (RB-2026-09-07).

Fifth and final artifact type in the customer-side sales-methodology
suite (after Background Brief, Account Plan, Blue Sheet, Green Sheet,
Win Plan -- see system/CANONICAL_REGISTRY.yaml's customers_prospects
domain and the approved design in
~/.claude/plans/wiggly-kindling-willow.md). Formal RFP response tracking
-- a deadline, a prioritized list of what's still open before the response
can be submitted, the clarification questions owed back to the customer,
and a submission gate.

Content shape grounded in a real precedent, not invented from scratch:
system/_recovered_intel/pollo_campero_2026-08-27/codex_working_files/
response-workplan.md is a real, ad-hoc RFP response workplan Todd/a prior
session actually used for the real Pollo Campero RFP (deadline
2026-09-04) -- P0/P1/P2-prioritized open loops each with a required
owner and completion evidence, a numbered clarification-questions list,
and a submission gate requiring 7 specific things be true of every
pricing row before it can be certified. That submission gate is Todd's
own real, confirmed methodology (not fabricated, not caller-input) --
rendered as a fixed checklist every time, the same way Blue Sheet's
scoring_rules.json is invariant across accounts.

open_loops and clarification_questions ARE real, caller-supplied content
(never templated/invented) -- an RFP's specific open items and the
customer's actual outstanding questions are unique to that RFP, the same
"real, sourced content only" discipline as createBlueSheetAccount's
account_data. Gated like Account Plan/Win Plan/Blue Sheet creation
(user_authorization_quote required, non-empty-content guard) -- an RFP
response is customer-facing and commitment-bearing.

Storage: system/artifact_vault/rfp_response_plans/<slug>/ via
artifact_vault_common.py's shared versioning helper.

CLI:
    python3 system/scripts/rfp_response_plan.py generate <slug> --deadline 2026-09-04 ...
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
import account_background_brief as abb  # noqa: E402
import intelligence_index  # noqa: E402

ARTIFACT_TYPE_DIR = "rfp_response_plans"
ARTIFACT_TYPE_TITLE = "RFP Response Plan"

_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}

# Todd's own real, confirmed submission-gate methodology (see this
# module's docstring for the real precedent it's drawn from) -- fixed,
# not caller-input, the same "invariant across accounts" discipline
# blue_sheets/_standard/scoring_rules.json already uses for the
# qualification scorecard.
SUBMISSION_GATE_REQUIREMENTS = [
    "A compliance status",
    "Deliverable description",
    "Assumptions and dependencies",
    "Exclusions",
    "Billing basis",
    "Approved fee or an explicitly approved pending-price treatment",
    "Internal owner approval",
]


def render_open_loops_section(open_loops: list[dict]) -> list[str]:
    """The '## Critical Open Loops' table -- real, caller-supplied items
    only, sorted P0 -> P1 -> P2 -> unrecognized priority last."""
    lines = ["\n## Critical Open Loops"]
    if not open_loops:
        lines.append("\n*No open loops recorded.*")
        return lines
    ordered = sorted(open_loops, key=lambda o: _PRIORITY_ORDER.get(o.get("priority", ""), 99))
    lines.append("\n| Priority | Open Loop | Owner | Completion Evidence | Status |")
    lines.append("|---|---|---|---|---|")
    for o in ordered:
        lines.append(
            f"| {o.get('priority', '')} | {o.get('loop', '')} | {o.get('owner', '')} | "
            f"{o.get('completion_evidence', '')} | {o.get('status', 'Open')} |"
        )
    return lines


def render_clarification_questions_section(clarification_questions: list[str]) -> list[str]:
    """The '## Customer Clarification Questions' numbered list -- real,
    caller-supplied questions actually owed back to the customer."""
    lines = ["\n## Customer Clarification Questions"]
    if not clarification_questions:
        lines.append("\n*No clarification questions recorded.*")
        return lines
    for i, q in enumerate(clarification_questions, start=1):
        lines.append(f"{i}. {q}")
    return lines


def render_related_discovery_questions_section(discovery_questions: list[dict]) -> list[str]:
    """The '## Related Open Discovery Questions' section -- open
    discovery_questions.json items surfaced as context, distinct from the
    RFP-specific clarification questions above (those go back to the
    customer; these are RBB's own internal research gaps)."""
    open_qs = [q for q in discovery_questions if q.get("status") == "open"]
    lines = ["\n## Related Open Discovery Questions"]
    if not open_qs:
        return []  # nothing to add -- omit the section entirely rather than show it empty
    for q in open_qs[:10]:
        lines.append(f"- {q['question']}")
    return lines


def render_submission_gate_section() -> list[str]:
    """The '## Submission Gate' section -- Todd's own fixed, real
    methodology, rendered identically every time (see module docstring)."""
    lines = [
        "\n## Submission Gate",
        "\n*Do not certify the pricing sheet until every required row has:*",
    ]
    for req in SUBMISSION_GATE_REQUIREMENTS:
        lines.append(f"- {req}")
    return lines


def render_rfp_response_plan(
    slug: str, *, deadline: str, open_loops: list[dict], clarification_questions: list[str],
    prepared_for: str = "", purpose: str = "RFP response tracking",
) -> str:
    """Pure rendering from the caller-supplied real RFP content plus
    persisted discovery-question context -- same 'documents are outputs'
    discipline as every other artifact type in this suite."""
    intel = abb.retrieve_existing_intelligence(slug)
    account = intel["dossier"]["account"]
    display_name = account.get("display_name", slug)

    lines: list[str] = []
    lines.append(f"# {display_name} RFP Response Plan")
    if prepared_for:
        lines.append(f"\nPrepared for: {prepared_for}")
    lines.append(f"\nPurpose: {purpose}")
    lines.append(f"\n**Deadline: {deadline}**")

    lines.extend(render_open_loops_section(open_loops))
    lines.extend(render_clarification_questions_section(clarification_questions))
    lines.extend(render_related_discovery_questions_section(intel["discovery_questions"]))
    lines.extend(render_submission_gate_section())

    # 2026-09-25: no live-timestamp footer -- see battle_card.py/
    # competitor_intelligence.py's identical fix; it silently defeated
    # register_version()'s no-op dedup guard on every regenerate.
    lines.append("\n---\n*Sourced from persisted RBB account intelligence — not re-researched from scratch.*")
    return "\n".join(lines)


def get_current_rfp_response_plan(slug: str, *, include_content: bool = False) -> Optional[dict]:
    return avc.get_current_version(ARTIFACT_TYPE_DIR, slug, include_content=include_content)


def generate_rfp_response_plan(
    slug: str, *, deadline: str, open_loops: list[dict], clarification_questions: list[str],
    generated_for: str = "", purpose: str = "RFP response tracking",
) -> dict:
    """The main entry point. Requires the account to already exist. Never
    mutates account.json -- an RFP response plan is a point-in-time
    tracking document for this specific RFP, versioned like every other
    artifact type in this suite, not a durable account fact."""
    if not deadline.strip():
        raise ValueError("deadline must be a real, non-empty date/description")
    if not open_loops:
        raise ValueError("open_loops must contain at least one real open item -- never an empty scaffold")
    if not clarification_questions:
        raise ValueError("clarification_questions must contain at least one real question")

    account_dir = cpc.account_dir(slug)  # raises FileNotFoundError if unknown
    account = cpc.load_json(account_dir / "account.json")
    display_name = account.get("display_name", slug)

    markdown = render_rfp_response_plan(
        slug, deadline=deadline, open_loops=open_loops, clarification_questions=clarification_questions,
        prepared_for=generated_for, purpose=purpose,
    )
    version = avc.register_version(
        ARTIFACT_TYPE_DIR, ARTIFACT_TYPE_TITLE, slug, display_name, markdown,
        generated_for=generated_for, purpose=purpose,
    )

    try:
        if version["version"] == 1:
            intelligence_index.register_document(
                display_name, "rfp_response_plan", f"RFP Response Plan: {slug}",
                version["path"], source_system="rfp_response_plan", created_at=avc.today(),
            )
        else:
            intelligence_index.log_update(
                display_name, version["path"], resource_type="rfp_response_plan",
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
    p_gen.add_argument("--deadline", required=True)
    p_gen.add_argument("--open-loops-json", required=True, help="JSON list of {priority, loop, owner, completion_evidence, status}")
    p_gen.add_argument("--clarification-questions-json", required=True, help="JSON list of strings")
    p_gen.add_argument("--for", dest="generated_for", default="")
    args = parser.parse_args()

    if args.cmd == "generate":
        result = generate_rfp_response_plan(
            args.slug, deadline=args.deadline,
            open_loops=json.loads(args.open_loops_json),
            clarification_questions=json.loads(args.clarification_questions_json),
            generated_for=args.generated_for,
        )
        print(result["markdown"])
        print(f"\n[registered as version {result['version']['version']}]", file=sys.stderr)


if __name__ == "__main__":
    main()
