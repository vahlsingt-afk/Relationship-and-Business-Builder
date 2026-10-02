#!/usr/bin/env python3
"""
vendor_profile_common.py — Company Profile for vendors (2026-09-28).

Sibling to brand_profile_common.py, not a generalization of it: a vendor's
"footprint" and "trajectory" don't mean the same thing as a brand's
(Technomic unit counts / franchise-vs-company-owned units don't apply to a
vendor), so this reuses brand_profile_common's entity-agnostic provenance
primitives (field(), unresearched_field(), signal_field()) rather than
forking every brand-specific computation inside that module with
entity_type branches -- same "separately-governed schemas, deliberate
duplication over shared mutable state" reasoning documented in
competitor_intelligence_common.py's own docstring.

Real gap this closes: the vendor page had no Company Profile card at all
(Todd, 2026-09-28: "We should have a company profile first - just like the
brand page"), while brand pages have had one since Phase A. Two fields are
live-computable today with no research pipeline needed:
  - identity.parent_ownership: from the vendor entity's own owner_name/
    owner_entity_id (ecosystem_intelligence.set_entity_owner) when set --
    e.g. Punchh -> "PAR Technology".
  - footprint.brand_count: competitive_landscape.vendor_brand_count(),
    real, live, across every tech-stack category.
synopsis borrows competitor_intelligence's positioning_summary when one
exists (real content already gathered for ~150 competitors) rather than
starting blank -- a genuine bridge, not new research. Everything else
(hq_city_state, founded_year, leadership) starts honestly blank until the
vendor-research pipeline (see export_research_gaps.py's vendor-side
follow-on) populates it, same discipline as brand_profile_common.py.

Storage: system/vendor_profiles/<vendor_id>.json -- deliberately a
separate tree from system/brand_profiles/, mirroring how competitor_
intelligence/ and customers_prospects/ are already separate trees for the
competitor-side vs. brand-side of this same distinction.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import ecosystem_intelligence as ei  # noqa: E402
import competitive_landscape as cland  # noqa: E402
import competitor_intelligence as compintel  # noqa: E402
import slug_safety  # noqa: E402
import brand_profile_common as bpc  # noqa: E402

SYSTEM_DIR = SCRIPTS_DIR.parent
ROOT = SYSTEM_DIR / "vendor_profiles"

field = bpc.field
unresearched_field = bpc.unresearched_field
signal_field = bpc.signal_field
add_signal = bpc.add_signal
is_human_reviewed = bpc.is_human_reviewed
today = bpc.today
now_iso = bpc.now_iso


class NotFoundError(Exception):
    pass


def resolve_vendor_entity(vendor_id: str, graph: dict | None = None) -> dict:
    graph = graph if graph is not None else ei._read_graph()
    entity = ei._index_by_id(graph["entities"]).get(vendor_id)
    if not entity or entity.get("entity_type") != "vendor":
        raise NotFoundError(f"No vendor entity '{vendor_id}'")
    return entity


def empty_profile(vendor_id: str, vendor_name: str) -> dict:
    now = now_iso()
    return {
        "vendor_id": vendor_id,
        "vendor_name": vendor_name,
        "identity": {
            "parent_ownership": unresearched_field(scope="vendor"),
            "hq_city_state": unresearched_field(scope="vendor"),
            "founded_year": unresearched_field(scope="vendor"),
        },
        "synopsis": unresearched_field(scope="vendor"),
        "leadership": {"confirmed": [], "reported_unverified": []},
        "footprint": {
            "brand_count": unresearched_field(scope="vendor"),
        },
        "recent_signals": [],
        "template_version": "vendor-profile-v1",
        "created_at": now,
        "updated_at": now,
    }


def profile_path(vendor_id: str) -> Path:
    slug_safety.assert_safe_slug(vendor_id, label="vendor_id")
    return ROOT / f"{vendor_id}.json"


def load_profile(vendor_id: str) -> dict | None:
    p = profile_path(vendor_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save_profile(vendor_id: str, profile: dict) -> None:
    p = profile_path(vendor_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    profile["updated_at"] = now_iso()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _compute_product_lines(graph: dict, vendor_id: str) -> list[dict]:
    """Real 2026-09-30 request (Todd): "we need to rationalize both
    products under each company and the AKAs that have come from
    acquisitions" -- e.g. PAR Technology's POS (Brink), back-office (PAR
    Ops, née Data Central), loyalty (PAR Loyalty, née Punchh), payment
    gateway (PAR Pay), payment processing (PAR Payments), online ordering
    (PAR Ordering, née MENU), drive-thru headset and drive-thru timer
    lines are all real, separately-branded products of one company, not
    eight unrelated vendors.

    Live-computed, not stored: every OTHER vendor entity in the graph
    whose owner_entity_id points at this one (the exact same owner_name/
    owner_entity_id link set_entity_owner() already uses for the reverse
    "owned by" field on the subsidiary's own profile -- e.g. PAR Punchh's
    parent_ownership already resolves to "PAR Technology" via this same
    link). Reusing that existing mechanism rather than inventing a
    parallel product-catalog field means every vendor-scoped feature
    (Company Profile, Partner Finder, tech-stack relationships) already
    understands a new product-line entity the moment it's added to the
    graph, with zero further code changes."""
    def _line_for(e: dict) -> dict:
        attrs = e.get("attributes") or {}
        category = attrs.get("primary_category")
        return {
            "vendor_id": e.get("id"),
            "product_name": e.get("name"),
            "category": category,
            "category_label": (category or "").replace("_", " ").title() or None,
            "aliases": e.get("aliases") or [],
            "is_parent": e.get("id") == vendor_id,
            # Real 2026-09-30 request (Todd): "older POS systems have a
            # higher signal for replacement" -- a property of the PRODUCT
            # LINE itself (legacy/current/next_gen, and the resulting
            # replacement-likelihood read), not of any one customer's
            # install. None when not set (most product lines don't have
            # this scored yet).
            "product_generation": attrs.get("product_generation"),
            "replacement_signal": attrs.get("replacement_signal"),
        }

    subsidiary_lines = []
    for e in graph.get("entities") or []:
        if e.get("entity_type") != "vendor" or e.get("id") == vendor_id:
            continue
        if e.get("owner_entity_id") != vendor_id:
            continue
        subsidiary_lines.append(_line_for(e))
    if not subsidiary_lines:
        # Nothing to rationalize -- the vendor's own identity/aliases
        # already show on the profile header, so a redundant one-row
        # table here would just be noise for the ~120 vendors with no
        # tracked subsidiary product lines yet.
        return []

    # The parent's own primary line (e.g. PAR Technology's own POS/Brink
    # product) belongs in the same table as its subsidiaries' lines --
    # Todd's own framing was one flat list ("PAR Tech has: POS software,
    # POS hardware, back office, loyalty, ..."), not "the company" versus
    # "its subsidiaries."
    by_id = ei._index_by_id(graph.get("entities") or [])
    parent = by_id.get(vendor_id)
    lines = [_line_for(parent)] if parent else []
    lines.extend(subsidiary_lines)
    lines.sort(key=lambda pl: (not pl["is_parent"], pl["product_name"] or ""))
    return lines


def _compute_category_rollups(graph: dict, lines: list[dict]) -> list[dict]:
    """Real 2026-09-30 request (Todd): the individual product lines
    (Aloha POS / Aloha Next / Aloha Cloud / NCR Silver, say) "should all
    roll up to NCR POS totals but should be kept intact underneath it" --
    i.e. show one combined total for a category alongside the
    individually-named lines, not instead of them (_compute_product_lines
    above already keeps every line intact; this adds the total).

    Unions real brand IDs (competitive_landscape.vendor_brand_ids) across
    every line sharing a category, rather than summing each line's own
    count, so a brand that happens to show up under two lines is never
    double-counted. Only returned for a category with 2+ lines -- a
    single-line category has nothing to roll up.

    Caveat, real and worth stating plainly: today essentially every
    historical uses_vendor_for_category relationship for a family like
    NCR's is still tagged to the parent vendor entity (vendor-ncr), not
    to the specific line a brand actually runs (vendor-ncr-aloha-pos vs.
    -aloha-next vs. ...) -- re-tagging those relationships to the correct
    specific line needs real per-brand evidence this module doesn't have,
    so most of the "rolled up" total below is really just the parent
    line's own count until that evidence exists."""
    from collections import defaultdict
    by_category: dict[str, list[dict]] = defaultdict(list)
    for line in lines:
        if line.get("category"):
            by_category[line["category"]].append(line)

    rollups = []
    for category, group in by_category.items():
        if len(group) < 2:
            continue
        brand_ids: set[str] = set()
        for line in group:
            brand_ids |= cland.vendor_brand_ids(graph, line["vendor_id"])
        rollups.append({
            "category": category,
            "category_label": group[0]["category_label"],
            "total_brand_count": len(brand_ids),
            "product_ids": [line["vendor_id"] for line in group],
            "product_names": [line["product_name"] for line in group],
        })
    rollups.sort(key=lambda r: r["category_label"] or "")
    return rollups


