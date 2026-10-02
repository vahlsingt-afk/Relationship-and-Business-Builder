#!/usr/bin/env python3
"""LinkedIn Freshness Bridge.

Daily LinkedIn inputs sit between two existing RB lanes:

* full LinkedIn archive ingestion, which is durable but periodic;
* manual RI intake, which handles relationship/message screenshots.

This bridge handles operator-provided LinkedIn post material without scraping:
screenshots after OCR, copied post text, profile/post URLs, notifications,
message-thread summaries, manual notes, and later full export reconciliation.
It classifies the input, records meaningful deltas, and promotes source-backed
industry items into market_signals so the daily brief can surface them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core  # noqa: E402
import market_signals as ms  # noqa: E402
import ecosystem_intelligence as eco  # noqa: E402
import intelligence_mutation_engine  # noqa: E402


DAILY_SIGNALS_PATH = core.INBOX_DIR / "linkedin.daily_signals.jsonl"
MARKET_SIGNALS_PATH = core.INBOX_DIR / "market_signals.json"
VENDOR_VERIFICATION_QUEUE_PATH = core.INBOX_DIR / "ecosystem" / "vendor_verification_queue.json"

SUPPORTED_INPUT_TYPES = {
    "linkedin_screenshot",
    "copied_post_text",
    "linkedin_post_url",
    "linkedin_profile_url",
    "linkedin_message_screenshot",
    "linkedin_notification",
    "manual_note",
    "linkedin_export",
}

ENTITY_PATTERNS = {
    "people": {
        "Brian Deck": r"\bBrian\s+Deck\b",
    },
    "companies": {
        "Qu": r"\bQu\b",
        "Blaze Pizza": r"\bBlaze\s+Pizza\b",
        "Dave's Hot Chicken": r"\bDave['’]s\s+Hot\s+Chicken\b",
        "GoTo Foods": r"\bGoTo\s+Foods\b",
        "Playa Bowls": r"\bPlaya\s+Bowls\b",
        "Smooth Commerce": r"\bSmooth\s+Commerce\b",
        "Pizza Hut": r"\bPizza\s+Hut\b",
        "Yum Brands": r"\bYum(?:!|\s+Brands?)\b",
        "Dragontail": r"\bDragontail\b",
        "Chaac Pizza Northeast": r"\bChaac\s+Pizza\s+Northeast\b",
    },
}

ROLE_PATTERNS = {
    "Brian Deck": r"\b(?:Chair\s*&\s*CEO|Chairman\s*(?:and|&)\s*CEO|CEO)\b"
}

OPERATIONAL_AI_RX = re.compile(
    r"\b(ai|artificial intelligence|automation|dragontail|rollout|lawsuit|"
    r"franchisee|franchisees|operator|operations|implementation|accuracy|"
    r"survivability|friday night|trust)\b",
    re.I,
)
JOB_RX = re.compile(r"\b(hiring|role|job|position|recruiter|interview)\b", re.I)
FOLLOWUP_RX = re.compile(r"\b(comment|repost|reply|connect|message|follow up|follow-up)\b", re.I)
LOW_VALUE_RX = re.compile(r"\b(giveaway|webinar reminder|sponsored|promoted)\b", re.I)
VENDOR_CUSTOMER_RX = re.compile(
    r"\b(customer|customers|client|clients|partner|partners|brand|brands|"
    r"powers|powered by|using|uses|selected|rollout|deploy|deployment)\b",
    re.I,
)
POS_RX = re.compile(r"\b(pos|point[- ]of[- ]sale|restaurant technology|ordering platform|commerce platform)\b", re.I)

KNOWN_VENDOR_CUSTOMER_POSTS = {
    "Qu": {
        "vendor_pattern": r"\bQu\b",
        "vendor_id": "vendor-qu",
        "vendor_name": "Qu",
        "category": "pos",
        "product": "Qu POS",
        "customer_brands": {
            "Blaze Pizza": r"\bBlaze\s+Pizza\b",
            "Dave's Hot Chicken": r"\bDave['’]s\s+Hot\s+Chicken\b",
            "GoTo Foods": r"\bGoTo\s+Foods\b",
            "Playa Bowls": r"\bPlaya\s+Bowls\b",
        },
        "holding_company_review": {
            "GoTo Foods": [
                "Auntie Anne's",
                "Carvel",
                "Cinnabon",
                "Jamba",
                "Moe's Southwest Grill",
                "Schlotzsky's",
            ],
        },
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _coerce_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _hash(value: str) -> str:
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def _norm_url(url: str | None) -> str | None:
    return ms._norm_url(url) if url else None


def _input_source(req: dict[str, Any], text: str) -> dict[str, Any]:
    input_type = req.get("input_type") or "manual_note"
    source_url = req.get("source_url") or req.get("post_url") or req.get("profile_url")
    is_linkedin = (
        str(input_type).startswith("linkedin")
        or "linkedin.com" in str(source_url or "").lower()
        or "linkedin" in text.lower()
    )
    return {
        "platform": "linkedin" if is_linkedin else "unknown",
        "input_type": input_type if input_type in SUPPORTED_INPUT_TYPES else "manual_note",
        "source_url": source_url,
        "source_url_canonical": _norm_url(source_url),
        "capture_policy": (
            "operator_provided_visible_data_only; no unsupported scraping; "
            "no LinkedIn credentials stored"
        ),
    }


def _extract_entities(text: str, req: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    people: list[dict[str, Any]] = []
    companies: list[dict[str, Any]] = []

    explicit_author = _coerce_text(req.get("author_name"))
    if explicit_author:
        people.append({
            "name": explicit_author,
            "role": _coerce_text(req.get("author_role")) or None,
            "company": _coerce_text(req.get("author_company")) or None,
            "source": "explicit",
        })

    seen_people = {p["name"].lower() for p in people if p.get("name")}
    for name, pattern in ENTITY_PATTERNS["people"].items():
        if re.search(pattern, text, re.I) and name.lower() not in seen_people:
            role = None
            if re.search(ROLE_PATTERNS.get(name, "$^"), text, re.I):
                role = "Chair & CEO"
            people.append({
                "name": name,
                "role": role,
                "company": "Smooth Commerce" if name == "Brian Deck" else None,
                "source": "extracted",
            })
            seen_people.add(name.lower())

    explicit_company = _coerce_text(req.get("author_company") or req.get("company"))
    if explicit_company:
        companies.append({"name": explicit_company, "source": "explicit"})
    seen_companies = {c["name"].lower() for c in companies if c.get("name")}
    for name, pattern in ENTITY_PATTERNS["companies"].items():
        if re.search(pattern, text, re.I) and name.lower() not in seen_companies:
            companies.append({"name": name, "source": "extracted"})
            seen_companies.add(name.lower())

    topics = []
    topic_checks = [
        ("restaurant_ai_implementation_risk", r"\b(ai|artificial intelligence|dragontail|automation)\b"),
        ("franchisee_trust", r"\b(franchisee|franchisees|trust|lawsuit)\b"),
        ("operator_workflow_reliability", r"\b(operator|operations|rollout|implementation|accuracy)\b"),
        ("friday_night_survivability", r"\b(friday night|survivability|rush|peak)\b"),
    ]
    for topic, pattern in topic_checks:
        if re.search(pattern, text, re.I):
            topics.append(topic)

    return {"people": people, "companies": companies, "topics": topics}


def _classify(text: str, entities: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    types: list[str] = []
    if OPERATIONAL_AI_RX.search(text):
        types.extend(["industry_intelligence", "market_signal"])
    if VENDOR_CUSTOMER_RX.search(text) and _extract_vendor_customer_claims(text, entities):
        types.extend(["industry_intelligence", "vendor_customer_intelligence", "ecosystem_graph_mutation"])
    if JOB_RX.search(text):
        types.append("job_opportunity_intelligence")
    if FOLLOWUP_RX.search(text):
        types.append("follow_up_opportunity")
    if (
        "industry_intelligence" in types
        and any(t in entities.get("topics", []) for t in (
            "restaurant_ai_implementation_risk",
            "franchisee_trust",
            "operator_workflow_reliability",
        ))
    ):
        types.append("content_opportunity")
    if not types or LOW_VALUE_RX.search(text):
        types.append("low_value_no_action")

    # Preserve order while de-duping.
    ordered_types = []
    for t in types:
        if t not in ordered_types:
            ordered_types.append(t)

    strategic_relevance = "high" if {
        "Pizza Hut",
        "Yum Brands",
        "Dragontail",
    }.issubset({c["name"] for c in entities.get("companies", [])}) else (
        "high" if "vendor_customer_intelligence" in ordered_types else
        "high" if "restaurant_ai_implementation_risk" in entities.get("topics", []) else "medium"
    )
    content_opportunity = "high" if "content_opportunity" in ordered_types else "low"
    relationship_relevance = "medium" if entities.get("people") else "low"
    recommended_posture = (
        "comment_or_save_as_research"
        if content_opportunity == "high"
        else "save_as_research"
        if "industry_intelligence" in ordered_types
        else "ignore"
    )
    if "low_value_no_action" in ordered_types and len(ordered_types) == 1:
        recommended_posture = "ignore"

    return {
        "signal_types": ordered_types,
        "primary_classification": ordered_types[0],
        "strategic_relevance": strategic_relevance,
        "relationship_relevance": relationship_relevance,
        "content_opportunity": content_opportunity,
        "recommended_posture": recommended_posture,
        "confidence": "high" if strategic_relevance == "high" else "medium",
    }


def _extract_vendor_customer_claims(text: str, entities: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Extract vendor-claimed customer relationships from operator-provided text.

    This is intentionally conservative and source-specific. It only emits claims
    when a known vendor and one or more known customer brands appear in a context
    that looks like customer/logo/usage language.
    """
    claims: list[dict[str, Any]] = []
    company_names = {c.get("name") for c in entities.get("companies", []) if c.get("name")}
    for vendor_name, config in KNOWN_VENDOR_CUSTOMER_POSTS.items():
        if not re.search(config["vendor_pattern"], text, re.I) and vendor_name not in company_names:
            continue
        customers = []
        for brand, pattern in config["customer_brands"].items():
            if re.search(pattern, text, re.I) or brand in company_names:
                customers.append(brand)
        if not customers:
            continue
        category = config["category"]
        if category == "pos" and not POS_RX.search(text):
            # Qu is POS-first in RB's current graph context. Keep category but
            # lower interpretation in the verification task if POS is not named.
            category = "pos"
        claims.append({
            "vendor": vendor_name,
            "vendor_id": config["vendor_id"],
            "customers": customers,
            "category": category,
            "product": config.get("product"),
            "source_claim_type": "vendor_claimed_customer_relationship",
            "weak_signal_classification": "vendor_claimed",
            "verification_state": "needs_verification",
            "verification_flags": [
                "vendor_claimed_relationship",
                "module_scope_unverified",
                "deployment_depth_unknown",
            ],
            "holding_company_review": {
                k: v for k, v in (config.get("holding_company_review") or {}).items()
                if k in customers
            },
        })
    return claims


