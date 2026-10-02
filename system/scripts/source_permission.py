#!/usr/bin/env python3
"""
source_permission.py — Metadata-First Connector permission profile (RB Phase 1).

Formalizes the "Metadata Mode" convention already proven in
fetch_apple_messages.py (metadata by default, content opt-in) as a
declarable property any capture source in settings.json can carry.

Reference: system/SCHEMAS.md, "Capture Source Permission Profile" section.

Composes with the existing privacy_guard.py guard chain (gate_action,
GATED_ACTION_TYPES) rather than duplicating a second enforcement mechanism.
The compliance precondition check below is deliberately soft (never
blocking) because the Employment Governance Layer it checks against
(system/employers/<id>/profile.yaml, see RB_EMPLOYMENT_GOVERNANCE_LAYER.md)
is still Phase 1 in-flight and does not exist for most users/setups yet.

CLI:
    python3 system/scripts/source_permission.py --smoke
    python3 system/scripts/source_permission.py --check <source_id>
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import privacy_guard as pg  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONTENT_ACCESS_LEVELS = {"metadata_only", "on_demand", "persistent"}
DEFAULT_CONTENT_ACCESS = "metadata_only"

# Derive-only toggles: default on, never require elevated content_access.
READ_TOGGLES = ("relationship_graph", "task_extraction", "calendar_correlation")

# Content-touching toggles: default off, require content_access != metadata_only.
CONTENT_TOGGLES = (
    "automatic_body_reading",
    "automatic_attachment_reading",
    "long_term_content_storage",
    "publish_outside_workspace",
)

DEFAULT_PERMISSION_PROFILE = {t: True for t in READ_TOGGLES} | {t: False for t in CONTENT_TOGGLES}

EMPLOYERS_DIR = core.SYSTEM_DIR / "employers"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class SourcePermission:
    source_id: str
    content_access: str
    permission_profile: dict[str, bool]


@dataclass
class ComplianceSoftCheckResult:
    precondition_met: bool
    reason: str
    blocking: bool = False


@dataclass
class ElevationGateResult:
    allowed: bool
    reason: str
    warnings: list[str] = field(default_factory=list)
    requires_confirmation: bool = False


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_source_permission(source_id: str, settings: dict | None = None) -> SourcePermission:
    """Resolve a source's content_access + permission_profile, applying safe defaults."""
    settings = settings if settings is not None else core.load_settings()
    sources = settings.get("capture_sources", {}).get("sources", [])
    entry = next((s for s in sources if s.get("id") == source_id), None)
    if entry is None:
        return SourcePermission(
            source_id=source_id,
            content_access=DEFAULT_CONTENT_ACCESS,
            permission_profile=dict(DEFAULT_PERMISSION_PROFILE),
        )

    content_access = entry.get("content_access", DEFAULT_CONTENT_ACCESS)
    if content_access not in CONTENT_ACCESS_LEVELS:
        content_access = DEFAULT_CONTENT_ACCESS

    profile = dict(DEFAULT_PERMISSION_PROFILE)
    profile.update(entry.get("permission_profile", {}))

    return SourcePermission(source_id=source_id, content_access=content_access, permission_profile=profile)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_source_permission(source: dict) -> list[str]:
    """Return a list of validation errors for a raw capture_sources.sources[] entry.

    This is a schema-shape check (declared content_access is internally
    consistent with declared toggles) — it does not inspect actual content,
    that's privacy_guard.py's job at ingestion time.
    """
    errors: list[str] = []
    source_id = source.get("id", "<unknown>")

    content_access = source.get("content_access", DEFAULT_CONTENT_ACCESS)
    if content_access not in CONTENT_ACCESS_LEVELS:
        errors.append(
            f"{source_id}: content_access={content_access!r} is not one of {sorted(CONTENT_ACCESS_LEVELS)}"
        )
        content_access = DEFAULT_CONTENT_ACCESS

    profile = source.get("permission_profile", {})
    if content_access == "metadata_only":
        for toggle in CONTENT_TOGGLES:
            if profile.get(toggle, False):
                errors.append(
                    f"{source_id}: permission_profile.{toggle}=true is inconsistent with "
                    f"content_access=metadata_only"
                )

    return errors


# ---------------------------------------------------------------------------
# Compliance soft-check (EGL precondition)
# ---------------------------------------------------------------------------

def _find_active_employer_profile() -> Path | None:
    if not EMPLOYERS_DIR.is_dir():
        return None
    for profile_path in sorted(EMPLOYERS_DIR.glob("*/profile.yaml")):
        try:
            text = profile_path.read_text()
        except OSError:
            continue
        # Deliberately avoid a YAML dependency for this soft check — a simple
        # line scan is sufficient since profile.yaml is hand-authored per EGL spec.
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.replace(" ", "") in ("status:active", "status:\"active\"", "status:'active'"):
                return profile_path
    return None


def check_compliance_precondition(employer_id: str | None = None) -> ComplianceSoftCheckResult:
    """Soft-check for an active Employment Governance Layer profile.

    Never blocking in Phase 1: the Employment Governance Layer
    (system/employers/<id>/profile.yaml, see RB_EMPLOYMENT_GOVERNANCE_LAYER.md)
    is still being built. This surfaces a warning so elevation decisions are
    informed, but does not hard-fail source elevation on its absence.
    """
    if employer_id is not None:
        profile_path = EMPLOYERS_DIR / employer_id / "profile.yaml"
        if profile_path.is_file():
            return ComplianceSoftCheckResult(
                precondition_met=True,
                reason=f"active employer profile found at {profile_path}",
                blocking=False,
            )
        return ComplianceSoftCheckResult(
            precondition_met=False,
            reason=f"no profile.yaml found for employer_id={employer_id!r}",
            blocking=False,
        )

    found = _find_active_employer_profile()
    if found is not None:
        return ComplianceSoftCheckResult(
            precondition_met=True,
            reason=f"active employer profile found at {found}",
            blocking=False,
        )
    return ComplianceSoftCheckResult(
        precondition_met=False,
        reason="no active employer profile found under system/employers/ "
        "(Employment Governance Layer not yet configured)",
        blocking=False,
    )


