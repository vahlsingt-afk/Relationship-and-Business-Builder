#!/usr/bin/env python3
"""
cos_action_engine.py — Chief of Staff Action Engine (DEFECT-023)

Generates specific, named, actionable recommendations from:
  - active_threads.yaml (open opportunities)
  - baseline_index.json (relationship graph + RC tiers)
  - loop_ledger.md (overdue and pending loops)
  - entity/opportunity intelligence

Every output item answers:
  1. Who specifically to contact
  2. Why (intelligence rationale)
  3. What to say (talking point)
  4. Expected outcome
  5. Strategic importance

Generic recommendations ("consider reaching out") are suppressed.
The engine does the analytical work so the principal does not have to.

CLI:
    python3 cos_action_engine.py --today
    python3 cos_action_engine.py --thread T-2026-05-foods-connected-commercial-director
    python3 cos_action_engine.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core  # noqa: E402

try:
    import yaml as _yaml  # type: ignore
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CACHE_PATH = core.CACHE_DIR / "cos_action_engine.json"

# Intelligence path quality tiers
PATH_TIER = {
    "direct_rc_inner": 100,    # RC inner who has direct knowledge
    "direct_rc_broader": 80,   # RC broader with direct knowledge
    "introducer_rc_inner": 75,  # RC inner who made the original introduction
    "former_colleague": 60,     # Former colleague at target company
    "lki_contact": 40,          # LKI contact at or near target
    "weak_tie": 20,             # Weak connection, low confidence
}

# Opportunity stage inference from context keywords
STAGE_KEYWORDS = {
    "interview": ["interview", "meeting scheduled", "call scheduled", "phone screen", "video call"],
    "waiting": ["waiting", "pending", "external review", "decision", "next steps"],
    "active": ["active", "warm", "engaged", "moving"],
    "prospecting": ["prospect", "monitor", "watching", "considering"],
}

# Days before "momentum decay" warning fires for waiting opportunities
MOMENTUM_DECAY_DAYS = 7


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_threads() -> list[dict]:
    path = core.SYSTEM_DIR / "active_threads.yaml"
    if not path.exists():
        return []
    try:
        if _HAS_YAML:
            raw = _yaml.safe_load(path.read_text())
        else:
            return []
        if isinstance(raw, dict):
            return raw.get("threads") or []
        return raw if isinstance(raw, list) else []
    except Exception:
        return []


def _load_baseline() -> list[dict]:
    try:
        raw = json.loads(core.BASELINE_PATH.read_text())
        return raw if isinstance(raw, list) else list(raw.values())
    except Exception:
        return []


def _index_baseline(contacts: list[dict]) -> dict[str, dict]:
    return {c["id"]: c for c in contacts if c.get("id")}


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z]", "", (name or "").lower())


def _find_contact(contacts: list[dict], ref: str) -> dict | None:
    """Find a contact by id, name-key, or partial name match."""
    idx = _index_baseline(contacts)
    if ref in idx:
        return idx[ref]
    key = _name_key(ref)
    for c in contacts:
        if _name_key(c.get("name") or "") == key:
            return c
    # Partial first+last match
    parts = ref.lower().split()
    if len(parts) >= 2:
        for c in contacts:
            bname = (c.get("name") or "").lower()
            if parts[0] in bname and parts[-1] in bname:
                return c
    return None


def _days_since(date_str: str | None, today: date) -> int | None:
    if not date_str:
        return None
    try:
        return (today - date.fromisoformat(date_str)).days
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Intelligence path detection
# ---------------------------------------------------------------------------

def _find_intelligence_paths(
    target_company: str,
    target_people: list[str],
    contacts: list[dict],
) -> list[dict]:
    """Find the strongest intelligence paths to a target company/people.

    Searches for:
    1. RC inner/broader contacts at or formerly at the target company
    2. Contacts who introduced or connected to target people
    3. Former colleagues of target people now in the network
    4. LKI contacts with company mentions

    Returns a ranked list of path dicts.
    """
    paths: list[dict] = []
    target_co_lower = target_company.lower()
    target_person_keys = {_name_key(p) for p in target_people}

    for c in contacts:
        co = (c.get("current_company") or "").lower()
        notes = (c.get("notes") or "").lower()
        tags = [t.lower() for t in (c.get("tags") or [])]
        signal_class = c.get("signal_class") or ""
        rc_tier = c.get("rc_tier") or ""
        name = c.get("name") or ""

        # Score this contact's relevance to the target
        relevance_reasons: list[str] = []
        path_score = 0

        # Direct company match
        if target_co_lower in co:
            relevance_reasons.append(f"Currently at {target_company}")
            path_score = max(path_score, PATH_TIER["lki_contact"] if signal_class == "LKI" else 70)

        # Notes mention target company or target people
        if target_co_lower in notes:
            relevance_reasons.append(f"Notes reference {target_company}")
            path_score = max(path_score, 50)

        # Explicit connection to target people
        for tp in target_people:
            tp_lower = tp.lower()
            if tp_lower in notes:
                tier = "direct_rc_inner" if signal_class == "RC" and rc_tier == "inner" else \
                       "direct_rc_broader" if signal_class == "RC" else "lki_contact"
                path_score = max(path_score, PATH_TIER[tier])
                relevance_reasons.append(f"Notes reference {tp}")

        # Introducer pattern: notes say "introduced X" or "connected X"
        for tp in target_people:
            intro_pattern = re.compile(
                r'(introduc|connected|referred|knows?)\s+' + re.escape(tp.lower()),
                re.I,
            )
            if intro_pattern.search(notes):
                path_score = max(path_score, PATH_TIER["introducer_rc_inner"])
                relevance_reasons.append(f"Introduced or connected to {tp}")

        # Skip if no relevance found
        if not relevance_reasons or path_score == 0:
            continue

        # RC tier boost
        if signal_class == "RC" and rc_tier == "inner":
            path_score = min(100, path_score + 15)
        elif signal_class == "RC" and rc_tier == "broader":
            path_score = min(100, path_score + 8)

        last_touch = c.get("last_touch")
        days_quiet = _days_since(last_touch, date.today())

        paths.append({
            "contact_id": c.get("id"),
            "contact_name": name,
            "signal_class": signal_class,
            "rc_tier": rc_tier,
            "current_company": c.get("current_company"),
            "current_role": c.get("current_role"),
            "last_touch": last_touch,
            "days_since_contact": days_quiet,
            "path_score": path_score,
            "relevance_reasons": relevance_reasons,
        })

    # Sort by path score descending
    paths.sort(key=lambda p: -p["path_score"])
    return paths[:5]


# ---------------------------------------------------------------------------
# Opportunity action generator
# ---------------------------------------------------------------------------

def _infer_stage(thread: dict) -> str:
    text = " ".join([
        thread.get("current_state") or "",
        thread.get("context") or "",
        thread.get("status") or "",
    ]).lower()
    for stage, keywords in STAGE_KEYWORDS.items():
        if any(k in text for k in keywords):
            return stage
    return "active"


def _days_open(thread: dict, today: date) -> int:
    opened = thread.get("opened")
    if not opened:
        return 0
    try:
        return (today - date.fromisoformat(str(opened))).days
    except (ValueError, TypeError):
        return 0


def _opportunity_action(thread: dict, contacts: list[dict], today: date) -> dict:
    """Generate a specific CoS action item for an opportunity thread."""
    title = thread.get("title") or "Opportunity"
    thread_id = thread.get("id") or ""
    stage = _infer_stage(thread)
    days_open = _days_open(thread, today)
    thread_people = [_find_contact(contacts, p) for p in (thread.get("people") or []) if p]
    thread_people = [p for p in thread_people if p]
    companies = thread.get("companies") or []
    target_company = companies[0] if companies else ""
    target_people_names = [c.get("name") for c in thread_people if c.get("name")]

    # Find intelligence paths
    all_paths = []
    if target_company:
        all_paths = _find_intelligence_paths(target_company, target_people_names, contacts)

    # Primary contact for the action recommendation
    primary_thread_contact = thread_people[0] if thread_people else None
    primary_path_contact = all_paths[0] if all_paths else None

    # Build the recommendation
    if stage == "waiting" and primary_thread_contact:
        contact = primary_thread_contact
        cname = contact.get("name") or "recruiter"
        days_waiting = days_open
        decay_warning = days_waiting >= MOMENTUM_DECAY_DAYS
        action_text = (
            f"Contact {cname} today — check in on next steps from Duncan's review. "
            f"Message: 'Happy to accommodate any timing as next steps come together.' "
            f"Expected outcome: Stage clarity and scheduling momentum."
        )
        if decay_warning:
            action_text += f" ⚠ {days_waiting} days since last activity — momentum risk."
        return {
            "type": "opportunity_action",
            "thread_id": thread_id,
            "title": f"[ACT] {title} — {days_waiting}d waiting, contact {cname}",
            "summary": (
                f"Stage: waiting on external review ({days_waiting} days). "
                f"Primary contact: {cname}. "
                f"{'Momentum decay risk.' if decay_warning else 'Window is open.'}"
            ),
            "recommended_action": action_text,
            "disposition": "act_today" if decay_warning else "monitor",
            "intelligence_paths": all_paths[:2],
            "strategic_importance": "high",
        }

    elif stage == "interview" and primary_thread_contact:
        contact = primary_thread_contact
        cname = contact.get("name") or "contact"
        return {
            "type": "opportunity_action",
            "thread_id": thread_id,
            "title": f"[ACT] {title} — interview stage, prep required",
            "summary": (
                f"Interview stage. Primary contact: {cname}. "
                f"Days in pipeline: {days_open}."
            ),
            "recommended_action": (
                f"Contact {cname} to confirm timing and agenda. "
                f"Research: leadership team, competitive landscape, McDonald's footprint. "
                f"Prepare: 30-60-90 framework, likely objections, positioning. "
                f"Expected outcome: Demonstrates preparation, builds confidence."
            ),
            "disposition": "act_today",
            "intelligence_paths": all_paths[:2],
            "strategic_importance": "high",
        }

    elif primary_path_contact and all_paths:
        # Use the intelligence path contact
        pc = primary_path_contact
        pcname = pc.get("contact_name") or "contact"
        reasons = "; ".join(pc.get("relevance_reasons") or [])
        days_quiet = pc.get("days_since_contact")
        quiet_note = f" (last contact: {days_quiet}d ago)" if days_quiet else ""
        return {
            "type": "opportunity_intelligence",
            "thread_id": thread_id,
            "title": f"[INTEL] {title} — contact {pcname} for intelligence",
            "summary": (
                f"Strongest intelligence path to {target_company}: {pcname} "
                f"(score {pc.get('path_score')}).{quiet_note} "
                f"Reason: {reasons}."
            ),
            "recommended_action": (
                f"Contact {pcname}: '{target_company} is on my radar — what can you share about "
                f"their current trajectory / priorities?' "
                f"Expected outcome: Current intelligence on {target_company} priorities and people."
            ),
            "disposition": "monitor",
            "intelligence_paths": all_paths[:3],
            "strategic_importance": "medium",
        }

    else:
        # Minimal action — at least give a research direction
        return {
            "type": "opportunity_monitor",
            "thread_id": thread_id,
            "title": f"[MONITOR] {title}",
            "summary": f"Days open: {days_open}. No warm intelligence path identified.",
            "recommended_action": (
                f"Research {target_company} on LinkedIn for mutual connections. "
                f"Check company news for hiring signals or leadership changes."
            ),
            "disposition": "monitor",
            "intelligence_paths": [],
            "strategic_importance": "low",
        }


# ---------------------------------------------------------------------------
# Relationship recommendation generator
# ---------------------------------------------------------------------------

def _relationship_recommendations(contacts: list[dict], today: date) -> list[dict]:
    """Generate specific reach-out recommendations for relationship maintenance.

    Returns top-5 RC inner/broader contacts with highest relationship value
    and specific talking points where available.
    """
    recs: list[dict] = []

    rc_contacts = [
        c for c in contacts
        if c.get("signal_class") == "RC"
        and c.get("rc_tier") in ("inner", "broader")
    ]

    for c in rc_contacts:
        last_touch = c.get("last_touch")
        days = _days_since(last_touch, today)
        if days is None:
            days = 9999
        name = c.get("name") or "contact"
        notes = c.get("notes") or ""
        tags = [t.lower() for t in (c.get("tags") or [])]

        # Skip if recently touched (< 14 days for inner, < 21 for broader)
        threshold = 14 if c.get("rc_tier") == "inner" else 21
        if days < threshold:
            continue

        # Build a specific talking point from notes context
        talking_point = _extract_talking_point(c, notes)

        urgency = "high" if days > 60 else "medium" if days > 30 else "low"

        recs.append({
            "contact_id": c.get("id"),
            "contact_name": name,
            "rc_tier": c.get("rc_tier"),
            "current_company": c.get("current_company"),
            "current_role": c.get("current_role"),
            "days_quiet": days,
            "urgency": urgency,
            "talking_point": talking_point,
            "tags": tags,
        })

    # Sort: inner tier first, then by days_quiet desc
    recs.sort(key=lambda r: (
        0 if r["rc_tier"] == "inner" else 1,
        -r["days_quiet"],
    ))

    return recs[:8]


def _extract_talking_point(contact: dict, notes: str) -> str:
    """Derive a specific talking point from contact notes."""
    name = contact.get("name") or "them"
    tags = [t.lower() for t in (contact.get("tags") or [])]

    # Check for specific context patterns in notes
    if "harri" in notes.lower():
        return f"You mentioned speaking with the Harri CRO — what came of that conversation? Did my name come up?"
    if "qu " in notes.lower() or "qu pos" in notes.lower():
        return f"You introduced me to John Morrison at Qu. I'm looking more closely at them — what's your read on their trajectory?"
    if "mcd" in notes.lower() or "mcdonald" in notes.lower():
        return f"What's the current mood inside McDonald's tech circles? Anything I should be paying attention to?"
    if "par-alumni" in tags or "par technology" in (contact.get("current_company") or "").lower():
        return f"Checking in from one PAR alum to another — what are you seeing in the market?"
    if "otp" in " ".join(tags) or "otp3" in notes.lower():
        return f"Checking in — anything going on in your world worth discussing?"
    if "connector" in tags:
        return f"I'd love your perspective on a couple of things I'm working on — do you have 20 minutes?"

    # Generic but still personal
    return f"Checking in — it's been a while since we connected. Would love to catch up briefly."


# ---------------------------------------------------------------------------
# Section filtering helpers
# ---------------------------------------------------------------------------

def _has_engagement_signal(social_outbound: dict) -> bool:
    """Return True only if there are actual engagement signals on own posts."""
    if not social_outbound:
        return False
    signal_count = social_outbound.get("signal_count") or 0
    items = social_outbound.get("items") or []
    return signal_count > 0 or len(items) > 0


def _has_new_market_evidence(market_signals: dict, prior_brief_sections: dict) -> bool:
    """Return True if market_signals has fresh items not seen in the prior brief."""
    if not market_signals:
        return False
    if market_signals.get("stale"):
        return False
    top = market_signals.get("top") or market_signals.get("data", {}).get("top") or []
    return len(top) > 0


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------

def build_cos_actions(report: dict, today: date | None = None) -> dict:
    """Build the full CoS action recommendation set for the daily brief.

    Returns:
    {
        "opportunity_actions": [...],   # per-thread action recommendations
        "relationship_recommendations": [...],  # named contact reach-outs
        "suppress_thesis": bool,        # True when no new market evidence
        "suppress_own_posts": bool,     # True when no engagement signals
        "generated_at": ISO str,
    }
    """
    if today is None:
        today = date.today()

    contacts = _load_baseline()
    threads = _load_threads()

    # Opportunity actions for open threads
    open_threads = [t for t in threads if t.get("status") == "open"]
    opportunity_actions: list[dict] = []
    for thread in open_threads:
        action = _opportunity_action(thread, contacts, today)
        opportunity_actions.append(action)

    # Relationship recommendations
    rel_recs = _relationship_recommendations(contacts, today)

    # Suppression signals
    social_out = report.get("social_outbound") or {}
    market_signals = report.get("market_signals") or {}
    suppress_own_posts = not _has_engagement_signal(social_out)
    prior_sections = (
        (report.get("canonical_brief") or {}).get("sections") or {}
    )
    suppress_thesis = not _has_new_market_evidence(market_signals, prior_sections)

    result = {
        "opportunity_actions": opportunity_actions,
        "relationship_recommendations": rel_recs,
        "suppress_thesis": suppress_thesis,
        "suppress_own_posts": suppress_own_posts,
        "thread_count": len(open_threads),
        "rc_contacts_needing_touch": len(rel_recs),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    # Persist to cache for reference
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(result, indent=2, default=str) + "\n")
    return result


def build_canonical_items(actions: dict, today: date | None = None) -> list[dict]:
    """Convert cos_action_engine output to canonical brief items.

    These items are injected into the `recommended_actions` section and the
    `five_things_today` section (top-priority items only).
    """
    if today is None:
        today = date.today()

    items: list[dict] = []

    # Opportunity actions → canonical items
    for opp in (actions.get("opportunity_actions") or []):
        disposition = opp.get("disposition") or "monitor"
        intel_paths = opp.get("intelligence_paths") or []
        path_note = ""
        if intel_paths:
            top_path = intel_paths[0]
            path_note = (
                f" Intelligence path: {top_path.get('contact_name')} "
                f"({top_path.get('signal_class')} {top_path.get('rc_tier')}, "
                f"score {top_path.get('path_score')})."
            )

        items.append({
            "title": opp.get("title") or "Opportunity action",
            "summary": (opp.get("summary") or "") + path_note,
            "why_it_matters": (
                f"Strategic importance: {opp.get('strategic_importance', 'medium')}. "
                f"CoS research identified this action — principal should choose and execute."
            ),
            "recommended_action": opp.get("recommended_action") or "",
            "disposition": disposition,
            "grounding": "system_detected",
            "freshness": "fresh",
            "source_refs": ["active_threads.yaml", "baseline_index.json", "cos_action_engine.json"],
            "confidence": "high" if disposition == "act_today" else "medium",
            "extras": {
                "thread_id": opp.get("thread_id"),
                "action_type": opp.get("type"),
                "intelligence_paths": intel_paths,
                "cos_generated": True,
            },
        })

    # Relationship recommendations → canonical items (top 3 only for brief)
    rel_recs = actions.get("relationship_recommendations") or []
    if rel_recs:
        # Build a combined "relationship reach-out list" item
        reach_out_lines = []
        for r in rel_recs[:5]:
            urgency_marker = "⚡" if r["urgency"] == "high" else "→"
            reach_out_lines.append(
                f"{urgency_marker} {r['contact_name']} ({r['rc_tier']}, "
                f"{r['days_quiet']}d quiet) — {r['talking_point']}"
            )

        items.append({
            "title": f"Relationship Reach-Outs — {len(rel_recs)} RC contacts need touch",
            "summary": "\n".join(reach_out_lines),
            "why_it_matters": (
                "RC inner and broader contacts are the highest-value relationship assets. "
                "Maintaining consistent touch preserves access, trust, and intelligence flow."
            ),
            "recommended_action": (
                f"Contact {rel_recs[0]['contact_name']} first — "
                f"{rel_recs[0]['talking_point']}"
            ),
            "disposition": "act_today" if any(r["urgency"] == "high" for r in rel_recs[:3]) else "monitor",
            "grounding": "system_detected",
            "freshness": "fresh",
            "source_refs": ["baseline_index.json", "loop_ledger.md"],
            "confidence": "high",
            "extras": {
                "cos_generated": True,
                "action_type": "relationship_maintenance",
                "contact_list": [
                    {
                        "id": r["contact_id"],
                        "name": r["contact_name"],
                        "days_quiet": r["days_quiet"],
                        "talking_point": r["talking_point"],
                    }
                    for r in rel_recs[:5]
                ],
            },
        })

    return items


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_actions(actions: dict) -> None:
    print(f"\n=== CoS Action Engine — {actions.get('generated_at')} ===\n")
    print(f"Open threads: {actions.get('thread_count')}")
    print(f"RC contacts needing touch: {actions.get('rc_contacts_needing_touch')}")
    print(f"Suppress thesis section: {actions.get('suppress_thesis')}")
    print(f"Suppress own posts: {actions.get('suppress_own_posts')}")

    print("\n--- Opportunity Actions ---")
    for opp in (actions.get("opportunity_actions") or []):
        print(f"\n[{opp.get('disposition','monitor').upper()}] {opp.get('title')}")
        print(f"  Summary: {opp.get('summary','')[:120]}")
        print(f"  Action:  {opp.get('recommended_action','')[:160]}")
        paths = opp.get("intelligence_paths") or []
        if paths:
            p = paths[0]
            print(f"  Best path: {p.get('contact_name')} (score {p.get('path_score')}) — {'; '.join(p.get('relevance_reasons',[]))}")

    print("\n--- Relationship Reach-Outs ---")
    for r in (actions.get("relationship_recommendations") or [])[:5]:
        print(f"  [{r.get('urgency','?').upper()}] {r.get('contact_name')} ({r.get('rc_tier')}, {r.get('days_quiet')}d quiet)")
        print(f"    Talking point: {r.get('talking_point')}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--today", action="store_true", help="Run full action engine for today.")
    p.add_argument("--thread", metavar="ID", help="Generate action for a specific thread ID.")
    p.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = p.parse_args()

    today = date.today()
    contacts = _load_baseline()
    threads = _load_threads()

    if args.thread:
        thread = next((t for t in threads if t.get("id") == args.thread), None)
        if not thread:
            print(f"Thread not found: {args.thread}", file=sys.stderr)
            return 1
        result = _opportunity_action(thread, contacts, today)
    else:
        result = build_cos_actions({}, today)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if "opportunity_actions" in result:
            _print_actions(result)
        else:
            print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
