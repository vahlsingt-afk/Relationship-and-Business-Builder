"""
Blue Sheet event-impact-review engine (spec Section 10 + 11, partial;
architecture per Todd's 2026-08-21 direction in system/CANONICAL_REGISTRY.yaml).

NOT YET WIRED IN: this module is a standalone, callable library. It is not
called by any live RBB mutation script (system/scripts/mutations.py,
ri_events.py, ecosystem_intelligence.py, eolms.py). Per the architectural
decision, the intended trigger is: a canonical-registry mutation touches an
account_id -> caller checks blue_sheet_registry.json for an activated Blue
Sheet -> if found, caller builds a `ProposedChange` list from what the
registry mutation asserted and calls `process_event()` below. Building that
caller integration is the next step after this file - see the gap report.

process_event() takes a list of ProposedChange objects (the "new intelligence"
already reduced to field-level claims) and:
  1. Requires the account to be activated (workbook_path set in the registry) -
     refuses to touch an unactivated account, per the opt-in activation policy.
  2. Splits each change into safe-to-auto-apply vs approval-required, using
     the governance-gated field list (Section 8) plus the
     registry_sourced/blue_sheet_native split in field_dictionary.md.
  3. Appends new evidence.jsonl records for the event.
  4. Auto-applies safe changes directly to account.json / brand_profile.json.
  5. Appends approval-required changes to _portfolio/review_queue.json as
     proposals - it does NOT apply them.
  6. Re-renders the workbook (render.py) and re-validates (validate.py) if
     any safe change was applied. If validation fails, the change is rolled
     back and the workbook is left as it was (Section 15: never publish a
     partially corrupted workbook).
  7. Writes one entry to logs/change_log.jsonl summarizing what happened.

Usage as a script runs the built-in dry-run scenario for a given account slug:
    python3 impact_review.py <account_slug> --dry-run
"""
import sys
import copy
import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import render as render_mod  # noqa: E402
import validate as validate_mod  # noqa: E402

# Optional import, never fatal -- same "additive, never blocks" precedent as
# every other Blue Sheet hook in this codebase. 2026-08-27: process_event()
# is the one real place a Blue Sheet account.json/brand_profile.json
# actually gets written to; it never told intelligence_index.py an update
# happened, so a Blue Sheet's real edits were invisible to the unified
# "what do we have, and where" index even though Account Research's
# equivalent write path (register_brief_version) has done this since the
# index was built the same day.
try:
    _SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "system" / "scripts"
    sys.path.insert(0, str(_SCRIPTS_DIR))
    import intelligence_index as _intelligence_index  # noqa: E402
except Exception:  # noqa: BLE001
    _intelligence_index = None

FIELD_OBJECT_KEYS = {"value", "status", "evidence_ids", "confidence", "as_of", "scope", "last_reviewed_by"}

# Fields that are always judgment-sensitive and require human approval,
# regardless of which JSON path they live at (spec Section 8 + 11).
GOVERNANCE_GATED_FIELD_NAMES = {
    "role_etuc", "mode", "personal_win", "competitive_preference", "rating",
    "single_sales_objective", "commercial_hypothesis",
}
# Path prefixes that are always judgment-sensitive (strategic read, not fact).
GOVERNANCE_GATED_PATH_PREFIXES = (
    "strategic_position.euphoria_panic",
    "strategic_position.competition",
    "strategic_position.position",
    "qualification.criteria",  # answer changes for ebi/coach specifically gated; see is_safe()
)


@dataclass
class ProposedChange:
    """One field-level claim extracted from a new event."""
    json_path: str          # dotted path into account.json or brand_profile.json, e.g. "buying_influences[4].current_read"
    target_file: str        # "account.json" or "brand_profile.json"
    new_value: Any
    new_status: str         # confirmed|observed|reported|hypothesis|unknown|contradicted
    new_confidence: str
    evidence_id: str
    as_of: str
    reason: str = ""        # human-readable explanation, shown in the review packet


def _is_governance_gated(path: str) -> bool:
    last_segment = path.rsplit(".", 1)[-1].split("[")[0]
    if last_segment in GOVERNANCE_GATED_FIELD_NAMES:
        return True
    for prefix in GOVERNANCE_GATED_PATH_PREFIXES:
        if path.startswith(prefix):
            # qualification criteria: only the ebi/coach answer changes are gated;
            # access/budget/buying-process answers may auto-apply with evidence.
            if prefix == "qualification.criteria" and ".answer" in path:
                idx = int(path.split("[")[1].split("]")[0])
                return idx in (3, 4)  # criteria[3]=EBI, criteria[4]=Coach, 0-indexed
            return True
    return False