# ---------------------------------------------------------------------------
# Elevation gate
# ---------------------------------------------------------------------------

def gate_elevation(source_id: str, requested_level: str, confirmed: bool = False) -> ElevationGateResult:
    """Gate elevating a source's content_access above metadata_only.

    Composes privacy_guard.gate_action's confirmation semantics with the
    compliance soft-check above, rather than inventing a second mechanism.
    """
    warnings: list[str] = []

    if requested_level not in CONTENT_ACCESS_LEVELS:
        return ElevationGateResult(
            allowed=False,
            reason=f"unknown content_access level {requested_level!r}",
        )

    if requested_level == "metadata_only":
        return ElevationGateResult(
            allowed=True,
            reason="metadata_only requires no elevation",
            requires_confirmation=False,
        )

    action_gate = pg.gate_action("apply_baseline_mutation", confirmed=confirmed)
    if not action_gate.allowed:
        return ElevationGateResult(
            allowed=False,
            reason=action_gate.reason,
            requires_confirmation=True,
        )

    compliance = check_compliance_precondition()
    if not compliance.precondition_met:
        warnings.append(compliance.reason)

    return ElevationGateResult(
        allowed=True,
        reason=f"elevation to {requested_level} confirmed",
        warnings=warnings,
        requires_confirmation=True,
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _smoke() -> bool:
    errors: list[str] = []

    # ── 1. Default resolution when source is absent from settings ────────
    resolved = load_source_permission("__nonexistent_source__", settings={"capture_sources": {"sources": []}})
    if resolved.content_access != "metadata_only":
        errors.append("load_source_permission: absent source should default to metadata_only")
    if not all(resolved.permission_profile[t] for t in READ_TOGGLES):
        errors.append("load_source_permission: read toggles should default true")
    if any(resolved.permission_profile[t] for t in CONTENT_TOGGLES):
        errors.append("load_source_permission: content toggles should default false")

    # ── 2. Explicit declaration is respected ──────────────────────────────
    fake_settings = {
        "capture_sources": {
            "sources": [
                {
                    "id": "test_source",
                    "content_access": "persistent",
                    "permission_profile": {"automatic_body_reading": True},
                }
            ]
        }
    }
    resolved2 = load_source_permission("test_source", settings=fake_settings)
    if resolved2.content_access != "persistent":
        errors.append("load_source_permission: explicit content_access not respected")
    if not resolved2.permission_profile["automatic_body_reading"]:
        errors.append("load_source_permission: explicit toggle override not respected")

    # ── 3. Validator flags inconsistent metadata_only + content toggle ────
    bad_entry = {
        "id": "bad_source",
        "content_access": "metadata_only",
        "permission_profile": {"automatic_body_reading": True},
    }
    bad_errors = validate_source_permission(bad_entry)
    if not bad_errors:
        errors.append("validate_source_permission: should flag metadata_only + automatic_body_reading=true")

    # ── 4. Validator passes a consistent entry ────────────────────────────
    good_entry = {
        "id": "good_source",
        "content_access": "persistent",
        "permission_profile": {"automatic_body_reading": True},
    }
    if validate_source_permission(good_entry):
        errors.append("validate_source_permission: persistent + automatic_body_reading=true should be valid")

    # ── 5. Compliance precondition soft-check never blocks ────────────────
    compliance = check_compliance_precondition(employer_id="__nonexistent_employer__")
    if compliance.blocking:
        errors.append("check_compliance_precondition: must never be blocking in Phase 1")
    if compliance.precondition_met:
        errors.append("check_compliance_precondition: nonexistent employer should not report precondition_met")

    # ── 6. Elevation gate requires confirmation, never hard-blocks on EGL ──
    gate_unconfirmed = gate_elevation("test_source", "persistent", confirmed=False)
    if gate_unconfirmed.allowed:
        errors.append("gate_elevation: unconfirmed elevation should not be allowed")

    gate_confirmed = gate_elevation("test_source", "persistent", confirmed=True)
    if not gate_confirmed.allowed:
        errors.append("gate_elevation: confirmed elevation should be allowed even without an EGL profile")

    gate_metadata_only = gate_elevation("test_source", "metadata_only", confirmed=False)
    if not gate_metadata_only.allowed:
        errors.append("gate_elevation: metadata_only requires no confirmation")

    if errors:
        for e in errors:
            print(f"  FAIL: {e}")
        return False

    print("source_permission smoke: all checks passed")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="RB capture source permission profile CLI")
    parser.add_argument("--smoke", action="store_true", help="Run smoke tests")
    parser.add_argument("--check", metavar="SOURCE_ID", help="Print resolved permission for a source")
    args = parser.parse_args()

    if args.smoke:
        ok = _smoke()
        sys.exit(0 if ok else 1)

    if args.check:
        resolved = load_source_permission(args.check)
        print(f"source_id: {resolved.source_id}")
        print(f"content_access: {resolved.content_access}")
        print(f"permission_profile: {resolved.permission_profile}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
