#!/usr/bin/env python3
"""
relationship_reactivation_scan.py — dormant-contact reactivation scan.

RB-2026-09-11. Item 3 of the original 3-category scoping ("does M&A/
exec-moves/relationship-intel deserve tech-stack-style weekend deep-
research?"), taken last after M&A (ownership_promotion.py) and executive-
moves (executive_move_promotion.py). Both of those turned out to be
"detection exists daily, structured capture doesn't." This one is a
genuinely different shape: the relationship-side artifact suite
(relationship_card.py, inner_circle.py, referral_network.py,
relationship_plan.py, closed out 2026-09-08) only serves the 20 contacts
already tagged rc_tier in {inner, broader, dormant_valuable} -- the other
3,095 of 3,115 total baseline contacts are untiered, exactly the "dormant
capacity" CHARTER.md's own mission statement names as the reason this
system exists. Nothing scored or surfaced which of those 3,095 are worth
reactivating.

Todd's own scoping choice (2026-09-11, asked directly): a cheap
cross-reference of data already on file, not new external research. This
checks whether an untiered contact's current_company (already known, from
LinkedIn/manual capture -- job-change detection for LinkedIn-connected
contacts is already passive via re-export, not a gap this closes) now
matches one of Todd's tracked customers_prospects accounts (14 registered,
both pre_engagement/research-stage and active_engagement -- a contact at a
prospect Todd is researching is just as worth surfacing as one at an
active deal, so this doesn't filter by engagement_tier) or a tracked
vendor/brand in the 1,790-entity restaurant-tech ecosystem graph. A match
means a currently-dormant relationship just became newly relevant.

Matching reuses ecosystem_intelligence._resolve_entity_id_any_type(),
already proven for this identical free-text-name -> entity resolution by
ownership_promotion.py and executive_move_promotion.py -- exact-after-
normalization only, no fuzzy matching, no legal-suffix stripping (relies
on each entity's own aliases list already covering variants). The
customers_prospects accounts aren't in that resolver's scope (a separate
registry, not the ecosystem graph), so a small local index is built from
the same _norm_key() for consistency.

Same "computed scan, no mutation, decay dedup" shape as
entity_convergence_scan.py -- not the review-first candidate-store shape
tech_stack_relationship_promotion.py/ownership_promotion.py/
executive_move_promotion.py use, because there's nothing to confirm or
write back. This only surfaces an observation from data already on file;
Todd decides manually whether to re-tier a contact or reach out. No
baseline_index.json field is ever touched by this script.

Usage:
    python3 relationship_reactivation_scan.py            # scan + write state, text report
    python3 relationship_reactivation_scan.py --json      # machine-readable
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402

STATE_PATH = core.CACHE_DIR / "relationship_reactivation_state.json"
RESULT_PATH = core.CACHE_DIR / "relationship_reactivation_scan.json"
INTERACTION_STATE_PATH = core.CACHE_DIR / "interaction_current_state.json"
RECENT_INTERACTION_DAYS = 90

# Already served by the existing relationship-side artifact suite
# (Inner Circle, Referral Network) -- not "dormant capacity". dormant_valuable
# and untiered (None) are both in scope: exactly what this feature targets.
EXCLUDED_TIERS = {"inner", "broader"}

MATCH_RANK = {"priority_account": 0, "ecosystem_vendor": 1, "ecosystem_brand": 2}


def _priority_account_index(registry: dict) -> dict[str, str]:
    """normalized alias/account_id -> account_id, from the tracked
    customers_prospects accounts. Uses ei._norm_key() for the same
    normalization the ecosystem resolver uses, so a contact's
    current_company is compared consistently regardless of which index
    resolves it."""
    index: dict[str, str] = {}
    for entry in registry.get("registry", []) or []:
        account_id = entry.get("account_id")
        if not account_id:
            continue
        for alias in entry.get("aliases", []) or []:
            key = ei._norm_key(alias)
            if key:
                index.setdefault(key, account_id)
        fallback_key = ei._norm_key(account_id.replace("acct-", "", 1).replace("-", " "))
        if fallback_key:
            index.setdefault(fallback_key, account_id)
    return index


_SLUG_SHAPED_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _account_display_name(registry: dict, account_id: str) -> str:
    """Real registry aliases lists mix a bare slug (e.g. "churchs-chicken")
    with the proper display name (e.g. "Church's Texas Chicken") in no
    fixed order -- confirmed live, several real accounts have the slug
    first. Prefer the first alias that doesn't look like a bare slug."""
    for entry in registry.get("registry", []) or []:
        if entry.get("account_id") == account_id:
            aliases = entry.get("aliases") or []
            for alias in aliases:
                if alias and not _SLUG_SHAPED_RE.match(alias):
                    return alias
            if aliases:
                return aliases[0]
    return account_id.replace("acct-", "", 1).replace("-", " ").title()


def _entity_index(graph: dict) -> dict[str, dict]:
    return {e["id"]: e for e in graph.get("entities") or [] if e.get("id")}


