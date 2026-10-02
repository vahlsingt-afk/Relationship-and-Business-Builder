"""
test_cos_brief_structure.py — RB 9.37 / DEFECT-020
CoS Daily Brief structure: operational command center (not news summary).

DEFECT-020 Sub-defects addressed:
  #1 Missing Recommended Actions — always present, prioritized
  #2 Missing Open Loop Identification — loops_and_obligations surfaced
  #3 Missing Loop Creation Prompt — suggested_loop_creation contract
  #4 Missing Decision Queue — decision_queue from ask_todd items
  #5 Missing Trust Metrics — user-facing coverage + confidence report
  #6 Suppression Log Exposed — suppressed_today is internal-only
  #7 Insufficient Dot-Connection — emerging_themes + contrarian_view
  #8 Missing Watchlist Reporting — watchlist_intelligence per-entity status

Test groups:
  CBS1 (6):  watchlist_intelligence — per-entity status logic
  CBS2 (6):  decision_queue — ask_todd collection and dedup
  CBS3 (6):  trust_metrics — coverage counts, confidence, stale sources
  CBS4 (6):  executive_summary, emerging_themes, contrarian_view scaffolds
  CBS5 (6):  brief_display_order, suppressed_today internal, rendering rules
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db
import intelligence_lifecycle as ilc

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SYNTHETIC_REPORT: dict = {
    "today": "2026-05-30",
    "relationship_signals": {
        "signals": [
            {"name": "Alice Chen", "tier": "inner", "drr_score": 0.82},
            {"name": "Bob Gibson", "tier": "inner", "drr_score": 0.75},
            {"name": "Carol Yamamoto", "tier": "broader", "drr_score": 0.50},
        ],
        "stale_sources": [],
    },
    "loops": {},
    "active_threads": [],
    "market_signals": {"signals": [], "freshness_status": "ok"},
    "strategic_memory": {},
    "daily_prep_summary": {
        "totals": {"inner": 2, "broader": 1, "dormant_valuable": 0},
        "source_health": {
            "sources": {
                "email": {"status": "fresh", "last_checked": "2026-05-30T07:00:00"},
                "calendar": {"status": "stale", "last_checked": "2026-05-29T07:00:00"},
            }
        },
        "signals": [],
        "overdue_loops": [],
        "stale_sources": ["calendar"],
        "meeting_prep": [],
    },
}

_SIGNAL_WITH_ASK_TODD = {
    "title": "Add Matt Brown to permanent watchlist?",
    "summary": "Matt Brown showed engagement signals — consider adding to watchlist.",
    "disposition": "ask_todd",
    "grounding": "system_detected",
    "freshness": "fresh",
    "source_refs": ["relationship_signals"],
    "confidence": "medium",
}


def _build_brief():
    """Build the canonical brief with an isolated, throwaway intelligence
    lifecycle store.

    `build_canonical_brief` reads/writes the real, persistent
    `system/.cache/intelligence_store.json` via
    `intelligence_lifecycle.IntelligenceStore.load()/.save()`. Without
    isolation, repeated test runs accumulate lifecycle records for these
    synthetic items (e.g. "Alice Chen", "McDonald's") until DEFECT-022's
    novelty filter (`filter_suppressed_from_sections`) marks them
    DORMANT/ACKNOWLEDGED and strips them from `watchlist_intelligence` and
    other sections — making tests fail nondeterministically depending on
    how many times they've previously run against the shared store.

    RB-DEFECT-066 backfill (2026-08-10): build_canonical_brief also calls
    earnings_monitor.build_earnings_intelligence(), which reads the real
    system/inbox/market_signals_earnings.jsonl. That file now contains real
    earnings_release rows (the backfill correctly reclassified them), so an
    unpatched call here made real network fetches (record_earnings_history's
    exhibit lookup) and wrote to the real system/earnings_history/
    earnings_calls.jsonl on every test run. Isolate both paths the same way
    the intelligence store is isolated above.
    """
    import earnings_monitor as em

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_store = Path(tmpdir) / "intelligence_store.json"
        orig_store_path = ilc._store_path
        ilc._store_path = lambda: tmp_store
        orig_output_path = em.OUTPUT_PATH
        orig_history_path = em.EARNINGS_HISTORY_PATH
        em.OUTPUT_PATH = Path(tmpdir) / "market_signals_earnings.jsonl"
        em.EARNINGS_HISTORY_PATH = Path(tmpdir) / "earnings_calls.jsonl"
        try:
            return db.build_canonical_brief(_SYNTHETIC_REPORT)
        finally:
            ilc._store_path = orig_store_path
            em.OUTPUT_PATH = orig_output_path
            em.EARNINGS_HISTORY_PATH = orig_history_path


# ---------------------------------------------------------------------------
# CBS1 — watchlist_intelligence
# ---------------------------------------------------------------------------

class CBS1WatchlistIntelligenceTests(unittest.TestCase):
    """Tests for _compute_watchlist_intelligence and watchlist_intelligence section."""

    def test_CBS1a_section_exists_in_brief(self):
        """watchlist_intelligence section is present in the brief."""
        brief = _build_brief()
        self.assertIn("watchlist_intelligence", brief.get("sections") or {})

    def test_CBS1b_inner_tier_contacts_appear(self):
        """Inner-tier contacts from relationship_signals appear as watchlist entries."""
        brief = _build_brief()
        items = (brief.get("sections") or {}).get("watchlist_intelligence") or []
        titles = [item.get("title", "") for item in items]
        # At least one inner-tier contact should be represented
        any_inner = any("Alice Chen" in t or "Bob Gibson" in t for t in titles)
        self.assertTrue(any_inner, f"No inner-tier contacts found in watchlist. Titles: {titles}")

    def test_CBS1c_entity_status_in_title(self):
        """Each watchlist item title includes a status category."""
        brief = _build_brief()
        items = (brief.get("sections") or {}).get("watchlist_intelligence") or []
        valid_statuses = {"Escalation", "New Activity", "Relevant Activity", "No Change"}
        for item in items:
            title = item.get("title") or ""
            has_status = any(s in title for s in valid_statuses)
            self.assertTrue(has_status, f"Watchlist item title missing status category: {title!r}")

    def test_CBS1d_extras_has_watchlist_status(self):
        """Each watchlist item has watchlist_status in extras."""
        brief = _build_brief()
        items = (brief.get("sections") or {}).get("watchlist_intelligence") or []
        for item in items:
            extras = item.get("extras") or {}
            self.assertIn("watchlist_status", extras,
                          f"Missing watchlist_status in extras for item: {item.get('title')}")

    def test_CBS1e_watchlist_status_function_escalation(self):
        """_watchlist_entity_status returns Escalation when act_today item mentions entity."""
        sections = {
            "new_intelligence_today": [
                {
                    "title": "Alice Chen replied — action required",
                    "summary": "Alice Chen sent urgent message.",
                    "disposition": "act_today",
                }
            ]
        }
        status, evidence = db._watchlist_entity_status("Alice Chen", sections)
        self.assertEqual(status, "Escalation")
        self.assertIn("Alice Chen", evidence)

    def test_CBS1f_watchlist_status_function_no_change(self):
        """_watchlist_entity_status returns No Change when entity not in any section."""
        sections = {
            "overnight_delta_intelligence": [
                {"title": "Unrelated signal", "summary": "Something else.", "disposition": "monitor"}
            ]
        }
        status, evidence = db._watchlist_entity_status("Bob Gibson", sections)
        self.assertEqual(status, "No Change")
        self.assertEqual(evidence, "")


# ---------------------------------------------------------------------------
# CBS2 — decision_queue
# ---------------------------------------------------------------------------

class CBS2DecisionQueueTests(unittest.TestCase):
    """Tests for _compute_decision_queue and decision_queue section."""

    def test_CBS2a_section_exists_in_brief(self):
        """decision_queue section is present in the brief."""
        brief = _build_brief()
        self.assertIn("decision_queue", brief.get("sections") or {})

    def test_CBS2b_ask_todd_items_collected(self):
        """ask_todd items from content sections appear in decision_queue."""
        sections = {
            "relationship_operational_signal_review": [_SIGNAL_WITH_ASK_TODD],
            "decision_queue": [],
            "suppressed_today": [],
            "trust_metrics": [],
            "source_audit": [],
            "active_knowledge_assets": [],
            "resource_verification_and_freshness_status": [],
        }
        result = db._compute_decision_queue(sections)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].get("title"), "Add Matt Brown to permanent watchlist?")

    def test_CBS2c_no_duplicates(self):
        """Duplicate ask_todd titles across multiple sections produce one entry."""
        sections = {
            "relationship_operational_signal_review": [_SIGNAL_WITH_ASK_TODD],
            "overnight_delta_intelligence": [_SIGNAL_WITH_ASK_TODD],  # same title
            "decision_queue": [],
            "suppressed_today": [],
            "trust_metrics": [],
            "source_audit": [],
            "active_knowledge_assets": [],
            "resource_verification_and_freshness_status": [],
        }
        result = db._compute_decision_queue(sections)
        self.assertEqual(len(result), 1, "Duplicate ask_todd items must be de-duplicated")

    def test_CBS2d_non_ask_todd_items_excluded(self):
        """Items with disposition != ask_todd are not included."""
        sections = {
            "relationship_operational_signal_review": [
                {
                    "title": "Monitor signal",
                    "summary": "Just monitoring.",
                    "disposition": "monitor",
                }
            ],
            "decision_queue": [],
            "suppressed_today": [],
            "trust_metrics": [],
            "source_audit": [],
            "active_knowledge_assets": [],
            "resource_verification_and_freshness_status": [],
        }
        result = db._compute_decision_queue(sections)
        self.assertEqual(result, [])

    def test_CBS2e_decision_queue_skip_sections_excluded(self):
        """decision_queue and suppressed_today are not scanned."""
        sections = {
            "decision_queue": [_SIGNAL_WITH_ASK_TODD],  # should be skipped
            "suppressed_today": [_SIGNAL_WITH_ASK_TODD],  # should be skipped
            "trust_metrics": [],
            "source_audit": [],
            "active_knowledge_assets": [],
            "resource_verification_and_freshness_status": [],
        }
        result = db._compute_decision_queue(sections)
        self.assertEqual(result, [], "Skip sections must not be scanned for decisions")

    def test_CBS2f_decision_item_disposition(self):
        """Decision queue items have disposition='ask_todd'."""
        sections = {
            "relationship_operational_signal_review": [_SIGNAL_WITH_ASK_TODD],
            "decision_queue": [],
            "suppressed_today": [],
            "trust_metrics": [],
            "source_audit": [],
            "active_knowledge_assets": [],
            "resource_verification_and_freshness_status": [],
        }
        result = db._compute_decision_queue(sections)
        for item in result:
            self.assertEqual(item.get("disposition"), "ask_todd")


# ---------------------------------------------------------------------------
# CBS3 — trust_metrics
# ---------------------------------------------------------------------------

class CBS3TrustMetricsTests(unittest.TestCase):
    """Tests for _compute_trust_metrics and trust_metrics section."""

    def _make_sections(self, **overrides):
        base = {
            "new_intelligence_today": [],
            "reactivated_intelligence": [],
            "suppressed_today": [],
            "watchlist_intelligence": [],
            "decision_queue": [],
            "resource_verification_and_freshness_status": [],
            "relationship_operational_signal_review": [],
            "overnight_delta_intelligence": [],
            "email_intelligence_harvest": [],
            "autonomous_discovery_evidence": [],
            "what_rb_found_without_you_telling_it": [],
            "strategic_industry_signals": [],
            "emerging_themes": [],
        }
        base.update(overrides)
        return base

    def test_CBS3a_trust_metrics_section_in_brief(self):
        """trust_metrics section is present in the canonical brief."""
        brief = _build_brief()
        self.assertIn("trust_metrics", brief.get("sections") or {})

    def test_CBS3b_required_metric_keys_present(self):
        """_compute_trust_metrics returns all required metric keys."""
        sections = self._make_sections()
        result = db._compute_trust_metrics(sections, {}, None)
        required = {
            "sources_assessed", "watchlist_entities_checked",
            "new_signals_identified", "reactivated_signals",
            "material_signals_surfaced", "suppressed_today_count",
            "decisions_queued", "stale_sources", "overall_confidence",
            "coverage_window",
        }
        for key in required:
            self.assertIn(key, result, f"trust_metrics missing key: {key!r}")

    def test_CBS3c_coverage_window_is_24h(self):
        """Coverage window is always 'Last 24 hours'."""
        result = db._compute_trust_metrics(self._make_sections(), {}, None)
        self.assertEqual(result["coverage_window"], "Last 24 hours")

    def test_CBS3d_stale_sources_detected(self):
        """Stale sources in source_health appear in trust_metrics.stale_sources."""
        source_health = {
            "sources": {
                "email": {"status": "fresh"},
                "calendar": {"status": "stale"},
                "linkedin": {"status": "unavailable"},
            }
        }
        result = db._compute_trust_metrics(self._make_sections(), {}, source_health)
        self.assertIn("calendar", result["stale_sources"])
        self.assertIn("linkedin", result["stale_sources"])
        self.assertNotIn("email", result["stale_sources"])

    def test_CBS3e_suppressed_count_reflected(self):
        """suppressed_today items are counted in trust_metrics, not suppressed from count."""
        suppressed_items = [
            {"title": f"Item {i}", "summary": "Old signal."} for i in range(3)
        ]
        sections = self._make_sections(suppressed_today=suppressed_items)
        result = db._compute_trust_metrics(sections, {}, None)
        self.assertEqual(result["suppressed_today_count"], 3)

    def test_CBS3f_confidence_high_when_most_scores_high(self):
        """overall_confidence is 'high' when section_confidence majority is high."""
        section_confidence = {
            "relationship_intelligence": "high",
            "market_intelligence": "high",
            "macro_environment": "medium",
        }
        result = db._compute_trust_metrics(self._make_sections(), section_confidence, None)
        self.assertEqual(result["overall_confidence"], "high")


# ---------------------------------------------------------------------------
# CBS4 — executive_summary, emerging_themes, contrarian_view
# ---------------------------------------------------------------------------

class CBS4SynthesisScaffoldTests(unittest.TestCase):
    """Tests for executive_summary, emerging_themes, and contrarian_view scaffolds."""

    def _make_sections(self, **overrides):
        base = {
            "new_intelligence_today": [],
            "reactivated_intelligence": [],
            "relationship_operational_signal_review": [],
            "overnight_delta_intelligence": [],
            "email_intelligence_harvest": [],
            "industry_brief": [],
            "condensed_industry_context": [],
            "strategic_industry_signals": [],
            "what_is_not_happening": [],
            "cos_judgment": [],
            "loops_and_obligations": [],
            "decision_queue": [],
        }
        base.update(overrides)
        return base

    def test_CBS4a_executive_summary_section_populated(self):
        """executive_summary section is populated in the canonical brief."""
        brief = _build_brief()
        items = (brief.get("sections") or {}).get("executive_summary") or []
        self.assertGreater(len(items), 0, "executive_summary must be populated")

    def test_CBS4b_executive_summary_has_synthesis_sources(self):
        """executive_summary extras.synthesis_sources lists source section keys."""
        sections = self._make_sections()
        result = db._executive_summary_scaffold(sections)
        self.assertEqual(len(result), 1)
        extras = result[0].get("extras") or {}
        self.assertIn("synthesis_sources", extras)
        self.assertIsInstance(extras["synthesis_sources"], list)
        self.assertGreater(len(extras["synthesis_sources"]), 0)

    def test_CBS4c_emerging_themes_section_populated(self):
        """emerging_themes section is populated in the canonical brief."""
        brief = _build_brief()
        items = (brief.get("sections") or {}).get("emerging_themes") or []
        self.assertGreater(len(items), 0, "emerging_themes must be populated")

    def test_CBS4d_emerging_themes_has_domain_counts(self):
        """emerging_themes extras has domain_signal_counts."""
        sections = self._make_sections(
            industry_brief=[{"title": "Signal 1", "summary": ""}],
            overnight_delta_intelligence=[{"title": "Signal 2", "summary": ""}],
        )
        result = db._emerging_themes_scaffold(sections)
        extras = result[0].get("extras") or {}
        self.assertIn("domain_signal_counts", extras)
        self.assertIn("convergence_detected", extras)
        self.assertIn("total_signals", extras)

    def test_CBS4e_contrarian_view_section_populated(self):
        """contrarian_view section is populated in the canonical brief."""
        brief = _build_brief()
        items = (brief.get("sections") or {}).get("contrarian_view") or []
        self.assertGreater(len(items), 0, "contrarian_view must be populated")

    def test_CBS4f_contrarian_view_has_contrarian_prompt(self):
        """contrarian_view extras has contrarian_prompt for GPT synthesis."""
        sections = self._make_sections()
        result = db._contrarian_view_scaffold(sections)
        extras = result[0].get("extras") or {}
        self.assertIn("contrarian_prompt", extras)
        self.assertIn("has_seed_material", extras)


# ---------------------------------------------------------------------------
# CBS5 — brief_display_order, suppressed_today internal, rendering rules
# ---------------------------------------------------------------------------

class CBS5DisplayOrderAndRenderingRulesTests(unittest.TestCase):
    """Tests for brief_display_order, suppression internal-only, and rendering rules."""

    def test_CBS5a_brief_display_order_present(self):
        """brief_display_order is a top-level key in the canonical brief."""
        brief = _build_brief()
        self.assertIn("brief_display_order", brief)
        self.assertIsInstance(brief["brief_display_order"], list)
        self.assertGreater(len(brief["brief_display_order"]), 0)

    def test_CBS5b_executive_summary_is_first_in_display_order(self):
        """Sprint A: five_things_today leads the display order; executive_summary follows.
        Prior to Sprint A this test checked that executive_summary was position 0.
        Sprint A promotes five_things_today to position 0 (quick-read command center).
        executive_summary must still appear somewhere in the display order."""
        brief = _build_brief()
        order = brief.get("brief_display_order") or []
        # Sprint B (Trust Contract) promotes source_trust_table to position 0.
        # five_things_today is now position 2 (after source_trust_table and source_gap_declarations).
        self.assertIn("source_trust_table", order[:2],
                      "source_trust_table must appear before five_things_today (Sprint B)")
        self.assertIn("five_things_today", order,
                      "five_things_today must appear in brief_display_order (Sprint A)")
        self.assertIn("executive_summary", order,
                      "executive_summary must still appear in brief_display_order")

    def test_CBS5c_trust_metrics_is_last_in_display_order(self):
        """trust_metrics is the last entry in brief_display_order."""
        brief = _build_brief()
        order = brief.get("brief_display_order") or []
        self.assertEqual(order[-1], "trust_metrics",
                         "trust_metrics must be the final display section")

    def test_CBS5d_suppressed_today_not_in_display_order(self):
        """suppressed_today is NOT in brief_display_order (internal-only section)."""
        brief = _build_brief()
        order = brief.get("brief_display_order") or []
        self.assertNotIn("suppressed_today", order,
                         "suppressed_today must be internal-only, not in brief_display_order")

    def test_CBS5e_rendering_rules_say_suppressed_is_internal(self):
        """rendering_rules include an explicit rule that suppressed_today is INTERNAL ONLY."""
        brief = _build_brief()
        rules = " ".join(brief.get("rendering_rules") or []).lower()
        self.assertIn("suppressed_today is internal only", rules,
                      "rendering_rules must explicitly label suppressed_today as INTERNAL ONLY")

    def test_CBS5f_decision_queue_before_trust_metrics_in_display_order(self):
        """decision_queue appears before trust_metrics in brief_display_order."""
        brief = _build_brief()
        order = brief.get("brief_display_order") or []
        dq_pos = order.index("decision_queue") if "decision_queue" in order else -1
        tm_pos = order.index("trust_metrics") if "trust_metrics" in order else -1
        self.assertGreaterEqual(dq_pos, 0, "decision_queue must be in brief_display_order")
        self.assertGreaterEqual(tm_pos, 0, "trust_metrics must be in brief_display_order")
        self.assertLess(dq_pos, tm_pos, "decision_queue must come before trust_metrics")


if __name__ == "__main__":
    unittest.main(verbosity=2)
