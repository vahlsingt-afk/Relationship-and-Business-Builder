"""
technology_lifecycle.py — governed read/write module for the Technology
Lifecycle & Change Propensity intelligence layer (system/technology_lifecycle/).

Phase 1 (2026-10-02): the governed module Phase 0 scaffolding named as
still missing (system/technology_lifecycle/README.md, "What exists vs.
what's Phase 1"). Full field-level schema: system/SCHEMAS.md, "Technology
Lifecycle & Change Events". This module is the ONLY place that appends to
the 5 jsonl stores -- nothing else should open them for writing.

FDD Technology Governance & Economics (2026-10-02, same day): adds the
source-record, economics, governance-change-event, penetration-
reconciliation, research-gap, and entity-resolution-review stores the
brief (system/technology_lifecycle/FDD_GOVERNANCE_ECONOMICS_BRIEF.md)
asks for. Governance facts themselves still go through the existing
record_governance() above -- its fdd_sourced_fields dict already covers
every brief §3/§4 field (contractual_authority, current_requirement,
named_vendor_id, grandfathering_status, etc.); it was deliberately built
generic enough in Technology Lifecycle Phase 1 that this program needed
no signature change there.

Append-only / supersedes, same discipline as the rest of this layer and
system/ARCHITECTURE.md's "event stream over snapshots" principle: a line
is never edited or deleted in place; a correction is a new line carrying
`supersedes` with the id it corrects. Every *_entity_id must already
resolve in ecosystem_intelligence.json -- this module never creates a new
entity id scheme; an unresolvable id is a real gap to surface (via
ValueError), not something to invent around.

This is a public (Tier 1, visibility_class default public_shared)
intelligence layer, deliberately separate from any account's private
Tier 2 tree -- see system/DATA_TIER_ARCHITECTURE.md.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import ecosystem_intelligence as ei  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent / "technology_lifecycle"  # .../system/technology_lifecycle

RELATIONSHIP_EVENTS_PATH = ROOT / "technology_relationship_events.jsonl"
GOVERNANCE_PATH = ROOT / "technology_governance.jsonl"
PENETRATION_PATH = ROOT / "technology_penetration.jsonl"
CHANGE_EVENTS_PATH = ROOT / "technology_change_events.jsonl"
FORCING_SIGNALS_PATH = ROOT / "technology_forcing_signals.jsonl"
FDD_SOURCES_PATH = ROOT / "fdd_sources.jsonl"
TECHNOLOGY_ECONOMICS_PATH = ROOT / "technology_economics.jsonl"
GOVERNANCE_CHANGE_EVENTS_PATH = ROOT / "technology_governance_change_events.jsonl"
PENETRATION_RECONCILIATION_PATH = ROOT / "technology_penetration_reconciliation.jsonl"
FDD_RESEARCH_GAPS_PATH = ROOT / "fdd_research_gaps.jsonl"
ENTITY_RESOLUTION_REVIEW_PATH = ROOT / "entity_resolution_review.jsonl"

# -- Shared vocabulary (system/SCHEMAS.md, "Technology Lifecycle & Change Events") --

TECHNOLOGY_CATEGORIES = {
    "pos", "pos_hardware", "payments", "back_office", "inventory", "labor_workforce",
    "kds_kitchen_ops", "loyalty", "crm_cdp", "mobile_apps", "online_ordering",
    "delivery_marketplace_orchestration", "digital_menu_boards", "drive_thru_ai", "kiosks",
    "voice_ai", "computer_vision", "automated_inventory", "restaurant_ai_platform",
    "ai_agents", "predictive_operations",
}

ENTITY_LEVELS = {"parent", "brand", "operator", "location"}

EVIDENCE_TYPES = {"vendor_claim", "operator_statement", "independent_evidence", "rbb_inference", "unknown"}

VISIBILITY_CLASSES = {"public_shared", "private_user", "private_organization", "restricted", "unknown"}

CONFIDENCE_LEVELS = {"high", "medium", "low"}

LIFECYCLE_STATES = {
    "discovery", "evaluation", "rfi", "rfp", "pilot", "selected", "contracted",
    "rollout_planned", "rollout_active", "rollout_paused", "rollout_restarted",
    "rollout_scaled", "deployed", "operationalized", "expanded", "renewed", "displaced",
    "retired", "pilot_abandoned", "pilot_not_scaled", "technology_reversal",
    "vendor_platform_discontinued", "strategic_divestiture_with_continued_use",
    "historical_deployment_current_state_uncertain",
}

SCOPE_TYPES = {
    "announced_scope", "contracted_scope", "mandated_scope", "committed_scope",
    "pilot_scope", "installed_scope", "live_scope", "verified_scope",
}

GOVERNANCE_STATES = {
    "mandated", "mandated_category_brand_selected_vendor", "approved_vendor_list",
    "preferred_not_required", "franchisee_choice_with_requirements", "grandfathered",
    "new_store_mandate", "pilot_optional", "corporate_only", "unknown",
}

PENETRATION_TYPES = {
    "location_penetration", "operator_penetration", "system_sales_penetration",
    "transaction_penetration", "module_penetration",
}

FORCING_EVENT_TYPES = {
    "os_eol", "hardware_eol", "pos_software_eol", "vendor_support_sunset", "compliance",
    "peripheral_incompatibility", "franchise_mandate", "leadership_change", "other",
}

# -- FDD Technology Governance & Economics (system/technology_lifecycle/
# FDD_GOVERNANCE_ECONOMICS_BRIEF.md, §2 "FDD Source Record") --
DOCUMENT_STATUSES = {
    "current", "historical", "amended", "superseded", "incomplete", "secondary_copy", "not_located",
}

# §8 "Historical FDD Comparison"
GOVERNANCE_CHANGE_TYPES = {
    "vendor_removed_from_approved_list", "vendor_added_to_approved_list", "optional_to_mandated",
    "approved_to_exclusive", "new_store_mandate_created", "grandfathering_created", "grandfathering_ended",
    "conversion_deadline_created", "technology_fee_increased", "technology_fee_decreased",
    "franchisor_authority_changed",
}

# §9 "Penetration Reconciliation"
RECONCILIATION_STATUSES = {
    "standard_vendor", "known_competing_installed_base", "grandfathering_possible",
    "migration_in_progress", "penetration_unknown", "conversion_deadline_unknown",
}

# §17 "Research Gaps"
FDD_RESEARCH_GAP_TYPES = {
    "current_vendor_unknown", "governance_unknown", "approved_vendor_list_incomplete",
    "penetration_unknown", "grandfathering_unknown", "conversion_deadline_unknown",
    "payment_flexibility_unknown", "current_FDD_not_located", "historical_FDD_missing",
    "conflicting_vendor_evidence",
}

# §1 "Canonical Entity Resolution" -- review queue statuses
ENTITY_RESOLUTION_REVIEW_STATUSES = {"pending", "resolved", "rejected"}

DEFAULT_VISIBILITY_CLASS = "public_shared"


class TechnologyLifecycleError(ValueError):
    """Raised on a structurally invalid record -- bad vocab, missing
    required field, or an *_entity_id that doesn't resolve in
    ecosystem_intelligence.json. Never silently coerced or dropped."""


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.date.today().isoformat()


def _load_graph() -> dict:
    return ei._read_graph()


def _entity_exists(entity_id: str | None, graph: dict) -> bool:
    if entity_id is None:
        return True  # optional fields (parent_entity_id, operator_entity_id) may be legitimately null
    return any(e.get("id") == entity_id for e in graph.get("entities") or [])


def assert_valid_relationship_key(relationship_key: dict, graph: dict | None = None) -> None:
    graph = graph if graph is not None else _load_graph()
    required = ("brand_entity_id", "technology_category", "vendor_entity_id")
    for key in required:
        if not relationship_key.get(key):
            raise TechnologyLifecycleError(f"relationship_key.{key} is required")
    if relationship_key["technology_category"] not in TECHNOLOGY_CATEGORIES:
        raise TechnologyLifecycleError(
            f"relationship_key.technology_category must be one of {sorted(TECHNOLOGY_CATEGORIES)}, "
            f"got {relationship_key['technology_category']!r}"
        )
    for key in ("brand_entity_id", "vendor_entity_id", "parent_entity_id", "operator_entity_id"):
        entity_id = relationship_key.get(key)
        if entity_id and not _entity_exists(entity_id, graph):
            raise TechnologyLifecycleError(
                f"relationship_key.{key}={entity_id!r} does not resolve to an existing entity in "
                "ecosystem_intelligence.json -- this module never invents a new entity id scheme; "
                "resolve or create the entity there first."
            )


def _assert_in(value, allowed: set, field_name: str) -> None:
    if value not in allowed:
        raise TechnologyLifecycleError(f"{field_name} must be one of {sorted(allowed)}, got {value!r}")


def _append(path: Path, record: dict) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False))
        f.write("\n")
    return record


def load_records(path: Path) -> list[dict]:
    """Every data line after the _schema_header, in file order."""
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("_schema_header"):
                continue
            out.append(rec)
    return out


def _non_superseded(records: list[dict]) -> list[dict]:
    """Drop any record whose id is named by a later record's `supersedes`
    -- the correcting line replaces it for "current state" purposes, while
    both stay on disk forever for audit (append-only)."""
    superseded_ids = {r.get("supersedes") for r in records if r.get("supersedes")}
    id_keys = (
        "event_id", "governance_id", "observation_id", "signal_id", "fdd_id",
        "change_event_id", "reconciliation_id", "fdd_gap_id",
    )
    def _own_id(r: dict) -> str | None:
        for k in id_keys:
            if k in r:
                return r[k]
        return None
    return [r for r in records if _own_id(r) not in superseded_ids]


def _matches_relationship_key(record_key: dict, filter_key: dict) -> bool:
    return all(record_key.get(k) == v for k, v in filter_key.items() if v is not None)


# ---------------------------------------------------------------------------
# Writers -- one per store, each validating required fields + vocabulary
# before appending. Optional nested blocks (push_factors, phasing, etc. on
# technology_change_events) are passed through via **extra, never
# individually re-validated here -- this module enforces the structural
# invariants (ids resolve, enums are real, append-only/supersedes), not
# the full depth of every optional research field.
# ---------------------------------------------------------------------------

def record_relationship_event(
    *, event_id: str, relationship_key: dict, entity_level: str, lifecycle_state: str,
    state_date_or_range: str, evidence: str, source_url: str | None, source_type: str,
    confidence: str, evidence_type: str, scope_observation: dict | None = None,
    observed_at: str | None = None, last_verified_current_date: str | None = None,
    visibility_class: str = DEFAULT_VISIBILITY_CLASS, supersedes: str | None = None,
) -> dict:
    graph = _load_graph()
    assert_valid_relationship_key(relationship_key, graph)
    _assert_in(entity_level, ENTITY_LEVELS, "entity_level")
    _assert_in(lifecycle_state, LIFECYCLE_STATES, "lifecycle_state")
    _assert_in(confidence, CONFIDENCE_LEVELS, "confidence")
    _assert_in(evidence_type, EVIDENCE_TYPES, "evidence_type")
    _assert_in(visibility_class, VISIBILITY_CLASSES, "visibility_class")
    if scope_observation is not None:
        _assert_in(scope_observation.get("scope_type"), SCOPE_TYPES, "scope_observation.scope_type")
    observed_at = observed_at or today()
    record = {
        "event_id": event_id, "relationship_key": relationship_key, "entity_level": entity_level,
        "lifecycle_state": lifecycle_state, "state_date_or_range": state_date_or_range,
        "scope_observation": scope_observation, "evidence": evidence, "source_url": source_url,
        "source_type": source_type, "confidence": confidence, "evidence_type": evidence_type,
        "observed_at": observed_at, "last_verified_current_date": last_verified_current_date or observed_at,
        "visibility_class": visibility_class, "supersedes": supersedes,
    }
    return _append(RELATIONSHIP_EVENTS_PATH, record)


def record_governance(
    *, governance_id: str, brand_entity_id: str, technology_category: str, governance_state: str,
    evidence: str, source_url: str | None, confidence: str, evidence_type: str,
    approved_vendors: list[str] | None = None, franchisor_change_authority: dict | None = None,
    fdd_sourced_fields: dict | None = None, observed_at: str | None = None,
    last_verified_current_date: str | None = None, visibility_class: str = DEFAULT_VISIBILITY_CLASS,
    supersedes: str | None = None,
) -> dict:
    graph = _load_graph()
    if not _entity_exists(brand_entity_id, graph):
        raise TechnologyLifecycleError(f"brand_entity_id={brand_entity_id!r} does not resolve in ecosystem_intelligence.json")
    _assert_in(technology_category, TECHNOLOGY_CATEGORIES, "technology_category")
    _assert_in(governance_state, GOVERNANCE_STATES, "governance_state")
    _assert_in(confidence, CONFIDENCE_LEVELS, "confidence")
    _assert_in(evidence_type, EVIDENCE_TYPES, "evidence_type")
    _assert_in(visibility_class, VISIBILITY_CLASSES, "visibility_class")
    for vendor_id in approved_vendors or []:
        if not _entity_exists(vendor_id, graph):
            raise TechnologyLifecycleError(f"approved_vendors entry {vendor_id!r} does not resolve in ecosystem_intelligence.json")
    observed_at = observed_at or today()
    record = {
        "governance_id": governance_id, "brand_entity_id": brand_entity_id,
        "technology_category": technology_category, "governance_state": governance_state,
        "approved_vendors": approved_vendors or [], "franchisor_change_authority": franchisor_change_authority,
        "fdd_sourced_fields": fdd_sourced_fields, "evidence": evidence, "source_url": source_url,
        "confidence": confidence, "evidence_type": evidence_type, "observed_at": observed_at,
        "last_verified_current_date": last_verified_current_date or observed_at,
        "visibility_class": visibility_class, "supersedes": supersedes,
    }
    return _append(GOVERNANCE_PATH, record)


def record_penetration(
    *, observation_id: str, relationship_key: dict, entity_level: str, penetration_type: str,
    observation_date: str, confidence: str, evidence_type: str, source: str | None = None,
    last_verified_date: str | None = None, visibility_class: str = DEFAULT_VISIBILITY_CLASS,
    supersedes: str | None = None, **counts,
) -> dict:
    """`counts` carries whatever of total_system_locations/eligible_locations/
    contracted_locations/mandated_locations/committed_locations/
    pilot_locations/installed_locations/live_locations/verified_locations/
    estimated_locations_low/estimated_locations_high/penetration_pct_low/
    penetration_pct_high/remaining_opportunity/rollout_velocity the evidence
    actually supports -- never required, never fabricated when absent."""
    graph = _load_graph()
    assert_valid_relationship_key(relationship_key, graph)
    _assert_in(entity_level, ENTITY_LEVELS, "entity_level")
    _assert_in(penetration_type, PENETRATION_TYPES, "penetration_type")
    _assert_in(confidence, CONFIDENCE_LEVELS, "confidence")
    _assert_in(evidence_type, EVIDENCE_TYPES, "evidence_type")
    _assert_in(visibility_class, VISIBILITY_CLASSES, "visibility_class")
    record = {
        "observation_id": observation_id, "relationship_key": relationship_key, "entity_level": entity_level,
        "penetration_type": penetration_type,
        "total_system_locations": None, "eligible_locations": None, "contracted_locations": None,
        "mandated_locations": None, "committed_locations": None, "pilot_locations": None,
        "installed_locations": None, "live_locations": None, "verified_locations": None,
        "estimated_locations_low": None, "estimated_locations_high": None,
        "penetration_pct_low": None, "penetration_pct_high": None, "remaining_opportunity": None,
        "rollout_velocity": None,
        **counts,
        "observation_date": observation_date, "last_verified_date": last_verified_date or observation_date,
        "confidence": confidence, "evidence_type": evidence_type, "source": source,
        "visibility_class": visibility_class, "supersedes": supersedes,
    }
    return _append(PENETRATION_PATH, record)


def record_change_event(
    *, event_id: str, brand_entity_id: str, technology_category: str,
    overall_confidence: str, visibility_class: str = DEFAULT_VISIBILITY_CLASS,
    supersedes: str | None = None, last_verified_current_date: str | None = None,
    recorded_at: str | None = None, **extra,
) -> dict:
    """`extra` carries whatever of the rich optional shape (previous_technology,
    replacement_technology, push_factors, pull_factors, pre_change_signals,
    pos_hardware_lifecycle, change_scope, existing_stack_retained, phasing,
    platform_relationship, follow_on_adoption, brand_maturity,
    migration_burden, change_economics, need_vs_willingness_vs_ability,
    migration_capacity, prior_implementation_scar, adoption_dimensions,
    pilot_outcome, opportunity_classification, outcome_research,
    is_non_switch_case, non_switch_note, relationship_key_incumbent,
    relationship_key_replacement, unit_count_at_decision, scope) the
    research actually supports -- see system/SCHEMAS.md for the full shape.
    Passed through as-is; this function validates only the structural
    invariants every change event must have."""
    graph = _load_graph()
    if not _entity_exists(brand_entity_id, graph):
        raise TechnologyLifecycleError(f"brand_entity_id={brand_entity_id!r} does not resolve in ecosystem_intelligence.json")
    _assert_in(technology_category, TECHNOLOGY_CATEGORIES, "technology_category")
    _assert_in(overall_confidence, CONFIDENCE_LEVELS, "overall_confidence")
    _assert_in(visibility_class, VISIBILITY_CLASSES, "visibility_class")
    recorded_at = recorded_at or today()
    record = {
        "event_id": event_id, "brand_entity_id": brand_entity_id, "technology_category": technology_category,
        **extra,
        "overall_confidence": overall_confidence,
        "last_verified_current_date": last_verified_current_date or recorded_at,
        "visibility_class": visibility_class, "supersedes": supersedes, "recorded_at": recorded_at,
    }
    return _append(CHANGE_EVENTS_PATH, record)


def record_forcing_signal(
    *, signal_id: str, brand_entity_id: str, entity_level: str, technology_category: str,
    forcing_event_type: str, detail: str, evidence: str, source_url: str | None, confidence: str,
    evidence_type: str, operator_entity_id: str | None = None, observed_at: str | None = None,
    last_verified_current_date: str | None = None, visibility_class: str = DEFAULT_VISIBILITY_CLASS,
    supersedes: str | None = None,
) -> dict:
    graph = _load_graph()
    if not _entity_exists(brand_entity_id, graph):
        raise TechnologyLifecycleError(f"brand_entity_id={brand_entity_id!r} does not resolve in ecosystem_intelligence.json")
    if operator_entity_id and not _entity_exists(operator_entity_id, graph):
        raise TechnologyLifecycleError(f"operator_entity_id={operator_entity_id!r} does not resolve in ecosystem_intelligence.json")
    _assert_in(entity_level, ENTITY_LEVELS, "entity_level")
    _assert_in(technology_category, TECHNOLOGY_CATEGORIES, "technology_category")
    _assert_in(forcing_event_type, FORCING_EVENT_TYPES, "forcing_event_type")
    _assert_in(confidence, CONFIDENCE_LEVELS, "confidence")
    _assert_in(evidence_type, EVIDENCE_TYPES, "evidence_type")
    _assert_in(visibility_class, VISIBILITY_CLASSES, "visibility_class")
    observed_at = observed_at or today()
    record = {
        "signal_id": signal_id, "brand_entity_id": brand_entity_id, "operator_entity_id": operator_entity_id,
        "entity_level": entity_level, "technology_category": technology_category,
        "forcing_event_type": forcing_event_type, "detail": detail, "evidence": evidence,
        "source_url": source_url, "confidence": confidence, "evidence_type": evidence_type,
        "observed_at": observed_at, "last_verified_current_date": last_verified_current_date or observed_at,
        "visibility_class": visibility_class, "supersedes": supersedes,
    }
    return _append(FORCING_SIGNALS_PATH, record)


def record_fdd_source(
    *, fdd_id: str, brand_id: str, fdd_year: str, document_status: str, evidence_type: str,
    confidence: str, franchisor_entity_id: str | None = None, effective_date: str | None = None,
    amendment_date: str | None = None, source_url: str | None = None, source_title: str | None = None,
    accessed_date: str | None = None, prior_fdd_id: str | None = None, superseded_by_fdd_id: str | None = None,
    notes: str | None = None, visibility_class: str = DEFAULT_VISIBILITY_CLASS, supersedes: str | None = None,
) -> dict:
    """One row per FDD document reviewed (brief §2) -- NOT per governance
    fact; record_governance() below is what a reviewed FDD's actual
    findings get written as, each citing this fdd_id as source_id.
    document_status "not_located"/"superseded" are themselves real,
    honest findings (a document search came up empty, or a newer filing
    replaced this one) -- never omit a brand's FDD row just because
    nothing was found; that's the difference between "no FDD exists" and
    "no FDD was looked for yet" that export_fdd_target_population.py's
    coverage computation depends on."""
    graph = _load_graph()
    if not _entity_exists(brand_id, graph):
        raise TechnologyLifecycleError(f"brand_id={brand_id!r} does not resolve in ecosystem_intelligence.json")
    if franchisor_entity_id and not _entity_exists(franchisor_entity_id, graph):
        raise TechnologyLifecycleError(f"franchisor_entity_id={franchisor_entity_id!r} does not resolve in ecosystem_intelligence.json")
    _assert_in(document_status, DOCUMENT_STATUSES, "document_status")
    _assert_in(confidence, CONFIDENCE_LEVELS, "confidence")
    _assert_in(evidence_type, EVIDENCE_TYPES, "evidence_type")
    _assert_in(visibility_class, VISIBILITY_CLASSES, "visibility_class")
    record = {
        "fdd_id": fdd_id, "brand_id": brand_id, "franchisor_entity_id": franchisor_entity_id,
        "fdd_year": fdd_year, "effective_date": effective_date, "amendment_date": amendment_date,
        "source_url": source_url, "source_title": source_title, "accessed_date": accessed_date or today(),
        "document_status": document_status, "document_confidence": confidence, "confidence": confidence,
        "evidence_type": evidence_type, "prior_fdd_id": prior_fdd_id, "superseded_by_fdd_id": superseded_by_fdd_id,
        "notes": notes, "visibility_class": visibility_class, "supersedes": supersedes,
    }
    return _append(FDD_SOURCES_PATH, record)


def record_economics_observation(
    *, observation_id: str, brand_id: str, technology_category: str, evidence_type: str, confidence: str,
    fdd_id: str | None = None, source_url: str | None = None, franchisee_pays: bool | None = None,
    franchisor_subsidy: bool | None = None, vendor_subsidy: bool | None = None,
    supplier_rebate_or_commission: bool | None = None, no_cap_disclosed: bool | None = None,
    payment_frequency: str | None = None, cost_unit: str | None = None, notes: str | None = None,
    observed_at: str | None = None, last_verified_date: str | None = None,
    visibility_class: str = DEFAULT_VISIBILITY_CLASS, supersedes: str | None = None, **cost_fields,
) -> dict:
    """Brief §6 "Technology Economics". `cost_fields` carries whatever of
    initial_technology_investment/hardware_cost/software_fee/
    recurring_technology_fee/digital_fee/loyalty_fee/online_ordering_fee/
    payment_related_fee/support_fee/maintenance_fee/upgrade_cost/
    replacement_cost/future_spending_cap/cost_range_low/cost_range_high the
    FDD actually discloses -- never required, never fabricated when
    absent. Preserve disclosed ranges (cost_range_low/high) rather than
    inventing a point estimate, per the brief's own instruction."""
    graph = _load_graph()
    if not _entity_exists(brand_id, graph):
        raise TechnologyLifecycleError(f"brand_id={brand_id!r} does not resolve in ecosystem_intelligence.json")
    _assert_in(technology_category, TECHNOLOGY_CATEGORIES, "technology_category")
    _assert_in(confidence, CONFIDENCE_LEVELS, "confidence")
    _assert_in(evidence_type, EVIDENCE_TYPES, "evidence_type")
    _assert_in(visibility_class, VISIBILITY_CLASSES, "visibility_class")
    observed_at = observed_at or today()
    record = {
        "observation_id": observation_id, "brand_id": brand_id, "technology_category": technology_category,
        "fdd_id": fdd_id, "source_url": source_url,
        "initial_technology_investment": None, "hardware_cost": None, "software_fee": None,
        "recurring_technology_fee": None, "digital_fee": None, "loyalty_fee": None, "online_ordering_fee": None,
        "payment_related_fee": None, "support_fee": None, "maintenance_fee": None, "upgrade_cost": None,
        "replacement_cost": None, "future_spending_cap": None, "cost_range_low": None, "cost_range_high": None,
        **cost_fields,
        "franchisee_pays": franchisee_pays, "franchisor_subsidy": franchisor_subsidy,
        "vendor_subsidy": vendor_subsidy, "supplier_rebate_or_commission": supplier_rebate_or_commission,
        "no_cap_disclosed": no_cap_disclosed, "payment_frequency": payment_frequency, "cost_unit": cost_unit,
        "notes": notes, "confidence": confidence, "evidence_type": evidence_type, "observed_at": observed_at,
        "last_verified_date": last_verified_date or observed_at, "visibility_class": visibility_class,
        "supersedes": supersedes,
    }
    return _append(TECHNOLOGY_ECONOMICS_PATH, record)


