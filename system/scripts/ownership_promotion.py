#!/usr/bin/env python3
"""
ownership_promotion.py — RB-2026-09-11.

Scoping "does M&A deserve tech-stack-style weekend deep-research
treatment?" (2026-09-10/11) found detection was never the gap -- M&A
signals are already caught daily by entity_alerts.py,
technomic_watchlist_scan.py, and priority_account_publisher_scan.py via
ecosystem_brief.py's `funding_event` signal class. The real gap: no
entity has ever had a structured owner/parent-company field. Even a
fully-verified acquisition Todd researched and wrote up by hand
(Del Taco / Yadav Enterprises, in system/account_intelligence/
2026-09-04-del-taco-*.md) never became a queryable fact on
brand-del-taco's own entity record -- a real funding_event signal for
that exact acquisition already sits in ecosystem_intelligence.json's
signals array too, captured and classified, then going nowhere.

Same discipline as tech_stack_relationship_promotion.py, which this
module mirrors directly: never invents a fact. It proposes candidates
for review -- persisted in a pending-proposal store, same shape as every
other review-first module in this codebase -- and only
ecosystem_intelligence.set_entity_owner() ever touches the real graph,
and only after an explicit confirm via record_proposal().

Confidence-Based Auto-Recording Phase 6 (2026-09-25): unlike Phase 5's
three queues, set_entity_owner() DOES overwrite an existing value outright
(no ownership history tracked) -- the one thing in scope here that
genuinely needs the "don't overwrite a stronger claim" discipline, not just
"is this purely additive." record_proposal() now auto-applies whenever
proposed_owner_confidence isn't "unknown" (that rule is unchanged --
"unknown" never auto-applies, and confirming it still requires a human to
supply owner_name): a brand-new owner is a pure add; an existing owner is
overwritten only when the new claim's confidence_calibration score clears
the same +0.15/0.9 margin Phase 2 uses for relationships, using source_type
as the score driver (proposed_owner_confidence == "unknown" is a hard gate
before source_type is even consulted). Otherwise the new claim is appended
to the entity's own reported_alternates list -- visible, dated, sourced,
never silently dropped. Every write is tagged owner_confirmed_by
("system:ownership_promotion" vs "human"), the same human:/system:
provenance convention used elsewhere in this codebase (e.g.
brand_profile_common.py's last_reviewed_by); an existing owner_name with no
owner_confidence on file predates this feature and is treated as a
deliberate human decision at "high" (0.85) trust, not a blank slate.

Acquirer-name extraction (who the owner actually is) is best-effort
regex against a small set of common real phrasings, confirmed against
this account's own real acquisition headlines before shipping -- never
guessed beyond what the pattern actually matches, and a candidate is
still created (with a blank proposed owner) when extraction finds
nothing. record_proposal() requires a real owner_name to confirm --
supplied by the caller or already prefilled -- exactly like
tech_stack_relationship_promotion.py blocks confirm on an unknown
category until one is supplied.

CLI:
    python3 ownership_promotion.py scan            # scan + write candidates, print summary
    python3 ownership_promotion.py pending          # list pending candidates
    python3 ownership_promotion.py confirm <id> --owner-name "X" [--owner-entity-id Y]
    python3 ownership_promotion.py reject <id>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import ecosystem_brief as eb  # noqa: E402
import confidence_calibration as cc  # noqa: E402

STORE_PATH = core.SYSTEM_DIR / ".cache" / "ownership_promotion_candidates.json"
ACCOUNT_INTELLIGENCE_DIR = core.SYSTEM_DIR / "account_intelligence"

# RB-2026-09-18 (next-sprint Workstream 1): same-day visibility manifest,
# identical rationale/pattern as executive_move_promotion.py's PROMOTED_PATH
# -- purely additive VISIBILITY (daily_brief.py reads this), never bypasses
# record_proposal()'s confirm-before-mutate gate on the real graph.
PROMOTED_PATH = core.SYSTEM_DIR / ".cache" / "ownership_promotion_promoted.json"

_MIN_NAME_LEN = 4  # same short-alias noise guard as account_reference_detector._name_in_text

_LINE_SPLIT_RE = re.compile(r"\r?\n")

# RB-2026-09-11: funding_event (ecosystem_brief.SIGNAL_CLASS_KEYWORDS) also
# covers pure financing language ("raised", "series a/b/c", "capital
# raise", "investment", "ipo") that does NOT imply a new owner. This local
# filter narrows to the acquisition/ownership-change subset specifically --
# scoped to this module only, not a change to the shared taxonomy, same
# precedent as priority_account_publisher_scan.py's known-vendor-name
# requirement (added locally, not to SIGNAL_CLASS_KEYWORDS itself).
_OWNERSHIP_SIGNAL_RE = re.compile(
    r"acqui|merger|merged|owned by|now owns|-owned|parent company|sold to|sells to",
    re.IGNORECASE,
)


def _is_ownership_signal(text: str) -> bool:
    return bool(_OWNERSHIP_SIGNAL_RE.search(text or ""))


def _acquirer_patterns(target_name: str) -> list[re.Pattern]:
    """Best-effort acquirer-name extraction anchored on the already-known
    target entity's name. Validated against this account's own two real
    Del Taco acquisition headlines (2022 Jack in the Box acquisition, 2025
    Yadav Enterprises acquisition) and real account_intelligence prose
    before shipping -- not hypothetical patterns. NAME's capture is
    deliberately non-greedy and case-SENSITIVE (inline (?i:...) scopes
    case-insensitivity to the literal keywords only, not the capture group
    itself -- a blanket re.IGNORECASE would make [A-Z] match lowercase too
    and silently swallow leading filler words like "supporting Yadav's...").
    """
    t = re.escape(target_name)
    name = r"[A-Z][\w&.,' -]{0,58}?"
    return [
        re.compile(rf"\b({name})\s+(?i:has\s+)?(?i:acquir(?:ed|es))\s+(?i:the\s+brand\s+)?{t}\b"),
        re.compile(rf"\b({name})\s+(?i:complet(?:es|ed)\s+its\s+acquisition\s+of)\s+{t}\b"),
        re.compile(rf"{t}\b.{{0,60}}?(?i:acqui(?:red|sition)\s+by)\s+({name})(?=[.,\n]|\s+-\s|$)"),
        re.compile(rf"{t}\s+(?i:is\s+now\s+(?:a\s+)?)({name})-(?i:owned)"),
        re.compile(rf"{t}\s+(?i:is\s+now\s+owned\s+by)\s+({name})(?=[.,\n]|$)"),
        re.compile(rf"\b({name})'s\s+(?i:{t}\s+acquisition|acquisition\s+of\s+{t})"),
    ]


def _extract_acquirer_name(target_name: str, text: str) -> tuple[Optional[str], str]:
    """Returns (proposed_owner_name_or_None, confidence). confidence is
    "extracted_from_text" when a pattern matched, "unknown" otherwise --
    mirrors tech_stack_relationship_promotion.py's category_confidence
    field. Never returns a fabricated guess; a miss is just None.

    RB-2026-09-11, confirmed live against real account_intelligence prose:
    the capture group's flat character class has no word-by-word
    structure, so when an unrelated capitalized phrase sits between the
    true sentence start and the real name it gets swept in too -- e.g.
    "Chief Transformation Officer supporting Yadav's Del Taco acquisition"
    over-captured as "Chief Transformation Officer supporting Yadav"
    instead of "Yadav". _is_plausible_company_name() below is a cheap,
    bounded backstop for exactly this: too many words, or a common
    English connector/verb this pattern set shouldn't be swallowing, and
    the match is discarded (falls back to None/unknown) rather than
    proposed as a name-shaped-but-wrong guess. Not a grammar rewrite --
    the real safety net stays human review, this only trims the most
    obviously-wrong cases before a reviewer ever sees them."""
    for pattern in _acquirer_patterns(target_name):
        m = pattern.search(text or "")
        if m:
            name = m.group(1).strip().strip("'\"")
            if len(name) >= 2 and _is_plausible_company_name(name):
                return name, "extracted_from_text"
    return None, "unknown"


_IMPLAUSIBLE_NAME_WORDS = {
    "because", "while", "supporting", "who", "which", "that", "who's",
    "continuing", "including", "according", "following", "after", "before",
}


def _is_plausible_company_name(name: str) -> bool:
    words = name.split()
    if len(words) > 5:
        return False
    return not any(w.strip(".,'\"").lower() in _IMPLAUSIBLE_NAME_WORDS for w in words)


def _counterparty_patterns(name: str) -> list[re.Pattern]:
    """Patterns matching NAME appearing as the ACQUIRER or SELLER in an
    acquisition sentence -- i.e. a party to someone ELSE's ownership
    change, not itself the thing being acquired. Fully case-insensitive
    (unlike _acquirer_patterns' capture group): there's no unknown span
    being captured here to over-grab, just a known entity name being
    checked for its grammatical role, and real headlines are frequently
    all-caps ("MAIN EVENT TO BE ACQUIRED BY DAVE & BUSTER'S").

    Validated against real false positives found live in the 2026-09-29
    ownership review: Yadav Enterprises/Dave & Buster's/Biscuit Belly/
    J. Alexander's each wrongly got their own "who owns this?" candidate
    from a sentence where they were plainly the one doing the acquiring,
    and Jack in the Box got the same treatment from sentences where it
    was the SELLER divesting Del Taco, not itself changing hands:
      - "Yadav Enterprises acquired Del Taco" (active acquirer)
      - "J. Alexander's Proposed Acquisition of 99 Restaurants",
        "Biscuit Belly ... with Acquisition of Maple Street Biscuit Co."
        (NAME ... acquisition of X -- NAME is the acquirer; order matters,
        so this does NOT fire on "SPB Hospitality Completes Acquisition
        of J. Alexander's Holdings", where the true target's name comes
        AFTER the phrase)
      - "MAIN EVENT TO BE ACQUIRED BY DAVE & BUSTER'S" (passive voice
        naming the acquirer after "by")
      - "Yadav Enterprises acquired Del Taco from Jack in the Box"
        (NAME is the seller, named after "from")
      - "Jack in the Box officially completed the ... sale to Yadav
        Enterprises" (NAME is the seller, active voice with "sale to")
    Same "validated against real headlines, not hypothetical patterns"
    discipline as _acquirer_patterns -- this does not attempt to resolve
    every possible phrasing (a co-mention deep inside one long,
    multi-sentence account-intelligence paragraph unrelated to NAME's own
    role can still slip through; that's the separate same-line-
    granularity issue, not this one), only the concrete roles observed to
    actually occur in this account's own real text."""
    n = re.escape(name)
    return [
        re.compile(rf"\b{n}\s+(?:has\s+)?acquir(?:ed|es)\b", re.IGNORECASE),
        re.compile(rf"\b{n}\b.{{0,80}}?\bacquisition\s+of\b", re.IGNORECASE),
        re.compile(rf"\bacquir(?:ed|ing)\s+by\s+{n}\b", re.IGNORECASE),
        re.compile(rf"\bacquir(?:ed|es)\b.{{0,80}}?\bfrom\s+{n}\b", re.IGNORECASE),
        re.compile(rf"\b{n}\b.{{0,80}}?\bsale\s+to\b", re.IGNORECASE),
    ]


def _is_named_as_acquirer_or_seller(name: str, text: str) -> bool:
    """True when NAME is described as a party performing/completing an
    acquisition (buyer or seller) in TEXT, rather than being the entity
    whose ownership changed -- see _counterparty_patterns()."""
    return any(p.search(text or "") for p in _counterparty_patterns(name))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_store() -> dict:
    if not STORE_PATH.exists():
        return {"candidates": {}}
    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"candidates": {}}


