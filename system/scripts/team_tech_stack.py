#!/usr/bin/env python3
"""team_tech_stack.py — read/write logic for the Team Portal.

The Team Portal (system/api/team_portal_api.py) is the first multi-user-
facing surface in RBB: a small team (~2-5 people, each with their own
credential — see team_portal_admin.py) can look up an existing brand or
vendor already tracked in system/ecosystem_intelligence.json, see/edit the
tech-stack (vendor relationships) reported for a brand, and generate an
Account Background Brief / Competitor Profile for it.

This module holds the actual logic, independent of FastAPI, so it's
directly unit-testable. Three hard constraints run through everything
here, all decided explicitly by Todd on 2026-08-29:

1. Existing entities only. No function here ever creates a new brand or
   vendor entity in ecosystem_intelligence.json — every read/write is
   validated against entities that already exist. (Generating a
   Competitor Profile does cause competitor_intelligence.py to create a
   *profile-store* shell record for that vendor if one doesn't exist yet
   — a separate, lower-stakes store from ecosystem_intelligence.json's
   entities, and an expected side effect of "generate a profile" rather
   than new-entity creation in the graph this module governs.)

2. Editorial content stays out of team-facing responses — enforced with
   an ALLOWLIST, not a blocklist. For tech-stack relationships that's a
   hand-picked field whitelist (never a passthrough of the raw JSON). For
   the Background Brief it's stronger than that: a first version tried
   redacting just the one section that verbatim-includes Todd's own
   account_intelligence/*.md notes, and live testing on 2026-08-29 caught
   real sensitive content (his sales strategy, named colleagues,
   competitive tactics) surfacing through OTHER sections of that same
   document that a blocklist never anticipated — Executive Summary,
   Bottom Line, Recommended Approach, Current Business Situation, and any
   other free-text field. Per Todd's explicit decision after seeing that,
   the team-facing Background Brief is now assembled from only four
   allowlisted structural sections (Brand Profile facts, Technology
   Environment, Leadership, Related Artifacts) via account_background_
   brief.py's extracted per-section renderers — it never touches the full
   render_background_brief()/generate_brief() pipeline at all, so a new
   free-text field added there in the future can't silently leak into the
   team's view the way "Bottom Line" did. The Competitor Profile is
   lower-stakes (Todd confirmed "Positioning"/"Gap analysis vs. Genius"
   read as sales enablement, not internal-only strategy) and keeps the
   blocklist redaction of just "Todd's POV". The underlying generated
   files on disk (system/account_research/, system/competitor_intelligence/)
   are never altered by any of this — only what this module hands back to
   the Team Portal is affected.

3. Every mutation goes through ecosystem_intelligence.py's governed write
   path (resolve_and_upsert_relationship + _write_graph — snapshot +
   jsonschema validation), never a raw read/write of the graph file.
   Several other writers in this codebase bypass that path; this module
   deliberately does not.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import rb_core as core  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import audit_log  # noqa: E402
import account_background_brief as abb  # noqa: E402
import competitor_intelligence as compintel  # noqa: E402


class NotFoundError(Exception):
    """An id/name the caller passed doesn't resolve to an existing entity."""


class ValidationError(Exception):
    """The submitted tech-stack entry is missing a required field."""


# ---------------------------------------------------------------------------
# Field whitelists — the enforcement point for constraint (2) above.
# ---------------------------------------------------------------------------

# Deliberately excludes: risk, strategic_note (Todd's 2026-08-29 decision —
# both sit between raw fact and editorial judgment; excluded as the safer
# default). Everything else here is deployment/provenance fact, not opinion.
_TECH_STACK_RELATIONSHIP_FIELDS = (
    "relationship_id", "vendor", "vendor_id", "category", "vendor_role", "product",
    "status", "deployment_stage", "deployment_scope", "geography", "channel",
    "service_role", "deployment_claim_type", "customer_operator", "scope_unit_count",
    "deployed_units", "penetration_pct", "estimated_franchise_groups",
    "geographic_concentration", "corporate_vs_franchise", "relationship_classification",
    "evidence_posture", "confidence", "sources", "last_modified_by",
)

_CANONICAL_TECH_CATEGORY_MAP = {
    "pos": "pos",
    "back office": "back_office",
    "payments": "payments",
    "pos hardware": "pos_hardware",
    "drive-thru timers": "drive_thru_timers",
    "digital menu boards": "digital_menu_boards",
    "loyalty": "loyalty",
    "online ordering": "online_ordering",
    "kds": "kds_kitchen_ops",
    "labor / workforce": "labor_workforce",
    "kiosks": "kiosks",
    "menu management": "menu_management",
    "ops execution": "ops_execution",
    "broadband / network": "broadband_network",
}


def _canonical_technology(entity: dict) -> dict[str, dict]:
    profile = ((entity.get("attributes") or {}).get("deep_research_profile") or {})
    return profile.get("canonical_technology") or {}


def _canonical_category(label: str) -> str:
    normalized = " ".join(str(label or "").strip().lower().split())
    return _CANONICAL_TECH_CATEGORY_MAP.get(normalized, normalized.replace(" ", "_"))


def _vendor_named_in_finding(vendor_name: str, finding: str) -> bool:
    """Conservative identity match for a canonical technology finding.

    The finding is authoritative by category, but may name products after an
    em dash or parentheses. Match complete normalized vendor names only; never
    infer a vendor from its product category.
    """
    vendor = re.sub(r"[^a-z0-9]+", " ", (vendor_name or "").lower()).strip()
    text = re.sub(r"[^a-z0-9]+", " ", (finding or "").lower()).strip()
    return bool(vendor and re.search(rf"(?:^| )({re.escape(vendor)})(?: |$)", text))


def _reconcile_tech_stack_rows(entity: dict, graph: dict, relationship_rows: list[dict]) -> tuple[list[dict], dict]:
    """Project one trustworthy roster from canonical facts plus graph history.

    Canonical research wins for every category it covers. Matching graph rows
    are retained as the linked vendor record; conflicting rows are suppressed
    from the current roster but counted in the reconciliation receipt. Graph
    rows in categories the canonical facts do not cover remain visible with an
    explicit supplemental/unreconciled posture. Nothing historical is deleted.
    """
    canonical = _canonical_technology(entity)
    if not canonical:
        rows = [dict(row, roster_posture="graph_only_unreconciled") for row in relationship_rows]
        return rows, {"policy": "graph_only_no_canonical_facts", "suppressed": 0, "canonical_categories": 0}

    current: list[dict] = []
    suppressed: list[dict] = []
    used_relationship_ids: set[str] = set()
    graph_entities = [e for e in graph.get("entities") or [] if e.get("entity_type") == "vendor"]

    for label, leaf in canonical.items():
        if not isinstance(leaf, dict) or not leaf.get("value"):
            continue
        category = _canonical_category(label)
        finding = str(leaf["value"])
        category_rows = [row for row in relationship_rows if row.get("category") == category]
        matches = [row for row in category_rows if _vendor_named_in_finding(row.get("vendor") or "", finding)]
        for row in category_rows:
            if row not in matches:
                suppressed.append({
                    "relationship_id": row.get("relationship_id"), "vendor": row.get("vendor"),
                    "category": category, "reason": "superseded_by_canonical_account_fact",
                    "canonical_value": finding,
                })
        if matches:
            for row in matches:
                used_relationship_ids.add(row.get("relationship_id"))
                current.append(dict(
                    row, confidence=leaf.get("confidence", row.get("confidence")),
                    roster_posture="canonical_current", canonical_value=finding,
                ))
            continue

        # The fact is current even when the older graph never had a matching
        # relationship. Resolve a known vendor only by its actual name; otherwise
        # keep the sourced fact as an unlinked/internal technology row.
        named_vendors = sorted(
            (v for v in graph_entities if _vendor_named_in_finding(v.get("name") or "", finding)),
            key=lambda v: len(v.get("name") or ""), reverse=True,
        )
        if named_vendors:
            for vendor in named_vendors:
                current.append({
                    "relationship_id": None, "vendor": vendor.get("name"), "vendor_id": vendor.get("id"),
                    "category": category, "product": finding, "status": "active",
                    "confidence": leaf.get("confidence"), "roster_posture": "canonical_current",
                    "canonical_value": finding, "sources": [leaf.get("source_url")] if leaf.get("source_url") else [],
                })
        else:
            current.append({
                "relationship_id": None, "vendor": finding, "vendor_id": None,
                "category": category, "product": finding, "status": "active",
                "confidence": leaf.get("confidence"), "roster_posture": "canonical_current_unlinked",
                "canonical_value": finding, "sources": [leaf.get("source_url")] if leaf.get("source_url") else [],
            })

    canonical_categories = {
        _canonical_category(label) for label, leaf in canonical.items()
        if isinstance(leaf, dict) and leaf.get("value")
    }
    for row in relationship_rows:
        if row.get("relationship_id") in used_relationship_ids or row.get("category") in canonical_categories:
            continue
        current.append(dict(row, roster_posture="supplemental_unreconciled"))

    return current, {
        "policy": "canonical_account_facts_win_by_category",
        "canonical_categories": len(canonical_categories),
        "suppressed": len(suppressed),
        "suppressed_relationships": suppressed,
    }

