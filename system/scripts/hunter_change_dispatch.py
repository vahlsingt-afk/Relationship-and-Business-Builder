#!/usr/bin/env python3
"""Dispatch validated Hunter changes into governed RBB mutation and CoS paths."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import brand_profile_common as bpc  # noqa: E402
import hunter  # noqa: E402
import import_brand_company_profile_research as brand_importer  # noqa: E402
import import_competitor_platform_research as competitor_importer  # noqa: E402
import import_technology_lifecycle_research as tech_lifecycle_importer  # noqa: E402
import import_fdd_research as fdd_importer  # noqa: E402
import import_franchisee_research as franchisee_importer  # noqa: E402
import mutation_policy  # noqa: E402


SYSTEM_DIR = SCRIPTS_DIR.parent
PROPOSAL_QUEUE_PATH = SYSTEM_DIR / ".cache" / "hunter_mutation_proposals.jsonl"
COS_HANDOFF_PATH = SYSTEM_DIR / "research" / "hunter_cos_handoffs.jsonl"
SOURCE = "system:hunter"


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def _resolve_sources(packet: dict, source_ids: list[str]) -> list[dict]:
    """The compact, human-facing fields a reviewer actually needs to judge
    a proposal's new value -- never the full source_ledger row (internal
    fields like evidence_chain_id/access_status add noise, not trust).
    Unknown source_ids are silently skipped rather than raising: a
    reviewer seeing fewer citations than expected is a visible, honest
    gap; crashing dispatch over a dangling id in someone's packet is not
    a trade worth making."""
    by_id = {s.get("source_id"): s for s in packet.get("source_ledger") or []}
    resolved = []
    for sid in source_ids:
        s = by_id.get(sid)
        if not s:
            continue
        resolved.append({
            "source_id": sid,
            "title": s.get("title"),
            "publisher": s.get("publisher"),
            "url": s.get("url"),
            "published_at": s.get("published_at"),
            "accessed_at": s.get("accessed_at"),
        })
    return resolved


def _source_url(proposal: dict, packet: dict) -> str | None:
    by_id = {source.get("source_id"): source for source in packet.get("source_ledger") or []}
    for source_id in proposal.get("source_ids") or []:
        if by_id.get(source_id, {}).get("url"):
            return by_id[source_id]["url"]
    return None


def _apply_brand_recent_signal(proposal: dict, packet: dict, *, dry_run: bool) -> tuple[bool, str | None]:
    if proposal.get("field_path") != "brand_profile.recent_signals":
        return False, None
    target = str(proposal.get("target_key") or "")
    if not target.startswith("company:") or not isinstance(proposal.get("new_value"), dict):
        return False, None
    brand_id = target.split(":", 1)[1]
    value = proposal["new_value"]
    if not value.get("value") or value.get("signal_type") not in bpc.SIGNAL_TYPES:
        return False, None
    if dry_run:
        return True, str(bpc.profile_path(brand_id))
    profile = bpc.get_profile(brand_id, persist=True)
    bpc.add_signal(
        profile,
        signal_type=value["signal_type"],
        value=value["value"],
        status=value.get("status", "reported"),
        evidence_ids=value.get("evidence_ids") or proposal.get("finding_ids") or [],
        confidence=value.get("confidence", "medium"),
        as_of=value.get("as_of") or proposal.get("effective_at"),
        source_url=value.get("source_url") or _source_url(proposal, packet),
        last_reviewed_by=SOURCE,
        visibility="team_shareable",
    )
    bpc.save_profile(brand_id, profile)
    return True, str(bpc.profile_path(brand_id))


def dispatch(packet: dict, *, dry_run: bool = True) -> dict:
    validation = hunter.validate_packet(packet)
    if not validation["valid"]:
        return {"ok": False, "validation_errors": validation["errors"], "scores": validation["scores"]}

    summary = {
        "ok": True,
        "packet_id": packet["packet_id"],
        "dry_run": dry_run,
        "canonical_applied": 0,
        "queued_for_review_or_unhandled": 0,
        "rejected": 0,
        "mutation_results": [],
        "cos_handoffs_recorded": 0,
    }

    # Existing narrow importer remains the authoritative writer for the
    # competitor-platform payload. Its own receipts prove actual mutation.
    if packet.get("payload_schema") == "rb.competitor_platform_research.v1":
        imported = competitor_importer.import_findings(packet, packet_id=packet["packet_id"], dry_run=dry_run)
        summary["payload_import"] = imported
        summary["canonical_applied"] += imported.get("applied", 0)

    # Technology Lifecycle Phase 1 (2026-10-02): same narrow-importer
    # pattern, for the technology_replacement_lifecycle playbook's payload.
    if packet.get("payload_schema") == "rb.technology_lifecycle_research.v1":
        imported = tech_lifecycle_importer.import_findings(packet, packet_id=packet["packet_id"], dry_run=dry_run)
        summary["payload_import"] = imported
        summary["canonical_applied"] += imported.get("applied", 0)

    # FDD Technology Governance & Economics (2026-10-02): same narrow-importer
    # pattern, for the fdd_governance_economics playbook's payload.
    if packet.get("payload_schema") == "rb.fdd_governance_economics_research.v1":
        imported = fdd_importer.import_findings(packet, packet_id=packet["packet_id"], dry_run=dry_run)
        summary["payload_import"] = imported
        summary["canonical_applied"] += imported.get("applied", 0)

    if packet.get("payload_schema") == "rb.brand_company_profile.v1":
        imported = brand_importer.import_records(packet, dry_run=dry_run)
        summary["payload_import"] = imported
        summary["canonical_applied"] += imported.get("applied", 0)

    if packet.get("payload_schema") == "rb.franchisee_organization_profile.v1":
        imported = franchisee_importer.import_profile_findings(packet, dry_run=dry_run)
        summary["payload_import"] = imported
        summary["canonical_applied"] += imported.get("applied", 0)

    if packet.get("payload_schema") == "rb.franchisee_discovery.v1":
        imported = franchisee_importer.import_discovery_findings(packet, dry_run=dry_run)
        summary["payload_import"] = imported
        summary["canonical_applied"] += imported.get("applied", 0)

    for proposal in packet.get("mutation_proposals") or []:
        operation = proposal["operation"]
        decision = mutation_policy.decide(
            source=SOURCE,
            new_value=proposal.get("new_value"),
            existing_value=proposal.get("existing_value"),
            is_set_member=operation in {"add", "append_event"},
            new_date=proposal.get("effective_at"),
            is_replacement=operation in {"update", "supersede"},
            low_confidence=float(proposal.get("confidence_pct") or 0) < 50,
            low_confidence_reason="Hunter mutation proposal confidence is below 50%.",
            observed_at=packet.get("completed_at"),
            confidence=float(proposal.get("confidence_pct") or 0) / 100.0,
            field_name=proposal.get("field_path"),
            entity_id=proposal.get("target_key"),
        )
        applied = False
        artifact = None
        handler_recognized = False
        if decision.auto_apply:
            handler_recognized, artifact = _apply_brand_recent_signal(proposal, packet, dry_run=dry_run)
            applied = handler_recognized and not dry_run
        if not dry_run:
            mutation_policy.record_receipt(decision, artifact=artifact, applied=applied)
        result = {
            "proposal_id": proposal["proposal_id"],
            "decision": decision.status,
            "handler_recognized": handler_recognized,
            "applied": applied,
            "artifact": artifact,
        }
        if applied:
            summary["canonical_applied"] += 1
        elif decision.status in {mutation_policy.REJECTED_DUPLICATE, mutation_policy.REJECTED_LOW_CONFIDENCE}:
            summary["rejected"] += 1
        else:
            summary["queued_for_review_or_unhandled"] += 1
            if not dry_run:
                _append_jsonl(PROPOSAL_QUEUE_PATH, {
                    "schema": "rb.hunter_mutation_queue.v1",
                    "queued_at": datetime.now(timezone.utc).isoformat(),
                    "packet_id": packet["packet_id"],
                    "proposal": proposal,
                    "decision": decision.receipt,
                    "reason": "review required or no registered narrow writer",
                    # RB-2026-10-10 (Todd's review feedback): the queued row only
                    # ever carried source_ids, never what those sources actually
                    # say -- a reviewer had no way to see "this new value comes
                    # from X, published/accessed on Y" without separately
                    # re-opening the archived packet. The packet (and its
                    # source_ledger) is only ever in scope right here, at
                    # dispatch time -- resolve it now, once, so it survives
                    # unchanged however long the proposal sits pending.
                    "resolved_sources": _resolve_sources(packet, proposal.get("source_ids") or []),
                })
        summary["mutation_results"].append(result)

    for handoff in packet.get("cos_handoffs") or []:
        if not dry_run:
            _append_jsonl(COS_HANDOFF_PATH, {
                "schema": "rb.hunter_cos_handoff.v1",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "packet_id": packet["packet_id"],
                "handoff": handoff,
                "status": "ready_for_cos_synthesis",
            })
        summary["cos_handoffs_recorded"] += 1
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Dispatch a validated Hunter packet")
    parser.add_argument("packet")
    parser.add_argument("--confirm", action="store_true", help="Apply policy-authorized registered writes and persist queues")
    args = parser.parse_args()
    packet = json.loads(Path(args.packet).read_text(encoding="utf-8"))
    result = dispatch(packet, dry_run=not args.confirm)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
