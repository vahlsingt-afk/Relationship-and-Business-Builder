#!/usr/bin/env python3
"""End-to-end Hunter cycle preparation and returned-packet intake.

Preparation creates a portable Chat Deep Research job plus an immutable before
snapshot. Finalization validates and compares the returned packet, then invokes
the governed dispatcher in dry-run mode unless --confirm is explicit.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import hunter  # noqa: E402
import hunter_change_dispatch  # noqa: E402
import hunter_snapshot  # noqa: E402


def prepare(playbook: str, **kwargs) -> dict:
    directive = hunter.prepare_cycle(playbook, **kwargs)
    snapshot = hunter_snapshot.from_directive(directive)
    return {
        "schema": "rb.hunter_cycle_job.v1",
        "execution_boundary": "Submit directive to ChatGPT Deep Research; return JSON packet for finalization.",
        "directive": directive,
        "before_snapshot": snapshot,
    }


def finalize(job: dict, packet: dict, *, confirm: bool = False) -> dict:
    validation = hunter.validate_packet(packet)
    comparison = hunter_snapshot.compare(job.get("before_snapshot") or {}, packet)
    ok = validation["valid"] and comparison["valid"]
    dispatch = None
    if ok:
        dispatch = hunter_change_dispatch.dispatch(packet, dry_run=not confirm)
    return {
        "schema": "rb.hunter_cycle_receipt.v1",
        "packet_id": packet.get("packet_id"),
        "ok": ok and bool(dispatch and dispatch.get("ok")),
        "confirmed": confirm,
        "validation": validation,
        "change_comparison": comparison,
        "dispatch": dispatch,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare or finalize a Hunter research cycle")
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("playbook")
    prep.add_argument("--depth")
    prep.add_argument("--universe", choices=["all", "brands", "competitors", "franchisees"], default="all")
    prep.add_argument("--target", action="append", dest="target_keys")
    prep.add_argument("--limit", type=int)
    prep.add_argument("--output", required=True)
    fin = sub.add_parser("finalize")
    fin.add_argument("job")
    fin.add_argument("packet")
    fin.add_argument("--confirm", action="store_true")
    fin.add_argument("--output")
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.playbook, depth=args.depth, universe=args.universe,
                         target_keys=args.target_keys, limit=args.limit)
        Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        job = json.loads(Path(args.job).read_text(encoding="utf-8"))
        packet = json.loads(Path(args.packet).read_text(encoding="utf-8"))
        result = finalize(job, packet, confirm=args.confirm)
        if args.output:
            Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
