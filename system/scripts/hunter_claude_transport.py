#!/usr/bin/env python3
"""Claude Code headless transport for the `claude_code_headless` Hunter engine.

Runs a leased Hunter job through a non-interactive `claude -p` call instead of
a human-driven ChatGPT Deep Research or Work session. It does not change the
research standard: the model is pointed at the same HUNTER.md methodology and
packet schema, runs in this repo with read access to the same RBB context, and
its output goes through the same `hunter_cycle.py sweep` validator as every
other engine. This script never writes canonical RBB state directly.

Verified 2026-10-06: a `claude -p` call with Read/Glob/Grep/WebSearch allowed
returns a well-formed, citable packet and draws from the account's Pro
session/weekly limits (confirmed against claude.ai Settings > Usage before and
after test calls), not a separate charge.

Usage:
  python3 hunter_claude_transport.py run JOB_ID [--dry-run]

JOB_ID must already be leased to claude_code_headless by
`hunter_orchestrator.py dispatch --confirm` -- this script reads the job path
from that lease event. It then:
  1. records `start` on the job,
  2. runs one `claude -p` call per subjob,
  3. assembles a single packet (or a bundle response for a two-subjob
     assignment), writes it to the Hunter packets inbox,
  4. runs `hunter_cycle.py sweep` (no --confirm: validates and finalizes the
     packet but does not apply canonical mutations -- same review-first step
     every engine's packet goes through),
  5. records `complete` with the validation outcome and engine telemetry.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import hunter_orchestrator as ho  # noqa: E402
import hunter_cycle  # noqa: E402 -- only for BUNDLE_SCHEMA; sweep() itself runs as a subprocess below

ROOT = SCRIPTS_DIR.parent.parent  # repo root: hunter_orchestrator.py's ROOT is system/
ALLOWED_TOOLS = "Read,Glob,Grep,WebSearch"
CLAUDE_TIMEOUT_S = 900


def _claude_bin() -> str:
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude"
    if fallback.exists():
        return str(fallback)
    raise SystemExit("claude CLI not found on PATH or in ~/.local/bin")


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    return text.strip()


def build_prompt(subjob: dict) -> str:
    target_key = subjob["target_key"]
    playbook = subjob.get("suggested_playbook") or subjob["job"]["plan"]["payload_schema"]
    payload_schema = subjob["job"]["plan"]["payload_schema"]
    known_gap_ids = subjob["job"]["packet_requirements"]["known_gap_ids"]
    discovery_domains = subjob["job"]["packet_requirements"]["discovery_domains"]
    return f"""You are Hunter, RBB's public-source research agent, running through Claude Code
as an execution engine rather than ChatGPT Deep Research. The research standard does not
change with the engine.

Before researching, read in full:
  - system/research/HUNTER.md (mission, public-source boundary, invariant research method,
    evidence discipline, JSON and provenance standards)
  - system/schemas/hunter_research_packet.schema.json (the required packet shape)
  - system/research/hunter_playbooks.json (the "{playbook}" playbook entry, for this cycle's
    required_modules and exit_criteria)

Target: {target_key}
Playbook: {playbook}
Known gap IDs to resolve first: {json.dumps(known_gap_ids)}
Discovery domains to also search beyond the known gaps: {json.dumps(discovery_domains)}

Research this target using public web search only, following HUNTER.md's method exactly:
resolve the target, research known gaps first with a gap outcome for each (even if
unresolved), run a discovery pass, atomize evidence with one claim per finding and real
source citations, triangulate material claims, and run the identity/date/scope/conflict
audit before responding.

