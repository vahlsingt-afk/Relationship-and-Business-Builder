#!/usr/bin/env python3
"""
campaign_engine.py — Conference Campaign Intelligence Engine (Phase 1: Roster Engine).

Turns a conference/user-conference/webinar into a structured, config-driven
campaign: given the operator's own network already in `baseline_index.json`
(LinkedIn connections + personal contacts), determine who should be invited,
score and tier them, reconcile a registration-list export against the
roster, and track status (invited/registered/declined/attended) over time.

Event type is a config setting (`system/campaigns/<campaign_id>/config.yaml`),
not new code — a future conference (MURTEC, NRA Show, ...) is a new config
file, not a new script.

**Hard data-scope guard**: this engine refuses to run unless a campaign's
config declares `data_scope.source: personal_network_only`. It reads
`baseline_index.json` — the operator's own network — read-only, and nothing
else. It does not open, ingest, or reference any employer CRM/customer
export. See `system/employers/global-payments/policies/code-of-conduct.yaml`
(open AI-governance flag on employer-confidential data) for why this
boundary is enforced at runtime, not left as a convention.

5-stage pattern (mirrors hubspot_ingest.py):
  Stage 1 — Load:        baseline (read-only) + campaign config + prior roster
  Stage 2 — Eligibility:  include/exclude rules -> candidate set + excluded list
  Stage 3 — Score/Tier:   config-driven point weights -> explainable score_breakdown -> tier
  Stage 4 — Reconcile:    registration-list CSV -> identity_matcher -> status transitions
  Stage 5 — Report:       roster.json, roster_report.md, company_rollup.json,
                          company_dashboard.md, delta report, ri_events lifecycle facts

Storage (all under system/campaigns/<campaign_id>/, never scattered into
baseline_index.json):
    config.yaml           — campaign definition (hand-authored)
    roster.json           — authoritative per-contact score/tier/status
    roster_report.md       — rendered ranked roster with score breakdowns
    company_rollup.json    — company-level aggregation
    company_dashboard.md   — rendered company report

Drop location for registration-list exports:
    system/inbox/conference_exports/   (*.csv)

CLI:
    python3 campaign_engine.py --campaign <id> --build-roster [--dry-run]
    python3 campaign_engine.py --campaign <id> --reconcile-registrations PATH.csv [--dry-run]
    python3 campaign_engine.py --campaign <id> --set-status CONTACT_ID STATUS [--reason TEXT] [--dry-run]
    python3 campaign_engine.py --campaign <id> --report
    python3 campaign_engine.py --list
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import identity_matcher as im  # noqa: E402
import ri_events  # noqa: E402
import interaction_capture  # noqa: E402
import xlsx_safety  # noqa: E402

CAMPAIGNS_DIR = core.SYSTEM_DIR / "campaigns"
REGISTRY_PATH = CAMPAIGNS_DIR / "registry.yaml"
EXPORTS_DIR = core.INBOX_DIR / "conference_exports"
DELTAS_DIR = core.SYSTEM_DIR / "deltas"
OPPORTUNITIES_PATH = core.SYSTEM_DIR / "tracked_opportunities.json"

VALID_STATUSES = (
    "not_yet_invited",
    "invited",
    "registered",
    "declined",
    "attended",
    "no_show",
    "follow_up_due",
    "follow_up_complete",
)

# Statuses that mean "this person's roster membership is settled" — once
# reached, subsequent --build-roster runs carry the entry forward unchanged
# rather than re-running eligibility/scoring against them. Eligibility rules
# govern who *joins* the roster, not who stays on it.
SETTLED_STATUSES = ("registered", "declined", "attended", "no_show")

# The enumerated exclusion vocabulary — every exclude_if_any rule's `reason`
# should be one of these so the roster/company reports stay auditable
# against a fixed set of categories rather than free-text per config author.
# Not enforced as a hard error (a config predating this list shouldn't break),
# but load_campaign_config() warns when a reason falls outside it.
VALID_EXCLUSION_REASONS = (
    "already_invited",
    "already_registered",
    "not_restaurant_operator",
    "not_first_degree_connection",
    "vendor",
    "consultant",
    "consultant_or_vendor",  # kept for backward compat with existing configs
    "recruiter",
    "employee_of_hosting_company",
    "competitor",
    "duplicate",
    "insufficient_relationship_confidence",
    # Added 2026-07-13 for the relationship-first model's explicit exclude
    # categories — each a curated current_company_in list (same pattern as
    # "competitor"), not a keyword heuristic: these are named-entity
    # judgments the operator makes, not something a company name reliably
    # signals on its own.
    "restaurant_technology_vendor",
    "payment_company",
    "agency",
    "media",
    "analyst",
    "event_specific_exclusion",  # e.g. McDonald's operators/franchisees for this campaign only
    # Added 2026-07-14: is_restaurant_operator's keyword heuristic matches
    # "qsr"/"restaurant" as standalone words, which is correct for real
    # brands but also catches industry-adjacent orgs that use the same
    # vocabulary without being an actual multi-unit restaurant brand/
    # franchise (a loss-prevention vendor, a networking/community group,
    # ...). Distinct from restaurant_technology_vendor because these
    # aren't confirmed vendors either — just confirmed non-operators.
    "not_a_restaurant_brand",
)

DEFAULT_EXEC_KEYWORDS = [
    "vp", "svp", "evp", "chief", "president", "owner", "founder",
    "ceo", "coo", "cfo", "managing director", "partner",
]
DEFAULT_TECH_OPS_KEYWORDS = [
    "cto", "cio", "vp technology", "vp of technology", "vp operations",
    "vp of operations", "vp it", "director of technology",
    "director of operations", "head of technology", "head of operations",
    "digital", "it director",
]

# Heuristic keyword match against current_company for "does this person work
# at a restaurant/foodservice operator" — same pattern as the executive/
# tech-ops keyword heuristics: transparent, overridable via a manual tag,
# not a hidden classifier. Deliberately narrower than linkedin_ingest.py's
# TARGET_DOMAIN_RX (which also matches "ai"/"payments"/"automation" for
# restaurant-*tech* adjacency reporting) — this one is scoped to identifying
# the operator/brand itself, not vendors serving the industry.
DEFAULT_RESTAURANT_OPERATOR_KEYWORDS = [
    "restaurant", "restaurants", "cafe", "café", "coffee", "pizza", "pizzeria",
    "burger", "chicken", "taco", "bbq", "barbecue", "grill", "diner", "bistro",
    "bagel", "donut", "doughnut", "ice cream", "creamery", "sandwich", "deli",
    "bakery", "brewery", "pub", "steakhouse", "buffet", "foodservice",
    "food service", "qsr", "fast casual", "hospitality group", "dining",
    "smoothie", "kitchen",
]

# Same heuristic pattern, for the "recruiter" exclusion reason — mirrors
# linkedin_ingest.py's RECRUITER_RX vocabulary so the two stay consistent.
DEFAULT_RECRUITER_KEYWORDS = [
    "recruit", "recruiter", "talent acquisition", "talent partner",
    "headhunter", "executive search", "sourcer", "people partner",
    "hr business partner",
]


def _keyword_hit(text: str, keywords: list[str]) -> bool:
    """Word-boundary keyword match. Plain substring containment (the
    original pattern here) false-positives on short tokens embedded in an
    unrelated name — e.g. 'qsr' inside 'QSRSoft', 'pub' inside 'Republic' —
    which is exactly how a restaurant-tech vendor (QSRSoft) got scored as a
    restaurant operator and surfaced in Tier 1 once relationship-first
    scoring stopped masking company-heuristic false positives (2026-07-13
    live verification)."""
    for kw in keywords:
        if re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", text):
            return True
    return False


class CampaignDataScopeError(RuntimeError):
    """Raised when a campaign config does not declare personal_network_only scope."""


class CampaignNotFoundError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Stage 1 — Load
# ---------------------------------------------------------------------------

def _campaign_dir(campaign_id: str) -> Path:
    return CAMPAIGNS_DIR / campaign_id


def load_campaign_config(campaign_id: str) -> dict[str, Any]:
    config_path = _campaign_dir(campaign_id) / "config.yaml"
    if not config_path.exists():
        raise CampaignNotFoundError(f"No campaign config at {config_path}")
    return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}


def validate_exclusion_reasons(config: dict[str, Any]) -> list[str]:
    """Non-fatal check: every exclude_if_any rule's `reason` should be one of
    VALID_EXCLUSION_REASONS so the audit trail stays against a fixed
    vocabulary. Returns warning strings; does not raise."""
    warnings: list[str] = []
    for rule in (config.get("eligibility") or {}).get("exclude_if_any") or []:
        reason = rule.get("reason")
        if reason and reason not in VALID_EXCLUSION_REASONS:
            warnings.append(
                f"exclude_if_any rule has reason={reason!r}, not in the enumerated "
                f"VALID_EXCLUSION_REASONS vocabulary: {VALID_EXCLUSION_REASONS}"
            )
    return warnings


def _assert_personal_network_scope(config: dict[str, Any]) -> None:
    scope = (config.get("data_scope") or {}).get("source")
    if scope != "personal_network_only":
        raise CampaignDataScopeError(
            f"Refusing to run: data_scope.source={scope!r}, expected "
            "'personal_network_only'. This engine is scoped to the operator's "
            "own personal network (baseline_index.json) only — it must never "
            "be pointed at an employer CRM/customer export."
        )


def _load_roster(campaign_id: str) -> dict[str, Any] | None:
    path = _campaign_dir(campaign_id) / "roster.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_opportunities(path: Path | None = None) -> list[dict]:
    path = path or OPPORTUNITIES_PATH
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("opportunities", [])


def _known_customer_contacts_path(campaign_id: str) -> Path:
    return _campaign_dir(campaign_id) / "known_customer_contacts.json"


def _load_known_customer_contacts(campaign_id: str) -> dict[str, Any]:
    path = _known_customer_contacts_path(campaign_id)
    if not path.exists():
        return {"contacts": []}
    return json.loads(path.read_text(encoding="utf-8"))


def list_campaigns() -> list[dict[str, Any]]:
    if REGISTRY_PATH.exists():
        registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or {}
        return registry.get("campaigns", [])
    if not CAMPAIGNS_DIR.is_dir():
        return []
    return [
        {"id": p.parent.name}
        for p in sorted(CAMPAIGNS_DIR.glob("*/config.yaml"))
    ]


def refresh_active_campaigns(*, baseline_path: Path | None = None, opportunities_path: Path | None = None, dry_run: bool = False) -> list[dict[str, Any]]:
    """Living Campaign: rebuild every active campaign's roster in place.
    Intended to be called (best-effort, never fatal to the caller) by
    ingest scripts right after they write new baseline data, so the
    campaign detects new LinkedIn connections/registrations/company changes
    without anyone having to remember to re-run --build-roster by hand.
    build_roster() already only recomputes non-settled prospects and diffs
    against the prior roster — this never regenerates from scratch, it just
    triggers the existing incremental-refresh behavior automatically."""
    results = []
    for c in list_campaigns():
        if c.get("status") not in (None, "active"):
            continue  # skip archived/closed campaigns — don't burn cycles or clutter delta reports
        campaign_id = c["id"]
        try:
            result = build_roster(
                campaign_id, baseline_path=baseline_path,
                opportunities_path=opportunities_path, dry_run=dry_run,
            )
        except Exception as exc:  # noqa: BLE001 — never let a campaign refresh break the caller's ingest
            result = {"ok": False, "campaign_id": campaign_id, "error": str(exc)}
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Stage 2 — Eligibility
# ---------------------------------------------------------------------------

def _norm_company(name: str | None) -> str:
    return im.norm_name(name)


def _entry_tags(entry: dict) -> set[str]:
    return set(entry.get("tags") or [])


def is_first_degree_linkedin_connection(entry: dict) -> bool:
    """Proxy for 'is this a confirmed first-degree LinkedIn connection': a
    LinkedIn connections export only ever contains first-degree connections,
    so the presence of a linkedin_url or a linkedin_export-tagged source is
    the closest real signal baseline carries. Contacts sourced only from
    HubSpot/Apple Contacts/manual intake don't get this — they may be real
    relationships, just not LinkedIn-graph-confirmed ones."""
    if entry.get("linkedin_url"):
        return True
    return any("linkedin" in (s or "").lower() for s in (entry.get("sources") or []))


def is_recruiter(entry: dict, config: dict) -> bool:
    """Same transparent keyword-heuristic pattern as is_restaurant_operator —
    overridable via a 'recruiter-override'/'recruiter-exclude' tag pair."""
    tags = _entry_tags(entry)
    if "recruiter-exclude" in tags:
        return False
    if "recruiter-override" in tags:
        return True
    role = (entry.get("current_role") or "").lower()
    if not role:
        return False
    keywords = [k.lower() for k in (config.get("scoring") or {}).get("recruiter_keywords") or DEFAULT_RECRUITER_KEYWORDS]
    return _keyword_hit(role, keywords)


def _rule_condition_matches(
    entry: dict, rule: dict, *,
    known_registered: set[str] = frozenset(),
    known_customer_ids: set[str] = frozenset(),
    config: dict | None = None,
) -> bool:
    if "is_known_customer" in rule:
        return (entry.get("id") in known_customer_ids) == bool(rule["is_known_customer"])
    if "tag_in" in rule:
        return bool(_entry_tags(entry) & set(rule["tag_in"]))
    if "signal_class_in" in rule:
        return entry.get("signal_class") in set(rule["signal_class_in"])
    if "current_role_matches" in rule:
        role = (entry.get("current_role") or "").lower()
        return any(kw.lower() in role for kw in rule["current_role_matches"])
    if "current_company_in" in rule:
        # employment_status guard is defense-in-depth alongside the current_company
        # null check: a no_stated_current_role person (RB ended-role cleanup,
        # 2026-08-06) must never satisfy a current-company eligibility rule.
        if entry.get("employment_status") == "no_stated_current_role":
            return False
        company = _norm_company(entry.get("current_company"))
        targets = {_norm_company(c) for c in rule["current_company_in"]}
        return bool(company) and company in targets
    if "current_company_equals" in rule:
        if entry.get("employment_status") == "no_stated_current_role":
            return False
        return bool(entry.get("current_company")) and _norm_company(entry.get("current_company")) == _norm_company(rule["current_company_equals"])
    if "already_registered" in rule:
        is_known = entry.get("id") in known_registered
        return is_known == bool(rule["already_registered"])
    if "is_first_degree_linkedin" in rule:
        return is_first_degree_linkedin_connection(entry) == bool(rule["is_first_degree_linkedin"])
    if "is_recruiter" in rule:
        return is_recruiter(entry, config or {}) == bool(rule["is_recruiter"])
    raise ValueError(f"Unrecognized eligibility rule clause: {rule!r}")


def _describe_rule(rule: dict) -> str:
    """Human-readable citation of which business rule a match satisfied —
    used for the per-decision audit trail (Decision Accountability)."""
    for key in ("is_known_customer", "tag_in", "signal_class_in", "current_role_matches",
                "current_company_in", "current_company_equals", "already_registered",
                "is_first_degree_linkedin", "is_recruiter"):
        if key in rule:
            return f"{key}={rule[key]!r}"
    return repr(rule)


def is_included(entry: dict, config: dict, *, known_customer_ids: set[str] = frozenset()) -> tuple[bool, list[str]]:
    """OR logic across include_if_any. No include rules configured means
    every baseline entry is a candidate (excludes still apply). Known-customer
    membership always qualifies regardless of other include rules — a
    confirmed customer contact is inherently a legitimate prospect.

    Returns (included, matched_rule_descriptions) — the descriptions are the
    per-contact citation of *which* business rule was satisfied, not just a
    yes/no answer."""
    if entry.get("id") in known_customer_ids:
        return True, ["is_known_customer=True (known_customer_contacts.json)"]
    eligibility = config.get("eligibility") or {}
    rules = eligibility.get("include_if_any") or []
    if not rules:
        included, matched = True, ["no include_if_any rules configured — every baseline entry is a candidate"]
    else:
        matched = [_describe_rule(r) for r in rules if _rule_condition_matches(entry, r, config=config)]
        included = bool(matched)

    # require_restaurant_affiliation — an additional AND-gate, opt-in per
    # campaign (config.eligibility.require_restaurant_affiliation: true).
    # Added 2026-07-13: relationship-first scoring surfaced restaurant-tech
    # vendors and other non-operator contacts in Tier 1 purely because they
    # held an executive title and a strong personal relationship — neither
    # of which implies they work at a restaurant brand/franchise the
    # conference is actually targeting. include_if_any rules stay OR-based
    # (multiple independent signals of "worth considering"); this gate is a
    # separate, narrower requirement layered on top only when a campaign
    # asks for it. A 'restaurant-affiliation-override' tag always wins, for
    # a legitimate contact the keyword heuristic misses.
    if included and eligibility.get("require_restaurant_affiliation"):
        if "restaurant-affiliation-override" not in _entry_tags(entry) and not is_restaurant_operator(entry, config):
            return False, []
    return included, matched


def excluded_reason(entry: dict, config: dict, *, known_registered: set[str]) -> tuple[str | None, str | None]:
    """First matching exclude rule wins. Returns (reason, mode) or (None, None)."""
    for rule in (config.get("eligibility") or {}).get("exclude_if_any") or []:
        match_keys = {k: v for k, v in rule.items() if k not in ("reason", "mode")}
        if _rule_condition_matches(entry, match_keys, known_registered=known_registered, config=config):
            return rule.get("reason", "excluded"), rule.get("mode", "exclude")
    return None, None


def is_restaurant_operator(entry: dict, config: dict) -> bool:
    """Heuristic keyword match on current_company — same transparent,
    overridable pattern as the executive/tech-ops title heuristics. A
    'restaurant-operator-override' tag always wins (both to add a company
    the keyword list misses, and to remove a false positive by pairing with
    a 'restaurant-operator-exclude' tag)."""
    tags = _entry_tags(entry)
    if "restaurant-operator-exclude" in tags:
        return False
    if "restaurant-operator-override" in tags:
        return True
    company = (entry.get("current_company") or "").lower()
    if not company:
        return False
    keywords = [k.lower() for k in (config.get("scoring") or {}).get("restaurant_operator_keywords") or DEFAULT_RESTAURANT_OPERATOR_KEYWORDS]
    return _keyword_hit(company, keywords)


# ---------------------------------------------------------------------------
# Stage 3 — Score + tier
# ---------------------------------------------------------------------------

def score_entry(
    entry: dict, config: dict, *, opportunities: list[dict],
    known_customer_ids: set[str] = frozenset(),
    recent_engagement_by_id: dict[str, str] | None = None,
    today: date | None = None,
    currently_invited: bool = False,
) -> tuple[int, list[dict]]:
    """Every point awarded is recorded in the breakdown — explainable, not a
    black box. Returns (total_score, score_breakdown)."""
    scoring = config.get("scoring") or {}
    weights = scoring.get("weights") or {}
    breakdown: list[dict] = []
    total = 0

    def award(dimension: str, points: int, source: str) -> None:
        nonlocal total
        if points:
            total += points
            breakdown.append({"dimension": dimension, "points": points, "source": source})

    # Enterprise Brand — user-curated named list, no company-size field exists in baseline.
    eb_points = weights.get("enterprise_brand", 0)
    if eb_points:
        eb_list = {_norm_company(c) for c in scoring.get("enterprise_brand_list") or []}
        if _norm_company(entry.get("current_company")) in eb_list:
            award("enterprise_brand", eb_points, "current_company")

    # Current Opportunity — cross-referenced against the operator's OWN
    # tracked_opportunities.json, not any employer CRM.
    op_points = weights.get("current_opportunity", 0)
    if op_points and opportunities:
        company_norm = _norm_company(entry.get("current_company"))
        if company_norm and any(_norm_company(o.get("company")) == company_norm for o in opportunities):
            award("current_opportunity", op_points, "tracked_opportunities.json")

    # Existing Relationship — reuses relationship_health.drr_score when present,
    # falls back to rc_tier/signal_class proxy (never recomputes relationship
    # signal from scratch).
    er_max = weights.get("existing_relationship", 0)
    if er_max:
        drr = (entry.get("relationship_health") or {}).get("drr_score")
        if drr is not None:
            frac = max(0.0, min(1.0, drr / 100.0))
            source = "relationship_health.drr_score"
        else:
            tier_proxy = {"inner": 1.0, "broader": 0.8, "dormant_valuable": 0.6}
            class_proxy = {"RC": 0.7, "LKI": 0.5, "LMI": 0.3, "NPR": 0.1, "VC": 0.05}
            frac = tier_proxy.get(entry.get("rc_tier")) or class_proxy.get(entry.get("signal_class"), 0.0)
            source = "rc_tier/signal_class"
        pts = round(er_max * frac)
        award("existing_relationship", pts, source)

    # Executive Decision Maker — heuristic keyword match over current_role;
    # a manual override tag always wins over a heuristic miss or hit.
    edm_points = weights.get("executive_decision_maker", 0)
    if edm_points:
        if "exec-decision-maker" in _entry_tags(entry):
            award("executive_decision_maker", edm_points, "tags.exec-decision-maker (override)")
        else:
            role = (entry.get("current_role") or "").lower()
            kws = [k.lower() for k in scoring.get("executive_keywords") or DEFAULT_EXEC_KEYWORDS]
            if role and _keyword_hit(role, kws):
                award("executive_decision_maker", edm_points, "current_role keyword match")

    # Technology/Operations Leadership — independent dimension from above;
    # a title can hit both.
    tol_points = weights.get("technology_operations_leadership", 0)
    if tol_points:
        if "tech-ops-leader" in _entry_tags(entry):
            award("technology_operations_leadership", tol_points, "tags.tech-ops-leader (override)")
        else:
            role = (entry.get("current_role") or "").lower()
            kws = [k.lower() for k in scoring.get("tech_ops_keywords") or DEFAULT_TECH_OPS_KEYWORDS]
            if role and _keyword_hit(role, kws):
                award("technology_operations_leadership", tol_points, "current_role keyword match")

    # Current Customer — no signal exists in baseline today by default. Two
    # ways to trigger it, both operator-supplied, never inferred: (1) a
    # manually-applied personal-knowledge tag, or (2) membership in this
    # campaign's own known_customer_contacts.json (built from a customer
    # contact list the operator explicitly confirmed is theirs to use — see
    # apply_customer_contact_list()).
    cc_points = weights.get("current_customer", 0)
    if cc_points and "gp-customer-contact-personal-knowledge" in _entry_tags(entry):
        award("current_customer", cc_points, "tags.gp-customer-contact-personal-knowledge (manual)")
    elif cc_points and entry.get("id") in known_customer_ids:
        award("current_customer", cc_points, "known_customer_contacts.json")

    # Strategic Brand Value — tiered, relationship-first model (2026-07-13):
    # confirmed Genius/Worldpay customer (known_customer_contacts.json) is
    # the top tier ("Five Guys"-class recognized brand); a restaurant
    # operator that isn't a confirmed customer is the middle tier
    # ("independent restaurant"); anything else scores 0 here (vendors are
    # excluded upstream by eligibility rules, not scored down). Supersedes
    # enterprise_brand/current_customer as separate dimensions when this
    # weight is set — both remain available at weight 0 for backward compat.
    sbv_points = weights.get("strategic_brand_value", 0)
    if sbv_points:
        if entry.get("id") in known_customer_ids or "gp-customer-contact-personal-knowledge" in _entry_tags(entry):
            award("strategic_brand_value", sbv_points, "known_customer_contacts.json (confirmed customer brand)")
        elif is_restaurant_operator(entry, config):
            pts = round(sbv_points * 0.5)
            award("strategic_brand_value", pts, "is_restaurant_operator (independent restaurant)")

    # Existing Invite Status — a small deliberate bonus for contacts already
    # invited (via Genius or Worldpay) but not yet registered: they're
    # warmer, one nudge from converting. Registered contacts are handled by
    # the eligibility/status filter, never by a scoring bonus here.
    invite_status_points = weights.get("existing_invite_status", 0)
    if invite_status_points and currently_invited:
        award("existing_invite_status", invite_status_points, "already invited, not yet registered")

    # Champion / Reference Potential — no data source infers this; purely a
    # manual tag the operator applies from personal knowledge. Never guessed
    # from title/company/anything else.
    champ_points = weights.get("champion_reference_potential", 0)
    if champ_points and "champion" in _entry_tags(entry):
        award("champion_reference_potential", champ_points, "tags.champion (manual)")

    # Recent Engagement — reuses interaction_capture.py's durable,
    # content-free interaction facts (see recent_engagement_by_id, built
    # once per build_roster call, not recomputed per contact). Falls back to
    # baseline last_touch when no ri_events interaction fact exists yet.
    engage_points = weights.get("recent_engagement", 0)
    if engage_points:
        today = today or date.today()
        last_at = (recent_engagement_by_id or {}).get(entry.get("id")) or entry.get("last_touch")
        source = "interaction_capture (ri_events)" if (recent_engagement_by_id or {}).get(entry.get("id")) else "last_touch"
        if last_at:
            try:
                days_since = (today - date.fromisoformat(last_at[:10])).days
            except ValueError:
                days_since = None
            if days_since is not None and days_since <= 90:
                frac = max(0.0, 1.0 - days_since / 90.0)
                pts = round(engage_points * frac)
                award("recent_engagement", pts, source)

    return total, breakdown


def assign_tier(score: int, entry: dict, config: dict) -> tuple[str, str]:
    tiering = config.get("tiering") or {}
    mode = tiering.get("mode", "score_bands")
    if mode == "score_bands":
        bands = sorted(tiering.get("bands") or [], key=lambda b: b["min_score"], reverse=True)
        for band in bands:
            if score >= band["min_score"]:
                return band["id"], band.get("label", band["id"])
        return "unscored", "Unscored"
    if mode == "rule_based":
        for rule in tiering.get("rules") or []:
            if _rule_condition_matches(entry, rule.get("match") or {}, config=config):
                return rule["id"], rule.get("label", rule["id"])
        return "unscored", "Unscored"
    raise ValueError(f"Unknown tiering.mode: {mode!r}")


# ---------------------------------------------------------------------------
# Confidence assessment
#
# A relationship graph is never perfect — this scores how much to trust
# each recommendation along four independent dimensions, then an overall
# verdict. Overall=low routes the prospect to roster["validation_queue"]
# instead of roster["prospects"] (and out of Queue A/B/company coverage)
# until a human confirms it — never silently contaminating the primary list.
# ---------------------------------------------------------------------------

CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1, "n/a": 0}


def identity_confidence(entry: dict) -> str:
    """How solid is the match to a real, specific person. A LinkedIn URL or
    email is a near-unique key; name+company alone is weaker."""
    if entry.get("linkedin_url") or entry.get("email"):
        return "high"
    if entry.get("name") and entry.get("current_company"):
        return "medium"
    return "low"


def company_confidence(entry: dict) -> str:
    """Is current_company populated and not flagged as an unresolved
    conflict (ingest scripts append '[date] CONFLICT:' to notes rather than
    silently overwrite — see hubspot_ingest.py/linkedin_ingest.py)."""
    company = entry.get("current_company")
    if not company:
        return "low"
    if "CONFLICT" in (entry.get("notes") or ""):
        return "medium"
    return "high"


def relationship_confidence(entry: dict) -> str:
    """Reuses the same signal_class/rc_tier/drr_score signal as the
    existing_relationship scoring dimension — not a new computation."""
    drr = (entry.get("relationship_health") or {}).get("drr_score")
    if drr is not None:
        if drr >= 60:
            return "high"
        if drr >= 25:
            return "medium"
        return "low"
    signal_class = entry.get("signal_class")
    if signal_class == "RC":
        return "high"
    if signal_class == "LKI":
        return "medium"
    return "low"


def executive_role_confidence(score_breakdown: list[dict]) -> str:
    """Derived from the *source* of the executive_decision_maker dimension,
    already recorded on every prospect: a manual override tag is asserted by
    the operator (high); a keyword-heuristic hit is a guess about a title
    string (medium); no dimension present at all means not applicable."""
    for b in score_breakdown:
        if b["dimension"] == "executive_decision_maker":
            return "high" if "override" in b.get("source", "") else "medium"
    return "n/a"


def assess_confidence(entry: dict, score_breakdown: list[dict]) -> dict[str, str]:
    dims = {
        "identity": identity_confidence(entry),
        "company": company_confidence(entry),
        "relationship": relationship_confidence(entry),
        "executive_role": executive_role_confidence(score_breakdown),
    }
    # Overall: conservative majority — count high/medium/low among the three
    # dimensions that always apply (identity, company, relationship);
    # executive_role only counts when it's not n/a. Any two "low" votes (or
    # a single low among only 3 applicable dims) drags the overall down.
    applicable = [v for v in dims.values() if v != "n/a"]
    low_count = sum(1 for v in applicable if v == "low")
    high_count = sum(1 for v in applicable if v == "high")
    if low_count >= 2 or (low_count >= 1 and high_count == 0):
        overall = "low"
    elif high_count == len(applicable):
        overall = "high"
    else:
        overall = "medium"
    dims["overall"] = overall
    return dims


# ---------------------------------------------------------------------------
# Stage 3 (cont.) — roster build orchestration
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_roster(
    campaign_id: str,
    *,
    baseline_path: Path | None = None,
    opportunities_path: Path | None = None,
    dry_run: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    config = load_campaign_config(campaign_id)
    _assert_personal_network_scope(config)
    today = today or date.today()
    baseline_path = baseline_path or core.BASELINE_PATH

    baseline = core.load_baseline(path=baseline_path)  # READ-ONLY — never written by this engine
    opportunities = _load_opportunities(opportunities_path)

    prior_roster = _load_roster(campaign_id)
    prior_by_id = {p["contact_id"]: p for p in (prior_roster or {}).get("prospects", [])}
    known_registered = set((config.get("eligibility") or {}).get("known_registered_contact_ids") or [])
    customer_contacts = _load_known_customer_contacts(campaign_id)
    known_customer_ids = {c["contact_id"] for c in customer_contacts.get("contacts", [])}
    customer_context_by_id = {c["contact_id"]: c for c in customer_contacts.get("contacts", [])}
    tier0 = (config.get("tiering") or {}).get("tier_0") or {}
    recent_engagement_by_id: dict[str, str] = {}
    if (config.get("scoring") or {}).get("weights", {}).get("recent_engagement"):
        try:
            recent_engagement_by_id = interaction_capture.most_recent_interaction_by_contact()
        except Exception:  # noqa: BLE001 — scoring dimension is best-effort, never fatal to roster build
            recent_engagement_by_id = {}

    prospects: list[dict] = []
    excluded: list[dict] = []
    validation_queue: list[dict] = []

    for entry in baseline:
        cid = entry["id"]
        prior = prior_by_id.get(cid)

        # Settled roster members (registered/declined/attended/no_show) are
        # carried forward unchanged — eligibility rules govern who *joins*
        # the roster, not who stays on it once a real-world outcome landed.
        if prior and prior.get("status") in SETTLED_STATUSES:
            prospects.append(prior)
            continue

        included, inclusion_reasons = is_included(entry, config, known_customer_ids=known_customer_ids)
        if not included:
            continue  # not a candidate at all — not cluttering the excluded list with the whole baseline

        reason, mode = excluded_reason(entry, config, known_registered=known_registered)
        # Peek at what status this contact currently has (or is about to get)
        # so the existing_invite_status scoring dimension can see it — the
        # authoritative status/status_history assignment happens below,
        # this is read-only lookahead, not a second source of truth.
        currently_invited = (prior["status"] == "invited") if prior else (cid in known_customer_ids)
        score, breakdown = score_entry(
            entry, config, opportunities=opportunities, known_customer_ids=known_customer_ids,
            recent_engagement_by_id=recent_engagement_by_id, today=today,
            currently_invited=currently_invited,
        )

        if reason and mode == "exclude":
            excluded.append({
                "contact_id": cid,
                "name": entry.get("name"),
                "current_company": entry.get("current_company"),
                "reason": reason,
            })
            continue

        if reason and mode == "penalize":
            penalty = (config.get("scoring") or {}).get("penalties", {}).get(reason, -100)
            score += penalty
            breakdown.append({"dimension": reason, "points": penalty, "source": "eligibility_penalty"})

        tier_id, tier_label = assign_tier(score, entry, config)
        if prior:
            status = prior["status"]
            status_history = prior["status_history"]
            invited_via = prior.get("invited_via") or []
            # Backfill: a contact carried forward from a roster built before
            # invited_via existed (or before this contact_id was added to
            # known_customer_contacts.json) never gets this source recorded
            # otherwise, since this branch — not the "first time joining"
            # branch below — runs on every subsequent build once prior
            # exists. Additive, never downgrades status.
            if cid in known_customer_ids and "worldpay" not in invited_via:
                invited_via = invited_via + ["worldpay"]
        elif cid in known_customer_ids:
            # A known customer contact joining the roster for the first time
            # is already invited by the operator's own account (that's what
            # the customer contact list represents) — never starts at
            # not_yet_invited.
            status = "invited"
            status_history = [{"status": status, "at": today.isoformat(), "reason": "known_customer_contact_list — already invited per operator"}]
            invited_via = ["worldpay"]
        else:
            status = "not_yet_invited"
            status_history = [{"status": status, "at": today.isoformat(), "reason": "roster_created"}]
            invited_via = []

        # Tier 0 override: a known customer contact who has been invited but
        # not yet registered is the highest-probability conversion — always
        # surfaces above the normal score-band tiers, regardless of score.
        if tier0 and cid in known_customer_ids and status == "invited":
            tier_id = tier0.get("id", "tier_0")
            tier_label = tier0.get("label", "Tier 0")

        prospect = {
            "contact_id": cid,
            "name": entry.get("name"),
            "current_company": entry.get("current_company"),
            "current_role": entry.get("current_role"),
            "score": score,
            "score_breakdown": breakdown,
            "tier": tier_id,
            "tier_label": tier_label,
            "status": status,
            "status_history": status_history,
            "invited_via": invited_via,
            "sources": entry.get("sources") or [],
            "is_restaurant_operator": is_restaurant_operator(entry, config),
            "inclusion_reasons": inclusion_reasons,
            "confidence": assess_confidence(entry, breakdown),
        }
        if cid in customer_context_by_id:
            cc = customer_context_by_id[cid]
            prospect["customer_context"] = {"merchant": cc.get("merchant"), "title": cc.get("title")}

        # Only route to validation_queue while status is still
        # not_yet_invited — a human already acting on this prospect
        # (inviting, registering them, etc.) is itself a stronger signal
        # than the confidence heuristic, and must never be hidden from
        # Queue B / company coverage by a fresh low-confidence recompute.
        if (
            status == "not_yet_invited"
            and prospect["confidence"]["overall"] == "low"
            and "confidence-override" not in _entry_tags(entry)
        ):
            # Add a 'confidence-override' tag on the baseline entry to
            # confirm manually and pull it into the main roster despite
            # the heuristic.
            validation_queue.append(prospect)
        else:
            prospects.append(prospect)

    prospects.sort(key=lambda p: p["score"], reverse=True)
    validation_queue.sort(key=lambda p: p["score"], reverse=True)

    roster = {
        "campaign_id": campaign_id,
        "generated_at": _now_iso(),
        "prospects": prospects,
        "excluded": excluded,
        "validation_queue": validation_queue,
    }
    company_rollup = build_company_rollup(campaign_id, roster)

    tier_counts = Counter(p["tier_label"] for p in prospects)
    status_counts = Counter(p["status"] for p in prospects)

    result: dict[str, Any] = {
        "ok": True,
        "campaign_id": campaign_id,
        "prospect_count": len(prospects),
        "excluded_count": len(excluded),
        "validation_queue_count": len(validation_queue),
        "tier_counts": dict(tier_counts),
        "status_counts": dict(status_counts),
        "company_count": len(company_rollup["companies"]),
        "exclusion_reason_warnings": validate_exclusion_reasons(config),
        "dry_run": dry_run,
    }

    if dry_run:
        result["roster_preview"] = roster
        return result

    _write_roster(campaign_id, roster)
    _write_company_rollup(campaign_id, company_rollup)
    _write_roster_report(campaign_id, roster, config)
    _write_company_dashboard(campaign_id, company_rollup)
    _write_executive_dashboard(campaign_id, roster, company_rollup)
    gaps = build_coverage_gaps(roster, baseline=baseline)
    _write_coverage_gaps(campaign_id, gaps, config)
    result["coverage_gap_count"] = len(gaps)
    plan = build_execution_plan(campaign_id, roster, config, gaps)
    _write_execution_plan(campaign_id, plan, config)
    account_first_rows = build_account_first_recommendations(roster, campaign_id)
    _write_account_first(campaign_id, account_first_rows, config)
    result["account_first_count"] = len(account_first_rows)
    priority_invite_rows = build_priority_invite_list(roster, config)
    _write_priority_invite_list(campaign_id, priority_invite_rows, config)
    result["priority_invite_count"] = len(priority_invite_rows)
    delta_path = _write_delta_report(campaign_id, prior_roster, roster, today)
    result["delta_report_path"] = _display_path(delta_path) if delta_path else None
    _write_latest_cache(campaign_id, result)
    return result


# ---------------------------------------------------------------------------
# Stage 4 — Registration-list reconciliation
# ---------------------------------------------------------------------------

REGISTRATION_FIELD_ALIASES = {
    "name": ("Attendee Name", "Name", "Full Name"),
    "email": ("Email", "Email Address", "email"),
    "first_name": ("First Name",),
    "last_name": ("Last Name",),
}

# The Worldpay/merchant-customer contact list (apply_customer_contact_list)
# uses a distinct header vocabulary — kept separate from registration
# aliases so "Company" doesn't collide across the two very different sources.
CUSTOMER_CONTACT_FIELD_ALIASES = {
    "merchant": ("Merchant", "merchant name"),
    "name": ("Contact", "invite recipient"),
    "email": ("email", "Email"),
    "title": ("Contact Title", "invitees role within merchant"),
}


def _row_field(row: dict, aliases: dict[str, tuple[str, ...]], key: str) -> str:
    for alias in aliases[key]:
        if alias in row and row[alias]:
            return str(row[alias]).strip()
    return ""


def _reg_field(row: dict[str, str], key: str) -> str:
    value = _row_field(row, REGISTRATION_FIELD_ALIASES, key)
    if value or key != "name":
        return value
    # No single "name"-shaped column — try combining First Name + Last Name
    # (e.g. the "Invite List" sheet in Genius World Registration.xlsx).
    first = _row_field(row, REGISTRATION_FIELD_ALIASES, "first_name")
    last = _row_field(row, REGISTRATION_FIELD_ALIASES, "last_name")
    return f"{first} {last}".strip()


def parse_registration_csv(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader if any((v or "").strip() for v in row.values())]


def _read_xlsx_sheet_rows(path: Path, sheet_name: str) -> list[dict[str, Any]]:
    """Read one sheet of an xlsx workbook into a list of header-keyed dicts.

    Handles a quirk seen in real exports: a header cell left blank in row 1
    but clarified by a lowercase label in row 2 (e.g. Genius World Invites.xlsx
    leaves its email column header blank in row 1, labeling it "email" in
    row 2). When detected, row 2 is treated as part of the header, not data.
    """
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet_name not in wb.sheetnames:
            return []
        ws = wb[sheet_name]
        rows_iter = ws.iter_rows(values_only=True)
        header = list(next(rows_iter, ()) or ())
        peek = next(rows_iter, None)
        consumed_peek = False
        if peek is not None and any(h is None for h in header):
            for i, h in enumerate(header):
                if h is None and i < len(peek) and isinstance(peek[i], str) and peek[i].strip():
                    header[i] = peek[i].strip()
                    consumed_peek = True
        header = [str(h).strip() if h is not None else f"col_{i}" for i, h in enumerate(header)]

        pending: list[tuple] = []
        if peek is not None and not consumed_peek:
            pending.append(peek)
        pending.extend(rows_iter)

        out: list[dict[str, Any]] = []
        for row in pending:
            if not any((c is not None and str(c).strip()) for c in row):
                continue
            out.append({header[i]: row[i] for i in range(min(len(header), len(row)))})
        return out
    finally:
        wb.close()


def _add_invite_source(prospect: dict, source: str) -> None:
    """Additive, never overwritten — a contact invited via both Genius and
    Worldpay lists carries both sources (feeds the 🟣 'invited by both'
    annotation, distinct from 🟡 Genius-only / 🔵 Worldpay-only)."""
    sources = prospect.setdefault("invited_via", [])
    if source not in sources:
        sources.append(source)


def _apply_registration_rows(
    rows: list[dict],
    *,
    prospects_by_id: dict[str, dict],
    by_email: dict[str, dict],
    by_name: dict[str, list[dict]],
    target_status: str,
    today: date,
    match_reason: str,
    invite_source: str | None = None,
) -> tuple[list[dict], list[dict], list[tuple[str, str, str]]]:
    """Shared row-matching logic for both CSV and xlsx registration/invite
    sources. Never advances a contact backward (e.g. won't downgrade
    'registered' to 'invited') and never touches settled statuses.
    `invite_source` (e.g. "genius"/"worldpay") is recorded on the prospect's
    `invited_via` list whenever target_status=="invited" — even when the
    contact was already invited via a *different* source, so a second-source
    match still registers (that's exactly the 🟣 'invited by both' case)."""
    matched: list[dict] = []
    unmatched: list[dict] = []
    events: list[tuple[str, str, str]] = []
    status_rank = {s: i for i, s in enumerate(VALID_STATUSES)}

    for row in rows:
        name = _reg_field(row, "name")
        email = _reg_field(row, "email").strip().lower()
        entry = by_email.get(email) if email else None
        basis = "email"
        if entry is None:
            entry, ambiguous = im.match_unique_name(name, by_name)
            basis = "name" if entry else ("ambiguous_name" if ambiguous else "unmatched")
        if entry is None:
            unmatched.append({"name": name, "email": email, "basis": basis})
            continue

        cid = entry["id"]
        prospect = prospects_by_id.get(cid)
        if prospect is None:
            unmatched.append({"name": name, "email": email, "basis": "matched_baseline_not_on_roster"})
            continue

        if prospect["status"] in SETTLED_STATUSES or status_rank.get(prospect["status"], 0) >= status_rank.get(target_status, 0):
            if target_status == "invited" and invite_source and prospect["status"] not in SETTLED_STATUSES:
                _add_invite_source(prospect, invite_source)
            matched.append({"contact_id": cid, "name": prospect["name"], "already": True})
            continue

        prospect["status"] = target_status
        prospect.setdefault("status_history", []).append({
            "status": target_status, "at": today.isoformat(),
            "reason": f"matched {match_reason} via {basis}",
        })
        if target_status == "invited" and invite_source:
            _add_invite_source(prospect, invite_source)
        matched.append({"contact_id": cid, "name": prospect["name"], "already": False})
        events.append((cid, prospect["name"], target_status))

    return matched, unmatched, events


def reconcile_registrations(
    campaign_id: str,
    csv_path: str | Path,
    *,
    baseline_path: Path | None = None,
    dry_run: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    config = load_campaign_config(campaign_id)
    _assert_personal_network_scope(config)
    today = today or date.today()
    baseline_path = baseline_path or core.BASELINE_PATH

    roster = _load_roster(campaign_id)
    if not roster:
        return {"ok": False, "error": f"No roster exists yet for campaign {campaign_id!r} — run --build-roster first."}

    # Match against the FULL baseline, not just current roster candidates —
    # a registration is direct ground truth that this person exists and is
    # engaged, even if they weren't otherwise eligibility-qualified. If they
    # matched a baseline identity but aren't a roster prospect, that's
    # reported distinctly (basis=matched_baseline_not_on_roster), never
    # silently dropped as if they were never found at all.
    baseline = core.load_baseline(path=baseline_path)
    by_email = im.build_email_index(baseline)
    by_name = im.build_name_index(baseline)

    rows = parse_registration_csv(Path(csv_path))
    prospects_by_id = {p["contact_id"]: p for p in roster["prospects"]}

    matched, unmatched, events = _apply_registration_rows(
        rows, prospects_by_id=prospects_by_id, by_email=by_email, by_name=by_name,
        target_status="registered", today=today, match_reason="registration list",
    )

    result: dict[str, Any] = {
        "ok": True,
        "campaign_id": campaign_id,
        "registrations_processed": len(rows),
        "matched_count": sum(1 for m in matched if not m["already"]),
        "already_registered_count": sum(1 for m in matched if m["already"]),
        "unmatched_count": len(unmatched),
        "unmatched": unmatched,
        "dry_run": dry_run,
    }

    if dry_run:
        return result

    roster["generated_at"] = _now_iso()
    company_rollup = build_company_rollup(campaign_id, roster)
    _write_roster(campaign_id, roster)
    _write_company_rollup(campaign_id, company_rollup)
    _write_roster_report(campaign_id, roster, config)
    _write_company_dashboard(campaign_id, company_rollup)
    for cid, name, status in events:
        _emit_campaign_event(campaign_id, cid, name, status, today=today, reason="registration_list_match")
    _write_latest_cache(campaign_id, result)
    return result


def reconcile_registration_workbook(
    campaign_id: str,
    xlsx_path: str | Path,
    *,
    sheet_status_map: dict[str, str] | None = None,
    sheet_source_map: dict[str, str] | None = None,
    baseline_path: Path | None = None,
    dry_run: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    """Reconcile a multi-sheet xlsx workbook against the roster — e.g. a
    conference platform export with a 'Registered' sheet and a separate
    'Invite List' sheet of who's already been invited. Sheets are applied in
    the order given so a later sheet's target_status never downgrades an
    earlier one (see _apply_registration_rows' status_rank guard).
    `sheet_source_map` records which invited_via source (e.g. "genius") an
    'invited'-target sheet represents — omit for sheets that aren't an
    invite-source signal (e.g. "Registered")."""
    sheet_status_map = sheet_status_map or {"Registered": "registered", "Invite List": "invited"}
    sheet_source_map = sheet_source_map or {"Invite List": "genius"}
    config = load_campaign_config(campaign_id)
    _assert_personal_network_scope(config)
    today = today or date.today()
    baseline_path = baseline_path or core.BASELINE_PATH

    roster = _load_roster(campaign_id)
    if not roster:
        return {"ok": False, "error": f"No roster exists yet for campaign {campaign_id!r} — run --build-roster first."}

    # Match against the FULL baseline, not just current roster candidates —
    # see reconcile_registrations for why.
    baseline = core.load_baseline(path=baseline_path)
    by_email = im.build_email_index(baseline)
    by_name = im.build_name_index(baseline)
    prospects_by_id = {p["contact_id"]: p for p in roster["prospects"]}

    per_sheet: dict[str, dict] = {}
    all_events: list[tuple[str, str, str]] = []
    for sheet_name, target_status in sheet_status_map.items():
        rows = _read_xlsx_sheet_rows(Path(xlsx_path), sheet_name)
        matched, unmatched, events = _apply_registration_rows(
            rows, prospects_by_id=prospects_by_id, by_email=by_email, by_name=by_name,
            target_status=target_status, today=today, match_reason=f"'{sheet_name}' sheet",
            invite_source=sheet_source_map.get(sheet_name),
        )
        all_events.extend(events)
        per_sheet[sheet_name] = {
            "target_status": target_status,
            "rows_processed": len(rows),
            "matched_count": sum(1 for m in matched if not m["already"]),
            "already_at_or_past_status_count": sum(1 for m in matched if m["already"]),
            "unmatched_count": len(unmatched),
            "unmatched": unmatched,
        }

    result: dict[str, Any] = {
        "ok": True,
        "campaign_id": campaign_id,
        "sheets": per_sheet,
        "dry_run": dry_run,
    }

    if dry_run:
        return result

    roster["generated_at"] = _now_iso()
    company_rollup = build_company_rollup(campaign_id, roster)
    _write_roster(campaign_id, roster)
    _write_company_rollup(campaign_id, company_rollup)
    _write_roster_report(campaign_id, roster, config)
    _write_company_dashboard(campaign_id, company_rollup)
    for cid, name, status in all_events:
        _emit_campaign_event(campaign_id, cid, name, status, today=today, reason="registration_workbook_match")
    _write_latest_cache(campaign_id, result)
    return result


def apply_customer_contact_list(
    campaign_id: str,
    xlsx_path: str | Path,
    *,
    sheet_name: str = "Sheet1",
    baseline_path: Path | None = None,
    dry_run: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    """Ingest a known-customer contact list (e.g. Worldpay merchant contacts)
    — feeds the 'current_customer' scoring dimension and Tier 0 eligibility.
    Matches against the FULL baseline (not just the current roster), since a
    confirmed customer contact should join the roster even if they wouldn't
    otherwise pass the generic eligibility rules. Also marks matched, roster-
    settled-eligible contacts as 'invited' (per the operator's own account:
    this list represents contacts already invited as existing customers).

    Writes system/campaigns/<id>/known_customer_contacts.json — durable,
    campaign-scoped, never mutates baseline_index.json.
    """
    config = load_campaign_config(campaign_id)
    _assert_personal_network_scope(config)
    today = today or date.today()
    baseline_path = baseline_path or core.BASELINE_PATH

    baseline = core.load_baseline(path=baseline_path)
    by_email = im.build_email_index(baseline)
    by_name = im.build_name_index(baseline)

    rows = _read_xlsx_sheet_rows(Path(xlsx_path), sheet_name)

    matched_contacts: list[dict] = []
    unmatched: list[dict] = []
    for row in rows:
        name = _row_field(row, CUSTOMER_CONTACT_FIELD_ALIASES, "name")
        email = _row_field(row, CUSTOMER_CONTACT_FIELD_ALIASES, "email").strip().lower()
        merchant = _row_field(row, CUSTOMER_CONTACT_FIELD_ALIASES, "merchant")
        title = _row_field(row, CUSTOMER_CONTACT_FIELD_ALIASES, "title")

        entry = by_email.get(email) if email else None
        basis = "email"
        if entry is None:
            entry, ambiguous = im.match_unique_name(name, by_name)
            basis = "name" if entry else ("ambiguous_name" if ambiguous else "unmatched")
        if entry is None:
            unmatched.append({"name": name, "email": email, "merchant": merchant, "basis": basis})
            continue

        matched_contacts.append({
            "contact_id": entry["id"], "name": entry["name"],
            "merchant": merchant, "title": title, "matched_via": basis,
        })

    result: dict[str, Any] = {
        "ok": True,
        "campaign_id": campaign_id,
        "rows_processed": len(rows),
        "matched_count": len(matched_contacts),
        "unmatched_count": len(unmatched),
        "unmatched": unmatched,
        "dry_run": dry_run,
    }
    if dry_run:
        result["matched_preview"] = matched_contacts
        return result

    # Merge with any prior list rather than overwrite (additive, matches the
    # "known_registered_contact_ids seed list" convention elsewhere).
    existing = _load_known_customer_contacts(campaign_id)
    by_id = {c["contact_id"]: c for c in existing.get("contacts", [])}
    for c in matched_contacts:
        by_id[c["contact_id"]] = c
    payload = {
        "campaign_id": campaign_id,
        "generated_at": _now_iso(),
        "contacts": list(by_id.values()),
        "unmatched": unmatched,
    }
    path = _known_customer_contacts_path(campaign_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    # Mark matched contacts already on the roster as invited (unless settled)
    # — building the roster fresh afterward (build_roster) is what actually
    # adds newly-qualifying customer contacts who weren't on the roster yet;
    # this step only advances status for those already present.
    roster = _load_roster(campaign_id)
    if roster:
        prospects_by_id = {p["contact_id"]: p for p in roster["prospects"]}
        events: list[tuple[str, str, str]] = []
        changed = False
        for c in matched_contacts:
            prospect = prospects_by_id.get(c["contact_id"])
            if prospect is None or prospect["status"] in SETTLED_STATUSES:
                continue
            before = list(prospect.get("invited_via") or [])
            _add_invite_source(prospect, "worldpay")  # additive — records 🟣 even if already invited via another source
            if prospect["invited_via"] != before:
                changed = True
            if prospect["status"] == "invited":
                continue
            prospect["status"] = "invited"
            prospect.setdefault("status_history", []).append({
                "status": "invited", "at": today.isoformat(),
                "reason": "known customer contact list — already invited per operator",
            })
            events.append((c["contact_id"], prospect["name"], "invited"))
            changed = True
        if changed:
            roster["generated_at"] = _now_iso()
            _write_roster(campaign_id, roster)
            for cid, name, status in events:
                _emit_campaign_event(campaign_id, cid, name, status, today=today, reason="customer_contact_list_match")

    result["known_customer_contacts_path"] = _display_path(path)
    return result


def set_status(
    campaign_id: str,
    contact_id: str,
    status: str,
    *,
    reason: str | None = None,
    dry_run: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    if status not in VALID_STATUSES:
        return {"ok": False, "error": f"Unknown status {status!r}; expected one of {VALID_STATUSES}"}
    today = today or date.today()

    config = load_campaign_config(campaign_id)
    _assert_personal_network_scope(config)

    roster = _load_roster(campaign_id)
    if not roster:
        return {"ok": False, "error": f"No roster exists yet for campaign {campaign_id!r}."}

    prospect = next((p for p in roster["prospects"] if p["contact_id"] == contact_id), None)
    if prospect is None:
        return {"ok": False, "error": f"{contact_id!r} is not on the {campaign_id!r} roster."}

    old_status = prospect["status"]
    prospect["status"] = status
    prospect.setdefault("status_history", []).append({
        "status": status, "at": today.isoformat(), "reason": reason or "manual_set_status",
    })

    result = {
        "ok": True, "campaign_id": campaign_id, "contact_id": contact_id,
        "old_status": old_status, "new_status": status, "dry_run": dry_run,
    }
    if dry_run:
        return result

    roster["generated_at"] = _now_iso()
    company_rollup = build_company_rollup(campaign_id, roster)
    _write_roster(campaign_id, roster)
    _write_company_rollup(campaign_id, company_rollup)
    _write_roster_report(campaign_id, roster, config)
    _write_company_dashboard(campaign_id, company_rollup)
    _emit_campaign_event(campaign_id, contact_id, prospect["name"], status, today=today, reason=reason)
    return result


# ---------------------------------------------------------------------------
# Company rollup
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Account-first recommendations — group by account, classify the account
# tier from real uploaded customer data, then pick the best contact by role
# priority rather than raw score. Every row here is a real, matched baseline
# contact; a company/name that only exists in a source spreadsheet and never
# matched anyone in the operator's network is never fabricated into a row.
# ---------------------------------------------------------------------------

# Priority order for "who's the right contact at this account" — checked in
# order; first keyword match wins. Falls back to score only when nobody's
# title matches any of these.
ROLE_PRIORITY_KEYWORDS = [
    ("cio", "CIO"),
    ("vp it", "VP IT"),
    ("vp information technology", "VP IT"),
    ("vp technology", "VP Technology"),
    ("director restaurant technology", "Director Restaurant Technology"),
    ("director of restaurant technology", "Director Restaurant Technology"),
    ("digital", "Digital"),
    ("payments", "Payments"),
    ("enterprise applications", "Enterprise Applications"),
    ("operations technology", "Operations Technology"),
    ("technology operations", "Operations Technology"),
]


def _role_priority_rank(role: str | None) -> int:
    role = (role or "").lower()
    for i, (kw, _label) in enumerate(ROLE_PRIORITY_KEYWORDS):
        if kw in role:
            return i
    return len(ROLE_PRIORITY_KEYWORDS)  # no keyword matched — lowest priority


def select_best_contact_by_role(members: list[dict]) -> dict:
    """Prefer a CIO/VP IT/VP Technology/etc. title over raw score; falls
    back to highest score only when no member's title matches any
    role-priority keyword."""
    return sorted(members, key=lambda p: (_role_priority_rank(p.get("current_role")), -p["score"]))[0]


def classify_account_tier(known_customer_ids: set[str], members: list[dict]) -> tuple[str, str]:
    """Tier 1 = confirmed existing Genius/Worldpay customer, from
    known_customer_contacts.json (a real uploaded customer list the
    operator confirmed is theirs to use — see apply_customer_contact_list).
    Tier 3 = restaurant operator by the keyword heuristic. Deliberately no
    Tier 2 ('existing GP restaurant customer, not yet Genius') — there is no
    compliant data source for Global Payments' own customer status inside
    this personal_network_only engine; inventing one here would be exactly
    the kind of fabrication this whole engine exists to prevent."""
    if any(p["contact_id"] in known_customer_ids for p in members):
        return "tier_1", "Tier 1 — Existing Genius Customer"
    if any(p.get("is_restaurant_operator") for p in members):
        return "tier_3", "Tier 3 — Strategic Restaurant Target"
    return "unranked", "Unranked"


def build_account_first_recommendations(roster: dict[str, Any], campaign_id: str) -> list[dict]:
    known_customer_ids = {c["contact_id"] for c in _load_known_customer_contacts(campaign_id).get("contacts", [])}
    groups: dict[str, list[dict]] = defaultdict(list)
    for p in roster["prospects"]:
        company = p.get("current_company")
        if not company:
            continue
        groups[company].append(p)

    rows: list[dict] = []
    for company, members in groups.items():
        tier_id, tier_label = classify_account_tier(known_customer_ids, members)
        if tier_id == "unranked":
            continue  # account-first view surfaces only classified accounts
        best = select_best_contact_by_role(members)
        role_rank = _role_priority_rank(best.get("current_role"))
        role_matched = role_rank < len(ROLE_PRIORITY_KEYWORDS)

        if best["status"] == "registered":
            registered, invited, action = "Yes", "—", "Already attending — ask to bring colleagues" if len(members) > 1 else "Already attending"
        elif best["status"] == "invited":
            registered, invited, action = "No", "Yes", "Follow up"
        else:
            registered, invited, action = "No", "No", "Invite"

        # "Who should be asked to bring colleagues" — other known contacts
        # at the same account, real names only, never invented.
        colleagues = [m["name"] for m in members if m["contact_id"] != best["contact_id"]]

        rows.append({
            "account": company,
            "account_tier": tier_label,
            "contact_id": best["contact_id"],
            "name": best["name"],
            "title": best.get("current_role"),
            "why_this_person": (
                f"{ROLE_PRIORITY_KEYWORDS[role_rank][1]} — role-priority match"
                if role_matched else
                "Highest-scored known contact (no role-priority title on file)"
            ),
            "registered": registered,
            "invited": invited,
            "action": action,
            "score": best["score"],
            "other_known_contacts_to_invite": colleagues,
        })

    rows.sort(key=lambda r: (r["account_tier"], -r["score"]))
    return rows


def _render_account_first(rows: list[dict], config: dict) -> str:
    name = config.get("name", "Campaign")
    lines = [
        f"# {name} — Account-First Recommendations", "",
        f"_Generated {_now_iso()}_", "",
        f"{len(rows)} accounts classified (Tier 1 = confirmed existing Genius/Worldpay customer; "
        "Tier 3 = restaurant operator). Every row is a real, matched contact in your network — "
        "no row is invented from a source-spreadsheet name RB has never actually connected to you.",
        "",
        "| Priority | Account | Tier | Recommended Contact | Title | Why This Person | Registered | Invited | Action | Also Invite (same account) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, start=1):
        colleagues = ", ".join(r.get("other_known_contacts_to_invite") or []) or "—"
        lines.append(
            f"| {i} | {r['account']} | {r['account_tier']} | {r['name']} | {r.get('title') or ''} | "
            f"{r['why_this_person']} | {r['registered']} | {r['invited']} | {r['action']} | {colleagues} |"
        )
    lines.append("")
    return "\n".join(lines)


def _write_account_first(campaign_id: str, rows: list[dict], config: dict) -> Path:
    d = _campaign_dir(campaign_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "account_first_recommendations.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    path = d / "account_first_recommendations.md"
    path.write_text(_render_account_first(rows, config), encoding="utf-8")
    return path


def build_company_rollup(campaign_id: str, roster: dict[str, Any]) -> dict[str, Any]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for p in roster["prospects"]:
        company = p.get("current_company") or "Unknown / Unaffiliated"
        groups[company].append(p)

    companies = []
    for company, members in groups.items():
        best = max(members, key=lambda p: p["score"])
        status_summary = dict(Counter(p["status"] for p in members))
        companies.append({
            "company": company,
            "contact_count": len(members),
            "top_tier": best["tier"],
            "top_tier_label": best["tier_label"],
            "best_contact_id": best["contact_id"],
            "best_contact_name": best["name"],
            "best_contact_score": best["score"],
            "status_summary": status_summary,
            "contact_ids": [p["contact_id"] for p in members],
        })
    companies.sort(key=lambda c: c["best_contact_score"], reverse=True)

    return {"campaign_id": campaign_id, "generated_at": _now_iso(), "companies": companies}


# ---------------------------------------------------------------------------
# Gap discovery — account-planning view, not just an invite list.
# Reuses rb_core.find_intro_paths (the existing broker/intro-path finder)
# rather than inventing a new relationship-path algorithm.
# ---------------------------------------------------------------------------

def _has_executive_signal(prospect: dict) -> bool:
    return any(
        b["dimension"] in ("executive_decision_maker", "technology_operations_leadership")
        for b in prospect.get("score_breakdown", [])
    )


def _existing_relationship_points(prospect: dict) -> int:
    for b in prospect.get("score_breakdown", []):
        if b["dimension"] == "existing_relationship":
            return b["points"]
    return 0


def build_coverage_gaps(
    roster: dict[str, Any],
    *,
    baseline: list[dict] | None = None,
    registration_gap_min_contacts: int = 2,
    inactive_relationship_threshold: int = 5,
) -> list[dict]:
    """Identify companies where the relationship graph is weak, not just
    where it's strong — the account-planning half of this engine, not just
    an invite list. For each gap, recommends who should make the
    introduction (via rb_core.find_intro_paths, the existing broker
    finder — not a new algorithm) and estimates effort from whether that
    broker has an actual proximity signal to the company."""
    baseline = baseline if baseline is not None else core.load_baseline()

    groups: dict[str, list[dict]] = defaultdict(list)
    for p in roster["prospects"]:
        company = p.get("current_company") or "Unknown / Unaffiliated"
        groups[company].append(p)

    gaps: list[dict] = []
    for company, members in groups.items():
        if company == "Unknown / Unaffiliated":
            continue
        gap_types: list[str] = []
        if not any(_has_executive_signal(p) for p in members):
            gap_types.append("no_executive_relationship")
        best = max(members, key=lambda p: p["score"])
        if _existing_relationship_points(best) <= inactive_relationship_threshold:
            gap_types.append("existing_contacts_inactive")
        invited_or_registered = [p for p in members if p["status"] in ("invited", "registered", "attended")]
        if invited_or_registered and not any(_has_executive_signal(p) for p in invited_or_registered):
            gap_types.append("invitation_not_reached_decision_maker")
        if len(members) >= registration_gap_min_contacts and not any(p["status"] in ("registered", "invited") for p in members):
            gap_types.append("registration_missing_despite_relationships")
        if not gap_types:
            continue

        intro = core.find_intro_paths(company, limit=1, baseline=baseline, threads=[])
        brokers = intro.get("candidate_brokers") or []
        top_broker = brokers[0] if brokers else None
        if top_broker:
            effort = "low" if top_broker.get("has_proximity") else "moderate"
            recommended_introducer = top_broker.get("name")
            introducer_reason = top_broker.get("reason")
        else:
            effort = "high — no known broker path, cold outreach needed"
            recommended_introducer = None
            introducer_reason = None

        gaps.append({
            "company": company,
            "contact_count": len(members),
            "gap_types": gap_types,
            "door_opener": best["name"],
            "door_opener_score": best["score"],
            "recommended_introducer": recommended_introducer,
            "introducer_reason": introducer_reason,
            "estimated_effort": effort,
        })

    gaps.sort(key=lambda g: (len(g["gap_types"]), g["contact_count"]), reverse=True)
    return gaps


def _render_coverage_gaps(campaign_id: str, gaps: list[dict], config: dict) -> str:
    name = config.get("name", campaign_id)
    lines = [f"# {name} — Coverage Gap Analysis", "", f"_Generated {_now_iso()}_", "",
              f"{len(gaps)} companies flagged. Account-planning view: where the relationship graph is weak, not where it's strong.", ""]
    for g in gaps:
        lines.append(f"## {g['company']} ({g['contact_count']} contact(s))")
        lines.append("")
        lines.append(f"- **Gap type(s):** {', '.join(g['gap_types'])}")
        lines.append(f"- **Door opener today:** {g['door_opener']} (score {g['door_opener_score']})")
        if g["recommended_introducer"]:
            lines.append(f"- **Recommended introducer:** {g['recommended_introducer']} — {g['introducer_reason']}")
        else:
            lines.append("- **Recommended introducer:** none found — no broker path in your network")
        lines.append(f"- **Estimated effort:** {g['estimated_effort']}")
        lines.append("")
    return "\n".join(lines)


def _write_coverage_gaps(campaign_id: str, gaps: list[dict], config: dict) -> Path:
    d = _campaign_dir(campaign_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "coverage_gaps.json").write_text(json.dumps(gaps, indent=2) + "\n", encoding="utf-8")
    path = d / "coverage_gaps.md"
    path.write_text(_render_coverage_gaps(campaign_id, gaps, config), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Queues, executive dashboard, workbook export
# ---------------------------------------------------------------------------

def _relationship_strength_label(prospect: dict, config: dict) -> str:
    """Thresholds are fractions of the configured existing_relationship
    weight, not absolute points — so relabeling stays correct however the
    weight is tuned (e.g. the 2026-07-13 reweight to 40 from 15)."""
    er_max = ((config.get("scoring") or {}).get("weights") or {}).get("existing_relationship", 0)
    for b in prospect.get("score_breakdown", []):
        if b["dimension"] == "existing_relationship":
            frac = (b["points"] / er_max) if er_max else 0.0
            if frac >= 0.9:
                return "Strong"
            if frac >= 0.5:
                return "Moderate"
            return "Light"
    return "Unknown"


def _crm_status_label(prospect: dict) -> str:
    sources = [s.lower() for s in prospect.get("sources", [])]
    tags = []
    if any("hubspot" in s for s in sources):
        tags.append("HubSpot")
    if any("linkedin" in s for s in sources):
        tags.append("LinkedIn")
    return ", ".join(tags) if tags else "Personal network"


def _why_attend(prospect: dict, config: dict) -> str:
    """Built only from real score_breakdown/tier/customer_context fields
    already computed for this prospect — never invents a reason that isn't
    backed by evidence already on the record."""
    er_max = ((config.get("scoring") or {}).get("weights") or {}).get("existing_relationship", 0)
    reasons: list[str] = []
    if prospect.get("customer_context"):
        cc = prospect["customer_context"]
        reasons.append(f"Existing Worldpay customer contact at {cc.get('merchant') or prospect.get('current_company') or 'their company'}")
    for b in prospect.get("score_breakdown", []):
        dim = b["dimension"]
        if dim == "executive_decision_maker":
            reasons.append("Executive decision maker")
        elif dim == "technology_operations_leadership":
            reasons.append("Technology/operations leadership")
        elif dim == "enterprise_brand":
            reasons.append("Enterprise brand")
        elif dim == "strategic_brand_value":
            reasons.append("Strategic brand value (recognized customer/operator)")
        elif dim == "existing_relationship" and er_max and (b["points"] / er_max) >= 0.9:
            reasons.append("Strong existing relationship")
        elif dim == "current_opportunity":
            reasons.append("Active tracked opportunity at this company")
    if not reasons:
        reasons.append(f"On the {prospect.get('tier_label', 'roster')} list for this campaign")
    return "; ".join(dict.fromkeys(reasons))  # de-dupe, preserve order


def _suggested_linkedin_outreach(prospect: dict, config: dict) -> str:
    """Short, evidence-bounded draft — always requires human review before
    sending. This is a lighter template than action_drafts.py's full
    invariant system (channel caps, banned-phrase guard, restricted-contact
    check); Phase 1B still covers wiring this into that shared engine
    properly. Never auto-sent — send_allowed is not a field this function
    produces at all, by design."""
    name = (prospect.get("name") or "").split(" ")[0] or "there"
    event_name = config.get("name", "the conference")
    company = prospect.get("current_company")
    company_clause = f" at {company}" if company else ""
    return (
        f"Hi {name} — thinking of you given your work{company_clause}. "
        f"Would love to have you join us at {event_name} this year — interested? "
        f"[DRAFT — requires review before sending; not auto-sent by RB.]"
    )


def build_queue_a(roster: dict, config: dict, *, limit: int | None = None) -> list[dict]:
    """Net New Enterprise Invitations — not_yet_invited prospects, ranked by
    score (which already weighs enterprise_brand/executive/tech-ops/existing
    relationship). Populate config.yaml's enterprise_brand_list to sharpen
    enterprise-vs-regional distinction; it's empty by default."""
    rows = [p for p in roster["prospects"] if p["status"] == "not_yet_invited"]
    rows.sort(key=lambda p: p["score"], reverse=True)
    if limit:
        rows = rows[:limit]
    return [{
        "contact_id": p["contact_id"],
        "name": p["name"],
        "company": p.get("current_company"),
        "title": p.get("current_role"),
        "relationship_strength": _relationship_strength_label(p, config),
        "crm_status": _crm_status_label(p),
        "tier": p["tier_label"],
        "score": p["score"],
        "why_attend": _why_attend(p, config),
        "suggested_linkedin_outreach": _suggested_linkedin_outreach(p, config),
    } for p in rows]


def query_restaurant_operator_prospects(roster: dict, config: dict, *, limit: int | None = None) -> list[dict]:
    """Directly answers: 'every first-degree connection who works for a
    restaurant operator, not in the invite/registration lists, ranked by
    account value.' Restaurant-operator status is precomputed on each
    prospect by build_roster() (is_restaurant_operator) — a keyword
    heuristic against current_company, overridable per-contact via the
    restaurant-operator-override/-exclude tags."""
    rows = [
        p for p in roster["prospects"]
        if p["status"] == "not_yet_invited" and p.get("is_restaurant_operator")
    ]
    rows.sort(key=lambda p: p["score"], reverse=True)
    if limit:
        rows = rows[:limit]
    return [{
        "contact_id": p["contact_id"],
        "name": p["name"],
        "company": p.get("current_company"),
        "title": p.get("current_role"),
        "relationship_strength": _relationship_strength_label(p, config),
        "tier": p["tier_label"],
        "score": p["score"],
        "why_attend": _why_attend(p, config),
    } for p in rows]


def build_queue_b(roster: dict, config: dict, *, limit: int | None = None) -> list[dict]:
    """Registration Conversion — invited-but-not-registered prospects.
    Priority: known-customer (Worldpay) contacts first, then score (which
    already weighs enterprise_brand, existing_relationship, and
    executive_decision_maker — the exact priority order requested)."""
    rows = [p for p in roster["prospects"] if p["status"] == "invited"]
    rows.sort(key=lambda p: (0 if p.get("customer_context") else 1, -p["score"]))
    if limit:
        rows = rows[:limit]
    return [{
        "contact_id": p["contact_id"],
        "name": p["name"],
        "company": p.get("current_company"),
        "title": p.get("current_role"),
        "is_worldpay_customer": bool(p.get("customer_context")),
        "relationship_strength": _relationship_strength_label(p, config),
        "tier": p["tier_label"],
        "score": p["score"],
        "why_attend": _why_attend(p, config),
        "suggested_linkedin_outreach": _suggested_linkedin_outreach(p, config),
    } for p in rows]


def _invite_status_annotation(prospect: dict) -> str:
    """Emoji annotation scheme the operator specified 2026-07-13: distinguish
    which invite source(s) already reached a contact, not just whether
    they're 'invited' — a contact invited via both Genius and Worldpay is a
    different, higher-confidence case than a single-source invite."""
    status = prospect.get("status")
    if status == "registered":
        return "✅ Registered (exclude from action list)"
    if status != "invited":
        return "🟢 Not invited"
    sources = set(prospect.get("invited_via") or [])
    if sources == {"genius", "worldpay"}:
        return "🟣 Invited by both"
    if sources == {"worldpay"}:
        return "🔵 Invited via Worldpay"
    if sources == {"genius"}:
        return "🟡 Invited via Genius"
    return "🟡 Invited (source not recorded)"


# Single-operator system — every prospect's relationship owner is the
# operator himself, same default already used for ELoop.owner in rb_core.py.
# Not a per-contact field to compute; RB has no multi-user ownership model.
RELATIONSHIP_OWNER = "Todd Vahlsing"


def _outreach_method(prospect: dict) -> str:
    """Derived only from the real 'sources' field already on the prospect —
    never guesses at an email address or channel that isn't evidenced."""
    sources = [s.lower() for s in prospect.get("sources") or []]
    has_linkedin = any("linkedin" in s for s in sources)
    has_hubspot = any("hubspot" in s for s in sources)
    if has_linkedin and has_hubspot:
        return "LinkedIn or Email (HubSpot CRM contact)"
    if has_linkedin:
        return "LinkedIn"
    if has_hubspot:
        return "Email (HubSpot CRM contact)"
    return "Not determined from available sources"


def _next_action(prospect: dict) -> str:
    status = prospect.get("status")
    if status == "not_yet_invited":
        return "Send invitation"
    if status == "invited":
        sources = prospect.get("invited_via") or []
        if sources:
            via = " + ".join(sorted(s.capitalize() for s in sources))
            return f"Follow up for registration (invited via {via})"
        return "Follow up for registration"
    return "No action needed"


def build_priority_invite_list(roster: dict, config: dict, *, limit: int | None = None) -> list[dict]:
    """The relationship-first ranked PEOPLE list the operator asked for
    2026-07-13 — one row per person (not an account rollup), ordered by
    score, which is already 40% relationship / 25% executive buying
    authority / 20% strategic brand value / 10% recent engagement / 5%
    existing invite status. Registered contacts are removed entirely (their
    workflow is done); invited-but-not-registered contacts are kept and
    annotated rather than dropped — they're one nudge from converting, not
    out of the running.

    Note on 'Units' (store/location count per brand): no field for this
    exists anywhere in baseline_index.json or any company record RB has —
    it is never included here, and should never be invented by a consumer
    of this data either. 'Segment' is the real scoring tier_label, not an
    invented Enterprise/Large-Franchise/Regional size category with no
    data behind it. 'owner' is always RELATIONSHIP_OWNER — RB is a
    single-operator system, there's no multi-user ownership model to
    derive a real per-contact value from. 'outreach_method' is derived
    from the real 'sources' field (linkedin/hubspot), never a guessed
    channel or invented email address."""
    rows = [p for p in roster["prospects"] if p["status"] in ("not_yet_invited", "invited")]
    rows.sort(key=lambda p: -p["score"])
    if limit:
        rows = rows[:limit]
    return [{
        "priority": i,
        "contact_id": p["contact_id"],
        "contact": p["name"],
        "brand": p.get("current_company"),
        "role": p.get("current_role"),
        "segment": p["tier_label"],
        "existing_worldpay_customer": bool(p.get("customer_context")),
        "relationship": _relationship_strength_label(p, config),
        "invite_status": _invite_status_annotation(p),
        "status": p["status"],
        "owner": RELATIONSHIP_OWNER,
        "outreach_method": _outreach_method(p),
        "next_action": _next_action(p),
        "recommendation": _why_attend(p, config),
        "score": p["score"],
        "tier": p["tier_label"],
    } for i, p in enumerate(rows, start=1)]


def _render_priority_invite_list(rows: list[dict], config: dict) -> str:
    name = config.get("name", "Campaign")
    lines = [
        f"# {name} — Priority Invite List", "",
        f"_Generated {_now_iso()}_", "",
        f"{len(rows)} people ranked by relationship-first score (40% relationship graph / "
        "25% executive buying authority / 20% strategic brand value / 10% recent engagement / "
        "5% existing invite status). Registered contacts are excluded from this list entirely; "
        "invited-but-not-registered contacts are kept and annotated, not removed. No 'Units' "
        "(store/location count) column — that data doesn't exist anywhere in this system; "
        "Segment is the real scoring tier, not an invented size category.",
        "",
        "| Priority | Contact | Brand | Role | Segment | Existing Worldpay | Relationship | Invite Status | "
        "Score | Owner | Outreach Method | Next Action | Recommendation |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['priority']} | {r['contact']} | {r.get('brand') or ''} | {r.get('role') or ''} | "
            f"{r['segment']} | {'Yes' if r['existing_worldpay_customer'] else 'No'} | {r['relationship']} | "
            f"{r['invite_status']} | {r['score']} | {r['owner']} | {r['outreach_method']} | "
            f"{r['next_action']} | {r['recommendation']} |"
        )
    lines.append("")
    return "\n".join(lines)


def _write_priority_invite_list(campaign_id: str, rows: list[dict], config: dict) -> Path:
    d = _campaign_dir(campaign_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "priority_invite_list.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    path = d / "priority_invite_list.md"
    path.write_text(_render_priority_invite_list(rows, config), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Execution plan — sequenced outreach recommendation + tracking scaffold.
# A plan for the operator to execute, not an automation: no step here sends
# anything. Tracking fields report real, currently-computable numbers (status
# counts already on the roster); anything not actually derivable from data
# this engine has (e.g. "responses," which needs interaction/reply tracking
# this engine doesn't do) is marked not_computed with a reason, following the
# same convention as mutation_report.py rather than inventing a number.
# ---------------------------------------------------------------------------

def build_execution_plan(campaign_id: str, roster: dict, config: dict, gaps: list[dict], *, week1_size: int = 25, week2_size: int = 50, executive_introductions_limit: int = 15) -> dict:
    queue_a = build_queue_a(roster, config)
    queue_b = build_queue_b(roster, config)
    tier0 = [p for p in roster["prospects"] if p.get("customer_context")]
    # Cap to a realistic single-week workload — "low effort" (a broker with
    # real proximity, not just highest-DRR-in-baseline) first. Surfacing all
    # gap companies here would read as an inflated, unactionable number, the
    # exact failure mode this whole engine exists to avoid.
    all_introducible = [g for g in gaps if g.get("recommended_introducer")]
    introducible_gaps = sorted(
        all_introducible, key=lambda g: (g["estimated_effort"] != "low", -g["contact_count"])
    )[:executive_introductions_limit]

    week1 = queue_a[:week1_size]
    week2 = queue_a[week1_size:week1_size + week2_size]
    week3 = queue_a[week1_size + week2_size:week1_size + week2_size + week2_size]
    week4 = queue_a[week1_size + week2_size + week2_size:]

    plan = {
        "campaign_id": campaign_id,
        "generated_at": _now_iso(),
        "weeks": [
            {
                "week": 1,
                "focus": "Top-ranked net-new invitations",
                "actions": [f"Send {len(week1)} top-ranked invitations (Queue A rank 1-{len(week1)})"],
                "contacts": [c["contact_id"] for c in week1],
                "messaging_template": "Use each contact's suggested_linkedin_outreach draft — review before sending, per RB's send_allowed=False invariant.",
                "follow_up_schedule": "If no response in 5-7 business days, one follow-up. No further follow-up without a new signal.",
            },
            {
                "week": 2,
                "focus": "Remaining enterprise targets + executive introductions + registration monitoring",
                "actions": [
                    f"Send next {len(week2)} invitations (Queue A rank {week1_size + 1}-{week1_size + len(week2)})",
                    f"Pursue the top {len(introducible_gaps)} executive introduction(s) via known brokers this week "
                    f"({len(all_introducible)} total gap companies have a broker path — see coverage_gaps.json for the rest)",
                    "Re-run --reconcile-workbook / --reconcile-registrations against any new registration export",
                ],
                "contacts": [c["contact_id"] for c in week2],
                "executive_introductions": [
                    {"company": g["company"], "introducer": g["recommended_introducer"], "reason": g["introducer_reason"]}
                    for g in introducible_gaps
                ],
            },
            {
                "week": 3,
                "focus": "Regional/growth brands + follow-up campaign + executive escalation",
                "actions": [
                    f"Send remaining {len(week3)} invitations (Queue A rank {week1_size + week2_size + 1}-{week1_size + week2_size + len(week3)})",
                    f"Follow up with {len(queue_b)} invited-not-registered contact(s) (Queue B)",
                    f"Escalate {len(tier0)} Tier 0 Worldpay customer contact(s) directly",
                ],
                "contacts": [c["contact_id"] for c in week3],
                "follow_up_campaign": [c["contact_id"] for c in queue_b],
                "executive_escalation": [p["contact_id"] for p in tier0],
            },
            {
                "week": 4,
                "focus": "Final invitation wave + registration recovery",
                "actions": [
                    f"Send final {len(week4)} invitations (remainder of Queue A)",
                    f"Registration recovery push for anyone still in Queue B ({len(queue_b)} as of this run)",
                ],
                "contacts": [c["contact_id"] for c in week4],
            },
        ],
        "tracking": _build_execution_tracking(roster, config),
    }
    return plan


def _build_execution_tracking(roster: dict, config: dict) -> dict:
    prospects = roster["prospects"]
    status_counts = Counter(p["status"] for p in prospects)
    by_strength: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "registered": 0})
    for p in prospects:
        strength = _relationship_strength_label(p, config)
        by_strength[strength]["total"] += 1
        if p["status"] == "registered":
            by_strength[strength]["registered"] += 1
    conversion_by_strength = {
        strength: (round(v["registered"] / v["total"], 3) if v["total"] else 0.0)
        for strength, v in by_strength.items()
    }
    return {
        "invitations_sent": status_counts.get("invited", 0) + status_counts.get("registered", 0)
                            + status_counts.get("declined", 0) + status_counts.get("attended", 0),
        "registrations": status_counts.get("registered", 0),
        "attended": status_counts.get("attended", 0),
        "declined": status_counts.get("declined", 0),
        "acceptances": {
            "value": None,
            "not_computed_reason": "RB tracks invited/registered/declined status; a distinct 'accepted the invite but hasn't registered yet' signal isn't captured by any ingested source.",
        },
        "responses": {
            "value": None,
            "not_computed_reason": "Requires reply/interaction tracking cross-referenced per contact — interaction_capture.py records that an interaction occurred, not its content or direction.",
        },
        "conversion_rate_by_relationship_strength": conversion_by_strength,
    }


def _render_execution_plan(plan: dict, config: dict) -> str:
    name = config.get("name", plan["campaign_id"])
    lines = [f"# {name} — Execution Plan", "", f"_Generated {plan['generated_at']}_", "",
             "This is a recommended sequence for you to execute manually — nothing here sends automatically.", ""]
    for w in plan["weeks"]:
        lines.append(f"## Week {w['week']} — {w['focus']}")
        lines.append("")
        for a in w["actions"]:
            lines.append(f"- {a}")
        if w.get("messaging_template"):
            lines.append(f"- **Messaging:** {w['messaging_template']}")
        if w.get("follow_up_schedule"):
            lines.append(f"- **Follow-up:** {w['follow_up_schedule']}")
        lines.append("")
    lines.append("## Tracking")
    lines.append("")
    t = plan["tracking"]
    lines.append(f"- Invitations sent (to date): {t['invitations_sent']}")
    lines.append(f"- Registrations: {t['registrations']}")
    lines.append(f"- Attended: {t['attended']}  |  Declined: {t['declined']}")
    lines.append(f"- Acceptances: not computed — {t['acceptances']['not_computed_reason']}")
    lines.append(f"- Responses: not computed — {t['responses']['not_computed_reason']}")
    lines.append("- Conversion rate by relationship strength:")
    for strength, rate in t["conversion_rate_by_relationship_strength"].items():
        lines.append(f"  - {strength}: {rate:.1%}")
    lines.append("")
    return "\n".join(lines)


def _write_execution_plan(campaign_id: str, plan: dict, config: dict) -> Path:
    d = _campaign_dir(campaign_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "execution_plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    path = d / "execution_plan.md"
    path.write_text(_render_execution_plan(plan, config), encoding="utf-8")
    return path


def build_executive_dashboard(campaign_id: str, roster: dict, company_rollup: dict, *, top_n: int = 10) -> dict:
    prospects = roster["prospects"]
    status_counts = Counter(p["status"] for p in prospects)
    tier0_prospects = [p for p in prospects if p.get("customer_context")]
    top_brands = sorted(company_rollup["companies"], key=lambda c: c["best_contact_score"], reverse=True)[:top_n]
    top_relationships = sorted(prospects, key=lambda p: p["score"], reverse=True)[:top_n]

    return {
        "campaign_id": campaign_id,
        "generated_at": _now_iso(),
        "eligible_enterprise_prospects": len(prospects),
        "invited_not_registered": status_counts.get("invited", 0),
        "registered": status_counts.get("registered", 0),
        "not_yet_invited": status_counts.get("not_yet_invited", 0),
        "declined": status_counts.get("declined", 0),
        "attended": status_counts.get("attended", 0),
        "company_count": len(company_rollup["companies"]),
        "worldpay_customer_conversion_opportunities": len(tier0_prospects),
        "top_target_brands": [
            {"company": c["company"], "contact_count": c["contact_count"],
             "best_contact_name": c["best_contact_name"], "best_contact_score": c["best_contact_score"]}
            for c in top_brands
        ],
        "highest_value_relationships": [
            {"name": p["name"], "company": p.get("current_company"), "score": p["score"], "tier": p["tier_label"]}
            for p in top_relationships
        ],
    }


def _render_executive_dashboard(dashboard: dict, config: dict) -> str:
    name = config.get("name", dashboard["campaign_id"])
    lines = [
        f"# {name} — Executive Dashboard",
        "",
        f"_Generated {dashboard['generated_at']}_",
        "",
        f"- **Eligible enterprise prospects:** {dashboard['eligible_enterprise_prospects']}",
        f"- **Invited, not yet registered:** {dashboard['invited_not_registered']}",
        f"- **Registered:** {dashboard['registered']}",
        f"- **Not yet invited:** {dashboard['not_yet_invited']}",
        f"- **Declined:** {dashboard['declined']}  |  **Attended:** {dashboard['attended']}",
        f"- **Companies represented:** {dashboard['company_count']}",
        f"- **Worldpay customer conversion opportunities (Tier 0):** {dashboard['worldpay_customer_conversion_opportunities']}",
        "",
        "## Top Target Brands",
        "",
        "| Company | # Contacts | Best Contact | Score |",
        "|---|---|---|---|",
    ]
    for b in dashboard["top_target_brands"]:
        lines.append(f"| {b['company']} | {b['contact_count']} | {b['best_contact_name']} | {b['best_contact_score']} |")
    lines.append("")
    lines.append("## Highest-Value Relationships")
    lines.append("")
    lines.append("| Name | Company | Score | Tier |")
    lines.append("|---|---|---|---|")
    for r in dashboard["highest_value_relationships"]:
        lines.append(f"| {r['name']} | {r['company'] or ''} | {r['score']} | {r['tier']} |")
    lines.append("")
    return "\n".join(lines)


def _write_executive_dashboard(campaign_id: str, roster: dict, company_rollup: dict) -> Path:
    config = load_campaign_config(campaign_id)
    dashboard = build_executive_dashboard(campaign_id, roster, company_rollup)
    d = _campaign_dir(campaign_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "executive_dashboard.json").write_text(json.dumps(dashboard, indent=2) + "\n", encoding="utf-8")
    path = d / "executive_dashboard.md"
    path.write_text(_render_executive_dashboard(dashboard, config), encoding="utf-8")
    return path


def export_campaign_workbook(campaign_id: str, output_path: str | Path | None = None) -> Path:
    """Multi-tab Excel workbook: Executive Dashboard, Tier 0, Queue A, Queue B,
    Company Coverage. Reads already-built roster/company_rollup — does not
    recompute scoring/tiering itself."""
    from openpyxl import Workbook

    # RB-SECURITY-2026-09-05: rows here can carry externally-influenced
    # strings (a contact/company name captured from LinkedIn, a role
    # title, a free-text "why attend"/"recommendation" string) into a real
    # exported artifact -- same formula/CSV-injection guard (CWE-1236)
    # already applied to Blue Sheets, extracted to xlsx_safety.py so every
    # xlsx writer in RB shares one implementation.
    def _safe_append(ws, row):
        ws.append(xlsx_safety.sanitize_row(row))

    roster = _load_roster(campaign_id)
    if not roster:
        raise CampaignNotFoundError(f"No roster exists yet for campaign {campaign_id!r} — run --build-roster first.")
    config = load_campaign_config(campaign_id)
    company_rollup = build_company_rollup(campaign_id, roster)
    dashboard = build_executive_dashboard(campaign_id, roster, company_rollup)
    queue_a = build_queue_a(roster, config)
    queue_b = build_queue_b(roster, config)
    restaurant_ops = query_restaurant_operator_prospects(roster, config)
    account_first_rows = build_account_first_recommendations(roster, campaign_id)
    priority_invite_rows = build_priority_invite_list(roster, config)
    tier0_rows = [p for p in roster["prospects"] if p.get("customer_context")]

    wb = Workbook()
    ws = wb.active
    ws.title = "Executive Dashboard"
    _safe_append(ws, ["Metric", "Value"])
    for key in (
        "eligible_enterprise_prospects", "invited_not_registered", "registered",
        "not_yet_invited", "declined", "attended", "company_count",
        "worldpay_customer_conversion_opportunities",
    ):
        _safe_append(ws, [key, dashboard[key]])
    _safe_append(ws, [])
    _safe_append(ws, ["Top Target Brands"])
    _safe_append(ws, ["Company", "# Contacts", "Best Contact", "Score"])
    for b in dashboard["top_target_brands"]:
        _safe_append(ws, [b["company"], b["contact_count"], b["best_contact_name"], b["best_contact_score"]])
    _safe_append(ws, [])
    _safe_append(ws, ["Highest-Value Relationships"])
    _safe_append(ws, ["Name", "Company", "Score", "Tier"])
    for r in dashboard["highest_value_relationships"]:
        _safe_append(ws, [r["name"], r["company"], r["score"], r["tier"]])

    ws_pi = wb.create_sheet("Priority Invite List")
    _safe_append(ws_pi, [
        "Priority", "Contact", "Brand", "Role", "Segment", "Existing Worldpay", "Relationship",
        "Invite Status", "Score", "Owner", "Outreach Method", "Next Action", "Recommendation",
    ])
    for r in priority_invite_rows:
        _safe_append(ws_pi, [
            r["priority"], r["contact"], r["brand"], r["role"], r["segment"],
            "Yes" if r["existing_worldpay_customer"] else "No", r["relationship"],
            r["invite_status"], r["score"], r["owner"], r["outreach_method"], r["next_action"],
            r["recommendation"],
        ])

    ws0 = wb.create_sheet("Tier 0 - Worldpay Conversion")
    _safe_append(ws0, ["Name", "Company", "Title", "Merchant", "Status", "Score"])
    for p in tier0_rows:
        cc = p.get("customer_context") or {}
        _safe_append(ws0, [p["name"], p.get("current_company"), p.get("current_role"), cc.get("merchant"), p["status"], p["score"]])

    ws_a = wb.create_sheet("Queue A - New Invitations")
    _safe_append(ws_a, ["Name", "Company", "Title", "Relationship Strength", "CRM Status", "Tier", "Score", "Why Attend", "Suggested LinkedIn Outreach"])
    for r in queue_a:
        _safe_append(ws_a, [r["name"], r["company"], r["title"], r["relationship_strength"], r["crm_status"], r["tier"], r["score"], r["why_attend"], r["suggested_linkedin_outreach"]])

    ws_b = wb.create_sheet("Queue B - Reg Conversion")
    _safe_append(ws_b, ["Name", "Company", "Title", "Worldpay Customer", "Relationship Strength", "Tier", "Score", "Why Attend", "Suggested LinkedIn Outreach"])
    for r in queue_b:
        _safe_append(ws_b, [r["name"], r["company"], r["title"], r["is_worldpay_customer"], r["relationship_strength"], r["tier"], r["score"], r["why_attend"], r["suggested_linkedin_outreach"]])

    ws_r = wb.create_sheet("Restaurant Operators")
    _safe_append(ws_r, ["Score", "Name", "Company", "Title", "Relationship Strength", "Tier", "Why Attend"])
    for r in restaurant_ops:
        _safe_append(ws_r, [r["score"], r["name"], r["company"], r["title"], r["relationship_strength"], r["tier"], r["why_attend"]])

    ws_af = wb.create_sheet("Account-First Recommendations")
    _safe_append(ws_af, ["Priority", "Account", "Tier", "Recommended Contact", "Title", "Why This Person", "Registered", "Invited", "Action", "Score", "Also Invite (same account)"])
    for i, r in enumerate(account_first_rows, start=1):
        colleagues = ", ".join(r.get("other_known_contacts_to_invite") or [])
        _safe_append(ws_af, [i, r["account"], r["account_tier"], r["name"], r["title"], r["why_this_person"], r["registered"], r["invited"], r["action"], r["score"], colleagues])

    ws_c = wb.create_sheet("Company Coverage")
    _safe_append(ws_c, ["Company", "# Contacts", "Best Contact", "Top Tier", "Status Summary"])
    for c in company_rollup["companies"]:
        status = ", ".join(f"{k}: {v}" for k, v in c["status_summary"].items())
        _safe_append(ws_c, [c["company"], c["contact_count"], c["best_contact_name"], c["top_tier_label"], status])

    output_path = Path(output_path) if output_path else _campaign_dir(campaign_id) / f"{campaign_id}_workbook.xlsx"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return output_path


# ---------------------------------------------------------------------------
# RI events — relationship learning
# ---------------------------------------------------------------------------

def _emit_campaign_event(
    campaign_id: str,
    contact_id: str,
    contact_name: str,
    status: str,
    *,
    today: date | None = None,
    reason: str | None = None,
) -> ri_events.AppendResult:
    today = today or date.today()
    event = {
        "event_at": today.isoformat(),
        "event_at_confidence": "high",
        "source": {"type": "campaign_lifecycle", "id": f"{campaign_id}:{contact_id}:{status}:{today.isoformat()}"},
        "entities": {
            "people": [{"id": contact_id, "raw": contact_name, "decision": "matched_existing", "matched_id": contact_id}],
            "companies": [],
        },
        "dedupe": {"decision": "new_event"},
        "signal": {"type": "campaign_status_change", "campaign_id": campaign_id, "status": status, "reason": reason},
        "persistence": {"status": "persisted"},
    }
    return ri_events.append(event)


# ---------------------------------------------------------------------------
# Stage 5 — Rendering + writes
# ---------------------------------------------------------------------------

def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(core.PROJECT_DIR))
    except ValueError:
        return str(path)


def _write_roster(campaign_id: str, roster: dict[str, Any]) -> None:
    d = _campaign_dir(campaign_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "roster.json").write_text(json.dumps(roster, indent=2) + "\n", encoding="utf-8")


def _write_company_rollup(campaign_id: str, rollup: dict[str, Any]) -> None:
    d = _campaign_dir(campaign_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "company_rollup.json").write_text(json.dumps(rollup, indent=2) + "\n", encoding="utf-8")


def _render_roster_report(campaign_id: str, roster: dict[str, Any], config: dict[str, Any]) -> str:
    name = config.get("name", campaign_id)
    lines = [
        f"# {name} — Roster",
        "",
        f"_Generated {roster['generated_at']}_",
        "",
        f"**Prospects:** {len(roster['prospects'])}  |  **Excluded:** {len(roster['excluded'])}",
        "",
        "| Rank | Name | Company | Role | Score | Tier | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, p in enumerate(roster["prospects"], start=1):
        lines.append(
            f"| {i} | {p['name']} | {p.get('current_company') or ''} | "
            f"{p.get('current_role') or ''} | {p['score']} | {p['tier_label']} | {p['status']} |"
        )
    lines.append("")

    if roster["excluded"]:
        lines.append("## Excluded")
        lines.append("")
        lines.append("| Name | Company | Reason |")
        lines.append("|---|---|---|")
        for e in roster["excluded"]:
            lines.append(f"| {e['name']} | {e.get('current_company') or ''} | {e['reason']} |")
        lines.append("")

    lines.append("## Score Breakdown")
    lines.append("")
    for p in roster["prospects"]:
        if not p.get("score_breakdown"):
            continue
        parts = ", ".join(f"{b['dimension']} +{b['points']}" for b in p["score_breakdown"])
        lines.append(f"- **{p['name']}** ({p['score']}): {parts}")
    lines.append("")
    return "\n".join(lines)


def _write_roster_report(campaign_id: str, roster: dict[str, Any], config: dict[str, Any]) -> Path:
    d = _campaign_dir(campaign_id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "roster_report.md"
    path.write_text(_render_roster_report(campaign_id, roster, config), encoding="utf-8")
    return path


def _render_company_dashboard(campaign_id: str, rollup: dict[str, Any], config: dict[str, Any]) -> str:
    name = config.get("name", campaign_id)
    lines = [
        f"# {name} — Company Dashboard",
        "",
        f"_Generated {rollup['generated_at']}_",
        "",
        "| Company | # Contacts | Best Contact | Top Tier | Status Summary |",
        "|---|---|---|---|---|",
    ]
    for c in rollup["companies"]:
        status = ", ".join(f"{k}: {v}" for k, v in c["status_summary"].items())
        lines.append(
            f"| {c['company']} | {c['contact_count']} | "
            f"{c['best_contact_name']} ({c['best_contact_score']}) | {c['top_tier_label']} | {status} |"
        )
    lines.append("")
    return "\n".join(lines)


def _write_company_dashboard(campaign_id: str, rollup: dict[str, Any]) -> Path:
    config = load_campaign_config(campaign_id)
    d = _campaign_dir(campaign_id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "company_dashboard.md"
    path.write_text(_render_company_dashboard(campaign_id, rollup, config), encoding="utf-8")
    return path


def _diff_roster(prior_roster: dict[str, Any] | None, new_roster: dict[str, Any]) -> dict[str, Any]:
    prior_by_id = {p["contact_id"]: p for p in (prior_roster or {}).get("prospects", [])}
    new_by_id = {p["contact_id"]: p for p in new_roster["prospects"]}

    newly_added = [p for cid, p in new_by_id.items() if cid not in prior_by_id]
    newly_excluded_ids = set(prior_by_id) - set(new_by_id)
    prior_excluded_by_id = {e["contact_id"]: e for e in (prior_roster or {}).get("excluded", [])}
    newly_excluded = [
        prior_excluded_by_id.get(cid) or {"contact_id": cid, "name": prior_by_id[cid]["name"], "reason": "no_longer_eligible"}
        for cid in newly_excluded_ids
    ]

    score_changes, status_transitions = [], []
    for cid, new_p in new_by_id.items():
        old_p = prior_by_id.get(cid)
        if not old_p:
            continue
        if old_p["score"] != new_p["score"]:
            score_changes.append({"contact_id": cid, "name": new_p["name"], "old_score": old_p["score"], "new_score": new_p["score"]})
        if old_p["status"] != new_p["status"]:
            status_transitions.append({"contact_id": cid, "name": new_p["name"], "old_status": old_p["status"], "new_status": new_p["status"]})

    return {
        "newly_added": newly_added,
        "newly_excluded": newly_excluded,
        "score_changes": score_changes,
        "status_transitions": status_transitions,
    }


def _render_delta_report(campaign_id: str, diff: dict[str, Any], today: date) -> str:
    lines = [f"# Campaign Delta — {campaign_id} — {today.isoformat()}", ""]
    if diff["newly_added"]:
        lines.append(f"## Newly Eligible ({len(diff['newly_added'])})")
        lines.append("")
        for p in diff["newly_added"]:
            lines.append(f"- {p['name']} ({p.get('current_company') or 'no company on file'}) — score {p['score']}, tier {p['tier_label']}")
        lines.append("")
    if diff["newly_excluded"]:
        lines.append(f"## Newly Excluded ({len(diff['newly_excluded'])})")
        lines.append("")
        for e in diff["newly_excluded"]:
            lines.append(f"- {e['name']} — {e.get('reason', 'no longer eligible')}")
        lines.append("")
    if diff["score_changes"]:
        lines.append(f"## Score Changes ({len(diff['score_changes'])})")
        lines.append("")
        for s in diff["score_changes"]:
            lines.append(f"- {s['name']}: {s['old_score']} -> {s['new_score']}")
        lines.append("")
    if diff["status_transitions"]:
        lines.append(f"## Status Transitions ({len(diff['status_transitions'])})")
        lines.append("")
        for t in diff["status_transitions"]:
            lines.append(f"- {t['name']}: {t['old_status']} -> {t['new_status']}")
        lines.append("")
    if not any(diff.values()):
        lines.append("No changes since the last run.")
        lines.append("")
    return "\n".join(lines)


def _write_delta_report(campaign_id: str, prior_roster: dict[str, Any] | None, new_roster: dict[str, Any], today: date) -> Path | None:
    diff = _diff_roster(prior_roster, new_roster)
    DELTAS_DIR.mkdir(parents=True, exist_ok=True)
    path = DELTAS_DIR / f"campaign_{campaign_id}_{today.isoformat()}.md"
    path.write_text(_render_delta_report(campaign_id, diff, today), encoding="utf-8")
    return path


def _write_latest_cache(campaign_id: str, result: dict[str, Any]) -> None:
    core.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = core.CACHE_DIR / f"campaign_{campaign_id}_latest.json"
    path.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="RB Conference Campaign Intelligence Engine")
    parser.add_argument("--campaign", metavar="CAMPAIGN_ID")
    parser.add_argument("--build-roster", action="store_true")
    parser.add_argument("--reconcile-registrations", metavar="PATH", help="CSV registration list")
    parser.add_argument("--reconcile-workbook", metavar="PATH", help="xlsx with 'Registered'/'Invite List' sheets")
    parser.add_argument("--apply-customer-contacts", metavar="PATH", help="xlsx known-customer contact list (e.g. Worldpay merchants)")
    parser.add_argument("--sheet", metavar="NAME", default="Sheet1", help="Sheet name for --apply-customer-contacts")
    parser.add_argument("--set-status", nargs=2, metavar=("CONTACT_ID", "STATUS"))
    parser.add_argument("--reason", metavar="TEXT")
    parser.add_argument("--report", action="store_true", help="Regenerate markdown reports from the existing roster")
    parser.add_argument("--export-workbook", nargs="?", const="", metavar="PATH", help="Write the multi-tab Excel workbook (default path if PATH omitted)")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--refresh-all", action="store_true", help="Living Campaign: rebuild every active campaign's roster (e.g. after a CLI-driven ingest)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.list:
        print(json.dumps(list_campaigns(), indent=2, default=str))
        return 0

    if args.refresh_all:
        print(json.dumps(refresh_active_campaigns(dry_run=args.dry_run), indent=2, default=str))
        return 0

    if not args.campaign:
        parser.print_help()
        return 0

    if args.build_roster:
        result = build_roster(args.campaign, dry_run=args.dry_run)
    elif args.reconcile_registrations:
        result = reconcile_registrations(args.campaign, args.reconcile_registrations, dry_run=args.dry_run)
    elif args.reconcile_workbook:
        result = reconcile_registration_workbook(args.campaign, args.reconcile_workbook, dry_run=args.dry_run)
    elif args.apply_customer_contacts:
        result = apply_customer_contact_list(args.campaign, args.apply_customer_contacts, sheet_name=args.sheet, dry_run=args.dry_run)
    elif args.set_status:
        contact_id, status = args.set_status
        result = set_status(args.campaign, contact_id, status, reason=args.reason, dry_run=args.dry_run)
    elif args.report:
        roster = _load_roster(args.campaign)
        if not roster:
            result = {"ok": False, "error": f"No roster exists yet for campaign {args.campaign!r}."}
        else:
            config = load_campaign_config(args.campaign)
            rollup = build_company_rollup(args.campaign, roster)
            _write_company_rollup(args.campaign, rollup)
            _write_roster_report(args.campaign, roster, config)
            _write_company_dashboard(args.campaign, rollup)
            dashboard_path = _write_executive_dashboard(args.campaign, roster, rollup)
            gaps = build_coverage_gaps(roster)
            gaps_path = _write_coverage_gaps(args.campaign, gaps, config)
            plan = build_execution_plan(args.campaign, roster, config, gaps)
            plan_path = _write_execution_plan(args.campaign, plan, config)
            account_first_rows = build_account_first_recommendations(roster, args.campaign)
            account_first_path = _write_account_first(args.campaign, account_first_rows, config)
            priority_invite_rows = build_priority_invite_list(roster, config)
            priority_invite_path = _write_priority_invite_list(args.campaign, priority_invite_rows, config)
            result = {
                "ok": True, "campaign_id": args.campaign, "regenerated": True,
                "executive_dashboard_path": _display_path(dashboard_path),
                "coverage_gaps_path": _display_path(gaps_path),
                "execution_plan_path": _display_path(plan_path),
                "account_first_recommendations_path": _display_path(account_first_path),
                "priority_invite_list_path": _display_path(priority_invite_path),
            }
    elif args.export_workbook is not None:
        try:
            path = export_campaign_workbook(args.campaign, args.export_workbook or None)
            result = {"ok": True, "campaign_id": args.campaign, "workbook_path": _display_path(path)}
        except CampaignNotFoundError as exc:
            result = {"ok": False, "error": str(exc)}
    else:
        parser.print_help()
        return 0

    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
