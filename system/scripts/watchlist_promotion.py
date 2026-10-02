#!/usr/bin/env python3
"""
watchlist_promotion.py — RB-2026-09-05, watchlist auto-expansion scoping.

Todd, after confirming the real canonical watchlist: "The system also
needs the ability to identify and add more watchlist companies based on
intelligence." Two halves, scoped via direct questions to Todd:

  Brand side (promotion bar: "repeated appearance over a window" --
  Todd's choice): technomic_watchlist_scan.py already scans the full
  Technomic 1500 daily/weekly and "reports" brands with real activity into
  a same-day-only cache -- a brand could show up for months with no path
  to permanent promotion. HISTORY_PATH (technomic_watchlist_scan.py's own
  append-only log) now gives this a real cross-day record to read.

  Vendor side (discovery approach: "mine what RB already collects" --
  Todd's choice): there is no "Technomic 1500" equivalent for restaurant-
  tech vendors, but ecosystem_intelligence.json already holds 89 real
  vendor entities, 63 of them not on the watchlist -- found while scoping
  this, with zero new data-sourcing work required. Filters out name
  variants of already-tracked vendors (reusing technomic_watchlist_scan's
  own fuzzy-match logic) and internal/placeholder entity rows, then
  requires a minimum real relationship count so a thin one-off entry isn't
  proposed as if it were a real emerging vendor.

Confidence-Based Auto-Recording Phase 5 (2026-09-25): every candidate here
is purely additive -- confirming a brand just appends a name to
watchlist_registry.json's restaurant_brands list, and confirming a vendor
appends to restaurant_tech; there is never an existing value to protect
(unlike ownership_promotion.py/executive_move_promotion.py, the two queues
still gated on confidence, since they can overwrite). Both scan functions
now auto-apply a newly-detected candidate the moment it clears its own
already-real evidence bar (3+ appearance days, 2+ active relationships --
unchanged), tagging it "system:watchlist_promotion" via record_proposal()'s
confirmed_by, the same human:/system: provenance convention used elsewhere
in this codebase (e.g. brand_profile_common.py's last_reviewed_by). The
candidate store stays -- it's now a same-shaped audit trail of what was
auto-applied and why, not a queue anyone waits on.

CLI:
    python3 watchlist_promotion.py scan            # scan both sides, write candidates
    python3 watchlist_promotion.py pending          # list pending candidates
    python3 watchlist_promotion.py confirm <id>     # promote into watchlist_registry.json
    python3 watchlist_promotion.py reject <id>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import technomic_watchlist_scan as tws  # noqa: E402
import watchlist_registry as wr  # noqa: E402

STORE_PATH = core.CACHE_DIR / "watchlist_promotion_candidates.json"

# RB-2026-09-05: 90 days, not 14 -- technomic_watchlist_scan.py's own
# tiering means a Tier 2 brand (the ~1400 outside the top 100) is only
# actually scanned in a ~weekly rotating batch, not daily. A 14-day window
# would make Tier 2 promotion mathematically impossible (at most ~2 scan
# opportunities). 90 days gives Tier 2 brands a realistic number of
# opportunities to cross the bar while still meaning something as a
# "keeps coming up" signal, not an unbounded lifetime count.
BRAND_PROMOTION_WINDOW_DAYS = 90
BRAND_PROMOTION_MIN_APPEARANCES = 3

# A vendor entity with fewer than this many real ACTIVE brand relationships
# is too thin to treat as an emerging candidate worth Todd's review time.
VENDOR_MIN_ACTIVE_RELATIONSHIPS = 2

# Internal/placeholder vendor rows (e.g. "custom_internal_pos", "Panera_
# internal_digital") have no distinguishing schema field from a real vendor
# entity (entity_type/subtype/status are identical) -- name shape is the
# only real signal. Real vendor names in this graph are never underscore-
# joined and never contain the literal word "internal".
_VENDOR_JUNK_RE = re.compile(r"internal|_", re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_store() -> dict:
    if not STORE_PATH.exists():
        return {"candidates": {}}
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"candidates": {}}
    data.setdefault("candidates", {})
    return data


def _save_store(store: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    store["_generated_at"] = _now_iso()
    STORE_PATH.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")


def _candidate_id(category: str, name: str) -> str:
    return f"{category}::{name}"


def _add_candidate(store: dict, *, category: str, name: str, reason: str, evidence: dict) -> bool:
    """Insert one pending candidate. Returns True if genuinely new (a
    candidate already resolved -- confirmed or rejected -- is never
    re-proposed; a still-pending one is left untouched, not refreshed,
    since re-scanning shouldn't reset Todd's own review clock)."""
    cid = _candidate_id(category, name)
    existing = store["candidates"].get(cid)
    if existing:
        return False
    store["candidates"][cid] = {
        "candidate_id": cid,
        "status": "proposed_pending_confirmation",
        "category": category,  # "brand" | "vendor"
        "name": name,
        "reason": reason,
        "evidence": evidence,
        "detected_at": _now_iso(),
        "resolved_at": None,
    }
    return True


def pending_candidates() -> list[dict]:
    store = _load_store()
    return [c for c in store["candidates"].values() if c["status"] == "proposed_pending_confirmation"]


def _load_history(today: date, window_days: int) -> dict[str, list[str]]:
    """{entity_name: [scan dates within the trailing window]}, read from
    technomic_watchlist_scan.py's own append-only HISTORY_PATH."""
    if not tws.HISTORY_PATH.exists():
        return {}
    cutoff = (today - timedelta(days=window_days)).isoformat()
    by_name: dict[str, list[str]] = {}
    for line in tws.HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        d = row.get("date")
        if not d or d < cutoff:
            continue
        by_name.setdefault(row["name"], []).append(d)
    return by_name


