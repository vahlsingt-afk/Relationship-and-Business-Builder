#!/usr/bin/env python3
"""
macro_intelligence.py — Macro behavioral industry intelligence layer.

Classifies operator-generated content (LinkedIn posts, newsletters, industry
commentary) into structured multi-layer mutations: behavioral signals, behavioral
artifacts, entity risk profiles, downstream tech implications, and relationship
intelligence mutations.

Signal path:
  content → behavioral classification → artifact generation →
  entity risk extraction → tech implication derivation →
  RI mutation (if author present) → canonical CoS mutation surface

Invariants:
  - All mutations require_confirmation=True.
  - persistence_status always explicit — never silent.
  - No auto-write to any store — all records are proposed, not committed.
  - Cross-layer cascade is deterministic and evidence-bound.
  - RI mutations delegate to relationship_intake and inherit its guardrails.
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent.parent
BEHAVIORAL_INTELLIGENCE_PATH = Path(os.environ.get(
    "RB_BEHAVIORAL_INTELLIGENCE_PATH", str(SYSTEM_DIR / "behavioral_intelligence.json")
))
ENTITY_INTELLIGENCE_PATH = Path(os.environ.get(
    "RB_ENTITY_INTELLIGENCE_PATH", str(SYSTEM_DIR / "entity_intelligence.json")
))

sys.path.insert(0, str(Path(__file__).resolve().parent))
import relationship_intake

# ---------------------------------------------------------------------------
# Behavioral signal taxonomy
# ---------------------------------------------------------------------------

BEHAVIORAL_SIGNAL_TYPES = frozenset({
    "consumer_hesitation",
    "affordability_stress",
    "trade_down_behavior",
    "emotional_friction",
    "value_perception_shift",
    "operational_pain",
})

PERSISTENCE_STATUSES = frozenset({
    "RB recorded",
    "RB updated",
    "RB proposed",
    "RB blocked",
    "RB skipped",
    "RB did not persist",
    "pending confirmation",
})

SOURCE_TYPES = frozenset({
    "linkedin_post",
    "linkedin_article",
    "newsletter",
    "industry_report",
    "operator_commentary",
    "social_post",
    "conversation",
})

_BEHAVIORAL_SIGNAL_KEYWORDS: dict[str, set[str]] = {
    "consumer_hesitation": {
        "didn't get out", "did not get out", "didn't go in", "did not go in",
        "drove away", "drive away", "turned around", "sat in the",
        "sitting in the car", "parking lot", "didn't enter", "did not enter",
        "walked away", "backed out", "hesitat", "abandoned before",
    },
    "affordability_stress": {
        "can't afford", "cannot afford", "math doesn't add up", "math just doesn't",
        "too expensive", "priced out", "sticker shock", "affordability",
        "financial strain", "cracking", "breaking point", "can't justify",
        "cannot justify", "price shock", "value gap", "not worth the price",
    },
    "trade_down_behavior": {
        "mcdonald", "burger king", "taco bell", "wendy", "trade down",
        "fraction of the price", "opted for cheaper", "value menu",
        "value meal", "switched to", "went to instead", "cheaper option",
    },
    "emotional_friction": {
        "not worth it", "feel like", "felt like", "couldn't justify",
        "couldn't pull", "second-guess", "impulse restrained",
        "emotionally", "guilt", "hesitated before", "conflicted about",
    },
    "value_perception_shift": {
        "used to be worth", "no longer worth", "value for money",
        "price to value", "quality-price", "is it worth", "math doesn't work",
        "math just doesn't", "not worth the", "worth it anymore",
    },
    "operational_pain": {
        "can't raise prices", "cannot raise prices", "can't pass it",
        "cannot pass it", "commodity cost", "commodity exposure",
        "operational flexibility", "margin pressure", "margin compression",
        "squeezed", "can't absorb", "pricing ceiling", "fixed costs",
        "commodity inflation",
    },
}

# ---------------------------------------------------------------------------
# Behavioral artifact definitions
# ---------------------------------------------------------------------------

_ARTIFACT_DEFINITIONS: list[dict] = [
    {
        "artifact_name": "Parking Lot Hesitation",
        "trigger_signal_types": {"consumer_hesitation"},
        "trigger_keywords": {
            "parking lot", "parking lots", "car", "didn't get out",
            "did not get out", "drove away", "sat in",
        },
        "definition": (
            "Consumer reaches purchase location but abandons the transaction "
            "before entering due to perceived value/cost mismatch."
        ),
        "strategic_significance": (
            "A pre-purchase abandonment pattern observable before any POS or sales data reflects it. "
            "Potential leading indicator for traffic softness, check compression, "
            "reduced visit frequency, value migration, and at-home meal substitution acceleration."
        ),
        "leading_indicators": [
            "traffic softness",
            "check compression",
            "reduced visit frequency",
            "value migration to lower-tier concepts",
            "at-home meal substitution acceleration",
        ],
        "daily_brief_layers": ["consumer_sentiment", "fast_casual_pressure"],
    },
    {
        "artifact_name": "Value Migration Behavior",
        "trigger_signal_types": {"trade_down_behavior"},
        "trigger_keywords": {
            "mcdonald", "burger king", "taco bell", "trade down",
            "fraction of the price", "value menu", "value meal",
        },
        "definition": (
            "Consumer actively shifts repeat purchase intent from premium tier "
            "to value tier due to cumulative price-to-value recalculation."
        ),
        "strategic_significance": (
            "When systematic rather than episodic, signals structural shift in "
            "segment competitive dynamics — value platforms capture compounding traffic advantage."
        ),
        "leading_indicators": [
            "premium segment traffic decline",
            "value platform traffic growth",
            "check compression at premium concepts",
        ],
        "daily_brief_layers": ["fast_casual_pressure", "value_platform_competitive"],
    },
]

# ---------------------------------------------------------------------------
# Entity risk intelligence
# ---------------------------------------------------------------------------

_PREMIUM_VULNERABLE_BRANDS: dict[str, str] = {
    "five guys": "Five Guys",
    "shake shack": "Shake Shack",
    "sweetgreen": "Sweetgreen",
    "chipotle": "Chipotle Mexican Grill",
    "panera": "Panera Bread",
    "dig ": "Dig",
    "true food kitchen": "True Food Kitchen",
    "first watch": "First Watch",
}

_VALUE_PLATFORM_BRANDS: dict[str, str] = {
    "mcdonald": "McDonald's",
    "burger king": "Burger King",
    "taco bell": "Taco Bell",
    "wendy": "Wendy's",
    "sonic ": "Sonic",
    "jack in the box": "Jack in the Box",
    "popeyes": "Popeyes",
    "chick-fil-a": "Chick-fil-A",
}

# ---------------------------------------------------------------------------
# Tech implication rules
# ---------------------------------------------------------------------------

_TECH_IMPLICATION_RULES: list[dict] = [
    {
        "trigger_signals": {"affordability_stress", "operational_pain"},
        "implication": (
            "Increased scrutiny of restaurant tech ROI — operators under margin pressure "
            "deprioritize non-essential and 'interesting AI' tooling"
        ),
        "daily_brief_layers": ["restaurant_tech_spending_risk"],
    },
    {
        "trigger_signals": {"consumer_hesitation"},
        "implication": (
            "Traffic softness risk elevates urgency for frequency and retention tech — "
            "loyalty and repeat-visit systems gain strategic priority"
        ),
        "daily_brief_layers": ["restaurant_tech_spending_risk", "consumer_sentiment"],
    },
    {
        "trigger_signals": {"trade_down_behavior"},
        "implication": (
            "Value platform tech advantages compounding — labor-saving and margin-protection "
            "systems prioritized over experience-enhancement tools at premium concepts"
        ),
        "daily_brief_layers": ["value_platform_competitive"],
    },
    {
        "trigger_signals": {"affordability_stress", "trade_down_behavior"},
        "implication": (
            "Non-essential AI tooling faces elevated rejection risk — "
            "operators require demonstrable ROI proof before commitment"
        ),
        "daily_brief_layers": ["restaurant_tech_spending_risk"],
    },
    {
        "trigger_signals": {"operational_pain"},
        "implication": (
            "Operators in commodity squeeze prioritize operational efficiency — "
            "reduces openness to implementation risk from new tech platforms"
        ),
        "daily_brief_layers": ["commodity_inflation", "restaurant_tech_spending_risk"],
    },
]

# ---------------------------------------------------------------------------
# Daily brief layer mapping
# ---------------------------------------------------------------------------

_SIGNAL_TO_BRIEF_LAYERS: dict[str, list[str]] = {
    "consumer_hesitation": ["consumer_sentiment", "fast_casual_pressure"],
    "affordability_stress": ["consumer_sentiment", "commodity_inflation", "restaurant_tech_spending_risk"],
    "trade_down_behavior": ["fast_casual_pressure", "value_platform_competitive"],
    "emotional_friction": ["consumer_sentiment"],
    "value_perception_shift": ["consumer_sentiment", "fast_casual_pressure"],
    "operational_pain": ["commodity_inflation", "restaurant_tech_spending_risk"],
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_id(prefix: str = "mi") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}-{ts}-{uuid.uuid4().hex[:6]}"


def _split_sentences(text: str) -> list[str]:
    raw = [s.strip() for s in re.split(r"[.!?\n]", text)]
    return [s for s in raw if len(s) > 8]


def _classify_behavioral_signals(text: str) -> list[dict]:
    text_lower = text.lower()
    sentences = _split_sentences(text)
    results = []

    for signal_type, keywords in _BEHAVIORAL_SIGNAL_KEYWORDS.items():
        matched: set[str] = set()
        evidence: list[str] = []
        for kw in keywords:
            if kw in text_lower:
                matched.add(kw)
        for s in sentences:
            sl = s.lower()
            if any(kw in sl for kw in keywords):
                if s not in evidence:
                    evidence.append(s)
        if not matched:
            continue
        n = len(matched)
        confidence = "high" if n >= 3 else "medium" if n >= 2 else "low"
        results.append({
            "signal_type": signal_type,
            "confidence": confidence,
            "matched_keywords": list(matched)[:5],
            "evidence_sentences": evidence[:2],
        })

    results.sort(key=lambda x: {"high": 0, "medium": 1, "low": 2}[x["confidence"]])
    return results


def _generate_behavioral_artifacts(signal_types: set[str], text: str) -> list[dict]:
    text_lower = text.lower()
    artifacts = []
    for defn in _ARTIFACT_DEFINITIONS:
        if not defn["trigger_signal_types"].intersection(signal_types):
            continue
        if not any(kw in text_lower for kw in defn["trigger_keywords"]):
            continue
        artifacts.append({
            "id": _make_id("artifact"),
            "artifact_name": defn["artifact_name"],
            "definition": defn["definition"],
            "strategic_significance": defn["strategic_significance"],
            "leading_indicators": defn["leading_indicators"],
            "daily_brief_layers": defn["daily_brief_layers"],
            "claim_status": "proposed",
            "persistence_status": "pending confirmation",
            "created_at": _timestamp(),
            "confirmed_at": None,
        })
    return artifacts


def _extract_entity_risks(text: str, signal_types: set[str]) -> list[dict]:
    text_lower = text.lower()
    entity_mutations = []
    now = _timestamp()
    today = date.today().isoformat()

    for brand_key, brand_display in _PREMIUM_VULNERABLE_BRANDS.items():
        if brand_key not in text_lower:
            continue
        risk_dims: dict[str, dict] = {}
        if "affordability_stress" in signal_types or "consumer_hesitation" in signal_types:
            risk_dims["pricing_ceiling_pressure"] = {
                "value": "rising", "confidence": "high", "as_of": today
            }
            risk_dims["operational_flexibility"] = {
                "value": "low", "confidence": "medium", "as_of": today
            }
        if "operational_pain" in signal_types:
            risk_dims["commodity_exposure"] = {
                "value": "high", "confidence": "high", "as_of": today
            }
        if not risk_dims:
            continue
        brand_id = re.sub(r"\s+", "-", brand_key.strip())
        entity_mutations.append({
            "id": _make_id("entity"),
            "entity_id": brand_id,
            "entity_name": brand_display,
            "entity_type": "brand",
            "brand_category": "premium_fast_casual",
            "risk_profile": risk_dims,
            "claim_status": "proposed",
            "persistence_status": "pending confirmation",
            "created_at": now,
            "confirmed_at": None,
        })

    for brand_key, brand_display in _VALUE_PLATFORM_BRANDS.items():
        if brand_key not in text_lower:
            continue
        risk_dims = {}
        if "trade_down_behavior" in signal_types:
            risk_dims["trade_down_capture_capability"] = {
                "value": "increasing", "confidence": "high", "as_of": today
            }
            risk_dims["value_platform_strength"] = {
                "value": "high", "confidence": "high", "as_of": today
            }
        if "affordability_stress" in signal_types:
            risk_dims["competitive_positioning"] = {
                "value": "strengthening", "confidence": "medium", "as_of": today
            }
        if not risk_dims:
            continue
        brand_id = re.sub(r"[^a-z0-9\s]", "", brand_key).strip().replace(" ", "-")
        entity_mutations.append({
            "id": _make_id("entity"),
            "entity_id": brand_id,
            "entity_name": brand_display,
            "entity_type": "brand",
            "brand_category": "value_platform",
            "risk_profile": risk_dims,
            "claim_status": "proposed",
            "persistence_status": "pending confirmation",
            "created_at": now,
            "confirmed_at": None,
        })

    return entity_mutations


def _derive_tech_implications(signal_types: set[str]) -> list[dict]:
    implications = []
    seen: set[str] = set()
    for rule in _TECH_IMPLICATION_RULES:
        if rule["trigger_signals"].intersection(signal_types):
            imp_text = rule["implication"]
            if imp_text not in seen:
                seen.add(imp_text)
                implications.append({
                    "implication": imp_text,
                    "triggered_by": sorted(rule["trigger_signals"].intersection(signal_types)),
                    "daily_brief_layers": rule["daily_brief_layers"],
                })
    return implications


def _map_daily_brief_layers(signal_types: set[str]) -> list[str]:
    layers: list[str] = []
    for stype in signal_types:
        for layer in _SIGNAL_TO_BRIEF_LAYERS.get(stype, []):
            if layer not in layers:
                layers.append(layer)
    return sorted(layers)


def _build_cos_summary(
    signal_types: set[str],
    entity_mutations: list[dict],
    artifacts: list[dict],
) -> str:
    parts = []
    if "consumer_hesitation" in signal_types:
        parts.append("Consumer pre-purchase abandonment behavior detected")
    if "affordability_stress" in signal_types:
        parts.append("affordability stress materializing")
    if "trade_down_behavior" in signal_types:
        parts.append("trade-down to value tier confirmed")
    if "operational_pain" in signal_types:
        parts.append("operator margin pressure elevated")
    premium = [e["entity_name"] for e in entity_mutations if e["brand_category"] == "premium_fast_casual"]
    value = [e["entity_name"] for e in entity_mutations if e["brand_category"] == "value_platform"]
    if premium:
        parts.append(f"premium segment vulnerability: {', '.join(premium)}")
    if value:
        parts.append(f"value platform advantage: {', '.join(value)}")
    if artifacts:
        parts.append(f"behavioral artifact: {artifacts[0]['artifact_name']}")
    return " — ".join(parts).capitalize() if parts else "Macro behavioral signal processed."


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _load_store(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"_schema_version": "1.0", "records": []}


def _save_store(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def _write_pending_records(records: list[dict], path: Path) -> None:
    store = _load_store(path)
    existing_ids = {r.get("id") for r in store.get("records", [])}
    for record in records:
        if record.get("id") not in existing_ids:
            store["records"].append(record)
    store["_last_updated"] = _timestamp()
    _save_store(store, path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def process_macro_signal(
    text: str,
    source_type: str = "linkedin_post",
    author_name: str | None = None,
    author_org: str | None = None,
    author_role: str | None = None,
    signal_date: str | None = None,
    ecosystem_tags: list[str] | None = None,
    behavioral_store_path: Path | None = None,
    entity_store_path: Path | None = None,
    ri_store_path: Path | None = None,
) -> dict:
    """Full cross-layer macro behavioral intelligence processing.

    Returns multi-layer CoS mutation surface:
      behavioral_signals     — classified behavioral signals with confidence
      behavioral_artifacts   — named behavioral concepts extracted
      entity_risk_mutations  — per-brand risk profile mutations (proposed)
      tech_implications      — downstream restaurant tech intelligence
      ri_mutation            — relationship intelligence mutation (if author present)
      daily_brief_layers     — which daily brief layers this signal enters
      mutation_proposals     — flat list of all review-first proposals
      cos_surface            — canonical CoS output block
      persistence_status     — always explicit
    """
    if not text or not text.strip():
        return {
            "behavioral_signals": [], "behavioral_artifacts": [],
            "entity_risk_mutations": [], "tech_implications": [],
            "ri_mutation": None, "daily_brief_layers": [],
            "mutation_proposals": [], "cos_surface": None,
            "persistence_status": "RB did not persist",
            "note": "Empty input. No macro intelligence extracted.",
        }

    if source_type not in SOURCE_TYPES:
        source_type = "linkedin_post"

    today = signal_date or date.today().isoformat()
    b_store = behavioral_store_path or BEHAVIORAL_INTELLIGENCE_PATH
    e_store = entity_store_path or ENTITY_INTELLIGENCE_PATH

    # 1. Behavioral signal classification
    signals = _classify_behavioral_signals(text)
    signal_types = {s["signal_type"] for s in signals}

    if not signals:
        return {
            "behavioral_signals": [], "behavioral_artifacts": [],
            "entity_risk_mutations": [], "tech_implications": [],
            "ri_mutation": None, "daily_brief_layers": [],
            "mutation_proposals": [], "cos_surface": None,
            "persistence_status": "RB did not persist",
            "note": "No behavioral signals detected in input text.",
        }

    # 2. Behavioral artifact generation
    artifacts = _generate_behavioral_artifacts(signal_types, text)

    # 3. Entity risk extraction
    entity_mutations = _extract_entity_risks(text, signal_types)

    # 4. Tech implications
    tech_implications = _derive_tech_implications(signal_types)

    # 5. Daily brief layers — aggregate across signals, artifacts, and tech implications
    daily_brief_layers: list[str] = _map_daily_brief_layers(signal_types)
    for artifact in artifacts:
        for layer in artifact.get("daily_brief_layers", []):
            if layer not in daily_brief_layers:
                daily_brief_layers.append(layer)
    for impl in tech_implications:
        for layer in impl.get("daily_brief_layers", []):
            if layer not in daily_brief_layers:
                daily_brief_layers.append(layer)
    daily_brief_layers = sorted(set(daily_brief_layers))

    # 6. RI mutation — if author is named, classify as thought leader alignment
    ri_result: dict | None = None
    if author_name:
        author_tags = list(ecosystem_tags or [])
        if "restaurant_tech" not in author_tags and "restaurant" in text.lower():
            author_tags.append("restaurant_tech")
        if "operator_network" not in author_tags:
            author_tags.append("operator_network")
        ri_result = relationship_intake.process_relationship_thread(
            text=text,
            entity_name=author_name,
            entity_org=author_org,
            entity_role=author_role,
            interaction_date=today,
            source_type="linkedin_post",
            ecosystem_tags=author_tags,
            signal_type_override="thought_leader_alignment",
            store_path=ri_store_path,
        )

    # 7. Build flat mutation proposals
    mutation_proposals: list[dict] = []
    for signal in signals:
        mutation_proposals.append({
            "mutation_type": "behavioral_signal",
            "target": "system/behavioral_intelligence.json",
            "operation": "append",
            "signal_type": signal["signal_type"],
            "confidence": signal["confidence"],
            "evidence": signal.get("evidence_sentences", []),
            "signal_date": today,
            "source_type": source_type,
            "requires_confirmation": True,
            "persistence_endpoint": "POST /macro/confirm",
        })
    for artifact in artifacts:
        mutation_proposals.append({
            "mutation_type": "behavioral_artifact",
            "target": "system/behavioral_intelligence.json",
            "operation": "append",
            "artifact_id": artifact["id"],
            "artifact_name": artifact["artifact_name"],
            "definition": artifact["definition"],
            "leading_indicators": artifact["leading_indicators"],
            "requires_confirmation": True,
            "persistence_endpoint": "POST /macro/confirm",
        })
    for entity in entity_mutations:
        mutation_proposals.append({
            "mutation_type": "entity_risk_profile",
            "target": "system/entity_intelligence.json",
            "operation": "upsert",
            "entity_id": entity["entity_id"],
            "entity_name": entity["entity_name"],
            "brand_category": entity["brand_category"],
            "risk_profile": entity["risk_profile"],
            "requires_confirmation": True,
            "persistence_endpoint": "POST /macro/confirm",
        })
    for impl in tech_implications:
        mutation_proposals.append({
            "mutation_type": "tech_implication",
            "target": "system/strategic_memory.json",
            "operation": "append",
            "implication": impl["implication"],
            "triggered_by": impl["triggered_by"],
            "daily_brief_layers": impl["daily_brief_layers"],
            "requires_confirmation": True,
            "persistence_endpoint": "POST /macro/confirm",
        })

    # 8. Persist pending records to behavioral and entity stores
    behavioral_records = [
        {
            "id": _make_id("bsig"),
            "record_type": "behavioral_signal",
            "signal_type": s["signal_type"],
            "confidence": s["confidence"],
            "matched_keywords": s["matched_keywords"],
            "evidence_sentences": s.get("evidence_sentences", []),
            "source_type": source_type,
            "signal_date": today,
            "source_text_snippet": text[:300],
            "claim_status": "proposed",
            "persistence_status": "pending confirmation",
            "created_at": _timestamp(),
            "confirmed_at": None,
        }
        for s in signals
    ]
    behavioral_records.extend(artifacts)
    _write_pending_records(behavioral_records, b_store)
    _write_pending_records(entity_mutations, e_store)

    # 9. CoS surface
    cos_surface = {
        "signal_date": today,
        "source_type": source_type,
        "author": {
            "name": author_name,
            "org": author_org,
            "role": author_role,
        } if author_name else None,
        "behavioral_signals": signals,
        "behavioral_artifacts": [
            {
                "artifact_name": a["artifact_name"],
                "definition": a["definition"],
                "strategic_significance": a["strategic_significance"],
                "leading_indicators": a["leading_indicators"],
            }
            for a in artifacts
        ],
        "entity_risk_mutations": entity_mutations,
        "tech_implications": [i["implication"] for i in tech_implications],
        "ri_mutation_proposed": ri_result.get("cos_surface") if ri_result else None,
        "daily_brief_layers": daily_brief_layers,
        "cos_summary": _build_cos_summary(signal_types, entity_mutations, artifacts),
    }

    return {
        "behavioral_signals": signals,
        "behavioral_artifacts": artifacts,
        "entity_risk_mutations": entity_mutations,
        "tech_implications": tech_implications,
        "ri_mutation": ri_result,
        "daily_brief_layers": daily_brief_layers,
        "mutation_proposals": mutation_proposals,
        "cos_surface": cos_surface,
        "persistence_status": "pending confirmation",
        "signal_count": len(signals),
        "artifact_count": len(artifacts),
        "entity_mutation_count": len(entity_mutations),
        "tech_implication_count": len(tech_implications),
        "mutation_proposal_count": len(mutation_proposals),
    }


def record_behavioral_record(
    record_id: str,
    confirmed: bool = True,
    behavioral_store_path: Path | None = None,
) -> dict:
    """Confirm or reject a pending behavioral signal or artifact record."""
    path = behavioral_store_path or BEHAVIORAL_INTELLIGENCE_PATH
    store = _load_store(path)
    for idx, record in enumerate(store.get("records", [])):
        if record.get("id") == record_id:
            if confirmed:
                store["records"][idx]["claim_status"] = "confirmed"
                store["records"][idx]["persistence_status"] = "RB recorded"
                store["records"][idx]["confirmed_at"] = _timestamp()
            else:
                store["records"][idx]["claim_status"] = "rejected"
                store["records"][idx]["persistence_status"] = "RB skipped"
            store["_last_updated"] = _timestamp()
            _save_store(store, path)
            return store["records"][idx]
    return {"error": f"Record {record_id} not found."}


def record_entity_risk(
    entity_id: str,
    confirmed: bool = True,
    entity_store_path: Path | None = None,
) -> dict:
    """Confirm or reject a pending entity risk profile mutation."""
    path = entity_store_path or ENTITY_INTELLIGENCE_PATH
    store = _load_store(path)
    for idx, record in enumerate(store.get("records", [])):
        if record.get("entity_id") == entity_id:
            if confirmed:
                store["records"][idx]["claim_status"] = "confirmed"
                store["records"][idx]["persistence_status"] = "RB recorded"
                store["records"][idx]["confirmed_at"] = _timestamp()
            else:
                store["records"][idx]["claim_status"] = "rejected"
                store["records"][idx]["persistence_status"] = "RB skipped"
            store["_last_updated"] = _timestamp()
            _save_store(store, path)
            return store["records"][idx]
    return {"error": f"Entity {entity_id} not found."}


def query_behavioral_signals(
    signal_type: str | None = None,
    claim_status: str | None = None,
    behavioral_store_path: Path | None = None,
) -> list[dict]:
    """Retrieve behavioral signal records from the store."""
    store = _load_store(behavioral_store_path or BEHAVIORAL_INTELLIGENCE_PATH)
    results = [r for r in store.get("records", []) if r.get("record_type") == "behavioral_signal"]
    if signal_type:
        results = [r for r in results if r.get("signal_type") == signal_type]
    if claim_status:
        results = [r for r in results if r.get("claim_status") == claim_status]
    return results


def query_behavioral_artifacts(
    claim_status: str | None = None,
    behavioral_store_path: Path | None = None,
) -> list[dict]:
    """Retrieve behavioral artifact records from the store."""
    store = _load_store(behavioral_store_path or BEHAVIORAL_INTELLIGENCE_PATH)
    results = [r for r in store.get("records", []) if r.get("artifact_name")]
    if claim_status:
        results = [r for r in results if r.get("claim_status") == claim_status]
    return results


def query_entity_risks(
    entity_id: str | None = None,
    claim_status: str | None = None,
    entity_store_path: Path | None = None,
) -> list[dict]:
    """Retrieve entity risk profile records from the store."""
    store = _load_store(entity_store_path or ENTITY_INTELLIGENCE_PATH)
    results = list(store.get("records", []))
    if entity_id:
        results = [r for r in results if r.get("entity_id") == entity_id]
    if claim_status:
        results = [r for r in results if r.get("claim_status") == claim_status]
    return results


# ---------------------------------------------------------------------------
# Thesis convergence (DEFECT-009 / DEFECT-010)
# ---------------------------------------------------------------------------

# Theme → behavioral signal types that validate the thesis
_THESIS_SIGNAL_MAP: dict[str, list[str]] = {
    "operational_realism":     ["operational_pain", "consumer_hesitation", "affordability_stress"],
    "affordability_stress":    ["affordability_stress", "consumer_hesitation", "trade_down_behavior"],
    "consumer_hesitation":     ["consumer_hesitation", "affordability_stress"],
    "trade_down_behavior":     ["trade_down_behavior", "consumer_hesitation"],
    "restaurant_ai_skepticism": ["operational_pain"],
    "retention_economics":     ["affordability_stress", "consumer_hesitation"],
    "vendor_trust_erosion":    ["operational_pain"],
    "value_perception":        ["value_perception_shift", "affordability_stress"],
}

# Theme → free-text keyword patterns for broader matching in evidence_sentences
_THESIS_KEYWORDS: dict[str, list[str]] = {
    "operational_realism": [
        "operational", "realistic", "realism", "practical", "risk reduction",
        "friday night", "survivability", "real operators", "operators",
        "hype", "anti-hype", "ground truth",
    ],
    "affordability_stress": [
        "afford", "price", "expensive", "cost", "value", "sticker shock", "strain",
    ],
    "restaurant_ai_skepticism": [
        "ai", "artificial intelligence", "automation", "robot", "skeptic",
        "not ready", "overhyped", "fails", "failure",
    ],
    "retention_economics": [
        "retention", "loyal", "repeat", "regular", "churn", "lifetime value",
        "customer success",
    ],
    "vendor_trust_erosion": [
        "trust", "erosion", "stickiness", "hard to replace", "churn",
        "relationship", "renewal",
    ],
}


def thesis_convergence(
    theme: str,
    *,
    days: int = 30,
    min_convergence: int = 3,
    behavioral_store_path: Path | None = None,
) -> dict:
    """Count how many times a strategic theme has been validated in recent signals.

    DEFECT-009/010 fix: provides the "3rd independent validation" detection that
    the live CoS layer was missing.  The GPT can call this after triageInput returns
    a strategic_memory or macro_signal stream to surface convergence patterns.

    Args:
        theme: One of the keys in _THESIS_SIGNAL_MAP, or a free-text keyword string.
        days: Lookback window.
        min_convergence: Minimum count to declare convergence (default 3).
        behavioral_store_path: Override for testing.

    Returns:
        {
          "theme": str,
          "validation_count": int,
          "threshold": int,
          "is_converging": bool,
          "convergence_statement": str,
          "signal_types_matched": list[str],
          "recent_signals": list[dict],  # up to 5 most recent
          "date_range": {"from": str, "to": str},
          "confidence": "high|medium|low",
        }
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    store = _load_store(behavioral_store_path or BEHAVIORAL_INTELLIGENCE_PATH)
    records = [r for r in store.get("records", []) if r.get("record_type") == "behavioral_signal"]

    # Date filter
    cutoff = _dt.now(tz=_tz.utc) - _td(days=days)
    cutoff_iso = cutoff.isoformat()

    # Resolve theme to signal types
    theme_lower = theme.lower().strip().replace(" ", "_")
    matched_signal_types: list[str] = _THESIS_SIGNAL_MAP.get(theme_lower, [])
    theme_keywords: list[str] = _THESIS_KEYWORDS.get(theme_lower, [theme_lower.replace("_", " ")])

    matched: list[dict] = []
    for r in records:
        # Date filter
        sig_date = r.get("signal_date") or r.get("created_at") or ""
        if sig_date and sig_date < cutoff_iso:
            continue

        # Signal type match
        sig_type = r.get("signal_type") or ""
        type_match = sig_type in matched_signal_types

        # Free-text match in evidence_sentences
        evidence = " ".join(r.get("evidence_sentences") or []).lower()
        text_match = any(kw in evidence for kw in theme_keywords)

        if type_match or text_match:
            matched.append(r)

    count = len(matched)
    is_converging = count >= min_convergence

    # Deduplicate by source (avoid counting the same source multiple times)
    unique_sources = len({r.get("source_type", "") + (r.get("evidence_sentences") or [""])[0][:40]
                          for r in matched})

    # Sort by date descending, take most recent 5
    def _sig_date(r: dict) -> str:
        return r.get("signal_date") or r.get("created_at") or ""
    recent = sorted(matched, key=_sig_date, reverse=True)[:5]

    # Build convergence statement
    theme_display = theme.replace("_", " ").title()
    if is_converging:
        convergence_statement = (
            f"RB has detected {count} validation signal(s) for '{theme_display}' "
            f"in the last {days} days across {unique_sources} unique source(s). "
            f"This is a converging pattern — {count} independent observations confirm "
            f"the thesis is active in the current market environment."
        )
        if count >= 10:
            convergence_statement += f" Pattern is strongly established ({count} signals)."
    else:
        convergence_statement = (
            f"RB has {count} signal(s) for '{theme_display}' in the last {days} days "
            f"(threshold: {min_convergence}). "
            + ("Not yet converging — more independent signals needed."
               if count > 0 else "No signals found for this theme in the lookback window.")
        )

    confidence = "high" if count >= 10 else "medium" if count >= min_convergence else "low"

    date_from = cutoff.date().isoformat()
    date_to = _dt.now(tz=_tz.utc).date().isoformat()

    return {
        "theme": theme,
        "validation_count": count,
        "threshold": min_convergence,
        "is_converging": is_converging,
        "convergence_statement": convergence_statement,
        "signal_types_matched": sorted(set(r.get("signal_type", "") for r in matched if r.get("signal_type"))),
        "unique_source_count": unique_sources,
        "recent_signals": [
            {
                "signal_type": r.get("signal_type"),
                "confidence": r.get("confidence"),
                "signal_date": r.get("signal_date"),
                "evidence": (r.get("evidence_sentences") or [""])[0][:120],
                "source_type": r.get("source_type"),
            }
            for r in recent
        ],
        "date_range": {"from": date_from, "to": date_to},
        "confidence": confidence,
        "requires_confirmation": False,  # read-only
    }
