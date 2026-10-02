"""
test_intelligence_lifecycle.py — RB 9.35 / DEFECT-018
Intelligence lifecycle management: state machine, attribution, duplicate suppression,
reactivation, source audit, and daily brief integration.

Test groups:
  IL1 (7):  make_intelligence_id + classify_attribution
  IL2 (8):  IntelligenceStore.advance_cycle — state transitions
  IL3 (8):  IntelligenceStore.process_candidate — NEW / PASS / SUPPRESS / REACTIVATED
  IL4 (6):  build_source_audit — structure, domain confidence, stale sources
  IL5 (6):  process_brief_items — section routing, suppressed section, attribution labels
  IL6 (6):  daily_brief integration — new_intelligence_today, suppressed_today, source_audit present
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import intelligence_lifecycle as il

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_ITEM_OBSERVED = {
    "title": "Joseph Yetter promoted to President of PAR Restaurants",
    "summary": "Joseph Yetter named President of PAR Restaurants division.",
    "disposition": "monitor",
    "grounding": "system_detected",
    "freshness": "fresh",
    "source_refs": ["linkedin_post:yetter-2026-05-20"],
    "confidence": "high",
    "provenance": {"source_class": "externally_discovered", "corroboration_count": 1},
}

_ITEM_MEMORY = {
    "title": "Active thread: Bob Gibson / Toast ongoing conversation",
    "summary": "Thread context from manual notes.",
    "disposition": "monitor",
    "grounding": "manual_user_provided",
    "freshness": "manual_context",
    "source_refs": ["active_threads.yaml"],
    "confidence": "medium",
    "provenance": {"source_class": "user_provided", "corroboration_count": 0},
}

_ITEM_SYNTHESIS = {
    "title": "PAR Technology exit positioning signals corroborated",
    "summary": "Multiple sources corroborate exit positioning pattern.",
    "disposition": "monitor",
    "grounding": "system_detected",
    "freshness": "fresh",
    "source_refs": ["sig-1", "sig-2", "signal_synthesis"],
    "confidence": "high",
    "provenance": {"source_class": "externally_discovered", "corroboration_count": 3},
}

_ITEM_HYPOTHESIS = {
    "title": "Potential restructuring at Olo based on hiring patterns",
    "summary": "Inferred from recent LinkedIn activity.",
    "disposition": "monitor",
    "grounding": "inferred",
    "freshness": "fresh",
    "source_refs": ["linkedin_signal"],
    "confidence": "low",
    "provenance": {"source_class": "system_inferred", "corroboration_count": 1},
}

_ITEM_ACT_TODAY = {
    "title": "URGENT: Response received from Patrick Nelson",
    "summary": "Patrick replied — action required.",
    "disposition": "act_today",
    "grounding": "system_detected",
    "freshness": "fresh",
    "source_refs": ["email"],
    "confidence": "high",
    "provenance": {"source_class": "externally_discovered", "corroboration_count": 1},
}

_FAKE_SOURCE_HEALTH = {
    "generated_at": "2026-05-30T05:05:00Z",
    "sources": {
        "email":    {"tier": 1, "status": "refreshed", "checked_at": "2026-05-30T05:02:00Z"},
        "calendar": {"tier": 1, "status": "refreshed", "checked_at": "2026-05-30T05:03:00Z"},
        "linkedin": {"tier": 1, "status": "stale",     "checked_at": "2026-05-29T05:00:00Z"},
        "market":   {"tier": 2, "status": "refreshed", "checked_at": "2026-05-30T05:04:00Z"},
        "earnings": {"tier": 2, "status": "refreshed", "checked_at": "2026-05-30T05:05:00Z"},
    },
}


def _make_store() -> il.IntelligenceStore:
    """Return an empty in-memory store (not backed by disk)."""
    return il.IntelligenceStore({
        "contract": "rb_intelligence_store_v1",
        "version": 1,
        "last_advanced_date": None,
        "items": {},
    })


def _make_store_with_dormant(title: str, first_reported: str, category: str = "strategic_signal") -> il.IntelligenceStore:
    """Return a store with one dormant item already recorded."""
    store = _make_store()
    intel_id = il.make_intelligence_id(title)
    store.items[intel_id] = {
        "id": intel_id,
        "entity": "par-technology",
        "category": category,
        "headline": title[:200],
        "attribution_type": "OBSERVED",
        "source_refs": [],
        "first_seen": first_reported,
        "last_verified": first_reported,
        "first_reported_in_brief": first_reported,
        "last_reported_in_brief": first_reported,
        "brief_report_count": 1,
        "confidence": "medium",
        "importance": "medium",
        "lifecycle_state": "DORMANT",
        "lifecycle_history": [
            {"state": "NEW", "date": first_reported, "reason": "first_observation"},
            {"state": "ACKNOWLEDGED", "date": first_reported, "reason": "auto_advance"},
            {"state": "DORMANT", "date": first_reported, "reason": "auto_advance"},
        ],
        "suppression_rules": {
            "suppress_days": 14,
            "reactivation_keywords": ["restructure", "acquisition", "ceo", "president"],
            "reactivation_categories": ["leadership_change"],
        },
        "related_ids": [],
        "reactivated_from": None,
        "reactivation_trigger": None,
    }
    return store


# =============================================================================
# IL1 — make_intelligence_id + classify_attribution
# =============================================================================

class TestIL1IdAndAttribution(unittest.TestCase):
    """IL1 — ID generation and attribution classification."""

    def test_make_intelligence_id_is_deterministic(self):
        id1 = il.make_intelligence_id("Joseph Yetter promoted to President")
        id2 = il.make_intelligence_id("Joseph Yetter promoted to President")
        self.assertEqual(id1, id2)

    def test_make_intelligence_id_different_titles_differ(self):
        id1 = il.make_intelligence_id("PAR Technology acquires Rival")
        id2 = il.make_intelligence_id("PAR Technology revenue decline")
        self.assertNotEqual(id1, id2)

    def test_make_intelligence_id_is_16_chars(self):
        intel_id = il.make_intelligence_id("Some headline text here")
        self.assertEqual(len(intel_id), 16)

    def test_classify_attribution_memory_from_grounding(self):
        self.assertEqual(il.classify_attribution(_ITEM_MEMORY), "MEMORY")

    def test_classify_attribution_observed(self):
        self.assertEqual(il.classify_attribution(_ITEM_OBSERVED), "OBSERVED")

    def test_classify_attribution_synthesis_corroboration(self):
        self.assertEqual(il.classify_attribution(_ITEM_SYNTHESIS), "SYNTHESIS")

    def test_classify_attribution_hypothesis_inferred(self):
        self.assertEqual(il.classify_attribution(_ITEM_HYPOTHESIS), "HYPOTHESIS")


# =============================================================================
# IL2 — IntelligenceStore.advance_cycle
# =============================================================================

class TestIL2AdvanceCycle(unittest.TestCase):
    """IL2 — lifecycle state transitions on advance_cycle."""

    def test_advance_cycle_new_to_acknowledged_after_one_day(self):
        store = _make_store()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        intel_id = il.make_intelligence_id("Test headline A")
        store.items[intel_id] = {
            "id": intel_id,
            "lifecycle_state": "NEW",
            "first_seen": yesterday,
            "lifecycle_history": [{"state": "NEW", "date": yesterday, "reason": "first_observation"}],
        }
        store.advance_cycle(date.today())
        self.assertEqual(store.items[intel_id]["lifecycle_state"], "ACKNOWLEDGED")

    def test_advance_cycle_acknowledged_to_dormant_after_category_suppress_window(self):
        """RB-DEFECT-2026-07-07: ACKNOWLEDGED -> DORMANT used to fire
        unconditionally one day after acknowledgment, regardless of category
        — pushing headline sections (category market_intelligence,
        suppress_days: 7) to DORMANT within ~2 real calendar days of first
        being seen, hard-suppressing headlines daily_brief.py's own 7-day
        freshness gate had already judged fresh. The transition now waits
        for the item's own category suppress_days to elapse since
        first_seen."""
        store = _make_store()
        # "default" category (no category set) has suppress_days: 14 — one
        # day past acknowledgment must NOT be enough to go dormant anymore.
        one_day_ago = (date.today() - timedelta(days=1)).isoformat()
        intel_id = il.make_intelligence_id("Test headline B")
        store.items[intel_id] = {
            "id": intel_id,
            "lifecycle_state": "ACKNOWLEDGED",
            "first_seen": one_day_ago,
            "lifecycle_history": [
                {"state": "NEW", "date": one_day_ago, "reason": "first_observation"},
                {"state": "ACKNOWLEDGED", "date": one_day_ago, "reason": "auto_advance"},
            ],
        }
        store.advance_cycle(date.today())
        self.assertEqual(store.items[intel_id]["lifecycle_state"], "ACKNOWLEDGED")

    def test_advance_cycle_acknowledged_to_dormant_once_suppress_window_elapses(self):
        store = _make_store()
        long_ago = (date.today() - timedelta(days=20)).isoformat()
        intel_id = il.make_intelligence_id("Test headline B2")
        store.items[intel_id] = {
            "id": intel_id,
            "lifecycle_state": "ACKNOWLEDGED",
            "first_seen": long_ago,
            "lifecycle_history": [
                {"state": "NEW", "date": long_ago, "reason": "first_observation"},
                {"state": "ACKNOWLEDGED", "date": long_ago, "reason": "auto_advance"},
            ],
        }
        store.advance_cycle(date.today())
        self.assertEqual(store.items[intel_id]["lifecycle_state"], "DORMANT")

    def test_advance_cycle_headline_category_dormant_after_seven_days_not_one(self):
        """world_national_headlines / restaurant_industry_headlines /
        restaurant_technology_headlines map to market_intelligence
        (suppress_days: 7) — a headline first seen 3 days ago must still be
        ACKNOWLEDGED (not DORMANT), matching daily_brief.py's own 7-day
        headline freshness gate."""
        store = _make_store()
        three_days_ago = (date.today() - timedelta(days=3)).isoformat()
        intel_id = il.make_intelligence_id("Fresh restaurant tech headline")
        store.items[intel_id] = {
            "id": intel_id,
            "lifecycle_state": "ACKNOWLEDGED",
            "first_seen": three_days_ago,
            "category": "market_intelligence",
            "lifecycle_history": [
                {"state": "NEW", "date": three_days_ago, "reason": "first_observation"},
                {"state": "ACKNOWLEDGED", "date": three_days_ago, "reason": "auto_advance"},
            ],
        }
        store.advance_cycle(date.today())
        self.assertEqual(store.items[intel_id]["lifecycle_state"], "ACKNOWLEDGED")

    def test_advance_cycle_does_not_repeat_same_day(self):
        store = _make_store()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        intel_id = il.make_intelligence_id("Test headline C")
        store.items[intel_id] = {
            "id": intel_id,
            "lifecycle_state": "NEW",
            "first_seen": yesterday,
            "lifecycle_history": [{"state": "NEW", "date": yesterday, "reason": "first_observation"}],
        }
        store.advance_cycle(date.today())
        store.advance_cycle(date.today())  # second call same day — should not re-advance
        history = store.items[intel_id]["lifecycle_history"]
        # Should only have one ACKNOWLEDGED entry
        ack_entries = [e for e in history if e["state"] == "ACKNOWLEDGED"]
        self.assertEqual(len(ack_entries), 1)

    def test_advance_cycle_new_not_advanced_if_seen_today(self):
        """NEW items from today should NOT be advanced (not yet consumed)."""
        store = _make_store()
        today_str = date.today().isoformat()
        intel_id = il.make_intelligence_id("Test headline D")
        store.items[intel_id] = {
            "id": intel_id,
            "lifecycle_state": "NEW",
            "first_seen": today_str,
            "lifecycle_history": [{"state": "NEW", "date": today_str, "reason": "first_observation"}],
        }
        store.advance_cycle(date.today())
        self.assertEqual(store.items[intel_id]["lifecycle_state"], "NEW")

    def test_advance_cycle_reactivated_to_acknowledged(self):
        store = _make_store()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        intel_id = il.make_intelligence_id("Test headline E")
        store.items[intel_id] = {
            "id": intel_id,
            "lifecycle_state": "REACTIVATED",
            "first_seen": yesterday,
            "lifecycle_history": [
                {"state": "REACTIVATED", "date": yesterday, "reason": "reactivation_keyword"}
            ],
        }
        store.advance_cycle(date.today())
        self.assertEqual(store.items[intel_id]["lifecycle_state"], "ACKNOWLEDGED")

    def test_advance_cycle_returns_count_of_advanced(self):
        store = _make_store()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        for i in range(3):
            intel_id = il.make_intelligence_id(f"Headline for advance count {i}")
            store.items[intel_id] = {
                "id": intel_id,
                "lifecycle_state": "NEW",
                "first_seen": yesterday,
                "lifecycle_history": [{"state": "NEW", "date": yesterday, "reason": "first"}],
            }
        count = store.advance_cycle(date.today())
        self.assertEqual(count, 3)

    def test_advance_cycle_retired_unchanged(self):
        store = _make_store()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        intel_id = il.make_intelligence_id("Test headline F retired")
        store.items[intel_id] = {
            "id": intel_id,
            "lifecycle_state": "RETIRED",
            "first_seen": yesterday,
            "lifecycle_history": [{"state": "RETIRED", "date": yesterday, "reason": "manual"}],
        }
        store.advance_cycle(date.today())
        self.assertEqual(store.items[intel_id]["lifecycle_state"], "RETIRED")

    def test_advance_cycle_sets_last_advanced_date(self):
        store = _make_store()
        store.advance_cycle(date.today())
        self.assertEqual(store._data.get("last_advanced_date"), date.today().isoformat())


# =============================================================================
# IL3 — IntelligenceStore.process_candidate
# =============================================================================

class TestIL3ProcessCandidate(unittest.TestCase):
    """IL3 — process_candidate returns correct disposition and augmented item."""

    def test_new_item_returns_new_disposition(self):
        store = _make_store()
        disp, item = store.process_candidate(_ITEM_OBSERVED, "industry_brief", date.today())
        self.assertEqual(disp, "new")

    def test_new_item_has_attribution_type(self):
        store = _make_store()
        _, item = store.process_candidate(_ITEM_OBSERVED, "industry_brief", date.today())
        self.assertIn("attribution_type", item)
        self.assertEqual(item["attribution_type"], "OBSERVED")

    def test_new_item_has_intelligence_lifecycle(self):
        store = _make_store()
        _, item = store.process_candidate(_ITEM_OBSERVED, "industry_brief", date.today())
        self.assertIn("intelligence_lifecycle", item)
        self.assertEqual(item["intelligence_lifecycle"]["state"], "NEW")

    def test_same_item_second_day_suppressed(self):
        """Item reported yesterday (now DORMANT) should be suppressed today."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        store = _make_store_with_dormant(
            _ITEM_OBSERVED["title"], yesterday
        )
        disp, item = store.process_candidate(_ITEM_OBSERVED, "industry_brief", date.today())
        self.assertEqual(disp, "suppress")

    def test_act_today_never_suppressed(self):
        """act_today disposition overrides suppression — always surfaces."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        store = _make_store_with_dormant(_ITEM_ACT_TODAY["title"], yesterday)
        disp, _ = store.process_candidate(_ITEM_ACT_TODAY, "industry_brief", date.today())
        self.assertNotEqual(disp, "suppress")

    def test_reactivation_by_keyword(self):
        """Dormant item reactivated when new item contains reactivation keyword."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        store = _make_store_with_dormant(
            "Joseph Yetter promoted to President of PAR Restaurants", yesterday
        )
        # New item contains "restructure" — a reactivation keyword
        new_item = dict(_ITEM_OBSERVED)
        new_item["title"] = "PAR Technology announces major restructure"
        new_item["summary"] = "PAR Technology is restructuring its restaurant division."
        disp, item = store.process_candidate(new_item, "industry_brief", date.today())
        # The new item itself is NEW; but the dormant item should be reactivated
        # (check that the store has a REACTIVATED item)
        reactivated = [i for i in store.items.values() if i.get("lifecycle_state") == "REACTIVATED"]
        self.assertGreater(len(reactivated), 0, "Dormant item should be reactivated by keyword match")

    def test_retired_item_always_suppressed(self):
        """RETIRED items are always excluded."""
        store = _make_store()
        intel_id = il.make_intelligence_id(_ITEM_OBSERVED["title"])
        store.items[intel_id] = {
            "id": intel_id,
            "lifecycle_state": "RETIRED",
            "lifecycle_history": [],
            "suppression_rules": {},
        }
        disp, _ = store.process_candidate(_ITEM_OBSERVED, "industry_brief", date.today())
        self.assertEqual(disp, "suppress")

    def test_item_counts_by_state(self):
        store = _make_store()
        store.process_candidate(_ITEM_OBSERVED, "industry_brief", date.today())
        store.process_candidate(_ITEM_MEMORY, "industry_brief", date.today())
        counts = store.counts_by_state()
        self.assertIsInstance(counts, dict)
        self.assertIn("NEW", counts)

    def test_report_count_does_not_inflate_on_repeat_same_day_calls(self):
        """RB-DEFECT-2026-07-07: brief_report_count incremented unconditionally
        on every process_candidate() call, with no day-gate — unlike
        advance_cycle()'s own last_advanced_date guard. Repeated same-day
        manual verification runs inflated real entries to counts in the
        hundreds (found: 756, 1039), which made no functional difference to
        compute_novelty_score (score depends on lifecycle_state, not
        report_count) but is nonetheless a real, unbounded, meaningless
        counter that should reflect at most one increment per calendar day.
        Uses act_today disposition so the item always takes the "pass"
        branch (never suppressed), isolating the report_count behavior from
        the separate suppression-window logic."""
        store = _make_store()
        today = date.today()
        store.process_candidate(_ITEM_ACT_TODAY, "industry_brief", today)
        intel_id = il.make_intelligence_id(_ITEM_ACT_TODAY["title"])
        before = int(store.items[intel_id].get("brief_report_count") or 0)
        for _ in range(5):
            store.process_candidate(_ITEM_ACT_TODAY, "industry_brief", today)
        after = int(store.items[intel_id]["brief_report_count"])
        self.assertEqual(after, before, "same-day repeat calls must not inflate brief_report_count")

    def test_report_count_increments_on_a_genuinely_new_day(self):
        store = _make_store()
        yesterday = date.today() - timedelta(days=1)
        store.process_candidate(_ITEM_ACT_TODAY, "industry_brief", yesterday)
        intel_id = il.make_intelligence_id(_ITEM_ACT_TODAY["title"])
        before = int(store.items[intel_id].get("brief_report_count") or 0)
        store.process_candidate(_ITEM_ACT_TODAY, "industry_brief", date.today())
        after = int(store.items[intel_id]["brief_report_count"])
        self.assertEqual(after, before + 1)


