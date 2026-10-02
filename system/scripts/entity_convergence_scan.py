#!/usr/bin/env python3
"""
entity_convergence_scan.py — proactive cross-signal pattern scan.

RB-2026-08-28. Priority item #5 from the same-day strategic assessment:
"the least-built capability... seeing what the CEO doesn't see." The hard
part -- safely correlating signals across sources without inventing
anything -- was already solved: signal_synthesis.py aggregates every RB
store for a named entity and mechanically scores real, already-persisted
signals against a fixed pattern taxonomy (exit_positioning, distress,
consolidation, competitive_shift, growth_mode, transition, stable,
unknown). No free text generation, no new fabrication surface. It just
never ran on anything -- it only ever worked on-demand, for one entity a
human already had to think to ask about.

This closes that gap: run it every morning across active Blue Sheet
accounts, tracked competitors, and the FULL watchlist (not just tier_1 --
Todd's explicit correction, 2026-08-28: "we should be tracking all of our
watchlist companies if the goal is to see things coming that the user
cannot see on our own"). RB-2026-08-29: the "full watchlist" source used
at first (ecosystem_intelligence.json's watch_list, 15 entries) turned out
to be the wrong, stale list -- the real canonical watchlist Todd scoped to
120-150 companies on 2026-08-19 is entity_alerts.MANDATORY_ALL (155
entries: 78 restaurant brands + 77 restaurant-tech vendors), already used
daily by daily_brief.py and entity_alerts.py for material-activity
surfacing. Fixed to scan that instead.

Surfaces only genuinely actionable classifications
(not stable/unknown/transition) at medium+ confidence, and -- same
discipline already proven twice elsewhere in this codebase (watchlist
escalation decay, loop-ledger repeat suppression) -- never re-show the
identical finding in full every single day. A persisting classification
gets a brief mention; only a new or changed one gets full detail.

Explicitly scoped as v1, not the full vision. Todd's actual ambition
(2026-08-28) is considerably larger: genuine event recording and scoring
over time (not just a point-in-time snapshot), a deep historical baseline
built from 24+ months of earnings-call analysis across both watchlist
brands and competitors, real predictive read ("is this customer shopping
for a new solution," "is this competitor vulnerable," "which go-to-market
strategies are genuinely disrupting the category vs. generic industry-
speak"), and a competitive battle-card artifact comparing Genius's own
products against what's on file for a customer/competitor. None of that is
built here -- it needs its own scoped design and data-ingestion effort
(see the roadmap entry logged the same day). This module lays real
groundwork for it (the entity list, the per-entity signal engine, the
first-seen/last-seen state trail this can extend into a real event log)
without pretending to be the finished thing.

Usage:
    python3 entity_convergence_scan.py            # scan + write state, text report
    python3 entity_convergence_scan.py --json      # machine-readable
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import signal_synthesis as ss  # noqa: E402
import entity_alerts as ea  # noqa: E402

sys.path.insert(0, str(SCRIPTS_DIR.parent.parent / "blue_sheets" / "_engine"))
import common as bs_common  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402

STATE_PATH = core.CACHE_DIR / "entity_convergence_state.json"
RESULT_PATH = core.CACHE_DIR / "entity_convergence_scan.json"

# stable/unknown/transition are "nothing notable" outcomes by the pattern
# taxonomy's own design -- only these represent a real, actionable read.
ACTIONABLE_PATTERNS = {
    ss.PATTERN_EXIT, ss.PATTERN_DISTRESS, ss.PATTERN_CONSOLIDATION,
    ss.PATTERN_COMPETITIVE, ss.PATTERN_GROWTH,
}
MIN_CONFIDENCE = {"medium", "high"}


def _watched_entity_names() -> list[str]:
    """The bounded scan scope: active Blue Sheet accounts, tracked
    competitors, and the full mandatory watchlist (entity_alerts.MANDATORY_ALL
    -- 155 entities, 78 restaurant brands/potential-customers + 77
    restaurant-tech vendors/competitors, Todd's 120-150-company scope from
    2026-08-19) -- everything Todd is actually working, not the whole
    ecosystem graph. Deduped, real display names."""
    names: dict[str, None] = {}  # dict as ordered set

    try:
        reg = bs_common.load_registry()
        for entry in reg.get("registry", []):
            if entry.get("status") not in ("active", "current"):
                continue
            slug = entry.get("account_id", "").replace("acct-", "", 1)
            display_name = slug.replace("-", " ").title()
            account_json = bs_common.CUSTOMERS_PROSPECTS_ROOT / "accounts" / slug / "account.json"
            if account_json.exists():
                try:
                    acct = json.loads(account_json.read_text(encoding="utf-8"))
                    display_name = acct.get("display_name") or display_name
                except (OSError, json.JSONDecodeError):
                    pass
            names.setdefault(display_name)
    except Exception:  # noqa: BLE001 -- one source's failure must not block the others
        pass

    try:
        reg = cic.load_registry()
        for entry in reg.get("registry", []):
            display_name = entry.get("display_name")
            if display_name:
                names.setdefault(display_name)
    except Exception:  # noqa: BLE001
        pass

    # RB-2026-08-29: was ecosystem_intelligence.json's watch_list (15
    # entries) -- a much smaller, stale list, not the real watchlist. The
    # canonical 120-150-company watchlist Todd scoped 2026-08-19 lives here.
    try:
        for name in ea.MANDATORY_ALL:
            names.setdefault(name)
    except Exception:  # noqa: BLE001
        pass

    return list(names.keys())


def _entity_aliases_by_name() -> dict[str, list[str]]:
    """display_name -> real alias list, for every tracked competitor.

    RB-DEFECT (2026-09-16): _watched_entity_names() above returns only the
    single canonical display_name per entity, discarding each competitor's
    real, already-recorded alias list (e.g. "NCR Corporation", "NCR Voyix",
    "NCR Aloha", "Aloha POS" for the competitor whose canonical name is
    "NCR Voyix"). Without this, a signal mentioning a tracked entity only
    by an alternate/short name never counted toward its convergence
    pattern -- this is RB's actual "connect the dots" engine silently
    under-counting for any company with a rebrand, an abbreviation, or an
    acquisition-driven name change.

    Thin wrapper: the real lookup moved to
    competitor_intelligence_common.aliases_by_display_name() so
    query_engine.py's ad-hoc lookups share the same source of truth
    instead of drifting from this scan's copy. Kept here (same name) so
    the daily scan's call site and this file's existing test coverage
    don't need to change."""
    return cic.aliases_by_display_name()


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _save_result(result: dict) -> None:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")


