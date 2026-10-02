#!/usr/bin/env python3
"""Generate the compact, read-only RBB cockpit context projection."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SYSTEM = ROOT / "system"
SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = SYSTEM / "cockpit" / "context.json"
INPUTS = {
    "registry": SYSTEM / "CANONICAL_REGISTRY.yaml",
    "manifest": SYSTEM / "MANIFEST.md",
    "weekly_plan": SYSTEM / "weekly_plan.json",
    "legacy_loops": SYSTEM / "loop_ledger.md",
    "executive_loops": SYSTEM / "eolms" / "loops.json",
    "active_threads": SYSTEM / "active_threads.yaml",
    "strategic_events": SYSTEM / "strategic_events.json",
    "identity_matches": SYSTEM / ".cache" / "identity_match_candidates.json",
    "ecosystem_intelligence": SYSTEM / "ecosystem_intelligence.json",
}

# RB-2026-08-23: was a second, independently-maintained copy of this set
# (RB-DEFECT-2026-08-20's note here used to say "keep in sync by hand," the
# same tradeoff accepted for MANDATORY_RESTAURANT_BRANDS elsewhere). Now a
# real import -- ecosystem_brief.py's MATERIAL_SIGNAL_CLASSES/_is_material_signal
# are the single source of truth; both callers use the same function.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from ecosystem_brief import _is_material_signal  # noqa: E402
import loop_reconciliation  # noqa: E402


def _cross_namespace_loop_conflicts() -> list[dict]:
    """RB-2026-08-23 (P1-4): surfaces loop_reconciliation.py's findings in
    the same reconciliation_queue that already carries the other three
    unresolved-authority classes (authority_gaps, stale_active_threads,
    pending_identity_matches). Non-fatal: a failure here must not break
    cockpit context generation over a detection-only, non-canonical check."""
    try:
        report = loop_reconciliation.build_report()
        return report["possible_duplicate_intent"]
    except Exception:  # noqa: BLE001
        return []


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _git_revision() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _legacy_loops(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| L-"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 6 or "closed" in cells[5].lower():
            continue
        rows.append({
            "id": cells[0], "opened": cells[1], "party": cells[2],
            "action": re.sub(r"\*+", "", cells[3]).strip(),
            "target_date": cells[4], "status": re.sub(r"\*+", "", cells[5]).strip(),
            "authority": "system/loop_ledger.md",
        })
    return rows


def _active_eolms(rows: list[dict]) -> list[dict]:
    closed = {"completed", "cancelled", "abandoned", "closed"}
    return [
        {k: row.get(k) for k in (
            "id", "title", "category", "status", "priority", "owner",
            "next_action", "waiting_on", "due_date", "related_people",
            "related_orgs", "updated_at")}
        | {"authority": "system/eolms/loops.json"}
        for row in rows if str(row.get("status", "")).lower() not in closed
    ]


def _pending_identity_matches(path: Path) -> list[dict]:
    """Unresolved contact-identity candidates from identity_match_review.py.

    Only proposed_pending_confirmation surfaces here — confirmed/rejected
    candidates are resolved and shouldn't be re-asked (see that script's
    docstring, tied to the "two J.B.s" false-positive incident).
    """
    if not path.exists():
        return []
    data = _load_json(path)
    return [
        {k: c.get(k) for k in (
            "id", "baseline_id", "baseline_name", "baseline_company",
            "sender_name", "sender_email", "account", "thread_subject",
            "last_message_at", "first_seen")} | {"match_type": "exact_name_no_email_on_file"}
        for c in data.get("candidates", {}).values()
        if c.get("status") == "proposed_pending_confirmation"
    ]


def _material_ecosystem_signals(path: Path, *, limit: int = 8) -> list[dict]:
    """Recent, material watchlist signals from the earnings/ecosystem-scan
    pipeline (ecosystem_brief.py, earnings_monitor.py, technomic_watchlist_scan.py).

    This was the actual gap behind RB-DEFECT-2026-08-20: a real, material
    finding (Red Robin's Q2 earnings-call technology signal) existed nowhere
    the RBB Project could see it, because context.json only ever read
    strategic_events.json -- the entity-level watchlist signal stream in
    ecosystem_intelligence.json was invisible to the cockpit projection
    entirely, independent of which chat surface asked about it.
    """
    if not path.exists():
        return []
    data = _load_json(path)
    entity_names = {e.get("id"): e.get("name") for e in data.get("entities", []) if e.get("id")}
    material = [
        s for s in data.get("signals", [])
        if _is_material_signal((s.get("confidence") or {}).get("level"), s.get("signal_type"))
    ]
    material.sort(key=lambda s: s.get("captured_at", ""), reverse=True)
    return [{
        "signal_id": s.get("id"),
        "signal_type": s.get("signal_type"),
        "entities": [entity_names.get(eid, eid) for eid in (s.get("entities") or [])],
        "summary": s.get("summary"),
        "confidence": (s.get("confidence") or {}).get("level"),
        "event_at": s.get("event_at"),
        "captured_at": s.get("captured_at"),
        "source": "ecosystem_intelligence",
    } for s in material[:limit]]


def _freshness(now: datetime) -> dict:
    files = {}
    stale = []
    digest = hashlib.sha256()
    for name, path in INPUTS.items():
        if not path.exists():
            continue
        stat = path.stat()
        age_hours = round((now.timestamp() - stat.st_mtime) / 3600, 1)
        files[name] = {
            "path": str(path.relative_to(ROOT)),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "age_hours": age_hours,
        }
        if age_hours > 48:
            stale.append(name)
        digest.update(name.encode())
        digest.update(path.read_bytes())
    return {"inputs": files, "stale_after_hours": 48, "stale_inputs": stale,
            "input_fingerprint": digest.hexdigest()}


# RB-2026-08-23: which INPUTS keys each top-level payload section is built
# from, so a stale input can be traced to exactly which sections it taints.
# A parallel section_freshness block (not embedded per-section) because
# several sections are bare lists (active_opportunities,
# active_theses_and_evidence_state, recent_material_intelligence,
# pending_decisions) that already have external consumers depending on
# their shape -- adding a key inside them would be a breaking schema
# change. One place to check is also simpler for a cockpit chat to reason
# about than "some sections carry it inline, some don't."
SECTION_INPUTS = {
    "weekly_outcomes_and_priorities": ["weekly_plan"],
    "active_opportunities": ["active_threads"],
    "open_loops": ["legacy_loops", "executive_loops"],
    "active_theses_and_evidence_state": ["strategic_events"],
    "recent_material_intelligence": ["strategic_events", "ecosystem_intelligence"],
    "pending_decisions": ["executive_loops"],
}


def _section_freshness(stale_inputs: list[str]) -> dict:
    stale_set = set(stale_inputs)
    return {
        section: {
            "stale": bool(stale_set & set(inputs)),
            "based_on_stale_inputs": sorted(stale_set & set(inputs)),
        }
        for section, inputs in SECTION_INPUTS.items()
    }


def build_context(*, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    weekly = _load_json(INPUTS["weekly_plan"])
    legacy = _legacy_loops(INPUTS["legacy_loops"])
    eolms = _active_eolms(_load_json(INPUTS["executive_loops"]))
    threads = yaml.safe_load(INPUTS["active_threads"].read_text(encoding="utf-8"))["threads"]
    events = _load_json(INPUTS["strategic_events"])["events"]
    registry = yaml.safe_load(INPUTS["registry"].read_text(encoding="utf-8"))
    active_threads = [t for t in threads if t.get("status") == "open"]
    recent_events = sorted(events, key=lambda e: e.get("last_seen_at", ""), reverse=True)[:10]
    pending_decisions = [x for x in eolms if x.get("category") == "decision" or x.get("status") == "identified"]
    freshness = _freshness(now)
    payload = {
        "schema_version": "1.0",
        "projection_type": "generated_read_only",
        "generated_at": now.isoformat(),
        "as_of_date": now.date().isoformat(),
        "repository_revision": _git_revision(),
        "authority_registry": "system/CANONICAL_REGISTRY.yaml",
        "freshness": freshness,
        "section_freshness": _section_freshness(freshness["stale_inputs"]),
        "weekly_outcomes_and_priorities": {
            "week_of": weekly.get("week_of"), "status": weekly.get("status"),
            "outcomes": weekly.get("outcomes", []), "risks": weekly.get("risks", []),
            "forcing_functions": weekly.get("forcing_functions", []),
        },
        "active_opportunities": active_threads,
        "open_loops": {"legacy": legacy, "executive": eolms,
                       "total": len(legacy) + len(eolms)},
        "active_theses_and_evidence_state": [{
            "event_id": e.get("event_id"), "title": e.get("title"),
            "lifecycle": e.get("lifecycle"), "confidence_score": e.get("confidence_score"),
            "last_seen_at": e.get("last_seen_at"), "entities": e.get("entities"),
            "thesis_alignment": e.get("thesis_alignment"), "proof_stats": e.get("proof_stats"),
        } for e in recent_events],
        "recent_material_intelligence": sorted(
            [{
                "event_id": e.get("event_id"), "title": e.get("title"),
                "last_seen_at": e.get("last_seen_at"),
                "recommended_actions": e.get("recommended_actions", []),
                "source": "strategic_events",
            } for e in recent_events]
            + _material_ecosystem_signals(INPUTS["ecosystem_intelligence"]),
            key=lambda i: i.get("last_seen_at") or i.get("captured_at") or "",
            reverse=True,
        )[:12],
        "pending_decisions": pending_decisions,
        "reconciliation_queue": {
            "authority_gaps": registry.get("unresolved_governance_decisions", []),
            "stale_active_threads": [t.get("id") for t in active_threads
                                     if t.get("target_close") and str(t["target_close"]) < now.date().isoformat()],
            "pending_identity_matches": _pending_identity_matches(INPUTS["identity_matches"]),
            "cross_namespace_loop_conflicts": _cross_namespace_loop_conflicts(),
        },
        "promotion_contract": registry["global_policies"]["persistence_receipts"],
    }
    # PyYAML materializes ISO dates as date objects; the projection contract is JSON.
    return json.loads(json.dumps(payload, default=str))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="Build and validate without writing")
    parser.add_argument("--no-drive-export", action="store_true",
                        help="Regenerate locally without the non-fatal Drive projection push")
    args = parser.parse_args()
    context = build_context()
    encoded = json.dumps(context, indent=2, ensure_ascii=False) + "\n"
    if args.check:
        print(json.dumps({"status": "ok", "open_loops": context["open_loops"]["total"],
                          "fingerprint": context["freshness"]["input_fingerprint"]}))
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(encoded, encoding="utf-8")
    os.replace(tmp, args.output)
    print(args.output)
    if not args.no_drive_export and args.output.resolve() == DEFAULT_OUTPUT.resolve():
        result = subprocess.run(
            [sys.executable, str(SYSTEM / "scripts/cockpit_drive_export.py")],
            cwd=ROOT, capture_output=True, text=True)
        if result.stdout.strip():
            print(result.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