_COMPETITOR_POV_SECTION_HEADING = "## Todd's POV"


def _redact_markdown_section(markdown: str, heading: str) -> str:
    """Remove one '## <heading>' section — from its heading line up to
    (not including) the next top-level '## ' heading, or end of document —
    from already-rendered markdown. Used only for the Competitor Profile's
    "Todd's POV" section now (see module docstring for why the Background
    Brief switched from this blocklist approach to an allowlist instead —
    a blocklist here missed real sensitive content elsewhere in the
    document). competitor_intelligence.py joins its sections with
    "\\n".join(lines), so every real heading is preceded by at least one
    newline regardless of whether the individual list item that produced
    it started with one; searching for "\\n" + heading is the reliable
    anchor either way."""
    marker = f"\n{heading}"
    idx = markdown.find(marker)
    if idx == -1:
        return markdown
    rest = markdown[idx + len(marker):]
    next_idx = rest.find("\n## ")
    if next_idx == -1:
        return markdown[:idx].rstrip() + "\n"
    return markdown[:idx].rstrip() + "\n" + rest[next_idx:].lstrip("\n")


# ---------------------------------------------------------------------------
# Search / read
# ---------------------------------------------------------------------------

def _brand_summary(entity: dict) -> dict:
    attrs = entity.get("attributes") or {}
    return {
        "id": entity["id"],
        "name": entity["name"],
        "segment": attrs.get("segment"),
        "subsegment": attrs.get("subsegment"),
        "rank": attrs.get("rank"),
        "unit_count": attrs.get("unit_count"),
    }


def _vendor_summary(entity: dict) -> dict:
    attrs = entity.get("attributes") or {}
    return {
        "id": entity["id"],
        "name": entity["name"],
        "primary_category": attrs.get("primary_category"),
    }


def search_brands(query: str, *, limit: int = 25) -> list[dict]:
    graph = ei._read_graph()
    q = (query or "").strip().lower()
    results = [
        _brand_summary(e) for e in graph.get("entities", [])
        if e.get("entity_type") == "brand" and (not q or q in e.get("name", "").lower())
    ]
    results.sort(key=lambda r: (r.get("rank") is None, r.get("rank") or 0))
    return results[:limit]


def search_vendors(query: str, *, limit: int = 25, competes_on_category: str | None = None) -> list[dict]:
    """`competes_on_category` (2026-09-28, one of cic.GENIUS_PRODUCT_LINES,
    optional): Todd's direction for Value Wedge -- "we should always go
    1-1 product... a drop down list of POS competitors" once a Genius
    product is picked, not every tracked vendor. Restricts results to
    vendors whose competitor_intelligence record has declared that
    category in competes_on (via setCompetitorProductLines) -- a real,
    reviewed fact, never guessed from the vendor's tech-stack category
    (a different vocabulary, see competitive_landscape.TECH_STACK_
    CATEGORIES vs. GENIUS_PRODUCT_LINES)."""
    graph = ei._read_graph()
    q = (query or "").strip().lower()
    allowed_vendor_ids: set[str] | None = None
    if competes_on_category:
        allowed_vendor_ids = set()
        for entry in cic.load_registry().get("registry", []):
            slug = entry.get("competitor_slug")
            if not slug:
                continue
            try:
                comp = cic.load_competitor(slug)["competitor"]
            except FileNotFoundError:
                continue
            vendor_entity_id = comp.get("vendor_entity_id")
            if vendor_entity_id and competes_on_category in (comp.get("competes_on") or []):
                allowed_vendor_ids.add(vendor_entity_id)
    results = [
        _vendor_summary(e) for e in graph.get("entities", [])
        if e.get("entity_type") == "vendor"
        and (not q or q in e.get("name", "").lower())
        and (allowed_vendor_ids is None or e["id"] in allowed_vendor_ids)
    ]
    results.sort(key=lambda r: (r.get("name") or "").lower())
    return results[:limit]


# ---------------------------------------------------------------------------
# Partner Finder (2026-09-28) -- capability/problem search across the
# tracked vendors, not just name matching (search_vendors above only
# matches the vendor's own name). Todd's scoped-down first release: no new
# partner_status/gaps_filled data model -- those are curated fields for a
# later release once the team's validated how they actually search. Every
# field surfaced here is derived from what's already on the vendor entity
# and its brand-vendor relationship edges (categories, vendor_role,
# service_role, product, confidence). partner_status is always "unknown"
# because no formally-reviewed approved/integrated/referral status is
# tracked yet -- a vendor showing up in a search result here means
# "tracked in the ecosystem graph," never an implied approved relationship
# (see module docstring's constraint 2: never overstate what's on file;
# the vendor graph includes competitors, incumbents, and ecosystem-only
# vendors right alongside real partners, and this search must not blur
# that distinction).
# ---------------------------------------------------------------------------

# Plain-English phrase -> canonical TECH_STACK_CATEGORIES slug. Substring-
# matched against the query (phrase in query), not the reverse -- this is
# what lets a problem statement like "we need better inventory visibility"
# or "labor scheduling" resolve to a category even though the category's
# own display name ("Inventory", "Labor Workforce") doesn't literally
# appear in the query. Direct category/display-name substring matches
# (e.g. "digital menu boards", "voice ai") work without an entry here at
# all -- see _partner_query_categories.
_PARTNER_QUERY_SYNONYMS: dict[str, str] = {
    "scheduling": "labor_workforce", "labor": "labor_workforce", "workforce": "labor_workforce",
    "staffing": "labor_workforce", "shift": "labor_workforce",
    "menu board": "digital_menu_boards", "digital menu": "digital_menu_boards",
    "franchise accounting": "back_office_accounting", "accounting": "back_office_accounting",
    "bookkeeping": "back_office_accounting",
    "voice assistant": "voice_ai",
    "inventory": "inventory", "food cost": "inventory", "stock count": "inventory",
    "kitchen display": "kds_kitchen_ops", "kds": "kds_kitchen_ops",
    "drive thru": "drive_thru_ai", "drive-thru": "drive_thru_ai",
    "kiosk": "kiosks", "self order": "kiosks", "self-order": "kiosks",
    "loyalty": "loyalty", "rewards": "loyalty", "crm": "loyalty",
    "online order": "online_ordering", "digital order": "online_ordering", "mobile order": "online_ordering",
    "payment": "payments", "gift card": "payments",
    "point of sale": "pos",
    "training": "training_lms", "lms": "training_lms", "onboarding": "training_lms",
    "business intelligence": "bi_analytics", "analytics": "bi_analytics", "reporting": "bi_analytics",
    "computer vision": "computer_vision_robotics", "robotics": "computer_vision_robotics",
    "franchise management": "franchise_management",
    "networking": "networking_wifi", "wifi": "networking_wifi",
    "unified commerce": "unified_commerce",
    "hardware": "pos_hardware",
}

_CONFIDENCE_ORDER = {"critical": 3, "high": 2, "medium": 1, "low": 0}


def _partner_query_categories(q: str, known_categories: set[str]) -> set[str]:
    hits = {cat for phrase, cat in _PARTNER_QUERY_SYNONYMS.items() if phrase in q}
    normed = ei._norm_category(q)
    if normed and normed in known_categories:
        hits.add(normed)
    return hits