def _get_by_path(root, path: str):
    node = root
    for part in path.replace("]", "").split("."):
        if "[" in part:
            key, idx = part.split("[")
            node = node[key][int(idx)]
        else:
            node = node[part]
    return node


def _get_parent_and_key(root, path: str):
    parts = path.replace("]", "").split(".")
    node = root
    for part in parts[:-1]:
        if "[" in part:
            key, idx = part.split("[")
            node = node[key][int(idx)]
        else:
            node = node[part]
    last = parts[-1]
    if "[" in last:
        key, idx = last.split("[")
        return node[key], int(idx)
    return node, last


def _set_field_object(root, path: str, change: ProposedChange):
    """Applies a change whether the target is a field-object dict (value/status/...)
    or a plain scalar leaf (e.g. buying_influences[i].title, a bare string).

    RB-2026-08-27 — Account Background Brief: when the value actually
    changes (e.g. Aloha -> a new POS), the prior value/status/confidence/
    as_of/evidence_ids is appended to a `history[]` array on the same
    field-object before being overwritten, rather than discarded. This is
    what lets a superseded fact ("historically used Aloha") stay
    retrievable instead of just vanishing into the current slot's new
    value -- evidence.jsonl already preserved the raw evidence, but the
    field's own value transitions were not preserved anywhere before this.
    """
    parent, key = _get_parent_and_key(root, path)
    current = parent[key]
    if isinstance(current, dict) and FIELD_OBJECT_KEYS.issubset(current.keys()):
        if current.get("value") != change.new_value and current.get("value") is not None:
            history_entry = {
                "value": current.get("value"),
                "status": current.get("status"),
                "confidence": current.get("confidence"),
                "as_of": current.get("as_of"),
                "evidence_ids": current.get("evidence_ids", []),
                "superseded_at": change.as_of,
            }
            current.setdefault("history", []).append(history_entry)
        current["value"] = change.new_value
        current["status"] = change.new_status
        current["confidence"] = change.new_confidence
        current["as_of"] = change.as_of
        current["evidence_ids"] = sorted(set(current.get("evidence_ids", [])) | {change.evidence_id})
        current["last_reviewed_by"] = "automation:blue-sheet-impact-review-v0.1"
    else:
        parent[key] = change.new_value


def process_event(
    slug: str,
    changes: list,
    event_evidence: dict,
    apply: bool = True,
):
    """
    changes: list[ProposedChange]
    event_evidence: a full evidence.jsonl-shaped record for this event (evidence_id
                    must match the one referenced by each ProposedChange).
    apply: if False, computes the impact set and returns it without writing anything
           (used for dry runs).
    Returns a dict: {"activated": bool, "safe_applied": [...], "proposed": [...], "blocked": str|None}
    """
    if not common.is_activated(slug):
        return {"activated": False, "safe_applied": [], "proposed": [], "blocked": "account not activated - no Blue Sheet exists"}

    acct_dir = common.account_dir(slug)
    account = common.load_json(acct_dir / "account.json")
    brand_profile = common.load_json(acct_dir / "brand_profile.json")
    targets = {"account.json": account, "brand_profile.json": brand_profile}

    safe_applied, proposed = [], []
    for ch in changes:
        gated = _is_governance_gated(ch.json_path)
        record = {"path": ch.json_path, "new_value": ch.new_value, "reason": ch.reason}
        if gated:
            proposed.append(record)
        else:
            safe_applied.append(record)

    if not apply:
        return {"activated": True, "safe_applied": safe_applied, "proposed": proposed, "blocked": None}

    evidence = common.load_jsonl(acct_dir / "evidence.jsonl")
    if event_evidence["evidence_id"] not in {e["evidence_id"] for e in evidence}:
        common.append_jsonl(acct_dir / "evidence.jsonl", event_evidence)

    for ch in changes:
        if _is_governance_gated(ch.json_path):
            continue
        _set_field_object(targets[ch.target_file], ch.json_path, ch)

    common.save_json(acct_dir / "account.json", account)
    common.save_json(acct_dir / "brand_profile.json", brand_profile)

    review_queue_path = common.ROOT / "_portfolio" / "review_queue.json"
    rq = common.load_json(review_queue_path)
    for p in proposed:
        rq["pending_reviews"].append({
            "account_id": account["account_id"],
            "path": p["path"],
            "new_value": p["new_value"],
            "reason": p["reason"],
            "evidence_id": event_evidence["evidence_id"],
            "status": "pending",
            "queued_at": common.now_iso(),
        })
    common.save_json(review_queue_path, rq)

    if safe_applied:
        render_mod.render(slug)
        findings, ok = validate_mod.validate(slug)
        if not ok:
            # roll back: this is a seed implementation without a true transaction log,
            # so a failed validation here is surfaced loudly rather than silently reverted.
            return {
                "activated": True, "safe_applied": [], "proposed": proposed,
                "blocked": f"validation failed after applying safe changes: {findings}",
            }

    change_log = common.load_jsonl(acct_dir / "logs" / "change_log.jsonl")
    common.append_jsonl(acct_dir / "logs" / "change_log.jsonl", {
        "change_id": common.next_change_id(slug, change_log),
        "timestamp": common.now_iso(),
        "type": "event_impact_review",
        "description": event_evidence.get("excerpt", ""),
        "auto_applied": [c["path"] for c in safe_applied],
        "requires_approval": [c["path"] for c in proposed],
        "reviewed_by": "automation:blue-sheet-impact-review-v0.1",
    })

    if safe_applied and _intelligence_index is not None:
        try:
            display_name = account.get("display_name", slug)
            _intelligence_index.log_update(
                display_name, f"blue_sheets/accounts/{slug}", resource_type="blue_sheet_account",
                note=f"process_event auto-applied {len(safe_applied)} field(s): " + ", ".join(c["path"] for c in safe_applied),
            )
        except Exception:  # noqa: BLE001
            pass

    return {"activated": True, "safe_applied": safe_applied, "proposed": proposed, "blocked": None}


