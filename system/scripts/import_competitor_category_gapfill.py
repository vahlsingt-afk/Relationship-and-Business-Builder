#!/usr/bin/env python3
"""Import researched competitor portfolio/category gap-fill packets."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import competitor_intelligence_common as cic  # noqa: E402
import rb_core as core  # noqa: E402

ARTIFACT_TYPE = "rbb_competitor_category_gapfill"
RECEIPT_PATH = core.CACHE_DIR / "competitor_category_gapfill_ingest.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_category_gapfill_dataset(data: Any) -> bool:
    return bool(
        isinstance(data, dict)
        and data.get("artifact_type") == ARTIFACT_TYPE
        and isinstance(data.get("competitors"), list)
    )


def classify_bytes(content: bytes) -> bool:
    try:
        return is_category_gapfill_dataset(json.loads(content.decode("utf-8-sig")))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False


def _confidence(value: Any) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "medium"
    return "critical" if score >= 95 else ("high" if score >= 80 else ("medium" if score >= 55 else "low"))


def _source_url(row: dict) -> str | None:
    for source in row.get("sources") or []:
        if isinstance(source, dict) and source.get("url"):
            return str(source["url"])
    return None


def _extended(value: str, row: dict, packet_id: str, *, finding_type: str | None = None) -> dict:
    return {
        "value": value,
        "status": "confirmed",
        "evidence_ids": [],
        "confidence": _confidence(row.get("confidence")),
        "as_of": row.get("as_of") or row.get("date"),
        "scope": "competitor",
        "last_reviewed_by": "system:competitor_category_gapfill",
        "source_url": _source_url(row) or f"rbb-packet:{packet_id}",
        "finding_type": finding_type or row.get("finding_type") or "vendor_stated",
        "source_type": "structured_research_packet",
        "is_vendor_claim": (finding_type or row.get("finding_type")) == "vendor_stated",
        "is_inference": (finding_type or row.get("finding_type")) == "inference",
    }


def _merge_extended(existing: list, incoming: list) -> tuple[list, int, int]:
    out = list(existing or [])
    seen = {str(row.get("value") or "").strip().casefold() for row in out if isinstance(row, dict)}
    added = unchanged = 0
    for row in incoming:
        key = str(row.get("value") or "").strip().casefold()
        if not key or key in seen:
            unchanged += 1
            continue
        out.append(row)
        seen.add(key)
        added += 1
    return out, added, unchanged


def ingest(path: Path, *, dry_run: bool = True) -> dict:
    packet = json.loads(path.read_text(encoding="utf-8-sig"))
    if not is_category_gapfill_dataset(packet):
        raise ValueError(f"not a competitor category gap-fill dataset: {path}")
    packet_id = packet.get("packet_id") or path.stem
    counts = {
        "records_seen": 0, "competitors_resolved": 0, "competitors_unresolved": 0,
        "category_sets_changed": 0, "category_sets_unchanged": 0,
        "profile_fields_added": 0, "profile_fields_unchanged": 0,
        "cos_commentary_written": 0,
    }
    unresolved: list[str] = []
    planned: list[tuple[Path, dict]] = []

    for rec in packet.get("competitors") or []:
        counts["records_seen"] += 1
        slug = str(rec.get("competitor_slug") or "").strip()
        try:
            comp_path = cic.competitor_dir(slug) / "competitor.json"
        except (FileNotFoundError, ValueError):
            counts["competitors_unresolved"] += 1
            unresolved.append(slug or str(rec.get("display_name") or "unknown"))
            continue
        comp = cic.load_json(comp_path)
        counts["competitors_resolved"] += 1
        categories = sorted(set(rec.get("genius_competes_on") or []))
        invalid = [cat for cat in categories if cat not in cic.GENIUS_PRODUCT_LINES]
        if invalid:
            raise ValueError(f"{slug}: invalid Genius categories {invalid}")
        if sorted(comp.get("competes_on") or []) == categories:
            counts["category_sets_unchanged"] += 1
        else:
            comp["competes_on"] = categories
            counts["category_sets_changed"] += 1

        for key in ("canonical_company_name", "website", "ownership", "operating_status",
                    "relationship_class_recommendation", "restaurant_tech_categories",
                    "negative_findings", "conflicts_notes"):
            value = rec.get(key)
            # Empty arrays for researched category/negative/conflict fields are
            # meaningful: they distinguish "assessed and none found" from an
            # untouched competitor shell.
            if value in (None, "") or (value == [] and key not in {
                "restaurant_tech_categories", "negative_findings", "conflicts_notes"
            }):
                continue
            if comp.get(key) == value:
                counts["profile_fields_unchanged"] += 1
            else:
                comp[key] = value
                counts["profile_fields_added"] += 1

        comp["category_assessment_status"] = "researched"
        comp["category_assessment_packet_id"] = packet_id
        comp["category_assessed_at"] = packet.get("generated_date") or cic.today()

        aliases = list(comp.get("aliases") or [])
        for alias in rec.get("aliases") or []:
            if alias and alias.casefold() not in {a.casefold() for a in aliases}:
                aliases.append(alias)
                counts["profile_fields_added"] += 1
        comp["aliases"] = aliases

        positioning = rec.get("positioning_summary") or {}
        if isinstance(positioning, dict) and positioning.get("value"):
            if comp.get("positioning_summary") == positioning["value"]:
                counts["profile_fields_unchanged"] += 1
            else:
                comp["positioning_summary"] = positioning["value"]
                counts["profile_fields_added"] += 1

        mappings = {
            "products": "products", "strengths": "strengths",
            "weaknesses_and_risks": "vulnerabilities", "key_customers": "key_customers",
            "recent_news": "recent_news", "product_lineage": "product_lineage",
        }
        for source_key, target_key in mappings.items():
            incoming = []
            for row in rec.get(source_key) or []:
                if not isinstance(row, dict):
                    continue
                value = row.get("value") or row.get("name")
                if source_key == "products" and row.get("categories"):
                    value = f"{value} ({', '.join(row['categories'])})"
                if not value:
                    continue
                kind = row.get("finding_type")
                if source_key == "strengths" and row.get("evidence_class") == "vendor_claim":
                    kind = "vendor_stated"
                elif source_key == "weaknesses_and_risks" and row.get("evidence_class") == "research_gap":
                    kind = "inference"
                incoming.append(_extended(str(value), row, packet_id, finding_type=kind))
            comp[target_key], added, unchanged = _merge_extended(comp.get(target_key) or [], incoming)
            counts["profile_fields_added"] += added
            counts["profile_fields_unchanged"] += unchanged

        commentary = rec.get("cos_commentary")
        if isinstance(commentary, dict) and commentary:
            if comp.get("latest_cos_commentary") == commentary:
                counts["profile_fields_unchanged"] += 1
            else:
                comp["latest_cos_commentary"] = commentary
                counts["cos_commentary_written"] += 1
        comp["last_evidence_date"] = packet.get("generated_date") or cic.today()
        comp["updated_at"] = cic.now_iso()
        planned.append((comp_path, comp))

    result = {
        "ok": True, "dataset_type": "competitor_category_gapfill", "packet_id": packet_id,
        "source_file": str(path), "dry_run": dry_run, "counts": counts,
        "unresolved_competitors": unresolved, "completed_at": _now(),
    }
    if dry_run:
        return result

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = core.SNAPSHOTS_DIR / f"competitor_category_gapfill.{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for comp_path, comp in planned:
        shutil.copy2(comp_path, backup_dir / f"{comp_path.parent.name}.json")
        cic.save_json_atomic(comp_path, comp)
        cic.register_competitor(comp["competitor_slug"], comp.get("display_name") or comp["competitor_slug"])
    result["backup_dir"] = str(backup_dir)
    RECEIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    print(json.dumps(ingest(Path(args.file), dry_run=not args.confirm), indent=2))
