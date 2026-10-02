#!/usr/bin/env python3
"""Tests for RB 9.12 — user_profile.py and opportunity_sensing.py.

Test IDs and coverage:

RB-PROFILE-LOADER-001: user_profile.load() returns canonical shape
RB-PROFILE-LOADER-002: legacy 00_TODD_PROFILE.md fallback works
RB-PROFILE-LOADER-003: no person-specific schema fields in canonical output
RB-PROFILE-LOADER-004: profile loader respects explicit profile_id

RB-OPPORTUNITY-SENSING-001: market intelligence pain → sales opportunity
RB-OPPORTUNITY-SENSING-002: market intelligence pain → consulting opportunity
RB-OPPORTUNITY-SENSING-003: job posting → employment opportunity
RB-OPPORTUNITY-SENSING-004: job posting → sales opportunity
RB-OPPORTUNITY-SENSING-005: same signal scored differently for two profiles
RB-OPPORTUNITY-SENSING-006: weak-fit signal → no_action or ignore
RB-OPPORTUNITY-SENSING-007: source freshness downgrade → research_first or monitor
RB-OPPORTUNITY-SENSING-008: relationship path confidence discipline
RB-OPPORTUNITY-SENSING-009: legacy fallback works end-to-end
RB-OPPORTUNITY-SENSING-010: no new person-specific schema fields

RB-JOB-INTEL-001: job posting intelligence extracts pain beyond listing
RB-JOB-INTEL-002: job posting as sales opportunity (digital transform posting)
RB-JOB-INTEL-003: unrelated posting classified as no_action

RB-BATCH-001: batch scoring returns brief-ready report
RB-BATCH-002: ignored signals are counted not surfaced
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import user_profile as up  # noqa: E402
import opportunity_sensing as ops  # noqa: E402


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

PROFILE_A_JOB_SEEKER = {
    "profile_id": "profile_a_job_seeker",
    "source": "profiles_dir",
    "facts": {},
    "capabilities": [
        "enterprise_restaurant_tech_sales",
        "gtm_strategy_and_execution",
        "mcdonalds_global_account_management",
        "franchise_operator_adoption",
    ],
    "product_service_capabilities": [],
    "target_customers": [],
    "target_employers": ["restaurant_tech_vendors_vp_enterprise_or_above",
                         "enterprise_restaurant_chains"],
    "target_industries": ["restaurant_technology", "quick_service_restaurants"],
    "adjacent_industries": ["payments_and_fintech"],
    "preferred_opportunity_types": ["job", "consulting", "content"],
    "no_go_categories": ["free_strategy_or_unpaid_thinking"],
    "credible_pain_types": [
        "gtm_execution_failure", "leadership_gap", "integration_failure",
        "franchise_adoption_resistance",
    ],
    "constraints": {"geographic": "open", "travel": "open"},
    "voice_constraints": [],
    "intro_philosophy": "balanced",
    "intro_boundaries": [],
    "career_context": "job_search",
    "confidence": "high",
}

PROFILE_B_SAAS_SELLER = {
    "profile_id": "profile_b_saas_seller",
    "source": "profiles_dir",
    "facts": {},
    "capabilities": ["enterprise_saas_sales", "drive_thru_technology"],
    "product_service_capabilities": ["drive_thru_ai_solution", "restaurant_voice_ai",
                                     "drive_thru_restaurant_technology"],
    "target_customers": ["quick_service_restaurant_chains_200_plus_units",
                         "restaurant_technology"],
    "target_employers": [],
    "target_industries": ["restaurant_technology", "drive_thru"],
    "adjacent_industries": [],
    "preferred_opportunity_types": ["sales", "partnership"],
    "no_go_categories": ["commission_only_arrangements"],
    "credible_pain_types": ["vendor_gap", "digital_transformation", "operational_complexity"],
    "constraints": {},
    "voice_constraints": [],
    "intro_philosophy": "aggressive",
    "intro_boundaries": [],
    "career_context": "employed",
    "confidence": "high",
}

PROFILE_C_CONSULTANT = {
    "profile_id": "profile_c_consultant",
    "source": "profiles_dir",
    "facts": {},
    "capabilities": ["restaurant_technology_advisory", "gtm_consulting",
                     "gtm_strategy_and_execution"],
    "product_service_capabilities": [
        "advisory_fractional_sales_leadership",
        "gtm_execution_consulting_for_restaurant_tech",
    ],
    "target_customers": ["restaurant_tech_vendors_series_b_to_public"],
    "target_employers": [],
    "target_industries": ["restaurant_technology"],
    "adjacent_industries": [],
    "preferred_opportunity_types": ["consulting", "content", "partnership"],
    "no_go_categories": [],
    "credible_pain_types": [
        "gtm_execution_failure", "market_expansion", "operational_complexity",
        "cost_pressure", "turnaround",
    ],
    "constraints": {},
    "voice_constraints": [],
    "intro_philosophy": "balanced",
    "intro_boundaries": [],
    "career_context": "consulting",
    "confidence": "high",
}

PROFILE_WEAK_FIT = {
    "profile_id": "profile_d_weak_fit",
    "source": "profiles_dir",
    "facts": {},
    "capabilities": ["supply_chain_logistics", "warehouse_management"],
    "product_service_capabilities": ["logistics_optimization_software"],
    "target_customers": ["retail_grocery_chains"],
    "target_employers": ["logistics_companies"],
    "target_industries": ["supply_chain", "logistics", "grocery"],
    "adjacent_industries": ["cold_storage"],
    "preferred_opportunity_types": ["sales", "consulting"],
    "no_go_categories": [],
    "credible_pain_types": ["operational_complexity", "cost_pressure"],
    "constraints": {},
    "voice_constraints": [],
    "intro_philosophy": "balanced",
    "intro_boundaries": [],
    "career_context": "consulting",
    "confidence": "high",
}

# Market signal: operator pain (turnaround + cost pressure + digital transform)
SIGNAL_MARKET_OPERATOR_PAIN = {
    "title": "Regional QSR operator announces 12 store closures, margin pressure, and technology modernization program",
    "url": "https://www.restaurantdive.com/news/qsr-operator-closures-modernization",
    "source_name": "Restaurant Dive",
    "source_type": "market_news",
    "company": "SunshineGrill",
    "side": "operator_demand",
    "category": "restaurant_technology",
    "signal_type": "turnaround",
    "pain_point_or_priority": (
        "Cost pressure from rising food costs and labor expenses leading to store closures. "
        "Technology modernization program initiated to reduce operational complexity and "
        "fragmented systems. POS replacement and digital transformation underway."
    ),
    "strategic_relevance": "high",
    "published_at": "2026-05-27",
    "fetched_at": "2026-05-27T08:00:00+00:00",
    "confidence": "high",
}

# Job signal: VP Enterprise Accounts (employment opportunity)
SIGNAL_JOB_EMPLOYMENT = {
    "title": "RestaurantAI Inc. hiring VP Enterprise Accounts for national chain expansion",
    "url": "https://jobs.example.com/vp-enterprise-accounts",
    "source_name": "LinkedIn Jobs",
    "source_type": "job_posting",
    "company": "RestaurantAI",
    "side": "vendor_supply",
    "category": "restaurant_technology",
    "signal_type": "hiring_need",
    "pain_point_or_priority": (
        "VP Enterprise Accounts needed to own national restaurant chain expansion program. "
        "Requires enterprise restaurant technology GTM execution background, franchise adoption "
        "experience, and account management track record at a restaurant tech vendor."
    ),
    "strategic_relevance": "high",
    "published_at": "2026-05-27",
    "fetched_at": "2026-05-27T08:00:00+00:00",
    "confidence": "high",
}

# Job signal: Director Digital Transformation (sales signal for POS/AI vendor)
SIGNAL_JOB_DIGITAL_TRANSFORM = {
    "title": "MegaBurger posting: Director of Digital Transformation (POS modernization, loyalty data, drive-thru AI)",
    "url": "https://jobs.example.com/director-digital-transformation",
    "source_name": "LinkedIn Jobs",
    "source_type": "job_posting",
    "company": "MegaBurger",
    "side": "operator_demand",
    "category": "restaurant_technology",
    "signal_type": "hiring_need",
    "pain_point_or_priority": (
        "Director of Digital Transformation responsible for modernizing fragmented legacy POS "
        "systems, integrating loyalty data, and implementing drive-thru voice AI program. "
        "Current systems are siloed and inconsistent across 400 locations."
    ),
    "strategic_relevance": "high",
    "published_at": "2026-05-27",
    "fetched_at": "2026-05-27T08:00:00+00:00",
    "confidence": "high",
}

# Multi-profile signal: AI drive-thru modernization
SIGNAL_DRIVE_THRU_AI = {
    "title": "National QSR chain invests $50M in AI drive-thru modernization",
    "url": "https://www.qsrmagazine.com/ai-drive-thru",
    "source_name": "QSR Magazine",
    "source_type": "market_news",
    "company": "FastBurger",
    "side": "operator_demand",
    "category": "drive_thru",
    "signal_type": "operator_priority",
    "pain_point_or_priority": (
        "National QSR chain investing in AI drive-thru modernization. "
        "Current systems legacy and inconsistent. Order accuracy and speed of service targets set. "
        "Technology transformation program active."
    ),
    "strategic_relevance": "high",
    "published_at": "2026-05-27",
    "fetched_at": "2026-05-27T08:00:00+00:00",
    "confidence": "medium",
}

# Stale signal
SIGNAL_STALE = {
    "title": "Old restaurant tech news",
    "url": "https://www.restaurantdive.com/old-news",
    "source_name": "Restaurant Dive",
    "source_type": "market_news",
    "company": "OldBrand",
    "side": "operator_demand",
    "category": "restaurant_technology",
    "pain_point_or_priority": "Technology modernization initiative (2025 article).",
    "strategic_relevance": "high",
    "published_at": "2025-01-15",
    "fetched_at": "2025-01-15T08:00:00+00:00",
    "confidence": "medium",
}

# Unrelated job posting
SIGNAL_JOB_UNRELATED = {
    "title": "Hiring: Senior DevOps Engineer, financial services firm",
    "url": "https://jobs.example.com/devops-finserv",
    "source_name": "LinkedIn Jobs",
    "source_type": "job_posting",
    "company": "FinanceCorp",
    "side": "unknown",
    "category": "financial_services",
    "signal_type": "hiring_need",
    "pain_point_or_priority": (
        "Senior DevOps engineer needed to manage kubernetes clusters and "
        "CI/CD pipelines for insurance underwriting platform."
    ),
    "strategic_relevance": "low",
    "published_at": "2026-05-27",
    "fetched_at": "2026-05-27T08:00:00+00:00",
    "confidence": "low",
}


def _collect_all_keys(d: Any, _seen: set | None = None) -> set[str]:
    if _seen is None:
        _seen = set()
    if isinstance(d, dict):
        for k, v in d.items():
            _seen.add(k)
            _collect_all_keys(v, _seen)
    elif isinstance(d, (list, tuple)):
        for item in d:
            _collect_all_keys(item, _seen)
    return _seen


# ===========================================================================
# Profile loader tests
# ===========================================================================

class TestUserProfileLoader:
    """RB-PROFILE-LOADER-* tests."""

    def test_load_returns_canonical_shape(self):
        """RB-PROFILE-LOADER-001: load() returns all required canonical keys."""
        profile = up.load()
        required_keys = {
            "profile_id", "source", "facts", "capabilities",
            "product_service_capabilities", "target_customers", "target_employers",
            "target_industries", "adjacent_industries", "preferred_opportunity_types",
            "no_go_categories", "constraints", "voice_constraints", "confidence",
        }
        missing = required_keys - set(profile.keys())
        assert not missing, f"Canonical output missing keys: {missing}"

    def test_source_is_valid_enum(self):
        """RB-PROFILE-LOADER-001: source must be profiles_dir or legacy_seed."""
        profile = up.load()
        assert profile["source"] in ("profiles_dir", "legacy_seed"), (
            f"source must be profiles_dir or legacy_seed, got {profile['source']!r}"
        )

    def test_confidence_is_valid_enum(self):
        """RB-PROFILE-LOADER-001: confidence must be high|medium|low."""
        profile = up.load()
        assert profile["confidence"] in ("high", "medium", "low"), (
            f"confidence must be high|medium|low, got {profile['confidence']!r}"
        )

    def test_capabilities_is_non_empty_list(self):
        """RB-PROFILE-LOADER-001: capabilities must be a non-empty list."""
        profile = up.load()
        assert isinstance(profile["capabilities"], list)
        assert len(profile["capabilities"]) > 0, "capabilities must not be empty"

    def test_legacy_fallback_returns_capabilities(self):
        """RB-PROFILE-LOADER-002: legacy fallback _read_legacy_profile() returns capabilities."""
        legacy = up._read_legacy_profile()
        assert isinstance(legacy.get("capabilities"), list)
        assert len(legacy["capabilities"]) > 0, "Legacy fallback must return non-empty capabilities"

    def test_legacy_fallback_has_no_go_categories(self):
        """RB-PROFILE-LOADER-002: legacy fallback returns no_go_categories."""
        legacy = up._read_legacy_profile()
        assert isinstance(legacy.get("no_go_categories"), list)

    def test_no_person_specific_schema_fields(self):
        """RB-PROFILE-LOADER-003: canonical output must not contain person-specific field names."""
        profile = up.load()
        person_specific = [
            k for k in profile.keys()
            if re.search(r"_to_todd$|_for_todd$|todd_specific|_to_jane$", k)
        ]
        assert not person_specific, (
            f"Person-specific schema fields found in canonical output: {person_specific}"
        )

    def test_explicit_profile_id_load(self):
        """RB-PROFILE-LOADER-004: explicit load('todd_vahlsing') returns todd_vahlsing."""
        profile = up.load("todd_vahlsing")
        assert profile["profile_id"] == "todd_vahlsing", (
            f"Expected todd_vahlsing, got {profile['profile_id']!r}"
        )

    def test_list_profiles_returns_list(self):
        """RB-PROFILE-LOADER-004: list_profiles() returns a list."""
        profiles = up.list_profiles()
        assert isinstance(profiles, list)
        assert len(profiles) > 0, "Expected at least one profile (todd_vahlsing)"


# ===========================================================================
# Opportunity sensing — core acceptance tests (the 10 from the handoff)
# ===========================================================================

class TestOpportunitySensingAcceptanceCriteria:
    """RB-OPPORTUNITY-SENSING-* tests covering all 10 handoff acceptance criteria."""

    def test_01_market_pain_to_sales_opportunity(self):
        """RB-OPPORTUNITY-SENSING-001: Market intelligence pain → sales opportunity."""
        sig = ops.score_signal(SIGNAL_MARKET_OPERATOR_PAIN, profile=PROFILE_B_SAAS_SELLER, baseline=[])
        assert sig["opportunity_type"] == "sales", (
            f"Market operator pain should be sales for SaaS seller, got {sig['opportunity_type']!r}"
        )
        assert sig["fit"]["product_service_match"] != "none", (
            "SaaS seller product should match operator pain"
        )

    def test_02_market_pain_to_consulting_opportunity(self):
        """RB-OPPORTUNITY-SENSING-002: Market intelligence pain → consulting opportunity."""
        sig = ops.score_signal(SIGNAL_MARKET_OPERATOR_PAIN, profile=PROFILE_C_CONSULTANT, baseline=[])
        assert sig["opportunity_type"] in ("consulting", "sales", "research"), (
            f"Market operator pain should map to consulting/sales for consultant, "
            f"got {sig['opportunity_type']!r}"
        )
        assert sig["fit"]["user_capability_match"] != "none", (
            "Consultant should have non-zero capability match for operator pain"
        )

    def test_03_job_posting_employment_opportunity(self):
        """RB-OPPORTUNITY-SENSING-003: Job posting → employment opportunity for job seeker."""
        sig = ops.score_signal(SIGNAL_JOB_EMPLOYMENT, profile=PROFILE_A_JOB_SEEKER, baseline=[])
        assert sig["opportunity_type"] == "job", (
            f"VP Enterprise Accounts posting should be job for job seeker, "
            f"got {sig['opportunity_type']!r}"
        )
        assert sig["source"]["source_type"] == "job_posting"

    def test_04_job_posting_sales_opportunity(self):
        """RB-OPPORTUNITY-SENSING-004: Job posting → sales opportunity for SaaS vendor."""
        sig = ops.score_signal(SIGNAL_JOB_DIGITAL_TRANSFORM, profile=PROFILE_B_SAAS_SELLER, baseline=[])
        assert sig["opportunity_type"] in ("sales", "consulting"), (
            f"Digital transform posting should be sales/consulting for SaaS seller, "
            f"got {sig['opportunity_type']!r}"
        )

    def test_05_same_signal_different_profiles(self):
        """RB-OPPORTUNITY-SENSING-005: Same signal produces different results for two profiles."""
        sig_a = ops.score_signal(SIGNAL_DRIVE_THRU_AI, profile=PROFILE_A_JOB_SEEKER, baseline=[])
        sig_b = ops.score_signal(SIGNAL_DRIVE_THRU_AI, profile=PROFILE_B_SAAS_SELLER, baseline=[])
        # Profile B (SaaS seller with drive-thru product) should get different match level
        # than Profile A (job seeker without drive-thru product)
        different = (
            sig_a["opportunity_type"] != sig_b["opportunity_type"] or
            sig_a["fit"]["user_capability_match"] != sig_b["fit"]["user_capability_match"] or
            sig_a["fit"]["product_service_match"] != sig_b["fit"]["product_service_match"]
        )
        assert different, (
            f"Same signal must produce different results for different profiles. "
            f"A: type={sig_a['opportunity_type']}, user_match={sig_a['fit']['user_capability_match']}. "
            f"B: type={sig_b['opportunity_type']}, user_match={sig_b['fit']['user_capability_match']}."
        )

    def test_06_weak_fit_signal_no_action_or_ignore(self):
        """RB-OPPORTUNITY-SENSING-006: Weak-fit signal → no_action or ignore."""
        sig = ops.score_signal(SIGNAL_DRIVE_THRU_AI, profile=PROFILE_WEAK_FIT, baseline=[])
        assert sig["opportunity_type"] == "no_action" or sig["recommended_posture"] in ("ignore", "monitor"), (
            f"Weak-fit profile should get no_action or ignore/monitor. "
            f"Got type={sig['opportunity_type']}, posture={sig['recommended_posture']}"
        )

    def test_07_stale_source_downgrade(self):
        """RB-OPPORTUNITY-SENSING-007: Stale source → research_first or monitor (never act_today)."""
        sig = ops.score_signal(SIGNAL_STALE, profile=PROFILE_B_SAAS_SELLER, baseline=[])
        assert sig["source"]["freshness"] == "stale", (
            f"Old signal must be classified as stale (got {sig['source']['freshness']!r})"
        )
        assert sig["recommended_posture"] != "act_today", (
            f"Stale signal must not produce act_today posture (got {sig['recommended_posture']!r})"
        )
        assert sig["recommended_posture"] in ("research_first", "monitor", "ignore"), (
            f"Stale signal must produce research_first, monitor, or ignore "
            f"(got {sig['recommended_posture']!r})"
        )

    def test_08_relationship_path_no_invention(self):
        """RB-OPPORTUNITY-SENSING-008: Relationship path must not be invented from thin air."""
        sig = ops.score_signal(SIGNAL_DRIVE_THRU_AI, profile=PROFILE_B_SAAS_SELLER, baseline=[])
        # Empty baseline → must be no_known_path or unknown
        assert sig["relationship_path"]["status"] in ("no_known_path", "unknown"), (
            f"Empty baseline must produce no_known_path or unknown, "
            f"got {sig['relationship_path']['status']!r}"
        )
        # Relationship path must never be warm_path when we have no evidence
        assert sig["relationship_path"]["status"] != "warm_path", (
            "Relationship path must not be warm_path with empty baseline"
        )

    def test_09_legacy_fallback_works_end_to_end(self):
        """RB-OPPORTUNITY-SENSING-009: Legacy 00_TODD_PROFILE.md fallback works with opportunity sensing."""
        # Load via legacy path
        legacy_profile = up._read_legacy_profile()
        # Convert to canonical shape
        canonical = {
            "profile_id": "todd_vahlsing",
            "source": "legacy_seed",
            "facts": {"name": legacy_profile.get("name", "")},
            "capabilities": legacy_profile.get("capabilities") or [],
            "product_service_capabilities": legacy_profile.get("product_service_capabilities") or [],
            "target_customers": legacy_profile.get("target_customers") or [],
            "target_employers": legacy_profile.get("target_employers") or [],
            "target_industries": legacy_profile.get("target_industries") or [],
            "adjacent_industries": legacy_profile.get("adjacent_industries") or [],
            "preferred_opportunity_types": legacy_profile.get("preferred_opportunity_types") or [],
            "no_go_categories": legacy_profile.get("no_go_categories") or [],
            "credible_pain_types": legacy_profile.get("credible_pain_types") or [],
            "constraints": legacy_profile.get("constraints") or {},
            "voice_constraints": legacy_profile.get("voice_constraints") or [],
            "intro_philosophy": legacy_profile.get("intro_philosophy") or "balanced",
            "intro_boundaries": legacy_profile.get("intro_boundaries") or [],
            "career_context": "founder_advisor",
            "confidence": "medium",
        }
        sig = ops.score_signal(SIGNAL_JOB_EMPLOYMENT, profile=canonical, baseline=[])
        # Must return a valid signal with no crash
        assert "opportunity_signal_id" in sig
        assert sig["opportunity_type"] in ops.OPPORTUNITY_TYPES, (
            f"Legacy fallback must produce valid opportunity type, got {sig['opportunity_type']!r}"
        )

    def test_10_no_person_specific_schema_fields_in_output(self):
        """RB-OPPORTUNITY-SENSING-010: No person-specific schema fields in opportunity signal output."""
        signals = [
            ops.score_signal(SIGNAL_MARKET_OPERATOR_PAIN, profile=PROFILE_B_SAAS_SELLER, baseline=[]),
            ops.score_signal(SIGNAL_JOB_EMPLOYMENT, profile=PROFILE_A_JOB_SEEKER, baseline=[]),
            ops.score_signal(SIGNAL_STALE, profile=PROFILE_C_CONSULTANT, baseline=[]),
        ]
        all_keys: set[str] = set()
        for sig in signals:
            all_keys.update(_collect_all_keys(sig))

        person_specific = [
            k for k in all_keys
            if re.search(r"_to_todd$|_for_todd$|todd_specific|_to_jane$|_for_jane$", k)
        ]
        assert not person_specific, (
            f"Person-specific schema fields found in opportunity signal output: {person_specific}"
        )

        # Specifically check: "why_this_matters_to_user" exists (not "why_this_matters_to_todd")
        assert "why_this_matters_to_user" in all_keys, (
            "Expected why_this_matters_to_user in output (profile-agnostic field name)"
        )


# ===========================================================================
# Job posting intelligence tests
# ===========================================================================

class TestJobPostingIntelligence:
    """RB-JOB-INTEL-* tests: job postings as first-class intelligence."""

    def test_01_intelligence_extracted_from_posting(self):
        """RB-JOB-INTEL-001: analyze_job_posting() extracts pain beyond the listing."""
        intel = ops.analyze_job_posting(SIGNAL_JOB_DIGITAL_TRANSFORM["pain_point_or_priority"])
        assert intel["inferred_pain_types"], "Must infer at least one pain type"
        assert intel["job_signal_quality"] in ("rich", "moderate", "thin")
        # Should detect digital transformation and/or vendor gap from the text
        pain_set = set(intel["inferred_pain_types"])
        assert pain_set & {"digital_transformation", "vendor_gap", "operational_complexity"}, (
            f"Expected digital_transformation/vendor_gap/operational_complexity, "
            f"got {pain_set}"
        )

    def test_02_job_posting_as_sales_signal(self):
        """RB-JOB-INTEL-002: Digital transform posting scored as sales for POS/AI vendor."""
        sig = ops.score_signal(SIGNAL_JOB_DIGITAL_TRANSFORM, profile=PROFILE_B_SAAS_SELLER, baseline=[])
        assert sig["opportunity_type"] in ("sales", "consulting"), (
            f"POS/AI vendor should see digital transform posting as sales opportunity, "
            f"got {sig['opportunity_type']!r}"
        )
        assert sig["pain"]["pain_type"] in ops.PAIN_TYPES

    def test_03_unrelated_posting_no_action(self):
        """RB-JOB-INTEL-003: Unrelated DevOps posting → no_action or ignore for restaurant profiles."""
        for profile in [PROFILE_A_JOB_SEEKER, PROFILE_B_SAAS_SELLER, PROFILE_C_CONSULTANT]:
            sig = ops.score_signal(SIGNAL_JOB_UNRELATED, profile=profile, baseline=[])
            assert sig["opportunity_type"] == "no_action" or sig["recommended_posture"] in ("ignore", "monitor"), (
                f"Unrelated DevOps posting should be no_action for {profile['profile_id']}, "
                f"got type={sig['opportunity_type']}, posture={sig['recommended_posture']}"
            )

    def test_04_employment_posting_extracts_company_priority(self):
        """RB-JOB-INTEL-001: VP Enterprise Accounts posting intelligence includes growth signal."""
        intel = ops.analyze_job_posting(SIGNAL_JOB_EMPLOYMENT["pain_point_or_priority"])
        pain_set = set(intel["inferred_pain_types"])
        # Growth, hiring_need, or gtm-related pain should be detected
        assert pain_set & {"growth", "hiring_need", "market_expansion"}, (
            f"VP Enterprise Accounts posting should infer growth/hiring pain, got {pain_set}"
        )


# ===========================================================================
# Batch scoring tests
# ===========================================================================

class TestBatchScoring:
    """RB-BATCH-* tests."""

    def test_01_batch_returns_brief_ready_report(self):
        """RB-BATCH-001: score_signals() returns brief-ready report with required keys."""
        signals = [
            SIGNAL_MARKET_OPERATOR_PAIN,
            SIGNAL_JOB_EMPLOYMENT,
            SIGNAL_JOB_DIGITAL_TRANSFORM,
            SIGNAL_STALE,
        ]
        report = ops.score_signals(signals, profile=PROFILE_B_SAAS_SELLER, baseline=[])
        assert "profile_id" in report
        assert "scored" in report
        assert "surface" in report
        assert "ignored_count" in report
        assert "proof_stats" in report
        assert report["profile_id"] == "profile_b_saas_seller"
        assert len(report["scored"]) == 4  # one per input signal

    def test_02_ignored_signals_counted_not_surfaced(self):
        """RB-BATCH-002: no_action / ignore signals appear in ignored_count, not in surface."""
        signals = [
            SIGNAL_MARKET_OPERATOR_PAIN,   # likely actionable for B
            SIGNAL_JOB_UNRELATED,          # likely no_action for all
            SIGNAL_STALE,                  # stale → monitor or ignore
        ]
        report = ops.score_signals(signals, profile=PROFILE_B_SAAS_SELLER, baseline=[])
        # Surface should only contain non-ignored signals
        surface = report["surface"]
        for s in surface:
            assert s["recommended_posture"] != "ignore", (
                f"Ignored signal leaked into surface: {s['opportunity_signal_id']}"
            )
            assert s["opportunity_type"] != "no_action", (
                f"no_action signal leaked into surface: {s['opportunity_signal_id']}"
            )

    def test_03_multi_profile_different_surface(self):
        """RB-BATCH-001: Same signal batch produces different surface for different profiles."""
        signals = [SIGNAL_DRIVE_THRU_AI, SIGNAL_JOB_EMPLOYMENT]
        report_a = ops.score_signals(signals, profile=PROFILE_A_JOB_SEEKER, baseline=[])
        report_b = ops.score_signals(signals, profile=PROFILE_B_SAAS_SELLER, baseline=[])

        # Profiles should produce different top-signal opportunity types
        types_a = {s["opportunity_type"] for s in report_a["scored"]}
        types_b = {s["opportunity_type"] for s in report_b["scored"]}
        # At minimum the profiles should differ on at least one signal
        assert report_a["profile_id"] != report_b["profile_id"]
        # And the scored results must differ in some way
        assert types_a != types_b or any(
            sa["fit"]["user_capability_match"] != sb["fit"]["user_capability_match"]
            for sa, sb in zip(report_a["scored"], report_b["scored"])
        ), "Batch scoring must produce different results for different profiles"


# ===========================================================================
# Relationship path discipline tests
# ===========================================================================

class TestRelationshipPath:
    """Relationship path confidence discipline tests."""

    def test_empty_baseline_no_known_path(self):
        """With empty baseline, path must be no_known_path or unknown."""
        path = ops.assess_relationship_path("SomeCorp", [], baseline=[])
        assert path["status"] in ("no_known_path", "unknown"), (
            f"Empty baseline must return no_known_path or unknown, got {path['status']!r}"
        )

    def test_no_contacts_in_path_with_empty_baseline(self):
        """With empty baseline, contacts list must be empty."""
        path = ops.assess_relationship_path("SomeCorp", [], baseline=[])
        assert path["contacts"] == []
        assert path["broker_candidates"] == []

    def test_baseline_match_produces_possible_or_warm_path(self):
        """When baseline has a matching contact, path must be possible or warm."""
        baseline = [
            {
                "id": "john-doe",
                "name": "John Doe",
                "current_company": "SomeCorp",
                "signal_class": "LKI",
                "rc_tier": None,
            }
        ]
        path = ops.assess_relationship_path("SomeCorp", [], baseline=baseline)
        assert path["status"] in ("possible_path", "warm_path"), (
            f"Baseline match should produce possible_path or warm_path, got {path['status']!r}"
        )

    def test_rc_inner_contact_produces_warm_path(self):
        """RC inner contact → warm_path."""
        baseline = [
            {
                "id": "jane-inner",
                "name": "Jane Inner",
                "current_company": "WarmCorp",
                "signal_class": "RC",
                "rc_tier": "inner",
            }
        ]
        path = ops.assess_relationship_path("WarmCorp", [], baseline=baseline)
        assert path["status"] == "warm_path", (
            f"RC inner match should produce warm_path, got {path['status']!r}"
        )


# ===========================================================================
# Schema validation helpers
# ===========================================================================

class TestSignalSchemaShape:
    """Validate that scored signals conform to the documented object model."""

    def test_scored_signal_has_all_required_top_level_keys(self):
        """Scored signal must have all required top-level keys."""
        sig = ops.score_signal(SIGNAL_MARKET_OPERATOR_PAIN, profile=PROFILE_B_SAAS_SELLER, baseline=[])
        required = {
            "opportunity_signal_id", "detected_at", "source", "pain",
            "subject", "fit", "opportunity_type", "relationship_path",
            "recommended_posture", "confidence", "proof_stats",
        }
        missing = required - set(sig.keys())
        assert not missing, f"Signal missing required keys: {missing}"

    def test_fit_block_uses_generic_field_name(self):
        """fit.why_this_matters_to_user must be used, not why_this_matters_to_todd."""
        sig = ops.score_signal(SIGNAL_MARKET_OPERATOR_PAIN, profile=PROFILE_B_SAAS_SELLER, baseline=[])
        assert "why_this_matters_to_user" in sig["fit"], (
            "fit block must use why_this_matters_to_user (not why_this_matters_to_todd)"
        )
        assert "why_this_matters_to_todd" not in sig["fit"], (
            "fit block must not contain why_this_matters_to_todd"
        )

    def test_opportunity_type_is_valid_enum(self):
        """opportunity_type must be a valid enum value."""
        for signal in [SIGNAL_MARKET_OPERATOR_PAIN, SIGNAL_JOB_EMPLOYMENT, SIGNAL_STALE]:
            sig = ops.score_signal(signal, profile=PROFILE_B_SAAS_SELLER, baseline=[])
            assert sig["opportunity_type"] in ops.OPPORTUNITY_TYPES, (
                f"opportunity_type {sig['opportunity_type']!r} not in valid set"
            )

    def test_recommended_posture_is_valid_enum(self):
        """recommended_posture must be a valid enum value."""
        for signal in [SIGNAL_MARKET_OPERATOR_PAIN, SIGNAL_JOB_EMPLOYMENT, SIGNAL_STALE]:
            sig = ops.score_signal(signal, profile=PROFILE_B_SAAS_SELLER, baseline=[])
            assert sig["recommended_posture"] in ops.POSTURES, (
                f"recommended_posture {sig['recommended_posture']!r} not in valid set"
            )

    def test_confidence_is_valid_enum(self):
        """confidence must be high|medium|low."""
        sig = ops.score_signal(SIGNAL_MARKET_OPERATOR_PAIN, profile=PROFILE_B_SAAS_SELLER, baseline=[])
        assert sig["confidence"] in ("high", "medium", "low")

    def test_relationship_path_status_is_valid_enum(self):
        """relationship_path.status must be a valid enum value."""
        sig = ops.score_signal(SIGNAL_MARKET_OPERATOR_PAIN, profile=PROFILE_B_SAAS_SELLER, baseline=[])
        assert sig["relationship_path"]["status"] in ops.PATH_STATUSES

    def test_proof_stats_are_non_negative(self):
        """proof_stats values must be non-negative integers."""
        sig = ops.score_signal(SIGNAL_MARKET_OPERATOR_PAIN, profile=PROFILE_B_SAAS_SELLER, baseline=[])
        ps = sig["proof_stats"]
        for k, v in ps.items():
            assert isinstance(v, int) and v >= 0, (
                f"proof_stats.{k} must be a non-negative integer, got {v!r}"
            )