def _save_store(store: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(store, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _candidate_id(entity_id: str, origin_ref: str) -> str:
    return f"{entity_id}::{origin_ref}"


def _load_promoted_manifest() -> dict:
    if not PROMOTED_PATH.exists():
        return {"_scan_date": None, "candidates": []}
    try:
        manifest = json.loads(PROMOTED_PATH.read_text(encoding="utf-8"))
        if isinstance(manifest, dict) and isinstance(manifest.get("candidates"), list):
            return manifest
    except (OSError, json.JSONDecodeError):
        pass
    return {"_scan_date": None, "candidates": []}


def _save_promoted_manifest(manifest: dict) -> None:
    PROMOTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROMOTED_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _promote_same_day(store: dict, new_ids: list[str]) -> None:
    """Purely additive same-day visibility -- see executive_move_promotion.py's
    twin for the full rationale. Never touches candidate status or the real
    graph; that stays gated on record_proposal()."""
    if not new_ids:
        return
    today = date.today().isoformat()
    manifest = _load_promoted_manifest()
    if manifest.get("_scan_date") != today:
        manifest = {"_scan_date": today, "candidates": []}
    seen = {c["candidate_id"] for c in manifest["candidates"]}
    for cid in new_ids:
        if cid in seen:
            continue
        cand = store["candidates"].get(cid)
        if not cand:
            continue
        manifest["candidates"].append({
            "candidate_id": cid,
            "entity_name": cand["entity_name"],
            "proposed_owner_name": cand["proposed_owner_name"],
            "proposed_owner_confidence": cand["proposed_owner_confidence"],
            "source_title": cand["source_title"],
            "source_url": cand["source_url"],
            "evidence_excerpt": cand["evidence_excerpt"],
            "evidence_date": cand["evidence_date"],
        })
        seen.add(cid)
    _save_promoted_manifest(manifest)


def same_day_candidates(today: Optional[date] = None) -> list[dict]:
    """Public read API for daily_brief.py: candidates newly detected on
    `today` (default: actual today), or [] if none / the manifest is stale.
    Never raises -- a brief-generation caller should never break on this."""
    manifest = _load_promoted_manifest()
    want = (today or date.today()).isoformat()
    if manifest.get("_scan_date") != want:
        return []
    return manifest.get("candidates") or []


def _add_candidate(
    store: dict, *, entity_id: str, entity_name: str, proposed_owner_name: Optional[str],
    proposed_owner_confidence: str, source_type: str, source_title: str, source_url: Optional[str],
    evidence_excerpt: str, origin: str, origin_ref: str, evidence_date: Optional[str] = None,
) -> bool:
    """Insert one pending candidate. Returns True if genuinely new.
    Always creates a candidate even when proposed_owner_name is None --
    detection is never blocked on extraction succeeding; the reviewer
    supplies/corrects the real owner at confirm time."""
    cid = _candidate_id(entity_id, origin_ref)
    if cid in store["candidates"]:
        return False  # already proposed (pending, confirmed, or rejected) -- never re-propose
    store["candidates"][cid] = {
        "candidate_id": cid,
        "status": "proposed_pending_confirmation",
        "entity_id": entity_id,
        "entity_name": entity_name,
        "proposed_owner_name": proposed_owner_name,
        "proposed_owner_confidence": proposed_owner_confidence,  # "extracted_from_text" | "unknown"
        "source_type": source_type,
        "source_title": source_title,
        "source_url": source_url,
        "evidence_excerpt": evidence_excerpt[:600],
        "origin": origin,
        "origin_ref": origin_ref,
        "evidence_date": evidence_date,
        "detected_at": _now_iso(),
        "resolved_at": None,
    }
    return True


def scan_existing_signals(*, dry_run: bool = False) -> dict:
    """Scans ecosystem_intelligence.json's own signals array for
    funding_event signals whose text also matches the local ownership
    filter (_is_ownership_signal) -- distinguishing a real acquisition
    from a plain financing round, which funding_event alone doesn't."""
    graph = ei._read_graph()
    by_id = ei._index_by_id(graph.get("entities") or [])
    store = _load_store()

    scanned = 0
    new_count = 0
    new_ids: list[str] = []
    to_auto_apply: list[str] = []
    for sig in graph.get("signals") or []:
        if sig.get("signal_type") != "funding_event":
            continue
        summary = sig.get("summary") or ""
        if not _is_ownership_signal(summary):
            continue
        scanned += 1
        for entity_id in sig.get("entities") or []:
            entity = by_id.get(entity_id)
            if not entity or entity.get("entity_type") not in ("brand", "vendor"):
                continue
            if _is_named_as_acquirer_or_seller(entity.get("name", ""), summary):
                continue
            owner_name, confidence = _extract_acquirer_name(entity.get("name", ""), summary)
            source_id = (sig.get("sources") or [None])[0]
            source = next((s for s in graph.get("sources") or [] if s.get("id") == source_id), None)
            cid = _candidate_id(entity_id, sig["id"])
            added = _add_candidate(
                store, entity_id=entity_id, entity_name=entity.get("name", ""),
                proposed_owner_name=owner_name, proposed_owner_confidence=confidence,
                source_type=(source or {}).get("source_type") or "credible_reporting",
                source_title=(source or {}).get("title") or summary, source_url=(source or {}).get("url"),
                evidence_excerpt=summary, origin="signal_scan", origin_ref=sig["id"],
                evidence_date=sig.get("event_at"),
            )
            if added:
                new_ids.append(cid)
                if confidence != "unknown":
                    to_auto_apply.append(cid)
            new_count += added

    auto_applied = 0
    if not dry_run:
        _save_store(store)
        for cid in to_auto_apply:
            result = record_proposal(cid, confirmed=True, confirmed_by="system:ownership_promotion", auto_apply_gate=True)
            if result.get("confirmed"):
                auto_applied += 1
    return {"signals_scanned": scanned, "new_candidates": new_count, "new_candidate_ids": new_ids,
            "auto_applied": auto_applied,
            "total_pending": sum(1 for c in store["candidates"].values()
                                  if c["status"] == "proposed_pending_confirmation")}


_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-|$)")


def _date_prefix_from_filename(stem: str) -> Optional[str]:
    m = _DATE_PREFIX_RE.match(stem)
    return m.group(1) if m else None


def scan_account_intelligence_docs(*, dry_run: bool = False) -> dict:
    """Scans Todd's own account_intelligence notes for a known brand
    mentioned on the same LINE as ownership-change language -- same
    mechanical, no-NLP, line-level discipline as tech_stack_relationship_
    promotion.py's own doc scan (avoids the real, confirmed cross-row-
    contamination bug that scan found in table-shaped docs). This is the
    path that directly closes the real, confirmed Del Taco/Yadav gap --
    that content already sits in 2026-09-04-del-taco-*.md, unused.

    brand entities only (RB-2026-09-29): originally also scanned vendor
    entities, on the theory that a vendor's own ownership change (e.g. a
    POS company being acquired) is just as worth catching here. Live
    review of the resulting pending queue found the opposite -- every
    single vendor-entity candidate this scanner had ever produced (34 of
    145 pending items, ~25% of the whole queue) was a false positive:
    Todd's account-intelligence notes are brand/account strategy docs
    that mention vendors constantly as tech-stack context (e.g. "protect
    the Worldpay gateway/acquiring position", "what does Fiserv own
    today: acquiring only, devices, gateway..."), and "acquiring"/"owned"/
    "parent company" show up naturally in that payments/product-strategy
    vocabulary with zero connection to a real M&A event for the vendor
    itself. Not one true positive was found among them. A vendor's real
    ownership changes (e.g. Marigold's 2025 acquisition by Zeta Global)
    are already captured properly through competitor_intelligence.py's
    own evidence/cos_commentary pipeline, sourced from actual deep
    research rather than incidental name mentions -- that remains the
    right home for vendor-ownership facts; this scanner now stays
    brand-only, which is also the entity type its own docstring and
    original Del Taco/Yadav motivation were about."""
    import account_reference_detector as ard  # noqa: E402 -- reuses its proven word-boundary _name_in_text

    graph = ei._read_graph()
    entities = [e for e in graph.get("entities") or [] if e.get("entity_type") == "brand"]
    store = _load_store()

    scanned = 0
    new_count = 0
    new_ids: list[str] = []
    to_auto_apply: list[str] = []
    if not ACCOUNT_INTELLIGENCE_DIR.exists():
        return {"docs_scanned": 0, "new_candidates": 0, "new_candidate_ids": new_ids,
                "auto_applied": 0, "total_pending": 0}

    for path in sorted(ACCOUNT_INTELLIGENCE_DIR.glob("*.md")):
        scanned += 1
        evidence_date = _date_prefix_from_filename(path.stem)
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(_LINE_SPLIT_RE.split(text)):
            if not _is_ownership_signal(line):
                continue
            matched = [e for e in entities if ard._name_in_text(e.get("name", ""), line.lower())]
            for entity in matched:
                if _is_named_as_acquirer_or_seller(entity.get("name", ""), line):
                    continue
                owner_name, confidence = _extract_acquirer_name(entity.get("name", ""), line)
                origin_ref = f"{path.name}#L{line_no}"
                cid = _candidate_id(entity["id"], origin_ref)
                added = _add_candidate(
                    store, entity_id=entity["id"], entity_name=entity.get("name", ""),
                    proposed_owner_name=owner_name, proposed_owner_confidence=confidence,
                    source_type="operator_context", source_title=path.name, source_url=None,
                    evidence_excerpt=line.strip(), origin="account_intelligence_scan",
                    origin_ref=origin_ref, evidence_date=evidence_date,
                )
                if added:
                    new_ids.append(cid)
                    if confidence != "unknown":
                        to_auto_apply.append(cid)
                new_count += added

    auto_applied = 0
    if not dry_run:
        _save_store(store)
        for cid in to_auto_apply:
            result = record_proposal(cid, confirmed=True, confirmed_by="system:ownership_promotion", auto_apply_gate=True)
            if result.get("confirmed"):
                auto_applied += 1
    return {"docs_scanned": scanned, "new_candidates": new_count, "new_candidate_ids": new_ids,
            "auto_applied": auto_applied,
            "total_pending": sum(1 for c in store["candidates"].values()
                                  if c["status"] == "proposed_pending_confirmation")}


def scan(*, dry_run: bool = False) -> dict:
    """Run both scan sources and return a combined summary."""
    signals_result = scan_existing_signals(dry_run=dry_run)
    docs_result = scan_account_intelligence_docs(dry_run=dry_run)
    new_ids = signals_result.pop("new_candidate_ids", []) + docs_result.pop("new_candidate_ids", [])
    if not dry_run and new_ids:
        _promote_same_day(_load_store(), new_ids)
    return {"signals": signals_result, "account_intelligence": docs_result}


def propose_ownership_finding(
    entity_id: str, *, evidence_text: str, owner_name: Optional[str] = None,
    source_url: Optional[str] = None, source_title: Optional[str] = None,
    evidence_date: Optional[str] = None,
) -> dict:
    """Propose one candidate from research already gathered by the CALLER
    (rbb-chat/Codex, real web search/fetch tools) -- same posture as
    tech_stack_relationship_promotion.propose_research_finding(). Unlike
    the scan paths, owner_name here is usually already known (manual
    research typically already answers "who"); still runs through
    _extract_acquirer_name() as a fallback only when omitted."""
    if evidence_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", evidence_date):
        return {"error": f"evidence_date {evidence_date!r} is not a valid YYYY-MM-DD date."}
    graph = ei._read_graph()
    by_id = ei._index_by_id(graph.get("entities") or [])
    entity = by_id.get(entity_id)
    if not entity or entity.get("entity_type") not in ("brand", "vendor"):
        return {"error": f"no brand/vendor entity '{entity_id}' -- resolve a real entity_id first (e.g. via queryIntelligenceIndex)."}
    if not (evidence_text or "").strip():
        return {"error": "evidence_text is required -- never propose a finding with no real evidence attached."}

    confidence = "stated_by_caller"
    if not owner_name:
        owner_name, confidence = _extract_acquirer_name(entity.get("name", ""), evidence_text)

    store = _load_store()
    origin_ref = source_url or f"research::{entity_id}::{_now_iso()}"
    cid = _candidate_id(entity_id, origin_ref)
    added = _add_candidate(
        store, entity_id=entity_id, entity_name=entity.get("name", ""),
        proposed_owner_name=owner_name, proposed_owner_confidence=confidence,
        source_type="credible_trade_reporting", source_title=source_title or entity.get("name", ""),
        source_url=source_url, evidence_excerpt=evidence_text, origin="external_research",
        origin_ref=origin_ref, evidence_date=evidence_date,
    )
    _save_store(store)
    if added:
        _promote_same_day(store, [cid])
    return {"proposed": added, "candidate_id": cid,
            "note": "already pending" if not added else None}


def pending_candidates() -> list[dict]:
    store = _load_store()
    return [c for c in store["candidates"].values() if c["status"] == "proposed_pending_confirmation"]


# An existing owner_name with no owner_confidence recorded predates this
# feature -- every owner_name write ever made went through this same
# record_proposal(), always after a human confirm, so it's treated as a
# deliberate human decision at "high" trust, not a blank slate a routine
# trade-press claim can casually overwrite.
_UNSCORED_EXISTING_OWNER_SCORE = cc.CONFIDENCE_BAND_TO_SCHEMA["high"][1]


def record_proposal(
    candidate_id: str, *, confirmed: bool, owner_name: Optional[str] = None, owner_entity_id: Optional[str] = None,
    confirmed_by: str = "human", auto_apply_gate: bool = False,
) -> dict:
    """Confirm (write owner_name/owner_entity_id via
    ecosystem_intelligence.set_entity_owner(), the only path that ever
    touches the real graph) or reject one pending candidate.

    owner_name is required to confirm -- either already prefilled on the
    candidate (proposed_owner_name) or supplied here, whichever the
    caller provides last (an explicit owner_name argument always wins,
    letting a reviewer correct a wrong extraction). Never auto-resolves
    which of two conflicting candidates for the same entity is "right" --
    that's the reviewer's call; confirming one candidate does not
    auto-reject any other pending candidate for the same entity.

    Confidence-Based Auto-Recording Phase 6: auto_apply_gate=True (used
    only by scan()'s own unattended auto-apply loop) means: when the
    entity already has a DIFFERENT owner_name on file, this claim only
    overwrites it when its confidence_calibration score (source_type-
    derived) clears the same +0.15/0.9 margin ecosystem_intelligence.py's
    relationship conflict engine uses, otherwise it's appended to the
    entity's reported_alternates list instead of overwriting. An explicit
    confirm (auto_apply_gate=False, the default -- a human or a caller
    reviewing one specific candidate, e.g. the CLI `confirm` command or a
    Team Portal action) always applies directly, exactly like before this
    feature -- the whole point of a human choosing to confirm THIS
    candidate is that their decision is authoritative, not silently
    redirected because of a value they may not even have been shown."""
    store = _load_store()
    cand = store["candidates"].get(candidate_id)
    if not cand:
        return {"error": f"unknown candidate id: {candidate_id}"}
    if cand["status"] != "proposed_pending_confirmation":
        return {"error": f"candidate {candidate_id} already resolved: {cand['status']}"}

    if not confirmed:
        cand["status"] = "rejected"
        cand["resolved_at"] = _now_iso()
        _save_store(store)
        return {"rejected": True, "candidate_id": candidate_id}

    resolved_owner_name = (owner_name or "").strip() or cand.get("proposed_owner_name")
    if not resolved_owner_name:
        return {
            "error": (
                f"candidate {candidate_id} has no owner name (proposed_owner_confidence="
                "'unknown') -- supply owner_name to confirm, or reject this candidate; "
                "never guessed here."
            ),
        }

    graph = ei._read_graph()
    by_id = ei._index_by_id(graph.get("entities") or [])
    entity = by_id.get(cand["entity_id"])
    if entity is None:
        return {"error": f"entity {cand['entity_id']} no longer exists in the graph"}

    resolved_owner_entity_id = owner_entity_id
    if resolved_owner_entity_id is None:
        resolved_owner_entity_id = ei._resolve_entity_id_any_type(resolved_owner_name, graph)
    source_id = None
    if cand["origin"] == "signal_scan":
        sig = next((s for s in graph.get("signals") or [] if s.get("id") == cand["origin_ref"]), None)
        source_id = (sig.get("sources") or [None])[0] if sig else None

    level, new_score = cc.score_for_source(cand.get("source_type"))
    existing_owner_name = (entity.get("owner_name") or "").strip()

    if auto_apply_gate and existing_owner_name and existing_owner_name.lower() != resolved_owner_name.strip().lower():
        existing_conf = entity.get("owner_confidence") or {}
        existing_score = existing_conf.get("score")
        if not isinstance(existing_score, (int, float)):
            existing_score = _UNSCORED_EXISTING_OWNER_SCORE
        if not cc.should_supersede(new_score, existing_score):
            entity.setdefault("reported_alternates", []).append({
                "owner_name": resolved_owner_name,
                "owner_entity_id": resolved_owner_entity_id,
                "confidence": {"level": level, "score": new_score},
                "source_title": cand.get("source_title"),
                "source_url": cand.get("source_url"),
                "evidence_date": cand.get("evidence_date"),
                "recorded_at": _now_iso(),
                "candidate_id": candidate_id,
            })
            entity["updated_at"] = ei._now()
            ei._write_graph(graph)
            cand["status"] = "confirmed"
            cand["resolved_at"] = _now_iso()
            cand["confirmed_by"] = confirmed_by
            cand["outcome"] = "recorded_alongside"
            _save_store(store)
            return {
                "confirmed": True, "candidate_id": candidate_id, "outcome": "recorded_alongside",
                "existing_owner_name": existing_owner_name, "reported_alternate": resolved_owner_name,
            }

    ok = ei.set_entity_owner(graph, cand["entity_id"], resolved_owner_name, resolved_owner_entity_id, source_id)
    if not ok:
        return {"error": f"entity {cand['entity_id']} no longer exists in the graph"}
    entity["owner_confidence"] = {"level": level, "score": new_score}
    entity["owner_confirmed_by"] = confirmed_by
    ei._write_graph(graph)

    cand["status"] = "confirmed"
    cand["resolved_at"] = _now_iso()
    cand["confirmed_owner_name"] = resolved_owner_name
    cand["confirmed_owner_entity_id"] = resolved_owner_entity_id
    cand["confirmed_by"] = confirmed_by
    cand["outcome"] = "applied"
    _save_store(store)
    return {
        "confirmed": True, "candidate_id": candidate_id, "outcome": "applied",
        "owner_name": resolved_owner_name, "owner_entity_id": resolved_owner_entity_id,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    sub.add_parser("pending")
    confirm_p = sub.add_parser("confirm")
    confirm_p.add_argument("candidate_id")
    confirm_p.add_argument("--owner-name", default=None)
    confirm_p.add_argument("--owner-entity-id", default=None)
    reject_p = sub.add_parser("reject")
    reject_p.add_argument("candidate_id")
    args = p.parse_args()

    if args.cmd == "scan":
        print(json.dumps(scan(), indent=2, ensure_ascii=False))
    elif args.cmd == "pending":
        print(json.dumps(pending_candidates(), indent=2, ensure_ascii=False))
    elif args.cmd == "confirm":
        result = record_proposal(args.candidate_id, confirmed=True,
                                  owner_name=args.owner_name, owner_entity_id=args.owner_entity_id)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.cmd == "reject":
        print(json.dumps(record_proposal(args.candidate_id, confirmed=False), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
