#!/usr/bin/env python3
"""
cos_judgment.py — Chief-of-Staff judgment layer for RB 9.20.

Produces deterministic, evidence-bound CoS blocks:
  - source_freshness: per-source labels and stale flags
  - cos_judgment: hard truths, focus leaks, unsupported assumptions, execution pressure
  - what_is_not_happening: detected absences and strategic silences
  - execution_options: closure options for every recommendation
  - macro_to_operator_synthesis: reads from cache; labels stale/unavailable
  - behavioral_intelligence: reads proposed/confirmed macro behavioral signals
  - linkedin_relationship_delta: reads from cache; labels stale; guards against current-state claims

Invariants:
  - Hard truths are evidence-bound. No invented criticism.
  - When evidence is absent: "RB is under-instrumented here; this conclusion cannot be trusted yet."
  - No auto-send, no auto-close, no relationship-tier promotion from LinkedIn edge alone.
  - Write-like execution options always set requires_confirmation=True.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

SYSTEM_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = SYSTEM_DIR / ".cache"
BEHAVIORAL_INTELLIGENCE_PATH = SYSTEM_DIR / "behavioral_intelligence.json"

# Freshness thresholds
INBOX_STALE_HOURS = 6
LINKEDIN_STALE_DAYS = 7
MACRO_STALE_DAYS = 7

# Allowed execution action types
EXECUTION_ACTIONS = frozenset({
    "create_task",
    "open_loop",
    "monitor_relationship",
    "schedule_reminder",
    "escalate_priority",
    "add_to_opportunity_pipeline",
    "open_outreach_loop",
    "ignore",
})

# Write-like actions that require explicit confirmation
WRITE_ACTIONS = frozenset({"open_loop", "create_task", "open_outreach_loop"})

# --------------------------------------------------------------------------- #
# Source freshness                                                             #
# --------------------------------------------------------------------------- #

def _age_hours(fetched_at: str | None) -> float | None:
    if not fetched_at:
        return None
    try:
        ts = datetime.fromisoformat(fetched_at)
        return (datetime.now() - ts).total_seconds() / 3600
    except (ValueError, TypeError):
        return None


def _age_days_from_epoch(epoch: int | float | None) -> float | None:
    if epoch is None:
        return None
    return (datetime.now() - datetime.fromtimestamp(epoch)).total_seconds() / 86400


def build_source_freshness(report: dict) -> dict:
    """Produce a per-source freshness status dict.

    Labels: fresh | stale | source_unavailable | refresh_failed | historical_memory | inferred
    """
    sources: dict[str, dict] = {}

    # Email
    em = report.get("email") or {}
    if em.get("fetched_at"):
        age_h = _age_hours(em["fetched_at"])
        label = "stale" if (em.get("stale") or (age_h is not None and age_h > INBOX_STALE_HOURS)) else "fresh"
        sources["email"] = {
            "label": label,
            "fetched_at": em["fetched_at"],
            "accounts": em.get("accounts_seen") or [],
            "stale": label == "stale",
            "note": f"Last refresh {age_h:.1f}h ago." if age_h is not None else None,
        }
    else:
        sources["email"] = {
            "label": "source_unavailable",
            "fetched_at": None,
            "stale": True,
            "note": "No email overlay fetched. Direct-communications coverage is zero.",
        }

    # Calendar
    cal = report.get("calendar") or {}
    if cal.get("fetched_at"):
        age_h = _age_hours(cal["fetched_at"])
        label = "stale" if (cal.get("stale") or (age_h is not None and age_h > INBOX_STALE_HOURS)) else "fresh"
        sources["calendar"] = {
            "label": label,
            "fetched_at": cal["fetched_at"],
            "stale": label == "stale",
        }
    else:
        sources["calendar"] = {
            "label": "source_unavailable",
            "fetched_at": None,
            "stale": True,
            "note": "No calendar overlay fetched.",
        }

    # Social
    soc = report.get("social") or {}
    if soc.get("fetched_at"):
        age_h = _age_hours(soc["fetched_at"])
        label = "stale" if (soc.get("stale") or (age_h is not None and age_h > INBOX_STALE_HOURS * 4)) else "fresh"
        sources["social"] = {
            "label": label,
            "fetched_at": soc["fetched_at"],
            "stale": label == "stale",
        }
    else:
        sources["social"] = {
            "label": "source_unavailable",
            "fetched_at": None,
            "stale": True,
            "note": "No social overlay fetched.",
        }

    # LinkedIn ingest cache
    li_path = CACHE_DIR / "linkedin_ingest_latest.json"
    if li_path.exists():
        try:
            li_raw = json.loads(li_path.read_text())
            ingested_at = li_raw.get("_generated_at") or li_raw.get("ingested_at")
            age_d = _age_hours(ingested_at) / 24 if ingested_at and _age_hours(ingested_at) is not None else None
            stale = age_d is None or age_d > LINKEDIN_STALE_DAYS
            if stale and age_d is not None:
                _li_note = (
                    f"LinkedIn delta cache is {age_d:.1f} days old — older than {LINKEDIN_STALE_DAYS}d threshold. "
                    "Not used for current-state claims."
                )
            elif stale:
                _li_note = (
                    f"LinkedIn delta cache has no timestamp — treating as stale. "
                    "Run linkedin_ingest.py to refresh."
                )
            else:
                _li_note = None
            sources["linkedin_delta"] = {
                "label": "stale" if stale else "fresh",
                "ingested_at": ingested_at,
                "stale": stale,
                "note": _li_note,
            }
        except (json.JSONDecodeError, Exception):
            sources["linkedin_delta"] = {
                "label": "refresh_failed",
                "stale": True,
                "note": "LinkedIn delta cache exists but could not be parsed.",
            }
    else:
        sources["linkedin_delta"] = {
            "label": "source_unavailable",
            "stale": True,
            "note": "No LinkedIn delta cache found. Run linkedin_ingest.py to generate.",
        }

    # Macro synthesis cache
    macro_path = CACHE_DIR / "macro_synthesis.json"
    if macro_path.exists():
        try:
            mc_raw = json.loads(macro_path.read_text())
            gen_at = mc_raw.get("_generated_at")
            age_d = _age_hours(gen_at) / 24 if gen_at and _age_hours(gen_at) is not None else None
            stale = age_d is None or age_d > MACRO_STALE_DAYS
            sources["macro_synthesis"] = {
                "label": "stale" if stale else "fresh",
                "generated_at": gen_at,
                "stale": stale,
            }
        except Exception:
            sources["macro_synthesis"] = {"label": "refresh_failed", "stale": True}
    else:
        sources["macro_synthesis"] = {
            "label": "source_unavailable",
            "stale": True,
            "note": "No macro synthesis cache. Market signals unavailable.",
        }

    # Baseline
    baseline_path = SYSTEM_DIR / "baseline_index.json"
    if baseline_path.exists():
        import os
        mtime = os.path.getmtime(str(baseline_path))
        age_d = _age_days_from_epoch(mtime)
        sources["baseline"] = {
            "label": "fresh" if (age_d is not None and age_d <= 1) else "historical_memory",
            "last_modified_days_ago": round(age_d, 1) if age_d is not None else None,
            "stale": age_d is not None and age_d > 7,
        }
    else:
        sources["baseline"] = {"label": "source_unavailable", "stale": True}

    # Summarize tier-1 coverage
    tier1_stale = [k for k in ("email",) if sources.get(k, {}).get("stale")]
    tier1_unavailable = [k for k in ("email",) if sources.get(k, {}).get("label") == "source_unavailable"]

    return {
        "sources": sources,
        "tier1_stale": tier1_stale,
        "tier1_unavailable": tier1_unavailable,
        "direct_comms_covered": not sources["email"]["stale"],
        "quiet_claim_allowed": not (sources["email"]["stale"] or sources["email"]["label"] == "source_unavailable"),
    }


# --------------------------------------------------------------------------- #
# Hard-truth heuristics                                                        #
# --------------------------------------------------------------------------- #

def _recent_loop_closures(loops_buckets: dict, today: date, window_days: int = 14) -> list:
    cutoff = today - timedelta(days=window_days)
    closed = loops_buckets.get("closed") or []
    recent = []
    for L in closed:
        try:
            t = L.get("target") if isinstance(L, dict) else getattr(L, "target", None)
            if t is None:
                continue
            t_date = date.fromisoformat(t) if isinstance(t, str) else t
            if t_date >= cutoff:
                recent.append(L)
        except (ValueError, AttributeError):
            pass
    return recent


def _loop_party_names(loops_buckets: dict) -> set[str]:
    names: set[str] = set()
    for bucket_key in ("overdue", "due_today", "this_week", "future"):
        for L in (loops_buckets.get(bucket_key) or []):
            party = L.get("party") if isinstance(L, dict) else getattr(L, "party", "")
            if party:
                names.add(party.strip().lower())
    return names


def _thread_has_loop(thread: dict, loops_buckets: dict) -> bool:
    """Return True if any open loop references a person or company in the thread.

    Handles both hyphenated IDs (jane-doe) and space-separated names (Jane Doe)
    by normalizing both to lowercase and treating hyphens as spaces.
    """
    def _norm(s: str) -> str:
        return s.strip().lower().replace("-", " ")

    people_raw = [p.strip().lower() for p in (thread.get("people") or [])]
    # Normalized forms: "jane-doe" → "jane doe"
    people = [_norm(p) for p in (thread.get("people") or [])]
    companies = [_norm(c) for c in (thread.get("companies") or [])]
    thread_title_words = set((thread.get("title") or "").lower().split())

    for bucket_key in ("overdue", "due_today", "this_week", "future"):
        for L in (loops_buckets.get(bucket_key) or []):
            party = (L.get("party") if isinstance(L, dict) else getattr(L, "party", "")) or ""
            party_norm = _norm(party)
            desc = (L.get("description") if isinstance(L, dict) else getattr(L, "description", "")) or ""
            desc_norm = _norm(desc)

            if any(p in party_norm or p in desc_norm for p in people if p):
                return True
            if any(c in party_norm or c in desc_norm for c in companies if c):
                return True
            # Thread title keyword match (words > 4 chars to avoid common words)
            if any(w in desc_norm for w in thread_title_words if len(w) > 4):
                return True
    return False


def _count_architecture_loops(loops_buckets: dict) -> int:
    ARCH_KEYWORDS = {"architecture", "rb", "system", "build", "product", "pipeline", "schema", "sprint"}
    count = 0
    for bucket_key in ("overdue", "due_today", "this_week", "future"):
        for L in (loops_buckets.get(bucket_key) or []):
            desc = (L.get("description") if isinstance(L, dict) else getattr(L, "description", "")) or ""
            if any(kw in desc.lower() for kw in ARCH_KEYWORDS):
                count += 1
    return count


def _count_revenue_loops(loops_buckets: dict) -> int:
    REV_KEYWORDS = {"proposal", "commercial", "engagement", "retainer", "pitch", "revenue", "consulting", "sale", "contract", "agreement"}
    count = 0
    for bucket_key in ("overdue", "due_today", "this_week", "future"):
        for L in (loops_buckets.get(bucket_key) or []):
            desc = (L.get("description") if isinstance(L, dict) else getattr(L, "description", "")) or ""
            if any(kw in desc.lower() for kw in REV_KEYWORDS):
                count += 1
    return count


def build_hard_truths(report: dict, today: date, source_freshness: dict) -> list[dict]:
    truths: list[dict] = []
    loops_buckets = report.get("loops") or {}
    crossings = report.get("crossings") or []
    social = report.get("social") or {}
    active_threads = report.get("active_threads") or []
    drr_top = report.get("drr_top") or []

    # T1: Execution backlog
    overdue = loops_buckets.get("overdue") or []
    if len(overdue) > 3:
        parties = ", ".join(
            (L.get("party") if isinstance(L, dict) else getattr(L, "party", "?")) or "?"
            for L in overdue[:5]
        )
        truths.append({
            "claim": (
                f"{len(overdue)} loops are past their target date. "
                "Activity is not converting to closed commitments."
            ),
            "why_it_matters": "Overdue loops signal execution drift — commitments are being accumulated but not resolved.",
            "evidence": f"Overdue loop parties include: {parties}.",
            "confidence": "high",
            "grounding": "loop_ledger.md",
            "recommended_disposition": "act_today",
        })
    elif len(overdue) > 0:
        truths.append({
            "claim": f"{len(overdue)} loop(s) past target. Monitor — not yet a critical backlog.",
            "why_it_matters": "Unresolved loops erode trust and operational discipline.",
            "evidence": f"Loop IDs: {', '.join((L.get('id') if isinstance(L, dict) else getattr(L, 'id', '?')) or '?' for L in overdue)}.",
            "confidence": "high",
            "grounding": "loop_ledger.md",
            "recommended_disposition": "monitor",
        })

    # T2: Content signals without conversion
    social_signals = len(social.get("from_baseline") or [])
    recent_closures = _recent_loop_closures(loops_buckets, today, window_days=14)
    if social_signals >= 2 and len(recent_closures) == 0 and len(crossings) > 0:
        truths.append({
            "claim": (
                f"Social content visibility ({social_signals} posts from network in view) "
                "is not converting into strategic conversations. "
                f"No loops closed in the past 14 days; {len(crossings)} relationship(s) past dormancy threshold."
            ),
            "why_it_matters": "Visibility without outreach is passive positioning, not relationship capital.",
            "evidence": f"{social_signals} posts from baseline contacts. 0 loop closures in 14 days. {len(crossings)} crossings.",
            "confidence": "medium",
            "grounding": "social_overlay, loop_ledger.md",
            "recommended_disposition": "act_today",
        })
    elif social_signals == 0 and not source_freshness["sources"].get("social", {}).get("stale"):
        truths.append({
            "claim": "No social signal from baseline contacts in current window. Network is quiet or disengaged.",
            "why_it_matters": "Zero social signal may indicate posting has declined or monitoring window is too narrow.",
            "evidence": "social_overlay returned 0 posts from baseline.",
            "confidence": "medium",
            "grounding": "social_overlay",
            "recommended_disposition": "monitor",
        })

    # T3: Architecture vs. revenue focus leak
    arch_loops = _count_architecture_loops(loops_buckets)
    rev_loops = _count_revenue_loops(loops_buckets)
    if arch_loops >= 2 and rev_loops == 0:
        truths.append({
            "claim": (
                f"System-building work ({arch_loops} active loops) is not paired with revenue-generating execution. "
                "RB architecture is producing operational clarity but no monetization loops are open."
            ),
            "why_it_matters": "Architecture work without adjacent revenue loops means the system is being built but not deployed for income.",
            "evidence": f"{arch_loops} architecture/product loops. {rev_loops} revenue/proposal loops.",
            "confidence": "medium",
            "grounding": "loop_ledger.md",
            "recommended_disposition": "act_today",
        })
    elif arch_loops > rev_loops * 2 and rev_loops > 0:
        truths.append({
            "claim": (
                f"Architecture loops ({arch_loops}) outnumber revenue loops ({rev_loops}) by more than 2:1. "
                "Focus allocation may be unbalanced."
            ),
            "why_it_matters": "Disproportionate system-building vs. execution risks crowding out near-term income.",
            "evidence": f"{arch_loops} architecture loops vs. {rev_loops} revenue loops.",
            "confidence": "medium",
            "grounding": "loop_ledger.md",
            "recommended_disposition": "monitor",
        })

    # T4: Threads without associated loops (drifting)
    drifting_threads = [
        t for t in active_threads
        if not _thread_has_loop(t, loops_buckets)
    ]
    if drifting_threads:
        titles = ", ".join(t.get("title", "?") for t in drifting_threads[:3])
        truths.append({
            "claim": (
                f"{len(drifting_threads)} active thread(s) have no open loop. "
                "These are drifting contexts — named but not advancing."
            ),
            "why_it_matters": "A thread with no loop has no forcing function. It will not move on its own.",
            "evidence": f"Threads without loops: {titles}.",
            "confidence": "high",
            "grounding": "active_threads.yaml, loop_ledger.md",
            "recommended_disposition": "act_today",
        })

    # T5: Direct comms not covered
    if not source_freshness["quiet_claim_allowed"]:
        email_status = source_freshness["sources"].get("email", {})
        truths.append({
            "claim": (
                f"Direct communications are {email_status.get('label', 'unavailable')}. "
                "RB cannot prove the day is quiet. Any 'quiet' interpretation is unsupported."
            ),
            "why_it_matters": "Quiet claims without email coverage are false negatives. Urgent messages may be unread.",
            "evidence": f"Email source label: {email_status.get('label')}. Fetched at: {email_status.get('fetched_at', 'never')}.",
            "confidence": "high",
            "grounding": "email_overlay",
            "recommended_disposition": "monitor",
        })

    # T6: High-DRR contacts with no active loop
    if drr_top:
        top_uncovered = [
            r for r in drr_top[:5]
            if not any(
                r.get("name", "").lower() in (
                    (L.get("party") if isinstance(L, dict) else getattr(L, "party", "")) or ""
                ).lower()
                for bucket_key in ("overdue", "due_today", "this_week")
                for L in (loops_buckets.get(bucket_key) or [])
            )
            and r.get("score", 0) > 30
        ]
        if top_uncovered:
            names = ", ".join(r["name"] for r in top_uncovered[:3])
            truths.append({
                "claim": (
                    f"Top DRR contacts ({names}) have no active loop tracking engagement. "
                    "High-relevance contacts are not under active management."
                ),
                "why_it_matters": "DRR score identifies the highest-leverage relationships. No loop means no forcing function.",
                "evidence": f"DRR scores: {', '.join(str(round(r['score'], 1)) for r in top_uncovered[:3])}.",
                "confidence": "medium",
                "grounding": "drr_score + loop_ledger.md",
                "recommended_disposition": "act_today",
            })

    # Fallback: if no hard truths could be derived
    if not truths:
        truths.append({
            "claim": "RB is under-instrumented here; no hard truths could be derived from available evidence.",
            "why_it_matters": "Absence of hard truths does not mean everything is healthy — it means evidence is insufficient.",
            "evidence": "Insufficient data from current source overlays.",
            "confidence": "low",
            "grounding": "meta",
            "recommended_disposition": "monitor",
        })

    return truths


# --------------------------------------------------------------------------- #
# Negative-space analysis                                                      #
# --------------------------------------------------------------------------- #

def build_what_is_not_happening(report: dict, today: date) -> list[dict]:
    absences: list[dict] = []
    loops_buckets = report.get("loops") or {}
    crossings = report.get("crossings") or []
    active_threads = report.get("active_threads") or []
    drr_top = report.get("drr_top") or []

    # N1: Overdue loops — silence from expected follow-through
    for L in (loops_buckets.get("overdue") or []):
        lid = (L.get("id") if isinstance(L, dict) else getattr(L, "id", "?")) or "?"
        party = (L.get("party") if isinstance(L, dict) else getattr(L, "party", "?")) or "?"
        target = (L.get("target") if isinstance(L, dict) else getattr(L, "target", None))
        target_str = target if isinstance(target, str) else (target.isoformat() if target else "?")
        try:
            t_date = date.fromisoformat(target_str) if target_str != "?" else None
            days_overdue = (today - t_date).days if t_date else None
        except ValueError:
            days_overdue = None

        absences.append({
            "type": "overdue_commitment",
            "expected_signal": f"Loop {lid} closed by {target_str} ({party})",
            "observed_absence": (
                f"Loop {lid} is {days_overdue} day(s) past target." if days_overdue else
                f"Loop {lid} is past target."
            ),
            "why_it_matters": "Overdue loop signals a broken commitment or avoided action.",
            "confidence": "high",
            "next_check": today.isoformat(),
            "recommended_disposition": "act_today",
            "source_ref": "loop_ledger.md",
        })

    # N2: Active threads with no open loop (drifting)
    for t in active_threads:
        if not _thread_has_loop(t, loops_buckets):
            absences.append({
                "type": "thread_without_next_action",
                "expected_signal": f"Thread '{t.get('title')}' has an active loop tracking its next step",
                "observed_absence": "No open loop references this thread's people or companies.",
                "why_it_matters": "A thread with no loop has no forcing function. It drifts until someone notices.",
                "confidence": "high",
                "next_check": (today + timedelta(days=3)).isoformat(),
                "recommended_disposition": "act_today",
                "source_ref": f"active_threads.yaml: {t.get('id')}",
            })

    # N3: High-DRR contacts in crossings but no active loop
    crossing_names = {c.get("name", "").lower() for c in crossings}
    for r in drr_top[:10]:
        if r.get("score", 0) < 20:
            continue
        rname = (r.get("name") or "").lower()
        if rname not in crossing_names:
            continue
        has_loop = any(
            rname in ((L.get("party") if isinstance(L, dict) else getattr(L, "party", "")) or "").lower()
            for bucket_key in ("overdue", "due_today", "this_week")
            for L in (loops_buckets.get(bucket_key) or [])
        )
        if not has_loop:
            overage = next((c.get("overage") for c in crossings if c.get("name", "").lower() == rname), None)
            absences.append({
                "type": "high_relevance_contact_drifting",
                "expected_signal": f"Engagement loop for {r['name']} (DRR {r['score']:.0f})",
                "observed_absence": (
                    f"{r['name']} is {overage} day(s) past dormancy threshold. No active loop."
                    if overage else
                    f"{r['name']} is past dormancy threshold. No active loop."
                ),
                "why_it_matters": "High-relevance contacts without engagement loops will drift out of reach.",
                "confidence": "medium",
                "next_check": (today + timedelta(days=7)).isoformat(),
                "recommended_disposition": "act_today",
                "source_ref": "baseline_index.json + loop_ledger.md",
            })

    return absences


# --------------------------------------------------------------------------- #
# Execution options                                                            #
# --------------------------------------------------------------------------- #

def build_execution_options(
    report: dict,
    today: date,
    cos_judgment: dict,
    what_is_not_happening: list[dict],
    linkedin_delta: dict | None,
) -> list[dict]:
    options: list[dict] = []
    loops_buckets = report.get("loops") or {}
    crossings = report.get("crossings") or []

    # EO: Each overdue loop → escalate_priority
    for L in (loops_buckets.get("overdue") or []):
        lid = (L.get("id") if isinstance(L, dict) else getattr(L, "id", "?")) or "?"
        party = (L.get("party") if isinstance(L, dict) else getattr(L, "party", "?")) or "?"
        desc = (L.get("description") if isinstance(L, dict) else getattr(L, "description", "")) or ""
        options.append({
            "recommendation_id": f"overdue-{lid}",
            "action": "escalate_priority",
            "target": party,
            "reason": f"Loop {lid} is past target: {desc[:80]}",
            "default_priority": "high",
            "requires_confirmation": True,
            "endpoint": f"POST /loops — close loop {lid} or extend target",
        })

    # EO: Each due-today loop → create_task
    for L in (loops_buckets.get("due_today") or []):
        lid = (L.get("id") if isinstance(L, dict) else getattr(L, "id", "?")) or "?"
        party = (L.get("party") if isinstance(L, dict) else getattr(L, "party", "?")) or "?"
        desc = (L.get("description") if isinstance(L, dict) else getattr(L, "description", "")) or ""
        options.append({
            "recommendation_id": f"due-today-{lid}",
            "action": "create_task",
            "target": party,
            "reason": f"Loop {lid} due today: {desc[:80]}",
            "default_priority": "high",
            "requires_confirmation": True,
            "endpoint": f"POST /loops or manual execution",
        })

    # EO: Each crossing without active loop → open_loop
    loop_parties = _loop_party_names(loops_buckets)
    for c in crossings[:5]:
        cname = c.get("name", "")
        if cname.lower() not in loop_parties:
            options.append({
                "recommendation_id": f"crossing-{c.get('id', cname.lower().replace(' ', '-'))}",
                "action": "open_loop",
                "target": cname,
                "reason": f"{cname} is {c.get('overage')} day(s) past {c.get('tier')} dormancy threshold with no active loop.",
                "default_priority": "medium",
                "requires_confirmation": True,
                "endpoint": "POST /loops",
            })

    # EO: Threads without loops → open_loop
    active_threads = report.get("active_threads") or []
    for t in active_threads:
        if not _thread_has_loop(t, loops_buckets):
            options.append({
                "recommendation_id": f"thread-no-loop-{t.get('id', '?')}",
                "action": "open_loop",
                "target": t.get("title", "?"),
                "reason": f"Thread '{t.get('title')}' has no active loop. Create one to give it a forcing function.",
                "default_priority": "medium",
                "requires_confirmation": True,
                "endpoint": "POST /loops",
            })

    # EO: LinkedIn outreach queue
    if linkedin_delta and not linkedin_delta.get("stale"):
        delta_data = linkedin_delta.get("data") or {}
        outreach_queue = delta_data.get("outreach_queue") or []
        for item in outreach_queue[:5]:
            person = item.get("name") or item.get("person") or "?"
            reason = item.get("reason") or item.get("why") or "LinkedIn delta signal"
            options.append({
                "recommendation_id": f"linkedin-outreach-{person.lower().replace(' ', '-')}",
                "action": "open_outreach_loop",
                "target": person,
                "reason": reason,
                "default_priority": item.get("priority", "medium"),
                "requires_confirmation": True,
                "endpoint": "POST /loops",
            })

        # EO: LinkedIn monitor queue (no outreach, just watch)
        monitor_queue = delta_data.get("monitor_queue") or []
        for item in monitor_queue[:3]:
            person = item.get("name") or item.get("person") or "?"
            options.append({
                "recommendation_id": f"linkedin-monitor-{person.lower().replace(' ', '-')}",
                "action": "monitor_relationship",
                "target": person,
                "reason": item.get("reason") or "Professional movement detected; no immediate action required.",
                "default_priority": "low",
                "requires_confirmation": False,
                "endpoint": None,
            })

        # EO: Stale relationship reactivation candidates
        reactivation = delta_data.get("reactivation_candidates") or []
        for item in reactivation[:3]:
            person = item.get("name") or "?"
            options.append({
                "recommendation_id": f"linkedin-reactivate-{person.lower().replace(' ', '-')}",
                "action": "open_outreach_loop",
                "target": person,
                "reason": item.get("reason") or "Dormant relationship reactivation candidate from LinkedIn delta.",
                "default_priority": "low",
                "requires_confirmation": True,
                "endpoint": "POST /loops",
            })

    # EO: Suppression / ignore options from hard truths
    hard_truths = cos_judgment.get("hard_truths") or []
    for ht in hard_truths:
        if ht.get("recommended_disposition") == "monitor":
            # Generate a monitor or ignore option
            claim_slug = (ht.get("claim") or "")[:40].lower().replace(" ", "-").replace(".", "").replace(",", "")
            options.append({
                "recommendation_id": f"monitor-{claim_slug}",
                "action": "monitor_relationship",
                "target": "RB system",
                "reason": f"Hard truth flagged as 'monitor': {(ht.get('claim') or '')[:80]}",
                "default_priority": "low",
                "requires_confirmation": False,
                "endpoint": None,
            })

    return options


# --------------------------------------------------------------------------- #
# Macro-to-operator synthesis                                                  #
# --------------------------------------------------------------------------- #

def build_macro_synthesis(source_freshness: dict) -> dict:
    """Read macro synthesis from cache. Label stale/unavailable before returning."""
    macro_status = source_freshness["sources"].get("macro_synthesis", {})

    if macro_status.get("label") == "source_unavailable":
        return {
            "available": False,
            "label": "source_unavailable",
            "note": (
                "Macro synthesis cache not found. "
                "Market signals are unavailable. "
                "Do not infer market conditions from historical memory alone."
            ),
            "macro_chain": [],
        }

    if macro_status.get("stale"):
        return {
            "available": False,
            "label": "stale",
            "note": (
                f"Macro synthesis cache is stale (generated: {macro_status.get('generated_at', 'unknown')}). "
                f"Threshold: {MACRO_STALE_DAYS} days. "
                "Do not use stale macro context for current-state claims."
            ),
            "macro_chain": [],
        }

    try:
        macro_path = CACHE_DIR / "macro_synthesis.json"
        raw = json.loads(macro_path.read_text())
        data = raw.get("data") or raw
        return {
            "available": True,
            "label": "fresh",
            "generated_at": macro_status.get("generated_at"),
            "macro_chain": data.get("macro_chain") or [],
        }
    except Exception as e:
        return {
            "available": False,
            "label": "refresh_failed",
            "note": f"Macro synthesis cache read error: {e}",
            "macro_chain": [],
        }


# --------------------------------------------------------------------------- #
# LinkedIn delta intelligence                                                  #
# --------------------------------------------------------------------------- #

def build_linkedin_delta(source_freshness: dict) -> dict:
    """Read LinkedIn delta from cache. Label stale; guard against current-state claims."""
    li_status = source_freshness["sources"].get("linkedin_delta", {})

    if li_status.get("label") == "source_unavailable":
        return {
            "available": False,
            "label": "source_unavailable",
            "stale": True,
            "note": "No LinkedIn delta cache. Run linkedin_ingest.py with a second export to generate delta intelligence.",
            "data": {},
        }

    if li_status.get("stale"):
        return {
            "available": False,
            "label": "stale",
            "stale": True,
            "ingested_at": li_status.get("ingested_at"),
            "note": (
                f"LinkedIn delta cache is stale (ingested: {li_status.get('ingested_at', 'unknown')}). "
                f"Threshold: {LINKEDIN_STALE_DAYS} days. "
                "Not used for current-state claims. Re-run linkedin_ingest.py with a fresh export."
            ),
            "data": {},
        }

    try:
        li_path = CACHE_DIR / "linkedin_ingest_latest.json"
        raw = json.loads(li_path.read_text())
        data = raw.get("data") or raw.get("delta_intelligence") or raw
        return {
            "available": True,
            "label": "fresh",
            "stale": False,
            "ingested_at": li_status.get("ingested_at"),
            "data": data,
        }
    except Exception as e:
        return {
            "available": False,
            "label": "refresh_failed",
            "stale": True,
            "note": f"LinkedIn delta cache read error: {e}",
            "data": {},
        }


# --------------------------------------------------------------------------- #
# Top-level builder                                                            #
# --------------------------------------------------------------------------- #

def _is_weekend(today: date) -> bool:
    return today.weekday() >= 5  # Saturday=5, Sunday=6


def build_operating_mode(today: date, plan: dict | None = None) -> dict:
    """RB-DEFECT-021A — generalized five-mode weekly operating calendar.

    Supersedes the binary weekend/weekday split in build_weekend_guidance
    (kept below for backward compatibility / existing call sites). Every
    mode answers its framing question — "should Todd work today?", "what
    would make this week successful?", etc — *before* the brief generates
    execution recommendations. This is a gate, not a ranking signal.

    `plan` is an optional weekly_plan.json dict (see weekly_planning.py);
    if its operating_mode_calendar overrides the default, that's honored.
    """
    try:
        import weekly_planning as _wp
    except ImportError:  # pragma: no cover - defensive; module always present
        return {"mode": "execution", "day": today.strftime("%A"),
                "framing_question": "What should happen today given this week's goals?"}

    calendar = None
    if plan and plan.get("operating_mode_calendar"):
        names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        calendar = {i: plan["operating_mode_calendar"].get(name, _wp._DEFAULT_CALENDAR[i])
                    for i, name in enumerate(names)}
    return _wp.operating_mode_for(today, calendar)


def build_weekend_guidance(today: date) -> dict | None:
    """DEFECT-011: Weekend-aware CoS guidance.

    On Saturday/Sunday, replaces aggressive action pressure with
    energy optimization: close open loops, strategic reading, Monday prep.
    Returns None on weekdays.
    """
    if not _is_weekend(today):
        return None

    day_name = today.strftime("%A")
    is_sunday = today.weekday() == 6

    return {
        "is_weekend": True,
        "day": day_name,
        "mode": "sunday_prep" if is_sunday else "saturday_recovery",
        "guidance": (
            "Sunday: preparation window — review the week ahead, confirm Monday priorities, "
            "set loops, do light relationship maintenance."
            if is_sunday else
            "Saturday: recovery and reading window — close any open loops, "
            "strategic reading, low-commitment outreach only."
        ),
        "recommended_focus": [
            "Close overdue loops before Monday" if is_sunday else "Close any loops opened this week",
            "Review Monday calendar and flag any prep needed",
            "Light relationship maintenance (WhatsApp, text) — avoid formal outreach",
            "Strategic reading: restaurant tech, McDonald's, opportunity ecosystem",
        ] if is_sunday else [
            "Resolve any loops that have been open > 7 days",
            "Read industry publications without pressure to act",
            "Respond to any backlog messages at your own pace",
            "Prepare one key talking point for Monday conversations",
        ],
        "suppress_aggressive_actions": True,
    }


def build_all(report: dict, today: date) -> dict:
    """Single entry point. Returns all CoS judgment blocks."""
    source_freshness = build_source_freshness(report)
    linkedin_delta = build_linkedin_delta(source_freshness)
    macro_synthesis = build_macro_synthesis(source_freshness)
    behavioral_intelligence = build_behavioral_intelligence_layer()

    hard_truths = build_hard_truths(report, today, source_freshness)
    what_is_not_happening = build_what_is_not_happening(report, today)
    weekend_guidance = build_weekend_guidance(today)

    cos_judgment = {
        "hard_truths": hard_truths,
        "negative_space": what_is_not_happening,
        "focus_leaks": [ht for ht in hard_truths if "crowding" in (ht.get("claim") or "") or "outnumber" in (ht.get("claim") or "")],
        "unsupported_assumptions": (
            [{
                "claim": "The day is quiet.",
                "evidence": "email source is stale or unavailable",
                "recommended_disposition": "do_not_state",
            }] if not source_freshness["quiet_claim_allowed"] else []
        ),
        "opportunity_costs": [],
        "prioritization_tradeoffs": [],
        "deprioritize_or_ignore": [],
        "execution_pressure": [ht for ht in hard_truths if ht.get("recommended_disposition") == "act_today"],
        "confidence": _overall_confidence(hard_truths, source_freshness),
        "source_refs": list(source_freshness["sources"].keys()),
        "weekend_guidance": weekend_guidance,  # DEFECT-011
    }

    execution_options = build_execution_options(
        report, today, cos_judgment, what_is_not_happening, linkedin_delta
    )

    return {
        "source_freshness": source_freshness,
        "cos_judgment": cos_judgment,
        "what_is_not_happening": what_is_not_happening,
        "execution_options": execution_options,
        "macro_to_operator_synthesis": macro_synthesis,
        "behavioral_intelligence": behavioral_intelligence,
        "linkedin_relationship_delta": linkedin_delta,
    }


def build_behavioral_intelligence_layer(
    behavioral_path: Path | None = None,
    entity_path: Path | None = None,
) -> dict:
    """Load confirmed macro behavioral intelligence for brief rendering.

    This keeps the macro behavioral layer persistent and source-bound. It does
    not promote proposed records to fact. Proposed/rejected records remain
    queryable through the API, but the daily brief only receives confirmed
    signals, artifacts, and entity risk profiles.
    """
    behavioral_path = behavioral_path or BEHAVIORAL_INTELLIGENCE_PATH
    entity_path = entity_path or (SYSTEM_DIR / "entity_intelligence.json")

    if not behavioral_path.exists() and not entity_path.exists():
        return {
            "available": False,
            "label": "source_unavailable",
            "records": [],
            "signals": [],
            "artifacts": [],
            "entities": [],
            "signal_count": 0,
            "artifact_count": 0,
            "entity_count": 0,
            "brief_layer_summary": {},
            "note": "No behavioral intelligence stores found.",
        }

    def _read_records(path: Path) -> tuple[list[dict], str | None]:
        if not path.exists():
            return [], None
        try:
            raw = json.loads(path.read_text())
        except Exception as exc:  # noqa: BLE001
            return [], f"{path.name} read error: {exc}"
        records = raw.get("records") if isinstance(raw, dict) else raw
        if not isinstance(records, list):
            return [], f"{path.name} must contain a records list."
        return [r for r in records if isinstance(r, dict)], None

    behavioral_records, behavioral_error = _read_records(behavioral_path)
    entity_records, entity_error = _read_records(entity_path)

    def _created_at(record: dict) -> str:
        return str(record.get("created_at") or record.get("signal_date") or "")

    confirmed_behavioral = [
        r for r in behavioral_records
        if r.get("claim_status") == "confirmed"
    ]
    signals = [
        r for r in confirmed_behavioral
        if r.get("record_type") == "behavioral_signal"
    ]
    artifacts = [
        r for r in confirmed_behavioral
        if r.get("record_type") != "behavioral_signal"
        and (r.get("artifact_name") or r.get("name"))
    ]
    entities = [
        r for r in entity_records
        if r.get("claim_status") == "confirmed"
    ]

    brief_layer_summary: dict[str, int] = {}
    for record in signals + artifacts:
        for layer in record.get("daily_brief_layers") or []:
            brief_layer_summary[layer] = brief_layer_summary.get(layer, 0) + 1

    available = bool(signals or artifacts or entities)
    note = None
    if behavioral_error or entity_error:
        note = "; ".join(e for e in (behavioral_error, entity_error) if e)
    elif not available:
        note = "No confirmed behavioral intelligence records available."

    return {
        "available": available,
        "label": "active" if available else "empty",
        "record_count": len(confirmed_behavioral) + len(entities),
        "signal_count": len(signals),
        "artifact_count": len(artifacts),
        "entity_count": len(entities),
        "recent_signal_count": len(signals),
        "records": sorted(confirmed_behavioral + entities, key=_created_at, reverse=True)[:20],
        "signals": sorted(signals, key=_created_at, reverse=True)[:10],
        "artifacts": artifacts[:5],
        "entities": entities[:10],
        "brief_layer_summary": brief_layer_summary,
        "source": str(behavioral_path.relative_to(SYSTEM_DIR)) if behavioral_path.is_relative_to(SYSTEM_DIR) else str(behavioral_path),
        "entity_source": str(entity_path.relative_to(SYSTEM_DIR)) if entity_path.is_relative_to(SYSTEM_DIR) else str(entity_path),
        "note": note,
    }


def build_behavioral_intelligence(today: date) -> dict:
    """Backward-compatible alias for older callers."""
    return build_behavioral_intelligence_layer()


def _overall_confidence(hard_truths: list[dict], source_freshness: dict) -> str:
    if source_freshness.get("tier1_unavailable"):
        return "low"
    if any(ht.get("confidence") == "high" for ht in hard_truths):
        return "medium"
    return "medium"
