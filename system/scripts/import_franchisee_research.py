#!/usr/bin/env python3
"""
import_franchisee_research.py — Franchisee Finder Phase 1 (2026-10-02).

Review-first intake for the two new Hunter payload schemas, mirroring
import_competitor_platform_research.py's discipline:

  - rb.franchisee_discovery.v1 (payload.discovered_organizations): names a
    franchisee organization and the brand it was discovered operating.
    NEVER auto-creates when a similarly-named organization already exists
    on the registry (spec section 3) -- routes to mutation_policy's
    identity-ambiguity review instead, unless the finding itself names
    the existing slug explicitly (`matched_existing_franchisee_slug`),
    which this importer still verifies actually exists before trusting it.
  - rb.franchisee_organization_profile.v1 (payload.findings): a dated,
    sourced assertion about an ALREADY-KNOWN organization (target
    "franchisee:<slug>"). Never creates a new organization from a profile
    finding -- that is discovery's job, not profile's (spec section 3's
    warning applies here too: a profile finding about an unresolvable
    slug is an identity problem, not license to guess a new one into
    existence).

Both auto-apply only through mutation_policy.decide() -- a conflicting
undated value is queued for review, never silently overwritten, same
standard as every other Hunter importer in this codebase.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import franchisee_intelligence as fi  # noqa: E402
import franchisee_intelligence_common as fic  # noqa: E402
import mutation_policy  # noqa: E402

SOURCE_TAG = "system:franchisee_research_2026-10"

VALID_PROFILE_FIELDS = set(fic.EXTENDED_PROFILE_FIELDS) | set(fic.EXTENDED_SCALAR_FIELDS)
VALID_CONFIDENCE = {"low", "medium", "high", "critical"}
VALID_FINDING_TYPES = {"vendor_stated", "independently_verified", "marketplace_reported", "inference"}

_PROFILE_REQUIRED_KEYS = ("target", "field", "value", "finding_type", "source_url", "source_type", "confidence", "observed_at")
_DISCOVERY_REQUIRED_KEYS = ("proposed_name", "discovered_from_brand", "evidence", "source_url", "confidence", "observed_at")


class _IdentityUnresolved(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


# ---------------------------------------------------------------------------
# Profile findings (rb.franchisee_organization_profile.v1)
# ---------------------------------------------------------------------------

def _validate_profile_finding(finding: dict) -> Optional[str]:
    if not isinstance(finding, dict):
        return "finding is not an object"
    missing = [k for k in _PROFILE_REQUIRED_KEYS if not finding.get(k)]
    if missing:
        return f"missing required key(s): {missing}"
    target = finding["target"]
    if not (isinstance(target, str) and target.startswith("franchisee:")):
        return f"target must start with 'franchisee:', got {target!r}"
    if finding["field"] not in VALID_PROFILE_FIELDS:
        return f"field must be one of {sorted(VALID_PROFILE_FIELDS)}, got {finding['field']!r}"
    if finding["confidence"] not in VALID_CONFIDENCE:
        return f"confidence must be one of {sorted(VALID_CONFIDENCE)}, got {finding['confidence']!r}"
    if finding["finding_type"] not in VALID_FINDING_TYPES:
        return f"finding_type must be one of {sorted(VALID_FINDING_TYPES)}, got {finding['finding_type']!r}"
    return None


def _resolve_profile_target(target: str) -> str:
    slug = target.split(":", 1)[1]
    try:
        fic.franchisee_dir(slug)
    except FileNotFoundError:
        raise _IdentityUnresolved(f"franchisee slug {slug!r} is not a tracked organization; profile findings never auto-create one")
    return slug


def _apply_profile_finding(slug: str, finding: dict, *, dry_run: bool) -> dict:
    field = finding["field"]
    value = str(finding["value"]).strip()
    is_scalar = field in fic.EXTENDED_SCALAR_FIELDS
    existing_value = None
    existing_date = None
    if is_scalar:
        org = fic.load_json(fic.franchisee_dir(slug) / "organization.json")
        existing_value = (org.get(field) or {}).get("value")
        existing_date = (org.get(field) or {}).get("as_of")
    decision = mutation_policy.decide(
        source=SOURCE_TAG, new_value=value,
        existing_value=existing_value if is_scalar else None,
        is_set_member=not is_scalar, is_replacement=is_scalar,
        new_date=finding.get("observed_at"), existing_date=existing_date,
        observed_at=finding.get("observed_at"), confidence=finding.get("confidence"),
        field_name=field, entity_id=slug,
    )
    applied = False
    deduped = False
    if not dry_run and decision.auto_apply:
        result = fi.add_extended_profile_finding(
            slug, field, value, confidence=finding.get("confidence", "medium"),
            source_url=finding.get("source_url"), as_of=finding.get("observed_at"),
            finding_type=finding.get("finding_type"), source_owner=finding.get("source_owner"),
            source_type=finding.get("source_type"), published_at=finding.get("published_at"),
            is_vendor_claim=finding.get("is_vendor_claim"), is_inference=finding.get("is_inference"),
            limitations_or_conflicts=finding.get("limitations_or_conflicts"),
        )
        deduped = bool(result.get("deduped"))
        applied = not deduped
    if not dry_run:
        mutation_policy.record_receipt(
            decision, artifact=f"franchisee_finder/{slug}/organization.json" if applied else None, applied=applied,
        )
    return {"status": decision.status, "applied": applied, "deduped": deduped}


def import_profile_findings(sidecar: dict, *, dry_run: bool = True) -> dict:
    research_data = sidecar
    if sidecar.get("schema") == "rb.hunter_research_packet.v1" and isinstance(sidecar.get("payload"), dict):
        research_data = sidecar["payload"]
    findings = research_data.get("findings") or []

    summary: dict[str, Any] = {
        "findings_received": len(findings), "findings_malformed": 0, "applied": 0,
        "deduped": 0, "queued_for_review": 0, "identity_unresolved": 0, "malformed_errors": [],
    }
    for idx, finding in enumerate(findings):
        error = _validate_profile_finding(finding)
        if error:
            summary["findings_malformed"] += 1
            summary["malformed_errors"].append({"index": idx, "error": error})
            continue
        try:
            slug = _resolve_profile_target(finding["target"])
        except _IdentityUnresolved as exc:
            decision = mutation_policy.decide(
                source=SOURCE_TAG, new_value=finding["value"], identity_ambiguous=True,
                identity_reason=str(exc), observed_at=finding.get("observed_at"),
                confidence=finding.get("confidence"), field_name=finding["field"], entity_id=finding["target"],
            )
            if not dry_run:
                mutation_policy.record_receipt(decision, artifact=None, applied=False)
            summary["identity_unresolved"] += 1
            continue
        outcome = _apply_profile_finding(slug, finding, dry_run=dry_run)
        if outcome["applied"]:
            summary["applied"] += 1
        elif outcome["deduped"]:
            summary["deduped"] += 1
        else:
            summary["queued_for_review"] += 1
    summary["findings_rejected"] = summary["findings_malformed"] + summary["identity_unresolved"]
    return summary


# ---------------------------------------------------------------------------
# Discovery findings (rb.franchisee_discovery.v1)
# ---------------------------------------------------------------------------

def _validate_discovery_finding(finding: dict) -> Optional[str]:
    if not isinstance(finding, dict):
        return "finding is not an object"
    missing = [k for k in _DISCOVERY_REQUIRED_KEYS if not finding.get(k)]
    if missing:
        return f"missing required key(s): {missing}"
    if finding["confidence"] not in VALID_CONFIDENCE:
        return f"confidence must be one of {sorted(VALID_CONFIDENCE)}, got {finding['confidence']!r}"
    return None


def _apply_discovery_finding(finding: dict, *, dry_run: bool) -> dict:
    proposed_name = str(finding["proposed_name"]).strip()
    claimed_slug = finding.get("matched_existing_franchisee_slug")
    slug = None
    if claimed_slug:
        try:
            fic.franchisee_dir(claimed_slug)
            slug = claimed_slug
        except FileNotFoundError:
            pass  # claimed match doesn't actually exist -- fall through to normal matching, never trusted blindly

    matches = [] if slug else fi.find_similar_franchisees(proposed_name)
    if slug is None and matches:
        decision = mutation_policy.decide(
            source=SOURCE_TAG, new_value=proposed_name, identity_ambiguous=True,
            identity_reason=f"possible duplicate of existing franchisee(s): {[m['display_name'] for m in matches]}",
            observed_at=finding.get("observed_at"), confidence=finding.get("confidence"),
            field_name="discovery", entity_id=proposed_name,
        )
        if not dry_run:
            mutation_policy.record_receipt(decision, artifact=None, applied=False)
        return {"status": decision.status, "applied": False, "created": False, "matched_slug": None}

    decision = mutation_policy.decide(
        source=SOURCE_TAG, new_value=proposed_name, is_set_member=True,
        observed_at=finding.get("observed_at"), confidence=finding.get("confidence"),
        field_name="discovery", entity_id=slug or fi.slugify(proposed_name),
    )
    applied = False
    if not dry_run and decision.auto_apply:
        if slug is None:
            slug = fi.slugify(proposed_name)
            fi.create_franchisee(proposed_name, slug=slug)
            org = fic.load_json(fic.franchisee_dir(slug) / "organization.json")
            org["source_discovery"] = {
                "method": "hunter_franchisee_discovery", "discovered_from_brand": finding.get("discovered_from_brand"),
                "confidence": finding.get("confidence"), "as_of": finding.get("observed_at"),
                "source_url": finding.get("source_url"),
            }
            fic.save_json(fic.franchisee_dir(slug) / "organization.json", org)
        fi.add_franchisee_evidence(
            slug, str(finding.get("evidence") or f"Discovered operating {finding.get('discovered_from_brand')}"),
            category="discovery", source=finding.get("source_url"), confidence=finding.get("confidence"),
        )
        applied = True
    if not dry_run:
        mutation_policy.record_receipt(
            decision, artifact=f"franchisee_finder/{slug}/organization.json" if applied else None, applied=applied,
        )
    return {"status": decision.status, "applied": applied, "created": applied and claimed_slug is None, "matched_slug": slug}


def import_discovery_findings(sidecar: dict, *, dry_run: bool = True) -> dict:
    research_data = sidecar
    if sidecar.get("schema") == "rb.hunter_research_packet.v1" and isinstance(sidecar.get("payload"), dict):
        research_data = sidecar["payload"]
    findings = research_data.get("discovered_organizations") or []

    summary: dict[str, Any] = {
        "findings_received": len(findings), "findings_malformed": 0, "applied": 0,
        "queued_for_review": 0, "malformed_errors": [],
    }
    for idx, finding in enumerate(findings):
        error = _validate_discovery_finding(finding)
        if error:
            summary["findings_malformed"] += 1
            summary["malformed_errors"].append({"index": idx, "error": error})
            continue
        outcome = _apply_discovery_finding(finding, dry_run=dry_run)
        if outcome["applied"]:
            summary["applied"] += 1
        else:
            summary["queued_for_review"] += 1
    summary["findings_rejected"] = summary["findings_malformed"]
    return summary
