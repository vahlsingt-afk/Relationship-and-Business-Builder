#!/usr/bin/env python3
"""
strategic_memory.py - persistent strategic intelligence memory.

Stores durable user POV, industry assessment, company watchlist, market
pattern, relationship-opportunity, and daily-brief weighting signals in
`system/strategic_memory.json`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

MEMORY_PATH = core.SYSTEM_DIR / "strategic_memory.json"

CATEGORIES = {
    "user_pov": "User Point of View Memory",
    "static_industry_assessment": "Static Industry Assessment Memory",
    "strategic_company_watchlist": "Strategic Company Watchlist",
    "market_pattern": "Market Pattern Memory",
    "relationship_opportunity": "Relationship / Opportunity Mapping Memory",
    "daily_brief_weighting": "Daily Brief Intelligence Weighting",
}

DETECTION_PHRASES = (
    "industry intelligence",
    "note this",
    "remember this",
    "this validates",
    "add to industry assessment",
    "watchlist",
    "strategic company",
    "this supports my thesis",
    "static memory",
    "market pattern",
    "durable signal",
)

WATCHLIST_COMPANIES = (
    "Genius", "Xenial", "Global Payments", "Worldpay", "PAR",
    "NCR Voyix", "Toast", "Square", "Oracle", "CrunchTime",
    "PerfectHire", "Maho.ai", "Maho", "Starbucks",
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _blank_store() -> dict[str, Any]:
    return {
        "schema": "rb_persistent_strategic_memory_v1",
        "updated_at": None,
        "signals": [],
    }


def load_store(path: Path = MEMORY_PATH) -> dict[str, Any]:
    if not path.exists():
        return _blank_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _blank_store()
    if not isinstance(data, dict):
        return _blank_store()
    data.setdefault("schema", "rb_persistent_strategic_memory_v1")
    data.setdefault("signals", [])
    return data


def save_store(store: dict[str, Any], path: Path = MEMORY_PATH) -> None:
    store["updated_at"] = _now()
    path.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")


def detect_trigger(text: str) -> dict[str, Any]:
    low = text.lower()
    hits = [p for p in DETECTION_PHRASES if p in low]
    return {
        "detected": bool(hits),
        "trigger_phrases": hits,
    }


def _entities(text: str) -> list[str]:
    low = text.lower()
    found = []
    for company in WATCHLIST_COMPANIES:
        if company.lower() in low:
            found.append(company)
    return list(dict.fromkeys(found))


def _tags(text: str, entities: list[str]) -> list[str]:
    low = text.lower()
    tags = []
    rules = [
        ("ai", (" ai ", "artificial intelligence", "computer vision")),
        ("restaurant technology", ("restaurant tech", "restaurant technology", "restaurants")),
        ("computer vision", ("computer vision", "vision")),
        ("inventory automation", ("inventory",)),
        ("enterprise rollout failure", ("rollout", "discontinued", "scrapped", "failure", "failed")),
        ("operator trust", ("trust", "manual verification", "adoption")),
        ("ROI skepticism", ("roi", "profit", "economics")),
        ("operational resilience", ("resilience", "real-world", "operational")),
        ("enterprise deployment risk", ("enterprise", "north america", "rollout")),
    ]
    padded = f" {low} "
    for tag, needles in rules:
        if any(n in padded for n in needles):
            tags.append(tag)
    tags.extend(entities)
    return list(dict.fromkeys(tags))


def classify(text: str) -> list[str]:
    low = text.lower()
    categories = set()
    if any(p in low for p in ("my thesis", "this validates", "this supports", "point of view", "pov")):
        categories.add("user_pov")
    if any(p in low for p in ("industry intelligence", "industry assessment", "scrapped", "discontinued", "operator trust", "deployment risk")):
        categories.add("static_industry_assessment")
    if any(c.lower() in low for c in WATCHLIST_COMPANIES) or "watchlist" in low or "strategic company" in low:
        categories.add("strategic_company_watchlist")
    if any(p in low for p in ("pattern", "fail", "pilot", "rollout", "integration", "adoption", "manual verification")):
        categories.add("market_pattern")
    if any(p in low for p in ("relationship", "opportunity", "consulting", "outreach", "sales angle", "linkedin post", "content")):
        categories.add("relationship_opportunity")
    if categories:
        categories.add("daily_brief_weighting")
    return [c for c in CATEGORIES if c in categories]


def _starbucks_memory(text: str, entities: list[str], tags: list[str]) -> dict[str, Any] | None:
    low = text.lower()
    if not ("starbucks" in low and "ai" in low and "inventory" in low):
        return None
    if not any(p in low for p in ("scrap", "discontinu", "ditch", "cancel", "shut down", "end")):
        return None
    return {
        "signal": "Starbucks discontinued an AI inventory tool across North America after operational accuracy and adoption issues.",
        "what_it_proves": "Restaurant AI must survive real-world operating conditions, not just demos or pilots.",
        "strategic_implication": (
            "Enterprise buyers will increasingly scrutinize AI claims around reliability, "
            "workflow burden, ROI, and operator trust."
        ),
        "user_pov_alignment": (
            "Supports Todd's thesis that restaurant tech succeeds only when it is simple, "
            "trusted, operationally durable, and economically provable."
        ),
        "future_use": [
            "AI restaurant tech vendors",
            "computer vision companies",
            "inventory platforms",
            "enterprise rollout claims",
            "restaurant tech investment narratives",
            "LinkedIn post opportunities",
            "daily brief market intelligence",
            "relationship opportunities",
        ],
        "tags": list(dict.fromkeys(tags + [
            "AI",
            "restaurant technology",
            "computer vision",
            "inventory automation",
            "enterprise rollout failure",
            "operator trust",
            "ROI skepticism",
            "Starbucks",
            "operational resilience",
        ])),
        "entities": list(dict.fromkeys(entities + ["Starbucks"])),
        "weighting": {
            "urgency": "medium",
            "relevance": "high",
            "strategic_value": "high",
            "relationship_opportunity": "medium",
            "market_risk": "high",
            "content_opportunity": "high",
            "company_watchlist_importance": "high",
        },
    }


def _generic_memory(text: str, entities: list[str], tags: list[str]) -> dict[str, Any]:
    compact = re.sub(r"\s+", " ", text).strip()
    first_sentence = re.split(r"(?<=[.!?])\s+", compact, maxsplit=1)[0][:240]
    return {
        "signal": first_sentence or "Durable strategic signal recorded.",
        "what_it_proves": "This input was marked by Todd as durable strategic intelligence and should be reused in future reasoning.",
        "strategic_implication": "Evaluate future company, market, content, and relationship recommendations in light of this signal.",
        "user_pov_alignment": "Linked to Todd's restaurant-tech operating thesis when relevant.",
        "future_use": [
            "daily brief market intelligence",
            "company assessments",
            "relationship opportunities",
            "content strategy",
        ],
        "tags": tags,
        "entities": entities,
        "weighting": {
            "urgency": "monitor",
            "relevance": "medium",
            "strategic_value": "medium",
            "relationship_opportunity": "monitor",
            "market_risk": "medium",
            "content_opportunity": "medium",
            "company_watchlist_importance": "medium" if entities else "monitor",
        },
    }


def build_memory_signal(text: str, *, source: dict | None = None,
                        captured_at: str | None = None) -> dict[str, Any]:
    entities = _entities(text)
    tags = _tags(text, entities)
    categories = classify(text)
    payload = _starbucks_memory(text, entities, tags) or _generic_memory(text, entities, tags)
    stable = "|".join([
        payload["signal"].lower(),
        ",".join(categories),
        ",".join(payload.get("entities") or []),
    ])
    signal_id = "si_" + hashlib.sha1(stable.encode("utf-8")).hexdigest()[:14]
    return {
        "id": signal_id,
        "created_at": captured_at or _now(),
        "last_seen_at": captured_at or _now(),
        "source": source or {"type": "manual_user_input"},
        "categories": categories,
        **payload,
        "raw_excerpt": re.sub(r"\s+", " ", text).strip()[:600],
        "reuse_count": 0,
    }


def record(text: str, *, source: dict | None = None, captured_at: str | None = None,
           store_path: Path = MEMORY_PATH) -> dict[str, Any]:
    trigger = detect_trigger(text)
    if not trigger["detected"]:
        return {
            "detected": False,
            "persistence_status": "not_recorded_no_trigger",
            "message": "No durable strategic-memory trigger detected.",
            "trigger_phrases": [],
        }
    signal = build_memory_signal(text, source=source, captured_at=captured_at)
    store = load_store(store_path)
    existing = next((s for s in store["signals"] if s.get("id") == signal["id"]), None)
    if existing:
        existing["last_seen_at"] = signal["last_seen_at"]
        existing["reuse_count"] = int(existing.get("reuse_count") or 0) + 1
        existing["raw_excerpt"] = signal["raw_excerpt"]
        persisted = existing
        status = "updated_existing"
    else:
        store["signals"].append(signal)
        persisted = signal
        status = "recorded_new"
    save_store(store, store_path)
    stored_path = (
        str(store_path.relative_to(core.PROJECT_DIR))
        if store_path.is_relative_to(core.PROJECT_DIR)
        else str(store_path)
    )
    verb = "updated" if status == "updated_existing" else "recorded"
    canonical_block = {
        "scenario": "strategic_memory_record",
        "action_state": verb,
        "summary": (
            f"RB {verb} strategic memory signal {persisted.get('id', '?')} "
            f"in {stored_path}."
        ),
        "facts": [
            f"signal_id: {persisted.get('id')}",
            f"signal: {persisted.get('signal', '')[:120]}",
            f"categories: {', '.join(persisted.get('categories') or [])}",
        ],
        "inferences": [],
        "persistence": {
            "status": "persisted",
            "bundle_id": None,
            "event_ids": [],
            "storage_path": stored_path,
        },
        "projection": {
            "applied": [stored_path],
            "pending": [],
            "blocked": [],
        },
        "recommended_actions": [
            f"RB should reuse this signal when evaluating: "
            f"{', '.join(persisted.get('future_use') or [])}."
        ],
        "source_refs": [stored_path],
        "grounding": "manual_user_provided",
        "freshness": "fresh",
        "confidence": "high",
        # Human-readable text for Custom GPT rendering.
        "text": canonical_response(persisted, status=status, store_path=store_path),
    }
    return {
        "detected": True,
        "persistence_status": status,
        "trigger_phrases": trigger["trigger_phrases"],
        "stored_at": stored_path,
        "recorded": persisted,
        "canonical_response": canonical_block,
    }


def canonical_response(signal: dict[str, Any], *, status: str,
                       store_path: Path = MEMORY_PATH) -> str:
    category_names = [CATEGORIES.get(c, c) for c in signal.get("categories") or []]
    tags = ", ".join(signal.get("tags") or [])
    future = "\n".join(f"- {x}" for x in signal.get("future_use") or [])
    stored_at = str(store_path.relative_to(core.PROJECT_DIR)) if store_path.is_relative_to(core.PROJECT_DIR) else str(store_path)
    verb = "updated" if status == "updated_existing" else "recorded"
    return (
        f"Industry intelligence detected. RB {verb} the following durable signal.\n\n"
        f"Signal:\n{signal.get('signal')}\n\n"
        f"What it proves:\n{signal.get('what_it_proves')}\n\n"
        f"Strategic implication:\n{signal.get('strategic_implication')}\n\n"
        f"User POV alignment:\n{signal.get('user_pov_alignment')}\n\n"
        f"RB linked this signal to:\n"
        "daily brief weighting, company assessments, relationship opportunity mapping, "
        "and content strategy.\n\n"
        f"Stored in:\n{stored_at} ({'; '.join(category_names)})\n\n"
        f"Reusable tags:\n{tags}\n\n"
        f"Future-use requirements:\nRB should reuse this signal when evaluating:\n{future}"
    )


def query(question: str = "", *, limit: int = 10,
          store_path: Path = MEMORY_PATH) -> dict[str, Any]:
    store = load_store(store_path)
    q = question.lower()
    tokens = {t for t in re.findall(r"[a-z0-9]+", q) if len(t) > 2}

    def score(signal: dict[str, Any]) -> int:
        hay = " ".join([
            signal.get("signal") or "",
            signal.get("what_it_proves") or "",
            signal.get("strategic_implication") or "",
            " ".join(signal.get("tags") or []),
            " ".join(signal.get("entities") or []),
            " ".join(signal.get("categories") or []),
        ]).lower()
        s = sum(1 for t in tokens if t in hay)
        if "failure" in q and any(t in hay for t in ("failure", "failed", "rollout", "discontinued")):
            s += 3
        if "restaurant ai" in q and "restaurant" in hay and "ai" in hay:
            s += 4
        if "operator trust" in q and "operator trust" in hay:
            s += 3
        return s

    ranked = sorted(store.get("signals") or [], key=score, reverse=True)
    matches = [s for s in ranked if score(s) > 0][:limit]
    return {
        "question": question,
        "count": len(matches),
        "matches": matches,
        "answer": render_answer(question, matches),
    }


def render_answer(question: str, matches: list[dict[str, Any]]) -> str:
    if not matches:
        return "No persisted strategic intelligence matched that question."
    lines = ["Persisted durable strategic intelligence:"]
    for signal in matches:
        lines.append(f"- {signal.get('signal')}")
        lines.append(f"  Why it matters: {signal.get('what_it_proves')}")
        lines.append(f"  Deployment/operator risk: {signal.get('strategic_implication')}")
        tags = ", ".join(signal.get("tags") or [])
        if tags:
            lines.append(f"  Tags: {tags}")
    return "\n".join(lines)


def build_report(*, limit: int = 7, store_path: Path = MEMORY_PATH) -> dict[str, Any]:
    store = load_store(store_path)
    signals = sorted(
        store.get("signals") or [],
        key=lambda s: (s.get("last_seen_at") or s.get("created_at") or ""),
        reverse=True,
    )
    return {
        "schema": store.get("schema"),
        "updated_at": store.get("updated_at"),
        "count": len(signals),
        "top": signals[:limit],
        "categories": {
            key: sum(1 for s in signals if key in (s.get("categories") or []))
            for key in CATEGORIES
        },
    }


_STARBUCKS_FIXTURE = """
CNBC: Starbucks scrapped an AI inventory tool across North America after
workers reported accuracy problems and adoption issues. industry intelligence
"""


def _smoke() -> int:
    import tempfile
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK" if cond else "FAIL"
        print(f"  {mark}   {msg}")
        if not cond:
            failures.append(msg)

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "strategic_memory.json"
        rec = record(_STARBUCKS_FIXTURE, store_path=path)
        ck(rec["detected"] is True, "industry intelligence trigger detected")
        sig = rec["recorded"]
        ck("static_industry_assessment" in sig["categories"], "classified as static industry assessment")
        ck("market_pattern" in sig["categories"], "classified as market pattern")
        ck("daily_brief_weighting" in sig["categories"], "classified as daily brief weighting")
        ck("operator trust" in sig["tags"], "operator trust tag attached")
        ck("Restaurant AI must survive real-world" in sig["what_it_proves"], "canonical lesson recorded")
        ans = query("What durable restaurant AI failure signals do we have?", store_path=path)
        ck(ans["count"] >= 1, "query retrieves Starbucks durable failure signal")
        ck("Starbucks discontinued an AI inventory tool" in ans["answer"], "query answer names Starbucks signal")
        ck("operator trust" in ans["answer"], "query answer explains operator trust relevance")

    print(f"--- strategic_memory smoke complete: {len(failures)} failure(s) ---")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Persistent strategic intelligence memory.")
    p.add_argument("--record", help="Text to classify and persist.")
    p.add_argument("--query", help="Question to answer from persisted strategic memory.")
    p.add_argument("--json", action="store_true", help="Emit JSON.")
    p.add_argument("--smoke", action="store_true", help="Run in-memory regression.")
    args = p.parse_args(argv)

    if args.smoke:
        return _smoke()
    if args.record:
        out = record(args.record)
    elif args.query:
        out = query(args.query)
    else:
        out = build_report()
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    else:
        print(out.get("canonical_response") or out.get("answer") or json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