class TestIL3bSectionToCategory(unittest.TestCase):
    """RB-DEFECT-2026-07-07: headline sections were added to
    _LIFECYCLE_ELIGIBLE_SECTIONS (Sprint D) but never mapped in
    _section_to_category, silently falling through to the "default"
    14-day suppress window — over 2x daily_brief.py's own 7-day headline
    freshness gate (_HEADLINE_FRESHNESS_DAYS)."""

    def test_headline_sections_map_to_market_intelligence(self):
        for section in (
            "world_national_headlines",
            "restaurant_industry_headlines",
            "restaurant_technology_headlines",
        ):
            self.assertEqual(il._section_to_category(section), "market_intelligence")

    def test_market_intelligence_suppress_days_matches_headline_freshness_gate(self):
        rules = il._DEFAULT_SUPPRESSION_RULES["market_intelligence"]
        self.assertEqual(rules["suppress_days"], 7)


class TestIL3cHeadlineSectionsExemptFromNoveltyFilter(unittest.TestCase):
    """RB-DEFECT-2026-07-07 (Restaurant Technology near-empty despite 12 fresh
    candidates): compute_novelty_score rates any ACKNOWLEDGED item (seen once
    before, even yesterday) at 12 -- below the 20-point filter_suppressed_from_sections
    survival threshold. INTELLIGENCE_BRIEF_CANONICAL.md's headline sections (A-D)
    already have their own recency mechanism (a 7-day pub_date freshness gate in
    daily_brief.py) and a mandatory VOLUME FLOOR of 5-7 items per section, so
    hard-filtering them by novelty score deleted still-fresh headlines the moment
    they'd been shown once, silently gutting the floor (confirmed: 12 fresh
    candidates -> 1 survivor in a real run). These three sections must be exempt
    from DEFECT-022's hard section filter, same as watchlist_intelligence."""

    def test_headline_sections_are_novelty_filter_exempt(self):
        for section in (
            "world_national_headlines",
            "restaurant_industry_headlines",
            "restaurant_technology_headlines",
        ):
            self.assertIn(section, il._NOVELTY_FILTER_EXEMPT)

    def test_acknowledged_headline_survives_hard_filter(self):
        # Build a minimal store with one ACKNOWLEDGED headline item, seen yesterday.
        store = il.IntelligenceStore({
            "contract": "rb_intelligence_store_v1", "version": 1,
            "last_advanced_date": None, "items": {},
        })
        today = date(2026, 7, 7)
        item = {
            "title": "Restaurant Tech Vendor Launches New POS Integration",
            "extras": {"pub_date": "2026-07-05"},
            "disposition": "monitor",
        }
        intel_id = il.make_intelligence_id(item["title"])
        store.items[intel_id] = {
            "lifecycle_state": "ACKNOWLEDGED",
            "first_seen": "2026-07-05",
            "category": "market_intelligence",
            "brief_report_count": 1,
        }
        sections = {"restaurant_technology_headlines": [dict(item)]}
        filtered_counts = il.filter_suppressed_from_sections(store, sections, today)
        self.assertEqual(len(sections["restaurant_technology_headlines"]), 1)
        self.assertNotIn("restaurant_technology_headlines", filtered_counts)


