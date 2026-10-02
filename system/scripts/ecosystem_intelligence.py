#!/usr/bin/env python3
"""
ecosystem_intelligence.py — industry-agnostic ecosystem graph operations.

This is the mutable intelligence artifact behind restaurant ecosystem mapping,
but the core primitives are deliberately universal: entity, relationship,
signal, assessment, source, user relevance, and strategic recommendation.

CLI:
    python3 system/scripts/ecosystem_intelligence.py summary
    python3 system/scripts/ecosystem_intelligence.py query-brand --brand "McDonald's"
    python3 system/scripts/ecosystem_intelligence.py query-brands --max-rank 10
    python3 system/scripts/ecosystem_intelligence.py query-vendor --vendor PAR --category pos
    python3 system/scripts/ecosystem_intelligence.py ingest-restaurants path/to/technomic.csv --dry-run
    python3 system/scripts/ecosystem_intelligence.py ingest-vendors path/to/vendor_evidence.csv --dry-run
    python3 system/scripts/ecosystem_intelligence.py promote-posture --relationship-id rel-... --new-posture substantiated --source-title "..." --source-type primary_operator_statement
    python3 system/scripts/ecosystem_intelligence.py smoke
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import audit_log  # noqa: E402  (RB 9.13 audit log — always available alongside this script)
import confidence_calibration  # noqa: E402  (Confidence-Based Auto-Recording, 2026-09-25 — see check_relationship_conflict())

# RB-2026-08-23: Blue Sheet coverage hook (CANONICAL_REGISTRY.yaml's
# blue_sheets.mutation_owner). Optional import, never fatal to this
# script's own job -- same "sync is additive, never blocks" precedent as
# mutations.cmd_loop_close's eolms sync (system/scripts/mutations.py).
try:
    _BLUE_SHEET_ENGINE_DIR = SCRIPTS_DIR.parents[1] / "blue_sheets" / "_engine"
    sys.path.insert(0, str(_BLUE_SHEET_ENGINE_DIR))
    import common as _blue_sheet_common  # noqa: E402
    import impact_review as _blue_sheet_impact_review  # noqa: E402
except Exception:  # noqa: BLE001
    _blue_sheet_common = None
    _blue_sheet_impact_review = None


def _log_blue_sheet_coverage(entity_id: str | None, *, mutation_type: str, detail: str) -> None:
    """Best-effort coverage logging for a canonical mutation that touched a
    brand entity. Never raises -- a Blue Sheet logging failure must not
    block or fail the ecosystem-intelligence mutation it's observing."""
    if _blue_sheet_common is None or not entity_id:
        return
    try:
        slug = _blue_sheet_common.entity_id_to_slug(entity_id)
        if slug is None:
            return
        activated = _blue_sheet_common.is_activated(slug)
        _blue_sheet_common.log_coverage_event(
            entity_id=entity_id, slug=slug, activated=activated,
            mutation_type=mutation_type, detail=detail,
        )
    except Exception:  # noqa: BLE001
        pass


def _sync_blue_sheet_technology_stack(
    entity_id: str | None, *, category: str | None, new_posture: str | None,
    source_title: str, source_url: str | None,
) -> None:
    """RB-2026-08-24: narrow, match-gated field-level sync -- the piece
    RB-2026-08-23's coverage-log-only hook deliberately deferred (see
    blue_sheets/_engine/common.py's module docstring). Only ever writes
    technology_stack[].status, only when the relationship's category maps
    to exactly one unambiguous row (blue_sheets.common.match_technology_
    stack_row), only when the posture maps to a non-operational status
    value (POSTURE_TO_STATUS -- never "Active"/"Reconcile"). Everything
    else falls through silently; the coverage log above already recorded
    that this mutation touched the account either way.

    Same "additive, never blocks" contract as _log_blue_sheet_coverage:
    never raises, never affects the caller's own return value -- this only
    ever runs after the real ecosystem_intelligence.json write has already
    succeeded.
    """
    if _blue_sheet_common is None or _blue_sheet_impact_review is None or not entity_id:
        return
    if not category or not new_posture:
        return
    status = _blue_sheet_common.POSTURE_TO_STATUS.get(new_posture)
    if status is None:
        return
    try:
        slug = _blue_sheet_common.entity_id_to_slug(entity_id)
        if slug is None or not _blue_sheet_common.is_activated(slug):
            return
        account = _blue_sheet_common.load_json(_blue_sheet_common.account_dir(slug) / "account.json")
        idx = _blue_sheet_common.match_technology_stack_row(account, category)
        if idx is None:
            return

        evidence = _blue_sheet_common.load_jsonl(_blue_sheet_common.account_dir(slug) / "evidence.jsonl")
        evidence_id = _blue_sheet_common.next_evidence_id(slug, evidence)
        as_of = _blue_sheet_common.today()
        event_evidence = {
            "evidence_id": evidence_id,
            "account_id": account["account_id"],
            "opportunity_ids": [],
            "source_type": "ecosystem_intelligence_mutation",
            "durable_source_id": source_url or source_title,
            "source_author": None,
            "participants": [],
            "event_date": as_of,
            "ingestion_date": as_of,
            "excerpt": source_title,
            "extracted_claims": [f"{category} posture promoted to {new_posture}"],
            "evidence_class": "system_promotion",
            "confidence": new_posture,
            "scope": "account",
            "limitations": "Auto-synced from an ecosystem_intelligence.json posture promotion; category-to-row match only, not a human review.",
            "contradiction_links": [],
            "processing_version": "blue-sheet-tech-stack-sync-v0.1",
        }
        change = _blue_sheet_impact_review.ProposedChange(
            json_path=f"technology_stack[{idx}].status",
            target_file="account.json",
            new_value=status,
            new_status="confirmed",
            new_confidence=new_posture,
            evidence_id=evidence_id,
            as_of=as_of,
            reason=f"ecosystem_intelligence.json posture promoted to {new_posture} for category={category!r}; matched technology_stack[{idx}] unambiguously.",
        )
        _blue_sheet_impact_review.process_event(slug, [change], event_evidence, apply=True)
    except Exception:  # noqa: BLE001
        pass

VALIDATOR = core.SYSTEM_DIR / "schemas" / "validate.py"
SCHEMA_PATH = core.SYSTEM_DIR / "schemas" / "ecosystem_intelligence.schema.json"

# Ecosystem vendor evidence files are derived intelligence, not raw source content.
# They are retained for 90 days under the derived_intelligence class.
ECOSYSTEM_RETENTION_CLASS = "derived_intelligence"

# Posture promotion ladder — only forward transitions are permitted by this function.
# Conflicts and refutations must be set via ingest-vendors (preserving source evidence).
POSTURE_LADDER = ["provisional", "partially_substantiated", "substantiated"]


BRAND_NAME_KEYS = ("brand", "brand_name", "chain", "chain_name", "concept", "restaurant_brand", "name")
SEGMENT_KEYS = ("segment", "menu_segment", "category", "sector")
RANK_KEYS = ("rank", "top_1500_rank", "technomic_rank")
SALES_KEYS = ("sales", "system_sales", "sales_2024", "sales_2025", "us_sales")
UNITS_KEYS = ("units", "unit_count", "locations", "stores", "total_units", "us_units")
AUV_KEYS = ("auv", "average_unit_volume", "avg_unit_volume")
SALES_DELTA_KEYS = ("sales_delta", "sales_change", "sales_growth", "yoy_sales")
UNIT_DELTA_KEYS = ("unit_delta", "unit_change", "unit_growth", "yoy_units")
IGNITE_ID_KEYS = ("ignite_id", "technomic_id")
SUBSEGMENT_KEYS = ("subsegment",)
MENU_TYPE_KEYS = ("menu_type", "menu_category")

VENDOR_KEYS = ("vendor", "vendor_name", "provider", "supplier")
CATEGORY_KEYS = ("category", "vendor_category", "technology_category", "tech_category")
PRODUCT_KEYS = ("product", "product_name", "platform", "product_module", "module")
STATUS_KEYS = ("status", "deployment_status", "relationship_status")
STAGE_KEYS = ("stage", "rollout_stage", "deployment_stage")
DEPLOYED_KEYS = ("deployed_units", "stores_deployed", "deployed_stores")
TARGET_KEYS = ("target_units", "target_stores")
TARGET_DATE_KEYS = ("target_date", "target_completion", "rollout_target_date")
PENETRATION_KEYS = ("penetration", "penetration_pct", "deployment_penetration")
RISK_KEYS = ("risk", "risk_color", "vendor_risk")
CONFIDENCE_KEYS = ("confidence", "confidence_level")
SOURCE_KEYS = ("source", "source_title", "source_name")
SOURCE_URL_KEYS = ("source_url", "url", "link")
SOURCE_DATE_KEYS = ("source_date", "published_at", "date")
STRATEGIC_NOTE_KEYS = ("strategic_note", "note", "notes", "interpretation")
EVIDENCE_POSTURE_KEYS = ("evidence_posture", "verification_status", "substantiation")
INTERPRETATION_SCOPE_KEYS = ("interpretation_scope", "vendor_scope", "scope")
SOURCE_TYPE_KEYS = ("source_type", "evidence_type", "source_class")
POS_ROLE_KEYS = ("pos_role", "vendor_role", "relationship_role", "scope_role")
GEOGRAPHY_KEYS = ("geography", "territory", "region", "market_scope")
CHANNEL_KEYS = ("channel", "deployment_channel", "sales_channel")
SERVICE_ROLE_KEYS = ("service_role", "service_scope", "partner_role")
DEPLOYMENT_CLAIM_KEYS = ("deployment_claim_type", "claim_type", "deployment_claim", "evidence_scope")
CUSTOMER_OPERATOR_KEYS = ("customer_operator", "franchisee", "operator", "operator_scope", "franchisee_operator")
SCOPE_UNIT_COUNT_KEYS = ("scope_unit_count", "case_study_units", "operator_units", "franchisee_units")

# POS-specific role vocabulary (aligns with domain pack verification rules)
POS_ROLE_VOCAB = {
    "system_of_record_pos",
    "approved_hardware_vendor",
    "hardware_reseller_service_provider",
    "installation_service_provider",
    "service_maintenance_provider",
    "payment_device_vendor",
    "acquirer_or_payments",
    "franchisee_deployment",
    "regional_deployment",
    "pilot",
    "legacy_incumbent",
    "replacement_candidate",
    "unknown",
}

# Relationship lifecycle status vocabulary. "conflicting" and "superseded" are
# set only by check_relationship_conflict() — never assigned directly from an
# ingested row's status column.
RELATIONSHIP_STATUS_VOCAB = {
    "active", "historical", "rumored", "evaluating", "replacing", "unknown",
    "conflicting", "superseded",
}

# Statuses that represent a live, unresolved claim on a brand+category — i.e.
# something that could plausibly be true right now, as opposed to a claim
# already resolved (historical, superseded) or explicitly discounted
# (conflicting). Two LIVE_CLAIM_STATUSES entries for different vendors in the
# same brand+category are what check_relationship_conflict() compares.
LIVE_CLAIM_STATUSES = {"active", "evaluating", "replacing"}

# Vendor roles that occupy a functionally different slot from "the vendor for
# this category" and therefore never genuinely rival another claim in the
# same brand+category -- an approved hardware vendor, reseller, or installer
# can be simultaneously true alongside a system-of-record software vendor.
# Confirmed live (2026-08-01 workbook migration dry-run): HP/MAPS/NCR at
# McDonald's were each flagged as false-positive conflicts against NewPOS
# purely because check_relationship_conflict() only compared brand+category,
# never vendor_role -- despite the schema's own vendor_role docstring saying
# its purpose is "prevents collapsing distinct vendor relationships into one
# edge." A relationship with no vendor_role set (most non-workbook ingestion
# paths) is unaffected -- None is not a member of this set, so conflict
# detection there behaves exactly as before.
_NON_EXCLUSIVE_VENDOR_ROLES = {
    "approved_hardware_vendor", "hardware_reseller_service_provider",
    "installation_service_provider", "service_maintenance_provider",
    "payment_device_vendor", "acquirer_or_payments",
    "franchisee_deployment", "regional_deployment", "pilot",
}

# Source quality model: source_type → (schema_quality, default_evidence_posture, default_confidence_level)
# Ordered from most to least authoritative. Row-level fields override these defaults.
SOURCE_QUALITY_MODEL: dict[str, tuple[str, str, str]] = {
    "primary_operator_statement":       ("primary", "substantiated",           "high"),
    "primary_vendor_announcement":      ("strong",  "partially_substantiated", "medium"),
    "linkedin_vendor_post":             ("medium",  "provisional",             "medium"),
    "case_study":                       ("strong",  "partially_substantiated", "medium"),
    "investor_filing_deck":             ("strong",  "partially_substantiated", "medium"),
    "franchisee_evidence":              ("medium",  "partially_substantiated", "medium"),
    "credible_trade_reporting":         ("medium",  "partially_substantiated", "medium"),
    "operator_context":                 ("medium",  "provisional",             "medium"),
    "job_posting":                      ("medium",  "provisional",             "low"),
    "implementation_partner_evidence":  ("medium",  "provisional",             "low"),
    "vendor_logo_customer_page":        ("weak",    "provisional",             "low"),
    "unsourced_spreadsheet":            ("weak",    "provisional",             "low"),
}


def _now() -> str:
    # RB-2026-08-28: was naive datetime.now() (local system time), the one
    # inconsistent timestamp source in this codebase -- everywhere else
    # (relationship_intake.py, mutation_reconciliation.py, self_audit_sweep.py,
    # etc.) uses UTC. Confirmed live: during the ~5-6h window each day when
    # local and UTC dates diverge (CDT, currently), a real conflict record
    # written here with today's LOCAL date compared false against
    # build_mutation_brief_block()'s UTC-based "today" filter
    # (intelligence_mutation_engine.py) -- a same-day conflict silently
    # excluded from the same-day brief's conflict count.
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


def _strip_diacritics(value: str) -> str:
    """'Café Rio' -> 'Cafe Rio'. Without this, the [^a-z0-9]+ normalization
    below treats accented letters as separators -- 'Café' becomes token
    'caf', which never matches 'cafe' from an unaccented spelling of the
    same brand, and entity resolution silently mints a duplicate."""
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _strip_diacritics(value).lower()).strip("-")
    return slug or "unknown"


def _norm_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _strip_diacritics(str(value)).strip().lower()).strip("_")


def _norm_pos_role(value: str | None) -> str:
    """Normalise a POS role string to a known vocab value, or 'unknown'."""
    if not value:
        return "unknown"
    candidate = _norm_key(value)
    return candidate if candidate in POS_ROLE_VOCAB else "unknown"


def _infer_vendor_role(category: str | None, product: str | None) -> str:
    text = f"{category or ''} {product or ''}".lower()
    if category == "pos" or " pos" in f" {text}":
        return "system_of_record_pos"
    return "unknown"


def _norm_source_type(value: str | None) -> str:
    if not value:
        return "unsourced_spreadsheet"
    candidate = _norm_key(value)
    aliases = {
        "vendor_case_study": "case_study",
        "vendor_case_studies_listing": "case_study",
        "vendor_case_study_listing": "case_study",
        "vendor_customer_page_case_study": "case_study",
        "vendor_blog_case_study_reference": "case_study",
        "vendor_customer_quote_case_studies_listing": "case_study",
        "vendor_customer_page": "vendor_logo_customer_page",
        "vendor_customer_page_video": "vendor_logo_customer_page",
        "vendor_video_resource": "vendor_logo_customer_page",
        "vendor_resource": "vendor_logo_customer_page",
        "vendor_resource_customer_story_reference": "vendor_logo_customer_page",
        "vendor_press_sec_filing": "investor_filing_deck",
        "sec_filing": "investor_filing_deck",
        "sec_corroborated": "investor_filing_deck",
    }
    candidate = aliases.get(candidate, candidate)
    return candidate if candidate in SOURCE_QUALITY_MODEL else "unsourced_spreadsheet"


def _norm_confidence_level(value: str | None, default: str) -> str:
    if not value:
        return default
    candidate = _norm_key(value)
    aliases = {
        "medium_high": "high",
        "med_high": "high",
        "mid_high": "high",
    }
    candidate = aliases.get(candidate, candidate)
    return candidate if candidate in {"low", "medium", "high", "critical"} else default


def _norm_evidence_posture(value: str | None, default: str) -> str:
    if not value:
        return default
    candidate = _norm_key(value)
    if "sec" in candidate and "corrobor" in candidate:
        return "substantiated"
    if "verified_by_vendor" in candidate:
        return "partially_substantiated"
    aliases = {
        "vendor_claimed": "provisional",
        "brand_vendor_claimed": "provisional",
        "verified_by_vendor": "partially_substantiated",
        "sec_corroborated": "substantiated",
        "contradicted": "conflicting",
        "stale": "unknown",
    }
    candidate = aliases.get(candidate, candidate)
    if candidate in {
        "provisional",
        "partially_substantiated",
        "substantiated",
        "conflicting",
        "refuted",
        "unknown",
    }:
        return candidate
    return default


DEPLOYMENT_CLAIM_TYPES = {
    "systemwide_deployment",
    "partial_deployment",
    "limited_operator_deployment",
    "franchisee_deployment",
    "pilot",
    "approved_vendor",
    "reseller_service",
    "logo_or_customer_page",
    "reference_only",
    "unknown",
}


def _norm_deployment_claim(value: str | None) -> str | None:
    if not value:
        return None
    candidate = _norm_key(value)
    aliases = {
        "logo": "logo_or_customer_page",
        "customer_logo": "logo_or_customer_page",
        "website_logo": "logo_or_customer_page",
        "case_study": "limited_operator_deployment",
        "operator_case_study": "limited_operator_deployment",
        "single_operator": "limited_operator_deployment",
        "approved_hardware": "approved_vendor",
        "approved_vendor_list": "approved_vendor",
        "systemwide": "systemwide_deployment",
        "system_wide": "systemwide_deployment",
        "full_deployment": "systemwide_deployment",
        "reseller": "reseller_service",
        "service_provider": "reseller_service",
    }
    candidate = aliases.get(candidate, candidate)
    return candidate if candidate in DEPLOYMENT_CLAIM_TYPES else "unknown"


_DEPLOYMENT_STATUS_TO_CLASSIFIER_STAGE = {
    "brand_wide_deployment": "systemwide_deployment",
    "enterprise_wide_deployment": "systemwide_deployment",
    "all_location_deployment": "systemwide_deployment",
    "active_rollout": "active_rollout",
    "significant_deployed_footprint": "partial_deployment",
    "multi_unit_deployment": "partial_deployment",
    "historical_reseller_relationship_superseded": "reseller",
    "pilot_only": "pilot",
}


def _infer_deployment_claim_type(
    *,
    source_type: str,
    vendor_role: str,
    stage: str,
    deployed_units: float | None,
    scope_unit_count: float | None,
    customer_operator: str | None,
) -> str:
    explicit_scope = bool(scope_unit_count or customer_operator)
    if source_type == "vendor_logo_customer_page":
        return "logo_or_customer_page"
    if source_type == "case_study" and explicit_scope:
        return "limited_operator_deployment"
    if source_type == "case_study":
        return "reference_only"
    if vendor_role == "hardware_reseller_service_provider" or "reseller" in stage or "service" in stage:
        return "reseller_service"
    if vendor_role in {"approved_hardware_vendor", "payment_device_vendor"} or "approved" in stage:
        return "approved_vendor"
    if vendor_role in {"franchisee_deployment", "regional_deployment"}:
        return "franchisee_deployment"
    if vendor_role == "pilot" or stage == "pilot":
        return "pilot"
    if stage in {"full_deployment", "systemwide_deployment"}:
        return "systemwide_deployment"
    if stage in {"partial_deployment", "active_rollout", "replacing"} or deployed_units:
        return "partial_deployment"
    return "unknown"


