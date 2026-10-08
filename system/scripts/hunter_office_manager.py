#!/usr/bin/env python3
"""Office manager: watches Hunter's deposits into the local scan folder and
tells Todd about them.

Reads hunter_drive_inbox_sync.py's append-only receipts ledger
(system/.cache/hunter_inbox_sync_receipts.jsonl) with its own cursor, so it
sees exactly the rows written since it last ran -- never reprocesses a
receipt, never misses one. For each newly observed receipt it resolves the
target_key(s) to a human company name (hunter_gap_manifest's own manifest,
the same resolver the priority queue uses) and:

  1. Appends one completion record per company to
     system/.cache/hunter_office_manager_log.jsonl -- render_intelligence_brief.py
     reads this for the "Hunter Office Manager" section.
  2. Optionally texts Todd, one message per company, via the SMTP-to-SMS
     carrier gateway configured in settings.json under
     daily_briefing.hunter_office_manager.sms. This is a temporary channel
     (Todd's words: "for a little while") while the multi-engine Hunter
     automation proves itself -- gated by settings so it's a one-line
     config change to dial back to a digest or turn off, not a code change.

Like every other write in this pipeline, nothing is recorded or sent without
--confirm; without it this prints what it would do.
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import hunter_gap_manifest as hgm  # noqa: E402
import hunter_drive_inbox_sync as dis  # noqa: E402

STATE_PATH = core.CACHE_DIR / "hunter_office_manager_state.json"
LOG_PATH = core.CACHE_DIR / "hunter_office_manager_log.jsonl"


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"processed_receipts": 0}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_new_receipts(already_processed: int) -> list[dict]:
    if not dis.RECEIPTS_PATH.exists():
        return []
    lines = dis.RECEIPTS_PATH.read_text(encoding="utf-8").splitlines()
    fresh = []
    for raw in lines[already_processed:]:
        try:
            fresh.append(json.loads(raw))
        except ValueError:
            continue
    return fresh


def _display_names(target_keys: list[str]) -> dict[str, str]:
    """target_key -> human company name, via the same manifest the priority
    queue resolves names from. Falls back to a slug-derived title when a key
    isn't (or is no longer) in the manifest -- never drops a completion just
    because the name lookup came up empty."""
    names: dict[str, str] = {}
    try:
        manifest = hgm.build_manifest(universe="all", target_keys=target_keys)
        for target in manifest.get("targets", []):
            if target.get("target_key"):
                names[target["target_key"]] = target.get("display_name") or target["target_key"]
    except Exception:
        pass
    for key in target_keys:
        if key not in names:
            slug = key.split(":", 1)[-1]
            names[key] = slug.replace("-", " ").title()
    return names


def _sms_config() -> dict:
    settings = core.load_settings()
    return ((settings.get("daily_briefing") or {}).get("hunter_office_manager") or {}).get("sms") or {}


def _resolve_smtp() -> dict | None:
    host = os.environ.get("RB_SMTP_HOST", "").strip()
    port = int(os.environ.get("RB_SMTP_PORT", "587") or 587)
    user = os.environ.get("RB_SMTP_USER", "").strip()
    pw = os.environ.get("RB_SMTP_PASS", "").strip()
    if not all([host, user, pw]):
        return None
    return {"host": host, "port": port, "user": user, "password": pw}


def send_text(message: str) -> dict:
    """Send one text via the configured carrier email-to-SMS gateway. Every
    gateway address is itself a plain email recipient, so this reuses the
    exact same SMTP transport send_brief_email.py already has wired
    (RB_SMTP_HOST/PORT/USER/PASS) -- no new credential or service to set up."""
    cfg = _sms_config()
    if not cfg.get("enabled"):
        return {"sent": False, "status": "disabled"}
    gateway = (cfg.get("gateway_address") or "").strip()
    if not gateway:
        return {"sent": False, "status": "no_gateway_address"}
    smtp = _resolve_smtp()
    if not smtp:
        return {"sent": False, "status": "no_smtp",
                "detail": "Set RB_SMTP_HOST, RB_SMTP_USER, RB_SMTP_PASS env vars"}
    try:
        msg = MIMEText(message, "plain", "utf-8")
        msg["From"] = smtp["user"]
        msg["To"] = gateway
        # Carrier gateways render Subject inconsistently (some prepend it to
        # the body, some drop it) -- leave it unset so the text is just the
        # message, not "None " or a stray header line in front of it.
        with smtplib.SMTP(smtp["host"], smtp["port"], timeout=30) as srv:
            srv.starttls()
            srv.login(smtp["user"], smtp["password"])
            srv.sendmail(smtp["user"], [gateway], msg.as_bytes())
        return {"sent": True, "status": "smtp_sent", "gateway": gateway}
    except Exception as error:
        return {"sent": False, "status": "smtp_error", "detail": str(error)}


def _message_for(company: str) -> str:
    return (
        f"This is the RBB office manager. Hunter completed a research cycle "
        f"on {company} and successfully deposited the data in the scan "
        f"folder for tomorrow."
    )


def run(*, confirm: bool) -> dict:
    state = _load_state()
    already = int(state.get("processed_receipts") or 0)
    receipts = _read_new_receipts(already)
    all_target_keys = sorted({key for r in receipts for key in (r.get("targets") or [])})
    names = _display_names(all_target_keys)

    sms_cfg = _sms_config()
    completions = []
    for receipt in receipts:
        for key in receipt.get("targets") or []:
            company = names.get(key, key)
            record = {
                "observed_at": receipt.get("observed_at"),
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "target_key": key,
                "company": company,
                "file": receipt.get("file"),
                "destination": receipt.get("destination"),
                "sms": {"attempted": False},
            }
            if confirm and sms_cfg.get("enabled"):
                record["sms"] = {"attempted": True, **send_text(_message_for(company))}
            completions.append(record)

    if confirm:
        if completions:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOG_PATH.open("a", encoding="utf-8") as fh:
                for record in completions:
                    fh.write(json.dumps(record, sort_keys=True) + "\n")
        _save_state({"processed_receipts": already + len(receipts)})

    return {
        "dry_run": not confirm,
        "new_receipts": len(receipts),
        "completions": completions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="Write the log and send any configured texts; without it, preview only")
    args = parser.parse_args()
    print(json.dumps(run(confirm=args.confirm), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
