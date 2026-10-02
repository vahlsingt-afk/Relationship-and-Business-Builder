#!/usr/bin/env python3
"""verify_weekly_plan_consistency.py — RB-DEFECT-067 semantic post-build check.

Confirms every daily artifact that displays or depends on the weekly plan
actually agrees on which week is active, after a build/render/publish cycle.
Root cause of the original defect: `weekly_plan.json` could change (a
mid-day --confirm) without the already-rendered Daily Brief, the published
artifact, or the live API payload ever finding out -- each of those reads
its own copy/cache of the plan state, and nothing checked them against each
other. `render_daily_brief.py`/`render_intelligence_brief.py` now
self-invalidate via `rb_core.weekly_plan_fingerprint()` (see those files),
which closes the most common case; this script is the independent check
that proves it, and catches the artifacts that fingerprinting doesn't cover
(the published snapshot, the live API).

Best-effort by design for the network-dependent check (live API): a
reachability failure there is reported, not treated as a hard failure of
the whole script, since the API may simply not be running when this is
invoked manually.

Usage:
    python3 verify_weekly_plan_consistency.py [--date YYYY-MM-DD] [--json]

Exit code: 0 if every checked artifact agrees with weekly_plan.json's
current week_of, 1 if any checked artifact disagrees or is stale.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core

BRIEFS_DIR = core.SYSTEM_DIR / "briefs"
PUBLISHED_DIR = core.SYSTEM_DIR / "published" / "daily"
DAILY_BRIEF_CACHE = core.SYSTEM_DIR / ".cache" / "daily_brief.json"
API_BASE = "http://127.0.0.1:8765"


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "mismatch" | "missing" | "unreachable"
    detail: str
    checked_week: str | None = None


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _week_from_sections(payload: dict | None) -> str | None:
    """Pull week_of out of a canonical_brief-shaped payload's
    weekly_plan_focus section, if present."""
    if not payload:
        return None
    data = payload.get("data") or payload
    sections = (data.get("canonical_brief") or {}).get("sections") or {}
    items = sections.get("weekly_plan_focus") or []
    for item in items:
        wk = (item.get("extras") or {}).get("week_of")
        if wk:
            return wk
    return None


def _weekly_focus_records(payload: dict | None) -> list[dict]:
    """Return every displayed weekly-plan week with its declared source.

    A pending current-week draft and an older active plan are valid at the
    same time. Treating the first week found as the active plan caused the
    2026-09-14 false alarm.
    """
    if not payload:
        return []
    data = payload.get("data") or payload
    sections = (data.get("canonical_brief") or {}).get("sections") or {}
    records = []
    for item in sections.get("weekly_plan_focus") or []:
        week = (item.get("extras") or {}).get("week_of")
        if not week:
            continue
        refs = item.get("source_refs") or []
        source = "weekly_plan_draft.json" if "weekly_plan_draft.json" in refs else "weekly_plan.json"
        records.append({"week_of": week, "source": source})
    return records


def _check_weekly_focus(name: str, payload: dict | None, source_week: str) -> CheckResult:
    records = _weekly_focus_records(payload)
    if not records:
        return CheckResult(name, "ok", "no weekly_plan_focus section present (nothing to check)")
    draft = _load_json(core.SYSTEM_DIR / "weekly_plan_draft.json") or {}
    expected = {
        "weekly_plan.json": source_week,
        "weekly_plan_draft.json": draft.get("week_of"),
    }
    for record in records:
        wanted = expected.get(record["source"])
        if not wanted or record["week_of"] != wanted:
            return CheckResult(
                name, "mismatch",
                f"displayed {record['source']} week_of={record['week_of']}, "
                f"but that source currently shows {wanted or 'missing'}",
                checked_week=record["week_of"],
            )
    summary = ", ".join(f"{r['source']}={r['week_of']}" for r in records)
    return CheckResult(name, "ok", f"weekly focus matches its declared source ({summary})")


def check_weekly_plan_source(today: date) -> tuple[str, list[CheckResult]]:
    """Returns (source_week_of, [draft-consistency check]). The live
    weekly_plan.json is the ground truth every other artifact is compared
    against — it's what render_daily_brief.py's _load_weekly_plan() reads
    directly, not a cached snapshot."""
    results: list[CheckResult] = []
    plan = _load_json(core.SYSTEM_DIR / "weekly_plan.json") or {}
    source_week = plan.get("week_of") or "unknown"

    draft = _load_json(core.SYSTEM_DIR / "weekly_plan_draft.json")
    if draft and draft.get("week_of") == source_week and draft.get("status") == "draft_pending_confirmation":
        # RB-DEFECT-067 regression guard: this exact state (draft for the
        # SAME week the active plan already covers, still marked pending)
        # is what confirm_draft() forgetting to update the draft file
        # produced. If this fires again, that fix regressed.
        results.append(CheckResult(
            "draft_state_consistency", "mismatch",
            f"weekly_plan_draft.json still shows draft_pending_confirmation for week_of="
            f"{source_week}, the SAME week weekly_plan.json already has active — the draft "
            f"was confirmed but its own status was never updated (RB-DEFECT-067 regression).",
            checked_week=source_week,
        ))
    else:
        results.append(CheckResult("draft_state_consistency", "ok",
                                    "no stale pending-draft for the active week", source_week))
    return source_week, results


def check_daily_brief_cache(source_week: str) -> CheckResult:
    payload = _load_json(DAILY_BRIEF_CACHE)
    if payload is None:
        return CheckResult("daily_brief_cache", "missing", f"{DAILY_BRIEF_CACHE} not found")
    return _check_weekly_focus("daily_brief_cache", payload, source_week)


def check_rendered_daily_brief(target_date: date) -> CheckResult:
    """Rather than re-parsing rendered markdown, compares the fingerprint
    stamped at render time (see render_daily_brief.py) against the CURRENT
    live fingerprint -- a mismatch here means the on-disk .md predates the
    weekly-plan state that's live right now, i.e. it's the exact stale-
    render failure mode this defect started from."""
    meta_path = BRIEFS_DIR / f"{target_date.isoformat()}-daily-brief.json"
    meta = _load_json(meta_path)
    if meta is None:
        return CheckResult("rendered_daily_brief", "missing", f"{meta_path} not found")
    stamped = meta.get("weekly_plan_fingerprint")
    if stamped is None:
        return CheckResult("rendered_daily_brief", "mismatch",
                            "rendered before the RB-DEFECT-067 fingerprint fix existed -- "
                            "re-render (render_daily_brief.py --force) to pick it up")
    current = core.weekly_plan_fingerprint()
    if stamped != current:
        return CheckResult("rendered_daily_brief", "mismatch",
                            f"{meta_path.name} was rendered against an older weekly-plan state "
                            f"than what's live now — the .md on disk is stale")
    return CheckResult("rendered_daily_brief", "ok", "fingerprint matches current weekly-plan state")


def check_published_artifact(source_week: str) -> CheckResult:
    latest = _load_json(PUBLISHED_DIR / "latest_brief.json")
    if latest is None:
        return CheckResult("published_artifact", "missing", "latest_brief.json not found")
    return _check_weekly_focus("published_artifact", latest, source_week)


def check_live_api(source_week: str, api_key: str | None) -> CheckResult:
    if not api_key:
        return CheckResult("live_api", "unreachable", "no API key available to check getDailyBrief")
    req = urllib.request.Request(
        f"{API_BASE}/daily_brief", headers={"X-Api-Key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as e:
        return CheckResult("live_api", "unreachable", f"API not reachable: {e}")
    week = _week_from_sections(payload)
    if week is None:
        return CheckResult("live_api", "ok", "no weekly_plan_focus section present")
    if week != source_week:
        return CheckResult("live_api", "mismatch",
                            f"live getDailyBrief shows week_of={week}, live plan shows {source_week}",
                            checked_week=week)
    return CheckResult("live_api", "ok", f"week_of={week} matches", checked_week=week)


def run_checks(target_date: date, api_key: str | None = None) -> list[CheckResult]:
    source_week, draft_results = check_weekly_plan_source(target_date)
    results = list(draft_results)
    results.append(check_daily_brief_cache(source_week))
    results.append(check_rendered_daily_brief(target_date))
    results.append(check_published_artifact(source_week))
    results.append(check_live_api(source_week, api_key))
    return results


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", default=None, help="Date to check (YYYY-MM-DD). Default: today.")
    p.add_argument("--json", action="store_true", help="Machine-readable output.")
    p.add_argument("--api-key", default=None, help="X-Api-Key for the live-API check (optional).")
    args = p.parse_args()

    target = date.fromisoformat(args.date) if args.date else date.today()
    results = run_checks(target, api_key=args.api_key)

    mismatches = [r for r in results if r.status == "mismatch"]

    if args.json:
        print(json.dumps({
            "date": target.isoformat(),
            "ok": not mismatches,
            "checks": [r.__dict__ for r in results],
        }, indent=2))
    else:
        for r in results:
            icon = {"ok": "✓", "mismatch": "✗", "missing": "?", "unreachable": "⚠"}[r.status]
            print(f"{icon} {r.name}: {r.detail}")
        print()
        if mismatches:
            print(f"FAIL — {len(mismatches)} artifact(s) inconsistent with weekly_plan.json.")
        else:
            print("PASS — all checked artifacts agree with the live weekly plan.")

    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
