#!/usr/bin/env python3
"""
account_background_brief.py — persistent Account Background Brief lifecycle.

RB-2026-08-27. Implements Todd's canonical-artifact spec: an Account
Background Brief is a durable, versioned, retrievable intelligence product,
not a one-off document. "Documents are outputs. Intelligence is the durable
asset." (spec section 25.)

Canonical presentation decision (Todd, 2026-09-08): every Background Brief
uses the approved internal Del Taco Background Brief structure by default.
The caller never needs to opt into or name that template. The renderer must
remain internal-facing, suppress empty optional headings, distinguish fact
from reported intelligence and hypothesis, and never expose RBB/system
implementation details in the document body. Word exports preserve the
approved reference document's visual system.

Deliberately does NOT do autonomous live web research — RBB has no
web-search tool today, and building one carries the same fabrication risk
already found and fixed once this session (see
rb_ingestcontent_autopersist_fabrication_2026_08_27.md in memory). This
module retrieves and assesses what RBB already knows, identifies gaps as
discovery questions rather than auto-researching them, and persists
structured intelligence a human (or a disciplined, structured tool call —
never free-form auto-persist) actually supplies.

Corrected 2026-08-27, same day as the original build: an Account Background
Brief is a PRE-ENGAGEMENT document -- digging into a brand from public
information and RBB's own account research, to build a plan before
discovery even starts. A Blue Sheet is the plan used DURING an active
engagement. The brief is upstream of the Blue Sheet (it can feed facts into
one once an engagement starts, never the reverse) and must never require a
Blue Sheet to already exist. Originally built as a blue_sheets/ sub-feature
-- wrong call, corrected here: storage now lives in its own independent
system/account_research/ tree (system/scripts/account_research_common.py),
with no dependency on any blue_sheets/ account existing.

Architecture: extends two existing systems rather than building a third.
  - system/account_research/accounts/<slug>/ (account_research_common.py)
    — the per-account structured dossier: account.json field-objects,
    append-only evidence.jsonl, contradictions.json. Cheap to create --
    account_research_common.create_account_shell() has no explicit-
    authorization gate the way Blue Sheet creation does, because a
    pre-engagement research record is meant to be easy to start.
  - ecosystem_intelligence.py's brand entity graph — account/entity
    identity resolution (_resolve_brand_entity_id) and industry-level
    technology-relationship evidence (source_assertions/supersession),
    consulted here as read-only context. Also the general intelligence
    artifact registry (system/artifacts/registry.json, via
    intelligence_triage._load_artifact_registry) for real, already-built
    Micro Graph / account-dossier artifacts scoped to this account (e.g.
    McDonald's operator-topology data) — surfaced in the rendered brief so
    it shows what RBB already knows, not re-derived or duplicated.

New per-account files this module owns:
  - opportunity_hypotheses.json — unconfirmed potential fits. NEVER
    auto-promoted into account.json's opportunities[] (spec section 6/18 —
    "interesting account != opportunity" stays a human, qualification
    action).
  - discovery_questions.json — research gaps as actionable questions,
    grouped by topic.
  - briefs/current/Background_Brief.md + briefs/history/<ts>_....md +
    briefs/registry.json — the versioned document artifact, mirroring the
    render.py current/history pattern already used for the Blue Sheet xlsx
    workbook.

CLI:
    python3 system/scripts/account_background_brief.py resolve "Café Rio"
    python3 system/scripts/account_background_brief.py status cafe-rio
    python3 system/scripts/account_background_brief.py generate cafe-rio
    python3 system/scripts/account_background_brief.py --smoke
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import intelligence_triage as itriage  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402
import intelligence_index  # noqa: E402

VALID_VALIDATION_STATUS = {"unvalidated", "partially_validated", "validated", "invalidated"}
VALID_GAP_TYPE = {"missing", "stale", "conflicting"}
VALID_QUESTION_STATUS = {"open", "answered", "superseded"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# Step 1 — resolve account identity
# ---------------------------------------------------------------------------

def _deslug_candidates(name: str) -> list[str]:
    """Plausible real-name guesses for a possibly slug-shaped input, for
    matching against ecosystem_intelligence.json's brand entities.

    RB-DEFECT-2026-08-29: a first pass at this (title-casing the de-
    hyphenated slug alone) still failed for any brand whose real name has
    an apostrophe -- ei._norm_key() treats an apostrophe as a token
    separator, so "Churchs Chicken" (tokens {churchs, chicken}) does NOT
    token-subset-match "Church's Texas Chicken" (tokens {church, s, texas,
    chicken}) the way "Church's Chicken" (tokens {church, s, chicken})
    does. Confirmed live: this is the same failure shape for any
    possessive brand name (McDonald's, Wendy's, Denny's, Arby's, ...), not
    just this one account. Also try inserting an apostrophe before a
    trailing "s" on each word as a bounded, common-enough heuristic.
    """
    title_cased = name.replace("-", " ").title()
    candidates = [name, title_cased]
    words = title_cased.split()
    for i, w in enumerate(words):
        if len(w) > 2 and w.lower().endswith("s") and "'" not in w:
            variant = words.copy()
            variant[i] = w[:-1] + "'s"
            candidates.append(" ".join(variant))
    return candidates


def resolve_account(name: str) -> tuple[Optional[str], bool]:
    """Resolve a brand/account name to an Account Research slug.

    Returns (slug, exists). exists=False means no Account Research folder
    exists yet for this account -- caller decides whether to create one.
    Unlike Blue Sheets, this is deliberately NOT gated behind explicit
    authorization: a background brief is meant to be a cheap, pre-engagement
    starting point (see create_new_account below), not a qualified pursuit.
    This function never touches blue_sheets/ at all.
    """
    # Real bug found live 2026-08-27: ei._slug()/_norm_key() treat an
    # apostrophe as a word-separator, not something to strip -- "McDonald's"
    # -> "mcdonald-s" while "McDonalds" -> "mcdonalds". Two real,
    # un-merged account_research folders got created for the same brand
    # depending on which way the user (or the model) phrased it. Strip
    # apostrophes here first so both phrasings converge on one slug, same
    # fix already applied in intelligence_index.py/account_intelligence
    # matching -- this is the account-identity version of the same bug.
    normalized_name = name.replace("'", "").replace("’", "")
    slug = ei._slug(normalized_name)
    try:
        cpc.account_dir(slug)
        return slug, True
    except FileNotFoundError:
        pass

    # Try the portfolio registry's aliases before giving up -- a slug
    # mismatch (e.g. requested "Cafe Rio", real slug "cafe-rio-fresh-mexican")
    # shouldn't produce a false "no account" result.
    registry = cpc.load_registry()
    name_norm = ei._norm_key(normalized_name)
    for entry in registry.get("registry", []):
        candidates = [entry.get("account_id", "").removeprefix("acct-")] + [
            ei._norm_key(a.replace("'", "").replace("’", "")) for a in entry.get("aliases", [])
        ]
        if name_norm in candidates or ei._norm_key(entry.get("account_id", "")) == name_norm:
            real_slug = entry["account_id"].removeprefix("acct-")
            try:
                cpc.account_dir(real_slug)
                return real_slug, True
            except FileNotFoundError:
                return real_slug, False

    # RB-DEFECT-2026-08-29: the registry-alias check above only catches a
    # brand this function has already seen once before. A first-ever
    # request phrased differently from ecosystem_intelligence.json's
    # canonical name (a raw URL-shaped slug, a shorter/longer variant) fell
    # straight through to "doesn't exist" even when RB already had real,
    # persisted relationship data for the actual entity -- confirmed live,
    # "churchs-chicken" produced a brand-new blank account while
    # "Church's Texas Chicken" already had two real relationships on file.
    # Cross-check the ecosystem graph itself before giving up -- if the
    # canonical entity has an existing Account Research folder under its
    # real name, prefer that; if not, this is genuinely new (handled by the
    # caller via create_new_account, which does its own equivalent check).
    graph = ei._read_graph()
    for candidate in [normalized_name] + _deslug_candidates(name):
        entity_id, is_new = ei._resolve_brand_entity_id(candidate, graph)
        if not entity_id or is_new:
            continue
        match = next((e for e in graph.get("entities") or [] if e["id"] == entity_id), None)
        if not match or not match.get("name"):
            continue
        canonical_slug = ei._slug(match["name"].replace("'", "").replace("’", ""))
        try:
            cpc.account_dir(canonical_slug)
            return canonical_slug, True
        except FileNotFoundError:
            pass
        break

    return slug, False


def create_new_account(name: str) -> str:
    """Creates a brand-new, empty Account Research record and registers it
    in the portfolio registry. Call only after resolve_account() reports
    exists=False -- never silently re-creates an existing account.

    RB-DEFECT-2026-08-29: `name` here is often a URL-shaped account_slug the
    model guessed (e.g. "churchs-chicken"), not a real brand name -- the
    server route passes account_slug through unchanged whenever
    resolve_account() can't find an existing folder. Stored verbatim as
    display_name, that produced a visibly wrong brief title ("churchs-
    chicken Background Brief") AND a real substantive miss: generate_brief()
    independently re-resolves display_name against ecosystem_intelligence.json
    via _resolve_brand_entity_id(), and "churchs-chicken" (concatenated, no
    separator) doesn't token-match "Church's Texas Chicken" the way "Church's
    Chicken" does -- so two already-persisted real relationships (loyalty,
    POS) never made it into the brief, even though RB had them on file the
    whole time. Confirmed live. Fix: before creating a blank shell under
    whatever the caller passed, check whether that string (or a de-slugified
    guess) already resolves to a REAL, existing ecosystem entity -- if so,
    seed the new account with that entity's actual name, not raw slug text.
    """
    graph = ei._read_graph()
    resolved_name = name
    for candidate in [name] + _deslug_candidates(name):
        entity_id, is_new = ei._resolve_brand_entity_id(candidate, graph)
        if entity_id and not is_new:
            match = next((e for e in graph.get("entities") or [] if e["id"] == entity_id), None)
            if match and match.get("name"):
                resolved_name = match["name"]
            break

    slug = ei._slug(resolved_name.replace("'", "").replace("’", ""))
    cpc.create_pre_engagement_shell(slug, display_name=resolved_name)
    seed_starter_discovery_questions(slug)
    registry = cpc.load_registry()
    account_id = f"acct-{slug}"
    if not any(e.get("account_id") == account_id for e in registry.get("registry", [])):
        aliases = [name] if resolved_name == name else [name, resolved_name]
        registry.setdefault("registry", []).append({
            "account_id": account_id,
            "aliases": aliases,
            "owner": "",
            "status": "pre_engagement",
            "engagement_tier": "pre_engagement",
            "last_review_date": cpc.today(),
        })
        cpc.save_registry(registry)
    try:
        intelligence_index.register_document(
            resolved_name, "account_research_record", f"Account Research: {slug}",
            f"customers_prospects/accounts/{slug}", source_system="account_research",
            created_at=cpc.today(),
        )
    except Exception:  # noqa: BLE001 — indexing is best-effort, never blocks account creation
        pass
    return slug


# ---------------------------------------------------------------------------
# New record types: opportunity_hypotheses.json, discovery_questions.json
# ---------------------------------------------------------------------------

def _hypotheses_path(slug: str) -> Path:
    return cpc.account_dir(slug) / "opportunity_hypotheses.json"


def _questions_path(slug: str) -> Path:
    return cpc.account_dir(slug) / "discovery_questions.json"


def load_hypotheses(slug: str) -> list[dict]:
    p = _hypotheses_path(slug)
    if not p.exists():
        return []
    return cpc.load_json(p).get("hypotheses", [])


def load_discovery_questions(slug: str) -> list[dict]:
    p = _questions_path(slug)
    if not p.exists():
        return []
    return cpc.load_json(p).get("questions", [])


def save_hypotheses(slug: str, hypotheses: list[dict]) -> None:
    cpc.save_json(_hypotheses_path(slug), {"account_id": f"acct-{slug}", "hypotheses": hypotheses})


def save_discovery_questions(slug: str, questions: list[dict]) -> None:
    cpc.save_json(_questions_path(slug), {"account_id": f"acct-{slug}", "questions": questions})


def add_hypothesis(
    slug: str, *, hypothesis_id: str, name: str, description: str,
    supporting_evidence_ids: list[str], contradictory_evidence_ids: Optional[list[str]] = None,
    likely_buyer: Optional[str] = None, relevant_capability: Optional[str] = None,
    discovery_question_ids: Optional[list[str]] = None,
    validation_status: str = "unvalidated",
) -> dict:
    if validation_status not in VALID_VALIDATION_STATUS:
        raise ValueError(f"invalid validation_status: {validation_status}")
    record = {
        "hypothesis_id": hypothesis_id, "name": name, "description": description,
        "supporting_evidence_ids": supporting_evidence_ids,
        "contradictory_evidence_ids": contradictory_evidence_ids or [],
        "likely_buyer": likely_buyer, "relevant_capability": relevant_capability,
        "discovery_question_ids": discovery_question_ids or [],
        "validation_status": validation_status, "as_of": _today(),
    }
    hyps = load_hypotheses(slug)
    hyps = [h for h in hyps if h.get("hypothesis_id") != hypothesis_id]
    hyps.append(record)
    save_hypotheses(slug, hyps)
    return record


def add_discovery_question(
    slug: str, *, question_id: str, topic: str, question: str, gap_type: str,
    related_fact_path: Optional[str] = None, importance: str = "medium",
    status: str = "open",
) -> dict:
    if gap_type not in VALID_GAP_TYPE:
        raise ValueError(f"invalid gap_type: {gap_type}")
    if status not in VALID_QUESTION_STATUS:
        raise ValueError(f"invalid question status: {status}")
    record = {
        "question_id": question_id, "topic": topic, "question": question,
        "gap_type": gap_type, "related_fact_path": related_fact_path,
        "importance": importance, "status": status, "as_of": _today(),
    }
    qs = load_discovery_questions(slug)
    qs = [q for q in qs if q.get("question_id") != question_id]
    qs.append(record)
    save_discovery_questions(slug, qs)
    return record


# RB-DEFECT-2026-08-29: a brand-new account rendered a Background Brief with
# no "## Key Discovery Questions" section at all -- Todd: "This is not the
# canonical standard for the background brief." The canonical reference
# (Cafe Rio, seeded from Todd's own real handoff document -- see
# rb_account_background_brief_closeout_2026_08_27 memory) has 29 real
# discovery questions across 5 categories, and the render function ALREADY
# correctly renders them; a brand-new account's discovery_questions.json is
# just empty, so the whole section is silently suppressed. RB's own KB
# instructions already promise this: "An empty, honestly-labeled brief with
# real Discovery Questions is correct behavior for a brand RBB knows little
# about" -- but nothing ever generated the questions half of that promise.
# These are safe to auto-generate (unlike opportunity hypotheses, which
# require actual judgment about a SPECIFIC account's likely buyer/fit and
# would risk fabrication if templated blind): a question asserts nothing
# about the account, so genuinely generic, universal pre-engagement
# questions carry no fabrication risk. Categories mirror Cafe Rio's own
# real structure (Business/Operating Priorities, Loyalty and Digital,
# Payments, Restaurant Technology, Decision Process).
_STARTER_DISCOVERY_QUESTIONS: list[tuple[str, str]] = [
    ("Business and Operating Priorities", "What are current leadership's top strategic priorities?"),
    ("Business and Operating Priorities", "What operating metrics (comps, traffic, margin) are under the most pressure right now?"),
    ("Business and Operating Priorities", "Is there an active turnaround, growth push, or ownership/leadership change underway?"),
    ("Business and Operating Priorities", "What is driving any current technology or vendor evaluation, if one exists?"),
    ("Loyalty and Digital", "What loyalty platform is currently in use, if any?"),
    ("Loyalty and Digital", "What percentage of transactions come through loyalty or digital channels?"),
    ("Loyalty and Digital", "What are the current app, online-ordering, and delivery platforms?"),
    ("Loyalty and Digital", "Who owns digital engagement and customer data internally?"),
    ("Payments", "Who processes in-store and digital payments today?"),
    ("Payments", "Is there a single payments gateway/acquirer, or a fragmented setup across channels?"),
    ("Payments", "When do the current payments agreements renew?"),
    ("Payments", "Are there known pain points (fees, chargebacks, fraud, reliability)?"),
    ("Restaurant Technology", "What POS system is currently deployed?"),
    ("Restaurant Technology", "What hardware is installed, and is there a known refresh or support deadline?"),
    ("Restaurant Technology", "What systems handle KDS, back office, inventory, and labor management?"),
    ("Restaurant Technology", "Are there known integration or data-quality issues across systems?"),
    ("Decision Process", "Who owns technology strategy and vendor decisions?"),
    ("Decision Process", "Who owns loyalty, payments, and digital commerce internally?"),
    ("Decision Process", "Is there an existing Global Payments/Genius relationship or contact?"),
    ("Decision Process", "What are the next known renewal, budgeting, or vendor-review dates?"),
]


def seed_starter_discovery_questions(slug: str) -> int:
    """Populates a brand-new account's discovery_questions.json with the
    generic starter set above. Idempotent by question_id -- safe to call
    more than once, never duplicates. Returns the number of questions
    written. Never overwrites an existing, human-curated set (only called
    from create_new_account, on a genuinely just-created shell)."""
    for i, (topic, question) in enumerate(_STARTER_DISCOVERY_QUESTIONS, start=1):
        add_discovery_question(
            slug, question_id=f"dq-starter-{i:03d}", topic=topic, question=question,
            gap_type="missing", importance="medium", status="open",
        )
    return len(_STARTER_DISCOVERY_QUESTIONS)


# ---------------------------------------------------------------------------
# Step 2 — retrieve existing intelligence
# ---------------------------------------------------------------------------

def find_related_artifacts(display_name: str) -> list[dict]:
    """Match the general intelligence artifact registry (micro graphs,
    account dossiers -- system/artifacts/registry.json) against this
    account's name, reusing the same word-boundary-safe term matching as
    intelligence_triage's live triage path rather than re-deriving it (a
    short term like "par" or "mcd" must not substring-match unrelated text
    -- see RB-2026-08-25's mutation-engine word-boundary defect). Only
    'active' artifacts are surfaced in the brief; 'stub'/'building' entries
    are real but have no retrievable content yet, so they're skipped here
    rather than rendered as an empty section."""
    registry = itriage._load_artifact_registry()
    name_lower = display_name.lower()
    tokens = set(itriage._tokens(display_name))
    matches: list[dict] = []
    for art in registry.get("artifacts") or []:
        if art.get("status") != "active":
            continue
        terms = [str(t).lower() for t in (art.get("entity_aliases") or [])]
        if art.get("entity"):
            terms.append(str(art["entity"]).lower())
        if any(itriage._term_in_text(t, name_lower, tokens) for t in terms if t):
            matches.append(art)
    return matches


def _micro_graph_topology_summary(art: dict) -> Optional[dict]:
    """Same derivation as server.py's getArtifact endpoint for active
    micro_graph artifacts -- kept here as a second consumer of the same
    index.json rather than calling the API, since this module already runs
    file-local (see resolve_account/retrieve_existing_intelligence)."""
    index_path_rel = art.get("index_path")
    if not index_path_rel:
        return None
    index_path = core.PROJECT_DIR / index_path_rel
    if not index_path.exists():
        return None
    try:
        idx = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    counts = idx.get("counts") or {}
    tiers = counts.get("operator_tiers") or {}
    tmpl = (idx.get("retrieval_answer_templates") or {}).get("operator_distribution") or ""
    answer = tmpl.format(
        operator_entities_with_stores=counts.get("operator_entities_with_stores", 0),
        stores_with_operator_entity=counts.get("stores_with_operator_entity", 0),
        enterprise_25_plus=tiers.get("enterprise_25_plus", 0),
        mid_tier_5_to_24=tiers.get("mid_tier_5_to_24", 0),
        single_digit_1_to_4=tiers.get("single_digit_1_to_4", 0),
    ) if tmpl else None
    return {
        "answer": answer,
        "counts": counts,
        "top_operator_entities": (idx.get("top_operator_entities") or [])[:10],
        "top_states": (idx.get("state_distribution") or [])[:10],
    }


ACCOUNT_INTELLIGENCE_DIR = core.SYSTEM_DIR / "account_intelligence"


def find_account_intelligence_docs(display_name: str) -> list[dict]:
    """Real gap found 2026-08-27: system/account_intelligence/ is a whole,
    pre-existing corpus of Todd's own hand-authored account research
    (strategic account plans, executive summaries, call-intelligence
    writeups) that this module never looked at -- a McDonald's background
    brief was rendering with none of it, because nothing here ever scanned
    this directory. Matches by filename token (YYYY-MM-DD-<slug-ish-name>
    .md convention, confirmed against real files), word-boundary-safe via
    the same itriage._term_in_text used for the artifact registry match
    above -- a short brand token must not substring-match an unrelated
    filename. Returns matches sorted newest-first by the filename's date
    prefix. Markdown files only -- .docx originals are left alone (no
    reliable stdlib text extraction here, and every .md already has a
    .docx counterpart per real production files, e.g. McDonald's)."""
    if not ACCOUNT_INTELLIGENCE_DIR.is_dir():
        return []
    # Real filenames drop apostrophes entirely ("mcdonalds", not "mcdonald's"
    # or "mcdonald-s") -- itriage._tokens() keeps the apostrophe inline, so
    # without this normalization "McDonald's" never matches its own real
    # filenames (confirmed live 2026-08-27, the same failure class as the
    # earlier diacritic bug, different punctuation).
    normalized_name = display_name.replace("'", "").replace("’", "")
    name_tokens = set(itriage._tokens(normalized_name))
    if not name_tokens:
        return []
    matches: list[dict] = []
    for path in ACCOUNT_INTELLIGENCE_DIR.glob("*.md"):
        stem_lower = path.stem.lower()
        if any(itriage._term_in_text(t, stem_lower, set(itriage._tokens(path.stem))) for t in name_tokens):
            date_prefix = path.stem[:10]
            matches.append({
                "filename": path.name,
                "path": str(path.relative_to(core.PROJECT_DIR)),
                "date": date_prefix if len(date_prefix) == 10 and date_prefix[4] == "-" else None,
                "size_bytes": path.stat().st_size,
                "is_executive_summary": "executive-summary" in stem_lower or "exec-summary" in stem_lower,
            })
    matches.sort(key=lambda m: m["date"] or "", reverse=True)
    return matches


def retrieve_existing_intelligence(slug: str) -> dict:
    """Gather everything RBB already knows about this account before
    generating (or regenerating) a brief. Never triggers new research."""
    dossier = cpc.load_account(slug)
    hypotheses = load_hypotheses(slug)
    questions = load_discovery_questions(slug)
    prior_brief = get_current_brief_version(slug)

    entity_relationships: list[dict] = []
    try:
        graph = ei._read_graph()
        display_name = dossier["account"].get("display_name", slug)
        entity_id, _is_new = ei._resolve_brand_entity_id(display_name, graph)
        if entity_id:
            entities_by_id = {e["id"]: e for e in graph.get("entities") or []}
            # RB-DEFECT-2026-08-29: this list was computed but never actually
            # reached the rendered brief -- render_technology_environment_
            # section only ever read account.technology_stack (a separate,
            # persisted field nothing ever populates from the ecosystem
            # graph), so a real, active relationship here (confirmed live:
            # Church's Texas Chicken had three -- loyalty/PAR, POS/Qu,
            # payments/Worldpay) still rendered an empty Technology
            # Environment table. Attach the vendor's real display name here
            # so the renderer doesn't need its own copy of the graph.
            for r in graph.get("relationships", []):
                if r.get("from_entity_id") != entity_id and r.get("to_entity_id") != entity_id:
                    continue
                other_id = r["to_entity_id"] if r.get("from_entity_id") == entity_id else r["from_entity_id"]
                enriched = dict(r)
                enriched["_other_entity_name"] = (entities_by_id.get(other_id) or {}).get("name", other_id)
                entity_relationships.append(enriched)
    except Exception:  # noqa: BLE001 — best-effort context, never blocks retrieval
        pass

    related_artifacts: list[dict] = []
    try:
        related_artifacts = find_related_artifacts(dossier["account"].get("display_name", slug))
    except Exception:  # noqa: BLE001 — best-effort context, never blocks retrieval
        pass

    account_intelligence_docs: list[dict] = []
    try:
        account_intelligence_docs = find_account_intelligence_docs(dossier["account"].get("display_name", slug))
    except Exception:  # noqa: BLE001 — best-effort context, never blocks retrieval
        pass

    return {
        "dossier": dossier,
        "opportunity_hypotheses": hypotheses,
        "discovery_questions": questions,
        "prior_brief_version": prior_brief,
        "ecosystem_relationships": entity_relationships,
        "related_artifacts": related_artifacts,
        "account_intelligence_docs": account_intelligence_docs,
    }


# ---------------------------------------------------------------------------
# Step 3 — assess freshness
# ---------------------------------------------------------------------------

def _fact_type_for_path(path: str) -> str:
    """Best-effort map from a field-object's location to a
    FACT_TYPE_CADENCE_DAYS key. Falls back to the default cadence for
    anything unmapped rather than guessing wrong."""
    mapping = {
        "brand_profile.founded": "founding_year",
        "brand_profile.headquarters": "headquarters",
        "brand_profile.ownership": "ownership",
        "account.opportunities": "opportunity_stage",
    }
    for prefix, fact_type in mapping.items():
        if path.startswith(prefix):
            return fact_type
    if "technology_stack" in path:
        return "pos_platform"
    if "buying_influences" in path:
        return "buying_influence_role"
    return "strategic_narrative"


def _find_structural_tech_stack_conflicts(ecosystem_relationships: list[dict]) -> list[dict]:
    """RB-2026-09-19: contradictions.json (system/RBB_STRATEGIC_ASSESSMENT_
    2026-09-19.md, Finding 4) has never had anything write to it -- every
    account's file is permanently seeded empty by customers_prospects_
    common.py, so this brief's own "Conflicting information requiring
    reconciliation" section could never contain anything, no matter how
    real the underlying conflict was.

    Minimal, highest-confidence fix: read the EXPLICIT requires_confirmation
    / status=="conflicting" / conflicts_with fields ecosystem_intelligence.
    check_relationship_conflict() already writes onto a relationship the
    moment it detects a real rival claim for the same brand+category -- the
    exact, already-proven signal, not a re-derivation. (An earlier version
    of this function re-counted rival "live-status" claims per category
    instead; that was wrong two ways: it would false-positive on two
    legitimately-competing "evaluating" candidates, which
    check_relationship_conflict()'s own docstring explicitly says is NOT a
    conflict, and it would MISS a real flagged conflict, because the
    flagged relationship's own status becomes the literal string
    "conflicting", which isn't a member of LIVE_CLAIM_STATUSES at all.)

    Deliberately does NOT attempt the assessment's other example (cross-
    referencing free-text earnings-call mentions or evidence.jsonl excerpts
    against brand_profile.json) -- that's NLP-shaped and false-positive-
    prone; this only surfaces what the graph's own structured data has
    already unambiguously flagged, using data retrieve_existing_intelligence()
    already fetches (ecosystem_relationships), no new I/O."""
    by_id = {r.get("id"): r for r in ecosystem_relationships if r.get("id")}
    conflicts: list[dict] = []
    for r in ecosystem_relationships:
        if r.get("relationship_type") != "uses_vendor_for_category":
            continue
        if not (r.get("requires_confirmation") or r.get("status") == "conflicting"):
            continue
        category = r.get("category") or "unknown"
        rival = by_id.get(r.get("conflicts_with"))
        claim_a_name = r.get("_other_entity_name") or r.get("to_entity_id") or "unknown"
        claim_b_name = (rival.get("_other_entity_name") if rival else None) or r.get("conflicts_with") or "unknown"
        conflicts.append({
            "field": f"technology_stack.{category}",
            "claim_a": {"value": claim_a_name, "status": r.get("status")},
            "claim_b": {"value": claim_b_name, "status": rival.get("status") if rival else None},
            "note": (
                f"{claim_a_name}'s {category} claim was flagged as conflicting with an existing "
                f"{claim_b_name} claim in ecosystem_intelligence.json and never reconciled."
            ),
        })
    return conflicts


def assess_freshness(intelligence: dict) -> dict:
    """Buckets every field-object found in the dossier into
    reliable/stale/missing, using FACT_TYPE_CADENCE_DAYS. "conflicting"
    combines contradictions.json's explicitly-flagged entries (a manual/
    future write path -- still empty in practice as of 2026-09-19) with
    live structural tech-stack conflicts detected fresh from the ecosystem
    graph each time (see _find_structural_tech_stack_conflicts) -- neither
    is a freshness question in the reliable/stale/missing sense, both are
    already explicitly flagged as needing reconciliation."""
    reliable: list[dict] = []
    stale: list[dict] = []
    missing: list[dict] = []

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            # technology_stack[] rows use `current_state` instead of `value`
            # -- a deliberate, pre-existing convention (confirmed against
            # real Pollo Campero data) that keeps their authorization-style
            # `status` vocabulary (Verify/Confirmed/Open/...) out of
            # blue_sheets/_engine/validate.py's fact-level status check,
            # which only fires when `value` is present. Recognize both, or
            # every technology_stack row silently never gets a freshness
            # assessment at all.
            has_value_key = "value" in node or "current_state" in node
            if has_value_key and {"status", "as_of"}.issubset(node.keys()):
                fact_type = _fact_type_for_path(path)
                effective_value = node.get("value", node.get("current_state"))
                entry = {"path": path, "fact_type": fact_type, "value": effective_value, "as_of": node.get("as_of")}
                is_missing = (
                    node.get("status") in ("unknown", "Open")
                    or node.get("vendor") == "unknown"
                    or effective_value in (None, "", "unknown")
                )
                if is_missing:
                    missing.append(entry)
                elif ei.fact_is_stale(fact_type, node.get("as_of")):
                    stale.append(entry)
                else:
                    reliable.append(entry)
                return
            for k, v in node.items():
                _walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{path}[{i}]")

    _walk(intelligence["dossier"]["account"], "account")
    _walk(intelligence["dossier"]["brand_profile"], "brand_profile")

    stored_conflicts = (intelligence["dossier"].get("contradictions") or {}).get("contradictions", [])
    try:
        structural_conflicts = _find_structural_tech_stack_conflicts(intelligence.get("ecosystem_relationships") or [])
    except Exception:  # noqa: BLE001 -- best-effort, never blocks freshness assessment
        structural_conflicts = []

    return {
        "reliable": reliable, "stale": stale, "missing": missing,
        "conflicting": stored_conflicts + structural_conflicts,
    }


# ---------------------------------------------------------------------------
# Step 4 — render the canonical Background Brief structure
# ---------------------------------------------------------------------------

def _fmt_field(obj: Optional[dict], default: str = "Unknown") -> str:
    if not obj or obj.get("value") in (None, "", "unknown"):
        return default
    return str(obj["value"])


def render_brand_profile_section(brand: dict) -> list[str]:
    """The '## Brand Profile' bullet block -- structural facts (founded,
    headquarters, segment, ...), never free-text strategy. Extracted so
    system/scripts/team_tech_stack.py's team-safe brief can reuse the
    exact same rendering instead of drifting a second copy."""
    lines = ["\n## Brand Profile"]
    for label, key in [("Founded", "founded"), ("Headquarters", "headquarters"), ("Segment", "segment"),
                        ("Footprint", "footprint"), ("Ownership", "ownership"), ("Geography", "geography"),
                        ("Differentiation", "differentiation"), ("Competitive set", "competitive_set")]:
        v = brand.get(key)
        if v is not None:
            lines.append(f"- {label}: {_fmt_field(v) if isinstance(v, dict) else v}")
    return lines


def render_leadership_section(account: dict) -> list[str]:
    """The '## Leadership' block -- named roles/titles, not commentary."""
    leadership = account.get("leadership") or {}
    if not leadership:
        return []
    confirmed = leadership.get("confirmed", [])
    reported = leadership.get("reported_unverified", [])
    if not confirmed and not reported:
        return []
    lines = ["\n## Leadership"]
    if confirmed:
        lines.append("\n### Confirmed")
        for p in confirmed:
            detail = p.get("relevance") or p.get("notes")
            text = f"- {p.get('name')} — {p.get('title')}"
            lines.append(f"{text}. {detail}" if detail else text)
    if reported:
        lines.append("\n### Reported / Requires Validation")
        for p in reported:
            detail = p.get("relevance") or p.get("notes")
            text = f"- {p.get('name')} — {p.get('title')}"
            lines.append(f"{text}. {detail}" if detail else text)
    return lines


def _clean_executive_summary_doc(text: str) -> str:
    """Use a persisted executive-summary document as reader-facing content
    without exposing its filename/path or duplicating its H1 title."""
    rows = text.strip().splitlines()
    if rows and rows[0].startswith("# "):
        rows = rows[1:]
    return "\n".join(rows).strip()


def render_relationship_access_section(account: dict) -> list[str]:
    owners = account.get("owners") or []
    influences = account.get("buying_influences") or []
    if not owners and not influences:
        return []
    lines = ["\n## Relationship and Access Map"]
    for person in owners:
        lines.append(f"- {person.get('name')} — {person.get('role', 'account owner')}")
    for person in influences:
        detail = person.get("role") or person.get("title") or "buying influence"
        status = person.get("status") or person.get("influence_type")
        lines.append(f"- {person.get('name')} — {detail}" + (f" ({status})" if status else ""))
    return lines


def render_technology_environment_section(account: dict, ecosystem_relationships: list[dict] | None = None) -> list[str]:
    """The '## Technology Environment' table -- layer/vendor/status/
    confidence/as-of. Two real sources, merged: account.technology_stack
    (Todd's own persisted, hand-reviewed findings -- always wins for a
    layer both sources cover) and ecosystem_relationships (already-known
    vendor relationships from ecosystem_intelligence.json's macro graph --
    RB-DEFECT-2026-08-29: this second source existed and was already being
    computed by retrieve_existing_intelligence, but nothing here ever read
    it, so a brand with real, active graph relationships and zero manually-
    curated technology_stack rows still rendered an empty table)."""
    lines = [
        "\n## Technology Environment",
        "\n*\"Unknown\" means reliable evidence was not found. It does not mean the account lacks the system.*",
    ]
    def _layer_key(value: str) -> str:
        normalized = str(value or "").strip().lower().replace("_", " ")
        if "voice" in normalized and "ai" in normalized:
            return "voice ai"
        if "pos" in normalized or "unified commerce" in normalized:
            return "pos"
        if "loyalty" in normalized:
            return "loyalty"
        if "payment" in normalized:
            return "payments"
        return normalized

    tech = list(account.get("technology_stack", []))
    covered_layers = {_layer_key(row.get("layer", "")) for row in tech}
    for rel in ecosystem_relationships or []:
        if rel.get("status") != "active":
            continue
        layer = rel.get("category")
        if not layer or _layer_key(layer) in covered_layers:
            continue
        covered_layers.add(_layer_key(layer))
        tech.append({
            "layer": layer,
            "vendor": rel.get("_other_entity_name", "Unknown"),
            "status": rel.get("deployment_status") or "confirmed",
            "confidence": (rel.get("confidence") or {}).get("level", "unknown"),
            "as_of": (rel.get("updated_at") or "")[:10],
        })
    if tech:
        lines.append("\n| Layer | Vendor | Status | Confidence | As of |")
        lines.append("|---|---|---|---|---|")
        for row in tech:
            lines.append(f"| {row.get('layer')} | {row.get('vendor', 'Unknown')} | {row.get('status')} | {row.get('confidence')} | {row.get('as_of', '')} |")
    return lines


def render_related_artifacts_section(intel: dict) -> list[str]:
    """The '## Related Intelligence Artifacts' block -- other RBB
    artifacts (e.g. micro graphs) scoped to this account, with their own
    structural metadata. Not Todd's free-text commentary."""
    if not intel.get("related_artifacts"):
        return []
    lines = [
        "\n## Related Intelligence Artifacts",
        "\n*Other RBB intelligence artifacts scoped to this account, beyond this Account Research record itself.*",
    ]
    for art in intel["related_artifacts"]:
        lines.append(f"\n### {art.get('name', art.get('artifact_id'))}")
        lines.append(f"- Type: {art.get('artifact_type')} | Confidence: {art.get('confidence', 'unknown')} | As of: {art.get('freshness_date', 'unknown')}")
        if art.get("artifact_type") == "micro_graph":
            topo = _micro_graph_topology_summary(art)
            if art.get("store_count"):
                lines.append(f"- {art['store_count']:,} stores / {art.get('operator_entity_count', 0):,} operator entities mapped ({art.get('node_count', 0):,} nodes, {art.get('edge_count', 0):,} edges)")
            if topo and topo.get("answer"):
                lines.append(f"- {topo['answer']}")
            if topo and topo.get("top_states"):
                top_states_str = ", ".join(f"{s.get('state')} ({s.get('store_count')})" for s in topo["top_states"][:5] if s.get("state"))
                if top_states_str:
                    lines.append(f"- Top states by store count: {top_states_str}")
        scope = art.get("scope_domains") or []
        if scope:
            lines.append(f"- Scope: {', '.join(scope)}")
        queryable = art.get("queryable_via")
        if queryable:
            lines.append(f"- Query directly via `{queryable}` for full detail.")
    return lines


def render_background_brief(slug: str, *, prepared_for: str = "", purpose: str = "Account background and discovery preparation") -> str:
    """Pure rendering from retrieved+assessed structured data -- never
    re-synthesized from the model's general knowledge. This is the
    'documents are outputs' half of the spec: the brief is generated FROM
    persisted intelligence, not the other way around."""
    intel = retrieve_existing_intelligence(slug)
    account = intel["dossier"]["account"]
    brand = intel["dossier"]["brand_profile"]
    freshness = assess_freshness(intel)

    display_name = account.get("display_name", slug)
    posture = account.get("portfolio_status", {})
    posture_str = _fmt_field(posture, "research and qualification — not yet a confirmed active opportunity") if isinstance(posture, dict) else str(posture)

    lines: list[str] = []
    lines.append(f"# {display_name} Background Brief")
    if prepared_for:
        lines.append(f"\nPrepared by: {prepared_for}")
    lines.append(f"\nPurpose: {purpose}")
    lines.append(f"\nCurrent posture: {posture_str}")

    docs = intel.get("account_intelligence_docs") or []
    exec_summary_doc = next((d for d in docs if d["is_executive_summary"]), None)
    executive_summary = brand.get("executive_summary", {}).get("value", "") if isinstance(brand.get("executive_summary"), dict) else account.get("executive_summary", "")
    if not executive_summary and exec_summary_doc:
        try:
            executive_summary = _clean_executive_summary_doc(
                (core.PROJECT_DIR / exec_summary_doc["path"]).read_text(encoding="utf-8")
            )
        except Exception:  # noqa: BLE001
            executive_summary = ""
    if executive_summary:
        lines.append("\n## Executive Summary")
        lines.append(executive_summary)

    lines.extend(render_brand_profile_section(brand))

    if freshness["conflicting"]:
        lines.append("\n### Conflicting information requiring reconciliation")
        for c in freshness["conflicting"]:
            lines.append(f"- **{c.get('field')}**: {c.get('claim_a', {}).get('value')} vs. {c.get('claim_b', {}).get('value')} — {c.get('note', 'unresolved')}")

    lines.extend(render_related_artifacts_section(intel))

    current_situation = brand.get("current_business_situation") or account.get("current_business_situation", "")
    if current_situation:
        lines.append("\n## Current Business Situation")
        lines.append(current_situation)

    lines.extend(render_leadership_section(account))

    lines.extend(render_technology_environment_section(account, intel.get("ecosystem_relationships")))

    lines.extend(render_relationship_access_section(account))

    # Account-specific strategic sections (spec section 5) -- any free-form
    # narrative fields on brand_profile.json beyond the standard core get a
    # section here, titled from the field name. Deliberately generic rather
    # than hardcoding "Why Loyalty Matters" so a different account's own
    # account-specific sections (e.g. "Franchise Structure") render too.
    _CORE_BRAND_FIELDS = {
        "founded", "headquarters", "segment", "footprint", "ownership", "geography",
        "differentiation", "competitive_set", "current_business_situation",
        "executive_summary", "recommended_approach", "immediate_actions", "risks_and_cautions",
    }
    for key, val in brand.items():
        if key in _CORE_BRAND_FIELDS or key == "account_id" or not isinstance(val, str) or not val.strip():
            continue
        title = key.replace("_", " ").title()
        lines.append(f"\n## {title}")
        lines.append(val)

    if intel["opportunity_hypotheses"]:
        lines.append("\n## Opportunity Assessment")
        lines.append("\n*These are hypotheses, not confirmed opportunities.*")
        for h in intel["opportunity_hypotheses"]:
            lines.append(f"\n### {h['name']}")
            lines.append(h.get("description", ""))
            if h.get("likely_buyer"):
                lines.append(f"- Likely buyer: {h['likely_buyer']}")
            lines.append(f"- Validation status: {h.get('validation_status', 'unvalidated')}")

    if intel["discovery_questions"]:
        lines.append("\n## Key Discovery Questions")
        by_topic: dict[str, list[dict]] = {}
        for q in intel["discovery_questions"]:
            if q.get("status") != "open":
                continue
            by_topic.setdefault(q.get("topic", "General"), []).append(q)
        for topic, qs in by_topic.items():
            lines.append(f"\n### {topic}")
            for q in qs:
                lines.append(f"- {q['question']}")

    lines.append("\n## Risks and Cautions")
    for r in brand.get("risks_and_cautions", []):
        lines.append(f"- {r}")
    if freshness["stale"]:
        lines.append(f"- {len(freshness['stale'])} field(s) have not been reverified within their expected cadence and should be treated with caution.")
    if freshness["missing"]:
        lines.append(f"- {len(freshness['missing'])} field(s) remain unknown — absence of evidence, not evidence of absence.")
    if freshness["conflicting"]:
        lines.append(f"- {len(freshness['conflicting'])} open contradiction(s) require reconciliation before external use.")

    if brand.get("recommended_approach"):
        lines.append("\n## Recommended Engagement Strategy")
        lines.append(brand["recommended_approach"])

    immediate_actions = brand.get("immediate_actions") or account.get("immediate_actions") or []
    if immediate_actions:
        lines.append("\n## Immediate Actions")
        if isinstance(immediate_actions, str):
            lines.append(immediate_actions)
        else:
            for action in immediate_actions:
                lines.append(f"- {action}")

    if account.get("bottom_line"):
        lines.append("\n## Bottom Line")
        lines.append(account["bottom_line"])

    sources = (intel["dossier"].get("source_index") or {}).get("sources", [])
    public_sources = [s for s in sources if s.get("type") == "public_reference"]
    internal_sources = [s for s in sources if s.get("type") != "public_reference"]
    if public_sources or internal_sources or docs:
        lines.append("\n## Research and Internal Intelligence Sources")
        for s in public_sources:
            lines.append(f"- [{s.get('description', s.get('source_id'))}]({s.get('durable_source_id')})")
        for s in internal_sources:
            event_date = s.get("event_date") or s.get("as_of")
            date_suffix = f", {event_date}" if event_date else ""
            lines.append(f"- {s.get('description', s.get('source_id', 'Internal intelligence'))}{date_suffix}")
        for d in docs:
            label = "Internal executive summary" if d is exec_summary_doc else "Internal account research"
            lines.append(f"- {label}, updated {d.get('date', 'undated')}")

    rendered = "\n".join(lines)
    if prepared_for.strip().casefold() == "todd vahlsing":
        # These are Todd-authored internal documents. Keep his name only in
        # the byline and render his role or access in first person.
        replacements = (
            ("introduce Todd Vahlsing", "introduce me"),
            ("introduce Todd", "introduce me"),
            ("with Todd Vahlsing", "with me"),
            ("with Todd", "with me"),
            ("Todd Vahlsing's", "my"),
            ("Todd's", "my"),
            ("Todd Vahlsing owns", "I own"),
            ("Todd owns", "I own"),
        )
        for old, new in replacements:
            rendered = rendered.replace(old, new)
    return rendered


# ---------------------------------------------------------------------------
# Versioned brief artifact + registry
# ---------------------------------------------------------------------------

def _briefs_dir(slug: str) -> Path:
    return cpc.account_dir(slug) / "briefs"


def _briefs_registry_path(slug: str) -> Path:
    return _briefs_dir(slug) / "registry.json"


def get_current_brief_version(slug: str, *, include_markdown: bool = False) -> Optional[dict]:
    """include_markdown=True reads and attaches the actual current brief
    text (RB-2026-08-27: without this, a caller can only see version
    metadata, which pushed the model toward re-summarizing getAccountStatus's
    raw JSON in its own words instead of relaying the real, already-written
    document verbatim -- the same RULE 0 discipline used for daily briefs)."""
    try:
        cpc.account_dir(slug)
    except FileNotFoundError:
        return None
    reg_path = _briefs_dir(slug) / "registry.json"
    if not reg_path.exists():
        return None
    reg = cpc.load_json(reg_path)
    versions = reg.get("versions", [])
    current = [v for v in versions if not v.get("superseded")]
    if not current:
        return None
    entry = dict(current[-1])
    if include_markdown:
        brief_path = core.SYSTEM_DIR.parent / entry["path"]
        entry["markdown"] = brief_path.read_text(encoding="utf-8") if brief_path.exists() else None
    return entry


def register_brief_version(slug: str, markdown: str, *, generated_for: str = "", purpose: str = "") -> dict:
    """Writes the brief to briefs/current/, archives the prior current
    version to briefs/history/ (mirrors render.py's xlsx current/history
    pattern), and records the new version in briefs/registry.json.

    No-op guard (2026-09-25, mirrors artifact_vault_common.register_
    version()'s identical fix): if `markdown` is byte-identical to the
    current version, returns that existing entry unchanged rather than
    writing a new one. Needed for the same reason -- see
    refresh_persisted_briefs.py, which calls this once a day for every
    customers_prospects account; without the guard, an account with no
    real news would still accumulate 365 identical versions a year."""
    briefs_dir = _briefs_dir(slug)
    current_dir = briefs_dir / "current"
    history_dir = briefs_dir / "history"
    current_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)

    current_path = current_dir / "Background_Brief.md"
    reg_path = _briefs_registry_path(slug)
    reg = cpc.load_json(reg_path) if reg_path.exists() else {"account_id": f"acct-{slug}", "versions": []}

    if current_path.exists() and current_path.read_text(encoding="utf-8") == markdown:
        current = [v for v in reg["versions"] if not v.get("superseded")]
        if current:
            return current[-1]

    if current_path.exists():
        # Version number in the filename, not just a timestamp: two
        # regenerations within the same wall-clock second (confirmed live
        # by this feature's own tests) would otherwise collide and silently
        # overwrite one archived version with another.
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        prior_version = len(reg["versions"])
        archive_path = history_dir / f"{ts}_v{prior_version}_Background_Brief.md"
        archive_path.write_text(current_path.read_text(encoding="utf-8"), encoding="utf-8")
        for v in reg["versions"]:
            v["superseded"] = True

    current_path.write_text(markdown, encoding="utf-8")
    version_num = len(reg["versions"]) + 1
    entry = {
        "version": version_num, "generated_at": _now_iso(), "generated_for": generated_for,
        "purpose": purpose, "intelligence_verification_date": _today(),
        "path": str(current_path.relative_to(core.SYSTEM_DIR.parent)), "superseded": False,
    }
    reg["versions"].append(entry)
    cpc.save_json(reg_path, reg)

    # Portfolio-level visibility (spec section 16) -- one field added to the
    # existing cross-account registry, not a new registry.
    try:
        portfolio_reg = cpc.load_registry()
        for e in portfolio_reg.get("registry", []):
            if e.get("account_id") == f"acct-{slug}":
                e["latest_background_brief_date"] = _today()
        cpc.save_json(cpc.registry_path(), portfolio_reg)
    except FileNotFoundError:
        pass

    # Index/log split per Todd's direction (2026-08-27): the FIRST version
    # of a brief is a new document -> register it in the shared index.
    # Every regeneration after that is an update to the SAME document at
    # the SAME path -> log it, don't re-register (the index entry created
    # at v1 is still correct).
    try:
        display_name = cpc.load_json(cpc.account_dir(slug) / "account.json").get("display_name", slug)
        if version_num == 1:
            intelligence_index.register_document(
                display_name, "account_research_brief", f"Background Brief: {slug}",
                entry["path"], source_system="account_research", created_at=_today(),
            )
        else:
            intelligence_index.log_update(
                display_name, entry["path"], resource_type="account_research_brief",
                note=f"regenerated as v{version_num}" + (f" for {generated_for}" if generated_for else ""),
            )
    except Exception:  # noqa: BLE001 — indexing is best-effort, never blocks a real brief write
        pass

    # 24h-SLA flag (2026-08-27, per Todd's explicit direction): a brief
    # regenerating for ANY reason -- Todd-initiated via chat, or the next
    # day's cascade sweep auto-processing a stale flag -- means whatever
    # prompted the flag has now been addressed. Clear it here, not in the
    # cascade module, so both trigger paths get this for free.
    try:
        cpc.clear_refresh_flag(slug)
    except Exception:  # noqa: BLE001
        pass

    return entry


