#!/usr/bin/env python3
"""
import_competitor_research_pack.py — RB-2026-09-28.

Loads a "Competitor Research Cycle" JSONL export (one research pack per
line, schema fields: entity/category/pack_order/pack_phase/recommended_add/
flag_acquired/as_of_date/pack_claim_count*/gap_audit/verified_findings/
cos_commentary/legacy_claims/research_complete) into RB's real competitor
intelligence store. This is the successor pack format to the single-JSON
"rb.competitive_research.v1/v2" export that import_genius_competitive_
research.py (2026-09-26) consumes -- same phase2_competitor-only scope
(this pack format has not been seen carrying phase1_genius entities), but
restructured per-entity with an explicit research-completeness self-audit
(`gap_audit`) and a synthesis block (`cos_commentary`) that the older
schema didn't have.

Field mapping:
  - `legacy_claims` is exactly the older script's `claims` shape (dimension/
    claim/confidence/is_inference/urls/source_text) -- carried forward for
    continuity/provenance, not re-imported here. Confirmed live against
    Toast: every legacy_claims entry already exists in toast/evidence.jsonl
    (note-0007..note-0022, logged when import_genius_competitive_research.py
    ran on 2026-09-26). Re-running that script against this file's
    legacy_claims would be a no-op (its own import log already has these
    keys); this script does not attempt to re-import them at all.
  - `verified_findings` is this cycle's NEW claim evidence -- each one
    becomes a competitor_intelligence evidence.jsonl record via
    add_competitive_note(). `dimension` is present on some findings (the
    ones that map to a specific research dimension, e.g. "Named
    Customers") and absent on others (a general/overview finding, usually
    the pack's first) -- absent or unrecognized dimensions land honestly
    in evidence category "other" (same discipline as the 2026-09-26
    script: no force-fitting). A `scope_note` (the pack's own analytical
    guardrail against over-reading the finding) is appended to the
    evidence summary in parens rather than dropped -- it's real analytical
    content the research pass produced, not internal bookkeeping.
  - `confidence_numeric` (a 0-1 float) is bucketed into RB's schema-level
    low/medium/high/critical vocabulary using the same band centers
    confidence_calibration.py's CONFIDENCE_BAND_TO_SCHEMA already commits
    to (medium=0.5/0.7, high=0.85, very_high=0.95 -> critical): >=0.9
    critical, >=0.775 high, >=0.45 medium, else low. Every claim in the
    first real pack file (2026-09-28) carries status "verified_source_claim"
    or would be filtered as skip-worthy elsewhere -- there is no separate
    per-claim inference flag on verified_findings the way legacy_claims had
    is_inference, so none are treated as inferences here.
  - Strengths/Weaknesses-dimensioned findings still promote to the
    structured vs_genius gap-analysis field via add_gap_point(), same
    mapping and confidence floor as the 2026-09-26 script (kept for
    forward-compatibility -- the first real cycle's dimension vocabulary
    happens not to use these two dimension names, so no gap points fire
    from it, but a future cycle's pack may).
  - `cos_commentary` and the pack's own research-quality metadata
    (`gap_audit`, `pack_claim_count`/`pack_claims_with_url`/
    `pack_claims_inherited_url`/`pack_claims_with_structured_source_date`,
    `research_complete`, `as_of_date`, `pack_order`) are NOT evidence --
    they're a synthesis/self-audit snapshot "as of" one cycle, not a
    discrete sourced fact, so appending them as evidence.jsonl rows would
    misrepresent them as raw findings. Instead they overwrite (not
    accumulate) two top-level fields on competitor.json --
    `latest_cos_commentary` and `latest_research_pack_status` -- so the
    newest cycle's synthesis and gap list are always the ones visible,
    the same "current snapshot, not a growing log" treatment
    positioning_summary already gets.
  - `flag_acquired` entities are skipped ONLY when this cycle's pack has
    zero verified_findings (a pure legacy cross-reference, no new
    content this cycle) -- same "flag_acquired means don't treat as a
    live independent rival forever, not discard its real history" rule
    as 2026-09-26. When a flag_acquired entity DOES carry a real new
    finding this cycle (e.g. Compeat, 2026-09-28: Restaurant365 announced
    Compeat Advantage's EOL date), it's processed normally.
  - A `recommended_add` entity is resolved-or-created via
    ensure_competitor() using its real name, same as the 2026-09-26
    script -- a real display name is always present in this schema.
  - `category` (a free-text vendor category label, sometimes carrying an
    inline annotation like "(RECOMMENDED ADD)" or "(ACQUIRED; remove as
    standalone)") is descriptive-only in this file and is not written
    anywhere -- competitor.json's own `primary_category` continues to
    come from the matched vendor entity in ecosystem_intelligence.json
    (via ensure_competitor()), not from this free-text label.

Idempotent: a local import log (system/.cache/competitor_research_pack_
import_log.json) tracks which (entity, finding-text, url) triples have
already been written, keyed by a stable hash -- a separate log from
import_genius_competitive_research.py's (different schema, different
key shape), so re-running this script against a corrected/re-sent version
of the same cycle file only writes what's genuinely new.

CLI:
    python3 import_competitor_research_pack.py <path.jsonl>            # dry run (default)
    python3 import_competitor_research_pack.py <path.jsonl> --confirm  # write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import competitor_intelligence as compintel  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402

IMPORT_LOG_PATH = core.CACHE_DIR / "competitor_research_pack_import_log.json"

# dimension (as it appears in verified_findings) -> evidence category.
# Only an exact-meaning match gets its own category; everything else lands
# honestly in "other" (same discipline as import_genius_competitive_
# research.py's _DIMENSION_TO_EVIDENCE_CATEGORY).
_DIMENSION_TO_EVIDENCE_CATEGORY = {
    "Positioning": "positioning",
    "Strengths": "strength",
    "Weaknesses / Vulnerabilities": "weakness",
    "Named Customers": "reference_customer",
    "Pricing signals": "pricing",
}
_DEFAULT_EVIDENCE_CATEGORY = "other"

_GAP_POINT_DIMENSIONS = {"Strengths": "competitor", "Weaknesses / Vulnerabilities": "genius"}
_GAP_POINT_MIN_LEVELS = {"high", "critical", "medium"}


def _confidence_level(confidence_numeric) -> str:
    """Bucket a 0-1 confidence_numeric into RB's low/medium/high/critical
    vocabulary, using confidence_calibration.py's band centers (medium=
    0.5/0.7, high=0.85, very_high=0.95->critical) to place cut points at
    the midpoints between adjacent bands: 0.45, 0.775, 0.9."""
    try:
        val = float(confidence_numeric)
    except (TypeError, ValueError):
        return "low"
    if val >= 0.9:
        return "critical"
    if val >= 0.775:
        return "high"
    if val >= 0.45:
        return "medium"
    return "low"


def _claim_key(entity_name: str, finding_text: str, url: str) -> str:
    basis = f"{entity_name}\x1f{finding_text}\x1f{url}".encode("utf-8")
    return hashlib.sha1(basis).hexdigest()[:20]


def _load_import_log() -> dict:
    if not IMPORT_LOG_PATH.exists():
        return {"imported_claim_keys": []}
    try:
        data = json.loads(IMPORT_LOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"imported_claim_keys": []}
    data.setdefault("imported_claim_keys", [])
    return data


def _save_import_log(data: dict) -> None:
    IMPORT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    IMPORT_LOG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _finding_note(finding: dict) -> str:
    text = (finding.get("finding") or "").strip()
    scope_note = (finding.get("scope_note") or "").strip()
    if scope_note:
        text = f"{text} (Scope note: {scope_note})"
    return text


def _finding_source(finding: dict) -> str:
    url = (finding.get("url") or "").strip()
    if url:
        return url
    name = (finding.get("source_name") or "").strip()
    date = (finding.get("source_date") or "").strip()
    if name:
        return f"{name}, {date}".rstrip(", ")
    return "RBB Competitor Research Cycle"


def _import_finding(slug: str, finding: dict, *, dry_run: bool) -> Optional[dict]:
    dimension = finding.get("dimension")
    category = _DIMENSION_TO_EVIDENCE_CATEGORY.get(dimension, _DEFAULT_EVIDENCE_CATEGORY)
    level = _confidence_level(finding.get("confidence_numeric"))
    note = _finding_note(finding)
    source = _finding_source(finding)
    if dry_run:
        return {"action": "add_competitive_note", "slug": slug, "category": category,
                "confidence": level, "note": note[:100]}
    return compintel.add_competitive_note(slug, note, category=category, source=source, confidence=level)


def _import_gap_point(slug: str, side: str, finding: dict, evidence_id: Optional[str], *, dry_run: bool) -> Optional[dict]:
    point = (finding.get("finding") or "").strip()
    if dry_run:
        return {"action": "add_gap_point", "slug": slug, "side": side, "point": point[:100]}
    return compintel.add_gap_point(slug, side, point, evidence_id=evidence_id)


def _set_cycle_synthesis(slug: str, pack: dict, *, dry_run: bool) -> None:
    """Overwrite (not accumulate) the current-cycle synthesis fields --
    these describe the pack as of one as_of_date, not a discrete sourced
    claim, so they replace the prior cycle's snapshot rather than piling
    up alongside evidence.jsonl."""
    if dry_run:
        return
    data = cic.load_competitor(slug)
    comp = data["competitor"]
    comp["latest_cos_commentary"] = {
        **pack.get("cos_commentary", {}),
        "as_of_date": pack.get("as_of_date"),
        "pack_order": pack.get("pack_order"),
    }
    comp["latest_research_pack_status"] = {
        "as_of_date": pack.get("as_of_date"),
        "pack_order": pack.get("pack_order"),
        "pack_claim_count": pack.get("pack_claim_count"),
        "pack_claims_with_url": pack.get("pack_claims_with_url"),
        "pack_claims_inherited_url": pack.get("pack_claims_inherited_url"),
        "pack_claims_with_structured_source_date": pack.get("pack_claims_with_structured_source_date"),
        "research_complete": pack.get("research_complete"),
        "gap_audit": pack.get("gap_audit"),
    }
    comp["updated_at"] = cic.now_iso()
    cic.save_json(cic.competitor_dir(slug) / "competitor.json", comp)


def import_pack_file(path: Path, *, dry_run: bool = True) -> dict:
    log = _load_import_log()
    seen: set[str] = set(log["imported_claim_keys"])
    new_keys: list[str] = []

    summary = {
        "packs_processed": 0,
        "packs_skipped": [],
        "competitor_evidence_added": 0,
        "gap_points_added": 0,
        "cycle_synthesis_updated": 0,
        "findings_skipped_already_imported": 0,
        "actions_preview": [],
    }

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        pack = json.loads(line)
        entity_name = pack.get("entity") or ""
        phase = pack.get("pack_phase")
        findings = pack.get("verified_findings") or []

        if phase != "phase2_competitor":
            summary["packs_skipped"].append({"entity": entity_name, "reason": f"unknown pack_phase {phase!r}"})
            continue

        if pack.get("flag_acquired") and not findings:
            summary["packs_skipped"].append({
                "entity": entity_name,
                "reason": "flag_acquired with no new verified_findings this cycle (pure legacy cross-reference)",
            })
            continue

        summary["packs_processed"] += 1
        slug: Optional[str] = None
        if not dry_run:
            slug, _already = compintel.ensure_competitor(entity_name)

        for finding in findings:
            key = _claim_key(entity_name, finding.get("finding", ""), finding.get("url", ""))
            if key in seen:
                summary["findings_skipped_already_imported"] += 1
                continue
            evidence_result = _import_finding(slug or entity_name, finding, dry_run=dry_run)
            if evidence_result is not None:
                summary["competitor_evidence_added"] += 1
                if dry_run:
                    summary["actions_preview"].append(evidence_result)
            new_keys.append(key)

            dimension = finding.get("dimension")
            gap_side = _GAP_POINT_DIMENSIONS.get(dimension)
            level = _confidence_level(finding.get("confidence_numeric"))
            if gap_side and level in _GAP_POINT_MIN_LEVELS:
                evidence_id = evidence_result.get("evidence_id") if evidence_result else None
                gap_result = _import_gap_point(slug or entity_name, gap_side, finding, evidence_id, dry_run=dry_run)
                if gap_result is not None:
                    summary["gap_points_added"] += 1
                    if dry_run:
                        summary["actions_preview"].append(gap_result)

        _set_cycle_synthesis(slug or entity_name, pack, dry_run=dry_run)
        summary["cycle_synthesis_updated"] += 1

    if not dry_run and new_keys:
        log["imported_claim_keys"] = sorted(seen | set(new_keys))
        _save_import_log(log)

    return summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("jsonl_path")
    p.add_argument("--confirm", action="store_true", help="Actually write (default: dry run only).")
    args = p.parse_args()

    result = import_pack_file(Path(args.jsonl_path), dry_run=not args.confirm)
    preview = result.pop("actions_preview", [])
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not args.confirm:
        print(f"\n[dry run -- {len(preview)} action(s) previewed, nothing written. Re-run with --confirm to apply.]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
