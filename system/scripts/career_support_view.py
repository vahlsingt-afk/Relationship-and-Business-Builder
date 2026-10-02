#!/usr/bin/env python3
"""
career_support_view.py — no-stated-current-role network view.

RB ended-role cleanup (2026-08-06): people whose LinkedIn evidence shows
their last listed role ended and no successor role is stated are excluded
from active company/leadership maps, but they are still a potentially
meaningful relationship/career-support opportunity. This view surfaces them,
ranked by relationship strength, last-touch recency, and how fresh the
employment read itself is.

This is a data-quality and human-support signal, not a conclusion about
unemployment. Never render or imply "unemployed" or "job seeking" — the
precise wording is "no stated current role."

Usage:
    python3 career_support_view.py                # markdown to stdout
    python3 career_support_view.py --json
    python3 career_support_view.py --write         # system/analysis/<date>-career-support-view.md
    python3 career_support_view.py --cache         # system/.cache/career_support_view.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core  # noqa: E402


ANALYSIS_DIR = core.SYSTEM_DIR / "analysis"


def _recency_days(entry: dict, today: date) -> int | None:
    last_touch = entry.get("last_touch")
    if not last_touch:
        return None
    try:
        return (today - date.fromisoformat(last_touch)).days
    except ValueError:
        return None


def _observed_epoch(observed_at: str | None) -> float:
    if not observed_at:
        return 0.0
    try:
        return datetime.fromisoformat(observed_at).timestamp()
    except ValueError:
        return 0.0


def build_view(baseline: list[dict] | None = None, today: date | None = None) -> dict:
    """Query baseline for people with employment_status=no_stated_current_role
    (equivalently, tag linkedin_no_stated_current_role) and rank them by
    relationship strength (drr_score), last-touch recency, and freshness of
    the employment-status read. Never asserts unemployment or job-seeking."""
    if baseline is None:
        baseline = core.load_baseline()
    if today is None:
        today = date.today()

    candidates = [
        e for e in baseline
        if e.get("employment_status") == "no_stated_current_role"
        or "linkedin_no_stated_current_role" in (e.get("tags") or [])
    ]

    rows = []
    for e in candidates:
        recency = _recency_days(e, today)
        drr_score = (e.get("relationship_health") or {}).get("drr_score")
        rows.append({
            "id": e.get("id"),
            "name": e.get("name"),
            "last_known_company": e.get("last_known_company"),
            "last_known_role": e.get("last_known_role"),
            "last_known_role_dates": e.get("last_known_role_dates"),
            "employment_end_date": e.get("employment_end_date"),
            "employment_status_source": e.get("employment_status_source"),
            "employment_status_observed_at": e.get("employment_status_observed_at"),
            "employment_date_confidence": e.get("employment_date_confidence"),
            "signal_class": e.get("signal_class"),
            "rc_tier": e.get("rc_tier"),
            "drr_score": drr_score,
            "last_touch": e.get("last_touch"),
            "days_since_last_touch": recency,
            "circles": e.get("circles") or [],
        })

    def _sort_key(r: dict) -> tuple:
        drr = r["drr_score"] if r["drr_score"] is not None else -1.0
        recency = r["days_since_last_touch"] if r["days_since_last_touch"] is not None else 10 ** 6
        return (-drr, recency, -_observed_epoch(r["employment_status_observed_at"]))

    rows.sort(key=_sort_key)

    return {
        "as_of": today.isoformat(),
        "baseline_total": len(baseline),
        "candidate_count": len(rows),
        "candidates": rows,
        "guardrails": [
            "This view lists people with no stated current role — it is a "
            "data-quality and relationship-support signal, not a conclusion "
            "about unemployment or job-seeking status.",
            "Excluded from active company/leadership/insider/account-map/"
            "campaign/warm-introduction calculations elsewhere in RB.",
            "Offer supportive outreach only when appropriate; never assert "
            "or imply the person is unemployed or job-seeking without "
            "explicit evidence.",
        ],
    }


def _cell(text: str | None) -> str:
    """Escape markdown table-breaking pipe characters (titles like
    'EVP | Chief Technology & Innovation Officer' contain literal pipes)."""
    return (text or "—").replace("|", "\\|")


def render_markdown(rep: dict) -> str:
    out: list[str] = []
    out.append(f"# No stated current role — network support view — {rep['as_of']}\n")
    out.append(
        f"**{rep['candidate_count']}** of {rep['baseline_total']:,} baseline contacts "
        "currently have no stated current role. This is a data-quality and "
        "relationship-support signal, not a conclusion about unemployment.\n"
    )
    out.append("---\n")

    if not rep["candidates"]:
        out.append("(no contacts currently in this state)")
        out.append("")
        return "\n".join(out) + "\n"

    out.append("| Name | Last known role | Last known company | Ended | Relationship | Last touch |")
    out.append("|---|---|---|---|---:|---:|")
    for r in rep["candidates"]:
        role = _cell(r["last_known_role"])
        company = _cell(r["last_known_company"])
        ended = _cell(r["employment_end_date"] or r["last_known_role_dates"])
        drr = f"{r['drr_score']:.0f}" if r["drr_score"] is not None else "—"
        touch = (
            f"{r['days_since_last_touch']}d ago" if r["days_since_last_touch"] is not None
            else "never recorded"
        )
        out.append(f"| **{_cell(r['name'])}** | {role} | {company} | {ended} | {drr} | {touch} |")

    out.append("\n## Guardrails\n")
    out.extend(f"- {g}" for g in rep["guardrails"])
    out.append("")
    out.append(
        f"*Generated by `system/scripts/career_support_view.py` against baseline of "
        f"{rep['baseline_total']:,} entries on {rep['as_of']}.*"
    )
    return "\n".join(out) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="ISO date (default: system date)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--write", action="store_true",
                   help="Persist the markdown to system/analysis/<date>-career-support-view.md")
    p.add_argument("--cache", action="store_true",
                   help="Write to system/.cache/career_support_view.json")
    args = p.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()
    rep = build_view(today=today)

    if args.cache:
        core.write_cache("career_support_view", rep, source="career_support_view.py")

    if args.json:
        print(json.dumps(rep, indent=2, default=str))
        return 0

    md = render_markdown(rep)

    if args.write:
        ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
        target = ANALYSIS_DIR / f"{today.isoformat()}-career-support-view.md"
        target.write_text(md)
        print(f"Wrote {target.relative_to(core.PROJECT_DIR)}.")
        return 0

    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