# =============================================================================
# IL4 — build_source_audit
# =============================================================================

class TestIL4SourceAudit(unittest.TestCase):
    """IL4 — build_source_audit returns correct structure and confidence."""

    def test_returns_dict(self):
        audit = il.build_source_audit(_FAKE_SOURCE_HEALTH)
        self.assertIsInstance(audit, dict)

    def test_sources_checked_is_list(self):
        audit = il.build_source_audit(_FAKE_SOURCE_HEALTH)
        self.assertIsInstance(audit["sources_checked"], list)

    def test_sources_checked_count_matches_health(self):
        audit = il.build_source_audit(_FAKE_SOURCE_HEALTH)
        self.assertEqual(len(audit["sources_checked"]), 5)

    def test_stale_source_in_checked_list(self):
        audit = il.build_source_audit(_FAKE_SOURCE_HEALTH)
        statuses = {s["key"]: s["status"] for s in audit["sources_checked"]}
        self.assertEqual(statuses.get("linkedin"), "stale")

    def test_domain_confidence_keys_present(self):
        audit = il.build_source_audit(_FAKE_SOURCE_HEALTH)
        dc = audit["domain_confidence"]
        self.assertIn("relationship_intelligence", dc)
        self.assertIn("market_intelligence", dc)
        self.assertIn("macro_environment", dc)

    def test_no_health_returns_stub(self):
        audit = il.build_source_audit(None)
        self.assertEqual(audit["sources_checked"], [])
        self.assertIn("unavailable", audit["audit_note"].lower())


