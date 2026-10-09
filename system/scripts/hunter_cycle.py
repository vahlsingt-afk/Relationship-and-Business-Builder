#!/usr/bin/env python3
"""End-to-end Hunter cycle preparation and returned-packet intake.

Preparation creates a portable Chat Deep Research job plus an immutable before
snapshot. Finalization validates and compares the returned packet, then invokes
the governed dispatcher in dry-run mode unless --confirm is explicit.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import hunter  # noqa: E402
import hunter_change_dispatch  # noqa: E402
import hunter_snapshot  # noqa: E402
import hunter_orchestrator as ho  # noqa: E402

GATHERER_QUEUE_PATH = SCRIPTS_DIR.parent / ".cache" / "gatherer_hunter_escalations.jsonl"
import hunter_packet_normalize  # noqa: E402

# Deep Research is the only research engine for Hunter. Browser safety must
# not be bypassed to inject local job contents into a remote page. Codex owns
# deterministic preparation, validation, and intake; `sweep()` consumes only
# a packet returned through a supported safe transport.
PENDING_JOBS_DIR = SCRIPTS_DIR.parent / ".cache" / "hunter_pending_jobs"
PACKETS_INBOX_DIR = SCRIPTS_DIR.parent / "inbox" / "hunter_packets"
PROCESSED_JOBS_DIR = PENDING_JOBS_DIR / "processed"
PROCESSED_PACKETS_DIR = PACKETS_INBOX_DIR / "processed"

# 2026-10-03 (CLAUDE_HANDOFF_RB_HUNTER_GATHERER_END_TO_END_DEFECTS):
# confirmed live -- 9 jobs sat pending with zero completions while the
# hourly automation kept queueing more, because queue_prepare() refused
# only a *duplicate* target, never a ceiling across *different* targets.
# "Recommended initial ceiling: three total pending jobs, or one pending
# job per enabled research family, whichever is smaller" -- enforced as
# two independent caps in queue_prepare() below; either tripping refuses
# the write.
#
# RB-DEFECT-2026-10-09: raised from 3 to 6. That ceiling was sized for a
# single engine working one job at a time; with three independent Hunter
# engines now dispatching concurrently (chatgpt_deep_research, chatgpt_work,
# claude_code_headless), each holding its own in-flight job, 3 left
# essentially no room for prepare-priority --queue to stage the next
# target while even two engines were still out -- confirmed live: with
# two real jobs already leased, the next hunter_drive_assignment_export.py
# run hit pending_ceiling_reached on its second prepare-priority call and
# chatgpt_work got nothing. 6 gives each of the three engines its own
# in-flight slot plus one spare for staging the next target. The
# PENDING_JOB_CEILING_TOTAL-dependent tests in test_hunter_cycle_queue_sweep.py
# pin this back down to 3 themselves -- they test the enforcement logic, not
# this specific value, so they don't need updating when this number does.
QUARANTINED_JOBS_DIR = PENDING_JOBS_DIR / "quarantined"
PENDING_JOB_CEILING_TOTAL = 6
PENDING_JOB_CEILING_PER_FAMILY = 1

# 2026-10-03: real runs (Tim Hortons, then KFC) confirmed Codex does not
# reliably save a returned packet into PACKETS_INBOX_DIR as instructed --
# both landed instead in the pre-existing, well-known system/inbox/
# chatgpt_intelligence_drop/ folder (one as a ChatGPT-Library-exported
# generic "Deep Research report(...)" file, not even the right filename).
# Rather than keep fighting that habit, sweep() also watches this folder.
# It is a busy, general-purpose drop used by many non-Hunter cycles too
# (zips, unrelated topic .md/.json), so scanning it is deliberately
# narrower: only .json/.md files, and a file only ever gets touched here
# if load_packet_artifact can parse it AND its targets match a real
# queued job -- everything else (the overwhelming majority of this
# folder) is left completely alone, same "never guess, never discard" the
# PACKETS_INBOX_DIR sweep already followed.
LEGACY_PACKETS_INBOX_DIR = SCRIPTS_DIR.parent / "inbox" / "chatgpt_intelligence_drop"
LEGACY_PROCESSED_PACKETS_DIR = LEGACY_PACKETS_INBOX_DIR / "processed"
QUEUE_PATH = SCRIPTS_DIR.parent / ".cache" / "hunter_priority_queue.json"
BUNDLE_SCHEMA = "rb.hunter_research_bundle_response.v1"


def _target_slug(target_key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", target_key.lower()).strip("-") or "target"


def _target_family(target_key: str) -> str:
    """The research family a target belongs to, derived from its key
    prefix (e.g. "company:brand-kfc" -> "company"). Used only for the
    per-family pending-job ceiling -- never persisted as a new taxonomy
    elsewhere, since no such concept exists anywhere else in this
    codebase (confirmed 2026-10-03)."""
    prefix, sep, _ = target_key.partition(":")
    return prefix if sep else "unknown"


def _pending_job_files() -> list[Path]:
    """Every top-level pending-job file -- excludes processed/ and
    quarantined/ subdirectories, which is what makes those two actually
    free up ceiling room once a job leaves this count."""
    if not PENDING_JOBS_DIR.is_dir():
        return []
    return [p for p in PENDING_JOBS_DIR.iterdir() if p.is_file() and p.suffix == ".json"]


def _read_queue() -> dict:
    try:
        return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _queue_completed_targets(queue: dict) -> set[str]:
    """Targets completed through a valid DR packet after this queue snapshot."""
    generated = queue.get("generated_at")
    if not generated or not PROCESSED_PACKETS_DIR.is_dir():
        return set()
    completed = set()
    for path in PROCESSED_PACKETS_DIR.iterdir():
        if not path.is_file() or path.suffix.lower() not in {".json", ".md", ".txt"}:
            continue
        receipt_path = path.with_name(path.name + ".receipt.json")
        try:
            packet = load_packet_artifact(path)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if packet.get("schema") == BUNDLE_SCHEMA and receipt.get("ok"):
            successful_children = {row.get("target_key") for row in receipt.get("child_receipts") or [] if (row.get("receipt") or {}).get("ok")}
            for child in packet.get("packets") or []:
                child_targets = {t.get("target_key") for t in child.get("targets") or [] if isinstance(t, dict) and t.get("target_key")}
                tier = (child.get("resource_usage") or {}).get("selected_execution_tier")
                when = child.get("completed_at") or ""
                if child_targets & successful_children and tier in {"chatgpt_deep_research_economy", "deep_public_source_research"} and when >= generated:
                    completed.update(child_targets)
        else:
            tier = (packet.get("resource_usage") or {}).get("selected_execution_tier")
            when = packet.get("completed_at") or ""
            if receipt.get("ok") and tier in {"chatgpt_deep_research_economy", "deep_public_source_research"} and when >= generated:
                completed.update(t.get("target_key") for t in packet.get("targets") or [] if isinstance(t, dict) and t.get("target_key"))
    return completed


def _pending_target_keys() -> set[str]:
    keys = set()
    for path in _pending_job_files():
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        keys.update((job.get("directive", {}).get("packet_requirements", {}) or {}).get("target_keys") or [])
        for subjob in job.get("subjobs") or []:
            if subjob.get("target_key"):
                keys.add(subjob["target_key"])
    return keys


def prepare_priority_bundle(*, queue: dict | None = None, transport_available: bool = False) -> dict:
    """Prepare the first eligible CoS row, pairing a brand with its discovery row."""
    queue = queue or _read_queue()
    rows = queue.get("queue") or []
    if not rows:
        raise ValueError("CoS Hunter priority queue is missing or empty; regenerate it through the RBB pipeline")
    generated = queue.get("generated_at")
    try:
        generated_dt = datetime.fromisoformat(str(generated).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("CoS Hunter priority queue has no valid generated_at timestamp") from error
    if generated_dt.tzinfo is None:
        generated_dt = generated_dt.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - generated_dt > timedelta(hours=48):
        raise ValueError("CoS Hunter priority queue is older than 48 hours; regenerate it through the RBB pipeline")
    pending = _pending_target_keys()
    completed = _queue_completed_targets(queue)
    consumed: set[str] = set()
    brand_by_slug = {}
    discovery_by_slug = {}
    for row in rows:
        key = row.get("target_key", "")
        if key.startswith("company:"):
            brand_by_slug[key.split(":", 1)[1]] = row
        elif key.startswith("franchise-discovery:"):
            discovery_by_slug[key.split(":", 1)[1]] = row

    selected = None
    companion = None
    for row in rows:
        key = row.get("target_key")
        if not key or key in consumed:
            continue
        if key in pending or key in completed:
            consumed.add(key)
            continue
        if key.startswith("company:"):
            slug = key.split(":", 1)[1]
            possible = discovery_by_slug.get(slug)
            selected = row
            if possible and possible.get("target_key") not in pending | completed:
                companion = possible
            break
        elif key.startswith("franchise-discovery:"):
            slug = key.split(":", 1)[1]
            possible = brand_by_slug.get(slug)
            selected = row
            if possible and possible.get("target_key") not in pending | completed:
                companion = possible
            break
        else:
            selected = row
            break
    if not selected:
        return {"schema": "rb.hunter_priority_assignment.v1", "status": "no_eligible_target", "queue_generated_at": queue.get("generated_at"), "pending_job_count": len(_pending_job_files()), "completed_targets": sorted(completed)}

    if companion:
        targets = [selected["target_key"], companion["target_key"]]
        assignment_id = "priority-" + _target_slug("-".join(targets))
        subjobs = []
        for row in (selected, companion):
            job = prepare(row["suggested_playbook"], universe="all", target_keys=[row["target_key"]], limit=1, deep_research_available=transport_available)
            subjobs.append({"target_key": row["target_key"], "rank": row.get("rank"), "suggested_playbook": row.get("suggested_playbook"), "job": job})
        return {
            "schema": "rb.hunter_priority_assignment.v1", "status": "prepared_local_assignment",
            "queue_generated_at": queue.get("generated_at"), "queue_ranks": [selected.get("rank"), companion.get("rank")],
            "target_keys": targets, "display_name": selected.get("display_name"),
            "bundle_schema": BUNDLE_SCHEMA, "assignment_id": assignment_id, "subjobs": subjobs,
            "pending_job_count": len(_pending_job_files()),
            "transport_required": True, "research_authorized": transport_available,
            "submission_note": "One ChatGPT Deep Research request must research both subjobs as one brand work unit. Return one outer bundle response with one ordinary Hunter packet per subjob. No packet is active until safely returned and swept locally."
        }
    job = prepare(selected["suggested_playbook"], target_keys=[selected["target_key"]], limit=1, deep_research_available=transport_available)
    return {
        "schema": "rb.hunter_priority_assignment.v1", "status": "prepared_local_assignment",
        "queue_generated_at": queue.get("generated_at"), "queue_ranks": [selected.get("rank")],
        "target_keys": [selected["target_key"]], "display_name": selected.get("display_name"),
        "bundle_schema": None, "subjobs": [{"target_key": selected["target_key"], "rank": selected.get("rank"), "suggested_playbook": selected.get("suggested_playbook"), "job": job}],
        "pending_job_count": len(_pending_job_files()), "transport_required": True, "research_authorized": transport_available,
        "submission_note": "Run the single suggested ChatGPT Deep Research request and return its ordinary Hunter packet through supported safe transport. No packet is active until safely returned and swept locally."
    }


def _write_job(job: dict, target_key: str, *, overwrite: bool = False) -> Path | None:
    PENDING_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    path = PENDING_JOBS_DIR / f"{_target_slug(target_key)}.json"
    if path.exists() and not overwrite:
        return None
    path.write_text(json.dumps(job, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _is_bundle_response(value: dict) -> bool:
    return value.get("schema") == BUNDLE_SCHEMA and isinstance(value.get("packets"), list)


def _finalize_bundle(assignment: dict, response: dict, *, confirm: bool = False) -> dict:
    packets = response.get("packets") or []
    subjobs = assignment.get("subjobs") or []
    by_target = {row.get("target_key"): row for row in subjobs}
    receipts = []
    seen_targets = set()
    for child in packets:
        targets = child.get("targets") or []
        keys = [t.get("target_key") if isinstance(t, dict) else t for t in targets]
        if len(keys) != 1 or keys[0] not in by_target or keys[0] in seen_targets:
            return {"schema": "rb.hunter_cycle_receipt.v1", "packet_id": response.get("bundle_id"), "ok": False, "confirmed": confirm, "bundle_error": f"unexpected or duplicate child packet target(s): {keys}"}
        seen_targets.add(keys[0])
        receipt = finalize(by_target[keys[0]]["job"], child, confirm=confirm)
        receipts.append({"target_key": keys[0], "receipt": receipt})
    missing = set(by_target) - {row["target_key"] for row in receipts}
    return {"schema": "rb.hunter_cycle_receipt.v1", "packet_id": response.get("bundle_id"), "ok": not missing and bool(receipts) and all(row["receipt"].get("ok") for row in receipts), "confirmed": confirm, "bundle": True, "missing_targets": sorted(missing), "child_receipts": receipts}


def queue_prepare(playbook: str, **kwargs) -> dict:
    """Same as prepare(), but persists the job to PENDING_JOBS_DIR keyed
    by its first selected target, instead of (or in addition to) an
    --output path the caller may also give. One pending job per target --
    a target already queued is left untouched rather than silently
    overwritten, so a human hasn't lost work by re-running prepare before
    submitting the first one.

    Also enforces PENDING_JOB_CEILING_TOTAL and PENDING_JOB_CEILING_PER_
    FAMILY (2026-10-03, Defect 2) -- counted fresh per target so that
    preparing N targets in one call can partially succeed (fills
    remaining ceiling room, then reports the rest as ceiling_reached)
    rather than all-or-nothing."""
    job = prepare(playbook, **kwargs)
    selected = (job.get("directive", {}).get("packet_requirements", {}) or {}).get("target_keys") or []
    research_authorized = (job.get("directive", {}).get("packet_requirements", {}) or {}).get("research_authorized") is True
    if not research_authorized:
        return {
            "job": job,
            "target_keys": selected,
            "queued_paths": [],
            "skipped_existing_targets": [],
            "skipped_ceiling": [],
            "ceiling_reached": False,
            "transport_blocked": True,
            "submission_note": "ChatGPT Deep Research transport is unavailable; no target was queued. Run sweep, report pending-job count and the concrete blocker, and stop.",
        }
    PENDING_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    queued_paths = []
    skipped_existing = []
    skipped_ceiling = []
    for target_key in selected:
        path = PENDING_JOBS_DIR / f"{_target_slug(target_key)}.json"
        if path.exists():
            skipped_existing.append(target_key)
            continue
        pending = _pending_job_files()
        pending_targets = len(_pending_target_keys())
        family = _target_family(target_key)
        family_count = sum(1 for p in pending if _target_family_of_file(p) == family)
        if pending_targets >= PENDING_JOB_CEILING_TOTAL or family_count >= PENDING_JOB_CEILING_PER_FAMILY:
            skipped_ceiling.append({
                "target_key": target_key,
                "family": family,
                "pending_total": pending_targets,
                "pending_total_ceiling": PENDING_JOB_CEILING_TOTAL,
                "pending_family": family_count,
                "pending_family_ceiling": PENDING_JOB_CEILING_PER_FAMILY,
            })
            continue
        path.write_text(json.dumps(job, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        queued_paths.append(str(path))
    return {
        "job": job,
        "target_keys": selected,
        "queued_paths": queued_paths,
        "skipped_existing_targets": skipped_existing,
        "skipped_ceiling": skipped_ceiling,
        "ceiling_reached": bool(skipped_ceiling),
        "transport_blocked": False,
        "submission_note": (
            "Submit the approved research request in the Relationship & Business Builder ChatGPT project. "
            "ChatGPT Deep Research must use the CoS-ranked queue and project files, then return its "
            "evidence-native response through a supported safe transport. Do not transmit raw local "
            "job contents into a browser page or bypass a safety refusal. Save the returned packet "
            f"locally into {PACKETS_INBOX_DIR} and run `sweep()`. If safe submission or local capture "
            "is unavailable, stop without queuing another target."
        ),
    }


def _target_family_of_file(path: Path) -> str:
    """Recover a pending job file's target family from its own stored
    directive, falling back to the filename if the directive can't be
    read -- so a malformed file never crashes the ceiling check."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        keys = (data.get("directive", {}).get("packet_requirements", {}) or {}).get("target_keys") or []
        if keys:
            return _target_family(keys[0])
    except (OSError, ValueError):
        pass
    return "unknown"