def _thesis_alignment(text: str, entities: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    matched = []
    checks = [
        ("operational_ai_realism", r"\b(ai|artificial intelligence|automation|dragontail)\b"),
        ("restaurant_tech_implementation_risk", r"\b(rollout|implementation|operator|operations|accuracy)\b"),
        ("franchisee_trust", r"\b(franchisee|franchisees|trust|lawsuit)\b"),
        ("friday_night_survivability", r"\b(friday night|survivability|rush|peak)\b"),
    ]
    for label, pattern in checks:
        if re.search(pattern, text, re.I) or label in entities.get("topics", []):
            matched.append(label)
    return {
        "aligned": bool(matched),
        "matched_theses": matched,
        "summary": (
            "Validates Todd's operational AI realism lens: AI has to work in "
            "real restaurant operating conditions, not only in a demo."
            if matched else "No durable Todd thesis match detected."
        ),
    }


def _stable_key(req: dict[str, Any], source: dict[str, Any], text: str,
                entities: dict[str, list[dict[str, Any]]]) -> str:
    if source.get("source_url_canonical"):
        return "linkedin:" + source["source_url_canonical"]
    author = req.get("author_name") or (
        (entities.get("people") or [{}])[0].get("name")
        if entities.get("people") else "unknown_author"
    )
    company_bits = ",".join(sorted(c["name"] for c in entities.get("companies", []) if c.get("name")))
    return "linkedin:" + _hash("|".join([
        _coerce_text(author).lower(),
        company_bits.lower(),
        text.lower()[:500],
    ]))


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(core.PROJECT_DIR))
    except ValueError:
        return str(path)