def record_governance_change_event(
    *, change_event_id: str, brand_id: str, technology_category: str, change_type: str, evidence: str,
    evidence_type: str, confidence: str, effective_date: str | None = None, from_value: object = None,
    to_value: object = None, prior_fdd_id: str | None = None, new_fdd_id: str | None = None,
    source_url: str | None = None, observed_at: str | None = None,
    visibility_class: str = DEFAULT_VISIBILITY_CLASS, supersedes: str | None = None,
) -> dict:
    """Brief §8 "Historical FDD Comparison" -- a detected change between two
    FDD years' governance, kept as its own event type rather than
    overloading technology_relationship_events.jsonl's vendor-relationship
    lifecycle_state enum (these are brand x category governance-authority
    changes, e.g. a fee increase or a mandate taking effect, not
    necessarily tied to one specific vendor relationship)."""
    graph = _load_graph()
    if not _entity_exists(brand_id, graph):
        raise TechnologyLifecycleError(f"brand_id={brand_id!r} does not resolve in ecosystem_intelligence.json")
    _assert_in(technology_category, TECHNOLOGY_CATEGORIES, "technology_category")
    _assert_in(change_type, GOVERNANCE_CHANGE_TYPES, "change_type")
    _assert_in(confidence, CONFIDENCE_LEVELS, "confidence")
    _assert_in(evidence_type, EVIDENCE_TYPES, "evidence_type")
    _assert_in(visibility_class, VISIBILITY_CLASSES, "visibility_class")
    observed_at = observed_at or today()
    record = {
        "change_event_id": change_event_id, "brand_id": brand_id, "technology_category": technology_category,
        "change_type": change_type, "effective_date": effective_date, "from_value": from_value,
        "to_value": to_value, "prior_fdd_id": prior_fdd_id, "new_fdd_id": new_fdd_id, "evidence": evidence,
        "source_url": source_url, "confidence": confidence, "evidence_type": evidence_type,
        "observed_at": observed_at, "visibility_class": visibility_class, "supersedes": supersedes,
    }
    return _append(GOVERNANCE_CHANGE_EVENTS_PATH, record)


