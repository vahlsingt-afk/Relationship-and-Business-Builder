#!/usr/bin/env python3
"""
import_competitor_platform_research.py — RB-DEFECT-073 (2026-09-28).

The canonical-import step that RB-DEFECT-073 found missing: a deep-research
packet's JSON sidecar (system/templates/deep_research_intelligence_drop.md)
used to carry only coverage metadata (packet_id/targets/pages_reviewed/
source_ledger) -- no structured findings ever reached competitor_intelligence
or genius_capabilities. This module defines and imports the sidecar's new,
optional "findings" array (schema "rb.competitor_platform_research.v1"),
routing each finding to the correct canonical store with review-first
semantics.

Per-finding routing:
  - target "competitor:<slug>"  -> must already exist (cic.competitor_dir);
    never auto-created, never guessed. If the resolved competitor's
    display_name trips cic.is_own_company() (or the slug is literally
    "global-payments"), the finding is transparently redirected to
    "genius:parent" evidence instead of a competitor profile -- this is
    the structural fix for the confirmed live Global Payments/competitor
    misrouting risk (see migrate_global_payments_to_parent_evidence()
    below for the one-time data correction).
  - target "genius:<scope>"     -> scope must be a real
    genius_capabilities.GENIUS_EVIDENCE_SCOPES value.
  - field "vendor_claims"       -> always a labeled vendor claim, NEVER
    promoted into "strengths" regardless of confidence (acceptance
    criterion #2 of the defect).
  - field in {strengths, weaknesses, vulnerabilities, key_customers,
    product_lineage} (competitor target) -> routed through
    mutation_policy.decide() (net-new list member vs. exact duplicate,
    handled by add_extended_profile_finding()'s own dedupe) then applied
    via competitor_intelligence.add_extended_profile_finding(). Every
    decision gets a durable mutation_policy receipt, whether or not it
    was actually written -- these reconcile into morning_pipeline.py's
    existing mutation_policy_reconciliation report for free.
  - field "trends" (competitor target) -> scalar; conflicting values
    without a resolving date route to confirmation, never a silent
    overwrite.
  - field "marketplace_signals"  -> competitor_intelligence.add_competitive_
    note(category="other") / genius_capabilities.add_evidence(category=
    "other").
  - genius target, field "strengths" -> promoted to a genius_capabilities
    capability ONLY when finding_type == "independently_verified",
    confidence in {"high","critical"}, not is_inference, and scope is a
    real GENIUS_PRODUCT_LINES value (the same confidence/inference gate
    import_genius_competitive_research.py already established) --
    everything else about a genius target lands in the evidence log.

A malformed individual finding (missing required keys, unknown field/
target/confidence vocabulary) is rejected with a structured error and does
NOT block the rest of the packet (acceptance criterion #8) -- schema
validation is deliberately kept separate from mutation_policy, which
governs write decisions for well-formed findings, not payload shape.

Idempotent: system/.cache/competitor_platform_research_import_log.json
tracks (packet_id, target, field, value, source_url) hashes already
processed, same pattern as import_genius_competitive_research.py's
claim-key log -- a finding already processed (applied, queued, or
rejected) is not re-processed on a later --sweep run. A human resolving a
queued (confirmation/review-required) mutation_policy receipt does so
through the existing review surfaces (mutation_policy_reconciliation in
the morning brief, or a direct add_extended_profile_finding call); this
importer's own job is only to get well-formed net-new findings applied and
everything else durably queued, once, not to be a review UI itself.

CLI:
    python3 import_competitor_platform_research.py --file <sidecar.json> [--dry-run|--confirm]
    python3 import_competitor_platform_research.py --sweep [--dry-run|--confirm]
    python3 import_competitor_platform_research.py --audit-existing
    python3 import_competitor_platform_research.py --migrate-global-payments [--dry-run|--confirm]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import competitor_intelligence as compintel  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import genius_capabilities as gc  # noqa: E402
import mutation_policy  # noqa: E402
import hunter  # noqa: E402

SCHEMA = "rb.competitor_platform_research.v1"
SOURCE_TAG = "system:competitor_platform_research_2026-09"

DROP_DIR = core.SYSTEM_DIR / "inbox" / "chatgpt_intelligence_drop"
IMPORT_LOG_PATH = core.CACHE_DIR / "competitor_platform_research_import_log.json"
SIDECAR_MANIFEST_PATH = core.SYSTEM_DIR / "research" / "competitor_platform_research_sidecar_manifest.json"

# RB defect 2026-09-30: this importer's own outcome (applied/deduped/queued/
# rejected findings, exact canonical targets changed) was never persisted
# anywhere durable and packet-keyed -- it only ever existed as this
# process's stdout. The capture receipt for the same packet (written by
# server.py's capture-processing path) had no way to look it up, so a
# deep-research capture's receipt reported "exec_mutations: 0" while this
# importer silently applied real canonical mutations moments later.
# reconcile_deep_research_capture_receipts.py reads this file to fold the
# real outcome into the matching capture's processing_result.
IMPORT_RECEIPTS_PATH = core.CACHE_DIR / "competitor_platform_research_import_receipts.json"

# List-shaped competitor fields this importer can write to, via
# add_extended_profile_finding(). "trends" is handled separately (scalar).
# "recent_news" and "products" are deliberately NOT accepted here -- this
# importer's payload schema (§ finding fields below) doesn't model either
# one; a future extension can add them the same way vendor_extended_
# profile_ingest.py already covers "products"/"recent_news" through its
# own, narrower import path.
_COMPETITOR_LIST_FIELDS = {"products", "vendor_claims", "strengths", "weaknesses", "vulnerabilities", "key_customers", "product_lineage"}
_COMPETITOR_SCALAR_FIELDS = {"trends"}
_MARKETPLACE_SIGNAL_FIELD = "marketplace_signals"
VALID_FIELDS = _COMPETITOR_LIST_FIELDS | _COMPETITOR_SCALAR_FIELDS | {_MARKETPLACE_SIGNAL_FIELD}

VALID_CONFIDENCE = {"low", "medium", "high", "critical"}
VALID_FINDING_TYPES = {"vendor_stated", "independently_verified", "marketplace_reported", "inference"}

_REQUIRED_KEYS = ("target", "field", "value", "finding_type", "source_url", "source_type", "confidence", "observed_at")

# Genius evidence category per field -- reuses the same evidence-category
# vocabulary genius_capabilities.add_evidence() already validates against.
_GENIUS_FIELD_TO_EVIDENCE_CATEGORY = {
    "products": "other",
    "vendor_claims": "positioning",
    "strengths": "strength",
    "weaknesses": "weakness",
    "vulnerabilities": "weakness",
    "key_customers": "reference_customer",
    "product_lineage": "other",
    "marketplace_signals": "other",
    "trends": "other",
}

_CAPABILITY_MIN_CONFIDENCE = {"high", "critical"}


def _now_iso() -> str:
    return cic.now_iso()


def classify_bytes(content: bytes) -> bool:
    """Recognize a structured RBB competitor-platform baseline packet."""
    try:
        data = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(data, dict)
        and data.get("artifact_type") == "rbb_marketplace_intelligence_import"
        and isinstance(data.get("competitors"), list)
        and data.get("competitors")
    )


def _confidence_level(value: Any) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "medium"
    if score >= 95:
        return "critical"
    if score >= 80:
        return "high"
    if score >= 55:
        return "medium"
    return "low"


def _baseline_packet_findings(packet: dict) -> list[dict]:
    """Translate the legacy baseline packet into governed v1 findings."""
    observed_at = packet.get("research_access_date") or packet.get("generated_date")
    packet_id = packet.get("packet_id") or "competitor-platform-baseline"
    findings: list[dict] = []

    def add(target: str, field: str, value: str, *, finding_type: str,
            confidence: Any = 75, source_url: str | None = None,
            limitations: str | None = None) -> None:
        value = str(value or "").strip()
        if not value:
            return
        findings.append({
            "target": target,
            "field": field,
            "value": value,
            "finding_type": finding_type,
            "source_url": source_url or f"rbb-packet:{packet_id}",
            "source_type": "structured_research_packet",
            "confidence": _confidence_level(confidence),
            "observed_at": observed_at,
            "limitations_or_conflicts": limitations,
            "is_inference": finding_type == "inference",
            "is_vendor_claim": finding_type == "vendor_stated",
        })

    for competitor in packet.get("competitors") or []:
        target = competitor.get("key")
        if not isinstance(target, str):
            continue
        sources = [s for s in competitor.get("sources") or [] if isinstance(s, str) and s.strip()]
        primary_source = sources[0] if sources else None
        add(target, "vendor_claims", competitor.get("positioning"), finding_type="vendor_stated", confidence=85, source_url=primary_source)
        for product in competitor.get("verified_product_families") or []:
            if isinstance(product, dict):
                value = product.get("name")
                if product.get("category"):
                    value = f"{value} ({product['category']})"
                add(target, "products", value, finding_type="vendor_stated", confidence=90, source_url=primary_source)
        for row in competitor.get("strengths") or []:
            if not isinstance(row, dict):
                continue
            evidence_type = str(row.get("evidence_type") or "").lower()
            if "independent" in evidence_type:
                field, kind = "strengths", "independently_verified"
            elif "inference" in evidence_type:
                field, kind = "strengths", "inference"
            else:
                field, kind = "vendor_claims", "vendor_stated"
            add(target, field, row.get("observation"), finding_type=kind, confidence=row.get("confidence"), source_url=primary_source, limitations=row.get("limitations"))
        for row in competitor.get("weaknesses_and_risks") or []:
            if not isinstance(row, dict):
                continue
            evidence_type = str(row.get("evidence_type") or "").lower()
            kind = "marketplace_reported" if any(x in evidence_type for x in ("customer", "marketplace", "status")) else "inference"
            add(target, "vulnerabilities", row.get("observation"), finding_type=kind, confidence=row.get("confidence"), source_url=primary_source, limitations=row.get("limitations"))
        for row in competitor.get("conflicts") or []:
            if isinstance(row, dict):
                value = row.get("observation")
                if row.get("interpretation"):
                    value = f"{value} Interpretation: {row['interpretation']}"
                add(target, "marketplace_signals", value, finding_type="inference", confidence=row.get("confidence"), source_url=primary_source)
        for gap in competitor.get("research_gaps") or []:
            add(target, "marketplace_signals", f"Research gap: {gap}", finding_type="inference", confidence=60, source_url=primary_source)
        for old_name, new_name in (competitor.get("rbb_normalization") or {}).items():
            add(target, "product_lineage", f"{old_name} normalizes to {new_name}", finding_type="independently_verified", confidence=90, source_url=primary_source)
    return findings


# ---------------------------------------------------------------------------
# Import log (idempotency)
# ---------------------------------------------------------------------------

def _finding_key(packet_id: str, target: str, field: str, value: str, source_url: str) -> str:
    basis = f"{packet_id}\x1f{target}\x1f{field}\x1f{value}\x1f{source_url}".encode("utf-8")
    return hashlib.sha1(basis).hexdigest()[:20]


def _load_import_log() -> dict:
    if not IMPORT_LOG_PATH.exists():
        return {"processed_finding_keys": []}
    try:
        data = json.loads(IMPORT_LOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"processed_finding_keys": []}
    data.setdefault("processed_finding_keys", [])
    return data


def _save_import_log(data: dict) -> None:
    IMPORT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    IMPORT_LOG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Sidecar sweep manifest (which sidecars have already been offered to the
# importer -- distinct from deep_research_coverage.py's own manifest, since
# a sidecar with no "findings" key at all should still be marked processed
# so --sweep doesn't re-open it forever).
# ---------------------------------------------------------------------------

def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _load_sweep_manifest() -> dict:
    if SIDECAR_MANIFEST_PATH.exists():
        try:
            manifest = json.loads(SIDECAR_MANIFEST_PATH.read_text(encoding="utf-8"))
            if isinstance(manifest, dict) and isinstance(manifest.get("processed"), dict):
                return manifest
        except Exception:
            pass
    return {"processed": {}}


def _save_sweep_manifest(manifest: dict) -> None:
    SIDECAR_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    SIDECAR_MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_import_receipts() -> dict:
    if not IMPORT_RECEIPTS_PATH.exists():
        return {"receipts": {}}
    try:
        data = json.loads(IMPORT_RECEIPTS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("receipts"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"receipts": {}}


def _save_import_receipts(data: dict) -> None:
    IMPORT_RECEIPTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    IMPORT_RECEIPTS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def get_import_receipt(packet_id: str) -> dict | None:
    """Durable, packet-keyed outcome of a structured-findings import run --
    read by reconcile_deep_research_capture_receipts.py. See
    IMPORT_RECEIPTS_PATH's module-level comment for why this exists."""
    return _load_import_receipts()["receipts"].get(packet_id)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_finding(finding: dict) -> Optional[str]:
    """Returns an error string if malformed, else None. Deliberately
    separate from mutation_policy -- this is payload-shape validation, not
    a write-decision policy."""
    if not isinstance(finding, dict):
        return "finding is not an object"
    missing = [k for k in _REQUIRED_KEYS if not finding.get(k)]
    if missing:
        return f"missing required key(s): {missing}"
    target = finding["target"]
    if not (isinstance(target, str) and (target.startswith("competitor:") or target.startswith("genius:"))):
        return f"target must start with 'competitor:' or 'genius:', got {target!r}"
    if finding["field"] not in VALID_FIELDS:
        return f"field must be one of {sorted(VALID_FIELDS)}, got {finding['field']!r}"
    if finding["confidence"] not in VALID_CONFIDENCE:
        return f"confidence must be one of {sorted(VALID_CONFIDENCE)}, got {finding['confidence']!r}"
    if finding["finding_type"] not in VALID_FINDING_TYPES:
        return f"finding_type must be one of {sorted(VALID_FINDING_TYPES)}, got {finding['finding_type']!r}"
    if finding["field"] == "vendor_claims" and finding["finding_type"] not in {"vendor_stated"}:
        return "field 'vendor_claims' must have finding_type 'vendor_stated'"
    if finding["field"] in ("strengths", "weaknesses", "vulnerabilities") and finding["finding_type"] == "vendor_stated":
        return f"field {finding['field']!r} may not carry finding_type 'vendor_stated' -- use field 'vendor_claims' for vendor-stated content"
    return None


