#!/usr/bin/env python3
"""
impact_review.py — safe-apply vs. flag-for-review engine for Master Account
Plans, mirroring blue_sheets/_engine/impact_review.py's discipline exactly.

Per Todd's direction (2026-08-28): "This should be a mid-stream artifact
that mutates with gathered intelligence — and should be updated like a
blue sheet — facts and safe updates automatically, flagged for user input
when needed."

Safe, auto-applied (never involves interpretation):
  1. Evidence ledger append for any material signal touching a ranked account.
  2. Cross-link refresh (linked_blue_sheet_slug / linked_account_research_slug)
     when a match becomes newly unambiguous.
  3. rm_portfolios.json aggregate recompute (opportunity_accounts_count,
     p1_accounts_count, average_score) -- pure arithmetic over
     ranked_portfolio.json's own already-approved scores.
  4. Coverage-gap logging (into blue_sheets' own coverage_log.jsonl -- the
     exact consumer intelligence_cascade.py's assess_coverage_gaps()
     already reads) for a P1 account with no Blue Sheet coverage yet.

Flagged for Todd (never auto-applied): any Score/Tier/Rank/Priority change
implied by a material signal -- recomputing the weighted Scoring Model is
itself an interpretive call, mirrors Blue Sheet's buying-influence ratings
staying human-only.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

ENGINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_DIR))
import mp_common as common  # noqa: E402
import create_plan  # noqa: E402 (reuses bs_common loaded via explicit-path import there)
import mp_render as render_mod  # noqa: E402

SCRIPTS_DIR = ENGINE_DIR.parent.parent / "system" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import ecosystem_brief as eb  # noqa: E402

bs_common = create_plan.bs_common


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _entity_slug_for_row(row: dict, known_entity_ids: set) -> Optional[str]:
    for candidate in (row.get("linked_blue_sheet_slug"), row.get("linked_account_research_slug")):
        if candidate:
            return candidate
    guess = _slugify(row.get("account_name", ""))
    return guess if guess and f"brand-{guess}" in known_entity_ids else None


def _load_graph() -> dict:
    core_dir = SCRIPTS_DIR
    graph_path = core_dir.parent / "ecosystem_intelligence.json"
    return common.load_json(graph_path)


def _material_signals_since(graph: dict, since_date: str) -> list[dict]:
    out = []
    for sig in graph.get("signals", []):
        captured = (sig.get("captured_at") or "")[:10]
        if captured <= since_date:
            continue
        conf = (sig.get("confidence") or {}).get("level", "")
        if eb._is_material_signal(conf, sig.get("signal_type", "")):
            out.append(sig)
    return out


def _recompute_rm_aggregates(ranked_portfolio: list[dict], rm_portfolios: list[dict]) -> list[dict]:
    """Pure arithmetic over already-approved ranked_portfolio scores --
    never interpretive, safe to recompute unconditionally."""
    by_rm: dict[str, list[dict]] = {}
    for row in ranked_portfolio:
        by_rm.setdefault(row.get("rm_name") or "", []).append(row)

    out = []
    for rm in rm_portfolios:
        rm = dict(rm)
        rows = by_rm.get(rm.get("rm_name") or "", [])
        if rows:
            scores = [r.get("score") for r in rows if isinstance(r.get("score"), (int, float))]
            rm["opportunity_accounts_count"] = len(rows)
            rm["p1_accounts_count"] = sum(1 for r in rows if r.get("priority") == "P1")
            if scores:
                rm["average_score"] = round(sum(scores) / len(scores), 1)
        out.append(rm)
    return out


def run_safe_apply(vendor_slug: str) -> dict:
    """The daily entry point, called from intelligence_cascade.py. Returns
    real named counts -- never a template placeholder."""
    vendor_dir = common.ROOT / "vendors" / vendor_slug
    if not vendor_dir.exists():
        return {"ok": False, "error": f"no Master Account Plan for vendor '{vendor_slug}'"}

    plan = common.load_json(vendor_dir / "plan.json")
    ranked_portfolio = common.load_json(vendor_dir / "ranked_portfolio.json")
    rm_portfolios = common.load_json(vendor_dir / "rm_portfolios.json")

    graph = _load_graph()
    known_entity_ids = {e.get("id") for e in graph.get("entities", [])}
    since_date = plan.get("last_evidence_date") or "1970-01-01"
    material_signals = _material_signals_since(graph, since_date)

    # Idempotency: a signal already recorded in evidence.jsonl on a prior
    # run must never be re-appended -- since_date doesn't shrink between
    # runs (last_evidence_date only advances on a real re-upload), so
    # without this every daily cascade run would re-append the same
    # evidence forever.
    already_recorded_evidence_ids = {
        e.get("evidence_id") for e in common.load_jsonl(vendor_dir / "evidence.jsonl")
    }

    evidence_appended = []
    evidence_records = []
    queued_for_review = []
    cross_links_refreshed = 0
    coverage_gaps_logged = []

    review_queue_path = common.review_queue_path()
    review_queue = common.load_review_queue()
    existing_pending_keys = {
        (r.get("account_id"), r.get("evidence_id"))
        for r in review_queue.get("pending_reviews", [])
        if r.get("status") == "pending"
    }

    for row in ranked_portfolio:
        entity_slug = _entity_slug_for_row(row, known_entity_ids)

        # Safe #2: cross-link refresh -- re-check against current registries
        # in case a Blue Sheet/Account Research entry appeared since ingest.
        bs_reg = bs_common.load_registry()
        ar_reg_path = create_plan.ACCOUNT_RESEARCH_DIR / "_portfolio" / "account_research_registry.json"
        ar_reg = common.load_json(ar_reg_path) if ar_reg_path.exists() else {"registry": []}
        new_bs_link = create_plan._resolve_cross_link(row.get("account_name", ""), bs_reg)
        new_ar_link = create_plan._resolve_cross_link(row.get("account_name", ""), ar_reg)
        if new_bs_link and not row.get("linked_blue_sheet_slug"):
            row["linked_blue_sheet_slug"] = new_bs_link
            cross_links_refreshed += 1
        if new_ar_link and not row.get("linked_account_research_slug"):
            row["linked_account_research_slug"] = new_ar_link
            cross_links_refreshed += 1

        if not entity_slug:
            continue
        row_signals = [s for s in material_signals if f"brand-{entity_slug}" in (s.get("entities") or [])]
        for sig in row_signals:
            evidence_id = sig.get("id", "")
            if evidence_id in already_recorded_evidence_ids:
                continue
            # Safe #1: evidence ledger append -- mechanical fact-recording.
            evidence_record = {
                "evidence_id": evidence_id,
                "logged_at": common.now_iso(),
                "account_name": row.get("account_name"),
                "signal_summary": sig.get("summary", ""),
                "signal_type": sig.get("signal_type", ""),
                "event_at": sig.get("event_at"),
            }
            common.append_jsonl(vendor_dir / "evidence.jsonl", evidence_record)
            evidence_appended.append(evidence_id)
            evidence_records.append(evidence_record)
            already_recorded_evidence_ids.add(evidence_id)

            # Flagged, never auto-applied: this signal implies a possible
            # Score/Tier re-review, which is an interpretive call.
            key = (row.get("account_name"), evidence_id)
            if key not in existing_pending_keys:
                review_queue.setdefault("pending_reviews", []).append({
                    "vendor_slug": vendor_slug,
                    "account_id": row.get("account_name"),
                    "kind": "score_review_suggested",
                    "evidence_id": evidence_id,
                    "reason": f"New material signal ({sig.get('signal_type')}): {sig.get('summary', '')[:200]}",
                    "current_score": row.get("score"),
                    "current_tier": row.get("tier"),
                    "status": "pending",
                    "queued_at": common.now_iso(),
                })
                queued_for_review.append({"account_name": row.get("account_name"), "evidence_id": evidence_id})
                existing_pending_keys.add(key)

        # Safe #4: coverage-gap logging for P1 accounts with no Blue Sheet.
        if row.get("priority") == "P1" and not row.get("linked_blue_sheet_slug"):
            bs_common.log_coverage_event(
                entity_id=f"brand-{entity_slug}" if entity_slug else "unknown",
                slug=None, activated=False,
                mutation_type="master_account_plan_p1_no_blue_sheet",
                detail=f"'{row.get('account_name')}' is a P1 priority in {vendor_slug}'s Master Account Plan with no Blue Sheet.",
            )
            coverage_gaps_logged.append(row.get("account_name"))

    # Safe #3: RM aggregate recompute -- pure arithmetic, always safe.
    rm_portfolios = _recompute_rm_aggregates(ranked_portfolio, rm_portfolios)

    common.save_json(vendor_dir / "ranked_portfolio.json", ranked_portfolio)
    common.save_json(vendor_dir / "rm_portfolios.json", rm_portfolios)
    if queued_for_review:
        common.save_json(review_queue_path, review_queue)

    if evidence_appended or queued_for_review or cross_links_refreshed:
        common.append_jsonl(vendor_dir / "logs" / "change_log.jsonl", {
            "at": common.now_iso(),
            "event": "safe_apply_pass",
            "evidence_appended": len(evidence_appended),
            "queued_for_review": len(queued_for_review),
            "cross_links_refreshed": cross_links_refreshed,
        })

    # Reflect safe-applied changes in the live current/ xlsx -- Todd's
    # original tab structure/formatting preserved, only specific cells
    # updated, never rebuilt from scratch (see render.py's module docstring).
    xlsx_evidence_rows = 0
    xlsx_rm_rows = 0
    try:
        if evidence_records:
            xlsx_evidence_rows = render_mod.append_evidence_rows(vendor_slug, evidence_records)
        xlsx_rm_rows = render_mod.refresh_rm_portfolio_sheet(vendor_slug, rm_portfolios)
    except Exception:  # noqa: BLE001
        pass  # additive, never blocks the JSON-state safe-apply above

    return {
        "ok": True,
        "vendor_slug": vendor_slug,
        "evidence_appended": evidence_appended,
        "queued_for_review": queued_for_review,
        "cross_links_refreshed": cross_links_refreshed,
        "coverage_gaps_logged": coverage_gaps_logged,
        "xlsx_evidence_rows_appended": xlsx_evidence_rows,
        "xlsx_rm_rows_updated": xlsx_rm_rows,
    }


def run_all_vendors() -> dict:
    registry = common.load_registry()
    results = {}
    for entry in registry.get("registry", []):
        slug = entry.get("vendor_slug")
        if slug:
            results[slug] = run_safe_apply(slug)
    return results


if __name__ == "__main__":
    import json
    print(json.dumps(run_all_vendors(), indent=2, default=str))