def _load_jsonl(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or DAILY_SIGNALS_PATH
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _append_jsonl(record: dict[str, Any], path: Path | None = None) -> None:
    path = path or DAILY_SIGNALS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def _load_market(path: Path | None = None) -> dict[str, Any]:
    path = path or MARKET_SIGNALS_PATH
    if not path.exists():
        return {"fetched_at": None, "source_note": "", "items": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = {"fetched_at": None, "source_note": "", "items": []}
    payload.setdefault("items", [])
    return payload


def _save_market(payload: dict[str, Any], path: Path | None = None) -> None:
    path = path or MARKET_SIGNALS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _market_item(record: dict[str, Any]) -> dict[str, Any]:
    companies = {c["name"] for c in record["entities"].get("companies", [])}
    company = " / ".join(
        c for c in ["Pizza Hut", "Yum Brands", "Dragontail", "Chaac Pizza Northeast"]
        if c in companies
    ) or "LinkedIn restaurant-tech signal"
    source_url = record["source"].get("source_url")
    return {
        "title": "Pizza Hut / Yum / Dragontail AI rollout lawsuit signal",
        "url": source_url,
        "source_name": "LinkedIn",
        "source_type": "linkedin_social",
        "published_at": record["event_at"][:10],
        "company": company,
        "side": "operator_demand",
        "category": "restaurant_ai",
        "signal_type": "implementation_risk",
        "pain_point_or_priority": (
            "AI rollout risk, franchisee trust, operational reliability, and "
            "whether the technology survives real restaurant conditions."
        ),
        "strategic_relevance": record["classification"]["strategic_relevance"],
        "affected_relationships_or_threads": ["target-restaurants-tech-leaders"],
        "restaurant_operator_impact": (
            "Operators will pressure-test AI against store-level execution, "
            "franchisee confidence, accuracy, and rollout governance."
        ),
        "restaurant_tech_vendor_implication": (
            "AI vendors need proof that systems work operationally under peak "
            "restaurant conditions, not just technical demos."
        ),
        "second_order_impact": (
            "Franchisee trust and change management become buying criteria for "
            "restaurant AI rollouts."
        ),
        "relationship_opportunity": (
            "Use as a precise conversation or content hook with restaurant-tech "
            "operators and vendors."
        ),
        "why_this_matters_to_todd": record["thesis_alignment"]["summary"],
        "timing_priority": "this_week",
        "recommended_action": "monitor",
        "confidence": record["classification"]["confidence"],
        "bridge_signal_id": record["bridge_signal_id"],
    }


def _upsert_market_signal(record: dict[str, Any]) -> dict[str, Any]:
    payload = _load_market()
    item = _market_item(record)
    canonical_url = _norm_url(item.get("url"))
    existing_items = payload.get("items") or []
    updated = False
    for i, existing in enumerate(existing_items):
        same_url = canonical_url and _norm_url(existing.get("url")) == canonical_url
        same_bridge_id = existing.get("bridge_signal_id") == record["bridge_signal_id"]
        same_title_date = (
            existing.get("title") == item["title"]
            and existing.get("published_at") == item["published_at"]
        )
        if same_url or same_bridge_id or same_title_date:
            existing_items[i] = {**existing, **item}
            updated = True
            break
    if not updated:
        existing_items.append(item)
    payload["items"] = existing_items
    payload["fetched_at"] = record["captured_at"]
    note = payload.get("source_note") or ""
    bridge_note = "LinkedIn Freshness Bridge can add operator-provided LinkedIn signals between full exports."
    if bridge_note not in note:
        payload["source_note"] = (note + " " + bridge_note).strip()
    _save_market(payload)
    return {
        "market_signal": "updated" if updated else "added",
        "path": _display_path(MARKET_SIGNALS_PATH),
    }


def _load_verification_queue(path: Path | None = None) -> dict[str, Any]:
    path = path or VENDOR_VERIFICATION_QUEUE_PATH
    if not path.exists():
        return {"version": 1, "items": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = {"version": 1, "items": []}
    payload.setdefault("items", [])
    return payload


def _save_verification_queue(payload: dict[str, Any], path: Path | None = None) -> None:
    path = path or VENDOR_VERIFICATION_QUEUE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _upsert_verification_task(task: dict[str, Any]) -> bool:
    payload = _load_verification_queue()
    items = payload.get("items") or []
    for i, existing in enumerate(items):
        if existing.get("task_id") == task["task_id"]:
            items[i] = {**existing, **task, "updated_at": _now_iso()}
            payload["items"] = items
            payload["updated_at"] = _now_iso()
            _save_verification_queue(payload)
            return False
    items.append(task)
    payload["items"] = items
    payload["updated_at"] = _now_iso()
    _save_verification_queue(payload)
    return True


def _source_title(record: dict[str, Any], claim: dict[str, Any]) -> str:
    source_url = record["source"].get("source_url_canonical") or record["source"].get("source_url")
    suffix = source_url or record.get("bridge_signal_id")
    return f"LinkedIn vendor post: {claim['vendor']} customer claim ({record['event_at'][:10]}) {suffix}"


def _relationship_row(record: dict[str, Any], claim: dict[str, Any], customer: str) -> dict[str, str]:
    return {
        "brand": customer,
        "vendor": claim["vendor"],
        "category": claim["category"],
        "product": claim.get("product") or claim["vendor"],
        "status": "active",
        "stage": "unknown",
        "source_type": "linkedin_vendor_post",
        "source": _source_title(record, claim),
        "source_url": record["source"].get("source_url_canonical") or record["source"].get("source_url") or "",
        "confidence": "medium",
        "evidence_posture": "provisional",
        "vendor_role": "system_of_record_pos" if claim["category"] == "pos" else "unknown",
        "deployment_claim_type": "logo_or_customer_page",
        "interpretation_scope": (
            "Vendor-claimed customer relationship from LinkedIn. Module scope, "
            "deployment depth, and system-of-record status require verification."
        ),
        "strategic_note": (
            f"{claim['vendor']} publicly named {customer} as a customer. "
            "Treat as vendor-claimed weak signal until corroborated."
        ),
    }


def _ecosystem_signal(record: dict[str, Any], source_id: str, claim: dict[str, Any],
                      customer_ids: list[str]) -> dict[str, Any]:
    slug = _hash(source_id + "|".join(customer_ids))[:10]
    return {
        "id": f"sig-{record['event_at'][:10]}-vendor-claim-{slug}",
        "event_at": record["event_at"][:10],
        "captured_at": record["captured_at"],
        "signal_type": "vendor_claimed_customer_relationship",
        "summary": (
            f"{claim['vendor']} LinkedIn post named {', '.join(claim['customers'])} "
            f"as customer brands."
        ),
        "domains": ["restaurants"],
        "entities": [claim["vendor_id"], *customer_ids],
        "sources": [source_id],
        "confidence": eco._confidence(
            "medium",
            "Vendor-claimed LinkedIn signal; requires corroboration before substantiated posture.",
        ),
        "interpretation": (
            "Weak signal intelligence: vendor_claimed. Queue verification for module scope, "
            "deployment depth, and holding-company brand-family implications."
        ),
    }


def _upsert_signal(graph: dict, signal: dict) -> bool:
    signals = {s.get("id"): s for s in graph.get("signals", [])}
    if signal["id"] not in signals:
        graph.setdefault("signals", []).append(signal)
        return True
    signals[signal["id"]].update(signal)
    return False


def _mutate_ecosystem_graph(record: dict[str, Any], claims: list[dict[str, Any]]) -> dict[str, Any]:
    if not claims:
        return {"status": "not_applicable", "relationships_added": 0, "relationships_updated": 0, "verification_tasks": []}
    graph = eco._read_graph(core.ECOSYSTEM_INTELLIGENCE_PATH)
    added = updated = signals_added = signals_updated = 0
    verification_tasks: list[dict[str, Any]] = []
    relationship_ids: list[str] = []

    for claim in claims:
        source_title = _source_title(record, claim)
        source_id = f"src-{eco._slug(source_title)}"
        eco._upsert_source(graph, {
            "id": source_id,
            "source_type": "linkedin_vendor_post",
            "title": source_title,
            "url": record["source"].get("source_url_canonical") or record["source"].get("source_url"),
            "path": None,
            "published_at": record["event_at"][:10],
            "captured_at": record["captured_at"],
            "quality": "medium",
            "notes": "Vendor-claimed weak signal from operator-provided LinkedIn source.",
        })
        customer_ids: list[str] = []
        for customer in claim["customers"]:
            rel = eco.vendor_relationship(_relationship_row(record, claim, customer), graph)
            if not rel:
                continue
            # Conflict-checked upsert (RB Research Intelligence Engine Phase 1,
            # 2026-07-20) — a LinkedIn vendor-claimed relationship must not
            # silently coexist unflagged with a rival vendor's existing live
            # claim for the same brand+category, same as the structured-file
            # and article-ingestion paths.
            is_new = eco.resolve_and_upsert_relationship(graph, rel)["added"]
            if is_new:
                added += 1
            else:
                updated += 1
            relationship_ids.append(rel["id"])
            customer_ids.append(rel["from_entity_id"])
            task_id = f"verify-{rel['id']}"
            task = {
                "task_id": task_id,
                "status": "open",
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "source_signal_id": record["bridge_signal_id"],
                "relationship_id": rel["id"],
                "vendor": claim["vendor"],
                "customer_brand": customer,
                "category": claim["category"],
                "weak_signal_classification": claim["weak_signal_classification"],
                "verification_state": claim["verification_state"],
                "flags": claim["verification_flags"],
                "recommended_checks": [
                    "Find operator/vendor primary statement or case study.",
                    "Verify whether this is POS, online ordering, loyalty, or another Qu module.",
                    "Verify deployment depth: pilot, partial, franchisee subset, or systemwide.",
                    "Check whether customer relationship applies to holding-company siblings.",
                ],
                "source": {
                    "type": "linkedin_vendor_post",
                    "url": record["source"].get("source_url_canonical") or record["source"].get("source_url"),
                    "event_at": record["event_at"],
                    "captured_at": record["captured_at"],
                },
            }
            if customer in (claim.get("holding_company_review") or {}):
                task["holding_company_expansion_review"] = {
                    "platform": customer,
                    "candidate_brands": claim["holding_company_review"][customer],
                    "warning": "Do not infer sibling-brand deployment without corroboration.",
                }
            _upsert_verification_task(task)
            verification_tasks.append(task)
        if customer_ids:
            sig = _ecosystem_signal(record, source_id, claim, customer_ids)
            if _upsert_signal(graph, sig):
                signals_added += 1
            else:
                signals_updated += 1

    eco._write_graph(graph)
    return {
        "status": "persisted",
        "graph_path": _display_path(core.ECOSYSTEM_INTELLIGENCE_PATH),
        "verification_queue_path": _display_path(VENDOR_VERIFICATION_QUEUE_PATH),
        "relationships_added": added,
        "relationships_updated": updated,
        "signals_added": signals_added,
        "signals_updated": signals_updated,
        "relationship_ids": relationship_ids,
        "verification_tasks": verification_tasks,
    }


def _proof_stats(*, captured: int, recorded: int, updated: int, ignored: int,
                 deduped: int, stale: int = 0) -> dict[str, int]:
    return {
        "captured": captured,
        "recorded": recorded,
        "updated": updated,
        "ignored": ignored,
        "deduped": deduped,
        "marked_stale": stale,
    }


def process(req: dict[str, Any], *, confirm: bool | None = None) -> dict[str, Any]:
    """Classify and optionally persist a LinkedIn-derived daily signal."""
    raw_text = _coerce_text(req.get("raw_text") or req.get("text") or req.get("summary"))
    captured_at = req.get("captured_at") or _now_iso()
    event_at = req.get("event_at") or captured_at[:10]
    confirm = bool(req.get("confirm") if confirm is None else confirm)
    source = _input_source(req, raw_text)
    if source["platform"] != "linkedin":
        return {
            "detected": False,
            "summary": "No LinkedIn source context detected.",
            "persistence_status": "not_persisted",
            "proof_stats": _proof_stats(captured=0, recorded=0, updated=0, ignored=1, deduped=0),
        }

    entities = _extract_entities(raw_text, req)
    classification = _classify(raw_text, entities)
    vendor_customer_claims = _extract_vendor_customer_claims(raw_text, entities)
    thesis = _thesis_alignment(raw_text, entities)
    stable_key = _stable_key(req, source, raw_text, entities)
    bridge_id = "li_sig_" + event_at[:10].replace("-", "") + "_" + _hash(stable_key)[:10]
    meaningful = classification["primary_classification"] != "low_value_no_action"
    recommended_action = (
        "Consider a Todd-style LinkedIn response tying this to Friday-night "
        "survivability, franchisee trust, and the difference between AI working "
        "technically versus working operationally."
        if classification.get("content_opportunity") == "high"
        else "Save as research; no direct outreach needed yet."
        if meaningful
        else "Ignore; no RB persistence needed."
    )

    record = {
        "bridge_signal_id": bridge_id,
        "stable_key": stable_key,
        "captured_at": captured_at,
        "event_at": event_at,
        "source": source,
        "raw_text_hash": "sha256:" + _hash(raw_text),
        "entities": entities,
        "classification": classification,
        "ecosystem_intelligence": {
            "vendor_customer_claims": vendor_customer_claims,
            "expected_graph_mutation": bool(vendor_customer_claims),
            "mutation_policy": (
                "confirm=true persists provisional vendor/customer edges, source metadata, "
                "weak-signal classification, and verification tasks."
            ),
        },
        "thesis_alignment": thesis,
        "recommended_action": recommended_action,
        "reconciliation": {
            "dedupe_key": stable_key,
            "next_export_match_policy": (
                "match by source_url_canonical first; otherwise author + "
                "company/topic/text hash"
            ),
            "status": "pending_next_linkedin_export",
        },
    }

    prior = _load_jsonl()
    duplicate = next((r for r in prior if r.get("stable_key") == stable_key), None)
    market_result = None
    ecosystem_result = None
    mutation_result = None
    auto_persist_ecosystem = bool(vendor_customer_claims)
    persistence_status = "not_persisted"
    persistence_action = "No persistence needed because the signal was low-value/no-action."
    stats = _proof_stats(
        captured=1,
        recorded=0,
        updated=0,
        ignored=0 if meaningful else 1,
        deduped=1 if duplicate else 0,
    )

    if duplicate:
        persistence_status = duplicate.get("persistence_status") or "persisted"
        persistence_action = "Already recorded; no duplicate written."
        record["bridge_signal_id"] = duplicate.get("bridge_signal_id") or bridge_id
        if meaningful and auto_persist_ecosystem:
            ecosystem_result = _mutate_ecosystem_graph(record, vendor_customer_claims)
            persistence_action = "Already recorded; ecosystem vendor graph checked/updated."
    elif meaningful and (confirm or auto_persist_ecosystem):
        record["persistence_status"] = "persisted"
        _append_jsonl(record)
        market_result = _upsert_market_signal(record)
        if vendor_customer_claims:
            ecosystem_result = _mutate_ecosystem_graph(record, vendor_customer_claims)
        persistence_status = "persisted"
        persistence_action = (
            "Persisted LinkedIn signal and updated ecosystem vendor graph."
            if ecosystem_result and ecosystem_result.get("status") == "persisted"
            else "Added to operational AI case-study/watchlist queue."
        )
        stats = _proof_stats(captured=1, recorded=1, updated=0, ignored=0, deduped=0)
    elif meaningful:
        persistence_status = "proposed_write_pending_confirmation"
        persistence_action = (
            "Ready to record and mutate ecosystem vendor graph when confirm=true."
            if vendor_customer_claims else "Ready to record when confirm=true."
        )

    if meaningful:
        author = next(
            (person for person in entities.get("people", []) if person.get("name")),
            {},
        )
        mutation_result = intelligence_mutation_engine.run(
            raw_text,
            source_title=(
                f"LinkedIn post by {author.get('name')}"
                if author.get("name") else "LinkedIn post"
            ),
            source_url=source.get("source_url_canonical") or source.get("source_url") or "",
            source_date=event_at,
            source_author_name=author.get("name") or "",
            source_author_org=author.get("company") or "",
            source_author_role=author.get("role") or "",
            auto_apply=True,
        )

    canonical_output = {
        "headline": "LinkedIn signal detected.",
        "rb_recorded": {
            "source": "LinkedIn post",
            "person": ", ".join(p["name"] for p in entities.get("people", []) if p.get("name")),
            "company": ", ".join(c["name"] for c in entities.get("companies", []) if c.get("name")),
            "topic": "Pizza Hut / Yum / Dragontail AI rollout lawsuit"
            if {"Pizza Hut", "Yum Brands", "Dragontail"} & {c["name"] for c in entities.get("companies", [])}
            else ", ".join(entities.get("topics", [])),
            "classification": " + ".join(classification["signal_types"]),
            "strategic_relevance": classification["strategic_relevance"],
            "relationship_relevance": classification["relationship_relevance"],
            "content_opportunity": classification["content_opportunity"],
            "persistence_action": persistence_action,
            "ecosystem_graph_mutation": ecosystem_result or {
                "status": "proposed_write_pending_confirmation" if vendor_customer_claims and not confirm else "not_applicable",
                "vendor_customer_claims": vendor_customer_claims,
            },
        },
        "recommended_action": recommended_action,
        "proof_stats": stats,
    }

    return {
        "detected": True,
        "bridge_signal_id": record["bridge_signal_id"],
        "persistence_status": persistence_status,
        "record": record,
        "market_signal_result": market_result,
        "ecosystem_graph_result": ecosystem_result,
        "intelligence_mutation_result": mutation_result,
        "canonical_output": canonical_output,
        "proof_stats": stats,
    }


def reconcile_with_export(export_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare daily bridge records with later LinkedIn export/session rows."""
    records = _load_jsonl()
    export_keys = set()
    for item in export_items:
        text = _coerce_text(item.get("text") or item.get("raw_text") or item.get("summary"))
        source = _input_source(item, text)
        entities = _extract_entities(text, item)
        export_keys.add(_stable_key(item, source, text, entities))
    matched = [r for r in records if r.get("stable_key") in export_keys]
    return {
        "daily_records": len(records),
        "export_items": len(export_items),
        "matched_without_duplicate": len(matched),
        "unmatched_daily_records": len(records) - len(matched),
        "policy": "export rows refresh proof; they do not create duplicates when stable_key matches",
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="infile", help="JSON request file; defaults to stdin")
    p.add_argument("--confirm", action="store_true", help="Persist meaningful signals")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    raw = Path(args.infile).read_text(encoding="utf-8") if args.infile else sys.stdin.read()
    result = process(json.loads(raw), confirm=args.confirm)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
