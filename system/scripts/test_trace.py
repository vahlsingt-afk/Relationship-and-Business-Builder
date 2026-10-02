#!/usr/bin/env python3
"""
test_trace.py — RB 8.0-style trace/developer logging for RB 9.0.

Phase 14 of OPERATIONALIZATION.md and CLAUDE_HANDOFF section "Restore Trace
Mode / Developer Logs". Captures a structured, operator-visible record of:

    - timestamped user prompts (verbatim)
    - assistant/RB responses (verbatim)
    - the tool/operation the interface intended to call
    - the actual endpoint hit
    - request parameters (with secrets redacted)
    - response status + a compact response summary
    - write-confirmation text (if any)
    - before/after validation call when a mutation occurs
    - operator-visible defects or feedback

This does NOT capture hidden chain-of-thought. It captures the operational
trace: what the interface asked, what the API did, what RB said, and what
failed.

Storage (matches the two test traces already on disk):

    system/test_traces/YYYY-MM-DD-<slug>.md     # human-readable, paired
    system/test_traces/YYYY-MM-DD-<slug>.json   # structured payload

Trace IDs follow the loop/defect naming convention: T-YYYY-MM-DD-NNN.

Usage (CLI):
    # Append a trace from a JSON file on disk
    python3 test_trace.py --append-from path/to/trace.json

    # Append a trace from stdin
    cat trace.json | python3 test_trace.py --append

    # List the most recent N traces (summary only)
    python3 test_trace.py --list-recent --limit 5

The HTTP API exposes the same two operations under
`POST /test_traces` and `GET /test_traces/recent`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = SYSTEM_DIR.parent
TRACES_DIR = SYSTEM_DIR / "test_traces"

MAX_BODY_BYTES = 256 * 1024  # 256KB cap to keep traces reviewable

# Keys whose values must be redacted out of any captured request/response/header
# payload before persistence. Match case-insensitively against header names and
# JSON keys.
SECRET_KEYS = {
    "x-api-key", "x_api_key", "api-key", "api_key", "apikey",
    "authorization", "auth", "bearer",
    "rb_api_key", "rb-api-key",
    "password", "passwd", "secret", "token", "access_token",
    "cookie", "set-cookie", "x-auth-token",
}

# Patterns that look like long opaque tokens. Matches inline strings too —
# useful for any header value that wasn't keyed by name.
TOKEN_RX = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9_\-\.=/+]{16,})")
LONG_HEX_RX = re.compile(r"\b[A-Fa-f0-9]{32,}\b")
# Catch RB_API_KEY=... or x-api-key: ... patterns left in free text
KV_SECRET_RX = re.compile(
    r"(?i)(rb[_-]?api[_-]?key|x-api-key|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+"
)


# ----------------------------------------------------------------------------
# Redaction
# ----------------------------------------------------------------------------

def _is_secret_key(name: str) -> bool:
    return name.strip().lower().replace("_", "-") in {
        k.replace("_", "-") for k in SECRET_KEYS
    }


def redact(value):
    """Recursively redact secret-shaped values from a payload. Tries to keep
    everything else intact so the trace remains useful.

    Strings get their inline tokens replaced ("Bearer abc123..." -> "Bearer ***").
    Dicts get any secret-keyed values replaced wholesale ("***REDACTED***").
    Lists recurse element-wise. Other scalars pass through.
    """
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            if _is_secret_key(str(k)):
                out[k] = "***REDACTED***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        s = value
        s = TOKEN_RX.sub(lambda m: m.group(1) + "***", s)
        s = KV_SECRET_RX.sub(lambda m: m.group(1) + "=***REDACTED***", s)
        s = LONG_HEX_RX.sub("***", s)
        return s
    return value


# ----------------------------------------------------------------------------
# Trace IDs and paths
# ----------------------------------------------------------------------------

@dataclass
class TraceRef:
    trace_id: str
    md_path: Path
    json_path: Path


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text or "").strip("-").lower()
    return s[:80] or "trace"


def _existing_ids_for_date(d: date) -> set[str]:
    out: set[str] = set()
    if not TRACES_DIR.exists():
        return out
    for p in TRACES_DIR.glob(f"{d.isoformat()}-*.json"):
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        tid = data.get("trace_id")
        if tid:
            out.add(tid)
    return out


def _next_trace_id(d: date) -> str:
    existing = _existing_ids_for_date(d)
    n = 1
    while True:
        candidate = f"T-{d.isoformat()}-{n:03d}"
        if candidate not in existing:
            return candidate
        n += 1


def _build_paths(d: date, slug: str) -> tuple[Path, Path]:
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    base = TRACES_DIR / f"{d.isoformat()}-{slug}"
    return base.with_suffix(".md"), base.with_suffix(".json")


# ----------------------------------------------------------------------------
# Append
# ----------------------------------------------------------------------------

def append_trace(payload: dict) -> dict:
    """Persist a single test trace. Returns metadata about the written files.

    Required fields:
        title           — short human title; used to build the filename slug.

    Optional fields:
        captured_at     — ISO timestamp; defaults to now.
        trace_type      — e.g. "interface_test", "regression", "field_defect".
        source          — e.g. "ChatGPT Custom GPT", "Codex CLI".
        operator        — e.g. "Todd".
        steps           — list of step dicts (see schema below).
        summary         — overall narrative.
        defects         — [{id, title, severity, recommendation}, ...].
        observed_issue  — short top-level issue text (back-compat with the
                          two traces already on disk).
        raw_text        — optional verbatim transcript paste, stored as-is in
                          the .md body when present.

    Step shape (all fields optional except prompt OR action):
        {
          "step": 1,
          "user_prompt": "verbatim user text",
          "assistant_response": "verbatim assistant text",
          "intended_operation": "getDailyBrief",
          "endpoint": "GET /daily_brief",
          "request_params": {...},
          "response_status": 200,
          "response_summary": "short summary, not full payload",
          "mutation_confirmation": "Todd confirmed close of L-...",
          "post_validation": "...",
          "timestamp": "2026-05-18T17:30:00Z",
          "user_feedback": "...",
          "observed_issue": "...",
        }
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    title = (payload.get("title") or "").strip()
    if not title:
        raise ValueError("payload.title is required")

    # Redact before anything else touches the structure.
    safe = redact(payload)

    # Cap raw_text size so a runaway paste doesn't blow up the repo.
    raw = safe.get("raw_text") or ""
    if isinstance(raw, str) and len(raw.encode("utf-8")) > MAX_BODY_BYTES:
        truncated = raw.encode("utf-8")[:MAX_BODY_BYTES].decode("utf-8", errors="replace")
        safe["raw_text"] = truncated + "\n\n[…truncated — exceeded 256KB cap]"
        safe.setdefault("warnings", []).append("raw_text truncated at 256KB")

    captured_at = safe.get("captured_at") or datetime.utcnow().isoformat(timespec="seconds") + "Z"
    try:
        d = date.fromisoformat(captured_at[:10])
    except ValueError:
        d = date.today()
    trace_id = safe.get("trace_id") or _next_trace_id(d)

    slug = _slugify(title)
    md_path, json_path = _build_paths(d, slug)
    # If a file with this slug already exists for this date, append a counter
    n = 2
    while md_path.exists() or json_path.exists():
        md_path, json_path = _build_paths(d, f"{slug}-{n}")
        n += 1

    record = dict(safe)
    record["trace_id"] = trace_id
    record["captured_at"] = captured_at

    json_path.write_text(json.dumps(record, indent=2, default=str) + "\n")
    md_path.write_text(render_markdown(record))

    return {
        "trace_id": trace_id,
        "captured_at": captured_at,
        "md_file": str(md_path.relative_to(PROJECT_DIR)),
        "json_file": str(json_path.relative_to(PROJECT_DIR)),
        "redacted": True,
        "warnings": record.get("warnings") or [],
    }


