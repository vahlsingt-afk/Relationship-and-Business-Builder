#!/usr/bin/env python3
"""Plan and record RB deep-research coverage without performing research.

The dispatcher uses this ledger to finish an initial pass across every ranked
restaurant brand and tracked competitor, then moves to recurring coverage:
competitors every 30 days and restaurant brands every 75 days.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse


SYSTEM_DIR = Path(__file__).resolve().parent.parent
GRAPH_PATH = SYSTEM_DIR / "ecosystem_intelligence.json"
COMPETITOR_REGISTRY_PATH = SYSTEM_DIR / "competitor_intelligence" / "_portfolio" / "competitor_registry.json"
STATE_PATH = SYSTEM_DIR / "research" / "deep_research_coverage.json"
DROP_DIR = SYSTEM_DIR / "inbox" / "chatgpt_intelligence_drop"
SIDECAR_MANIFEST_PATH = SYSTEM_DIR / "research" / "deep_research_sidecar_manifest.json"

COMPETITOR_INTERVAL_DAYS = 30
COMPANY_INTERVAL_DAYS = 75
ENTERPRISE_UNIT_THRESHOLD = 100
SECONDARY_PRIORITY_UNIT_THRESHOLD = 70
PRIMARY_BRAND_SHARE = 0.95


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _universe() -> dict[str, dict]:
    graph = _read_json(GRAPH_PATH, {})
    rows: dict[str, dict] = {}
    for entity in graph.get("entities") or []:
        attrs = entity.get("attributes") or {}
        rank = attrs.get("rank")
        if entity.get("entity_type") != "brand" or rank is None:
            continue
        try:
            rank_num = float(rank)
        except (TypeError, ValueError):
            continue
        if rank_num > 1500:
            continue
        key = f"company:{entity['id']}"
        rows[key] = {
            "kind": "company",
            "entity_id": entity["id"],
            "name": entity.get("name") or entity["id"],
            "rank": rank_num,
            "unit_count": attrs.get("unit_count"),
            "unit_delta": attrs.get("unit_delta"),
            "emerging": bool(attrs.get("emerging") or attrs.get("is_emerging")),
            "refresh_days": COMPANY_INTERVAL_DAYS,
        }

    registry = _read_json(COMPETITOR_REGISTRY_PATH, {}).get("registry") or []
    for item in registry:
        slug = item.get("competitor_slug")
        if not slug:
            continue
        key = f"competitor:{slug}"
        rows[key] = {
            "kind": "competitor",
            "entity_id": slug,
            "name": item.get("display_name") or slug,
            "rank": None,
            "refresh_days": COMPETITOR_INTERVAL_DAYS,
        }
    return rows


def sync() -> dict:
    universe = _universe()
    state = _read_json(STATE_PATH, {"version": 1, "targets": {}})
    targets = state.setdefault("targets", {})
    for key, row in universe.items():
        existing = targets.setdefault(key, {})
        existing.update(row)
        existing.setdefault("coverage_count", 0)
        existing.setdefault("last_covered_at", None)
        existing.setdefault("last_packet", None)
    for key in list(targets):
        targets[key]["active"] = key in universe
    state["updated_at"] = _now().isoformat(timespec="seconds")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return state


def _is_due(row: dict, now: datetime) -> bool:
    last = row.get("last_covered_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    except ValueError:
        return True
    return now >= last_dt + timedelta(days=int(row.get("refresh_days") or COMPANY_INTERVAL_DAYS))


def plan(limit: int) -> dict:
    state = sync()
    active = [row for row in state["targets"].values() if row.get("active", True)]
    baseline_complete = all(int(row.get("coverage_count") or 0) > 0 for row in active)
    now = _now()
    if baseline_complete:
        candidates = [row for row in active if _is_due(row, now)]
    else:
        candidates = [row for row in active if int(row.get("coverage_count") or 0) == 0]

    competitors = sorted(
        (row for row in candidates if row.get("kind") == "competitor"),
        key=lambda row: (row.get("name") or ""),
    )
    companies = [row for row in candidates if row.get("kind") == "company"]

    def units(row: dict) -> float:
        try:
            return float(row.get("unit_count") or 0)
        except (TypeError, ValueError):
            return 0

    def is_primary_brand(row: dict) -> bool:
        # "Emerging" is deliberately an explicit data flag; growth is not guessed
        # from one year's unit delta. Enterprise follows Todd's >100-location rule.
        return units(row) > ENTERPRISE_UNIT_THRESHOLD or bool(row.get("emerging"))

    def brand_order(row: dict):
        # Within the 5% secondary lane, work the 70-100-location brands first.
        secondary_band = 0 if units(row) >= SECONDARY_PRIORITY_UNIT_THRESHOLD else 1
        return (secondary_band, row.get("rank") or 999999, row.get("name") or "")

    selected = []
    brand_slots = max(0, limit)
    if brand_slots:
        primary = sorted((row for row in companies if is_primary_brand(row)), key=brand_order)
        secondary = sorted((row for row in companies if not is_primary_brand(row)), key=brand_order)

        # Allocate brand work cumulatively so small batches still converge to 95/5:
        # every twentieth brand-research slot goes to the secondary lane.
        active_companies = [row for row in active if row.get("kind") == "company"]
        completed_brand_runs = sum(int(row.get("coverage_count") or 0) for row in active_companies)
        allocation_cycle = round(1 / (1 - PRIMARY_BRAND_SHARE))
        secondary_slots = min(
            len(secondary),
            sum(
                1
                for offset in range(1, brand_slots + 1)
                if (completed_brand_runs + offset) % allocation_cycle == 0
            ),
        )
        primary_slots = min(len(primary), brand_slots - secondary_slots)
        remaining = brand_slots - primary_slots - secondary_slots
        if remaining:
            secondary_slots = min(len(secondary), secondary_slots + remaining)
        selected.extend(primary[:primary_slots])
        selected.extend(secondary[:secondary_slots])

    # Competitor-page mining remains valuable, but it must not displace the
    # user-defined 95/5 restaurant-brand allocation. Use it only when no brand
    # target is due or to fill capacity the brand pools cannot use.
    remaining_slots = max(0, limit - len(selected))
    if remaining_slots:
        selected.extend(competitors[:remaining_slots])

    for row in selected:
        if row.get("kind") != "company":
            continue
        row["research_priority"] = "primary_95" if is_primary_brand(row) else "secondary_5"
        if row["research_priority"] == "secondary_5" and units(row) >= SECONDARY_PRIORITY_UNIT_THRESHOLD:
            row["secondary_band"] = "70_to_100_locations"
    counts = {
        "companies_total": sum(r.get("kind") == "company" for r in active),
        "companies_covered": sum(r.get("kind") == "company" and int(r.get("coverage_count") or 0) > 0 for r in active),
        "competitors_total": sum(r.get("kind") == "competitor" for r in active),
        "competitors_covered": sum(r.get("kind") == "competitor" and int(r.get("coverage_count") or 0) > 0 for r in active),
    }
    return {
        "phase": "maintenance" if baseline_complete else "initial_coverage",
        "allocation_policy": {
            "primary_share": PRIMARY_BRAND_SHARE,
            "primary_definition": ">100 locations or explicitly marked emerging",
            "secondary_share": 1 - PRIMARY_BRAND_SHARE,
            "secondary_order": "70-100 locations first, then smaller brands",
        },
        "counts": counts,
        "targets": selected,
    }


def record(packet: str, target_keys: list[str]) -> dict:
    state = sync()
    timestamp = _now().isoformat(timespec="seconds")
    recorded = []
    for key in target_keys:
        row = state["targets"].get(key)
        if not row:
            continue
        row["coverage_count"] = int(row.get("coverage_count") or 0) + 1
        row["last_covered_at"] = timestamp
        row["last_packet"] = packet
        recorded.append(key)
    state["updated_at"] = timestamp
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return {"recorded": recorded, "packet": packet, "recorded_at": timestamp}


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        host = ""
    if host.startswith("www."):
        host = host[4:]
    return host or url


def record_outcome(
    packet_id: str, target_keys: list[str], source_ledger: list[dict], *, dry_run: bool = False
) -> dict:
    """Fold a completed packet's self-reported source productivity into the ledger.

    This is the research-quality feedback layer: record() above only tracks
    that a target was covered, not whether the coverage was any good. Each
    source_ledger entry's `productive` flag is attributed to every target the
    packet was scoped to (the schema has no per-URL target association) and
    also rolled up per source domain, so a domain that keeps producing
    nothing can eventually be deprioritized -- not wired into plan()'s
    ordering yet; that is a deliberate follow-up once real outcome data
    accumulates, not an oversight.
    """
    state = sync()
    timestamp = _now().isoformat(timespec="seconds")
    productive = sum(1 for entry in source_ledger if entry.get("productive"))
    unproductive = len(source_ledger) - productive
    scored = []
    for key in target_keys:
        row = state["targets"].get(key)
        if not row:
            continue
        row["productive_pages"] = int(row.get("productive_pages") or 0) + productive
        row["unproductive_pages"] = int(row.get("unproductive_pages") or 0) + unproductive
        row["last_outcome_packet"] = packet_id
        scored.append(key)
    sources = state.setdefault("sources", {})
    for entry in source_ledger:
        url = entry.get("url")
        if not url:
            continue
        source_row = sources.setdefault(
            _domain(url), {"checked": 0, "productive": 0, "unproductive": 0, "last_checked_at": None}
        )
        source_row["checked"] += 1
        if entry.get("productive"):
            source_row["productive"] += 1
        else:
            source_row["unproductive"] += 1
        source_row["last_checked_at"] = timestamp
    if not dry_run:
        state["updated_at"] = timestamp
        STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return {
        "packet_id": packet_id,
        "targets_scored": scored,
        "pages_scored": len(source_ledger),
        "productive_pages": productive,
        "unproductive_pages": unproductive,
        "recorded_at": timestamp,
    }


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _load_sidecar_manifest() -> dict:
    if SIDECAR_MANIFEST_PATH.exists():
        try:
            manifest = json.loads(SIDECAR_MANIFEST_PATH.read_text(encoding="utf-8"))
            if isinstance(manifest, dict) and isinstance(manifest.get("processed"), dict):
                return manifest
        except Exception:
            pass
    return {"processed": {}}


def _save_sidecar_manifest(manifest: dict) -> None:
    SIDECAR_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    SIDECAR_MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def sweep_sidecars(*, dry_run: bool = False) -> list[dict]:
    """Fold every new deep-research JSON sidecar in DROP_DIR into the ledger.

    Sidecars pair with their `.md` packet by basename (see
    system/templates/deep_research_intelligence_drop.md). capture_ingest.py's
    own convention never deletes source files from this drop folder, so a
    sidecar needs its own file-hash manifest -- otherwise every pipeline run
    would re-record the same packet's outcome, the same class of bug this
    session already fixed once in hubspot_ingest.py's manifest-less scan().
    A malformed sidecar (bad JSON, wrong shape) is reported and still marked
    processed rather than retried forever, since re-parsing it will never
    succeed without a human fixing the file.
    """
    if not DROP_DIR.is_dir():
        return []
    manifest = _load_sidecar_manifest()
    results: list[dict] = []
    newly_processed: dict[str, dict] = {}
    for path in sorted(DROP_DIR.glob("*.json")):
        fhash = _file_hash(path)
        if fhash in manifest["processed"]:
            continue
        entry: dict = {"path": str(path)}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            target_keys = data.get("targets") or []
            source_ledger = data.get("source_ledger") or []
            if not isinstance(target_keys, list) or not isinstance(source_ledger, list):
                raise ValueError("targets and source_ledger must be lists")
            packet_id = data.get("packet_id") or path.stem
            outcome = record_outcome(packet_id, target_keys, source_ledger, dry_run=dry_run)
            entry.update(ok=True, **outcome)
        except (OSError, ValueError) as exc:
            entry.update(ok=False, error=str(exc))
        results.append(entry)
        if not dry_run:
            newly_processed[fhash] = {"path": str(path), "processed_at": date.today().isoformat()}
    if newly_processed and not dry_run:
        manifest["processed"].update(newly_processed)
        _save_sidecar_manifest(manifest)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync")
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--limit", type=int, default=5)
    record_parser = sub.add_parser("record")
    record_parser.add_argument("--packet", required=True)
    record_parser.add_argument("--target", action="append", default=[])
    sweep_parser = sub.add_parser("sweep-sidecars")
    sweep_parser.add_argument("--dry-run", action="store_true")
    sub.add_parser("domain-quality")
    args = parser.parse_args()
    if args.command == "sync":
        result = sync()
    elif args.command == "plan":
        result = plan(args.limit)
    elif args.command == "record":
        result = record(args.packet, args.target)
    elif args.command == "sweep-sidecars":
        result = {"results": sweep_sidecars(dry_run=args.dry_run)}
    else:
        result = _read_json(STATE_PATH, {}).get("sources", {})
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