def record_penetration_reconciliation(
    *, reconciliation_id: str, relationship_key: dict, reconciliation_status: str, evidence: str,
    evidence_type: str, confidence: str, conversion_deadline: str | None = None,
    source_url: str | None = None, observed_at: str | None = None,
    visibility_class: str = DEFAULT_VISIBILITY_CLASS, supersedes: str | None = None,
) -> dict:
    """Brief §9 "Penetration Reconciliation" -- explicitly distinct from a
    raw technology_penetration.jsonl observation: this records the
    RELATIONSHIP between an FDD governance mandate and what's actually
    known to be installed (e.g. "FDD names Vendor A as the required future
    standard; a competing product is independently known to remain
    installed at some locations"). Never overwrite a penetration
    observation because a governance mandate exists -- both coexist."""
    graph = _load_graph()
    assert_valid_relationship_key(relationship_key, graph)
    _assert_in(reconciliation_status, RECONCILIATION_STATUSES, "reconciliation_status")
    _assert_in(confidence, CONFIDENCE_LEVELS, "confidence")
    _assert_in(evidence_type, EVIDENCE_TYPES, "evidence_type")
    _assert_in(visibility_class, VISIBILITY_CLASSES, "visibility_class")
    observed_at = observed_at or today()
    record = {
        "reconciliation_id": reconciliation_id, "relationship_key": relationship_key,
        "reconciliation_status": reconciliation_status, "conversion_deadline": conversion_deadline,
        "evidence": evidence, "source_url": source_url, "confidence": confidence, "evidence_type": evidence_type,
        "observed_at": observed_at, "visibility_class": visibility_class, "supersedes": supersedes,
    }
    return _append(PENETRATION_RECONCILIATION_PATH, record)


