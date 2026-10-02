#!/usr/bin/env python3
"""import_technology_lifecycle_cycles.py -- lossless intake for ChatGPT's
Technology Lifecycle Deep Research cycle packets (2026-10-01).

The real research (see system/technology_lifecycle/RESEARCH_KICKOFF.md)
started arriving as zip packets in system/inbox/chatgpt_intelligence_drop/
before the strict rb.technology_lifecycle_research.v1 sidecar schema in
system/templates/deep_research_technology_lifecycle_drop.md was finalized
end-to-end, and before any schema-exact importer existed. Record shapes
vary cycle to cycle (some use a top-level "records" key, some "events";
field names differ per cycle -- "brand"/"event"/"state" in one, "brand"/
"layer"/"announced_scope"/"verified_deployed_scope" in another). Forcing
every record through a brittle strict mapper in one pass risks silently
misclassifying real research; this script instead guarantees NOTHING IS
LOST (every record is preserved verbatim, forever, in a durable Tier 1
intake log) and produces a human-readable digest so a human or a future,
more careful importer pass can promote the strongest findings into the
strict schema (technology_relationship_events.jsonl / technology_change_
events.jsonl / technology_governance.jsonl / technology_penetration.jsonl
/ technology_forcing_signals.jsonl) deliberately.

This is intake, not promotion. Every record lands in raw_cycle_records.
jsonl with visibility_class defaulted to public_shared (all source packets
are public-source ChatGPT Deep Research) -- see system/SCHEMAS.md's
visibility_class subsection.

Idempotent: a manifest of already-processed zip content-hashes means
re-running --sweep never re-ingests or duplicates a packet already seen.
Never deletes or moves the original zip files.

2026-10-02 (Hunter adoption): RBB's research method for this domain is now
Hunter (system/research/HUNTER.md, hunter_cycle.py prepare/finalize), not
the free-form ChatGPT cycles this script was built to catch up on. This
script is kept as compatibility intake only -- it reads whatever already
landed in the inbox (the 10 real pre-Hunter cycles it already swept) and
never authors or requests new research itself, so it does not bypass
Hunter in the sense that document prohibits. It should not be used as a
model for a new research cycle; see RESEARCH_KICKOFF.md for the current
(Hunter) workflow.

CLI:
    python3 import_technology_lifecycle_cycles.py --sweep [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent.parent
DROP_DIR = ROOT / "system" / "inbox" / "chatgpt_intelligence_drop"
INTAKE_DIR = ROOT / "system" / "technology_lifecycle" / "_intake"
RAW_RECORDS_PATH = INTAKE_DIR / "raw_cycle_records.jsonl"
MANIFEST_PATH = ROOT / "system" / ".cache" / "technology_lifecycle_cycle_sweep_manifest.json"

# Packet-level keys that are curator commentary / meta-findings rather than
# individual records -- preserved in the digest verbatim, not flattened
# into raw_cycle_records.jsonl (which is for the record list itself).
META_KEYS = (
    "methodology_additions", "cross_case_observations", "working_model",
    "cycle_findings", "schema_recommendations", "economic_framework",
    "research_gaps", "schema_additions", "key_findings",
    "unresolved_questions", "next_targets", "deep_findings", "new_metrics",
    "methodology_rules", "next_deep_queue", "research_rules",
)
RECORD_LIST_KEYS = ("records", "events")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _load_manifest() -> dict:
    if MANIFEST_PATH.is_file():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {"processed": {}}


def _save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _extract_packet_json(zip_path: Path) -> tuple[dict, str] | None:
    """Return (parsed_json, json_filename) for the first .json member in
    the zip whose parsed content looks like a technology-lifecycle packet
    (has a records/events list, or a packet_type mentioning
    technology_lifecycle), else None."""
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".json"):
                continue
            try:
                data = json.loads(zf.read(name).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            packet_type = str(data.get("packet_type") or "")
            has_records = any(isinstance(data.get(k), list) for k in RECORD_LIST_KEYS)
            if "technology_lifecycle" in packet_type or has_records:
                return data, name
    return None


def _record_summary_line(rec: dict) -> str:
    """Best-effort one-line human summary across the varying record
    shapes different cycles used -- never raises on a missing field."""
    brand = rec.get("brand") or rec.get("brand_name") or "?"
    layer = rec.get("layer") or rec.get("technology_category") or rec.get("event") or ""
    state = rec.get("state") or rec.get("lifecycle_state") or ""
    confidence = rec.get("confidence")
    bits = [str(brand)]
    if layer:
        bits.append(str(layer)[:80])
    if state:
        bits.append(f"[{state}]")
    if confidence is not None:
        bits.append(f"(confidence {confidence})")
    return " — ".join(bits)


def sweep(*, dry_run: bool = True) -> dict:
    INTAKE_DIR.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest()
    processed = manifest.setdefault("processed", {})

    if not DROP_DIR.is_dir():
        return {"packets_found": 0, "packets_processed": 0, "records_ingested": 0}

    results = []
    new_raw_lines: list[str] = []
    digest_sections: list[str] = []
    total_records = 0

    for zip_path in sorted(DROP_DIR.glob("*.zip")):
        fhash = _file_hash(zip_path)
        if fhash in processed:
            continue
        try:
            extracted = _extract_packet_json(zip_path)
        except (OSError, zipfile.BadZipFile) as exc:
            results.append({"zip": zip_path.name, "ok": False, "error": str(exc)})
            continue
        if extracted is None:
            # Not a technology-lifecycle packet; leave untouched for
            # whatever other importer (if any) handles this inbox.
            continue
        data, json_name = extracted

        record_list: list = []
        record_key_used = None
        for k in RECORD_LIST_KEYS:
            v = data.get(k)
            if isinstance(v, list) and v:
                record_list = v
                record_key_used = k
                break

        cycle = data.get("cycle")
        date = data.get("date") or data.get("research_date")
        focus = data.get("focus") or data.get("research_question")
        packet_type = data.get("packet_type")

        for idx, rec in enumerate(record_list):
            if not isinstance(rec, dict):
                continue
            envelope = {
                "intake_id": f"tlc-{fhash[:10]}-{idx}",
                "source_zip": zip_path.name,
                "source_json": json_name,
                "packet_type": packet_type,
                "cycle": cycle,
                "packet_date": date,
                "focus": focus,
                "record_index": idx,
                "record_key": record_key_used,
                "record": rec,
                "visibility_class": "public_shared",
                "ingested_at": _now_iso(),
                "promoted": False,
                "promotion_note": "Raw intake only -- not yet mapped into technology_relationship_events.jsonl / technology_change_events.jsonl / technology_governance.jsonl / technology_penetration.jsonl / technology_forcing_signals.jsonl. See CYCLE_DIGEST for promotion candidates.",
            }
            new_raw_lines.append(json.dumps(envelope))
            total_records += 1

        meta = {k: data[k] for k in META_KEYS if k in data}
        digest_sections.append(_render_digest_section(zip_path.name, packet_type, cycle, date, focus, record_list, meta))

        results.append({"zip": zip_path.name, "ok": True, "records": len(record_list), "packet_type": packet_type, "cycle": cycle})
        if not dry_run:
            processed[fhash] = {"zip": zip_path.name, "processed_at": _now_iso(), "records": len(record_list)}

    if not dry_run and new_raw_lines:
        with RAW_RECORDS_PATH.open("a", encoding="utf-8") as f:
            for line in new_raw_lines:
                f.write(line + "\n")
        _save_manifest(manifest)

    if not dry_run and digest_sections:
        _write_or_append_digest(digest_sections)

    return {
        "packets_found": len(results),
        "packets_processed": sum(1 for r in results if r.get("ok")),
        "records_ingested": total_records,
        "dry_run": dry_run,
        "details": results,
    }


def _render_digest_section(zip_name: str, packet_type, cycle, date, focus, record_list: list, meta: dict) -> str:
    lines = [f"## {zip_name}", "", f"- packet_type: `{packet_type}`", f"- cycle: {cycle}", f"- date: {date}", f"- focus: {focus}", f"- records: {len(record_list)}", ""]
    for i, rec in enumerate(record_list):
        if isinstance(rec, dict):
            lines.append(f"{i+1}. {_record_summary_line(rec)}")
    if meta:
        lines.append("")
        lines.append("**Packet-level notes:**")
        for k, v in meta.items():
            lines.append(f"- *{k}*:")
            if isinstance(v, list):
                for item in v:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"  - {v}")
    lines.append("")
    return "\n".join(lines)


def _write_or_append_digest(new_sections: list[str]) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    digest_path = INTAKE_DIR / f"CYCLE_DIGEST_{today}.md"
    header = (
        f"# Technology Lifecycle Cycle Digest — {today}\n\n"
        "Auto-generated by `system/scripts/import_technology_lifecycle_cycles.py --sweep`. "
        "Every record below is preserved verbatim and losslessly in "
        "`system/technology_lifecycle/_intake/raw_cycle_records.jsonl` "
        "(visibility_class: public_shared). This digest is for human/Claude "
        "review to decide which records get promoted into the strict "
        "technology_relationship_events.jsonl / technology_change_events.jsonl "
        "/ technology_governance.jsonl / technology_penetration.jsonl / "
        "technology_forcing_signals.jsonl schemas -- nothing here has been "
        "promoted automatically.\n\n"
    )
    existing = digest_path.read_text(encoding="utf-8") if digest_path.is_file() else header
    digest_path.write_text(existing + "\n".join(new_sections) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep", action="store_true", help="Sweep the inbox for unprocessed technology-lifecycle zip packets.")
    parser.add_argument("--confirm", action="store_true", help="Actually write the intake log and digest (default without this flag: dry run / report only, same convention as import_competitor_platform_research.py).")
    args = parser.parse_args()

    if not args.sweep:
        parser.print_help()
        return 1

    result = sweep(dry_run=not args.confirm)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