# ----------------------------------------------------------------------------
# Markdown render
# ----------------------------------------------------------------------------

def render_markdown(record: dict) -> str:
    title = record.get("title") or "Test trace"
    captured_at = record.get("captured_at") or ""
    trace_id = record.get("trace_id") or ""
    out: list[str] = []
    out.append(f"# RB Test Trace — {title}\n")
    if trace_id:
        out.append(f"**Trace ID:** {trace_id}  ")
    if captured_at:
        out.append(f"**Captured at:** {captured_at}  ")
    if record.get("source"):
        out.append(f"**Source:** {record['source']}  ")
    if record.get("operator"):
        out.append(f"**Operator:** {record['operator']}  ")
    if record.get("trace_type"):
        out.append(f"**Trace type:** {record['trace_type']}  ")
    if record.get("session_id"):
        out.append(f"**Session:** {record['session_id']}  ")
    out.append("")

    if record.get("summary"):
        out.append("## Summary\n")
        out.append(str(record["summary"]).strip() + "\n")

    if record.get("observed_issue"):
        out.append("## Observed issue\n")
        out.append(str(record["observed_issue"]).strip() + "\n")

    steps = record.get("steps") or []
    if steps:
        out.append("## Steps\n")
        for s in steps:
            num = s.get("step") or steps.index(s) + 1
            out.append(f"### {num}. {s.get('label') or s.get('intended_operation') or 'Step'}\n")
            if s.get("timestamp"):
                out.append(f"**Timestamp:** {s['timestamp']}\n")
            if s.get("user_prompt"):
                out.append("**User prompt**\n")
                out.append("```text")
                out.append(str(s["user_prompt"]).rstrip())
                out.append("```\n")
            if s.get("intended_operation") or s.get("endpoint"):
                out.append("**Tool / API call**\n")
                if s.get("intended_operation"):
                    out.append(f"- intended_operation: `{s['intended_operation']}`")
                if s.get("endpoint"):
                    out.append(f"- endpoint: `{s['endpoint']}`")
                if s.get("request_params") is not None:
                    params_str = json.dumps(s["request_params"], indent=2, default=str)
                    out.append("- request_params:")
                    out.append("```json")
                    out.append(params_str)
                    out.append("```")
                if s.get("response_status") is not None:
                    out.append(f"- response_status: `{s['response_status']}`")
                if s.get("response_summary"):
                    out.append("- response_summary:")
                    out.append("```text")
                    out.append(str(s["response_summary"]).rstrip())
                    out.append("```")
                out.append("")
            if s.get("assistant_response"):
                out.append("**Assistant response**\n")
                out.append("```text")
                out.append(str(s["assistant_response"]).rstrip())
                out.append("```\n")
            if s.get("mutation_confirmation"):
                out.append(f"**Mutation confirmation:** {s['mutation_confirmation']}\n")
            if s.get("post_validation"):
                out.append(f"**Post-mutation validation:** {s['post_validation']}\n")
            if s.get("user_feedback"):
                out.append(f"**User feedback:** {s['user_feedback']}\n")
            if s.get("observed_issue"):
                out.append(f"**Observed issue:** {s['observed_issue']}\n")
            out.append("")

    defects = record.get("defects") or []
    if defects:
        out.append("## Defects\n")
        for d in defects:
            did = d.get("id") or ""
            title_d = d.get("title") or ""
            sev = d.get("severity") or ""
            head = " — ".join(p for p in [did, title_d] if p)
            out.append(f"### {head}\n")
            if sev:
                out.append(f"**Severity:** {sev}\n")
            if d.get("description"):
                out.append(str(d["description"]).strip() + "\n")
            if d.get("recommendation"):
                out.append(f"**Recommendation:** {d['recommendation']}\n")
            out.append("")

    if record.get("raw_text"):
        out.append("## Raw verbatim paste\n")
        out.append("This block is the operator-provided verbatim text. RB has not "
                   "interpreted or summarized it; it is preserved here for replay.\n")
        out.append("```text")
        out.append(str(record["raw_text"]).rstrip())
        out.append("```\n")

    if record.get("warnings"):
        out.append("## Warnings\n")
        for w in record["warnings"]:
            out.append(f"- {w}")
        out.append("")

    out.append("---")
    out.append(f"*Generated by `system/scripts/test_trace.py`. Secrets redacted before persistence.*")
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------
# Listing
# ----------------------------------------------------------------------------