def record_fdd_research_gap(
    *, fdd_gap_id: str, brand_id: str, gap_type: str, detail: str, status: str = "open",
    technology_category: str | None = None, observed_at: str | None = None,
    visibility_class: str = DEFAULT_VISIBILITY_CLASS, supersedes: str | None = None,
) -> dict:
    """Brief §17 "Research Gaps" -- missing information generates a
    structured gap rather than disappearing. Closing a gap is a NEW record
    (same fdd_gap_id lineage, a fresh id of its own) with status:"resolved"
    and `supersedes` naming the gap it resolves -- never an in-place edit,
    same append-only discipline as every other store here.
    list_fdd_research_gaps() returns only non-superseded, status:"open"
    rows, so a resolution record both hides its predecessor and excludes
    itself."""
    graph = _load_graph()
    if not _entity_exists(brand_id, graph):
        raise TechnologyLifecycleError(f"brand_id={brand_id!r} does not resolve in ecosystem_intelligence.json")
    _assert_in(gap_type, FDD_RESEARCH_GAP_TYPES, "gap_type")
    _assert_in(status, {"open", "resolved"}, "status")
    _assert_in(visibility_class, VISIBILITY_CLASSES, "visibility_class")
    if technology_category is not None:
        _assert_in(technology_category, TECHNOLOGY_CATEGORIES, "technology_category")
    observed_at = observed_at or today()
    record = {
        "fdd_gap_id": fdd_gap_id, "brand_id": brand_id, "gap_type": gap_type, "status": status,
        "technology_category": technology_category, "detail": detail, "observed_at": observed_at,
        "visibility_class": visibility_class, "supersedes": supersedes,
    }
    return _append(FDD_RESEARCH_GAPS_PATH, record)


