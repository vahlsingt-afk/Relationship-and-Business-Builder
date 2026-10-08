#!/usr/bin/env python3
"""
render_reachability_check.py — RB defect 2026-10-08, self-healing check #1.

Confirmed live three separate times in one session: a function computes
real, correct data, but no code path reachable from the actual live
rendering entry point ever calls it -- render_daily_brief.py's render()
replaced its original verbose, every-section markdown assembly with a
curated `compact_parts` path and an early `return markdown`, leaving ~290
lines of now-genuinely-unreachable code sitting below it. Several later
sessions edited THAT dead code, believing they were landing a fix
("computed daily, never rendered -- fixed here"), because nothing checked
whether the function they were calling was actually reachable.

This is a structural (AST-based) reachability check, not a content check:
for a given module and entry function, find every top-level function
matching a name pattern (default `_render_*`) that is not reachable --
directly or transitively, through this module's own call graph -- from
the statements in the entry function that execute BEFORE its first
unconditional top-level `return` (an early-exit special case genuinely
distinct from a `return` nested inside an `if`/`for`/`try`, which is
ordinary conditional control flow, not a reachability break).

No auto-repair, on purpose -- same "detection only" discipline every
other self_audit_sweep.py check already established. A function flagged
here may be unreachable for a real reason (deliberately retired, or never
finished); the point is only to make that fact impossible to miss, the
same way mutation_reconciliation.py does for API operations with zero
confirmed mutations.

CLI:
    python3 render_reachability_check.py            # text report
    python3 render_reachability_check.py --json      # machine-readable
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = SCRIPTS_DIR.parent
ALLOWLIST_PATH = SYSTEM_DIR / "render_reachability_allowlist.json"

# Modules this check understands today. Each entry is one (file, entry
# function, candidate-name-prefix) triple -- add another tuple here to
# extend coverage to a different renderer/orchestrator, not a rewrite.
DEFAULT_TARGETS: list[dict] = [
    {
        "file": SCRIPTS_DIR / "render_daily_brief.py",
        "entry": "render",
        "prefix": "_render_",
    },
]


def _top_level_functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _first_unconditional_return_index(body: list[ast.stmt]) -> int | None:
    """Index of the first `return` statement that is a direct child of
    `body` (the function's own top-level statement list) -- NOT one nested
    inside an `if`/`for`/`while`/`try`, which is ordinary conditional
    control flow and does not make later top-level statements dead."""
    for i, stmt in enumerate(body):
        if isinstance(stmt, ast.Return):
            return i
    return None


def _bare_name_calls(nodes: list[ast.AST]) -> set[str]:
    """Every `some_name(...)` call reachable by walking `nodes` -- deliberately
    only ast.Name callees (how every _render_* call site in this codebase
    actually looks: plain function calls, never module/attribute-qualified),
    not ast.Attribute (a.b()) or anything dynamic."""
    names: set[str] = set()
    for node in nodes:
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                names.add(child.func.id)
    return names


def _load_allowlist() -> dict:
    try:
        data = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def check_file(file_path: Path, entry_name: str, prefix: str, *, allowlist: dict | None = None) -> dict:
    try:
        source = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "file": str(file_path), "error": f"could not read: {exc}"}
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as exc:
        return {"ok": False, "file": str(file_path), "error": f"could not parse: {exc}"}

    functions = _top_level_functions(tree)
    entry = functions.get(entry_name)
    if entry is None:
        return {"ok": False, "file": str(file_path), "error": f"entry function {entry_name!r} not found"}

    return_idx = _first_unconditional_return_index(entry.body)
    # The early-return statement itself is INCLUDED in the live slice (not
    # just everything strictly before it) -- its own return expression
    # (e.g. `return _render_a(sections)`) still executes and may itself
    # contain the only call to a candidate function, which must count as
    # reachable. Only the statements AFTER it are genuinely dead.
    live_body = entry.body if return_idx is None else entry.body[:return_idx + 1]

    # Module-internal call graph: function name -> set of (other top-level
    # module function) names it calls anywhere in its own body. Used to
    # follow multi-hop reachability (a live-called function that itself
    # calls a candidate), not just render()'s own direct calls.
    call_graph = {
        name: _bare_name_calls([node]) & functions.keys()
        for name, node in functions.items()
    }

    reachable: set[str] = set()
    frontier = _bare_name_calls(live_body) & functions.keys()
    while frontier:
        reachable |= frontier
        next_frontier: set[str] = set()
        for name in frontier:
            next_frontier |= call_graph.get(name, set())
        frontier = next_frontier - reachable

    candidates = {name for name in functions if name.startswith(prefix)}
    all_unreachable = sorted(candidates - reachable - {entry_name})
    allowlist = allowlist or {}
    acknowledged = sorted(n for n in all_unreachable if n in allowlist)
    unacknowledged = sorted(n for n in all_unreachable if n not in allowlist)

    return {
        "ok": True,
        "file": str(file_path),
        "entry": entry_name,
        "has_early_return": return_idx is not None,
        "total_candidates": len(candidates),
        "reachable_count": len(candidates & reachable),
        "unreachable": all_unreachable,
        "acknowledged_unreachable": acknowledged,
        "unacknowledged_unreachable": unacknowledged,
    }


def run_all_checks(targets: list[dict] | None = None) -> dict:
    allowlist_by_key = _load_allowlist()
    results = []
    findings: list[str] = []
    for t in (targets or DEFAULT_TARGETS):
        key = f"{t['file'].name}::{t['entry']}"
        r = check_file(t["file"], t["entry"], t["prefix"], allowlist=allowlist_by_key.get(key, {}))
        results.append(r)
        if not r.get("ok"):
            findings.append(f"render_reachability_check: could not analyze {Path(r['file']).name}: {r.get('error')}")
            continue
        names = r["unacknowledged_unreachable"]
        if names:
            findings.append(
                f"{len(names)} function(s) in {Path(r['file']).name} are never called from {r['entry']}()'s "
                f"live (pre-return) code path and are not in {ALLOWLIST_PATH.name} as an acknowledged exclusion: "
                + ", ".join(names[:8]) + ("..." if len(names) > 8 else "")
            )
    return {"results": results, "findings": findings, "clean": not findings}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    report = run_all_checks()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        if report["clean"]:
            print("render_reachability_check: CLEAN")
        else:
            print("render_reachability_check:")
            for f in report["findings"]:
                print(f"  - {f}")
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