def scan_brand_promotions(today: date | None = None, *, dry_run: bool = False) -> dict:
    """Propose permanent promotion for any brand that's appeared in
    technomic_watchlist_scan.py's daily "reported" surface at least
    BRAND_PROMOTION_MIN_APPEARANCES times within BRAND_PROMOTION_WINDOW_DAYS
    and isn't already on the watchlist."""
    today = today or date.today()
    registry = wr.load_registry()
    mandatory_lower = {n.strip().lower() for n in wr.mandatory_all(registry)}

    history = _load_history(today, BRAND_PROMOTION_WINDOW_DAYS)
    store = _load_store()
    new_count = 0
    scanned = 0
    to_auto_apply: list[str] = []
    for name, dates in history.items():
        scanned += 1
        if name.strip().lower() in mandatory_lower:
            continue
        distinct_dates = sorted(set(dates))
        if len(distinct_dates) < BRAND_PROMOTION_MIN_APPEARANCES:
            continue
        added = _add_candidate(
            store, category="brand", name=name,
            reason=(
                f"Reported {len(distinct_dates)} time(s) in the trailing "
                f"{BRAND_PROMOTION_WINDOW_DAYS} days (technomic_watchlist_scan.py)."
            ),
            evidence={"appearance_dates": distinct_dates},
        )
        if added:
            new_count += 1
            to_auto_apply.append(_candidate_id("brand", name))

    auto_applied = 0
    if not dry_run and new_count:
        _save_store(store)
        for cid in to_auto_apply:
            result = record_proposal(cid, confirmed=True, confirmed_by="system:watchlist_promotion")
            if result.get("confirmed"):
                auto_applied += 1
    return {"scanned": scanned, "new_candidates": new_count, "auto_applied": auto_applied}