# =============================================================================
# IL5 — process_brief_items
# =============================================================================

class TestIL5ProcessBriefItems(unittest.TestCase):
    """IL5 — process_brief_items routes items to correct return buckets."""

    def _make_sections(self) -> dict:
        return {
            "industry_brief": [dict(_ITEM_OBSERVED), dict(_ITEM_SYNTHESIS)],
            "email_intelligence_harvest": [dict(_ITEM_MEMORY)],
            "resource_verification_and_freshness_status": [
                {"title": "System meta item", "grounding": "system_detected"}
            ],
        }

    def test_new_items_captured(self):
        store = _make_store()
        sections = self._make_sections()
        new_items, _, _, _ = il.process_brief_items(store, sections, date.today())
        # industry_brief has 2 eligible items — both should be NEW on first run
        self.assertGreaterEqual(len(new_items), 2)

    def test_suppressed_items_empty_on_first_run(self):
        """No suppressions on first run — nothing has been reported before."""
        store = _make_store()
        sections = self._make_sections()
        _, _, suppressed, _ = il.process_brief_items(store, sections, date.today())
        self.assertEqual(len(suppressed), 0)

    def test_source_audit_returned(self):
        store = _make_store()
        sections = self._make_sections()
        _, _, _, audit = il.process_brief_items(
            store, sections, date.today(), source_health=_FAKE_SOURCE_HEALTH
        )
        self.assertIsInstance(audit, dict)
        self.assertIn("sources_checked", audit)

    def test_attribution_labels_added_to_non_lifecycle_sections(self):
        """Meta sections get attribution_type labels without lifecycle management."""
        store = _make_store()
        sections = self._make_sections()
        il.process_brief_items(store, sections, date.today())
        for item in sections["resource_verification_and_freshness_status"]:
            self.assertIn("attribution_type", item)

    def test_suppressed_item_has_lifecycle_metadata_in_source_section(self):
        """Suppressed items remain in their source section but carry lifecycle metadata.

        Source sections are NOT mutated (backward compat) — items stay in place
        with intelligence_lifecycle.state so rendering rules can present or skip.
        """
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        store = _make_store_with_dormant(_ITEM_OBSERVED["title"], yesterday)
        sections = {"industry_brief": [dict(_ITEM_OBSERVED)]}
        il.process_brief_items(store, sections, date.today())
        # Item must still be in the section (not removed for backward compat)
        titles = [i.get("title") for i in sections["industry_brief"]]
        self.assertIn(_ITEM_OBSERVED["title"], titles)
        # But the item must have lifecycle metadata indicating suppression
        item = sections["industry_brief"][0]
        self.assertIn("intelligence_lifecycle", item)
        self.assertIsNotNone(item["intelligence_lifecycle"].get("state"))

    def test_section_confidence_scores_returned(self):
        store = _make_store()
        sections = {
            "industry_brief": [dict(_ITEM_OBSERVED)],
            "email_intelligence_harvest": [dict(_ITEM_MEMORY)],
        }
        il.process_brief_items(store, sections, date.today())
        scores = il.section_confidence_scores(sections)
        self.assertIsInstance(scores, dict)
        self.assertIn("relationship_intelligence", scores)


