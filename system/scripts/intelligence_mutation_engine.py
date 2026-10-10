#!/usr/bin/env python3
"""
intelligence_mutation_engine.py — Knowledge graph mutation engine (DEFECT-026).

Converts article/signal text into durable knowledge graph mutations.
The mutation is the deliverable, not the article.

Pipeline:
  Stage 1 — Entity Extraction: brands, executives, vendors, products, POVs
  Stage 2 — Entity Resolution: match against ecosystem_intelligence + baseline
  Stage 3 — Mutation Generation: vendor-customer relationships, exec POVs,
             entity profiles, thesis validations
  Stage 4 — Confidence Assessment: auto-apply (≥0.80) or propose (0.50-0.79)
  Stage 5 — Brief Reporting: "what changed" not "what was mentioned"

Used by:
  - POST /intelligence/mutate (API)
  - ingestContent (auto-called after macro signal processing)
  - Daily brief "overnight_knowledge_mutations" section

CLI:
  python3 intelligence_mutation_engine.py --text "Article text..."
  python3 intelligence_mutation_engine.py --triage-file path/to/triage.json
  python3 intelligence_mutation_engine.py --text "..." --auto-apply --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import ecosystem_intelligence as eco  # noqa: E402

MUTATION_LOG_PATH = core.CACHE_DIR / "knowledge_mutations.json"
EXEC_POV_PATH = core.CACHE_DIR / "executive_povs.json"
ENGAGEMENT_OPPORTUNITIES_PATH = core.CACHE_DIR / "engagement_opportunities.json"

# RB Unified Restaurant-Tech Graph (2026-07-31), Phase 5 -- daily mutation
# workflow inputs/outputs. Named module-level constants (not derived from
# core.SYSTEM_DIR/core.INBOX_DIR inline inside a function) so tests can
# monkeypatch each one explicitly, the same way MUTATION_LOG_PATH/EXEC_POV_PATH
# above are patched -- core.INBOX_DIR/core.CACHE_DIR are themselves bound to
# the real filesystem at rb_core.py's import time, so patching core.SYSTEM_DIR
# alone in a test does NOT retroactively change any path already derived from
# it (the exact bug that briefly corrupted the real ecosystem_intelligence.json
# during this phase's development -- see the Phase 5 handoff note).
EARNINGS_SIGNALS_PATH = core.INBOX_DIR / "market_signals_earnings.jsonl"
MARKET_FEED_SIGNALS_PATH = core.INBOX_DIR / "market_signals_feed.jsonl"
ECOSYSTEM_MUTATION_RECEIPT_PATH = core.CACHE_DIR / "ecosystem_mutation_receipt.json"

# Signal types (shared vocabulary between earnings_monitor.py and
# market_source_feeds.py's classifiers, Phase 4) that describe a vendor-
# customer relationship lifecycle event, mapped onto Phase 2's
# reconciliation-outcome vocabulary (ecosystem_intelligence.RECONCILIATION_OUTCOMES).
SIGNAL_TYPE_TO_RECONCILIATION_OUTCOME: dict[str, str] = {
    "provider_win": "new_relationship",
    "contract_renewal_expansion": "lifecycle_update",
    "vendor_churn_loss": "supersedes_existing_relationship",
}

_CONFIDENCE_LABEL_TO_SCORE = {"high": 0.85, "medium": 0.65, "low": 0.45}

# ---------------------------------------------------------------------------
# Stage 1 — Entity Extraction
# ---------------------------------------------------------------------------

# Known restaurant tech vendors — exact match anchors
KNOWN_VENDORS: dict[str, dict] = {
    "par technology": {"id": "vendor-par-technology", "category": "pos", "products": ["Brink POS", "PAR Loyalty"]},
    "par tech": {"id": "vendor-par-technology", "category": "pos"},
    "toast": {"id": "vendor-toast", "category": "pos"},
    "olo": {"id": "vendor-olo", "category": "online_ordering"},
    "crunchtime": {"id": "vendor-crunchtime", "category": "back_office"},
    "zenput": {"id": "vendor-zenput", "category": "operations"},
    "wisetail": {"id": "vendor-wisetail", "category": "training_lms"},
    "arrowstream": {"id": "vendor-arrowstream", "category": "supply_chain"},
    "qu pos": {"id": "vendor-qu-pos", "category": "pos"},
    "qu": {"id": "vendor-qu-pos", "category": "pos"},
    "ncrvoix": {"id": "vendor-ncr-voix", "category": "voice_ai"},
    "ncr": {"id": "vendor-ncr", "category": "pos"},
    "revel": {"id": "vendor-revel", "category": "pos"},
    "lightspeed": {"id": "vendor-lightspeed", "category": "pos"},
    "oracle hospitality": {"id": "vendor-oracle-hospitality", "category": "pos"},
    "micros": {"id": "vendor-micros", "category": "pos"},
    "xenial": {"id": "vendor-xenial", "category": "pos"},
    "global payments": {"id": "vendor-global-payments", "category": "payments"},
    "heartland": {"id": "vendor-heartland", "category": "payments"},
    "square": {"id": "vendor-square", "category": "pos"},
    "paytronix": {"id": "vendor-paytronix", "category": "loyalty"},
    "punchh": {"id": "vendor-punchh", "category": "loyalty"},
    "thanx": {"id": "vendor-thanx", "category": "loyalty"},
    "personica": {"id": "vendor-personica", "category": "loyalty"},
    "taskus": {"id": "vendor-taskus", "category": "service"},
    "presto": {"id": "vendor-presto", "category": "automation"},
    "miso robotics": {"id": "vendor-miso-robotics", "category": "automation"},
    "encounter ai": {"id": "vendor-encounter-ai", "category": "voice_ai"},
    "hi auto": {"id": "vendor-hi-auto", "category": "voice_ai"},
    "sounthound": {"id": "vendor-soundhound", "category": "voice_ai"},
    "soundhound": {"id": "vendor-soundhound", "category": "voice_ai"},
    "tillster": {"id": "vendor-tillster", "category": "online_ordering"},
    "sparkfly": {"id": "vendor-sparkfly", "category": "loyalty"},
    "restaurant365": {"id": "vendor-restaurant365", "category": "back_office"},
    "aloha": {"id": "vendor-aloha-ncrvoix", "category": "pos"},
    "focus pos": {"id": "vendor-focus-pos", "category": "pos"},
    "harbortouch": {"id": "vendor-harbortouch", "category": "pos"},
    "compeat": {"id": "vendor-compeat", "category": "back_office"},
    "ctuit": {"id": "vendor-ctuit", "category": "back_office"},
    "schedulefly": {"id": "vendor-schedulefly", "category": "workforce"},
    "hotschedules": {"id": "vendor-hotschedules", "category": "workforce"},
    "4th": {"id": "vendor-fourth", "category": "workforce"},
    "restaurant magic": {"id": "vendor-restaurant-magic", "category": "back_office"},
}

# Relationship extraction patterns
VENDOR_RELATIONSHIP_PATTERNS = [
    # "X uses Y for Z" / "X deploys Y" / "X runs Y"
    re.compile(
        r"(?P<brand>[A-Z][a-zA-Z&'\s]{2,40}?)\s+(?:uses?|deploys?|runs?|powered?\s+by|partnered?\s+with|"
        r"implement(?:ed|ing|s)?|roll(?:ed|ing|s)?\s+out|select(?:ed|ing|s)?|chose?|adopt(?:ed|ing|s)?)\s+"
        r"(?P<vendor>[A-Z][a-zA-Z\s]{2,30}?)(?:\s+for|\s+as|\s+to|\.|,)",
        re.I,
    ),
    # "Y powers X" / "Y serves X" / "Y is X's platform"
    re.compile(
        r"(?P<vendor>[A-Z][a-zA-Z\s]{2,30}?)\s+(?:powers?|serves?|provides?|supports?|is\s+the\s+platform\s+for)\s+"
        r"(?P<brand>[A-Z][a-zA-Z&'\s]{2,40}?)'?s?(?:\s+|\.)",
        re.I,
    ),
]

# Executive title detection.
# RB-DEFECT-2026-08-29: the whole pattern used to compile with re.I, which
# made [A-Z] and [a-z] equivalent -- silently defeating the Title-Case
# constraint the (?P<name>...) group was written to enforce. Any lowercase
# 2-4 word phrase immediately followed by a role keyword ("CEO", "Director",
# ...) matched as a fabricated executive name -- confirmed live: a real
# LinkedIn screenshot about Starbucks/NomadGo produced a persisted
# executive_pov mutation naming "at the centre of" as a "CEO". Fixed by
# dropping the blanket re.I and scoping case-insensitivity to only the
# title-keyword alternation (?i:...), so the name/company groups stay
# strictly proper-noun-shaped as originally intended.
EXEC_TITLE_PATTERN = re.compile(
    r"(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}),?\s+"
    r"(?P<title>(?i:(?:Chief|C[EOTIMF]O|President|VP|Vice\s+President|SVP|EVP|Director|"
    r"Head\s+of|General\s+Manager|Managing\s+Director|Founder|Co-Founder|Partner))[\w\s,]*?)"
    r"(?:\s+at\s+(?P<company>[A-Z][a-zA-Z\s&']{2,40}?))?(?:\s|,|\.|$)"
)

# Quote/POV extraction — look for attribution patterns.
# Same re.I name-shape bug as EXEC_TITLE_PATTERN above -- scoped the
# case-insensitivity to just the attribution-verb alternation so a
# lowercase phrase can no longer be misread as a real speaker's name.
QUOTE_PATTERN = re.compile(
    r'"([^"]{20,300})"[,\s]*(?i:(?:said|says|noted|explained|stated|according\s+to|per))\s+'
    r'(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})'
)
BLOCK_QUOTE_PATTERN = re.compile(
    r'(?P<name>[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})[,\s]+(?i:(?:said|says|noted|explained|stated))[:,]\s+'
    r'"([^"]{20,300})"'
)

# Thesis signal keywords — mapped to strategic memory themes
THESIS_SIGNALS: dict[str, list[str]] = {
    "human_in_loop_ai": [
        "human in the loop", "human accountability", "ai should not own",
        "humans must remain", "operator oversight", "ai augment",
        "not replace humans", "human judgment", "ai assist",
        "remain in the loop",
    ],
    "execution_over_technology": [
        "execution", "operational discipline", "technology amplifies",
        "information does not equal execution", "people over platform",
        "culture eats strategy", "consistent execution", "operational excellence",
    ],
    "restaurant_ai_skepticism": [
        "ai rollback", "discontinued ai", "failed ai", "ai accuracy",
        "ai reliability", "operator skepticism", "not ready", "false positive",
        "ai friction", "adoption challenge",
    ],
    "technology_consolidation": [
        "consolidation", "fewer vendors", "platform play", "all-in-one",
        "single vendor", "integrated stack", "vendor fatigue",
    ],
    "franchise_technology": [
        "franchise", "franchisee", "system-wide", "brand standard",
        "corporate mandate", "franchisee adoption",
    ],
}


# ---------------------------------------------------------------------------
# RB 9.89 (RB-DEFECT-046 Slice 2): Strategic Narrative — tech stack categories
# ---------------------------------------------------------------------------

# Categories tracked for the "tech_stack_modernization" strategic narrative.
# Mirrors the categories present in KNOWN_VENDORS.
_TECH_STACK_CATEGORIES = {
    "pos", "back_office", "loyalty", "online_ordering", "payments",
    "workforce", "voice_ai", "automation",
}

# Free-text keywords (no vendor name required) used to detect a brand
# discontinuing/replacing a category of its tech stack — e.g. "Howie Rewards
# loyalty program will be sunset" — even when the replacement isn't yet named.
_CATEGORY_LIFECYCLE_KEYWORDS: dict[str, list[str]] = {
    "loyalty": ["loyalty program", "loyalty platform", "rewards program", "rewards app"],
    "pos": ["pos system", "point of sale system", "point-of-sale system"],
    "back_office": ["back-office platform", "back office platform", "back-office system"],
    "online_ordering": ["online ordering platform", "digital ordering platform", "online ordering system"],
}

_LIFECYCLE_VERBS = re.compile(
    r"\b(sunset(?:ting|s|ted)?|discontinu(?:e|ed|ing|es)|retir(?:e|ed|ing|es)|"
    r"wind(?:ing)?\s+down|phas(?:e|ed|ing)\s+out|replac(?:e|ed|ing|es))\b",
    re.I,
)


def _extract_category_lifecycle_signals(text: str) -> list[dict]:
    """Detect tech-stack category decommission/sunset language.

    Returns a list of `{"category", "signal_type", "sentence_evidence",
    "confidence"}` dicts — one per (category, sentence) match. Does not
    require a known vendor name, since brands often retire a proprietary
    product (e.g. "Howie Rewards") rather than a third-party vendor.
    """
    signals: list[dict] = []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        if not _LIFECYCLE_VERBS.search(sentence):
            continue
        sent_lower = sentence.lower()
        for category, keywords in _CATEGORY_LIFECYCLE_KEYWORDS.items():
            if any(kw in sent_lower for kw in keywords):
                signals.append({
                    "category": category,
                    "signal_type": "sunset",
                    "sentence_evidence": sentence.strip()[:200],
                    # >= 0.75 auto-apply threshold: a narrative supporting
                    # signal is additive/soft (not a canonical-fact mutation),
                    # so it doesn't need human confirmation to accumulate.
                    "confidence": 0.80,
                })
    return signals


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _detect_article_subject(text: str) -> str | None:
    """Detect the primary brand/company the article is about.

    Looks for possessive forms, title patterns, and frequency.
    Returns the brand name string or None.
    """
    # Look for "X's [technology|growth|expansion|CTO|CEO]" pattern — possessive = subject
    possessive_pat = re.compile(
        r"(?:^|\.\s+)([A-Z][a-zA-Z&’'\s]{3,40}?)(?:'s|'s|’s)\s+(?:technolog|growth|expan|digit|"
        r"cto|ceo|chief|system|franchise|platform|operat|footprint|journey|strateg|philosoph|brand)",
        re.I | re.M,
    )
    for m in possessive_pat.finditer(text):
        name = m.group(1).strip()
        if len(name.split()) >= 1 and len(name) > 4:
            return name
    return None


def _extract_entities(text: str) -> dict:
    """Stage 1: extract all entity signals from article text."""
    text_lower = text.lower()
    found_vendors: list[dict] = []
    found_brands: list[dict] = []
    exec_mentions: list[dict] = []
    quotes: list[dict] = []
    thesis_signals: list[dict] = []

    # Detect article subject (the brand being profiled)
    article_subject = _detect_article_subject(text)

    # Vendor detection — exact match first.
    # Word-boundaried, not a bare substring check: `vendor_name in text_lower`
    # false-positives on short vendor keys like "olo"/"ncr"/"qu" matching
    # inside unrelated words ("techn-OLO-gy", "i-NCR-easing", "-QU-arter").
    # Mirrors the fix already applied in _find_known_vendors_in_text below —
    # that fix never got propagated to this sibling extraction path.
    for vendor_name, vendor_data in KNOWN_VENDORS.items():
        if re.search(r"\b" + re.escape(vendor_name) + r"\b", text_lower):
            found_vendors.append({
                "name": vendor_name.title(),
                "id": vendor_data["id"],
                "category": vendor_data.get("category"),
                "confidence": 0.90,
                "match_method": "exact",
            })

    # If we know the article subject, look for explicit vendor relationship language
    # Use a broader approach: any sentence mentioning a known vendor + the article subject
    # or relationship language → brand-vendor relationship
    RELATIONSHIP_VERBS = re.compile(
        r"\b(uses?|deploys?|runs?|implement(?:ed|ing|s)?|roll(?:ed|ing|s)?\s+out|"
        r"select(?:ed|ing|s)?|chose?|adopt(?:ed|ing|s)?|powered?\s+by|partner(?:ed|ing|s)?\s+with|"
        r"also\s+uses?|including|along\s+with|as\s+well\s+as|such\s+as|"
        r"renews?|renewed|extends?\s+(?:its\s+|their\s+)?(?:agreement|contract|partnership)|"
        r"expands?\s+(?:its\s+|their\s+)?(?:rollout|partnership|agreement|deployment)|"
        r"replac(?:e|es|ed|ing)\s+[\w\s]{1,30}?\s+with|switch(?:es|ed|ing)?\s+(?:to|from|away\s+from)|"
        r"ends?\s+(?:its\s+|their\s+)?relationship\s+with|terminates?\s+(?:its\s+|their\s+)?agreement\s+with)\b",
        re.I,
    )
    # RB Unified Restaurant-Tech Graph (2026-07-31): classify which lifecycle
    # bucket a relationship sentence describes, tagged onto the mutation as
    # `reconciliation_outcome` using ecosystem_intelligence.RECONCILIATION_OUTCOMES'
    # vocabulary — the same vocabulary Phase 2's workbook migration tool groups
    # its dry-run report by, so a daily-signal mutation and a workbook-migration
    # row speak the same language downstream.
    _RENEWAL_VERBS = re.compile(
        r"\b(renews?|renewed|extends?\s+(?:its\s+|their\s+)?(?:agreement|contract|partnership)|"
        r"expands?\s+(?:its\s+|their\s+)?(?:rollout|partnership|agreement|deployment))\b",
        re.I,
    )
    _CHURN_VERBS = re.compile(
        r"\b(replac(?:e|es|ed|ing)\s+[\w\s]{1,30}?\s+with|switch(?:es|ed|ing)?\s+(?:to|from|away\s+from)|"
        r"ends?\s+(?:its\s+|their\s+)?relationship\s+with|terminates?\s+(?:its\s+|their\s+)?agreement\s+with)\b",
        re.I,
    )

    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        sent_lower = sentence.lower()
        has_rel_verb = bool(RELATIONSHIP_VERBS.search(sentence))
        if _CHURN_VERBS.search(sentence):
            reconciliation_outcome = "supersedes_existing_relationship"
        elif _RENEWAL_VERBS.search(sentence):
            reconciliation_outcome = "lifecycle_update"
        else:
            reconciliation_outcome = "new_relationship"

        for vendor_name, vendor_data in KNOWN_VENDORS.items():
            if not re.search(r"\b" + re.escape(vendor_name) + r"\b", sent_lower):
                continue

            # Find the brand in this sentence (either subject or nearby brand)
            brand = article_subject
            if not brand:
                # Try to extract from sentence: first capitalized phrase before verb
                brand_m = re.search(
                    r"^([A-Z][a-zA-Z&'\s]{2,30}?)\s+(?:has|have|is|are|also|uses?|deploys?|"
                    r"renews?|renewed|extends?|expands?|replac(?:e|es|ed|ing)|switch(?:es|ed|ing)?)",
                    sentence.strip()
                )
                if brand_m:
                    brand = brand_m.group(1).strip()

            if brand:
                # Explicit relationship language → high confidence
                # Just co-mention in article profile → medium confidence
                conf = 0.85 if has_rel_verb else 0.65
                # Avoid duplicates
                existing = next(
                    (r for r in found_brands
                     if r["vendor_id"] == vendor_data["id"] and _slug(r["brand"]) == _slug(brand)),
                    None
                )
                if not existing:
                    found_brands.append({
                        "brand": brand,
                        "vendor": vendor_name.title(),
                        "vendor_id": vendor_data["id"],
                        "category": vendor_data.get("category"),
                        "confidence": conf,
                        "relationship_type": "uses_vendor_for_category",
                        "sentence_evidence": sentence.strip()[:200],
                        "reconciliation_outcome": reconciliation_outcome,
                    })

    # Executive mentions
    for m in EXEC_TITLE_PATTERN.finditer(text):
        name = m.group("name").strip()
        title = m.group("title").strip().rstrip(",.")
        company = (m.group("company") or "").strip()
        # If article has a subject and no company was found in the regex, use subject
        if not company and article_subject:
            company = article_subject
        if len(name.split()) >= 2:  # require first + last
            exec_mentions.append({
                "name": name,
                "title": title,
                "company": company,
                "confidence": 0.80 if company else 0.60,
            })

    # Quote extraction
    for m in QUOTE_PATTERN.finditer(text):
        quotes.append({"speaker": m.group("name"), "quote": m.group(1).strip(), "confidence": 0.85})
    for m in BLOCK_QUOTE_PATTERN.finditer(text):
        quotes.append({"speaker": m.group("name"), "quote": m.group(2).strip(), "confidence": 0.80})

    # Thesis signals
    for theme, keywords in THESIS_SIGNALS.items():
        matched_kws = [kw for kw in keywords if kw in text_lower]
        if matched_kws:
            thesis_signals.append({
                "theme": theme,
                "matched_keywords": matched_kws[:5],
                "direction": "supports",
                "confidence": min(0.92, 0.60 + len(matched_kws) * 0.10),
            })

    return {
        "article_subject": article_subject,
        "vendors_detected": found_vendors,
        "brand_vendor_relationships": found_brands,
        "exec_mentions": _dedupe_execs(exec_mentions),
        "quotes": _dedupe_quotes(quotes),
        "thesis_signals": thesis_signals,
        "category_lifecycle_signals": _extract_category_lifecycle_signals(text),
    }


def _dedupe_execs(execs: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for e in execs:
        key = _slug(e["name"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _dedupe_quotes(quotes: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for q in quotes:
        key = q["quote"][:40]
        if key not in seen:
            seen.add(key)
            out.append(q)
    return out


# ---------------------------------------------------------------------------
# Stage 2 — Entity Resolution
# ---------------------------------------------------------------------------

def _load_ecosystem() -> dict:
    try:
        return json.loads(core.SYSTEM_DIR.joinpath("ecosystem_intelligence.json").read_text())
    except Exception:
        return {}


def _load_baseline() -> list[dict]:
    try:
        raw = json.loads(core.BASELINE_PATH.read_text())
        return raw if isinstance(raw, list) else list(raw.values())
    except Exception:
        return []


def _load_strategic_memory() -> dict:
    sm_path = core.SYSTEM_DIR / "strategic_memory.json"
    try:
        return json.loads(sm_path.read_text())
    except Exception:
        return {"signals": []}


def _resolve_brand(brand_name: str, ecosystem: dict) -> dict | None:
    """Find brand in ecosystem entities by name."""
    raw_entities = ecosystem.get("entities") or []
    # Normalize: ecosystem stores entities as list or dict
    if isinstance(raw_entities, dict):
        entities_list = list(raw_entities.values())
    else:
        entities_list = list(raw_entities)

    brand_lower = brand_name.lower()
    slug_id = f"brand-{_slug(brand_name)}"

    for entity in entities_list:
        if not isinstance(entity, dict):
            continue
        if entity.get("id") == slug_id:
            return entity
        ename = (entity.get("name") or "").lower()
        aliases = [str(a).lower() for a in (entity.get("aliases") or [])]
        if brand_lower in ename or ename in brand_lower:
            return entity
        if any(brand_lower in a or a in brand_lower for a in aliases):
            return entity

    return None


# ---------------------------------------------------------------------------
# RB 9.89 (RB-DEFECT-046 Slice 2): persisted Strategic Narrative
# ---------------------------------------------------------------------------

_CATEGORY_LABELS = {
    "pos": "POS",
    "back_office": "back-office platform",
    "loyalty": "loyalty platform",
    "online_ordering": "online ordering platform",
    "payments": "payments",
    "workforce": "workforce management",
    "voice_ai": "voice AI",
    "automation": "automation",
}


def _get_or_create_brand_entity(ecosystem: dict, brand_id: str, brand_name: str, now: str) -> dict:
    """Find `brand_id` in `ecosystem["entities"]`, creating a minimal stub if absent."""
    entities = ecosystem.get("entities") or []
    if not isinstance(entities, list):
        entities = list(entities.values()) if isinstance(entities, dict) else []
    ecosystem["entities"] = entities
    for entity in entities:
        if isinstance(entity, dict) and entity.get("id") == brand_id:
            return entity
    entity = {
        "id": brand_id,
        "name": brand_name,
        "entity_type": "brand",
        "status": "active",
        "aliases": [],
        "attributes": {},
        "sources": [],
        "confidence": {"level": "low", "score": None, "rationale": "Auto-created from mutation engine.", "review_after": None},
        "notes": "",
        "created_at": now,
        "updated_at": now,
    }
    entities.append(entity)
    return entity


def update_strategic_narratives(
    brand_entity: dict,
    *,
    category: str,
    signal_type: str,
    description: str,
    source: dict,
    now: str,
) -> dict | None:
    """Update (or create) `brand_entity["strategic_narratives"]` in place.

    Accumulates `category`-scoped signals (vendor selections and
    sunset/decommission events) into a single `tech_stack_modernization`
    narrative per brand entity. Returns the updated narrative dict, or
    `None` if `category` is not a tracked tech-stack category.

    Confidence escalates with the number of distinct categories touched:
    1 -> low, 2 -> medium, 3+ -> high. `next_expected_signals` lists the
    tracked categories not yet represented, plus a replacement-platform
    expectation for any category with an unresolved sunset signal.
    """
    if category not in _TECH_STACK_CATEGORIES:
        return None

    narratives = brand_entity.setdefault("strategic_narratives", [])
    narrative = next(
        (n for n in narratives
         if n.get("narrative_type") == "tech_stack_modernization"
         and n.get("status", "active") == "active"),
        None,
    )
    if narrative is None:
        narrative = {
            "id": f"narrative-{_slug(brand_entity.get('name') or brand_entity.get('id') or '')}-tech-stack-modernization",
            "narrative_type": "tech_stack_modernization",
            "status": "active",
            "confidence": "low",
            "supporting_signals": [],
            "next_expected_signals": [],
            "last_updated": now,
        }
        narratives.append(narrative)

    narrative["supporting_signals"].append({
        "category": category,
        "signal_type": signal_type,
        "description": description,
        "source": source,
        "added_at": now,
    })

    categories_seen = {s["category"] for s in narrative["supporting_signals"]}
    if len(categories_seen) >= 3:
        narrative["confidence"] = "high"
    elif len(categories_seen) == 2:
        narrative["confidence"] = "medium"
    else:
        narrative["confidence"] = "low"

    # next_expected_signals: unresolved sunsets expect a replacement
    # announcement; uncovered tracked categories are open questions.
    sunset_categories = {
        s["category"] for s in narrative["supporting_signals"]
        if s["signal_type"] == "sunset"
    }
    replaced_categories = {
        s["category"] for s in narrative["supporting_signals"]
        if s["signal_type"] != "sunset"
    }
    next_expected: list[str] = []
    for cat in sorted(sunset_categories - replaced_categories):
        label = _CATEGORY_LABELS.get(cat, cat)
        next_expected.append(f"{label} replacement announcement")
    # Only project "what else might change" once a narrative has at least
    # two corroborating signals — a single signal isn't enough to imply a
    # broader modernization is underway.
    if len(categories_seen) >= 2:
        for cat in sorted(_TECH_STACK_CATEGORIES - categories_seen):
            label = _CATEGORY_LABELS.get(cat, cat)
            next_expected.append(f"{label} selection or change")
    narrative["next_expected_signals"] = next_expected
    narrative["last_updated"] = now

    return narrative


def build_company_intelligence_file(entity_name: str, ecosystem: dict | None = None) -> dict:
    """RB 9.90 (RB-DEFECT-046 Slice 3): render a brand's tech stack + strategic
    narratives — "what RB already knows" about `entity_name`.

    Returns `{"available": False, "reason": ...}` if no matching brand entity
    exists. Otherwise returns `{available, brand_id, brand_name, tech_stack,
    strategic_narratives, last_verified}`, where `tech_stack` maps each
    tracked category to `{vendor, status, source}` with `status` one of
    `active` (an active `uses_vendor_for_category` relationship exists),
    `sunset` (the strategic narrative records a sunset signal for this
    category with no subsequent vendor selection), or `unknown`.
    """
    if ecosystem is None:
        ecosystem = _load_ecosystem()

    brand_entity = _resolve_brand(entity_name, ecosystem)
    if brand_entity is None:
        return {"available": False, "reason": f"no entity matching '{entity_name}'"}

    entities_by_id = {
        e.get("id"): e for e in (ecosystem.get("entities") or []) if isinstance(e, dict)
    }
    relationships = ecosystem.get("relationships") or []
    brand_id = brand_entity.get("id")

    tech_stack: dict[str, dict] = {}
    last_verified_dates: list[str] = []
    for rel in relationships:
        if not isinstance(rel, dict) or rel.get("from_entity_id") != brand_id:
            continue
        category = rel.get("category")
        if category not in _TECH_STACK_CATEGORIES or rel.get("status") != "active":
            continue
        vendor_entity = entities_by_id.get(rel.get("to_entity_id"))
        vendor_name = vendor_entity.get("name") if vendor_entity else rel.get("to_entity_id")
        tech_stack[category] = {
            "vendor": vendor_name,
            "status": "active",
            "source": rel.get("source"),
        }
        last_verified_dates.append(rel.get("updated_at") or rel.get("created_at") or "")

    narratives = brand_entity.get("strategic_narratives") or []
    active_narrative = next(
        (n for n in narratives
         if n.get("narrative_type") == "tech_stack_modernization"
         and n.get("status", "active") == "active"),
        None,
    )
    if active_narrative:
        sunset_categories = {
            s["category"] for s in active_narrative.get("supporting_signals", [])
            if s.get("signal_type") == "sunset"
        }
        for cat in sunset_categories - set(tech_stack):
            tech_stack[cat] = {"vendor": None, "status": "sunset", "source": None}
        if active_narrative.get("last_updated"):
            last_verified_dates.append(active_narrative["last_updated"])

    for cat in sorted(_TECH_STACK_CATEGORIES - set(tech_stack)):
        tech_stack[cat] = {"vendor": None, "status": "unknown", "source": None}

    last_verified_dates = [d for d in last_verified_dates if d]
    return {
        "available": True,
        "brand_id": brand_id,
        "brand_name": brand_entity.get("name"),
        "tech_stack": tech_stack,
        "strategic_narratives": narratives,
        "last_verified": max(last_verified_dates) if last_verified_dates else brand_entity.get("updated_at"),
    }


def _resolve_baseline_contact(exec_name: str, company: str, baseline: list[dict]) -> dict | None:
    """Find baseline contact matching an executive mention."""
    name_key = _slug(exec_name)
    company_lower = company.lower() if company else ""

    for c in baseline:
        cname_key = _slug(c.get("name") or "")
        if cname_key == name_key:
            return c
        # Partial name + company match
        cparts = (c.get("name") or "").lower().split()
        eparts = exec_name.lower().split()
        if eparts and cparts and eparts[-1] == cparts[-1]:
            cco = (c.get("current_company") or "").lower()
            if company_lower and (company_lower[:8] in cco or cco[:8] in company_lower):
                return c

    return None


# ---------------------------------------------------------------------------
# Stage 3 — Mutation Generation
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mutation_id(content: str) -> str:
    return "mut-" + hashlib.sha256(content.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Phase 5 (RB Unified Restaurant-Tech Graph, 2026-07-31): daily mutation
# workflow -- turns today's already-classified earnings/trade-press signal
# rows (Phase 4's provider_win/contract_renewal_expansion/vendor_churn_loss
# vocabulary) into vendor_customer_relationship mutations directly, rather
# than re-deriving brand/vendor/category from free text the way
# generate_mutations() does for arbitrary article text. The rows already
# carry a structured `company` and `category`, so this path is more reliable
# than re-running NLP extraction over a headline.
# ---------------------------------------------------------------------------

def _load_signal_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _find_known_vendors_in_text(text: str) -> list[dict]:
    """Return every KNOWN_VENDORS entry mentioned in text -- a churn/loss
    headline like 'replaces Presto with Hi Auto' names both the outgoing and
    incoming vendor, and both need their own mutation.

    Word-boundaried (not a bare substring check): a plain `vendor_name in
    text_lower` check would false-positive on short vendor keys like "olo"
    matching inside unrelated words ("techn-OLO-gy"), which a headline about
    "PAR Technology" hits directly.
    """
    text_lower = text.lower()
    found = []
    seen_ids: set[str] = set()
    for vendor_name, vendor_data in KNOWN_VENDORS.items():
        if vendor_data["id"] in seen_ids:
            continue
        if re.search(r"\b" + re.escape(vendor_name) + r"\b", text_lower):
            found.append({"name": vendor_name.title(), **vendor_data})
            seen_ids.add(vendor_data["id"])
    return found


def generate_mutations_from_signal_row(row: dict, *, ecosystem: dict, source_label: str) -> dict:
    """Turn one classified earnings_monitor.py/market_source_feeds.py row
    into vendor_customer_relationship mutation(s).

    Returns the same {"mutations", "auto_applied_mutations",
    "proposed_mutations"} shape generate_mutations() returns, so both feed
    apply_mutations() identically. Returns an empty set (no mutation) when:
    - the row's signal_type isn't in SIGNAL_TYPE_TO_RECONCILIATION_OUTCOME
      (not a vendor-relationship-lifecycle signal at all), or
    - the row names no company (an unnamed "major QSR" win, per the Unified
      Restaurant-Tech Graph decision, is kept as a signal only and never
      auto-promoted to a named relationship), or
    - the row's text names no KNOWN_VENDORS entry (nothing to attach the
      claim to).
    """
    empty = {"mutations": [], "auto_applied_mutations": [], "proposed_mutations": []}
    signal_type = row.get("signal_type")
    reconciliation_outcome = SIGNAL_TYPE_TO_RECONCILIATION_OUTCOME.get(signal_type)
    if reconciliation_outcome is None:
        return empty

    brand_name = (row.get("company") or "").strip()
    if not brand_name:
        return empty

    text = f"{row.get('title', '')} {row.get('pain_point_or_priority', '')}"
    vendors = _find_known_vendors_in_text(text)
    if not vendors:
        return empty

    brand_entity = _resolve_brand(brand_name, ecosystem)
    brand_id = brand_entity["id"] if brand_entity else f"brand-{_slug(brand_name)}"
    resolved_brand_name = brand_entity.get("name") if brand_entity else brand_name
    confidence = _CONFIDENCE_LABEL_TO_SCORE.get(row.get("confidence"), 0.5)
    source = {
        "title": row.get("title", ""),
        "url": row.get("url", ""),
        "date": (row.get("published_at") or date.today().isoformat()),
    }
    now = _now()

    mutations: list[dict] = []
    auto_applied: list[dict] = []
    proposed: list[dict] = []
    for vendor in vendors:
        category = row.get("category") or vendor.get("category") or "other"
        mut = {
            "id": _mutation_id(f"{brand_id}:{vendor['id']}:{category}:{signal_type}:{source['url']}"),
            "type": "vendor_customer_relationship",
            "description": f"{resolved_brand_name} {signal_type.replace('_', ' ')} — {vendor['name']} ({category})",
            "from_entity_id": brand_id,
            "brand_name": resolved_brand_name,
            "to_entity_id": vendor["id"],
            "relationship_type": "uses_vendor_for_category",
            "category": category,
            "confidence": confidence,
            "source": source,
            "created_at": now,
            # Same churn-always-requires-review rule as generate_mutations()'s
            # free-text path: a row can't tell which named vendor is the one
            # being dropped vs. the one taking over, so never auto-apply.
            "requires_confirmation": (
                True if reconciliation_outcome == "supersedes_existing_relationship"
                else confidence < 0.80
            ),
            "reconciliation_outcome": reconciliation_outcome,
            "signal_source": source_label,
        }
        mutations.append(mut)
        if not mut["requires_confirmation"] and confidence >= 0.75:
            auto_applied.append(mut)
        else:
            proposed.append(mut)

    return {"mutations": mutations, "auto_applied_mutations": auto_applied, "proposed_mutations": proposed}


def refresh_ecosystem_daily(*, dry_run: bool = False) -> dict:
    """Daily driver: read today's classified earnings/trade-press signal
    rows, convert mutation-worthy ones into ecosystem_intelligence.json
    mutations, apply them (reusing apply_mutations()'s existing conflict
    handling and confidence gate), and write a receipt.

    Idempotent: _mutation_id() is a deterministic hash of the row's brand,
    vendor, category, signal_type, and source URL, and apply_mutations()
    already skips any mutation id already present in the mutation log --
    so re-running against unchanged signal rows applies zero new mutations
    rather than duplicating them.

    Never raises -- a failure here must not block the morning brief; it is
    reported in the receipt's "status"/"error" fields instead.
    """
    receipt: dict[str, Any] = {
        "generated_at": _now(),
        "dry_run": dry_run,
        "rows_processed": 0,
        "rows_skipped_no_signal": 0,
        "mutations_generated": 0,
        "mutations_auto_applied": 0,
        "mutations_proposed": 0,
        "status": "ok",
    }
    try:
        ecosystem = _load_ecosystem()
        combined: dict[str, list] = {"mutations": [], "auto_applied_mutations": [], "proposed_mutations": []}
        for path, label in (
            (EARNINGS_SIGNALS_PATH, "earnings_monitor"),
            (MARKET_FEED_SIGNALS_PATH, "market_source_feeds"),
        ):
            for row in _load_signal_rows(path):
                receipt["rows_processed"] += 1
                result = generate_mutations_from_signal_row(row, ecosystem=ecosystem, source_label=label)
                if not result["mutations"]:
                    receipt["rows_skipped_no_signal"] += 1
                for key in combined:
                    combined[key].extend(result[key])

        receipt["mutations_generated"] = len(combined["mutations"])
        receipt["mutations_auto_applied"] = len(combined["auto_applied_mutations"])
        receipt["mutations_proposed"] = len(combined["proposed_mutations"])

        apply_result = apply_mutations(combined, dry_run=dry_run)
        receipt["applied"] = apply_result.get("applied", 0)
        receipt["apply_failed"] = apply_result.get("failed", 0)
        if apply_result.get("failed"):
            receipt["status"] = "degraded"
            receipt["apply_failed_details"] = apply_result.get("failed_details")
    except Exception as exc:  # noqa: BLE001 -- never fail the morning brief
        receipt["status"] = "failed"
        receipt["error"] = str(exc)

    try:
        ECOSYSTEM_MUTATION_RECEIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
        ECOSYSTEM_MUTATION_RECEIPT_PATH.write_text(json.dumps(receipt, indent=2, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass
    return receipt


def generate_mutations(
    text: str,
    source_title: str = "",
    source_url: str = "",
    source_date: str = "",
    source_author_name: str = "",
    source_author_org: str = "",
    source_author_role: str = "",
    *,
    ecosystem: dict | None = None,
    baseline: list[dict] | None = None,
    strategic_memory: dict | None = None,
) -> dict:
    """Core mutation generation from article text.

    Returns a mutation report with proposed and auto-applied mutations.
    """
    if ecosystem is None:
        ecosystem = _load_ecosystem()
    if baseline is None:
        baseline = _load_baseline()
    if strategic_memory is None:
        strategic_memory = _load_strategic_memory()

    entities = _extract_entities(text)
    source = {"title": source_title, "url": source_url, "date": source_date or date.today().isoformat()}
    now = _now()

    mutations: list[dict] = []
    auto_applied: list[dict] = []
    proposed: list[dict] = []

    # --- Mutation 1: Vendor-customer relationships ---
    for rel in entities["brand_vendor_relationships"]:
        brand_entity = _resolve_brand(rel["brand"], ecosystem)
        brand_id = brand_entity["id"] if brand_entity else f"brand-{_slug(rel['brand'])}"
        brand_name = brand_entity.get("name") if brand_entity else rel["brand"]

        reconciliation_outcome = rel.get("reconciliation_outcome", "new_relationship")
        if reconciliation_outcome not in eco.RECONCILIATION_OUTCOMES:
            reconciliation_outcome = "new_relationship"
        mut = {
            "id": _mutation_id(f"{brand_id}:{rel['vendor_id']}:{rel['category']}"),
            "type": "vendor_customer_relationship",
            "description": f"{brand_name} confirmed as {rel['vendor'].title()} customer ({rel['category']})",
            "from_entity_id": brand_id,
            "brand_name": brand_name,
            "to_entity_id": rel["vendor_id"],
            "relationship_type": "uses_vendor_for_category",
            "category": rel["category"],
            "confidence": rel["confidence"],
            "source": source,
            "created_at": now,
            # A churn/replacement sentence names both the outgoing and incoming
            # vendor together ("replaces Presto with Hi Auto") — this loop can't
            # tell from the sentence alone which named vendor is which, so an
            # auto-apply here risks writing the WRONG vendor as newly-active.
            # Always route churn-tagged mutations to human review rather than
            # guess; see resolve_and_upsert_relationship() for how a correctly-
            # identified new claim still auto-supersedes a weak prior one.
            "requires_confirmation": (
                True if reconciliation_outcome == "supersedes_existing_relationship"
                else rel["confidence"] < 0.80
            ),
            # RB Unified Restaurant-Tech Graph (2026-07-31): tags which of
            # Phase 2's reconciliation outcomes this mutation represents
            # (new_relationship / lifecycle_update / supersedes_existing_relationship)
            # based on win/renewal/churn language in the source sentence.
            "reconciliation_outcome": reconciliation_outcome,
        }
        mutations.append(mut)

    # --- Mutation 2: Executive POV capture ---
    for exec_mention in entities["exec_mentions"]:
        # Find quotes attributed to this executive
        exec_quotes = [
            q for q in entities["quotes"]
            if _slug(q["speaker"]) == _slug(exec_mention["name"])
            or exec_mention["name"].split()[-1].lower() in q["speaker"].lower()
        ]
        baseline_match = _resolve_baseline_contact(
            exec_mention["name"], exec_mention.get("company") or "", baseline
        )

        mut = {
            "id": _mutation_id(f"exec:{exec_mention['name']}:{source_title[:40]}"),
            "type": "executive_pov",
            "description": (
                f"{exec_mention['name']} ({exec_mention['title']}) "
                + (f"at {exec_mention['company']}" if exec_mention.get("company") else "")
            ),
            "executive": exec_mention,
            "quotes": exec_quotes,
            "baseline_contact_id": baseline_match.get("id") if baseline_match else None,
            "confidence": exec_mention["confidence"],
            "source": source,
            "created_at": now,
            "requires_confirmation": not baseline_match,
        }
        mutations.append(mut)

    # --- Mutation 3: Thesis validation ---
    sm_signals = strategic_memory.get("signals") or []
    sm_themes = {s.get("theme") or s.get("categories", [""])[0]: s for s in sm_signals}

    for ts in entities["thesis_signals"]:
        theme = ts["theme"]
        existing = sm_themes.get(theme)

        mut = {
            "id": _mutation_id(f"thesis:{theme}:{source_title[:40]}"),
            "type": "thesis_validation",
            "description": f"Thesis '{theme}' strengthened — article provides supporting evidence",
            "theme": theme,
            "direction": ts["direction"],
            "matched_keywords": ts["matched_keywords"],
            "existing_thesis_id": existing.get("id") if existing else None,
            "confidence": ts["confidence"],
            "confidence_delta": "+evidence" if ts["direction"] == "supports" else "-evidence",
            "source": source,
            "created_at": now,
            "requires_confirmation": False,  # Thesis validation auto-applies
        }
        mutations.append(mut)

    # --- Mutation 4: Known-person thesis alignment and engagement opportunity ---
    author_match = (
        _resolve_baseline_contact(source_author_name, source_author_org, baseline)
        if source_author_name else None
    )
    if author_match and entities["thesis_signals"]:
        themes = sorted({signal["theme"] for signal in entities["thesis_signals"]})
        alignment_strength = max(
            float(signal.get("confidence") or 0.5)
            for signal in entities["thesis_signals"]
        )
        person_id = author_match.get("id")
        alignment_id = _mutation_id(
            f"alignment:{person_id}:{','.join(themes)}:{source_url or source_title}"
        )
        mutations.append({
            "id": alignment_id,
            "type": "thesis_alignment_detected",
            "description": (
                f"{source_author_name} independently reinforced Todd's "
                f"{', '.join(themes)} thesis"
            ),
            "person_id": person_id,
            "person_name": author_match.get("name") or source_author_name,
            "person_org": source_author_org or author_match.get("current_company"),
            "person_role": source_author_role or author_match.get("current_role"),
            "thesis_ids": themes,
            "alignment_strength": alignment_strength,
            "affinity_score_delta": 0.10 if alignment_strength >= 0.80 else 0.05,
            "relationship_tag": "thought-partner",
            "confidence": alignment_strength,
            "source": source,
            "created_at": now,
            "requires_confirmation": alignment_strength < 0.75,
        })
        mutations.append({
            "id": _mutation_id(f"engagement:{alignment_id}"),
            "type": "engagement_opportunity",
            "description": (
                f"Engage {source_author_name} on shared thesis: {', '.join(themes)}"
            ),
            "opportunity_type": "engagement_opportunity",
            "basis": "thesis_reinforcement",
            "person_id": person_id,
            "person_name": author_match.get("name") or source_author_name,
            "thesis_ids": themes,
            "suggested_action": (
                f"Respond to or reference {source_author_name}'s post with Todd's "
                f"point of view on {themes[0].replace('_', ' ')}."
            ),
            "confidence": alignment_strength,
            "source": source,
            "created_at": now,
            "requires_confirmation": alignment_strength < 0.75,
        })

    # Hoisted out of the vendor loop (was previously recomputed per-vendor and
    # undefined when no vendors were detected — RB-DEFECT-032's contradiction
    # detector below needs it unconditionally).
    try:
        import yaml as _yaml
        at_path = core.SYSTEM_DIR / "active_threads.yaml"
        at_data = _yaml.safe_load(at_path.read_text()) if at_path.exists() else {}
        threads = at_data.get("threads") if isinstance(at_data, dict) else []
        thread_cos = {
            co.lower()
            for t in (threads or [])
            for co in (t.get("companies") or [])
        }
    except Exception:
        thread_cos = set()

    # --- Mutation 5: Entity profile expansion (new vendor facts) ---
    for vendor in entities["vendors_detected"]:
        vendor_name = vendor["name"].lower()
        is_watchlist = any(vendor_name in co or co in vendor_name for co in thread_cos)

        if is_watchlist:
            mut = {
                "id": _mutation_id(f"vendor-signal:{vendor['id']}:{source_title[:40]}"),
                "type": "watchlist_signal",
                "description": f"Watchlist vendor {vendor['name']} mentioned in article — review for opportunity signal",
                "vendor_id": vendor["id"],
                "vendor_name": vendor["name"],
                "confidence": vendor["confidence"],
                "source": source,
                "created_at": now,
                "requires_confirmation": False,
            }
            mutations.append(mut)

    # --- Mutation 6 (RB-DEFECT-032): Contradictory opportunity signal ---
    # Detects when trusted-source content names a watchlist company AND
    # carries contradiction-cue language ("couldn't find", "no evidence of",
    # etc.) — exactly the shape of Jeff Coffland's SMS ("I search McDonald's
    # files and couldn't find any market with Foods Connected as a
    # provider"). Heuristic, not deep NLP — but it's the mechanism that was
    # entirely absent, and it lands in the same structured home
    # (opportunity.contradictory_signals, RB-DEFECT-032 schema addition)
    # rather than being silently dropped.
    _CONTRADICTION_CUES = (
        "couldn't find", "could not find", "no evidence of", "no record of",
        "doesn't appear", "does not appear", "not listed", "no sign of",
        "contrary to", "didn't find", "did not find", "unable to find",
    )
    _text_lower = text.lower()
    _cue_hit = next((cue for cue in _CONTRADICTION_CUES if cue in _text_lower), None)
    def _display_company_name(name: str) -> str:
        # Title-case word-by-word but don't mangle possessives like
        # "mcdonald's" -> "Mcdonald'S"; capitalize only the leading letter
        # of each apostrophe-delimited segment within a word.
        def _cap_word(w: str) -> str:
            parts = w.split("'")
            parts[0] = parts[0][:1].upper() + parts[0][1:]
            return "'".join(parts)
        return " ".join(_cap_word(w) for w in name.split(" "))

    if _cue_hit and thread_cos:
        for company in thread_cos:
            if company and company in _text_lower:
                source_trust = (
                    "high" if author_match and author_match.get("rc_tier") in {"core", "inner_circle"}
                    else "medium" if author_match else "unknown"
                )
                contradiction_confidence = (
                    0.65 if author_match else 0.45  # below auto-apply threshold either way —
                )                                    # contradictions always require human review
                sig_id = _mutation_id(f"contradiction:{company}:{source_url or source_title}")
                mutations.append({
                    "id": sig_id,
                    "type": "contradictory_opportunity_signal",
                    "description": (
                        f"{source_author_name or 'A trusted source'} reported evidence "
                        f"potentially contradicting the {_display_company_name(company)} opportunity thesis"
                    ),
                    "company_name": company,
                    "source_person": source_author_name or "unknown",
                    "source_person_id": (author_match or {}).get("id"),
                    "source_trust": source_trust,
                    "signal": text.strip(),
                    "interpretation": (
                        "Potential disconnect between perceived footprint and actual "
                        "deployment footprint — requires validation, not assumption."
                    ),
                    "alternative_explanations": [
                        "Supplier-side relationship rather than the assumed relationship type",
                        "Local-market deployment not visible in the source's vantage point",
                        "Future growth target rather than existing business",
                    ],
                    "recommended_validation": [
                        "Cross-check with other relationship-graph contacts at the target company",
                        "Raise directly in next interview/conversation round",
                    ],
                    "confidence": contradiction_confidence,
                    "source": source,
                    "created_at": now,
                    "requires_confirmation": True,  # contradictions are never auto-applied
                })

    # --- Mutation 7 (RB 9.89): category lifecycle signal -> strategic narrative ---
    # "Hungry Howie's is sunsetting its loyalty program" — a tech-stack
    # category event with no named replacement vendor yet. Feeds
    # update_strategic_narratives() in apply_mutations() so it accumulates
    # alongside vendor_customer_relationship signals for the same brand.
    article_subject_for_lifecycle = entities["article_subject"]
    if entities["category_lifecycle_signals"] and article_subject_for_lifecycle:
        brand_entity = _resolve_brand(article_subject_for_lifecycle, ecosystem)
        brand_id = brand_entity["id"] if brand_entity else f"brand-{_slug(article_subject_for_lifecycle)}"
        brand_name = brand_entity.get("name") if brand_entity else article_subject_for_lifecycle
        for sig in entities["category_lifecycle_signals"]:
            label = _CATEGORY_LABELS.get(sig["category"], sig["category"])
            mutations.append({
                "id": _mutation_id(f"{brand_id}:{sig['category']}:{sig['signal_type']}:{source_title[:40]}"),
                "type": "category_lifecycle_signal",
                "description": f"{brand_name} {sig['signal_type']} of {label}",
                "from_entity_id": brand_id,
                "brand_name": brand_name,
                "category": sig["category"],
                "signal_type": sig["signal_type"],
                "sentence_evidence": sig["sentence_evidence"],
                "confidence": sig["confidence"],
                "source": source,
                "created_at": now,
                "requires_confirmation": False,
            })

    # Stage 4 — Confidence triage
    for mut in mutations:
        conf = mut.get("confidence", 0.5)
        if not mut.get("requires_confirmation") and conf >= 0.75:
            auto_applied.append(mut)
        else:
            proposed.append(mut)

    # --- RB 9.90 (RB-DEFECT-046 Slice 3): enrich-before-comment ---
    # For every brand touched by a vendor-relationship or category-lifecycle
    # mutation, surface "what RB already knew" (existing tech stack +
    # strategic narrative, BEFORE this article's mutations are applied) so
    # callers can correlate new signals against accumulated context instead
    # of summarizing each article in isolation.
    enrichment: dict[str, dict] = {}
    for mut in mutations:
        if mut["type"] not in ("vendor_customer_relationship", "category_lifecycle_signal"):
            continue
        brand_id = mut["from_entity_id"]
        if brand_id in enrichment:
            continue
        enrichment[brand_id] = build_company_intelligence_file(mut["brand_name"], ecosystem)

    # Trust stats
    trust_stats = {
        "sources_assessed": 1,
        "vendors_detected": len(entities["vendors_detected"]),
        "brand_vendor_relationships": len(entities["brand_vendor_relationships"]),
        "category_lifecycle_signals": len(entities["category_lifecycle_signals"]),
        "executive_mentions": len(entities["exec_mentions"]),
        "quotes_extracted": len(entities["quotes"]),
        "thesis_signals": len(entities["thesis_signals"]),
        "known_author_resolved": bool(author_match),
        "relationship_mutations": sum(
            m["type"] == "thesis_alignment_detected" for m in mutations
        ),
        "opportunities_generated": sum(
            m["type"] == "engagement_opportunity" for m in mutations
        ),
        "total_mutations": len(mutations),
        "auto_applied": len(auto_applied),
        "proposed_pending_confirmation": len(proposed),
        "confidence": "high" if len(mutations) > 3 else "medium" if mutations else "low",
        "trust_contract_met": len(mutations) > 0,
    }

    result = {
        "source_title": source_title,
        "source_url": source_url,
        "generated_at": now,
        "trust_stats": trust_stats,
        "extracted_entities": entities,
        "enrichment": enrichment,
        "mutations": mutations,
        "auto_applied_mutations": auto_applied,
        "proposed_mutations": proposed,
    }

    return result


# ---------------------------------------------------------------------------
# Stage 5 — Persistence
# ---------------------------------------------------------------------------

def apply_mutations(mutation_result: dict, *, dry_run: bool = False) -> dict:
    """Persist auto_applied mutations to the knowledge stores."""
    auto = mutation_result.get("auto_applied_mutations") or []
    applied: list[str] = []
    failed: list[str] = []
    now = _now()

    if dry_run:
        return {"dry_run": True, "would_apply": len(auto), "mutations": [m["id"] for m in auto]}

    # Load stores
    ecosystem = _load_ecosystem()
    exec_povs = json.loads(EXEC_POV_PATH.read_text()) if EXEC_POV_PATH.exists() else {"povs": []}
    mutation_log = json.loads(MUTATION_LOG_PATH.read_text()) if MUTATION_LOG_PATH.exists() else {"mutations": []}
    opportunities = (
        json.loads(ENGAGEMENT_OPPORTUNITIES_PATH.read_text())
        if ENGAGEMENT_OPPORTUNITIES_PATH.exists()
        else {"opportunities": []}
    )
    baseline = _load_baseline()
    baseline_changed = False
    narratives_updated = 0

    existing_log_ids = {m["id"] for m in mutation_log.get("mutations", [])}

    for mut in auto:
        mid = mut["id"]
        if mid in existing_log_ids:
            continue  # already applied

        mtype = mut["type"]
        try:
            if mtype == "vendor_customer_relationship":
                # Add to ecosystem_intelligence relationships
                rels = ecosystem.get("relationships") or []
                if not isinstance(rels, list):
                    rels = []
                ecosystem["relationships"] = rels
                existing_rel_ids = {r.get("id") for r in rels if isinstance(r, dict)}
                # RB-2026-08-28: confirmed live -- `mid` (from _mutation_id())
                # is always "mut-<hash>", but the schema requires a
                # relationship id to match "^rel-[a-z0-9][a-z0-9-]{1,160}$".
                # This path has therefore never written a schema-valid
                # relationship id either. Build a real, deterministic
                # "rel-<from>-<category>-<to>" id instead (same shape as
                # existing real relationship records, e.g.
                # "rel-brand-mcdonald-s-pos-system-of-record-pos-vendor-newpos"),
                # so re-applying the same logical relationship is still
                # idempotent.
                rel_id = f"rel-{mut['from_entity_id']}-{mut.get('category') or 'general'}-{mut['to_entity_id']}"
                if rel_id not in existing_rel_ids:
                    # RB-2026-08-28: confirmed live -- this dict previously
                    # included "source" (singular, a nested object) and
                    # "auto_applied", neither of which
                    # schemas/ecosystem_intelligence.schema.json's relationship
                    # definition allows (additionalProperties: false). Every
                    # vendor_customer_relationship mutation through this path
                    # therefore failed schema validation on write -- silently
                    # corrupting the live file before the write-then-validate
                    # rollback fix above existed, or (after that fix) silently
                    # failing outright. mut["source"]'s real content is not
                    # lost: it's already captured in `sources` below, and more
                    # fully in `source_assertions`, which the schema does
                    # allow and existing real relationship records also use.
                    conf_score = mut.get("confidence")
                    if not isinstance(conf_score, (int, float)):
                        conf_score = 0.5
                    # 2026-09-25 (Confidence-Based Auto-Recording): capped at
                    # 0.5 ("medium" band -- the same score confidence_
                    # calibration.py gives an uncalibrated/generic source
                    # like "credible_trade_reporting"), never higher, no
                    # matter how definitively the source TEXT reads.
                    # mut["confidence"] here comes from _extract_entities()'s
                    # sentence-language heuristic ("has selected... to power
                    # ... nationwide" scores 0.85) -- that measures how
                    # confidently a SENTENCE is worded, not how reliable its
                    # SOURCE is; this module has no source_type
                    # classification at all (unlike ei.vendor_relationship()'s
                    # SOURCE_QUALITY_MODEL). check_relationship_conflict() now
                    # compares this score directly against real, source-
                    # calibrated scores elsewhere in the graph -- letting a
                    # confidently-WORDED but unverified article auto-
                    # supersede an already-established incumbent would be
                    # exactly the confident-sounding-but-unsourced failure
                    # mode Todd's design is meant to guard against. Confirmed
                    # live: without this cap, "Papa John's has selected PAR
                    # Technology..." (0.85, an "Unverified trade blurb" per
                    # its own source_title) silently superseded a confirmed
                    # NCR incumbent.
                    conf_score = min(conf_score, 0.5)
                    conf_level = "high" if conf_score >= 0.8 else "medium" if conf_score >= 0.55 else "low"
                    new_rel = {
                        "id": rel_id,
                        "from_entity_id": mut["from_entity_id"],
                        "to_entity_id": mut["to_entity_id"],
                        "relationship_type": mut["relationship_type"],
                        "category": mut.get("category"),
                        "status": "active",
                        "evidence_posture": "provisional",
                        "confidence": {
                            "level": conf_level, "score": conf_score,
                            "rationale": "Applied via intelligence_mutation_engine vendor_customer_relationship mutation.",
                        },
                        "sources": [mut["source"].get("url") or mut["source"].get("title") or "unknown"],
                        "source_assertions": [{
                            "source_id": _slug(mut["source"].get("title") or mut["source"].get("url") or mid),
                            "url": mut["source"].get("url") or None,
                            "title": mut["source"].get("title") or None,
                            "published_at": mut["source"].get("date"),
                            "discovered_at": now[:10],
                            "paraphrase": mut.get("description", ""),
                            "posture": "current",
                        }],
                        "created_at": now,
                        "updated_at": now,
                    }
                    # Conflict-check against whatever is already on file for this
                    # brand+category before writing — an article asserting a new
                    # vendor relationship must not silently overwrite or coexist
                    # unflagged with a rival vendor's existing live claim. See
                    # ecosystem_intelligence.check_relationship_conflict().
                    eco.resolve_and_upsert_relationship(ecosystem, new_rel)

                # RB 9.89: accumulate into the brand's tech_stack_modernization
                # strategic narrative.
                brand_entity = _get_or_create_brand_entity(
                    ecosystem, mut["from_entity_id"], mut["brand_name"], now,
                )
                if update_strategic_narratives(
                    brand_entity,
                    category=mut.get("category"),
                    signal_type="vendor_selected",
                    description=mut["description"],
                    source=mut["source"],
                    now=now,
                ):
                    narratives_updated += 1

            elif mtype == "category_lifecycle_signal":
                brand_entity = _get_or_create_brand_entity(
                    ecosystem, mut["from_entity_id"], mut["brand_name"], now,
                )
                if update_strategic_narratives(
                    brand_entity,
                    category=mut["category"],
                    signal_type=mut["signal_type"],
                    description=mut["description"],
                    source=mut["source"],
                    now=now,
                ):
                    narratives_updated += 1

            elif mtype == "executive_pov":
                # Store executive POV
                exec_povs.setdefault("povs", []).append({
                    "id": mid,
                    "executive": mut["executive"],
                    "quotes": mut["quotes"],
                    "baseline_contact_id": mut.get("baseline_contact_id"),
                    "source": mut["source"],
                    "created_at": now,
                })

            elif mtype in ("thesis_validation", "watchlist_signal"):
                pass  # Logged in mutation_log, no structural store update needed

            elif mtype == "thesis_alignment_detected":
                contact = next(
                    (row for row in baseline if row.get("id") == mut.get("person_id")),
                    None,
                )
                if contact:
                    tags = contact.setdefault("tags", [])
                    if mut["relationship_tag"] not in tags:
                        tags.append(mut["relationship_tag"])
                    current_affinity = float(contact.get("affinity_score") or 0.0)
                    contact["affinity_score"] = round(
                        min(1.0, current_affinity + mut["affinity_score_delta"]), 2
                    )
                    note = (
                        f"[{now[:10]}] Thesis alignment: "
                        f"{', '.join(mut['thesis_ids'])} ({mut['source'].get('title')})."
                    )
                    if note not in (contact.get("notes") or ""):
                        contact["notes"] = (
                            ((contact.get("notes") or "").rstrip() + "\n" + note).strip()
                        )
                    baseline_changed = True

            elif mtype == "engagement_opportunity":
                existing_opportunities = {
                    row.get("id") for row in opportunities.get("opportunities", [])
                }
                if mid not in existing_opportunities:
                    opportunities.setdefault("opportunities", []).append({
                        **mut,
                        "status": "open",
                        "recorded_at": now,
                    })

            mutation_log.setdefault("mutations", []).append({
                "id": mid,
                "type": mtype,
                "description": mut["description"],
                "applied_at": now,
                "source_title": mut["source"].get("title"),
                "source_url": mut["source"].get("url"),
                **{
                    key: mut[key]
                    for key in (
                        "person_id", "person_name", "thesis_ids",
                        "alignment_strength", "affinity_score_delta",
                        "opportunity_type", "basis", "suggested_action",
                    )
                    if key in mut
                },
            })
            applied.append(mid)

        except Exception as exc:
            failed.append(f"{mid}: {exc}")

    # Write stores
    try:
        if narratives_updated or any(
            m["type"] in ("vendor_customer_relationship", "category_lifecycle_signal")
            for m in auto
        ):
            # RB Unified Restaurant-Tech Graph (2026-07-31), Phase 5: snapshot
            # before write and validate after write -- the same pre-write
            # snapshot + schema-validation gate Phase 2's workbook migration
            # tool already has (eco._write_graph()), reimplemented here against
            # core.SYSTEM_DIR directly (rather than calling eco._write_graph()
            # itself) because that function's own module-level VALIDATOR /
            # core.ECOSYSTEM_INTELLIGENCE_PATH constants are bound once at
            # import time and don't observe a test's core.SYSTEM_DIR patch.
            ecosystem_path = core.SYSTEM_DIR.joinpath("ecosystem_intelligence.json")
            snapshot_path = None
            if ecosystem_path.exists():
                snapshots_dir = core.SYSTEM_DIR / "_snapshots"
                snapshots_dir.mkdir(parents=True, exist_ok=True)
                # RB defect 2026-10-08: same second-resolution collision as
                # ecosystem_intelligence.py's own _write_graph() -- two
                # writes in the same wall-clock second silently overwrite
                # each other's snapshot. Microsecond resolution here too.
                tag = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
                snapshot_path = snapshots_dir / f"ecosystem_intelligence.pre-write-{tag}.json"
                shutil.copy2(ecosystem_path, snapshot_path)
            ecosystem["last_updated"] = now
            ecosystem_path.write_text(json.dumps(ecosystem, indent=2, default=str) + "\n")
            validator = core.SYSTEM_DIR / "schemas" / "validate.py"
            if validator.exists():
                # RB-2026-09-27: was `--ecosystem-only`, which makes the
                # validator subprocess check its own hardcoded default path
                # instead of `ecosystem_path` above -- the same
                # subprocess-boundary blind spot this function's own
                # comment already flagged for core.SYSTEM_DIR (a fresh
                # subprocess re-imports rb_core and never sees a test's
                # patch). Pass the path actually written, plus its schema,
                # explicitly instead of relying on the flag's default.
                ecosystem_schema = core.SYSTEM_DIR / "schemas" / "ecosystem_intelligence.schema.json"
                vrc = subprocess.run(
                    [sys.executable or "python3", str(validator), str(ecosystem_path), "--schema", str(ecosystem_schema)],
                    capture_output=True, text=True,
                )
                if vrc.returncode != 0:
                    # RB-2026-08-28: confirmed live -- this write-then-validate
                    # sequence writes ecosystem_intelligence.json to disk BEFORE
                    # validating it. A validation failure used to just raise
                    # here, which the except block below logs as "failed" --
                    # but the invalid file was already persisted by that point,
                    # with no rollback. A caller seeing "failed" reasonably
                    # assumes nothing changed; in reality the live production
                    # file was left corrupted (caught only because a snapshot
                    # happened to exist and was checked by hand). The snapshot
                    # this function already takes above was never used to
                    # recover -- restore it now, before raising, so "failed"
                    # actually means "nothing changed" again.
                    if snapshot_path is not None:
                        shutil.copy2(snapshot_path, ecosystem_path)
                    raise RuntimeError(
                        "ecosystem validation failed -- write rolled back to pre-write snapshot: "
                        + (vrc.stderr or vrc.stdout or "")
                    )
        EXEC_POV_PATH.parent.mkdir(parents=True, exist_ok=True)
        EXEC_POV_PATH.write_text(json.dumps(exec_povs, indent=2, default=str) + "\n")
        MUTATION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        MUTATION_LOG_PATH.write_text(json.dumps(mutation_log, indent=2, default=str) + "\n")
        if baseline_changed:
            core.BASELINE_PATH.write_text(
                json.dumps(baseline, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
        ENGAGEMENT_OPPORTUNITIES_PATH.write_text(
            json.dumps(opportunities, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        failed.append(f"store_write: {exc}")

    return {
        "applied": len(applied),
        "failed": len(failed),
        "failed_details": failed,
        "applied_ids": applied,
        "narratives_updated": narratives_updated,
    }


# ---------------------------------------------------------------------------
# Brief block builder
# ---------------------------------------------------------------------------

def build_mutation_brief_block() -> dict:
    """Return today's knowledge mutations for the daily brief section."""
    if not MUTATION_LOG_PATH.exists():
        return {"available": False, "reason": "no mutations logged yet"}

    try:
        log = json.loads(MUTATION_LOG_PATH.read_text())
    except Exception:
        return {"available": False, "reason": "mutation log unreadable"}

    mutations = log.get("mutations") or []
    today = datetime.now(timezone.utc).date().isoformat()
    today_mutations = [m for m in mutations if (m.get("applied_at") or "")[:10] == today]

    # Group by type
    by_type: dict[str, list[dict]] = {}
    for m in today_mutations:
        t = m.get("type", "other")
        by_type.setdefault(t, []).append(m)

    # Conflicts detected today (see ecosystem_intelligence.check_relationship_conflict).
    # Read straight from the conflict queue rather than the mutation log — a
    # conflict can be recorded there from either the article-ingestion path
    # (this module) or the structured file-ingestion path (ecosystem_intelligence.py).
    conflicts_today: list[dict] = []
    conflict_queue_path = core.CONFLICT_QUEUE_PATH
    if conflict_queue_path.exists():
        for line in conflict_queue_path.read_text().splitlines():
            try:
                record = json.loads(line)
            except Exception:
                continue
            if (record.get("detected_at") or "")[:10] == today:
                conflicts_today.append(record)
    # 2026-09-25 (Confidence-Based Auto-Recording): check_relationship_
    # conflict() no longer ever produces "requires_confirmation" -- every
    # conflict today is either "auto_superseded" (a stronger claim replaced
    # a weaker one) or "recorded_alongside" (a real, sourced, confidence-
    # scored claim that didn't beat the incumbent, but was recorded, not
    # blocked). conflicts_recorded_alongside replaces conflicts_requiring_
    # confirmation -- nothing here is ever "waiting on a human."
    recorded_alongside = [
        c for c in conflicts_today if c.get("resolution") == "recorded_alongside"
    ]

    return {
        "available": True,
        "total_mutations_today": len(today_mutations),
        "vendor_customer_relationships": len(by_type.get("vendor_customer_relationship", [])),
        "executive_povs": len(by_type.get("executive_pov", [])),
        "thesis_validations": len(by_type.get("thesis_validation", [])),
        "watchlist_signals": len(by_type.get("watchlist_signal", [])),
        "relationship_alignments": len(by_type.get("thesis_alignment_detected", [])),
        "engagement_opportunities": len(by_type.get("engagement_opportunity", [])),
        "category_lifecycle_signals": len(by_type.get("category_lifecycle_signal", [])),
        "strategic_narratives_updated": len(by_type.get("vendor_customer_relationship", []))
        + len(by_type.get("category_lifecycle_signal", [])),
        "conflicts_detected": len(conflicts_today),
        "conflicts_recorded_alongside": len(recorded_alongside),
        "conflicts": conflicts_today,
        "mutations": today_mutations,
    }


