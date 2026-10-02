#!/usr/bin/env python3
"""
Tests for Employment Governance Layer Phase 1 — compliance_engine.py

Test IDs and coverage:

RB-EGL-001: exactly one active primary_employer profile exists (global-payments)
RB-EGL-002: bridgepoint-ops is active but not primary_employer (advisory_engagement)
RB-EGL-003: validate_employer_registry() returns no warnings for the current registry
RB-EGL-004: active_primary_employer_id() resolves to global-payments
RB-EGL-005: load_policies() for global-payments returns non-superseded policies only
RB-EGL-006: content with no restricted phrases -> low risk, allow
RB-EGL-007: "on behalf of Global Payments" -> high risk, block
RB-EGL-008: unreleased roadmap disclosure -> high risk, block
RB-EGL-009: mentioning the company without the personal-views disclaimer -> medium, warn
RB-EGL-010: mentioning the company WITH the disclaimer -> no disclosure-reminder finding
RB-EGL-011: content that never names the company -> low risk regardless of content_type
RB-EGL-012: explicit employer_id overrides active-employer resolution
RB-EGL-013: unknown employer_id with no profile -> low risk, explicit "skipped" guidance
RB-EGL-014: content_type outside disclosure-applicable set skips the disclaimer check
RB-EGL-015: format_verdict_dashboard() marks flagged policies with a warning glyph
RB-EGL-016: format_verdict_dashboard() marks all-clear policies with a check glyph
RB-EGL-017: findings are deduplicated by restriction when multiple patterns match the same restriction
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import compliance_engine as ce  # noqa: E402


# ---------------------------------------------------------------------------
# RB-EGL-001..005: employer/policy registry
# ---------------------------------------------------------------------------

def test_global_payments_is_the_active_primary_employer():
    """RB-EGL-001: exactly one active primary_employer profile exists (global-payments)."""
    profile = ce.load_employer_profile("global-payments")
    assert profile is not None
    assert profile["status"] == "active"
    assert profile["relationship_type"] == "primary_employer"


def test_bridgepoint_is_active_advisory_not_primary():
    """RB-EGL-002: bridgepoint-ops is active but not primary_employer (advisory_engagement)."""
    profile = ce.load_employer_profile("bridgepoint-ops")
    assert profile is not None
    assert profile["status"] == "active"
    assert profile["relationship_type"] == "advisory_engagement"


def test_registry_has_no_warnings():
    """RB-EGL-003: validate_employer_registry() returns no warnings for the current registry."""
    assert ce.validate_employer_registry() == []


def test_active_primary_employer_resolves():
    """RB-EGL-004: active_primary_employer_id() resolves to global-payments."""
    assert ce.active_primary_employer_id() == "global-payments"


def test_load_policies_excludes_superseded():
    """RB-EGL-005: load_policies() for global-payments returns non-superseded policies only."""
    policies = ce.load_policies("global-payments")
    ids = {p["policy_id"] for p in policies}
    assert "social-media-policy" in ids
    assert "code-of-conduct" in ids
    assert all(p.get("superseded_by") in (None, False) for p in policies)


# ---------------------------------------------------------------------------
# RB-EGL-006..011: content evaluation
# ---------------------------------------------------------------------------

def test_clean_content_is_low_risk():
    """RB-EGL-006: content with no restricted phrases -> low risk, allow."""
    verdict = ce.check_content_compliance(
        "Thinking about how software-funded payments changes the buying conversation "
        "for enterprise restaurant operators.",
        content_type="linkedin_post",
    )
    assert verdict.overall_risk == "low"
    assert verdict.recommendation == "allow"
    assert verdict.findings == []


def test_on_behalf_of_is_blocked():
    """RB-EGL-007: "on behalf of Global Payments" -> high risk, block."""
    verdict = ce.check_content_compliance(
        "On behalf of Global Payments, I can confirm this partnership.",
        content_type="linkedin_post",
    )
    assert verdict.overall_risk == "high"
    assert verdict.recommendation == "block"
    assert any(f.restriction_id == "no-speaking-on-behalf-of-company" for f in verdict.findings)


def test_unreleased_roadmap_is_blocked():
    """RB-EGL-008: unreleased roadmap disclosure -> high risk, block."""
    verdict = ce.check_content_compliance(
        "Our unreleased roadmap includes a major new feature launching next quarter.",
        content_type="linkedin_post",
    )
    assert verdict.overall_risk == "high"
    assert verdict.recommendation == "block"
    assert any(f.restriction_id == "no-product-information-discussion" for f in verdict.findings)


def test_company_mention_without_disclaimer_is_medium():
    """RB-EGL-009: mentioning the company without the personal-views disclaimer -> medium, warn."""
    verdict = ce.check_content_compliance(
        "Excited for my first week at Global Payments.",
        content_type="linkedin_post",
    )
    assert verdict.overall_risk == "medium"
    assert verdict.recommendation == "warn"
    assert any(f.restriction_id == "disclosure-reminder" for f in verdict.findings)


def test_company_mention_with_disclaimer_has_no_disclosure_finding():
    """RB-EGL-010: mentioning the company WITH the disclaimer -> no disclosure-reminder finding."""
    verdict = ce.check_content_compliance(
        "Excited for my first week at Global Payments. I am an employee of Global "
        "Payments. The posts (or views) on this site are my own and do not reflect "
        "the positions, strategies or opinions of Global Payments Inc.",
        content_type="linkedin_post",
    )
    assert not any(f.restriction_id == "disclosure-reminder" for f in verdict.findings)
    assert verdict.overall_risk == "low"


def test_content_never_naming_company_is_low_risk():
    """RB-EGL-011: content that never names the company -> low risk regardless of content_type."""
    verdict = ce.check_content_compliance(
        "Grateful for the network that got me here.",
        content_type="linkedin_post",
    )
    assert verdict.overall_risk == "low"


# ---------------------------------------------------------------------------
# RB-EGL-012..014: resolution edge cases
# ---------------------------------------------------------------------------

def test_explicit_employer_id_overrides_resolution():
    """RB-EGL-012: explicit employer_id overrides active-employer resolution."""
    verdict = ce.check_content_compliance(
        "Some advisory content with no restricted phrases.",
        employer_id="bridgepoint-ops",
    )
    assert verdict.employer_id == "bridgepoint-ops"
    assert verdict.policies_checked == []  # no policy docs on file for this engagement
    assert verdict.overall_risk == "low"


def test_unknown_employer_id_is_low_risk_but_explicit():
    """RB-EGL-013: unknown employer_id with no profile -> low risk, explicit "skipped" guidance."""
    verdict = ce.check_content_compliance("anything", employer_id="does-not-exist")
    assert verdict.overall_risk == "low"
    assert verdict.policies_checked == []


def test_non_disclosure_applicable_content_type_skips_disclaimer_check():
    """RB-EGL-014: content_type outside disclosure-applicable set skips the disclaimer check."""
    verdict = ce.check_content_compliance(
        "Excited for my first week at Global Payments.",
        content_type="email",
    )
    assert not any(f.restriction_id == "disclosure-reminder" for f in verdict.findings)


# ---------------------------------------------------------------------------
# RB-EGL-015..017: dashboard rendering + dedup
# ---------------------------------------------------------------------------

def test_dashboard_marks_flagged_policy_with_warning_glyph():
    """RB-EGL-015: format_verdict_dashboard() marks flagged policies with a warning glyph."""
    verdict = ce.check_content_compliance(
        "On behalf of Global Payments, I can confirm this partnership.",
        content_type="linkedin_post",
    )
    dashboard = ce.format_verdict_dashboard(verdict)
    assert "⚠ Social Media" in dashboard
    assert "Overall Risk: HIGH" in dashboard


def test_dashboard_marks_clear_policies_with_check_glyph():
    """RB-EGL-016: format_verdict_dashboard() marks all-clear policies with a check glyph."""
    verdict = ce.check_content_compliance(
        "Thinking about the payments industry today.",
        content_type="linkedin_post",
    )
    dashboard = ce.format_verdict_dashboard(verdict)
    assert "✓ Social Media" in dashboard
    assert "✓ Code of Conduct" in dashboard
    assert "Overall Risk: LOW" in dashboard


def test_findings_deduplicated_by_restriction():
    """RB-EGL-017: findings deduplicated by restriction when multiple patterns match the same restriction."""
    verdict = ce.check_content_compliance(
        "On behalf of Global Payments, Global Payments announces our unreleased roadmap.",
        content_type="linkedin_post",
    )
    behalf_findings = [f for f in verdict.findings if f.restriction_id == "no-speaking-on-behalf-of-company"]
    assert len(behalf_findings) == 2  # both patterns for this restriction can match distinct spans
    dedup_guidance = [g for g in verdict.rewrite_guidance if "on behalf of" in g.lower()]
    assert len(dedup_guidance) == 1  # but rewrite_guidance is deduplicated per restriction