def generate_brief(slug: str, *, generated_for: str = "", purpose: str = "Account background and discovery preparation") -> dict:
    """The main entry point: render the current canonical brief from
    persisted intelligence and register it as a new version."""
    markdown = render_background_brief(slug, prepared_for=generated_for, purpose=purpose)
    version = register_brief_version(slug, markdown, generated_for=generated_for, purpose=purpose)
    return {"slug": slug, "markdown": markdown, "version": version}


def _smoke() -> bool:
    ok = True
    slug, exists = resolve_account("Cafe Rio")
    if not (slug == "cafe-rio" and exists):
        print(f"FAIL: resolve_account('Cafe Rio') -> {slug}, {exists}")
        ok = False
    intel = retrieve_existing_intelligence("cafe-rio")
    if not intel["dossier"]["account"]:
        print("FAIL: retrieve_existing_intelligence returned empty dossier")
        ok = False
    freshness = assess_freshness(intel)
    if not isinstance(freshness["reliable"], list):
        print("FAIL: assess_freshness malformed")
        ok = False
    print("smoke: OK" if ok else "smoke: FAILED")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    p_resolve = sub.add_parser("resolve")
    p_resolve.add_argument("name")
    p_status = sub.add_parser("status")
    p_status.add_argument("slug")
    p_generate = sub.add_parser("generate")
    p_generate.add_argument("slug")
    p_generate.add_argument("--for", dest="generated_for", default="")
    sub.add_parser("--smoke")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    if args.smoke or args.cmd == "--smoke":
        sys.exit(0 if _smoke() else 1)
    if args.cmd == "resolve":
        slug, exists = resolve_account(args.name)
        print(json.dumps({"slug": slug, "exists": exists}, indent=2))
    elif args.cmd == "status":
        intel = retrieve_existing_intelligence(args.slug)
        print(json.dumps(assess_freshness(intel), indent=2, default=str))
    elif args.cmd == "generate":
        result = generate_brief(args.slug, generated_for=args.generated_for)
        print(result["markdown"])
        print(f"\n[registered as version {result['version']['version']}]", file=sys.stderr)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