# ---------------------------------------------------------------------------
# Entity Resolution Review Queue (brief §1) -- a mutable workflow queue,
# deliberately NOT append-only/supersedes like the ledgers above: a review
# item's whole point is to change status in place (pending -> resolved/
# rejected) once a human confirms the right entity, the same shape as
# blue_sheets/_portfolio/review_queue.json elsewhere in this codebase.
# Stored as one JSON object keyed by review_id, not a .jsonl log.
# ---------------------------------------------------------------------------

def _load_entity_resolution_review() -> dict:
    if not ENTITY_RESOLUTION_REVIEW_PATH.exists():
        return {"items": {}}
    data = json.loads(ENTITY_RESOLUTION_REVIEW_PATH.read_text(encoding="utf-8"))
    data.setdefault("items", {})
    return data


def _save_entity_resolution_review(data: dict) -> None:
    ENTITY_RESOLUTION_REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENTITY_RESOLUTION_REVIEW_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def queue_entity_resolution_review(
    *, review_id: str, raw_name_or_identifier: str, context: str, candidate_entity_ids: list[str] | None = None,
) -> dict:
    """An incoming finding named an entity (brand/vendor/franchisor/etc.)
    that could not be confidently resolved against ecosystem_
    intelligence.json -- queued for human review rather than silently
    creating a duplicate entity (brief §1's explicit instruction)."""
    data = _load_entity_resolution_review()
    item = {
        "review_id": review_id, "raw_name_or_identifier": raw_name_or_identifier, "context": context,
        "candidate_entity_ids": candidate_entity_ids or [], "status": "pending",
        "queued_at": now_iso(), "resolved_entity_id": None, "resolved_at": None,
    }
    data["items"][review_id] = item
    _save_entity_resolution_review(data)
    return item


