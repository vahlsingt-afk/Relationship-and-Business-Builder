#!/usr/bin/env python3
"""
competitor_intelligence_review.py — RB-2026-09-01.

Safe-apply vs. flag-for-review scan for Competitor Intelligence, mirroring
master_account_plans/_engine/mp_impact_review.py's discipline exactly
(itself mirroring blue_sheets/_engine/impact_review.py).

Safe, auto-applied (already happens, unchanged by this module): mechanical
evidence-ledger append for every ecosystem signal tagged to a competitor's
vendor_entity_id -- competitor_intelligence.sync_all_tracked_competitors(),
already scheduled as morning_pipeline.py's competitor_intelligence_sync
step, which runs immediately before this module's own pipeline step.

Flagged for Todd (never auto-applied): any category_battle_cards content
implied by a material signal on that competitor -- an interpretive call,
same principle as Master Account Plan Score/Tier changes and Blue Sheet
buying-influence ratings staying human-only -- plus a staleness check on
existing category battle cards whose last_validated has aged past
stale_after_days. This module only ever proposes; resolveCompetitorReviewItem
(system/api/server.py) is the only way a pending item's status changes, and
even then it never mutates category_battle_cards content itself -- that
stays a direct edit via upsert_category_battle_card.

RB-DEFECT (2026-09-14): a third pass, _capture_signals_for_vendor, closes a
real gap found live -- Todd pasting a competitive-intelligence note (e.g. a
Goldman Sachs research summary on Toast) is triaged by intelligence_triage.py
into micro_graph_enrichment/micro_graph_build streams and auto-persisted to
IntelligenceDB (system/scripts/intelligence_db.py) for daily-brief surfacing
only -- nothing previously bridged those entity-scoped captures into a
tracked competitor's evidence/review queue, unlike ecosystem_intelligence
signals above. Confirmed live: a Toast enterprise-strategy capture with new,
specific figures (ARR targets, Toast IQ Grow ARR) surfaced in the brief's
"Capture Intelligence" section while Toast's battle card stayed unchanged at
3 stale evidence records. Same review-first discipline as the material-signal
pass -- an LLM-triaged capture summary is exactly the kind of interpretive
content this codebase never auto-applies. Deliberately scoped to
micro_graph_enrichment/micro_graph_build items only (never macro_signal,
which can name a dozen unrelated companies in one item, and whose
entity_scoped flag is only True for a single-entity match) -- and deliberately
NOT extended to other entity="<name>" tagged items in IntelligenceDB, because
a live check while building this found web_scan-sourced items tagged
entity="Toast" for RSS headlines merely containing the word "toast" as a food
term (e.g. "tom yam shrimp toast") -- a real, separate word-boundary
collision bug in whatever tags web_scan entities, unrelated to this fix.
micro_graph_enrichment/micro_graph_build streams are safe because
intelligence_triage._term_in_text() already does word-boundary-safe matching
(the same discipline the ecosystem mutation engine needed fixing into after
its own olo/ncr/qu substring-collision incident).

CLI:
    python3 competitor_intelligence_review.py scan
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import ecosystem_intelligence as ei  # noqa: E402
import ecosystem_brief as eb  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402

try:
    import intelligence_db as idb  # noqa: E402
    _HAS_INTELLIGENCE_DB = True
except Exception:  # noqa: BLE001
    _HAS_INTELLIGENCE_DB = False

_CAPTURE_PROMOTABLE_TYPES = frozenset({"micro_graph_enrichment", "micro_graph_build"})


def _material_signals_for_vendor(graph: dict, vendor_entity_id: str) -> list[dict]:
    out = []
    for sig in graph.get("signals", []):
        if vendor_entity_id not in (sig.get("entities") or []):
            continue
        conf = (sig.get("confidence") or {}).get("level", "")
        if eb._is_material_signal(conf, sig.get("signal_type", "")):
            out.append(sig)
    return out


def _capture_signals_for_vendor(comp: dict, *, days: int = 30) -> list[dict]:
    """Entity-scoped captures (pasted notes, transcripts) that name this
    competitor by display_name/alias and were classified as
    micro_graph_enrichment/micro_graph_build -- see module docstring for why
    those two types only. Matches via IntelligenceDB's exact (case-insensitive)
    tag match, same as competitor_intelligence.sync_from_ecosystem's own
    name matching -- never a raw substring search."""
    if not _HAS_INTELLIGENCE_DB:
        return []
    names = [n for n in [comp.get("display_name", "")] + (comp.get("aliases") or []) if n]
    if not names:
        return []
    try:
        db = idb.open_db()
    except Exception:  # noqa: BLE001
        return []
    try:
        seen_ids: set[str] = set()
        out: list[dict] = []
        for name in names:
            for item in db.get_by_entity(name, days=days):
                if item["id"] in seen_ids:
                    continue
                seen_ids.add(item["id"])
                try:
                    intel_type = (json.loads(item.get("raw_json") or "{}")
                                  .get("triage_stream", {}).get("intelligence_type"))
                except Exception:  # noqa: BLE001
                    intel_type = None
                if intel_type in _CAPTURE_PROMOTABLE_TYPES:
                    out.append(item)
        return out
    finally:
        db.close()


def _existing_keys(queue: dict) -> set[tuple]:
    return {
        (item.get("competitor_slug"), item.get("category"), item.get("evidence_id"), item.get("kind"))
        for item in queue.get("pending_reviews", [])
    }


def _next_review_id(queue: dict, slug: str) -> str:
    n = sum(1 for i in queue.get("pending_reviews", []) if i.get("competitor_slug") == slug) + 1
    return f"rev-{slug}-{n:04d}"


def run_review_scan(*, stale_after_days: int = 90) -> dict:
    """For every registered competitor: (a) material-signal pass -- real
    ecosystem signals tagged to its vendor_entity_id, classified via
    ecosystem_brief._is_material_signal(); idempotent on (competitor_slug,
    evidence_id) against the queue's own history, never auto-applied to
    category_battle_cards content. (b) capture-signal pass -- entity-scoped
    micro_graph_enrichment/micro_graph_build captures naming this competitor
    (see _capture_signals_for_vendor), idempotent on (competitor_slug,
    evidence_id=capture-<item_id>). (c) staleness pass -- any
    category_battle_cards entry with last_validated older than
    stale_after_days AND status not already in {stale, do_not_use} queues
    once, idempotent on (competitor_slug, category). Never mutates
    battle-card content itself -- purely proposes for Todd's review."""
    graph = ei._read_graph()
    reg = cic.load_registry()
    queue = cic.load_review_queue()
    queue.setdefault("pending_reviews", [])
    existing = _existing_keys(queue)
    today = date.today()
    n_queued = 0

    for entry in reg.get("registry", []):
        slug = entry.get("competitor_slug", "")
        comp_path = cic.competitor_dir(slug) / "competitor.json"
        if not comp_path.exists():
            continue
        comp = cic.load_json(comp_path)

        vendor_entity_id = comp.get("vendor_entity_id")
        if vendor_entity_id:
            for sig in _material_signals_for_vendor(graph, vendor_entity_id):
                evidence_id = f"eco-{sig.get('id', '')}"
                key = (slug, None, evidence_id, "material_signal_review")
                if key in existing:
                    continue
                queue["pending_reviews"].append({
                    "review_id": _next_review_id(queue, slug),
                    "competitor_slug": slug,
                    "category": None,
                    "evidence_id": evidence_id,
                    "kind": "material_signal_review",
                    "reason": f"New material signal ({sig.get('signal_type')}): {sig.get('summary', '')[:200]}",
                    "current_status": None,
                    "status": "pending",
                    "queued_at": cic.now_iso(),
                })
                existing.add(key)
                n_queued += 1

        for item in _capture_signals_for_vendor(comp):
            evidence_id = f"capture-{item['id']}"
            key = (slug, None, evidence_id, "capture_signal_review")
            if key in existing:
                continue
            queue["pending_reviews"].append({
                "review_id": _next_review_id(queue, slug),
                "competitor_slug": slug,
                "category": None,
                "evidence_id": evidence_id,
                "kind": "capture_signal_review",
                "reason": f"Captured intelligence ({item.get('gathered_date', '')}): {item.get('content', '')[:200]}",
                "current_status": None,
                "status": "pending",
                "queued_at": cic.now_iso(),
            })
            existing.add(key)
            n_queued += 1

        for category, card in (comp.get("category_battle_cards") or {}).items():
            status = card.get("status")
            if status in ("stale", "do_not_use"):
                continue
            last_validated = card.get("last_validated")
            is_stale = True
            if last_validated:
                try:
                    is_stale = (today - date.fromisoformat(last_validated)).days > stale_after_days
                except ValueError:
                    is_stale = True
            if not is_stale:
                continue
            key = (slug, category, None, "staleness_review")
            if key in existing:
                continue
            queue["pending_reviews"].append({
                "review_id": _next_review_id(queue, slug),
                "competitor_slug": slug,
                "category": category,
                "evidence_id": None,
                "kind": "staleness_review",
                "reason": f"Battle card for '{category}' has not been validated in over {stale_after_days} days.",
                "current_status": status,
                "status": "pending",
                "queued_at": cic.now_iso(),
            })
            existing.add(key)
            n_queued += 1

    cic.save_review_queue(queue)
    return {
        "ok": True,
        "reviews_queued": n_queued,
        "total_pending": sum(1 for i in queue["pending_reviews"] if i.get("status") == "pending"),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Competitor Intelligence review-queue scan.")
    p.add_argument("--stale-after-days", type=int, default=90)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    args = p.parse_args()

    if args.cmd == "scan":
        print(json.dumps(run_review_scan(stale_after_days=args.stale_after_days), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
