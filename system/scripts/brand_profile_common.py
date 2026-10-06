#!/usr/bin/env python3
"""
brand_profile_common.py — storage primitives for per-brand company
profiles (Ecosystem Lookup Tool project, Phase A).

Why a separate per-brand file tree instead of embedding into
system/ecosystem_intelligence.json's entity.attributes: this codebase's own
precedent is per-entity file trees for anything that accumulates real
narrative/curated content (customers_prospects/, competitor_intelligence/),
not embedding it into the one large graph file that everything else already
writes to. Keeps this project's writes isolated from the graph's own
write-lock/snapshot discipline (ecosystem_intelligence.py's _write_graph
already re-validates and snapshots on every write) and keeps individual
brand profiles independently diffable.

Field-level provenance shape is copied verbatim from the one real, proven
precedent for exactly what Todd asked for ("every field gets an as-of date
and a source; stale data is worse than blank") --
customers_prospects/accounts/cafe-rio/brand_profile.json's own field shape:
{value, status, evidence_ids, confidence, as_of, scope, last_reviewed_by}.

Every field starts in the honest "not_yet_researched" state (see
unresearched_field()) -- never fabricated, never silently blank with no
explanation. It only ever becomes populated through: (a) fields computable
directly from already-trusted graph data (identity.segment via the entity
itself, footprint via Technomic ingestion, trajectory computed fresh on
every read, never stored stale), or (b) real evidence -- a confirmed
candidate from executive_move_promotion.py/ownership_promotion.py, a
confirmed team-portal submission (see brand_profile_review_queue.py), or a
promoted deep-research observation.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import ecosystem_intelligence as ei  # noqa: E402
import slug_safety  # noqa: E402

SYSTEM_DIR = SCRIPTS_DIR.parent
ROOT = SYSTEM_DIR / "brand_profiles"

# Decision #3 (plan): Technomic net-unit-change YoY, falling back to
# sales_delta_pct when unit data is missing. Both attributes.unit_delta and
# attributes.sales_delta are already expressed as real percentages (e.g.
# 0.74 == +0.74%, confirmed against real McDonald's data: unit_count
# 13457->13557 == +0.743%), not fractions -- compare directly, no *100.
GROWING_THRESHOLD_PCT = 3.0
CONTRACTING_THRESHOLD_PCT = -3.0

TRAJECTORY_RULE_DESCRIPTION = (
    f"Technomic net-unit-change YoY (sales-delta fallback when unit data is "
    f"missing): > +{GROWING_THRESHOLD_PCT:.0f}% Growing, "
    f"{CONTRACTING_THRESHOLD_PCT:.0f}% to +{GROWING_THRESHOLD_PCT:.0f}% Flat, "
    f"< {CONTRACTING_THRESHOLD_PCT:.0f}% Contracting."
)


def today() -> str:
    return date.today().isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Field-level provenance
# ---------------------------------------------------------------------------

def field(
    value,
    *,
    status: str = "confirmed",
    evidence_ids: list[str] | None = None,
    confidence: str = "high",
    as_of: str | None = None,
    scope: str = "brand",
    last_reviewed_by: str = "system:brand_profile_common",
    source_url: str | None = None,
) -> dict:
    """status: "confirmed" | "reported" | "not_yet_researched". Matches
    customers_prospects/accounts/*/brand_profile.json's real field shape
    exactly, so a team-submitted-then-confirmed fact and a deep-research-
    sourced one are indistinguishable in storage. source_url (2026-10-03,
    added for import_brand_company_profile_research.py) is included only
    when given, same optional-key convention as competitor_intelligence_
    common.py's extended_field()."""
    leaf = {
        "value": value,
        "status": status,
        "evidence_ids": evidence_ids or [],
        "confidence": confidence,
        "as_of": as_of or today(),
        "scope": scope,
        "last_reviewed_by": last_reviewed_by,
    }
    if source_url:
        leaf["source_url"] = source_url
    return leaf


def unresearched_field(*, scope: str = "brand") -> dict:
    """The honest-blank state every field starts in. Never a guessed value,
    never a silently-absent key -- a reader can always tell the difference
    between "checked, nothing found" (not this) and "never researched"
    (this)."""
    return {
        "value": None,
        "status": "not_yet_researched",
        "evidence_ids": [],
        "confidence": "unknown",
        "as_of": None,
        "scope": scope,
        "last_reviewed_by": None,
    }


def is_human_reviewed(f: dict) -> bool:
    """True if a field's current value was set by a real person (team
    submission or Todd's own review), not system-derived. Used to decide
    whether a system refresh is allowed to overwrite it -- a human's
    confirmed correction always wins over a re-derived value."""
    reviewer = (f or {}).get("last_reviewed_by") or ""
    return reviewer.startswith("human:") or reviewer.startswith("team:")


# ---------------------------------------------------------------------------
# recent_signals -- structured, typed entries (2026-09-25, deep-research
# expansion). Until now this field had no defined shape (empty_profile()
# only ever initialized it to []; nothing wrote to it) -- Todd's direct
# instruction ("gather all information relevant to their business,
# initiatives, health, status, priorities, weaknesses, challenges and
# overall positioning ... anything that helps us understand them better")
# is the trigger to give it one, rather than leave it an undocumented
# free-text bucket a future writer has to guess the shape of. Each entry
# is a signal_field()-shaped dict -- same {value, status, evidence_ids,
# confidence, as_of, scope, last_reviewed_by} provenance as every other
# field in this module, plus `signal_type` from SIGNAL_TYPES so a reader
# (or the UI) can group/filter without parsing free text.
# ---------------------------------------------------------------------------

SIGNAL_TYPES = (
    "strategic_initiative",       # a real, named initiative or stated priority (e.g. a loyalty relaunch, a new kitchen format)
    "financial_health",           # earnings/funding/liquidity signal not already captured by the trajectory badge's Technomic numbers
    "leadership_change",          # an exec move not yet promoted into the leadership field itself (e.g. reported but unconfirmed)
    "ownership_change",           # M&A, PE sponsor change, franchise-group sale
    "expansion_or_contraction",   # real unit growth/closure news distinct from the computed trajectory badge
    "challenge_or_headwind",      # a real, sourced difficulty -- traffic decline, litigation, supply issue, brand controversy
    "competitive_positioning",    # how the brand positions itself, or is positioned by press/analysts, against its category
    "other",
)


def signal_field(
    value: str,
    *,
    signal_type: str,
    status: str = "reported",
    evidence_ids: list[str] | None = None,
    confidence: str = "medium",
    as_of: str | None = None,
    last_reviewed_by: str = "system:deep_research",
    source_url: str | None = None,
    visibility: str = "team_shareable",
) -> dict:
    """One entry for profile["recent_signals"]. status defaults to
    "reported" (not "confirmed") because a signal is typically a single
    sourced observation, not a fact independently corroborated the way a
    footprint number or a confirmed leadership entry is -- promote to
    "confirmed" explicitly only when a second independent source, or
    Todd's own review, actually corroborates it.

    visibility (added 2026-09-29): "team_shareable" (default) or
    "private". shareable_view() below only ever allowlisted this whole
    FIELD, never individual entries within it -- real gap found live: a
    single entry added through the Team Portal's own intended write path
    can carry content Todd didn't mean to expose broadly, with no way to
    mark just that one entry private without pulling the whole field.
    Never inferred from last_reviewed_by -- an explicit, later-settable
    flag, same "structured field, not a guess" discipline as everything
    else in this module."""
    if signal_type not in SIGNAL_TYPES:
        raise ValueError(f"signal_type must be one of {SIGNAL_TYPES}, got {signal_type!r}")
    f = {
        "signal_type": signal_type,
        "value": value,
        "status": status,
        "evidence_ids": evidence_ids or [],
        "confidence": confidence,
        "as_of": as_of or today(),
        "scope": "brand",
        "last_reviewed_by": last_reviewed_by,
        "visibility": visibility,
    }
    if source_url:
        f["source_url"] = source_url
    return f


def add_signal(profile: dict, *, dedupe: bool = True, **signal_kwargs) -> dict:
    """Appends a signal_field() to profile["recent_signals"], in place.
    Never overwrites or removes an existing signal -- each is its own
    discrete, dated observation (same reasoning as competitor_intelligence
    _common.py's EXTENDED_PROFILE_FIELDS). dedupe=True (default) skips
    appending when an entry with the same signal_type and value already
    exists, so re-running promotion against the same source doesn't pile
    up duplicates."""
    new_entry = signal_field(**signal_kwargs)
    signals = profile.setdefault("recent_signals", [])
    if dedupe and any(
        s.get("signal_type") == new_entry["signal_type"] and s.get("value") == new_entry["value"]
        for s in signals
    ):
        return profile
    signals.append(new_entry)
    return profile


# ---------------------------------------------------------------------------
# pain_points -- Value Wedge's "Customer Needs" circle (2026-09-28). Real,
# ongoing evidence-driven data (calls/emails/JPR recordings), same shape
# and same dedupe discipline as recent_signals -- starts empty for nearly
# every brand and fills in over time, never a one-time backfill.
# ---------------------------------------------------------------------------

def pain_point_field(
    value: str, *, status: str = "reported", evidence_ids: list[str] | None = None,
    confidence: str = "medium", as_of: str | None = None,
    last_reviewed_by: str = "system:team_portal", source_url: str | None = None,
    visibility: str = "team_shareable",
) -> dict:
    """visibility: "team_shareable" (default) or "private" -- see
    signal_field()'s docstring for why this exists at the entry level,
    not just as a whole-field allowlist decision."""
    f = {
        "value": value, "status": status, "evidence_ids": evidence_ids or [],
        "confidence": confidence, "as_of": as_of or today(), "scope": "brand",
        "last_reviewed_by": last_reviewed_by, "visibility": visibility,
    }
    if source_url:
        f["source_url"] = source_url
    return f


def add_pain_point(profile: dict, *, dedupe: bool = True, **pain_point_kwargs) -> dict:
    new_entry = pain_point_field(**pain_point_kwargs)
    points = profile.setdefault("pain_points", [])
    if dedupe and any(p.get("value") == new_entry["value"] for p in points):
        return profile
    points.append(new_entry)
    return profile


# ---------------------------------------------------------------------------
# Trajectory (computed fresh every read -- never persisted as a stored,
# potentially-stale value)
# ---------------------------------------------------------------------------

def compute_trajectory(entity: dict) -> dict:
    """Pure function, no I/O. Returns {badge, metric_used, value_pct,
    as_of_year, underlying, rule}. badge is one of "Growing"/"Flat"/
    "Contracting"/"insufficient_data" -- the last is an explicit, honest
    outcome for the ~5% of brands with no Technomic delta data at all,
    never a guessed default."""
    attrs = entity.get("attributes") or {}
    unit_delta = attrs.get("unit_delta")
    sales_delta = attrs.get("sales_delta")

    metric_used = None
    pct = None
    if isinstance(unit_delta, (int, float)):
        pct = float(unit_delta)
        metric_used = "unit_delta_pct"
    elif isinstance(sales_delta, (int, float)):
        pct = float(sales_delta)
        metric_used = "sales_delta_pct"

    tech_hist = attrs.get("technomic_history") or {}
    latest_year = max(tech_hist.keys(), default=None) if tech_hist else None
    underlying = tech_hist.get(latest_year) if latest_year else None

    if pct is None:
        return {
            "badge": "insufficient_data",
            "metric_used": None,
            "value_pct": None,
            "as_of_year": latest_year,
            "underlying": underlying,
            "rule": TRAJECTORY_RULE_DESCRIPTION,
        }

    if pct > GROWING_THRESHOLD_PCT:
        badge = "Growing"
    elif pct < CONTRACTING_THRESHOLD_PCT:
        badge = "Contracting"
    else:
        badge = "Flat"

    return {
        "badge": badge,
        "metric_used": metric_used,
        "value_pct": pct,
        "as_of_year": latest_year,
        "underlying": underlying,
        "rule": TRAJECTORY_RULE_DESCRIPTION,
    }


# ---------------------------------------------------------------------------
# Footprint (system-derivable from already-ingested Technomic data)
# ---------------------------------------------------------------------------

def _derive_footprint_fields(entity: dict) -> dict:
    attrs = entity.get("attributes") or {}
    tech_hist = attrs.get("technomic_detailed_history") or {}
    latest_year = max(tech_hist.keys(), default=None) if tech_hist else None
    detail = tech_hist.get(latest_year) if latest_year else None

    out = {}
    total_units = attrs.get("unit_count")
    if total_units is not None:
        out["total_units"] = field(
            total_units, status="confirmed", confidence="high",
            as_of=today(), last_reviewed_by="system:technomic_ingest",
        )
    if detail:
        if detail.get("franchise_units") is not None:
            out["franchised_units"] = field(
                detail["franchise_units"], status="confirmed", confidence="high",
                as_of=today(), last_reviewed_by="system:technomic_ingest",
            )
        if detail.get("company_units") is not None:
            out["company_owned_units"] = field(
                detail["company_units"], status="confirmed", confidence="high",
                as_of=today(), last_reviewed_by="system:technomic_ingest",
            )
    return out


def _refresh_footprint(profile: dict, entity: dict) -> None:
    """Fills in footprint fields from Technomic data, but only where the
    existing field isn't already a human-reviewed value -- a confirmed
    human correction always wins over a re-derived one, never silently
    overwritten by the next refresh."""
    derived = _derive_footprint_fields(entity)
    footprint = profile.setdefault("footprint", {})
    for name, new_field in derived.items():
        existing = footprint.get(name)
        if existing is not None and is_human_reviewed(existing):
            continue
        footprint[name] = new_field


# ---------------------------------------------------------------------------
# Profile skeleton
# ---------------------------------------------------------------------------

def empty_profile(brand_id: str, brand_name: str) -> dict:
    now = now_iso()
    return {
        "brand_id": brand_id,
        "brand_name": brand_name,
        "identity": {
            "parent_ownership": unresearched_field(),
            "hq_city_state": unresearched_field(),
            "founded_year": unresearched_field(),
            # 2026-10-03: the legal franchisor entity (e.g. "Gosh
            # Enterprises, Inc." for Charleys) -- distinct from
            # parent_ownership, which is the ownership STRUCTURE/owner,
            # not the contracting legal entity itself.
            "legal_entity": unresearched_field(),
        },
        "synopsis": unresearched_field(),
        "leadership": {"confirmed": [], "reported_unverified": []},
        "footprint": {
            "total_units": unresearched_field(),
            "franchised_units": unresearched_field(),
            "company_owned_units": unresearched_field(),
            "franchisee_count": unresearched_field(),
        },
        "recent_signals": [],
        "pain_points": [],
        # 2026-10-03: added to give Hunter's enterprise_account_profile
        # playbook (required_modules: franchise_disclosure, financial_
        # operating_health) a real home -- previously these two modules'
        # findings had nowhere to land, so hunter_change_dispatch.py's
        # generic mutation_proposals loop always reported them unhandled
        # (RB live incident, 2026-10-03: Charleys' FDD/financial findings
        # stuck in review forever). Each is a single synthesized
        # field()-shaped leaf whose `value` is a structured dict (an FDD
        # snapshot / a financial snapshot is one cohesive disclosure, not
        # a list of independent string facts the way products/leadership
        # are) -- replaced wholesale on a newer-dated update, same
        # conflict discipline as every other scalar field in this module.
        "franchise_disclosure": unresearched_field(),
        "financial_operating_health": unresearched_field(),
        "template_version": "brand-profile-v1",
        "created_at": now,
        "updated_at": now,
    }


def _backfill_missing_fields(profile: dict) -> dict:
    """Read-time backward-compat for real, pre-existing brand_profile.json
    files on disk that predate legal_entity/franchise_disclosure/
    financial_operating_health -- never KeyErrors a reader, never
    overwrites a field that already has content. Mutates and returns
    `profile` in place (callers already treat get_profile()'s return as
    the live object to pass to save_profile())."""
    identity = profile.setdefault("identity", {})
    identity.setdefault("legal_entity", unresearched_field())
    profile.setdefault("franchise_disclosure", unresearched_field())
    profile.setdefault("financial_operating_health", unresearched_field())
    return profile


def add_leadership_person(profile: dict, *, name: str, title: str, verified: bool,
                           dedupe: bool = True, **field_kwargs) -> bool:
    """Appends to profile["leadership"]["confirmed"] when verified (an
    independently_verified Hunter finding, or a human submission) or
    "reported_unverified" otherwise (vendor_stated/marketplace_reported) --
    the same confirmed-vs-reported split get_profile()'s own schema has
    carried since empty_profile() was first written, just never had a
    writer. dedupe=True (default) skips a name already present in EITHER
    bucket (a name promoted from reported_unverified to confirmed by a
    stronger later source should move buckets explicitly, not duplicate).
    Mutates `profile` in place; returns True if a new entry was actually
    added, False if skipped as a duplicate -- a caller gating its own
    "applied" count on mutation_policy's is_set_member auto-apply (always
    true for a set addition) needs this to avoid over-counting a dedupe
    as a real write."""
    entry = field(f"{name} -- {title}", **field_kwargs)
    entry["name"] = name
    entry["title"] = title
    leadership = profile.setdefault("leadership", {"confirmed": [], "reported_unverified": []})
    existing_names = {p.get("name") for p in leadership.get("confirmed", []) + leadership.get("reported_unverified", [])}
    if dedupe and name in existing_names:
        return False
    bucket = "confirmed" if verified else "reported_unverified"
    leadership.setdefault(bucket, []).append(entry)
    return True


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def profile_path(brand_id: str) -> Path:
    slug_safety.assert_safe_slug(brand_id, label="brand_id")
    return ROOT / f"{brand_id}.json"


def load_profile(brand_id: str) -> dict | None:
    p = profile_path(brand_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save_profile(brand_id: str, profile: dict) -> None:
    p = profile_path(brand_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    profile["updated_at"] = now_iso()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)


class NotFoundError(Exception):
    pass


def resolve_brand_entity(brand_id: str, graph: dict | None = None) -> dict:
    graph = graph if graph is not None else ei._read_graph()
    entity = ei._index_by_id(graph["entities"]).get(brand_id)
    if not entity or entity.get("entity_type") != "brand":
        raise NotFoundError(f"No brand entity '{brand_id}'")
    return entity


def get_profile(brand_id: str, *, persist: bool = False, graph: dict | None = None) -> dict:
    """Read-through: the persisted profile if one exists, else a fresh
    honest-blank skeleton. Trajectory is always recomputed live from the
    graph on every call (never trusted from a stored value, so it can
    never silently go stale); footprint fields are refreshed from Technomic
    data the same way, except where a human has already reviewed them.

    persist=False (default) never writes -- callers building a read-only
    view (e.g. Team Portal routes) shouldn't create a file on disk just
    because someone looked. Pass persist=True for backfill/refresh jobs
    that explicitly want the computed state saved.

    graph: pass an already-loaded graph dict when calling this in a loop
    (e.g. an xlsx export iterating all ~1,663 brands) -- without it, every
    call re-reads and re-parses ecosystem_intelligence.json from disk via
    resolve_brand_entity()'s own default, which is fine for a single
    Team Portal request but wasteful at bulk-export scale.
    """
    entity = resolve_brand_entity(brand_id, graph)
    profile = load_profile(brand_id) or empty_profile(brand_id, entity.get("name") or brand_id)
    profile["trajectory"] = compute_trajectory(entity)
    _refresh_footprint(profile, entity)
    _backfill_missing_fields(profile)
    if persist:
        save_profile(brand_id, profile)
    return profile


# ---------------------------------------------------------------------------
# Internal vs. shareable (Phase C) — allowlist, not a blocklist, matching
# team_tech_stack.py's own established precedent (_TECH_STACK_RELATIONSHIP_
# FIELDS' docstring: "a blocklist here missed real sensitive content
# elsewhere in the document" — the codebase moved to allowlist-by-design
# after a real incident, not as a style preference).
# ---------------------------------------------------------------------------

# Top-level profile keys safe to hand to a teammate or an outside party.
# Excludes nothing today at the top level (identity/synopsis/leadership/
# footprint/trajectory/recent_signals are all facts, not Todd's private
# judgment) -- the real redaction happens one level down, on leadership,
# where an UNVERIFIED claim about a person is treated as more sensitive
# than a confirmed one.
_SHAREABLE_TOP_LEVEL_KEYS = (
    "brand_id", "brand_name", "identity", "synopsis", "footprint",
    "trajectory", "recent_signals", "pain_points", "template_version",
)


def _is_entry_shareable(entry: dict) -> bool:
    # Missing key = pre-2026-09-29 entries written before visibility
    # existed -- defaults to shareable (their whole field was already
    # allowlisted, so this preserves existing behavior for old data).
    return entry.get("visibility", "team_shareable") != "private"


def shareable_view(profile: dict) -> dict:
    """Strips a full profile down to what's safe to show outside Todd's
    own working view. leadership.reported_unverified is excluded entirely
    (an unconfirmed claim about a real person is the one place this schema
    currently carries something riskier to share than a plain sourced
    fact) -- leadership.confirmed passes through unchanged.

    pain_points/recent_signals are filtered per-entry by visibility, not
    just allowlisted as whole fields -- real 2026-09-29 finding: a single
    entry added through the Team Portal's own intended write path
    (add_brand_pain_point) can carry content that shouldn't be broadly
    shareable, with the field-level allowlist alone giving no way to
    exclude just that one entry."""
    view = {k: profile[k] for k in _SHAREABLE_TOP_LEVEL_KEYS if k in profile}
    for list_key in ("pain_points", "recent_signals"):
        if list_key in view:
            view[list_key] = [e for e in view[list_key] if _is_entry_shareable(e)]
    leadership = profile.get("leadership") or {}
    view["leadership"] = {"confirmed": leadership.get("confirmed", [])}
    return view


def set_pain_point_visibility(brand_id: str, value: str, visibility: str, *, graph: dict | None = None) -> dict:
    """Governed op to flip one existing pain_points entry's visibility in
    place (matched by exact value text) -- never a raw JSON edit. Raises
    ValueError if no entry with that exact value exists. graph: same
    testability/loop-reuse escape hatch as get_profile()'s own param."""
    if visibility not in ("team_shareable", "private"):
        raise ValueError(f"visibility must be 'team_shareable' or 'private', got {visibility!r}")
    profile = get_profile(brand_id, persist=False, graph=graph)
    points = profile.get("pain_points") or []
    matched = [p for p in points if p.get("value") == value]
    if not matched:
        raise ValueError(f"No pain_points entry with value {value!r} on brand '{brand_id}'")
    for p in matched:
        p["visibility"] = visibility
    save_profile(brand_id, profile)
    return {"brand_id": brand_id, "pain_points": profile["pain_points"]}
