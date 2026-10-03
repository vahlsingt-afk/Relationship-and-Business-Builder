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
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import hunter  # noqa: E402
import hunter_change_dispatch  # noqa: E402
import hunter_snapshot  # noqa: E402

GATHERER_QUEUE_PATH = SCRIPTS_DIR.parent / ".cache" / "gatherer_hunter_escalations.jsonl"
import hunter_packet_normalize  # noqa: E402

# 2026-10-02: Computer Use's browser-safety guardrail categorically
# refuses to carry a locally-prepared directive's content into a ChatGPT
# web session -- both a direct file-attach and a pasted-text fallback
# were rejected on a real run (RB Hunter 90+ Unit Gap Cycle, 2026-10-02
# ~14:50 Central). That refusal is correct (reading a local file and
# injecting its content into a remote page is exactly the shape of
# exfiltration a safety classifier should block) and is not something to
# route around. The real fix: automations stop trying to submit the
# directive themselves. `queue_prepare()` persists a prepared job
# durably (not /tmp, which an hourly cron would silently lose between
# runs) so a human can submit it manually; `sweep()` later matches a
# manually-returned packet (dropped in PACKETS_INBOX_DIR) back to its
# queued job and finalizes it the same way a single prepare/finalize
# pair always did.
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
QUARANTINED_JOBS_DIR = PENDING_JOBS_DIR / "quarantined"
PENDING_JOB_CEILING_TOTAL = 3
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
        family = _target_family(target_key)
        family_count = sum(1 for p in pending if _target_family_of_file(p) == family)
        if len(pending) >= PENDING_JOB_CEILING_TOTAL or family_count >= PENDING_JOB_CEILING_PER_FAMILY:
            skipped_ceiling.append({
                "target_key": target_key,
                "family": family,
                "pending_total": len(pending),
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
        "submission_note": (
            "Browser hand-off to ChatGPT Deep Research is structurally blocked (2026-10-02) -- "
            "a human or Codex (never ChatGPT's own file-save) must save the single returned JSON "
            f"packet verbatim into {PACKETS_INBOX_DIR} (preferred) or {LEGACY_PACKETS_INBOX_DIR} "
            "(also watched, since real runs keep landing there instead) for `hunter_cycle.py sweep` "
            "to finalize. sweep() only matches a packet to a job queued before that packet's own "
            "file timestamp -- never backdate or touch an older unrelated file's modified time to "
            "force a match."
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
            targets = packet.get("targets") or []
            job_path = None
            for target_key in targets:
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
            receipt = finalize(job, packet, confirm=confirm)
            results.append({"packet_path": str(packet_path), "job_path": str(job_path), "receipt": receipt})
            job_path.rename(PROCESSED_JOBS_DIR / job_path.name)
            packet_path.rename(processed_dir / packet_path.name)
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
    return {
        "schema": "rb.hunter_cycle_job.v1",
        "execution_boundary": "Submit directive to ChatGPT Deep Research; capture its single inline JSON object as UTF-8 text (.txt, .md, or .json) for local finalization.",
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
    prep.add_argument("--universe", choices=["all", "brands", "competitors", "franchisees"], default="all")
    prep.add_argument("--target", action="append", dest="target_keys")
    prep.add_argument("--limit", type=int)
    prep.add_argument("--output")
    prep.add_argument("--queue", action="store_true", help=f"Also persist the job to {PENDING_JOBS_DIR} for manual submission + later `sweep`")
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
    if args.command == "prepare":
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