def _vendor_search_profile(entity: dict, relationships: list[dict]) -> dict:
    """Derives everything Partner Finder searches/displays for one vendor
    from its entity attributes plus its uses_vendor_for_category
    relationship edges (there is no capabilities/products/gaps_filled field
    on the vendor entity itself yet -- see module header)."""
    attrs = entity.get("attributes") or {}
    categories = {r["category"] for r in relationships if r.get("category")}
    if attrs.get("primary_category"):
        categories.add(attrs["primary_category"])
    vendor_roles = sorted({r["vendor_role"] for r in relationships if r.get("vendor_role")})
    service_roles = sorted({r["service_role"] for r in relationships if r.get("service_role")})
    products = sorted({r["product"] for r in relationships if r.get("product")})
    active = [r for r in relationships if r.get("status") == "active"]
    brand_ids = {r["from_entity_id"] for r in (active or relationships) if r.get("from_entity_id")}
    best_confidence = None
    for r in relationships:
        level = (r.get("confidence") or {}).get("level")
        if level and _CONFIDENCE_ORDER.get(level, -1) > _CONFIDENCE_ORDER.get(best_confidence, -1):
            best_confidence = level
    return {
        "id": entity["id"],
        "name": entity["name"],
        "aliases": entity.get("aliases") or [],
        "primary_category": attrs.get("primary_category"),
        "categories": sorted(categories),
        "vendor_roles": vendor_roles,
        "service_roles": service_roles,
        "products": products,
        "tracked_brand_count": len(brand_ids),
        "confidence": best_confidence,
    }


def search_partners(query: str, *, category: str | None = None, limit: int = 25) -> list[dict]:
    """Partner Finder: search vendors by name, alias, capability/category,
    product, vendor role, service role, or a plain-English problem
    statement -- not just the vendor's own name (search_vendors' current
    limitation, which this leaves untouched for its existing callers).
    Every result carries a human-readable match_reason and an explicit
    partner_status="unknown" (see module header). Returns [] for a blank
    query with no category filter -- same "don't dump the whole list"
    discipline as search_brands/search_vendors in the UI."""
    graph = ei._read_graph()
    q = (query or "").strip().lower()
    norm_category_filter = ei._norm_category(category) if category else None
    if not q and not norm_category_filter:
        return []

    vendor_entities = [e for e in graph.get("entities", []) if e.get("entity_type") == "vendor"]
    rels_by_vendor: dict[str, list[dict]] = {}
    for r in graph.get("relationships") or []:
        if r.get("relationship_type") != "uses_vendor_for_category":
            continue
        to_id = r.get("to_entity_id")
        if to_id:
            rels_by_vendor.setdefault(to_id, []).append(r)

    # TECH_STACK_CATEGORIES (the workbook-sync-derived list) is missing a
    # few real category values that do appear on vendor entities/relationships
    # (e.g. "voice_ai") -- union with what's actually in the graph so a
    # capability search/filter never misses a category just because it's
    # absent from that older, narrower list.
    known_categories = set(cland.TECH_STACK_CATEGORIES)
    for e in vendor_entities:
        pc = (e.get("attributes") or {}).get("primary_category")
        if pc:
            known_categories.add(pc)
    for rels in rels_by_vendor.values():
        for r in rels:
            if r.get("category"):
                known_categories.add(r["category"])
    target_categories = _partner_query_categories(q, known_categories) if q else set()

    scored: list[tuple[float, dict]] = []
    for entity in vendor_entities:
        profile = _vendor_search_profile(entity, rels_by_vendor.get(entity["id"], []))
        if norm_category_filter and norm_category_filter not in profile["categories"]:
            continue

        name_lower = profile["name"].lower()
        alias_lower = [a.lower() for a in profile["aliases"]]
        category_display = {c: cic.category_display_name(c) for c in profile["categories"]}
        corpus = " ".join([
            name_lower, *alias_lower, *(c.lower() for c in profile["categories"]),
            *(d.lower() for d in category_display.values()),
            *(r.replace("_", " ").lower() for r in profile["vendor_roles"]),
            *(r.lower() for r in profile["service_roles"] if r),
            *(p.lower() for p in profile["products"]),
        ])

        score = 0.0
        reasons: list[str] = []
        if not q:
            reasons.append(f"In category: {cic.category_display_name(norm_category_filter)}")
        else:
            if name_lower == q:
                score += 10
                reasons.append("Exact name match")
            elif q in name_lower:
                score += 6
                reasons.append("Name match")
            elif any(q in a for a in alias_lower):
                score += 5
                reasons.append("Alias match")
            matched_cats = target_categories & set(profile["categories"])
            if matched_cats:
                score += 4
                reasons.append("Matches capability: " + ", ".join(sorted(cic.category_display_name(c) for c in matched_cats)))
            elif q in corpus:
                score += 2
                reasons.append("Matches product, role, or category on file")
            if not reasons:
                continue

        score += min(profile["tracked_brand_count"], 20) * 0.1
        score += _CONFIDENCE_ORDER.get(profile["confidence"], -1) * 0.2

        scored.append((score, {
            "vendor_id": profile["id"],
            "name": profile["name"],
            "match_reason": "; ".join(reasons),
            "categories": [cic.category_display_name(c) for c in profile["categories"]],
            "products": profile["products"],
            "vendor_roles": profile["vendor_roles"],
            "partner_status": "unknown",
            "tracked_brand_count": profile["tracked_brand_count"],
            "confidence": profile["confidence"],
        }))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [row for _, row in scored[:limit]]


def get_partner_categories() -> list[dict]:
    """Category list for the Partner Finder filter dropdown: TECH_STACK_
    CATEGORIES (the tech-stack table/battle cards' vocabulary) unioned with
    whatever category values are actually present on vendor entities/
    relationships -- that list is missing a few real values in use (e.g.
    "voice_ai"), and the filter dropdown should never omit an option a
    vendor is actually tagged with."""
    graph = ei._read_graph()
    categories = set(cland.TECH_STACK_CATEGORIES)
    for e in graph.get("entities", []):
        if e.get("entity_type") != "vendor":
            continue
        pc = (e.get("attributes") or {}).get("primary_category")
        if pc:
            categories.add(pc)
    for r in graph.get("relationships") or []:
        if r.get("relationship_type") == "uses_vendor_for_category" and r.get("category"):
            categories.add(r["category"])
    return [
        {"value": c, "label": cic.category_display_name(c)}
        for c in sorted(categories, key=lambda c: cic.category_display_name(c))
    ]


def _vendor_competes_with_genius(vendor_name: str) -> bool:
    """Read-only check -- true only when this vendor already has a
    tracked competitor_intelligence record with at least one declared
    Genius product-line competition (competes_on). Deliberately never
    calls ensure_competitor()/ensure_competitor_by_slug() (which create a
    shell on first lookup) -- this runs on every tech-stack row render, so
    a shell-creating check here would silently seed a junk competitor
    record for every plain vendor/partner (e.g. a payments processor, or
    the brand's own first-party system) shown on a brand's page, never
    just for vendors someone actually chose to explore via Value Wedge."""
    if not vendor_name:
        return False
    slug, _vendor_entity_id, is_new = compintel.resolve_competitor(vendor_name)
    if is_new:
        return False
    try:
        competitor = cic.load_competitor(slug)["competitor"]
    except FileNotFoundError:
        return False
    return bool(competitor.get("competes_on"))