def list_recent(limit: int = 10) -> list[dict]:
    """Return summaries of the most recent traces, newest first."""
    if not TRACES_DIR.exists():
        return []
    rows: list[dict] = []
    for p in TRACES_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        rows.append({
            "trace_id": data.get("trace_id"),
            "title": data.get("title"),
            "captured_at": data.get("captured_at"),
            "trace_type": data.get("trace_type"),
            "source": data.get("source"),
            "operator": data.get("operator"),
            "step_count": len(data.get("steps") or []),
            "defect_count": len(data.get("defects") or []),
            "md_file": str(p.with_suffix(".md").relative_to(PROJECT_DIR)),
            "json_file": str(p.relative_to(PROJECT_DIR)),
        })
    # Also catch markdown-only legacy traces (the two existing files have no .json sibling)
    for p in TRACES_DIR.glob("*.md"):
        if p.with_suffix(".json").exists():
            continue
        try:
            stat = p.stat()
            captured = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        except OSError:
            captured = None
        # Try to pull the date prefix off the filename
        name = p.stem
        trace_date = name[:10] if len(name) >= 10 else None
        rows.append({
            "trace_id": None,
            "title": name,
            "captured_at": trace_date or captured,
            "trace_type": "legacy_markdown",
            "source": None,
            "operator": None,
            "step_count": None,
            "defect_count": None,
            "md_file": str(p.relative_to(PROJECT_DIR)),
            "json_file": None,
        })
    rows.sort(key=lambda r: r.get("captured_at") or "", reverse=True)
    return rows[:limit]


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="RB test-trace recorder.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--append", action="store_true",
                   help="Read a JSON payload from stdin and append it as a trace.")
    g.add_argument("--append-from", metavar="PATH",
                   help="Read a JSON payload from PATH and append it as a trace.")
    g.add_argument("--list-recent", action="store_true",
                   help="List the most recent traces (summary only).")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true",
                   help="Emit JSON output (for --list-recent).")
    args = p.parse_args()

    if args.list_recent:
        rows = list_recent(limit=args.limit)
        if args.json:
            print(json.dumps(rows, indent=2, default=str))
        else:
            for r in rows:
                print(f"{r.get('captured_at') or '—':<22} "
                      f"{r.get('trace_id') or '(legacy)':<20} "
                      f"{r.get('title') or ''}")
        return 0

    if args.append_from:
        text = Path(args.append_from).read_text()
    else:
        text = sys.stdin.read()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"invalid JSON: {e}\n")
        return 2
    try:
        result = append_trace(payload)
    except ValueError as e:
        sys.stderr.write(f"trace rejected: {e}\n")
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