def run(
    text: str,
    source_title: str = "",
    source_url: str = "",
    source_date: str = "",
    source_author_name: str = "",
    source_author_org: str = "",
    source_author_role: str = "",
    *,
    auto_apply: bool = True,
    dry_run: bool = False,
) -> dict:
    """Full pipeline: generate mutations and optionally apply them."""
    result = generate_mutations(
        text,
        source_title,
        source_url,
        source_date,
        source_author_name=source_author_name,
        source_author_org=source_author_org,
        source_author_role=source_author_role,
    )
    if auto_apply and not dry_run:
        apply_result = apply_mutations(result, dry_run=False)
        result["apply_result"] = apply_result
    elif dry_run:
        result["apply_result"] = {"dry_run": True}
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_result(result: dict) -> None:
    ts = result.get("trust_stats") or {}
    print(f"\n=== Intelligence Mutation Engine ===")
    print(f"Source: {result.get('source_title','?')}")
    print(f"Trust: {ts.get('confidence','?')} | Mutations: {ts.get('total_mutations',0)} "
          f"({ts.get('auto_applied',0)} auto-applied, {ts.get('proposed_pending_confirmation',0)} proposed)")
    print()

    entities = result.get("extracted_entities") or {}
    if entities.get("brand_vendor_relationships"):
        print("Vendor-Customer Relationships Extracted:")
        for r in entities["brand_vendor_relationships"]:
            print(f"  {r['brand']} → {r['vendor']} ({r['category']}) conf={r['confidence']:.0%}")

    if entities.get("exec_mentions"):
        print("\nExecutive Mentions:")
        for e in entities["exec_mentions"]:
            print(f"  {e['name']} — {e['title']}" + (f" at {e['company']}" if e.get('company') else ""))

    if entities.get("quotes"):
        print("\nQuotes Extracted:")
        for q in entities["quotes"][:3]:
            print(f"  {q['speaker']}: \"{q['quote'][:100]}…\"")

    if entities.get("thesis_signals"):
        print("\nThesis Signals:")
        for ts in entities["thesis_signals"]:
            print(f"  [{ts['direction']}] {ts['theme']} conf={ts['confidence']:.0%} "
                  f"— {', '.join(ts['matched_keywords'][:3])}")

    ar = result.get("apply_result") or {}
    if ar:
        print(f"\nApplied: {ar.get('applied',0)} | Failed: {ar.get('failed',0)}")


