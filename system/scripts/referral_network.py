#!/usr/bin/env python3
"""
referral_network.py — Referral Network: overview + per-target analysis
(RB-2026-09-08).

Third relationship-side artifact, after Relationship Card (publish) and
Inner Circle (compute+persist). Todd confirmed "Referral Network" maps
onto TWO different real, existing things -- build both:

1. Overview (publish, not generate -- same discipline as
   relationship_card.py): system/intro_brokers.md is already Todd's own
   hand-curated strategic narrative about broker clusters/domains.
   WHAT_PERSISTS.md documents it as deliberately NEVER mechanically
   regenerated ("a genuine strategic-narrative document... not a
   mechanical data pull"). publish_referral_network_overview() takes a
   verbatim snapshot into the governed vault pattern -- never rewrites the
   file, never synthesizes narrative. It does NOT fix the file's own real
   105-day staleness (confirmed live, 2026-09-08) -- that's Todd's content
   to refresh; versioning at least makes the staleness visible going
   forward via each snapshot's own generated_at, which the un-versioned
   file today does not have.

2. Analysis (compute + persist -- same discipline as battle_card.py):
   rb_core.py::find_intro_paths(target) is already the real, tested,
   already-API-exposed (GET /intro, MCP rb.find_intro) broker-path finder
   -- but purely computed, never persisted; every call recomputes from
   scratch. render_referral_network_analysis(target) renders that
   already-correct dict to markdown and persists it per-target. GET /intro
   itself is untouched -- this is a parallel, additive persisted view, the
   same relationship battle_card.py has to getCategoryMarketShare.

IMPORTANT: find_intro_paths() itself does `if baseline is None: baseline =
load_baseline()` / `if threads is None: threads = [... load_active_threads()
...]` -- both BARE calls with the same stale-default-binding risk
rb_core.py::load_baseline(path=BASELINE_PATH) already has (see
relationship_card.py's own docstring for the full history). Every call
here passes baseline=/threads= explicitly so test isolation is real.

Neither capability requires user_authorization_quote -- the overview is a
verbatim snapshot of content Todd already wrote, and the analysis is
fully computed from already-persisted data, no caller-supplied judgment.

Storage:
    system/artifact_vault/referral_network_overview/master/
    system/artifact_vault/referral_network_analyses/<target-slug>/

CLI:
    python3 system/scripts/referral_network.py publish-overview
    python3 system/scripts/referral_network.py get-overview
    python3 system/scripts/referral_network.py generate-analysis <target>
    python3 system/scripts/referral_network.py get-analysis <target>
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index  # noqa: E402

ARTIFACT_TYPE_DIR_OVERVIEW = "referral_network_overview"
ARTIFACT_TYPE_TITLE_OVERVIEW = "Referral Network Overview"
OVERVIEW_SLUG = "master"

ARTIFACT_TYPE_DIR_ANALYSIS = "referral_network_analyses"
ARTIFACT_TYPE_TITLE_ANALYSIS = "Referral Network Analysis"


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


# ---------------------------------------------------------------------------
# Capability 1 — Referral Network Overview (publish intro_brokers.md)
# ---------------------------------------------------------------------------

def get_current_referral_network_overview(*, include_content: bool = False) -> Optional[dict]:
    return avc.get_current_version(ARTIFACT_TYPE_DIR_OVERVIEW, OVERVIEW_SLUG, include_content=include_content)


def publish_referral_network_overview(*, generated_for: str = "") -> dict:
    """The main entry point for capability 1. Takes a verbatim snapshot of
    the current intro_brokers.md -- never rewrites it, never synthesizes
    narrative content."""
    overview_path = core.SYSTEM_DIR / "intro_brokers.md"
    if not overview_path.exists():
        raise FileNotFoundError(f"No intro_brokers.md found at {overview_path}")

    raw_text = overview_path.read_text(encoding="utf-8")
    version = avc.register_version(
        ARTIFACT_TYPE_DIR_OVERVIEW, ARTIFACT_TYPE_TITLE_OVERVIEW, OVERVIEW_SLUG, "Referral Network Overview", raw_text,
        generated_for=generated_for, purpose="referral network overview snapshot",
    )

    try:
        if version["version"] == 1:
            intelligence_index.register_document(
                "Referral Network Overview", "referral_network_overview", "Referral Network Overview",
                version["path"], source_system="referral_network", created_at=avc.today(),
            )
        else:
            intelligence_index.log_update(
                "Referral Network Overview", version["path"], resource_type="referral_network_overview",
                note=f"republished as v{version['version']}" + (f" for {generated_for}" if generated_for else ""),
            )
    except Exception:  # noqa: BLE001 — indexing is best-effort, never blocks a real artifact write
        pass

    return {"markdown": raw_text, "version": version}


# ---------------------------------------------------------------------------
# Capability 2 — Referral Network Analysis (compute + persist per target)
# ---------------------------------------------------------------------------

def _render_broker(b: dict) -> list[str]:
    scorecard = b.get("scorecard") or {}
    lines = [f"\n### {b.get('name')}"]
    lines.append(
        f"\n{b.get('current_company') or 'Unknown company'} | {b.get('signal_class')} | "
        f"DRR: {b.get('drr_score')} | Composite score: {b.get('composite_score')}"
    )
    if scorecard.get("recommended_posture"):
        lines.append(f"\n**Recommended posture:** {scorecard['recommended_posture']}")
    if b.get("reason"):
        lines.append(f"\n**Reason:** {b['reason']}")
    if b.get("domain_explanation"):
        lines.append(f"\n**Domain relevance:** {b['domain_explanation']}")
    if b.get("draft_ask"):
        lines.append(f"\n**Draft ask:**\n\n> {b['draft_ask'].replace(chr(10), chr(10) + '> ')}")
    return lines


def render_referral_network_analysis(target: str) -> str:
    """Pure rendering from find_intro_paths()'s own already-computed,
    already-correct dict -- same 'documents are outputs' discipline as
    every other artifact type in this suite. Never re-derives scoring."""
    baseline = core.load_baseline(core.BASELINE_PATH)
    threads = [t for t in core.load_active_threads(core.ACTIVE_THREADS_PATH) if t.get("status") == "open"]
    result = core.find_intro_paths(target, baseline=baseline, threads=threads, today=date.today())

    if result["target_resolved"]["type"] == "unknown":
        raise ValueError(f"'{target}' did not resolve to a known person or company -- nothing to analyze")

    resolved = result["target_resolved"]
    lines: list[str] = [f"# Referral Network Analysis: {target}"]
    lines.append(f"\nResolved as {resolved['type']}: {resolved.get('name') or target}")

    lines.append(f"\n## Insiders at {resolved.get('name') or target}")
    if not result["insiders"]:
        lines.append("\n*No known contacts at this company yet.*")
    else:
        lines.append("\n| Name | Signal Class | DRR |")
        lines.append("|---|---|---|")
        for ins in result["insiders"]:
            lines.append(f"| {ins.get('name')} | {ins.get('signal_class')} | {ins.get('drr_score', '')} |")

    lines.append("\n## Candidate Brokers")
    if not result["candidate_brokers"]:
        lines.append("\n*No candidate brokers identified.*")
    else:
        for b in result["candidate_brokers"]:
            lines.extend(_render_broker(b))

    if result.get("notes"):
        lines.append("\n## Notes")
        for note in result["notes"]:
            lines.append(f"- {note}")

    lines.append(f"\n---\n*Generated {avc.today()} from persisted RBB relationship intelligence — not re-researched from scratch.*")
    return "\n".join(lines)


def get_current_referral_network_analysis(target: str, *, include_content: bool = False) -> Optional[dict]:
    return avc.get_current_version(ARTIFACT_TYPE_DIR_ANALYSIS, _slugify(target), include_content=include_content)


def generate_referral_network_analysis(target: str, *, generated_for: str = "") -> dict:
    """The main entry point for capability 2. Fully computed, freely
    regenerable -- no non-empty-content guard beyond the unknown-target
    check, since there is no caller-supplied content to validate."""
    markdown = render_referral_network_analysis(target)  # raises ValueError for an unresolvable target
    slug = _slugify(target)

    version = avc.register_version(
        ARTIFACT_TYPE_DIR_ANALYSIS, ARTIFACT_TYPE_TITLE_ANALYSIS, slug, target, markdown,
        generated_for=generated_for, purpose="referral network broker-path analysis",
    )

    try:
        if version["version"] == 1:
            intelligence_index.register_document(
                target, "referral_network_analysis", f"Referral Network Analysis: {target}",
                version["path"], source_system="referral_network", created_at=avc.today(),
            )
        else:
            intelligence_index.log_update(
                target, version["path"], resource_type="referral_network_analysis",
                note=f"regenerated as v{version['version']}" + (f" for {generated_for}" if generated_for else ""),
            )
    except Exception:  # noqa: BLE001 — indexing is best-effort, never blocks a real artifact write
        pass

    return {"target": target, "slug": slug, "markdown": markdown, "version": version}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("publish-overview")
    sub.add_parser("get-overview")
    p_gen = sub.add_parser("generate-analysis")
    p_gen.add_argument("target")
    p_gen.add_argument("--for", dest="generated_for", default="")
    p_get = sub.add_parser("get-analysis")
    p_get.add_argument("target")
    args = parser.parse_args()

    if args.cmd == "publish-overview":
        result = publish_referral_network_overview()
        print(result["markdown"])
        print(f"\n[registered as version {result['version']['version']}]", file=sys.stderr)
    elif args.cmd == "get-overview":
        current = get_current_referral_network_overview(include_content=True)
        if current is None:
            print("No Referral Network Overview published yet.", file=sys.stderr)
            sys.exit(1)
        print(current["content"])
    elif args.cmd == "generate-analysis":
        result = generate_referral_network_analysis(args.target, generated_for=args.generated_for)
        print(result["markdown"])
        print(f"\n[registered as version {result['version']['version']}]", file=sys.stderr)
    elif args.cmd == "get-analysis":
        current = get_current_referral_network_analysis(args.target, include_content=True)
        if current is None:
            print(f"No Referral Network Analysis published for '{args.target}' yet.", file=sys.stderr)
            sys.exit(1)
        print(current["content"])


if __name__ == "__main__":
    main()