def _refresh_parent_ownership(profile: dict, entity: dict) -> None:
    existing = profile["identity"].get("parent_ownership")
    if existing is not None and is_human_reviewed(existing):
        return
    owner_name = entity.get("owner_name")
    if not owner_name:
        return
    profile["identity"]["parent_ownership"] = field(
        owner_name, status="confirmed", confidence="high", as_of=today(),
        scope="vendor", last_reviewed_by="system:vendor_profile_common",
    )


def _refresh_brand_count(profile: dict, entity: dict, graph: dict) -> None:
    existing = profile["footprint"].get("brand_count")
    if existing is not None and is_human_reviewed(existing):
        return
    count = cland.vendor_brand_count(graph, entity["id"])
    profile["footprint"]["brand_count"] = field(
        count, status="confirmed", confidence="high", as_of=today(),
        scope="vendor", last_reviewed_by="system:vendor_profile_common",
    )


def _refresh_synopsis_from_positioning(profile: dict, entity: dict) -> None:
    """Borrows competitor_intelligence.positioning_summary as a starting
    synopsis when the profile has none of its own yet -- real, already-
    gathered content (see module docstring), never overwrites a human-
    reviewed synopsis."""
    existing = profile.get("synopsis")
    if existing is not None and is_human_reviewed(existing):
        return
    if existing is not None and existing.get("status") == "confirmed":
        return
    try:
        slug, _created = compintel.ensure_competitor(entity["name"])
        comp = compintel.cic.load_competitor(slug)["competitor"]
    except Exception:  # noqa: BLE001 -- no competitor_intelligence record yet is a valid state
        return
    positioning = comp.get("positioning_summary")
    if not positioning:
        return
    profile["synopsis"] = field(
        positioning, status="reported", confidence="medium", as_of=today(),
        scope="vendor", last_reviewed_by="system:competitor_intelligence_positioning",
    )


