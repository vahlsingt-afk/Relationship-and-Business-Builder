#!/usr/bin/env python3
"""
strategic_operators.py — persistent Strategic Operator Intelligence overlay.

Reads the canonical entity lane (`system/strategic_operators.yaml`), the
ephemeral news/source lane (`system/inbox/market_signals.json`), active
threads, and baseline. Produces a CoS-ready overlay with recent movements,
relationship proximity, categorical rubric buckets, and reconciliation prompts.

This module is deliberately separate from market_signals.py. Market signals can
feed operator movements, but strategic operators are persistent entities.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

MARKET_SIGNALS_PATH = core.INBOX_DIR / "market_signals.json"
LEVELS = ("low", "medium", "high", "critical")
LEVEL_RANK = {level: i for i, level in enumerate(LEVELS)}


def _today() -> date:
    return date.today()


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _contains_any(haystack: str, needles: list[str]) -> bool:
    low = haystack.lower()
    return any(n and n.lower() in low for n in needles)


def _read_market_raw(path: Path = MARKET_SIGNALS_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _operator_aliases(op: dict) -> list[str]:
    aliases = [op.get("name") or "", op.get("id") or ""]
    aliases.extend(op.get("brands_in_portfolio") or [])
    aliases.extend(op.get("companies_owned") or [])
    return [str(a).strip() for a in aliases if str(a).strip()]


def market_items_for_operator(op: dict, market_raw: dict) -> list[dict]:
    """Return source-backed market rows that mention the operator or portfolio.

    This is a feeder bridge only. It does not mutate the operator record; a
    future operator-record-movement mutation should persist material rows.
    """
    aliases = _operator_aliases(op)
    out = []
    for item in market_raw.get("items") or []:
        text = " ".join(
            str(item.get(k) or "")
            for k in (
                "title", "company", "pain_point_or_priority",
                "restaurant_operator_impact", "relationship_opportunity",
                "why_this_matters_to_todd",
            )
        )
        if _contains_any(text, aliases):
            out.append(item)
    return out


def _baseline_by_id(baseline: list[dict]) -> dict[str, dict]:
    return {str(e.get("id")): e for e in baseline if e.get("id")}


def _is_relationship_class(entry: dict) -> bool:
    return str(entry.get("signal_class") or "") in {"RC", "LKI", "LMI"}


def resolve_relationship_proximity(op: dict, baseline: list[dict], threads: list[dict]) -> dict:
    """Resolve relationship proximity using deterministic, conservative rules."""
    by_id = _baseline_by_id(baseline)
    evidence: list[str] = []
    mutual_connections: list[str] = []

    for eid in op.get("executives") or []:
        entry = by_id.get(str(eid))
        if entry and _is_relationship_class(entry):
            mutual_connections.append(str(eid))
            evidence.append(f"executive:{eid}:{entry.get('signal_class')}")

    if mutual_connections:
        return {
            "resolved": "direct",
            "evidence": evidence,
            "mutual_connections": mutual_connections,
        }

    brand_slugs = {_slug(str(b)) for b in (op.get("brands_in_portfolio") or [])}
    company_texts = [str(c) for c in (op.get("companies_owned") or [])]
    for entry in baseline:
        if not _is_relationship_class(entry):
            continue
        company = str(entry.get("current_company") or "")
        company_slug = _slug(company)
        if company_slug and company_slug in brand_slugs:
            mutual_connections.append(str(entry.get("id")))
            evidence.append(f"portfolio_company_match:{entry.get('id')}:{company}")
        elif company and _contains_any(company, company_texts):
            mutual_connections.append(str(entry.get("id")))
            evidence.append(f"company_text_match:{entry.get('id')}:{company}")
        if len(mutual_connections) >= 6:
            break

    if mutual_connections:
        return {
            "resolved": "one_hop",
            "evidence": evidence,
            "mutual_connections": mutual_connections,
        }

    aliases = _operator_aliases(op)
    matched_threads = []
    for thread in threads:
        text = " ".join(
            str(thread.get(k) or "")
            for k in ("title", "context", "current_state", "type")
        )
        companies = " ".join(str(c) for c in (thread.get("companies") or []))
        if _contains_any(f"{text} {companies}", aliases):
            matched_threads.append(str(thread.get("id")))

    if matched_threads:
        return {
            "resolved": "two_hop",
            "evidence": [f"active_thread:{tid}" for tid in matched_threads[:6]],
            "mutual_connections": [],
        }

    return {"resolved": "none", "evidence": [], "mutual_connections": []}


def recent_movements(op: dict, *, today: date, days: int = 90) -> list[dict]:
    cutoff = today - timedelta(days=days)
    out = []
    for movement in op.get("movements") or []:
        event_at = _parse_date(movement.get("event_at"))
        if event_at and event_at >= cutoff:
            out.append(movement)
    return sorted(out, key=lambda m: str(m.get("event_at") or ""), reverse=True)


def _max_level(values: list[str]) -> str:
    cleaned = [v for v in values if v in LEVEL_RANK]
    if not cleaned:
        return "low"
    return max(cleaned, key=lambda v: LEVEL_RANK[v])


def _bump(level: str, target: str) -> str:
    return target if LEVEL_RANK[target] > LEVEL_RANK[level] else level


def score_operator(op: dict, proximity: dict, movements: list[dict], feeder_items: list[dict]) -> dict:
    """Categorical rubric, grounded in observable entity/movement conditions."""
    entity_type = op.get("entity_type")
    brand_count = len(op.get("brands_in_portfolio") or [])
    movement_types = {m.get("movement_type") for m in movements}

    influence = "low"
    if entity_type in {"regional_scale", "franchisee_group"} or brand_count >= 1:
        influence = "medium"
    if entity_type in {"multi_brand_operator", "pe_backed", "holding_co", "consolidator"} or brand_count >= 3:
        influence = "high"
    notes = str(op.get("notes") or "").lower()
    if "largest" in notes or "2,400" in notes or "1000" in notes or "1,000" in notes:
        influence = "critical"

    relationship_value = {
        "direct": "critical",
        "one_hop": "high",
        "two_hop": "medium",
        "unknown": "medium",
        "none": "low",
    }.get(proximity.get("resolved"), "low")
    if feeder_items and relationship_value == "low":
        relationship_value = "medium"

    pressure = "low"
    if feeder_items or movements:
        pressure = "medium"
    if movement_types & {
        "acquisition", "restructure", "franchise_transfer", "geographic_expansion",
        "concept_expansion", "technology_standardization", "vendor_transition",
        "integration_signal", "reporting_visibility_signal",
    }:
        pressure = "high"
    if movement_types & {"bankruptcy", "divestiture"}:
        pressure = "critical"

    ecosystem = "low"
    if entity_type in {"regional_scale", "franchisee_group"}:
        ecosystem = "medium"
    if entity_type in {"multi_brand_operator", "pe_backed", "holding_co", "consolidator"}:
        ecosystem = "high"
    if influence == "critical":
        ecosystem = "critical"

    opportunity = "low"
    if feeder_items or proximity.get("resolved") in {"two_hop", "unknown"}:
        opportunity = "medium"
    if proximity.get("resolved") in {"direct", "one_hop"} or pressure in {"high", "critical"}:
        opportunity = "high"
    if proximity.get("resolved") == "direct" and pressure in {"high", "critical"}:
        opportunity = "critical"

    follow_up = _max_level([relationship_value, opportunity])
    if pressure == "critical":
        follow_up = _bump(follow_up, "high")
    if follow_up == "critical":
        disposition = "act_today"
    elif follow_up == "high":
        disposition = "act_today"
    elif follow_up == "medium":
        disposition = "monitor"
    else:
        disposition = "ignore"

    return {
        "influence": influence,
        "relationship_value": relationship_value,
        "operational_pressure": pressure,
        "ecosystem_impact": ecosystem,
        "future_opportunity": opportunity,
        "follow_up_priority": follow_up,
        "recommended_disposition": disposition,
    }


def _reconciliation_prompt(op: dict, proximity: dict) -> dict | None:
    asserted = op.get("relationship_proximity") or "unknown"
    resolved = proximity.get("resolved") or "unknown"
    if asserted == resolved:
        return None
    if asserted == "unknown" and resolved == "none":
        return None
    return {
        "operator_id": op.get("id"),
        "prompt": (
            f"{op.get('name')} has asserted proximity `{asserted}` but the "
            f"overlay resolved `{resolved}`. Confirm the right relationship path?"
        ),
        "asserted": asserted,
        "resolved": resolved,
        "evidence": proximity.get("evidence") or [],
    }


def build_overlay(*, today: date | None = None, recent_days: int = 90,
                  operators: list[dict] | None = None,
                  market_raw: dict | None = None,
                  baseline: list[dict] | None = None,
                  threads: list[dict] | None = None) -> dict:
    today = today or _today()
    operators = operators if operators is not None else core.load_strategic_operators()
    market_raw = market_raw if market_raw is not None else _read_market_raw()
    baseline = baseline if baseline is not None else core.load_baseline()
    threads = threads if threads is not None else core.load_active_threads()

    rows = []
    prompts = []
    for op in operators:
        movements = recent_movements(op, today=today, days=recent_days)
        feeder_items = market_items_for_operator(op, market_raw)
        proximity = resolve_relationship_proximity(op, baseline, threads)
        scores = score_operator(op, proximity, movements, feeder_items)
        prompt = _reconciliation_prompt(op, proximity)
        if prompt:
            prompts.append(prompt)
        rows.append({
            "id": op.get("id"),
            "name": op.get("name"),
            "entity_type": op.get("entity_type"),
            "watchlist_bucket": op.get("watchlist_bucket"),
            "status": op.get("status"),
            "opened": op.get("opened"),
            "last_movement_at": op.get("last_movement_at"),
            "companies_owned": op.get("companies_owned") or [],
            "brands_in_portfolio": op.get("brands_in_portfolio") or [],
            "relationship_proximity": {
                "asserted": op.get("relationship_proximity") or "unknown",
                "resolved": proximity["resolved"],
                "evidence": proximity.get("evidence") or [],
            },
            "mutual_connections": proximity.get("mutual_connections") or [],
            "vendor_relationships": op.get("vendor_relationships") or [],
            "recent_movements": movements,
            "market_signal_matches": feeder_items,
            "scores": scores,
            "recommended_action": _recommended_action(op, scores, proximity, movements, feeder_items),
            "source_refs": [
                "strategic_operators.yaml",
                *[
                    f"market_signals.url:{item.get('url')}"
                    for item in feeder_items[:3]
                    if item.get("url")
                ],
            ],
        })

    ranked = sorted(
        rows,
        key=lambda r: (
            -LEVEL_RANK.get((r.get("scores") or {}).get("follow_up_priority"), 0),
            -LEVEL_RANK.get((r.get("scores") or {}).get("influence"), 0),
            str(r.get("name") or ""),
        ),
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "recent_days": recent_days,
        "operator_count": len(operators),
        "active_count": sum(1 for op in operators if op.get("status") == "active"),
        "recent_movement_count": sum(len(r.get("recent_movements") or []) for r in rows),
        "market_signal_match_count": sum(len(r.get("market_signal_matches") or []) for r in rows),
        "top": ranked,
        "reconciliation_prompts": prompts,
        "rubric": {
            "path": "system/scoring/strategic_operator_rubric.md",
            "levels": list(LEVELS),
            "primary_state": "categorical",
        },
    }


def _recommended_action(op: dict, scores: dict, proximity: dict,
                        movements: list[dict], feeder_items: list[dict]) -> str:
    priority = scores.get("follow_up_priority")
    if priority in {"critical", "high"}:
        return (
            "Map relationship path and create a monitored operator loop; "
            "watch leadership, vendor, hiring, and integration signals."
        )
    if movements or feeder_items:
        return "Monitor; persist material movements and revisit if another signal appears."
    if proximity.get("resolved") == "none":
        return "Keep on watchlist; no known relationship path yet."
    return "Monitor relationship proximity and refresh when new movements appear."


def render(report: dict, *, limit: int = 10) -> str:
    lines = [
        "Strategic operators overlay",
        f"operators={report.get('operator_count')} active={report.get('active_count')} "
        f"recent_movements={report.get('recent_movement_count')} "
        f"market_matches={report.get('market_signal_match_count')}",
        "",
    ]
    for row in (report.get("top") or [])[:limit]:
        scores = row.get("scores") or {}
        prox = row.get("relationship_proximity") or {}
        lines.append(
            f"- [{scores.get('follow_up_priority')}] {row.get('name')} "
            f"({row.get('entity_type')}; {row.get('watchlist_bucket')})"
        )
        lines.append(
            f"  proximity={prox.get('resolved')} "
            f"influence={scores.get('influence')} pressure={scores.get('operational_pressure')} "
            f"opportunity={scores.get('future_opportunity')}"
        )
        if row.get("recent_movements"):
            m = row["recent_movements"][0]
            lines.append(f"  latest movement: {m.get('event_at')} {m.get('movement_type')} — {m.get('summary')}")
        if row.get("recommended_action"):
            lines.append(f"  action: {row['recommended_action']}")
    if report.get("reconciliation_prompts"):
        lines.append("")
        lines.append("Reconciliation:")
        for p in report["reconciliation_prompts"][:5]:
            lines.append(f"- {p['prompt']}")
    return "\n".join(lines) + "\n"


def _smoke() -> int:
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK" if cond else "FAIL"
        print(f"  {mark}   {msg}")
        if not cond:
            failures.append(msg)

    fixture_ops = [
        {
            "id": "ghai-management",
            "name": "Ghai Management",
            "entity_type": "multi_brand_operator",
            "watchlist_bucket": "multi_brand_operator",
            "status": "active",
            "opened": "2026-05-01",
            "companies_owned": ["Taco Bell franchisee"],
            "brands_in_portfolio": ["taco-bell", "burger-king"],
            "executives": ["taco-bell-operator"],
            "vendor_relationships": [],
            "relationship_proximity": "none",
            "movements": [
                {
                    "id": "M-2026-05-20-acquired-44-taco-bell",
                    "event_at": "2026-05-20",
                    "movement_type": "acquisition",
                    "summary": "Acquired 44 Taco Bell locations",
                    "source": "fixture",
                    "confidence": "high",
                    "inferences": {"standardization_pressure": "high"},
                }
            ],
        },
        {
            "id": "quiet-local-operator",
            "name": "Quiet Local Operator",
            "entity_type": "regional_scale",
            "watchlist_bucket": "strategic_operator",
            "status": "active",
            "opened": "2026-05-01",
            "companies_owned": [],
            "brands_in_portfolio": [],
            "executives": [],
            "vendor_relationships": [],
            "relationship_proximity": "none",
            "movements": [],
        },
    ]
    fixture_baseline = [
        {
            "id": "taco-bell-operator",
            "name": "Taylor Operator",
            "current_company": "Taco Bell",
            "signal_class": "RC",
        }
    ]
    fixture_threads = []
    fixture_market = {
        "items": [
            {
                "title": "Ghai Management acquired 44 Taco Bell locations",
                "company": "Ghai Management",
                "url": "https://example.com/ghai",
                "source_name": "fixture",
            }
        ]
    }
    report = build_overlay(
        today=date(2026, 5, 22),
        operators=fixture_ops,
        market_raw=fixture_market,
        baseline=fixture_baseline,
        threads=fixture_threads,
    )
    top = report["top"]
    ghai = next(r for r in top if r["id"] == "ghai-management")
    quiet = next(r for r in top if r["id"] == "quiet-local-operator")

    ck(report["operator_count"] == 2, "operator_count from fixture")
    ck(report["recent_movement_count"] == 1, "recent movements counted")
    ck(report["market_signal_match_count"] == 1, "market feeder match counted")
    ck(ghai["relationship_proximity"]["resolved"] == "direct", "executive RC gives direct proximity")
    ck(ghai["scores"]["operational_pressure"] == "high", "acquisition gives high operational pressure")
    ck(ghai["scores"]["influence"] == "high", "multi-brand operator gives high influence")
    ck(ghai["scores"]["follow_up_priority"] in {"high", "critical"}, "Ghai ranks as high-priority follow-up")
    ck(quiet["scores"]["follow_up_priority"] == "low", "quiet operator stays low priority")
    ck(report["reconciliation_prompts"], "asserted/resolved proximity mismatch creates prompt")
    ck("Strategic operators overlay" in render(report), "renderer emits heading")

    print(f"--- strategic_operators smoke complete: {len(failures)} failure(s) ---")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Strategic Operator Intelligence overlay.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument("--cache", action="store_true", help="Write system/.cache/strategic_operators.json.")
    parser.add_argument("--recent-days", type=int, default=90, help="Movement recency window.")
    parser.add_argument("--smoke", action="store_true", help="Run in-memory smoke test.")
    args = parser.parse_args()

    if args.smoke:
        return _smoke()

    report = build_overlay(recent_days=args.recent_days)
    if args.cache:
        core.write_cache("strategic_operators", report, source="strategic_operators.py")
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
