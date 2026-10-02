#!/usr/bin/env python3
"""
mcp_smoke_test.py — verify every MCP tool's underlying compute path works.

This script does NOT require the `mcp` package. It calls the same
functions the MCP server's tool handlers call, against your real
baseline, and validates the response shape. If this passes, you can
install `mcp` and register the server with high confidence.

Usage:
    python3 mcp_smoke_test.py

Exits 0 on success, 1 on any failure. Prints a per-tool table.
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core  # noqa: E402
import daily_brief  # noqa: E402
import gap_detection  # noqa: E402
import validate_baseline as vb  # noqa: E402

TODAY = date.today()


CHECKS = []


def check(name: str, expected_keys: list[str] | None = None):
    """Decorator that turns a function into a check."""
    def wrap(fn):
        CHECKS.append((name, fn, expected_keys))
        return fn
    return wrap


# Each check returns a (sample, ok) tuple. ok==True if the result has the
# expected shape, False otherwise.

@check("rb.daily_brief", ["today", "baseline", "crossings", "loops", "active_threads"])
def t_daily_brief():
    return daily_brief.build_report(TODAY)

@check("rb.validate_baseline", ["integrity"])
def t_validate():
    return {"integrity": vb.integrity_checks(core.load_baseline(), TODAY)}

@check("rb.gap_detection", ["rcs_without_cards", "totals"])
def t_gap():
    return gap_detection.build_report()

@check("rb.loop_parser", ["today", "buckets", "totals"])
def t_loops():
    from dataclasses import asdict
    loops = core.parse_loop_ledger()
    buckets = core.loops_by_status(loops, TODAY)
    return {
        "today": TODAY.isoformat(),
        "buckets": {k: [{**asdict(L), "opened": L.opened.isoformat(), "target": L.target.isoformat()} for L in v] for k, v in buckets.items()},
        "totals": {k: len(v) for k, v in buckets.items()},
    }

@check("rb.network_gap", None)
def t_network_gap():
    return core.cluster_inner_anchor_score(core.load_baseline(), min_cluster=5)[:5]

@check("rb.drr_score", None)
def t_drr_top():
    baseline = core.load_baseline()
    rows = [core.drr_score(e, TODAY) for e in baseline]
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:5]

@check("rb.drr_score (by id)", ["score", "components"])
def t_drr_one():
    baseline = core.load_baseline()
    target = next((e for e in baseline if e.get("signal_class") == "RC"), None)
    if not target:
        return {}
    return core.drr_score(target, TODAY)

@check("rb.read_status", None)
def t_read_status():
    return (core.PROJECT_DIR / "system" / "STATUS.md").read_text()[:300]

@check("rb.read_manifest", None)
def t_read_manifest():
    return (core.PROJECT_DIR / "system" / "MANIFEST.md").read_text()[:300]

@check("rb.list_protocols", ["count", "protocols"])
def t_protocols():
    return json.loads((core.SYSTEM_DIR / "protocols" / "index.json").read_text())

@check("rb.list_active_threads", None)
def t_threads():
    return [t for t in core.load_active_threads() if t.get("status") == "open"]

@check("rb.calendar_overlay", ["fetched_at", "today", "tomorrow", "this_week"])
def t_cal():
    return core.calendar_overlay(TODAY)

@check("rb.email_overlay", ["from_baseline", "active_thread_company_hits"])
def t_email():
    return core.email_overlay()

@check("rb.social_overlay", ["from_baseline", "active_thread_company_hits", "topic_signal"])
def t_social():
    return core.social_overlay()

@check("rb.social_outbound_overlay", ["recent_posts", "engagement_by_contact", "engagement_silence"])
def t_social_out():
    return core.social_outbound_overlay(today=TODAY)

@check("rb.post_recommendations", None)
def t_post_recs():
    return core.post_recommendations(today=TODAY)

@check("rb.network_analysis", ["strengths", "weaknesses", "bridges", "composition_health", "recommendations"])
def t_network_analysis():
    return core.network_analysis(today=TODAY)

@check("rb.find_intro", ["target", "candidate_brokers", "insiders"])
def t_find_intro():
    return core.find_intro_paths("Toast", limit=3, today=TODAY)

@check("rb.recent_sessions", None)
def t_recent_sessions():
    return core.load_recent_sessions(limit=2)

@check("rb.interaction_overlay", ["matched_contacts", "proposed_last_touch_updates", "unmatched_recurring_handles", "totals"])
def t_interaction():
    return core.interaction_overlay(today=TODAY)


def run() -> int:
    failures = 0
    print(f"{'tool':<40} {'shape':<10} {'sample':<40}")
    print("-" * 100)
    for name, fn, expected_keys in CHECKS:
        try:
            result = fn()
            if expected_keys:
                missing = [k for k in expected_keys if not isinstance(result, dict) or k not in result]
                ok = not missing
                shape = f"keys ok" if ok else f"MISSING {missing}"
            else:
                ok = result is not None
                shape = "ok" if ok else "EMPTY"
            sample = ""
            if isinstance(result, dict):
                keys = list(result.keys())[:4]
                sample = ",".join(keys)
            elif isinstance(result, list):
                sample = f"list[{len(result)}]"
            elif isinstance(result, str):
                sample = result[:38].replace("\n", " ") + "..."
            status = "OK" if ok else "FAIL"
            print(f"{name:<40} {status:<10} {sample:<40}")
            if not ok:
                failures += 1
        except Exception as e:
            failures += 1
            print(f"{name:<40} {'EXCEPT':<10} {type(e).__name__}: {str(e)[:30]}")
            traceback.print_exc(file=sys.stderr)

    print("-" * 100)
    if failures == 0:
        print(f"All {len(CHECKS)} tool compute paths pass.")
        print("Ready to install `mcp` and register the server. See system/mcp/README.md.")
        return 0
    print(f"{failures}/{len(CHECKS)} failed. Fix before registering the server.")
    return 1


if __name__ == "__main__":
    sys.exit(run())
