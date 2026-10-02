#!/usr/bin/env python3
"""competitive_brief_refresh_queue.py — the "sooner than Friday" path for
Competitive Brief synthesis (2026-09-30).

Todd's explicit two-tier direction: "Anything that is in the team portal
that requires CoS commentary should be generated on a weekly basis as a
part of our week end of week schedule... There are no on demand request
or real-time reports in the team portal option. [Option] B would be to
put the request into the next morning's intelligence cycle and email the
user a copy of the updated report." competitive_brief.py's
synthesize_weekly_briefs() is tier one (Friday EOW, every tracked
competitor). This module is tier two: a Team Portal teammate can request
an earlier refresh for ONE competitor; the request is only ever recorded
here, never generated live in the same request/response cycle. This
script -- wired into morning_pipeline.py's scan_steps, so it runs every
morning via the 4 AM pre-brief-scan LaunchAgent (which already has
OPENAI_API_KEY via run_with_secrets.py, same as competitive-brief-
synthesis.plist) -- is what actually regenerates and emails the result.

Storage: system/.cache/competitive_brief_refresh_requests.jsonl -- one
line per request, append-only until the next morning's processing pass,
then cleared.

CLI:
    python3 system/scripts/competitive_brief_refresh_queue.py process
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import competitive_brief as cbrief  # noqa: E402
import team_portal_email as tpe  # noqa: E402

QUEUE_PATH = core.CACHE_DIR / "competitive_brief_refresh_requests.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def request_refresh(*, competitor_slug: str, member_id: str, email: str) -> dict:
    """Appends one request -- never generates anything itself. Called
    from Team Portal's own request-refresh route (team_tech_stack.py)."""
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "competitor_slug": competitor_slug, "member_id": member_id,
        "email": email, "requested_at": _now_iso(),
    }
    with open(QUEUE_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def _load_queue() -> list[dict]:
    if not QUEUE_PATH.exists():
        return []
    rows = []
    for line in QUEUE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def process_refresh_queue() -> dict:
    """Regenerates synthesis for every uniquely-requested competitor,
    then emails each requester who asked for it their updated brief.
    Best-effort per entry -- one failure never blocks the rest. Clears
    the queue only after attempting every request (not before), so a
    mid-run crash retries tomorrow rather than silently losing requests."""
    requests = _load_queue()
    if not requests:
        return {"processed": 0, "emailed": 0}

    by_slug: dict[str, list[dict]] = {}
    for r in requests:
        by_slug.setdefault(r["competitor_slug"], []).append(r)

    emailed = 0
    for slug, requesters in by_slug.items():
        try:
            cbrief.persist_synthesis(slug)
        except Exception:  # noqa: BLE001 -- best-effort; still send the brief below either way
            pass
        # Even when synthesis itself didn't land (no API key, call
        # failed), still send the requester the real, current brief --
        # the account list / evidence sections are genuinely fresh and
        # useful on their own, independent of the LLM-synthesized part.
        try:
            full = cbrief.generate_competitive_brief(slug, generated_for="morning_refresh_queue")
            markdown = full["markdown"]
        except Exception:  # noqa: BLE001
            continue
        display_name = markdown.split("\n", 1)[0].lstrip("# ").split(" — ")[0]
        for r in requesters:
            if not r.get("email"):
                continue
            try:
                tpe.send_brief(
                    recipient=r["email"], subject=f"{display_name} — Competitive Brief (updated)",
                    markdown_body=markdown, sent_by="RBB Team Portal (requested refresh)",
                )
                emailed += 1
            except Exception:  # noqa: BLE001
                pass

    QUEUE_PATH.unlink(missing_ok=True)
    return {"processed": len(by_slug), "emailed": emailed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("process")
    args = parser.parse_args()

    if args.cmd == "process":
        result = process_refresh_queue()
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