def list_entity_resolution_review(status: str | None = None) -> list[dict]:
    if status is not None:
        _assert_in(status, ENTITY_RESOLUTION_REVIEW_STATUSES, "status")
    items = list(_load_entity_resolution_review()["items"].values())
    if status:
        items = [i for i in items if i.get("status") == status]
    return items


def resolve_entity_resolution_review(review_id: str, *, resolved_entity_id: str) -> dict:
    """Confirms the real entity id and marks the item resolved -- never
    creates the entity itself; resolved_entity_id must already exist in
    ecosystem_intelligence.json, same invariant every writer above
    enforces."""
    graph = _load_graph()
    if not _entity_exists(resolved_entity_id, graph):
        raise TechnologyLifecycleError(f"resolved_entity_id={resolved_entity_id!r} does not resolve in ecosystem_intelligence.json")
    data = _load_entity_resolution_review()
    item = data["items"].get(review_id)
    if item is None:
        raise TechnologyLifecycleError(f"no entity_resolution_review item with review_id={review_id!r}")
    item["status"] = "resolved"
    item["resolved_entity_id"] = resolved_entity_id
    item["resolved_at"] = now_iso()
    _save_entity_resolution_review(data)
    return item


def reject_entity_resolution_review(review_id: str, *, reason: str) -> dict:
    data = _load_entity_resolution_review()
    item = data["items"].get(review_id)
    if item is None:
        raise TechnologyLifecycleError(f"no entity_resolution_review item with review_id={review_id!r}")
    item["status"] = "rejected"
    item["rejection_reason"] = reason
    item["resolved_at"] = now_iso()
    _save_entity_resolution_review(data)
    return item


