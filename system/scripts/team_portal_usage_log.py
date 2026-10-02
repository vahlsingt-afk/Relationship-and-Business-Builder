#!/usr/bin/env python3
"""team_portal_usage_log.py — raw per-request traffic log for the Team Portal.

Real 2026-09-30 finding: zero request-level logging existed anywhere for
the Team Portal (system/api/team_portal_api.py) -- no way to answer
"who used this and how much." A 25MB system/api/request.log exists, but
it belongs to a completely different, unrelated server (system/api/
server.py's main rb-api) and has no caller identity anyway (that server
uses one shared static API key, not per-user tokens).

Deliberately separate from system/scripts/audit_log.py, not a new event
type added to it: audit_log.py is a curated log of CONTENT MUTATIONS
(low volume, git-tracked, one line is a meaningful business event this
codebase already commits to history). This is raw REQUEST TRAFFIC (every
GET included -- searches, profile fetches, market-news polling) at
whatever volume real usage produces -- a different concern, a different
growth rate, and no editorial value in git history. Mirrors audit_log.
py's one-file-per-month JSONL partition convention, but its own module
and its own directory (system/team/usage_log/, gitignored).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

USAGE_LOG_DIR = Path(os.environ.get(
    "RB_TEAM_PORTAL_USAGE_LOG_DIR", str(core.SYSTEM_DIR / "team" / "usage_log"),
))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _partition_key(ts: str) -> str:
    """YYYY-MM from an ISO timestamp string, same convention as audit_log.py."""
    try:
        return ts[:7]
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc).strftime("%Y-%m")


def _log_path(partition: str) -> Path:
    USAGE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    return USAGE_LOG_DIR / f"{partition}.jsonl"


def append_request(
    *, member_id: Optional[str], route: str, method: str, status_code: int,
    latency_ms: float, query: str = "", timestamp: Optional[str] = None,
    route_template: Optional[str] = None,
) -> dict:
    """One line per request. member_id may be None -- e.g. a request with
    no Authorization header at all never resolves an identity, but is
    still worth counting (traffic to a route with no credential attempt
    at all is a different signal than a revoked-token retry).

    route_template (2026-09-30 addition): the FastAPI route's own path
    pattern (e.g. "/api/vendors/{vendor_id}/company-profile"), pulled
    from request.scope["route"] by the caller -- real 2026-09-30 finding:
    `route` alone (request.url.path) bakes in the actual resolved value
    ("/api/market/earnings/Red Robin Gourmet Burgers, Inc./talking-
    points"), which fragments a per-member "what are they using" rollup
    into one row per distinct company/vendor/brand ever looked up instead
    of one row per real feature. Falls back to `route` itself when unset
    (e.g. a 404 for a path that never matched any route)."""
    ts = timestamp or _now_iso()
    record = {
        "timestamp": ts, "member_id": member_id, "route": route,
        "route_template": route_template or route, "method": method,
        "status_code": status_code, "latency_ms": round(latency_ms, 1), "query": query,
    }
    path = _log_path(_partition_key(ts))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def load_events(
    *, since: Optional[str] = None, until: Optional[str] = None, member_id: Optional[str] = None,
) -> list[dict]:
    """since/until: ISO timestamp prefixes, inclusive, plain string
    comparison (works because timestamps are always zero-padded ISO8601)."""
    if not USAGE_LOG_DIR.exists():
        return []
    events = []
    for path in sorted(USAGE_LOG_DIR.glob("*.jsonl")):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if member_id and rec.get("member_id") != member_id:
                    continue
                ts = rec.get("timestamp") or ""
                if since and ts < since:
                    continue
                if until and ts > until:
                    continue
                events.append(rec)
    return events


# Real 2026-09-30 finding (Todd): the admin UI's flat "unknown" bucket
# conflated two completely different signals -- 213 of 234 "unknown"
# requests that day were just GET / (the public SPA shell loading before
# its JS ever attaches a token to an actual /api/* call -- expected,
# harmless, by design), a handful more were browser/tooling auto-probes
# (favicons, /health, /openapi.json), and only 2 were a real /api/* call
# that got rejected for having no valid token. Those are very different
# things to see in an admin panel -- one is "people are opening the
# page," the other is "someone is hitting the API without credentials."
# Anything NOT under /api/ is a page load; under /api/ it's a rejected
# API attempt (a request that reached get_current_member() and failed to
# resolve a member, which is always 401 given this app's auth model).
def _unauthenticated_kind(route: str) -> str:
    return "rejected_api_attempt" if (route or "").startswith("/api/") else "page_load"


def summarize_by_member(events: Optional[list[dict]] = None) -> dict:
    """{key: {request_count, last_seen}} -- backs the admin UI's
    per-member usage column. Real member_id keys are real members.
    member_id=None (no credential ever resolved) is split into two
    synthetic keys instead of one flat "unknown" -- see
    _unauthenticated_kind: "unknown:page_load" (the public page/static
    assets, no token involved at all -- expected traffic) and
    "unknown:rejected_api_attempt" (an actual /api/* call that failed to
    authenticate -- the signal worth actually watching)."""
    events = events if events is not None else load_events()
    summary: dict = {}
    for rec in events:
        mid = rec.get("member_id")
        key = mid if mid else f"unknown:{_unauthenticated_kind(rec.get('route') or '')}"
        s = summary.setdefault(key, {"request_count": 0, "last_seen": None})
        s["request_count"] += 1
        ts = rec.get("timestamp")
        if ts and (s["last_seen"] is None or ts > s["last_seen"]):
            s["last_seen"] = ts
    return summary


def summarize_routes_by_member(events: Optional[list[dict]] = None, *, top_n: int = 8) -> dict:
    """{member_id: [{route_template, count}, ...]} -- the actual "who is
    using what" answer (Todd, 2026-09-30), not just a request count.
    Grouped by route_template (the FastAPI path pattern, e.g. "/api/
    vendors/{vendor_id}/company-profile"), not the raw resolved path, so
    looking up 12 different vendors doesn't fragment into 12 separate
    rows. Real member_ids only -- unauthenticated traffic has no "what
    feature" to attribute, by definition. Top `top_n` routes per member
    by count, most-used first."""
    events = events if events is not None else load_events()
    by_member: dict[str, dict] = {}
    for rec in events:
        mid = rec.get("member_id")
        if not mid:
            continue
        route = rec.get("route_template") or rec.get("route") or "?"
        counts = by_member.setdefault(mid, {})
        counts[route] = counts.get(route, 0) + 1
    return {
        mid: sorted(
            ({"route_template": r, "count": c} for r, c in counts.items()),
            key=lambda x: x["count"], reverse=True,
        )[:top_n]
        for mid, counts in by_member.items()
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cmd", choices=["summary"], nargs="?", default="summary")
    parser.add_argument("--member-id")
    args = parser.parse_args()

    events = load_events(member_id=args.member_id)
    summary = summarize_by_member(events)
    if not summary:
        print("No usage recorded yet.")
        return 0
    for member_id, s in sorted(summary.items(), key=lambda kv: -kv[1]["request_count"]):
        print(f"  {member_id:<20} {s['request_count']:>6} requests   last seen {s['last_seen']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
