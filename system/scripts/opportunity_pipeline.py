#!/usr/bin/env python3
"""
opportunity_pipeline.py — RB-DEFECT-037: Active Opportunity Pipeline persistence.

Background
-----------
RB previously had no persistence model for the user's own active career/
business opportunity pipeline (job offers, candidate ranking, interview
timelines, deal stage). `relationship_intake.py` only captures generic
contact-touch events; `insight_intake.py` only captures market/thesis
content; `job_intelligence.py` is a read-only scanner for new postings.

When the user told the GPT "I received a verbal offer from Global Payments
... and Foods Connected confirmed I'm one of the final two candidates, with
final interviews June 23-24", none of those modules had anywhere to put that
information — so it was lost, and the next day's brief had no record of it.

This module closes that gap: a small, durable, review-first store for
*active opportunities the user is personally pursuing* (jobs, consulting
engagements, advisory roles, etc.), with a stage taxonomy and a change
history so the daily brief can answer "what changed since yesterday?" for
the user's own pipeline — not just market/relationship intelligence.

Invariants:
  - All mutations require_confirmation=True by default. apply=True persists
    immediately (same pattern as other intake endpoints' apply flag).
  - persistence_status is always explicit — never silent.
  - Every update appends to the opportunity's `history` list, so the brief
    can render "NEW SINCE LAST BRIEF" vs "NO MATERIAL CHANGE DETECTED".
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent.parent
TRACKED_OPPORTUNITIES_PATH = SYSTEM_DIR / "tracked_opportunities.json"

# ---------------------------------------------------------------------------
# Stage taxonomy
# ---------------------------------------------------------------------------

STAGES = [
    "target_identified",
    "applied",
    "screening",
    "interviewing",
    "final_round",
    "offer_verbal",
    "offer_written",
    "negotiating",
    "accepted",
    "declined",
    "rejected",
    "closed",
]

# Stage rank — used to detect forward/backward movement for history framing.
STAGE_RANK = {stage: i for i, stage in enumerate(STAGES)}

# Ordered most-specific-first; first match wins.
_STAGE_INDICATORS: list[tuple[str, tuple[str, ...]]] = [
    ("offer_written", (
        "written offer", "formal offer", "offer letter", "signed offer",
    )),
    ("accepted", (
        "accepted the offer", "i accepted", "i've accepted", "accepted offer",
    )),
    ("declined", (
        "declined the offer", "i declined", "turning down the offer", "i'm declining",
    )),
    ("rejected", (
        "rejected me", "i was rejected", "passed on me", "went with another candidate",
        "not moving forward with my candidacy", "they passed",
    )),
    ("negotiating", (
        "negotiating", "counter offer", "counteroffer", "negotiation",
    )),
    ("offer_verbal", (
        "verbal offer", "offered me the role", "offered me the position",
        "extended a verbal", "informal offer",
    )),
    ("final_round", (
        "final round", "final interview", "final two candidates", "final-two",
        "down to two candidates", "one of the final", "finalist",
    )),
    ("interviewing", (
        "interview scheduled", "had an interview", "phone screen", "onsite interview",
        "interview with", "interviewing for",
    )),
    ("screening", (
        "recruiter screen", "initial screen", "screening call",
    )),
    ("applied", (
        "applied for", "submitted my application", "application submitted",
    )),
]

# Trigger phrases for triageInput's career_pipeline_update classifier.
CAREER_PIPELINE_TRIGGERS: tuple[str, ...] = tuple(
    phrase for _, phrases in _STAGE_INDICATORS for phrase in phrases
) + (
    "candidate position", "offer package", "comp package", "compensation package",
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"opp-{ts}-{uuid.uuid4().hex[:6]}"


def _opportunity_id_from_company(company: str) -> str:
    slug = re.sub(r"[^a-z0-9\s]", "", company.lower())
    return re.sub(r"\s+", "-", slug.strip())


def _classify_stage(text: str) -> str | None:
    text_lower = text.lower()
    for stage, indicators in _STAGE_INDICATORS:
        if any(ind in text_lower for ind in indicators):
            return stage
    return None


def _extract_company(text: str, known_companies: list[str] | None = None) -> str | None:
    """Match against target_companies from opportunity_context.yaml, then
    fall back to a generic 'at <Company>' / '<Company> process' pattern."""
    for company in known_companies or []:
        if re.search(re.escape(company), text, re.IGNORECASE):
            return company

    m = re.search(r"\bfrom ([A-Z][A-Za-z0-9&.,' ]{1,40}?)(?:\s+and\b|[.,\n]|$)", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"\bat ([A-Z][A-Za-z0-9&.,' ]{1,40}?)(?:\s+about\b|[.,\n]|$)", text)
    if m:
        return m.group(1).strip()
    return None


def _extract_candidate_position(text: str) -> str | None:
    m = re.search(
        r"(one of the final (?:two|three|2|3) candidates|final[- ]two candidates|"
        r"down to (?:two|three|2|3) candidates)",
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1)
    return None


def _extract_key_dates(text: str) -> list[dict]:
    """Best-effort extraction of date references near 'interview'/'final' language."""
    dates: list[dict] = []
    for m in re.finditer(
        r"(interview[s]?|final round[s]?)\s+(?:expected|scheduled|on|for)?\s*"
        r"((?:January|February|March|April|May|June|July|August|September|October|"
        r"November|December)\s+\d{1,2}(?:[–\-]\d{1,2})?)",
        text, re.IGNORECASE,
    ):
        dates.append({"label": m.group(1).strip().title(), "date_text": m.group(2).strip()})
    return dates


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_store(store_path: Path | None = None) -> dict:
    path = store_path or TRACKED_OPPORTUNITIES_PATH
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"_schema_version": "1.0", "opportunities": []}


def _save_store(store: dict, store_path: Path | None = None) -> None:
    path = store_path or TRACKED_OPPORTUNITIES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    store["_last_updated"] = _timestamp()
    path.write_text(json.dumps(store, indent=2, default=str))


def _find_opportunity(store: dict, opportunity_id: str) -> dict | None:
    for opp in store.get("opportunities", []):
        if opp.get("id") == opportunity_id:
            return opp
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def process_opportunity_update(
    text: str,
    company: str | None = None,
    role: str | None = None,
    stage_override: str | None = None,
    source_type: str = "conversation",
    apply: bool = False,
    known_companies: list[str] | None = None,
    store_path: Path | None = None,
) -> dict:
    """Detect and propose an update to the user's active opportunity pipeline.

    Returns:
      detected            — bool, whether an opportunity-pipeline signal was found
      opportunity         — proposed/updated opportunity record
      mutation_proposals  — review-first mutation(s) against tracked_opportunities.json
      persistence_status  — "applied" | "pending confirmation" | "RB did not persist"
      what_changed        — human-readable diff summary for brief "what changed" framing
    """
    if not text or not text.strip():
        return {
            "detected": False,
            "opportunity": None,
            "mutation_proposals": [],
            "persistence_status": "RB did not persist",
            "what_changed": None,
            "note": "Empty input. No opportunity-pipeline signal detected.",
        }

    stage = stage_override if stage_override in STAGES else _classify_stage(text)
    detected_company = company or _extract_company(text, known_companies)

    if not stage and not detected_company:
        return {
            "detected": False,
            "opportunity": None,
            "mutation_proposals": [],
            "persistence_status": "RB did not persist",
            "what_changed": None,
            "note": (
                "No opportunity-pipeline stage or company detected. "
                "Provide company and/or stage_override to record an update."
            ),
        }

    if not detected_company:
        return {
            "detected": True,
            "opportunity": None,
            "mutation_proposals": [],
            "persistence_status": "RB did not persist",
            "what_changed": None,
            "note": (
                f"Detected stage signal '{stage}' but no company could be resolved. "
                "Provide company explicitly."
            ),
        }

    opportunity_id = _opportunity_id_from_company(detected_company)
    candidate_position = _extract_candidate_position(text)
    key_dates = _extract_key_dates(text)
    today_str = date.today().isoformat()
    now = _timestamp()

    store = _load_store(store_path=store_path)
    existing = _find_opportunity(store, opportunity_id)

    history_entry = {
        "date": today_str,
        "recorded_at": now,
        "stage": stage or (existing or {}).get("stage") or "target_identified",
        "narrative": text[:500],
        "candidate_position": candidate_position,
        "key_dates": key_dates,
        "source_type": source_type,
    }

    what_changed_parts = []
    if existing:
        prev_stage = existing.get("stage")
        new_stage = stage or prev_stage
        if stage and stage != prev_stage:
            direction = "advanced" if STAGE_RANK.get(stage, -1) > STAGE_RANK.get(prev_stage, -1) else "moved"
            what_changed_parts.append(f"Stage {direction}: {prev_stage} -> {stage}")
        if candidate_position and candidate_position != existing.get("candidate_position"):
            what_changed_parts.append(f"Candidate position update: {candidate_position}")
        if key_dates:
            existing_dates = {(d.get("label"), d.get("date_text")) for d in existing.get("key_dates") or []}
            new_dates = [d for d in key_dates if (d.get("label"), d.get("date_text")) not in existing_dates]
            for d in new_dates:
                what_changed_parts.append(f"New key date: {d['label']} {d['date_text']}")
        opportunity = dict(existing)
        opportunity["company"] = detected_company
        opportunity["role"] = role or existing.get("role")
        opportunity["stage"] = new_stage
        opportunity["status_narrative"] = text[:500]
        opportunity["candidate_position"] = candidate_position or existing.get("candidate_position")
        opportunity["key_dates"] = (existing.get("key_dates") or []) + [
            d for d in key_dates
            if (d.get("label"), d.get("date_text")) not in {
                (e.get("label"), e.get("date_text")) for e in existing.get("key_dates") or []
            }
        ]
        opportunity["last_updated"] = today_str
        opportunity["history"] = (existing.get("history") or []) + [history_entry]
    else:
        what_changed_parts.append(
            f"New tracked opportunity: {detected_company}"
            + (f" ({stage})" if stage else "")
        )
        opportunity = {
            "id": opportunity_id,
            "company": detected_company,
            "role": role,
            "stage": stage or "target_identified",
            "status_narrative": text[:500],
            "candidate_position": candidate_position,
            "key_dates": key_dates,
            "created_at": today_str,
            "last_updated": today_str,
            "history": [history_entry],
        }

    what_changed = "; ".join(what_changed_parts) if what_changed_parts else "No material change detected."

    mutation_proposal = {
        "mutation_type": "opportunity_upsert",
        "target": "system/tracked_opportunities.json",
        "operation": "upsert",
        "opportunity_id": opportunity_id,
        "opportunity_data": opportunity,
        "requires_confirmation": not apply,
        "persistence_endpoint": "POST /opportunity/update",
    }

    if apply:
        idx = next(
            (i for i, o in enumerate(store.get("opportunities", []))
             if o.get("id") == opportunity_id),
            None,
        )
        if idx is not None:
            store["opportunities"][idx] = opportunity
        else:
            store.setdefault("opportunities", []).append(opportunity)
        _save_store(store, store_path=store_path)
        persistence_status = "applied"
    else:
        persistence_status = "pending confirmation"

    return {
        "detected": True,
        "opportunity": opportunity,
        "mutation_proposals": [mutation_proposal],
        "persistence_status": persistence_status,
        "what_changed": what_changed,
    }


def query_pipeline(
    active_only: bool = True,
    store_path: Path | None = None,
) -> dict:
    """Return all tracked opportunities, most-recently-updated first.

    active_only=True (default) excludes opportunities in terminal stages
    (accepted, declined, rejected, closed).
    """
    store = _load_store(store_path=store_path)
    opportunities = list(store.get("opportunities") or [])

    if active_only:
        terminal = {"accepted", "declined", "rejected", "closed"}
        opportunities = [o for o in opportunities if o.get("stage") not in terminal]

    opportunities.sort(key=lambda o: o.get("last_updated") or "", reverse=True)

    return {
        "contract": "rb_opportunity_pipeline_v1",
        "opportunities": opportunities,
        "count": len(opportunities),
        "generated_at": _timestamp(),
    }


def recent_changes(within_days: int = 1, store_path: Path | None = None) -> list[dict]:
    """Return opportunities with a history entry dated within the last
    `within_days` days — used by the daily brief's 'what changed' framing."""
    store = _load_store(store_path=store_path)
    cutoff = date.today()
    results = []
    for opp in store.get("opportunities") or []:
        recent_entries = []
        for entry in opp.get("history") or []:
            try:
                entry_date = date.fromisoformat(entry.get("date", ""))
            except ValueError:
                continue
            if (cutoff - entry_date).days <= within_days:
                recent_entries.append(entry)
        if recent_entries:
            results.append({**opp, "recent_history": recent_entries})
    return results