def _norm_category(value: str | None) -> str | None:
    if not value:
        return None
    text = _slug(value).replace("-", "_")
    aliases = {
        "point_of_sale": "pos",
        "pos_system": "pos",
        "pos_software": "pos",
        # "labor_workforce" (not "labor_scheduling") is the canonical target --
        # it's the bucket the Phase 2 vendor-first project's own "Labor/
        # Workforce" category batch actually populated (17 relationships vs.
        # labor_scheduling's 1), and the one this taxonomy normalization
        # (2026-08-05) consolidated everything else into.
        "labor": "labor_workforce",
        "labor_management": "labor_workforce",
        "scheduling": "labor_workforce",
        "labor_scheduling": "labor_workforce",
        "online_order": "online_ordering",
        "digital_ordering": "online_ordering",
        "loyalty_crm": "loyalty",
        "loyalty_marketing": "loyalty",
        "loyalty_online_ordering": "loyalty",
        "online_ordering_loyalty": "loyalty",
        "loyalty_digital_ordering": "loyalty",
        "loyalty_mobile": "loyalty",
        "loyalty_ordering": "loyalty",
        "analytics": "bi_analytics",
        "bi": "bi_analytics",
        "business_intelligence": "bi_analytics",
        "bi_analytics": "bi_analytics",
        "back_office_bi_team_management": "bi_analytics",
        "ai_intelligence": "bi_analytics",
        "back_office_accounting": "back_office_accounting",
        "back_office_inventory": "inventory",
        "inventory": "inventory",
        "ops_execution_kitchen": "ops_execution",
        "task_management_ops": "ops_execution",
        "back_office_operations_execution": "ops_execution",
        "back_office_operations_intelligence": "ops_execution",
        "drive_thru_voice_ai": "drive_thru_ai",
        "smart_camera": "smart_camera",
        "wifi": "networking_wifi",
        "networking": "networking_wifi",
        "kds": "kds_kitchen_ops",
        "kiosks_digital_menus": "kiosks",
        "unified_commerce_guest_experience": "unified_commerce",
        # "back_office_operations" is the canonical umbrella for a multi-module
        # back-office SUITE deal (accounting + inventory + workforce together,
        # e.g. Restaurant365/Crunchtime's own product positioning) -- kept
        # distinct from the narrower "back_office_accounting" and "inventory"
        # buckets above, which represent single-function claims.
        "back_office_accounting_inventory": "back_office_operations",
        "back_office_accounting_inventory_workforce": "back_office_operations",
        "back_office_accounting_inventory_workforce_payroll_hr": "back_office_operations",
        "back_office_accounting_payroll_hr": "back_office_operations",
        "back_office_inventory_labor": "back_office_operations",
        "back_office_inventory_workforce": "back_office_operations",
        "back_office_workforce_management": "labor_workforce",
        "back_office_workforce_training": "training_lms",
    }
    return aliases.get(text, text)


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("$", "").replace(",", "").replace("%", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return None


def _year_metrics(row: dict[str, str]) -> dict[str, dict[str, float]]:
    """Extract Technomic-style historical metrics by year.

    The Top 1500 sheets store sales and AUV columns in $000, e.g.
    `2024_u_s_sales_000`. RB stores normalized dollar values and keeps the
    full history under `technomic_history`.
    """
    history: dict[str, dict[str, float]] = {}
    for key, value in row.items():
        if "_yoy_" in key:
            continue
        match = re.match(r"^(20\d{2})_.*?(sales|units|auv)(?:_000)?$", key)
        if not match:
            continue
        year, metric = match.groups()
        number = _number(value)
        if number is None:
            continue
        history.setdefault(year, {})
        if metric == "sales":
            history[year]["system_sales_usd"] = number * 1000 if key.endswith("_000") else number
        elif metric == "auv":
            history[year]["auv_usd"] = number * 1000 if key.endswith("_000") else number
        elif metric == "units":
            history[year]["unit_count"] = number

    for key, value in row.items():
        match = re.match(r"^(20\d{2})_.*?yoy_(sales|units)$", key)
        if not match:
            continue
        year, metric = match.groups()
        number = _number(value)
        if number is None:
            continue
        history.setdefault(year, {})[f"{metric}_delta_pct"] = number * 100
    return history


def _latest_year(history: dict[str, dict[str, float]]) -> str | None:
    years = sorted((int(y) for y in history.keys()), reverse=True)
    return str(years[0]) if years else None


# A schema `level` string maps to the (arbitrarily chosen, but explicit and
# stable) confidence_calibration band that shares its name -- picking the
# LOWER of two bands that share a schema level (e.g. "medium" over
# "medium_high") rather than guessing which one a bare level string meant.
_LEVEL_TO_BAND = {"low": "low", "medium": "medium", "high": "high", "critical": "very_high"}


def _confidence(level: str | None = None, rationale: str = "") -> dict:
    """2026-09-25 (Confidence-Based Auto-Recording): score is no longer
    always None -- every caller of this helper gets a real numeric score
    derived from its level via confidence_calibration.CONFIDENCE_BAND_TO_
    SCHEMA, the same table check_relationship_conflict() now compares
    scores against. Previously every relationship built through this
    helper had confidence.score=None permanently -- 38 of 419 real
    relationships were stuck that way until this fix + the one-time
    backfill_confidence_scores.py migration."""
    clean = (level or "medium").strip().lower()
    if clean not in {"low", "medium", "high", "critical"}:
        clean = "medium"
    _, score = confidence_calibration.CONFIDENCE_BAND_TO_SCHEMA[_LEVEL_TO_BAND[clean]]
    return {"level": clean, "score": score, "rationale": rationale, "review_after": None}


def _read_graph(path: Path | None = None) -> dict:
    # RB 2026-08-27: confirmed live -- a `Path = core.ECOSYSTEM_INTELLIGENCE_PATH`
    # default is bound ONCE at this module's first import in a process, not
    # re-evaluated per call. A caller relying on reassigning
    # core.ECOSYSTEM_INTELLIGENCE_PATH afterward (e.g. test isolation) and
    # then calling _read_graph() with no argument silently gets the STALE
    # path instead -- in a real run, this caused a genuine unmocked write
    # into the production ecosystem_intelligence.json during what was meant
    # to be an isolated test. Resolve the live path at call time instead.
    if path is None:
        path = core.ECOSYSTEM_INTELLIGENCE_PATH
    if not path.exists():
        return {
            "version": 1,
            "contract": "rb_ecosystem_intelligence_v1",
            "last_updated": _today(),
            "description": "Industry-agnostic ecosystem intelligence graph.",
            "domain_packs": ["restaurants"],
            "entities": [],
            "relationships": [],
            "signals": [],
            "assessments": [],
            "sources": [],
            "user_relevance": [],
            "strategic_recommendations": [],
        }
    return json.loads(path.read_text(encoding="utf-8"))


def _write_graph(graph: dict, *, dry_run: bool = False) -> None:
    graph["last_updated"] = _today()
    # RB-2026-09-28: multibrand_franchisee_operator_ingest.py's operator
    # creation and intelligence_assessment.py's watchlist_add brand shell
    # both build entity dicts by hand and never set "ticker" -- 50 entities
    # (48 operators + 3 brand shells) reached the graph missing the key
    # entirely, tripping test_ecosystem_entities_have_ticker_field_and_
    # alias_backfill. Both call sites funnel through this one writer, so
    # backfilling here (schema allows ticker: null) closes the gap for any
    # entity-creation path, not just those two, without touching each one.
    for entity in graph.get("entities") or []:
        entity.setdefault("ticker", None)
    payload = json.dumps(graph, indent=2, sort_keys=False)
    if dry_run:
        print(payload)
        return
    if core.ECOSYSTEM_INTELLIGENCE_PATH.exists():
        core.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        tag = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(
            core.ECOSYSTEM_INTELLIGENCE_PATH,
            core.SNAPSHOTS_DIR / f"ecosystem_intelligence.pre-write-{tag}.json",
        )
    core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(payload + "\n", encoding="utf-8")
    # RB-2026-08-28: was a bare "python3" -- resolves via PATH, not
    # necessarily the interpreter/environment running this process. Same
    # class of bug confirmed live in mutations.py's baseline validator
    # (production runs with PYTHONNOUSERSITE=1 + a vendored PYTHONPATH;
    # "python3" on PATH resolved to /usr/bin/python3, which doesn't see
    # the vendored jsonschema, so every call here would fail identically
    # to a real validation failure). sys.executable guarantees the same
    # interpreter/environment this process actually launched with.
    #
    # RB-2026-09-27: was `--ecosystem-only`, which makes the validator
    # subprocess check its own hardcoded DEFAULT_ECOSYSTEM_TARGET
    # (system/ecosystem_intelligence.json) -- a separate process that
    # re-imports rb_core fresh and can never see this process's
    # core.ECOSYSTEM_INTELLIGENCE_PATH monkeypatch. Confirmed live: every
    # isolated test that wrote a schema-invalid entity through this
    # function still passed, because the validator was silently checking
    # the real production file (which happened to be valid) instead of
    # the test's own tmp graph. Pass the actual path being written, plus
    # its schema, explicitly -- the validator already supports this via
    # its positional target + --schema arguments, and resolving
    # core.ECOSYSTEM_INTELLIGENCE_PATH here (an attribute lookup, not a
    # bound default) picks up whatever path this call actually wrote to.
    rc = subprocess.run(
        [sys.executable, str(VALIDATOR), str(core.ECOSYSTEM_INTELLIGENCE_PATH), "--schema", str(SCHEMA_PATH)],
        capture_output=True,
        text=True,
    )
    if rc.returncode != 0:
        raise RuntimeError(rc.stderr or rc.stdout or "ecosystem validation failed")


def _index_by_id(items: list[dict]) -> dict[str, dict]:
    return {str(item.get("id")): item for item in items if item.get("id")}


def _upsert_entity(graph: dict, entity: dict) -> bool:
    entities = _index_by_id(graph["entities"])
    existing = entities.get(entity["id"])
    if not existing:
        graph["entities"].append(entity)
        return True
    existing["updated_at"] = _now()
    existing.setdefault("aliases", [])
    for alias in entity.get("aliases") or []:
        if alias not in existing["aliases"]:
            existing["aliases"].append(alias)
    existing.setdefault("domains", [])
    for domain in entity.get("domains") or []:
        if domain not in existing["domains"]:
            existing["domains"].append(domain)
    existing.setdefault("sources", [])
    for source in entity.get("sources") or []:
        if source not in existing["sources"]:
            existing["sources"].append(source)
    # RB-2026-10-01 (Todd): confirmed live on vendor-par-ops -- an
    # unattended spreadsheet/"vendor evidence row" ingestion (vendor_
    # relationship() below) silently flipped its operator-provided
    # attributes.primary_category from "back_office_operations" to "pos"
    # overnight, because this merge used to unconditionally overwrite any
    # existing attribute value with whatever the latest upsert supplied.
    # A vendor/brand's identity-level attributes (e.g. primary_category)
    # are not supposed to churn based on which single row last happened to
    # mention the entity in which context -- that's what a relationship's
    # own `category` field is for. Fix: fill in an attribute only when it
    # isn't already set; never silently overwrite an attribute that
    # already has a value. A deliberate correction goes through an
    # explicit, reviewed update (e.g. a direct entity edit), not a routine
    # upsert from a bulk ingestion row.
    existing.setdefault("attributes", {})
    for key, value in (entity.get("attributes") or {}).items():
        if value is None:
            continue
        if existing["attributes"].get(key) is None:
            existing["attributes"][key] = value
    if entity.get("confidence"):
        existing["confidence"] = entity["confidence"]
    return False


def set_entity_owner(
    graph: dict, entity_id: str, owner_name: str, owner_entity_id: str | None, source_id: str | None = None,
) -> bool:
    """RB-2026-09-11: dedicated writer for owner_name/owner_entity_id --
    small and targeted rather than routed through _upsert_entity()'s merge
    logic, which only ever touches a fixed set of named fields
    (aliases/domains/sources/attributes/confidence) and would silently
    never pick these two up. Only called from
    ownership_promotion.py's record_proposal() after an explicit human
    confirm -- same review-first discipline as every other structured
    fact in this graph. Returns False (no-op, entity not found) rather
    than raising, so a caller can decide how to report that.

    A second confirmed ownership simply overwrites the prior owner --
    ownership history is not tracked here; that's a real, separate
    follow-on if Todd wants it."""
    by_id = _index_by_id(graph.get("entities") or [])
    entity = by_id.get(entity_id)
    if entity is None:
        return False
    entity["owner_name"] = owner_name
    entity["owner_entity_id"] = owner_entity_id
    if source_id and source_id not in entity.setdefault("sources", []):
        entity["sources"].append(source_id)
    entity["updated_at"] = _now()
    return True


# 2026-09-30: allowlisted, not any key -- same "structured, named
# operations, never an arbitrary edit" discipline as everywhere else in
# this graph. Grow this set deliberately, one field at a time, as a real
# need for clearing that field comes up (see clear_entity_field() below).
_CLEARABLE_ENTITY_FIELDS = {"notes"}


def clear_entity_field(entity_id: str, field: str, *, reason: str) -> bool:
    """Real 2026-09-30 need, from the Data Tier Architecture work: no
    existing public function could edit or clear an arbitrary entity-
    level scalar field -- set_entity_owner() above is the only
    precedent, narrowly scoped to owner_name/owner_entity_id. Used to
    clear a stray private sales-opportunity note that had been written
    into entities[].notes (a Tier 1 "public ecosystem" field) instead of
    Tier 2 account-specific storage where it belonged.

    Reads and writes the graph itself (unlike set_entity_owner(), which
    takes an already-loaded graph and leaves persistence to its caller)
    since this is meant to be called directly for a one-off fix, not
    threaded through a larger existing read/mutate/write call site.
    Returns False (no-op) if the entity isn't found or the field is
    already empty, rather than raising."""
    if field not in _CLEARABLE_ENTITY_FIELDS:
        raise ValueError(f"field {field!r} is not in the clearable allowlist {_CLEARABLE_ENTITY_FIELDS}")
    graph = _read_graph()
    by_id = _index_by_id(graph.get("entities") or [])
    entity = by_id.get(entity_id)
    if entity is None or not entity.get(field):
        return False
    # Schema requires `notes` be a string when present (no null) -- most
    # entities simply omit the key rather than carry an empty string, so
    # deleting it, not nulling it, is the schema-valid "clear."
    old_value = entity.pop(field)
    entity["updated_at"] = _now()
    _write_graph(graph)
    audit_log.append_event(
        event_type="mutation_executed",
        item_summary=f"Entity field cleared: {entity_id}.{field} (was: {old_value!r})",
        reason=reason,
        outcome=f"{field} set to null",
        data_class="intelligence",
        retention_class=ECOSYSTEM_RETENTION_CLASS,
    )
    return True


def _upsert_source(graph: dict, source: dict) -> None:
    sources = _index_by_id(graph["sources"])
    if source["id"] not in sources:
        graph["sources"].append(source)


def _upsert_relationship(graph: dict, rel: dict) -> bool:
    rels = _index_by_id(graph["relationships"])
    existing = rels.get(rel["id"])
    if not existing:
        graph["relationships"].append(rel)
        return True
    existing.update({k: v for k, v in rel.items() if v is not None})
    existing["updated_at"] = _now()
    return False


def _remove_unscoped_superseded_relationships(graph: dict, rel: dict) -> int:
    """Drop older generic duplicate edges once a scoped edge exists.

    Example: `McDonald's + NCR + pos + approved_hardware_vendor` was originally
    represented without geography/channel. Once the evidence row says
    `domestic/approved_hardware`, the scoped edge is the more precise canonical
    representation and the generic duplicate should not keep surfacing.
    """
    if not (rel.get("geography") or rel.get("channel") or rel.get("service_role")):
        return 0
    removed = 0
    kept = []
    for existing in graph["relationships"]:
        same_identity = (
            existing.get("id") != rel.get("id")
            and existing.get("from_entity_id") == rel.get("from_entity_id")
            and existing.get("to_entity_id") == rel.get("to_entity_id")
            and existing.get("category") == rel.get("category")
            and existing.get("vendor_role") == rel.get("vendor_role")
        )
        existing_scoped = existing.get("geography") or existing.get("channel") or existing.get("service_role")
        if same_identity and not existing_scoped:
            removed += 1
            continue
        kept.append(existing)
    if removed:
        graph["relationships"] = kept
    return removed


# Moved to confidence_calibration.py (Phase 6, 2026-09-25) so ownership_
# promotion.py/executive_move_promotion.py reuse the exact same rule --
# these names stay as aliases so migrate_conflicting_relationships.py's
# existing ei._SUPERSEDE_SCORE_MARGIN/ei._HIGH_CONFIDENCE_THRESHOLD
# references keep working unchanged.
_SUPERSEDE_SCORE_MARGIN = confidence_calibration.SUPERSEDE_SCORE_MARGIN
_HIGH_CONFIDENCE_THRESHOLD = confidence_calibration.HIGH_CONFIDENCE_THRESHOLD


def _relationship_confidence_score(rel: dict) -> float:
    """A real 0-1 score for a relationship dict, deriving one from its
    confidence.level via confidence_calibration's bands when a caller built
    the relationship without going through _confidence() (e.g. a hand-built
    dict passing confidence as a bare string). Since the 2026-09-25 backfill,
    every relationship already in the graph carries a real score -- this
    fallback exists for incoming claims only."""
    confidence = rel.get("confidence")
    if isinstance(confidence, dict):
        score = confidence.get("score")
        if isinstance(score, (int, float)):
            return float(score)
        level = confidence.get("level")
    else:
        level = confidence if isinstance(confidence, str) else None
    band = _LEVEL_TO_BAND.get((level or "medium").strip().lower(), "medium")
    _, score = confidence_calibration.CONFIDENCE_BAND_TO_SCHEMA[band]
    return score


def check_relationship_conflict(graph: dict, rel: dict) -> dict:
    """Check an incoming brand+category vendor claim against existing live claims.

    This is the Research Intelligence Engine's conflict-detection gate (RB
    architectural directive, 2026-07-20; made score-aware 2026-09-25 per
    Confidence-Based Auto-Recording): the graph must never silently accept a
    new vendor-for-category claim without weighing it against what's already
    on file. Two shapes of outcome once a live rival exists for the same
    brand+category:

      - The incoming claim's confidence.score is meaningfully higher than the
        existing rival's (a real, calibrated margin — not just "both say
        active"): the prior claim is being overtaken by stronger evidence —
        auto-supersede it, but keep it in the graph with status "superseded"
        rather than deleting it. The incoming claim becomes "active".
      - Otherwise (comparable or lower confidence than the incumbent — the
        Papa John's/Worldpay case, where a stale "evaluating" claim must not
        be treated as an open opportunity once a stronger claim is already on
        file, and also a brand-new low-confidence rumor arriving against an
        established incumbent): don't pick a winner, but DON'T block either.
        The incoming claim is recorded with status "rumored" — a real, dated,
        sourced, confidence-scored entry, visible in the graph, just not
        promoted to "the current answer". Todd's explicit direction
        (2026-09-25): "fuzzy is okay" — nothing sourced ever waits in a
        queue; it just doesn't get to overwrite a stronger existing claim.

    Two open/tentative claims for the same category (e.g. two vendors both
    "evaluating") are not a conflict — multiple candidates can legitimately
    compete for the same not-yet-decided category.

    Returns:
        {"conflict": False}
        {"conflict": True, "resolution": "auto_superseded", "existing": <rel>}
        {"conflict": True, "resolution": "recorded_alongside", "existing": <rel>}
    """
    brand_id = rel.get("from_entity_id")
    vendor_id = rel.get("to_entity_id")
    category = rel.get("category")
    incoming_status = rel.get("status") or "active"
    if not (brand_id and vendor_id and category):
        return {"conflict": False}

    # A non-exclusive incoming role (e.g. approved_hardware_vendor) can never
    # rival anything -- it doesn't claim to be "the" vendor for this category.
    if rel.get("vendor_role") in _NON_EXCLUSIVE_VENDOR_ROLES:
        return {"conflict": False}

    rivals = [
        existing for existing in graph.get("relationships", [])
        if existing.get("from_entity_id") == brand_id
        and existing.get("category") == category
        and existing.get("to_entity_id") != vendor_id
        and existing.get("status") in LIVE_CLAIM_STATUSES
        and existing.get("vendor_role") not in _NON_EXCLUSIVE_VENDOR_ROLES
    ]
    if not rivals:
        return {"conflict": False}

    # An incoming claim that isn't even live (status outside LIVE_CLAIM_STATUSES
    # -- e.g. historical/superseded) isn't competing for the category at all,
    # so it can never conflict with anything. Confirmed live (2026-08-01):
    # Burger King's NCR row is explicitly historical ("Historical --
    # superseded" per the workbook's own lifecycle_current_state column,
    # displaced by PAR Brink) -- writing it as historical alongside PAR's
    # still-active claim is not a conflict, it's the whole point of recording
    # lifecycle history. This is narrower than skipping on incoming_status !=
    # "active": an "evaluating"/"replacing" incoming claim IS still live and
    # must still flag against an already-confirmed active incumbent (the
    # Papa John's/Worldpay case this function's docstring names).
    if incoming_status not in LIVE_CLAIM_STATUSES:
        return {"conflict": False}

    # Most recently updated rival is the one the incoming claim actually collides with.
    existing = max(rivals, key=lambda r: r.get("updated_at") or "")
    existing_status = existing.get("status") or "active"

    if incoming_status != "active" and existing_status != "active":
        return {"conflict": False}

    # Status tier still decides FIRST, unchanged from the original 2026-07-20
    # rule -- a claim literally marked "active" always outranks one that
    # isn't (evaluating/rumored/replacing), before confidence even enters the
    # picture. This preserves the Papa John's/Worldpay case exactly: a stale
    # "evaluating" claim never overtakes an already-confirmed incumbent, no
    # matter its score.
    if existing_status != "active" and incoming_status == "active":
        rel["status"] = "active"
        return {"conflict": True, "resolution": "auto_superseded", "existing": existing}

    if existing_status == "active" and incoming_status != "active":
        rel["related_claim_id"] = existing.get("id")
        return {"conflict": True, "resolution": "recorded_alongside", "existing": existing}

    # Both claims are "active" -- status tier alone can't break the tie.
    # 2026-09-25 (Confidence-Based Auto-Recording): this is the new case the
    # feature exists for. Two automated findings can both claim to be
    # confirmed truth (a trade-press guess and an official filing are both
    # proposed as status="active" by whatever found them) -- only their real
    # confidence score can tell them apart. Never returns "requires_
    # confirmation" -- every incoming claim gets recorded either way (see
    # resolve_and_upsert_relationship() below, which always upserts).
    new_score = _relationship_confidence_score(rel)
    existing_score = _relationship_confidence_score(existing)

    if confidence_calibration.should_supersede(new_score, existing_score):
        return {"conflict": True, "resolution": "auto_superseded", "existing": existing}

    rel["status"] = "rumored"
    rel["related_claim_id"] = existing.get("id")
    return {"conflict": True, "resolution": "recorded_alongside", "existing": existing}


def _write_conflict_record(record: dict) -> None:
    """Append a conflict-RESOLUTION record to the shared decision-audit log.

    2026-09-25 (Confidence-Based Auto-Recording): this file used to be a
    blocking queue -- every entry had resolution "requires_confirmation" and
    nothing in the codebase ever read it back to mark one resolved (confirmed
    live: 20 entries, oldest 53 days, permanently stuck). check_relationship_
    conflict() no longer produces that resolution at all, so every record
    written here now documents a decision that was ALREADY made (auto_
    superseded or recorded_alongside) -- pure transparency ("let RBB
    interpret that as they build the brief"), never a pending action. Still
    append-only JSONL, same as before, so the full history survives."""
    queue_path = core.SYSTEM_DIR / "inbox" / "ecosystem" / "conflict_queue.jsonl"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    with open(queue_path, "a") as f:
        f.write(json.dumps(record) + "\n")


def resolve_and_upsert_relationship(graph: dict, rel: dict, by_id: dict[str, dict] | None = None) -> dict:
    """Apply conflict detection, then upsert `rel` into the graph. Always
    upserts -- 2026-09-25 (Confidence-Based Auto-Recording): there is no
    longer a code path where this function declines to write `rel` because a
    human hasn't confirmed it yet. check_relationship_conflict() either finds
    no rival, decides the incoming claim wins ("auto_superseded" -- the rival
    is marked superseded, kept, never deleted), or decides it doesn't beat
    the incumbent ("recorded_alongside" -- rel's own status is downgraded to
    "rumored" by check_relationship_conflict() itself, and it's still
    written, just not as the current answer).

    `by_id` is an optional entity id→entity index (avoids rebuilding it per
    row when called from a loop); if omitted it's built from `graph`.

    Returns a result dict: {"added": bool, "conflict": <check_relationship_conflict result>}.
    """
    if by_id is None:
        by_id = _index_by_id(graph.get("entities") or [])

    verdict = check_relationship_conflict(graph, rel)
    if verdict["conflict"]:
        existing = verdict["existing"]
        existing_status_before = existing.get("status")
        brand = by_id.get(rel.get("from_entity_id"), {})
        incoming_vendor = by_id.get(rel.get("to_entity_id"), {})
        existing_vendor = by_id.get(existing.get("to_entity_id"), {})
        record = {
            "detected_at": _now(),
            "brand_id": rel.get("from_entity_id"),
            "brand_name": brand.get("name"),
            "category": rel.get("category"),
            "resolution": verdict["resolution"],
            "incoming": {
                "relationship_id": rel.get("id"),
                "vendor_id": rel.get("to_entity_id"),
                "vendor_name": incoming_vendor.get("name"),
                "status": rel.get("status"),
                "confidence_score": _relationship_confidence_score(rel),
                "source": (rel.get("sources") or [None])[0],
            },
            "existing": {
                "relationship_id": existing.get("id"),
                "vendor_id": existing.get("to_entity_id"),
                "vendor_name": existing_vendor.get("name"),
                "status": existing_status_before,
                "confidence_score": _relationship_confidence_score(existing),
                "source": (existing.get("sources") or [None])[0],
            },
        }
        if verdict["resolution"] == "auto_superseded":
            existing["status"] = "superseded"
            existing["superseded_by"] = rel.get("id")
            existing["updated_at"] = _now()
            record["note"] = (
                f"Auto-superseded: {existing_vendor.get('name')}'s claim for "
                f"{rel.get('category')} was '{existing_status_before}' status "
                f"(confidence {record['existing']['confidence_score']}); "
                f"{incoming_vendor.get('name')}'s claim (confidence "
                f"{record['incoming']['confidence_score']}) meaningfully overtakes it."
            )
        else:
            # rel["status"]/rel["related_claim_id"] are already set by
            # check_relationship_conflict() itself -- related_claim_id is a
            # plain cross-reference, not a "requires action" flag; nothing
            # here blocks the write.
            record["note"] = (
                f"Recorded alongside: {existing_vendor.get('name')} already holds "
                f"{rel.get('category')} for {brand.get('name')} at confidence "
                f"{record['existing']['confidence_score']}; {incoming_vendor.get('name')}'s "
                f"claim (confidence {record['incoming']['confidence_score']}) is recorded "
                f"as a reported, non-current alternative -- not treated as an open "
                f"opportunity or promoted over the existing claim."
            )
        _write_conflict_record(record)

    is_new = _upsert_relationship(graph, rel)
    return {"added": is_new, "conflict": verdict}


def _load_csv_like(path: Path) -> list[dict[str, str]]:
    dialect = "excel-tab" if path.suffix.lower() == ".tsv" else "excel"
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, dialect=dialect)
        return [{_norm_key(k): (v or "").strip() for k, v in row.items()} for row in reader]


