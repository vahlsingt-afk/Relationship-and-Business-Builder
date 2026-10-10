#!/usr/bin/env python3
"""
self_audit_sweep.py — RB-2026-08-28.

Closes the gap named in the same day's strategic assessment: RB's own
self-check tools (validate_kb_consistency.py, mutation_reconciliation.py,
jpr_recordings_index.py --verify) already existed and would have caught
several of today's real incidents (a KB-consistency check validating a
retired schema, 5 orphaned JPR recordings) -- but none of them ran on a
schedule or surfaced their findings to Todd. Each wrote a cache file or a
log line nobody was reading unless someone went looking.

This script runs all three, and when any of them has a real finding,
opens (or updates) ONE standing loop in loop_ledger.md -- which
daily_brief.py already renders prominently -- so drift surfaces on its
own instead of waiting to be rediscovered. When everything's clean, it
closes that loop if one is open. It never touches anything else; no
auto-repair, same "detection only" discipline mutation_reconciliation.py
already established.

RB-2026-09-15: added a fourth check -- the full `system/tests/` pytest
suite. Every real defect found in that day's session (a severity mislabel
in brief_acceptance_check.py that would have false-blocked a healthy
brief send, a wrong settings.json nesting path, a word-boundary substring
collision in intelligence_triage.py) was caught ONLY because the full
suite ran, not because of the three named checks above -- none of those
would have caught any of them. This is the single broadest self-check
this system has, and until now it only ran when a human remembered to.

Run as its own morning_pipeline.py step, after mutation_reconciliation
(so this reads that step's freshly-written cache, not a stale one) and
after capture_process_all (so the JPR index reflects today's sweep).

Usage:
    python3 self_audit_sweep.py            # run + apply loop update, text report
    python3 self_audit_sweep.py --json     # machine-readable
    python3 self_audit_sweep.py --dry-run  # report only, never touches loop_ledger.md
    python3 self_audit_sweep.py --skip-test-suite  # skip the ~6-7min full suite (fast iteration only)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import mutations  # noqa: E402
import validate_kb_consistency as vkc  # noqa: E402
import jpr_recordings_index as jpri  # noqa: E402
import render_reachability_check as rrc  # noqa: E402
import friday_eow_routine as fer  # noqa: E402
import conflict_pattern_monitor as cpm  # noqa: E402

TEST_SUITE_TIMEOUT_SECONDS = 900  # well above the ~6-7min observed real runtime

# RB-2026-08-28: NOT a fixed loop id -- loop_ledger.md's convention is one
# fresh L-YYYY-MM-DD-NNN id per real loop, and IDs are assumed unique by
# every downstream consumer (parse_loop_ledger(), the brief renderer).
# Reusing one fixed id across repeated open/close cycles would leave a
# closed row and a later open row sharing the same id. Instead this marker
# string, always the first thing in the description, is how a real (freshly
# assigned) loop id is found again on a later run.
#
# RB-2026-10-08: this marker and the "clean" reason string below are now
# mutations.cmd_loop_close()'s single source of truth for recognizing (and
# gating the manual close of) a self-audit loop -- imported from there,
# not redefined here, so the two can never drift apart.
DESCRIPTION_MARKER = mutations.SELF_AUDIT_DESCRIPTION_MARKER
MUTATION_RECONCILIATION_CACHE = core.SYSTEM_DIR / ".cache" / "mutation_reconciliation.json"


def _kb_findings() -> list[str]:
    report = vkc.build_report()
    findings = []
    if report["stale_op_mentions"]:
        findings.append(
            f"KB instructions mention {len(report['stale_op_mentions'])} op(s) not in the live "
            f"tool set: {', '.join(report['stale_op_mentions'][:5])}"
            + ("..." if len(report["stale_op_mentions"]) > 5 else "")
        )
    if report["unrouted_live_ops"]:
        findings.append(
            f"{len(report['unrouted_live_ops'])} live tool(s) have zero KB routing: "
            f"{', '.join(report['unrouted_live_ops'][:5])}"
            + ("..." if len(report["unrouted_live_ops"]) > 5 else "")
        )
    return findings


def _mutation_reconciliation_findings() -> list[str]:
    if not MUTATION_RECONCILIATION_CACHE.exists():
        return []
    try:
        report = json.loads(MUTATION_RECONCILIATION_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    silent = [op for op, data in report.get("ops", {}).items() if data.get("status") == "silent"]
    if not silent:
        return []
    return [f"{len(silent)} write op(s) receiving traffic with zero confirmed mutations: {', '.join(silent[:5])}"]


def _jpr_findings() -> list[str]:
    index = jpri.build_index()
    if "error" in index:
        return []
    jpri.save_index(index)
    unresolved = [r for r in index["recordings"] if r["status"] in ("raw_only_unqueued", "pending")]
    if not unresolved:
        return []
    return [f"{len(unresolved)} JPR recording(s) sitting unqueued or unprocessed"]


# RB-DEFECT-074: skipped, failed and passed must be distinguishable. Previously
# `--skip-test-suite` returned [] -- the same value as a green run -- so a sweep
# with every other check clean would close the self-audit loop as "verified
# clean" without a single test having run, and an empty `test_suite` field read
# as "passed". The report below is the single record of what actually happened.
_TEST_REPORT_UNKNOWN = {"status": "unknown"}
_last_test_report: dict = dict(_TEST_REPORT_UNKNOWN)
LAST_RUN_PATH = core.SYSTEM_DIR / "audit" / "self_audit_last_run.json"


def _parse_pytest_summary(output: str) -> dict:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    line = ""
    for candidate in re.findall(r"^=*\s*(.*\d+ (?:passed|failed|error).*?)\s*=*$", output, re.MULTILINE):
        line = candidate
    for n, word in re.findall(r"(\d+) (passed|failed|errors?|skipped)", line):
        counts["errors" if word.startswith("error") else word] = int(n)
    return counts


def _test_suite_findings(*, skip: bool = False) -> list[str]:
    """Run the full system/tests/ pytest suite -- the broadest self-check
    this system has (see module docstring for why the three checks above
    are not a substitute for it). Best-effort: a suite that can't even run
    (timeout, import error) is itself a finding, never silently ignored.

    Side effect: records what happened in `_last_test_report` with an explicit
    status of passed | failed | skipped | error, counts, and ran_at."""
    global _last_test_report
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if skip:
        _last_test_report = {"status": "skipped", "ran_at": None, "recorded_at": now,
                             "detail": "--skip-test-suite: no tests were executed"}
        return []
    tests_dir = core.SYSTEM_DIR / "tests"
    started = datetime.now(timezone.utc)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(tests_dir), "-q"],
            capture_output=True, text=True, cwd=str(core.PROJECT_DIR),
            timeout=TEST_SUITE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        _last_test_report = {"status": "error", "ran_at": now,
                             "detail": f"did not complete within {TEST_SUITE_TIMEOUT_SECONDS}s"}
        return [f"full test suite did not complete within {TEST_SUITE_TIMEOUT_SECONDS}s"]
    except Exception as exc:  # noqa: BLE001
        _last_test_report = {"status": "error", "ran_at": now, "detail": str(exc)}
        return [f"full test suite could not be run: {exc}"]

    output = proc.stdout + proc.stderr
    counts = _parse_pytest_summary(output)
    duration = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
    if proc.returncode == 0:
        _last_test_report = {"status": "passed", "ran_at": now, "duration_s": duration, **counts}
        return []

    failed_names = re.findall(r"^FAILED (\S+)", output, re.MULTILINE)
    summary_match = re.search(r"^(\d+ failed.*)$", output, re.MULTILINE)
    summary = summary_match.group(1) if summary_match else f"pytest exited {proc.returncode}"
    _last_test_report = {"status": "failed", "ran_at": now, "duration_s": duration,
                         "failed_tests": failed_names[:20], **counts}
    names_part = (
        (": " + ", ".join(failed_names[:5]) + ("..." if len(failed_names) > 5 else ""))
        if failed_names else ""
    )
    return [f"full test suite failing ({summary}){names_part}"]


def _reachability_findings() -> list[str]:
    """Self-healing check #1 (RB-2026-10-08): structural (AST-based) check
    for the dominant recurring defect class this system has -- a function
    computes real data but nothing reachable from its entry function's live
    code path ever calls it. Found by hand three times in one session
    (pending_mutations, review-queue backlog, the Group B sections) before
    this check existed. Allowlist-filtered -- only reports functions that
    are both unreachable AND not a reviewed, acknowledged exclusion in
    render_reachability_allowlist.json, so this doesn't re-flag Group C's
    deliberately-excluded routine/cadence sections every single day."""
    try:
        report = rrc.run_all_checks()
    except Exception as exc:  # noqa: BLE001
        return [f"render_reachability_check could not run: {exc}"]
    return report["findings"]


def _artifact_consistency_findings() -> list[str]:
    """RB defect 2026-09-30 (Five Guys), "Reconciliation control"
    recommendation -- promoted from weekly-only (friday_eow_routine.py) to
    daily (Todd, 2026-10-08): cross-artifact drift between an active
    engagement's Blue Sheet, Background Brief, evidence ledger, and linked
    Master Account Plan shouldn't wait up to a week to surface. Reuses
    friday_eow_routine.check_artifact_consistency() directly -- one
    detection function, now fed into both the daily sweep and the weekly
    narrative report, not two divergent implementations."""
    try:
        results = fer.check_artifact_consistency()
    except Exception as exc:  # noqa: BLE001
        return [f"check_artifact_consistency could not run: {exc}"]
    findings: list[str] = []
    for r in results:
        if r.get("error"):
            findings.append(f"artifact consistency check error: {r['error']}")
            continue
        lagging_str = ", ".join(f"{name} {days}d behind" for name, days in r["lagging_artifacts"].items())
        findings.append(
            f"{r['account_id']}: artifact drift -- newest is {r['newest_artifact']} "
            f"({r['newest_date']}), {lagging_str}"
        )
    return findings


def _conflict_pattern_findings() -> list[str]:
    """Self-healing / learning check #2 (RB-2026-10-08, Todd: "monitor and
    expand as needed... the CoS learns about the conflicts, why they occur
    and how they are being resolved"). ecosystem_intelligence.py's
    check_relationship_conflict() already makes a real, audited decision
    every time a claim rivals an existing one, logged to conflict_queue.
    jsonl -- but nothing ever read that log back. Confirmed live: the same
    Blaze Pizza POS rivalry (Qu vs. Oracle) was independently re-confirmed
    as unresolved on 62 separate pipeline runs across 13 days, invisible
    the whole time. This surfaces exactly that pattern -- a recurring,
    never-escalated rivalry, or a legacy stuck-forever entry -- without
    ever picking a winner itself."""
    try:
        return cpm.collect_findings()
    except Exception as exc:  # noqa: BLE001
        return [f"conflict_pattern_monitor could not run: {exc}"]


def collect_findings(*, skip_test_suite: bool = False) -> dict:
    global _last_test_report
    _last_test_report = dict(_TEST_REPORT_UNKNOWN)
    findings = {
        "kb_consistency": _kb_findings(),
        "mutation_reconciliation": _mutation_reconciliation_findings(),
        "conflict_patterns": _conflict_pattern_findings(),
        "jpr_captures": _jpr_findings(),
        "render_reachability": _reachability_findings(),
        "artifact_consistency": _artifact_consistency_findings(),
        "test_suite": _test_suite_findings(skip=skip_test_suite),
    }
    all_findings = [f for group in findings.values() for f in group]
    report = dict(_last_test_report)
    return {
        "findings_by_check": findings, "all_findings": all_findings, "clean": not all_findings,
        # An empty `test_suite` findings list is NOT a pass: read this instead.
        "test_suite_report": report,
        # clean AND the tests actually ran green ("unknown" = legacy/mocked caller).
        "verified_clean": (not all_findings) and report.get("status") in ("passed", "unknown"),
    }


def _find_open_self_audit_loop_id() -> str | None:
    """Find the id of the currently-open self-audit loop, if any, by its
    description marker -- not a fixed id (see DESCRIPTION_MARKER note)."""
    text = mutations._read_ledger()
    for line in text.splitlines():
        if not line.startswith("| L-"):
            continue
        if DESCRIPTION_MARKER in line and "open" in line.lower():
            return line.split("|")[1].strip()
    return None


def _refresh_open_loop_description(loop_id: str, new_description: str, *, dry_run: bool = False) -> bool:
    """Rewrite an open loop's description field in place (target date left
    untouched -- refreshing findings shouldn't reset the urgency clock).
    Returns True if the row was found and changed. Same '| '-split pattern
    mutations.cmd_loop_redate() already uses for in-place field edits."""
    text = mutations._read_ledger()
    lines = text.splitlines()
    changed = False
    out = []
    for line in lines:
        if line.startswith(f"| {loop_id} |") and "open" in line.lower():
            parts = line.split(" | ")
            if len(parts) >= 6 and parts[3].strip() != new_description:
                parts[3] = f" {new_description} "
                line = " | ".join(parts)
                changed = True
        out.append(line)
    if not changed:
        return False
    new_text = "\n".join(out) + ("\n" if text.endswith("\n") else "")
    if dry_run:
        return True
    mutations.snapshot(core.LOOP_LEDGER_PATH, f"pre-self-audit-refresh-{loop_id}-{mutations._now_tag()}")
    mutations._write_ledger(new_text)
    return True


def _prior_self_audit_recurrences(current_findings: list[str]) -> list[dict]:
    """Find previously-CLOSED self-audit loops that reported at least one of
    TODAY's findings verbatim.

    RB-2026-09-19: real, live gap found in a strategic-assessment follow-up.
    The identical "closeThread, refreshSources" silent-op finding was
    closed on 2026-09-04 (no fix recorded) and again on 2026-09-11 ("Todd
    believes ... was addressed in development this week") -- neither
    closure was actually verified against a fresh mutation_reconciliation
    run, and the finding was still live both times. Nothing distinguished
    "I believe this is fixed" from "the automated check confirms this is
    fixed," so the same defect kept getting marked resolved without ever
    being root-caused.

    Each `_xxx_findings()` function above generates its finding text
    deterministically from the same underlying data, so an unfixed defect
    reproduces byte-identical finding text run over run -- exact substring
    matching against a prior loop's full description is reliable here, not
    a fuzzy guess. This does not block anyone from closing a recurring
    finding (that stays a human judgment call, e.g. deliberately
    deprioritizing something) -- it only makes the recurrence impossible to
    miss by putting it in the very text a closer has to read."""
    loops = core.parse_loop_ledger(path=core.LOOP_LEDGER_PATH)
    recurrences = []
    for loop in loops:
        if not loop.closed or DESCRIPTION_MARKER not in loop.description:
            continue
        if any(f and f in loop.description for f in current_findings):
            recurrences.append({"loop_id": loop.id, "closed_reason": loop.status_raw})
    return recurrences


def apply_loop_update(result: dict, *, dry_run: bool = False) -> str:
    """Open a new loop, refresh the currently-open one's description if
    today's findings differ from what it already says, or close it based
    on today's findings. Idempotent: re-running with the exact same
    findings while one's already open is a no-op; re-running with no
    findings closes whichever one is open, if any."""
    open_id = _find_open_self_audit_loop_id()

    if result["clean"] and not result.get("verified_clean", True):
        # No findings, but the test suite never ran green (skipped/errored):
        # that is "unverified", not "clean". Never close a loop on it.
        return "no_action_unverified_tests_" + str(result["test_suite_report"].get("status"))

    if result["clean"]:
        if open_id:
            args = SimpleNamespace(id=open_id, reason=mutations.SELF_AUDIT_VERIFIED_CLEAN_REASON, dry_run=dry_run)
            mutations.cmd_loop_close(args)
            return "closed"
        return "no_action_already_clean"

    description = DESCRIPTION_MARKER + " " + "; ".join(result["all_findings"])

    if open_id:
        refreshed = _refresh_open_loop_description(open_id, description, dry_run=dry_run)
        return "refreshed" if refreshed else "no_action_already_open"

    recurrences = _prior_self_audit_recurrences(result["all_findings"])
    if recurrences:
        ids = ", ".join(r["loop_id"] for r in recurrences)
        description = (
            f"[RECURRING x{len(recurrences) + 1} -- previously closed without a "
            f"verified fix: {ids}] " + description
        )

    args = SimpleNamespace(
        id=None,
        opened=date.today().isoformat(),
        party="RB self-audit",
        description=description,
        target=(date.today() + timedelta(days=3)).isoformat(),
        dry_run=dry_run,
    )
    mutations.cmd_loop_add(args)
    return "opened"


def _persist_last_run(result: dict) -> None:
    """Explicit last-run time + test coverage for the CoS to read. A skipped
    run must not overwrite the last REAL test result."""
    prior = {}
    try:
        prior = json.loads(LAST_RUN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    report = result.get("test_suite_report") or {}
    last_real = report if report.get("status") in ("passed", "failed", "error") else prior.get("last_test_run")
    LAST_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_RUN_PATH.write_text(json.dumps({
        "last_run": result["last_run"], "loop_action": result.get("loop_action"),
        "clean": result["clean"], "verified_clean": result["verified_clean"],
        "finding_count": len(result["all_findings"]),
        "this_run_test_suite": report, "last_test_run": last_real,
    }, indent=2), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-test-suite", action="store_true",
                    help="Skip the ~6-7min full pytest suite (fast local iteration only).")
    args = p.parse_args()

    result = collect_findings(skip_test_suite=args.skip_test_suite)
    action = apply_loop_update(result, dry_run=args.dry_run)
    result["loop_action"] = action
    result["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not args.dry_run:
        _persist_last_run(result)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("self_audit_sweep:")
        if result["clean"]:
            print("  status: CLEAN")
        else:
            print(f"  status: {len(result['all_findings'])} finding(s)")
            for f in result["all_findings"]:
                print(f"    - {f}")
        print(f"  loop_action: {action}")

    return 1 if not result["clean"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
