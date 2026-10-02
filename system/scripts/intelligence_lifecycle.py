#!/usr/bin/env python3
"""
intelligence_lifecycle.py — RB 9.35 / DEFECT-018
Intelligence lifecycle management for daily briefings.

Provides an intelligence object store with lifecycle states and attribution
classification.  The daily brief uses this to suppress already-reported items,
surface new intelligence first, reactivate dormant items when related news
arrives, and produce a structured source audit at the end of every brief.

Lifecycle states (per defect spec):
  NEW          — first observation; surface immediately
  ACKNOWLEDGED — user has consumed the information; suppress
  DORMANT      — stored in graph; available for reasoning; suppressed
  REACTIVATED  — new related intelligence triggers re-surface with linkage
  RETIRED      — no longer relevant; excluded from briefings

Attribution types (per defect spec):
  MEMORY    — user-specific stored context replayed from memory
  OBSERVED  — directly observed from external source(s)
  SYNTHESIS — conclusion derived from multiple corroborating sources
  HYPOTHESIS — inference not yet fully supported

Key exports:
  IntelligenceStore              persistent JSON-backed lifecycle state machine
  classify_attribution(item)     → MEMORY | OBSERVED | SYNTHESIS | HYPOTHESIS
  build_source_audit(health)     → structured source audit block
  process_brief_items(...)       → (new_items, reactivated_items, suppressed_items)
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LIFECYCLE_STATES = ("NEW", "ACKNOWLEDGED", "DORMANT", "REACTIVATED", "RETIRED")

# Sections of the canonical brief that are eligible for lifecycle management.
# Meta/system sections are excluded (resource_verification, daily_prep, etc.)
_LIFECYCLE_ELIGIBLE_SECTIONS = frozenset({
    "autonomous_discovery_evidence",
    "overnight_delta_intelligence",
    "email_intelligence_harvest",
    "thesis_confidence_and_challenges",
    "relationship_operational_signal_review",
    "industry_brief",
    "local_regional_restaurant_environment",
    "world_macro_macroeconomic_impact",
    "strategic_industry_signals",
    "strategic_operator_movements",
    "macro_pressure_stack",
    "restaurant_pain_mapping",
    "franchise_alignment_gap",
    # Sprint D — sections where real intelligence lives; must enter lifecycle
    # so previously-reported items are suppressed on subsequent briefs.
    "condensed_industry_context",   # market signals, filings, vendor news
    "watchlist_intelligence",        # PAR/Yetter, watchlist entity events
    "last_24h_relationship_signals", # relationship signals (contact events)
    "new_intelligence_today",        # output section — lifecycle-annotated items
    # Sprint D: industry domain sections (populated by external scan)
    "world_national_headlines",
    "restaurant_industry_headlines",
    "restaurant_technology_headlines",
})

# Default suppression rules by item category/section.
# Items in DORMANT state are re-surfaced only when a trigger condition fires.
_DEFAULT_SUPPRESSION_RULES: dict[str, dict] = {
    "leadership_change": {
        "suppress_days": 30,
        "reactivation_keywords": [
            "president", "ceo", "cto", "cfo", "vp", "svp", "evp",
            "resign", "depart", "fired", "hire", "promote", "restructure",
            "reorganize", "acquisition", "merger", "appoint",
        ],
        "reactivation_categories": ["leadership_change", "restructuring", "strategic_direction", "acquisition"],
    },
    "market_intelligence": {
        "suppress_days": 7,
        "reactivation_keywords": [
            "earnings", "revenue", "guidance", "acquisition", "partnership",
            "ipo", "funding", "bankruptcy", "layoff", "expansion",
        ],
        "reactivation_categories": ["market_intelligence", "earnings_watch", "strategic_signal"],
    },
    "relationship_intelligence": {
        "suppress_days": 14,
        "reactivation_keywords": [
            "promotion", "new role", "left", "joined", "connected", "meeting",
            "follow-up", "response", "reply",
        ],
        "reactivation_categories": ["relationship_intelligence", "leadership_change"],
    },
    "strategic_signal": {
        "suppress_days": 14,
        "reactivation_keywords": [
            "acquisition", "merger", "partnership", "expansion", "pivot",
            "launch", "product", "contract", "deal",
        ],
        "reactivation_categories": ["strategic_signal", "market_intelligence"],
    },
    "earnings_watch": {
        "suppress_days": 90,
        "reactivation_keywords": ["earnings", "quarterly", "annual", "guidance", "results"],
        "reactivation_categories": ["earnings_watch", "market_intelligence"],
    },
    "default": {
        "suppress_days": 14,
        "reactivation_keywords": [],
        "reactivation_categories": [],
    },
    # DEFECT-022: MEMORY items (user-supplied) require external corroboration
    # to resurface. 180-day suppress window; keyword match alone is insufficient.
    "memory_user_supplied": {
        "suppress_days": 180,
        "require_external_corroboration": True,
        "reactivation_keywords": [],  # keyword match disabled for MEMORY items
        "reactivation_categories": [],
    },
}

# Source labels for the source audit — maps source_health key → display name
_SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "email": "Email inbox",
    "calendar": "Calendar",
    "linkedin": "LinkedIn",
    "social": "Social / LinkedIn feed",
    "market": "Market signals (trade press)",
    "earnings": "Earnings monitor (EDGAR/IR RSS)",
    "active_threads": "Active threads (manual)",
    "loop_ledger": "Loop ledger (commitments)",
    "strategic_memory": "Strategic memory (operator memory)",
    "baseline": "Relationship baseline",
}

# Source confidence by tier
_TIER_CONFIDENCE: dict[int, str] = {1: "high", 2: "medium", 3: "low"}

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _system_dir() -> Path:
    """Locate system/ relative to this script."""
    return Path(__file__).resolve().parent.parent


def _store_path() -> Path:
    cache = _system_dir() / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    return cache / "intelligence_store.json"


# ---------------------------------------------------------------------------
# ID / hash utilities
# ---------------------------------------------------------------------------

def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    t = (title or "").lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def make_intelligence_id(title: str) -> str:
    """Deterministic content-addressed ID from a normalized headline.

    Two items with the same semantic title produce the same ID regardless
    of the date they were generated.
    """
    normalized = _normalize_title(title)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _extract_entity_hint(item: dict) -> str:
    """Best-effort entity extraction from a canonical item.

    Checks extras.operator_id first, then scans title/source_refs for known
    entity patterns.  Falls back to 'general'.
    """
    extras = item.get("extras") or {}
    if extras.get("operator_id"):
        return str(extras["operator_id"])

    # Check for known entities in title + summary
    text = " ".join([
        item.get("title") or "",
        item.get("summary") or "",
        " ".join(item.get("source_refs") or []),
    ]).lower()

    _KNOWN_ENTITIES = [
        ("par-technology", ["par technology", "par tech", "par restaurants", "par ", " par "]),
        ("olo", ["olo ", " olo", "olo.com"]),
        ("toast", ["toast ", " toast", "toast.com", "toast pos"]),
        ("mcdonalds", ["mcdonald", "mcdonalds", "mcd "]),
        ("global-payments", ["global payments", "globalpayments"]),
        ("genius-sports", ["genius sports"]),
        ("yum-brands", ["yum brands", "yum!", "yum "]),
    ]
    for slug, patterns in _KNOWN_ENTITIES:
        if any(p in text for p in patterns):
            return slug
    return "general"


def _section_to_category(section: str) -> str:
    """Map a brief section key to an intelligence category for suppression rules."""
    _MAP = {
        "autonomous_discovery_evidence": "strategic_signal",
        "overnight_delta_intelligence": "strategic_signal",
        "email_intelligence_harvest": "relationship_intelligence",
        "thesis_confidence_and_challenges": "strategic_signal",
        "relationship_operational_signal_review": "relationship_intelligence",
        "industry_brief": "market_intelligence",
        "local_regional_restaurant_environment": "market_intelligence",
        "world_macro_macroeconomic_impact": "market_intelligence",
        "strategic_industry_signals": "strategic_signal",
        "strategic_operator_movements": "leadership_change",
        "macro_pressure_stack": "market_intelligence",
        "restaurant_pain_mapping": "market_intelligence",
        "franchise_alignment_gap": "market_intelligence",
        "earnings_watch_list_mutations": "earnings_watch",
        # Sprint D added these three to _LIFECYCLE_ELIGIBLE_SECTIONS but never
        # mapped them here, so they silently fell through to the "default"
        # category's 14-day suppress_days — over 2x daily_brief.py's own
        # 7-day headline freshness gate (_HEADLINE_FRESHNESS_DAYS). A wire
        # story an RSS feed still lists 3-4 days later (common — feeds don't
        # rotate hourly) was already correctly judged "fresh" by that gate,
        # then hard-suppressed here anyway because the generic 14-day window
        # hadn't elapsed. Mapping to market_intelligence (suppress_days: 7)
        # aligns the two and stops genuinely fresh headlines from being
        # treated as if reported two weeks ago.
        "world_national_headlines": "market_intelligence",
        "restaurant_industry_headlines": "market_intelligence",
        "restaurant_technology_headlines": "market_intelligence",
    }
    return _MAP.get(section, "default")


# ---------------------------------------------------------------------------
# Attribution classification
# ---------------------------------------------------------------------------

def classify_attribution(item: dict) -> str:
    """Derive the attribution type for a canonical brief item.

    Returns one of: MEMORY | OBSERVED | SYNTHESIS | HYPOTHESIS

    Logic:
      MEMORY    — grounding is manual/user-provided or operator memory
      SYNTHESIS — 3+ corroborating sources or signal_synthesis output
      HYPOTHESIS — grounding is inferred or confidence is low
      OBSERVED  — default for externally detected single/dual source items
    """
    grounding = (item.get("grounding") or "").lower()
    prov = item.get("provenance") or {}
    source_class = (prov.get("source_class") or "").lower()
    corroboration = int(prov.get("corroboration_count") or len(item.get("source_refs") or []) or 0)
    source_refs = item.get("source_refs") or []
    confidence = (item.get("confidence") or "medium").lower()

    # MEMORY: explicitly user-provided or replayed from operator memory
    _memory_groundings = {
        "manual_user_provided", "manual_context", "operator_memory",
        "manual_operator_context", "user_uploaded_artifact",
    }
    if grounding in _memory_groundings or source_class == "user_provided":
        return "MEMORY"

    # HYPOTHESIS: inferred or low-confidence
    if grounding == "inferred" or confidence == "low":
        return "HYPOTHESIS"

    # SYNTHESIS: 3+ sources or signal_synthesis provenance
    if corroboration >= 3:
        return "SYNTHESIS"
    if any("signal_synthesis" in (r or "") for r in source_refs):
        return "SYNTHESIS"
    if any("multi_source" in (r or "") or "corroborated" in (r or "") for r in source_refs):
        return "SYNTHESIS"

    # Dual-source system items qualify as SYNTHESIS
    if corroboration >= 2 and source_class in ("externally_discovered", "system_inferred"):
        return "SYNTHESIS"

    # OBSERVED: default for single-source externally detected items
    _observed_groundings = {
        "system_detected", "source_backed", "external_source", "externally_observed",
    }
    if source_class in ("externally_discovered", "system_inferred") or grounding in _observed_groundings:
        return "OBSERVED"

    # Default for anything else
    return "HYPOTHESIS"


# ---------------------------------------------------------------------------
# Source audit builder
# ---------------------------------------------------------------------------

def build_source_audit(source_health: dict | None) -> dict:
    """Build a structured source audit block from source_health.json data.

    Returns:
        {
            "generated_at": ISO timestamp,
            "sources_checked": [
                {
                    "name": display_name,
                    "key": source_key,
                    "status": refreshed | stale | unavailable | unknown,
                    "last_checked": ISO or null,
                    "tier": 1|2|3,
                    "confidence_contribution": high|medium|low,
                }
            ],
            "domain_confidence": {
                "relationship_intelligence": high|medium|low,
                "market_intelligence": high|medium|low,
                "macro_environment": high|medium|low,
            },
            "audit_note": str,
        }
    """
    if not source_health:
        return {
            "generated_at": None,
            "sources_checked": [],
            "domain_confidence": {
                "relationship_intelligence": "unknown",
                "market_intelligence": "unknown",
                "macro_environment": "unknown",
            },
            "audit_note": "Source health data unavailable — confidence cannot be assessed.",
        }

    health_sources = source_health.get("sources") or {}
    sources_checked = []
    for key, row in health_sources.items():
        if not isinstance(row, dict):
            continue
        tier = int(row.get("tier") or 2)
        status = row.get("status") or "unknown"
        sources_checked.append({
            "name": _SOURCE_DISPLAY_NAMES.get(key, key.replace("_", " ").title()),
            "key": key,
            "status": status,
            "last_checked": row.get("checked_at") or row.get("last_refreshed") or None,
            "tier": tier,
            "confidence_contribution": _TIER_CONFIDENCE.get(tier, "low"),
        })

    # Sort by tier then name
    sources_checked.sort(key=lambda s: (s["tier"], s["name"]))

    # Derive domain confidence from source statuses
    _domain_source_keys = {
        "relationship_intelligence": ["email", "calendar", "linkedin", "social", "loop_ledger"],
        "market_intelligence": ["market", "earnings", "social"],
        "macro_environment": ["market", "earnings"],
    }
    domain_confidence: dict[str, str] = {}
    for domain, keys in _domain_source_keys.items():
        relevant = [s for s in sources_checked if s["key"] in keys]
        if not relevant:
            domain_confidence[domain] = "unknown"
            continue
        fresh = sum(1 for s in relevant if s["status"] == "refreshed")
        ratio = fresh / len(relevant)
        if ratio >= 0.8:
            domain_confidence[domain] = "high"
        elif ratio >= 0.5:
            domain_confidence[domain] = "medium"
        else:
            domain_confidence[domain] = "low"

    stale_count = sum(1 for s in sources_checked if s["status"] not in ("refreshed", "ok"))
    note = (
        f"{len(sources_checked)} sources checked; {stale_count} stale or unavailable."
        if sources_checked else
        "No source records available."
    )

    return {
        "generated_at": source_health.get("generated_at") or source_health.get("checked_at"),
        "sources_checked": sources_checked,
        "domain_confidence": domain_confidence,
        "audit_note": note,
    }


# ---------------------------------------------------------------------------
# Intelligence Store
# ---------------------------------------------------------------------------

class IntelligenceStore:
    """Persistent JSON-backed intelligence object lifecycle manager.

    Usage pattern (in daily_brief.py):

        store = IntelligenceStore.load()
        store.advance_cycle(today)
        new_items, reactivated, suppressed = process_brief_items(store, sections, today)
        store.save()
    """

    def __init__(self, data: dict):
        self._data = data
        # In-memory sets for this cycle's results
        self._suppressed_this_cycle: list[dict] = []
        self._new_this_cycle: list[str] = []
        self._reactivated_this_cycle: list[str] = []

    @classmethod
    def load(cls) -> "IntelligenceStore":
        path = _store_path()
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("contract") == "rb_intelligence_store_v1":
                    return cls(raw)
            except Exception:  # noqa: BLE001
                pass
        return cls({
            "contract": "rb_intelligence_store_v1",
            "version": 1,
            "last_advanced_date": None,
            "items": {},
        })

    def save(self) -> None:
        path = _store_path()
        path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    @property
    def items(self) -> dict[str, dict]:
        return self._data.setdefault("items", {})

    def advance_cycle(self, today: date) -> int:
        """Advance lifecycle states for a new brief cycle.

        Called once per day at the start of brief generation.

        Transitions:
          NEW          → ACKNOWLEDGED  (items first seen before today)
          ACKNOWLEDGED → DORMANT       (items acknowledged on a prior day)
          REACTIVATED  → ACKNOWLEDGED  (reactivated items consumed; become acknowledged)

        Returns the number of items advanced.
        """
        today_str = today.isoformat()
        last_advanced = self._data.get("last_advanced_date")

        # Only advance if we haven't already advanced today
        if last_advanced == today_str:
            return 0

        advanced = 0
        for item in self.items.values():
            state = item.get("lifecycle_state")
            first_seen = item.get("first_seen") or today_str

            if state == "NEW" and first_seen < today_str:
                self._transition(item, "ACKNOWLEDGED", today_str, "auto_advance_post_brief")
                advanced += 1

            elif state == "ACKNOWLEDGED":
                # Acknowledged items become DORMANT once the category's own
                # suppress_days window has elapsed since first observation —
                # not unconditionally "one day after acknowledgment"
                # regardless of category. The flat 1-day rule pushed headline
                # sections (world_national/restaurant_industry/restaurant_
                # technology_headlines, category market_intelligence,
                # suppress_days: 7) to DORMANT within ~2 real calendar days
                # of first being seen — directly conflicting with
                # daily_brief.py's own 7-day headline freshness gate. An
                # item already judged "fresh" by that gate was getting
                # hard-suppressed here anyway (DORMANT scores 0 in
                # compute_novelty_score), silently emptying entire sections.
                rules = item.get("suppression_rules") or _DEFAULT_SUPPRESSION_RULES.get(
                    item.get("category", ""), _DEFAULT_SUPPRESSION_RULES["default"]
                )
                suppress_days = int(rules.get("suppress_days") or 14)
                try:
                    age_days = (date.fromisoformat(today_str) - date.fromisoformat(first_seen)).days
                except ValueError:
                    age_days = suppress_days  # unparseable date — fail safe to old behavior
                if age_days >= suppress_days:
                    self._transition(item, "DORMANT", today_str, "auto_advance_post_brief")
                    advanced += 1

            elif state == "REACTIVATED":
                # Reactivated items acknowledged after one brief cycle
                react_date = self._last_transition_date(item, "REACTIVATED")
                if react_date and react_date < today_str:
                    self._transition(item, "ACKNOWLEDGED", today_str, "reactivation_consumed")
                    advanced += 1

        self._data["last_advanced_date"] = today_str
        return advanced

    def process_candidate(
        self,
        item: dict,
        section: str,
        today: date,
    ) -> tuple[str, dict]:
        """Process a candidate brief item through the lifecycle engine.

        Returns:
            (disposition, augmented_item)

        Dispositions:
            "new"          — first observation; surface in new_intelligence_today
            "reactivated"  — was dormant; reactivated by related content
            "suppress"     — already reported; add to suppressed_today
            "pass"         — normal; include in its existing section

        The augmented item gains:
            attribution_type: MEMORY | OBSERVED | SYNTHESIS | HYPOTHESIS
            intelligence_lifecycle: {state, id, first_seen, brief_report_count,
                                      suppression_reason, reactivated_from}
        """
        today_str = today.isoformat()
        intel_id = make_intelligence_id(item.get("title") or "")
        if not intel_id:
            # Title-less items can't be tracked
            item = dict(item)
            item["attribution_type"] = classify_attribution(item)
            return "pass", item

        category = _section_to_category(section)
        entity = _extract_entity_hint(item)
        existing = self.items.get(intel_id)

        item = dict(item)  # defensive copy
        # Preserve attribution_type if already set by the daily_brief pre-pass;
        # only call classify_attribution when the field is absent.
        if not item.get("attribution_type"):
            item["attribution_type"] = classify_attribution(item)

        if existing is None:
            # First observation — NEW state.
            # Also scan all DORMANT items to see if this new item triggers reactivation.
            self._scan_dormant_for_reactivation(item, today_str)
            # DEFECT-022: MEMORY items get stricter suppression rules
            attribution_type = item["attribution_type"]
            if attribution_type == "MEMORY":
                suppression_rules = _DEFAULT_SUPPRESSION_RULES["memory_user_supplied"]
            else:
                suppression_rules = _DEFAULT_SUPPRESSION_RULES.get(
                    category, _DEFAULT_SUPPRESSION_RULES["default"]
                )
            record = {
                "id": intel_id,
                "entity": entity,
                "category": category,
                "headline": (item.get("title") or "")[:200],
                "attribution_type": attribution_type,
                "source_refs": item.get("source_refs") or [],
                "first_seen": today_str,
                "last_verified": today_str,
                "first_reported_in_brief": today_str,
                "last_reported_in_brief": today_str,
                "brief_report_count": 1,
                "confidence": item.get("confidence") or "medium",
                "importance": _importance_from_disposition(item.get("disposition")),
                "lifecycle_state": "NEW",
                "lifecycle_history": [
                    {"state": "NEW", "date": today_str, "reason": "first_observation"}
                ],
                "suppression_rules": suppression_rules,
                "related_ids": [],
                "reactivated_from": None,
                "reactivation_trigger": None,
            }
            self.items[intel_id] = record
            self._new_this_cycle.append(intel_id)
            item["intelligence_lifecycle"] = self._lifecycle_summary(record, None)
            return "new", item

        # Item exists — check lifecycle state
        state = existing.get("lifecycle_state", "DORMANT")

        # RETIRED items are excluded
        if state == "RETIRED":
            item["intelligence_lifecycle"] = self._lifecycle_summary(existing, "retired")
            return "suppress", item

        # NEW items first seen TODAY must re-surface in new_intelligence_today on every
        # brief render that day.  On the morning pipeline the item is registered as NEW
        # (existing is None path above).  When the brief is regenerated later the same day
        # (e.g. via GPT API call), existing is now present with state NEW — but the item
        # is still fresh news and must appear in the "What Todd Doesn't Know Yet" section.
        if state == "NEW" and existing.get("first_seen") == today_str:
            existing["last_verified"] = today_str
            item["intelligence_lifecycle"] = self._lifecycle_summary(existing, None)
            if intel_id not in self._new_this_cycle:
                self._new_this_cycle.append(intel_id)
            return "new", item

        # Check suppression conditions
        should_suppress, suppress_reason = self._evaluate_suppression(existing, item, today_str, category)

        # If the item should be suppressed, check for reactivation first
        if should_suppress:
            reactivation_reason = self._check_reactivation(existing, item, today_str)
            if reactivation_reason:
                self._transition(existing, "REACTIVATED", today_str, reactivation_reason)
                existing["reactivation_trigger"] = (item.get("title") or "")[:200]
                existing["last_verified"] = today_str
                self._bump_report_count_once_per_day(existing, today_str)
                self._reactivated_this_cycle.append(intel_id)
                item["intelligence_lifecycle"] = self._lifecycle_summary(
                    existing, None, reactivated_from=existing.get("first_reported_in_brief")
                )
                return "reactivated", item

            # Truly suppress
            self._suppressed_this_cycle.append({
                "id": intel_id,
                "headline": existing.get("headline") or item.get("title") or "",
                "suppression_reason": suppress_reason,
                "lifecycle_state": state,
                "first_seen": existing.get("first_seen"),
                "category": category,
            })
            existing["last_verified"] = today_str
            item["intelligence_lifecycle"] = self._lifecycle_summary(existing, suppress_reason)
            return "suppress", item

        # Item passes suppression — surface normally
        existing["last_verified"] = today_str
        self._bump_report_count_once_per_day(existing, today_str)
        item["intelligence_lifecycle"] = self._lifecycle_summary(existing, None)
        return "pass", item

    def get_suppressed_today(self) -> list[dict]:
        """Return items suppressed during this brief cycle."""
        return list(self._suppressed_this_cycle)

    def get_new_today(self) -> list[str]:
        """Return IDs of items first seen today."""
        return list(self._new_this_cycle)

    def get_reactivated_today(self) -> list[str]:
        """Return IDs of items reactivated today."""
        return list(self._reactivated_this_cycle)

    def item_count(self) -> int:
        return len(self.items)

    def counts_by_state(self) -> dict[str, int]:
        counts: dict[str, int] = {s: 0 for s in LIFECYCLE_STATES}
        for item in self.items.values():
            state = item.get("lifecycle_state") or "NEW"
            counts[state] = counts.get(state, 0) + 1
        return counts

    # ── Private helpers ───────────────────────────────────────────────────────

    def _transition(self, item: dict, new_state: str, date_str: str, reason: str) -> None:
        item["lifecycle_state"] = new_state
        history = item.setdefault("lifecycle_history", [])
        history.append({"state": new_state, "date": date_str, "reason": reason})

    def _bump_report_count_once_per_day(self, item: dict, today_str: str) -> None:
        """Increment brief_report_count at most once per calendar day.

        advance_cycle() already guards its own state transitions with a
        last_advanced_date check, but this counter had no equivalent guard —
        every call to process_candidate() for an already-seen item bumped it
        unconditionally, so re-running the brief generator multiple times on
        the same day (routine during manual verification/testing) inflated
        it without bound. Real entries were found with counts in the
        hundreds for content only days old, which pushed those items to
        DORMANT/ACKNOWLEDGED with a novelty score of 0-12 — below the
        suppression threshold — hard-filtering genuinely fresh headlines out
        of the brief as if they were stale.
        """
        if item.get("last_reported_in_brief") == today_str:
            return
        item["last_reported_in_brief"] = today_str
        item["brief_report_count"] = int(item.get("brief_report_count") or 0) + 1

    def _last_transition_date(self, item: dict, state: str) -> str | None:
        for entry in reversed(item.get("lifecycle_history") or []):
            if entry.get("state") == state:
                return entry.get("date")
        return None

    def _scan_dormant_for_reactivation(self, trigger_item: dict, today_str: str) -> None:
        """When a new item arrives, scan all DORMANT store items for reactivation.

        A dormant item is reactivated when the new item's title/summary contains
        any of the dormant item's reactivation_keywords.
        """
        trigger_text = " ".join([
            (trigger_item.get("title") or "").lower(),
            (trigger_item.get("summary") or "").lower(),
        ])
        for existing in self.items.values():
            if existing.get("lifecycle_state") != "DORMANT":
                continue
            reason = self._check_reactivation(existing, trigger_item, today_str)
            if reason:
                self._transition(existing, "REACTIVATED", today_str, reason)
                existing["reactivation_trigger"] = (trigger_item.get("title") or "")[:200]
                existing["last_verified"] = today_str
                self._bump_report_count_once_per_day(existing, today_str)
                self._reactivated_this_cycle.append(existing["id"])

    def _evaluate_suppression(
        self, existing: dict, candidate: dict, today_str: str, category: str
    ) -> tuple[bool, str]:
        """Determine if a candidate item should be suppressed.

        Returns (should_suppress, reason_string).
        Items are NOT suppressed when:
          - They are in NEW state (first cycle)
          - disposition == act_today (actionable items always surface)
          - Confidence upgraded vs. existing record AND state is not DORMANT
            (DORMANT items must go through reactivation, not confidence bypass)
          - Brief report count is 0 (never been reported)
        """
        state = existing.get("lifecycle_state", "DORMANT")

        # Never suppress NEW items — they haven't been reported yet
        if state == "NEW":
            return False, ""

        # Never suppress actionable items
        if candidate.get("disposition") == "act_today":
            return False, ""

        # RB-DEFECT-017 Issue #5: Never suppress sent-outreach items awaiting response.
        # These represent active pending loops — suppressing them hides unresolved follow-through.
        _extras = candidate.get("extras") or {}
        if _extras.get("response_status") in ("awaiting_response", "response_overdue"):
            return False, ""
        if _extras.get("sent_followup_state") or _extras.get("is_sent_followup"):
            return False, ""

        # Confidence-upgrade bypass: only for non-DORMANT items.
        # DORMANT items must go through reactivation logic, not confidence bypass,
        # to prevent the same headline resurfacing just because confidence differs.
        if state not in ("DORMANT",):
            old_conf = existing.get("confidence") or "medium"
            new_conf = candidate.get("confidence") or "medium"
            _conf_rank = {"low": 0, "medium": 1, "high": 2}
            if _conf_rank.get(new_conf, 1) > _conf_rank.get(old_conf, 1):
                return False, ""

        # Check suppression window
        rules = existing.get("suppression_rules") or _DEFAULT_SUPPRESSION_RULES.get(
            category, _DEFAULT_SUPPRESSION_RULES["default"]
        )
        suppress_days = int(rules.get("suppress_days") or 14)
        first_reported = existing.get("first_reported_in_brief") or existing.get("first_seen")
        if first_reported:
            age_days = (
                date.fromisoformat(today_str) - date.fromisoformat(first_reported)
            ).days
            if age_days <= suppress_days:
                return True, (
                    f"Reported {age_days} day(s) ago (suppress window: {suppress_days} days). "
                    f"Lifecycle state: {state}."
                )

        # DORMANT items always suppressed outside suppression window unless reactivated
        if state == "DORMANT":
            return True, f"Lifecycle state: DORMANT. No reactivation trigger found."

        return False, ""

    def _check_reactivation(
        self, existing: dict, candidate: dict, today_str: str
    ) -> str | None:
        """Check whether a dormant item should be reactivated by this candidate.

        Returns the reactivation reason string if reactivation is warranted,
        None otherwise.

        Self-reactivation is always blocked — a dormant item should only be
        reactivated by a DIFFERENT, new intelligence item, not by another
        occurrence of the same headline.

        DEFECT-022: MEMORY-attributed items require external corroboration
        (OBSERVED or SYNTHESIS candidate) to reactivate. Keyword match alone
        is insufficient for user-supplied intelligence.
        """
        state = existing.get("lifecycle_state", "DORMANT")
        if state not in ("DORMANT",):
            return None

        # Prevent self-reactivation: same content → same ID → skip
        candidate_id = make_intelligence_id(candidate.get("title") or "")
        if candidate_id == existing.get("id"):
            return None

        # DEFECT-022: MEMORY items require external corroboration
        existing_attribution = existing.get("attribution_type") or ""
        rules = existing.get("suppression_rules") or {}
        if (
            existing_attribution == "MEMORY"
            or rules.get("require_external_corroboration")
        ):
            # Only reactivate MEMORY items when the triggering candidate is
            # externally corroborated (not another MEMORY or HYPOTHESIS item)
            candidate_attribution = classify_attribution(candidate)
            if candidate_attribution not in ("OBSERVED", "SYNTHESIS"):
                return None
            # Candidate is external — proceed to keyword check below with
            # an expanded set of signals that confirm the original fact changed.
            return f"External corroboration received ({candidate_attribution})"

        keywords = [k.lower() for k in (rules.get("reactivation_keywords") or [])]
        if not keywords:
            return None

        text = " ".join([
            (candidate.get("title") or "").lower(),
            (candidate.get("summary") or "").lower(),
        ])

        matched = [k for k in keywords if k in text]
        if matched:
            return f"Reactivation keyword(s) matched: {', '.join(matched[:3])}"
        return None

    @staticmethod
    def _lifecycle_summary(
        record: dict, suppress_reason: str | None, reactivated_from: str | None = None
    ) -> dict:
        return {
            "intelligence_id": record.get("id"),
            "state": record.get("lifecycle_state"),
            "first_seen": record.get("first_seen"),
            "brief_report_count": record.get("brief_report_count") or 1,
            "suppression_reason": suppress_reason,
            "reactivated_from": reactivated_from or record.get("reactivated_from"),
            "reactivation_trigger": record.get("reactivation_trigger"),
        }


# ---------------------------------------------------------------------------
# Convenience function for daily_brief.py
# ---------------------------------------------------------------------------

def process_brief_items(
    store: "IntelligenceStore",
    sections: dict[str, list[dict]],
    today: date,
    source_health: dict | None = None,
) -> tuple[list[dict], list[dict], list[dict], dict]:
    """Run the lifecycle pass over all eligible brief sections.

    Augments items in-place with attribution_type and intelligence_lifecycle.
    Source sections are NOT mutated (items are not removed) — this preserves
    backward compatibility with existing rendering paths.  The GPT rendering
    rules use intelligence_lifecycle.state to determine whether to present an
    item as new, dormant/suppressed, or reactivated.

    Suppressed items are collected in the suppressed_today return value for the
    dedicated suppressed_today section — they still remain in their source
    sections with lifecycle state metadata so old renderers continue to work.

    Returns:
        (new_items, reactivated_items, suppressed_items, source_audit)

    Where:
        new_items        — items in NEW lifecycle state (first observation today)
        reactivated_items — items just transitioned from DORMANT to REACTIVATED
        suppressed_items  — items that were suppressed (for the suppressed_today section)
        source_audit      — structured source audit block
    """
    new_items: list[dict] = []
    reactivated_items: list[dict] = []

    for section_key, item_list in sections.items():
        if section_key not in _LIFECYCLE_ELIGIBLE_SECTIONS:
            continue
        for idx, item in enumerate(item_list):
            disposition, augmented = store.process_candidate(item, section_key, today)
            # Update item in-place with lifecycle metadata (no removal)
            item_list[idx] = augmented
            if disposition == "new":
                new_items.append(augmented)
            elif disposition == "reactivated":
                reactivated_items.append(augmented)
            # "suppress" and "pass" items stay in their source section;
            # suppressed items are already recorded in store._suppressed_this_cycle

    # Also add attribution_type to non-lifecycle sections (just labelling, no suppression)
    for section_key, item_list in sections.items():
        if section_key in _LIFECYCLE_ELIGIBLE_SECTIONS:
            continue
        for item in item_list:
            if "attribution_type" not in item:
                item["attribution_type"] = classify_attribution(item)

    suppressed_items = store.get_suppressed_today()
    source_audit = build_source_audit(source_health)
    return new_items, reactivated_items, suppressed_items, source_audit


# ---------------------------------------------------------------------------
# DEFECT-022: Novelty scoring
# ---------------------------------------------------------------------------

def compute_novelty_score(item: dict, store: "IntelligenceStore", today: date) -> int:
    """Compute a 0–100 novelty score for a canonical brief item.

    Novelty answers: "Why is the user seeing this today that they did not know
    yesterday?"

    Score bands:
      90–100  first observation today (NEW state)
      70–89   reactivated by external evidence (REACTIVATED from OBSERVED/SYNTHESIS)
      50–69   previously reported but has a new signal or confidence upgrade
      20–49   periodically surfaced actionable item (act_today disposition)
      10–19   previously reported, no change (ACKNOWLEDGED)
      1–9     user-supplied, no external corroboration (MEMORY, DORMANT)
      0       dormant, should not surface

    The score is written into item["novelty_score"] and item["novelty_reason"].
    """
    intel_id = make_intelligence_id(item.get("title") or "")
    if not intel_id:
        item.setdefault("novelty_score", 50)
        item.setdefault("novelty_reason", "untracked_item")
        return 50

    record = store.items.get(intel_id)
    attribution = item.get("attribution_type") or classify_attribution(item)
    disposition = item.get("disposition") or "monitor"

    if record is None:
        # First observation
        score, reason = 95, "first_observation"
    else:
        state = record.get("lifecycle_state", "DORMANT")
        report_count = int(record.get("brief_report_count") or 0)

        if state == "NEW":
            score, reason = 95, "first_observation"
        elif state == "REACTIVATED":
            reactivation_trigger = record.get("reactivation_trigger") or ""
            reactivating_attribution = item.get("attribution_type") or "OBSERVED"
            if reactivating_attribution in ("OBSERVED", "SYNTHESIS"):
                score, reason = 75, "reactivated_external_evidence"
            else:
                score, reason = 45, "reactivated_keyword_match"
        elif state == "DORMANT":
            if attribution == "MEMORY":
                score, reason = 3, "dormant_user_supplied_no_external_corroboration"
            else:
                score, reason = 0, "dormant_no_reactivation_trigger"
        elif state == "ACKNOWLEDGED":
            if attribution == "MEMORY":
                score, reason = 5, "acknowledged_user_supplied"
            elif disposition == "act_today":
                score, reason = 35, "acknowledged_actionable"
            else:
                score, reason = 12, "acknowledged_previously_reported"
        elif state == "RETIRED":
            score, reason = 0, "retired"
        else:
            score, reason = 25, f"state_{state.lower()}"

    # Boost for actionable items that are not dormant
    if disposition == "act_today" and score > 15:
        score = max(score, 40)
        reason = reason + "+act_today_boost"

    item["novelty_score"] = score
    item["novelty_reason"] = reason
    return score


# ---------------------------------------------------------------------------
# DEFECT-022: Hard section filtering
# ---------------------------------------------------------------------------

# Minimum novelty score for an item to appear in its source section.
# Items below this threshold are filtered out; they remain in suppressed_today.
NOVELTY_THRESHOLD = 20

# Sections exempt from novelty filtering — system/meta sections always render,
# as do sections that are primary proof-of-value (autonomous discovery).
_NOVELTY_FILTER_EXEMPT = frozenset({
    "resource_verification_and_freshness_status",
    "daily_prep_summary",
    "source_audit",
    "suppressed_today",
    "new_intelligence_today",
    "reactivated_intelligence",
    "recommended_actions",
    "decision_layer",
    "information_debt_queue",
    "pending_graph_mutations",
    "action_lifecycle_state",
    "active_knowledge_assets",
    "sent_followups_awaiting_response",
    "proposed_relationship_mutations",
    # Autonomous discovery is the primary proof-of-value section.
    # Items here are filtered by their own generation rules, not lifecycle.
    "autonomous_discovery_evidence",
    "what_rb_found_without_you_telling_it",
    "operational_changes_from_connected_sources",
    # RB 9.71: watchlist_intelligence's "No Change" rollups and per-entity
    # status items are RB-INTEL-021's mandatory-coverage proof — they are
    # SUPPOSED to repeat every cycle (silence would mean "not scanned").
    # Novelty-filtering them as DORMANT/ACKNOWLEDGED defeats that guarantee
    # (it dropped the coverage rollups entirely in RB 9.70's brief).
    # _watchlist_entity_status()'s own prior/today comparison already
    # surfaces genuine status changes via "status_changed"/"prior_status".
    "watchlist_intelligence",
    # INTELLIGENCE_BRIEF_CANONICAL.md's headline sections (A-D) have their own
    # freshness gate (7-day pub_date window) and a mandatory VOLUME FLOOR of
    # 5-7 items per section. Novelty scoring rates any ACKNOWLEDGED item
    # (previously seen, even once) at 12 -- below the 20-point survival
    # threshold -- so hard-filtering these sections deletes still-fresh
    # headlines the moment they've been shown once, silently gutting the
    # volume floor (e.g. 12 fresh candidates -> 1 survivor). Recycling
    # prevention for these sections is the pub_date freshness gate itself;
    # lifecycle classification still runs for new_intelligence_today purposes,
    # it just shouldn't delete items from the headline sections themselves.
    "world_national_headlines",
    "restaurant_industry_headlines",
    "restaurant_technology_headlines",
})


def filter_suppressed_from_sections(
    store: "IntelligenceStore",
    sections: dict[str, list[dict]],
    today: date,
    *,
    novelty_threshold: int = NOVELTY_THRESHOLD,
) -> dict[str, int]:
    """DEFECT-022: Physically remove suppressed/low-novelty items from sections.

    Called after process_brief_items(). Iterates all lifecycle-eligible sections
    and removes items whose lifecycle state is DORMANT or ACKNOWLEDGED AND whose
    novelty score is below the threshold.

    Items are NOT removed from suppressed_today, new_intelligence_today, or
    recommended_actions — these sections always render.

    Returns a dict of {section_key: count_filtered} for telemetry.
    """
    filtered_counts: dict[str, int] = {}

    for section_key, item_list in sections.items():
        if section_key in _NOVELTY_FILTER_EXEMPT:
            continue
        if section_key not in _LIFECYCLE_ELIGIBLE_SECTIONS:
            # Score non-lifecycle sections but don't filter
            for item in item_list:
                compute_novelty_score(item, store, today)
            continue

        survivors: list[dict] = []
        removed = 0
        for item in item_list:
            score = compute_novelty_score(item, store, today)
            lc = item.get("intelligence_lifecycle") or {}
            state = lc.get("state") or ""

            # Always keep act_today items regardless of novelty
            if item.get("disposition") == "act_today":
                survivors.append(item)
                continue

            # Always keep items awaiting response (active loops)
            extras = item.get("extras") or {}
            if extras.get("response_status") in ("awaiting_response", "response_overdue"):
                survivors.append(item)
                continue

            # Filter DORMANT/ACKNOWLEDGED items below threshold.
            # Only filter items that have an actual lifecycle record (state != "").
            # Untracked items (no store record, state="") are never filtered.
            if state in ("DORMANT", "ACKNOWLEDGED") and score < novelty_threshold:
                removed += 1
                continue

            survivors.append(item)

        if removed:
            filtered_counts[section_key] = removed
            sections[section_key] = survivors

    return filtered_counts


# ---------------------------------------------------------------------------
# Section-level confidence scoring
# ---------------------------------------------------------------------------

def section_confidence_scores(sections: dict[str, list[dict]]) -> dict[str, str]:
    """Derive section-level confidence labels from item confidence values.

    Uses modal confidence with a penalty for stale sources.
    """
    _DOMAIN_SECTIONS = {
        "relationship_intelligence": [
            "last_24h_relationship_signals",
            "email_intelligence_harvest",
            "relationship_operational_signal_review",
            "sent_followups_awaiting_response",
        ],
        "restaurant_technology": [
            "industry_brief",
            "strategic_industry_signals",
        ],
        "macro_environment": [
            "world_macro_macroeconomic_impact",
            "macro_pressure_stack",
        ],
        "hiring_market": [
            "autonomous_discovery_evidence",
            "overnight_delta_intelligence",
        ],
    }
    scores: dict[str, str] = {}
    _rank = {"high": 2, "medium": 1, "low": 0}
    _unrank = {2: "high", 1: "medium", 0: "low"}

    for domain, section_keys in _DOMAIN_SECTIONS.items():
        conf_vals: list[int] = []
        for key in section_keys:
            for item in sections.get(key) or []:
                c = (item.get("confidence") or "medium").lower()
                conf_vals.append(_rank.get(c, 1))
                if item.get("freshness") == "stale_source_limited":
                    conf_vals.append(0)
        if not conf_vals:
            scores[domain] = "unknown"
        else:
            avg = sum(conf_vals) / len(conf_vals)
            scores[domain] = _unrank[min(2, max(0, round(avg)))]

    return scores


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _importance_from_disposition(disposition: str | None) -> str:
    return {
        "act_today": "high",
        "ask_todd": "high",
        "monitor": "medium",
        "ignore": "low",
    }.get(disposition or "monitor", "medium")