def load_last_result() -> dict | None:
    """For render_intelligence_brief.py -- reads the last scan's persisted
    result. Rendering never re-runs the scan itself (a scheduled
    morning_pipeline.py step does that); this is a pure read."""
    if not RESULT_PATH.exists():
        return None
    try:
        return json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def run_scan(*, lookback_days: int = 90) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = now[:10]
    state = _load_state()
    new_state: dict = {}

    new_findings: list[dict] = []
    persisting_findings: list[dict] = []

    aliases_by_name = _entity_aliases_by_name()
    for name in _watched_entity_names():
        try:
            result = ss.synthesize_entity_signals(
                name, lookback_days=lookback_days, aliases=aliases_by_name.get(name),
            )
        except Exception as exc:  # noqa: BLE001 -- one entity's failure must not block the scan
            continue

        pattern = result.get("dominant_pattern")
        confidence = result.get("pattern_confidence")
        if pattern not in ACTIONABLE_PATTERNS or confidence not in MIN_CONFIDENCE:
            continue

        prior = state.get(name)
        is_new_or_changed = not prior or prior.get("dominant_pattern") != pattern
        first_seen = today if is_new_or_changed or not prior else prior.get("first_seen", today)

        new_state[name] = {
            "dominant_pattern": pattern,
            "pattern_confidence": confidence,
            "first_seen": first_seen,
            "last_seen": today,
        }

        finding = {
            "entity": name,
            "dominant_pattern": pattern,
            "pattern_confidence": confidence,
            "synthesis_hypothesis": result.get("synthesis_hypothesis"),
            "opportunity_or_risk": result.get("opportunity_or_risk"),
            "signal_count": result.get("signal_count"),
            "first_seen": first_seen,
        }
        if is_new_or_changed:
            new_findings.append(finding)
        else:
            persisting_findings.append(finding)

    _save_state(new_state)

    result = {
        "generated_at": now,
        "entities_scanned": len(_watched_entity_names()),
        "new_findings": new_findings,
        "persisting_findings": persisting_findings,
    }
    _save_result(result)
    return result


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("--days", type=int, default=90)
    args = p.parse_args()

    result = run_scan(lookback_days=args.days)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"entity_convergence_scan: {result['entities_scanned']} entities scanned")
        print(f"  new/changed: {len(result['new_findings'])}")
        for f in result["new_findings"]:
            print(f"    {f['entity']}: {f['dominant_pattern']} ({f['pattern_confidence']})")
        print(f"  persisting: {len(result['persisting_findings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