# ---------------------------------------------------------------------------
# Readers / derived views
# ---------------------------------------------------------------------------

def current_state_for_relationship(relationship_key: dict) -> dict | None:
    """The most recent non-superseded technology_relationship_events line
    for this exact relationship_key, or None if nothing's recorded. "Most
    recent" is observed_at order, never assumed from the oldest
    announcement (system/SCHEMAS.md, Lifecycle states)."""
    all_events = _non_superseded(load_records(RELATIONSHIP_EVENTS_PATH))
    matching = [e for e in all_events if e.get("relationship_key") == relationship_key]
    if not matching:
        return None
    return max(matching, key=lambda e: e.get("observed_at") or "")


def list_relationships_for_brand(brand_entity_id: str) -> list[dict]:
    """Every distinct relationship_key with at least one event for this
    brand, each with its own current_state_for_relationship() result."""
    all_events = _non_superseded(load_records(RELATIONSHIP_EVENTS_PATH))
    seen_keys: list[dict] = []
    for e in all_events:
        rk = e.get("relationship_key") or {}
        if rk.get("brand_entity_id") == brand_entity_id and rk not in seen_keys:
            seen_keys.append(rk)
    return [
        {"relationship_key": rk, "current_state": current_state_for_relationship(rk)}
        for rk in seen_keys
    ]