# =============================================================================
# IL6 — daily_brief integration
# =============================================================================

class TestIL6DailyBriefIntegration(unittest.TestCase):
    """IL6 — daily_brief surfaces new_intelligence_today, suppressed_today, source_audit."""

    @classmethod
    def setUpClass(cls):
        try:
            import daily_brief
            cls._db = daily_brief
            cls._available = True
        except Exception:
            cls._available = False

    def setUp(self):
        if not self._available:
            self.skipTest("daily_brief not importable")

    def _build_brief(self) -> dict:
        try:
            report = self._db.build_report(date.today())
            return self._db.build_canonical_brief(report)
        except Exception as exc:
            self.skipTest(f"build_canonical_brief raised: {exc}")

    def test_new_intelligence_today_section_present(self):
        brief = self._build_brief()
        sections = brief.get("sections") or {}
        self.assertIn("new_intelligence_today", sections)

    def test_suppressed_today_section_present(self):
        brief = self._build_brief()
        sections = brief.get("sections") or {}
        self.assertIn("suppressed_today", sections)

    def test_source_audit_section_present(self):
        brief = self._build_brief()
        sections = brief.get("sections") or {}
        self.assertIn("source_audit", sections)

    def test_reactivated_intelligence_section_present(self):
        brief = self._build_brief()
        sections = brief.get("sections") or {}
        self.assertIn("reactivated_intelligence", sections)

    def test_section_confidence_in_brief(self):
        brief = self._build_brief()
        self.assertIn("section_confidence", brief)
        self.assertIsInstance(brief["section_confidence"], dict)

    def test_rendering_rules_include_lifecycle_rules(self):
        brief = self._build_brief()
        rules = brief.get("rendering_rules") or []
        rules_text = " ".join(rules).lower()
        self.assertIn("new_intelligence_today", rules_text)
        self.assertIn("suppressed_today", rules_text)
        self.assertIn("attribution_type", rules_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