class _IdentityUnresolved(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _resolve_target(target: str) -> tuple[str, str]:
    """Returns (kind, key): ("competitor", slug) or ("genius", scope).
    Never creates a new competitor and never guesses a slug -- an
    unresolvable target raises _IdentityUnresolved, which the caller routes
    to mutation_policy's identity-ambiguity review path. Global Payments/
    Genius's own name is redirected to genius:parent even when a stray
    competitor:global-payments target somehow appears in a packet."""
    kind, _, key = target.partition(":")
    if kind == "genius":
        if key not in gc.GENIUS_EVIDENCE_SCOPES:
            raise _IdentityUnresolved(f"unknown genius scope {key!r}; valid: {sorted(gc.GENIUS_EVIDENCE_SCOPES)}")
        return "genius", key
    if kind == "competitor":
        if key == "global-payments" or cic.is_own_company(key.replace("-", " ")):
            return "genius", "parent"
        try:
            cic.competitor_dir(key)
        except FileNotFoundError:
            raise _IdentityUnresolved(f"competitor slug {key!r} is not a tracked competitor; never auto-created")
        comp = cic.load_competitor(key)["competitor"]
        if cic.is_own_company(comp.get("display_name") or ""):
            return "genius", "parent"
        return "competitor", key
    raise _IdentityUnresolved(f"target has unknown kind {kind!r}")


# ---------------------------------------------------------------------------
# Write paths
# ---------------------------------------------------------------------------

def _apply_competitor_list_finding(slug: str, finding: dict, *, dry_run: bool) -> dict:
    field = finding["field"]
    value = str(finding["value"]).strip()
    decision = mutation_policy.decide(
        source=SOURCE_TAG,
        new_value=value,
        is_set_member=True,
        observed_at=finding.get("observed_at"),
        confidence=finding.get("confidence"),
        field_name=field,
        entity_id=slug,
    )
    applied = False
    deduped = False
    if not dry_run and decision.auto_apply:
        result = compintel.add_extended_profile_finding(
            slug, field, value,
            confidence=finding.get("confidence", "medium"),
            source_url=finding.get("source_url"),
            as_of=finding.get("observed_at"),
            finding_type=finding.get("finding_type"),
            source_owner=finding.get("source_owner"),
            source_type=finding.get("source_type"),
            published_at=finding.get("published_at"),
            deployment_scope=finding.get("deployment_scope"),
            is_vendor_claim=finding.get("is_vendor_claim"),
            is_inference=finding.get("is_inference"),
            limitations_or_conflicts=finding.get("limitations_or_conflicts"),
        )
        deduped = bool(result.get("deduped"))
        applied = not deduped
    if not dry_run:
        mutation_policy.record_receipt(
            decision, artifact=f"competitor_intelligence/{slug}/competitor.json" if applied else None,
            applied=applied,
        )
    return {"status": decision.status, "applied": applied, "deduped": deduped}


def _apply_competitor_trends(slug: str, finding: dict, *, dry_run: bool) -> dict:
    value = str(finding["value"]).strip()
    comp = cic.load_competitor(slug)["competitor"]
    existing = (comp.get("trends") or {}).get("value")
    existing_date = (comp.get("trends") or {}).get("as_of")
    decision = mutation_policy.decide(
        source=SOURCE_TAG,
        new_value=value,
        existing_value=existing,
        new_date=finding.get("observed_at"),
        existing_date=existing_date,
        is_replacement=True,
        observed_at=finding.get("observed_at"),
        confidence=finding.get("confidence"),
        field_name="trends",
        entity_id=slug,
    )
    applied = False
    if not dry_run and decision.auto_apply:
        compintel.add_extended_profile_finding(
            slug, "trends", value,
            confidence=finding.get("confidence", "medium"),
            source_url=finding.get("source_url"),
            as_of=finding.get("observed_at"),
            finding_type=finding.get("finding_type"),
            source_owner=finding.get("source_owner"),
            source_type=finding.get("source_type"),
            published_at=finding.get("published_at"),
            deployment_scope=finding.get("deployment_scope"),
            is_vendor_claim=finding.get("is_vendor_claim"),
            is_inference=finding.get("is_inference"),
            limitations_or_conflicts=finding.get("limitations_or_conflicts"),
        )
        applied = True
    if not dry_run:
        mutation_policy.record_receipt(
            decision, artifact=f"competitor_intelligence/{slug}/competitor.json" if applied else None,
            applied=applied,
        )
    return {"status": decision.status, "applied": applied, "deduped": False}


def _apply_competitor_marketplace_signal(slug: str, finding: dict, *, dry_run: bool) -> dict:
    value = str(finding["value"]).strip()
    decision = mutation_policy.decide(
        source=SOURCE_TAG, new_value=value, is_set_member=True,
        observed_at=finding.get("observed_at"), confidence=finding.get("confidence"),
        field_name="marketplace_signals", entity_id=slug,
    )
    applied = False
    if not dry_run and decision.auto_apply:
        compintel.add_competitive_note(
            slug, value, category="other",
            source=finding.get("source_owner") or finding.get("source_url") or SOURCE_TAG,
            confidence=finding.get("confidence", "medium"),
        )
        applied = True
    if not dry_run:
        mutation_policy.record_receipt(
            decision, artifact=f"competitor_intelligence/{slug}/evidence.jsonl" if applied else None,
            applied=applied,
        )
    return {"status": decision.status, "applied": applied, "deduped": False}


def _is_genius_capability_eligible(scope: str, finding: dict) -> bool:
    return (
        scope in cic.GENIUS_PRODUCT_LINES
        and finding["field"] == "strengths"
        and finding["finding_type"] == "independently_verified"
        and finding["confidence"] in _CAPABILITY_MIN_CONFIDENCE
        and not finding.get("is_inference")
    )


def _apply_genius_finding(scope: str, finding: dict, *, dry_run: bool) -> dict:
    value = str(finding["value"]).strip()
    is_capability = _is_genius_capability_eligible(scope, finding)
    decision = mutation_policy.decide(
        source=SOURCE_TAG, new_value=value, is_set_member=True,
        observed_at=finding.get("observed_at"), confidence=finding.get("confidence"),
        field_name=finding["field"], entity_id=f"genius:{scope}",
    )
    applied = False
    artifact = None
    if not dry_run and decision.auto_apply:
        if is_capability:
            gc.add_capability(
                scope, value,
                why_it_matters=finding.get("limitations_or_conflicts"),
                added_by=SOURCE_TAG,
            )
            artifact = "genius_capabilities.json"
        else:
            category = _GENIUS_FIELD_TO_EVIDENCE_CATEGORY.get(finding["field"], "other")
            gc.add_evidence(
                scope, value, category=category,
                source=finding.get("source_owner") or finding.get("source_url") or SOURCE_TAG,
                confidence=finding.get("confidence", "medium"),
            )
            artifact = "genius_own_evidence.jsonl"
        applied = True
    if not dry_run:
        mutation_policy.record_receipt(decision, artifact=artifact, applied=applied)
    return {"status": decision.status, "applied": applied, "deduped": False, "is_capability": is_capability}


# ---------------------------------------------------------------------------
# Core import
# ---------------------------------------------------------------------------

def import_findings(sidecar: dict, *, packet_id: str | None = None, dry_run: bool = True) -> dict:
    packet_id = packet_id or sidecar.get("packet_id") or "unknown-packet"
    # Hunter keeps one stable research envelope while preserving this
    # importer's existing cycle-specific contract inside payload. Legacy
    # sidecars remain supported unchanged.
    research_data = sidecar
    if (
        sidecar.get("schema") == "rb.hunter_research_packet.v1"
        and sidecar.get("payload_schema") == "rb.competitor_platform_research.v1"
        and isinstance(sidecar.get("payload"), dict)
    ):
        research_data = sidecar["payload"]
    findings = research_data.get("findings") or _baseline_packet_findings(research_data)

    log = _load_import_log()
    seen: set[str] = set(log["processed_finding_keys"])
    new_keys: list[str] = []

    summary: dict[str, Any] = {
        "packet_id": packet_id,
        "findings_received": len(findings),
        "findings_malformed": 0,
        "findings_already_processed": 0,
        "applied": 0,
        "deduped": 0,
        "queued_for_review": 0,
        "identity_unresolved": 0,
        "malformed_errors": [],
        "by_target": {},
    }

    for idx, finding in enumerate(findings):
        error = _validate_finding(finding)
        if error:
            summary["findings_malformed"] += 1
            summary["malformed_errors"].append({"index": idx, "error": error})
            continue

        target_raw = finding["target"]
        field = finding["field"]
        value = str(finding["value"]).strip()
        source_url = finding["source_url"]
        key = _finding_key(packet_id, target_raw, field, value, source_url)
        if key in seen:
            summary["findings_already_processed"] += 1
            continue

        target_bucket = summary["by_target"].setdefault(target_raw, {"applied": 0, "deduped": 0, "queued": 0, "rejected_identity": 0})

        try:
            kind, resolved_key = _resolve_target(target_raw)
        except _IdentityUnresolved as exc:
            decision = mutation_policy.decide(
                source=SOURCE_TAG, new_value=value, identity_ambiguous=True,
                identity_reason=str(exc), observed_at=finding.get("observed_at"),
                confidence=finding.get("confidence"), field_name=field, entity_id=target_raw,
            )
            if not dry_run:
                mutation_policy.record_receipt(decision, artifact=None, applied=False)
            summary["identity_unresolved"] += 1
            target_bucket["rejected_identity"] += 1
            new_keys.append(key)
            continue

        if kind == "competitor":
            if field in _COMPETITOR_LIST_FIELDS:
                outcome = _apply_competitor_list_finding(resolved_key, finding, dry_run=dry_run)
            elif field in _COMPETITOR_SCALAR_FIELDS:
                outcome = _apply_competitor_trends(resolved_key, finding, dry_run=dry_run)
            else:
                outcome = _apply_competitor_marketplace_signal(resolved_key, finding, dry_run=dry_run)
        else:
            outcome = _apply_genius_finding(resolved_key, finding, dry_run=dry_run)

        if outcome["applied"]:
            summary["applied"] += 1
            target_bucket["applied"] += 1
        elif outcome["deduped"]:
            summary["deduped"] += 1
            target_bucket["deduped"] += 1
        else:
            summary["queued_for_review"] += 1
            target_bucket["queued"] += 1
        new_keys.append(key)

    if not dry_run and new_keys:
        log["processed_finding_keys"] = sorted(seen | set(new_keys))
        _save_import_log(log)

    # RB defect 2026-09-30: exact canonical targets changed, for the
    # unified capture receipt (a target only counts here if at least one of
    # its findings was actually applied, not merely received or queued).
    summary["canonical_targets_changed"] = sorted(
        target for target, bucket in summary["by_target"].items()
        if bucket.get("applied", 0) > 0
    )
    summary["findings_rejected"] = summary["findings_malformed"] + summary["identity_unresolved"]

    if not dry_run:
        receipts = _load_import_receipts()
        receipts["receipts"][packet_id] = {**summary, "recorded_at": _now_iso()}
        _save_import_receipts(receipts)

    return summary


def sweep(*, dry_run: bool = True) -> dict:
    """Scan DROP_DIR for sidecars carrying a "findings" array not yet
    offered to the importer (tracked via this module's own file-hash
    manifest, independent of deep_research_coverage.py's manifest, which
    tracks something else -- source-productivity outcome recording, not
    findings import). A sidecar with no findings key is a valid,
    coverage-only sidecar (pre-dating this schema, or a target with nothing
    structured to report) and is simply marked processed, not an error."""
    if not DROP_DIR.is_dir():
        return {"packets": []}
    manifest = _load_sweep_manifest()
    results = []
    newly_processed: dict[str, dict] = {}
    for path in sorted(DROP_DIR.glob("*.json")):
        fhash = _file_hash(path)
        if fhash in manifest["processed"]:
            continue
        entry: dict = {"path": str(path)}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            is_hunter_platform_packet = (
                data.get("schema") == "rb.hunter_research_packet.v1"
                and data.get("payload_schema") == "rb.competitor_platform_research.v1"
                and isinstance(data.get("payload"), dict)
                and "findings" in data["payload"]
            )
            if is_hunter_platform_packet:
                hunter_gate = hunter.validate_packet(data)
                if not hunter_gate["valid"]:
                    entry.update(
                        ok=False,
                        has_findings=True,
                        quality_gate="rejected",
                        quality_errors=hunter_gate["errors"],
                        quality_scores=hunter_gate["scores"],
                    )
                    results.append(entry)
                    if not dry_run:
                        newly_processed[fhash] = {
                            "path": str(path),
                            "processed_at": _now_iso(),
                            "quality_gate": "rejected",
                        }
                    continue
            if "findings" in data or is_hunter_platform_packet:
                result = import_findings(data, packet_id=data.get("packet_id") or path.stem, dry_run=dry_run)
                entry.update(
                    ok=True,
                    has_findings=True,
                    quality_gate="passed" if is_hunter_platform_packet else "legacy",
                    **result,
                )
            else:
                entry.update(ok=True, has_findings=False)
        except (OSError, ValueError) as exc:
            entry.update(ok=False, error=str(exc))
        results.append(entry)
        if not dry_run:
            newly_processed[fhash] = {"path": str(path), "processed_at": _now_iso()}
    if newly_processed and not dry_run:
        manifest["processed"].update(newly_processed)
        _save_sweep_manifest(manifest)
    return {"packets": results}


# ---------------------------------------------------------------------------
# Read-only audit — surfaces the gap between cycle-state "complete" and
# real canonical content, per Todd's direction (audit only, no automated
# historical-Markdown backfill in this pass).
# ---------------------------------------------------------------------------

def audit_existing() -> dict:
    cycle_state_path = core.SYSTEM_DIR / "research" / "competitor_platform_cycle_2026-09-28.json"
    mismatches = []
    if cycle_state_path.exists():
        state = json.loads(cycle_state_path.read_text(encoding="utf-8"))
        for item in state.get("competitors", []):
            if item.get("status") != "complete":
                continue
            slug = item["slug"]
            try:
                comp = cic.load_competitor(slug)["competitor"]
            except FileNotFoundError:
                mismatches.append({"slug": slug, "reason": "marked complete but no competitor record exists"})
                continue
            full = cic.get_extended_profile(comp)
            populated_fields = [f for f in cic.EXTENDED_PROFILE_FIELDS if full.get(f)]
            trends_populated = bool((full.get("trends") or {}).get("value"))
            if not populated_fields and not trends_populated:
                mismatches.append({
                    "slug": slug,
                    "reason": "cycle-state status is 'complete' but competitor.json has zero populated extended-profile fields",
                    "qualifying_packet_path": item.get("qualifying_packet_path"),
                })
    return {
        "cycle_state_path": str(cycle_state_path),
        "mismatches_found": len(mismatches),
        "mismatches": mismatches,
    }


# ---------------------------------------------------------------------------
# One-time migration: global-payments out of the competitor registry
# ---------------------------------------------------------------------------

def migrate_global_payments_to_parent_evidence(*, dry_run: bool = True) -> dict:
    """RB-DEFECT-073 one-time correction: 'global-payments' (Genius's own
    parent company) was a live, registered tracked competitor despite
    cic.is_own_company() existing precisely to prevent this. Moves its
    existing evidence.jsonl history into genius_capabilities' parent-scope
    evidence log, then removes it from the competitor registry/directory
    and (if present) from the platform-cycle state file. Explicit,
    reviewable, re-runnable -- not a silent side effect of the importer's
    normal sweep path."""
    slug = "global-payments"
    result: dict[str, Any] = {"slug": slug, "existed": False, "evidence_migrated": 0, "removed_from_registry": False}
    try:
        data = cic.load_competitor(slug)
    except FileNotFoundError:
        return result
    result["existed"] = True
    comp = data["competitor"]
    evidence = data["evidence"]
    result["display_name"] = comp.get("display_name")
    result["evidence_migrated"] = len(evidence)
    result["registry_entry_removed_at"] = None

    if dry_run:
        result["dry_run"] = True
        return result

    for note in evidence:
        category = note.get("category") if note.get("category") in gc.VALID_EVIDENCE_CATEGORIES else "other"
        gc.add_evidence(
            "parent",
            note.get("summary", ""),
            category=category,
            source=f"{SOURCE_TAG}:global_payments_migration (was {note.get('source', 'unknown source')})",
            confidence=note.get("confidence") or "medium",
        )

    comp_dir = cic.competitor_dir(slug)
    shutil.rmtree(comp_dir)

    with cic._registry_lock():  # noqa: SLF001 -- same critical section register_competitor() uses
        reg = cic.load_registry()
        before = len(reg.get("registry", []))
        reg["registry"] = [e for e in reg.get("registry", []) if e.get("competitor_slug") != slug]
        cic.save_registry(reg)
        result["removed_from_registry"] = len(reg["registry"]) < before

    cycle_state_path = core.SYSTEM_DIR / "research" / "competitor_platform_cycle_2026-09-28.json"
    if cycle_state_path.exists():
        state = json.loads(cycle_state_path.read_text(encoding="utf-8"))
        before_n = len(state.get("competitors", []))
        state["competitors"] = [c for c in state.get("competitors", []) if c.get("slug") != slug]
        state["updated_at"] = _now_iso()
        cycle_state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        result["removed_from_cycle_state"] = len(state["competitors"]) < before_n

    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", type=Path, help="Import one sidecar JSON file.")
    p.add_argument("--sweep", action="store_true", help="Scan the ChatGPT Intelligence Drop inbox for unimported sidecars.")
    p.add_argument("--audit-existing", action="store_true", help="Read-only: report cycle-state/canonical-data mismatches.")
    p.add_argument("--migrate-global-payments", action="store_true", help="One-time: migrate global-payments to Genius parent-scope evidence.")
    p.add_argument("--confirm", action="store_true", help="Actually write (default: dry run only).")
    p.add_argument("--dry-run", action="store_true", help="Explicit dry run (default when --confirm is omitted).")
    args = p.parse_args()

    dry_run = not args.confirm

    if args.audit_existing:
        print(json.dumps(audit_existing(), indent=2, ensure_ascii=False))
        return 0
    if args.migrate_global_payments:
        result = migrate_global_payments_to_parent_evidence(dry_run=dry_run)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if dry_run:
            print("\n[dry run -- nothing written. Re-run with --confirm to apply.]", file=sys.stderr)
        return 0
    if args.file:
        sidecar = json.loads(args.file.read_text(encoding="utf-8"))
        result = import_findings(sidecar, dry_run=dry_run)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if dry_run:
            print("\n[dry run -- nothing written. Re-run with --confirm to apply.]", file=sys.stderr)
        return 0
    if args.sweep:
        result = sweep(dry_run=dry_run)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if dry_run:
            print("\n[dry run -- nothing written. Re-run with --confirm to apply.]", file=sys.stderr)
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
