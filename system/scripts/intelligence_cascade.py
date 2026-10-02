#!/usr/bin/env python3
"""
intelligence_cascade.py — the missing link between daily intelligence
collection and RBB's downstream artifacts (Blue Sheets, Account Research).

Built 2026-08-27/28, after Todd asked RB to verify a concern that the
morning intelligence-gathering cycle wasn't actually persisting/mutating
real records, and — once that turned out to be two real, narrower bugs
(touchContact/confirmProposal passing the wrong kind of id) — described
the full cycle he actually wants, closing with: "CoS assess downstream
artifacts (account briefs, blue sheets, account plans, etc) and updates as
needed." That step did not exist anywhere. This module is it.

Design, confirmed with Todd directly:
  - New intelligence gets FLAGGED, not immediately processed. Todd can act
    on a flag any time during the day via chat.
  - A flag may not sit open indefinitely: if still unaddressed by the next
    day's cascade run (>=24h old), that run automatically processes it.
  - The 24h SLA applies ONLY to things safe to resolve without human
    judgment: Account Research regeneration (re-rendering from facts that
    already exist -- no fabrication risk) and Blue Sheet updates that are
    already an unambiguous, single-field structural match (the same narrow
    guard ecosystem_intelligence.py already enforces). Genuinely ambiguous
    Blue Sheet matches are explicitly EXEMPT from the SLA and stay queued
    until Todd resolves them, no matter how long that takes -- an aging
    clock never turns a judgment call into an auto-apply.

This module does not re-scan raw sources or re-derive detection/
materiality logic that already exists elsewhere (ecosystem_brief.py,
technomic_watchlist_scan.py, entity_alerts.py) -- it reads what those
already wrote (ecosystem_intelligence.json signals/relationships,
entity_alerts_cache.json, technomic_watchlist_promoted.json) and acts on
it. It also never synthesizes a guessed field-level change from free
text -- the only auto-apply path is the same narrow
match_technology_stack_row() single-row match ecosystem_intelligence.py's
_sync_blue_sheet_technology_stack() already restricts itself to; anything
else becomes a real review_queue.json entry, never a silent write.

CLI:
    python3 system/scripts/intelligence_cascade.py --run [--date YYYY-MM-DD]
    python3 system/scripts/intelligence_cascade.py --verify [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import ecosystem_brief as eb  # noqa: E402
import account_background_brief as abb  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402

BLUE_SHEETS_ENGINE_DIR = core.PROJECT_DIR / "blue_sheets" / "_engine"
sys.path.insert(0, str(BLUE_SHEETS_ENGINE_DIR))
import common as bs_common  # noqa: E402

# Master Account Plan (RB-2026-08-28) -- "mid-stream artifact that mutates
# with gathered intelligence... updated like a blue sheet" per Todd's
# direction. Additive, never blocks: same optional-import precedent as
# every other hook in this module.
MASTER_ACCOUNT_PLANS_ENGINE_DIR = core.PROJECT_DIR / "master_account_plans" / "_engine"
sys.path.insert(0, str(MASTER_ACCOUNT_PLANS_ENGINE_DIR))
try:
    import mp_impact_review  # noqa: E402
except Exception:  # noqa: BLE001
    mp_impact_review = None

CACHE_PATH = core.CACHE_DIR / "intelligence_cascade.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


# ---------------------------------------------------------------------------
# Detection — reuse recorded state, never re-scan raw sources
# ---------------------------------------------------------------------------

def _entity_name_for(entity_id: Optional[str], graph: dict) -> str:
    if not entity_id:
        return "unknown"
    for e in graph.get("entities", []):
        if e.get("id") == entity_id:
            return e.get("name", entity_id)
    return entity_id


def _signals_today(today_str: str, graph: dict) -> list[dict]:
    return [s for s in graph.get("signals", []) if str(s.get("captured_at", ""))[:10] == today_str]


def _relationships_today(today_str: str, graph: dict) -> list[dict]:
    return [r for r in graph.get("relationships", []) if str(r.get("updated_at", ""))[:10] == today_str]


def _entities_with_new_intelligence_today(today_str: str, graph: dict) -> dict[str, dict]:
    """Union of every already-populated real-time detection source, keyed
    by entity display name. Each entry: {sources: [...], material: bool,
    entity_id}. 'material' reuses ecosystem_brief's own materiality gate
    (eb._is_material_signal) -- not re-derived here."""
    result: dict[str, dict] = {}

    for sig in _signals_today(today_str, graph):
        confidence = (sig.get("confidence") or {}).get("level", "")
        signal_type = sig.get("signal_type", "")
        material = eb._is_material_signal(confidence, signal_type)
        for entity_id in sig.get("entities", []):
            name = _entity_name_for(entity_id, graph)
            entry = result.setdefault(name, {"sources": [], "material": False, "entity_id": entity_id})
            entry["sources"].append({"type": "ecosystem_signal", "id": sig.get("id")})
            entry["material"] = entry["material"] or material

    alerts = _load_json(core.CACHE_DIR / "entity_alerts_cache.json", {})
    for name, rec in (alerts.get("entities") or {}).items():
        items = (rec.get("items") or []) + (rec.get("vulnerability_items") or [])
        hits = [i for i in items if str(i.get("pub_date", ""))[:10] == today_str]
        if hits:
            entry = result.setdefault(name, {"sources": [], "material": True, "entity_id": None})
            entry["sources"].append({"type": "entity_alert", "count": len(hits)})
            entry["material"] = True

    promoted = _load_json(core.CACHE_DIR / "technomic_watchlist_promoted.json", {})
    if promoted.get("_scan_date") == today_str:
        for ent in promoted.get("entities") or []:
            name = ent.get("name")
            if not name:
                continue
            entry = result.setdefault(name, {"sources": [], "material": True, "entity_id": None})
            entry["sources"].append({"type": "technomic_promoted", "tier": ent.get("tier")})
            entry["material"] = True

    return result


# ---------------------------------------------------------------------------
# customers_prospects/ cascade — merges what were two separate functions
# (_apply_blue_sheet_sync, _process_account_research) per the original
# 3-store unification plan's own step 4 (RB-2026-09-08). Both pieces of
# logic operate against the same unified customers_prospects/ tree but
# remain genuinely different behaviors internally -- Blue Sheet's narrow,
# structural, single-row technology_stack sync (auto-apply only on an
# unambiguous match, otherwise a real review_queue.json entry, never a
# guessed write) vs. Account Research's time-based refresh-flag SLA sweep
# (flag today, auto-regenerate tomorrow if still unaddressed). This is a
# structural consolidation, not a behavioral change -- every existing
# dispatch/gating rule from both original functions is preserved exactly,
# including that neither is driven by customers_prospects' own
# engagement_tier field (Blue Sheet still gates on bs_common.is_activated()
# / workbook_path; Account Research still gates on abb.resolve_account()'s
# exists flag, which is tier-agnostic) -- confirmed via investigation this
# is real, pre-existing behavior, not something this merge should silently
# change.
# ---------------------------------------------------------------------------

def _apply_customers_prospects_sync(relationships: list[dict], entities: dict[str, dict], today_str: str) -> dict:
    # --- Blue Sheet technology-stack sync -----------------------------------
    auto_synced: list[dict] = []
    queued_for_review: list[dict] = []
    coverage_gaps: list[dict] = []

    for rel in relationships:
        entity_id = rel.get("from_entity_id")
        category = rel.get("category")
        posture = rel.get("evidence_posture")
        if not entity_id:
            continue
        slug = bs_common.entity_id_to_slug(entity_id)
        if slug is None:
            continue  # not a brand entity -- no Blue Sheet concept applies

        if not bs_common.is_activated(slug):
            try:
                bs_common.log_coverage_event(
                    entity_id=entity_id, slug=slug, activated=False,
                    mutation_type="daily_cascade_relationship_touch",
                    detail=f"category={category} posture={posture}",
                )
            except Exception:  # noqa: BLE001
                pass
            coverage_gaps.append({"entity_id": entity_id, "slug": slug, "category": category})
            continue

        status = bs_common.POSTURE_TO_STATUS.get(posture) if posture else None
        if not category or status is None:
            continue  # nothing this narrow mechanism can act on either way

        account: dict = {}
        idx = None
        try:
            account = bs_common.load_json(bs_common.account_dir(slug) / "account.json")
            idx = bs_common.match_technology_stack_row(account, category)
        except Exception:  # noqa: BLE001
            idx = None

        if idx is not None:
            ei._sync_blue_sheet_technology_stack(
                entity_id, category=category, new_posture=posture,
                source_title=f"intelligence_cascade: {rel.get('id', '')}", source_url=None,
            )
            auto_synced.append({"slug": slug, "category": category, "row": idx})
        else:
            # Genuinely ambiguous -- never guess. A real signal_notification,
            # never a synthesized ProposedChange from free text. Explicitly
            # exempt from the 24h SLA: this stays queued until Todd resolves
            # it by hand, regardless of how long that takes.
            rq_path = bs_common.ROOT / "_portfolio" / "review_queue.json"
            rq = bs_common.load_json(rq_path) if rq_path.exists() else {"pending_reviews": []}
            rq.setdefault("pending_reviews", []).append({
                "account_id": account.get("account_id", f"acct-{slug}"),
                "kind": "signal_notification",
                "path": None,
                "new_value": None,
                "reason": (
                    f"New intelligence today for category={category!r}, posture={posture!r} "
                    "-- no unambiguous technology_stack row to auto-apply. Needs human review."
                ),
                "evidence_id": None,
                "status": "pending",
                "queued_at": _now_iso(),
            })
            bs_common.save_json(rq_path, rq)
            queued_for_review.append({"slug": slug, "category": category})

    # --- Account Research refresh-flag SLA sweep + new-flag detection ------
    flagged_today: list[str] = []
    auto_processed: list[str] = []

    # Sweep first: any PRIOR-day pending flag gets processed now, regardless
    # of whether that entity has fresh intelligence today -- the SLA is
    # about the flag's age, not today's activity.
    flags = cpc.load_refresh_flags()
    for slug, flag in list(flags.items()):
        if flag.get("status") != "pending":
            continue
        first_flagged = flag.get("first_flagged_at")
        if first_flagged and first_flagged < today_str:
            try:
                abb.generate_brief(
                    slug, generated_for="",
                    purpose="Automated 24h SLA refresh — flagged stale on a prior run and not yet addressed.",
                )
                auto_processed.append(slug)  # generate_brief() clears the flag itself
            except Exception:  # noqa: BLE001
                pass  # leave it pending; retried on the next run

    # New flags: entities with real new intelligence today, an existing
    # Account Research record, and real staleness.
    for name in entities:
        try:
            slug, exists = abb.resolve_account(name)
        except Exception:  # noqa: BLE001
            continue
        if not exists:
            continue
        try:
            intel = abb.retrieve_existing_intelligence(slug)
            freshness = abb.assess_freshness(intel)
        except Exception:  # noqa: BLE001
            continue
        stale_count = len(freshness.get("stale") or [])
        missing_count = len(freshness.get("missing") or [])
        if stale_count or missing_count:
            created = cpc.flag_needs_refresh(
                slug,
                reason=f"New intelligence detected {today_str}; {stale_count} stale + {missing_count} missing field(s).",
            )
            if created:
                flagged_today.append(slug)

    return {
        "blue_sheets_auto_synced": auto_synced,
        "blue_sheets_queued_for_review": queued_for_review,
        "coverage_gaps": coverage_gaps,
        "account_research_flagged_stale": flagged_today,
        "account_research_auto_processed": auto_processed,
    }


# ---------------------------------------------------------------------------
# Coverage-gap consumption — the actual missing reader for coverage_log.jsonl
# ---------------------------------------------------------------------------

def assess_coverage_gaps(today: date, lookback_days: int = 14) -> list[dict]:
    """Reads the (now-populated) coverage_log.jsonl, groups by entity, and
    surfaces entities touched >=2 times in the window with no activated
    Blue Sheet and no Account Research match. Threshold is an initial
    default, tunable once real volume exists -- this is the exact question
    log_coverage_event()'s own docstring was written to answer, with no
    consumer until now."""
    cutoff = (today - timedelta(days=lookback_days)).isoformat()
    events = bs_common.load_jsonl(bs_common.coverage_log_path())
    graph = ei._read_graph()

    by_entity: dict[str, list[dict]] = {}
    for e in events:
        if str(e.get("logged_at", ""))[:10] < cutoff:
            continue
        if e.get("activated"):
            continue
        entity_id = e.get("entity_id")
        if not entity_id:
            continue
        by_entity.setdefault(entity_id, []).append(e)

    gaps = []
    for entity_id, touches in by_entity.items():
        if len(touches) < 2:
            continue
        name = _entity_name_for(entity_id, graph)
        try:
            _slug, has_account_research = abb.resolve_account(name)
        except Exception:  # noqa: BLE001
            has_account_research = False
        if has_account_research:
            continue
        gaps.append({
            "entity_id": entity_id, "entity_name": name,
            "touch_count": len(touches), "last_touch": touches[-1].get("logged_at"),
        })
    return gaps


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_cascade(today: date) -> dict:
    """Idempotent per calendar day -- daily_brief.build_report() runs twice
    per full pipeline cycle (write_today_and_manifest, then independently
    inside publish_canonical_artifacts), so this must be a correctness
    requirement, not an optimization: re-running the apply steps would
    double-append review_queue.json/coverage_log.jsonl."""
    today_str = today.isoformat()
    cached = _load_json(CACHE_PATH, {})
    if cached.get("date") == today_str:
        return cached

    graph = ei._read_graph()
    entities = _entities_with_new_intelligence_today(today_str, graph)
    relationships = _relationships_today(today_str, graph)

    sync_result = _apply_customers_prospects_sync(relationships, entities, today_str)
    coverage_gaps_14d = assess_coverage_gaps(today)

    master_account_plans_auto_synced = []
    master_account_plans_queued_for_review = []
    if mp_impact_review is not None:
        try:
            for vendor_slug, result in mp_impact_review.run_all_vendors().items():
                if not result.get("ok"):
                    continue
                if result.get("evidence_appended") or result.get("cross_links_refreshed"):
                    master_account_plans_auto_synced.append(vendor_slug)
                master_account_plans_queued_for_review.extend(
                    f"{vendor_slug}:{item['account_name']}" for item in result.get("queued_for_review", [])
                )
        except Exception:  # noqa: BLE001
            pass

    report = {
        "date": today_str,
        "generated_at": _now_iso(),
        "entities_with_new_intelligence": len(entities),
        "relationships_touched": len(relationships),
        "blue_sheets_auto_synced": sync_result["blue_sheets_auto_synced"],
        "blue_sheets_queued_for_review": sync_result["blue_sheets_queued_for_review"],
        "account_research_flagged_stale": sync_result["account_research_flagged_stale"],
        "account_research_auto_processed": sync_result["account_research_auto_processed"],
        "coverage_gaps_today": sync_result["coverage_gaps"],
        "coverage_gaps_14d_pattern": coverage_gaps_14d,
        "master_account_plans_auto_synced": master_account_plans_auto_synced,
        "master_account_plans_queued_for_review": master_account_plans_queued_for_review,
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return report


def build_section(today: Optional[date] = None) -> dict:
    """Entry point for daily_brief.py — call immediately after
    ecosystem_intelligence_report = _eb.build_section(today=today) so this
    sees the same day's freshly-written signals/relationships."""
    return run_cascade(today or date.today())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--date", default=None)
    args = parser.parse_args()

    the_date = date.fromisoformat(args.date) if args.date else date.today()

    if args.verify:
        cached = _load_json(CACHE_PATH, {})
        ok = cached.get("date") == the_date.isoformat() and "error" not in cached
        print(json.dumps({"ok": ok, "date": the_date.isoformat(), "cache_date": cached.get("date")}, indent=2))
        return 0 if ok else 1

    report = run_cascade(the_date)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