def _xlsx_shared_strings(root: ET.Element) -> list[str]:
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    out = []
    for si in root.findall("x:si", ns):
        parts = [t.text or "" for t in si.findall(".//x:t", ns)]
        out.append("".join(parts))
    return out


def _xlsx_col_index(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref.upper())
    if not letters:
        return 0
    total = 0
    for ch in letters.group(1):
        total = total * 26 + (ord(ch) - ord("A") + 1)
    return total - 1


def _xlsx_cell_text(cell: ET.Element, ns: dict[str, str], shared: list[str]) -> str:
    raw = cell.find("x:v", ns)
    if raw is not None:
        value = raw.text or ""
        if cell.attrib.get("t") == "s" and value:
            return shared[int(value)]
        return value
    if cell.attrib.get("t") == "inlineStr":
        parts = [t.text or "" for t in cell.findall(".//x:t", ns)]
        return "".join(parts)
    return ""


def _xlsx_rows_from_sheet_xml(payload: bytes, ns: dict[str, str], shared: list[str]) -> list[list[str]]:
    root = ET.fromstring(payload)
    rows: list[list[str]] = []
    for row in root.findall(".//x:sheetData/x:row", ns):
        values_by_col: dict[int, str] = {}
        max_col = -1
        for cell in row.findall("x:c", ns):
            ref = cell.attrib.get("r", "")
            col = _xlsx_col_index(ref) if ref else max_col + 1
            max_col = max(max_col, col)
            values_by_col[col] = _xlsx_cell_text(cell, ns, shared)
        if max_col >= 0:
            rows.append([values_by_col.get(i, "") for i in range(max_col + 1)])
    return rows


def _rows_to_dicts(rows: list[list[str]]) -> list[dict[str, str]]:
    for idx, row in enumerate(rows):
        headers = [_norm_key(h) for h in row]
        header_set = {h for h in headers if h}
        if "brand" in header_set and (
            "vendor" in header_set
            or "category" in header_set
            or "tech_category" in header_set
            or "technology_category" in header_set
        ):
            return [
                {headers[i]: (str(record[i]).strip() if i < len(record) else "") for i in range(len(headers)) if headers[i]}
                for record in rows[idx + 1:]
                if any(str(value).strip() for value in record)
            ]
    if not rows:
        return []
    headers = [_norm_key(h) for h in rows[0]]
    return [
        {headers[i]: (str(row[i]).strip() if i < len(row) else "") for i in range(len(headers)) if headers[i]}
        for row in rows[1:]
        if any(str(value).strip() for value in row)
    ]