def get_profile(vendor_id: str, *, persist: bool = False, graph: dict | None = None) -> dict:
    graph = graph if graph is not None else ei._read_graph()
    entity = resolve_vendor_entity(vendor_id, graph)
    profile = load_profile(vendor_id) or empty_profile(vendor_id, entity.get("name") or vendor_id)
    _refresh_parent_ownership(profile, entity)
    _refresh_brand_count(profile, entity, graph)
    _refresh_synopsis_from_positioning(profile, entity)
    if persist:
        save_profile(vendor_id, profile)
    # Added after the persist branch, deliberately -- always live-
    # recomputed from the graph's own owner_entity_id links, never
    # written into the stored profile file, so it can never go stale.
    # See _compute_product_lines.
    profile["product_lines"] = _compute_product_lines(graph, entity.get("id") or vendor_id)
    profile["product_line_rollups"] = _compute_category_rollups(graph, profile["product_lines"])
    return profile


_SHAREABLE_TOP_LEVEL_KEYS = (
    "vendor_id", "vendor_name", "identity", "synopsis", "footprint",
    "recent_signals", "template_version", "product_lines", "product_line_rollups",
)


def shareable_view(profile: dict) -> dict:
    """Same allowlist discipline as brand_profile_common.shareable_view --
    leadership.reported_unverified excluded, everything else here is a
    fact, not editorial judgment."""
    view = {k: profile[k] for k in _SHAREABLE_TOP_LEVEL_KEYS if k in profile}
    leadership = profile.get("leadership") or {}
    view["leadership"] = {"confirmed": leadership.get("confirmed", [])}
    return view
