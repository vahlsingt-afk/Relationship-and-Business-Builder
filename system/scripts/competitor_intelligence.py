#!/usr/bin/env python3
"""
competitor_intelligence.py — persistent Competitor Intelligence lifecycle.

RB-2026-08-28. Closes a real gap found while reviewing RB's artifact
inventory: RB persists intelligence on restaurant BRANDS (the macro graph,
account research, Blue Sheets) but had no equivalent durable artifact for
the vendors competing with Genius (PAR, Toast, Oracle, NCR, Qu, Revel,
Nory, Restaurant365, ...) despite real competitive-analysis work already
happening in chat. Same principle as account_background_brief.py: durable,
versioned, retrievable intelligence, not a one-off document.

Fed two ways, per Todd's explicit direction:
  1. Intelligence gathering process — sync_from_ecosystem() mechanically
     pulls ecosystem_intelligence.json signals already tagged to this
     vendor's entity_id, and account_intelligence/ docs mentioning it by
     name. Never LLM-interpreted; only what's already been persisted
     elsewhere in RB.
  2. User input — add_competitive_note() appends a structured, human- (or
     disciplined-tool-call-) supplied evidence entry. Never free-form
     auto-persist, matching the fabrication-risk lesson already learned
     once this session with the general-purpose mutation engine.

Deliberately does NOT do autonomous live web research, for the same reason
account_background_brief.py doesn't: RBB has no web-search tool, and
building an ungated one carries real fabrication risk. This module
retrieves and renders what RBB already knows.

CLI:
    python3 system/scripts/competitor_intelligence.py resolve "PAR Technology"
    python3 system/scripts/competitor_intelligence.py sync par-technology
    python3 system/scripts/competitor_intelligence.py generate par-technology
    python3 system/scripts/competitor_intelligence.py --smoke
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402

try:
    import intelligence_index  # noqa: E402
except Exception:  # noqa: BLE001
    intelligence_index = None


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


# ---------------------------------------------------------------------------
# Step 1 — resolve competitor identity against the vendor entity graph
# ---------------------------------------------------------------------------

def _root_owner_entity(graph: dict, entity: dict) -> dict:
    """Walk owner_entity_id up to the ultimate parent (e.g. a PAR/NCR
    product-line entity like "Aloha POS" or "PAR Ops" up to "PAR
    Technology"/"NCR"). Real 2026-09-30 request (Todd): a vendor's
    product-line/subsidiary entities matter most for RESEARCH --
    encountering "Aloha Essentials" or "Data Central" in a filing or
    press release should roll that evidence up under the one real
    competitor record (NCR Voyix, PAR Technology), not fragment into a
    brand-new standalone competitor shell per product name. Cycle-
    guarded (a stray owner_entity_id loop must never hang this)."""
    by_id = ei._index_by_id(graph.get("entities") or [])
    seen = {entity.get("id")}
    current = entity
    while current.get("owner_entity_id"):
        parent = by_id.get(current["owner_entity_id"])
        if not parent or parent.get("id") in seen:
            break
        seen.add(parent.get("id"))
        current = parent
    return current


def resolve_competitor(name: str) -> tuple[Optional[str], Optional[str], bool]:
    """Match a name against ecosystem_intelligence.json's vendor entities.

    Returns (competitor_slug, vendor_entity_id, is_new). vendor_entity_id is
    None if no matching vendor entity exists yet -- tracking a competitor
    RB hasn't seen in the ecosystem graph is still allowed (the vendor may
    simply not have any brand relationships recorded yet), it just starts
    with no ecosystem_signal evidence to sync.

    When the matched entity is itself a product-line/subsidiary entity
    (owner_entity_id set -- e.g. "Aloha POS" owned by "NCR"), resolves
    against its root owner instead of the matched entity itself, so
    evidence about a sub-brand rolls up into the one real competitor
    record rather than spawning a new one per product name.
    """
    graph = ei._read_graph()
    name_lower = (name or "").strip().lower()
    slug = _slugify(name)
    for entity in graph.get("entities", []):
        if entity.get("entity_type") != "vendor":
            continue
        candidates = [entity.get("name", "")] + (entity.get("aliases") or [])
        if any((c or "").strip().lower() == name_lower for c in candidates):
            root = _root_owner_entity(graph, entity)
            matched_slug = _slugify(root["name"])
            return matched_slug, root["id"], not _shell_exists(matched_slug)
    return slug, None, not _shell_exists(slug)


def _find_vendor_entity(vendor_entity_id: str) -> Optional[dict]:
    if not vendor_entity_id:
        return None
    graph = ei._read_graph()
    for entity in graph.get("entities", []):
        if entity.get("id") == vendor_entity_id:
            return entity
    return None


def _shell_exists(slug: str) -> bool:
    try:
        cic.competitor_dir(slug)
        return True
    except FileNotFoundError:
        return False


class CompetitorCreationError(Exception):
    """Raised by ensure_competitor()/generate_profile() with enough state
    for a caller to know exactly what already landed and whether a retry
    is safe. RB-DEFECT-071 (2026-09-11): the prior bare-exception behavior
    left 141 real competitor shells created with no way for a caller to
    know registration hadn't completed -- and, separately, no way to know a
    retry was actually safe (see the ensure_competitor() fix below, which
    makes it always safe now)."""

    def __init__(self, stage: str, slug: str, *, shell_created: bool, registered: bool, cause: Exception):
        self.stage = stage
        self.slug = slug
        self.shell_created = shell_created
        self.registered = registered
        self.cause = cause
        super().__init__(f"competitor creation failed at stage '{stage}' for '{slug}': {cause}")


def ensure_competitor(name: str) -> tuple[str, bool]:
    """Resolve-or-create. Returns (competitor_slug, already_tracked). When a
    matching vendor entity exists in the ecosystem graph, seeds aliases and
    primary_category from it so downstream matching (account_intelligence
    doc discovery, evidence lookups) has real data to work with instead of
    an empty shell.

    RB-DEFECT-071 (2026-09-11): register_competitor() used to run only
    inside `if is_new:` (gated on whether the shell directory already
    existed) -- so a competitor whose shell was created but whose registry
    write then failed (exactly what happened to 141 of the FSTEC batch,
    once the unlocked registry write started racing) could never self-heal
    on retry: a second attempt saw the shell already existed, treated it as
    "not new," and skipped registration forever. register_competitor() is
    already idempotent on its own (updates last_updated_at if already
    registered, appends if not), so the fix is just to call it
    unconditionally rather than gating it on shell freshness.

    RB-DEFECT-073 (2026-09-28): cic.is_own_company() existed since
    RB-DEFECT-071 but was never actually enforced here -- confirmed live,
    "global-payments" (Genius's own parent company) was a real, registered
    tracked competitor. Genius/Global Payments intelligence belongs in
    genius_capabilities.add_evidence(scope="parent", ...), never a
    competitor profile; refuse at the one choke point every
    resolve-or-create path already goes through rather than relying on
    every future caller to check first."""
    if cic.is_own_company(name):
        raise ValueError(
            f"refusing to track {name!r} as a competitor -- it resolves to Genius/Global "
            "Payments (see competitor_intelligence_common.is_own_company()). Genius's own "
            "intelligence belongs in genius_capabilities.add_evidence(scope='parent', ...), "
            "not a competitor profile."
        )
    slug, vendor_entity_id, is_new = resolve_competitor(name)
    shell_created = False
    if is_new:
        try:
            cic.create_competitor_shell(slug, name, vendor_entity_id)
            shell_created = True
            entity = _find_vendor_entity(vendor_entity_id)
            if entity:
                data = cic.load_competitor(slug)
                comp = data["competitor"]
                comp["aliases"] = entity.get("aliases") or []
                comp["primary_category"] = (entity.get("attributes") or {}).get("primary_category")
                cic.save_json(cic.competitor_dir(slug) / "competitor.json", comp)
        except Exception as exc:  # noqa: BLE001
            raise CompetitorCreationError(
                "create_shell", slug, shell_created=shell_created, registered=False, cause=exc,
            ) from exc
    try:
        cic.register_competitor(slug, name)
    except Exception as exc:  # noqa: BLE001
        raise CompetitorCreationError(
            "register", slug, shell_created=True, registered=False, cause=exc,
        ) from exc
    return slug, not is_new


def ensure_competitor_by_slug(slug: str) -> dict:
    """Load a competitor by slug, auto-creating a shell via ensure_competitor()
    if none exists yet. RB-2026-09-25, Todd's explicit direction: reverses
    this module's original strict-on-purpose design (competitive_brief.py's
    generate_competitive_brief() and value_wedge.py's generate_value_wedge()
    used to require the competitor already exist, specifically to avoid
    silently tracking a typo'd slug as a new competitor) -- accepting that a
    genuinely typo'd slug now creates a new, mostly-empty shell rather than
    erroring, the same way bulkImportCompetitors already tolerates a bad
    name in a batch.

    The slug is title-cased back into a guessed display name (same
    `slug.replace("-", " ").title()` fallback priority_account_publisher_
    scan.py/job_postings_promotion.py already use elsewhere in this
    codebase for the same "no display name on hand" situation) and run
    through the normal resolve-or-create path -- so a slug that DOES match
    a real, already-known vendor entity still gets its real name/aliases/
    primary_category seeded exactly as ensure_competitor() always does;
    only a slug with no real match falls back to the guessed name as-is.

    _slugify(guessed_name) round-trips back to the original slug for
    every real case this has been checked against, but isn't guaranteed
    for a pathological slug (repeated separators, leading digits) --
    rather than risk silently creating a shell at a DIFFERENT slug than
    the one requested, this falls back to creating the shell directly at
    the exact requested slug (skipping vendor-entity name matching, since
    a slug this irregular was never going to match one anyway)."""
    try:
        return cic.load_competitor(slug)
    except FileNotFoundError:
        pass
    guessed_name = slug.replace("-", " ").title()
    new_slug, _already_tracked = ensure_competitor(guessed_name)
    if new_slug != slug:
        cic.create_competitor_shell(slug, guessed_name, None)
        cic.register_competitor(slug, guessed_name)
    return cic.load_competitor(slug)


# ---------------------------------------------------------------------------
# Step 2 — sync from the intelligence-gathering process (ecosystem signals +
# account_intelligence docs)
# ---------------------------------------------------------------------------

def sync_from_ecosystem(slug: str) -> dict:
    """Mechanically pull new evidence for this competitor from sources RB
    already gathers, idempotently. Never invents; only appends what's
    already persisted elsewhere and not yet recorded here."""
    data = cic.load_competitor(slug)
    comp = data["competitor"]
    already_recorded = {e.get("evidence_id") for e in data["evidence"]}
    appended: list[dict] = []

    # Source 1: ecosystem_intelligence.json signals tagged to this vendor's
    # entity_id -- the same "material signal" concept mp_impact_review.py
    # uses for Master Account Plans, applied to competitor tracking.
    vendor_entity_id = comp.get("vendor_entity_id")
    if vendor_entity_id:
        graph = ei._read_graph()
        for sig in graph.get("signals", []):
            if vendor_entity_id not in (sig.get("entities") or []):
                continue
            evidence_id = f"eco-{sig.get('id', '')}"
            if evidence_id in already_recorded:
                continue
            record = {
                "evidence_id": evidence_id,
                "logged_at": cic.now_iso(),
                "category": "ecosystem_signal",
                "summary": sig.get("summary", ""),
                "event_at": sig.get("event_at"),
                "source": "ecosystem_intelligence.json",
                "confidence": (sig.get("confidence") or {}).get("level"),
            }
            cic.append_jsonl(cic.competitor_dir(slug) / "evidence.jsonl", record)
            appended.append(record)
            already_recorded.add(evidence_id)

    # Source 2: account_intelligence/ docs whose title mentions this
    # competitor by display name or alias -- same discovery pattern
    # account_background_brief.find_account_intelligence_docs() uses.
    names = [comp.get("display_name", "")] + (comp.get("aliases") or [])
    name_tokens = [_slugify(n) for n in names if n]
    ai_dir = core.SYSTEM_DIR / "account_intelligence"
    if ai_dir.is_dir():
        for path in sorted(ai_dir.glob("*.md")):
            fname_slug = path.stem.lower()
            if not any(tok and tok in fname_slug.replace("-", "") for tok in
                       [t.replace("-", "") for t in name_tokens]):
                continue
            evidence_id = f"doc-{path.stem}"
            if evidence_id in already_recorded:
                continue
            record = {
                "evidence_id": evidence_id,
                "logged_at": cic.now_iso(),
                "category": "other",
                "summary": f"Referenced in account_intelligence doc: {path.name}",
                "source": f"system/account_intelligence/{path.name}",
                "confidence": None,
            }
            cic.append_jsonl(cic.competitor_dir(slug) / "evidence.jsonl", record)
            appended.append(record)
            already_recorded.add(evidence_id)

    if appended:
        comp["last_evidence_date"] = cic.today()
    comp["last_synced_at"] = cic.now_iso()
    comp["updated_at"] = cic.now_iso()
    cic.save_json(cic.competitor_dir(slug) / "competitor.json", comp)
    cic.register_competitor(slug, comp.get("display_name", slug))

    return {"ok": True, "competitor_slug": slug, "evidence_appended": len(appended), "appended": appended}


def sync_all_tracked_competitors() -> dict:
    """Run sync_from_ecosystem() for every registered competitor.
    RB-2026-09-01: this function existed per-competitor (sync_from_ecosystem
    above, syncCompetitorIntelligence's API) but was never called for the
    whole registry anywhere -- confirmed live, morning_pipeline.py never
    invoked it, so 'tracking competitors through daily intelligence' wasn't
    actually happening despite the mechanism being real and working.
    Wired into the daily pipeline as its own step; never fails the whole
    run (same discipline as every other best-effort morning_pipeline step)."""
    reg = cic.load_registry()
    results = []
    for entry in reg.get("registry", []):
        slug = entry.get("competitor_slug", "")
        try:
            results.append(sync_from_ecosystem(slug))
        except Exception as exc:  # noqa: BLE001
            results.append({"ok": False, "competitor_slug": slug, "error": str(exc)})
    return {
        "ok": True,
        "competitors_synced": len(results),
        "total_evidence_appended": sum(r.get("evidence_appended", 0) for r in results),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Step 3 — user-supplied competitive notes
# ---------------------------------------------------------------------------

def add_competitive_note(
    slug: str, note: str, *, category: str = "other", source: str = "Todd Vahlsing",
    confidence: str = "high",
) -> dict:
    """The user-input feed. Appends one structured, human-supplied evidence
    entry -- never inferred, never auto-generated from free text."""
    if category not in cic.VALID_EVIDENCE_CATEGORIES:
        raise ValueError(f"category must be one of {sorted(cic.VALID_EVIDENCE_CATEGORIES)}, got {category!r}")
    if not (note or "").strip():
        raise ValueError("note must not be empty")

    data = cic.load_competitor(slug)
    comp = data["competitor"]
    n = len(data["evidence"])
    evidence_id = f"note-{n + 1:04d}"
    record = {
        "evidence_id": evidence_id,
        "logged_at": cic.now_iso(),
        "category": category,
        "summary": note.strip(),
        "source": source,
        "confidence": confidence,
    }
    cic.append_jsonl(cic.competitor_dir(slug) / "evidence.jsonl", record)
    comp["last_evidence_date"] = cic.today()
    comp["updated_at"] = cic.now_iso()
    if category == "pov":
        comp["todds_pov"] = note.strip()
    cic.save_json(cic.competitor_dir(slug) / "competitor.json", comp)
    cic.register_competitor(slug, comp.get("display_name", slug))
    return {"ok": True, "competitor_slug": slug, "evidence_id": evidence_id}


def add_extended_profile_finding(
    slug: str, field: str, value: str, *, confidence: str = "medium",
    source_url: str | None = None, as_of: str | None = None,
    finding_type: str | None = None, source_owner: str | None = None,
    source_type: str | None = None, published_at: str | None = None,
    deployment_scope: str | None = None, is_vendor_claim: bool | None = None,
    is_inference: bool | None = None, limitations_or_conflicts: str | None = None,
) -> dict:
    """Writer for the EXTENDED_PROFILE_FIELDS (products/strengths/
    weaknesses/vulnerabilities/key_customers/recent_news/vendor_claims/
    product_lineage -- list fields, each entry independently dated/sourced
    -- and trends, a single synthesized-prose field). This is the gap
    `get_competitor_extended_profile()`/Team Portal's "Competitor Snapshot"
    card was showing honest-empty for every tracked vendor (2026-09-28,
    Todd: "we have no information on products, strengths, key customers,
    recent news or trends"), and the field these are actually stored in
    already existed (competitor_intelligence_common.extended_field()) but
    had no writer -- unlike add_competitive_note()'s evidence.jsonl log,
    never fabricated, same discipline: every call is one real, sourced
    observation.

    The keyword-only provenance parameters (RB-DEFECT-073, 2026-09-28) are
    an optional pass-through to extended_field() -- a caller with richer
    per-finding provenance (a deep-research importer) can record it; every
    pre-existing caller that omits them still works exactly as before."""
    if field == "trends":
        pass
    elif field not in cic.EXTENDED_PROFILE_FIELDS:
        raise ValueError(f"field must be one of {cic.EXTENDED_PROFILE_FIELDS + ('trends',)}, got {field!r}")
    if not (value or "").strip():
        raise ValueError("value must not be empty")

    data = cic.load_competitor(slug)
    comp = data["competitor"]
    leaf = cic.extended_field(
        value.strip(), confidence=confidence, as_of=as_of, source_url=source_url,
        finding_type=finding_type, source_owner=source_owner, source_type=source_type,
        published_at=published_at, deployment_scope=deployment_scope,
        is_vendor_claim=is_vendor_claim, is_inference=is_inference,
        limitations_or_conflicts=limitations_or_conflicts,
    )

    if field == "trends":
        comp["trends"] = leaf
    else:
        existing = comp.setdefault(field, [])
        if any(e.get("value") == leaf["value"] for e in existing):
            return {"ok": True, "competitor_slug": slug, "field": field, "deduped": True}
        existing.append(leaf)

    comp["last_evidence_date"] = cic.today()
    comp["updated_at"] = cic.now_iso()
    cic.save_json(cic.competitor_dir(slug) / "competitor.json", comp)
    cic.register_competitor(slug, comp.get("display_name", slug))
    return {"ok": True, "competitor_slug": slug, "field": field, "deduped": False}


# ---------------------------------------------------------------------------
# Step 3b — product-line competition mapping + gap analysis (user-supplied,
# same discipline as add_competitive_note: structured, never inferred)
# ---------------------------------------------------------------------------

def set_competes_on(slug: str, product_lines: list[str]) -> dict:
    """Declare which Genius product line(s) this competitor directly
    competes on. Replaces the full set (not additive) -- callers pass the
    complete current list, same as how a Blue Sheet field is set, not
    appended to."""
    invalid = [p for p in product_lines if p not in cic.GENIUS_PRODUCT_LINES]
    if invalid:
        raise ValueError(f"unknown product line(s) {invalid}; valid: {sorted(cic.GENIUS_PRODUCT_LINES)}")
    data = cic.load_competitor(slug)
    comp = data["competitor"]
    comp["competes_on"] = sorted(set(product_lines))
    comp["updated_at"] = cic.now_iso()
    cic.save_json(cic.competitor_dir(slug) / "competitor.json", comp)
    return {"ok": True, "competitor_slug": slug, "competes_on": comp["competes_on"]}


def add_gap_point(
    slug: str, side: str, point: str, *, evidence_id: str | None = None, category: str | None = None,
) -> dict:
    """Add one gap-analysis point: side='genius' means Genius wins/leads
    here, side='competitor' means the competitor wins/leads here. Each
    point is a discrete, sourced claim -- not a freeform paragraph -- so
    the gap analysis stays auditable back to real evidence rather than
    becoming another place unverified claims accumulate.

    `category` (2026-09-28, one of cic.GENIUS_PRODUCT_LINES, optional):
    tags a point as specific to one Genius product line (e.g. a PAR
    weakness that's really about their POS/back-office products, not
    their loyalty product, Punchh) -- omit it for a point that's true of
    the whole company regardless of which product is being compared
    (financial health, overall strategy). Caught live: Value Wedge's
    Circle 3 had no way to tell these apart, so a POS-specific weakness
    (PAR's RTI/Data Central lineage) was showing up on a Loyalty-scoped
    wedge for McDonald's, where it's simply off-topic."""
    if side not in ("genius", "competitor"):
        raise ValueError("side must be 'genius' or 'competitor'")
    if not (point or "").strip():
        raise ValueError("point must not be empty")
    if category is not None and category not in cic.GENIUS_PRODUCT_LINES:
        raise ValueError(f"category must be one of {sorted(cic.GENIUS_PRODUCT_LINES)}, got {category!r}")
    data = cic.load_competitor(slug)
    comp = data["competitor"]
    key = "genius_advantages" if side == "genius" else "competitor_advantages"
    entry = {"point": point.strip(), "evidence_id": evidence_id, "added_at": cic.now_iso()}
    if category:
        entry["category"] = category
    comp.setdefault("vs_genius", {}).setdefault(key, []).append(entry)
    comp["updated_at"] = cic.now_iso()
    cic.save_json(cic.competitor_dir(slug) / "competitor.json", comp)
    return {"ok": True, "competitor_slug": slug, "vs_genius": comp["vs_genius"]}


def correct_gap_point(
    slug: str, side: str, old_point: str, new_point: str, *, evidence_id: str | None = None,
) -> dict:
    """Fix a gap-analysis point that's factually wrong -- add_gap_point()
    above is append-only by design (every point is a discrete, sourced
    claim), but that has no path for correcting one that was simply
    incorrect, as opposed to adding a new one. Matches by exact existing
    `point` text rather than evidence_id, since one evidence_id (e.g. a
    recovered positioning doc) commonly backs several distinct points --
    matching on evidence_id alone could correct the wrong sibling point.
    Raises ValueError if old_point doesn't match exactly one entry, so a
    stale caller surfaces loudly rather than silently correcting nothing
    or the wrong thing."""
    if side not in ("genius", "competitor"):
        raise ValueError("side must be 'genius' or 'competitor'")
    if not (new_point or "").strip():
        raise ValueError("new_point must not be empty")
    data = cic.load_competitor(slug)
    comp = data["competitor"]
    key = "genius_advantages" if side == "genius" else "competitor_advantages"
    entries = (comp.get("vs_genius") or {}).get(key, [])
    matches = [e for e in entries if e.get("point") == old_point]
    if not matches:
        raise ValueError(f"no {key} point matches old_point exactly for {slug!r}")
    if len(matches) > 1:
        raise ValueError(f"old_point matches {len(matches)} {key} entries for {slug!r} -- ambiguous")
    entry = matches[0]
    entry["point"] = new_point.strip()
    entry["corrected_at"] = cic.now_iso()
    if evidence_id is not None:
        entry["evidence_id"] = evidence_id
    comp["updated_at"] = cic.now_iso()
    cic.save_json(cic.competitor_dir(slug) / "competitor.json", comp)
    return {"ok": True, "competitor_slug": slug, "vs_genius": comp["vs_genius"]}


# ---------------------------------------------------------------------------
# Step 3c — category-scoped RM battle cards (user-supplied, same discipline
# as add_competitive_note/add_gap_point: structured, never inferred, partial
# updates only). Sits alongside the vendor-level positioning_summary/
# todds_pov/vs_genius fields above -- competitive_landscape.py prefers a
# category entry when present and falls back to the vendor-level fields
# otherwise, so this is purely additive.
# ---------------------------------------------------------------------------

def valid_battle_card_categories() -> set[str]:
    """Every category a category_battle_cards entry may be filed under --
    the real graph-derived tech-stack categories plus the RM-only
    categories (platform, delivery_aggregation) that have no per-brand
    graph data source yet. Lazy import to avoid a module-load-order
    dependency on competitive_landscape.py."""
    import competitive_landscape as cl
    return set(cl.TECH_STACK_CATEGORIES) | set(cl.EXTRA_BATTLE_CARD_CATEGORIES)


def upsert_category_battle_card(
    slug: str, category: str, *,
    status: str | None = None,
    confidence_pct: int | None = None,
    rm_plain_english_posture: str | None = None,
    when_to_bring_todd_in: str | None = None,
    evidence_id: str | None = None,
) -> dict:
    """Create-or-update the RM battle card for one (competitor, category)
    pair. Only overwrites fields explicitly passed -- None means leave
    unchanged, same never-invented, never-blank-filled discipline as
    add_competitive_note. evidence_id, if passed, is appended to the
    card's evidence_ids (not replaced)."""
    if category not in valid_battle_card_categories():
        raise ValueError(f"unknown category {category!r}. Call valid_battle_card_categories() for the real list.")
    if status is not None and status not in cic.CATEGORY_BATTLE_CARD_STATUSES:
        raise ValueError(f"status must be one of {sorted(cic.CATEGORY_BATTLE_CARD_STATUSES)}, got {status!r}")
    if confidence_pct is not None and not (0 <= confidence_pct <= 100):
        raise ValueError(f"confidence_pct must be 0-100, got {confidence_pct!r}")

    data = cic.load_competitor(slug)
    comp = data["competitor"]
    cards = comp.setdefault("category_battle_cards", {})
    card = cards.setdefault(category, cic._empty_category_battle_card())

    if status is not None:
        card["status"] = status
        card["last_validated"] = cic.today()
    if confidence_pct is not None:
        card["confidence_pct"] = confidence_pct
    if rm_plain_english_posture is not None:
        card["rm_plain_english_posture"] = rm_plain_english_posture.strip()
    if when_to_bring_todd_in is not None:
        card["when_to_bring_todd_in"] = when_to_bring_todd_in.strip()
    if evidence_id is not None and evidence_id not in card["evidence_ids"]:
        card["evidence_ids"].append(evidence_id)
    card["updated_at"] = cic.now_iso()

    comp["updated_at"] = cic.now_iso()
    cic.save_json(cic.competitor_dir(slug) / "competitor.json", comp)
    return {"ok": True, "competitor_slug": slug, "category": category, "battle_card": card}


def add_category_battle_card_point(
    slug: str, category: str, field: str, point: str, *, evidence_id: str | None = None,
) -> dict:
    """Append one discrete, sourced point to a category battle card's
    listen_for/discovery_questions/red_flags list -- mirrors add_gap_point
    exactly: one claim per call, never a paragraph, category must already
    be valid (call upsert_category_battle_card first if the card doesn't
    exist yet -- this does NOT create a card on its own)."""
    if field not in ("listen_for", "discovery_questions", "red_flags"):
        raise ValueError("field must be one of 'listen_for', 'discovery_questions', 'red_flags'")
    if not (point or "").strip():
        raise ValueError("point must not be empty")
    data = cic.load_competitor(slug)
    comp = data["competitor"]
    cards = comp.get("category_battle_cards", {})
    if category not in cards:
        raise ValueError(f"no battle card exists yet for category {category!r} -- call upsert_category_battle_card first")
    card = cards[category]
    card[field].append(point.strip())
    if evidence_id is not None and evidence_id not in card["evidence_ids"]:
        card["evidence_ids"].append(evidence_id)
    card["updated_at"] = cic.now_iso()
    comp["updated_at"] = cic.now_iso()
    cic.save_json(cic.competitor_dir(slug) / "competitor.json", comp)
    return {"ok": True, "competitor_slug": slug, "category": category, "battle_card": card}


# ---------------------------------------------------------------------------
# Step 4 — render the profile
# ---------------------------------------------------------------------------

def render_competitor_profile(slug: str) -> str:
    """Deterministic template rendering from persisted evidence only --
    same discipline as account_background_brief.render_background_brief:
    display what's on file, never re-summarize or invent.

    Deliberately does NOT render `last_synced_at` (2026-09-25 fix -- it
    originally did, in the header line). sync_from_ecosystem() bumps that
    timestamp on EVERY call, whether or not it actually found anything
    new, so embedding it here meant this profile's rendered text could
    never be byte-identical across two calls -- silently defeating
    artifact_vault_common.register_version()'s no-op dedup guard for
    every single competitive brief (found live: a routine refresh run
    against all 153 tracked competitors, twice in a row with no real
    data change, still produced 153 new versions on the second run,
    exactly the version-bloat problem that guard exists to prevent).
    `last_evidence_date` stays -- it only advances when sync_from_
    ecosystem() actually appends real new evidence, so it's a legitimate
    content signal, not a live clock."""
    data = cic.load_competitor(slug)
    comp = data["competitor"]
    evidence = data["evidence"]

    lines = [
        f"# {comp.get('display_name', slug)} — Competitor Intelligence Profile",
        "",
        f"*Last evidence: {comp.get('last_evidence_date') or 'none recorded'} | "
        f"{len(evidence)} evidence record(s)*",
        "",
    ]
    if comp.get("competes_on"):
        lines += ["## Competes with Genius on", ", ".join(sorted(comp["competes_on"])), ""]
    if comp.get("positioning_summary"):
        lines += ["## Positioning", comp["positioning_summary"], ""]
    if comp.get("todds_pov"):
        lines += ["## Todd's POV", comp["todds_pov"], ""]

    vs_genius = comp.get("vs_genius") or {}
    if vs_genius.get("genius_advantages") or vs_genius.get("competitor_advantages"):
        lines.append("## Gap analysis vs. Genius")
        for adv in vs_genius.get("genius_advantages", []):
            lines.append(f"- **Genius wins:** {adv.get('point', '')}")
        for adv in vs_genius.get("competitor_advantages", []):
            lines.append(f"- **{comp.get('display_name', slug)} wins:** {adv.get('point', '')}")
        lines.append("")

    # RB-2026-09-28: category_battle_cards' RM-facing content (status,
    # posture, listen-for, discovery questions, red flags) used to render
    # here too (added RB-2026-09-25), duplicating -- and, for team-portal
    # viewers, leaking -- content that belongs solely to the dedicated
    # Battle Card artifact (battle_card.py / get_battle_card_view). Profile
    # stays the static reference document; Battle Card is the only place
    # category_battle_cards renders now.

    by_category: dict[str, list[dict]] = {}
    for e in evidence:
        by_category.setdefault(e.get("category", "other"), []).append(e)

    category_order = [
        "strength", "weakness", "pricing", "market_share", "reference_customer",
        "customer_win", "customer_loss", "positioning", "ecosystem_signal", "other",
    ]
    category_labels = {
        "strength": "Strengths", "weakness": "Weaknesses", "pricing": "Pricing signals",
        "market_share": "Market share data", "reference_customer": "Reference customers",
        "customer_win": "Customer wins", "customer_loss": "Customer losses",
        "positioning": "Positioning notes", "ecosystem_signal": "Ecosystem signals",
        "other": "Other evidence",
    }
    for cat in category_order:
        items = by_category.get(cat)
        if not items:
            continue
        lines.append(f"## {category_labels[cat]}")
        for e in items:
            date = e.get("event_at") or e.get("logged_at", "")[:10]
            if e.get("_source_title"):
                # RB-2026-09-28: account_reference_detector.link_references()
                # appends these -- a fact-free "this uploaded document
                # mentions this competitor by name" discoverability nudge for
                # TODD to review (its own docstring: "never invents a
                # specific commercial claim... extracting the real
                # structured facts stays a separate, reviewed action"). It
                # was never meant to be a citable fact, but rendering its raw
                # `summary` (up to 400 chars straight from whatever document
                # matched) did exactly that -- caught live: Todd's own
                # confidential Pollo Campero RFP/SOW pricing documents and
                # unrelated LinkedIn screenshot text surfaced verbatim on
                # PAR Technology's Competitor Profile, visible to every
                # teammate, because "PAR" (or another short alias) happened
                # to appear somewhere in those documents. Render the pointer
                # only, never the excerpt.
                lines.append(f"- ({date}) An uploaded document mentions this competitor: *{e.get('source', 'unknown source')}* — ask Todd for details.")
            else:
                lines.append(f"- ({date}) {e.get('summary', '')} — *{e.get('source', 'unknown source')}*")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def generate_profile(name: str) -> dict:
    """Full pipeline: resolve/create, sync, render. The one call the API
    and chat tool use end to end.

    RB-DEFECT-071 (2026-09-11): `already_tracked` lets a caller distinguish
    "this already existed" from "this was newly created" -- the matching
    logic itself was already correct (resolve_competitor()'s exact
    case-insensitive name/alias match), this just makes it visible in the
    contract. A sync-stage failure is raised as a CompetitorCreationError
    (shell_created=True, registered=True) rather than propagating a bare
    exception -- ensure_competitor() having already succeeded means a retry
    of this whole call is always safe (register_competitor() is
    idempotent), which the caller should be told rather than left guessing."""
    slug, already_tracked = ensure_competitor(name)
    try:
        sync_result = sync_from_ecosystem(slug)
    except Exception as exc:  # noqa: BLE001
        raise CompetitorCreationError(
            "sync", slug, shell_created=True, registered=True, cause=exc,
        ) from exc
    markdown = render_competitor_profile(slug)
    data = cic.load_competitor(slug)
    return {
        "ok": True,
        "competitor_slug": slug,
        "already_tracked": already_tracked,
        "competitor": data["competitor"],
        "evidence_count": len(data["evidence"]),
        "sync_result": sync_result,
        "markdown": markdown,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _smoke() -> bool:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cic.ROOT = Path(td)
        slug, _already_tracked = ensure_competitor("Smoke Test Vendor")
        add_competitive_note(slug, "Test note", category="strength")
        md = render_competitor_profile(slug)
        assert "Smoke Test Vendor" in md
        assert "Test note" in md
    print("smoke OK")
    return True


def main() -> None:
    p = argparse.ArgumentParser(description="Competitor Intelligence CLI.")
    p.add_argument("--smoke", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("resolve")
    r.add_argument("name")

    s = sub.add_parser("sync")
    s.add_argument("slug")

    sub.add_parser("sync-all")

    g = sub.add_parser("generate")
    g.add_argument("name")

    n = sub.add_parser("note")
    n.add_argument("slug")
    n.add_argument("text")
    n.add_argument("--category", default="other")

    args = p.parse_args()

    if args.smoke:
        _smoke()
        return
    if args.cmd == "resolve":
        print(resolve_competitor(args.name))
    elif args.cmd == "sync":
        print(sync_from_ecosystem(args.slug))
    elif args.cmd == "sync-all":
        print(json.dumps(sync_all_tracked_competitors(), indent=2))
    elif args.cmd == "generate":
        result = generate_profile(args.name)
        print(result["markdown"])
    elif args.cmd == "note":
        print(add_competitive_note(args.slug, args.text, category=args.category))
    else:
        p.print_help()


if __name__ == "__main__":
    main()
