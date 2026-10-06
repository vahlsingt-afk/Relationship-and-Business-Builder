"""
competitor_intelligence_common.py — storage primitives for Competitor
Intelligence.

RB-2026-08-28: real gap found while reviewing RB's artifact inventory —
ecosystem_intelligence.json's vendor entities are thin graph nodes (id,
name, category, sources) that model WHICH brands use a vendor, not
competitive intelligence ABOUT the vendor (positioning, strengths/
weaknesses, pricing signals, reference wins/losses, Todd's own POV). There
was no durable home for exactly this kind of content, confirmed live the
same day a real competitive-analysis conversation (PAR/Genius/Oracle/Toast)
had nowhere structured to land except a generic account_intelligence/ doc.

Deliberately mirrors account_research_common.py's shape (same JSON/JSONL
primitives, same account_dir()/create_account_shell() pattern, same
philosophy: cheap to create, no authorization gate, per-entity dossier)
rather than sharing code with it — same reasoning that module gives for not
sharing with blue_sheets/_engine/common.py: these are separately-governed
systems, and duplication keeps each one free to evolve its own schema.

Fed two ways, matching Todd's explicit direction (2026-08-28):
  1. Intelligence gathering process — competitor_intelligence.sync_from_ecosystem()
     pulls signals from ecosystem_intelligence.json already tagged to this
     vendor's entity_id, mechanically, never LLM-interpreted.
  2. User input — competitor_intelligence.add_competitive_note() appends a
     structured, human-supplied evidence entry.

Both land in the same evidence.jsonl, append-only, same discipline as
master_account_plans' evidence ledger and Blue Sheets' evidence trail.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import datetime
import logging
import os
import re
import tempfile
from pathlib import Path

import slug_safety

_LOG = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent / "competitor_intelligence"  # .../system/competitor_intelligence


def competitor_dir(slug: str, *, create: bool = False) -> Path:
    # RB-SECURITY-2026-09-05: defense-in-depth -- every real caller today
    # (ensure_competitor -> resolve_competitor) already derives slug via
    # _slugify() before reaching here, but this is the single choke point
    # every competitor-path lookup in this engine goes through, so it gets
    # the same guard rather than depending on every future caller
    # remembering to slugify first.
    slug_safety.assert_safe_slug(slug, label="competitor_slug")
    d = ROOT / "competitors" / slug
    if not d.is_dir():
        if create:
            d.mkdir(parents=True, exist_ok=True)
            return d
        raise FileNotFoundError(f"No competitor intelligence folder for slug '{slug}' at {d}")
    return d


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_jsonl(path: Path):
    if not path.exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append_jsonl(path: Path, record) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False))
        f.write("\n")


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.date.today().isoformat()


VALID_EVIDENCE_CATEGORIES = {
    "positioning", "strength", "weakness", "pricing", "market_share",
    "reference_customer", "customer_win", "customer_loss", "pov",
    "ecosystem_signal", "other",
    # 2026-10-02, top-10-per-category competitor Hunter cycle:
    "features", "customer_feedback_testimonials", "value_statement",
}

# Genius's own product lines, per Global Payments' own positioning (POS,
# payments processing, back-office/RTI heritage, kitchen/drive-thru
# orchestration, loyalty/engagement, digital menu boards) -- a competitor
# is tagged with whichever of these it directly competes on, since most
# restaurant-tech vendors only overlap Genius on a subset (e.g. PAR Punchh
# competes on loyalty only; PAR Technology/Toast compete broadly across
# POS + payments + loyalty).
#
# "digital_menu_boards" (2026-09-28, renamed from "digital_ordering" --
# Todd: "genius does not have digital ordering - we have digital menu
# boards"). The mislabel predated this fix: the Genius Capability Library
# content already stored under the old key was itself genuinely DMB
# content (sourced from globalpayments.com/point-of-sale/enterprise/
# digital-menu-board), so this was a pure naming bug, not a data error --
# nothing needed re-researching, just renaming. Distinct from
# competitive_landscape.TECH_STACK_CATEGORIES' real, correctly-named
# "online_ordering" (from Technomic's workbook column, e.g. Olo) -- that
# is a genuinely different product category Genius does not claim here.
GENIUS_PRODUCT_LINES = {
    "pos", "payments", "back_office", "kitchen_drive_thru", "loyalty_engagement",
    "digital_menu_boards", "restaurant_os_platform",
}

# RB-DEFECT-071 (2026-09-11): the FSTEC bulk-import batch manually excluded
# Genius/Global Payments from the 142-name request -- there was no code-
# level guard against tracking your own company as a competitor, purely
# conversational discipline by whoever built that request. Legal suffixes
# stripped first (a plain non-alphanumeric strip alone would leave "Global
# Payments Inc." as "globalpaymentsinc", which wouldn't match
# "globalpayments") so "Global Payments", "Global Payments Inc.",
# "GlobalPayments Corporation" all match the same base name.
OWN_COMPANY_NAMES = {"genius", "geniusforrestaurants", "globalpayments"}
_LEGAL_SUFFIX_RE = re.compile(r"\b(inc|incorporated|llc|corp|corporation|co|ltd|plc)\b")


def is_own_company(name: str) -> bool:
    stripped = _LEGAL_SUFFIX_RE.sub("", (name or "").lower())
    normalized = re.sub(r"[^a-z0-9]+", "", stripped)
    return normalized in OWN_COMPANY_NAMES

# RB-2026-09-01: a competitor can compete differently across multiple
# tech-stack categories (e.g. PAR competes on platform, POS, AND loyalty,
# each with its own posture/wedge) -- competes_on/positioning_summary/
# todds_pov/vs_genius above stay vendor-level (unchanged, still the
# fallback when no category-specific card exists), and category_battle_cards
# holds the RM-facing, per-category content on top of that. Governed status
# lifecycle for each entry, matching Todd's 2026-09-01 design request.
CATEGORY_BATTLE_CARD_STATUSES = {
    "draft", "needs_research", "source_backed", "todd_review_needed",
    "todd_validated", "stale", "contradicted", "do_not_use",
}


def _empty_category_battle_card() -> dict:
    return {
        "status": "draft",
        "confidence_pct": None,  # int 0-100, distinct from evidence-level free-string confidence
        "rm_plain_english_posture": "",
        "listen_for": [],
        "discovery_questions": [],
        "when_to_bring_todd_in": "",
        "red_flags": [],
        "evidence_ids": [],
        "source_urls": [],
        "last_validated": None,
        "updated_at": now_iso(),
    }


# Real acronyms in the category vocabulary that plain .title() mangles
# (e.g. "pos" -> "Pos", "ai_solution_1" -> "Ai Solution 1") -- a small,
# hand-reviewed override list, not a general acronym detector. Moved here
# from battle_card.py (2026-09-25) so competitor_intelligence.py/
# value_wedge.py can share the same category display formatting instead
# of each re-deriving it. "os" added 2026-09-25 -- caught live rendering
# GENIUS_PRODUCT_LINES' "restaurant_os_platform" as "Restaurant Os
# Platform" instead of "Restaurant OS Platform".
_ACRONYM_WORDS = {"pos", "ai", "kds", "bi", "os"}


def category_display_name(category: str) -> str:
    words = category.replace("_", " ").split()
    return " ".join(w.upper() if w.lower() in _ACRONYM_WORDS else w.capitalize() for w in words)


def render_category_battle_card_body(card: dict) -> list[str]:
    """Pure markdown-line rendering of one category_battle_cards[category]
    entry's RM-facing content -- status+confidence_pct, rm_plain_english_
    posture, listen_for, discovery_questions, when_to_bring_todd_in,
    red_flags, last_validated. Extracted from battle_card.py's
    _render_competitor_card() (2026-09-07), which rendered this inline;
    shared here (2026-09-25) so competitor_intelligence.py's Competitive
    Brief and value_wedge.py's Value Wedge render the exact same content
    the same way instead of drifting apart.

    Caller supplies its own heading and its own "nothing on file yet"
    placeholder text (wording differs per caller) -- this function never
    emits either; it returns [] when the card has nothing renderable,
    which is a valid, non-error state, not a placeholder itself."""
    lines: list[str] = []
    if card.get("status"):
        conf = f" (confidence: {card['confidence_pct']}%)" if card.get("confidence_pct") is not None else ""
        lines.append(f"\n**Status:** {card['status']}{conf}")
    if card.get("rm_plain_english_posture"):
        lines.append(f"\n**RM posture:** {card['rm_plain_english_posture']}")
    if card.get("listen_for"):
        lines.append("\n**Listen for:**")
        lines.extend(f"- {x}" for x in card["listen_for"])
    if card.get("discovery_questions"):
        lines.append("\n**Discovery questions:**")
        lines.extend(f"- {x}" for x in card["discovery_questions"])
    if card.get("when_to_bring_todd_in"):
        lines.append(f"\n**When to bring Todd in:** {card['when_to_bring_todd_in']}")
    if card.get("red_flags"):
        lines.append("\n**Red flags:**")
        lines.extend(f"- {x}" for x in card["red_flags"])
    if card.get("last_validated"):
        lines.append(f"\n*Last validated: {card['last_validated']}*")
    return lines


# Ecosystem Lookup Tool, Competitors tab (Phase B, 2026-09-25). Same
# field-level provenance shape as brand_profile_common.py's
# field()/unresearched_field() -- deliberately duplicated rather than
# imported (see this module's own docstring on why: separately-governed
# systems, duplication keeps each free to evolve its own schema). Each of
# the fields is a LIST of individually-dated, individually-sourced
# entries (a competitor's "recent news" or "vulnerabilities" are discrete
# dated facts, not one blob with a single as-of date) -- except `trends`,
# which reads as synthesized prose (parallel to positioning_summary above)
# rather than a list of discrete items.
#
# "vendor_claims" and "product_lineage" (RB-DEFECT-073, 2026-09-28): added
# alongside the original 6 so a vendor's own marketing/positioning
# statements have a labeled home distinct from `strengths` -- the whole
# point of the separation is that a vendor claim must never be silently
# promoted into an independently-verified strength just because it came
# through the same research pipeline. `product_lineage` (acquisitions,
# rebrands, prior names) is likewise its own list rather than overloaded
# onto `products` or `recent_news`.
#
# "features" and "customer_feedback_testimonials" (2026-10-02, top-10-per-
# category competitor Hunter cycle): `products` names what a vendor sells;
# `features` is the granular, per-capability claim level underneath it
# (e.g. "real-time kitchen display routing" as a discrete, independently
# dated finding, not folded into one product-name string). `key_customers`
# names who uses a product; `customer_feedback_testimonials` is what those
# customers (or independent reviewers) actually said about it -- a quote
# or review finding, never a vendor's own marketing copy relabeled as
# feedback (that stays `vendor_claims`).
EXTENDED_PROFILE_FIELDS = (
    "products", "strengths", "weaknesses", "vulnerabilities", "key_customers",
    "recent_news", "vendor_claims", "product_lineage", "features",
    "customer_feedback_testimonials",
)

# Scalar (single synthesized-prose value, not a list) extended-profile
# fields -- parallel to `trends`. "value_statement" (2026-10-02) is the
# vendor's own current how-they-pitch-themselves summary, distinct from
# `positioning_summary` (RBB's own synthesis of independent market
# positioning) the same way `vendor_claims` stays distinct from
# `strengths`.
EXTENDED_SCALAR_FIELDS = ("trends", "value_statement")


def extended_field(
    value,
    *,
    status: str = "confirmed",
    evidence_ids: list | None = None,
    confidence: str = "high",
    as_of: str | None = None,
    last_reviewed_by: str = "system:competitor_intelligence_common",
    source_url: str | None = None,
    finding_type: str | None = None,
    source_owner: str | None = None,
    source_type: str | None = None,
    published_at: str | None = None,
    deployment_scope: str | None = None,
    is_vendor_claim: bool | None = None,
    is_inference: bool | None = None,
    limitations_or_conflicts: str | None = None,
) -> dict:
    """RB-DEFECT-073 (2026-09-28): extended the original 7-key leaf shape
    with optional provenance kwargs (finding_type/source_owner/source_type/
    published_at/deployment_scope/is_vendor_claim/is_inference/limitations_
    or_conflicts) so a deep-research importer can record everything the
    defect's required schema calls for. All optional and omitted (not
    stored as null) when not passed -- every one of the real, pre-existing
    entries on disk lacks these keys entirely, and this must stay a valid,
    non-breaking shape for every existing reader."""
    f = {
        "value": value,
        "status": status,
        "evidence_ids": evidence_ids or [],
        "confidence": confidence,
        "as_of": as_of or today(),
        "scope": "competitor",
        "last_reviewed_by": last_reviewed_by,
    }
    if source_url:
        f["source_url"] = source_url
    if finding_type is not None:
        f["finding_type"] = finding_type
    if source_owner is not None:
        f["source_owner"] = source_owner
    if source_type is not None:
        f["source_type"] = source_type
    if published_at is not None:
        f["published_at"] = published_at
    if deployment_scope is not None:
        f["deployment_scope"] = deployment_scope
    if is_vendor_claim is not None:
        f["is_vendor_claim"] = is_vendor_claim
    if is_inference is not None:
        f["is_inference"] = is_inference
    if limitations_or_conflicts is not None:
        f["limitations_or_conflicts"] = limitations_or_conflicts
    return f


def _empty_competitor_json(slug: str, display_name: str, vendor_entity_id: str | None) -> dict:
    return {
        "competitor_id": f"comp-{slug}",
        "competitor_slug": slug,
        "display_name": display_name,
        "aliases": [],
        "vendor_entity_id": vendor_entity_id,  # link back to ecosystem_intelligence.json's vendor-<slug>, if resolved
        "primary_category": None,  # e.g. pos, payments, loyalty — from the linked vendor entity when known
        "competes_on": [],  # subset of GENIUS_PRODUCT_LINES this vendor directly competes with Genius on
        "positioning_summary": "",
        "todds_pov": "",
        "vs_genius": {
            "genius_advantages": [],      # each: {"point": str, "evidence_id": str|None}
            "competitor_advantages": [],  # each: {"point": str, "evidence_id": str|None}
        },
        "category_battle_cards": {},  # {category: _empty_category_battle_card()-shaped dict}
        # Each a list of extended_field()-shaped entries — empty for a
        # brand-new shell, filled in only through real deep-research
        # observations or a confirmed team submission (never fabricated).
        "products": [],
        "strengths": [],
        "weaknesses": [],
        "vulnerabilities": [],
        "key_customers": [],
        "recent_news": [],
        "vendor_claims": [],
        "product_lineage": [],
        "features": [],
        "customer_feedback_testimonials": [],
        "trends": {
            "value": None, "status": "not_yet_researched", "evidence_ids": [],
            "confidence": "unknown", "as_of": None, "scope": "competitor", "last_reviewed_by": None,
        },
        "value_statement": {
            "value": None, "status": "not_yet_researched", "evidence_ids": [],
            "confidence": "unknown", "as_of": None, "scope": "competitor", "last_reviewed_by": None,
        },
        "last_evidence_date": None,
        "last_synced_at": None,
        "template_version": "competitor-intelligence-v1",
        "updated_at": now_iso(),
    }


# Phase C: allowlist, not blocklist (same reasoning as
# team_tech_stack.py's _TECH_STACK_RELATIONSHIP_FIELDS). "weaknesses" and
# "vulnerabilities" are excluded from the shareable view alongside the
# pre-existing todds_pov/strategic_note caution -- these are Todd's own
# competitive read on a rival, the exact kind of content that shouldn't
# reach an outside party or even every teammate. "products"/"strengths"/
# "key_customers"/"recent_news"/"trends" are market facts, often already
# public, and pass through. "vendor_claims" (the vendor's own public
# marketing statements) and "product_lineage" (acquisitions/rebrands/prior
# names) are the same kind of already-public market fact -- added
# RB-DEFECT-073, 2026-09-28. "features", "customer_feedback_testimonials",
# and "value_statement" (2026-10-02) are likewise already-public market
# facts (granular capability claims, customer quotes/reviews, and the
# vendor's own public pitch) and pass through the same way.
_SHAREABLE_EXTENDED_FIELDS = (
    "products", "strengths", "key_customers", "recent_news", "trends",
    "vendor_claims", "product_lineage", "features",
    "customer_feedback_testimonials", "value_statement",
)


def get_extended_profile(competitor: dict) -> dict:
    """Read-time backward-compat: the real 153 pre-project competitor.json
    files on disk have NONE of the 6 new fields (confirmed by reading every
    one during scoping) -- callers must never KeyError on an old record.
    Backfills only genuinely-missing keys with the honest-empty default;
    never touches a field that already has real content, populated or not.
    Does not mutate `competitor` in place or write anything to disk -- pure
    read-time defaulting, same as brand_profile_common.get_profile()'s own
    read-through pattern."""
    result = dict(competitor)
    for f in EXTENDED_PROFILE_FIELDS:
        if f not in result:
            result[f] = []
    for f in EXTENDED_SCALAR_FIELDS:
        if f not in result:
            result[f] = {
                "value": None, "status": "not_yet_researched", "evidence_ids": [],
                "confidence": "unknown", "as_of": None, "scope": "competitor", "last_reviewed_by": None,
            }
    return result


# Top-level competitor.json keys safe to hand to a teammate or outside
# party -- excludes todds_pov (pre-existing, the same content the markdown
# path above already redacts) and vs_genius (Genius's own competitive
# positioning against this vendor -- internal by definition).
#
# latest_cos_commentary (added 2026-09-28, import_competitor_research_
# pack.py) is deliberately included: it's a vendor-market-level synthesis
# (headline/genius_exposure/competitive_timing/sales_response/risk_and_
# uncertainty/priority) written for the whole team, never Todd's personal
# POV or a specific account's portfolio -- unlike todds_pov/vs_genius, it
# belongs in front of every teammate by design. latest_research_pack_status
# (the sibling field, same module) stays OUT of this list -- it's research-
# ops bookkeeping (gap checklist, pack claim counts), not competitive
# intelligence a teammate needs to see.
_SHAREABLE_TOP_LEVEL_KEYS = (
    "competitor_id", "competitor_slug", "display_name", "aliases",
    "primary_category", "competes_on", "positioning_summary",
    "latest_cos_commentary",
)


def shareable_extended_view(competitor: dict) -> dict:
    """Structured-JSON counterpart to _redact_markdown_section's markdown
    redaction (team_tech_stack.py) -- for the new Phase B/C routes, which
    return JSON, not rendered markdown. Applies get_extended_profile()
    first so a legacy record missing the new fields never KeyErrors here."""
    full = get_extended_profile(competitor)
    view = {k: full[k] for k in _SHAREABLE_TOP_LEVEL_KEYS if k in full}
    for f in _SHAREABLE_EXTENDED_FIELDS:
        view[f] = full[f]
    return view


def create_competitor_shell(slug: str, display_name: str, vendor_entity_id: str | None = None) -> Path:
    """Creates a brand-new, empty Competitor Intelligence record. Deliberately
    cheap and ungated — same philosophy as Account Research's
    create_account_shell(): tracking a competitor should never require an
    authorization step the way creating a Blue Sheet does."""
    d = competitor_dir(slug, create=True)
    save_json(d / "competitor.json", _empty_competitor_json(slug, display_name, vendor_entity_id))
    (d / "evidence.jsonl").touch()
    return d


def load_competitor(slug: str) -> dict:
    d = competitor_dir(slug)
    return {
        "competitor": load_json(d / "competitor.json"),
        "evidence": load_jsonl(d / "evidence.jsonl"),
    }


def aliases_by_display_name() -> dict[str, list[str]]:
    """display_name -> real alias list, for every tracked competitor that
    has one recorded. Single source of truth (RB-2026-09-16) shared by
    entity_convergence_scan.py's daily scan (call once, reuse across every
    watched entity) and query_engine.py's ad-hoc lookups (via
    resolve_aliases() below) -- previously this lived only in
    entity_convergence_scan.py, which would have let the two drift.
    Best-effort per competitor; one unreadable competitor.json must not
    block the rest."""
    aliases_by_name: dict[str, list[str]] = {}
    try:
        reg = load_registry()
    except Exception:  # noqa: BLE001
        return aliases_by_name
    for entry in reg.get("registry", []):
        slug = entry.get("competitor_slug")
        if not slug:
            continue
        try:
            comp = load_competitor(slug)["competitor"]
        except Exception:  # noqa: BLE001
            continue
        display_name = comp.get("display_name")
        aliases = [str(a) for a in (comp.get("aliases") or []) if a]
        if display_name and aliases:
            aliases_by_name[display_name] = aliases
    return aliases_by_name


def resolve_aliases(name: str) -> list[str]:
    """Given ANY name a caller might use for a tracked competitor -- its
    canonical display_name OR one of its recorded aliases -- return every
    other known name for that same competitor, case-insensitively. For a
    single ad-hoc lookup (e.g. query_engine.py resolving one user-typed
    entity name against a full alias scan each call) rather than
    aliases_by_display_name()'s build-once-reuse-many shape. Returns []
    if `name` isn't a tracked competitor at all -- most entity names
    (Blue Sheet customers, generic text) aren't, and that must fail
    closed, not raise."""
    if not name:
        return []
    needle = name.strip().casefold()
    if not needle:
        return []
    for display_name, aliases in aliases_by_display_name().items():
        all_names = [display_name] + aliases
        if needle in {n.casefold() for n in all_names}:
            # dedupe case-insensitively, preserving order -- a competitor's
            # own aliases list sometimes redundantly repeats its
            # display_name (real data: NCR Voyix's aliases include "NCR
            # Voyix" itself), which would otherwise surface as a literal
            # duplicate entry here.
            seen: set[str] = set()
            result: list[str] = []
            for n in all_names:
                key = n.casefold()
                if key == needle or key in seen:
                    continue
                seen.add(key)
                result.append(n)
            return result
    return []


def competitive_relationship_class(profile: dict) -> str:
    """Classify a tracked vendor without treating every vendor as a rival."""
    competitor = profile.get("competitor") if "competitor" in profile else profile
    competitor = competitor or {}
    if competitor.get("competes_on"):
        return "product_line_competitor"
    if competitor.get("primary_category") or competitor.get("category_battle_cards"):
        return "adjacent_ecosystem_vendor"
    return "unclassified_vendor"


def registry_path() -> Path:
    return ROOT / "_portfolio" / "competitor_registry.json"


def save_json_atomic(path: Path, data) -> None:
    """Write via temp-file-plus-replace -- never exposes a partially written
    file to a concurrent reader, unlike save_json()'s plain truncating
    open(). RB-DEFECT-071 (2026-09-11): save_json()'s plain open(path, "w")
    is exactly how 8 concurrent createCompetitor calls corrupted
    competitor_registry.json -- a losing writer's buffered tail landed
    after the winning writer's complete document. Used for the registry
    specifically; per-competitor competitor.json writes have no shared-
    state race and stay on save_json()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _registry_lock_path() -> Path:
    p = ROOT / "_portfolio" / ".registry.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@contextlib.contextmanager
