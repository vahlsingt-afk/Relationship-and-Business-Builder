#!/usr/bin/env python3
"""personal_log.py — daily check-in for manually-tracked life goals.

Reads life_goals.yaml to know which goals need manual logging, prompts the
user for each one, and writes to personal_log.json.

The morning pipeline can run this interactively, or it can be run on demand.
Entries are keyed by date — re-running on the same day overwrites.

Usage:
    python3 personal_log.py                    # log today interactively
    python3 personal_log.py --date 2026-06-27  # back-fill a missed day
    python3 personal_log.py --show             # show last 7 days
    python3 personal_log.py --show --days 30   # show last 30 days
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip3 install pyyaml")
    sys.exit(1)

GOALS_PATH = core.SYSTEM_DIR / "life_goals.yaml"
LOG_PATH = core.SYSTEM_DIR / "personal_log.json"
RELATIONSHIP_LOG_PATH = core.SYSTEM_DIR / "personal_relationship_log.json"
TIMELINE_PATH = core.SYSTEM_DIR / "personal_timeline.json"


def _load_goals() -> dict:
    if not GOALS_PATH.exists():
        return {}
    return yaml.safe_load(GOALS_PATH.read_text(encoding="utf-8")) or {}


def _load_log() -> dict:
    if LOG_PATH.exists():
        try:
            return json.loads(LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_log(log: dict) -> None:
    LOG_PATH.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")


def _manual_goals(goals_config: dict) -> list[dict]:
    """Return all goals with data_source == 'manual'. Used for the interactive
    terminal check-in (log_day) — only prompt for goals with no automated source."""
    goals = []
    for domain in goals_config.get("domains", []):
        for goal in domain.get("goals", []):
            if goal.get("data_source") == "manual":
                goals.append({**goal, "_domain": domain["label"]})
    return goals


def _keyword_loggable_goals(goals_config: dict) -> list[dict]:
    """Return all goals eligible for conversational logging — any goal that
    defines log_keywords, regardless of primary data_source.

    A calendar-sourced goal (date_night) can still opt in to conversational
    confirmation by defining log_keywords, since the calendar can miss an
    event the same way it missed logging Todd's actual date nights. A
    GPS-verified goal like exercise (Strava) deliberately has no log_keywords
    and stays untouched here — there's no equivalent risk of the automated
    source under-reporting, and conversational self-report could be gamed.
    """
    goals = []
    for domain in goals_config.get("domains", []):
        for goal in domain.get("goals", []):
            if goal.get("log_keywords"):
                goals.append({**goal, "_domain": domain["label"]})
    return goals


def _prompt_yn(question: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    raw = input(f"{question}{suffix}: ").strip().lower()
    if not raw:
        return default
    return raw.startswith("y")


def log_day(target_date: date, goals_config: dict, existing_log: dict) -> dict:
    """Interactively log manual goals for target_date. Returns updated entry."""
    manual = _manual_goals(goals_config)
    if not manual:
        print("No manually-tracked goals configured. Run life_goals_onboarding.py first.")
        return {}

    user_name = goals_config.get("user_name", "")
    date_str = target_date.isoformat()
    existing_entry = existing_log.get(date_str, {})

    print(f"\n{'─' * 50}")
    print(f"  Daily Check-In — {target_date.strftime('%A, %B %-d, %Y')}")
    if user_name:
        print(f"  Hi {user_name}. 30 seconds. How did today go?")
    print('─' * 50)

    entry: dict = dict(existing_entry)  # preserve any existing values

    # Group by domain for cleaner prompts
    by_domain: dict[str, list[dict]] = {}
    for g in manual:
        domain = g["_domain"]
        by_domain.setdefault(domain, []).append(g)

    for domain_label, domain_goals in by_domain.items():
        print(f"\n  {domain_label}")
        for goal in domain_goals:
            icon = goal.get("brief_icon", "•")
            label = goal.get("label", goal["id"])
            default = existing_entry.get(goal["id"], True)
            result = _prompt_yn(f"    {icon} {label}?", default)
            entry[goal["id"]] = result

    return entry


def _resolve_date(text: str, event_at: str | None) -> date:
    """"Yesterday" in the text wins over event_at, since the GPT isn't
    instructed to compute relative dates itself — the raw text is the more
    reliable signal for this specific phrasing. Otherwise: event_at if given,
    else today.
    """
    tl = text.lower()
    if "yesterday" in tl:
        return date.today() - timedelta(days=1)
    if event_at:
        try:
            return date.fromisoformat(event_at[:10])
        except ValueError:
            pass
    return date.today()


def _snapshot_log() -> None:
    if not LOG_PATH.exists():
        return
    import shutil
    from datetime import datetime as _dt
    core.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    tag = _dt.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(LOG_PATH, core.SNAPSHOTS_DIR / f"personal_log.pre-record-{tag}.json")


def record_personal_practice(text: str, event_at: str | None = None, *, apply: bool = False) -> dict:
    """Non-interactive counterpart to log_day(): match a first-person
    declaration against every manually-tracked goal's log_keywords and mark
    each match True for the resolved date.

    Multi-label by design — one sentence ("Mary and I prayed and read the
    bible") can complete morning_prayer, devotions, AND mary_connection at
    once; there's no identity ambiguity between goals the way there is
    between EOLMS loops, so no confidence gate is needed here — every
    keyword hit logs that goal.

    Wired to the live Custom GPT via ingestExecutiveDeclaration's
    personal_practice_logged event type (see intelligence_triage.py /
    server.py::_execute_executive_declaration()) — this is what makes "Mary
    and I prayed together yesterday" actually persist instead of just being
    narrated back with no backing mutation (RB-DEFECT-062).
    """
    goals_config = _load_goals()
    manual = _keyword_loggable_goals(goals_config)
    tl = text.lower()
    matched = [g for g in manual if any(kw.lower() in tl for kw in (g.get("log_keywords") or []))]
    if not matched:
        return {"status": "no_match"}

    target_date = _resolve_date(text, event_at)
    date_str = target_date.isoformat()

    log = _load_log()
    entry = dict(log.get(date_str) or {})
    for g in matched:
        entry[g["id"]] = True
    log[date_str] = entry

    goal_ids = [g["id"] for g in matched]
    if not apply:
        return {"status": "dry_run", "date": date_str, "goals_logged": goal_ids}

    _snapshot_log()
    _save_log(log)
    return {"status": "applied", "date": date_str, "goals_logged": goal_ids}


# ---------------------------------------------------------------------------
# Personal relationship events + life timeline (RB-DEFECT-062 sibling gap)
#
# record_personal_practice() above only answers "did you do goal X today" —
# it has no way to capture a narrative event ("we had a date night",
# "I supported Mary at her retirement party") as evidence on a relationship,
# nor to note a significant personal event on a timeline independent of any
# tracked goal. Before this, that content had literally nowhere to go: not
# a Life Lens goal, not a professional relationship_intake.py interaction
# (Mary isn't a baseline_index.json "network contact" — no executive_weight,
# no strategic_classification, none of that machinery applies to a spouse),
# and not a to-do (nothing was committed to for the future). RB would
# narrate a confident list of "mutations" for this content with nothing
# backing it — the same failure mode RB-DEFECT-062 fixed for prayer/
# devotions/mary_connection, just for a different subset of the same report.
# ---------------------------------------------------------------------------

# Event types logged against a named personal relationship (currently just
# Mary — the only spouse/family member life_goals.yaml models at all).
_PERSONAL_RELATIONSHIP_INDICATORS: dict[str, list[str]] = {
    "spiritual_activity": [
        "prayed together", "pray together", "read the bible together",
        "bible study together", "devotions together", "prayed with mary",
    ],
    "date_night": ["date night", "went on a date", "had a date"],
    "milestone_support": [
        "retirement party", "her graduation", "his graduation",
        "her promotion", "his promotion", "surprise party",
        "celebrated her", "celebrated his", "attended her", "attended his",
        "supported her at", "supported him at", "supported mary at",
    ],
    "quality_time": [
        "quality time with mary", "spent the day with mary", "time with mary",
    ],
}

# A significant personal event belongs on the timeline regardless of who
# else was involved — distinct from the per-relationship events above.
_LIFE_EVENT_INDICATORS = [
    "retirement party", "graduation", "promotion", "anniversary",
    "new baby", "engagement", "wedding", "milestone",
]


def _load_json_list(path: Path) -> list[dict]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_json_list(path: Path, items: list[dict]) -> None:
    path.write_text(json.dumps(items, indent=2, default=str), encoding="utf-8")


def _make_event_id(prefix: str) -> str:
    from datetime import datetime as _dt, timezone as _tz
    import uuid as _uuid
    ts = _dt.now(_tz.utc).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}-{ts}-{_uuid.uuid4().hex[:6]}"


def record_personal_relationship_events(text: str, event_at: str | None = None, *, apply: bool = False) -> dict:
    """Detect personal-relationship events and life events in a first-person
    declaration; when apply=True, append them to personal_relationship_log.json
    and/or personal_timeline.json.

    Deliberately produces intelligence mutations only — never a to-do. A
    report of something that already happened ("we had", "I supported") is
    evidence, not a commitment; nothing here writes to loop_ledger.md or
    system/eolms/loops.json.
    """
    from datetime import datetime as _dt, timezone as _tz

    tl = text.lower()
    target_date = _resolve_date(text, event_at)
    date_str = target_date.isoformat()
    now_iso = _dt.now(_tz.utc).isoformat()

    relationship_events = []
    for event_type, keywords in _PERSONAL_RELATIONSHIP_INDICATORS.items():
        if any(kw in tl for kw in keywords):
            relationship_events.append({
                "id": _make_event_id("pre"),
                "date": date_str,
                "person": "Mary",
                "event_type": event_type,
                "source_text_snippet": text[:300],
                "created_at": now_iso,
            })

    life_events = []
    if any(kw in tl for kw in _LIFE_EVENT_INDICATORS):
        life_events.append({
            "id": _make_event_id("lte"),
            "date": date_str,
            "description": text[:300],
            "valence": "positive",
            "created_at": now_iso,
        })

    if not relationship_events and not life_events:
        return {"status": "no_match"}

    result = {
        "status": "applied" if apply else "dry_run",
        "date": date_str,
        "relationship_events": [e["event_type"] for e in relationship_events],
        "life_events_recorded": len(life_events),
    }
    if not apply:
        return result

    if relationship_events:
        log = _load_json_list(RELATIONSHIP_LOG_PATH)
        log.extend(relationship_events)
        _save_json_list(RELATIONSHIP_LOG_PATH, log)
    if life_events:
        timeline = _load_json_list(TIMELINE_PATH)
        timeline.extend(life_events)
        _save_json_list(TIMELINE_PATH, timeline)

    return result


def cmd_record(text: str, event_at: str | None, confirm: bool) -> int:
    result = record_personal_practice(text, event_at, apply=confirm)
    print(json.dumps(result, indent=2))
    if result["status"] == "dry_run":
        print("\nDRY RUN — not written. Re-run with --confirm to apply.", file=sys.stderr)
    return 0


def show_log(days: int = 7) -> None:
    goals_config = _load_goals()
    log = _load_log()
    manual = _manual_goals(goals_config)

    if not manual:
        print("No manually-tracked goals configured.")
        return

    today = date.today()
    dates = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]

    # Header
    header_goals = [g["id"] for g in manual]
    header_icons = [g.get("brief_icon", "?") for g in manual]
    header_labels = [g.get("label", g["id"])[:12] for g in manual]

    print(f"\n{'─' * 60}")
    print(f"  Personal Log — last {days} days")
    print('─' * 60)
    print(f"  {'Date':<12}" + "  ".join(f"{l:<14}" for l in header_labels))
    print(f"  {'─'*10}" + "  ".join("─" * 14 for _ in header_labels))

    for d in dates:
        ds = d.isoformat()
        entry = log.get(ds, {})
        day_label = d.strftime("%a %-m/%-d")
        if d == today:
            day_label += " ◀"
        row = f"  {day_label:<12}"
        for gid, icon in zip(header_goals, header_icons):
            val = entry.get(gid)
            if val is None:
                cell = "·"
            elif val:
                cell = f"✓"
            else:
                cell = "✗"
            row += f"  {cell:<14}"
        print(row)

    # Streak summary
    print()
    for goal in manual:
        gid = goal["id"]
        label = goal.get("label", gid)
        icon = goal.get("brief_icon", "•")
        streak = 0
        for d in reversed(dates):
            val = log.get(d.isoformat(), {}).get(gid)
            if val is True:
                streak += 1
            else:
                break
        target = goal.get("target", 7)
        unit = goal.get("target_unit", "days/week")
        recent_week = [log.get((today - timedelta(days=i)).isoformat(), {}).get(gid)
                       for i in range(7)]
        week_count = sum(1 for v in recent_week if v is True)
        status = "✓" if week_count >= target else ("⚠️" if week_count >= target * 0.5 else "✗")
        print(f"  {icon} {label}: {week_count}/{target} this week {status}  (streak: {streak}d)")


def main() -> int:
    p = argparse.ArgumentParser(description="RB Personal Daily Check-In")
    p.add_argument("--date", default=None, help="Date to log (YYYY-MM-DD). Default: today.")
    p.add_argument("--show", action="store_true", help="Show log history")
    p.add_argument("--days", type=int, default=7, help="Days to show (with --show)")
    p.add_argument(
        "--record", default=None, metavar="TEXT",
        help="Non-interactive: match TEXT against manually-tracked goals' log_keywords "
             "and record matches for the resolved date. Dry-run by default — pass --confirm to write."
    )
    p.add_argument("--event-at", dest="event_at", default=None, help="ISO date override (with --record).")
    p.add_argument("--confirm", action="store_true", help="Apply the --record match (default: dry run).")
    args = p.parse_args()

    if args.record is not None:
        return cmd_record(args.record, args.event_at, args.confirm)

    if args.show:
        show_log(args.days)
        return 0

    target = date.fromisoformat(args.date) if args.date else date.today()
    goals_config = _load_goals()
    log = _load_log()

    entry = log_day(target, goals_config, log)
    if not entry:
        return 1

    log[target.isoformat()] = entry
    _save_log(log)
    print(f"\n✓ Check-in saved for {target.isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
