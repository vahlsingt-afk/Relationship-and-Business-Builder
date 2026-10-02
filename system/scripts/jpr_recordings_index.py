#!/usr/bin/env python3
"""
jpr_recordings_index.py — RB-2026-08-28.

Real gap found in a JPR-capture audit Todd asked for: there was no single,
durable record of every JPR (Just Press Record) recording RB has ever seen
-- only the raw source folder (which iCloud can evict/reorganize) and the
processed-captures JSON files (keyed by an opaque file_id, no easy way to
answer "does a recording from date X exist, and what happened to it"
without grepping 60+ files by hand). This script builds that index: one
row per JPR recording RB has ever observed in the raw source folder or the
capture registry, cross-referencing raw-file presence, registry status,
and (when processed) intelligence-extraction outcome.

Also the mechanism that caught the real bug this session: cross-referencing
the raw folder against the registry surfaced 5 real recordings that were
fully synced on disk but never queued at all (see capture_ingest.py's
2026-08-28 fix to _find_new_files -- the sweep_window_hours recency filter
silently orphaned anything older than the window whenever a sweep was
missed). This script's `--verify` mode is the permanent version of that
check, not a one-time audit.

Usage:
    python3 jpr_recordings_index.py --build     # (re)build the index, print summary
    python3 jpr_recordings_index.py --verify    # build + exit 1 if any raw file is unqueued/unprocessed
    python3 jpr_recordings_index.py --json      # print the full index as JSON
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import capture_ingest as ci  # noqa: E402

INDEX_PATH = core.SYSTEM_DIR / "captures" / "jpr_recordings_index.json"
SOURCE_ID = "just_press_record"


def _jpr_source() -> dict | None:
    for s in ci._load_sources():
        if s.get("id") == SOURCE_ID:
            return s
    return None


def _raw_files(source: dict) -> list[Path]:
    folder = ci._expand_folder(source.get("folder", ""))
    if not folder.exists():
        return []
    out = []
    for pattern in source.get("patterns", ["**/*.m4a"]):
        out.extend(p for p in folder.glob(pattern) if p.is_file())
    return sorted(out, key=lambda p: p.stat().st_mtime)


def _load_processed_by_file_id() -> dict[str, dict]:
    processed_dir = core.SYSTEM_DIR / "captures" / "processed"
    out = {}
    for f in processed_dir.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if d.get("source_id") == SOURCE_ID:
            out[d.get("file_id")] = d
    return out


def _load_pending_by_file_id() -> set[str]:
    pending_dir = core.SYSTEM_DIR / "captures" / "pending"
    out = set()
    for f in pending_dir.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if d.get("source_id") == SOURCE_ID:
            out.add(d.get("file_id"))
    return out


def build_index() -> dict:
    source = _jpr_source()
    if source is None:
        return {"error": f"no enabled source with id '{SOURCE_ID}' in settings.json", "recordings": []}

    registry = ci._load_registry()
    registered = registry.get("processed_ids", {})
    processed_by_id = _load_processed_by_file_id()
    pending_ids = _load_pending_by_file_id()

    seen_ids: set[str] = set()
    rows: list[dict] = []

    for path in _raw_files(source):
        fid = ci._file_id(path)
        seen_ids.add(fid)
        try:
            size_bytes = path.stat().st_size
        except OSError:
            size_bytes = None
        reg_entry = registered.get(fid)
        proc = processed_by_id.get(fid)
        pr = (proc or {}).get("processing_result") or {}

        if proc is not None:
            status = "processed"
        elif fid in pending_ids:
            status = "pending"
        elif reg_entry is not None:
            status = reg_entry.get("status", "queued")
        else:
            status = "raw_only_unqueued"

        rows.append({
            "file_id": fid,
            "source_file": str(path),
            "on_disk": True,
            "size_bytes": size_bytes,
            "status": status,
            "queued_at": (reg_entry or {}).get("queued_at"),
            "transcript_available": proc.get("transcript_available") if proc else None,
            "word_count": proc.get("word_count") if proc else None,
            "persisted_count": pr.get("persisted_count"),
            "exec_mutations": pr.get("exec_mutations"),
            # RB defect 2026-09-30: exec_mutations above is executive-
            # declaration mutations only, never a total mutation count --
            # see server.py's identical comment. structured_import_applied
            # is the real structured-findings count once reconcile_deep_
            # research_capture_receipts.py has run for this capture (None
            # if not a deep-research capture or not yet reconciled).
            "structured_import_applied": pr.get("structured_import_applied"),
            "canonical_mutation_statement": pr.get("canonical_mutation_statement"),
            "noise_only": pr.get("noise_only"),
        })

    # Registry entries whose raw file is no longer on disk (evicted from
    # iCloud, moved, deleted) -- still real, still worth an index row so
    # "does a recording from date X exist" stays answerable even after the
    # source file itself is gone.
    for fid, reg_entry in registered.items():
        if reg_entry.get("source_id") != SOURCE_ID or fid in seen_ids:
            continue
        proc = processed_by_id.get(fid)
        pr = (proc or {}).get("processing_result") or {}
        rows.append({
            "file_id": fid,
            "source_file": reg_entry.get("source_file"),
            "on_disk": False,
            "size_bytes": None,
            "status": "processed" if proc else reg_entry.get("status", "unknown"),
            "queued_at": reg_entry.get("queued_at"),
            "transcript_available": proc.get("transcript_available") if proc else None,
            "word_count": proc.get("word_count") if proc else None,
            "persisted_count": pr.get("persisted_count"),
            "exec_mutations": pr.get("exec_mutations"),
            "structured_import_applied": pr.get("structured_import_applied"),
            "canonical_mutation_statement": pr.get("canonical_mutation_statement"),
            "noise_only": pr.get("noise_only"),
        })

    rows.sort(key=lambda r: r["source_file"] or "")

    return {
        "generated_at": ci.datetime.now(ci.timezone.utc).isoformat(timespec="seconds"),
        "source_id": SOURCE_ID,
        "total_recordings": len(rows),
        "by_status": {
            status: sum(1 for r in rows if r["status"] == status)
            for status in sorted({r["status"] for r in rows})
        },
        "recordings": rows,
    }


def save_index(index: dict) -> Path:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return INDEX_PATH


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--build", action="store_true")
    p.add_argument("--verify", action="store_true", help="build + exit 1 if any raw file is unqueued/unprocessed")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    index = build_index()
    save_index(index)

    if args.json:
        print(json.dumps(index, indent=2))
        return 0

    print(f"JPR recordings index: {index['total_recordings']} total")
    for status, count in index.get("by_status", {}).items():
        print(f"  {status}: {count}")
    print(f"Saved to {INDEX_PATH}")

    if args.verify:
        unresolved = [r for r in index["recordings"] if r["status"] in ("raw_only_unqueued", "pending")]
        if unresolved:
            print(f"\nVERIFY FAILED: {len(unresolved)} recording(s) not fully processed:")
            for r in unresolved:
                print(f"  {r['status']}: {r['source_file']}")
            return 1
        print("\nVERIFY OK: every known recording is queued or processed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