def _classify_contact(
    contact: dict, priority_index: dict[str, str], entities_by_id: dict[str, dict], graph: dict,
) -> dict | None:
    if (contact.get("rc_tier") or None) in EXCLUDED_TIERS:
        return None
    company = (contact.get("current_company") or "").strip()
    if not company:
        return None

    norm_company = ei._norm_key(company)
    account_id = priority_index.get(norm_company) if norm_company else None
    if account_id:
        return {
            "match_type": "priority_account",
            "matched_id": account_id,
        }

    entity_id = ei._resolve_entity_id_any_type(company, graph)
    if not entity_id:
        return None
    entity = entities_by_id.get(entity_id)
    if not entity:
        return None
    entity_type = entity.get("entity_type")
    if entity_type == "vendor":
        match_type = "ecosystem_vendor"
    elif entity_type == "brand":
        match_type = "ecosystem_brand"
    else:
        return None
    return {"match_type": match_type, "matched_id": entity_id}


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


def _recent_interaction_index(today: str) -> dict[str, dict]:
    """Contacts with observed cross-channel activity recent enough that a
    generic reactivation prompt would be wrong or redundant."""
    if not INTERACTION_STATE_PATH.exists():
        return {}
    try:
        payload = json.loads(INTERACTION_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    today_d = datetime.fromisoformat(today).date()
    out = {}
    for row in payload.get("states") or []:
        raw = str(row.get("last_interaction_at") or "")[:10]
        try:
            age = (today_d - datetime.fromisoformat(raw).date()).days
        except ValueError:
            continue
        if 0 <= age <= RECENT_INTERACTION_DAYS and row.get("contact_id"):
            out[row["contact_id"]] = {**row, "age_days": age}
    return out


def load_last_result() -> dict | None:
    """For render_daily_brief.py -- reads the last scan's persisted result.
    Rendering never re-runs the scan itself (a scheduled morning_pipeline.py
    step does that); this is a pure read."""
    if not RESULT_PATH.exists():
        return None
    try:
        return json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def run_scan() -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = now[:10]

    baseline = core.load_baseline(core.BASELINE_PATH)
    graph = ei._read_graph()
    registry = cpc.load_registry()

    priority_index = _priority_account_index(registry)
    entities_by_id = _entity_index(graph)

    state = _load_state()
    recent_interactions = _recent_interaction_index(today)
    new_state: dict = {}
    new_findings: list[dict] = []
    persisting_findings: list[dict] = []
    suppressed_recent_interaction: list[dict] = []

    for contact in baseline:
        contact_id = contact.get("id")
        if not contact_id:
            continue
        classification = _classify_contact(contact, priority_index, entities_by_id, graph)
        if not classification:
            continue

        match_type = classification["match_type"]
        matched_id = classification["matched_id"]
        if match_type == "priority_account":
            matched_name = _account_display_name(registry, matched_id)
        else:
            matched_name = entities_by_id.get(matched_id, {}).get("name") or matched_id

        prior = state.get(contact_id)
        is_new_or_changed = not prior or prior.get("match_type") != match_type
        first_seen = today if is_new_or_changed or not prior else prior.get("first_seen", today)

        new_state[contact_id] = {
            "match_type": match_type,
            "matched_id": matched_id,
            "first_seen": first_seen,
            "last_seen": today,
        }

        finding = {
            "contact_id": contact_id,
            "name": contact.get("name"),
            "current_company": contact.get("current_company"),
            "current_role": contact.get("current_role"),
            "last_touch": contact.get("last_touch"),
            "match_type": match_type,
            "matched_id": matched_id,
            "matched_name": matched_name,
            "first_seen": first_seen,
        }
        recent = recent_interactions.get(contact_id)
        if recent:
            suppressed_recent_interaction.append({
                **finding,
                "last_interaction_at": recent.get("last_interaction_at"),
                "last_channel": recent.get("last_channel"),
                "response_state": recent.get("state"),
                "suppression_reason": (
                    f"Observed {recent.get('last_channel')} interaction {recent.get('age_days')} day(s) ago; "
                    "generic reactivation would be redundant."
                ),
            })
            continue
        if is_new_or_changed:
            new_findings.append(finding)
        else:
            persisting_findings.append(finding)

    new_findings.sort(key=lambda f: MATCH_RANK.get(f["match_type"], 99))
    persisting_findings.sort(key=lambda f: MATCH_RANK.get(f["match_type"], 99))

    _save_state(new_state)

    result = {
        "generated_at": now,
        "contacts_scanned": len(baseline),
        "new_findings": new_findings,
        "persisting_findings": persisting_findings,
        "suppressed_recent_interaction": suppressed_recent_interaction,
    }
    _save_result(result)
    return result


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    result = run_scan()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"relationship_reactivation_scan: {result['contacts_scanned']} contacts scanned")
        print(f"  new/changed: {len(result['new_findings'])}")
        for f in result["new_findings"]:
            print(f"    {f['name']} ({f['current_company']}) -> {f['match_type']}: {f['matched_name']}")
        print(f"  persisting: {len(result['persisting_findings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
