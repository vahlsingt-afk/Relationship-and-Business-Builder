from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
import competitor_intelligence_common as cic
import daily_brief as db
import intelligence_action_queue as queue


def test_contact_threshold_requires_active_pursuit_and_strong_evidence():
    action, _ = queue._threshold(
        85, active_pursuit=True, incumbent_count=1,
        confidence="high", evidence_count=2, entity_id="brand-test",
    )
    assert action == "contact_account"


def test_high_score_without_active_pursuit_can_be_displacement():
    action, _ = queue._threshold(
        85, active_pursuit=False, incumbent_count=1,
        confidence="high", evidence_count=2, entity_id="brand-test",
    )
    assert action == "competitive_displacement_opportunity"


def test_external_action_is_blocked_when_evidence_is_thin():
    action, _ = queue._threshold(
        90, active_pursuit=True, incumbent_count=1,
        confidence="high", evidence_count=1, entity_id="brand-test",
    )
    assert action == "research_further"


def test_low_score_stays_monitor_only():
    action, _ = queue._threshold(
        30, active_pursuit=False, incumbent_count=0,
        confidence="low", evidence_count=0, entity_id="brand-test",
    )
    assert action == "monitor"


def test_unresolved_entity_never_escalates_past_research_further():
    """RB-DEFECT (2026-09-15, Codex handoff item #2): live incident -- a
    Yum Brands hypothesis with entity_id=None still reached build_pursuit
    (RB's #1-ranked recommendation) on score alone. An unresolved entity
    must never clear contact_account/displacement/build_pursuit, regardless
    of how high the numeric score is."""
    action, _ = queue._threshold(
        99, active_pursuit=True, incumbent_count=5,
        confidence="high", evidence_count=10, entity_id=None,
    )
    assert action == "research_further"


def test_build_pursuit_requires_a_verified_incumbent():
    """The other half of the live incident: zero incumbent_exposure (no real
    buying trigger -- just generic corporate news) must not clear
    build_pursuit even with a resolved entity and high confidence."""
    action, _ = queue._threshold(
        90, active_pursuit=False, incumbent_count=0,
        confidence="high", evidence_count=5, entity_id="brand-yum-brands",
    )
    assert action == "research_further"


def test_distinct_evidence_count_collapses_same_story_different_outlets():
    """Live incident: two of Yum Brands' three supporting_evidence strings
    were Business Wire and Benzinga both reporting the identical Pizza Hut
    divestiture -- one real-world event, not two independent signals."""
    evidence = [
        "Yum Brands COO Tracy Skeans retiring after 25 years; Nai De Leon named successor - QSR Web",
        "LongRange Capital Completes Acquisition of Pizza Hut from Yum! Brands - Business Wire",
        "Deal Dispatch: Yum! Brands Sells Pizza Hut, Fox Corp. Buys Roku For $22 Billion, Salesforce Acquires Fin - Benzinga",
    ]
    assert queue._distinct_evidence_count(evidence) == 2


def test_distinct_evidence_count_keeps_genuinely_separate_stories():
    evidence = [
        "Starbucks appoints new Chief Technology Officer - Restaurant Dive",
        "PAR Technology reports Q3 earnings beat - Nation's Restaurant News",
    ]
    assert queue._distinct_evidence_count(evidence) == 2


def test_queue_ids_are_deterministic():
    assert queue._id("kind", "entity", "evidence") == queue._id("kind", "entity", "evidence")