Reply with ONLY one inline JSON object: the complete Hunter research packet for this
target, matching hunter_research_packet.schema.json exactly, with "payload_schema" set to
"{payload_schema}". No markdown fences, no commentary, nothing before or after the JSON."""


def run_claude(prompt: str) -> dict:
    proc = subprocess.run(
        [_claude_bin(), "-p", prompt, "--output-format", "json", "--allowedTools", ALLOWED_TOOLS],
        cwd=str(ROOT), capture_output=True, text=True, timeout=CLAUDE_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[:500]}")
    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude reported an error: {envelope.get('result')}")
    packet = json.loads(_strip_fences(envelope["result"]))
    return {"packet": packet, "envelope": envelope}


def assemble_response(assignment: dict, subjob_results: list[dict]) -> dict:
    """One ordinary packet for a single-target job, or a bundle response for two."""
    packets = [r["packet"] for r in subjob_results]
    if len(packets) == 1:
        return packets[0]
    assignment_id = assignment.get("assignment_id") or "-".join(assignment["target_keys"])
    return {
        "schema": hunter_cycle.BUNDLE_SCHEMA,
        "bundle_id": assignment_id,
        "assignment_id": assignment_id,
        "target_keys": assignment["target_keys"],
        "packets": packets,
    }


def write_response(response: dict, *, label: str) -> Path:
    inbox = ho.ROOT / "inbox" / "hunter_packets"
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / f"claude-headless-{label}-{int(time.time())}.json"
    path.write_text(json.dumps(response, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run_sweep() -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "hunter_cycle.py"), "sweep"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "sweep_stdout": proc.stdout[-2000:], "sweep_stderr": proc.stderr[-2000:]}


def _find_receipt(sweep_result: dict, job_path: Path) -> dict | None:
    """sweep()'s `processed` entries carry the job_path they matched, which is a
    more reliable key than re-deriving target keys from the packet."""
    for entry in sweep_result.get("processed") or []:
        if entry.get("job_path") == str(job_path):
            return entry.get("receipt")
    return None


def cmd_run(args) -> dict:
    jid = args.job_id
    lease = ho.job_states().get(jid)
    if not lease or lease.get("state") != "leased" or lease.get("engine") != "claude_code_headless":
        raise SystemExit(f"job {jid} is not leased to claude_code_headless (state={lease and lease.get('state')})")
    job_path = Path(lease["job_path"])
    assignment = json.loads(job_path.read_text(encoding="utf-8"))
    if assignment.get("schema") != "rb.hunter_priority_assignment.v1":
        raise SystemExit(f"{job_path} is schema {assignment.get('schema')!r}; this transport only handles "
                          "rb.hunter_priority_assignment.v1 (hunter_cycle.py prepare-priority's output)")
    subjobs = assignment["subjobs"]
    started_at = ho._iso(ho._now())

    if args.dry_run:
        return {"dry_run": True, "job_id": jid, "subjobs": [s["target_key"] for s in subjobs],
                "prompt_preview": build_prompt(subjobs[0])[:600]}

    ho.cmd_start(argparse.Namespace(job_id=jid, note="claude_code_headless transport run"))
    results, errors = [], []
    for subjob in subjobs:
        try:
            results.append(run_claude(build_prompt(subjob)))
        except Exception as exc:  # noqa: BLE001 -- surfaced in the validation summary, never swallowed
            errors.append(f"{subjob['target_key']}: {exc}")

    telemetry = {
        "engine": "claude_code_headless", "started_at": started_at, "ended_at": ho._iso(ho._now()),
        "subjob_count": len(subjobs), "errors": errors,
        "total_cost_usd": sum(r["envelope"].get("total_cost_usd", 0) for r in results),
    }
    tele_path = ho.LEDGER_DIR / f"claude_transport_{jid}.telemetry.json"
    tele_path.parent.mkdir(parents=True, exist_ok=True)
    tele_path.write_text(json.dumps(telemetry, indent=2) + "\n", encoding="utf-8")

    if errors or not results:
        validation = {"accepted": False, "errors": errors or ["no research results produced"]}
        val_path = ho.LEDGER_DIR / f"claude_transport_{jid}.validation.json"
        val_path.write_text(json.dumps(validation) + "\n", encoding="utf-8")
        ho.cmd_complete(argparse.Namespace(job_id=jid, validation_json=str(val_path), telemetry_json=str(tele_path)))
        return {"ok": False, "job_id": jid, "errors": errors, "telemetry": telemetry}

    response = assemble_response(assignment, results)
    packet_path = write_response(response, label=jid)
    sweep_result = run_sweep()
    receipt = _find_receipt(sweep_result, job_path)
    accepted = bool(receipt and receipt.get("ok"))
    validation = {"accepted": accepted, "errors": [] if accepted else [json.dumps(receipt or sweep_result)[:1000]]}
    val_path = ho.LEDGER_DIR / f"claude_transport_{jid}.validation.json"
    val_path.write_text(json.dumps(validation) + "\n", encoding="utf-8")
    ho.cmd_complete(argparse.Namespace(job_id=jid, validation_json=str(val_path), telemetry_json=str(tele_path)))
    return {"ok": accepted, "job_id": jid, "packet_path": str(packet_path), "receipt": receipt, "telemetry": telemetry}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("job_id")
    r.add_argument("--dry-run", action="store_true", help="Show the prompt that would be sent; makes no claude call and no state change")
    r.set_defaults(fn=cmd_run)
    args = p.parse_args(argv)
    print(json.dumps(args.fn(args), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