def get_brand_tech_stack(brand_id: str) -> dict:
    """Brand identity + its vendor relationships, whitelisted to exclude
    Todd's editorial layer. Raises NotFoundError if brand_id doesn't
    resolve to an existing brand entity.

    is_genius_competitor (2026-09-28, Todd: Del Taco's tech-stack table
    showed a "Value Wedge" button on every row, including its own
    first-party ordering system and plain payments/loyalty partners with
    no declared competition against Genius -- clicking it there is
    meaningless AND, via generate_value_wedge()'s ensure_competitor_by_
    slug() shell-creation, silently seeds a junk competitor record. The
    per-row button is now gated on this flag."""
    graph = ei._read_graph()
    by_id = ei._index_by_id(graph["entities"])
    entity = by_id.get(brand_id)
    if not entity or entity.get("entity_type") != "brand":
        raise NotFoundError(f"No brand entity '{brand_id}'")
    full_rels = ei.brand_vendor_relationships(graph, by_id, brand_id)
    whitelisted = []
    for rel in full_rels:
        row = {field: rel.get(field) for field in _TECH_STACK_RELATIONSHIP_FIELDS}
        row["is_genius_competitor"] = _vendor_competes_with_genius(rel.get("vendor"))
        whitelisted.append(row)
    reconciled, receipt = _reconcile_tech_stack_rows(entity, graph, whitelisted)
    for row in reconciled:
        row["is_genius_competitor"] = _vendor_competes_with_genius(row.get("vendor"))
    return {"brand": _brand_summary(entity), "vendor_relationships": reconciled, "reconciliation": receipt}


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def submit_tech_stack_entry(
    brand_id: str,
    member_id: str,
    *,
    vendor_id: str,
    category: str,
    relationship_id: str | None = None,
    product: str | None = None,
    vendor_role: str | None = None,
    deployment: dict | None = None,
    note: str | None = None,
) -> dict:
    """Add or update (relationship_id present -> update) a
    uses_vendor_for_category relationship on an EXISTING brand, for an
    EXISTING vendor. Never creates either entity. Tags the record with
    member_id (last_modified_by) and appends a full audit_log event —
    attribution-of-current-state lives on the graph record, the append-
    only edit history lives in audit_log.py, evidence provenance lives in
    the pre-existing sources[] pattern via a per-member registered source.
    """
    if not category or not category.strip():
        raise ValidationError("category is required")

    graph = ei._read_graph()
    by_id = ei._index_by_id(graph["entities"])

    brand = by_id.get(brand_id)
    if not brand or brand.get("entity_type") != "brand":
        raise NotFoundError(f"No brand entity '{brand_id}'")
    vendor = by_id.get(vendor_id)
    if not vendor or vendor.get("entity_type") != "vendor":
        raise NotFoundError(f"No vendor entity '{vendor_id}'")

    norm_category = ei._norm_category(category)

    source_id = f"src-team-{member_id}"
    ei._upsert_source(graph, {
        "id": source_id,
        "source_type": "team_submission",
        "title": f"Team portal submission by {member_id}",
        "captured_at": ei._today(),
    })

    if relationship_id:
        rels_by_id = ei._index_by_id(graph["relationships"])
        existing = rels_by_id.get(relationship_id)
        if not existing or existing.get("from_entity_id") != brand_id:
            raise NotFoundError(f"No relationship '{relationship_id}' on brand '{brand_id}'")
        rel = dict(existing)
    else:
        rel = {
            # RB-2026-09-27: was interpolating norm_category/vendor_role
            # (raw underscore-separated canonical forms, e.g.
            # "online_ordering", "system_of_record_pos") straight into the
            # id without slugging -- the schema's id pattern only allows
            # lowercase alnum + hyphens, so any category or vendor_role
            # with an underscore produced a schema-invalid relationship id.
            # Never caught because the validator subprocess this write path
            # calls was checking the wrong file (see ecosystem_intelligence.
            # py's _write_graph()); use ei._slug() like every other
            # relationship-id builder in the codebase (e.g.
            # ecosystem_intelligence.py's own vendor_role-based id at line
            # ~2091) to guarantee a valid id regardless of input shape.
            "id": f"rel-{brand_id}-{ei._slug(norm_category or '')}-{ei._slug(vendor_role or 'unknown')}-{vendor_id}",
            "from_entity_id": brand_id,
            "to_entity_id": vendor_id,
            "relationship_type": "uses_vendor_for_category",
            "status": "active",
            "sources": [],
            "confidence": ei._confidence("medium", "Reported via team portal."),
        }

    rel["category"] = norm_category
    rel["product"] = product
    if vendor_role:
        rel["vendor_role"] = vendor_role
    if deployment:
        rel["deployment"] = deployment
    if note:
        # Not stored as strategic_note (that field is Todd's editorial
        # layer and this is a team-submitted evidence note) — folded into
        # deployment.evidence, the same free-text field ingestion rows
        # already use for "how do we know this."
        rel.setdefault("deployment", {})["evidence"] = note
        rel["evidence_posture"] = rel.get("evidence_posture") or "provisional"
    if source_id not in (rel.get("sources") or []):
        rel.setdefault("sources", []).append(source_id)
    rel["last_modified_by"] = member_id

    result = ei.resolve_and_upsert_relationship(graph, rel, by_id)
    ei._write_graph(graph)

    action = "updated" if relationship_id else "added"
    audit_log.append_event(
        event_type="mutation_executed",
        item_summary=f"Team portal: {action} {norm_category} vendor ({vendor.get('name')}) for {brand.get('name')}",
        reason="team_tech_stack_submission",
        outcome="executed",
        data_class="intelligence",
        source="team_tech_stack_api",
        extra={
            "actor": member_id,
            "relationship_id": rel["id"],
            "brand_id": brand_id,
            "vendor_id": vendor_id,
        },
    )
    return {
        "relationship_id": rel["id"],
        "added": result["added"],
        "conflict": bool(result["conflict"].get("conflict")),
    }


# ---------------------------------------------------------------------------
# Background brief / competitor profile
# ---------------------------------------------------------------------------

def _resolve_brand_entity(brand_id: str) -> dict:
    graph = ei._read_graph()
    entity = ei._index_by_id(graph["entities"]).get(brand_id)
    if not entity or entity.get("entity_type") != "brand":
        raise NotFoundError(f"No brand entity '{brand_id}'")
    return entity


_DEEP_RESEARCH_LEAF_GROUPS = (
    ("scale_performance", "Scale & Performance"),
    ("public_business_contacts", "Public Business Contacts"),
    ("strategy", "Strategy"),
)

_DEEP_RESEARCH_LEDGER_FIELDS = (
    ("deep_pass_public_evidence", "Public Evidence Findings"),
    ("account_profile_evidence", "Additional Research Findings"),
    ("franchise_recruitment_evidence", "Franchise Recruitment Evidence"),
    ("evidence_ledger", "Research Findings"),
)

_FRANCHISE_DISCLOSURE_LABELS = {
    "wi_dfi_file": "WI DFI / state filing #", "restaurant_technology_fee": "Restaurant technology fee",
    "pos": "Disclosed POS", "pos_hardware_as_service": "POS hardware-as-a-service fee",
    "additional_costs": "Additional costs", "future_fee_right": "Future fee right reserved",
}


def _fmt_confidence(confidence) -> Optional[str]:
    if confidence is None:
        return None
    if isinstance(confidence, (int, float)):
        pct = confidence if confidence > 1 else confidence * 100
        return f"{pct:g}% confidence"
    return f"{confidence} confidence"


def _provenance_suffix(*, confidence=None, as_of=None, source_url=None) -> str:
    """RB-2026-09-27, Todd: 'confidence levels, data date...are important
    to these records.' One shared formatter so every deep-research fact
    line shows its provenance the same way, rather than some sections
    surfacing it and others silently dropping it -- storing this metadata
    on the attribute but never displaying it (the original version of
    this function) is exactly the kind of gap Todd's direction closes."""
    parts = [p for p in (_fmt_confidence(confidence), f"as of {as_of}" if as_of else None) if p]
    suffix = f" ({', '.join(parts)})" if parts else ""
    if source_url:
        suffix += f" [source]({source_url})"
    return suffix


def _leaf_provenance(leaf: dict) -> str:
    if not isinstance(leaf, dict):
        return ""
    source_url = leaf.get("source_url")
    if not source_url:
        sources = leaf.get("sources") or []
        first = sources[0] if sources else None
        source_url = first.get("url") if isinstance(first, dict) else first
    return _provenance_suffix(confidence=leaf.get("confidence"), as_of=leaf.get("as_of"), source_url=source_url)