def _registry_lock():
    """Cross-process exclusive lock around the registry's read-modify-write
    critical section. RB-DEFECT-071: register_competitor() did this with no
    lock at all -- a classic lost-update race. fcntl.flock, not a
    threading.Lock, because the same registry file is also written by
    separate subprocesses (competitor_intelligence.py sync-all, run daily
    by morning_pipeline.py), not just concurrent requests inside one API
    server process."""
    lock_path = _registry_lock_path()
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _rebuild_registry_from_disk() -> dict:
    """Reconstruct the registry from the real competitor directories -- the
    recovery technique used manually for RB-DEFECT-071 (2026-09-11), now
    automatic. First recovers whatever's still parseable from the existing
    (corrupted) file via raw_decode (so real historical first_tracked_at
    dates survive, same as the incident's manual recovery), then fills in
    any competitor directory missing from that using its own competitor.json
    updated_at as a same-batch approximation."""
    known: dict[str, dict] = {}
    p = registry_path()
    if p.exists():
        try:
            raw = p.read_text(encoding="utf-8")
            recovered, _ = json.JSONDecoder().raw_decode(raw)
            for e in recovered.get("registry", []):
                slug = e.get("competitor_slug")
                if slug:
                    known[slug] = e
        except Exception:
            pass  # nothing recoverable -- fall through to a full disk rebuild

    competitors_dir = ROOT / "competitors"
    if competitors_dir.is_dir():
        for d in sorted(competitors_dir.iterdir()):
            if not d.is_dir() or d.name in known:
                continue
            comp_path = d / "competitor.json"
            if not comp_path.exists():
                continue
            try:
                data = json.loads(comp_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            ts = data.get("updated_at") or now_iso()
            known[d.name] = {
                "competitor_slug": d.name,
                "display_name": data.get("display_name") or d.name,
                "first_tracked_at": ts,
                "last_updated_at": ts,
            }

    return {"registry": sorted(known.values(), key=lambda e: e.get("first_tracked_at", ""))}


def load_registry() -> dict:
    p = registry_path()
    if not p.exists():
        return {"registry": []}
    try:
        return load_json(p)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        # RB-DEFECT-071 (2026-09-11): this exact exception, unhandled, is
        # what took listCompetitors down for a full incident -- every read
        # of a corrupted registry raised, permanently, until manually
        # recovered. Self-heal instead: reconstruct from the real
        # competitor directories on disk (source of truth) and persist the
        # repair so this doesn't re-trigger on every subsequent call.
        _LOG.warning("competitor_registry.json unreadable (%s) -- self-healing from disk", exc)
        rebuilt = _rebuild_registry_from_disk()
        try:
            save_registry(rebuilt)
        except Exception:
            pass  # best-effort persistence; the caller still gets a valid in-memory result
        return rebuilt


def save_registry(reg: dict) -> None:
    save_json_atomic(registry_path(), reg)


def register_competitor(slug: str, display_name: str) -> None:
    with _registry_lock():
        reg = load_registry()
        entries = reg.setdefault("registry", [])
        existing = next((e for e in entries if e.get("competitor_slug") == slug), None)
        if existing is None:
            entries.append({
                "competitor_slug": slug,
                "display_name": display_name,
                "first_tracked_at": now_iso(),
                "last_updated_at": now_iso(),
            })
        else:
            existing["last_updated_at"] = now_iso()
        save_registry(reg)


def review_queue_path() -> Path:
    return ROOT / "_portfolio" / "review_queue.json"


def load_review_queue() -> dict:
    p = review_queue_path()
    if not p.exists():
        return {"pending_reviews": []}
    return load_json(p)


def save_review_queue(queue: dict) -> None:
    save_json(review_queue_path(), queue)