class _IsolatedQueueMixin(unittest.TestCase):
    """Isolates every module-level cache/state path so tests never touch
    the real production caches -- same discipline as
    test_competitor_intelligence_review.py's _IsolatedMixin."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        tmp = Path(self._tmpdir)

        self._orig_cache = queue.CACHE_PATH
        self._orig_radar = queue.RADAR_PATH
        self._orig_page = queue.PAGE_PATH
        self._orig_baseline = queue.BASELINE_PATH
        self._orig_ramification = queue.RAMIFICATION_PATH
        self._orig_state = queue.STATE_PATH
        queue.CACHE_PATH = tmp / "intelligence_action_queue.json"
        queue.RADAR_PATH = tmp / "sales_opportunity_radar.json"
        queue.PAGE_PATH = tmp / "entity_page_changes.json"
        queue.BASELINE_PATH = tmp / "baseline_research_gate.json"
        queue.RAMIFICATION_PATH = tmp / "intelligence_ramifications.json"
        queue.STATE_PATH = tmp / "intelligence_action_queue_state.json"

        self._orig_root = cic.ROOT
        cic.ROOT = tmp / "competitor_intelligence"

    def tearDown(self):
        queue.CACHE_PATH = self._orig_cache
        queue.RADAR_PATH = self._orig_radar
        queue.PAGE_PATH = self._orig_page
        queue.BASELINE_PATH = self._orig_baseline
        queue.RAMIFICATION_PATH = self._orig_ramification
        queue.STATE_PATH = self._orig_state
        cic.ROOT = self._orig_root
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class TestOtherItemsRequireResolvedEntity(_IsolatedQueueMixin):
    """RB-DEFECT (2026-09-16): found while checking a real overnight run's
    fresh data against the 2026-09-15 Yum Brands fix. That fix's own
    docstring (_threshold()) states "every action class beyond
    research_further/monitor now also requires a resolved entity_id" --
    but the gate was only ever wired into _radar_items(). _other_items()'s
    first_party_page_change and downstream_ramification branches computed
    action_class from raw score alone, with no entity_id check at all.
    Live on 2026-09-16: a real page-monitor item (Toast) happened to carry
    a resolved entity_id, so this stayed silent -- but an unresolved
    entity from either source would have reached build_pursuit exactly
    the way the Yum Brands hypothesis did before that fix."""

    def setUp(self):
        super().setUp()
        # _other_items() stamps source_cache as PAGE_PATH/RAMIFICATION_PATH
        # relative to core.SYSTEM_DIR -- _IsolatedQueueMixin swaps those
        # paths to a tmpdir but leaves core.SYSTEM_DIR pointing at the real
        # project, so any test that actually populates PAGE_PATH/
        # RAMIFICATION_PATH (unlike the existing tests in this file, which
        # never do) needs SYSTEM_DIR isolated too or .relative_to() raises.
        self._orig_system_dir = queue.core.SYSTEM_DIR
        queue.core.SYSTEM_DIR = Path(self._tmpdir)

    def tearDown(self):
        queue.core.SYSTEM_DIR = self._orig_system_dir
        super().tearDown()

    def test_page_change_with_resolved_entity_can_still_reach_build_pursuit(self):
        queue.PAGE_PATH.write_text(json.dumps({
            "changes": [{
                "entity": "Toast", "entity_id": "vendor-toast",
                "url": "https://careers.toasttab.com/jobs/search/search-page-r-d",
                "material_keywords_detected": True,
                "change_excerpt": "New R&D roles posted.",
            }]
        }), encoding="utf-8")
        items = queue._other_items()
        page_items = [i for i in items if i["item_type"] == "first_party_page_change"]
        self.assertEqual(len(page_items), 1)
        self.assertEqual(page_items[0]["action_class"], "build_pursuit")

    def test_page_change_with_unresolved_entity_cannot_reach_build_pursuit(self):
        queue.PAGE_PATH.write_text(json.dumps({
            "changes": [{
                "entity": "Some Unresolved Vendor", "entity_id": None,
                "url": "https://example.com/careers",
                "material_keywords_detected": True,
                "change_excerpt": "New R&D roles posted.",
            }]
        }), encoding="utf-8")
        items = queue._other_items()
        page_items = [i for i in items if i["item_type"] == "first_party_page_change"]
        self.assertEqual(len(page_items), 1)
        self.assertEqual(page_items[0]["action_class"], "research_further")

    def test_ramification_with_resolved_entity_can_still_reach_build_pursuit(self):
        queue.RAMIFICATION_PATH.write_text(json.dumps({
            "ramifications": [{
                "entity_id": "vendor-toast", "entity_name": "Toast",
                "ramification_id": "ram-001", "priority_score": 80,
                "evidence_summary": "Test ramification.",
            }]
        }), encoding="utf-8")
        items = queue._other_items()
        ram_items = [i for i in items if i["item_type"] == "downstream_ramification"]
        self.assertEqual(len(ram_items), 1)
        self.assertEqual(ram_items[0]["action_class"], "build_pursuit")

    def test_ramification_with_unresolved_entity_cannot_reach_build_pursuit(self):
        queue.RAMIFICATION_PATH.write_text(json.dumps({
            "ramifications": [{
                "entity_id": None, "entity_name": "Some Unresolved Vendor",
                "ramification_id": "ram-002", "priority_score": 80,
                "evidence_summary": "Test ramification.",
            }]
        }), encoding="utf-8")
        items = queue._other_items()
        ram_items = [i for i in items if i["item_type"] == "downstream_ramification"]
        self.assertEqual(len(ram_items), 1)
        self.assertEqual(ram_items[0]["action_class"], "research_further")


class TestSameCycleFreshness(_IsolatedQueueMixin):
    """RB-DEFECT (2026-09-15, Codex handoff item #1): intelligence_action_queue
    ran as its own morning_pipeline.py scan step BEFORE competitor_intelligence_
    review_scan (a later scan step). The on-disk queue could therefore miss
    same-cycle competitor-review items. The fix has daily_brief.py rebuild the
    queue after competitor review data (already current -- an earlier scan
    step) and intelligence_ramifications (freshly computed) are both ready.
    This proves the queue-rebuild mechanism itself: a second build() call
    picks up review-queue state written after the first build()."""

    def test_stale_build_misses_same_cycle_competitor_review(self):
        first = queue.build()
        self.assertEqual(first["total_pending"], 0)

    def test_rebuild_after_review_scan_reflects_same_cycle_item(self):
        # Simulate the pipeline: the queue's first (scan-phase) build happens
        # before competitor_intelligence_review_scan writes a pending item.
        stale = queue.build()
        self.assertEqual(stale["total_pending"], 0,
                          "sanity: nothing pending before the review scan runs")

        cic.create_competitor_shell("toast", "Toast", "vendor-toast")
        cic.register_competitor("toast", "Toast")
        review_queue = cic.load_review_queue()
        review_queue.setdefault("pending_reviews", []).append({
            "review_id": "rev-toast-0001", "competitor_slug": "toast",
            "category": None, "evidence_id": "capture-INT-test",
            "kind": "capture_signal_review", "reason": "Test capture signal.",
            "status": "pending", "queued_at": "2026-09-15T09:00:00Z",
        })
        cic.save_review_queue(review_queue)

        # The daily_brief.py fix: rebuild the queue again now that the
        # review scan (an earlier same-cycle scan step) is current.
        fresh = queue.build()
        self.assertEqual(fresh["total_pending"], 1,
                          "same-cycle competitor review item must appear after rebuild")
        self.assertEqual(fresh["items"][0]["item_type"], "competitor_review")
        self.assertEqual(fresh["items"][0]["entity"], "toast")


def _seed_one_item(slug: str = "toast") -> dict:
    """Register one competitor and queue one pending review, then build() --
    the smallest real path to a queue item with a stable queue_id."""
    cic.create_competitor_shell(slug, slug.title(), f"vendor-{slug}")
    cic.register_competitor(slug, slug.title())
    review_queue = cic.load_review_queue()
    review_queue.setdefault("pending_reviews", []).append({
        "review_id": f"rev-{slug}-0001", "competitor_slug": slug,
        "category": None, "evidence_id": "capture-INT-test",
        "kind": "capture_signal_review", "reason": "Test capture signal.",
        "status": "pending", "queued_at": "2026-09-15T09:00:00Z",
    })
    cic.save_review_queue(review_queue)
    report = queue.build()
    return report["items"][0]


class TestResolutionPersistence(_IsolatedQueueMixin):
    """RB-2026-09-15 — Codex handoff item #5 prerequisite: build() fully
    regenerates the queue every cycle, so a resolution has to live in a
    durable side-store (STATE_PATH) that survives the next rebuild, or
    outcome calibration has nothing to read."""

    def test_resolve_unknown_queue_id_raises_keyerror(self):
        _seed_one_item()
        with self.assertRaises(KeyError):
            queue.resolve("iaq-doesnotexist", "accepted")

    def test_resolve_invalid_disposition_raises_valueerror(self):
        item = _seed_one_item()
        with self.assertRaises(ValueError):
            queue.resolve(item["queue_id"], "maybe")

    def test_resolution_survives_next_rebuild(self):
        item = _seed_one_item()
        entry = queue.resolve(item["queue_id"], "accepted", note="Following up personally.")
        self.assertEqual(entry["disposition"], "accepted")
        self.assertEqual(entry["item_type"], "competitor_review")

        # Rebuild again -- simulates tomorrow's pipeline cycle regenerating
        # the same underlying candidate from the still-pending review item.
        rebuilt = queue.build()
        rebuilt_item = next(row for row in rebuilt["items"] if row["queue_id"] == item["queue_id"])
        self.assertEqual(rebuilt_item["status"], "resolved")
        self.assertEqual(rebuilt_item["disposition"], "accepted")
        self.assertEqual(rebuilt_item["disposition_note"], "Following up personally.")
        self.assertEqual(rebuilt["resolution_counts"], {"pending_review": 0, "resolved": 1})

    def test_unresolved_item_stays_pending_review(self):
        item = _seed_one_item()
        self.assertEqual(item["status"], "pending_review")
        rebuilt = queue.build()
        self.assertEqual(rebuilt["resolution_counts"], {"pending_review": 1, "resolved": 0})

    def test_resolution_snapshot_survives_item_disappearing(self):
        """If the underlying signal clears (e.g. the competitor review gets
        resolved elsewhere) the queue item can vanish from a later build(),
        but the resolution record itself -- what it was AT THE TIME Todd
        acted on it -- must remain in STATE_PATH for calibration, not be
        deleted just because nothing currently regenerates that queue_id."""
        item = _seed_one_item()
        queue.resolve(item["queue_id"], "accepted")

        review_queue = cic.load_review_queue()
        for entry in review_queue.get("pending_reviews", []):
            entry["status"] = "resolved"
        cic.save_review_queue(review_queue)

        rebuilt = queue.build()
        self.assertEqual(rebuilt["items"], [])  # the candidate no longer regenerates
        state = queue._load(queue.STATE_PATH)
        self.assertIn(item["queue_id"], state["resolutions"])
        self.assertEqual(state["resolutions"][item["queue_id"]]["disposition"], "accepted")


class TestDailyBriefExcludesResolvedFromTopEight(unittest.TestCase):
    """RB-2026-09-15: without this filter, a resolved item would keep
    occupying a top-8 slot forever on priority_score alone, crowding out
    genuinely new pending items -- the daily brief must only nag about
    what Todd hasn't already dispositioned."""

    def _row(self, queue_id: str, status: str, score: int) -> dict:
        return {
            "queue_id": queue_id, "status": status, "entity": queue_id,
            "action_class": "monitor", "priority_score": score, "rank": 1,
            "summary": "s", "threshold_reason": "r", "confidence": "medium",
        }

    def test_resolved_item_excluded_even_at_top_rank(self):
        report = {"intelligence_action_queue": {"items": [
            self._row("iaq-resolved", "resolved", 99),
            self._row("iaq-pending", "pending_review", 10),
        ]}}
        items = db._intelligence_action_queue_items(report)
        ids = [item["extras"]["queue_id"] for item in items]
        self.assertNotIn("iaq-resolved", ids)
        self.assertIn("iaq-pending", ids)

    def test_missing_status_treated_as_pending(self):
        """Backward compatibility: a cache written before this field existed
        (or a row type that doesn't set it) must still render, not vanish."""
        row = self._row("iaq-nostatus", "pending_review", 10)
        del row["status"]
        report = {"intelligence_action_queue": {"items": [row]}}
        items = db._intelligence_action_queue_items(report)
        self.assertEqual(len(items), 1)