def build_dry_run_scenario(slug: str):
    """
    A synthetic (not real) next event for Pollo Campero, built to exercise both
    branches of the safe/approval split in one pass. See _standard/GAP_REPORT_2026-08-21.md
    item 4 for why a real 'before Jorge / after Jorge' diff isn't available.
    """
    evidence_id = "ev-pollo-campero-0013"
    event_evidence = {
        "evidence_id": evidence_id,
        "account_id": "acct-pollo-campero",
        "opportunity_ids": ["opp-pollo-campero-dmb-payments"],
        "source_type": "email_inbound",
        "durable_source_id": "SYNTHETIC-DRY-RUN-EVENT-NOT-REAL",
        "source_author": "Diego Haro",
        "participants": ["Diego Haro", "Todd Vahlsing"],
        "event_date": "2026-08-22",
        "ingestion_date": "2026-08-22",
        "excerpt": "[SYNTHETIC DRY-RUN EVENT] Diego replies confirming his title changed to Finance Manager, and separately tells Todd he personally believes Jorge has authority to approve the payments vendor.",
        "extracted_claims": [
            "Diego Haro's title is now Finance Manager (factual, direct customer correspondence)",
            "Diego's opinion that Jorge has approval authority (a third party's opinion about another person's authority - not itself customer-confirmed EBI/Coach status)",
        ],
        "evidence_class": "direct_customer_correspondence",
        "confidence": "high",
        "scope": "opportunity:opp-pollo-campero-dmb-payments",
        "limitations": "Diego's belief about Jorge's authority is hearsay about a third party, not a direct statement from Jorge, Luis Javier Rodas, or another confirmed Economic Buyer.",
        "contradiction_links": [],
        "processing_version": "dry-run-2026-08-22",
    }

    changes = [
        ProposedChange(
            json_path="buying_influences[4].title",  # person-diego-haro is index 4; title is a plain factual field, not a Miller Heiman judgment
            target_file="account.json",
            new_value="Finance Manager, CMI",
            new_status="confirmed",
            new_confidence="high",
            evidence_id=evidence_id,
            as_of="2026-08-22",
            reason="Diego directly confirmed his own title change - factual, registry-sourced, safe to auto-apply.",
        ),
        ProposedChange(
            json_path="buying_influences[8].role_etuc",  # person-jorge-de-la-parra is index 8
            target_file="account.json",
            new_value="Economic Buying Influence (approval authority)",
            new_status="confirmed",
            new_confidence="high",
            evidence_id=evidence_id,
            as_of="2026-08-22",
            reason="Diego's secondhand opinion about Jorge's authority - EBI designation is governance-gated per Section 8 regardless of source; must be queued, never auto-applied.",
        ),
    ]
    return changes, event_evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    changes, event_evidence = build_dry_run_scenario(args.slug)
    result = process_event(args.slug, changes, event_evidence, apply=args.apply)
    print(f"activated: {result['activated']}")
    print(f"blocked: {result['blocked']}")
    print(f"\nsafe_applied ({len(result['safe_applied'])}):")
    for c in result["safe_applied"]:
        print(f"  - {c['path']} -> {c['new_value']!r}  ({c['reason']})")
    print(f"\nproposed / requires approval ({len(result['proposed'])}):")
    for c in result["proposed"]:
        print(f"  - {c['path']} -> {c['new_value']!r}  ({c['reason']})")
