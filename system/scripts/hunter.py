#!/usr/bin/env python3
"""Hunter planning, context, validation, scoring, and review feedback.

This module does not browse or mutate canonical intelligence. It gives cycle
functions a deterministic contract around Hunter's model-driven research.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker


SYSTEM_DIR = Path(__file__).resolve().parent.parent
PLAYBOOKS_PATH = SYSTEM_DIR / "research" / "hunter_playbooks.json"
RESOURCE_POLICY_PATH = SYSTEM_DIR / "research" / "hunter_resource_policy.json"
SCHEMA_PATH = SYSTEM_DIR / "schemas" / "hunter_research_packet.schema.json"
COVERAGE_PATH = SYSTEM_DIR / "research" / "deep_research_coverage.json"
DROP_DIR = SYSTEM_DIR / "inbox" / "chatgpt_intelligence_drop"
FEEDBACK_PATH = SYSTEM_DIR / "research" / "hunter_feedback.jsonl"

PRIMARY_OWNERS = {"government_or_regulator", "restaurant_or_operator", "customer_joint"}
INDEPENDENT_OWNERS = PRIMARY_OWNERS | {"trade_press", "general_news", "analyst_or_research"}
SOURCE_OWNER_SCORE = {
    "government_or_regulator": 100,
    "restaurant_or_operator": 95,
    "customer_joint": 90,
    "trade_press": 78,
    "general_news": 72,
    "analyst_or_research": 72,
    "vendor": 60,
    "public_review_or_directory": 45,
    "social_or_event": 40,
    "archive": 35,
    "other": 30,
}
TEMPORAL_SCORE = {
    "current_verified": 100,
    "current_probable": 80,
    "historical": 65,
    "superseded": 50,
    "currentness_unknown": 35,
}


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def registry() -> dict:
    return _load_json(PLAYBOOKS_PATH, {})


def resource_plan(
    depth: str,
    *,
    deep_research_available: bool = True,
) -> dict:
    policy = _load_json(RESOURCE_POLICY_PATH, {})
    recommended = int((policy.get("recommended_batch_sizes") or {}).get(depth, 1))
    return {
        "status": "authorized" if deep_research_available else "blocked",
        "reason": "ChatGPT Deep Research is available" if deep_research_available else "ChatGPT Deep Research transport is unavailable",
        "chat_research_status": "authorized" if deep_research_available else "unavailable",
        "codex_work_status": "blocked",
        "codex_work_reason": "Codex is not a Hunter research engine",
        "capacity_snapshot": {
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "five_hour_used_pct": None,
            "weekly_used_pct": None,
            "hours_to_weekly_reset": None,
        },
        "preferred_execution_tier": "chatgpt_deep_research_economy",
        "max_targets": None if deep_research_available else 0,
        "recommended_batch_targets": recommended,
        "five_hour_capacity_ceiling_pct": 0.0,
        "weekly_reserve_pct": None,
        "five_hour_reserve_pct": None,
        "reset_credit_allowed": False,
    }


def plan(playbook: str, depth: str | None = None) -> dict:
    data = registry()
    if playbook not in (data.get("playbooks") or {}):
        raise ValueError(f"unknown Hunter playbook: {playbook}")
    spec = data["playbooks"][playbook]
    selected_depth = depth or spec["default_depth"]
    if selected_depth not in (data.get("depth_levels") or {}):
        raise ValueError(f"unknown Hunter depth: {selected_depth}")
    return {
        "primary_mission": (data.get("operating_policy") or {}).get("primary_mission"),
        "required_phases": (data.get("operating_policy") or {}).get("required_phases") or [],
        "playbook": playbook,
        "depth": selected_depth,
        "payload_schema": spec["payload_schema"],
        "required_modules": spec["required_modules"],
        "preferred_sources": spec["preferred_sources"],
        "exit_criteria": spec["exit_criteria"],
        "budget": data["depth_levels"][selected_depth],
    }


def build_context(target_keys: list[str], research_modules: list[str] | None = None) -> dict:
    import hunter_source_registry

    wanted = set(target_keys)
    coverage = _load_json(COVERAGE_PATH, {}).get("targets") or {}
    targets = {key: coverage[key] for key in wanted if key in coverage}
    packets = []
    failed_sources = Counter()
    open_conflicts = []
    unanswered = []
    if DROP_DIR.is_dir():
        for path in sorted(DROP_DIR.glob("*.json"), reverse=True):
            data = _load_json(path, {})
            packet_targets = set(data.get("targets") or [])
            if not packet_targets.intersection(wanted):
                continue
            packets.append({
                "packet_id": data.get("packet_id") or path.stem,
                "path": str(path),
                "completed_at": data.get("completed_at"),
                "status": data.get("status"),
            })
            for source in data.get("source_ledger") or []:
                if not source.get("productive"):
                    host = urlparse(source.get("url") or "").netloc.lower().removeprefix("www.")
                    if host:
                        failed_sources[host] += 1
            open_conflicts.extend(c for c in data.get("conflicts") or [] if c.get("resolution_status") == "unresolved")
            unanswered.extend(data.get("unanswered_questions") or [])
            if len(packets) >= 20:
                break
    source_registry = hunter_source_registry.build_ranked_registry()
    return {
        "targets": targets,
        "prior_packets": packets,
        "repeatedly_unproductive_domains": [host for host, count in failed_sources.items() if count >= 2],
        "open_conflicts": open_conflicts,
        "unanswered_questions": list(dict.fromkeys(unanswered)),
        "source_registry_summary": {
            "source_count": source_registry["source_count"],
            "scoring_version": source_registry["scoring_version"],
            "updated_at": source_registry["updated_at"],
        },
        "recommended_sources": hunter_source_registry.research_route(
            source_registry, research_modules or [], limit=25
        ),
    }


def prepare_cycle(
    playbook: str,
    *,
    depth: str | None = None,
    universe: str = "all",
    target_keys: list[str] | None = None,
    limit: int | None = None,
    deep_research_available: bool = True,
) -> dict:
    """Create a research-ready cycle directive from live RBB gap state."""
    import hunter_gap_manifest

    cycle_plan = plan(playbook, depth)
    capacity = resource_plan(
        cycle_plan["depth"],
        deep_research_available=deep_research_available,
    )
    effective_limit = limit
    if capacity["status"] == "authorized":
        ceiling_targets = capacity.get("max_targets")
        if ceiling_targets is None:
            effective_limit = limit
        else:
            effective_limit = min(limit, ceiling_targets) if limit is not None else ceiling_targets
    else:
        # Gap export and context assembly are local/deterministic and remain
        # useful while model research is blocked. Keep the directive bounded,
        # but do not erase the work queue merely because capacity is scarce.
        preview_limit = int((_load_json(RESOURCE_POLICY_PATH, {}).get("recommended_batch_sizes") or {}).get(cycle_plan["depth"], 1))
        effective_limit = min(limit, preview_limit) if limit is not None else preview_limit
    manifest = hunter_gap_manifest.build_manifest(
        universe=universe,
        target_keys=target_keys,
        limit=effective_limit,
    )
    selected_keys = [target["target_key"] for target in manifest["targets"]]
    known_gap_ids = [gap["gap_id"] for target in manifest["targets"] for gap in target["gaps"]]
    discovery_domains = list(dict.fromkeys(
        domain for target in manifest["targets"] for domain in target["discovery_domains"]
    ))
    return {
        "schema": "rb.hunter_cycle_directive.v1",
        "prepared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "plan": cycle_plan,
        "resource_plan": capacity,
        "gap_manifest": manifest,
        "prior_context": build_context(selected_keys, cycle_plan["required_modules"]),
        "packet_requirements": {
            "response_contract": {
                "content": "one_inline_json_object",
                "transport": "utf8_text",
                "accepted_artifact_extensions": [".txt", ".md", ".json"],
                "downloadable_attachment_required": False,
                "canonical_format_after_validation": "json",
            },
            "prior_state_as_of": manifest["generated_at"],
            "known_gap_ids": known_gap_ids,
            "discovery_domains": discovery_domains,
            "target_keys": selected_keys,
            "payload_schema": cycle_plan["payload_schema"],
            "research_authorized": capacity["status"] == "authorized",
        },
    }


def _independent_chains(sources: list[dict]) -> int:
    chains = set()
    for source in sources:
        chain = source.get("evidence_chain_id")
        if chain:
            chains.add(chain)
            continue
        host = urlparse(source.get("canonical_url") or source.get("url") or "").netloc.lower().removeprefix("www.")
        if host:
            chains.add(host)
    return len(chains)


def score_packet(packet: dict) -> dict:
    sources = {s.get("source_id"): s for s in packet.get("source_ledger") or []}
    module_scores = {}
    for module in packet.get("research_modules") or []:
        name = module.get("name")
        matching = [f for f in packet.get("findings") or [] if f.get("module") == name]
        status_score = {"complete": 1.0, "partial": 0.55, "not_applicable": 1.0, "blocked": 0.0}.get(module.get("status"), 0.0)
        citation_score = sum(bool(f.get("source_ids")) for f in matching) / len(matching) if matching else (1.0 if module.get("status") == "not_applicable" else 0.0)
        module_scores[name] = round(0.65 * status_score + 0.35 * citation_score, 3)

    finding_scores = {}
    for finding in packet.get("findings") or []:
        cited = [sources[sid] for sid in finding.get("source_ids") or [] if sid in sources]
        source_score = max((SOURCE_OWNER_SCORE.get(s.get("source_owner"), 30) for s in cited), default=0)
        chain_score = min(100, _independent_chains(cited) * 45)
        temporal_score = TEMPORAL_SCORE.get(finding.get("temporal_status"), 35)
        scope_score = 35 if finding.get("scope") in {"unknown", "unknown deployment scope"} else 90
        score = round(0.4 * source_score + 0.25 * chain_score + 0.2 * temporal_score + 0.15 * scope_score)
        finding_scores[finding.get("finding_id")] = max(0, min(100, score))

    required = [m for m in packet.get("research_modules") or [] if m.get("required")]
    completeness = round(100 * sum(module_scores.get(m.get("name"), 0) for m in required) / len(required)) if required else 100
    return {
        "overall_completeness_pct": completeness,
        "module_completeness": module_scores,
        "finding_quality_scores": finding_scores,
        "independent_evidence_chains": _independent_chains(packet.get("source_ledger") or []),
    }


def validate_packet(packet: dict) -> dict:
    import hunter_citation_verify
    import hunter_payload_validate

    schema = _load_json(SCHEMA_PATH, {})
    errors = []
    for error in Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(packet):
        path = "/".join(map(str, error.absolute_path)) or "<root>"
        errors.append({"code": "schema", "path": path, "message": error.message})

    # Semantic checks assume the canonical container types. Model output is an
    # untrusted boundary, so return schema diagnostics instead of crashing on
    # malformed arrays or objects.
    expected_types = {
        "cycle": dict, "targets": list, "research_modules": list,
        "source_ledger": list, "findings": list, "gap_outcomes": list,
        "change_events": list, "mutation_proposals": list, "cos_handoffs": list,
        "quality": dict, "resource_usage": dict, "payload": dict,
    }
    unsafe = [key for key, kind in expected_types.items() if key in packet and not isinstance(packet.get(key), kind)]
    object_arrays = ("targets", "research_modules", "source_ledger", "findings", "gap_outcomes", "change_events", "mutation_proposals", "cos_handoffs")
    unsafe.extend(key for key in object_arrays if isinstance(packet.get(key), list) and any(not isinstance(item, dict) for item in packet[key]))
    if unsafe:
        return {
            "valid": False,
            "errors": errors + [{"code": "unsafe_packet_shape", "path": key, "message": "cannot run semantic validation on malformed container"} for key in sorted(set(unsafe))],
            "scores": {},
            "citation_verification": {"valid": False, "errors": [], "checked_findings": 0, "checked_sources": 0},
        }

    sources = packet.get("source_ledger") or []
    source_ids = [s.get("source_id") for s in sources]
    source_by_id = {s.get("source_id"): s for s in sources}
    if len(source_ids) != len(set(source_ids)):
        errors.append({"code": "duplicate_source_id", "path": "source_ledger", "message": "source_id values must be unique"})

    findings = packet.get("findings") or []
    known_gap_ids = set((packet.get("cycle") or {}).get("known_gap_ids") or [])
    finding_ids = {f.get("finding_id") for f in findings}
    for index, finding in enumerate(findings):
        path = f"findings/{index}"
        missing = [sid for sid in finding.get("source_ids") or [] if sid not in source_by_id]
        if missing:
            errors.append({"code": "unknown_source_reference", "path": path, "message": f"unknown source_ids: {missing}"})
        claimed_gap_ids = set(finding.get("gap_ids") or [])
        unknown_gaps = claimed_gap_ids - known_gap_ids
        if unknown_gaps:
            errors.append({"code": "unknown_gap_reference", "path": path, "message": f"unknown gap_ids: {sorted(unknown_gaps)}"})
        if finding.get("contribution_type") == "fills_known_gap" and not claimed_gap_ids:
            errors.append({"code": "gap_finding_without_gap", "path": path, "message": "fills_known_gap requires at least one gap_id"})
        if finding.get("contribution_type") == "new_data_point" and not finding.get("novelty_rationale"):
            errors.append({"code": "new_data_without_novelty", "path": path, "message": "new_data_point requires novelty_rationale against prior RBB state"})
        cited = [source_by_id[sid] for sid in finding.get("source_ids") or [] if sid in source_by_id]
        nonfree = [s.get("source_id") for s in cited if s.get("access_tier") != "free_public"]
        if nonfree:
            errors.append({"code": "nonfree_source_supports_finding", "path": path, "message": f"findings may use only free_public evidence: {nonfree}"})
        if finding.get("is_inference") and not finding.get("inference_premises"):
            errors.append({"code": "inference_without_premises", "path": path, "message": "inferences require premises"})
        if finding.get("evidence_status") == "verified":
            independent = [s for s in cited if s.get("source_owner") in INDEPENDENT_OWNERS]
            vendor_only = cited and all(s.get("source_owner") == "vendor" for s in cited)
            if vendor_only or not independent:
                errors.append({"code": "verified_without_independent_support", "path": path, "message": "verified claims need non-vendor independent support"})
            elif not any(s.get("source_owner") in PRIMARY_OWNERS for s in cited) and _independent_chains(independent) < 2:
                errors.append({"code": "verified_single_evidence_chain", "path": path, "message": "verified claims without a primary source need two independent evidence chains"})
        claim_lower = str(finding.get("claim") or "").lower()
        if any(term in claim_lower for term in ("deployed", "installed", "live at", "rollout")) and finding.get("scope") in {"unknown", "unknown deployment scope"}:
            errors.append({"code": "deployment_scope_unknown", "path": path, "message": "deployment claim requires explicit scope"})
        if any(term in claim_lower for term in ("deployed", "installed", "live at", "rollout")):
            announcement_only = cited and all(
                (s.get("source_type") or "").lower() in {"press release", "announcement"}
                and s.get("source_owner") in {"vendor", "customer_joint"}
                for s in cited
            )
            if announcement_only and finding.get("evidence_status") in {"verified", "supported"}:
                errors.append({"code": "announcement_not_deployment", "path": path, "message": "announcement evidence alone cannot establish live deployment"})
        if any(term in claim_lower for term in ("uses ", "customer", "deployed", "installed")) and cited and all(
            (s.get("source_type") or "").lower() in {"integration page", "partner directory"} for s in cited
        ):
            errors.append({"code": "integration_not_customer_proof", "path": path, "message": "integration availability does not establish customer use"})
        if finding.get("scope") in {"enterprise", "brand-wide", "systemwide"} and any(term in claim_lower for term in ("franchisee", "operator-owned", "one location")):
            errors.append({"code": "scope_text_conflict", "path": path, "message": "claim wording conflicts with enterprise scope"})
        if finding.get("temporal_status") == "current_verified" and finding.get("as_of") is None:
            errors.append({"code": "current_without_as_of", "path": path, "message": "current_verified requires as_of"})
        if finding.get("temporal_status") == "current_verified" and finding.get("as_of"):
            try:
                completed = datetime.fromisoformat(str(packet.get("completed_at")).replace("Z", "+00:00")).date()
                as_of = date.fromisoformat(finding["as_of"])
                if (completed - as_of).days > 550:
                    errors.append({"code": "stale_claim_marked_current", "path": path, "message": "current_verified evidence is more than 550 days old"})
            except (TypeError, ValueError):
                pass
        if any((s.get("source_type") or "").lower() in {"logo wall", "search snippet"} for s in cited) and finding.get("evidence_status") in {"verified", "supported"}:
            errors.append({"code": "discovery_source_overpromoted", "path": path, "message": "logo walls and search snippets are discovery-only"})

    quality = packet.get("quality") or {}
    expected = {
        "pages_opened": len(sources),
        "productive_pages": sum(bool(s.get("productive")) for s in sources),
        "primary_source_pages": sum(s.get("source_owner") in PRIMARY_OWNERS for s in sources),
        "findings_count": len(findings),
        "findings_with_sources": sum(bool(f.get("source_ids")) for f in findings),
    }
    for key, value in expected.items():
        if quality.get(key) != value:
            errors.append({"code": "quality_count_mismatch", "path": f"quality/{key}", "message": f"expected {value}, got {quality.get(key)}"})

    for index, source in enumerate(sources):
        if source.get("access_tier") == "paid_subscription" and source.get("productive"):
            errors.append({"code": "paid_source_marked_productive", "path": f"source_ledger/{index}", "message": "paid sources cannot be productive under Hunter's free-public policy"})
        if source.get("access_tier") == "paid_subscription" and source.get("access_status") == "accessible":
            errors.append({"code": "paid_source_accessed", "path": f"source_ledger/{index}", "message": "Hunter must not access paid subscription content"})

    outcomes = packet.get("gap_outcomes") or []
    outcome_gap_ids = [row.get("gap_id") for row in outcomes]
    if len(outcome_gap_ids) != len(set(outcome_gap_ids)):
        errors.append({"code": "duplicate_gap_outcome", "path": "gap_outcomes", "message": "each gap must have exactly one outcome"})
    missing_outcomes = known_gap_ids - set(outcome_gap_ids)
    extra_outcomes = set(outcome_gap_ids) - known_gap_ids
    if missing_outcomes:
        errors.append({"code": "missing_gap_outcome", "path": "gap_outcomes", "message": f"missing outcomes: {sorted(missing_outcomes)}"})
    if extra_outcomes:
        errors.append({"code": "unknown_gap_outcome", "path": "gap_outcomes", "message": f"unknown gap outcomes: {sorted(extra_outcomes)}"})
    for index, outcome in enumerate(outcomes):
        bad_finding_ids = set(outcome.get("finding_ids") or []) - finding_ids
        if bad_finding_ids:
            errors.append({"code": "unknown_gap_finding", "path": f"gap_outcomes/{index}", "message": f"unknown finding_ids: {sorted(bad_finding_ids)}"})
        if outcome.get("status") in {"filled", "partially_filled"} and not outcome.get("finding_ids"):
            errors.append({"code": "filled_gap_without_finding", "path": f"gap_outcomes/{index}", "message": "filled outcomes require finding_ids"})

    outcome_by_gap = {row.get("gap_id"): row for row in outcomes}
    for index, recommendation in enumerate(packet.get("paid_source_recommendations") or []):
        path = f"paid_source_recommendations/{index}"
        recommendation_gaps = set(recommendation.get("unresolved_gap_ids") or [])
        if recommendation_gaps - known_gap_ids:
            errors.append({"code": "paid_recommendation_unknown_gap", "path": path, "message": "paid recommendation references an unknown gap"})
        resolved = [gap for gap in recommendation_gaps if (outcome_by_gap.get(gap) or {}).get("status") == "filled"]
        if resolved:
            errors.append({"code": "paid_recommendation_for_filled_gap", "path": path, "message": f"paid source is unnecessary for already-filled gaps: {resolved}"})

    change_events = packet.get("change_events") or []
    change_ids = {event.get("change_event_id") for event in change_events}
    target_keys = {target.get("target_key") for target in packet.get("targets") or []}
    for index, event in enumerate(change_events):
        path = f"change_events/{index}"
        if event.get("target_key") not in target_keys:
            errors.append({"code": "change_unknown_target", "path": path, "message": "change event target is not in packet targets"})
        if set(event.get("finding_ids") or []) - finding_ids:
            errors.append({"code": "change_unknown_finding", "path": path, "message": "change event references unknown findings"})
        if set(event.get("source_ids") or []) - set(source_ids):
            errors.append({"code": "change_unknown_source", "path": path, "message": "change event references unknown sources"})

    proposals = packet.get("mutation_proposals") or []
    for index, proposal in enumerate(proposals):
        path = f"mutation_proposals/{index}"
        if proposal.get("target_key") not in target_keys:
            errors.append({"code": "mutation_unknown_target", "path": path, "message": "mutation target is not in packet targets"})
        if set(proposal.get("change_event_ids") or []) - change_ids:
            errors.append({"code": "mutation_unknown_change", "path": path, "message": "mutation proposal references unknown change events"})
        if set(proposal.get("finding_ids") or []) - finding_ids:
            errors.append({"code": "mutation_unknown_finding", "path": path, "message": "mutation proposal references unknown findings"})
        if set(proposal.get("source_ids") or []) - set(source_ids):
            errors.append({"code": "mutation_unknown_source", "path": path, "message": "mutation proposal references unknown sources"})

    for index, handoff in enumerate(packet.get("cos_handoffs") or []):
        path = f"cos_handoffs/{index}"
        if set(handoff.get("change_event_ids") or []) - change_ids:
            errors.append({"code": "cos_unknown_change", "path": path, "message": "CoS handoff references unknown change events"})
        if set(handoff.get("finding_ids") or []) - finding_ids:
            errors.append({"code": "cos_unknown_finding", "path": path, "message": "CoS handoff references unknown findings"})
        if set(handoff.get("connected_target_keys") or []) - target_keys:
            errors.append({"code": "cos_unknown_target", "path": path, "message": "CoS handoff references unknown targets"})

    high_materiality = {event.get("change_event_id") for event in change_events if event.get("materiality") in {"critical", "high"}}
    covered_changes = {
        change_id for proposal in proposals for change_id in proposal.get("change_event_ids") or []
    } | {
        change_id for handoff in packet.get("cos_handoffs") or [] for change_id in handoff.get("change_event_ids") or []
    }
    if high_materiality - covered_changes:
        errors.append({"code": "material_change_unrouted", "path": "change_events", "message": f"material changes need mutation or CoS routing: {sorted(high_materiality - covered_changes)}"})

    required_incomplete = [m.get("name") for m in packet.get("research_modules") or [] if m.get("required") and m.get("status") != "complete"]
    if packet.get("status") == "complete" and required_incomplete:
        errors.append({"code": "false_complete", "path": "status", "message": f"required modules incomplete: {required_incomplete}"})
    if packet.get("status") == "complete" and not quality.get("required_modules_complete"):
        errors.append({"code": "false_complete_quality", "path": "quality/required_modules_complete", "message": "complete packet must mark required modules complete"})

    resource = (packet.get("cycle") or {}).get("resource_plan") or {}
    usage = packet.get("resource_usage") or {}
    if resource.get("status") == "blocked" and packet.get("status") != "blocked":
        errors.append({"code": "research_ran_while_resource_blocked", "path": "status", "message": "resource-blocked cycle must remain blocked"})
    if resource.get("reset_credit_allowed") is not False:
        errors.append({"code": "reset_credit_not_allowed", "path": "cycle/resource_plan/reset_credit_allowed", "message": "Hunter may never authorize a reset credit"})
    if usage.get("premium_reasoning_calls", 0) > 0 and not usage.get("escalation_reasons"):
        errors.append({"code": "premium_without_escalation_reason", "path": "resource_usage", "message": "premium reasoning calls require an escalation reason"})
    if usage.get("targets_completed", 0) > usage.get("targets_attempted", 0):
        errors.append({"code": "resource_target_count_invalid", "path": "resource_usage", "message": "targets_completed cannot exceed targets_attempted"})
    if resource.get("max_targets") is not None and usage.get("targets_attempted", 0) > resource.get("max_targets", 0):
        errors.append({"code": "resource_target_ceiling_exceeded", "path": "resource_usage", "message": "targets_attempted exceeds authorized bounded batch"})

    playbook = (packet.get("cycle") or {}).get("playbook")
    if playbook:
        try:
            expected_schema = plan(playbook, (packet.get("cycle") or {}).get("depth"))["payload_schema"]
            if packet.get("payload_schema") != expected_schema:
                errors.append({"code": "payload_schema_mismatch", "path": "payload_schema", "message": f"playbook requires {expected_schema}"})
        except ValueError as exc:
            errors.append({"code": "unknown_playbook", "path": "cycle/playbook", "message": str(exc)})

    errors.extend(hunter_payload_validate.validate(packet.get("payload_schema") or "", packet.get("payload")))
    citation_report = hunter_citation_verify.verify(packet)
    errors.extend(citation_report["errors"])

    return {
        "valid": not errors,
        "errors": errors,
        "scores": score_packet(packet),
        "citation_verification": citation_report,
    }


def record_feedback(packet_id: str, outcome: str, accepted: int, rejected: int, corrected: int, notes: str | None = None) -> dict:
    if outcome not in {"accepted", "partially_accepted", "rejected", "corrected"}:
        raise ValueError("invalid feedback outcome")
    receipt = {
        "schema": "rb.hunter_feedback.v1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "packet_id": packet_id,
        "outcome": outcome,
        "findings_accepted": accepted,
        "findings_rejected": rejected,
        "findings_corrected": corrected,
        "notes": notes,
    }
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, sort_keys=True) + "\n")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Hunter research orchestration utilities")
    subs = parser.add_subparsers(dest="command", required=True)
    p_plan = subs.add_parser("plan")
    p_plan.add_argument("playbook")
    p_plan.add_argument("--depth")
    p_context = subs.add_parser("context")
    p_context.add_argument("targets", nargs="+")
    p_prepare = subs.add_parser("prepare")
    p_prepare.add_argument("playbook")
    p_prepare.add_argument("--depth")
    p_prepare.add_argument("--universe", choices=["all", "brands", "competitors", "franchisees", "fdd"], default="all")
    p_prepare.add_argument("--target", action="append", dest="targets")
    p_prepare.add_argument("--limit", type=int)
    p_prepare.add_argument("--output")
    p_prepare.add_argument("--deep-research-unavailable", action="store_true", help="Block research authorization when ChatGPT Deep Research transport is unavailable")
    p_resource = subs.add_parser("resource-plan")
    p_resource.add_argument("--depth", choices=["scan", "standard", "deep", "forensic", "monitor"], required=True)
    p_resource.add_argument("--deep-research-unavailable", action="store_true", help="Block research authorization when ChatGPT Deep Research transport is unavailable")
    p_validate = subs.add_parser("validate")
    p_validate.add_argument("packet")
    p_feedback = subs.add_parser("feedback")
    p_feedback.add_argument("packet_id")
    p_feedback.add_argument("outcome")
    p_feedback.add_argument("--accepted", type=int, default=0)
    p_feedback.add_argument("--rejected", type=int, default=0)
    p_feedback.add_argument("--corrected", type=int, default=0)
    p_feedback.add_argument("--notes")
    args = parser.parse_args()
    try:
        if args.command == "plan":
            result = plan(args.playbook, args.depth)
        elif args.command == "context":
            result = build_context(args.targets)
        elif args.command == "prepare":
            result = prepare_cycle(
                args.playbook,
                depth=args.depth,
                universe=args.universe,
                target_keys=args.targets,
                limit=args.limit,
                deep_research_available=not args.deep_research_unavailable,
            )
            if args.output:
                Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        elif args.command == "resource-plan":
            result = resource_plan(
                args.depth,
                deep_research_available=not args.deep_research_unavailable,
            )
        elif args.command == "validate":
            result = validate_packet(_load_json(Path(args.packet), {}))
        else:
            result = record_feedback(args.packet_id, args.outcome, args.accepted, args.rejected, args.corrected, args.notes)
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("valid", True) else 1


if __name__ == "__main__":
    sys.exit(main())
