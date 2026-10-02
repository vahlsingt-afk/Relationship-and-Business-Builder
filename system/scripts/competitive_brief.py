#!/usr/bin/env python3
"""
competitive_brief.py — Competitive Brief persistence (RB-2026-09-07).

Second of two competitive-side artifacts closing the persistence gap the
2026-09-07 artifact-inventory audit found and system/CANONICAL_REGISTRY.yaml
already documents under competitor_intelligence's known_gap:
render_competitor_profile()/generate_profile() produce a full markdown
profile that's only ever returned in the API response -- never written to
disk, no history of what was rendered when.

Through 2026-09-25 this module rendered the exact same document as
Competitor Profile (competitor_intelligence.render_competitor_profile()) --
literally the same function call, byte-identical output behind two
different buttons. Caught live 2026-09-28 (Todd: "generates some of the
same information... the competitive brief and competitor profile sections
should not be overlapping") -- this module now renders a genuinely
different, situational document: which live accounts currently have this
vendor in place (where Genius is actually competing against them right
now) and what's changed recently, in team voice. Competitor Profile stays
the static reference card (products, strengths, customers, news, trends,
positioning, full historical evidence). This module still calls
sync_from_ecosystem() first (same freshness step generate_profile()
takes) -- it just renders the result differently.

RB-2026-09-25, Todd's explicit direction: now auto-creates via
competitor_intelligence.ensure_competitor_by_slug() when the slug has no
competitor record yet, accepting that a typo'd slug creates a new,
mostly-empty shell rather than erroring -- same tolerance
bulkImportCompetitors already has for a bad name in a batch. This
reverses the module's original strict-on-purpose design (a Competitive
Brief used to require the competitor already exist, the same "the subject
must already be real" discipline RFP Response Plan applies to accounts) --
kept here only as history, not current behavior.

Competitor-scoped: slug = competitor_slug from
competitor_intelligence/_portfolio/competitor_registry.json.

Fully computed, no caller-supplied free text -- no user_authorization_quote
gate, same reasoning as battle_card.py (internal RM-facing intelligence,
not a customer-facing commitment).

Storage: system/artifact_vault/competitive_briefs/<competitor_slug>/ via
artifact_vault_common.py.

CLI:
    python3 system/scripts/competitive_brief.py generate <competitor_slug>
    python3 system/scripts/competitive_brief.py get <competitor_slug>
    python3 system/scripts/competitive_brief.py synthesize-weekly [--slug <one>]

Synthesis (2026-09-30 addition), Todd's explicit direction: "Anything
that is in the team portal that requires CoS commentary should be
generated on a weekly basis as a part of our week end of week schedule
and populated so that is available through the portal. There are no on
demand request or real-time reports in the team portal option." The
account list / recent-evidence sections above need no LLM and stay
exactly as they were -- fully computed, live, on-demand. The "bottom
line" + ranked themes below them is genuine synthesis/prioritization
judgment, which the same "no invented claim" discipline this whole
module follows means an LLM call, not a deterministic heuristic -- so
it follows restaurant_tech_trends.py's own precedent instead: a
separate weekly-scheduled script (synthesize_weekly_briefs() below,
wired to com.relationshipbuilder.competitive-brief-synthesis.plist,
Friday 16:10 -- after restaurant-tech-trends at 16:05) that persists
its result onto competitor.json's own "synthesis" field, which
render_competitive_brief() only ever READS. Team Portal's own process
(team_portal_api.py) deliberately never has OPENAI_API_KEY -- same
isolation principle as team_portal_email.py's dedicated SMTP credential
-- so the weekly job runs via run_with_secrets.py (same wrapper
morning-pipeline.plist already uses) for real API-key access, and
Team Portal just displays whatever was last written. See
competitive_brief_refresh_queue.py for the "request it sooner than
Friday" path into the next morning's intelligence cycle instead.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import competitor_intelligence as compintel  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import ecosystem_export as eco_export  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index  # noqa: E402

SYNTHESIS_MODEL = "gpt-4o-mini"  # same model llm_assist.py already uses

_SYNTHESIS_SYSTEM_PROMPT = (
    "You help a restaurant-technology sales and competitive-intelligence team "
    "read an evidence-grounded brief about one named competitor. You will be "
    "given three kinds of already-verified material: (1) real customer "
    "accounts where this competitor is confirmed in place, each tagged "
    "'confirmed, broad-scope' or with a specific limitation already noted "
    "(franchisee-only, hardware-only, undisclosed scope, weak evidence); "
    "(2) a chronological list of real, cited recent developments; (3) "
    "specific, already fact-checked advantages on each side (our company, "
    "'Genius', vs. the competitor). Produce exactly two things: "
    "(a) bottom_line: 2-3 sentences for an executive -- the overall "
    "competitive threat level right now and what, if anything, the team "
    "should do about it; (b) themes: 3-5 ranked strings (most important "
    "first), each one sentence, explaining WHY this competitor matters "
    "right now, synthesizing and prioritizing the material given -- not "
    "restating every point. CRITICAL: every claim in your output must be "
    "directly traceable to the material provided. Never introduce a fact, "
    "number, name, or claim that is not present in the input. If the "
    "material given is thin (e.g. no confirmed accounts, nothing recent), "
    "say so plainly in the bottom line rather than padding or overstating. "
    'Respond with a JSON object: {"bottom_line": "...", "themes": ["...", ...]}.'
)


def _llm_client():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    return OpenAI(api_key=api_key)

ARTIFACT_TYPE_DIR = "competitive_briefs"
ARTIFACT_TYPE_TITLE = "Competitive Brief"
RECENT_WINDOW_DAYS = 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_current_competitive_brief(competitor_slug: str, *, include_content: bool = False) -> Optional[dict]:
    return avc.get_current_version(ARTIFACT_TYPE_DIR, competitor_slug, include_content=include_content)


# Real 2026-09-30 finding (Todd, live): "I also don't think the where
# we're competing with them right now section is likely accurate."
# Confirmed for PAR Technology -- is_current_win() (ecosystem_export.py)
# only gates on status/deployment_status-not-historical, nothing about
# HOW solid the relationship actually is. Real spread found in the raw
# data behind that one list: McDonald's listed as "(POS)" is actually
# vendor_role "approved_hardware_vendor" (PAR supplies hardware there,
# NOT the POS system of record); Taco Bell's deployment_status is
# literally "franchisee_deployment_not_brand_standard"; several others
# ("deployed_scope_not_publicly_disclosed") have no known real footprint
# at all -- all rendered as flat, identical-confidence bullets next to
# genuinely confirmed brand-wide wins like Papa Johns or MOD Pizza. A
# caveat tag (below) now makes that distinction visible instead of
# silently flattening it.
_ROLE_CAVEATS = {
    "approved_hardware_vendor": "approved hardware vendor, not system of record",
    "hardware_reseller_service_provider": "hardware reseller/service provider, not system of record",
    "service_maintenance_provider": "service/maintenance provider only",
    "pilot": "pilot stage",
    "legacy_incumbent": "legacy incumbent -- may be mid-displacement",
    # franchisee_deployment/regional_deployment mean narrow scope BY
    # DEFINITION -- real 2026-09-30 finding: these were originally (and
    # wrongly) treated as "strong" roles needing no caveat. Caught live
    # on Burger King, whose relationship record's top-level vendor_role
    # said "franchisee_deployment" while its own nested deployment.scope
    # said "system_of_record_pos" and deployment.evidence described a
    # confirmed enterprise-wide active rollout (3,500/7,000 units) --
    # the top-level field was simply stale and has been corrected
    # directly (real evidence already cited in this same brief). A
    # relationship whose role genuinely IS franchisee/regional-only
    # should still show this caveat.
    "franchisee_deployment": "franchisee-level relationship, not confirmed brand-wide",
    "regional_deployment": "regional relationship, not confirmed brand-wide",
    "unknown": "vendor role not confirmed",
    None: "vendor role not confirmed",
}
_DEPLOYMENT_CAVEATS = {
    "franchisee_deployment_not_brand_standard": "one franchisee only, not brand standard",
    "deployed_scope_not_publicly_disclosed": "deployment scope not publicly disclosed",
    "historical_current_status_not_reconfirmed": "historical -- current status not reconfirmed",
    "contracted_deployment_pending": "contracted, not yet deployed",
    "parent_platform_relationship_brand_scope_unresolved": "brand-level scope unresolved",
}
# vendor_role values solid enough to need no role-level caveat at all --
# a genuine primary relationship, not hardware/service/franchisee/pilot.
_STRONG_ROLES = {"system_of_record_pos", "acquirer_or_payments"}


def _confidence_caveat(rel: dict) -> str | None:
    """A short, honest caveat for a relationship that isn't a clean,
    confirmed, broad-scope win -- None when it genuinely is one. Role
    caveat takes priority (a non-decision-maker role is the more
    fundamental overstatement); deployment-scope and provisional-
    evidence caveats layer on top of an otherwise-strong role."""
    role = rel.get("vendor_role")
    deployment_status = rel.get("deployment_status")
    parts = []
    if role not in _STRONG_ROLES:
        parts.append(_ROLE_CAVEATS.get(role, f"role: {role}"))
    # Skip the deployment-status caveat when the role caveat already says
    # the same "franchisee-only" thing -- avoids "franchisee-level
    # relationship...; one franchisee only, not brand standard" reading
    # as two separate problems when it's one.
    redundant_franchisee_pair = (
        role == "franchisee_deployment" and deployment_status == "franchisee_deployment_not_brand_standard"
    )
    dep_caveat = _DEPLOYMENT_CAVEATS.get(deployment_status)
    if dep_caveat and dep_caveat not in parts and not redundant_franchisee_pair:
        parts.append(dep_caveat)
    if rel.get("evidence_posture") == "provisional":
        parts.append("provisional evidence")
    return "; ".join(parts) if parts else None


def _accounts_with_vendor_in_place(vendor_entity_id: str) -> list[dict]:
    """Brands with a real, CURRENT tech-stack relationship to this vendor --
    i.e. live accounts where Genius is actually competing against them
    today, not a historical/every-brand-ever-mentioned list.

    Real 2026-09-30 finding (Todd, live): PAR Technology's brief listed
    "Mr. Pickle's Sandwich Shop (POS)" twice. Root cause is graph-wide,
    not PAR-specific -- confirmed 42 duplicate (brand, vendor, category)
    relationship pairs exist, 38 of them a gap-fill-ingested relationship
    duplicating one that already existed. Deduping here by (brand,
    category) is the safe, immediate display-level fix; the underlying
    duplicate relationship records themselves are a separate, larger
    cleanup (merging 42 pairs blind risks dropping real evidence/sources
    attached to only one side of a given duplicate) -- not done here."""
    graph = ei._read_graph()
    by_id = ei._index_by_id(graph.get("entities") or [])
    # brand+category -> best relationship record seen so far for it. When
    # the 42-duplicate-pairs problem (see above) gives two records for
    # the same brand+category, keep whichever is the STRONGER claim
    # (fewer/no caveats) rather than an arbitrary one -- e.g. Mr.
    # Pickle's Sandwich Shop has both a system_of_record_pos/
    # partially_substantiated record and a role:unknown/provisional one;
    # the brief should reflect the better-evidenced claim, not whichever
    # happened to be recorded first.
    best: dict[tuple[str, str], dict] = {}
    for rel in graph.get("relationships") or []:
        if rel.get("relationship_type") != "uses_vendor_for_category":
            continue
        if rel.get("to_entity_id") != vendor_entity_id:
            continue
        if not eco_export.is_current_win(rel):
            continue
        brand = by_id.get(rel.get("from_entity_id"))
        if not brand:
            continue
        name = brand.get("name", "")
        category = rel.get("category", "")
        key = (name, category)
        caveat = _confidence_caveat(rel)
        candidate = {"name": name, "category": category, "caveat": caveat}
        existing = best.get(key)
        # A record with no caveat always beats one with a caveat; between
        # two caveated records, prefer the one with fewer/shorter caveats
        # as a simple proxy for "the stronger of two weak claims."
        if existing is None or (existing["caveat"] and (not caveat or len(caveat) < len(existing["caveat"]))):
            best[key] = candidate
    rows = sorted(best.values(), key=lambda r: r["name"])
    return rows


def _recent_evidence(evidence: list[dict], *, window_days: int = RECENT_WINDOW_DAYS) -> list[dict]:
    """Evidence logged/dated within the recency window, excluding auto-
    linked references (see competitor_intelligence.render_competitor_
    profile()'s `_source_title` handling -- same reasoning: a fact-free
    'this document mentions them' nudge isn't a situational development)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).date().isoformat()
    out = []
    for e in evidence:
        if e.get("_source_title"):
            continue
        date = (e.get("event_at") or e.get("logged_at", ""))[:10]
        if date and date >= cutoff:
            out.append(e)
    out.sort(key=lambda e: (e.get("event_at") or e.get("logged_at", ""))[:10], reverse=True)
    return out


def render_competitive_brief(competitor_slug: str) -> str:
    """Situational synthesis, not a re-render of Competitor Profile: which
    live accounts currently have this vendor in place, and what's changed
    recently -- team voice throughout, no 'Todd's POV', no first person."""
    data = cic.load_competitor(competitor_slug)
    comp = data["competitor"]
    display_name = comp.get("display_name", competitor_slug)
    lines = [f"# {display_name} — Competitive Brief", ""]

    # Read-only -- see synthesize_weekly_briefs() below for how this gets
    # written. Never generated here; this function must stay fast,
    # deterministic, and callable with no API key at all (see module
    # docstring's Synthesis section).
    synthesis = comp.get("synthesis") or {}
    if synthesis.get("bottom_line") or synthesis.get("themes"):
        lines.append("## Bottom line")
        gen_at = (synthesis.get("generated_at") or "")[:10]
        lines.append(f"*AI-synthesized from the evidence below, generated {gen_at or 'date unknown'} -- verify against the citations below before acting.*")
        lines.append("")
        if synthesis.get("bottom_line"):
            lines.append(synthesis["bottom_line"])
            lines.append("")
        for theme in synthesis.get("themes") or []:
            lines.append(f"- {theme}")
        lines.append("")
    else:
        lines.append("## Bottom line")
        lines.append("*Not yet generated -- synthesis runs weekly (Friday EOW); check back after the next run, or request an earlier refresh.*")
        lines.append("")

    vendor_entity_id = comp.get("vendor_entity_id")
    accounts = _accounts_with_vendor_in_place(vendor_entity_id) if vendor_entity_id else []
    lines.append("## Where we're competing against them right now")
    if accounts:
        confirmed = [a for a in accounts if not a["caveat"]]
        caveated = [a for a in accounts if a["caveat"]]
        lines.append(
            f"{len(accounts)} account(s) on file -- {len(confirmed)} confirmed, broad-scope "
            f"relationship(s), {len(caveated)} with a real limitation noted below "
            "(franchisee-only, hardware-only, undisclosed scope, or weak evidence)."
        )
        lines.append("")
        if confirmed:
            lines.append("**Confirmed, broad-scope:**")
            lines.extend(f"- {a['name']} ({cic.category_display_name(a['category'])})" for a in confirmed)
            lines.append("")
        if caveated:
            lines.append("**Limited scope or unconfirmed -- do not treat as a clean win:**")
            lines.extend(
                f"- {a['name']} ({cic.category_display_name(a['category'])}) -- {a['caveat']}"
                for a in caveated
            )
    else:
        lines.append("*No current tech-stack relationship on file for this vendor.*")
    lines.append("")

    recent = _recent_evidence(data["evidence"])
    lines.append(f"## What's new (last {RECENT_WINDOW_DAYS} days)")
    if recent:
        for e in recent:
            date = e.get("event_at") or e.get("logged_at", "")[:10]
            lines.append(f"- ({date}) {e.get('summary', '')} — *{e.get('source', 'unknown source')}*")
    else:
        lines.append("*Nothing new recorded in this window.*")
    lines.append("")

    vs_genius = comp.get("vs_genius") or {}
    if vs_genius.get("genius_advantages") or vs_genius.get("competitor_advantages"):
        lines.append("## Why this matters for the team")
        for adv in vs_genius.get("genius_advantages", []):
            lines.append(f"- **Genius wins:** {adv.get('point', '')}")
        for adv in vs_genius.get("competitor_advantages", []):
            lines.append(f"- **{display_name} wins:** {adv.get('point', '')}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _format_accounts_for_prompt(accounts: list[dict]) -> str:
    if not accounts:
        return "(none on file)"
    confirmed = [a for a in accounts if not a["caveat"]]
    caveated = [a for a in accounts if a["caveat"]]
    out = []
    if confirmed:
        out.append("Confirmed, broad-scope:")
        out.extend(f"- {a['name']} ({cic.category_display_name(a['category'])})" for a in confirmed)
    if caveated:
        out.append("Limited scope or unconfirmed:")
        out.extend(f"- {a['name']} ({cic.category_display_name(a['category'])}) -- {a['caveat']}" for a in caveated)
    return "\n".join(out)


def _format_evidence_for_prompt(evidence: list[dict]) -> str:
    if not evidence:
        return "(none in the recent window)"
    return "\n".join(
        f"- ({e.get('event_at') or e.get('logged_at', '')[:10]}) {e.get('summary', '')}" for e in evidence
    )


def _format_vs_genius_for_prompt(vs_genius: dict, display_name: str) -> str:
    lines = []
    for adv in vs_genius.get("genius_advantages") or []:
        lines.append(f"- Genius advantage: {adv.get('point', '')}")
    for adv in vs_genius.get("competitor_advantages") or []:
        lines.append(f"- {display_name} advantage: {adv.get('point', '')}")
    return "\n".join(lines) if lines else "(none on file)"


def generate_synthesis(competitor_slug: str) -> Optional[dict]:
    """Best-effort LLM call, same contract as llm_assist.py: returns None
    (never raises) when OPENAI_API_KEY isn't configured, the openai
    package isn't installed, or the call fails for any reason -- callers
    must treat None as "no signal," not as an empty/negative result.
    Deliberately NOT called from render_competitive_brief() or
    generate_competitive_brief() -- see module docstring's Synthesis
    section for why this is a separate, weekly-scheduled path."""
    client = _llm_client()
    if client is None:
        return None
    data = cic.load_competitor(competitor_slug)
    comp = data["competitor"]
    display_name = comp.get("display_name", competitor_slug)
    vendor_entity_id = comp.get("vendor_entity_id")
    accounts = _accounts_with_vendor_in_place(vendor_entity_id) if vendor_entity_id else []
    recent = _recent_evidence(data["evidence"])
    vs_genius = comp.get("vs_genius") or {}

    user_content = (
        f"Competitor: {display_name}\n\n"
        f"ACCOUNTS WHERE THIS COMPETITOR IS IN PLACE:\n{_format_accounts_for_prompt(accounts)}\n\n"
        f"RECENT DEVELOPMENTS (last {RECENT_WINDOW_DAYS} days):\n{_format_evidence_for_prompt(recent)}\n\n"
        f"HEAD-TO-HEAD ADVANTAGES:\n{_format_vs_genius_for_prompt(vs_genius, display_name)}"
    )
    try:
        response = client.chat.completions.create(
            model=SYNTHESIS_MODEL,
            messages=[
                {"role": "system", "content": _SYNTHESIS_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=600,
        )
        result = json.loads(response.choices[0].message.content)
    except Exception:  # noqa: BLE001 -- best-effort, same as llm_assist.py
        return None
    if not isinstance(result, dict):
        return None
    bottom_line = result.get("bottom_line")
    themes = result.get("themes")
    if not isinstance(bottom_line, str) or not isinstance(themes, list):
        return None
    return {"bottom_line": bottom_line, "themes": [str(t) for t in themes]}


def persist_synthesis(competitor_slug: str) -> Optional[dict]:
    """Generates and writes the synthesis onto competitor.json's own
    "synthesis" field -- the only place render_competitive_brief() reads
    it from. Returns None (writes nothing) when generate_synthesis()
    itself returns None, so a failed/unavailable LLM call never
    overwrites a prior good synthesis with emptiness."""
    result = generate_synthesis(competitor_slug)
    if result is None:
        return None
    data = cic.load_competitor(competitor_slug)
    comp = data["competitor"]
    comp["synthesis"] = {**result, "generated_at": _now_iso()}
    comp["updated_at"] = cic.now_iso()
    cic.save_json(cic.competitor_dir(competitor_slug) / "competitor.json", comp)
    return comp["synthesis"]


def synthesize_weekly_briefs(*, only_slug: Optional[str] = None) -> dict:
    """The Friday EOW synthesis pass -- see module docstring's Synthesis
    section. Only competitors with a vendor_entity_id (a real tracked
    vendor, not a mostly-empty auto-created shell) get a synthesis
    attempt. Best-effort per competitor, same discipline as
    competitor_intelligence_common's own "one unreadable competitor.json
    must not blow up a full-registry pass" convention -- one failure
    never stops the rest."""
    registry = cic.load_registry()["registry"]
    results: dict[str, bool] = {}
    for entry in registry:
        slug = entry.get("competitor_slug")
        if not slug or (only_slug and slug != only_slug):
            continue
        try:
            data = cic.load_competitor(slug)
            if not data["competitor"].get("vendor_entity_id"):
                continue
            results[slug] = persist_synthesis(slug) is not None
        except Exception:  # noqa: BLE001
            results[slug] = False
    return results


def generate_competitive_brief(competitor_slug: str, *, generated_for: str = "") -> dict:
    """The main entry point. Auto-creates the competitor (a mostly-empty
    shell) via ensure_competitor_by_slug() when the slug has no record
    yet -- see module docstring. Freely regenerable -- no non-empty-content
    guard is needed, since there is no caller-supplied content to
    validate; every field comes from already-persisted competitor.json/
    evidence.jsonl plus live ecosystem_intelligence.json relationships."""
    data = compintel.ensure_competitor_by_slug(competitor_slug)
    display_name = data["competitor"].get("display_name") or competitor_slug

    compintel.sync_from_ecosystem(competitor_slug)
    markdown = render_competitive_brief(competitor_slug)

    version = avc.register_version(
        ARTIFACT_TYPE_DIR, ARTIFACT_TYPE_TITLE, competitor_slug, display_name, markdown,
        generated_for=generated_for, purpose="competitive brief",
    )

    try:
        if version["version"] == 1:
            intelligence_index.register_document(
                display_name, "competitive_brief", f"Competitive Brief: {display_name}",
                version["path"], source_system="competitive_brief", created_at=avc.today(),
            )
        else:
            intelligence_index.log_update(
                display_name, version["path"], resource_type="competitive_brief",
                note=f"regenerated as v{version['version']}" + (f" for {generated_for}" if generated_for else ""),
            )
    except Exception:  # noqa: BLE001 — indexing is best-effort, never blocks a real artifact write
        pass

    return {"competitor_slug": competitor_slug, "markdown": markdown, "version": version}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_gen = sub.add_parser("generate")
    p_gen.add_argument("competitor_slug")
    p_gen.add_argument("--for", dest="generated_for", default="")
    p_get = sub.add_parser("get")
    p_get.add_argument("competitor_slug")
    p_syn = sub.add_parser("synthesize-weekly")
    p_syn.add_argument("--slug", default=None, help="Only this one competitor, instead of the whole registry.")
    args = parser.parse_args()

    if args.cmd == "generate":
        result = generate_competitive_brief(args.competitor_slug, generated_for=args.generated_for)
        print(result["markdown"])
        print(f"\n[registered as version {result['version']['version']}]", file=sys.stderr)
    elif args.cmd == "get":
        current = get_current_competitive_brief(args.competitor_slug, include_content=True)
        if current is None:
            print(f"No competitive brief persisted for competitor '{args.competitor_slug}' yet.", file=sys.stderr)
            sys.exit(1)
        print(current["content"])
    elif args.cmd == "synthesize-weekly":
        results = synthesize_weekly_briefs(only_slug=args.slug)
        ok = sum(1 for v in results.values() if v)
        print(f"synthesized {ok}/{len(results)} competitor(s)", file=sys.stderr)
        for slug, success in sorted(results.items()):
            print(f"  {'OK' if success else 'FAILED/unavailable'}: {slug}", file=sys.stderr)


if __name__ == "__main__":
    main()
