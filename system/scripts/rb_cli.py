#!/usr/bin/env python3
"""
rb_cli.py — direct shell access to RB's live API, built for Codex.

RB-2026-08-28. Codex has trusted shell access to this exact repo but
structurally cannot call Custom GPT Actions -- a confirmed OpenAI platform
limitation (Actions are scoped to the specific Custom GPT they're
configured on; Codex only ever sees public Apps/plugins/Skills, never
another product's Actions), independent of anything else this session
found. Since the Custom GPT is now retired anyway (2026-08-28 decision;
see project_open_decisions_ledger memory item 13), the prior guidance to
route trusted work through it instead of Codex no longer has a live
target.

This is the real bridge: the exact same operation set the Trusted Chat
Client (rbb_chat.py) uses, routed the same way (rbb_chat_tools.py's
already-verified operation table -- no duplicate/divergent routing logic),
invoked directly over HTTP with the real API key. Nothing here talks to
OpenAI at all; it's a thin, deterministic HTTP client Codex's shell tool
runs directly.

Usage:
    python3 rb_cli.py list                              # every real, callable operation + description
    python3 rb_cli.py describe <operationId>             # full parameter schema for one operation
    python3 rb_cli.py call <operationId> '{"key": "value"}'   # call it for real, prints the raw response + HTTP status

Discipline (same rule that governs every other RB surface): a mutation
happened only if this prints a real 2xx response body from a `call`
you actually ran this turn. Never say "logged"/"updated"/"done" from
memory or inference -- if you didn't run `call` and see the response,
it didn't happen.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rbb_chat_tools as tools  # noqa: E402
import audit_log  # noqa: E402

RB_API_BASE = "http://127.0.0.1:8765"
SECRETS_PATH = Path("/Users/toddvahlsing/Library/Application Support/Relationship Builder/secrets.env")
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _audit_call(operation_id: str, method: str, arguments: dict, status_code: int) -> None:
    """Mirrors rbb_chat.py's _audit_tool_call so rb_cli and rbb-chat usage
    show up in the same audit trail, distinguished only by `source` --
    rb_cli previously logged nothing at all, making Codex-vs-rbb-chat usage
    unverifiable without grepping raw Codex session transcripts."""
    is_write = method in WRITE_METHODS
    ok = 200 <= status_code < 300
    event_type = "mutation_executed" if (is_write and ok) else (
        "mutation_rejected" if is_write else "source_accessed"
    )
    audit_log.append_event(
        event_type=event_type,
        item_summary=f"rb_cli call: {operation_id}",
        reason="codex_cli_direct_call" if is_write else f"Source read: {operation_id}",
        outcome="executed" if ok else "failed",
        data_class="memory" if is_write else "raw_source",
        source="rb_cli",
        extra={
            "operation_id": operation_id,
            "http_status": status_code,
            "arguments_keys": list(arguments.keys()),
            "caller": "codex",
        },
    )


def _load_api_key() -> str:
    """Reads RB_API_KEY from the same secrets.env the production services
    use -- Codex never needs to see or type the actual key value."""
    if not SECRETS_PATH.exists():
        sys.exit(f"ERROR: secrets file not found at {SECRETS_PATH}")
    for line in SECRETS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if line.startswith("RB_API_KEY="):
            value = line.split("=", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    sys.exit("ERROR: RB_API_KEY not found in secrets.env")


def cmd_list(_args) -> int:
    all_tools, _ops = tools.build_tools_and_operations()
    for t in sorted(all_tools, key=lambda t: t["name"]):
        one_line = " ".join((t.get("description") or "").split())
        desc = one_line.split(". ")[0].strip()
        print(f"{t['name']:<32} {desc}")
    return 0


def cmd_describe(args) -> int:
    all_tools, ops = tools.build_tools_and_operations()
    tool = next((t for t in all_tools if t["name"] == args.operation_id), None)
    if tool is None:
        print(f"ERROR: unknown operation '{args.operation_id}'. Run 'list' to see valid names.", file=sys.stderr)
        return 1
    print(json.dumps({"tool": tool, "routing": ops.get(args.operation_id)}, indent=2))
    return 0


def cmd_call(args) -> int:
    _all_tools, ops = tools.build_tools_and_operations()
    op = ops.get(args.operation_id)
    if op is None:
        print(f"ERROR: unknown operation '{args.operation_id}'. Run 'list' to see valid names.", file=sys.stderr)
        return 1

    try:
        arguments = json.loads(args.arguments) if args.arguments else {}
    except json.JSONDecodeError as exc:
        print(f"ERROR: arguments must be valid JSON: {exc}", file=sys.stderr)
        return 1

    path = op["path"]
    for pname in op["path_params"]:
        path = path.replace("{" + pname + "}", str(arguments.get(pname, "")))

    query = {k: arguments[k] for k in op["query_params"] if k in arguments and arguments[k] is not None}
    body = None
    if op["has_body"]:
        body = {k: arguments[k] for k in op["body_param_names"] if k in arguments}

    url = RB_API_BASE + path
    if query:
        url += "?" + urllib.parse.urlencode(query)

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=op["method"],
        headers={"Content-Type": "application/json", "x-api-key": _load_api_key()},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            print(f"HTTP {resp.status}")
            print(resp.read().decode("utf-8"))
            _audit_call(args.operation_id, op["method"], arguments, resp.status)
            return 0
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}")
        print(exc.read().decode("utf-8"))
        _audit_call(args.operation_id, op["method"], arguments, exc.code)
        return 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List every real, callable operation")

    p_describe = sub.add_parser("describe", help="Full parameter schema for one operation")
    p_describe.add_argument("operation_id")

    p_call = sub.add_parser("call", help="Call a real operation")
    p_call.add_argument("operation_id")
    p_call.add_argument("arguments", nargs="?", default="{}", help="JSON object of arguments")

    args = p.parse_args()
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "describe":
        return cmd_describe(args)
    if args.cmd == "call":
        return cmd_call(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
