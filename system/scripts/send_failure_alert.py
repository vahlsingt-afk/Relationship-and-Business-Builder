#!/usr/bin/env python3
"""
send_failure_alert.py — sent by morning_pipeline.py instead of the normal
brief emails when brief_acceptance_check.py's gate fails.

RB-2026-08-23: the whole point of the acceptance gate is that a failure is
loud, not a JSON field nobody opens. This sends an unmistakable, short
message in place of the brief content -- reusing the same SMTP transport
send_brief_email.py already has, not a second credential-resolution path.

Usage:
    python3 send_failure_alert.py --date 2026-08-23 --result-json '{...}'
    python3 send_failure_alert.py --date 2026-08-23   # reads the result
        brief_acceptance_check.py already wrote to .cache/brief_acceptance_result.json
"""
from __future__ import annotations

import argparse
import json
import smtplib
import sys
from datetime import date
from email.mime.text import MIMEText
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
from send_brief_email import _resolve_recipient, _resolve_smtp  # noqa: E402

RESULT_PATH = core.SYSTEM_DIR / ".cache" / "brief_acceptance_result.json"
REPAIR_RECEIPT_PATH = core.SYSTEM_DIR / ".cache" / "brief_repair_receipt.json"
BRIEFS_DIR = core.SYSTEM_DIR / "briefs"


def _repair_summary_lines(target_date: date) -> list[str]:
    """RB-DEFECT-072: 'Failure alerts state what RB tried, what changed, why
    recovery stopped' -- read the repair loop's own receipt (written by
    morning_pipeline.py only when it actually attempted a repair) rather
    than the alert re-deriving or guessing at what happened."""
    if not REPAIR_RECEIPT_PATH.exists():
        return []
    try:
        receipt = json.loads(REPAIR_RECEIPT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if receipt.get("date") != target_date.isoformat():
        return []  # stale receipt from a prior day -- not this run's story

    lines = [f"RB attempted repair before giving up ({len(receipt.get('attempts', []))} attempt(s)):"]
    for i, attempt in enumerate(receipt.get("attempts", []), 1):
        actions = attempt.get("actions_taken") or []
        if not actions:
            lines.append(f"  Attempt {i}: no safe repair action was available for the remaining failure(s).")
            continue
        for action in actions:
            check = action.get("check", "?")
            if "doc" in action:  # brief_repair.repair_brief_file's receipt shape
                removed = action.get("removed") or []
                if action.get("changed"):
                    lines.append(
                        f"  Attempt {i} [{check}]: removed {len(removed)} duplicate headline(s) "
                        f"from {action.get('doc', '?')}-brief.md."
                    )
                else:
                    lines.append(f"  Attempt {i} [{check}]: {action.get('reason', 'no change made')}.")
            elif action.get("repair_action") == "recompute_delivery_readiness":
                lines.append(
                    f"  Attempt {i} [{check}]: republished canonical artifacts "
                    f"({'succeeded' if action.get('changed') else 'did not complete cleanly'})."
                )
            else:
                lines.append(f"  Attempt {i} [{check}]: {json.dumps(action, default=str)}")
        if attempt.get("no_progress"):
            lines.append(f"  Attempt {i}: stopped -- repair made no difference to the remaining failure(s) (no-progress).")
    if not receipt.get("final_passed"):
        lines.append("Recovery stopped: repair budget exhausted or no further safe action available.")
    return lines


def _artifact_pointer_lines(target_date: date) -> list[str]:
    """Where the completed-but-undelivered artifacts can be accessed, per
    RB-DEFECT-072's acceptance criteria -- both briefs are usually still
    real, complete files on disk even when the gate blocked the email."""
    lines = []
    for label, name in (("Intelligence brief", "intelligence"), ("Daily brief", "daily")):
        path = BRIEFS_DIR / f"{target_date.isoformat()}-{name}-brief.md"
        lines.append(f"  {label}: {path} ({'exists' if path.exists() else 'not generated this run'})")
    return lines


def _build_body(target_date: date, result: dict) -> str:
    fail_lines = [
        f"  - [{f['check']}] {f['detail']}"
        for f in result.get("findings", [])
        if f.get("severity") == "fail" and f.get("passed") is False
    ]
    warn_lines = [
        f"  - [{f['check']}] {f['detail']}"
        for f in result.get("findings", [])
        if f.get("severity") == "warn"
    ]
    parts = [
        f"RBB Brief Acceptance FAILED for {target_date.isoformat()}.",
        "",
        "The brief was NOT sent because it failed the acceptance gate.",
        "This means at least one required check failed -- not a routine",
        "business-state fact, an actual defect in report generation.",
        "",
        f"Failing checks ({len(fail_lines)}):",
        *fail_lines,
    ]
    if warn_lines:
        parts += ["", f"Also flagged (did not block, review anyway) ({len(warn_lines)}):", *warn_lines]
    repair_lines = _repair_summary_lines(target_date)
    if repair_lines:
        parts += ["", *repair_lines]
    parts += ["", "Completed artifacts (may still be usable even though not emailed):",
              *_artifact_pointer_lines(target_date)]
    parts += ["", "Investigate system/.cache/brief_acceptance_result.json for full detail."]
    return "\n".join(parts)


def send(target_date: date, result: dict, *, dry_run: bool = False) -> dict:
    if result.get("passed"):
        return {"sent": False, "status": "not_sent_gate_passed",
                "detail": "send_failure_alert called but the gate actually passed — nothing to alert on"}

    recipient = _resolve_recipient()
    if not recipient:
        return {"sent": False, "status": "no_recipient"}

    subject = f"⚠ RBB Brief Acceptance FAILED — {target_date.strftime('%A, %B %-d, %Y')}"
    body = _build_body(target_date, result)

    if dry_run:
        print(f"Subject: {subject}")
        print(f"To: {recipient}")
        print(body)
        return {"sent": False, "status": "dry_run", "subject": subject, "recipient": recipient}

    smtp = _resolve_smtp()
    if not smtp:
        return {"sent": False, "status": "no_smtp"}

    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = smtp["user"]
        msg["To"] = recipient
        with smtplib.SMTP(smtp["host"], smtp["port"], timeout=30) as srv:
            srv.starttls()
            srv.login(smtp["user"], smtp["password"])
            srv.sendmail(smtp["user"], [recipient], msg.as_bytes())
        return {"sent": True, "status": "smtp_sent", "recipient": recipient, "subject": subject}
    except Exception as e:  # noqa: BLE001
        return {"sent": False, "status": "smtp_error", "detail": str(e)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--result-json", type=str, default=None,
                         help="Inline JSON result; if omitted, reads .cache/brief_acceptance_result.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date) if args.date else date.today()
    if args.result_json:
        result = json.loads(args.result_json)
    elif RESULT_PATH.exists():
        result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    else:
        result = {"passed": False, "findings": [
            {"check": "result_load", "severity": "fail", "passed": False,
             "detail": "no acceptance result available to report"},
        ]}

    outcome = send(target_date, result, dry_run=args.dry_run)
    print(json.dumps(outcome, indent=2, default=str))
    return 0 if outcome.get("sent") or args.dry_run else 1


if __name__ == "__main__":
    raise SystemExit(main())
