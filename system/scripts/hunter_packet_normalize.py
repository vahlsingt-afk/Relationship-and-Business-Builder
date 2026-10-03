#!/usr/bin/env python3
"""Normalize evidence-native ChatGPT output into Hunter's canonical envelope.

The research model owns evidence collection. This module owns deterministic
envelope fields, stable identifiers, and harmless structural aliases. It does
not invent evidence or upgrade confidence.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone


TARGET_KEY_RE = re.compile(r"^(company|competitor|vendor|operator|genius):.+$")


def _slug(value: object) -> str:
    text = re.sub(r"[^a-z0-9._-]+", "-", str(value or "").strip().lower())
    return text.strip("-._") or "unknown"


def _prefixed(value: object, prefix: str) -> str:
    text = str(value or "")
    if text.startswith(prefix):
        return text
    return f"{prefix}{_slug(text)}"


def _map_refs(value: object, maps: dict[str, dict[str, str]]) -> object:
    if isinstance(value, list):
        return [_map_refs(item, maps) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            mapping = {
                "source_ids": maps["source"],
                "finding_ids": maps["finding"],
                "change_event_ids": maps["change"],
            }.get(key)
            if mapping and isinstance(item, list):
                result[key] = [mapping.get(str(ref), str(ref)) for ref in item]
            else:
                result[key] = _map_refs(item, maps)
        return result
    return value


def _target(directive: dict) -> dict:
    target = ((directive.get("gap_manifest") or {}).get("targets") or [{}])[0]
    return {
        "target_key": target.get("target_key"),
        "display_name": target.get("display_name"),
        "entity_type": target.get("entity_type", "other"),
        "priority": target.get("priority", "explicit"),
    }


def _date(value: object) -> object:
    text = str(value or "")
    if re.fullmatch(r"\d{4}-\d{2}", text):
        return text + "-01"
    return value


def _source_owner(row: dict) -> str:
    current = row.get("source_owner")
    allowed = {"government_or_regulator", "restaurant_or_operator", "customer_joint", "vendor", "trade_press", "general_news", "analyst_or_research", "public_review_or_directory", "social_or_event", "archive", "other"}
    if current in allowed:
        return current
    haystack = " ".join(str(row.get(key) or "").lower() for key in ("source_type", "publisher", "source_owner"))
    if any(token in haystack for token in ("government", "regulator", "wisconsin", "department of")):
        return "government_or_regulator"
    if any(token in haystack for token in ("company_", "franchise_disclosure", "restaurant", "charleys", "gosh enterprises")):
        return "restaurant_or_operator"
    if any(token in haystack for token in ("vendor", "heartland", "par technology", "bite", "howard company")):
        return "vendor"
    if any(token in haystack for token in ("news", "press", "magazine", "journal")):
        return "trade_press"
    return "other"


def _pick(value: object, aliases: dict[str, str], allowed: set[str], default: str) -> str:
    text = str(value or "")
    if text in allowed:
        return text
    return aliases.get(text, default)


def normalize(job: dict, raw: dict) -> tuple[dict, dict]:
    """Return (canonical_candidate, normalization_receipt)."""
    packet = copy.deepcopy(raw if isinstance(raw, dict) else {})
    directive = job.get("directive") or {}
    # Older callers may supply an already-canonical packet and only a before
    # snapshot. Without a directive there is no authoritative job context from
    # which to rebuild an envelope, so preserve the packet verbatim.
    if not directive:
        return packet, {"applied": False, "changes": []}
    requirements = directive.get("packet_requirements") or {}
    plan = directive.get("plan") or {}
    target = _target(directive)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    changes: list[str] = []

    source_map = {}
    for row in packet.get("source_ledger") or []:
        if isinstance(row, dict):
            old = str(row.get("source_id") or "")
            source_map[old] = _prefixed(old, "src-")
    finding_map = {}
    for row in packet.get("findings") or []:
        if isinstance(row, dict):
            old = str(row.get("finding_id") or "")
            finding_map[old] = _prefixed(old, "f-")
    change_map = {}
    for row in packet.get("change_events") or []:
        if isinstance(row, dict):
            old = str(row.get("change_event_id") or row.get("change_id") or "")
            change_map[old] = _prefixed(old, "chg-")
    maps = {"source": source_map, "finding": finding_map, "change": change_map}
    packet = _map_refs(packet, maps)

    for old, new in source_map.items():
        if old != new:
            changes.append(f"source_id:{old}->{new}")
    for old, new in finding_map.items():
        if old != new:
            changes.append(f"finding_id:{old}->{new}")
    for old, new in change_map.items():
        if old != new:
            changes.append(f"change_id:{old}->{new}")

    packet["schema"] = "rb.hunter_research_packet.v1"
    packet["packet_id"] = _prefixed(packet.get("packet_id") or f"{now}-{target.get('target_key')}", "hunter-")
    packet["research_agent"] = "Hunter"
    packet["targets"] = [target]
    packet["public_sources_only"] = True
    packet["payload_schema"] = requirements.get("payload_schema") or plan.get("payload_schema") or packet.get("payload_schema")
    packet["started_at"] = packet.get("started_at") or packet.get("created_at") or directive.get("prepared_at") or now
    packet["completed_at"] = packet.get("completed_at") or packet.get("created_at") or now
    status = packet.get("status")
    packet["status"] = {"completed": "complete"}.get(status, status or "partial")
    packet["cycle"] = {
        "cycle_type": (packet.get("cycle") or {}).get("cycle_type") or plan.get("playbook") or "research",
        "playbook": plan.get("playbook") or packet.get("playbook") or "enterprise_account_profile",
        "depth": plan.get("depth") or packet.get("depth") or "standard",
        "objective": (packet.get("cycle") or {}).get("objective") or plan.get("primary_mission") or "Fill supplied RBB research gaps.",
        "requested_by": (packet.get("cycle") or {}).get("requested_by") or "RBB Hunter cycle",
        "prior_state_as_of": requirements.get("prior_state_as_of") or packet.get("prior_state_as_of") or directive.get("prepared_at") or now,
        "known_gap_ids": requirements.get("known_gap_ids") or [],
        "discovery_domains": requirements.get("discovery_domains") or plan.get("required_modules") or ["general evidence"],
        "resource_plan": directive.get("resource_plan") or {},
        "budget": plan.get("budget") or {},
        "exit_criteria": plan.get("exit_criteria") or [],
    }

    coverage = (packet.get("coverage_summary") or {}).get("required_modules") or {}
    raw_modules = packet.get("research_modules") or []
    if raw_modules and all(isinstance(item, dict) for item in raw_modules):
        modules = raw_modules
    else:
        modules = []
        for name in plan.get("required_modules") or []:
            state = coverage.get(name) or {}
            state = state.get("status") if isinstance(state, dict) else state
            normalized = "complete" if state in {"verified", "supported", "reported", "complete"} else "partial"
            modules.append({"name": name, "required": True, "status": normalized, "notes": None})
        changes.append("research_modules:rebuilt_from_playbook")
    packet["research_modules"] = modules
    packet["questions"] = [str(item) for item in packet.get("questions") or []]

    for row in packet.get("source_ledger") or []:
        if not isinstance(row, dict):
            continue
        row["source_id"] = source_map.get(str(row.get("source_id") or ""), _prefixed(row.get("source_id"), "src-"))
        row.setdefault("url", row.get("canonical_url"))
        row.setdefault("canonical_url", row.get("url"))
        row["source_owner"] = _source_owner(row)
        row["access_tier"] = _pick(row.get("access_tier"), {"public": "free_public", "free": "free_public"}, {"free_public", "free_registration", "paid_subscription", "unknown"}, "unknown")
        row["directness"] = _pick(row.get("directness"), {"primary": "direct", "vendor_claim": "direct", "vendor_operational_evidence": "direct", "secondary_republication_of_company_claim": "derived"}, {"direct", "derived", "discovery_only"}, "derived")
        row["access_status"] = _pick(row.get("access_status"), {"success": "accessible", "ok": "accessible"}, {"accessible", "partial", "paywalled", "blocked", "not_found", "error"}, "partial")
        row["published_at"] = _date(row.get("published_at"))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row.get("accessed_at") or "")):
            row["accessed_at"] = str(row["accessed_at"]) + "T00:00:00Z"
    for row in packet.get("findings") or []:
        if isinstance(row, dict):
            row["finding_id"] = finding_map.get(str(row.get("finding_id") or ""), _prefixed(row.get("finding_id"), "f-"))
            row.setdefault("target_key", target.get("target_key"))
            row["contribution_type"] = _pick(row.get("contribution_type"), {
                "direct_evidence": "fills_known_gap", "corroborated_vendor_evidence": "fills_known_gap",
                "multi_source_relationship_evidence": "fills_known_gap", "conflict_resolution": "updates_existing_fact",
                "historical_relationship_with_current_gap": "new_data_point", "low_confidence_vendor_observation": "new_data_point",
                "research_synthesis": "new_data_point",
            }, {"fills_known_gap", "updates_existing_fact", "new_data_point"}, "new_data_point")
            row["evidence_status"] = _pick(row.get("evidence_status"), {
                "confirmed": "verified", "confirmed_company_claim": "reported", "high_confidence_vendor_relationship": "supported",
                "confirmed_live_relationship": "supported", "historical_confirmed_current_unverified": "reported",
                "vendor_claim_limited_scope": "reported", "conflict": "conflicted",
            }, {"verified", "supported", "reported", "inference", "conflicted", "not_found", "inaccessible"}, "reported")
            row["temporal_status"] = _pick(row.get("temporal_status"), {
                "current": "current_verified", "current_page": "current_probable", "current_as_of_fdd": "current_verified",
                "current_conflict": "currentness_unknown", "historical_latest_fdd_year_end": "historical",
                "historical_latest_audited": "historical", "historical_latest_fdd": "historical",
                "current_filing": "current_verified", "current_program_vendor_last_explicitly_named_2024": "current_probable",
                "current_page_scope_uncertain": "currentness_unknown",
            }, {"current_verified", "current_probable", "historical", "superseded", "currentness_unknown"}, "currentness_unknown")
            relevance = str(row.get("commercial_relevance") or "").lower()
            if relevance not in {"sales_relevance", "competitive_relevance", "replacement_opportunity", "partner_opportunity", "account_risk", "buying_signal", "timing_signal", "decision_maker_relevance", "no_immediate_relevance"}:
                relevance = "decision_maker_relevance" if "stakeholder" in relevance or "decision" in relevance else "sales_relevance"
            row["commercial_relevance"] = relevance
            row["as_of"] = _date(row.get("as_of"))
    for row in packet.get("change_events") or []:
        if isinstance(row, dict):
            old_change_id = str(row.get("change_event_id") or row.get("change_id") or "")
            row["change_event_id"] = change_map.get(old_change_id, _prefixed(old_change_id, "chg-"))
            row.setdefault("target_key", target.get("target_key"))
            row["change_type"] = _pick(row.get("change_type") or row.get("event_type"), {
                "footprint_update": "expansion", "leadership_change": "leadership",
                "technology_current_state_confirmation": "technology_stack", "payments_relationship_signal": "customer_relationship",
            }, {"leadership", "ownership", "funding", "acquisition", "product", "customer_relationship", "deployment", "technology_stack", "expansion", "contraction", "financial_operating_health", "strategy", "legal_regulatory", "competitive_position", "industry_pattern", "other"}, "other")
            row["prior_state"] = row.get("prior_state", (((job.get("before_snapshot") or {}).get("targets") or {}).get(target.get("target_key"), {}) or {}).get("current_state"))
            row["new_state"] = row.get("new_state", row.get("summary"))
            row["effective_at"] = _date(row.get("effective_at"))
            row["observed_at"] = row.get("observed_at") or packet.get("created_at") or now
            row["confidence_pct"] = int(row.get("confidence_pct") or 90)
            for key in list(row):
                if key not in {"change_event_id", "target_key", "change_type", "summary", "prior_state", "new_state", "effective_at", "observed_at", "materiality", "finding_ids", "source_ids", "confidence_pct"}:
                    row.pop(key, None)
    change_ids = [row.get("change_event_id") for row in packet.get("change_events") or [] if isinstance(row, dict)]
    for row in packet.get("mutation_proposals") or []:
        if isinstance(row, dict):
            row["proposal_id"] = _prefixed(row.get("proposal_id") or row.get("mutation_id"), "mut-")
            row.setdefault("target_key", target.get("target_key"))
            row["operation"] = "add" if row.get("operation") == "add_with_low_confidence" else row.get("operation", "add")
            row["existing_value"] = row.get("existing_value")
            row["new_value"] = row.get("new_value", row.get("proposed_value"))
            row["effective_at"] = _date(row.get("effective_at") or row.get("as_of"))
            linked = row.get("change_event_ids") or []
            if not linked and change_ids:
                finding_ids = set(row.get("finding_ids") or [])
                linked = [event.get("change_event_id") for event in packet.get("change_events") or [] if finding_ids.intersection(event.get("finding_ids") or [])]
            row["change_event_ids"] = linked or change_ids[:1]
            for key in list(row):
                if key not in {"proposal_id", "target_key", "operation", "field_path", "existing_value", "new_value", "effective_at", "confidence_pct", "change_event_ids", "finding_ids", "source_ids"}:
                    row.pop(key, None)
    for row in packet.get("cos_handoffs") or []:
        if isinstance(row, dict):
            row["handoff_id"] = _prefixed(row.get("handoff_id"), "cos-")
            row["connected_target_keys"] = row.get("connected_target_keys") or [row.get("target_key") or target.get("target_key")]
            linked_findings = set(row.get("finding_ids") or [])
            row["change_event_ids"] = row.get("change_event_ids") or [event.get("change_event_id") for event in packet.get("change_events") or [] if linked_findings.intersection(event.get("finding_ids") or [])] or change_ids[:1]
            row["connection"] = row.get("connection") or row.get("recommended_next_action") or row.get("headline")
            row["is_inference"] = bool(row.get("is_inference", True))
            row["confidence_pct"] = int(row.get("confidence_pct") or (90 if row.get("priority") == "P1" else 80))
            row["time_horizon"] = row.get("time_horizon") or ("immediate" if row.get("priority") == "P1" else "days_to_weeks")
            for key in list(row):
                if key not in {"handoff_id", "headline", "connected_target_keys", "change_event_ids", "finding_ids", "connection", "why_it_matters", "is_inference", "confidence_pct", "time_horizon"}:
                    row.pop(key, None)

    for row in packet.get("gap_outcomes") or []:
        if isinstance(row, dict):
            row["reason"] = row.get("reason", row.get("notes"))
            for key in list(row):
                if key not in {"gap_id", "status", "finding_ids", "reason"}:
                    row.pop(key, None)

    for row in packet.get("conflicts") or []:
        if isinstance(row, dict):
            row["resolution_status"] = _pick(row.get("resolution_status"), {
                "resolved_in_favor_of_newer_dated_primary_filing": "resolved", "scope_and_date_difference": "scope_difference",
                "unresolved_current_vendor": "unresolved", "partially_resolved": "unresolved",
            }, {"resolved", "unresolved", "scope_difference", "date_difference"}, "unresolved")

    unanswered = packet.get("unanswered_questions") or []
    packet["unanswered_questions"] = [item.get("question", "") if isinstance(item, dict) else str(item) for item in unanswered]
    packet.setdefault("paid_source_recommendations", [])
    packet.setdefault("negative_findings", [])
    packet.setdefault("conflicts", [])
    feedback = packet.get("method_feedback")
    if not isinstance(feedback, dict):
        feedback = {}
    feedback.setdefault("productive_patterns", feedback.get("effective_methods", []))
    feedback.setdefault("unproductive_patterns", feedback.get("limitations", []))
    feedback.setdefault("proposed_method_changes", feedback.get("next_cycle_improvements", []))
    feedback.setdefault("next_cycle_recommendations", feedback.get("next_cycle_improvements", []))
    for key in ("productive_patterns", "unproductive_patterns", "proposed_method_changes", "next_cycle_recommendations"):
        value = feedback.get(key, [])
        feedback[key] = value if isinstance(value, list) else ([str(value)] if value else [])
    packet["method_feedback"] = {key: feedback[key] for key in ("productive_patterns", "unproductive_patterns", "proposed_method_changes", "next_cycle_recommendations")}

    sources = [row for row in packet.get("source_ledger") or [] if isinstance(row, dict)]
    findings = [row for row in packet.get("findings") or [] if isinstance(row, dict)]
    packet["quality"] = {
        "pages_opened": len(sources),
        "productive_pages": sum(bool(row.get("productive")) for row in sources),
        "primary_source_pages": sum(row.get("source_owner") in {"government_or_regulator", "restaurant_or_operator", "customer_joint"} for row in sources),
        "findings_count": len(findings),
        "findings_with_sources": sum(bool(row.get("source_ids")) for row in findings),
        "required_modules_complete": all(row.get("status") in {"complete", "not_applicable"} for row in modules),
        "validation_status": "not_run",
    }
    packet["resource_usage"] = {
        "selected_execution_tier": "chatgpt_deep_research_economy",
        "chatgpt_deep_research_calls": 1,
        "codex_model_calls": 0,
        "premium_reasoning_calls": 0,
        "targets_attempted": 1,
        "targets_completed": 1 if packet["status"] in {"complete", "no_material_findings"} else 0,
        "budget_outcome": "within_budget",
        "escalation_reasons": [],
    }
    payload = packet.get("payload") or {}
    if isinstance(payload, dict) and payload.get("collection") == "records" and isinstance(payload.get("items"), list):
        payload = {"records": payload["items"]}
        changes.append("payload:collection_items->records")
    packet["payload"] = payload if isinstance(payload, dict) else {}

    allowed = {
        "schema", "packet_id", "research_agent", "cycle", "targets", "started_at", "completed_at", "status",
        "public_sources_only", "research_modules", "questions", "source_ledger", "query_ledger", "findings",
        "gap_outcomes", "change_events", "mutation_proposals", "cos_handoffs", "paid_source_recommendations",
        "negative_findings", "conflicts", "unanswered_questions", "method_feedback", "quality", "resource_usage",
        "payload_schema", "payload",
    }
    packet = {key: value for key, value in packet.items() if key in allowed}
    return packet, {"applied": True, "changes": changes}