def quarantine_stale_jobs(*, max_age_hours: int = 48) -> list[dict]:
    """Move a pending job older than max_age_hours into QUARANTINED_JOBS_
    DIR (2026-10-03, Defect 1, scoped). sweep() already moves a job to
    processed/ on ANY finalize call, success or validation-failure -- so
    a job only ever sits pending forever in exactly one case: no packet
    ever arrived at all (a true transport failure, not a validation
    retry). This is the bounded, visible fate for that case: never
    deleted, never silently retried forever, and freed from the pending-
    job ceiling count so the automation can queue its next real target."""
    QUARANTINED_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - (max_age_hours * 3600)
    quarantined = []
    for path in _pending_job_files():
        mtime = path.stat().st_mtime
        if mtime > cutoff:
            continue
        age_hours = (time.time() - mtime) / 3600
        dest = QUARANTINED_JOBS_DIR / path.name
        note_path = QUARANTINED_JOBS_DIR / f"{path.stem}.quarantine.json"
        note_path.write_text(json.dumps({
            "quarantined_at": datetime.now(timezone.utc).isoformat(),
            "reason": "transport_blocked_stale",
            "age_hours": round(age_hours, 1),
            "max_age_hours": max_age_hours,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        path.rename(dest)
        quarantined.append({"path": str(dest), "age_hours": round(age_hours, 1)})
    return quarantined


def sweep(*, confirm: bool = False, quarantine_max_age_hours: int = 48) -> dict:
    """Matches every packet file across PACKETS_INBOX_DIR and the legacy
    LEGACY_PACKETS_INBOX_DIR to its queued job in PENDING_JOBS_DIR by
    target key, finalizes matched pairs, and archives both files under
    their respective processed/ subfolders. A packet with no matching
    queued job is left in place (never guessed at or discarded) and
    reported as unmatched -- true for every file in the legacy folder
    that isn't actually a Hunter packet, which is most of them.

    Runs quarantine_stale_jobs() first (2026-10-03, Defect 1/2) so a job
    that never received a packet at all ages out of the pending-job
    ceiling count on every sweep, not just when someone remembers to run
    it separately."""
    PENDING_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    quarantined = quarantine_stale_jobs(max_age_hours=quarantine_max_age_hours)

    # Built here, not as a module-level constant, so a caller (or a test)
    # that reassigns PACKETS_INBOX_DIR/LEGACY_PACKETS_INBOX_DIR etc. on
    # this module is actually honored.
    source_dirs = (
        (PACKETS_INBOX_DIR, PROCESSED_PACKETS_DIR, None),
        (LEGACY_PACKETS_INBOX_DIR, LEGACY_PROCESSED_PACKETS_DIR, {".json", ".md"}),
    )

    results = []
    unmatched = []
    for source_dir, processed_dir, extensions in source_dirs:
        source_dir.mkdir(parents=True, exist_ok=True)
        processed_dir.mkdir(parents=True, exist_ok=True)
        for packet_path in sorted(source_dir.iterdir()):
            if not packet_path.is_file() or packet_path.parent != source_dir:
                continue
            if extensions is not None and packet_path.suffix.lower() not in extensions:
                continue
            try:
                packet = load_packet_artifact(packet_path)
            except ValueError as error:
                if extensions is None:  # the dedicated inbox: every file here is meant to be a packet
                    unmatched.append({"packet_path": str(packet_path), "error": str(error)})
                continue  # the legacy drop: most files genuinely aren't packets -- not an error, just skip
            is_bundle = _is_bundle_response(packet)
            targets = packet.get("target_keys") or [] if is_bundle else packet.get("targets") or []
            if not is_bundle:
                targets = [t.get("target_key") if isinstance(t, dict) else t for t in targets]
            job_path = None
            if is_bundle:
                assignment_id = packet.get("assignment_id")
                candidate = PENDING_JOBS_DIR / f"bundle-{_target_slug(assignment_id or '')}.json"
                if candidate.exists() and candidate.stat().st_mtime <= packet_path.stat().st_mtime:
                    job_path = candidate
            for target_key in targets if job_path is None else []:
                candidate = PENDING_JOBS_DIR / f"{_target_slug(target_key)}.json"
                # RB live incident, 2026-10-03: a years-old, wholly
                # unrelated "candidate-validation" file sitting in the
                # legacy drop folder happened to carry a `targets` array
                # that included "company:brand-chipotle-mexican-grill" --
                # coincidence, not a real reply -- and matched a job
                # queued that same day, silently consuming it. A real
                # reply cannot predate the question it answers, so a
                # candidate older than the job it would match is never a
                # real match, in either source folder.
                if candidate.exists() and candidate.stat().st_mtime <= packet_path.stat().st_mtime:
                    job_path = candidate
                    break
            if job_path is None:
                if extensions is None:
                    unmatched.append({"packet_path": str(packet_path), "targets": targets, "error": "no queued job matches these targets (or the only match is older than this file, so can't be its answer)"})
                continue
            job = json.loads(job_path.read_text(encoding="utf-8"))
            receipt = _finalize_bundle(job, packet, confirm=confirm) if is_bundle else finalize(job, packet, confirm=confirm)
            # RB-DEFECT-2026-10-09: tell hunter_orchestrator.py's lease ledger
            # what sweep just decided -- best-effort, never fatal to the real
            # intake this loop exists for. A sync failure is recorded on the
            # receipt for visibility, not raised, so one bad ledger lookup
            # can't block a real packet from being processed.
            sync_state = None
            try:
                sync_state = ho.sync_from_sweep(job, confirm=confirm, ok=bool(receipt.get("ok")))
            except Exception as error:  # noqa: BLE001
                receipt["orchestrator_sync_error"] = str(error)
            results.append({"packet_path": str(packet_path), "job_path": str(job_path), "receipt": receipt})
            archived_packet = processed_dir / packet_path.name
            archived_receipt = archived_packet.with_name(archived_packet.name + ".receipt.json")
            archived_receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            packet_path.rename(archived_packet)
            if sync_state and sync_state.get("state") == "retryable":
                # Leave the job file in place -- archiving it here would
                # strand a job the ledger just marked retryable with nothing
                # left to match a corrected resubmission against. Confirmed
                # live: recovering from exactly this required manually moving
                # a job file back out of processed/ by hand.
                pass
            else:
                job_path.rename(PROCESSED_JOBS_DIR / job_path.name)
    return {"schema": "rb.hunter_sweep_result.v1", "confirmed": confirm, "processed": results, "unmatched": unmatched, "quarantined": quarantined}


def load_packet_artifact(path: str | Path) -> dict:
    """Load Hunter evidence from JSON, plain text, or a Markdown code fence.

    ChatGPT's transport artifact is deliberately format-agnostic. The validated
    canonical packet is JSON, but browser capture must not depend on ChatGPT
    successfully creating a downloadable ``.json`` attachment.
    """
    text = Path(path).read_text(encoding="utf-8-sig").strip()
    if not text:
        raise ValueError("Hunter response artifact is empty")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as direct_error:
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.IGNORECASE | re.DOTALL)
        candidates = [fenced.group(1)] if fenced else []
        candidates.extend(text[index:] for index, char in enumerate(text) if char == "{")
        decoder = json.JSONDecoder()
        value = None
        for candidate in candidates:
            try:
                decoded, _ = decoder.raw_decode(candidate.lstrip())
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                value = decoded
                break
        if value is None:
            raise ValueError(
                f"Hunter response artifact contains no valid JSON object: {direct_error.msg}"
            ) from direct_error
    if not isinstance(value, dict):
        raise ValueError("Hunter response artifact must contain one JSON object")
    return value


def prepare(playbook: str, **kwargs) -> dict:
    directive = hunter.prepare_cycle(playbook, **kwargs)
    snapshot = hunter_snapshot.from_directive(directive)
    boundary = "ChatGPT Deep Research performs all public-source research in the Relationship & Business Builder project and returns one evidence-native JSON object through supported safe local transport; Codex performs deterministic local preparation, validation, and intake only."
    return {
        "schema": "rb.hunter_cycle_job.v1",
        "execution_boundary": boundary,
        "directive": directive,
        "before_snapshot": snapshot,
    }


def finalize(job: dict, packet: dict, *, confirm: bool = False) -> dict:
    packet, normalization = hunter_packet_normalize.normalize(job, packet)
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
        "normalization": normalization,
        "validation": validation,
        "change_comparison": comparison,
        "dispatch": dispatch,
    }


def prepare_gatherer_escalation(*, queue_path: Path = GATHERER_QUEUE_PATH,
                                change_id: str | None = None) -> dict:
    candidates = []
    if queue_path.exists():
        for line in queue_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("status") == "ready_for_hunter_preparation":
                candidates.append(row)
    if change_id:
        candidates = [row for row in candidates if row.get("change_id") == change_id]
    if not candidates:
        raise ValueError("no matching ready Gatherer escalation")
    escalation = candidates[0]
    spec = escalation["hunter_job"]
    job = prepare(spec["playbook"], depth=spec.get("depth"),
                  target_keys=spec.get("target_keys"), limit=len(spec.get("target_keys") or []))
    if not (job.get("directive", {}).get("packet_requirements", {}).get("target_keys") or []):
        raise ValueError("Gatherer targets did not resolve through Hunter's live gap manifest")
    job["gatherer_escalation"] = escalation
    job["directive"]["gatherer_context"] = {
        "change_id": escalation.get("change_id"),
        "objective": spec.get("objective"),
        "known_source_urls": spec.get("known_source_urls") or [],
        "verification_questions": escalation.get("verification_questions") or [],
        "prior_state_hint": spec.get("prior_state_hint"),
    }
    return job


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare or finalize a Hunter research cycle")
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("playbook")
    prep.add_argument("--depth")
    prep.add_argument("--universe", choices=["all", "brands", "competitors", "franchisees", "fdd"], default="all")
    prep.add_argument("--target", action="append", dest="target_keys")
    prep.add_argument("--limit", type=int)
    prep.add_argument("--output")
    prep.add_argument("--queue", action="store_true", help=f"Also persist the job to {PENDING_JOBS_DIR} for manual submission + later `sweep`")
    priority = sub.add_parser("prepare-priority", help="Prepare a local assignment from the existing CoS-ranked queue without changing its rank or score")
    priority.add_argument("--queue", action="store_true", help="Persist the assignment only when transport availability is explicitly confirmed")
    priority.add_argument("--transport-available", action="store_true", help="Assert that safe ChatGPT submission and local response capture are available")
    priority.add_argument("--output")
    fin = sub.add_parser("finalize")
    fin.add_argument("job")
    fin.add_argument("packet")
    fin.add_argument("--confirm", action="store_true")
    fin.add_argument("--output")
    gather = sub.add_parser("prepare-gatherer")
    gather.add_argument("--queue", default=str(GATHERER_QUEUE_PATH))
    gather.add_argument("--change-id")
    gather.add_argument("--output", required=True)
    swp = sub.add_parser("sweep", help=f"Match returned packets in {PACKETS_INBOX_DIR} to queued jobs in {PENDING_JOBS_DIR} and finalize")
    swp.add_argument("--confirm", action="store_true")
    swp.add_argument("--output")
    swp.add_argument("--quarantine-max-age-hours", type=int, default=48,
                      help="Move a pending job with no returned packet older than this into "
                           f"{QUARANTINED_JOBS_DIR} before matching (default: 48)")
    args = parser.parse_args()
    if args.command == "prepare-priority":
        result = prepare_priority_bundle(transport_available=args.transport_available)
        if args.queue:
            if not args.transport_available:
                result["status"] = "transport_blocked"
                result["transport_blocker"] = "No supported safe transport has been confirmed; assignment was not queued."
            elif result.get("status") == "prepared_local_assignment":
                if len(_pending_job_files()) >= PENDING_JOB_CEILING_TOTAL:
                    result["status"] = "pending_ceiling_reached"
                elif result.get("bundle_schema"):
                    bundle_id = result.get("assignment_id") or ("priority-" + _target_slug("-".join(result.get("target_keys") or [])))
                    result["assignment_id"] = bundle_id
                    assignment_path = PENDING_JOBS_DIR / f"bundle-{_target_slug(bundle_id)}.json"
                    if assignment_path.exists() or set(result.get("target_keys") or []).intersection(_pending_target_keys()):
                        result["status"] = "already_pending"
                    else:
                        assignment_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                        result["queued_path"] = str(assignment_path)
                else:
                    subjob = result["subjobs"][0]
                    path = _write_job(subjob["job"], subjob["target_key"])
                    result["queued_path"] = str(path) if path else None
                    if not path:
                        result["status"] = "already_pending"
        if args.output:
            Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    elif args.command == "prepare":
        if not args.output and not args.queue:
            parser.error("prepare requires --output, --queue, or both")
        if args.queue:
            result = queue_prepare(args.playbook, depth=args.depth, universe=args.universe,
                                   target_keys=args.target_keys, limit=args.limit)
        else:
            result = prepare(args.playbook, depth=args.depth, universe=args.universe,
                             target_keys=args.target_keys, limit=args.limit)
        if args.output:
            Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif args.command == "prepare-gatherer":
        result = prepare_gatherer_escalation(queue_path=Path(args.queue), change_id=args.change_id)
        Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif args.command == "sweep":
        result = sweep(confirm=args.confirm, quarantine_max_age_hours=args.quarantine_max_age_hours)
        if args.output:
            Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        job = json.loads(Path(args.job).read_text(encoding="utf-8"))
        try:
            packet = load_packet_artifact(args.packet)
            result = finalize(job, packet, confirm=args.confirm)
        except (OSError, ValueError) as error:
            result = {
                "schema": "rb.hunter_cycle_receipt.v1",
                "packet_id": None,
                "ok": False,
                "confirmed": args.confirm,
                "normalization": {"applied": False, "changes": []},
                "validation": {"valid": False, "errors": [{
                    "code": "packet_artifact_invalid",
                    "path": str(args.packet),
                    "message": str(error),
                }]},
                "change_comparison": {"valid": False, "errors": []},
                "dispatch": None,
            }
        if args.output:
            Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
