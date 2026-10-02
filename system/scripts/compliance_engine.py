#!/usr/bin/env python3
"""
compliance_engine.py — Employment Governance Layer (EGL), Phase 1.

Evaluates outbound content Todd is about to publish/send (LinkedIn posts, drafted
customer/press emails, public statements) against the active employer's encoded
policies (system/employers/<employer_id>/policies/*.yaml) and returns a verdict.

Deterministic, rule-based — mirrors the "policy is code, not prose" principle in
RB_Action_Recommendation_Policy.docx. This module never calls an LLM and never makes
the compliance decision probabilistically; it matches known restriction patterns and
reports what it found. An LLM may be used *after* this returns to draft a compliant
rewrite, but not to decide whether content is compliant.

Detection is intentionally conservative in scope, not exhaustive — see
system/RB_EMPLOYMENT_GOVERNANCE_LAYER.md Section 6 on why automated policy extraction
and detection are treated as advisory (extracted_by: assisted) pending Todd's
line-by-line review, not as authoritative legal judgment. A "low" verdict here means
"no known-pattern match," not "legally cleared."

Design doc: system/RB_EMPLOYMENT_GOVERNANCE_LAYER.md
Employer profiles/policies: system/employers/<employer_id>/

CLI:
    python3 system/scripts/compliance_engine.py --check "post text" --content-type linkedin_post
    python3 system/scripts/compliance_engine.py --validate
    python3 system/scripts/compliance_engine.py --smoke
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
EMPLOYERS_DIR = SYSTEM_DIR / "employers"

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ComplianceFinding:
    policy_id: str
    restriction_id: str
    restriction_text: str
    severity: str  # "medium" | "high"
    matched_on: str


@dataclass
class ComplianceVerdict:
    overall_risk: str = "low"  # "low" | "medium" | "high"
    recommendation: str = "allow"  # "allow" | "warn" | "block"
    findings: list[ComplianceFinding] = field(default_factory=list)
    rewrite_guidance: list[str] = field(default_factory=list)
    employer_id: str | None = None
    policies_checked: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Employer / policy loading
# ---------------------------------------------------------------------------

def load_employer_profile(employer_id: str) -> dict[str, Any] | None:
    path = EMPLOYERS_DIR / employer_id / "profile.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def list_employer_profiles() -> list[dict[str, Any]]:
    if not EMPLOYERS_DIR.exists():
        return []
    profiles = []
    for d in sorted(EMPLOYERS_DIR.iterdir()):
        if not d.is_dir():
            continue
        profile = load_employer_profile(d.name)
        if profile is not None:
            profiles.append(profile)
    return profiles


def active_primary_employer_id() -> str | None:
    """
    Return the employer_id of the single profile with status=active and
    relationship_type=primary_employer. Returns None if zero or more than one match —
    ambiguous cases require the caller to pass employer_id explicitly rather than
    guessing. See validate_employer_registry() for the diagnostic version of this check.
    """
    matches = [
        p.get("employer_id")
        for p in list_employer_profiles()
        if p.get("status") == "active" and p.get("relationship_type") == "primary_employer"
    ]
    return matches[0] if len(matches) == 1 else None


def validate_employer_registry() -> list[str]:
    """Returns a list of warning strings for registry-invariant violations. Empty = healthy."""
    warnings: list[str] = []
    profiles = list_employer_profiles()
    primary_active = [
        p for p in profiles
        if p.get("status") == "active" and p.get("relationship_type") == "primary_employer"
    ]
    if len(primary_active) == 0:
        warnings.append("No active primary_employer profile found.")
    elif len(primary_active) > 1:
        ids = ", ".join(p.get("employer_id", "?") for p in primary_active)
        warnings.append(f"Multiple active primary_employer profiles found ({ids}) — exactly one is expected.")
    for p in profiles:
        if p.get("relationship_type") not in ("primary_employer", "advisory_engagement"):
            warnings.append(f"{p.get('employer_id', '?')}: missing or unrecognized relationship_type.")
    return warnings


def load_policies(employer_id: str) -> list[dict[str, Any]]:
    policies_dir = EMPLOYERS_DIR / employer_id / "policies"
    if not policies_dir.exists():
        return []
    policies = []
    for path in sorted(policies_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if data.get("superseded_by"):
            continue  # historical only — not evaluated against new content
        policies.append(data)
    return policies


def _iter_restrictions(policies: list[dict[str, Any]]):
    for policy in policies:
        for restriction in policy.get("restrictions", []) or []:
            yield policy["policy_id"], restriction


# ---------------------------------------------------------------------------
# Detection rules
# ---------------------------------------------------------------------------
# Maps (policy_id, restriction_id) -> [(pattern, severity), ...]. Deliberately kept
# separate from the extracted policy YAML (which mirrors the source document verbatim,
# per Section 2 of the design doc) so detection heuristics can be tuned without
# touching the ground-truth extraction. New restrictions with no entry here are simply
# not checked yet — silently, not as a false "clear."

DETECTION_RULES: dict[tuple[str, str], list[tuple[re.Pattern, str]]] = {
    ("social-media-policy", "no-speaking-on-behalf-of-company"): [
        (re.compile(r"\bon behalf of (global payments|genius)\b", re.I), "high"),
        (re.compile(r"\bglobal payments (announces|is proud to announce|confirms)\b", re.I), "high"),
    ],
    ("social-media-policy", "no-commenting-on-incidents"): [
        (re.compile(r"\b(outage|breach|incident|downtime)\b.{0,60}\b(global payments|genius|gpn)\b", re.I), "high"),
        (re.compile(r"\b(global payments|genius|gpn)\b.{0,60}\b(outage|breach|incident|downtime)\b", re.I), "high"),
    ],
    ("social-media-policy", "no-client-prospect-discussion"): [
        (re.compile(r"\b(our client|our customer|signed (a )?deal with|new customer)\b", re.I), "medium"),
    ],
    ("social-media-policy", "no-product-information-discussion"): [
        (re.compile(r"\b(unreleased|coming soon|release date|roadmap|not yet announced)\b", re.I), "high"),
    ],
    ("social-media-policy", "no-legal-financial-discussion"): [
        (re.compile(r"\b(gpn stock|stock price|earnings (call|report)|quarterly results)\b", re.I), "high"),
        (re.compile(r"\bpredict.{0,20}(performance|earnings)\b", re.I), "high"),
    ],
    ("code-of-conduct", "no-insider-trading"): [
        (re.compile(r"\b(buy|sell|trade)\b.{0,40}\b(gpn|global payments stock|global payments shares)\b", re.I), "high"),
    ],
    ("code-of-conduct", "confidential-information-protection"): [
        (re.compile(r"\b(internal pricing|trade secret|confidential (deal|contract|pricing))\b", re.I), "high"),
    ],
}

DISCLAIMER_PATTERN = re.compile(r"my own and do not reflect", re.I)
MENTIONS_COMPANY_PATTERN = re.compile(r"\bglobal payments\b|\bgenius\b", re.I)
DISCLOSURE_APPLICABLE_CONTENT_TYPES = {"linkedin_post", "public_comment"}


def check_content_compliance(
    content: str,
    content_type: str = "linkedin_post",
    employer_id: str | None = None,
) -> ComplianceVerdict:
    """
    Evaluate outbound content against the active (or explicitly specified) employer's
    encoded policies. Pure pattern matching — no LLM call, no network access.
    """
    resolved_employer_id = employer_id or active_primary_employer_id()
    if resolved_employer_id is None:
        return ComplianceVerdict(
            overall_risk="low",
            recommendation="allow",
            rewrite_guidance=["No active primary employer profile found — compliance check skipped, not cleared."],
            employer_id=None,
        )

    policies = load_policies(resolved_employer_id)
    findings: list[ComplianceFinding] = []

    for policy_id, restriction in _iter_restrictions(policies):
        restriction_id = restriction.get("id", "")
        for pattern, severity in DETECTION_RULES.get((policy_id, restriction_id), []):
            match = pattern.search(content)
            if match:
                findings.append(ComplianceFinding(
                    policy_id=policy_id,
                    restriction_id=restriction_id,
                    restriction_text=restriction.get("text", ""),
                    severity=severity,
                    matched_on=match.group(0),
                ))

    if (
        content_type in DISCLOSURE_APPLICABLE_CONTENT_TYPES
        and MENTIONS_COMPANY_PATTERN.search(content)
        and not DISCLAIMER_PATTERN.search(content)
    ):
        findings.append(ComplianceFinding(
            policy_id="social-media-policy",
            restriction_id="disclosure-reminder",
            restriction_text=(
                "If speaking 'about' Global Payments (not on its behalf), disclose your "
                "affiliation and that the views are your own: \"I am an employee of Global "
                "Payments. The posts (or views) on this site are my own and do not reflect "
                "the positions, strategies or opinions of Global Payments Inc.\""
            ),
            severity="medium",
            matched_on="(no personal-views disclaimer found)",
        ))

    policies_checked = [p.get("policy_id", "") for p in policies]

    if not findings:
        return ComplianceVerdict(
            overall_risk="low",
            recommendation="allow",
            employer_id=resolved_employer_id,
            policies_checked=policies_checked,
        )

    overall_severity = max(findings, key=lambda f: SEVERITY_RANK[f.severity]).severity
    overall_risk = overall_severity
    recommendation = "block" if overall_risk == "high" else "warn"

    seen_restrictions: set[str] = set()
    rewrite_guidance: list[str] = []
    for f in findings:
        key = f"{f.policy_id}:{f.restriction_id}"
        if key not in seen_restrictions:
            seen_restrictions.add(key)
            rewrite_guidance.append(f.restriction_text)

    return ComplianceVerdict(
        overall_risk=overall_risk,
        recommendation=recommendation,
        findings=findings,
        rewrite_guidance=rewrite_guidance,
        employer_id=resolved_employer_id,
        policies_checked=policies_checked,
    )


# ---------------------------------------------------------------------------
# Dashboard rendering
# ---------------------------------------------------------------------------

_POLICY_LABELS = {
    "social-media-policy": "Social Media",
    "code-of-conduct": "Code of Conduct",
    "confidentiality-assignment-non-solicitation": "Confidentiality",
    "travel-expense-policy": "Travel & Expense",
}


def format_verdict_dashboard(verdict: ComplianceVerdict) -> str:
    """Render the ✓/⚠ compliance dashboard format from the EGL design doc."""
    if verdict.employer_id is None:
        return "Employment Review\n(no active primary employer profile — check skipped)"

    flagged_policy_ids = {f.policy_id for f in verdict.findings}
    lines = ["Employment Review"]
    for policy_id in verdict.policies_checked:
        label = _POLICY_LABELS.get(policy_id, policy_id)
        mark = "⚠" if policy_id in flagged_policy_ids else "✓"
        lines.append(f"{mark} {label}")

    lines.append("")
    lines.append(f"Overall Risk: {verdict.overall_risk.upper()}")

    if verdict.findings:
        lines.append("")
        lines.append("Reason:")
        for g in verdict.rewrite_guidance:
            lines.append(f"- {g}")
        lines.append("")
        lines.append(f"Recommendation: {'do not post — ' if verdict.recommendation == 'block' else 'review before posting — '}rewrite to address the flagged item(s) above.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _smoke() -> bool:
    ok = True

    warnings = validate_employer_registry()
    if warnings:
        print("REGISTRY WARNINGS:")
        for w in warnings:
            print(f"  - {w}")
        ok = False
    else:
        print("Registry OK: exactly one active primary_employer profile.")

    clear_verdict = check_content_compliance(
        "Excited to start this new chapter in enterprise restaurant technology "
        "at Global Payments. Grateful for the network that got me here.",
        content_type="linkedin_post",
    )
    if clear_verdict.overall_risk != "medium":  # missing disclaimer -> medium, expected
        print(f"UNEXPECTED: career-announcement post scored {clear_verdict.overall_risk}, expected medium (disclaimer reminder)")
        ok = False
    else:
        print("PASS: career-announcement post mentioning the company -> medium (disclaimer reminder), not blocked.")

    truly_clear_verdict = check_content_compliance(
        "Excited to start this new chapter in enterprise restaurant technology. "
        "Grateful for the network that got me here.",
        content_type="linkedin_post",
    )
    if truly_clear_verdict.overall_risk != "low":
        print(f"UNEXPECTED: company-free post scored {truly_clear_verdict.overall_risk}, expected low")
        ok = False
    else:
        print("PASS: post that never names the company -> low, no disclaimer reminder triggered.")

    high_verdict = check_content_compliance(
        "On behalf of Global Payments, I can confirm our unreleased roadmap includes a "
        "major new feature launching next quarter.",
        content_type="linkedin_post",
    )
    if high_verdict.recommendation != "block":
        print(f"FAIL: on-behalf-of + roadmap post scored {high_verdict.recommendation}, expected block")
        ok = False
    else:
        print("PASS: on-behalf-of + unreleased roadmap post -> block.")

    print("Smoke test:", "PASS" if ok else "FAIL")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Employment Governance Layer compliance check")
    parser.add_argument("--check", metavar="TEXT", help="content to evaluate")
    parser.add_argument("--content-type", default="linkedin_post")
    parser.add_argument("--employer", default=None)
    parser.add_argument("--validate", action="store_true", help="check employer registry invariants")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    if args.smoke:
        return 0 if _smoke() else 1

    if args.validate:
        warnings = validate_employer_registry()
        if not warnings:
            print("Registry OK.")
            return 0
        for w in warnings:
            print(f"WARNING: {w}")
        return 1

    if args.check:
        verdict = check_content_compliance(args.check, content_type=args.content_type, employer_id=args.employer)
        print(format_verdict_dashboard(verdict))
        return 1 if verdict.recommendation == "block" else 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
