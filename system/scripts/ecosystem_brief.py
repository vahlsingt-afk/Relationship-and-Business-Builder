#!/usr/bin/env python3
"""
ecosystem_brief.py — Daily brief ecosystem intelligence section builder.

This module is called during daily brief generation. It:
1. Runs a staleness check on graph relationships.
2. Scans market_signals.json and linkedin.daily_signals.jsonl for entity matches.
3. Classifies matched signals into activation candidates, watch list hits, and ambient findings.
4. Writes new graph signal/assessment records for high-confidence matches.
5. Runs the interrupt queue check.
6. Returns a structured ecosystem_intelligence dict for inclusion in the canonical brief.

Privacy rule: no raw signal text is stored in the graph. Only data-only summaries and entity
references are persisted per P-038.

Signal classification taxonomy:
    leadership_change      — act_today, interrupt_eligible on tier_1
    rfp_cycle_signal       — act_today, interrupt_eligible on tier_1
    extreme_pain           — act_today, interrupt_eligible on tier_1
    vendor_displacement    — act_today, interrupt_eligible on tier_1
    funding_event          — monitor, not interrupt_eligible
    expansion_signal       — monitor (ambient only)
    general_market_context — ignore (ambient only, lowest priority)
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import passive_intelligence as pi  # noqa: E402
import ecosystem_intelligence as eco  # noqa: E402

# Signal class → (recommended_action, interrupt_eligible, ambient_eligible)
SIGNAL_CLASS_RULES: dict[str, tuple[str, bool, bool]] = {
    "leadership_change":   ("act_today", True,  False),
    "rfp_cycle_signal":    ("act_today", True,  False),
    "extreme_pain":        ("act_today", True,  False),
    "vendor_displacement": ("act_today", True,  False),
    "funding_event":       ("monitor",   False, True),
    "expansion_signal":    ("monitor",   False, True),
    "general_market_context": ("ignore", False, True),
}

# RB-2026-08-23: single source of truth for "material" (worth surfacing as an
# action, not just a company-profile record). Previously duplicated inline
# here and independently re-derived as a second copy in cockpit_context.py's
# _MATERIAL_SIGNAL_TYPES -- both now import this.
MATERIAL_SIGNAL_CLASSES: frozenset[str] = frozenset({
    "leadership_change", "rfp_cycle_signal", "extreme_pain",
    "vendor_displacement", "vendor_relationship_formed", "funding_event",
})


def _is_material_signal(confidence: str, sig_class: str) -> bool:
    return confidence in {"high", "medium"} and sig_class in MATERIAL_SIGNAL_CLASSES

# Keywords used to classify incoming signals into signal classes.
SIGNAL_CLASS_KEYWORDS: dict[str, list[str]] = {
    "leadership_change": [
        "cto", "ceo", "cio", "coo", "vp tech", "vp of technology", "chief technology",
        "chief information", "chief digital", "new hire", "promoted to", "joins as",
        "named as", "appointed", "steps down", "departs", "leadership change",
        "new president", "new executive",
    ],
    "rfp_cycle_signal": [
        "rfp", "request for proposal", "vendor evaluation", "evaluating vendors",
        "contract renewal", "re-evaluating", "platform decision", "technology review",
        "replacing", "new pos", "new platform", "migration",
    ],
    "extreme_pain": [
        "outage", "system failure", "pos failure", "down", "complaints", "angry",
        "operational issue", "disruption", "chaos", "crisis", "lawsuit", "recall",
        "franchisor complaint", "franchisee revolt",
    ],
    "vendor_displacement": [
        "switching from", "replacing", "migrated to", "dropped", "moved to",
        "transitioning away", "no longer uses", "new vendor", "competitive win",
        "displaced", "ripped and replaced",
    ],
    # RB-2026-08-31: real, confirmed gap -- vendor_displacement above only
    # catches a brand LEAVING a vendor ("switching from", "migrated to");
    # nothing caught a brand ADOPTING one. tech_stack_relationship_promotion
    # .py scans signals classified here to propose review-first candidate
    # uses_vendor_for_category relationships -- see that module for why this
    # matters (ecosystem_intelligence.json's real tech-stack coverage is
    # thin, ~17% of tracked brands, and this scan already runs weekly
    # across the full ~1,654-brand universe via technomic_watchlist_scan.py,
    # so strengthening detection here compounds over time rather than being
    # a one-time backfill). Ordered after vendor_displacement/rfp_cycle_signal
    # so genuinely evaluation-stage or departure language keeps matching
    # those first; this only fires on completed-adoption phrasing.
    "vendor_relationship_formed": [
        "selects", "selected", "chooses", "chose", "has chosen",
        "deploys", "deployed", "rolls out", "rolled out", "rolling out",
        "signs agreement with", "signs multi-year agreement", "signed a deal with",
        "partners with", "expands its relationship with", "expands relationship with",
        "adopts", "implements", "goes live with", "launches with",
        "to power", "selects as its", "names as its",
    ],
    "funding_event": [
        "raised", "series a", "series b", "series c", "funding round", "ipo",
        "acquisition", "acquired by", "merger", "capital raise", "investment",
    ],
    "expansion_signal": [
        "opening locations", "new markets", "international expansion", "franchise growth",
        "unit growth", "new stores", "expanding to",
    ],
}

STICKY_TECH_CATEGORIES = {"pos", "payments", "back_office", "erp", "back_office_accounting"}

INTERRUPT_QUEUE_PATH = core.SYSTEM_DIR / "inbox" / "ecosystem" / "interrupt_queue.jsonl"


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "unknown"


def _load_graph() -> dict:
    path = core.ECOSYSTEM_INTELLIGENCE_PATH
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_graph(graph: dict) -> None:
    core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(
        json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _entity_index(graph: dict) -> dict[str, dict]:
    """name slug → entity record. Also indexes aliases."""
    idx: dict[str, dict] = {}
    for e in graph.get("entities", []):
        idx[e["id"]] = e
        idx[_slug(e.get("name", ""))] = e
        for alias in e.get("aliases") or []:
            idx[_slug(alias)] = e
        if e.get("ticker"):
            idx[_slug(e["ticker"])] = e
    return idx


def _classify_signal(title: str, summary: str) -> str:
    """Return the best-matching signal class for a raw signal record."""
    text = f"{title} {summary}".lower()
    for cls, keywords in SIGNAL_CLASS_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return cls
    return "general_market_context"


def _load_market_signals() -> list[dict]:
    path = core.SYSTEM_DIR / "inbox" / "market_signals.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("items") or []
    except Exception:  # noqa: BLE001
        return []


def _load_linkedin_signals() -> list[dict]:
    path = core.SYSTEM_DIR / "inbox" / "linkedin.daily_signals.jsonl"
    if not path.exists():
        return []
    records = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass
    return records


def _existing_signal_ids(graph: dict) -> set[str]:
    return {s["id"] for s in graph.get("signals", [])}


def _make_signal_id(event_at: str, entity_id: str, signal_type: str) -> str:
    tag = _slug(f"{entity_id}-{signal_type}")[:60]
    return f"sig-{event_at}-{tag}"


def _match_signals_to_entities(
    raw_signals: list[dict],
    entity_idx: dict[str, dict],
    today: date,
    graph: dict | None = None,
) -> list[dict]:
    """Match raw signal records to graph entities. Returns enriched match records."""
    matched = []
    for sig in raw_signals:
        company = sig.get("company") or sig.get("title") or ""
        company_slug = _slug(company.split("'")[0].split(" ")[0]) if company else ""
        # Try direct slug match, then partial scan.
        entity = entity_idx.get(company_slug)
        if not entity:
            for slug_key, ent in entity_idx.items():
                if len(slug_key) > 3 and slug_key in _slug(company):
                    entity = ent
                    break
        if not entity:
            continue

        title = sig.get("title", "")
        body = sig.get("restaurant_operator_impact") or sig.get("summary") or sig.get("pain_point_or_priority") or ""
        signal_type = sig.get("signal_type") or _classify_signal(title, body)
        # Re-classify using our taxonomy.
        signal_class = _classify_signal(title, f"{body} {signal_type}")

        event_at = sig.get("published_at") or str(today)
        confidence_str = sig.get("confidence") or "medium"
        if confidence_str not in {"low", "medium", "high", "critical"}:
            confidence_str = "medium"

        passive_eval = pi.evaluate_passive_intelligence(
            f"{title}. {body}".strip(". "),
            {
                "source_type": sig.get("source_type"),
                "platform": sig.get("platform") or "daily_signal_inbox",
                "title": sig.get("source_name") or title,
                "url": sig.get("url"),
            },
            graph=graph or {"sources": [], "signals": [], "relationships": []},
            today=today,
        )
        intelligence_metadata = pi.signal_metadata_from_evaluation(passive_eval)

        matched.append({
            "entity": entity,
            "entity_id": entity["id"],
            "signal_class": signal_class,
            "title": title,
            "summary": f"{title}. {body}".strip(". ")[:300],
            "event_at": event_at,
            "confidence": confidence_str,
            "source_type": sig.get("source_type") or "vertical_trade",
            "source_name": sig.get("source_name") or "market_signals_inbox",
            "intelligence_evaluation": passive_eval,
            "intelligence_metadata": intelligence_metadata,
            "recommended_action": SIGNAL_CLASS_RULES.get(signal_class, ("monitor", False, True))[0],
            "interrupt_eligible": SIGNAL_CLASS_RULES.get(signal_class, ("monitor", False, True))[1],
            "ambient_eligible": SIGNAL_CLASS_RULES.get(signal_class, ("monitor", False, True))[2],
        })
    return matched


def _run_staleness_check(graph: dict, today: date) -> list[dict]:
    """Flag stale relationships in-memory. Returns list of stale records."""
    stale = []
    for rel in graph.get("relationships", []):
        posture = rel.get("evidence_posture", "unknown")
        category = rel.get("category") or ""
        conf = rel.get("confidence") or {}
        review_after_str = conf.get("review_after")

        if posture == "substantiated" and category in STICKY_TECH_CATEGORIES:
            rel["staleness_flag"] = False
            continue
        if not review_after_str:
            rel["staleness_flag"] = False
            continue
        try:
            review_after = date.fromisoformat(review_after_str)
        except ValueError:
            continue
        # Explicit clear as well as set — see ecosystem_intelligence.py's
        # _check_staleness_on_graph for why this matters (research-request
        # auto-resolve depends on staleness_flag reflecting current state).
        is_overdue = review_after < today and posture != "substantiated"
        rel["staleness_flag"] = is_overdue
        if is_overdue:
            stale.append({
                "relationship_id": rel["id"],
                "from_entity_id": rel.get("from_entity_id"),
                "to_entity_id": rel.get("to_entity_id"),
                "category": category,
                "evidence_posture": posture,
                "review_after": review_after_str,
                "days_overdue": (today - review_after).days,
            })
    return stale


def _write_signal_to_graph(graph: dict, match: dict, today: date) -> str:
    """Write a new signal record to the graph. Returns the signal ID."""
    event_at = match["event_at"]
    sig_id = _make_signal_id(event_at, match["entity_id"], match["signal_class"])
    # Idempotent — skip if already exists.
    if any(s["id"] == sig_id for s in graph.get("signals", [])):
        return sig_id

    src_id = f"src-{_slug(match['source_name'])}-{_slug(event_at)}"
    if not any(s["id"] == src_id for s in graph.get("sources", [])):
        graph.setdefault("sources", []).append({
            "id": src_id,
            "source_type": match["source_type"],
            "title": match["source_name"],
            "url": None,
            "path": None,
            "published_at": event_at,
            "captured_at": _now(),
            "quality": "medium",
            "notes": "Signal matched via daily brief ecosystem scan.",
        })

    intelligence_metadata = match.get("intelligence_metadata") or {}
    graph.setdefault("signals", []).append({
        "id": sig_id,
        "event_at": event_at,
        "captured_at": _now(),
        "signal_type": match["signal_class"],
        "summary": match["summary"],
        "domains": ["restaurants"],
        "entities": [match["entity_id"]],
        "sources": [src_id],
        "confidence": {"level": match["confidence"]},
        "interpretation": f"Auto-classified from daily brief ecosystem scan. Class: {match['signal_class']}. Recommended action: {match['recommended_action']}.",
        "source_type": intelligence_metadata.get("source_type") or match["source_type"],
        "source_quality": intelligence_metadata.get("source_quality") or "unknown",
        "confidence_score": intelligence_metadata.get("confidence_score"),
        "corroboration_count": intelligence_metadata.get("corroboration_count", 0),
        "corroboration_sources": intelligence_metadata.get("corroboration_sources", []),
        "claim_status": intelligence_metadata.get("claim_status") or "insufficient_evidence",
        "verification_timestamp": intelligence_metadata.get("verification_timestamp") or str(today),
        "narrative_classification": intelligence_metadata.get("narrative_classification") or "strategic_weak_signal",
        "graph_mutation_eligibility": intelligence_metadata.get("graph_mutation_eligibility") or "not_eligible",
        "strategic_relevance_score": intelligence_metadata.get("strategic_relevance_score"),
    })
    return sig_id


def _write_activation_assessment(graph: dict, match: dict, signal_id: str, today: date) -> str:
    """Write an activation_candidate assessment for qualifying signals."""
    asm_id = f"asm-{_slug(match['event_at'])}-{_slug(match['entity_id'])}-{_slug(match['signal_class'])}"
    if any(a["id"] == asm_id for a in graph.get("assessments", [])):
        return asm_id

    graph.setdefault("assessments", []).append({
        "id": asm_id,
        "entity_id": match["entity_id"],
        "assessment_type": "activation_candidate",
        "summary": (
            f"{match['signal_class'].replace('_', ' ').title()} signal detected at "
            f"{match['entity'].get('name', match['entity_id'])}. "
            f"{match['summary'][:200]}. "
            f"Recommended action: {match['recommended_action']}."
        ),
        "risk": "yellow",
        "confidence": {"level": match["confidence"]},
        "sources": [signal_id],
        "updated_at": _now(),
    })
    return asm_id


def _run_interrupt_check(graph: dict, today: date) -> list[dict]:
    """Write qualifying new signals to interrupt_queue.jsonl. Returns queued items."""
    wl = graph.get("watch_list") or []
    tier1_ids = {e["entity_id"] for e in wl if e.get("priority") == "tier_1"}
    if not tier1_ids:
        return []

    INTERRUPT_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing_ids: set[str] = set()
    if INTERRUPT_QUEUE_PATH.exists():
        for line in INTERRUPT_QUEUE_PATH.read_text().splitlines():
            try:
                existing_ids.add(json.loads(line)["signal_id"])
            except Exception:  # noqa: BLE001
                pass

    interrupt_classes = {"leadership_change", "rfp_cycle_signal", "extreme_pain", "vendor_displacement"}
    queued = []
    for sig in graph.get("signals", []):
        sig_id = sig.get("id", "")
        if sig_id in existing_ids:
            continue
        if not any(eid in tier1_ids for eid in (sig.get("entities") or [])):
            continue
        if sig.get("signal_type") not in interrupt_classes:
            continue
        if (sig.get("confidence") or {}).get("level") != "high":
            continue
        try:
            event_date = date.fromisoformat(sig.get("event_at", ""))
        except ValueError:
            continue
        if (today - event_date).days > 2:
            continue

        record = {
            "signal_id": sig_id,
            "entity_ids": sig.get("entities", []),
            "signal_type": sig.get("signal_type"),
            "summary": sig.get("summary", ""),
            "confidence": (sig.get("confidence") or {}).get("level"),
            "event_at": sig.get("event_at"),
            "detected_at": _now(),
            "acknowledged": False,
        }
        queued.append(record)
        with open(INTERRUPT_QUEUE_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")
    return queued


def build_section(today: date | None = None) -> dict:
    """Build the ecosystem_intelligence section for the daily brief.

    Returns a dict with:
        watch_list_signals    — signals touching watch list entities
        staleness_flags       — stale relationships needing verification
        ambient_surface       — 1-2 high-conviction non-watch-list findings
        mutation_log          — graph mutations applied during this cycle
        verification_queue    — relationships queued for verification
        research_requests     — persistent, prioritized research requests derived
                                 from staleness/conflict triggers (open only, top 10)
        needs_todd            — items the CoS cannot resolve alone
        interrupt_queue       — items pushed to interrupt queue this cycle
        source_freshness      — basic freshness report
        error                 — set if a non-fatal error occurred
    """
    if today is None:
        today = date.today()

    out: dict[str, Any] = {
        "watch_list_signals": [],
        "staleness_flags": [],
        "ambient_surface": [],
        "mutation_log": [],
        "verification_queue": [],
        "needs_todd": [],
        "interrupt_queue": [],
        "research_requests": [],
        "source_freshness": {},
    }

    graph = _load_graph()
    if not graph:
        out["error"] = "ecosystem graph unavailable"
        out["source_freshness"] = {"status": "unavailable"}
        return out

    entity_idx = _entity_index(graph)
    wl = graph.get("watch_list") or []
    tier1_ids = {e["entity_id"] for e in wl if e.get("priority") == "tier_1"}
    tier2_ids = {e["entity_id"] for e in wl if e.get("priority") == "tier_2"}
    watch_ids = tier1_ids | tier2_ids

    # 1 — Staleness check.
    stale = _run_staleness_check(graph, today)
    out["staleness_flags"] = stale[:10]

    # 1b — Reconcile persistent research requests from staleness + unresolved
    # conflicts (Phase 2 of the Research Intelligence Engine directive,
    # 2026-07-20). Unlike staleness_flags/verification_queue below, these
    # persist across brief cycles with an open/resolved lifecycle instead of
    # regenerating fresh (and getting silently truncated) every day.
    research_result = eco.update_research_requests(graph)
    out["research_requests"] = sorted(
        research_result["open_requests"],
        key=lambda r: {"high": 2, "medium": 1, "low": 0}.get(r.get("priority"), 0),
        reverse=True,
    )[:10]

    # 2 — Load and match signals.
    market_sigs = _load_market_signals()
    linkedin_sigs = _load_linkedin_signals()
    all_raw = market_sigs + linkedin_sigs

    out["source_freshness"] = {
        "market_signals_count": len(market_sigs),
        "linkedin_signals_count": len(linkedin_sigs),
        "status": "fresh" if (market_sigs or linkedin_sigs) else "empty",
    }

    matched = _match_signals_to_entities(all_raw, entity_idx, today, graph=graph)
    existing_sig_ids = _existing_signal_ids(graph)
    mutation_log = []

    watch_hits: list[dict] = []
    ambient_candidates: list[dict] = []

    for match in matched:
        is_watch = match["entity_id"] in watch_ids
        sig_class = match["signal_class"]
        action, interrupt_elig, ambient_elig = SIGNAL_CLASS_RULES.get(
            sig_class, ("monitor", False, True)
        )

        # RB-DEFECT-2026-08-19: Todd's standing rule for every scanned event:
        # "is this a meaningful signal to the user about the business - if
        # yes, report it - if no, record it in the company profile." Before
        # this, a non-material match (expansion_signal/general_market_context)
        # was neither reported NOR recorded -- should_write required both a
        # material signal_class AND medium+ confidence, so anything below
        # that bar just vanished after this run instead of leaving a trace on
        # the entity's graph record. Split the old single gate in two:
        # record_eligible controls whether ANYTHING gets written to the graph
        # (the company profile) -- broad, only vetoed by the existing
        # eligibility/privacy check -- while is_material (the original,
        # narrower bar) still controls the higher-stakes actions: activation
        # assessments, interrupt-queue eligibility, and mutation-log entries
        # (which is what daily_brief.py surfaces as "reported").
        record_eligible = (
            (match.get("intelligence_metadata") or {}).get("graph_mutation_eligibility") != "not_eligible"
        )
        is_material = _is_material_signal(match["confidence"], sig_class)
        sig_id = None
        if record_eligible:
            sig_id = _write_signal_to_graph(graph, match, today)
            is_new = sig_id not in existing_sig_ids
            if is_new:
                existing_sig_ids.add(sig_id)
                if is_material:
                    # Only material mutations are worth a mutation-log line --
                    # a recorded-but-not-material signal is exactly the quiet
                    # "company profile" case and shouldn't read as an action
                    # someone needs to look at.
                    mutation_log.append({
                        "mutation_type": "signal_added",
                        "entity_id": match["entity_id"],
                        "entity_name": match["entity"].get("name"),
                        "signal_type": sig_class,
                        "signal_id": sig_id,
                        "confidence": match["confidence"],
                        "summary": match["summary"][:150],
                        "claim_status": (match.get("intelligence_metadata") or {}).get("claim_status"),
                        "corroboration_count": (match.get("intelligence_metadata") or {}).get("corroboration_count", 0),
                        "graph_mutation_eligibility": (match.get("intelligence_metadata") or {}).get("graph_mutation_eligibility"),
                    })

            # Write activation assessment for qualifying (material) signals only.
            if is_material and sig_class in {"leadership_change", "rfp_cycle_signal", "extreme_pain", "vendor_displacement"}:
                _write_activation_assessment(graph, match, sig_id, today)

        surface_item = {
            "entity_id": match["entity_id"],
            "entity_name": match["entity"].get("name"),
            "signal_class": sig_class,
            "recommended_action": action,
            "summary": match["summary"][:200],
            "confidence": match["confidence"],
            "confidence_score": (match.get("intelligence_metadata") or {}).get("confidence_score"),
            "claim_status": (match.get("intelligence_metadata") or {}).get("claim_status"),
            "corroboration_count": (match.get("intelligence_metadata") or {}).get("corroboration_count", 0),
            "corroboration_sources": (match.get("intelligence_metadata") or {}).get("corroboration_sources", []),
            "source_quality": (match.get("intelligence_metadata") or {}).get("source_quality"),
            "graph_mutation_eligibility": (match.get("intelligence_metadata") or {}).get("graph_mutation_eligibility"),
            "interrupt_eligible": interrupt_elig and match["entity_id"] in tier1_ids,
        }

        if is_watch:
            watch_hits.append(surface_item)
        elif ambient_elig and len(ambient_candidates) < 3:
            ambient_candidates.append(surface_item)

    out["watch_list_signals"] = watch_hits
    out["mutation_log"] = mutation_log
    # Ambient surface: max 2, highest-conviction only (prefer non-general_market_context).
    ambient_sorted = sorted(
        ambient_candidates,
        key=lambda x: (
            0 if x["signal_class"] == "general_market_context" else 1,
            {"high": 2, "medium": 1, "low": 0}.get(x["confidence"], 0),
        ),
        reverse=True,
    )
    out["ambient_surface"] = ambient_sorted[:2]

    # 3 — Verification queue: top 5 stale records that need Todd or a source check.
    for s in stale[:5]:
        out["verification_queue"].append({
            "relationship_id": s["relationship_id"],
            "from_entity_id": s["from_entity_id"],
            "to_entity_id": s["to_entity_id"],
            "days_overdue": s.get("days_overdue", 0),
            "suggested_action": "Verify with a primary or credible trade source. Use promote-confidence once corroborated.",
        })

    # 4 — Needs Todd: ambient findings that are non-watch-list but high-conviction.
    for item in ambient_sorted:
        if item["signal_class"] in {"leadership_change", "rfp_cycle_signal"}:
            out["needs_todd"].append({
                "entity_name": item["entity_name"],
                "signal_class": item["signal_class"],
                "summary": item["summary"],
                "suggestion": f"Consider adding {item['entity_name']} to your watch list — CoS detected a {item['signal_class'].replace('_', ' ')} signal.",
            })

    # 5 — Save graph mutations.
    if mutation_log:
        graph["last_updated"] = str(today)
        _save_graph(graph)

    # 6 — Interrupt queue check.
    out["interrupt_queue"] = _run_interrupt_check(graph, today)

    return out
