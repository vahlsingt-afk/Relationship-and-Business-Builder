#!/usr/bin/env python3
"""Copy completed Hunter packets from the Drive Desktop inbox into RBB's inbox.

Only packet-shaped JSON files are considered. Outgoing assignment files and
unmatched/invalid files stay in Drive for inspection. Copies are idempotent by
content digest, including after Hunter sweep archives the local copy.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYSTEM = ROOT / "system"
PENDING = SYSTEM / ".cache" / "hunter_pending_jobs"
INBOX = SYSTEM / "inbox" / "hunter_packets"
STATE = SYSTEM / ".cache" / "hunter_drive_inbox_sync.json"
# Durable, append-only receipt per copy -- hunter_office_manager.py reads this
# (cursor-based, independent of STATE's cumulative-hash dedup) to know which
# targets were freshly deposited in this run, for the brief section and the
# completion text. Kept separate from STATE so STATE's own dedup contract
# (never reprocess a hash) isn't touched by a second reader's needs.
RECEIPTS_PATH = SYSTEM / ".cache" / "hunter_inbox_sync_receipts.jsonl"
DRIVE_INBOX = Path(os.environ.get("RB_HUNTER_DRIVE_INBOX", str(Path.home() / "My Drive" / "RBB Hunter Cycle Inbox"))).expanduser()
PACKET_SCHEMA = "rb.hunter_research_packet.v1"
BUNDLE_SCHEMA = "rb.hunter_research_bundle_response.v1"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def pending_targets() -> tuple[set[str], set[str]]:
    targets: set[str] = set()
    bundles: set[str] = set()
    if PENDING.is_dir():
        for path in PENDING.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            targets.update((job.get("directive", {}).get("packet_requirements", {}) or {}).get("target_keys") or [])
            targets.update(s.get("target_key") for s in job.get("subjobs") or [] if s.get("target_key"))
            if job.get("assignment_id"):
                bundles.add(job["assignment_id"])
    return targets, bundles


def packet_targets(packet: dict) -> set[str]:
    if packet.get("schema") == BUNDLE_SCHEMA:
        return set(packet.get("target_keys") or [])
    rows = packet.get("targets") or []
    return {row.get("target_key") if isinstance(row, dict) else row for row in rows} - {None, ""}


def run() -> dict:
    INBOX.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {"copied_sha256": []}
    copied_hashes = set(state.get("copied_sha256") or [])
    targets, bundles = pending_targets()
    out = {"copied": [], "unmatched": [], "invalid": [], "skipped": []}
    if not DRIVE_INBOX.is_dir():
        out["source_missing"] = str(DRIVE_INBOX)
        return out

    for src in sorted(DRIVE_INBOX.iterdir()):
        if not src.is_file() or src.suffix.lower() != ".json" or src.name.startswith("hunter-assignment-"):
            continue
        if not (src.name.startswith("hunter-") and any(token in src.stem for token in ("packet", "response", "batch", "bundle"))):
            continue
        sha = digest(src)
        if sha in copied_hashes:
            out["skipped"].append({"file": src.name, "reason": "content already imported"})
            continue
        try:
            packet = json.loads(src.read_text(encoding="utf-8-sig"))
            if not isinstance(packet, dict) or packet.get("schema") not in {PACKET_SCHEMA, BUNDLE_SCHEMA}:
                raise ValueError("unrecognized Hunter packet schema")
        except (OSError, ValueError) as error:
            out["invalid"].append({"file": src.name, "error": str(error)})
            continue
        packet_keys = packet_targets(packet)
        is_bundle = packet.get("schema") == BUNDLE_SCHEMA
        if not (packet_keys & targets) or (is_bundle and packet.get("assignment_id") not in bundles):
            out["unmatched"].append({"file": src.name, "targets": sorted(packet_keys), "reason": "no matching pending RBB Hunter job"})
            continue
        dest = INBOX / src.name
        if dest.exists() and digest(dest) != sha:
            dest = INBOX / f"{src.stem}--{sha[:8]}{src.suffix}"
        if not dest.exists():
            temp = dest.with_name(dest.name + ".sync-tmp")
            shutil.copy2(src, temp)
            os.replace(temp, dest)
        copied_hashes.add(sha)
        out["copied"].append({
            "file": src.name, "destination": str(dest), "sha256": sha,
            "targets": sorted(packet_keys),
        })

    state.update({"copied_sha256": sorted(copied_hashes), "last_run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": str(DRIVE_INBOX)})
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if out["copied"]:
        RECEIPTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with RECEIPTS_PATH.open("a", encoding="utf-8") as fh:
            for row in out["copied"]:
                fh.write(json.dumps({
                    "observed_at": observed_at, "file": row["file"],
                    "destination": row["destination"], "sha256": row["sha256"],
                    "targets": row["targets"],
                }, sort_keys=True) + "\n")

    return out


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
