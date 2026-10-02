#!/usr/bin/env python3
"""
import_fdd_research.py — FDD Technology Governance & Economics importer
(system/technology_lifecycle/FDD_GOVERNANCE_ECONOMICS_BRIEF.md, §21
"Importer Requirement").

Mirrors import_technology_lifecycle_research.py's shape exactly (idempotent
via a content-hash log, dry-run/confirm/sweep CLI, one malformed item never
blocks the rest of the packet) -- same event-sourced reasoning applies
here: there is no mutation_policy dedupe/apply-or-queue decision, because
every write is already either a well-formed new observation (appended) or
a validation failure (rejected), and a correction is `supersedes`, decided
by the research itself, never inferred here.

A validated Hunter packet (schema `rb.hunter_research_packet.v1`,
`payload_schema: "rb.fdd_governance_economics_research.v1"`) carries its
structured content under
`packet["payload"]["fdd_governance_economics_findings"]` -- a list of:

```json
{
  "record_type": "fdd_source | governance | economics | governance_change_event | penetration_reconciliation | relationship_event | penetration | forcing_signal | research_gap | entity_resolution_review",
  "finding_id": "the Hunter finding_id this traces back to, for citation cross-reference",
  "payload": { ...keyword arguments matching the matching technology_lifecycle.py writer exactly... }
}
```

"entity_resolution_review" is brief §1's safety valve: when Hunter's own
research can't confidently resolve a named entity (franchisor, vendor,
product) against ecosystem_intelligence.json, it emits this record_type
instead of a governance/economics finding -- queued for a human to
confirm the real entity id, never a guessed or invented one. Every other
record_type maps straight to its technology_lifecycle.py writer; relationship_
event/penetration/forcing_signal are included because an FDD research cycle
legitimately surfaces generic Technology Lifecycle findings too (e.g. "this
FDD names a specific deployed vendor"), not just FDD-specific record types,
and there's no reason to require a second packet for those.

Each item's `payload` is passed straight to the matching writer as keyword
arguments -- no field-remapping layer; a shape mismatch is a real,
surfaced rejection (TypeError), not something this importer guesses around.

CLI:
    python3 import_fdd_research.py --file <packet.json> [--dry-run|--confirm]
    python3 import_fdd_research.py --sweep [--dry-run|--confirm]
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

SCHEMA = "rb.fdd_governance_economics_research.v1"

DROP_DIR = core.SYSTEM_DIR / "inbox" / "chatgpt_intelligence_drop"
IMPORT_LOG_PATH = core.CACHE_DIR / "fdd_research_import_log.json"
SWEEP_MANIFEST_PATH = core.SYSTEM_DIR / "research" / "fdd_research_sweep_manifest.json"

_RECORD_TYPE_WRITERS = {
    "fdd_source": tl.record_fdd_source,
    "governance": tl.record_governance,
    "economics": tl.record_economics_observation,
    "governance_change_event": tl.record_governance_change_event,
    "penetration_reconciliation": tl.record_penetration_reconciliation,
    "relationship_event": tl.record_relationship_event,
    "penetration": tl.record_penetration,
    "forcing_signal": tl.record_forcing_signal,
    "research_gap": tl.record_fdd_research_gap,
    "entity_resolution_review": tl.queue_entity_resolution_review,
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
    items = payload.get("fdd_governance_economics_findings") or []

    log = _load_import_log()
    seen: set[str] = set(log["processed_item_keys"])
    new_keys: list[str] = []

    summary: dict[str, Any] = {
        "packet_id": packet_id,
        "items_received": len(items),
        "already_processed": 0,
        "applied": 0,
        "rejected": 0,
        "queued_for_entity_review": 0,
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

        if record_type == "entity_resolution_review":
            summary["queued_for_entity_review"] += 1
        else:
            summary["applied"] += 1
            bucket["applied"] += 1
        new_keys.append(key)

    if not dry_run and new_keys:
        log["processed_item_keys"] = sorted(seen | set(new_keys))
        _save_import_log(log)

    return summary


def sweep(*, dry_run: bool = True) -> dict:
    """Scan DROP_DIR for packets carrying fdd_governance_economics_findings
    not yet offered to this importer (own file-hash manifest, independent
    of the other narrow importers' manifests)."""
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
            if not payload.get("fdd_governance_economics_findings"):
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