def render_deep_research_intelligence_section(entity: dict) -> list[str]:
    """RB-2026-09-27: renders attributes.deep_research_profile -- the
    externally-sourced, publicly-cited research evidence ingested by the
    deep_research_dataset_ingest.py / multibrand_franchisee_operator_
    ingest.py / deep_account_intelligence_v56_ingest.py family of scripts.
    Every value here traces to a specific external source_url captured at
    ingest time, not Todd's own synthesis -- unlike Current Business
    Situation/Bottom Line/Executive Summary/etc. (proven live to leak his
    sales strategy and named colleagues, see the 2026-08-29 Team Portal
    closeout), this namespace is safe to expose to any teammate in full.

    Built because this data was landing in the graph but staying
    completely invisible in every brief: neither brand_profile_common.py's
    brand_profiles/ store (which the mechanical brief and Company Profile
    card read) nor a customers_prospects dossier (which the ~15-account
    brief reads) has ever touched this namespace -- Todd, 2026-09-27,
    after noticing the Team Portal wasn't reflecting today's ingested
    intelligence at all: 'still give the user the benefit of the
    intelligence gathered about a brand.'"""
    profile = (entity.get("attributes") or {}).get("deep_research_profile") or {}
    if not profile:
        return []
    lines: list[str] = []

    # Leadership: dataset-#3's roster (richer -- function/status/
    # verification_date) takes precedence when present; per-role leaves
    # (dataset #1's shape, e.g. leadership.ceo) are the fallback.
    roster = profile.get("current_leadership") or []
    role_leadership = profile.get("leadership") or {}
    if roster:
        lines.append("\n## Leadership (Research Findings)")
        for person in roster:
            if not isinstance(person, dict) or not person.get("name"):
                continue
            title = f" — {person['title']}" if person.get("title") else ""
            prov = _provenance_suffix(
                confidence=person.get("confidence"),
                as_of=person.get("verification_date"), source_url=person.get("source_url"),
            )
            lines.append(f"- {person['name']}{title}{prov}")
    elif role_leadership:
        role_lines = [
            f"- {role.replace('_', ' ').title()}: {leaf['value']}{_leaf_provenance(leaf)}"
            for role, leaf in role_leadership.items() if isinstance(leaf, dict) and leaf.get("value")
        ]
        if role_lines:
            lines.append("\n## Leadership (Research Findings)")
            lines.extend(role_lines)

    # Scale: dataset-#3's unit snapshot, or dataset-#1's per-period leaves.
    scale_snapshot = (profile.get("scale_snapshot") or {}).get("units")
    scale_value = scale_snapshot.get("value") if isinstance(scale_snapshot, dict) else None
    scale_perf = profile.get("scale_performance") or {}
    if isinstance(scale_value, dict):
        scale_lines = []
        for label, key in (("Units", "units"), ("Franchisee-owned units", "franchisee_owned_units"),
                            ("Company-owned units", "company_owned_units")):
            if scale_value.get(key) is not None:
                scale_lines.append(f"- {label}: {scale_value[key]}")
        if scale_lines:
            lines.append("\n## Scale (Research Findings)")
            lines.extend(scale_lines)
            trailer = _leaf_provenance(scale_snapshot).strip()
            if not scale_snapshot.get("source_url"):
                trailer = f"{trailer} No source citation was provided by the research pass for this snapshot.".strip()
            lines.append(f"\n*{trailer or 'No confidence/date on file for this snapshot.'}*")
    elif scale_perf:
        perf_lines = [
            f"- {period}: {leaf['value']}{_leaf_provenance(leaf)}" for period, leaf in scale_perf.items()
            if isinstance(leaf, dict) and leaf.get("value")
        ]
        if perf_lines:
            lines.append("\n## Scale (Research Findings)")
            lines.extend(perf_lines)

    # Technology: research-identified categories, ahead of (and often more
    # complete than) confirmed uses_vendor_for_category relationships,
    # which require a separate review-and-confirm step before they exist.
    canonical_technology = profile.get("canonical_technology") or {}
    tech_lines = [
        f"- {category}: {leaf['value']}{_leaf_provenance(leaf)}" for category, leaf in canonical_technology.items()
        if isinstance(leaf, dict) and leaf.get("value")
    ]
    if tech_lines:
        lines.append("\n## Technology (Research Findings)")
        lines.extend(tech_lines)
        if not any(leaf.get("source_url") for leaf in canonical_technology.values() if isinstance(leaf, dict)):
            # RB-2026-09-27, Todd: "no fluff, nothing made up" -- the
            # source dataset genuinely provides no per-category citation
            # for this section (only an aggregate confidence score), and
            # a URL is never invented to fill that gap. Say so explicitly
            # rather than let silence read as an oversight.
            lines.append("\n*No per-category source citation was provided by the research pass for this section.*")

    # Franchise disclosure -- FDD-derived fee/tech facts, dataset #3 only.
    # One shared confidence/date/source for the whole findings set (the
    # dataset attaches those to the disclosure document, not per fact),
    # shown once at the end rather than repeated on every line.
    disclosure_leaf = (profile.get("franchise_disclosure") or {}).get("findings")
    findings = disclosure_leaf.get("value") if isinstance(disclosure_leaf, dict) else None
    if isinstance(findings, dict) and findings:
        lines.append("\n## Franchise Disclosure")
        for key, val in findings.items():
            label = _FRANCHISE_DISCLOSURE_LABELS.get(key, key.replace("_", " ").title())
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val)
            lines.append(f"- {label}: {val}")
        lines.append(f"\n*{_leaf_provenance(disclosure_leaf).strip() or 'No confidence/date on file for this disclosure.'}*")

    for key, heading in _DEEP_RESEARCH_LEAF_GROUPS:
        group = profile.get(key) or {}
        group_lines = [
            f"- {field_name.replace('_', ' ').title()}: {leaf['value']}{_leaf_provenance(leaf)}"
            for field_name, leaf in group.items() if isinstance(leaf, dict) and leaf.get("value")
        ]
        if group_lines:
            lines.append(f"\n## {heading} (Research Findings)")
            lines.extend(group_lines)

    for key, heading in _DEEP_RESEARCH_LEDGER_FIELDS:
        items = [i for i in (profile.get(key) or []) if isinstance(i, dict) and i.get("finding")]
        if not items:
            continue
        lines.append(f"\n## {heading}")
        for item in items[:6]:
            date = (
                item.get("source_date") or item.get("accessed") or item.get("access_date")
                or item.get("reported_or_effective_date") or item.get("document_year")
            )
            prov = _provenance_suffix(confidence=item.get("confidence"), as_of=date, source_url=item.get("source_url"))
            lines.append(f"- {item['finding']}{prov}")
        if len(items) > 6:
            lines.append(f"- *...and {len(items) - 6} more finding(s) on file.*")

    return lines


