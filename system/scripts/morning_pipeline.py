#!/usr/bin/env python3
"""
morning_pipeline.py - build and publish the canonical RB morning artifact.

This is the job the local scheduler should run before ChatGPT's native Task
fires. Codex/RB owns collection, scoring, RI processing, market intelligence,
canonical brief construction, and local publication. ChatGPT owns rendering the
already-built brief and notifying Todd.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from intelligence_observability import (
    build_intelligence_health_dashboard,
    append_collection_scan_record,
)
import mutation_policy

PROJECT_DIR = Path(os.environ["RB_PROJECT_DIR"]).resolve() if os.environ.get("RB_PROJECT_DIR") else Path(__file__).resolve().parent.parent.parent
SYSTEM_DIR = PROJECT_DIR / "system"
SCRIPTS_DIR = SYSTEM_DIR / "scripts"
CACHE_PATH = SYSTEM_DIR / ".cache" / "morning_pipeline.json"
SCAN_CACHE_PATH = SYSTEM_DIR / ".cache" / "pre_brief_scan.json"
PUBLISHED_DIR = SYSTEM_DIR / "published" / "daily"


def _run(cmd: list[str]) -> dict:
    started = datetime.now(timezone.utc)
    proc = subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True)
    finished = datetime.now(timezone.utc)
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-40:]),
        "stderr_tail": "\n".join(proc.stderr.splitlines()[-40:]),
    }


def _earnings_monitor_fresh(today: date) -> bool:
    """RB-DEFECT-066: True if earnings_monitor_health.json was generated
    today. Checked independently of intelligence_assessment.json's cache
    date, since earnings_monitor.py runs several steps later in
    refresh_all.py's command list and can still be mid-fetch (or not yet
    started) when intelligence_assessment has already been stamped fresh.
    """
    health_path = SYSTEM_DIR / ".cache" / "earnings_monitor_health.json"
    if not health_path.exists():
        return False
    try:
        health = json.loads(health_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    generated_at = health.get("generated_at") or ""
    return generated_at[:10] == today.isoformat()


def _step(name: str, cmd: list[str], *, required: bool = True) -> dict:
    result = _run(cmd)
    status = "pass" if result["returncode"] == 0 else ("fail" if required else "warn")
    return {
        "name": name,
        "status": status,
        "required": required,
        "result": result,
    }


DELIVERY_STEP_NAMES = {
    "send_intelligence_brief_email",
    "send_daily_brief_email",
    "send_team_intelligence_brief_email",
    "send_backup_email",
}


def _compute_delivery_ok(steps: list[dict]) -> bool:
    """True unless the run attempted at least one delivery and all of them failed.

    Delivery steps (email sends) are marked required=False so a bad SMTP
    transport doesn't fail the build/publish phase. But a run where the
    brief was built yet never reached Todd isn't a real success -- gate
    overall pipeline "ok" on at least one delivery path succeeding whenever
    delivery was attempted at all.
    """
    delivery_steps = [s for s in steps if s.get("name") in DELIVERY_STEP_NAMES]
    if not delivery_steps:
        return True
    return any(s.get("status") == "pass" for s in delivery_steps)


def _published_paths(d: date) -> dict:
    day_dir = PUBLISHED_DIR / d.isoformat()
    return {
        "day_dir": str(day_dir.relative_to(PROJECT_DIR)),
        "index_md": str((day_dir / "index.md").relative_to(PROJECT_DIR)),
        "index_html": str((day_dir / "index.html").relative_to(PROJECT_DIR)),
        "brief_json": str((day_dir / "brief.json").relative_to(PROJECT_DIR)),
        "latest_html": str((PUBLISHED_DIR / "latest.html").relative_to(PROJECT_DIR)),
        "latest_brief_json": str((PUBLISHED_DIR / "latest_brief.json").relative_to(PROJECT_DIR)),
    }


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _fail_fingerprint(gate_result: dict) -> frozenset:
    """A hashable snapshot of the gate's current FAIL findings. RB-DEFECT-072:
    used to detect a repair attempt that made no real difference ("a retry
    that makes no artifact or finding change terminates with an explicit
    no-progress reason") -- compares (check, detail) pairs, not just pass
    counts, so a repair that fixes one fail while a different one appears
    still correctly reads as "changed", not silently ignored."""
    return frozenset(
        (f.get("check"), f.get("detail"))
        for f in gate_result.get("findings", [])
        if f.get("severity") == "fail" and f.get("passed") is False
    )


def _attempt_repair(py: str, target_date: date, date_args: list[str], gate_result: dict) -> dict:
    """RB-DEFECT-072: dispatch every safe_to_auto_repair FAIL finding from a
    brief_acceptance_check.py run to its repair_action. Never guesses at an
    action it doesn't recognize -- an unrecognized/unsafe finding is listed
    in `findings_considered` but nothing is done with it, so
    `any_repair_applied` correctly comes back False and the caller's loop
    stops instead of retrying on nothing.

    Deliberately does not re-invoke collection, assessment, or the renderer
    -- brief_repair.py edits the already-rendered .md file directly, and
    the delivery-readiness action only refreshes publish state. This is the
    "resume from the earliest affected stage, not collection" requirement,
    satisfied by construction rather than by tracking a resume point.
    """
    import brief_repair

    findings = gate_result.get("findings", [])
    fail_findings = [f for f in findings if f.get("severity") == "fail" and f.get("passed") is False]
    considered = [
        {"check": f.get("check"), "repair_action": f.get("repair_action"),
         "failure_class": f.get("failure_class"), "safe_to_auto_repair": f.get("safe_to_auto_repair")}
        for f in fail_findings
    ]
    actions_taken: list[dict] = []

    for finding in fail_findings:
        if finding.get("safe_to_auto_repair") is not True:
            continue
        action = finding.get("repair_action")
        if action == "dedup_story_clusters":
            scope = finding.get("artifact_scope")
            doc_key = scope if scope in ("intelligence", "daily") else "intelligence"
            receipt = brief_repair.repair_brief_file(target_date, doc_key)
            receipt["check"] = finding.get("check")
            actions_taken.append(receipt)
        elif action == "recompute_delivery_readiness":
            # check_delivery_readiness (brief_acceptance_check.py) is
            # already live/current-run -- the one legitimate, idempotent,
            # side-effect-safe action left to try is republishing, in case
            # a genuinely lagging artifact just needs one more chance to
            # catch up before the retry re-checks. No success is claimed
            # here; the rerun gate is the real verification, not this call's
            # return code.
            publish_result = _run([
                py, str(SCRIPTS_DIR / "publish.py"), "--write", "--confirm", *date_args,
            ])
            actions_taken.append({
                "check": finding.get("check"), "repair_action": action,
                "changed": publish_result["returncode"] == 0,
            })
        # else: a repair_action this dispatcher doesn't recognize -- left
        # alone on purpose rather than guessed at; still listed in
        # `considered` above so the receipt shows it was seen, not missed.

    return {
        "attempted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "findings_considered": considered,
        "actions_taken": actions_taken,
        "any_repair_applied": any(a.get("changed") for a in actions_taken),
    }


def _write_repair_receipt(target_date: date, attempts: list[dict], final_passed: bool) -> None:
    """Persists the full repair-loop trail for RB-DEFECT-072's acceptance
    criteria ("each repair attempt leaves a receipt") -- read by
    send_failure_alert.py when repair didn't fully succeed, so the alert can
    state what RB tried, not just that it gave up."""
    path = SYSTEM_DIR / ".cache" / "brief_repair_receipt.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "date": target_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "final_passed": final_passed,
        "attempts": attempts,
    }, indent=2, default=str) + "\n", encoding="utf-8")


def _run_acceptance_gate_with_repair(
    py: str, today: date, date_args: list[str], build_steps: list[dict],
    *, step_fn=None, max_attempts: int = 2,
) -> dict:
    """RB-DEFECT-072: runs brief_acceptance_check.py, and on a repairable
    failure, attempts a bounded repair-and-revalidate loop before giving up.
    Every step it runs (initial gate + any retries) is appended to
    `build_steps` in place, exactly like every other pipeline step, so it
    shows up in the normal step log/execution report.

    step_fn defaults to the real _step (subprocess-based); tests inject a
    fake to exercise the loop's own bounding/no-progress/status logic
    without shelling out to a real brief_acceptance_check.py.

    Returns {"gate_passed", "gate_result", "repair_attempts",
    "brief_acceptance_status", "intelligence_clean", "daily_clean"}.
    intelligence_clean/daily_clean implement the required-fix's partial-
    delivery policy: conservative by construction (True only when EVERY
    still-failing finding is scoped to the other document specifically).
    """
    step_fn = step_fn or _step
    acceptance_step = step_fn("brief_acceptance_gate", [
        py, str(SCRIPTS_DIR / "brief_acceptance_check.py"), "--json", *date_args,
    ], required=True)
    build_steps.append(acceptance_step)
    gate_passed = acceptance_step["status"] == "pass"
    gate_result = _load_json(SYSTEM_DIR / ".cache" / "brief_acceptance_result.json")
    repair_attempts: list[dict] = []
    status = "passed_first_attempt"

    attempt = 0
    while not gate_passed and attempt < max_attempts:
        attempt += 1
        before_fingerprint = _fail_fingerprint(gate_result)
        repair_outcome = _attempt_repair(py, today, date_args, gate_result)
        repair_attempts.append(repair_outcome)
        if not repair_outcome["any_repair_applied"]:
            # Nothing safe_to_auto_repair, or every available action's own
            # dispatcher reported no real change -- stop instead of
            # retrying against an unchanged artifact.
            break
        retry_step = step_fn(f"brief_acceptance_gate_retry_{attempt}", [
            py, str(SCRIPTS_DIR / "brief_acceptance_check.py"), "--json", *date_args,
        ], required=False)
        build_steps.append(retry_step)
        gate_passed = retry_step["status"] == "pass"
        gate_result = _load_json(SYSTEM_DIR / ".cache" / "brief_acceptance_result.json")
        if not gate_passed and _fail_fingerprint(gate_result) == before_fingerprint:
            # No-progress: a repair ran, but the exact same failures persist
            # -- an explicit reason to stop, not a silent infinite loop.
            repair_outcome["no_progress"] = True
            break

    if repair_attempts:
        _write_repair_receipt(today, repair_attempts, gate_passed)
        status = "repaired_and_delivered" if gate_passed else "failed_after_repair_exhausted"

    intelligence_clean = daily_clean = False
    if not gate_passed:
        remaining_fails = [f for f in gate_result.get("findings", [])
                            if f.get("severity") == "fail" and f.get("passed") is False]
        remaining_scopes = {f.get("artifact_scope") for f in remaining_fails}
        intelligence_clean = remaining_scopes == {"daily"}
        daily_clean = remaining_scopes == {"intelligence"}
        if intelligence_clean or daily_clean:
            status = "partially_delivered"

    return {
        "gate_passed": gate_passed,
        "gate_result": gate_result,
        "repair_attempts": repair_attempts,
        "brief_acceptance_status": status,
        "intelligence_clean": intelligence_clean,
        "daily_clean": daily_clean,
    }


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                rows.append(value)
        except (ValueError, TypeError):
            continue
    return rows


def _sum_numeric(mapping: dict, keys: tuple[str, ...]) -> int:
    return sum(int(mapping.get(key) or 0) for key in keys)


def _count_metric(value) -> int:
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _is_current_run(payload: dict, run_date: str | None) -> bool:
    if not payload or not run_date:
        return False
    for key in ("date", "ingest_date", "assessment_date"):
        if payload.get(key) == run_date:
            return True
    generated_at = str(payload.get("generated_at") or "")
    return generated_at[:10] == run_date


def build_execution_report(result: dict) -> dict:
    """Build the stable, user-facing receipt for one intelligence refresh."""
    source_health = _load_json(SYSTEM_DIR / ".cache" / "source_health.json")
    assessment = _load_json(SYSTEM_DIR / ".cache" / "intelligence_assessment.json")
    cascade = _load_json(SYSTEM_DIR / ".cache" / "intelligence_cascade.json")
    source_discovery = _load_json(SYSTEM_DIR / ".cache" / "public_source_discovery.json")
    baseline_gate = _load_json(SYSTEM_DIR / ".cache" / "baseline_research_gate.json")
    routine_evidence = _load_json(SYSTEM_DIR / ".cache" / "routine_research_evidence_sweep_latest.json")
    tech_research = _load_json(SYSTEM_DIR / ".cache" / "tech_stack_web_research_sweep_latest.json")
    ramifications = _load_json(SYSTEM_DIR / ".cache" / "intelligence_ramifications.json")
    contacts = _load_json(SYSTEM_DIR / ".cache" / "contacts_ingest_latest.json")
    linkedin = _load_json(SYSTEM_DIR / ".cache" / "linkedin_ingest_latest.json")
    published_brief = _load_json(PUBLISHED_DIR / "latest_brief.json")
    run_date = str(result.get("date") or "")
    routine_receipts = [
        row for row in _load_jsonl(SYSTEM_DIR / ".cache" / "routine_research_receipts.jsonl")
        if str(row.get("completed_at") or "")[:10] == run_date
    ]
    mutation_lifecycle = [
        row for row in _load_jsonl(SYSTEM_DIR / ".cache" / "intelligence_mutation_ledger.jsonl")
        if str(row.get("timestamp") or "")[:10] == run_date
    ]
    mutation_status_counts: dict[str, int] = {}
    for row in mutation_lifecycle:
        status = str(row.get("status") or "unknown")
        mutation_status_counts[status] = mutation_status_counts.get(status, 0) + 1

    # RB-DEFECT-2026-09-18 (KFC watchlist discrepancy): mutation_lifecycle
    # above only ever reflects the routine-research-evidence ledger. Real
    # auto-applied mutations from the shared mutation_policy decision path
    # (watchlist_add auto-apply, LinkedIn passive RI auto-confirm, and any
    # future caller) write their own durable receipts instead -- this was
    # the exact gap that let a watchlist proposal report auto_applied=true
    # with records_changed=0 and an empty mutation_lifecycle: the write was
    # real (ecosystem_intelligence.json changed), but nothing outside the
    # proposal dict itself ever recorded that it happened. Folding
    # mutation_policy's own receipts in here, reconciled against the same
    # run_date window, is what makes auto_applied/records_changed/
    # mutation_lifecycle agree for every mutation that goes through the
    # shared policy -- see system/scripts/mutation_policy.py.
    mutation_policy_receipts = [
        row for row in mutation_policy.load_receipts()
        if str(row.get("recorded_at") or "")[:10] == run_date
    ]
    mutation_policy_summary = mutation_policy.summarize_receipts(mutation_policy_receipts)
    routine_sources = sum(_count_metric(row.get("sources_checked")) for row in routine_receipts)
    routine_evidence_items = sum(_count_metric(row.get("evidence_items_added")) for row in routine_receipts)

    sources = source_health.get("sources") or {}
    processed_sources = sorted(
        name for name, row in sources.items()
        if isinstance(row, dict) and row.get("status") == "refreshed"
    )
    stale_sources = sorted(
        name for name, row in sources.items()
        if isinstance(row, dict) and row.get("status") not in ("refreshed", "ok", "fresh")
    )
    source_records = sum(
        int(row.get("item_count") or 0)
        for row in sources.values()
        if isinstance(row, dict) and row.get("status") == "refreshed"
    )

    assessment_stats = assessment.get("trust_stats") or {}
    contact_stats = (
        contacts.get("aggregate_trust_stats")
        or contacts.get("trust_stats")
        or {}
    )
    if not _is_current_run(contacts, result.get("date")):
        contact_stats = {}
    linkedin_intel = linkedin.get("delta_intelligence") or {}
    if not _is_current_run(linkedin, result.get("date")):
        linkedin_intel = {}
    linkedin_graph = linkedin_intel.get("graph_mutations") or {}
    linkedin_opportunities = linkedin_intel.get("opportunity_detection") or {}

    records_added = (
        int(linkedin_graph.get("baseline_entries_added") or 0)
        + int(contact_stats.get("reconciliation_queue_additions") or 0)
    )
    records_changed = (
        int(linkedin_graph.get("baseline_entries_updated") or 0)
        + int(contact_stats.get("baseline_mutations") or 0)
        + int(mutation_policy_summary.get("automatically_applied") or 0)
    )
    mutations_generated = (
        int(assessment_stats.get("mutation_proposals") or 0)
        + _sum_numeric(linkedin_graph, (
            "relationship_strength_mutations",
            "strategic_importance_mutations",
            "opportunity_graph_mutations",
            "who_matters_now_mutations",
        ))
        + int(contact_stats.get("baseline_mutations") or 0)
        + int(mutation_policy_summary.get("mutations_generated") or 0)
    )
    opportunities_generated = sum(
        len(value) for value in linkedin_opportunities.values()
        if isinstance(value, list)
    )
    relationship_changes = _sum_numeric(linkedin_graph, (
        "relationship_strength_mutations",
        "strategic_importance_mutations",
        "who_matters_now_mutations",
    ))

    step_errors = []
    report_steps = list(result.get("steps") or [])
    prior_scan = result.get("pre_brief_scan") or {}
    if isinstance(prior_scan, dict):
        report_steps = list(prior_scan.get("steps") or []) + report_steps
    for step in report_steps:
        if step.get("status") == "pass":
            continue
        detail = (
            (step.get("result") or {}).get("stderr_tail")
            or (step.get("result") or {}).get("stdout_tail")
            or "step failed"
        )
        step_errors.append({"step": step.get("name"), "status": step.get("status"),
                            "detail": str(detail).splitlines()[-1][:500]})
    for error in assessment.get("run_errors") or []:
        step_errors.append({"step": "intelligence_assessment", "status": "warn",
                            "detail": str(error)[:500]})

    brief_rebuilt = any(
        step.get("name") == "publish_canonical_artifacts" and step.get("status") == "pass"
        for step in result.get("steps") or []
    )
    if not result.get("ok"):
        refresh_status = "Failed"
    elif step_errors or stale_sources:
        refresh_status = "Partial"
    else:
        refresh_status = "Success"

    execution_report = {
        "cycle_type": "daily_intelligence_monitoring",
        "routine_research_performed": bool(routine_receipts),
        "daily_cycle_performed_routine_research": False,
        "intelligence_cycle_statistics": {
            "routine_research": {
                "accounts_or_entities_researched": len(routine_receipts),
                "sources_checked": routine_sources,
                "evidence_items_added": routine_evidence_items,
                "entities": [row.get("entity_name") for row in routine_receipts if row.get("entity_name")],
                "field_evidence_queued_for_review": (
                    int(routine_evidence.get("accepted_for_review") or 0)
                    if str(routine_evidence.get("generated_at") or "")[:10] == run_date else 0
                ),
                "tech_stack_candidates_proposed": (
                    int(tech_research.get("proposed") or 0)
                    if str(tech_research.get("generated_at") or "")[:10] == run_date else 0
                ),
            },
            "daily_monitoring": {
                "sources_scanned": int(assessment_stats.get("sources_assessed") or 0),
                "items_fetched": int(assessment_stats.get("items_fetched") or 0),
                "intelligence_signals_classified": int(assessment_stats.get("items_classified") or 0),
                "multi_source_convergences": int(assessment_stats.get("convergences_detected") or 0),
                "mutation_proposals": int(assessment_stats.get("mutation_proposals") or 0),
                "records_changed": records_changed,
                "mutation_lifecycle": mutation_status_counts,
                # Reconciliation view for every ingestion path that goes
                # through mutation_policy.decide(): mutations_generated,
                # automatically_applied, confirmation_required,
                # review_required, and rejected must always sum consistently
                # with records_changed above -- automatically_applied IS
                # records_changed's mutation_policy contribution, by
                # construction (see records_changed above), not a separately
                # maintained count that can drift out of sync with it.
                "mutation_policy_reconciliation": mutation_policy_summary,
                "newsworthy_baseline_gaps": int(baseline_gate.get("baseline_gaps_found") or 0),
                "urgent_research_requests": int(baseline_gate.get("urgent_requests_queued") or 0),
                "ramifications_generated": int(ramifications.get("ramifications_generated") or 0),
                "artifact_reviews_recommended": int(ramifications.get("artifact_reviews_recommended") or 0),
                "historical_events_routed_to_baseline": int(ramifications.get("historical_events_excluded") or 0),
            },
        },
        "refresh_status": refresh_status,
        "generated_at": result.get("generated_at"),
        "date": result.get("date"),
        "mode": result.get("mode"),
        "sources_processed": processed_sources,
        "records_processed": source_records + int(assessment_stats.get("items_fetched") or 0),
        "records_added": records_added,
        "records_changed": records_changed,
        "mutations_generated": mutations_generated,
        "opportunities_generated": opportunities_generated,
        "relationship_changes": relationship_changes,
        "trust_score": (
            source_health.get("trust_score")
            or source_health.get("brief_trust_score")
            or published_brief.get("trust_score")
            or (published_brief.get("canonical_brief") or {}).get("trust_score")
        ),
        "trust_level": source_health.get("brief_trustworthiness"),
        "stale_sources": stale_sources,
        "processing_errors": step_errors,
        "brief_rebuilt": brief_rebuilt,
        "intelligence_readiness": result.get("intelligence_readiness") or {},
        # Five-stage proof contract: distinguish collection from assessment,
        # durable decisions, downstream cascade, and delivery. A zero is a
        # real result; unavailable metrics remain explicit rather than being
        # guessed from a successful process exit.
        "cycle_proof": {
            "gather": {
                "sources_assessed": int(assessment_stats.get("sources_assessed") or 0),
                "sources_accepted": int(assessment_stats.get("sources_accepted") or 0),
                "sources_rejected": int(assessment_stats.get("sources_rejected") or 0),
                "items_fetched": int(assessment_stats.get("items_fetched") or 0),
                "items_written_to_intelligence_db": int(assessment_stats.get("items_written_to_db") or 0),
                "source_discovery_entities_checked": len(source_discovery.get("entities_checked") or []),
                "new_source_candidates": len(source_discovery.get("candidates") or []),
                "newsworthy_baseline_gaps": int(baseline_gate.get("baseline_gaps_found") or 0),
                "urgent_routine_research_requests": int(baseline_gate.get("urgent_requests_queued") or 0),
            },
            "assess": {
                "items_classified": int(assessment_stats.get("items_classified") or 0),
                "multi_source_convergences": int(assessment_stats.get("convergences_detected") or 0),
                "multi_source_entities": int(assessment_stats.get("multi_source_entities") or 0),
                "deduplicated_or_suppressed": assessment_stats.get("items_suppressed"),
                "discarded_as_noise": assessment_stats.get("items_discarded"),
            },
            "record": {
                "mutation_proposals": int(assessment_stats.get("mutation_proposals") or 0),
                "records_added": records_added,
                "records_changed": records_changed,
            },
            "cascade": {
                "entities_with_new_intelligence": int(cascade.get("entities_with_new_intelligence") or 0),
                "relationships_touched": int(cascade.get("relationships_touched") or 0),
                "blue_sheets_auto_synced": len(cascade.get("blue_sheets_auto_synced") or []),
                "blue_sheets_queued_for_review": len(cascade.get("blue_sheets_queued_for_review") or []),
                "account_research_flagged_stale": len(cascade.get("account_research_flagged_stale") or []),
                "master_plans_auto_synced": len(cascade.get("master_account_plans_auto_synced") or []),
            },
            "report": {
                "brief_rebuilt": brief_rebuilt,
                "delivery_ok": bool(result.get("delivery_ok")),
            },
        },
    }
    execution_report["intelligence_health_dashboard"] = build_intelligence_health_dashboard(
        SYSTEM_DIR,
        as_of=date.fromisoformat(result["date"]),
        execution_report=execution_report,
    )
    return execution_report


def _check_brief_readiness(py: str) -> dict:
    """RB-DEFECT-017: Validate required intelligence sources before brief generation.

    Validates required sources, attempts automatic recovery on any that failed,
    then returns a readiness result that is embedded in the pipeline output.

    Required sources (must be fresh ≤ 6h for brief to be COMPLETE):
      - email (personal + bridgepoint)
      - calendar (personal + bridgepoint)
      - messages (Apple Messages)
      - calls (Apple Calls)

    Secondary sources (DEGRADED if stale, not INCOMPLETE):
      - linkedin_messaging
      - market_signals
      - relationship_signals
      - social_feed

    Recovery: if a required source is stale, attempt one refresh run.
    Returns readiness dict with status, recovery_attempted, recovery_succeeded.
    """
    import json as _json

    from brief_acceptance_check import check_source_freshness, REQUIRED_SOURCES

    sh_path = SYSTEM_DIR / ".cache" / "source_health.json"
    if not sh_path.exists():
        return {"checked": True, "status": "UNKNOWN", "note": "source_health.json missing"}

    try:
        sh = _json.loads(sh_path.read_text())
    except Exception:
        return {"checked": True, "status": "UNKNOWN", "note": "source_health.json unreadable"}

    # RB-2026-08-23: this now shares its freshness rule with
    # brief_acceptance_check.py instead of carrying an independent copy.
    # Behavior note: the prior inline version only flagged a required
    # source as failed if it appeared in sources with a bad status --
    # a required source entirely ABSENT from sources silently passed as
    # neither failed nor stale. The shared helper treats "absent" as
    # failed, which is the correct behavior for a readiness check but is
    # a real (and deliberate) change from what this function used to do.
    freshness = check_source_freshness(sh, required_sources=REQUIRED_SOURCES)
    failed: list[str] = freshness["failed"]
    stale: list[str] = freshness["stale"]

    if not failed and not stale:
        return {
            "checked": True,
            "status": "READY",
            "note": "All required sources are fresh.",
            "recovery_attempted": False,
            "failed_sources": [],
            "stale_sources": [],
        }

    # Attempt recovery for failed/stale sources
    recovery_attempted = bool(failed or stale)
    recovery_succeeded = False
    if recovery_attempted:
        try:
            import subprocess as _sub
            _result = _sub.run(
                [py, str(SCRIPTS_DIR / "refresh_sources.py"), "--all", "--save-health"],
                cwd=PROJECT_DIR, capture_output=True, text=True, timeout=120,
            )
            recovery_succeeded = _result.returncode == 0
        except Exception:
            recovery_succeeded = False

    # Re-check after recovery
    post_failed: list[str] = []
    if recovery_attempted:
        try:
            sh2 = _json.loads(sh_path.read_text())
            sources2 = sh2.get("sources") or {}
            for src_name in failed + stale:
                src_data2 = sources2.get(src_name) or {}
                if src_data2.get("status") not in ("refreshed", "ok", "fresh"):
                    post_failed.append(src_name)
        except Exception:
            post_failed = failed + stale

    if not post_failed:
        final_status = "READY_AFTER_RECOVERY"
        note = f"Recovery succeeded for: {', '.join(failed + stale)}."
    else:
        final_status = "DEGRADED" if len(post_failed) < len(REQUIRED_SOURCES) else "INCOMPLETE"
        note = f"Recovery failed for: {', '.join(post_failed)}. Brief may be incomplete."

    return {
        "checked": True,
        "status": final_status,
        "note": note,
        "recovery_attempted": recovery_attempted,
        "recovery_succeeded": recovery_succeeded,
        "failed_sources": failed,
        "stale_sources": stale,
        "post_recovery_failed": post_failed,
    }


def run_pipeline(
    *,
    today: date,
    send_backup_email: bool = False,
    confirm_passive_ri: bool = True,
    mode: str = "full",
) -> dict:
    """Run the morning pipeline.

    mode:
      "full"       — (default) scan sources, then build and publish the brief.
      "scan_only"  — gather phase only: fetch Google, refresh caches, run
                     intelligence_assessment.  Does NOT build or publish the brief.
                     Intended for the 4 AM pre-scan LaunchAgent.
      "brief_only" — build/publish phase only: assumes caches are already fresh
                     (written by a prior scan_only run).  Falls back to a full
                     scan if today's intelligence_assessment cache is missing.
    """
    py = sys.executable or "python3"
    date_args = ["--date", today.isoformat()]
    refresh_cmd = [py, str(SCRIPTS_DIR / "refresh_all.py"), *date_args]
    if confirm_passive_ri:
        refresh_cmd.append("--confirm-passive-ri")

    # ── Gather phase (scan_only + full) ──────────────────────────────────────
    scan_steps: list[dict] = []
    if mode in ("full", "scan_only"):
        scan_steps = [
            _step("check_apple_messages_and_calls", [
                py,
                str(SCRIPTS_DIR / "apple_access_check.py"),
                "--json",
            ], required=False),
            _step("fetch_google_accounts", [
                py,
                str(SCRIPTS_DIR / "fetch_google.py"),
                "both",
                "--account",
                "all",
                "--mailbox",
                "both",
                "--days",
                "14",
                "--no-consent",
            ], required=False),
            # LinkedIn's full message export is delayed and therefore cannot
            # support same-day relationship awareness.  Pull message-notification
            # metadata from Todd's personal Gmail across All Mail and Trash,
            # then merge it into the canonical LinkedIn interaction feed.
            _step("linkedin_notification_ingest", [
                py,
                str(SCRIPTS_DIR / "linkedin_notification_ingest.py"),
                "--days",
                "30",
                "--confirm",
                "--no-consent",
                "--json",
            ], required=False),
            _step("refresh_intelligence_caches", refresh_cmd),
            # Sprint D — rebuild contact index so relationship activation in
            # cos_synthesis uses today's loop_ledger + email participants.
            _step("rebuild_contact_index", [
                py,
                str(SCRIPTS_DIR / "contact_index.py"),
                "--rebuild",
            ], required=False),
            # Most baseline contacts have no email on file (LinkedIn imports don't
            # expose email addresses). Scan fresh inbound email for exact-name
            # matches to those contacts and queue them for operator confirmation —
            # never auto-merge on name alone. Best-effort: never blocks the brief.
            _step("identity_match_scan", [
                py,
                str(SCRIPTS_DIR / "identity_match_review.py"),
                "--json",
            ], required=False),
            # DEFECT-007: Auto-ingest any newly dropped LinkedIn export ZIPs.
            # Runs --scan to detect new files, then --ingest-new --confirm to process them.
            # best-effort: a missing export watcher never blocks the brief.
            _step("linkedin_export_watcher_scan", [
                py,
                str(SCRIPTS_DIR / "linkedin_export_watcher.py"),
                "--ingest-new",
                "--confirm",
            ], required=False),
            # Import ChatGPT's completed Hunter packet from the private,
            # Drive-Desktop-synced RBB Hunter Cycle Inbox. The importer is
            # idempotent, ignores outgoing assignment files, and leaves
            # invalid/unmatched files in Drive for review. This must run
            # immediately before the governed local sweep.
            # Pick up Deep Research packets exported to Downloads and copy only
            # genuine Hunter envelopes into the private Drive inbox. Runs
            # immediately before the Drive sync and governed sweep so yesterday's
            # exports are swept this morning. Non-blocking.
            _step("hunter_download_watcher", [
                py,
                str(SCRIPTS_DIR / "hunter_download_watcher.py"),
            ], required=False),
            _step("hunter_drive_inbox_sync", [
                py,
                str(SCRIPTS_DIR / "hunter_drive_inbox_sync.py"),
            ], required=False),
            # Dry-run validation and governed-dispatch preview only; never
            # canonical writes from the morning pipeline.
            _step("hunter_packet_sweep", [
                py,
                str(SCRIPTS_DIR / "hunter_cycle.py"),
                "sweep",
            ], required=False),
            # Persist a per-batch status/quality report after every morning
            # inbox sync and sweep. Best-effort so reporting never blocks the brief.
            _step("hunter_batch_report", [
                py,
                str(SCRIPTS_DIR / "hunter_batch.py"),
                "report",
            ], required=False),
            # LinkedIn exports are ingested after refresh_all writes source
            # health. Recompute health from the now-current normalized files
            # so a successfully processed export is not reported as stale in
            # the brief generated a few minutes later.
            _step("recompute_source_health_after_ingest", [
                py,
                str(SCRIPTS_DIR / "refresh_sources.py"),
                "--health-only",
            ], required=False),
            # RB-DEFECT-064 Phase 1: Auto-ingest any HubSpot CRM export CSVs dropped
            # into system/inbox/crm_exports/. best-effort: never blocks the brief.
            _step("hubspot_ingest_scan", [
                py,
                str(SCRIPTS_DIR / "hubspot_ingest.py"),
                "--scan",
            ], required=False),
            # DEFECT-029: Convert newly captured LinkedIn feed content into
            # knowledge, relationship, and engagement-opportunity mutations.
            _step("linkedin_content_mutation", [
                py,
                str(SCRIPTS_DIR / "social_content_mutation.py"),
                "--confirm",
                "--json",
            ], required=False),
            # RB-DEFECT-032: Route trusted-sender SMS full-text through the mutation
            # engine. Requires system/sms_trusted_senders.json (opt-in allowlist) and
            # fetch_apple_messages.py --trusted-fulltext (now set in refresh_sources.py).
            # No-ops gracefully when the allowlist is empty or missing.
            _step("sms_content_mutation", [
                py,
                str(SCRIPTS_DIR / "sms_content_mutation.py"),
                "--confirm",
            ], required=False),
            # DEFECT-020: Auto-ingest any newly dropped contacts exports (.vcf/.csv).
            # Enriches baseline phone/email fields and writes contacts_resolved.json
            # for identity resolution. Best-effort: never blocks the brief.
            _step("contacts_ingest_scan", [
                py,
                str(SCRIPTS_DIR / "contacts_ingest.py"),
                "--ingest-new",
                "--confirm",
            ], required=False),
            # DEFECT-021: Auto-ingest any newly dropped WhatsApp exports (.txt/.zip).
            # Classifies chats, resolves identities, computes velocity.
            # Best-effort: never blocks the brief.
            _step("whatsapp_ingest_scan", [
                py,
                str(SCRIPTS_DIR / "whatsapp_ingest.py"),
                "--ingest-new",
                "--confirm",
            ], required=False),
            # RB-DEFECT-2026-07-29: Global Payments Outlook has no working
            # AppleScript bridge or local data store (New Outlook), and no
            # Graph API connector is built yet. Auto-ingest any manually
            # dragged-out .ics/.eml exports dropped into
            # system/inbox/outlook_exports/. Best-effort: never blocks the brief.
            _step("outlook_manual_ingest_scan", [
                py,
                str(SCRIPTS_DIR / "outlook_manual_ingest.py"),
                "--ingest-new",
                "--confirm",
            ], required=False),
            # Computer Use writes only metadata JSON into a staging folder.
            # The deterministic pipeline validates, deduplicates, and promotes
            # it here rather than allowing a GUI task to mutate canonical data.
            _step("outlook_gui_capture_ingest", [
                py,
                str(SCRIPTS_DIR / "outlook_gui_capture.py"),
                "--ingest-new",
                "--confirm",
            ], required=False),
            # Reconcile observed email, LinkedIn, and calendar activity into
            # one direction-aware state surface after all message captures
            # have landed. This is what prevents stale single-channel
            # reactivation and follow-up recommendations.
            _step("interaction_event_ledger", [
                py,
                str(SCRIPTS_DIR / "interaction_event_ledger.py"),
                "--json",
            ], required=False),
            # Press release monitoring — scans ALL watchlist entities every cycle
            # against PR Newswire / Business Wire / Globe Newswire. Runs separately
            # from the rotating general-purpose entity alerts so no press release
            # is ever missed due to rotation scheduling.
            _step("entity_alerts_press_releases", [
                py,
                str(SCRIPTS_DIR / "entity_alerts.py"),
                "--press-releases",
            ], required=False),
            # General-purpose entity alerts (rotating batch for M&A, exec moves, etc.)
            _step("entity_alerts_rotation", [
                py,
                str(SCRIPTS_DIR / "entity_alerts.py"),
                "--cache",
            ], required=False),
            # Discover recurring publisher domains outside the configured
            # feed registry using rotating entity-specific searches. This is
            # proposal-only; a human must approve additions to
            # industry_sources.yaml.
            _step("public_source_discovery", [
                py, str(SCRIPTS_DIR / "public_source_discovery.py"), *date_args,
            ], required=False),
            # Snapshot and diff first-party leadership, technology, careers,
            # procurement, integration, and investor pages. Changes remain
            # review candidates until supported as canonical evidence.
            _step("entity_page_monitor", [
                py, str(SCRIPTS_DIR / "entity_page_monitor.py"), *date_args,
            ], required=False),
            # RB-DEFECT-2026-08-19: tiered press-release scan of the full
            # Technomic 1500-brand universe (entity_alerts.py above only
            # covers the ~155-entity mandatory watchlist). Top 100 by rank
            # scanned every run; the remaining ~1,253 rotate through in
            # weekly batches internally (own rotation state in
            # technomic_scan_rotation.json) -- one daily call here covers
            # both tiers, no separate weekly cron entry needed. Material
            # findings promote into today's watchlist roster (Section F);
            # everything else is recorded to ecosystem_intelligence.json
            # only. Must run before the build/publish phase reads
            # technomic_watchlist_promoted.json. See
            # technomic_watchlist_scan.py's own docstring for the full
            # report-vs-record contract.
            _step("technomic_watchlist_scan", [
                py,
                str(SCRIPTS_DIR / "technomic_watchlist_scan.py"),
                "--cache",
                "--quiet",
            ], required=False),
            # RB-DEFECT-069 (2026-09-10): a real, material Del Taco article
            # went undetected for 8 days despite RestaurantNews.com being an
            # already-"monitored" source -- monitored only ever meant "feeds
            # the general news cap," not "every article about one of Todd's
            # priority accounts is guaranteed to surface." This scans trade
            # publishers PER priority account (customers_prospects +
            # every vendor's Master Account Plan). Confidence-Based Auto-
            # Recording Phase 5 (2026-09-25): auto-confirms every material
            # match immediately -- confirming only links a source and bumps
            # last_evidence_date, never claims a fact (extracted_claims is
            # always []), so there's nothing here to protect by waiting.
            # Runs after technomic_watchlist_scan so it reads a freshly-
            # refreshed ecosystem_intelligence.json for entity resolution.
            _step("priority_account_publisher_scan", [
                py,
                str(SCRIPTS_DIR / "priority_account_publisher_scan.py"),
                "scan",
            ], required=False),
            # Daily FDD-release discovery for restaurant brands. The monitor
            # establishes a baseline, then creates review-first candidates for
            # newly published or amended documents; it never promotes claims
            # directly into an account dossier.
            _step("fdd_release_monitor", [
                py,
                str(SCRIPTS_DIR / "fdd_release_monitor.py"),
                "scan",
                *date_args,
            ], required=False),
            _step("sitemap_signal_monitor", [
                py,
                str(SCRIPTS_DIR / "sitemap_signal_monitor.py"),
                *date_args,
            ], required=False),
            _step("newsletter_delivery_audit", [
                py,
                str(SCRIPTS_DIR / "newsletter_delivery_audit.py"),
                *date_args,
            ], required=False),
            _step("public_artifact_monitor", [
                py,
                str(SCRIPTS_DIR / "public_artifact_monitor.py"),
                *date_args,
            ], required=False),
            _step("distress_filing_monitor", [
                py,
                str(SCRIPTS_DIR / "distress_filing_monitor.py"),
                *date_args,
            ], required=False),
            _step("advertiser_target_audit", [
                py,
                str(SCRIPTS_DIR / "advertiser_target_audit.py"),
                *date_args,
            ], required=False),
            # RB-2026-09-05: watchlist auto-expansion, scoped via direct
            # questions to Todd (repeated-appearance promotion bar for
            # brands; mine ecosystem_intelligence.json's own vendor
            # entities rather than sourcing a new external universe list
            # for vendors). Runs after technomic_watchlist_scan above so
            # today's freshly-appended history row is visible to the same
            # day's brand-promotion scan. Confidence-Based Auto-Recording
            # Phase 5 (2026-09-25): auto-applies every qualifying candidate
            # immediately (purely additive, no existing value to protect --
            # see watchlist_promotion.py's module docstring); the candidate
            # store is now an audit trail, not a queue.
            _step("watchlist_promotion_scan", [
                py,
                str(SCRIPTS_DIR / "watchlist_promotion.py"),
                "scan",
            ], required=False),
            # RB-2026-09-01: syncCompetitorIntelligence (pulls newly-captured
            # ecosystem_intelligence.json signals + account_intelligence docs
            # into each tracked competitor's real evidence log) existed and
            # worked but was never scheduled anywhere -- confirmed live,
            # "tracking competitors through daily intelligence" wasn't
            # actually happening despite the mechanism being real. Runs
            # after technomic_watchlist_scan above so same-day signals get
            # picked up the same day, not just on the next manual sync call.
            _step("competitor_intelligence_sync", [
                py,
                str(SCRIPTS_DIR / "competitor_intelligence.py"),
                "sync-all",
            ], required=False),
            # Carry-over intake only: sweep evidence produced by the separate
            # weekend routine-research cycle. This daily step does NOT perform
            # research and must never be counted as daily source discovery or
            # proof that routine research ran. The automation
            # only ever appends structured findings to
            # system/inbox/tech_stack_web_research_queue.jsonl -- it never
            # calls RB's API or writes to the graph. This step is what
            # turns those findings into real, review-first candidates
            # (via tech_stack_relationship_promotion.propose_research_
            # finding()), run once per day here rather than once per
            # 3-hour automation run.
            _step("tech_stack_web_research_sweep", [
                py,
                str(SCRIPTS_DIR / "tech_stack_web_research_sweep.py"),
                "sweep",
            ], required=False),
            # Validate and persist non-tech baseline evidence (leadership,
            # ownership, strategy, financial profile, etc.) into an explicit
            # review-first queue. Never a canonical mutation by itself.
            _step("routine_research_evidence_sweep", [
                py, str(SCRIPTS_DIR / "routine_research_evidence_sweep.py"),
            ], required=False),
            # RB-2026-09-02: real gap found -- the ONLY scheduled path
            # feeding tech-stack candidates was the weekend Codex
            # automation's sweep above. tech_stack_relationship_promotion.
            # scan() (mines technomic_watchlist_scan's own signals array for
            # vendor_relationship_formed/vendor_claimed_customer_relationship
            # entries, plus Todd's account_intelligence notes) has existed
            # since 2026-08-31 but was never wired into anything scheduled --
            # it only ever ran when someone happened to invoke the CLI by
            # hand. Once the weekend backfill finishes the full brand/vendor
            # universe and pauses itself, THIS is what keeps tech-stack
            # candidates flowing from the daily press-release/signal scan
            # already running above (technomic_watchlist_scan), not a new
            # web-browsing mechanism -- the daily pipeline was never missing
            # research capability, just the step that mines what it already
            # collects. Idempotent: matches existing pending/resolved
            # candidates by (brand, vendor, category) and never re-proposes
            # or duplicates.
            _step("tech_stack_relationship_promotion_scan", [
                py,
                str(SCRIPTS_DIR / "tech_stack_relationship_promotion.py"),
                "scan",
            ], required=False),
            # RB-2026-09-11: same "mine what's already collected" posture as
            # tech_stack_relationship_promotion_scan directly above --
            # M&A/ownership-change signals are already detected daily
            # (entity_alerts/technomic_watchlist_scan/
            # priority_account_publisher_scan all feed funding_event
            # signals), but no entity ever had a structured owner field to
            # promote them into. Mines ecosystem_intelligence.json's own
            # signals array + Todd's account_intelligence notes for
            # ownership-change language. Confidence-Based Auto-Recording
            # Phase 6 (2026-09-25): auto-applies when a real owner name was
            # extracted (proposed_owner_confidence != "unknown" -- that gate
            # is unchanged) and either no existing owner is on file, or the
            # new claim's confidence clears the same +0.15/0.9 margin the
            # rest of this feature uses; otherwise the claim is recorded to
            # reported_alternates without overwriting. See
            # getOwnershipChangeProposals/confirmProposal
            # (kind="ownership_change") for the residual "unknown"-name
            # candidates a human still needs to name.
            _step("ownership_promotion_scan", [
                py,
                str(SCRIPTS_DIR / "ownership_promotion.py"),
                "scan",
            ], required=False),
            # RB-2026-09-11: same posture again, this time for executive
            # moves -- leadership_change is RBB's most common real material
            # signal type, but the named executive was discarded at
            # classification time, never checked against baseline_index.json
            # (RBB's real contact-tracking system). Mines the graph's
            # signals + account_intelligence notes, matches against known
            # contacts by name, and either updates an existing contact's
            # current_company/current_role or creates a new one.
            # Confidence-Based Auto-Recording Phase 6 (2026-09-25): same
            # auto-apply/reported_alternates discipline as ownership_
            # promotion.py above, applied to baseline_index.json contacts.
            # See getExecutiveMoveProposals/confirmProposal
            # (kind="executive_move") for the residual "unknown"-name
            # candidates.
            _step("executive_move_promotion_scan", [
                py,
                str(SCRIPTS_DIR / "executive_move_promotion.py"),
                "scan",
            ], required=False),
            # Next-sprint Workstream 3 (RB-2026-09-18), wired to chat/API
            # 2026-09-25: job postings as a leading-indicator source.
            # vulnerability_taxonomy.py's tech_hiring category already
            # classifies "Director of Restaurant Technology"/"POS Program
            # Manager"-shaped role titles and is already wired into
            # entity_alerts.py/competitive_vulnerability.py's scoring --
            # the gap was acquisition, not taxonomy: nothing ever fetched a
            # real job posting for a priority account. DuckDuckGo HTML
            # search against known ATS domains (greenhouse/lever/ashby/
            # workday/icims). Confidence-Based Auto-Recording Phase 7:
            # confirming here never claims a fact (extracted_claims is
            # always []), so scan() auto-confirms every material match
            # immediately -- see getJobPostingCandidates/confirmProposal
            # (kind="job_posting") for the rare still-pending residual.
            # Runs alongside the other promotion scanners above.
            _step("job_postings_promotion_scan", [
                py,
                str(SCRIPTS_DIR / "job_postings_promotion.py"),
                "scan",
            ], required=False),
            # RB-10.7: Stock price signal monitor — flags ≥3% daily moves,
            # volume spikes, and 52-week high/low proximity for watchlist tickers.
            # Writes signals to market_signals_earnings.jsonl for F: Watchlist.
            _step("price_watch", [
                py,
                str(SCRIPTS_DIR / "price_watch.py"),
            ], required=False),
            # Daily exception gate: material news about an entity with no
            # meaningful baseline may request one bounded routine-research
            # run. At most one request per day; this step performs no web
            # research and no canonical mutation itself.
            _step("baseline_research_gap_gate", [
                py, str(SCRIPTS_DIR / "baseline_research_gate.py"), *date_args,
            ], required=False),
            # Radar + Pursuit coverage: inventory research completeness across
            # all watched accounts and tracked vendors so later cycles pursue
            # the most valuable missing dimension rather than repeating broad
            # searches. Read-only with respect to canonical intelligence.
            _step("intelligence_coverage_matrix", [
                py, str(SCRIPTS_DIR / "intelligence_coverage_matrix.py"), *date_args,
            ], required=False),
            # 2026-10-02: the judgment/execution split Todd asked for --
            # RBB decides which companies matter most (reusing the same
            # baseline_research_gate._strategic_value signal
            # intelligence_coverage_matrix.py's own pursuit/radar lanes use,
            # but over the full brand+competitor gap universe, not just
            # already-watched entities) and leaves that stack-ranked order
            # in system/.cache/hunter_priority_queue.json. The Hunter
            # automations read this file for their next target instead of
            # each re-deriving their own narrower priority ad hoc.
            _step("hunter_research_priority_queue", [
                py, str(SCRIPTS_DIR / "hunter_research_priority_queue.py"),
            ], required=False),
            # Convert verified vulnerability signals plus incumbent exposure
            # into review-first buying-window hypotheses and retain outcome
            # history for later score calibration.
            _step("sales_opportunity_radar", [
                py, str(SCRIPTS_DIR / "sales_opportunity_radar.py"), *date_args,
            ], required=False),
            # One ranked decision surface across buying-window hypotheses,
            # first-party page changes, baseline gaps, downstream
            # ramifications, and competitor reviews. Recommendation-only:
            # existing authorization gates still control all external or
            # canonical actions.
            _step("intelligence_action_queue", [
                py, str(SCRIPTS_DIR / "intelligence_action_queue.py"), *date_args,
            ], required=False),
            # RB-2026-09-15: the accountability half of the action queue --
            # correlates Todd's durable accept/reject/defer dispositions
            # (intelligence_action_queue.resolve()/STATE_PATH) against real
            # downstream outcomes where one exists (currently: a
            # buying_window_hypothesis's entity later showing a real active
            # pursuit). Reads accumulated state only; no dependency on
            # anything earlier in this cycle, so its position here is not
            # ordering-sensitive the way intelligence_action_queue itself is.
            _step("intelligence_calibration", [
                py, str(SCRIPTS_DIR / "intelligence_calibration.py"), *date_args,
            ], required=False),
            _step("strava_sync", [
                py,
                str(SCRIPTS_DIR / "strava_sync.py"),
            ], required=False),
            # ICS Phase 1: scan Just Press Record iCloud folder for new transcripts,
            # run 6-stage intelligence pipeline, emit RI events + loops + audit.
            _step("capture_ingest_scan", [
                py,
                str(SCRIPTS_DIR / "capture_ingest.py"),
                "--scan",
            ], required=False),
            # RB-DEFECT-2026-07-09: the scan steps above only queue new
            # transcripts as pending -- the actual intelligence-extraction
            # step previously only ran via a live Custom GPT chat command
            # ("RB, process my captures"), so captures could sit pending
            # across multiple brief cycles if that was never said. Process
            # every pending capture automatically as part of the pipeline;
            # the chat command still works for anything recorded mid-day.
            _step("capture_process_all", [
                py,
                str(SCRIPTS_DIR / "process_pending_captures.py"),
            ], required=False),
            # Research-quality feedback ledger: fold any new deep-research
            # JSON sidecars in the ChatGPT Intelligence Drop into
            # deep_research_coverage.py's per-target and per-source-domain
            # outcome tracking. Independent of the capture pipeline above --
            # a sidecar is never queued as a capture itself (only .txt/.md
            # are), so this can run in either order; it sits here because it
            # is part of the same deep-research intake surface.
            _step("deep_research_sidecar_sweep", [
                py,
                str(SCRIPTS_DIR / "deep_research_coverage.py"),
                "sweep-sidecars",
            ], required=False),
            # RB-DEFECT-073 (2026-09-28): the sweep above only ever recorded
            # coverage/source-quality metadata -- a sidecar's structured
            # "findings" (products/strengths/weaknesses/vulnerabilities/
            # vendor claims/Genius evidence) never reached the canonical
            # competitor_intelligence/genius_capabilities stores on any
            # recurring schedule. Runs immediately after the coverage sweep
            # (same sidecar intake surface) and before the competitor
            # review scan below, so review-queue proposals reflect the
            # same day's imported findings. Every decision goes through
            # mutation_policy.decide(), so its receipts fold into
            # mutation_policy_reconciliation below for free.
            _step("competitor_platform_research_import", [
                py,
                str(SCRIPTS_DIR / "import_competitor_platform_research.py"),
                "--sweep",
                "--confirm",
            ], required=False),
            # 2026-10-01: Technology Lifecycle research cycles (see system/
            # technology_lifecycle/RESEARCH_KICKOFF.md) land as zip packets
            # in this same inbox. This is lossless intake only -- every
            # record is preserved verbatim in system/technology_lifecycle/
            # _intake/raw_cycle_records.jsonl plus a human-readable digest;
            # it does NOT yet promote records into the strict technology_
            # relationship_events.jsonl/technology_change_events.jsonl/etc.
            # schemas (that importer doesn't exist yet -- see system/
            # technology_lifecycle/README.md, "What exists vs. Phase 1").
            # Runs alongside the competitor-platform importer since both
            # read the same inbox folder; distinguished by packet_type, so
            # neither interferes with the other's sweep.
            _step("technology_lifecycle_cycle_sweep", [
                py,
                str(SCRIPTS_DIR / "import_technology_lifecycle_cycles.py"),
                "--sweep",
                "--confirm",
            ], required=False),
            # RB defect 2026-09-30: closes the exact gap the handoff
            # documented -- a deep-research capture's receipt (written two
            # steps above, in capture_process_all) measures only executive-
            # declaration mutations and has no idea the importer directly
            # above just applied real structured findings to the same
            # packet's canonical competitor record. Runs immediately after
            # that importer so today's reconciliation reflects today's
            # import receipts (import_competitor_platform_research.py's own
            # packet-keyed receipt store).
            _step("reconcile_deep_research_capture_receipts", [
                py,
                str(SCRIPTS_DIR / "reconcile_deep_research_capture_receipts.py"),
                "--since-date", today.isoformat(),
            ], required=False),
            # Captures must be processed before this review scan. Otherwise a
            # competitive capture can appear in today's brief while missing
            # today's competitor review queue (the live Toast failure found
            # 2026-09-14). Review-first: this proposes; it never rewrites a
            # battle card automatically.
            _step("competitor_intelligence_review_scan", [
                py,
                str(SCRIPTS_DIR / "competitor_intelligence_review.py"),
                "scan",
            ], required=False),
            # RB-DEFECT-2026-09-18 (intelligence-cycle repair): refresh_
            # intelligence_caches above (refresh_all.py) runs meeting_prep.py
            # and loop_autopilot.py near ITS OWN end -- but that happens
            # early in scan_steps, before rebuild_contact_index,
            # identity_match_scan, linkedin_export_watcher_scan, hubspot/sms/
            # contacts/whatsapp/outlook ingestion, and interaction_event_
            # ledger have all run. So even the routine scheduled pipeline
            # computed meeting-prep and loop state from stale interaction
            # data on every normal run, not just on a late/ad hoc manual
            # capture day. Splitting refresh_all.py's own internal ordering
            # is a bigger, riskier change than this repair's scope justifies
            # (it's a shared, heavily-used entrypoint); instead, guarantee a
            # final resync pass here, after every ingestion step above has
            # run, using the same cascade a manual GP/LinkedIn capture
            # already triggers (see post_capture_cascade.py and
            # outlook_gui_capture.py/linkedin_export_watcher.py's own
            # trigger_cascade wiring, which covers the ad hoc/off-schedule
            # case this step doesn't reach on its own).
            _step("post_capture_cascade_resync", [
                py,
                str(SCRIPTS_DIR / "post_capture_cascade.py"),
                "--trigger",
                "morning_pipeline_scan_complete",
                *date_args,
            ], required=False),
            # RB-2026-09-25: keeps Team Portal's owner-only-generated
            # documents (Canonical Background Brief, Competitive Brief,
            # Battle Card, Value Wedge) from going stale between Todd's
            # own manual clicks -- Team Portal itself never calls an LLM
            # and a
            # teammate's view is always read-only, so if nobody generates
            # one it just sits there. Runs last in this list on purpose:
            # every step above it (promotion scans, deep_research_sidecar_
            # sweep, competitor_intelligence_review_scan) is a source of
            # real new facts this refresh should reflect. Cheap and safe
            # to run unconditionally every day -- see refresh_persisted_
            # briefs.py's own module docstring for why regenerating
            # unchanged content never bloats version history.
            _step("refresh_persisted_briefs", [
                py,
                str(SCRIPTS_DIR / "refresh_persisted_briefs.py"),
            ], required=False),
            # RB-2026-09-30, Todd's explicit "Option B": a teammate can
            # request a Competitive Brief's LLM-synthesized "bottom line"
            # sooner than the weekly Friday EOW pass (competitive-brief-
            # synthesis.plist) via a Team Portal button; the request is
            # only ever recorded, never generated live. This step is what
            # actually processes that queue -- regenerates synthesis for
            # each uniquely-requested competitor and emails every
            # requester their updated brief. Runs after
            # refresh_persisted_briefs so the emailed copy reflects
            # today's freshest deterministic content too, not stale
            # account/evidence sections. Best-effort: never blocks the
            # brief if the queue is empty or a send fails.
            _step("competitive_brief_refresh_queue", [
                py,
                str(SCRIPTS_DIR / "competitive_brief_refresh_queue.py"),
                "process",
            ], required=False),
        ]

    # ── Brief_only fallback: if today's intelligence_assessment cache is
    #    missing, run the scan anyway so the brief is never stale. ─────────
    if mode == "brief_only":
        ia_cache = CACHE_PATH.parent / "intelligence_assessment.json"
        cache_fresh = False
        if ia_cache.exists():
            try:
                ia = json.loads(ia_cache.read_text(encoding="utf-8"))
                cache_fresh = ia.get("assessment_date") == today.isoformat()
            except Exception:
                pass
        if not cache_fresh:
            # Cache is missing or stale — fall back to full scan before building
            scan_steps = [
                _step("check_apple_messages_and_calls", [
                    py,
                    str(SCRIPTS_DIR / "apple_access_check.py"),
                    "--json",
                ], required=False),
                _step("fetch_google_accounts", [
                    py,
                    str(SCRIPTS_DIR / "fetch_google.py"),
                    "both",
                    "--account",
                    "all",
                    "--mailbox",
                    "both",
                    "--days",
                    "14",
                    "--no-consent",
                ], required=False),
                _step("linkedin_notification_ingest", [
                    py,
                    str(SCRIPTS_DIR / "linkedin_notification_ingest.py"),
                    "--days",
                    "30",
                    "--confirm",
                    "--no-consent",
                    "--json",
                ], required=False),
                _step("refresh_intelligence_caches", refresh_cmd),
                _step("public_source_discovery", [
                    py, str(SCRIPTS_DIR / "public_source_discovery.py"), *date_args,
                ], required=False),
                _step("rebuild_contact_index", [
                    py,
                    str(SCRIPTS_DIR / "contact_index.py"),
                    "--rebuild",
                ], required=False),
                # Identity match scan (fallback path) — see comment above.
                _step("identity_match_scan", [
                    py,
                    str(SCRIPTS_DIR / "identity_match_review.py"),
                    "--json",
                ], required=False),
                # DEFECT-007: Auto-ingest newly dropped LinkedIn exports (fallback path)
                _step("linkedin_export_watcher_scan", [
                    py,
                    str(SCRIPTS_DIR / "linkedin_export_watcher.py"),
                    "--ingest-new",
                    "--confirm",
                ], required=False),
                _step("recompute_source_health_after_ingest", [
                    py,
                    str(SCRIPTS_DIR / "refresh_sources.py"),
                    "--health-only",
                ], required=False),
                _step("interaction_event_ledger", [
                    py,
                    str(SCRIPTS_DIR / "interaction_event_ledger.py"),
                    "--json",
                ], required=False),
                # RB-DEFECT-064 Phase 1: HubSpot CRM export auto-ingest (fallback path)
                _step("hubspot_ingest_scan", [
                    py,
                    str(SCRIPTS_DIR / "hubspot_ingest.py"),
                    "--scan",
                ], required=False),
                _step("linkedin_content_mutation", [
                    py,
                    str(SCRIPTS_DIR / "social_content_mutation.py"),
                    "--confirm",
                    "--json",
                ], required=False),
                # RB-DEFECT-032: SMS content mutation (fallback path)
                _step("sms_content_mutation", [
                    py,
                    str(SCRIPTS_DIR / "sms_content_mutation.py"),
                    "--confirm",
                ], required=False),
                # DEFECT-020: Auto-ingest contacts exports (fallback path)
                _step("contacts_ingest_scan", [
                    py,
                    str(SCRIPTS_DIR / "contacts_ingest.py"),
                    "--ingest-new",
                    "--confirm",
                ], required=False),
                # DEFECT-021: Auto-ingest WhatsApp exports (fallback path)
                _step("whatsapp_ingest_scan", [
                    py,
                    str(SCRIPTS_DIR / "whatsapp_ingest.py"),
                    "--ingest-new",
                    "--confirm",
                ], required=False),
                _step("price_watch", [
                    py,
                    str(SCRIPTS_DIR / "price_watch.py"),
                ], required=False),
            ]
        elif not _earnings_monitor_fresh(today):
            # RB-DEFECT-066: intelligence_assessment.json is written partway
            # through refresh_all.py's command list; earnings_monitor.py
            # runs several steps later. A 4 AM pre-brief-scan job that is
            # still mid-run when the 5 AM brief_only job fires can leave
            # intelligence_assessment fresh (cache_fresh=True above) while
            # earnings_monitor hasn't run at all yet -- the exact race that
            # let PAR's Aug 6 earnings call render as "no earnings events."
            # Re-running the full 14-step scan just for this would be
            # wasteful; run earnings_monitor.py on its own and block on it
            # (required=True) so daily_brief.py never renders ahead of it.
            scan_steps = [
                _step("ensure_earnings_monitor_fresh", [
                    py,
                    str(SCRIPTS_DIR / "earnings_monitor.py"),
                    "--cache",
                ]),
            ]

    # Public collection is mandatory even when the 5 AM job reuses the 4 AM
    # scan. A current cache alone is insufficient: this contract proves the
    # feed scanner assessed sources and the entity-driven discovery rotation
    # checked at least one watch-list/account/competitor name today. Zero news
    # is valid; zero scanning is not.
    if mode in ("full", "scan_only", "brief_only"):
        scan_steps.append(_step("verify_public_intelligence_collection", [
            py,
            str(SCRIPTS_DIR / "verify_public_intelligence_collection.py"),
            *date_args,
            "--json",
        ]))

    # ── Build/publish phase (brief_only + full) ───────────────────────────
    build_steps: list[dict] = []
    # RB-DEFECT-072: default for scan_only (the gate/repair block below
    # never runs in that mode, so "not_applicable" -- never left undefined).
    brief_acceptance_status = "not_applicable"
    if mode in ("full", "brief_only"):
        build_steps = [
            # Tuesday+ recovery for a current-week Monday draft that was not
            # explicitly confirmed or rejected. The Monday EOD policy cannot
            # be satisfied by the 5 AM-only LaunchAgent, so the next scheduled
            # build is the durable recovery point.
            _step("weekly_plan_overdue_adoption", [
                py,
                str(SCRIPTS_DIR / "weekly_plan_generator.py"),
                "--auto-adopt-overdue",
                *date_args,
            ], required=False),
            _step("write_today_and_manifest", [
                py,
                str(SCRIPTS_DIR / "daily_brief.py"),
                "--cache",
                *date_args,
            ]),
            # RB-2026-08-27/28 — the actual downstream-artifact-cascade work
            # (Blue Sheet auto-sync, Account Research staleness flags + 24h
            # SLA sweep, coverage-gap detection) already ran automatically
            # inside write_today_and_manifest above (daily_brief.py embeds
            # intelligence_cascade.build_section() so it sees the same
            # day's ecosystem_intelligence.json signals). This step is
            # observability only -- confirms that actually happened and
            # gives it a real, named pass/fail line in the pipeline's
            # execution report, which is the daily receipt "the most
            # important function of RBB" needs.
            _step("verify_intelligence_cascade_ran", [
                py, str(SCRIPTS_DIR / "intelligence_cascade.py"), "--verify", *date_args,
            ], required=False),
            # Generated read model; it immediately performs its non-fatal,
            # fixed-file Google Drive projection export.
            _step("refresh_cockpit_context", [
                py, str(SCRIPTS_DIR / "cockpit_context.py"),
            ], required=False),
            # 2026-08-25 — recording-engine trust stat (RB founding principle:
            # input must be assessed and mutate the right artifact before the
            # CoS may speak). Sweeps system/audit/*.jsonl vs request.log for
            # every monitored write operation; flags SILENT ops (traffic with
            # zero confirmed mutations) via their own audit entry. Detection
            # only — no auto-repair (see script docstring for why).
            _step("mutation_reconciliation", [
                py, str(SCRIPTS_DIR / "mutation_reconciliation.py"),
            ], required=False),
            # 2026-08-28 — standing self-audit sweep (RB strategic assessment
            # finding: the self-check tools above already existed but never
            # ran on a schedule or surfaced to Todd; each wrote a cache file
            # nobody read unless someone went looking). Runs KB-consistency,
            # reads this run's own fresh mutation_reconciliation cache
            # (hence placed right after it), and JPR capture completeness.
            # Opens/closes ONE standing loop_ledger.md entry so drift
            # surfaces in the daily brief instead of waiting to be
            # rediscovered. Detection + loop tracking only, same "no
            # auto-repair" discipline as mutation_reconciliation itself.
            _step("self_audit_sweep", [
                py, str(SCRIPTS_DIR / "self_audit_sweep.py"),
            ], required=False),
            _step("publish_canonical_artifacts", [
                py,
                str(SCRIPTS_DIR / "publish.py"),
                "--write",
                "--confirm",
                *date_args,
            ]),
        ]

        # ── RB-9.64: Weekly Operating Rhythm ─────────────────────────────────
        # Monday (weekday 0): auto-generate draft weekly plan from current state.
        # Uses weekly_plan_generator.py --write-draft. The draft surfaces in the
        # brief's weekly_plan_focus section. Todd confirms via /weekly-plan confirm.
        if today.weekday() == 0:  # Monday
            build_steps.append(_step("weekly_plan_draft", [
                py,
                str(SCRIPTS_DIR / "weekly_plan_generator.py"),
                "--write-draft",
                *date_args,
            ], required=False))

        # Friday (weekday 4): auto-generate weekly review scorecard draft.
        # Uses weekly_review_generator.py --write-draft. Scores each outcome,
        # surfaces win/loss/pattern analysis in brief. Todd confirms via /weekly-review confirm.
        if today.weekday() == 4:  # Friday
            build_steps.append(_step("weekly_review_draft", [
                py,
                str(SCRIPTS_DIR / "weekly_review_generator.py"),
                "--write-draft",
                *date_args,
            ], required=False))
        # RB-2026-08-28: strategic-assessment priority item #5 ("seeing
        # what the CEO doesn't see"), v1. Runs signal_synthesis.py's
        # already-tested cross-store pattern engine across active Blue
        # Sheet accounts, tracked competitors, and the full watchlist;
        # writes its result before render_intelligence_brief reads it
        # (I+: Entity Signal Convergence). Detection only, same discipline
        # as mutation_reconciliation/self_audit_sweep above -- no writes to
        # any other artifact.
        build_steps.append(_step("entity_convergence_scan", [
            py, str(SCRIPTS_DIR / "entity_convergence_scan.py"),
        ], required=False))
        build_steps.append(_step("anticipatory_signal_synthesis", [
            py, str(SCRIPTS_DIR / "anticipatory_signal_synthesis.py"),
            *date_args,
        ], required=False))

        # RB-2026-09-11: item 3 of the M&A/exec-moves/relationship-intel
        # scoping (the other two became ownership_promotion.py and
        # executive_move_promotion.py). Cross-references every untiered
        # baseline contact's current_company against tracked
        # customers_prospects accounts and ecosystem_intelligence.json
        # vendor/brand entities -- surfaces dormant contacts who now work
        # somewhere relevant. Detection only, no mutation; writes its
        # result before render_daily_brief reads it (relationship-
        # intelligence cluster, next to Notable Contact Moves).
        build_steps.append(_step("relationship_reactivation_scan", [
            py, str(SCRIPTS_DIR / "relationship_reactivation_scan.py"),
        ], required=False))

        # ── RB-10.7: Pre-render Intelligence Brief and Daily Brief ───────────
        # Runs after daily_brief.py has written the cache. Produces
        # system/briefs/YYYY-MM-DD-{intelligence,daily}-brief.md so the GPT
        # can fetch and display pre-rendered content instead of generating it.
        build_steps.append(_step("render_intelligence_brief", [
            py,
            str(SCRIPTS_DIR / "render_intelligence_brief.py"),
            *date_args,
        ], required=False))
        build_steps.append(_step("render_daily_brief", [
            py,
            str(SCRIPTS_DIR / "render_daily_brief.py"),
            *date_args,
        ], required=False))
        # RB-DEFECT-067: semantic post-build check -- proves weekly_plan.json,
        # the daily_brief cache, today's rendered .md, and the published
        # snapshot all agree on which week is active, rather than trusting
        # that the render steps above succeeding means they're consistent
        # with each other. required=False (same as every other step in this
        # block): a detected mismatch is surfaced in the pipeline log for
        # review, not a hard block on brief delivery -- whether a genuine
        # inconsistency should block sending is a product-policy decision
        # (see the defect report's "Monday adoption contract" question)
        # deliberately left to Todd rather than decided here.
        build_steps.append(_step("verify_weekly_plan_consistency", [
            py,
            str(SCRIPTS_DIR / "verify_weekly_plan_consistency.py"),
            *date_args,
        ], required=False))

        # RB-2026-08-23: the actual quality gate. Runs AFTER the brief is
        # built and rendered (it needs the real artifacts to check) but
        # BEFORE either send step -- unlike _check_brief_readiness (RB-
        # DEFECT-017) above, whose result never blocked anything because it
        # only ran after delivery. required=True: a failing gate makes
        # required_ok False, which the existing result["ok"]/exit-code
        # plumbing below already propagates -- no new wiring needed there.
        #
        # RB-DEFECT-072 (2026-09-23): a real run generated both briefs
        # correctly, then aborted delivery entirely on two findings that
        # were each safely, deterministically repairable (a same-event
        # duplicate headline pair; a delivery-check snapshot embedded before
        # today's own publish step had run) -- the gate had no path from
        # "found a repairable defect" to "repaired it and continued." This
        # is that path: a bounded repair-and-revalidate loop. A finding only
        # gets an automatic repair attempt when brief_acceptance_check.py
        # itself marked it safe_to_auto_repair -- an unrepairable or unsafe
        # failure still goes straight to the failure alert, unchanged from
        # before.
        gate_outcome = _run_acceptance_gate_with_repair(py, today, date_args, build_steps)
        gate_passed = gate_outcome["gate_passed"]
        brief_acceptance_status = gate_outcome["brief_acceptance_status"]
        intelligence_clean = gate_outcome["intelligence_clean"]
        daily_clean = gate_outcome["daily_clean"]

        if gate_passed or intelligence_clean:
            build_steps.append(_step("send_intelligence_brief_email", [
                py,
                str(SCRIPTS_DIR / "send_brief_email.py"),
                "--part", "intelligence",
                *date_args,
            ], required=False))
        if gate_passed or daily_clean:
            build_steps.append(_step("send_daily_brief_email", [
                py,
                str(SCRIPTS_DIR / "send_brief_email.py"),
                "--part", "daily",
                *date_args,
            ], required=False))
        if not gate_passed:
            # brief_acceptance_status is already "partially_delivered" here
            # when intelligence_clean/daily_clean applied -- computed inside
            # _run_acceptance_gate_with_repair, not re-derived here.
            build_steps.append(_step("send_failure_alert", [
                py,
                str(SCRIPTS_DIR / "send_failure_alert.py"),
                *date_args,
            ], required=False))

        # ── RB-DEFECT-2026-07-10h: Team-facing Intelligence Brief edition ────
        # Industry news only (no personal content) -- see render_intelligence_
        # brief.render_team_edition. Emailed to Todd on the same cycle as the
        # personal briefs; he decides whether/how to forward it to his team.
        # Tuesday/Friday only (weekday 1/4): the team edition now accumulates
        # restaurant industry/tech headlines since the last published edition
        # rather than rendering a fresh cut every day, so it's only built and
        # sent on its actual publish days.
        if today.weekday() in (1, 4):  # Tuesday, Friday
            build_steps.append(_step("render_team_intelligence_brief", [
                py,
                str(SCRIPTS_DIR / "render_intelligence_brief.py"),
                "--team",
                *date_args,
            ], required=False))
            build_steps.append(_step("send_team_intelligence_brief_email", [
                py,
                str(SCRIPTS_DIR / "send_brief_email.py"),
                "--team",
                *date_args,
            ], required=False))

        if send_backup_email:
            build_steps.append(_step("send_backup_email", [
                py,
                str(SCRIPTS_DIR / "publish.py"),
                "--send",
                "--confirm",
                *date_args,
            ], required=False))

    # ── RB-DEFECT-017: Pre-brief intelligence readiness check ───────────────
    # After scan, before build: validate required sources, attempt recovery on
    # any that failed, and record the readiness result in the pipeline output.
    # This step runs in "full" and "brief_only" modes.
    readiness_result: dict = {"checked": False}
    if mode in ("full", "brief_only"):
        try:
            readiness_result = _check_brief_readiness(py)
        except Exception:  # noqa: BLE001
            readiness_result = {"checked": False, "error": "readiness check failed"}

    steps = scan_steps + build_steps

    required_ok = all(s["status"] == "pass" for s in steps if s["required"])
    latest_brief = PUBLISHED_DIR / "latest_brief.json"
    latest_html = PUBLISHED_DIR / "latest.html"
    # scan_only runs don't produce a brief — treat as ok if all required steps passed
    brief_ready = (latest_brief.exists() and latest_html.exists()) if mode != "scan_only" else True
    prior_scan = {}
    if mode == "brief_only":
        candidate = _load_json(SCAN_CACHE_PATH)
        if candidate.get("date") == today.isoformat():
            prior_scan = candidate

    delivery_ok = _compute_delivery_ok(steps)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": today.isoformat(),
        "mode": mode,
        "ok": required_ok and brief_ready and delivery_ok,
        "delivery_ok": delivery_ok,
        # RB-DEFECT-072: one of "not_applicable" (scan_only), "passed_first_attempt",
        # "repaired_and_delivered", or "failed_after_repair_exhausted" -- see
        # the brief_acceptance_gate repair-and-revalidate loop above. Distinct
        # from "ok": "ok" can still be False even when this is
        # "repaired_and_delivered" if some other required step failed.
        "brief_acceptance_status": brief_acceptance_status,
        "steps": steps,
        # RB-DEFECT-017: Pre-brief intelligence readiness check result
        "intelligence_readiness": readiness_result,
        "published": _published_paths(today),
        "chatgpt_contract": {
            "role": "ChatGPT renders and notifies from the prebuilt RB artifact.",
            "primary_action": "GET /daily_brief?use_cache=true",
            "local_source_of_truth": "system/published/daily/latest_brief.json",
            "full_artifact": "system/published/daily/latest.html",
            "native_task_should_not": [
                "research news live",
                "invent source freshness",
                "rewrite canonical state",
                "perform writes without confirmation",
            ],
        },
    }
    result["pre_brief_scan"] = prior_scan or None
    result["execution_report"] = build_execution_report(result)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if mode == "scan_only":
        SCAN_CACHE_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    # ── RB-INTEL-021: Append collection scan record to audit log ─────────────
    # Writes a durable, queryable scan receipt so the user can answer
    # "did the scan run today?" and "what sources were scanned?" from
    # recorded system activity — not inference.
    try:
        er = result["execution_report"]
        dashboard = er.get("intelligence_health_dashboard") or {}
        sources_summary = dashboard.get("sources") or []
        healthy_count = sum(1 for s in sources_summary if s.get("status") == "Healthy")
        failed_count = sum(1 for s in sources_summary if s.get("status") in {"Failed", "Partial"})
        unavail_count = sum(1 for s in sources_summary if s.get("status") == "Unavailable")
        total_sources = len(sources_summary)

        # Derive collection window: scan started at result["generated_at"] minus
        # cumulative step duration. Use step timestamps when available.
        all_steps = list(result.get("steps") or [])
        if all_steps:
            first_step = all_steps[0].get("result") or {}
            collection_window_start = first_step.get("started_at") or result["generated_at"]
        else:
            collection_window_start = result["generated_at"]
        collection_window_end = result["generated_at"]

        scan_record = {
            "event_type": "collection_scan_completed",
            "timestamp": result["generated_at"],
            "event_id": f"SCAN-{result['date'].replace('-','')}-{mode.upper()}",
            "date": result["date"],
            "mode": mode,
            "collection_window_start": collection_window_start,
            "collection_window_end": collection_window_end,
            "refresh_status": er.get("refresh_status"),
            "sources_attempted": total_sources,
            "sources_healthy": healthy_count,
            "sources_failed": failed_count,
            "sources_unavailable": unavail_count,
            "sources_scanned": er.get("sources_processed") or [],
            "records_processed": er.get("records_processed") or 0,
            "records_added": er.get("records_added") or 0,
            "records_changed": er.get("records_changed") or 0,
            "mutations_generated": er.get("mutations_generated") or 0,
            "opportunities_generated": er.get("opportunities_generated") or 0,
            "relationship_changes": er.get("relationship_changes") or 0,
            "trust_score": er.get("trust_score"),
            "trust_level": er.get("trust_level"),
            "stale_sources": er.get("stale_sources") or [],
            "processing_errors": er.get("processing_errors") or [],
            "brief_rebuilt": er.get("brief_rebuilt", False),
            "collection_health": (
                "healthy" if er.get("refresh_status") == "Success"
                else ("degraded" if er.get("refresh_status") == "Partial" else "failed")
            ),
            # Per-source detail row for audit traceability
            "source_detail": [
                {
                    "source": s.get("source"),
                    "source_key": s.get("source_key"),
                    "status": s.get("status"),
                    "records_processed": s.get("records_processed"),
                    "new_signals": s.get("new_signals"),
                    "mutations_detected": s.get("mutations_detected"),
                    "trust_score": s.get("trust_score"),
                    "last_refresh": s.get("last_refresh"),
                    "failure_reason": s.get("failure_reason"),
                }
                for s in sources_summary
            ],
        }
        append_collection_scan_record(SYSTEM_DIR, scan_record)
    except Exception:  # noqa: BLE001
        pass  # Audit log write is best-effort — never block pipeline completion

    return result


def _print_result(result: dict) -> None:
    for step in result["steps"]:
        print(f"[{step['status'].upper()}] {step['name']}")
        if step["status"] != "pass":
            stderr = (step["result"].get("stderr_tail") or "").strip()
            stdout = (step["result"].get("stdout_tail") or "").strip()
            print(stderr or stdout or f"returncode={step['result']['returncode']}")
    print()
    print(f"morning pipeline: {'PASS' if result['ok'] else 'FAIL'}")
    if not result.get("delivery_ok", True):
        print("  -> FAIL reason: brief was built but NOT delivered (all email sends failed)")
    print(f"refresh status:   {result['execution_report']['refresh_status']}")
    print(f"canonical brief: {result['published']['latest_brief_json']}")
    print(f"full artifact:    {result['published']['latest_html']}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", help="ISO date to build. Default: today.")
    p.add_argument("--send-backup-email", action="store_true",
                   help="Send the backup email after artifacts publish. Non-fatal if transport is unavailable.")
    # RB-2026-08-24: flipped to default-on per Todd's explicit decision.
    # --no-confirm-passive-ri is the escape hatch.
    p.add_argument("--confirm-passive-ri", dest="confirm_passive_ri", action="store_true", default=True,
                   help="Apply newly proposed high-confidence passive RI during refresh (default: on).")
    p.add_argument("--no-confirm-passive-ri", dest="confirm_passive_ri", action="store_false",
                   help="Disable auto-applying passive RI; leave it pending only.")
    p.add_argument("--json", action="store_true", help="Emit JSON summary.")
    # Two-phase scheduling flags
    mode_group = p.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--scan-only", action="store_true",
        help=(
            "Gather phase only: fetch Google accounts, refresh intelligence caches, "
            "run intelligence_assessment.  Does NOT build or publish the brief.  "
            "Intended for the 4 AM pre-scan LaunchAgent — data is warm in cache "
            "when the 5 AM brief-only job runs."
        ),
    )
    mode_group.add_argument(
        "--brief-only", action="store_true",
        help=(
            "Build/publish phase only: skip the gather phase and use already-cached "
            "data from a prior --scan-only run.  Falls back to a full scan if "
            "today's intelligence_assessment cache is missing or stale."
        ),
    )
    args = p.parse_args()

    mode = "scan_only" if args.scan_only else ("brief_only" if args.brief_only else "full")
    today = date.fromisoformat(args.date) if args.date else date.today()
    result = run_pipeline(
        today=today,
        send_backup_email=args.send_backup_email,
        confirm_passive_ri=args.confirm_passive_ri,
        mode=mode,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_result(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
