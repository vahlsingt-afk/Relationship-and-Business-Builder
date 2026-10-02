#!/usr/bin/env python3
"""
session_writer.py — write a session memory file from structured input.

Two ways to call it:

1. From a CLI (operator hand-edits a YAML/JSON of the summary):
       cat summary.json | python3 session_writer.py
       python3 session_writer.py --in summary.json

2. As a library — `mutations.py session-end` and the MCP/HTTP server
   call `write_session()` directly.

Expected input shape (JSON or YAML; either works):
    {
        "session_id": "2026-05-16-1900",     # optional; default = now in CT
        "date": "2026-05-16",                # optional; default = today
        "end_time": "2026-05-16T19:00:00-05:00",  # optional; default = now
        "duration_estimate": "~3 hours",
        "focus_areas": ["..."],
        "threads_touched": ["T-..."],
        "contacts_touched": ["contact-id"],
        "mutations": {"contact_add": 1, ...},
        "status": "complete",
        "next_session_should": "...",
        "body": {                            # narrative sections
            "what_we_worked_on": ["bullet", "bullet"],
            "what_was_decided": ["..."],
            "in_flight_at_session_end": ["..."],
            "notable_findings": ["..."]
        }
    }

The body keys are turned into ## headings in the file.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core

SESSIONS_DIR = core.SYSTEM_DIR / "_sessions"

# America/Chicago is the operating timezone. Hardcoded for now; could be moved
# into settings.json later.
CT_OFFSET = timedelta(hours=-5)


def _now_session_id() -> str:
    now = datetime.now(timezone(CT_OFFSET))
    return now.strftime("%Y-%m-%d-%H%M")


def _now_iso() -> str:
    return datetime.now(timezone(CT_OFFSET)).isoformat(timespec="seconds")


def write_session(summary: dict) -> Path:
    """Write the summary file. Returns the path."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    sid = summary.get("session_id") or _now_session_id()
    date = summary.get("date") or sid[:10]
    end_time = summary.get("end_time") or _now_iso()
    body = summary.get("body") or {}

    fm_keys = [
        ("session_id", sid),
        ("date", date),
        ("end_time", end_time),
        ("duration_estimate", summary.get("duration_estimate")),
        ("focus_areas", summary.get("focus_areas") or []),
        ("threads_touched", summary.get("threads_touched") or []),
        ("contacts_touched", summary.get("contacts_touched") or []),
        ("mutations", summary.get("mutations") or {}),
        ("status", summary.get("status") or "complete"),
        ("next_session_should", summary.get("next_session_should") or ""),
    ]

    out = ["---"]
    try:
        import yaml  # type: ignore
        fm_dict = {k: v for k, v in fm_keys if v not in (None, "", [], {})}
        # session_id, date, status always emitted even if default
        for required in ("session_id", "date", "status"):
            fm_dict.setdefault(required, dict(fm_keys).get(required))
        out.append(yaml.safe_dump(fm_dict, sort_keys=False, allow_unicode=True).rstrip())
    except ImportError:
        for k, v in fm_keys:
            if v in (None, "", [], {}) and k not in ("session_id", "date", "status"):
                continue
            if isinstance(v, list):
                out.append(f"{k}:")
                for item in v:
                    out.append(f"  - {item}")
            elif isinstance(v, dict):
                out.append(f"{k}:")
                for kk, vv in v.items():
                    out.append(f"  {kk}: {vv}")
            else:
                out.append(f"{k}: {v}")
    out.append("---")
    out.append("")
    out.append(f"# Session — {date}")
    out.append("")

    # Body sections in canonical order
    body_order = [
        ("what_we_worked_on", "What we worked on"),
        ("what_was_decided", "What was decided"),
        ("in_flight_at_session_end", "In flight at session end"),
        ("notable_findings", "Notable findings"),
        ("open_questions", "Open questions"),
        ("followups", "Followups"),
    ]
    for key, heading in body_order:
        items = body.get(key)
        if not items:
            continue
        out.append(f"## {heading}\n")
        if isinstance(items, str):
            out.append(items)
        else:
            for item in items:
                out.append(f"- {item}")
        out.append("")

    target = SESSIONS_DIR / f"{sid}.md"
    target.write_text("\n".join(out).rstrip() + "\n")
    return target


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="infile",
                   help="Path to JSON or YAML summary (default: stdin).")
    p.add_argument("--reindex", action="store_true",
                   help="Run session_index.py after writing.")
    args = p.parse_args()
    text = Path(args.infile).read_text() if args.infile else sys.stdin.read()
    text = text.strip()
    if text.startswith("{") or text.startswith("["):
        summary = json.loads(text)
    else:
        summary = core._yaml_load(text)
    target = write_session(summary)
    print(f"Wrote {target.relative_to(core.PROJECT_DIR)}.")
    if args.reindex:
        import subprocess
        subprocess.run([sys.executable, str(Path(__file__).parent / "session_index.py")])
    return 0


if __name__ == "__main__":
    sys.exit(main())
