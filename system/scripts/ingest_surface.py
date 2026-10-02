#!/usr/bin/env python3
"""
ingest_surface.py — Canonical CoS Mutation Surface formatter (RB 9.25).

Formats the output of any intelligence ingest call into a structured,
human-readable CoS mutation surface for inline display in live conversation
responses. Replaces silent processing with an explicit, trust-contract-compliant
output block the user can see and act on.

Surface format:
  INTELLIGENCE CAPTURED — <type> (<confidence>)
  ─────────────────────────────────────────────
  WHAT WAS FOUND
  TRUST STATS
  MUTATION PROPOSALS (requires confirmation)
  RETRIEVAL HOOKS ACTIVATED
  RECOMMENDED ACTIONS
  EMPLOYER SENSITIVITY WARNING (if applicable)

Invariants:
  - Never omits trust_stats — if the ingest result has no trust_stats,
    the surface emits an explicit "Trust stats missing — trust contract breach" warning.
  - Never implies persistence occurred without confirmation.
  - Employer-sensitive content is always flagged before any external use.
  - All mutation proposals are rendered as pending — never confirmed.
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Ingest type detection
# ---------------------------------------------------------------------------

def _detect_ingest_type(result: dict) -> str:
    """Detect which intelligence module produced this result."""
    if "experiences" in result and "employer_sensitive" in result:
        return "experience"
    if "behavioral_signals" in result and "behavioral_artifacts" in result:
        return "macro"
    if "interactions" in result and "cos_surface" in result:
        return "relationship"
    if "insights" in result and "retrieval_tags" in result:
        return "insight"
    return "unknown"


# ---------------------------------------------------------------------------
# Section formatters
# ---------------------------------------------------------------------------

def _fmt_trust_stats(trust_stats: dict | None) -> list[str]:
    lines = ["TRUST STATS"]
    if not trust_stats:
        lines.append("  ⚠  Trust stats missing — trust contract not satisfied.")
        return lines

    assessed = trust_stats.get("sources_assessed", "?")
    accepted = trust_stats.get("sources_accepted", "?")
    rejected = trust_stats.get("sources_rejected", "?")
    confidence = trust_stats.get("confidence", "unknown")
    contract_met = trust_stats.get("trust_contract_met", False)

    lines.append(f"  Sources assessed: {assessed} | accepted: {accepted} | rejected: {rejected}")
    lines.append(f"  Confidence: {confidence}")
    lines.append(f"  Trust contract met: {'yes' if contract_met else 'NO — review required'}")

    recs = trust_stats.get("mutation_recommendations") or []
    if recs:
        lines.append(f"  Mutation targets: {', '.join(recs)}")

    loops = trust_stats.get("follow_up_loops") or []
    for loop in loops:
        lines.append(f"  Follow-up: {loop}")

    ret = trust_stats.get("retrieval_classifications") or []
    if ret:
        lines.append(f"  Retrieval domains active: {', '.join(ret)}")

    return lines


def _fmt_mutations(proposals: list[dict]) -> list[str]:
    if not proposals:
        return []
    lines = [f"MUTATION PROPOSALS — {len(proposals)} pending (all require your confirmation)"]
    for p in proposals:
        mtype = p.get("mutation_type", "unknown")
        target = p.get("target", "?")
        op = p.get("operation", "add")
        lines.append(f"  [{mtype}] → {target} ({op}) — requires_confirmation: yes")
    return lines


def _fmt_retrieval_hooks(hooks: dict) -> list[str]:
    if not hooks:
        return []
    lines = [f"RETRIEVAL HOOKS ACTIVATED ({len(hooks)} domains)"]
    for domain, lessons in hooks.items():
        lesson_str = ", ".join(lessons[:3])
        lines.append(f"  {domain}: {lesson_str}")
    return lines


def _fmt_recommended_actions(actions: list[str]) -> list[str]:
    if not actions:
        return []
    lines = ["RECOMMENDED ACTIONS"]
    for action in actions:
        lines.append(f"  → {action}")
    return lines


def _fmt_employer_warning(employer_sensitive: bool, result: dict) -> list[str]:
    if not employer_sensitive:
        return []
    return [
        "⚠  EMPLOYER SENSITIVITY DETECTED",
        "  Internal version retains full fidelity (names, companies).",
        "  External version has been anonymized. Always use GET /ingest/experience/externalize",
        "  before referencing this intelligence in any external content.",
    ]


# ---------------------------------------------------------------------------
# Type-specific what-was-found sections
# ---------------------------------------------------------------------------

def _found_experience(result: dict) -> list[str]:
    experiences = result.get("experiences") or []
    if not experiences:
        return ["WHAT WAS FOUND", "  No experiential intelligence detected."]

    lines = [f"WHAT WAS FOUND — {len(experiences)} intelligence record(s) classified"]
    for exp in experiences:
        intel_type = exp.get("intel_type", "unknown")
        confidence = exp.get("confidence", "?")
        title = exp.get("case_study", {}).get("title", "untitled")
        lines.append(f"  [{intel_type} | {confidence}] {title}")
        iv = exp.get("case_study", {}).get("internal_version", {})
        lessons = iv.get("lessons_learned") or []
        if lessons:
            lines.append(f"    Lessons: {lessons[0][:120]}")
        root_causes = iv.get("root_causes") or []
        if root_causes:
            lines.append(f"    Root cause: {root_causes[0][:120]}")
    return lines


def _found_macro(result: dict) -> list[str]:
    signals = result.get("behavioral_signals") or []
    artifacts = result.get("behavioral_artifacts") or []
    entity_mutations = result.get("entity_risk_mutations") or []
    tech = result.get("tech_implications") or []
    brief_layers = result.get("daily_brief_layers") or []

    lines = [
        f"WHAT WAS FOUND — {len(signals)} behavioral signal(s), "
        f"{len(artifacts)} artifact(s), {len(entity_mutations)} entity mutation(s)"
    ]
    for sig in signals:
        stype = sig.get("signal_type", "?")
        conf = sig.get("confidence", "?")
        lines.append(f"  [{stype} | {conf}]")

    for art in artifacts:
        name = art.get("name", "?")
        defn = art.get("definition", "")[:80]
        lines.append(f"  Artifact: {name} — {defn}")

    for ent in entity_mutations:
        entity = ent.get("entity", "?")
        dims = ent.get("risk_dimensions") or {}
        lines.append(f"  Entity: {entity} → {', '.join(f'{k}={v}' for k, v in list(dims.items())[:3])}")

    if tech:
        lines.append(f"  Tech implications: {len(tech)}")

    ri = result.get("ri_mutation")
    if ri and not ri.get("error") and ri.get("cos_surface"):
        cos = ri["cos_surface"]
        lines.append(f"  RI mutation: {cos.get('entity_name', '?')} → {cos.get('strategic_classification', '?')}")

    if brief_layers:
        lines.append(f"  Daily brief layers: {', '.join(brief_layers)}")

    return lines


def _found_relationship(result: dict) -> list[str]:
    interactions = result.get("interactions") or []
    cos = result.get("cos_surface") or {}

    if not interactions:
        return ["WHAT WAS FOUND", "  No relationship intelligence extracted."]

    lines = [f"WHAT WAS FOUND — {len(interactions)} interaction(s) classified"]
    for interaction in interactions:
        entity = interaction.get("entity") or {}
        name = entity.get("name") or interaction.get("entity_name", "?")
        signal = interaction.get("signal_type", "?")
        trust_delta = interaction.get("trust_delta", 0)
        state = interaction.get("relationship_state_proposed") or interaction.get("relationship_state", "?")
        classification = interaction.get("strategic_classification", "?")
        lines.append(f"  {name} | {signal} | trust Δ+{trust_delta} | {state} | {classification}")

        posture = interaction.get("recommended_posture", "")
        if posture:
            lines.append(f"    Posture: {posture}")

    return lines


def _found_insight(result: dict) -> list[str]:
    insights = result.get("insights") or []
    if not insights:
        return ["WHAT WAS FOUND", "  No durable strategic insights detected."]

    lines = [f"WHAT WAS FOUND — {len(insights)} insight(s) classified"]
    for insight in insights:
        itype = insight.get("insight_type", "?")
        confidence = insight.get("confidence", "?")
        claim = insight.get("claim", "")[:120]
        lines.append(f"  [{itype} | {confidence}] {claim}")

    ret_tags = result.get("retrieval_tags") or []
    if ret_tags:
        lines.append(f"  Retrieval tags: {', '.join(ret_tags[:6])}")

    return lines


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

def _header(ingest_type: str, result: dict) -> list[str]:
    type_labels = {
        "experience": "EXPERIENTIAL INTELLIGENCE",
        "macro": "MACRO BEHAVIORAL INTELLIGENCE",
        "relationship": "RELATIONSHIP INTELLIGENCE",
        "insight": "STRATEGIC INSIGHT",
        "unknown": "INTELLIGENCE",
    }
    label = type_labels.get(ingest_type, "INTELLIGENCE")

    persistence_status = result.get("persistence_status", "unknown")
    count_key = {
        "experience": "experience_count",
        "macro": "signal_count",
        "relationship": None,
        "insight": "insight_count",
    }.get(ingest_type)
    count = result.get(count_key, "") if count_key else len(result.get("interactions") or [])
    count_str = f" | {count} record(s)" if count else ""

    return [
        f"── {label} CAPTURED{count_str} ──",
        f"  Status: {persistence_status} | All mutations pending confirmation",
        "─" * 55,
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def format_surface(result: dict) -> str:
    """Format any intelligence ingest result as a canonical CoS mutation surface.

    Accepts the output of:
      - experiential_intelligence.process_experiential_signal()
      - macro_intelligence.process_macro_signal()
      - relationship_intake.process_relationship_thread()
      - insight_intake.process_text()

    Returns a multi-line string ready for inline display in a GPT conversation response.
    """
    if not result or not isinstance(result, dict):
        return "── INTELLIGENCE SURFACE ERROR ──\n  No result provided.\n"

    ingest_type = _detect_ingest_type(result)

    sections: list[list[str]] = []

    # Header
    sections.append(_header(ingest_type, result))

    # What was found (type-specific)
    found_fn = {
        "experience": _found_experience,
        "macro": _found_macro,
        "relationship": _found_relationship,
        "insight": _found_insight,
    }.get(ingest_type)
    if found_fn:
        sections.append(found_fn(result))

    # Trust stats (always)
    trust_stats = result.get("trust_stats")
    sections.append(_fmt_trust_stats(trust_stats))

    # Mutation proposals
    proposals = result.get("mutation_proposals") or []
    if proposals:
        sections.append(_fmt_mutations(proposals))

    # Retrieval hooks (experience and macro)
    hooks = result.get("retrieval_hooks") or {}
    if hooks:
        sections.append(_fmt_retrieval_hooks(hooks))

    # Recommended actions (experience)
    actions = result.get("recommended_actions") or []
    if actions:
        sections.append(_fmt_recommended_actions(actions))

    # Employer sensitivity warning
    employer_sensitive = result.get("employer_sensitive", False)
    warning = _fmt_employer_warning(employer_sensitive, result)
    if warning:
        sections.append(warning)

    # Persist note
    persistence = result.get("persistence_status", "")
    if persistence not in ("RB did not persist",):
        sections.append([
            "NEXT STEPS",
            "  Confirm individual records via POST /ingest/<type>/confirm to lock mutations.",
            "  Rejected records are permanently skipped — not auto-confirmed.",
        ])

    lines = []
    for section in sections:
        lines.extend(section)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def format_confirm(record_type: str, record: dict) -> str:
    """Format a confirmation response into a brief CoS acknowledgment."""
    if "error" in record:
        return f"── CONFIRMATION ERROR ──\n  {record['error']}\n"

    rec_id = record.get("id", "?")
    claim_status = record.get("claim_status", "?")
    persistence_status = record.get("persistence_status", "?")
    confirmed_at = record.get("confirmed_at", "")

    status_icon = "✓" if claim_status == "confirmed" else "✗"
    return (
        f"── {record_type.upper()} {status_icon} {claim_status.upper()} ──\n"
        f"  ID: {rec_id}\n"
        f"  Persistence: {persistence_status}\n"
        f"  Confirmed at: {confirmed_at or 'n/a'}\n"
    )


def format_retrieval(domain: str, result: dict) -> str:
    """Format a retrieval hook query result as a CoS context brief."""
    match_count = result.get("match_count", 0)
    confidence = result.get("retrieval_confidence", "none")
    lesson_types = result.get("lesson_types") or []
    matched = result.get("matched_experiences") or []

    if match_count == 0:
        return (
            f"── RETRIEVAL: {domain.upper()} ──\n"
            f"  No confirmed prior lessons found for this domain.\n"
        )

    lines = [
        f"── RETRIEVAL: {domain.upper()} | {match_count} record(s) | confidence: {confidence} ──",
        f"  Lesson types available: {', '.join(lesson_types)}",
    ]
    for exp in matched[:3]:
        title = exp.get("case_study_title", "?")
        intel_type = exp.get("intel_type", "?")
        sensitive = " [employer-sensitive — use externalize endpoint]" if exp.get("employer_sensitive") else ""
        lines.append(f"  [{intel_type}] {title}{sensitive}")

    return "\n".join(lines) + "\n"