def _render_mechanical_brand_brief(entity: dict, graph: dict) -> str:
    """Structural-facts-only brief for a brand with NO customers_prospects
    account (2026-09-25, the ~1,648-of-1,663 case) -- sourced from brand_
    profile_common.py's ecosystem-wide Phase A store (the exact data Team
    Portal's Company Profile card already shows) plus a real Technology
    Environment table from ecosystem_intelligence.json relationships,
    reusing account_background_brief.py's own renderer with an empty
    `account` dict since there's no customers_prospects technology_stack
    to merge in.

    Deliberately NOT persisted/versioned: computed live on every call, the
    same zero-cost pattern get_brand_profile() already uses (bpc.get_
    profile() recomputes trajectory fresh and is fast even across all
    1,663 brands -- confirmed, ~0.6s for the whole graph). There is
    nothing to "keep fresh" via a scheduled job here, because it's never
    stale: it always reads whatever brand_profiles/{id}.json and the
    graph hold right now. Todd's decision (2026-09-25): every brand in
    the ecosystem graph must produce a real mechanical brief through
    Team Portal's existing view/email path, not a dead end, once it's
    clear that button can be clicked for any of the ~1,663 tracked
    brands, not just the 15 formally engaged accounts."""
    brand_id = entity["id"]
    profile = bpc.shareable_view(bpc.get_profile(brand_id, graph=graph))
    identity = profile.get("identity") or {}
    footprint = profile.get("footprint") or {}
    trajectory = profile.get("trajectory") or {}
    synopsis_field = profile.get("synopsis") or {}

    lines = [f"# {profile.get('brand_name') or entity['name']} — Company Profile"]

    if synopsis_field.get("value"):
        lines.append(f"\n{synopsis_field['value']}")

    # Todd, 2026-09-27: most-relevant-first -- a rep opening this brief
    # wants "who do I talk to" and "what's happening now" before static
    # identity/footprint trivia, so leadership/signals/trajectory/tech
    # lead, with Identity and Footprint (rarely decision-relevant on their
    # own) pushed to the bottom.
    lines.extend(abb.render_leadership_section({"leadership": profile.get("leadership") or {}}))
    lines.extend(render_deep_research_intelligence_section(entity))

    signals = profile.get("recent_signals") or []
    if signals:
        lines.append("\n## Recent Signals")
        for s in signals:
            as_of = f" (as of {s['as_of']})" if s.get("as_of") else ""
            lines.append(f"- [{s.get('signal_type')}] {s.get('value')}{as_of}")

    badge = trajectory.get("badge")
    if badge and badge != "insufficient_data":
        pct = trajectory.get("value_pct")
        pct_display = round(pct, 1) if isinstance(pct, (int, float)) else pct
        lines.append(
            f"\n## Trajectory\n{badge} — {pct_display}% "
            f"({trajectory.get('metric_used')}, as of {trajectory.get('as_of_year')})"
        )

    by_id = ei._index_by_id(graph["entities"])
    entity_relationships = []
    for r in graph.get("relationships") or []:
        if r.get("from_entity_id") != brand_id and r.get("to_entity_id") != brand_id:
            continue
        other_id = r["to_entity_id"] if r.get("from_entity_id") == brand_id else r["from_entity_id"]
        enriched = dict(r)
        enriched["_other_entity_name"] = (by_id.get(other_id) or {}).get("name", other_id)
        entity_relationships.append(enriched)
    # Only call the renderer (and so only show the section header at all)
    # when there's a real active relationship to show -- it unconditionally
    # emits the header + disclaimer regardless of content, which would
    # otherwise make an entirely-unresearched brand's brief look like it
    # has a Technology Environment section when it has none.
    active_relationships = [r for r in entity_relationships if r.get("status") == "active"]
    if active_relationships:
        lines.extend(abb.render_technology_environment_section({}, entity_relationships))

    identity_lines = []
    for label, field in (
        ("Parent / ownership", identity.get("parent_ownership")),
        ("Headquarters", identity.get("hq_city_state")),
        ("Founded", identity.get("founded_year")),
    ):
        if field and field.get("value") is not None:
            identity_lines.append(f"- {label}: {field['value']}")
    if identity_lines:
        lines.append("\n## Identity")
        lines.extend(identity_lines)

    footprint_lines = []
    for label, field in (
        ("Total units", footprint.get("total_units")),
        ("Franchised units", footprint.get("franchised_units")),
        ("Company-owned units", footprint.get("company_owned_units")),
        ("Franchisee count", footprint.get("franchisee_count")),
    ):
        if field and field.get("value") is not None:
            footprint_lines.append(f"- {label}: {field['value']}")
    if footprint_lines:
        lines.append("\n## Footprint")
        lines.extend(footprint_lines)

    if len(lines) <= 1:
        lines.append("\n*No research on file for this brand yet.*")
    return "\n".join(lines).strip() + "\n"


def get_brand_background_brief(brand_id: str) -> dict:
    """Structural-only account facts for an EXISTING brand entity: Brand
    Profile, Technology Environment, Leadership, Related Artifacts —
    assembled directly from account_background_brief.py's extracted
    per-section renderers (render_brand_profile_section, etc.), never from
    the full render_background_brief()/generate_brief() pipeline. That
    pipeline's output is Todd's own strategic document (Executive Summary,
    Bottom Line, Recommended Approach, and any other free-text field on
    the brand profile are all his synthesis, not public/structural fact)
    and is deliberately never assembled or exposed here at all — not
    generated, not redacted after the fact, not reachable through this
    function by any path.

    Read-only: never creates or versions anything in system/account_
    research/ (that pipeline's own create-if-missing/versioning behavior
    belongs to Todd's own workflow, not a side effect of a teammate's
    read). For the ~1,648 brands with no customers_prospects account,
    falls back to _render_mechanical_brand_brief() (2026-09-25) -- the
    ecosystem-wide Phase A profile data, not an empty dead end."""
    graph = ei._read_graph()
    entity = ei._index_by_id(graph["entities"]).get(brand_id)
    if not entity or entity.get("entity_type") != "brand":
        raise NotFoundError(f"No brand entity '{brand_id}'")
    try:
        slug, exists = abb.resolve_account(entity["name"])
    except Exception:
        exists = False
    if not exists:
        return {"brand_id": brand_id, "markdown": _render_mechanical_brand_brief(entity, graph)}

    intel = abb.retrieve_existing_intelligence(slug)
    account = intel["dossier"]["account"]
    brand = intel["dossier"]["brand_profile"]
    display_name = account.get("display_name", entity["name"])

    # Todd, 2026-09-27: most-relevant-first, same reordering rationale as
    # _render_mechanical_brand_brief above -- Leadership ahead of
    # Technology Environment, Related Artifacts last.
    lines = [f"# {display_name} — Account Facts"]
    lines.extend(abb.render_brand_profile_section(brand))
    lines.extend(abb.render_leadership_section(account))
    lines.extend(render_deep_research_intelligence_section(entity))
    lines.extend(abb.render_technology_environment_section(account))
    lines.extend(abb.render_related_artifacts_section(intel))
    markdown = "\n".join(lines).strip() + "\n"
    return {"brand_id": brand_id, "slug": slug, "markdown": markdown}


def _resolve_vendor_entity(graph: dict, vendor_id: str) -> tuple[str, dict]:
    """Resolve a vendor entity by id, raising NotFoundError if it doesn't
    exist or isn't a vendor. Follows owner_entity_id (2026-09-28, PAR/
    Punchh rollup) to the parent vendor when set, so looking up a
    subsidiary product (e.g. "Punchh") always resolves to its parent's
    page (e.g. "PAR Technology") instead of being treated as an
    independent competitor -- caught live: Punchh and PAR Technology were
    tracked as fully separate vendors despite PAR's own competitor.json
    narrative already describing Punchh as PAR's product, and no vendor-
    parent rollup mechanism existed anywhere (only exact-alias-collision
    dedup, which would never catch this). Every vendor-scoped Team Portal
    read goes through this one function so a future rollup only needs
    setting owner_entity_id via ecosystem_intelligence.set_entity_owner --
    no per-route change. Bounded to a few hops as a defensive guard
    against a future data error creating an ownership cycle."""
    by_id = ei._index_by_id(graph["entities"])
    entity = by_id.get(vendor_id)
    if not entity or entity.get("entity_type") != "vendor":
        raise NotFoundError(f"No vendor entity '{vendor_id}'")
    seen = {vendor_id}
    for _ in range(5):
        owner_id = entity.get("owner_entity_id")
        if not owner_id or owner_id in seen:
            break
        owner_entity = by_id.get(owner_id)
        if not owner_entity or owner_entity.get("entity_type") != "vendor":
            break
        vendor_id, entity = owner_id, owner_entity
        seen.add(owner_id)
    return vendor_id, entity


def get_vendor_competitor_profile(vendor_id: str, *, member_id: str) -> dict:
    """Generate (or refresh) the Competitor Profile for an EXISTING vendor
    entity, then redact "Todd's POV" before returning it. Never creates a
    new vendor entity in ecosystem_intelligence.json — competitor_
    intelligence.generate_profile()'s own resolve-or-create only touches
    its separate profile store (system/competitor_intelligence/), and is
    never given a name this module hasn't already confirmed is a real,
    existing vendor entity."""
    graph = ei._read_graph()
    vendor_id, entity = _resolve_vendor_entity(graph, vendor_id)

    result = compintel.generate_profile(entity["name"])
    redacted = _redact_markdown_section(result["markdown"], _COMPETITOR_POV_SECTION_HEADING)
    return {"vendor_id": vendor_id, "competitor_slug": result["competitor_slug"], "markdown": redacted}


# ---------------------------------------------------------------------------
# Ecosystem Lookup Tool (Phase C) — brand profile / competitor profile /
# team-submitted corrections. Same constraints as everything else in this
# module: existing entities only, allowlist-redacted responses only.
# ---------------------------------------------------------------------------

import brand_profile_common as bpc  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import team_profile_submissions as tps  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402
import competitive_brief as cbrief  # noqa: E402
import competitive_brief_refresh_queue as cbrq  # noqa: E402
import battle_card as bcard  # noqa: E402
import value_wedge as vwedge  # noqa: E402
import competitive_landscape as cland  # noqa: E402
import vendor_profile_common as vpc  # noqa: E402
import technology_lifecycle as tech_lifecycle  # noqa: E402