def scan_vendor_candidates(*, dry_run: bool = False) -> dict:
    """Propose permanent promotion for any real vendor entity already
    sitting in ecosystem_intelligence.json that isn't on the watchlist,
    isn't a name-variant of an already-tracked vendor, isn't an internal/
    placeholder row, and has real relationship weight behind it."""
    graph = ei._read_graph()
    registry = wr.load_registry()
    mandatory_all = wr.mandatory_all(registry)
    mandatory_lower = {n.strip().lower() for n in mandatory_all}
    mandatory_token_sets = [tws._normalize_name_tokens(n) for n in mandatory_all]

    rel_counts: dict[str, int] = {}
    for rel in graph.get("relationships") or []:
        if rel.get("status") == "active":
            vid = rel.get("to_entity_id")
            if vid:
                rel_counts[vid] = rel_counts.get(vid, 0) + 1

    store = _load_store()
    scanned = 0
    new_count = 0
    to_auto_apply: list[str] = []
    for entity in graph.get("entities") or []:
        if entity.get("entity_type") != "vendor":
            continue
        name = entity.get("name") or ""
        if not name:
            continue
        scanned += 1
        if name.strip().lower() in mandatory_lower:
            continue
        if tws._already_mandatory(name, mandatory_lower, mandatory_token_sets):
            continue
        if _VENDOR_JUNK_RE.search(name):
            continue
        count = rel_counts.get(entity.get("id"), 0)
        if count < VENDOR_MIN_ACTIVE_RELATIONSHIPS:
            continue
        added = _add_candidate(
            store, category="vendor", name=name,
            reason=(
                f"{count} real active brand relationship(s) already on file in "
                "ecosystem_intelligence.json; not yet on the watchlist."
            ),
            evidence={
                "active_relationship_count": count,
                "primary_category": (entity.get("attributes") or {}).get("primary_category"),
            },
        )
        if added:
            new_count += 1
            to_auto_apply.append(_candidate_id("vendor", name))

    auto_applied = 0
    if not dry_run and new_count:
        _save_store(store)
        for cid in to_auto_apply:
            result = record_proposal(cid, confirmed=True, confirmed_by="system:watchlist_promotion")
            if result.get("confirmed"):
                auto_applied += 1
    return {"scanned": scanned, "new_candidates": new_count, "auto_applied": auto_applied}


def scan(today: date | None = None, *, dry_run: bool = False) -> dict:
    brand_result = scan_brand_promotions(today, dry_run=dry_run)
    vendor_result = scan_vendor_candidates(dry_run=dry_run)
    return {"brand": brand_result, "vendor": vendor_result}


def record_proposal(candidate_id: str, *, confirmed: bool, confirmed_by: str = "human") -> dict:
    store = _load_store()
    cand = store["candidates"].get(candidate_id)
    if not cand:
        return {"error": f"unknown candidate id: {candidate_id}"}
    if cand["status"] != "proposed_pending_confirmation":
        return {"error": f"candidate {candidate_id} already resolved: {cand['status']}"}

    if not confirmed:
        cand["status"] = "rejected"
        cand["resolved_at"] = _now_iso()
        _save_store(store)
        return {"rejected": True, "candidate_id": candidate_id}

    if cand["category"] == "brand":
        wr.add_restaurant_brand(cand["name"])
    elif cand["category"] == "vendor":
        # Default bucket -- Todd (or a follow-up edit) can move it to a
        # more specific category directly in watchlist_registry.json; it's
        # a plain JSON file now, not two hardcoded Python literals.
        wr.add_restaurant_tech(cand["name"], category="restaurant_tech_other")
    else:
        return {"error": f"unknown candidate category: {cand['category']!r}"}

    cand["status"] = "confirmed"
    cand["resolved_at"] = _now_iso()
    cand["confirmed_by"] = confirmed_by
    _save_store(store)
    return {"confirmed": True, "candidate_id": candidate_id, "category": cand["category"], "name": cand["name"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    sub.add_parser("pending")
    p_confirm = sub.add_parser("confirm")
    p_confirm.add_argument("candidate_id")
    p_reject = sub.add_parser("reject")
    p_reject.add_argument("candidate_id")
    args = parser.parse_args()

    if args.cmd == "scan":
        result = scan()
        print(json.dumps(result, indent=2))
    elif args.cmd == "pending":
        print(json.dumps(pending_candidates(), indent=2))
    elif args.cmd == "confirm":
        print(json.dumps(record_proposal(args.candidate_id, confirmed=True), indent=2))
    elif args.cmd == "reject":
        print(json.dumps(record_proposal(args.candidate_id, confirmed=False), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