def _print_company_file(result: dict) -> None:
    if not result.get("available"):
        print(f"No Company Intelligence File: {result.get('reason')}")
        return
    print(f"\n=== Company Intelligence File: {result['brand_name']} ===")
    print("\nTechnology Stack")
    for category, info in result["tech_stack"].items():
        label = _CATEGORY_LABELS.get(category, category)
        if info["status"] == "active":
            print(f"  {label}: {info['vendor']}")
        elif info["status"] == "sunset":
            print(f"  {label}: (sunset) — replacement unknown")
        else:
            print(f"  {label}: Unknown")
    print(f"  Last Verified: {result.get('last_verified') or 'unknown'}")

    for narrative in result.get("strategic_narratives") or []:
        print(f"\nStrategic Narrative: {narrative['narrative_type']}")
        print(f"  Confidence: {narrative['confidence']}")
        sigs = "; ".join(s["description"] for s in narrative.get("supporting_signals", []))
        print(f"  Supporting Signals: {sigs}")
        if narrative.get("next_expected_signals"):
            print(f"  Next Expected Signals: {'; '.join(narrative['next_expected_signals'])}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--text", metavar="TEXT", help="Article text to process.")
    g.add_argument("--triage-file", metavar="PATH", help="Path to triage JSON file.")
    g.add_argument("--brief-block", action="store_true", help="Show today's mutation brief block.")
    g.add_argument("--company-file", metavar="ENTITY_NAME",
                    help="Show the Company Intelligence File (tech stack + strategic narratives) for a brand.")
    g.add_argument("--refresh-ecosystem", action="store_true",
                    help="Daily mutation workflow (RB Unified Restaurant-Tech Graph, 2026-07-31): "
                         "convert today's classified earnings/trade-press signal rows into "
                         "ecosystem_intelligence.json mutations and write a receipt.")
    p.add_argument("--source-title", default="")
    p.add_argument("--source-url", default="")
    p.add_argument("--auto-apply", action="store_true", default=True)
    p.add_argument("--no-apply", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.brief_block:
        result = build_mutation_brief_block()
    elif args.refresh_ecosystem:
        result = refresh_ecosystem_daily(dry_run=args.dry_run)
    elif args.company_file:
        result = build_company_intelligence_file(args.company_file)
    elif args.triage_file:
        triage = json.loads(Path(args.triage_file).read_text())
        text = triage.get("input_text") or triage.get("text") or ""
        result = run(text, args.source_title, args.source_url,
                     auto_apply=not args.no_apply, dry_run=args.dry_run)
    else:
        result = run(args.text, args.source_title, args.source_url,
                     auto_apply=not args.no_apply, dry_run=args.dry_run)

    if args.json or args.refresh_ecosystem:
        print(json.dumps(result, indent=2, default=str))
    elif args.company_file:
        _print_company_file(result)
    else:
        _print_result(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
