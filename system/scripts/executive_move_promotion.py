#!/usr/bin/env python3
"""
executive_move_promotion.py — RB-2026-09-11.

Item 1 of the 3-category scoping ("does M&A/exec-moves/relationship-intel
deserve tech-stack-style weekend deep-research?"), taken second per Todd's
own sequencing after ownership_promotion.py (M&A). Investigation found
`leadership_change` is RBB's most common real material signal type
(confirmed live 2026-09-10, 4/11 real priority-account candidates), but the
named executive is discarded at classification time -- ecosystem_brief.py's
leadership_change path only ever writes the COMPANY entity_id into
graph["signals"]; the person's own name never reaches anywhere. Confirmed
live: "Roland Gonzalez" (Church's Texas Chicken's real new CEO) doesn't
appear anywhere in ecosystem_intelligence.json.

Unlike ownership_promotion.py's flat schema-field fix, this spans two
stores: ecosystem_intelligence.json (the company-side signal) and
system/baseline_index.json (RBB's real contact/relationship-tracking
system, which already has exactly the fields needed -- current_company/
current_role -- no schema change required here).

A second real, pre-existing bug found during scoping, deliberately not
fixed here: ecosystem_intelligence.json's own leadership_change signal
classification has real noise -- some signals carry that signal_type with
zero leadership content at all (e.g. a real "Crumbl Partners With ezCater"
press release tagged leadership_change by whatever upstream pipeline
assigned it). This module's own _is_leadership_appointment_signal() local
filter neutralizes the practical impact regardless of why the upstream tag
is wrong.

Same review-first discipline as tech_stack_relationship_promotion.py/
priority_account_publisher_scan.py/ownership_promotion.py: never invents a
fact. Proposes candidates for review; only mutations.update_contact_fields()
(existing contact) or thread_promotion.apply_baseline_entry() (new
contact) ever touches baseline_index.json, and only after an explicit
confirm via record_proposal().

Confidence-Based Auto-Recording Phase 6 (2026-09-25): same overwrite
discipline as ownership_promotion.py's twin (the other queue in scope that
can actually overwrite an existing value -- update_contact_fields()
replaces current_company/current_role outright, no history tracked).
record_proposal() auto-applies whenever proposed_confidence isn't "unknown"
(unchanged rule: "unknown" never auto-applies, and confirming it still
needs a human-supplied name). A brand-new contact, or an existing contact
with no current_company on file, is a pure add. An existing, DIFFERENT
current_company is only overwritten when the new claim's
confidence_calibration score (source_type-derived) clears the same
+0.15/0.9 margin used everywhere else in this feature; otherwise it's
appended to the contact's reported_alternates list via mutations.
add_reported_alternate() instead. Every applied write is tagged via
update_contact_fields()'s confirmed_by kwarg ("system:executive_move_
promotion" vs "human"), matching ownership_promotion.py's
owner_confirmed_by convention.

CLI:
    python3 executive_move_promotion.py scan            # scan + write candidates, print summary
    python3 executive_move_promotion.py pending          # list pending candidates
    python3 executive_move_promotion.py confirm <id> --name "X" [--title "Y"] [--contact-id Z]
    python3 executive_move_promotion.py reject <id>
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
import mutations  # noqa: E402
import thread_promotion  # noqa: E402
import identity_match_review as imr  # noqa: E402
import confidence_calibration as cc  # noqa: E402

STORE_PATH = core.SYSTEM_DIR / ".cache" / "executive_move_candidates.json"
ACCOUNT_INTELLIGENCE_DIR = core.SYSTEM_DIR / "account_intelligence"

# RB-2026-09-18 (next-sprint Workstream 1): a candidate detected today used to
# be visible ONLY via `pending`/the candidate store -- nothing surfaced it in
# the brief unless Todd went looking. record_proposal() (confirm-before-
# mutate) is unchanged; this manifest is purely a same-day VISIBILITY path,
# mirroring technomic_watchlist_scan.py's proven pattern of writing today's
# finds into a dated cache file daily_brief.py checks via a plain
# `_scan_date == today` equality. Every genuinely new candidate is eligible
# -- there is no confidence tier to gate on beyond "was a name extracted at
# all" (see proposed_confidence: "extracted_from_text" | "unknown"), and
# surfacing an "unknown" one is still useful same-day signal (it tells Todd
# *something* changed at that company even before a name is known).
PROMOTED_PATH = core.SYSTEM_DIR / ".cache" / "executive_move_promoted.json"

_LINE_SPLIT_RE = re.compile(r"\r?\n")

# RB-2026-09-11: leadership_change (ecosystem_brief.SIGNAL_CLASS_KEYWORDS)
# also fires on a bare title mention ("CEO says...", "CEO of X") and,
# confirmed live, on genuinely mis-tagged signals with zero leadership
# content at all. This local filter requires a real appointment/departure
# verb phrase -- scoped to this module only, same precedent as
# ownership_promotion.py's funding_event/acquisition split.
_APPOINTMENT_SIGNAL_RE = re.compile(
    r"\bnames\b|promoted to|appoints|appointed|joins as|joined as|new hire|"
    r"steps down|\bdeparts?\b|\bto depart\b",
    re.IGNORECASE,
)


def _is_leadership_appointment_signal(text: str) -> bool:
    return bool(_APPOINTMENT_SIGNAL_RE.search(text or ""))


_NAME_RE = r"[A-Z][A-Za-z'-]*\s+[A-Z][A-Za-z'-]*"


def _name_title_patterns(target_name: str) -> list[re.Pattern]:
    """Best-effort name+title extraction anchored on the already-known
    target company name + a real appointment-verb phrase. Validated
    against real confirmed leadership_change signals in the live graph
    before shipping (Dairy Queen/Phil Crawford, McDonald's/Bryan Brown,
    P.F. Chang's/Patrick Benson, Black Bear Diner/Anita Adams, Church's
    Texas Chicken/Roland Gonzalez) -- see test file for the exact cases.
    Name capture is deliberately exactly 2 words (first + last) -- wider
    windows over-capture a leading qualifier word ("Bryan Brown U.S."
    instead of "Bryan Brown"). A miss is just no match; never guessed
    beyond what these patterns actually find."""
    t = re.escape(target_name)
    return [
        re.compile(rf"(?i:{t})\s+(?i:names)\s+({_NAME_RE})\s+(.+?)(?=\s+-\s|$)"),
        re.compile(rf"\b({_NAME_RE})\s+(?i:promoted\s+to)\s+(.+?)\s+(?i:of)\s+(?i:{t})\b"),
        # RB-2026-09-11: confirmed live -- "appoints" (present tense) alone
        # missed a real, clean case ("McDonald's appointed Skye Anderson
        # as president...", past tense).
        re.compile(rf"(?i:{t}).{{0,20}}?(?i:appoints?|appointed)\s+({_NAME_RE})\s+(?i:as)\s+(.+?)(?=\s+-\s|$)"),
        re.compile(rf"\b({_NAME_RE})\s+(?i:joins)\s+(?i:{t})\s+(?i:as)\s+(.+?)(?=\s+-\s|$)"),
        # RB-2026-09-11: confirmed live against real account_intelligence
        # notes -- Todd's own "- **Name — Title.**" bullet convention
        # (e.g. "Zerrick Pearson — Chief Information Officer",
        # "Mark Graff — Chief Financial Officer"). Not target-anchored --
        # both callers already only pass text already confirmed to be
        # about the target (a signal's own summary, or a doc line the
        # caller already matched the target's name against).
        re.compile(rf"\*\*({_NAME_RE})\s+—\s+(.+?)\.\*\*"),
        re.compile(rf"\b({_NAME_RE})\s+—\s+(.+?)\."),
    ]


_IMPLAUSIBLE_TITLE_WORDS = {
    "because", "while", "supporting", "who", "which", "that", "following",
    "after", "before", "according",
}


def _is_plausible_title(title: str) -> bool:
    words = title.split()
    if not words or len(words) > 8:
        return False
    return not any(w.strip(".,'\"").lower() in _IMPLAUSIBLE_TITLE_WORDS for w in words)


_TITLE_SHAPED_WORDS = {
    "new", "president", "ceo", "cto", "cio", "coo", "cfo", "chief", "chairman",
    "director", "vp", "vice", "senior", "executive", "seasoned", "restaurant",
    "industry", "leader", "leadership", "operator", "veteran", "officer",
}


def _is_plausible_person_name(name: str) -> bool:
    """RB-2026-09-11, confirmed live against real production leadership_change
    signals: a 2-capitalized-word capture doesn't guarantee an actual
    person's name -- "NOTHING BUNDT CAKES NAMES NEW CEO" captured "New CEO"
    as if it were a name, and "WaBa Grill Names Seasoned Restaurant
    Operator Afshin Compani..." captured "Seasoned Restaurant" instead of
    the real name appearing later in the sentence. Real personal names
    essentially never coincide with common title/descriptor words -- reject
    the match rather than propose an obviously-wrong "name"."""
    words = [w.strip(".,'\"").lower() for w in name.split()]
    return not any(w in _TITLE_SHAPED_WORDS for w in words)


def _extract_executive(target_name: str, text: str) -> tuple[Optional[str], Optional[str], str]:
    """Returns (name_or_None, title_or_None, confidence). confidence is
    "extracted_from_text" when a name was found (title may or may not
    accompany it), "unknown" otherwise."""
    for pattern in _name_title_patterns(target_name):
        m = pattern.search(text or "")
        if not m:
            continue
        name = m.group(1).strip().strip("'\"")
        title = m.group(2).strip().strip("'\".")
        if len(name.split()) != 2:
            continue
        if not _is_plausible_person_name(name):
            continue
        if not _is_plausible_title(title):
            title = None
        return name, title, "extracted_from_text"
    return None, None, "unknown"


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
    """Record newly-detected candidates in today's dated manifest so
    daily_brief.py can surface them same-day. Purely additive visibility --
    does not touch `store`/candidate status and never mutates baseline_index
    or ecosystem_intelligence (that stays gated on record_proposal())."""
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
            "proposed_name": cand["proposed_name"],
            "proposed_title": cand["proposed_title"],
            "proposed_confidence": cand["proposed_confidence"],
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


def _baseline_name_index(baseline: list[dict]) -> dict[str, dict]:
    """Reuses identity_match_review._normalize_name() directly -- same
    exact-normalized-name-only limitation that module already carries, not
    improved on here (no fuzzy/nickname matching)."""
    idx: dict[str, dict] = {}
    for entry in baseline:
        key = imr._normalize_name(entry.get("name"))
        if key:
            idx[key] = entry
    return idx


def _add_candidate(
    store: dict, *, entity_id: str, entity_name: str, proposed_name: Optional[str],
    proposed_title: Optional[str], confidence: str, matched_contact_id: Optional[str],
    source_type: str, source_title: str, source_url: Optional[str], evidence_excerpt: str,
    origin: str, origin_ref: str, evidence_date: Optional[str] = None,
) -> bool:
    cid = _candidate_id(entity_id, origin_ref)
    if cid in store["candidates"]:
        return False
    store["candidates"][cid] = {
        "candidate_id": cid,
        "status": "proposed_pending_confirmation",
        "entity_id": entity_id,
        "entity_name": entity_name,
        "proposed_name": proposed_name,
        "proposed_title": proposed_title,
        "proposed_confidence": confidence,  # "extracted_from_text" | "unknown"
        # Which real path record_proposal() will take on confirm --
        # surfaced now so the reviewer sees it before deciding, not a
        # decision this module makes silently.
        "action": "update_existing_contact" if matched_contact_id else "create_new_contact",
        "matched_contact_id": matched_contact_id,
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
    graph = ei._read_graph()
    by_id = ei._index_by_id(graph.get("entities") or [])
    baseline = core.load_baseline(core.BASELINE_PATH)
    name_index = _baseline_name_index(baseline)
    store = _load_store()

    scanned = 0
    new_count = 0
    new_ids: list[str] = []
    to_auto_apply: list[str] = []
    for sig in graph.get("signals") or []:
        if sig.get("signal_type") != "leadership_change":
            continue
        summary = sig.get("summary") or ""
        if not _is_leadership_appointment_signal(summary):
            continue
        scanned += 1
        for entity_id in sig.get("entities") or []:
            entity = by_id.get(entity_id)
            if not entity or entity.get("entity_type") not in ("brand", "vendor"):
                continue
            name, title, confidence = _extract_executive(entity.get("name", ""), summary)
            matched = name_index.get(imr._normalize_name(name)) if name else None
            source_id = (sig.get("sources") or [None])[0]
            source = next((s for s in graph.get("sources") or [] if s.get("id") == source_id), None)
            cid = _candidate_id(entity_id, sig["id"])
            added = _add_candidate(
                store, entity_id=entity_id, entity_name=entity.get("name", ""),
                proposed_name=name, proposed_title=title, confidence=confidence,
                matched_contact_id=matched.get("id") if matched else None,
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
            result = record_proposal(cid, confirmed=True, confirmed_by="system:executive_move_promotion", auto_apply_gate=True)
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
    import account_reference_detector as ard  # noqa: E402

    graph = ei._read_graph()
    entities = [e for e in graph.get("entities") or [] if e.get("entity_type") in ("brand", "vendor")]
    baseline = core.load_baseline(core.BASELINE_PATH)
    name_index = _baseline_name_index(baseline)
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
            if not _is_leadership_appointment_signal(line):
                continue
            matched_entities = [e for e in entities if ard._name_in_text(e.get("name", ""), line.lower())]
            for entity in matched_entities:
                name, title, confidence = _extract_executive(entity.get("name", ""), line)
                matched_contact = name_index.get(imr._normalize_name(name)) if name else None
                origin_ref = f"{path.name}#L{line_no}"
                cid = _candidate_id(entity["id"], origin_ref)
                added = _add_candidate(
                    store, entity_id=entity["id"], entity_name=entity.get("name", ""),
                    proposed_name=name, proposed_title=title, confidence=confidence,
                    matched_contact_id=matched_contact.get("id") if matched_contact else None,
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
            result = record_proposal(cid, confirmed=True, confirmed_by="system:executive_move_promotion", auto_apply_gate=True)
            if result.get("confirmed"):
                auto_applied += 1
    return {"docs_scanned": scanned, "new_candidates": new_count, "new_candidate_ids": new_ids,
            "auto_applied": auto_applied,
            "total_pending": sum(1 for c in store["candidates"].values()
                                  if c["status"] == "proposed_pending_confirmation")}


def scan(*, dry_run: bool = False) -> dict:
    signals_result = scan_existing_signals(dry_run=dry_run)
    docs_result = scan_account_intelligence_docs(dry_run=dry_run)
    new_ids = signals_result.pop("new_candidate_ids", []) + docs_result.pop("new_candidate_ids", [])
    if not dry_run and new_ids:
        _promote_same_day(_load_store(), new_ids)
    return {"signals": signals_result, "account_intelligence": docs_result}


def pending_candidates() -> list[dict]:
    store = _load_store()
    return [c for c in store["candidates"].values() if c["status"] == "proposed_pending_confirmation"]


# An existing current_company with no employment_confidence recorded
# predates this feature -- every current_company write ever made on an
# existing contact went through this same record_proposal(), always after
# a human confirm, so it's treated as a deliberate human decision at
# "high" trust, not a blank slate a routine trade-press claim can casually
# overwrite. Same reasoning/value as ownership_promotion.py's twin.
_UNSCORED_EXISTING_EMPLOYMENT_SCORE = cc.CONFIDENCE_BAND_TO_SCHEMA["high"][1]


def record_proposal(
    candidate_id: str, *, confirmed: bool, name: Optional[str] = None, title: Optional[str] = None,
    contact_id: Optional[str] = None, confirmed_by: str = "human", auto_apply_gate: bool = False,
) -> dict:
    """Confirm (write via mutations.update_contact_fields() for an
    existing-contact match, or thread_promotion.apply_baseline_entry() for
    a new contact) or reject one pending candidate.

    name is required to confirm -- either already prefilled on the
    candidate (proposed_name) or supplied here, whichever the caller
    provides last. contact_id lets the reviewer correct a wrong
    existing-contact match (or supply one the scanner didn't find) --
    when given, always overrides the candidate's own matched_contact_id.

    Confidence-Based Auto-Recording Phase 6: auto_apply_gate=True (used
    only by scan()'s own unattended auto-apply loop) means a matched
    contact with a DIFFERENT current_company already on file only gets
    overwritten when this claim's confidence_calibration score clears the
    same +0.15/0.9 margin used elsewhere in this feature -- otherwise it's
    appended to reported_alternates instead. An explicit confirm
    (auto_apply_gate=False, the default -- the CLI `confirm` command, a
    Team Portal action, any caller resolving one specific candidate)
    always applies directly, exactly like before this feature: a human
    choosing to confirm THIS candidate is authoritative, not silently
    redirected."""
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

    resolved_name = (name or "").strip() or cand.get("proposed_name")
    if not resolved_name:
        return {
            "error": (
                f"candidate {candidate_id} has no executive name (proposed_confidence="
                "'unknown') -- supply name to confirm, or reject this candidate; "
                "never guessed here."
            ),
        }
    resolved_title = (title or "").strip() or cand.get("proposed_title")
    resolved_contact_id = contact_id or cand.get("matched_contact_id")
    source_ref = f"executive_move_promotion::{cand['origin_ref']}"
    level, new_score = cc.score_for_source(cand.get("source_type"))

    if resolved_contact_id:
        baseline = core.load_baseline(core.BASELINE_PATH)
        entry = next((e for e in baseline if e.get("id") == resolved_contact_id), None)
        if entry is None:
            return {"error": f"no contact with id={resolved_contact_id!r}"}
        existing_company = (entry.get("current_company") or "").strip()

        if auto_apply_gate and existing_company and existing_company.lower() != cand["entity_name"].strip().lower():
            existing_conf = entry.get("employment_confidence") or {}
            existing_score = existing_conf.get("score")
            if not isinstance(existing_score, (int, float)):
                existing_score = _UNSCORED_EXISTING_EMPLOYMENT_SCORE
            if not cc.should_supersede(new_score, existing_score):
                alt_result = mutations.add_reported_alternate(resolved_contact_id, {
                    "current_company": cand["entity_name"],
                    "current_role": resolved_title,
                    "confidence": {"level": level, "score": new_score},
                    "source_title": cand.get("source_title"),
                    "source_url": cand.get("source_url"),
                    "evidence_date": cand.get("evidence_date"),
                    "recorded_at": _now_iso(),
                    "candidate_id": candidate_id,
                })
                if not alt_result.get("ok"):
                    return {"error": alt_result.get("error", f"failed to record alternate for {resolved_contact_id}")}
                cand["status"] = "confirmed"
                cand["resolved_at"] = _now_iso()
                cand["confirmed_by"] = confirmed_by
                cand["outcome"] = "recorded_alongside"
                _save_store(store)
                return {
                    "confirmed": True, "candidate_id": candidate_id, "outcome": "recorded_alongside",
                    "action": "reported_alternate", "contact_id": resolved_contact_id,
                    "existing_current_company": existing_company, "reported_alternate": cand["entity_name"],
                }

        result = mutations.update_contact_fields(
            resolved_contact_id, current_company=cand["entity_name"], current_role=resolved_title,
            source=source_ref, employment_confidence={"level": level, "score": new_score},
            confirmed_by=confirmed_by,
        )
        if not result.get("ok"):
            return {"error": result.get("error", f"failed to update contact {resolved_contact_id}")}
        write_result = {"action": "update_existing_contact", "contact_id": resolved_contact_id}
    else:
        new_contact_id = thread_promotion._slug(resolved_name)
        entry = {
            "id": new_contact_id, "name": resolved_name, "email": None,
            "current_company": cand["entity_name"], "current_role": resolved_title,
            "status": "prospect", "relationship_stage": "new_contact",
            "narrative": f"Named {resolved_title or 'a new role'} at {cand['entity_name']} ({cand['source_title']}).",
            "source": source_ref, "created_at": date.today().isoformat(),
            "signal_class": "VC", "sources": [source_ref],
            "employment_confidence": {"level": level, "score": new_score},
            "current_company_confirmed_by": confirmed_by,
            "rc_state": None, "rc_tier": None, "last_touch": None, "circles": [], "tags": [],
        }
        applied = thread_promotion.apply_baseline_entry(entry, baseline_path=core.BASELINE_PATH)
        if not applied.get("applied"):
            return {"error": f"could not create contact: {applied.get('reason')}"}
        write_result = {"action": "create_new_contact", "contact_id": applied["id"]}

    cand["status"] = "confirmed"
    cand["resolved_at"] = _now_iso()
    cand["confirmed_name"] = resolved_name
    cand["confirmed_title"] = resolved_title
    cand["confirmed_contact_id"] = write_result["contact_id"]
    cand["confirmed_by"] = confirmed_by
    cand["outcome"] = "applied"
    _save_store(store)
    return {"confirmed": True, "candidate_id": candidate_id, "outcome": "applied", **write_result,
            "name": resolved_name, "title": resolved_title}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    sub.add_parser("pending")
    confirm_p = sub.add_parser("confirm")
    confirm_p.add_argument("candidate_id")
    confirm_p.add_argument("--name", default=None)
    confirm_p.add_argument("--title", default=None)
    confirm_p.add_argument("--contact-id", default=None)
    reject_p = sub.add_parser("reject")
    reject_p.add_argument("candidate_id")
    args = p.parse_args()

    if args.cmd == "scan":
        print(json.dumps(scan(), indent=2, ensure_ascii=False))
    elif args.cmd == "pending":
        print(json.dumps(pending_candidates(), indent=2, ensure_ascii=False))
    elif args.cmd == "confirm":
        result = record_proposal(args.candidate_id, confirmed=True,
                                  name=args.name, title=args.title, contact_id=args.contact_id)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.cmd == "reject":
        print(json.dumps(record_proposal(args.candidate_id, confirmed=False), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