def list_forcing_signals(brand_entity_id: str | None = None, technology_category: str | None = None) -> list[dict]:
    signals = _non_superseded(load_records(FORCING_SIGNALS_PATH))
    if brand_entity_id:
        signals = [s for s in signals if s.get("brand_entity_id") == brand_entity_id]
    if technology_category:
        signals = [s for s in signals if s.get("technology_category") == technology_category]
    return signals


def list_fdd_sources_for_brand(brand_id: str) -> list[dict]:
    sources = _non_superseded(load_records(FDD_SOURCES_PATH))
    return [s for s in sources if s.get("brand_id") == brand_id]


def list_economics_for_brand(brand_id: str) -> list[dict]:
    rows = _non_superseded(load_records(TECHNOLOGY_ECONOMICS_PATH))
    return [r for r in rows if r.get("brand_id") == brand_id]


def list_governance_change_events_for_brand(brand_id: str) -> list[dict]:
    rows = _non_superseded(load_records(GOVERNANCE_CHANGE_EVENTS_PATH))
    return [r for r in rows if r.get("brand_id") == brand_id]


def list_penetration_reconciliation_for_brand(brand_id: str) -> list[dict]:
    rows = _non_superseded(load_records(PENETRATION_RECONCILIATION_PATH))
    return [r for r in rows if (r.get("relationship_key") or {}).get("brand_entity_id") == brand_id]


def list_fdd_research_gaps(brand_id: str | None = None) -> list[dict]:
    """Open, non-superseded FDD research gaps -- a status:"resolved"
    record both supersedes (hides) its open predecessor and is itself
    filtered out here, matching the rest of this module's append-only/
    supersedes discipline."""
    rows = _non_superseded(load_records(FDD_RESEARCH_GAPS_PATH))
    rows = [r for r in rows if r.get("status") == "open"]
    if brand_id:
        rows = [r for r in rows if r.get("brand_id") == brand_id]
    return rows


def get_entity_technology_profile(brand_entity_id: str) -> dict:
    """One shared, derived view -- consumed by both getTechnologyLifecycleProfile
    and Account Background Brief enrichment, so the two never drift apart.
    Pure read; computes nothing not already evidenced on disk.

    FDD Technology Governance & Economics (2026-10-02): fdd_sources,
    economics, governance_change_events, penetration_reconciliation, and
    open_research_gaps added here rather than a parallel profile function,
    same "ingest once, expose everywhere" principle the brief's own Core
    Architectural Principle states -- Account Background Brief and the API
    both read this one function, so neither can silently drift from the
    other."""
    governance = [g for g in _non_superseded(load_records(GOVERNANCE_PATH)) if g.get("brand_entity_id") == brand_entity_id]
    penetration = [
        p for p in _non_superseded(load_records(PENETRATION_PATH))
        if (p.get("relationship_key") or {}).get("brand_entity_id") == brand_entity_id
    ]
    change_events = [c for c in _non_superseded(load_records(CHANGE_EVENTS_PATH)) if c.get("brand_entity_id") == brand_entity_id]
    return {
        "brand_entity_id": brand_entity_id,
        "relationships": list_relationships_for_brand(brand_entity_id),
        "governance": governance,
        "penetration": penetration,
        "change_events": change_events,
        "forcing_signals": list_forcing_signals(brand_entity_id=brand_entity_id),
        "fdd_sources": list_fdd_sources_for_brand(brand_entity_id),
        "economics": list_economics_for_brand(brand_entity_id),
        "governance_change_events": list_governance_change_events_for_brand(brand_entity_id),
        "penetration_reconciliation": list_penetration_reconciliation_for_brand(brand_entity_id),
        "open_research_gaps": list_fdd_research_gaps(brand_entity_id),
    }