def _load_xlsx(path: Path, *, sheet_index: int = 1) -> list[dict[str, str]]:
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            shared = _xlsx_shared_strings(ET.fromstring(zf.read("xl/sharedStrings.xml")))
        sheet_files = sorted(
            (name for name in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", name)),
            key=lambda name: int(re.search(r"sheet(\d+)\.xml$", name).group(1)),
        )
        preferred = f"xl/worksheets/sheet{sheet_index}.xml"
        if preferred in sheet_files:
            sheet_files = [preferred] + [name for name in sheet_files if name != preferred]
        for sheet_name in sheet_files:
            rows = _xlsx_rows_from_sheet_xml(zf.read(sheet_name), ns, shared)
            records = _rows_to_dicts(rows)
            if records and any(row.get("brand") and row.get("vendor") for row in records):
                return records
        if sheet_files:
            return _rows_to_dicts(_xlsx_rows_from_sheet_xml(zf.read(sheet_files[0]), ns, shared))
    return []


def _xlsx_sheet_name_map(path: Path) -> dict[str, str]:
    """Map declared tab name -> zip member path (e.g. 'xl/worksheets/sheet11.xml'),
    read from xl/workbook.xml + xl/_rels/workbook.xml.rels rather than trusting
    sheetN.xml numeric order to match declared tab order (it isn't guaranteed to)."""
    ns_main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ns_r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ns_pkgrel = "http://schemas.openxmlformats.org/package/2006/relationships"
    with zipfile.ZipFile(path) as zf:
        wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
        rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_targets = {
            rel.attrib["Id"]: rel.attrib["Target"].lstrip("/")
            for rel in rels_root.findall(f"{{{ns_pkgrel}}}Relationship")
        }
        name_to_sheet: dict[str, str] = {}
        for sheet in wb_root.findall(f".//{{{ns_main}}}sheet"):
            rid = sheet.attrib.get(f"{{{ns_r}}}id")
            target = rel_targets.get(rid or "")
            if target:
                # RB-DEFECT-2026-08-29: workbook.xml.rels Target paths are
                # relative to xl/ (e.g. "worksheets/sheet5.xml"), not the
                # archive root -- confirmed live against a real 12-sheet
                # workbook whose Target values had no "xl/" prefix. A caller
                # zf.read()-ing the bare target got KeyError: no such member,
                # for every sheet not lucky enough to already carry the
                # prefix. Some xlsx writers do emit an "xl/"-prefixed or
                # absolute ("/xl/...") target; normalize all three shapes to
                # one real, always-correct archive-relative path.
                target = target.lstrip("/")
                if not target.startswith("xl/"):
                    target = f"xl/{target}"
                name_to_sheet[sheet.attrib["name"]] = target
        return name_to_sheet


def _load_xlsx_sheet_by_name(
    path: Path, sheet_name: str, *, required_header_keys: tuple[str, ...] = ("brand",),
) -> list[dict[str, str]]:
    """Load one specific named tab by its declared name, not heuristic order.

    Multi-tab research workbooks like the Unified Restaurant-Tech Graph export
    put a title row and a description row above the real header row (data
    starts at row 5, not row 1) and don't always use "brand"/"vendor" as the
    header names -- e.g. "Vendor Customer Lists" uses "Customer", not "Brand".
    `required_header_keys` lets the caller say which normalized column names
    (ANY of them) mark the real header row, instead of the generic
    brand+vendor heuristic `_rows_to_dicts()` uses elsewhere.
    """
    member = _xlsx_sheet_name_map(path).get(sheet_name)
    if not member:
        raise ValueError(f"No sheet named {sheet_name!r} in {path.name}")
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            shared = _xlsx_shared_strings(ET.fromstring(zf.read("xl/sharedStrings.xml")))
        rows = _xlsx_rows_from_sheet_xml(zf.read(member), ns, shared)

    def _row_to_dict(headers: list[str], record: list[str]) -> dict[str, str]:
        # Some sheets place a second, derived summary table to the right of the
        # main table sharing a column name (e.g. "Vendor" appears at both column
        # 0 and column 19 in "Vendor Customer Lists"). A plain dict comprehension
        # lets the later duplicate silently overwrite the real value. Keep the
        # first occurrence of each header only.
        out: dict[str, str] = {}
        for i, header in enumerate(headers):
            if not header or header in out:
                continue
            out[header] = str(record[i]).strip() if i < len(record) else ""
        return out

    for idx, row in enumerate(rows):
        headers = [_norm_key(h) for h in row]
        header_set = {h for h in headers if h}
        if any(key in header_set for key in required_header_keys):
            return [
                _row_to_dict(headers, record)
                for record in rows[idx + 1:]
                if any(str(value).strip() for value in record)
            ]
    return []


def load_rows(
    path: Path, *, sheet_index: int = 1, sheet_name: str | None = None,
    required_header_keys: tuple[str, ...] = ("brand",),
) -> list[dict[str, str]]:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        return _load_csv_like(path)
    if suffix == ".xlsx":
        if sheet_name:
            return _load_xlsx_sheet_by_name(path, sheet_name, required_header_keys=required_header_keys)
        return _load_xlsx(path, sheet_index=sheet_index)
    raise ValueError(f"Unsupported input format: {suffix}. Use CSV, TSV, or XLSX.")


def source_for_file(graph: dict, path: Path, *, source_type: str, title: str) -> str:
    sid = f"src-{_slug(path.stem)}"
    _upsert_source(
        graph,
        {
            "id": sid,
            "source_type": source_type,
            "title": title,
            "url": None,
            "path": str(path),
            "published_at": None,
            "captured_at": _now(),
            "quality": "primary",
            "notes": "Operator-supplied file imported into RB ecosystem intelligence.",
        },
    )
    return sid


def restaurant_brand_entity(row: dict[str, str], source_id: str) -> dict | None:
    name = _first(row, BRAND_NAME_KEYS)
    if not name:
        return None
    history = _year_metrics(row)
    latest = _latest_year(history)
    latest_metrics = history.get(latest or "", {})
    attributes = {
        "rank": _number(_first(row, RANK_KEYS)),
        "segment": _first(row, SEGMENT_KEYS),
        "subsegment": _first(row, SUBSEGMENT_KEYS),
        "menu_type": _first(row, MENU_TYPE_KEYS),
        "technomic_ignite_id": _first(row, IGNITE_ID_KEYS),
        "technomic_latest_year": _number(latest),
        "system_sales": latest_metrics.get("system_sales_usd") or _number(_first(row, SALES_KEYS)),
        "unit_count": latest_metrics.get("unit_count") or _number(_first(row, UNITS_KEYS)),
        "auv": latest_metrics.get("auv_usd") or _number(_first(row, AUV_KEYS)),
        "sales_delta": latest_metrics.get("sales_delta_pct") or _number(_first(row, SALES_DELTA_KEYS)),
        "unit_delta": latest_metrics.get("units_delta_pct") or _number(_first(row, UNIT_DELTA_KEYS)),
        "technomic_history": history,
    }
    return {
        "id": f"brand-{_slug(name)}",
        "name": name,
        "entity_type": "brand",
        "subtype": "restaurant_brand",
        "status": "active",
        "domains": ["restaurants"],
        "aliases": [],
        "attributes": {k: v for k, v in attributes.items() if v is not None},
        "sources": [source_id],
        "confidence": _confidence("high", "Imported from operator-supplied restaurant baseline file."),
        "notes": "",
        "created_at": _now(),
        "updated_at": _now(),
    }

def _detailed_year_metrics(row: dict[str, str]) -> tuple[str, dict[str, float]] | None:
    year = _first(row, ("year",))
    if not year:
        return None
    year = str(int(float(year))) if _number(year) is not None else year

    def dollars_000(*keys: str) -> float | None:
        value = _number(_first(row, tuple(keys)))
        return value * 1000 if value is not None else None

    metrics = {
        "system_sales_usd": dollars_000("u_s_sales_000"),
        "unit_count": _number(_first(row, ("u_s_units",))),
        "auv_usd": dollars_000("auv_000"),
        "company_units": _number(_first(row, ("u_s_company_units",))),
        "franchise_units": _number(_first(row, ("u_s_franchise_units",))),
        "company_sales_usd": dollars_000("u_s_company_sales_000"),
        "franchise_sales_usd": dollars_000("u_s_franchise_sales_000"),
        "international_units": _number(_first(row, ("international_units",))),
        "international_sales_usd": dollars_000("international_sales_000"),
    }
    return year, {k: v for k, v in metrics.items() if v is not None}


def merge_restaurant_detail_rows(graph: dict, rows: list[dict[str, str]], source_id: str) -> dict[str, int]:
    entities = _index_by_id(graph["entities"])
    updated = skipped = 0
    for row in rows:
        name = _first(row, BRAND_NAME_KEYS)
        detail = _detailed_year_metrics(row)
        if not name or not detail:
            skipped += 1
            continue
        entity_id = f"brand-{_slug(name)}"
        entity = entities.get(entity_id)
        if not entity:
            entity = restaurant_brand_entity(row, source_id)
            if not entity:
                skipped += 1
                continue
            graph["entities"].append(entity)
            entities[entity_id] = entity
        year, metrics = detail
        attrs = entity.setdefault("attributes", {})
        detail_history = attrs.setdefault("technomic_detailed_history", {})
        detail_history[year] = metrics
        history = attrs.setdefault("technomic_history", {})
        history.setdefault(year, {}).update(
            {
                k: v
                for k, v in metrics.items()
                if k in {"system_sales_usd", "unit_count", "auv_usd"}
            }
        )
        if source_id not in entity.setdefault("sources", []):
            entity["sources"].append(source_id)
        entity["updated_at"] = _now()
        updated += 1
    return {"detail_rows_updated": updated, "detail_rows_skipped": skipped}


def ingest_restaurants(args) -> int:
    path = Path(args.path)
    rows = load_rows(path)
    graph = _read_graph()
    source_id = source_for_file(graph, path, source_type="restaurant_baseline_file", title=args.title or path.name)
    added = updated = skipped = 0
    for row in rows:
        entity = restaurant_brand_entity(row, source_id)
        if not entity:
            skipped += 1
            continue
        if _upsert_entity(graph, entity):
            added += 1
        else:
            updated += 1
    detail_stats = {"detail_rows_updated": 0, "detail_rows_skipped": 0}
    if path.suffix.lower() == ".xlsx":
        detail_rows = load_rows(path, sheet_index=2)
        if detail_rows:
            detail_stats = merge_restaurant_detail_rows(graph, detail_rows, source_id)
    if args.dry_run:
        print(json.dumps({"rows": len(rows), "added": added, "updated": updated, "skipped": skipped, **detail_stats}, indent=2))
        return 0
    _write_graph(graph)
    print(json.dumps({"rows": len(rows), "added": added, "updated": updated, "skipped": skipped, **detail_stats}, indent=2))
    return 0


def vendor_relationship(row: dict[str, str], graph: dict) -> dict | None:
    brand = _first(row, BRAND_NAME_KEYS)
    vendor = _first(row, VENDOR_KEYS)
    category = _norm_category(_first(row, CATEGORY_KEYS))
    if not (brand and vendor and category):
        return None
    brand_id = f"brand-{_slug(brand)}"
    vendor_id = f"vendor-{_slug(vendor)}"

    # Derive source quality class and defaults from source_type.
    # Row-level evidence_posture / confidence override these defaults.
    source_type_raw = _first(row, SOURCE_TYPE_KEYS) or "unsourced_spreadsheet"
    source_type = _norm_source_type(source_type_raw)
    schema_quality, default_posture, default_conf = SOURCE_QUALITY_MODEL.get(
        source_type, ("weak", "provisional", "low")
    )

    source_title = _first(row, SOURCE_KEYS) or (
        f"{vendor} / {brand} — {_first(row, PRODUCT_KEYS) or category} ({_first(row, SOURCE_TYPE_KEYS) or 'vendor evidence'})"
    )
    source_url = _first(row, SOURCE_URL_KEYS)
    source_id = f"src-{_slug(source_title)}"
    _upsert_source(
        graph,
        {
            "id": source_id,
            "source_type": source_type,
            "title": source_title,
            "url": source_url,
            "path": None,
            "published_at": _first(row, SOURCE_DATE_KEYS),
            "captured_at": _now(),
            "quality": schema_quality,
            "notes": "Vendor-category uploads are provisional until corroborated by primary/operator/trade evidence.",
        },
    )
    _upsert_entity(
        graph,
        {
            "id": brand_id,
            "name": brand,
            "entity_type": "brand",
            "subtype": "restaurant_brand",
            "status": "active",
            "domains": ["restaurants"],
            "aliases": [],
            "attributes": {},
            "sources": [source_id],
            "confidence": _confidence("medium", "Observed in vendor evidence row."),
            "notes": "",
            # RB-2026-09-02: confirmed live -- a genuinely new entity created
            # via this path (not merged into an existing one) never got a
            # "ticker" key at all, tripping test_entity_identity_scope.py's
            # schema-completeness check the first time this path created a
            # brand-new vendor entity instead of reusing an existing one.
            # None is correct/honest for the common private-company case;
            # _upsert_entity's merge preserves a real value already on file.
            "ticker": None,
            "created_at": _now(),
            "updated_at": _now(),
        },
    )
    _upsert_entity(
        graph,
        {
            "id": vendor_id,
            "name": vendor,
            "entity_type": "vendor",
            "subtype": "restaurant_technology_vendor",
            "status": "active",
            "domains": ["restaurants"],
            "aliases": [],
            "attributes": {"primary_category": category},
            "sources": [source_id],
            "confidence": _confidence("medium", "Observed in vendor evidence row."),
            "notes": "",
            "ticker": None,
            "created_at": _now(),
            "updated_at": _now(),
        },
    )

    # POS role: captures the specific deployment scope so two vendors in the same
    # category (e.g. NCR as approved hardware and NewPOS as system-of-record) are
    # stored as separate edges rather than collapsed into one.
    product = _first(row, PRODUCT_KEYS)
    pos_role = _norm_pos_role(_first(row, POS_ROLE_KEYS))
    if pos_role == "unknown":
        pos_role = _infer_vendor_role(category, product)
    geography = _norm_category(_first(row, GEOGRAPHY_KEYS))
    channel = _norm_category(_first(row, CHANNEL_KEYS))
    service_role = _norm_category(_first(row, SERVICE_ROLE_KEYS))
    customer_operator = _first(row, CUSTOMER_OPERATOR_KEYS)
    scope_unit_count = _number(_first(row, SCOPE_UNIT_COUNT_KEYS))

    # Row-level overrides win; fall back to source-type defaults.
    confidence_level = _norm_confidence_level(_first(row, CONFIDENCE_KEYS), default_conf)
    evidence_posture = _norm_evidence_posture(_first(row, EVIDENCE_POSTURE_KEYS), default_posture)

    risk = (_first(row, RISK_KEYS) or "unknown").lower()
    if risk not in {"green", "yellow", "red", "unknown"}:
        risk = "unknown"
    stage = _norm_category(_first(row, STAGE_KEYS)) or "unknown"
    deployed_units = _number(_first(row, DEPLOYED_KEYS))
    explicit_claim = _norm_deployment_claim(_first(row, DEPLOYMENT_CLAIM_KEYS))
    deployment_claim_type = explicit_claim or _infer_deployment_claim_type(
        source_type=source_type,
        vendor_role=pos_role,
        stage=stage,
        deployed_units=deployed_units,
        scope_unit_count=scope_unit_count,
        customer_operator=customer_operator,
    )
    status = (_first(row, STATUS_KEYS) or "active").lower().replace(" ", "_")
    if status not in RELATIONSHIP_STATUS_VOCAB:
        status = "active"

    # Relationship ID includes vendor_role plus optional geography/channel/service
    # nuance. This prevents collisions when the same brand and vendor have
    # separate domestic, international, reseller, install, service, or hardware
    # roles in the same technology category.
    scope_bits = [category, pos_role]
    for bit in (geography, channel, service_role):
        if bit:
            scope_bits.append(bit)
    if deployment_claim_type in {"limited_operator_deployment", "franchisee_deployment", "pilot", "logo_or_customer_page", "reference_only"}:
        scope_bits.append(deployment_claim_type)
    if customer_operator:
        scope_bits.append(customer_operator)
    rel_id = f"rel-{brand_id}-{'-'.join(_slug(bit) for bit in scope_bits)}-{vendor_id}"

    return {
        "id": rel_id,
        "from_entity_id": brand_id,
        "to_entity_id": vendor_id,
        "relationship_type": "uses_vendor_for_category",
        "status": status,
        "domains": ["restaurants"],
        "category": category,
        "product": product,
        "vendor_role": pos_role,
        "geography": geography,
        "channel": channel,
        "service_role": service_role,
        "deployment_claim_type": deployment_claim_type,
        "customer_operator": customer_operator,
        "scope_unit_count": scope_unit_count,
        "deployment": {
            "stage": stage,
            "deployed_units": deployed_units,
            "target_units": _number(_first(row, TARGET_KEYS)),
            "target_date": _first(row, TARGET_DATE_KEYS),
            "penetration_pct": _number(_first(row, PENETRATION_KEYS)),
            "scope": pos_role,
            "geography": geography,
            "channel": channel,
            "service_role": service_role,
            "deployment_claim_type": deployment_claim_type,
            "customer_operator": customer_operator,
            "scope_unit_count": scope_unit_count,
            "evidence": source_title,
        },
        "evidence_posture": evidence_posture,
        "interpretation_scope": _first(row, INTERPRETATION_SCOPE_KEYS) or (
            "Uploaded vendor-category signal. Treat as a lead for verification, not proof of system-of-record incumbency."
        ),
        "risk": risk,
        "sources": [source_id],
        "confidence": _confidence(
            confidence_level,
            f"Source class: {source_type}. Requires corroboration before strategic assertion.",
        ),
        "strategic_note": _first(row, STRATEGIC_NOTE_KEYS) or "",
        "created_at": _now(),
        "updated_at": _now(),
    }


def ingest_vendors(args) -> int:
    path = Path(args.path)
    rows = load_rows(path)
    graph = _read_graph()
    source_type_override = getattr(args, "source_type", None)
    added = updated = skipped = removed_superseded = conflicts_detected = auto_superseded = 0

    # Audit: log that we are accessing this vendor evidence file.
    if not args.dry_run:
        audit_log.log_source_accessed(
            source=str(path),
            item_summary=f"Vendor evidence file: {path.name} ({len(rows)} rows)",
            data_class="intelligence",
        )

    by_id = _index_by_id(graph["entities"])
    for row in rows:
        if source_type_override:
            row = {**row, "source_type": source_type_override}
        rel = vendor_relationship(row, graph)
        if not rel:
            skipped += 1
            continue
        removed_superseded += _remove_unscoped_superseded_relationships(graph, rel)
        # by_id is rebuilt here (not reused across rows) because vendor_relationship()
        # may have just upserted new brand/vendor entities into graph["entities"].
        by_id = _index_by_id(graph["entities"])
        outcome = resolve_and_upsert_relationship(graph, rel, by_id=by_id)
        if outcome["conflict"]["conflict"]:
            conflicts_detected += 1
            if outcome["conflict"]["resolution"] == "auto_superseded":
                auto_superseded += 1
        if outcome["added"]:
            added += 1
        else:
            updated += 1

    result = {
        "rows": len(rows),
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "removed_superseded": removed_superseded,
        "conflicts_detected": conflicts_detected,
        "auto_superseded": auto_superseded,
    }

    if args.dry_run:
        print(json.dumps(result, indent=2))
        return 0

    _write_graph(graph)

    # Audit: log persistence of vendor intelligence into the ecosystem graph.
    audit_log.log_item_persisted(
        item_summary=(
            f"Vendor evidence ingested into ecosystem graph: {path.name} — "
            f"{added} added, {updated} updated, {skipped} skipped"
        ),
        data_class="intelligence",
        retention_class=ECOSYSTEM_RETENTION_CLASS,
        source=str(path),
        reason="vendor_evidence_ingest",
    )

    print(json.dumps(result, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Workbook-to-graph migration (Unified Restaurant-Tech Graph request, 2026-07-31)
#
# Reconciles the "Vendor Customer Lists" sheet of a research workbook into the
# canonical graph via resolve_and_upsert_relationship()/check_relationship_conflict()
# -- the same conflict-detection gate ingest_vendors() already uses. Does not
# build a second mutation pipeline; extends the existing one with a workbook-
# specific row mapper, a reconciliation-outcome classifier, and idempotent
# source_assertions[] attachment.
# ---------------------------------------------------------------------------

WORKBOOK_DEPLOYMENT_STATUS_VALUES = {
    "contracted_deployment_pending", "active_rollout", "significant_deployed_footprint",
    "brand_wide_deployment", "enterprise_wide_deployment", "all_location_deployment",
    "multi_unit_deployment", "franchisee_deployment_not_brand_standard",
    "parent_platform_relationship_brand_scope_unresolved", "deployed_scope_not_publicly_disclosed",
    "historical_current_status_not_reconfirmed", "historical_reseller_relationship_superseded",
    "discontinued_or_replaced", "pilot_only", "working_profile_public_source_required",
}

AI_APPLICATION_VALUES = {
    "voice_ai", "computer_vision", "ai_personalization_next_best_action", "forecasting",
    "labor_optimization", "food_waste_optimization", "robotics_autonomous_systems",
    "predictive_maintenance", "guest_sentiment_conversational_analytics",
    "order_accuracy_kitchen_throughput_ops_intelligence", "other",
}

# Reconciliation outcomes a workbook row can resolve to. Every row gets
# exactly one -- this is the vocabulary the migration report is grouped by.
RECONCILIATION_OUTCOMES = (
    "new_relationship", "enrich_existing_relationship", "duplicate_no_change",
    "new_source_for_existing_relationship", "lifecycle_update", "supersedes_existing_relationship",
    "conflict_requires_review", "working_profile_not_promoted", "pilot_not_promoted",
    "invalid_customer_or_module_excluded", "entity_resolution_required",
)

# Historical/superseded deployment_status values represent a lifecycle claim,
# not a currently-live one -- the relationship they produce is written with
# status="historical", never "active".
_HISTORICAL_DEPLOYMENT_STATUSES = {
    "historical_current_status_not_reconfirmed",
    "historical_reseller_relationship_superseded",
    "discontinued_or_replaced",
}


def _norm_deployment_status(value: str | None) -> str | None:
    """Normalize a workbook 'Deployment Status' cell to the canonical
    15-value deployment_status enum (schema: relationship.deployment_status)."""
    if not value:
        return None
    candidate = _norm_key(value)
    aliases = {
        "enterprise_deployment": "enterprise_wide_deployment",
        "enterprise_working_profile": "working_profile_public_source_required",
        "working_profile": "working_profile_public_source_required",
        "pilot": "pilot_only",
        "historical_reseller_relationship": "historical_reseller_relationship_superseded",
    }
    candidate = aliases.get(candidate, candidate)
    return candidate if candidate in WORKBOOK_DEPLOYMENT_STATUS_VALUES else None


def _norm_workbook_row_deployment_status(row: dict[str, str]) -> str | None:
    """_norm_deployment_status() on the row's "Deployment Status" cell, with
    an override from "Lifecycle Current State" when the two disagree.

    Confirmed live (2026-08-01): NCR/Burger King has deployment_status
    "Deployed -- scope not publicly disclosed" but lifecycle_current_state
    "Historical -- superseded" (NCR Aloha was displaced by PAR Brink).
    lifecycle_current_state is the more authoritative signal for whether a
    claim is still live -- an explicit "historical"/"superseded" there
    overrides a deployment_status that doesn't already say so, otherwise the
    row would be written "active" and false-conflict against the vendor that
    actually replaced it.
    """
    deployment_status = _norm_deployment_status(row.get("deployment_status"))
    lifecycle_text = _norm_key(row.get("lifecycle_current_state") or "")
    if ("historical" in lifecycle_text or "superseded" in lifecycle_text) and (
        deployment_status not in _HISTORICAL_DEPLOYMENT_STATUSES
    ):
        return "discontinued_or_replaced"
    return deployment_status


def _norm_ai_application(value: str | None) -> str | None:
    """Normalize a workbook 'AI Application' cell. 'None evidenced' (the
    workbook's explicit not-applicable marker) and similar map to None, not
    to the 'other' enum value -- 'other' means a real-but-uncategorized AI
    use case, not the absence of one."""
    if not value:
        return None
    candidate = _norm_key(value)
    if candidate in {"none_evidenced", "none", "n_a", "not_applicable"}:
        return None
    aliases = {
        "voice_ai": "voice_ai",
        "drive_thru_voice_ai": "voice_ai",
        "computer_vision": "computer_vision",
        "ai_personalization": "ai_personalization_next_best_action",
        "next_best_action": "ai_personalization_next_best_action",
        "labor_optimization": "labor_optimization",
        "workforce_optimization": "labor_optimization",
        "food_waste_optimization": "food_waste_optimization",
        "robotics": "robotics_autonomous_systems",
        "autonomous_systems": "robotics_autonomous_systems",
        "predictive_maintenance": "predictive_maintenance",
        "guest_sentiment": "guest_sentiment_conversational_analytics",
        "conversational_analytics": "guest_sentiment_conversational_analytics",
        "order_accuracy": "order_accuracy_kitchen_throughput_ops_intelligence",
        "kitchen_throughput": "order_accuracy_kitchen_throughput_ops_intelligence",
        "ops_intelligence": "order_accuracy_kitchen_throughput_ops_intelligence",
    }
    candidate = aliases.get(candidate, candidate)
    return candidate if candidate in AI_APPLICATION_VALUES else "other"


def _infer_workbook_vendor_role(row: dict[str, str], category: str | None, product: str | None) -> str:
    """Vendor role inference for a workbook row.

    _infer_vendor_role(category, product) alone defaults every "pos"-category
    row to "system_of_record_pos" -- confirmed live this collapses distinct,
    coexisting roles (e.g. HP and PAR Technology as McDonald's *approved
    hardware* vendors) onto the same role as NewPOS (McDonald's actual
    system-of-record), which then falsely collides with it in
    check_relationship_conflict() as if they were rival system-of-record
    claims. The workbook's own "Deployment Status" / "Relationship Scope"
    text usually names the real role explicitly -- check that first.
    """
    # deployment_status/relationship_scope are short, structured cells but
    # sometimes don't repeat the analyst's actual role language -- confirmed
    # live: MAPS's row says "Deployed -- scope not publicly disclosed" /
    # "Brand relationship; deployment scope unconfirmed" in those two
    # columns, while the real "MAPS resells NCR and PAR equipment ... retains
    # McDonald's installation and service business" sentence is only in
    # deployment_detail. Check it too, but after the two structured columns
    # (deployment_detail is longer free text and more prone to incidental
    # substring matches on an unrelated sentence).
    text = " ".join([
        row.get("deployment_status") or "", row.get("relationship_scope") or "",
        row.get("deployment_detail") or "",
    ]).lower()
    if "approved hardware" in text or "approved_hardware" in text:
        return "approved_hardware_vendor"
    if "resell" in text or "hardware reseller" in text:
        return "hardware_reseller_service_provider"
    # _infer_vendor_role(category, product) only special-cases "pos" -- every
    # other category (e.g. "payments") falls through to "unknown" with no
    # keyword path at all, which silently discards a Phase 2 vendor-evidence
    # record's already-researched acquirer/payment-device classification
    # (confirmed live, Payments category batch, 2026-08-03: Global Payments/
    # Adyen/FreedomPay/etc. records all need one of these two roles).
    if "acquir" in text or "payment processing" in text or "payment acquiring" in text:
        return "acquirer_or_payments"
    if "payment device" in text or "payment terminal" in text or "pay-at-table" in text or "pay at table" in text:
        return "payment_device_vendor"
    if "install" in text and "service" in text:
        return "installation_service_provider"
    if "maintenance" in text or "service provider" in text:
        return "service_maintenance_provider"
    if "franchisee" in text:
        return "franchisee_deployment"
    if "regional" in text:
        return "regional_deployment"
    # "pilot" is the one keyword most likely to appear negated in free-text
    # deployment_detail prose -- confirmed live: Hi Auto's row states "This
    # is a production deployment, not a pilot," which a bare substring check
    # would misread as a pilot classification (the exact opposite of what
    # the row says).
    if "pilot" in text and "not a pilot" not in text and "not pilot" not in text:
        return "pilot"
    # Same category-blind-spot as acquirer_or_payments/payment_device_vendor
    # above, hit by the Loyalty/CRM batch (2026-08-03): a discontinued vendor
    # relationship (e.g. LevelUp at Pret A Manger/sweetgreen, both migrated
    # off after Grubhub sunset the standalone app) needs to say so via
    # vendor_role, not just fall through to "unknown".
    if "legacy incumbent" in text or "legacy_incumbent" in text:
        return "legacy_incumbent"
    if "replacement candidate" in text or "replacement_candidate" in text:
        return "replacement_candidate"
    return _infer_vendor_role(category, product)


def _is_working_profile_row(row: dict[str, str]) -> bool:
    """True when a row is the user's own working-profile/research-hypothesis
    entry (e.g. Todd's McDonald's stack notes) rather than source-backed
    evidence -- must never be promoted to an active, source-backed relationship
    at face value, even at high user-supplied confidence."""
    evidence_type = _norm_key(row.get("evidence_type") or "")
    verification = _norm_key(row.get("verification_status") or "")
    deployment_status = _norm_deployment_status(row.get("deployment_status"))
    if deployment_status == "working_profile_public_source_required":
        return True
    if "working_profile" in evidence_type or "user_provided" in evidence_type:
        return True
    if "source_capture_required" in verification or "capture_required" in verification:
        return True
    return False


def _is_pilot_row(row: dict[str, str]) -> bool:
    return _norm_deployment_status(row.get("deployment_status")) == "pilot_only"


def _looks_like_product_not_customer(customer_name: str, vendor_name: str, product_name: str | None) -> bool:
    """Reject rows where the 'customer' column is actually the vendor's own
    product/module name rather than a real restaurant brand (a known failure
    mode in loosely-curated vendor evidence)."""
    norm_customer = _norm_key(customer_name)
    if not norm_customer:
        return True
    if norm_customer == _norm_key(vendor_name):
        return True
    if product_name and norm_customer == _norm_key(product_name):
        return True
    return False


def _assertion_key(assertion: dict) -> tuple[str, str]:
    """Stable identity for a source_assertion: (source_id, content hash).
    Same source re-asserting the same claim on a later run must not create a
    duplicate entry -- but a source correcting its own prior claim (different
    paraphrase/posture) is a distinct assertion, not a duplicate."""
    payload = json.dumps(
        {k: v for k, v in assertion.items() if k != "discovered_at"},
        sort_keys=True, default=str,
    )
    return assertion.get("source_id", ""), str(abs(hash(payload)))


def _merge_source_assertions(existing: list[dict] | None, new_assertions: list[dict]) -> list[dict]:
    """Idempotent merge for relationship.source_assertions[].

    _upsert_relationship() does a shallow dict merge that overwrites arrays
    wholesale (`existing.update({k: v for k, v in rel.items() if v is not None})`),
    so passing a freshly-built source_assertions list straight through would
    silently clobber prior assertions on every re-run instead of appending.
    Call this first and pass the *merged* list into the relationship dict.
    """
    merged = list(existing or [])
    seen = {_assertion_key(a) for a in merged}
    for assertion in new_assertions:
        key = _assertion_key(assertion)
        if key not in seen:
            merged.append(assertion)
            seen.add(key)
    return merged


def _resolve_entity_id_any_type(name: str, graph: dict) -> str | None:
    """Resolve a raw name to a canonical entity id, across ANY entity_type
    (brand OR vendor) -- unlike _resolve_brand_entity_id() below, which is
    hard-restricted to entity_type == "brand" and carries brand-migration-
    specific disambiguation logic (superset-name matching, vendor_category_
    hints continuity) that doesn't apply here.

    RB-2026-09-08, 3-store unification Phase 3: built to canonicalize the
    keying of real, external per-entity caches (entity_alerts_cache.json,
    technomic_watchlist_promoted.json, market_signals_earnings.jsonl) that
    mix both brand names (e.g. "McDonald's") and vendor names (e.g. "PAR
    Technology") under one raw string, with no existing resolver that
    handles both. Exact name/alias match only -- no fuzzy/superset
    matching, since the caller here already has an already-known real
    company name, not an ambiguous incoming brand-migration row.

    Returns the entity id on an unambiguous exact match, or None on no
    match or genuine ambiguity (more than one entity shares the name) --
    NEVER guesses."""
    norm_name = _norm_key(name)
    if not norm_name:
        return None
    matches = []
    for e in graph.get("entities") or []:
        existing_norm = _norm_key(e.get("name") or "")
        if existing_norm == norm_name:
            matches.append(e["id"])
            continue
        if any(_norm_key(a) == norm_name for a in e.get("aliases") or []):
            matches.append(e["id"])
    unique_matches = sorted(set(matches))
    if len(unique_matches) == 1:
        return unique_matches[0]
    return None


def _resolve_brand_entity_id(
    name: str, graph: dict, *, vendor_category_hints: list[tuple[str, str]] | None = None,
) -> tuple[str | None, bool]:
    """Resolve a brand name to a graph entity id.

    Precedence:
      1. Exact name/alias match, disambiguated by relationship continuity when
         more than one existing brand entity shares the name (see below).
      2. A single existing brand whose name is a superset of the incoming
         name's tokens (e.g. incoming "Checkers" vs. existing "Checkers & Rally's").
      3. A brand-new slug id, flagged is_new=True.

    `vendor_category_hints` should be every (vendor, category) pair the
    caller has seen for this customer name anywhere in the sheet being
    migrated -- not just the one row currently being processed. A single
    row's own vendor is often a *new* vendor with no history yet (e.g. Hi
    Auto has no prior Checkers relationship at all); resolving per-row would
    correctly disambiguate Presto's row (which has continuity) but leave Hi
    Auto's row ambiguous even though they describe the same real brand.
    Gathering every row's hints for the name up front lets one row's
    continuity resolve the whole name consistently.

    Returns (entity_id, is_new). entity_id is None when multiple existing
    brand entities plausibly match and none is disambiguated by continuity --
    the caller must treat this as entity_resolution_required rather than
    guessing.
    """
    entities = graph.get("entities") or []
    norm_name = _norm_key(name)
    incoming_tokens = {t for t in norm_name.split("_") if t}

    def _name_overlaps(ent: dict) -> bool:
        if ent.get("entity_type") != "brand":
            return False
        existing_norm = _norm_key(ent.get("name") or "")
        if existing_norm == norm_name:
            return True
        if any(_norm_key(a) == norm_name for a in ent.get("aliases") or []):
            return True
        existing_tokens = {t for t in existing_norm.split("_") if t}
        if not (incoming_tokens and incoming_tokens.issubset(existing_tokens)):
            return False
        # RB-2026-09-02: confirmed live -- this subset check is bag-of-
        # words, so "Del Taco" (incoming) was reported ambiguous against
        # "Taco Del Mar" (existing), an entirely different brand that
        # happens to share the same tokens in a different order. The
        # subset check exists for genuine prefix/superset names (incoming
        # "Checkers" vs. existing "Checkers & Rally's", where "checkers"
        # is a literal leading substring of "checkers_rally_s") -- require
        # that the token match, not just the words in some order.
        return existing_norm.startswith(norm_name + "_") or ("_" + norm_name) in existing_norm

    name_matches = [e["id"] for e in entities if _name_overlaps(e)]

    if len(name_matches) > 1 and vendor_category_hints:
        continuity = set()
        for vendor_hint, category_hint in vendor_category_hints:
            if not (vendor_hint and category_hint):
                continue
            vendor_id = f"vendor-{_slug(vendor_hint)}"
            for cid in name_matches:
                if any(
                    r.get("from_entity_id") == cid
                    and r.get("to_entity_id") == vendor_id
                    and r.get("category") == category_hint
                    for r in graph.get("relationships") or []
                ):
                    continuity.add(cid)
        if len(continuity) == 1:
            return next(iter(continuity)), False

    if len(name_matches) == 1:
        return name_matches[0], False
    if len(name_matches) > 1:
        return None, False

    return f"brand-{_slug(name)}", True


def _brand_hints_by_customer_name(rows: list[dict[str, str]]) -> dict[str, list[tuple[str, str]]]:
    """Pre-scan every row's (vendor, category) pair, grouped by normalized
    customer name, so _resolve_brand_entity_id can disambiguate one row using
    continuity evidence contributed by a *different* row for the same name."""
    hints: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        customer = (row.get("customer") or "").strip()
        vendor = (row.get("vendor") or "").strip()
        category = _norm_category(row.get("tech_category"))
        if not customer:
            continue
        hints.setdefault(_norm_key(customer), []).append((vendor, category or ""))
    return hints


def _workbook_source_assertion(row: dict[str, str], *, posture: str) -> dict:
    evidence_type_raw = row.get("evidence_type") or ""
    source_type = _norm_source_type(evidence_type_raw)
    source_authority_map = {
        "investor_filing_deck": "sec_or_regulatory_filing",
        "primary_operator_statement": "operator_filing_earnings_investor",
        "primary_vendor_announcement": "vendor_filing_earnings_investor",
        "case_study": "vendor_case_study_named_customer",
        "credible_trade_reporting": "credible_trade_reporting_direct_attribution",
        "operator_context": "official_customer_page_or_executive_statement",
        "vendor_logo_customer_page": "vendor_logo_or_listing_page",
    }
    notes = (row.get("notes") or "").strip()
    return {
        "source_id": f"src-workbook-{_slug(row.get('vendor', ''))}-{_slug(row.get('customer', ''))}-{_slug(row.get('tech_category', ''))}",
        "url": row.get("source_url") or None,
        "title": f"{row.get('vendor', '')} / {row.get('customer', '')} — {row.get('product_module') or row.get('tech_category', '')}",
        "publisher": None,
        "published_at": row.get("evidence_date") or None,
        "discovered_at": _today(),
        "source_type": source_type,
        "source_authority": source_authority_map.get(source_type),
        "commercial_incentive_posture": (
            "vendor_self_interested" if "vendor" in _norm_key(evidence_type_raw)
            else "operator_self_interested" if "operator" in _norm_key(evidence_type_raw)
            else "independent" if source_type == "credible_trade_reporting"
            else "unknown"
        ),
        "claim_type": _norm_key(row.get("relationship_scope") or "") or None,
        "contracted_locations": None,
        "live_locations": _number(row.get("units")),
        "rollout_target": None,
        "customer_explicitly_named": bool((row.get("customer") or "").strip()),
        "evidence_origin": (
            "regulator" if source_type == "investor_filing_deck" and "sec" in _norm_key(evidence_type_raw)
            else "vendor" if "vendor" in _norm_key(evidence_type_raw)
            else "operator" if "operator" in _norm_key(evidence_type_raw)
            else "independent_reporting" if source_type == "credible_trade_reporting"
            else "unknown"
        ),
        "paraphrase": notes[:600] if notes else None,
        "posture": posture,
    }


def reconcile_workbook_row(
    row: dict[str, str], graph: dict, by_id: dict[str, dict],
    *, brand_hints: dict[str, list[tuple[str, str]]] | None = None,
) -> tuple[str, dict | None, dict | None]:
    """Classify one 'Vendor Customer Lists' row and, when promotable, build
    the relationship + source_assertion to write.

    `brand_hints` should come from `_brand_hints_by_customer_name()` run over
    the *whole* sheet first, so one row's vendor/category continuity can
    disambiguate a different row sharing the same customer name (see
    _resolve_brand_entity_id's docstring for why this matters).

    Returns (outcome, relationship_or_None, source_assertion_or_None). The
    relationship is fully built but not yet upserted -- the caller applies it
    via resolve_and_upsert_relationship() so conflict detection stays in one
    place for every ingestion path.
    """
    vendor = (row.get("vendor") or "").strip()
    customer = (row.get("customer") or "").strip()
    category = _norm_category(row.get("tech_category"))
    product = row.get("product_module") or None

    if not (vendor and category):
        return "invalid_customer_or_module_excluded", None, None
    if _looks_like_product_not_customer(customer, vendor, product):
        return "invalid_customer_or_module_excluded", None, None

    hints = (brand_hints or {}).get(_norm_key(customer)) or [(vendor, category)]
    brand_id, brand_is_new = _resolve_brand_entity_id(customer, graph, vendor_category_hints=hints)
    if brand_id is None:
        return "entity_resolution_required", None, None

    if _is_working_profile_row(row):
        return "working_profile_not_promoted", None, None
    if _is_pilot_row(row):
        return "pilot_not_promoted", None, None

    deployment_status = _norm_workbook_row_deployment_status(row)
    is_historical = deployment_status in _HISTORICAL_DEPLOYMENT_STATUSES
    posture = "historical" if is_historical else "current"
    status = "historical" if is_historical else "active"

    vendor_id = f"vendor-{_slug(vendor)}"
    vendor_role = _infer_workbook_vendor_role(row, category, product)
    rel_id = f"rel-{brand_id}-{_slug(category)}-{_slug(vendor_role)}-{vendor_id}"

    assertion = _workbook_source_assertion(row, posture=posture)
    source_id = assertion["source_id"]

    existing_rels = _index_by_id(graph.get("relationships") or [])
    existing = existing_rels.get(rel_id)
    if existing is None:
        # rel_id encodes vendor_role, and vendor_role inference can differ
        # between ingestion paths/refinements over time (confirmed live: this
        # exact case -- an older ingest tagged the Presto/Checkers relationship
        # vendor_role "unknown", the workbook's own text now infers
        # "hardware_reseller_service_provider" for the same real-world edge).
        # An exact rel_id match alone would then miss the pre-existing record
        # entirely and silently create a duplicate instead of superseding/
        # enriching it. Fall back to the same brand+category+vendor identity
        # check_relationship_conflict() already uses (vendor_role-agnostic) to
        # find the real continuation of this relationship, if one exists.
        existing = next(
            (
                r for r in graph.get("relationships") or []
                if r.get("from_entity_id") == brand_id
                and r.get("to_entity_id") == vendor_id
                and r.get("category") == category
            ),
            None,
        )
        if existing is not None:
            rel_id = existing["id"]  # reuse the existing id -- don't fork a second record for the same edge

    ai_application = _norm_ai_application(row.get("ai_application"))
    confidence_level = _norm_confidence_level(None, "medium")
    try:
        confidence_score = float(row.get("confidence") or 0) or None
    except ValueError:
        confidence_score = None

    # relationship_classification.py's 6-level model reads deployment_claim_type
    # (classify_relationship() in that module), which this function never used
    # to set -- every relationship built here landed unclassified regardless of
    # how much real deployment detail the row actually carried. Confirmed live
    # (2026-08-05): 365 of 381 relationships were "unclassified" after the
    # Phase 2 vendor-first import wave, and running classify-all changed
    # nothing, because there was nothing here for it to read. Derive the same
    # claim_type _infer_deployment_claim_type() computes for the ingest-vendors
    # CSV path, from the fields this path actually has (deployment_status
    # standing in for "stage", the assertion's own source_type, units for
    # deployed_units). A case-study source with no cited unit count still
    # correctly lands "reference_only" / unclassified -- that's the classifier
    # correctly refusing to guess, not a gap to work around.
    deployment_claim_type = _infer_deployment_claim_type(
        source_type=assertion.get("source_type") or "",
        vendor_role=vendor_role,
        stage=_DEPLOYMENT_STATUS_TO_CLASSIFIER_STAGE.get(deployment_status or "", ""),
        deployed_units=_number(row.get("units")),
        scope_unit_count=None,
        customer_operator=None,
    )

    relationship = {
        "id": rel_id,
        "from_entity_id": brand_id,
        "to_entity_id": vendor_id,
        "relationship_type": "uses_vendor_for_category",
        "status": status,
        "domains": ["restaurants"],
        "category": category,
        "product": product,
        "vendor_role": vendor_role,
        "ai_application": ai_application,
        "deployment_status": deployment_status,
        "deployment_claim_type": deployment_claim_type,
        "deployment_detail": (row.get("deployment_detail") or "")[:500] or None,
        "evidence_posture": "substantiated" if posture == "historical" else "partially_substantiated",
        "interpretation_scope": row.get("relationship_scope") or "",
        "risk": "unknown",
        "sources": [source_id],
        "source_assertions": [assertion],  # merged with existing below before upsert
        "confidence": _confidence(confidence_level, f"Workbook migration: {row.get('evidence_type', 'unsourced')}."),
        "strategic_note": (row.get("notes") or "")[:500],
        "created_at": _now(),
        "updated_at": _now(),
    }
    if confidence_score is not None:
        relationship["confidence"]["score"] = confidence_score

    if not existing:
        outcome = "supersedes_existing_relationship" if is_historical else "new_relationship"
        relationship["source_assertions"] = _merge_source_assertions(None, [assertion])
        return outcome, relationship, assertion

    # Existing relationship: merge assertions idempotently rather than
    # replacing the record, and don't overwrite stronger existing evidence
    # with a weaker workbook row.
    merged_assertions = _merge_source_assertions(existing.get("source_assertions"), [assertion])
    already_had_this_assertion = len(merged_assertions) == len(existing.get("source_assertions") or [])
    relationship["source_assertions"] = merged_assertions

    existing_posture_rank = {"provisional": 0, "partially_substantiated": 1, "substantiated": 2, "conflicting": 1, "refuted": 0, "unknown": 0}
    if existing_posture_rank.get(existing.get("evidence_posture") or "unknown", 0) > existing_posture_rank.get(relationship["evidence_posture"], 0):
        # Preserve stronger existing top-level fields; the merged source_assertions
        # array (appended above) still preserves this row's own evidence for history.
        relationship["evidence_posture"] = existing.get("evidence_posture")
        relationship["confidence"] = existing.get("confidence") or relationship["confidence"]
        relationship["status"] = existing.get("status") or relationship["status"]
        if already_had_this_assertion:
            return "duplicate_no_change", None, None
        return "new_source_for_existing_relationship", relationship, assertion

    if is_historical and existing.get("status") == "active":
        return "supersedes_existing_relationship", relationship, assertion
    if existing.get("deployment_status") != deployment_status and deployment_status:
        return "lifecycle_update", relationship, assertion
    if already_had_this_assertion:
        return "duplicate_no_change", None, None
    return "enrich_existing_relationship", relationship, assertion


def migrate_workbook(args) -> int:
    path = Path(args.path)
    args.dry_run = not getattr(args, "confirm", False)  # dry-run by default; --confirm to write
    graph = _read_graph()
    by_id = _index_by_id(graph.get("entities") or [])

    rows = load_rows(path, sheet_name="Vendor Customer Lists", required_header_keys=("vendor", "customer"))
    brand_hints = _brand_hints_by_customer_name(rows)
    # Process historical/superseded rows before their "current successor" rows
    # for the same brand+category. Confirmed live: without this, a sheet
    # listing Hi Auto's active Checkers claim before Presto's own historical
    # row means check_relationship_conflict() compares Hi Auto against a
    # Presto record that (at that point in the loop) still looks active,
    # producing a spurious requires_confirmation conflict that self-resolves
    # a few rows later anyway once Presto's row writes it historical. Stable
    # sort preserves original relative order within each group.
    rows.sort(key=lambda r: 0 if _norm_workbook_row_deployment_status(r) in _HISTORICAL_DEPLOYMENT_STATUSES else 1)

    outcomes: dict[str, list[dict]] = {name: [] for name in RECONCILIATION_OUTCOMES}
    added = updated = conflicts_detected = auto_superseded = 0

    for row in rows:
        outcome, relationship, _assertion = reconcile_workbook_row(row, graph, by_id, brand_hints=brand_hints)
        outcomes[outcome].append({
            "vendor": row.get("vendor"), "customer": row.get("customer"),
            "tech_category": row.get("tech_category"), "relationship_id": relationship.get("id") if relationship else None,
        })
        if relationship is None:
            continue
        by_id = _index_by_id(graph.get("entities") or [])
        if not args.dry_run:
            _upsert_entity(graph, {
                "id": relationship["from_entity_id"], "name": row.get("customer") or relationship["from_entity_id"],
                "entity_type": "brand", "subtype": "restaurant_brand", "status": "active",
                "domains": ["restaurants"], "aliases": [], "attributes": {}, "sources": relationship["sources"],
                "confidence": _confidence("medium", "Observed in workbook migration row."),
                "notes": "", "created_at": _now(), "updated_at": _now(), "ticker": None,
            })
            _upsert_entity(graph, {
                "id": relationship["to_entity_id"], "name": row.get("vendor") or relationship["to_entity_id"],
                "entity_type": "vendor", "subtype": "restaurant_technology_vendor", "status": "active",
                "domains": ["restaurants"], "aliases": [], "attributes": {"primary_category": relationship["category"]},
                "sources": relationship["sources"], "confidence": _confidence("medium", "Observed in workbook migration row."),
                "notes": "", "created_at": _now(), "updated_at": _now(), "ticker": None,
            })
            by_id = _index_by_id(graph.get("entities") or [])
        outcome_result = resolve_and_upsert_relationship(graph, relationship, by_id=by_id)
        if outcome_result["conflict"]["conflict"]:
            conflicts_detected += 1
            if outcome_result["conflict"]["resolution"] == "auto_superseded":
                auto_superseded += 1
        if outcome_result["added"]:
            added += 1
        else:
            updated += 1

    report = {
        "workbook": str(path),
        "sheet": "Vendor Customer Lists",
        "rows_processed": len(rows),
        "dry_run": bool(args.dry_run),
        "added": added,
        "updated": updated,
        "conflicts_detected": conflicts_detected,
        "auto_superseded": auto_superseded,
        "outcome_counts": {name: len(items) for name, items in outcomes.items()},
        "outcomes": outcomes,
    }

    core.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = core.SNAPSHOTS_DIR / f"workbook_migration_report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    report_path.write_text(json.dumps(report, indent=2))

    if not args.dry_run:
        _write_graph(graph)

    print(json.dumps({k: v for k, v in report.items() if k != "outcomes"}, indent=2))
    print(f"\nFull reconciliation report written to {report_path}")
    return 0


# ── RB Vendor-First Baseline Project (Phase 2, 2026-08-03) ──────────────────
# Historical deep-research pass over the restaurant-tech VENDOR universe
# (system/research/vendor_universe_draft_2026-08-03.md), run one category
# batch at a time. Each batch's findings land in a staging JSON file
# (system/research/phase2_<category>_evidence_draft_<date>.json) for Todd's
# review; import_phase2_evidence() below feeds an approved batch through the
# exact same reconcile_workbook_row()/resolve_and_upsert_relationship()
# pipeline migrate-workbook uses, rather than a separate write path.

_PHASE2_CONFIDENCE_SCORE = {
    "high": 0.9, "medium_high": 0.75, "medium": 0.6, "low": 0.35,
}

_PHASE2_SOURCE_TYPE_MAP = {
    "press_release": "primary_vendor_announcement",
    "sec_filing": "sec_filing",
    "earnings_call": "investor_filing_deck",
    "case_study": "case_study",
    "trade_press": "credible_trade_reporting",
}

# Reverse hints for _infer_workbook_vendor_role()'s keyword scan, so a Phase 2
# record's already-researched vendor_role survives into the graph instead of
# being re-derived from scratch (that function only special-cases "pos"
# category by default, "unknown" for everything else).
_PHASE2_VENDOR_ROLE_HINTS = {
    "approved_hardware_vendor": "approved hardware vendor",
    "hardware_reseller_service_provider": "hardware reseller",
    "installation_service_provider": "installation and service provider",
    "service_maintenance_provider": "maintenance service provider",
    "franchisee_deployment": "franchisee deployment",
    "regional_deployment": "regional deployment",
    "acquirer_or_payments": "acquirer or payments processor",
    "payment_device_vendor": "payment device vendor",
    "legacy_incumbent": "legacy incumbent",
    "replacement_candidate": "replacement candidate",
}


def _extract_unit_count(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r"([\d,]{2,7})\+?\s*(?:location|restaurant|unit|store)", text, re.IGNORECASE)
    return m.group(1).replace(",", "") if m else None


def _phase2_record_to_row(record: dict) -> dict[str, str]:
    """Map one Phase 2 vendor-evidence record onto the row shape
    reconcile_workbook_row() expects from a 'Vendor Customer Lists' sheet, so
    the same conflict-detection/upsert pipeline migrate-workbook uses applies
    here unchanged rather than a second hand-built write path."""
    confidence_key = _norm_key(record.get("confidence") or "")
    combined_text = f"{record.get('confidence_rationale') or ''} {record.get('notes') or ''}"
    return {
        "vendor": record.get("vendor") or "",
        "customer": record.get("brand") or "",
        "tech_category": record.get("category") or "",
        "product_module": record.get("product") or None,
        "deployment_status": record.get("deployment_status") or "",
        "ai_application": record.get("ai_application") or None,
        "confidence": str(_PHASE2_CONFIDENCE_SCORE.get(confidence_key, 0.5)),
        "notes": record.get("notes") or "",
        "deployment_detail": record.get("confidence_rationale") or "",
        "relationship_scope": _PHASE2_VENDOR_ROLE_HINTS.get(record.get("vendor_role") or "", ""),
        "evidence_type": _PHASE2_SOURCE_TYPE_MAP.get(record.get("source_type") or "", "unsourced_spreadsheet"),
        "units": _extract_unit_count(combined_text),
        "source_url": record.get("source_url") or None,
        "evidence_date": record.get("source_date") or None,
    }


def import_phase2_evidence(args) -> int:
    path = Path(args.path)
    args.dry_run = not getattr(args, "confirm", False)  # dry-run by default; --confirm to write
    graph = _read_graph()
    by_id = _index_by_id(graph.get("entities") or [])

    payload = json.loads(path.read_text(encoding="utf-8"))
    all_records = payload.get("records") or []
    skip_keys = set(getattr(args, "skip", None) or [])
    records = [r for r in all_records if f"{r.get('vendor')}::{r.get('brand')}" not in skip_keys]

    rows = [_phase2_record_to_row(r) for r in records]
    brand_hints = _brand_hints_by_customer_name(rows)
    rows.sort(key=lambda r: 0 if _norm_workbook_row_deployment_status(r) in _HISTORICAL_DEPLOYMENT_STATUSES else 1)

    outcomes: dict[str, list[dict]] = {name: [] for name in RECONCILIATION_OUTCOMES}
    added = updated = conflicts_detected = auto_superseded = 0

    for row in rows:
        outcome, relationship, _assertion = reconcile_workbook_row(row, graph, by_id, brand_hints=brand_hints)
        outcomes[outcome].append({
            "vendor": row.get("vendor"), "customer": row.get("customer"),
            "tech_category": row.get("tech_category"), "relationship_id": relationship.get("id") if relationship else None,
        })
        if relationship is None:
            continue
        by_id = _index_by_id(graph.get("entities") or [])
        if not args.dry_run:
            _upsert_entity(graph, {
                "id": relationship["from_entity_id"], "name": row.get("customer") or relationship["from_entity_id"],
                "entity_type": "brand", "subtype": "restaurant_brand", "status": "active",
                "domains": ["restaurants"], "aliases": [], "attributes": {}, "sources": relationship["sources"],
                "confidence": _confidence("medium", "Observed in Phase 2 vendor-evidence import."),
                "notes": "", "created_at": _now(), "updated_at": _now(), "ticker": None,
            })
            _upsert_entity(graph, {
                "id": relationship["to_entity_id"], "name": row.get("vendor") or relationship["to_entity_id"],
                "entity_type": "vendor", "subtype": "restaurant_technology_vendor", "status": "active",
                "domains": ["restaurants"], "aliases": [], "attributes": {"primary_category": relationship["category"]},
                "sources": relationship["sources"], "confidence": _confidence("medium", "Observed in Phase 2 vendor-evidence import."),
                "notes": "", "created_at": _now(), "updated_at": _now(), "ticker": None,
            })
            by_id = _index_by_id(graph.get("entities") or [])
        outcome_result = resolve_and_upsert_relationship(graph, relationship, by_id=by_id)
        if outcome_result["conflict"]["conflict"]:
            conflicts_detected += 1
            if outcome_result["conflict"]["resolution"] == "auto_superseded":
                auto_superseded += 1
        if outcome_result["added"]:
            added += 1
        else:
            updated += 1

    report = {
        "phase2_file": str(path),
        "category": payload.get("category"),
        "rows_processed": len(rows),
        "rows_skipped": len(all_records) - len(rows),
        "dry_run": bool(args.dry_run),
        "added": added,
        "updated": updated,
        "conflicts_detected": conflicts_detected,
        "auto_superseded": auto_superseded,
        "outcome_counts": {name: len(items) for name, items in outcomes.items()},
        "outcomes": outcomes,
    }

    core.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = core.SNAPSHOTS_DIR / f"phase2_import_report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    report_path.write_text(json.dumps(report, indent=2))

    if not args.dry_run:
        _write_graph(graph)

    print(json.dumps({k: v for k, v in report.items() if k != "outcomes"}, indent=2))
    print(f"\nFull reconciliation report written to {report_path}")
    return 0


def merge_brand_entities(args) -> int:
    """Merge one or more duplicate brand entities into a canonical one.

    _resolve_brand_entity_id()'s token-subset rule only catches a *shorter*
    incoming name matching a *longer* existing one, never the reverse -- so a
    Phase 2 evidence record naming a brand with extra context baked in (e.g.
    "Applebee's (Dine Brands Global)") creates a brand-new duplicate entity
    instead of resolving to the existing plain "Applebee's" one. Confirmed
    live, Drive-Thru/Voice AI batch (2026-08-03): 6 of that batch's 39 rows
    hit entity_resolution_required because a *later* clean row (plain
    "Carl's Jr.") then collided ambiguously between the real "Carl's Jr."
    entity and the earlier compound "CKE Restaurants Holdings (Hardee's and
    Carl's Jr.)" one via the same token-subset rule running in reverse. This
    command reassigns every relationship from each duplicate onto the
    canonical entity id (merging source_assertions idempotently if the
    canonical already has the same edge), folds the duplicate's name/aliases
    into the canonical entity's aliases, and drops the duplicate entity.
    Dry-run by default; --confirm to write.
    """
    graph = _read_graph()
    report = _merge_brand_entities_in_graph(graph, canonical_id=args.into, duplicate_ids=args.duplicate)
    report["dry_run"] = not args.confirm
    if "error" in report:
        print(json.dumps(report, indent=2))
        return 1
    if args.confirm:
        _write_graph(graph)
    print(json.dumps(report, indent=2))
    return 0


def _merge_brand_entities_in_graph(graph: dict, *, canonical_id: str, duplicate_ids: list[str]) -> dict:
    """Pure graph-mutation core of merge-brand-entities -- no file I/O, so it's
    directly unit-testable against a synthetic graph. See merge_brand_entities()
    for why this command exists and _read_graph()'s module-import-time-bound
    default path argument for why this function must never call it itself
    (that exact hazard already caused one real incident with this graph --
    see this module's Phase 2 baseline project comment block, "Incident
    during Phase 5" in system/CLAUDE_HANDOFF_RB_UNIFIED_RESTAURANT_TECH_GRAPH_2026-08-01.md).

    Works on either side of a relationship (from_entity_id -- the usual brand
    duplicate -- or to_entity_id -- a vendor duplicate). Confirmed live,
    backlog-validation batch (2026-08-04): "PAR Technology" and "PAR
    Technology (Brink POS)" ended up as two separate vendor entities from
    different research batches, and check_relationship_conflict() then read
    them as two *rival* vendors both claiming the same brand+category --
    false conflicts, not real ones. Reassigning to_entity_id the same way
    from_entity_id was already handled fixes this the same way."""
    by_id = _index_by_id(graph.get("entities") or [])
    if canonical_id not in by_id:
        return {"error": f"canonical entity {canonical_id!r} not found"}

    canonical_entity = by_id[canonical_id]
    merged: list[dict] = []
    relationships_reassigned = 0
    relationships_merged = 0

    for dup_id in duplicate_ids:
        dup = by_id.get(dup_id)
        if dup is None:
            merged.append({"id": dup_id, "status": "not_found"})
            continue
        if dup_id == canonical_id:
            merged.append({"id": dup_id, "status": "skipped_same_as_canonical"})
            continue

        dup_rels = [
            r for r in graph["relationships"]
            if r.get("from_entity_id") == dup_id or r.get("to_entity_id") == dup_id
        ]
        for rel in dup_rels:
            side = "from_entity_id" if rel.get("from_entity_id") == dup_id else "to_entity_id"
            new_id = rel["id"].replace(dup_id, canonical_id, 1)
            existing = next(
                (r for r in graph["relationships"] if r["id"] == new_id and r is not rel), None,
            )
            if existing is not None:
                existing["source_assertions"] = _merge_source_assertions(
                    existing.get("source_assertions"), rel.get("source_assertions") or [],
                )
                existing["sources"] = sorted(set((existing.get("sources") or []) + (rel.get("sources") or [])))
                existing["updated_at"] = _now()
                graph["relationships"].remove(rel)
                relationships_merged += 1
            else:
                rel["id"] = new_id
                rel[side] = canonical_id
                rel["updated_at"] = _now()
                relationships_reassigned += 1

        canonical_entity.setdefault("aliases", [])
        for name in [dup.get("name")] + list(dup.get("aliases") or []):
            if name and name != canonical_entity.get("name") and name not in canonical_entity["aliases"]:
                canonical_entity["aliases"].append(name)

        graph["entities"] = [e for e in graph["entities"] if e.get("id") != dup_id]
        by_id.pop(dup_id, None)
        merged.append({"id": dup_id, "name": dup.get("name"), "status": "merged"})

    return {
        "canonical": canonical_id,
        "canonical_name": canonical_entity.get("name"),
        "relationships_reassigned": relationships_reassigned,
        "relationships_merged": relationships_merged,
        "duplicates": merged,
    }


def backfill_deployment_claim_type(args) -> int:
    graph = _read_graph()
    report = _backfill_deployment_claim_types_in_graph(graph, force=args.force)
    report["dry_run"] = not args.confirm
    if args.confirm:
        _write_graph(graph)
    print(json.dumps(report, indent=2))
    return 0


def _backfill_deployment_claim_types_in_graph(graph: dict, *, force: bool = False) -> dict:
    """Pure graph-mutation core of backfill-deployment-claim-type -- no file
    I/O, unit-testable against a synthetic graph (same reasoning as
    _merge_brand_entities_in_graph: never call _read_graph()/_write_graph()
    from here).

    reconcile_workbook_row() only started setting deployment_claim_type in
    this same change (see its comment block) -- every relationship written
    before that fix has none, which makes relationship_classification.py's
    classify_relationship() silently skip it (365 of 381 relationships,
    confirmed live 2026-08-05). Recompute it from what's already stored on
    each relationship (vendor_role, deployment_status, and the first source
    assertion's source_type/live_locations) using the same inference
    migrate-workbook/import-phase2-evidence now use going forward, so
    classify-all has something real to work with for the whole graph, not
    just relationships written after this fix landed."""
    updated = 0
    skipped_already_set = 0
    skipped_no_signal = 0

    for rel in graph.get("relationships") or []:
        if rel.get("deployment_claim_type") and not force:
            skipped_already_set += 1
            continue

        assertions = rel.get("source_assertions") or []
        source_type = next((a.get("source_type") for a in assertions if a.get("source_type")), "")
        deployed_units = next((a.get("live_locations") for a in assertions if a.get("live_locations")), None)
        deploy = rel.get("deployment") or {}
        stage = _DEPLOYMENT_STATUS_TO_CLASSIFIER_STAGE.get(rel.get("deployment_status") or "", "")

        claim_type = _infer_deployment_claim_type(
            source_type=source_type,
            vendor_role=rel.get("vendor_role") or "",
            stage=stage,
            deployed_units=deployed_units or deploy.get("deployed_units"),
            scope_unit_count=rel.get("scope_unit_count") or deploy.get("scope_unit_count"),
            customer_operator=rel.get("customer_operator") or deploy.get("customer_operator"),
        )
        if claim_type == "unknown" and not (rel.get("vendor_role") or stage or source_type):
            skipped_no_signal += 1
            continue

        rel["deployment_claim_type"] = claim_type
        rel["updated_at"] = _now()
        updated += 1

    return {
        "relationships_total": len(graph.get("relationships") or []),
        "updated": updated,
        "skipped_already_set": skipped_already_set,
        "skipped_no_signal": skipped_no_signal,
    }


def normalize_categories(args) -> int:
    graph = _read_graph()
    report = _normalize_categories_in_graph(graph)
    report["dry_run"] = not args.confirm
    if args.confirm:
        _write_graph(graph)
    print(json.dumps(report, indent=2))
    return 0


def _normalize_categories_in_graph(graph: dict) -> dict:
    """Pure graph-mutation core of normalize-categories -- no file I/O, unit-
    testable against a synthetic graph (same reasoning as
    _merge_brand_entities_in_graph and _backfill_deployment_claim_types_in_graph).

    `category` is free text in the schema (system/schemas/
    ecosystem_intelligence.schema.json), not an enum -- and _norm_category()'s
    alias table is the only thing keeping it from fragmenting, since every
    ingestion path (migrate-workbook, import-phase2-evidence) runs the raw
    category text through it once at write time and never revisits it.
    Confirmed live 2026-08-05: the Phase 2 vendor-first project's 14 research
    batches used their own free-text category labels per record rather than
    a single fixed string per category, producing 39 distinct category
    values in the graph where ~18 canonical ones were intended (e.g. "kds"
    and "kds_kitchen_ops" as two separate buckets for the same real
    category). _norm_category()'s alias table was extended in this same fix
    to catch these prospectively; this function re-runs every existing
    relationship's *already-normalized* category back through the (now
    extended) alias table and, for anything that maps to something new,
    rewrites category + regenerates the id (which embeds the category slug)
    -- merging into an existing relationship at the same new id via the same
    source_assertions merge logic _merge_brand_entities_in_graph uses, since
    two previously-distinct category buckets can now collapse onto the same
    brand+category+vendor_role+vendor edge."""
    changed = 0
    merged = 0
    unchanged = 0
    category_changes: dict[str, str] = {}

    # Snapshot the relationships list before mutating it in place, since
    # renaming one relationship's id can make a later one's "does this id
    # already exist" check see the rename rather than the original.
    for rel in list(graph.get("relationships") or []):
        if rel not in graph["relationships"]:
            continue  # already removed by an earlier merge in this pass
        old_category = rel.get("category")
        new_category = _norm_category(old_category)
        if new_category == old_category:
            unchanged += 1
            continue

        category_changes[old_category or ""] = new_category or ""
        # Rebuild the id from its known component fields rather than
        # substring-replacing the old category slug inside the existing id
        # string -- a short slug like "kds" can also occur inside the
        # brand_id or vendor_id portions of the same string, and a plain
        # .replace() would silently corrupt the wrong occurrence.
        new_id = (
            f"rel-{rel.get('from_entity_id')}-{_slug(new_category or '')}-"
            f"{_slug(rel.get('vendor_role') or 'unknown')}-{rel.get('to_entity_id')}"
        )

        existing = next(
            (r for r in graph["relationships"] if r["id"] == new_id and r is not rel), None,
        )
        rel["category"] = new_category
        if existing is not None:
            existing["source_assertions"] = _merge_source_assertions(
                existing.get("source_assertions"), rel.get("source_assertions") or [],
            )
            existing["sources"] = sorted(set((existing.get("sources") or []) + (rel.get("sources") or [])))
            existing["updated_at"] = _now()
            graph["relationships"].remove(rel)
            merged += 1
        else:
            rel["id"] = new_id
            rel["updated_at"] = _now()
            changed += 1

    return {
        "relationships_total": len(graph.get("relationships") or []) + merged,
        "changed": changed,
        "merged_into_existing": merged,
        "unchanged": unchanged,
        "category_remappings_applied": category_changes,
    }


def query_vendor(args) -> int:
    graph = _read_graph()
    by_id = _index_by_id(graph["entities"])
    vendor_text = args.vendor.lower()
    category = _norm_category(args.category) if args.category else None
    rows = []
    for rel in graph["relationships"]:
        vendor = by_id.get(rel.get("to_entity_id"), {})
        if vendor_text not in str(vendor.get("name", "")).lower() and vendor_text not in str(rel.get("to_entity_id", "")).lower():
            continue
        if category and rel.get("category") != category:
            continue
        brand = by_id.get(rel.get("from_entity_id"), {})
        attrs = brand.get("attributes") or {}
        deploy = rel.get("deployment") or {}
        rows.append(
            {
                "brand": brand.get("name"),
                "segment": attrs.get("segment"),
                "unit_count": attrs.get("unit_count"),
                "sales": attrs.get("system_sales"),
                "auv": attrs.get("auv"),
                "vendor_category": rel.get("category"),
                "vendor_role": rel.get("vendor_role", "unknown"),
                "product": rel.get("product"),
                "deployment_status": deploy.get("stage"),
                "deployment_scope": deploy.get("scope"),
                "geography": rel.get("geography") or deploy.get("geography"),
                "channel": rel.get("channel") or deploy.get("channel"),
                "service_role": rel.get("service_role") or deploy.get("service_role"),
                "deployment_claim_type": rel.get("deployment_claim_type") or deploy.get("deployment_claim_type"),
                "customer_operator": rel.get("customer_operator") or deploy.get("customer_operator"),
                "scope_unit_count": rel.get("scope_unit_count") or deploy.get("scope_unit_count"),
                "deployed_units": deploy.get("deployed_units"),
                "penetration_pct": deploy.get("penetration_pct"),
                "estimated_franchise_groups": deploy.get("estimated_franchise_groups"),
                "geographic_concentration": deploy.get("geographic_concentration"),
                "corporate_vs_franchise": deploy.get("corporate_vs_franchise"),
                "relationship_classification": rel.get("relationship_classification"),
                "risk": rel.get("risk"),
                "evidence_posture": rel.get("evidence_posture", "unknown"),
                "interpretation_scope": rel.get("interpretation_scope", ""),
                "confidence": (rel.get("confidence") or {}).get("level"),
                "sources": rel.get("sources") or [],
                "relationship_coverage": "unknown",
                "strategic_implication": rel.get("strategic_note"),
            }
        )

    estimated_total_stores = sum(
        row["deployed_units"] for row in rows if isinstance(row.get("deployed_units"), (int, float))
    )
    classification_breakdown: dict[str, int] = {}
    for row in rows:
        classification = row.get("relationship_classification")
        level_name = classification["level_name"] if classification else "unclassified"
        classification_breakdown[level_name] = classification_breakdown.get(level_name, 0) + 1

    print(json.dumps({
        "count": len(rows),
        "estimated_total_stores": estimated_total_stores,
        "classification_breakdown": classification_breakdown,
        "items": rows,
    }, indent=2))
    return 0


def brand_vendor_relationships(graph: dict, by_id: dict[str, dict], entity_id: str) -> list[dict]:
    """Every uses_vendor_for_category-shaped relationship from `entity_id`,
    joined against the vendor entity's name. Shared by query_brand (CLI)
    and system/scripts/team_tech_stack.py's read endpoint, so the two never
    drift into separately-maintained copies of this join."""
    vendor_rels = []
    for rel in graph["relationships"]:
        if rel.get("from_entity_id") != entity_id:
            continue
        vendor = by_id.get(rel.get("to_entity_id"), {})
        deploy = rel.get("deployment") or {}
        vendor_rels.append({
            "relationship_id": rel["id"],
            "vendor": vendor.get("name"),
            "vendor_id": rel.get("to_entity_id"),
            "category": rel.get("category"),
            "vendor_role": rel.get("vendor_role", "unknown"),
            "product": rel.get("product"),
            "status": rel.get("status"),
            "deployment_stage": deploy.get("stage"),
            "deployment_scope": deploy.get("scope"),
            "geography": rel.get("geography") or deploy.get("geography"),
            "channel": rel.get("channel") or deploy.get("channel"),
            "service_role": rel.get("service_role") or deploy.get("service_role"),
            "deployment_claim_type": rel.get("deployment_claim_type") or deploy.get("deployment_claim_type"),
            "customer_operator": rel.get("customer_operator") or deploy.get("customer_operator"),
            "scope_unit_count": rel.get("scope_unit_count") or deploy.get("scope_unit_count"),
            "deployed_units": deploy.get("deployed_units"),
            "penetration_pct": deploy.get("penetration_pct"),
            "estimated_franchise_groups": deploy.get("estimated_franchise_groups"),
            "geographic_concentration": deploy.get("geographic_concentration"),
            "corporate_vs_franchise": deploy.get("corporate_vs_franchise"),
            "relationship_classification": rel.get("relationship_classification"),
            "evidence_posture": rel.get("evidence_posture"),
            "confidence": (rel.get("confidence") or {}).get("level"),
            "risk": rel.get("risk"),
            "sources": rel.get("sources"),
            "strategic_note": rel.get("strategic_note"),
            "last_modified_by": rel.get("last_modified_by"),
        })
    return vendor_rels


def vendor_export_rows(graph: dict | None = None) -> list[dict]:
    """Every tracked vendor entity, one row each -- the canonical vendor
    list Todd asked to download (2026-09-01). is_tracked_competitor cross-
    references system/competitor_intelligence/'s own registry (loaded
    lazily here so this module doesn't need a hard import-time dependency
    on that one) via each competitor's real vendor_entity_id link, not a
    name guess."""
    graph = graph or _read_graph()
    by_id = _index_by_id(graph.get("entities") or [])

    competitor_vendor_ids: set[str] = set()
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import competitor_intelligence_common as cic
        reg = cic.load_registry()
        for entry in reg.get("registry", []):
            slug = entry.get("competitor_slug", "")
            comp_path = cic.competitor_dir(slug) / "competitor.json"
            comp = cic.load_json(comp_path) if comp_path.exists() else None
            vendor_entity_id = (comp or {}).get("vendor_entity_id")
            if vendor_entity_id:
                competitor_vendor_ids.add(vendor_entity_id)
    except Exception:  # noqa: BLE001
        pass  # never block the export over an optional cross-reference

    brand_counts: dict[str, int] = {}
    for rel in graph.get("relationships") or []:
        if rel.get("relationship_type") != "uses_vendor_for_category":
            continue
        vid = rel.get("to_entity_id")
        if vid:
            brand_counts[vid] = brand_counts.get(vid, 0) + 1

    rows = []
    for entity in graph.get("entities") or []:
        if entity.get("entity_type") != "vendor":
            continue
        rows.append({
            "vendor_id": entity["id"],
            "name": entity.get("name"),
            "aliases": ", ".join(entity.get("aliases") or []),
            "primary_category": (entity.get("attributes") or {}).get("primary_category"),
            "status": entity.get("status"),
            "brand_relationship_count": brand_counts.get(entity["id"], 0),
            "is_tracked_competitor": entity["id"] in competitor_vendor_ids,
            "created_at": entity.get("created_at"),
            "updated_at": entity.get("updated_at"),
        })
    rows.sort(key=lambda r: (r["name"] or "").lower())
    return rows


def query_brand(args) -> int:
    """Return full profile for a brand: entity, vendor relationships, signals, assessments."""
    graph = _read_graph()
    by_id = _index_by_id(graph["entities"])
    name_text = args.brand.lower()

    matches = [
        e for e in graph["entities"]
        if name_text in e["name"].lower() or name_text in e["id"]
    ]
    if not matches:
        print(json.dumps({"error": f"No entity found matching '{args.brand}'"}))
        return 1
    # Prefer exact name match; otherwise accept first partial match (or flag ambiguity).
    exact = [m for m in matches if m["name"].lower() == name_text]
    if len(matches) > 1 and not exact:
        print(json.dumps({
            "error": f"Ambiguous: {len(matches)} partial matches — be more specific.",
            "candidates": [m["name"] for m in matches[:10]],
        }))
        return 1
    entity = exact[0] if exact else matches[0]

    vendor_rels = brand_vendor_relationships(graph, by_id, entity["id"])

    signals = [s for s in graph["signals"] if entity["id"] in (s.get("entities") or [])]
    assessments = [a for a in graph["assessments"] if a.get("entity_id") == entity["id"]]
    user_rel = [u for u in graph.get("user_relevance", []) if u.get("entity_id") == entity["id"]]
    recs = [r for r in graph.get("strategic_recommendations", []) if entity["id"] in (r.get("entities") or [])]

    # Unresolved conflicts (status "conflicting") for this brand: surfaced
    # explicitly so a CoS/GPT consumer never mistakes a contested category for
    # white space. See check_relationship_conflict() / resolve_and_upsert_relationship().
    open_conflicts = []
    for rel in graph["relationships"]:
        if rel.get("from_entity_id") != entity["id"] or rel.get("status") != "conflicting":
            continue
        incoming_vendor = by_id.get(rel.get("to_entity_id"), {})
        existing_rel = next(
            (r for r in graph["relationships"] if r.get("id") == rel.get("conflicts_with")),
            None,
        )
        incumbent_vendor = by_id.get(existing_rel.get("to_entity_id"), {}) if existing_rel else {}
        open_conflicts.append({
            "category": rel.get("category"),
            "incumbent_vendor": incumbent_vendor.get("name"),
            "incumbent_status": existing_rel.get("status") if existing_rel else None,
            "contested_vendor": incoming_vendor.get("name"),
            "contested_status": rel.get("status"),
            "note": (
                f"CONFLICT DETECTED — {incumbent_vendor.get('name')} appears to already "
                f"hold {rel.get('category')} for {entity.get('name')}. Do not treat "
                f"{incoming_vendor.get('name')}'s claim as an open opportunity until "
                f"this conflict is resolved."
            ),
        })

    # Open research requests (staleness/conflict triggers) for this brand —
    # see generate_research_requests()/update_research_requests(). Read
    # straight from the persisted queue rather than regenerating, since
    # regeneration depends on staleness_flag having been computed this cycle
    # (done by check_staleness/_run_staleness_check, not by query_brand).
    open_research_requests = []
    rr_path = core.SYSTEM_DIR / "inbox" / "ecosystem" / RESEARCH_REQUESTS_FILENAME
    if rr_path.exists():
        try:
            rr_store = json.loads(rr_path.read_text(encoding="utf-8"))
            open_research_requests = [
                r for r in rr_store.get("requests", [])
                if r.get("entity_id") == entity["id"] and r.get("status") == "open"
            ]
        except Exception:
            pass

    print(json.dumps({
        "entity": entity,
        "vendor_relationships": vendor_rels,
        "vendor_relationship_count": len(vendor_rels),
        "signals": signals,
        "assessments": assessments,
        "user_relevance": user_rel,
        "strategic_recommendations": recs,
        "open_conflicts": open_conflicts,
        "has_unresolved_conflicts": bool(open_conflicts),
        "open_research_requests": open_research_requests,
    }, indent=2))
    return 0


def query_brands(args) -> int:
    """Filter and list brand entities by segment, rank, or sales threshold."""
    graph = _read_graph()
    results = []
    for entity in graph["entities"]:
        if entity.get("entity_type") != "brand":
            continue
        attrs = entity.get("attributes") or {}

        if args.segment and args.segment.lower() not in (attrs.get("segment") or "").lower():
            continue
        if args.subsegment and args.subsegment.lower() not in (attrs.get("subsegment") or "").lower():
            continue
        if args.min_sales is not None:
            sales = attrs.get("system_sales")
            if sales is None or sales < args.min_sales:
                continue
        if args.max_rank is not None:
            rank = attrs.get("rank")
            if rank is None or rank > args.max_rank:
                continue

        results.append({
            "id": entity["id"],
            "name": entity["name"],
            "segment": attrs.get("segment"),
            "subsegment": attrs.get("subsegment"),
            "rank": attrs.get("rank"),
            "system_sales": attrs.get("system_sales"),
            "unit_count": attrs.get("unit_count"),
            "auv": attrs.get("auv"),
            "sales_delta": attrs.get("sales_delta"),
            "unit_delta": attrs.get("unit_delta"),
        })

    results.sort(key=lambda x: x.get("rank") or 9999)
    if args.limit:
        results = results[: args.limit]

    print(json.dumps({"count": len(results), "brands": results}, indent=2))
    return 0


def summary(args) -> int:
    graph = _read_graph()
    by_type: dict[str, int] = {}
    for entity in graph["entities"]:
        t = entity.get("entity_type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    by_category: dict[str, int] = {}
    by_posture: dict[str, int] = {}
    by_confidence: dict[str, int] = {}
    by_vendor_role: dict[str, int] = {}

    for rel in graph["relationships"]:
        cat = rel.get("category") or "uncategorized"
        by_category[cat] = by_category.get(cat, 0) + 1
        posture = rel.get("evidence_posture") or "unknown"
        by_posture[posture] = by_posture.get(posture, 0) + 1
        conf = (rel.get("confidence") or {}).get("level") or "unknown"
        by_confidence[conf] = by_confidence.get(conf, 0) + 1
        role = rel.get("vendor_role") or "unknown"
        by_vendor_role[role] = by_vendor_role.get(role, 0) + 1

    print(json.dumps(
        {
            "entities": len(graph["entities"]),
            "relationships": len(graph["relationships"]),
            "signals": len(graph["signals"]),
            "assessments": len(graph["assessments"]),
            "sources": len(graph["sources"]),
            "user_relevance": len(graph.get("user_relevance", [])),
            "strategic_recommendations": len(graph.get("strategic_recommendations", [])),
            "entity_types": by_type,
            "relationship_categories": by_category,
            "relationship_evidence_posture": by_posture,
            "relationship_confidence": by_confidence,
            "relationship_vendor_roles": by_vendor_role,
        },
        indent=2,
    ))
    return 0


def promote_posture(args) -> int:
    """Advance a relationship's evidence_posture up the ladder when corroborating evidence exists.

    Rules:
    - Only forward ladder transitions are permitted: provisional → partially_substantiated → substantiated.
    - Setting conflicting or refuted must be done via ingest-vendors (adding a contradicting source row).
    - A corroborating source (title + type) is required — promotion without evidence is rejected.
    - Each promotion is audit-logged as a mutation_executed event.
    """
    graph = _read_graph()
    rel_id = args.relationship_id
    new_posture = args.new_posture
    source_title = args.source_title
    source_type_raw = args.source_type or "unsourced_spreadsheet"
    source_type = _norm_key(source_type_raw)
    source_url = getattr(args, "source_url", None) or None

    if new_posture not in POSTURE_LADDER:
        print(json.dumps({
            "error": f"Invalid posture '{new_posture}'. Ladder: {POSTURE_LADDER}. "
                     "For conflicting/refuted use ingest-vendors with an explicit row."
        }))
        return 1

    rels = _index_by_id(graph["relationships"])
    rel = rels.get(rel_id)
    if not rel:
        print(json.dumps({"error": f"Relationship not found: {rel_id}"}))
        return 1

    current_posture = rel.get("evidence_posture", "unknown")
    if current_posture in {"conflicting", "refuted"}:
        print(json.dumps({
            "error": f"Relationship is '{current_posture}'. Conflicting/refuted posture cannot be "
                     "promoted — resolve the conflict first via a new ingest-vendors pass."
        }))
        return 1

    current_idx = POSTURE_LADDER.index(current_posture) if current_posture in POSTURE_LADDER else -1
    new_idx = POSTURE_LADDER.index(new_posture)
    if new_idx <= current_idx:
        print(json.dumps({
            "error": f"'{new_posture}' is not a forward promotion from '{current_posture}'."
        }))
        return 1

    # Register the corroborating source.
    schema_quality, _, _ = SOURCE_QUALITY_MODEL.get(source_type, ("weak", "provisional", "low"))
    corroboration_source_id = f"src-{_slug(source_title)}"
    _upsert_source(graph, {
        "id": corroboration_source_id,
        "source_type": source_type,
        "title": source_title,
        "url": source_url,
        "path": None,
        "published_at": None,
        "captured_at": _now(),
        "quality": schema_quality,
        "notes": f"Corroborating source used to promote posture from {current_posture} to {new_posture}.",
    })
    if corroboration_source_id not in rel.get("sources", []):
        rel.setdefault("sources", []).append(corroboration_source_id)

    rel["evidence_posture"] = new_posture
    rel["updated_at"] = _now()

    if args.dry_run:
        print(json.dumps({
            "dry_run": True,
            "relationship_id": rel_id,
            "from_posture": current_posture,
            "to_posture": new_posture,
            "corroborating_source": source_title,
        }, indent=2))
        return 0

    _write_graph(graph)

    audit_log.append_event(
        event_type="mutation_executed",
        item_summary=(
            f"Posture promoted: {rel_id} | {current_posture} → {new_posture} | "
            f"corroborated by: {source_title} ({source_type})"
        ),
        reason=f"Corroborating evidence added: {source_type}",
        outcome=f"evidence_posture updated to {new_posture}",
        data_class="intelligence",
        retention_class=ECOSYSTEM_RETENTION_CLASS,
        extra={"relationship_id": rel_id, "from_posture": current_posture, "to_posture": new_posture},
    )
    _log_blue_sheet_coverage(
        rel.get("from_entity_id"), mutation_type="promote_posture",
        detail=f"{rel_id}: {current_posture} -> {new_posture}",
    )
    _sync_blue_sheet_technology_stack(
        rel.get("from_entity_id"), category=rel.get("category"), new_posture=new_posture,
        source_title=source_title, source_url=source_url,
    )

    print(json.dumps({
        "relationship_id": rel_id,
        "from_posture": current_posture,
        "to_posture": new_posture,
        "corroborating_source": source_title,
        "source_type": source_type,
    }, indent=2))
    return 0


# Categories treated as sticky tech — once substantiated, confidence.review_after is cleared.
STICKY_TECH_CATEGORIES = {"pos", "payments", "back_office", "erp", "back_office_accounting"}

# RB-2026-08-27 — Account Background Brief: per-fact-type revalidation cadence,
# in days. None means the fact does not go stale absent contradicting
# evidence (e.g. a founding year). Generalizes the STICKY_TECH_CATEGORIES
# idea (some things, once confirmed, stop needing reverification) beyond
# just technology categories, for use by account_background_brief.py's
# freshness assessment over blue_sheets/ account.json field-objects.
# Deliberately additive — does not change _check_staleness_on_graph's
# existing, already-tested ecosystem-relationship staleness logic.
FACT_TYPE_CADENCE_DAYS: dict[str, int | None] = {
    "founding_year": None,
    "headquarters": None,
    "segment": None,
    "footprint_history": None,
    "ownership": 365,
    "ceo_or_leadership": 90,
    "sales_figures": 180,
    "same_store_sales": 90,
    "pos_platform": 120,
    "payments_provider": 120,
    "loyalty_platform": 120,
    "digital_ordering_platform": 120,
    "kds_platform": 180,
    "menu_board_platform": 180,
    "buying_influence_role": 90,
    "opportunity_stage": 30,
    "strategic_narrative": 90,
}
DEFAULT_FACT_CADENCE_DAYS = 90


def fact_is_stale(fact_type: str, as_of: str | None, today: date | None = None) -> bool:
    """True if a field-object's as_of date is older than its fact type's
    revalidation cadence. A missing as_of is treated as stale (nothing to
    trust an age claim on)."""
    if not as_of:
        return True
    cadence = FACT_TYPE_CADENCE_DAYS.get(fact_type, DEFAULT_FACT_CADENCE_DAYS)
    if cadence is None:
        return False
    try:
        as_of_date = date.fromisoformat(as_of)
    except ValueError:
        return True
    return (( today or date.today()) - as_of_date).days > cadence

# Signal classes eligible for CoS activation.
ACTIVATION_SIGNAL_CLASSES = {
    "leadership_change",
    "rfp_cycle_signal",
    "extreme_pain",
    "vendor_displacement",
}

# Signal classes eligible for interrupt (subset of activation).
INTERRUPT_SIGNAL_CLASSES = ACTIVATION_SIGNAL_CLASSES


def _check_staleness_on_graph(graph: dict, today) -> dict:
    """Pure-function core of staleness checking — mutates graph dict in place and returns it.

    Extracted for testability: check_staleness() wraps this with disk I/O and args handling.

    Rules:
    - Sets staleness_flag=True on relationships where confidence.review_after is past and
      evidence_posture is not 'substantiated'.
    - Clears staleness_flag on substantiated + sticky-tech relationships (locked).
    """
    for rel in graph.get("relationships", []):
        posture = rel.get("evidence_posture", "unknown")
        category = rel.get("category") or ""
        conf = rel.get("confidence") or {}
        review_after_str = conf.get("review_after")

        if posture == "substantiated" and category in STICKY_TECH_CATEGORIES:
            rel["staleness_flag"] = False
            continue

        if not review_after_str:
            # No review_after set — nothing to be overdue against.
            rel["staleness_flag"] = False
            continue

        try:
            review_after = date.fromisoformat(review_after_str)
        except ValueError:
            continue

        # Explicit clear as well as set: a relationship re-verified since the
        # last check (posture now substantiated, or review_after pushed into
        # the future) must not keep carrying a stale True from a prior run —
        # generate_research_requests()'s auto-resolve depends on this.
        rel["staleness_flag"] = review_after < today and posture != "substantiated"
    return graph


def check_staleness(args) -> int:
    """Scan all relationships and flag those whose confidence.review_after date has passed.

    Sets staleness_flag=True on any relationship where:
    - confidence.review_after is set and is in the past
    - evidence_posture is not 'substantiated'

    Clears staleness_flag (sets to False) on relationships that are substantiated
    regardless of review_after date — substantiated sticky-tech relationships are locked.
    """
    graph = _read_graph()
    today = date.today()
    # Apply pure-function staleness logic first.
    _check_staleness_on_graph(graph, today)
    flagged = []
    cleared = []

    for rel in graph.get("relationships", []):
        posture = rel.get("evidence_posture", "unknown")
        category = rel.get("category") or ""
        conf = rel.get("confidence") or {}
        review_after_str = conf.get("review_after")

        if posture == "substantiated" and category in STICKY_TECH_CATEGORIES:
            # Track cleared records for audit output.
            if not rel.get("staleness_flag"):
                rel["updated_at"] = _now()
                cleared.append(rel["id"])
            continue

        if review_after_str and rel.get("staleness_flag"):
            try:
                review_after = date.fromisoformat(review_after_str)
            except ValueError:
                continue
            rel["updated_at"] = _now()
            flagged.append({
                "relationship_id": rel["id"],
                "from_entity_id": rel.get("from_entity_id"),
                "to_entity_id": rel.get("to_entity_id"),
                "category": category,
                "evidence_posture": posture,
                "review_after": review_after_str,
                "days_overdue": (today - review_after).days,
            })

    if not args.dry_run:
        _write_graph(graph)
        audit_log.append_event(
            event_type="mutation_executed",
            item_summary=f"check-staleness: {len(flagged)} relationships flagged, {len(cleared)} cleared",
            reason="Scheduled staleness check",
            outcome=f"staleness_flag set on {len(flagged)} records",
            data_class="intelligence",
            retention_class=ECOSYSTEM_RETENTION_CLASS,
        )

    result = {
        "dry_run": args.dry_run,
        "checked": len(graph.get("relationships", [])),
        "flagged": len(flagged),
        "cleared": len(cleared),
        "stale_records": flagged,
    }
    print(json.dumps(result, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Research Intelligence Engine Phase 2 — staleness/conflict → research request
# ---------------------------------------------------------------------------
#
# Phase 1 (check_relationship_conflict) stops the graph from silently picking
# a winner between competing vendor claims. Phase 2 turns "we know this is
# unresolved" into an actionable, persistent research request instead of a
# passive flag that resets every brief cycle. Three triggers, matching the
# architectural directive's trigger-condition list (2026-07-20):
#   - staleness_flag=True   — "existing intelligence is stale or lacks a
#     verification date." (generate_research_requests)
#   - status="conflicting"  — "new information conflicts with existing
#     canonical intelligence" (Phase 1's unresolved-conflict output;
#     generate_research_requests).
#   - vendor_verification_queue.json open items — vendor-claimed weak signals
#     (LinkedIn vendor posts) awaiting corroboration; previously a write-only
#     queue nothing read (generate_weak_evidence_requests).
# Requests persist with an open/resolved lifecycle in research_requests.json
# — unlike staleness_flags/verification_queue, which regenerate fresh (and
# get silently truncated) every brief cycle with no memory of what was
# already surfaced.

RESEARCH_REQUESTS_FILENAME = "research_requests.json"


def _research_request_priority(trigger: str, days_overdue: int | None) -> str:
    if trigger == "conflict":
        # A CoS decision could be actively wrong while a conflict sits
        # unresolved — always urgent, unlike staleness which is a spectrum.
        return "high"
    if days_overdue is None:
        return "low"
    if days_overdue > 90:
        return "high"
    if days_overdue > 30:
        return "medium"
    return "low"


def generate_research_requests(graph: dict, by_id: dict[str, dict] | None = None) -> list[dict]:
    """Derive open research requests from the graph's current staleness/conflict state.

    Pure function — does not read or write research_requests.json. Assumes the
    caller has already run staleness detection this cycle (i.e.
    relationship["staleness_flag"] reflects the current state), which both
    check_staleness() and ecosystem_brief._run_staleness_check() set on the
    same relationship dicts in place.
    """
    if by_id is None:
        by_id = _index_by_id(graph.get("entities") or [])
    today = date.today()
    requests: list[dict] = []

    for rel in graph.get("relationships", []):
        rel_id = rel.get("id")
        category = rel.get("category")
        brand = by_id.get(rel.get("from_entity_id"), {})
        vendor = by_id.get(rel.get("to_entity_id"), {})
        brand_name = brand.get("name") or rel.get("from_entity_id")
        vendor_name = vendor.get("name") or rel.get("to_entity_id")

        if rel.get("staleness_flag"):
            review_after_str = ((rel.get("confidence") or {}).get("review_after"))
            days_overdue = None
            if review_after_str:
                try:
                    days_overdue = (today - date.fromisoformat(review_after_str)).days
                except ValueError:
                    days_overdue = None
            requests.append({
                "id": f"rr-{rel_id}-staleness",
                "trigger": "staleness",
                "relationship_id": rel_id,
                "entity_id": rel.get("from_entity_id"),
                "entity_name": brand_name,
                "vendor_id": rel.get("to_entity_id"),
                "vendor_name": vendor_name,
                "category": category,
                "question": (
                    f"Is {vendor_name} still {brand_name}'s {category} vendor? "
                    f"No re-verification since {review_after_str or 'unknown'}"
                    + (f" ({days_overdue} days overdue)." if days_overdue is not None else ".")
                ),
                "priority": _research_request_priority("staleness", days_overdue),
                "reason": "evidence_posture below substantiated past its review_after date",
            })

        if rel.get("status") == "conflicting":
            existing_rel = next(
                (r for r in graph.get("relationships", []) if r.get("id") == rel.get("conflicts_with")),
                None,
            )
            incumbent = by_id.get((existing_rel or {}).get("to_entity_id"), {})
            requests.append({
                "id": f"rr-{rel_id}-conflict",
                "trigger": "conflict",
                "relationship_id": rel_id,
                "entity_id": rel.get("from_entity_id"),
                "entity_name": brand_name,
                "vendor_id": rel.get("to_entity_id"),
                "vendor_name": vendor_name,
                "category": category,
                "question": (
                    f"Which vendor actually holds {category} for {brand_name}: "
                    f"{incumbent.get('name') or 'the existing incumbent'} (existing) or "
                    f"{vendor_name} (new claim)? Conflict unresolved."
                ),
                "priority": _research_request_priority("conflict", None),
                "reason": "unresolved vendor/category conflict — see check_relationship_conflict",
            })

    return requests


def generate_weak_evidence_requests() -> list[dict]:
    """Fold vendor_verification_queue.json's open items into the same
    research-request shape as staleness/conflict triggers.

    That queue is written by linkedin_freshness_bridge.py whenever a
    LinkedIn vendor post claims a customer relationship — but nothing reads
    it; it is a write-only queue orphaned from the daily brief (found while
    building this Phase 2 pipeline, 2026-07-20). Reusing generate/update
    research_requests' existing brief-surfacing and query_brand plumbing
    means these weak signals finally reach the CoS instead of silently
    accumulating unread.
    """
    path = core.SYSTEM_DIR / "inbox" / "ecosystem" / "vendor_verification_queue.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    requests: list[dict] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict) or item.get("status") != "open":
            continue
        vendor_name = item.get("vendor")
        brand_name = item.get("customer_brand")
        checks = item.get("recommended_checks") or []
        requests.append({
            "id": f"rr-{item.get('task_id')}-weak-evidence",
            "trigger": "weak_evidence",
            "relationship_id": item.get("relationship_id"),
            "entity_id": f"brand-{_slug(brand_name)}" if brand_name else None,
            "entity_name": brand_name,
            "vendor_id": f"vendor-{_slug(vendor_name)}" if vendor_name else None,
            "vendor_name": vendor_name,
            "category": item.get("category"),
            "question": (
                f"{vendor_name} claimed {brand_name} as a customer via LinkedIn post "
                f"(unconfirmed). {checks[0] if checks else 'Needs primary-source corroboration.'}"
            ),
            "priority": "medium",
            "reason": item.get("weak_signal_classification") or "vendor_claimed weak signal awaiting corroboration",
        })
    return requests


def update_research_requests(graph: dict) -> dict:
    """Reconcile research_requests.json against the graph's current trigger state.

    Opens new requests, leaves already-open ones untouched (preserves original
    detected_at so age/priority stays meaningful), and auto-resolves any
    previously-open request whose trigger condition no longer holds (the
    relationship was re-verified or the conflict was resolved elsewhere).
    """
    # Lives alongside interrupt_queue.jsonl / vendor_verification_queue.json —
    # the established location for operational queue files (see
    # linkedin_freshness_bridge.py's VENDOR_VERIFICATION_QUEUE_PATH).
    # vendor_verification_queue.json's open items are folded in below via
    # generate_weak_evidence_requests() rather than duplicated here.
    path = core.SYSTEM_DIR / "inbox" / "ecosystem" / RESEARCH_REQUESTS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        store = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"requests": []}
    except Exception:
        store = {"requests": []}
    existing = {r["id"]: r for r in store.get("requests", []) if isinstance(r, dict)}

    current = generate_research_requests(graph) + generate_weak_evidence_requests()
    current_ids = {r["id"] for r in current}
    now = _now()

    opened = 0
    for req in current:
        prior = existing.get(req["id"])
        if prior is None:
            req["status"] = "open"
            req["detected_at"] = now
            existing[req["id"]] = req
            opened += 1
        else:
            # Refresh the derived fields (priority may have escalated) but
            # keep the original detection timestamp and any resolution history.
            prior.update({k: v for k, v in req.items() if k not in ("status", "detected_at")})

    resolved = 0
    for req_id, req in existing.items():
        if req.get("status") == "open" and req_id not in current_ids:
            req["status"] = "resolved"
            req["resolved_at"] = now
            resolved += 1

    store["requests"] = list(existing.values())
    path.write_text(json.dumps(store, indent=2, default=str) + "\n", encoding="utf-8")

    open_requests = [r for r in store["requests"] if r.get("status") == "open"]
    return {
        "opened": opened,
        "resolved": resolved,
        "still_open": len(open_requests),
        "open_requests": open_requests,
    }


def promote_confidence(args) -> int:
    """Promote a relationship's evidence_posture and set last_verified_at / verified_by.

    Extends promote-posture with confidence model fields:
    - Sets last_verified_at to today.
    - Sets verified_by to the source ID of the corroborating source.
    - Clears staleness_flag.
    - For substantiated + sticky-tech categories, sets confidence.review_after to null (locks confidence).
    """
    graph = _read_graph()
    rel_id = args.relationship_id
    new_posture = args.new_posture
    source_title = args.source_title
    source_type_raw = args.source_type or "credible_trade_reporting"
    source_type = _norm_key(source_type_raw)
    source_url = getattr(args, "source_url", None) or None

    if new_posture not in POSTURE_LADDER:
        print(json.dumps({
            "error": f"Invalid posture '{new_posture}'. Ladder: {POSTURE_LADDER}."
        }))
        return 1

    rels = _index_by_id(graph["relationships"])
    rel = rels.get(rel_id)
    if not rel:
        print(json.dumps({"error": f"Relationship not found: {rel_id}"}))
        return 1

    current_posture = rel.get("evidence_posture", "unknown")
    if current_posture in {"conflicting", "refuted"}:
        print(json.dumps({"error": f"Relationship is '{current_posture}'. Resolve conflict first."}))
        return 1

    current_idx = POSTURE_LADDER.index(current_posture) if current_posture in POSTURE_LADDER else -1
    new_idx = POSTURE_LADDER.index(new_posture)
    if new_idx <= current_idx:
        print(json.dumps({
            "error": f"'{new_posture}' is not a forward promotion from '{current_posture}'."
        }))
        return 1

    # Register corroborating source.
    schema_quality, _, _ = SOURCE_QUALITY_MODEL.get(source_type, ("weak", "provisional", "low"))
    source_id = f"src-{_slug(source_title)}"
    _upsert_source(graph, {
        "id": source_id,
        "source_type": source_type,
        "title": source_title,
        "url": source_url,
        "path": None,
        "published_at": None,
        "captured_at": _now(),
        "quality": schema_quality,
        "notes": f"Corroborating source for confidence promotion: {current_posture} → {new_posture}.",
    })
    if source_id not in rel.get("sources", []):
        rel.setdefault("sources", []).append(source_id)

    today_str = str(date.today())
    rel["evidence_posture"] = new_posture
    rel["last_verified_at"] = today_str
    rel["verified_by"] = source_id
    rel["staleness_flag"] = False
    rel["updated_at"] = _now()

    # Lock sticky-tech substantiated relationships.
    category = rel.get("category") or ""
    if new_posture == "substantiated" and category in STICKY_TECH_CATEGORIES:
        conf = rel.setdefault("confidence", {})
        conf["review_after"] = None

    if args.dry_run:
        print(json.dumps({
            "dry_run": True,
            "relationship_id": rel_id,
            "from_posture": current_posture,
            "to_posture": new_posture,
            "last_verified_at": today_str,
            "verified_by": source_id,
            "staleness_flag": False,
            "confidence_locked": new_posture == "substantiated" and category in STICKY_TECH_CATEGORIES,
        }, indent=2))
        return 0

    _write_graph(graph)
    audit_log.append_event(
        event_type="mutation_executed",
        item_summary=(
            f"promote-confidence: {rel_id} | {current_posture} → {new_posture} | "
            f"verified by: {source_title}"
        ),
        reason=f"Corroborating evidence: {source_type}",
        outcome=f"evidence_posture={new_posture}, last_verified_at={today_str}",
        data_class="intelligence",
        retention_class=ECOSYSTEM_RETENTION_CLASS,
        extra={"relationship_id": rel_id, "from_posture": current_posture, "to_posture": new_posture},
    )
    _log_blue_sheet_coverage(
        rel.get("from_entity_id"), mutation_type="promote_confidence",
        detail=f"{rel_id}: {current_posture} -> {new_posture} (verified_by={source_id})",
    )
    _sync_blue_sheet_technology_stack(
        rel.get("from_entity_id"), category=category, new_posture=new_posture,
        source_title=source_title, source_url=source_url,
    )

    print(json.dumps({
        "relationship_id": rel_id,
        "from_posture": current_posture,
        "to_posture": new_posture,
        "last_verified_at": today_str,
        "verified_by": source_id,
        "staleness_flag": False,
        "confidence_locked": new_posture == "substantiated" and category in STICKY_TECH_CATEGORIES,
    }, indent=2))
    return 0


def watch_list_cmd(args) -> int:
    """Manage the ecosystem intelligence watch list.

    Subcommands: show, add, remove, set-priority.
    """
    graph = _read_graph()
    wl = graph.setdefault("watch_list", [])
    action = args.watch_action

    if action == "show":
        entity_map = {e["id"]: e["name"] for e in graph.get("entities", [])}
        out = []
        for entry in wl:
            out.append({
                **entry,
                "entity_name": entity_map.get(entry["entity_id"], entry["entity_id"]),
            })
        print(json.dumps({"count": len(out), "watch_list": out}, indent=2))
        return 0

    entity_id = args.entity_id
    entities_by_id = {e["id"]: e for e in graph.get("entities", [])}

    if action == "add":
        if entity_id not in entities_by_id:
            # Try slug match.
            slug = _slug(entity_id)
            matched = [e for e in graph.get("entities", []) if e["id"] == slug or _slug(e["name"]) == slug]
            if matched:
                entity_id = matched[0]["id"]
            else:
                print(json.dumps({"error": f"Entity not found: {entity_id}"}))
                return 1

        existing = next((e for e in wl if e["entity_id"] == entity_id), None)
        if existing:
            print(json.dumps({"status": "already_present", "entity_id": entity_id, "priority": existing["priority"]}))
            return 0

        priority = getattr(args, "priority", "tier_1") or "tier_1"
        entry = {
            "entity_id": entity_id,
            "priority": priority,
            "added_at": str(date.today()),
            "added_by": "user",
            "reason": getattr(args, "reason", "") or "",
            "last_signal_at": None,
            "interrupt_eligible": priority == "tier_1",
        }
        wl.append(entry)
        _write_graph(graph)
        audit_log.append_event(
            event_type="item_persisted",
            item_summary=f"Watch list add: {entity_id} at {priority}",
            reason=entry["reason"] or "user request",
            outcome="added",
            data_class="intelligence",
            retention_class=ECOSYSTEM_RETENTION_CLASS,
        )
        print(json.dumps({"status": "added", **entry}, indent=2))
        return 0

    if action == "remove":
        before = len(wl)
        graph["watch_list"] = [e for e in wl if e["entity_id"] != entity_id]
        if len(graph["watch_list"]) == before:
            print(json.dumps({"status": "not_found", "entity_id": entity_id}))
            return 1
        _write_graph(graph)
        print(json.dumps({"status": "removed", "entity_id": entity_id}))
        return 0

    if action == "set-priority":
        entry = next((e for e in wl if e["entity_id"] == entity_id), None)
        if not entry:
            print(json.dumps({"error": f"Entity not on watch list: {entity_id}"}))
            return 1
        old_priority = entry["priority"]
        entry["priority"] = args.priority
        entry["interrupt_eligible"] = args.priority == "tier_1"
        _write_graph(graph)
        print(json.dumps({
            "status": "updated",
            "entity_id": entity_id,
            "from_priority": old_priority,
            "to_priority": args.priority,
        }, indent=2))
        return 0

    print(json.dumps({"error": f"Unknown watch action: {action}"}))
    return 1


def research_requests_cmd(args) -> int:
    """Run staleness detection, then reconcile research_requests.json against
    the graph's current staleness/conflict state. See update_research_requests()."""
    graph = _read_graph()
    _check_staleness_on_graph(graph, date.today())
    result = update_research_requests(graph)
    if not args.dry_run:
        _write_graph(graph)
    print(json.dumps(result, indent=2, default=str))
    return 0


def check_interrupt_queue(args) -> int:
    """Scan recent graph signals and write qualifying items to the interrupt queue.

    Qualifying conditions (ALL must be true):
    1. Signal entities include a tier_1 watch list entity.
    2. signal_type is in INTERRUPT_SIGNAL_CLASSES.
    3. confidence.level is 'high'.
    4. event_at is within 48 hours of today.

    Qualifying signals are written to system/inbox/ecosystem/interrupt_queue.jsonl.
    Already-queued signals (by signal id) are not re-written.
    """
    graph = _read_graph()
    wl = graph.get("watch_list", [])
    tier1_ids = {e["entity_id"] for e in wl if e.get("priority") == "tier_1"}

    if not tier1_ids:
        print(json.dumps({"status": "no_tier1_watch_list_entities", "queued": 0}))
        return 0

    today = date.today()
    queue_path = core.SYSTEM_DIR / "inbox" / "ecosystem" / "interrupt_queue.jsonl"
    queue_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing queue IDs to avoid duplication.
    existing_ids: set[str] = set()
    if queue_path.exists():
        for line in queue_path.read_text().splitlines():
            try:
                existing_ids.add(json.loads(line)["signal_id"])
            except Exception:  # noqa: BLE001
                pass

    queued = []
    for sig in graph.get("signals", []):
        sig_id = sig.get("id", "")
        if sig_id in existing_ids:
            continue
        # Condition 1: touches a tier_1 entity.
        if not any(eid in tier1_ids for eid in (sig.get("entities") or [])):
            continue
        # Condition 2: qualifying signal class.
        if sig.get("signal_type") not in INTERRUPT_SIGNAL_CLASSES:
            continue
        # Condition 3: high confidence.
        if (sig.get("confidence") or {}).get("level") != "high":
            continue
        # Condition 4: within 48 hours.
        try:
            event_date = date.fromisoformat(sig.get("event_at", ""))
        except ValueError:
            continue
        if (today - event_date).days > 2:
            continue

        record = {
            "signal_id": sig_id,
            "entity_ids": sig.get("entities", []),
            "signal_type": sig.get("signal_type"),
            "summary": sig.get("summary", ""),
            "confidence": (sig.get("confidence") or {}).get("level"),
            "event_at": sig.get("event_at"),
            "detected_at": _now(),
            "acknowledged": False,
        }
        queued.append(record)
        with open(queue_path, "a") as f:
            f.write(json.dumps(record) + "\n")

    result = {"queued": len(queued), "queue_path": str(queue_path), "new_items": queued}
    print(json.dumps(result, indent=2))
    return 0


def smoke(args) -> int:
    graph = _read_graph()
    assert graph["contract"] == "rb_ecosystem_intelligence_v1"
    assert "restaurants" in graph["domain_packs"]
    # RB-2026-09-27: pass the live path + schema explicitly (see the
    # matching comment in _write_graph()) instead of --ecosystem-only,
    # so this also validates the right file under a monkeypatched
    # core.ECOSYSTEM_INTELLIGENCE_PATH.
    rc = subprocess.run(
        [sys.executable, str(VALIDATOR), str(core.ECOSYSTEM_INTELLIGENCE_PATH), "--schema", str(SCHEMA_PATH)],
        capture_output=True,
        text=True,
    )
    if rc.returncode != 0:
        sys.stderr.write(rc.stderr or rc.stdout)
        return rc.returncode
    print("OK - ecosystem intelligence smoke passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Operate the RB ecosystem intelligence artifact.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("summary")
    p.set_defaults(func=summary)

    p = sub.add_parser("query-vendor")
    p.add_argument("--vendor", required=True)
    p.add_argument("--category")
    p.set_defaults(func=query_vendor)

    p = sub.add_parser("query-brand")
    p.add_argument("--brand", required=True, help="Brand name or partial name to look up")
    p.set_defaults(func=query_brand)

    p = sub.add_parser("query-brands")
    p.add_argument("--segment", help="Filter by segment (e.g. LSR, FSR)")
    p.add_argument("--subsegment", help="Filter by subsegment (e.g. QSR, Fast Casual)")
    p.add_argument("--min-sales", dest="min_sales", type=float, help="Minimum system sales (USD)")
    p.add_argument("--max-rank", dest="max_rank", type=float, help="Maximum Technomic rank")
    p.add_argument("--limit", type=int, default=0, help="Limit result count (0 = no limit)")
    p.set_defaults(func=query_brands)

    p = sub.add_parser("ingest-restaurants")
    p.add_argument("path")
    p.add_argument("--title")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=ingest_restaurants)

    p = sub.add_parser("ingest-vendors")
    p.add_argument("path")
    p.add_argument(
        "--source-type",
        dest="source_type",
        choices=list(SOURCE_QUALITY_MODEL.keys()),
        default=None,
        help="Override source quality class for all rows in this file.",
    )
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=ingest_vendors)

    p = sub.add_parser("migrate-workbook")
    p.add_argument("path", help="Path to the research workbook .xlsx.")
    p.add_argument(
        "--confirm", action="store_true",
        help="Actually write to the graph. Without this flag, produces a reconciliation "
             "report only (system/_snapshots/workbook_migration_report-*.json) and never touches ecosystem_intelligence.json.",
    )
    p.set_defaults(func=migrate_workbook)

    p = sub.add_parser("import-phase2-evidence")
    p.add_argument("path", help="Path to a phase2_*_evidence_draft_*.json staging file.")
    p.add_argument(
        "--skip", action="append", default=[],
        help="'<vendor>::<brand>' pair to exclude from this import (repeatable).",
    )
    p.add_argument(
        "--confirm", action="store_true",
        help="Actually write to the graph. Without this flag, produces a reconciliation "
             "report only (system/_snapshots/phase2_import_report-*.json) and never touches ecosystem_intelligence.json.",
    )
    p.set_defaults(func=import_phase2_evidence)

    p = sub.add_parser("merge-brand-entities")
    p.add_argument("--into", required=True, help="Canonical entity id to merge duplicates into.")
    p.add_argument(
        "--duplicate", action="append", required=True, dest="duplicate",
        help="Duplicate entity id to merge into --into (repeatable).",
    )
    p.add_argument(
        "--confirm", action="store_true",
        help="Actually write to the graph. Without this flag, produces a report only "
             "and never touches ecosystem_intelligence.json.",
    )
    p.set_defaults(func=merge_brand_entities)

    p = sub.add_parser("backfill-deployment-claim-type")
    p.add_argument(
        "--force", action="store_true",
        help="Recompute even for relationships that already have a deployment_claim_type set.",
    )
    p.add_argument(
        "--confirm", action="store_true",
        help="Actually write to the graph. Without this flag, produces a report only "
             "and never touches ecosystem_intelligence.json.",
    )
    p.set_defaults(func=backfill_deployment_claim_type)

    p = sub.add_parser("normalize-categories")
    p.add_argument(
        "--confirm", action="store_true",
        help="Actually write to the graph. Without this flag, produces a report only "
             "and never touches ecosystem_intelligence.json.",
    )
    p.set_defaults(func=normalize_categories)

    p = sub.add_parser("promote-posture")
    p.add_argument("--relationship-id", dest="relationship_id", required=True)
    p.add_argument("--new-posture", dest="new_posture", required=True,
                   choices=POSTURE_LADDER)
    p.add_argument("--source-title", dest="source_title", required=True,
                   help="Title of the corroborating source.")
    p.add_argument("--source-type", dest="source_type",
                   choices=list(SOURCE_QUALITY_MODEL.keys()),
                   default="credible_trade_reporting")
    p.add_argument("--source-url", dest="source_url", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=promote_posture)

    p = sub.add_parser("check-staleness")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=check_staleness)

    p = sub.add_parser("research-requests")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=research_requests_cmd)

    p = sub.add_parser("promote-confidence")
    p.add_argument("--relationship-id", dest="relationship_id", required=True)
    p.add_argument("--new-posture", dest="new_posture", required=True, choices=POSTURE_LADDER)
    p.add_argument("--source-title", dest="source_title", required=True)
    p.add_argument("--source-type", dest="source_type",
                   choices=list(SOURCE_QUALITY_MODEL.keys()),
                   default="credible_trade_reporting")
    p.add_argument("--source-url", dest="source_url", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=promote_confidence)

    p = sub.add_parser("watch-list")
    watch_sub = p.add_subparsers(dest="watch_action", required=True)

    wp = watch_sub.add_parser("show")
    p.set_defaults(func=watch_list_cmd)
    wp.set_defaults(func=watch_list_cmd, watch_action="show")

    wp = watch_sub.add_parser("add")
    wp.add_argument("entity_id")
    wp.add_argument("--priority", choices=["tier_1", "tier_2", "tier_3"], default="tier_1")
    wp.add_argument("--reason", default="")
    wp.set_defaults(func=watch_list_cmd, watch_action="add")

    wp = watch_sub.add_parser("remove")
    wp.add_argument("entity_id")
    wp.set_defaults(func=watch_list_cmd, watch_action="remove")

    wp = watch_sub.add_parser("set-priority")
    wp.add_argument("entity_id")
    wp.add_argument("--priority", choices=["tier_1", "tier_2", "tier_3"], required=True)
    wp.set_defaults(func=watch_list_cmd, watch_action="set-priority")

    p = sub.add_parser("check-interrupt-queue")
    p.set_defaults(func=check_interrupt_queue)

    p = sub.add_parser("smoke")
    p.set_defaults(func=smoke)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
