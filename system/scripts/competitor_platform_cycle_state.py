#!/usr/bin/env python3
"""Maintain the dedicated named-competitor platform research cycle state."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import competitor_intelligence_common as cic  # noqa: E402
import import_competitor_platform_research as icpr  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE = ROOT / "system/research/competitor_platform_cycle_2026-09-28.json"

# RB-DEFECT-073 (2026-09-28): the original 3-value status
# ({pending, complete, blocked}) let a competitor be marked "complete" from
# Markdown structural validation alone -- confirmed live, toast/
# par-technology/nory/qu/global-payments were all "complete" with zero
# populated canonical fields. The 6-value enum ties completion to a real
# import outcome: a packet can be validated (packet_validated) without its
# findings being importable yet; findings can be applied without every
# target reaching full canonical readback (import_applied); anything
# queued for human confirmation is import_partial_review_required, not
# silently "complete".
STATUS_PENDING_RESEARCH = "pending_research"
STATUS_PACKET_VALIDATED = "packet_validated"
STATUS_IMPORT_APPLIED = "import_applied"
STATUS_IMPORT_PARTIAL_REVIEW_REQUIRED = "import_partial_review_required"
STATUS_BLOCKED = "blocked"
STATUS_COMPLETE = "complete"
VALID_STATUSES = {
    STATUS_PENDING_RESEARCH, STATUS_PACKET_VALIDATED, STATUS_IMPORT_APPLIED,
    STATUS_IMPORT_PARTIAL_REVIEW_REQUIRED, STATUS_BLOCKED, STATUS_COMPLETE,
}
# Legacy (pre-2026-09-28) status values this file may still hold on disk.
_LEGACY_STATUS_MAP = {"pending": STATUS_PENDING_RESEARCH, "blocked": STATUS_BLOCKED}

EXPECTED_BATCH_TARGETS = {
    # RB-DEFECT-073 (2026-09-28): was "competitor:global-payments" --
    # Genius's own parent company must never be a competitor-scoped
    # research target (see cic.is_own_company() /
    # import_competitor_platform_research.migrate_global_payments_to_
    # parent_evidence()). "genius:parent" is the corrected target key for
    # any future re-run of this specific batch's export.
    "genius:parent",
    "competitor:par-technology",
    "competitor:toast",
    "competitor:nory",
    "competitor:qu",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def live_roster() -> dict:
    result = subprocess.run(
        [sys.executable, "system/scripts/rb_cli.py", "call", "listCompetitors", "{}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines or lines[0].strip() != "HTTP 200":
        raise RuntimeError(f"listCompetitors did not return HTTP 200: {result.stdout}")
    return json.loads("\n".join(lines[1:]))


def load_state(path: Path) -> dict:
    if not path.exists():
        return {
            "cycle_id": "competitor_platform_cycle_2026-09-28",
            "brief": "Fresh platform-level strengths/weaknesses baseline",
            "created_at": now_utc(),
            "updated_at": None,
            "live_roster_snapshot": {},
            "competitors": [],
        }
    return json.loads(path.read_text())


def _canonical_readback_populated(slug: str) -> bool:
    """True if the competitor's real competitor.json has at least one
    populated extended-profile field or a non-empty trends value -- the
    honest readback check acceptance criterion #10/#11 requires before a
    target can be called complete."""
    try:
        comp = cic.load_competitor(slug)["competitor"]
    except FileNotFoundError:
        return False
    full = cic.get_extended_profile(comp)
    if any(full.get(f) for f in cic.EXTENDED_PROFILE_FIELDS):
        return True
    return bool((full.get("trends") or {}).get("value"))


def _derive_status_for_legacy_complete(item: dict) -> str:
    """Re-derive a pre-2026-09-28 "complete" entry's real status instead of
    blindly carrying it forward -- the whole point of RB-DEFECT-073. A
    packet that predates the findings schema has nothing importable yet,
    so it honestly becomes import_partial_review_required (packet exists,
    findings don't) rather than staying falsely complete."""
    if _canonical_readback_populated(item["slug"]):
        return STATUS_COMPLETE
    if item.get("qualifying_packet_path"):
        return STATUS_IMPORT_PARTIAL_REVIEW_REQUIRED
    return STATUS_PENDING_RESEARCH


def merge(path: Path) -> dict:
    roster = live_roster()
    state = load_state(path)
    existing = {item["slug"]: item for item in state.get("competitors", [])}
    merged = []
    for live in roster["competitors"]:
        slug = live["competitor_slug"]
        prior = existing.get(slug, {})
        status = prior.get("status", STATUS_PENDING_RESEARCH)
        if status not in VALID_STATUSES:
            status = _LEGACY_STATUS_MAP.get(status, STATUS_PENDING_RESEARCH)
        # RB-DEFECT-073: "complete" is a valid status string under BOTH the
        # old (Markdown-structural-validation-only) and new (canonical-
        # readback-verified) semantics, so a stored "complete" can't be
        # trusted just because the string itself is still valid -- always
        # re-verify it against real canonical data on every merge, the same
        # self-healing discipline load_registry() already uses for a
        # corrupted registry. Cheap (one competitor.json read) and
        # idempotent: an already-honest "complete" re-derives to the same
        # value every time.
        if status == STATUS_COMPLETE:
            status = _derive_status_for_legacy_complete({**prior, "slug": slug})
        item = {
            "slug": slug,
            "display_name": live["display_name"],
            "relationship_class": live["relationship_class"],
            "status": status,
            "qualifying_packet_path": prior.get("qualifying_packet_path"),
            "products_platforms_covered": prior.get("products_platforms_covered", []),
            "completed_at": prior.get("completed_at"),
            "blocker": prior.get("blocker"),
        }
        merged.append(item)
    stamp = now_utc()
    state["updated_at"] = stamp
    state["live_roster_snapshot"] = {
        "captured_at": stamp,
        "competitor_count": roster["competitor_count"],
        "relationship_class_counts": roster["relationship_class_counts"],
        "entries": [
            {
                "slug": item["competitor_slug"],
                "display_name": item["display_name"],
                "relationship_class": item["relationship_class"],
            }
            for item in roster["competitors"]
        ],
    }
    state["competitors"] = merged
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    return state


def select_targets(state: dict, count: int) -> list[dict]:
    pending = [item for item in state["competitors"] if item["status"] == STATUS_PENDING_RESEARCH]
    class_rank = {
        "product_line_competitor": 1,
        "adjacent_ecosystem_vendor": 2,
        "unclassified_vendor": 3,
    }
    order = {item["slug"]: i for i, item in enumerate(state["competitors"])}

    def rank(item: dict) -> tuple[int, int]:
        if item["slug"] == "global-payments":
            return (0, order[item["slug"]])
        return (class_rank.get(item["relationship_class"], 4), order[item["slug"]])

    return sorted(pending, key=rank)[:count]


def ingest_export(state_path: Path, export_path: Path) -> dict:
    raw = export_path.read_text()
    match = re.fullmatch(
        r"\s*```markdown\s*\n(?P<markdown>.*?)\n```\s*\n\s*```json\s*\n(?P<json>.*?)\n```\s*",
        raw,
        flags=re.DOTALL,
    )
    if not match:
        raise RuntimeError("Export does not contain exactly one Markdown block and one JSON block")
    markdown = match.group("markdown").strip() + "\n"
    sidecar = json.loads(match.group("json"))
    targets = set(sidecar.get("targets", []))
    if targets != EXPECTED_BATCH_TARGETS:
        raise RuntimeError(f"Unexpected targets: {sorted(targets)}")
    required_markers = [
        "## Per-company and per-platform matrix",
        "## Detailed evidence observations",
        "## Company/competitor profile observations",
        "## Negative findings",
        "## Source ledger",
        "## Research notes and inferences",
        "Vendor-stated",
        "Marketplace strength",
        "Marketplace shortcomings",
    ]
    missing = [marker for marker in required_markers if marker not in markdown]
    if missing:
        raise RuntimeError(f"Markdown is missing required sections/markers: {missing}")
    for target in sorted(targets):
        if target not in markdown:
            raise RuntimeError(f"Markdown does not contain target {target}")
    ledger = sidecar.get("source_ledger", [])
    if not ledger or sidecar.get("pages_reviewed") != len(ledger):
        raise RuntimeError("JSON source ledger is empty or does not match pages_reviewed")
    missing_urls = [entry["url"] for entry in ledger if entry.get("url") not in markdown]
    if missing_urls:
        raise RuntimeError(f"JSON ledger URLs missing from Markdown: {missing_urls}")

    packet_id = sidecar["packet_id"]
    packet_stamp = re.search(r"dr-(\d{8})-(\d{4})-", packet_id)
    if not packet_stamp:
        raise RuntimeError(f"Packet ID has no timestamp: {packet_id}")
    day, hm = packet_stamp.groups()
    basename = f"{day[:4]}-{day[4:6]}-{day[6:]}_{hm}_competitor-platforms_deep-research"
    inbox = ROOT / "system/inbox/chatgpt_intelligence_drop"
    inbox.mkdir(parents=True, exist_ok=True)
    markdown_path = inbox / f"{basename}.md"
    json_path = inbox / f"{basename}.json"
    if markdown_path.exists() or json_path.exists():
        raise RuntimeError(f"Refusing to overwrite existing packet basename {basename}")
    markdown_path.write_text(markdown)
    json_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n")

    products: dict[str, list[str]] = {target: [] for target in targets}
    row_pattern = re.compile(r"^\| `(?P<target>(?:competitor|genius):[^`]+)` \| \*\*(?P<product>.+?)\*\* \|", re.MULTILINE)
    for row in row_pattern.finditer(markdown):
        target = row.group("target")
        if target in products:
            products[target].append(row.group("product"))
    empty_products = [target for target, values in products.items() if not values]
    if empty_products:
        raise RuntimeError(f"No platform matrix rows found for: {empty_products}")

    # RB-DEFECT-073 (2026-09-28): Markdown/ledger structural validation
    # above proves the export is well-formed -- it never proved the
    # findings actually landed in a canonical RBB record. Run the real
    # importer against this sidecar's "findings" array (if present) before
    # deciding each target's status; a target only reaches STATUS_COMPLETE
    # once its findings have a real disposition AND canonical readback
    # confirms the fields are actually populated.
    import_result = None
    if sidecar.get("findings"):
        import_result = icpr.import_findings(sidecar, packet_id=packet_id, dry_run=False)
    by_target_outcome = (import_result or {}).get("by_target", {})

    state = load_state(state_path)
    completed_at = now_utc()
    by_slug = {item["slug"]: item for item in state["competitors"]}
    for target in targets:
        target_kind, _, target_key = target.partition(":")
        if target_kind == "genius":
            # Genius/parent-scope targets aren't tracked in this
            # competitor-focused cycle-state file at all -- nothing to
            # update here, but the import above still ran for this target.
            continue
        slug = target_key
        if slug not in by_slug:
            raise RuntimeError(f"Target is not present in cycle state: {slug}")
        item = by_slug[slug]
        outcome = by_target_outcome.get(target, {})
        queued_or_rejected = outcome.get("queued", 0) + outcome.get("rejected_identity", 0)
        has_findings_for_target = bool(outcome)
        if not has_findings_for_target:
            status = STATUS_PACKET_VALIDATED
            blocker = "Export contains no structured findings for this target yet."
        elif queued_or_rejected:
            status = STATUS_IMPORT_PARTIAL_REVIEW_REQUIRED
            blocker = f"{queued_or_rejected} finding(s) require review/confirmation before this target is complete."
        elif _canonical_readback_populated(slug):
            status = STATUS_COMPLETE
            blocker = None
        else:
            status = STATUS_IMPORT_APPLIED
            blocker = "Findings applied, but canonical readback found no populated fields yet."
        item["status"] = status
        item["qualifying_packet_path"] = str(markdown_path.relative_to(ROOT))
        item["products_platforms_covered"] = products[target]
        item["completed_at"] = completed_at if status == STATUS_COMPLETE else item.get("completed_at")
        item["blocker"] = blocker
        item["import_receipt"] = outcome or None
    state["updated_at"] = completed_at
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    return {
        "markdown_path": str(markdown_path),
        "json_path": str(json_path),
        "targets": sorted(targets),
        "pages_reviewed": sidecar["pages_reviewed"],
        "products": products,
        "completed_at": completed_at,
        "import_result": import_result,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--select", type=int, default=0)
    parser.add_argument("--ingest-export", type=Path)
    args = parser.parse_args()
    if args.ingest_export:
        print(json.dumps(ingest_export(args.state, args.ingest_export), indent=2))
        return
    state = merge(args.state)
    selected = select_targets(state, args.select) if args.select else []
    status_counts = {
        status: sum(i["status"] == status for i in state["competitors"])
        for status in sorted(VALID_STATUSES)
    }
    print(json.dumps({
        "state_path": str(args.state),
        "live_count": len(state["competitors"]),
        "status_counts": status_counts,
        "pending_count": status_counts[STATUS_PENDING_RESEARCH],
        "complete_count": status_counts[STATUS_COMPLETE],
        "blocked_count": status_counts[STATUS_BLOCKED],
        "selected": selected,
    }, indent=2))


if __name__ == "__main__":
    main()