def get_vendor_profile(vendor_id: str) -> dict:
    """Vendor-side sibling of get_brand_profile -- the vendor Company
    Profile card Todd asked for (2026-09-28). Resolves through
    _resolve_vendor_entity first (not vpc.resolve_vendor_entity directly)
    so a subsidiary lookup (e.g. Punchh) shows PAR Technology's own
    profile, same PAR/Punchh rollup every other vendor-scoped route uses."""
    graph = ei._read_graph()
    vendor_id, _entity = _resolve_vendor_entity(graph, vendor_id)
    try:
        full = vpc.get_profile(vendor_id, persist=False, graph=graph)
    except vpc.NotFoundError as exc:
        raise NotFoundError(str(exc)) from exc
    return vpc.shareable_view(full)


def add_brand_pain_point(
    brand_id: str, value: str, *, member_id: str, source_url: str | None = None, confidence: str = "medium",
) -> dict:
    """Value Wedge's Circle 2 ("Customer Needs") writer -- evidence-driven,
    same discipline as recent_signals: one real, dated observation per
    call, never a bulk backfill. Not yet exposed as a chat tool in
    rbb_chat.py (a natural, deliberately-deferred follow-on so Todd can
    log a need the moment it comes up on a call/email) -- this function is
    the reusable primitive that tool would call."""
    graph = ei._read_graph()
    entity = ei._index_by_id(graph["entities"]).get(brand_id)
    if not entity or entity.get("entity_type") != "brand":
        raise NotFoundError(f"No brand entity '{brand_id}'")
    profile = bpc.get_profile(brand_id, persist=False, graph=graph)
    bpc.add_pain_point(
        profile, value=value, confidence=confidence, source_url=source_url,
        last_reviewed_by=f"team:{member_id}",
    )
    bpc.save_profile(brand_id, profile)
    return {"brand_id": brand_id, "pain_points": profile["pain_points"]}


def get_brand_profile(brand_id: str) -> dict:
    """Structured (not markdown) brand profile -- identity, synopsis,
    leadership, footprint, live-computed trajectory, recent signals.
    Always the shareable view (bpc.shareable_view) -- Team Portal never
    exposes anything Todd hasn't explicitly cleared for a teammate to see,
    same discipline as every other route in this module. Works for ANY of
    the ~1,663 brand entities, not just the 15 with a customers_prospects
    account -- unlike get_brand_background_brief above, which still only
    covers those 15 (a real, currently-unclosed gap, tracked separately)."""
    try:
        full = bpc.get_profile(brand_id, persist=False)
    except bpc.NotFoundError as exc:
        raise NotFoundError(str(exc)) from exc
    return bpc.shareable_view(full)


def get_fdd_governance_profile(brand_id: str) -> dict:
    """FDD Technology Governance & Economics (brief §14, "Team Portal").
    Everything Technology Lifecycle/FDD has on this brand: tracked
    technology relationships and their current lifecycle state, governance
    records (mandated/approved-vendor-list/franchisee-choice/etc.),
    economics observations, FDD source documents reviewed, detected
    governance-change events, penetration reconciliation, and open
    research gaps. No redaction/is_owner branch, same reasoning as
    get_brand_profile/get_brand_ecosystem_profile above -- this is all
    public-source research (visibility_class default public_shared, see
    FDD_GOVERNANCE_ECONOMICS_BRIEF.md's §19), not Todd's private editorial
    judgment, so every teammate sees the same thing. An empty profile (no
    exception, every list empty) is this brand's real, honest "nothing on
    file yet" -- see get_entity_technology_profile's own docstring.
    Raises NotFoundError only when brand_id itself isn't a tracked entity
    at all."""
    graph = ei._read_graph()
    by_id = ei._index_by_id(graph["entities"])
    if brand_id not in by_id:
        raise NotFoundError(f"No entity '{brand_id}'")
    return tech_lifecycle.get_entity_technology_profile(brand_id)


def get_competitor_extended_profile(vendor_id: str) -> dict:
    """The new Products/Strengths/Vulnerabilities/etc. competitor fields
    (Phase B), for an EXISTING vendor entity -- resolves to its tracked
    competitor_intelligence record the same way get_vendor_competitor_profile
    does, backfilling a legacy record's missing fields and applying the
    shareable allowlist (weaknesses/vulnerabilities/todds_pov/vs_genius
    excluded)."""
    graph = ei._read_graph()
    vendor_id, entity = _resolve_vendor_entity(graph, vendor_id)

    slug, _created = compintel.ensure_competitor(entity["name"])
    competitor = cic.load_competitor(slug)["competitor"]
    view = cic.shareable_extended_view(competitor)
    view["vendor_id"] = vendor_id  # competitor_slug is already in the shareable view (cic._SHAREABLE_TOP_LEVEL_KEYS) -- use it, not vendor_id, when submitting a correction (target_type="competitor")
    return view


def submit_profile_correction(
    *,
    target_type: str,
    target_id: str,
    field_path: str,
    proposed_value: Any,
    member_id: str,
    note: str = "",
    source_url: str | None = None,
) -> dict:
    """A teammate's proposed correction to a brand or competitor profile
    field -- goes into the pending review queue, never applied live (Todd's
    2026-09-25 direction). Validates target_type/target_id resolve to a
    real entity before queuing, same "existing entities only" discipline
    as every other write path in this module -- a typo'd brand_id should
    fail loudly here, not silently sit in the queue forever."""
    if target_type == "brand":
        try:
            bpc.resolve_brand_entity(target_id)
        except bpc.NotFoundError as exc:
            raise NotFoundError(str(exc)) from exc
    elif target_type == "competitor":
        try:
            cic.competitor_dir(target_id)
        except FileNotFoundError as exc:
            raise NotFoundError(str(exc)) from exc
    else:
        raise ValidationError(f"target_type must be 'brand' or 'competitor', got {target_type!r}")

    return tps.submit(
        target_type=target_type, target_id=target_id, field_path=field_path,
        proposed_value=proposed_value, submitted_by=member_id, note=note, source_url=source_url,
    )


def get_canonical_background_brief(brand_id: str, *, is_owner: bool, requested_by: str) -> dict:
    """The "generate a background brief" button (2026-09-25 addition).
    Two entirely separate code paths by identity, not a redaction toggle
    over one shared path:

    is_owner=True (Todd himself, identified via manifest is_owner -- see
    get_current_member in team_portal_api.py): calls the REAL, full
    account_background_brief.generate_brief() pipeline -- the exact same
    capability rbb-chat's generateAccountBackgroundBrief tool already gives
    him, just reachable from Team Portal too. Creates the customers_prospects
    account shell first if one doesn't exist yet (matches that tool's own
    documented behavior: "works even for a brand with no existing record...
    creates one and renders honestly from what's actually there"). This is
    a real write (registers a new brief version) -- appropriate here
    because it's Todd's own action, not a side effect of a teammate's read.

    is_owner=False (a teammate): delegates to get_brand_background_brief()
    above -- the already-proven allowlist of 4 structural sections. There
    is deliberately NO "strip personal/sales content out of the full
    document" code path here: a 2026-08-29 incident already found real
    sensitive content (sales strategy, named colleagues, competitive
    tactics) leaking through sections a blocklist over the full brief
    didn't anticipate, which is exactly why the allowlist exists. Reusing
    it here keeps that guarantee instead of reopening the same risk under
    a new button.
    """
    if not is_owner:
        result = get_brand_background_brief(brand_id)
        result["is_full_canonical"] = False
        return result

    entity = _resolve_brand_entity(brand_id)
    slug, exists = abb.resolve_account(entity["name"])
    if not exists:
        cpc.create_pre_engagement_shell(slug, entity["name"])
    generated = abb.generate_brief(slug, generated_for=requested_by)
    return {
        "brand_id": brand_id, "slug": slug, "markdown": generated["markdown"],
        "version": generated.get("version"), "is_full_canonical": True,
    }


# ---------------------------------------------------------------------------
# Competitive Brief + Battle Card buttons (2026-09-25 addition). Both
# artifact types already exist, fully built and versioned, from the
# 2026-09-07 competitive-side persistence work (competitive_brief.py,
# battle_card.py) -- this only exposes them to Team Portal with the same
# is_owner split as get_canonical_background_brief above: Todd's click
# generates and persists a real new official version; every other
# teammate's click only ever READS the current persisted version (Todd's
# POV stripped), and never creates one -- so a teammate can't spam the
# version history of a document that carries Todd's name as the generator
# of record, and can't see a competitive read of Genius's own position
# before Todd has reviewed and generated it once.
# ---------------------------------------------------------------------------

