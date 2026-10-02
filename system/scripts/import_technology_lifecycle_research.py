#!/usr/bin/env python3
"""
import_technology_lifecycle_research.py — Technology Lifecycle Phase 1
importer (system/technology_lifecycle/README.md, "What exists vs. what's
Phase 1": "An importer that turns a ChatGPT research packet... into real
lines in these files" -- the piece this module builds).

Mirrors import_competitor_platform_research.py's shape (idempotent via a
content-hash log, dry-run/confirm/sweep CLI, one malformed item never
blocks the rest of the packet) but adapted for an append-only,
event-sourced domain: there is no mutation_policy dedupe/apply-or-queue
decision here, because every write is already either a well-formed new
observation (appended) or a validation failure (rejected) -- the "decide
whether to apply" question mutation_policy answers for a mutable profile
field doesn't apply to a ledger where every well-formed, non-duplicate
observation is itself real data worth keeping, including ones that
contradict an earlier line (that's exactly what `supersedes` is for, and
Hunter's own research decides when to set it -- this importer doesn't
infer it).

A validated Hunter packet (schema `rb.hunter_research_packet.v1`,
`payload_schema: "rb.technology_lifecycle_research.v1"`) carries its
structured content under `packet["payload"]["technology_lifecycle_findings"]`
-- a list of:

```json
{
  "record_type": "relationship_event | governance | penetration | change_event | forcing_signal",
  "finding_id": "the Hunter finding_id this traces back to, for citation cross-reference",
  "payload": { ...keyword arguments matching technology_lifecycle.py's record_<record_type>() exactly... }
}
```

Each item's `payload` is passed straight to the matching
`technology_lifecycle.record_<record_type>()` as keyword arguments --
there is deliberately no field-remapping layer here; Hunter's research
packet is expected to already shape its findings to this module's writer
signatures (same discipline the technology_replacement_lifecycle playbook
and system/SCHEMAS.md establish), and a shape mismatch is a real,
surfaced rejection (TypeError on an unexpected/missing keyword), not
something this importer tries to guess around.

CLI:
    python3 import_technology_lifecycle_research.py --file <packet.json> [--dry-run|--confirm]
    python3 import_technology_lifecycle_research.py --sweep [--dry-run|--confirm]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import technology_lifecycle as tl  # noqa: E402

SCHEMA = "rb.technology_lifecycle_research.v1"

DROP_DIR = core.SYSTEM_DIR / "inbox" / "chatgpt_intelligence_drop"
IMPORT_LOG_PATH = core.CACHE_DIR / "technology_lifecycle_research_import_log.json"
SWEEP_MANIFEST_PATH = core.SYSTEM_DIR / "research" / "technology_lifecycle_research_sweep_manifest.json"

_RECORD_TYPE_WRITERS = {
    "relationship_event": tl.record_relationship_event,
    "governance": tl.record_governance,
    "penetration": tl.record_penetration,
    "change_event": tl.record_change_event,
    "forcing_signal": tl.record_forcing_signal,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _item_key(packet_id: str, record_type: str, payload: dict) -> str:
    basis = f"{packet_id}\x1f{record_type}\x1f{json.dumps(payload, sort_keys=True, default=str)}".encode("utf-8")
    return hashlib.sha1(basis).hexdigest()[:20]


def _load_import_log() -> dict:
    if not IMPORT_LOG_PATH.exists():
        return {"processed_item_keys": []}
    try:
        data = json.loads(IMPORT_LOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"processed_item_keys": []}
    data.setdefault("processed_item_keys", [])
    return data


def _save_import_log(data: dict) -> None:
    IMPORT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    IMPORT_LOG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_sweep_manifest() -> dict:
    if SWEEP_MANIFEST_PATH.exists():
        try:
            manifest = json.loads(SWEEP_MANIFEST_PATH.read_text(encoding="utf-8"))
            if isinstance(manifest, dict) and isinstance(manifest.get("processed"), dict):
                return manifest
        except Exception:  # noqa: BLE001
            pass
    return {"processed": {}}


def _save_sweep_manifest(manifest: dict) -> None:
    SWEEP_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    SWEEP_MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _extract_payload(packet: dict) -> dict:
    if (
        packet.get("schema") == "rb.hunter_research_packet.v1"
        and packet.get("payload_schema") == SCHEMA
        and isinstance(packet.get("payload"), dict)
    ):
        return packet["payload"]
    # Legacy/direct shape: the packet IS the payload (e.g. a hand-built test fixture).
    return packet


def import_findings(packet: dict, *, packet_id: str | None = None, dry_run: bool = True) -> dict:
    packet_id = packet_id or packet.get("packet_id") or "unknown-packet"
    payload = _extract_payload(packet)
    items = payload.get("technology_lifecycle_findings") or []

    log = _load_import_log()
    seen: set[str] = set(log["processed_item_keys"])
    new_keys: list[str] = []

    summary: dict[str, Any] = {
        "packet_id": packet_id,
        "items_received": len(items),
        "already_processed": 0,
        "applied": 0,
        "rejected": 0,
        "rejected_errors": [],
        "by_record_type": {},
    }

    for idx, item in enumerate(items):
        record_type = item.get("record_type")
        item_payload = item.get("payload")
        bucket = summary["by_record_type"].setdefault(record_type or "unknown", {"applied": 0, "rejected": 0})

        if record_type not in _RECORD_TYPE_WRITERS or not isinstance(item_payload, dict):
            summary["rejected"] += 1
            bucket["rejected"] += 1
            summary["rejected_errors"].append({
                "index": idx, "finding_id": item.get("finding_id"),
                "error": f"unknown or missing record_type (must be one of {sorted(_RECORD_TYPE_WRITERS)}) or payload is not an object",
            })
            continue

        key = _item_key(packet_id, record_type, item_payload)
        if key in seen:
            summary["already_processed"] += 1
            continue

        writer = _RECORD_TYPE_WRITERS[record_type]
        try:
            if not dry_run:
                writer(**item_payload)
        except (tl.TechnologyLifecycleError, TypeError) as exc:
            summary["rejected"] += 1
            bucket["rejected"] += 1
            summary["rejected_errors"].append({
                "index": idx, "finding_id": item.get("finding_id"), "record_type": record_type, "error": str(exc),
            })
            new_keys.append(key)  # a structurally bad item re-offered unchanged would reject the same way every sweep
            continue

        summary["applied"] += 1
        bucket["applied"] += 1
        new_keys.append(key)

    if not dry_run and new_keys:
        log["processed_item_keys"] = sorted(seen | set(new_keys))
        _save_import_log(log)

    return summary


def sweep(*, dry_run: bool = True) -> dict:
    """Scan DROP_DIR for packets carrying technology_lifecycle_findings not
    yet offered to this importer (own file-hash manifest, independent of
    import_competitor_platform_research.py's)."""
    manifest = _load_sweep_manifest()
    processed = manifest["processed"]
    results = []
    if DROP_DIR.is_dir():
        for path in sorted(DROP_DIR.glob("*.json")):
            file_hash = _file_hash(path)
            if processed.get(str(path)) == file_hash:
                continue
            try:
                packet = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                results.append({"file": str(path), "error": f"unreadable: {exc}"})
                continue
            payload = _extract_payload(packet)
            if not payload.get("technology_lifecycle_findings"):
                if not dry_run:
                    processed[str(path)] = file_hash
                continue
            summary = import_findings(packet, dry_run=dry_run)
            summary["file"] = str(path)
            results.append(summary)
            if not dry_run:
                processed[str(path)] = file_hash
    if not dry_run:
        _save_sweep_manifest(manifest)
    return {"files_processed": len(results), "results": results}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", type=Path, help="Import one Hunter packet JSON file.")
    p.add_argument("--sweep", action="store_true", help="Scan the ChatGPT Intelligence Drop inbox for unimported packets.")
    p.add_argument("--confirm", action="store_true", help="Actually write (default: dry run only).")
    p.add_argument("--dry-run", action="store_true", help="Explicit dry run (default when --confirm is omitted).")
    args = p.parse_args()

    dry_run = not args.confirm

    if args.file:
        packet = json.loads(args.file.read_text(encoding="utf-8"))
        result = import_findings(packet, dry_run=dry_run)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if dry_run:
            print("\n[dry run -- nothing written. Re-run with --confirm to apply.]", file=sys.stderr)
        return 0
    if args.sweep:
        result = sweep(dry_run=dry_run)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if dry_run:
            print("\n[dry run -- nothing written. Re-run with --confirm to apply.]", file=sys.stderr)
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
