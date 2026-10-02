#!/usr/bin/env python3
"""
hubspot_ingest.py — HubSpot CRM export ingest (RB-DEFECT-064 Phase 1).

Recognized artifact type: hubspot_crm_export (see dataset_classifier.py)
Intent: historical_relationship_database / baseline enhancement (Tenet 6)

Proof case for the generalized structured-dataset ingestion framework
requested in RB-DEFECT-064: a CRM export should be recognized and ingested
without the operator having to explain what the file is. Scoped narrower
than linkedin_ingest.py on purpose — no career-move/RC-tier narrative layer,
no Person->Industry/Event/Opportunity/Meeting graph edges (a CRM "export
contacts" CSV carries none of that data; the original request's Stage 5 is
still open for a source that actually has it).

5 stages:
  Stage 1 — Parse:     CSV -> raw contact rows
  Stage 2 — Resolve:   Email (exact) -> unique Name -> unmatched
  Stage 3 — Classify:  matched_update / new_person / duplicate_candidate
  Stage 4 — Mutate:    Enhance baseline (never overwrite operator-confirmed
                        fields silently), snapshot first, write delta report
  Stage 5 — Intelligence (RB-DEFECT-064 Phase 4): reuse rb_core's existing
                        broker-scoring and dormancy signal (no new graph
                        store) to surface dormant relationships this import
                        resurfaced and warm-intro candidates for new contacts.

Drop location:
    system/inbox/crm_exports/   (*.csv)

Outputs:
    system/_snapshots/baseline_index.pre-hubspot-ingest-<date>.json
    system/baseline_index.json                     — enhanced, not replaced
    system/deltas/hubspot_crm_export_<date>.md      — mutation report
    system/.cache/hubspot_ingest_latest.json        — machine-readable summary

CLI:
    python3 hubspot_ingest.py --file PATH [--dry-run] [--date YYYY-MM-DD]
    python3 hubspot_ingest.py --scan [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import dataset_classifier as classifier  # noqa: E402
import identity_matcher as im  # noqa: E402
import mutation_report as mr  # noqa: E402
import post_ingest_intelligence as pii  # noqa: E402

EXPORTS_DIR = core.INBOX_DIR / "crm_exports"
LATEST_PATH = core.CACHE_DIR / "hubspot_ingest_latest.json"
DELTAS_DIR = core.SYSTEM_DIR / "deltas"
MANIFEST_PATH = core.CACHE_DIR / "hubspot_ingest_manifest.json"

# HubSpot's own header vocabulary for the fields we act on. HubSpot export
# column names are stable across accounts for these; unrecognized/custom
# columns are ignored rather than guessed at.
FIELD_ALIASES = {
    "first_name": ("First Name",),
    "last_name": ("Last Name",),
    "email": ("Email",),
    "phone": ("Phone Number",),
    "company": ("Company Name",),
    "title": ("Job Title",),
    "city": ("City",),
    "state": ("State/Region",),
}


# ---------------------------------------------------------------------------
# Stage 1 — Parse
# ---------------------------------------------------------------------------

def _field(row: dict[str, str], key: str) -> str:
    for alias in FIELD_ALIASES[key]:
        if alias in row and row[alias]:
            return row[alias].strip()
    return ""


def _display_name(row: dict[str, str]) -> str:
    return f"{_field(row, 'first_name')} {_field(row, 'last_name')}".strip()


def parse_csv(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader if any((v or "").strip() for v in row.values())]


# ---------------------------------------------------------------------------
# Stage 2 — Identity resolution
# ---------------------------------------------------------------------------

def _build_matchers(baseline: list[dict]) -> dict[str, Any]:
    return {
        "by_email": im.build_email_index(baseline),
        "by_name": im.build_name_index(baseline),
    }


def match_row(row: dict[str, str], matchers: dict[str, Any]) -> tuple[dict | None, str]:
    """Return (matched baseline entry or None, match_basis)."""
    email = _field(row, "email").strip().lower()
    if email and email in matchers["by_email"]:
        return matchers["by_email"][email], "email"

    name = _display_name(row)
    match, ambiguous = im.match_unique_name(name, matchers["by_name"])
    if match is not None:
        return match, "name"
    if ambiguous:
        return None, "duplicate_candidate"

    return None, "unmatched"


# ---------------------------------------------------------------------------
# Stage 3/4 — Classify + mutate
# ---------------------------------------------------------------------------

def _append_note(entry: dict, text: str) -> None:
    existing = entry.get("notes") or ""
    entry["notes"] = f"{existing}\n{text}".strip() if existing else text


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(core.PROJECT_DIR))
    except ValueError:
        return str(path)


def _slug(name: str, existing_ids: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "hubspot-contact"
    if base not in existing_ids:
        existing_ids.add(base)
        return base
    i = 2
    while f"{base}-{i}" in existing_ids:
        i += 1
    candidate = f"{base}-{i}"
    existing_ids.add(candidate)
    return candidate


def _apply_update(entry: dict, row: dict[str, str], *, source_tag: str, d: date, report: dict) -> bool:
    """Enhance an existing entry in place. Returns True if anything changed."""
    changed = False
    entry.setdefault("sources", [])
    if source_tag not in entry["sources"]:
        entry["sources"].append(source_tag)
        changed = True

    email = _field(row, "email")
    if email and not entry.get("email"):
        entry["email"] = email
        changed = True

    phone = _field(row, "phone")
    if phone and not entry.get("phone"):
        entry["phone"] = phone
        changed = True

    company = _field(row, "company")
    old_company = entry.get("current_company")
    if company and im.norm_name(company) != im.norm_name(old_company):
        if old_company:
            # Conflicting company on file — never silently overwrite existing
            # canonical data (same guardrail as linkedin_ingest.py). Surface
            # as a conflict for the operator instead. HubSpot's own CSV
            # export carries no reliable "as of" date for this field (see
            # FIELD_ALIASES -- only name/email/phone/company/title/city/
            # state), so there's no basis to auto-sequence this the way a
            # genuinely dated conflict could; requiring confirmation here is
            # the CORRECT behavior per Todd's canonical mutation policy, not
            # a gap.
            #
            # RB-DEFECT-2026-09-18: what WAS a real gap -- this function had
            # no awareness of whether the identical conflict was already
            # logged. Combined with scan()'s complete absence of any file-
            # hash/manifest dedup (fixed below), a CSV sitting in system/
            # inbox/crm_exports/ across scheduled runs re-appended this
            # exact conflict note, unbounded, once per run, forever, growing
            # `notes` without limit. Same "don't repeat an already-recorded
            # observation" discipline intelligence_mutation_engine.py's
            # thesis_alignment_detected handler already uses for its own
            # note field. This check is deliberately independent of the
            # manifest fix -- a genuinely NEW/updated export that still
            # shows the same unresolved disagreement must not re-spam it
            # either.
            conflict_marker = f"CONFLICT: HubSpot reads company {company}; canonical retained as {old_company}."
            if conflict_marker not in (entry.get("notes") or ""):
                _append_note(entry, f"[{d.isoformat()}] {conflict_marker} Confirm.")
            report["conflicts"].append({"name": entry["name"], "field": "current_company", "canonical": old_company, "hubspot": company})
        else:
            entry["current_company"] = company
            entry["current_role"] = entry.get("current_role") or _field(row, "title") or None
            _append_note(entry, f"[{d.isoformat()}] HubSpot: company enrichment -> {company}.")
            changed = True

    title = _field(row, "title")
    if title and not entry.get("current_role"):
        entry["current_role"] = title
        changed = True

    if changed:
        report["updated"].append(entry["name"])
        report["updated_ids"].append(entry["id"])
    return changed


def _create_entry(row: dict[str, str], *, source_tag: str, existing_ids: set[str]) -> dict:
    name = _display_name(row) or "Unknown HubSpot Contact"
    return {
        "id": _slug(name, existing_ids),
        "name": name,
        "current_company": _field(row, "company") or None,
        "current_role": _field(row, "title") or None,
        "location": ", ".join(v for v in (_field(row, "city"), _field(row, "state")) if v) or None,
        "linkedin_url": None,
        "email": _field(row, "email") or None,
        "phone": _field(row, "phone") or None,
        "sources": [source_tag],
        "signal_class": "VC",
        "rc_state": None,
        "rc_tier": None,
        "last_touch": None,
        "circles": [],
        "tags": [],
        "notes": "",
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def ingest(
    path: str | Path,
    *,
    ingest_date: str | None = None,
    dry_run: bool = False,
    baseline_path: Path | None = None,
    threads: list[dict] | None = None,
) -> dict[str, Any]:
    """`baseline_path` overrides where baseline is read from/written to — tests
    point this at a tmp_path fixture so they never touch the real baseline.
    `threads` overrides active-threads context for warm-intro scoring (tests
    pass `[]` so they never depend on the real active_threads.yaml)."""
    src = Path(path)
    d = date.fromisoformat(ingest_date) if ingest_date else date.today()
    source_tag = f"hubspot_crm_export_{d.isoformat()}"
    baseline_path = baseline_path or core.BASELINE_PATH

    classification = classifier.classify(src)
    if classification.dataset_type != "hubspot_crm_export":
        return {
            "ok": False,
            "error": f"{src.name} did not classify as hubspot_crm_export "
                     f"(got {classification.dataset_type}, confidence {classification.confidence})",
            "classification": classification.to_dict(),
        }

    rows = parse_csv(src)
    baseline = core.load_baseline(path=baseline_path)
    existing_ids = {e["id"] for e in baseline}
    matchers = _build_matchers(baseline)

    report: dict[str, Any] = {
        "updated": [], "updated_ids": [], "created": [], "duplicate_candidates": [], "conflicts": [],
        "companies_seen": set(),
    }
    new_entries: list[dict] = []

    for row in rows:
        if not _display_name(row) and not _field(row, "email"):
            continue  # no identifying data at all — nothing actionable

        entry, basis = match_row(row, matchers)
        company = _field(row, "company")
        if company:
            report["companies_seen"].add(company)

        if entry is not None:
            _apply_update(entry, row, source_tag=source_tag, d=d, report=report)
        elif basis == "duplicate_candidate":
            report["duplicate_candidates"].append({"name": _display_name(row), "email": _field(row, "email")})
        else:
            new_entry = _create_entry(row, source_tag=source_tag, existing_ids=existing_ids)
            new_entries.append(new_entry)
            report["created"].append(new_entry["name"])

    existing_companies = {
        im.norm_name(e.get("current_company")) for e in baseline if e.get("current_company")
    }
    companies_added = sorted(
        c for c in report["companies_seen"] if im.norm_name(c) not in existing_companies
    )

    merged = baseline + new_entries

    baseline_by_id = {e["id"]: e for e in baseline}
    dormant = pii.dormant_relationships_resurfaced(report["updated_ids"], baseline_by_id, today=d)
    warm_intros = pii.warm_intro_candidates(new_entries, merged, today=d, threads=threads)

    result: dict[str, Any] = {
        "ok": True,
        "date": d.isoformat(),
        "source_tag": source_tag,
        "classification": classification.to_dict(),
        "people_imported": len(rows),
        "existing_people_updated": len(report["updated"]),
        "new_people_created": len(report["created"]),
        "duplicate_candidates": len(report["duplicate_candidates"]),
        "duplicate_candidate_detail": report["duplicate_candidates"],
        "companies_added": companies_added,
        "conflicts": report["conflicts"],
        "knowledge_mutations_applied": len(report["updated"]) + len(report["created"]),
        "relationship_links_created": None,  # Person->Company/Industry/Event/Opportunity/Meeting
                                              # edges: this CSV carries none of that data (see module docstring)
        "dormant_relationships_resurfaced": dormant,
        "warm_intro_candidates": warm_intros,
        "dry_run": dry_run,
    }

    if dry_run:
        return result

    core.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = core.SNAPSHOTS_DIR / f"baseline_index.pre-hubspot-ingest-{d.isoformat()}.json"
    if snapshot_path.exists():
        snapshot_path = core.SNAPSHOTS_DIR / f"baseline_index.pre-hubspot-ingest-{d.isoformat()}-{len(baseline)}.json"
    snapshot_path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")

    baseline_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")

    DELTAS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = DELTAS_DIR / f"hubspot_crm_export_{d.isoformat()}.md"
    report_path.write_text(_render_report(result), encoding="utf-8")
    result["report_path"] = _display_path(report_path)
    result["snapshot_path"] = _display_path(snapshot_path)

    core.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    return result


def _render_report(result: dict[str, Any]) -> str:
    report = mr.MutationReport(
        source_label="HubSpot CRM Export",
        date=result["date"],
        people_imported=result["people_imported"],
        existing_people_updated=result["existing_people_updated"],
        new_people_created=result["new_people_created"],
        duplicate_candidates=result["duplicate_candidates"],
        companies_added=len(result["companies_added"]),
        relationship_links_created=None,
        knowledge_mutations_applied=result["knowledge_mutations_applied"],
        confidence=result["classification"]["confidence"],
        not_computed_reasons=[
            "Relationship Links Created not computed — a HubSpot contacts CSV carries no "
            "Industry/Event/Opportunity/Meeting data to link (RB-DEFECT-064 Stage 5, still open).",
        ],
    )
    rendered = report.render_markdown().rstrip("\n")
    title, _, rest = rendered.partition("\n")
    lines = [
        title,
        "",
        f"**Source tag:** `{result['source_tag']}`",
        "",
        rest.lstrip("\n"),
        "",
    ]
    if result["companies_added"]:
        lines.append("## Companies Added")
        lines.append("")
        for c in result["companies_added"]:
            lines.append(f"- {c}")
        lines.append("")
    if result["duplicate_candidate_detail"]:
        lines.append("## Duplicate Candidates (needs operator confirmation)")
        lines.append("")
        for dup in result["duplicate_candidate_detail"]:
            lines.append(f"- {dup['name']} ({dup['email'] or 'no email'}) — multiple baseline entries share this name")
        lines.append("")
    if result["conflicts"]:
        lines.append("## Conflicts (canonical retained, not overwritten)")
        lines.append("")
        for c in result["conflicts"]:
            lines.append(f"- {c['name']}: {c['field']} — canonical `{c['canonical']}` vs. HubSpot `{c['hubspot']}`")
        lines.append("")

    dormant = result.get("dormant_relationships_resurfaced") or []
    warm_intros = result.get("warm_intro_candidates") or []
    lines.append("## Intelligence Generated")
    lines.append("")
    if dormant:
        lines.append(f"**Dormant relationships resurfaced ({len(dormant)}):**")
        lines.append("")
        for d_entry in dormant:
            days = d_entry["days_since_last_touch"]
            age = "no last_touch on file" if days is None else f"last touch {days} days ago"
            lines.append(f"- {d_entry['name']} — {age}")
        lines.append("")
    else:
        lines.append("- No dormant relationships among the contacts this import touched.")
        lines.append("")
    if warm_intros:
        lines.append(f"**Warm introduction candidates ({len(warm_intros)}):**")
        lines.append("")
        for w in warm_intros:
            lines.append(f"- {w['new_contact']} ({w['company']}) — via {w['broker_name']}: {w['reason']}")
        lines.append("")
    else:
        lines.append("- No warm-intro broker found for any newly created contact's company.")
        lines.append("")

    lines.append("## What the system did NOT do")
    lines.append("")
    lines.append("- Did not overwrite any existing canonical field silently (conflicts logged instead).")
    lines.append("- Did not create Person->Industry/Event/Opportunity/Meeting edges — this CSV has no such data (RB-DEFECT-064 Stage 5, still open).")
    return "\n".join(lines) + "\n"


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text())
            if isinstance(manifest, dict) and isinstance(manifest.get("processed"), dict):
                return manifest
        except Exception:
            pass
    return {"processed": {}}


def _save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def scan(*, dry_run: bool = False) -> list[dict[str, Any]]:
    """Ingest every CSV in EXPORTS_DIR not already processed.

    RB-DEFECT-2026-09-18: this had no manifest/file-hash tracking at all --
    every scheduled run (hubspot_ingest_scan --scan, unconditional, no
    --dry-run) re-ingested every CSV still sitting in the inbox, every time,
    with nothing to tell it a file had already been processed. Combined with
    _apply_update()'s per-field null-check guards (net-new fields become
    harmless no-ops on re-ingest once set), the concrete damage was any
    genuinely unresolved conflict note getting re-appended once per
    scheduled run, unbounded, for as long as the file remained in system/
    inbox/crm_exports/ -- confirmed by reading the code, no manifest or
    hash check existed anywhere in this module before this fix. Matches the
    same file-hash-manifest pattern contacts_ingest.py and
    whatsapp_ingest.py already use for the same purpose.
    """
    if not EXPORTS_DIR.is_dir():
        return []
    manifest = _load_manifest()
    results = []
    newly_processed: dict[str, dict] = {}
    for path in sorted(EXPORTS_DIR.glob("*.csv")):
        fhash = _file_hash(path)
        if fhash in manifest["processed"]:
            continue
        result = ingest(path, dry_run=dry_run)
        results.append(result)
        if not dry_run and result.get("ok"):
            newly_processed[fhash] = {
                "path": str(path),
                "processed_at": date.today().isoformat(),
            }
    if newly_processed:
        manifest["processed"].update(newly_processed)
        _save_manifest(manifest)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="RB HubSpot CRM export ingest")
    parser.add_argument("--file", metavar="PATH", help="Ingest a single HubSpot CRM export CSV")
    parser.add_argument("--scan", action="store_true", help=f"Ingest every CSV in {EXPORTS_DIR}")
    parser.add_argument("--dry-run", action="store_true", help="Compute the mutation report without writing")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Override ingest date (default: today)")
    args = parser.parse_args()

    if args.file:
        result = ingest(args.file, ingest_date=args.date, dry_run=args.dry_run)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok") else 1

    if args.scan:
        results = scan(dry_run=args.dry_run)
        print(json.dumps(results, indent=2, default=str))
        return 0 if all(r.get("ok") for r in results) else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