def _redact_battle_card_for_team(markdown: str) -> str:
    """Team-portal view of a Battle Card: drops the '**Todd's POV:**' line
    battle_card.py's _render_competitor_card embeds per-competitor (inline
    within a single document, unlike Competitor Profile's single removable
    '## Todd's POV' section, so _redact_markdown_section doesn't apply
    here), and rewords two RM-specific field labels that name Todd/RM
    posture directly by field name rather than by section -- caught live
    2026-09-28: this is a team portal, not Todd-specific, and the
    underlying category_battle_cards schema (rm_plain_english_posture,
    when_to_bring_todd_in -- see competitor_intelligence_common.py's
    render_category_battle_card_body) predates that as a distinct product
    surface. Renamed at render time only, not in the underlying field
    names -- other real callers (rbb_chat.py, Todd's own chat tools) keep
    the original field names and Todd's own team-portal owner view is
    unaffected (this redaction only runs on the is_owner=False path)."""
    lines = markdown.split("\n")
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("**Todd's POV:**"):
            if out and out[-1] == "":
                out.pop()
            continue
        if stripped.startswith("**RM posture:**"):
            line = line.replace("**RM posture:**", "**Team posture:**", 1)
        elif stripped.startswith("**When to bring Todd in:**"):
            line = line.replace("**When to bring Todd in:**", "**Escalation trigger:**", 1)
        out.append(line)
    return "\n".join(out)


def get_competitive_brief_view(vendor_id: str, *, is_owner: bool, requested_by: str) -> dict:
    """The "generate a competitive brief" button. is_owner=True (Todd)
    generates and persists a new official version via competitive_brief.py
    -- the same canonical artifact its own CLI produces, now reachable from
    Team Portal. is_owner=False (a teammate) only reads the current
    persisted version with Todd's POV redacted; if none has been generated
    yet, returns markdown=None rather than erroring or generating one on a
    teammate's behalf."""
    graph = ei._read_graph()
    vendor_id, entity = _resolve_vendor_entity(graph, vendor_id)
    slug, _created = compintel.ensure_competitor(entity["name"])

    if is_owner:
        generated = cbrief.generate_competitive_brief(slug, generated_for=requested_by)
        return {
            "vendor_id": vendor_id, "competitor_slug": slug, "markdown": generated["markdown"],
            "version": generated.get("version"), "is_full_canonical": True,
        }

    current = cbrief.get_current_competitive_brief(slug, include_content=True)
    if current is None:
        return {"vendor_id": vendor_id, "competitor_slug": slug, "markdown": None,
                "version": None, "is_full_canonical": False}
    redacted = _redact_markdown_section(current["content"], _COMPETITOR_POV_SECTION_HEADING)
    return {"vendor_id": vendor_id, "competitor_slug": slug, "markdown": redacted,
            "version": current.get("version"), "is_full_canonical": False}


def request_competitive_brief_refresh(vendor_id: str, *, member_id: str, member_email: str) -> dict:
    """Todd's explicit "Option B" (2026-09-30): any teammate (not just
    Todd) can ask for a Competitive Brief's LLM-synthesized "bottom line"
    sooner than the weekly Friday EOW pass. This ONLY records the
    request -- competitive_brief_refresh_queue.request_refresh() never
    generates anything itself, consistent with "there are no on demand
    request or real-time reports in the team portal option." The actual
    regeneration + email happens the next morning, via morning_pipeline.
    py's competitive_brief_refresh_queue step."""
    graph = ei._read_graph()
    vendor_id, entity = _resolve_vendor_entity(graph, vendor_id)
    slug, _created = compintel.ensure_competitor(entity["name"])
    record = cbrq.request_refresh(competitor_slug=slug, member_id=member_id, email=member_email)
    return {"vendor_id": vendor_id, "competitor_slug": slug, "requested_at": record["requested_at"]}


def get_value_wedge_view(
    vendor_id: str, brand_id: str, *, is_owner: bool, requested_by: str, category: str | None = None,
) -> dict:
    """The "generate a value wedge" button -- redesigned 2026-09-28 to be
    brand+vendor scoped (Todd's direction: the real three-circle
    methodology is inherently account-specific -- "Customer Needs" means
    THIS account's needs, not a generic category talking point). Entry
    point is the brand page's tech-stack table, not the vendor page, so
    both ids are always real, already-confirmed entities by the time this
    is called. Same is_owner generate-vs-read split as
    get_competitive_brief_view/get_battle_card_view above. Returns
    structured `data` (a dict), not markdown -- see value_wedge.py's
    module docstring for why. No redaction step: circle 2 (brand
    pain_points) and circle 3 (vs_genius.genius_advantages) are both
    already-shareable account-fact/gap-analysis content, same reasoning
    as Competitor Profile's "Gap analysis vs. Genius" section; circle 1 is
    the Genius Capability Library, never Todd-private."""
    graph = ei._read_graph()
    vendor_id, entity = _resolve_vendor_entity(graph, vendor_id)
    brand_entity = ei._index_by_id(graph["entities"]).get(brand_id)
    if not brand_entity or brand_entity.get("entity_type") != "brand":
        raise NotFoundError(f"No brand entity '{brand_id}'")
    slug, _created = compintel.ensure_competitor(entity["name"])

    if is_owner:
        generated = vwedge.generate_value_wedge(slug, brand_id, category=category, generated_for=requested_by)
        return {
            "vendor_id": vendor_id, "competitor_slug": slug, "brand_id": brand_id,
            "data": generated["data"], "markdown": vwedge.render_value_wedge_markdown(generated["data"]),
            "version": generated.get("version"), "is_full_canonical": True,
        }

    current = vwedge.get_current_value_wedge(slug, brand_id, include_content=True)
    if current is None:
        return {"vendor_id": vendor_id, "competitor_slug": slug, "brand_id": brand_id,
                "data": None, "markdown": None, "version": None, "is_full_canonical": False}
    return {"vendor_id": vendor_id, "competitor_slug": slug, "brand_id": brand_id,
            "data": current["data"], "markdown": vwedge.render_value_wedge_markdown(current["data"]),
            "version": current.get("version"), "is_full_canonical": False}


def _resolve_vendor_category(vendor_id: str) -> tuple[str, dict, str | None]:
    graph = ei._read_graph()
    vendor_id, entity = _resolve_vendor_entity(graph, vendor_id)
    category = (entity.get("attributes") or {}).get("primary_category")
    return vendor_id, entity, category


def get_battle_card_view(vendor_id: str, *, is_owner: bool, requested_by: str) -> dict:
    """The "generate a battle card" button. Computed category-wide
    (battle_card.py covers Genius's own position plus up to 5 competitors
    in the vendor's tech-stack category), resolved from the vendor
    entity's attributes.primary_category (populated for ~92% of tracked
    vendors via Technomic/tech-stack ingestion; the rest return
    available=False rather than guessing a category) -- but always
    featuring `vendor_id` itself first regardless of category market
    share (2026-09-28 fix: see battle_card_for_category's docstring for
    the live PAR/Qu bug this replaced), so each vendor gets its own
    independently-versioned artifact via battle_card.py's composite slug.
    Same is_owner split as get_competitive_brief_view above."""
    vendor_id, entity, category = _resolve_vendor_category(vendor_id)
    if not category or category not in cland.TECH_STACK_CATEGORIES:
        return {"vendor_id": vendor_id, "category": category, "available": False,
                "markdown": None, "version": None, "is_full_canonical": False}

    if is_owner:
        generated = bcard.generate_battle_card(category, generated_for=requested_by, feature_vendor_id=vendor_id)
        return {"vendor_id": vendor_id, "category": category, "available": True,
                "markdown": generated["markdown"], "version": generated.get("version"),
                "is_full_canonical": True}

    current = bcard.get_current_battle_card(category, include_content=True, feature_vendor_id=vendor_id)
    if current is None:
        return {"vendor_id": vendor_id, "category": category, "available": True,
                "markdown": None, "version": None, "is_full_canonical": False}
    redacted = _redact_battle_card_for_team(current["content"])
    return {"vendor_id": vendor_id, "category": category, "available": True,
            "markdown": redacted, "version": current.get("version"), "is_full_canonical": False}
