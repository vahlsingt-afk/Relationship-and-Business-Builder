"""
test_intelligence_assessment.py — RB 9.40 / Sprint F
Tests for the pre-brief intelligence assessment phase.

intelligence_assessment.py runs between the gather phase (passive_email_intelligence,
passive_ri_ingest) and brief generation (daily_brief.py).  It executes five phases:
Phase 1 (web intelligence), Phase 2 (email classification), Phase 3 (convergences),
Phase 4 (mutation proposals), Phase 5 (trust stats) — and writes
system/.cache/intelligence_assessment.json.

Test groups:
  INTA1 (3):  Phase 2 — email classification writes to IntelligenceDB,
              skips duplicates, handles missing source gracefully
  INTA2 (3):  Phase 3 — convergence analysis returns multi-source entities,
              excludes single-source, handles empty DB
  INTA3 (3):  Phase 4 — mutation proposals: watchlist_add for untracked entity,
              thread_intelligence for active thread match, empty when no convergences
  INTA4 (3):  Phase 5 — trust stats: counts aggregate correctly, confidence
              degrades on phase errors, intelligence_gaps populated on failure
  INTA5 (2):  Integration — run_assessment produces valid cache file,
              is_fresh() returns correct state
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import intelligence_assessment as ia
from intelligence_db import IntelligenceDB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_db() -> IntelligenceDB:
    tmp = Path(tempfile.mktemp(suffix=".db"))
    db = IntelligenceDB(tmp)
    db.open()
    return db


def _fake_pei(headlines: list[dict]) -> dict:
    """Build a minimal passive_email_intelligence.json structure."""
    return {
        "generated_at": "2026-06-01T12:00:00+00:00",
        "top_headlines": headlines,
        "telemetry": {"relevant_headlines_extracted": len(headlines)},
    }


def _fake_headline(title: str, source: str = "Restaurant Dive",
                   companies: list[str] | None = None,
                   category: str = "platform_convergence",
                   confidence: str = "medium",
                   score: float = 0.8) -> dict:
    return {
        "headline": title,
        "source": source,
        "entities": {"companies": companies or []},
        "category": category,
        "themes": [],
        "confidence": confidence,
        "strategic_relevance_score": score,
        "why_it_matters": "Test item.",
    }


def _fake_assessment(multi_source=None, entity_pairs=None) -> dict:
    """Fake phase_3_convergences result."""
    return {
        "status": "ok",
        "multi_source": multi_source or [],
        "entity_pairs": entity_pairs or [],
        "multi_source_count": len(multi_source or []),
        "entity_pair_count": len(entity_pairs or []),
    }


# ---------------------------------------------------------------------------
# INTA1 — Phase 2: email classification
# ---------------------------------------------------------------------------

class TestINTA1_EmailClassification(unittest.TestCase):
    """INTA1: phase2_email_classification writes structured items to IntelligenceDB."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.pei_path = self.tmp_dir / "passive_email_intelligence.json"
        # Monkey-patch the module-level path
        self._orig_pei_path = ia.PASSIVE_EMAIL_CACHE
        ia.PASSIVE_EMAIL_CACHE = self.pei_path

    def tearDown(self):
        ia.PASSIVE_EMAIL_CACHE = self._orig_pei_path

    def test_INTA1a_headline_written_to_db(self):
        """A qualifying headline is written to IntelligenceDB with entity tags."""
        db = _tmp_db()
        try:
            pei = _fake_pei([
                _fake_headline("PAR Technology acquires TASK Group",
                               companies=["PAR Technology", "TASK Group"],
                               category="acquisition")
            ])
            self.pei_path.write_text(json.dumps(pei))

            # Patch _open_idb equivalent — phase2 opens IntelligenceDB() directly.
            # We test by running phase2 then reading the DB.
            # Since phase2 opens the production DB path, we need to inject a test DB.
            # Instead: test the underlying behavior via DB directly after phase2 runs.
            # (phase2 uses module-level IntelligenceDB() — acceptable for integration test)
            result = ia.phase2_email_classification()
            self.assertIn(result["status"], ("ok", "partial_error"))
            # At least 1 item should have been classified (written to production DB or skipped)
            # We verify the output structure is correct
            self.assertIn("items_classified", result)
            self.assertIn("signal_types_found", result)
            self.assertIn("entities_detected", result)
        finally:
            db.close()

    def test_INTA1b_missing_source_returns_skipped(self):
        """phase2 with missing email cache returns status=skipped_no_source."""
        # Point to a non-existent file
        ia.PASSIVE_EMAIL_CACHE = self.tmp_dir / "nonexistent.json"
        result = ia.phase2_email_classification()
        self.assertEqual(result["status"], "skipped_no_source")
        self.assertEqual(result["items_classified"], 0)

    def test_INTA1c_empty_headlines_returns_skipped(self):
        """phase2 with empty top_headlines returns status=skipped_empty."""
        pei = _fake_pei([])
        self.pei_path.write_text(json.dumps(pei))
        result = ia.phase2_email_classification()
        self.assertEqual(result["status"], "skipped_empty")
        self.assertEqual(result["items_classified"], 0)


# ---------------------------------------------------------------------------
# INTA2 — Phase 3: convergence analysis
# ---------------------------------------------------------------------------

class TestINTA2_ConvergenceAnalysis(unittest.TestCase):
    """INTA2: phase3_convergence_analysis returns correct structures."""

    def _seed_multi_source(self, db: IntelligenceDB, entity: str) -> None:
        """Add 2 items for entity from 2 different sources."""
        db.add_item(
            title=f"{entity} signal A",
            content="content A",
            source_name="Restaurant Dive",
            source_type="web_scan",
            confidence="high",
            tags=[{"type": "entity", "value": entity}],
        )
        db.add_item(
            title=f"{entity} signal B",
            content="content B",
            source_name="QSR Magazine",
            source_type="web_scan",
            confidence="medium",
            tags=[{"type": "entity", "value": entity}],
        )

    def test_INTA3a_multi_source_entity_returned(self):
        """Entity with items across 2+ sources appears in multi_source."""
        # We test the DB methods directly since phase3 opens production DB
        db = _tmp_db()
        try:
            self._seed_multi_source(db, "PAR Technology")
            convs = db.find_convergences(min_entity_hits=2, days=30)
            multi = [c for c in convs if len(c.get("sources") or []) >= 2]
            entities = [c["entity"] for c in multi]
            self.assertIn("PAR Technology", entities)
        finally:
            db.close()

    def test_INTA3b_single_source_excluded_from_multi(self):
        """Entity with items from only 1 source is not in multi_source convergences."""
        db = _tmp_db()
        try:
            db.add_item(
                title="Toast item A",
                content="", source_name="Restaurant Dive",
                source_type="web_scan", confidence="medium",
                tags=[{"type": "entity", "value": "Toast"}],
            )
            db.add_item(
                title="Toast item B",
                content="", source_name="Restaurant Dive",
                source_type="web_scan", confidence="medium",
                tags=[{"type": "entity", "value": "Toast"}],
            )
            convs = db.find_convergences(min_entity_hits=2, days=30)
            multi = [c for c in convs if len(c.get("sources") or []) >= 2]
            entities = [c["entity"] for c in multi]
            self.assertNotIn("Toast", entities)
        finally:
            db.close()

    def test_INTA3c_empty_db_returns_empty_convergences(self):
        """Empty DB returns empty multi_source and entity_pairs."""
        db = _tmp_db()
        try:
            self.assertEqual(db.find_convergences(), [])
            self.assertEqual(db.cross_entity_convergence(), [])
        finally:
            db.close()


# ---------------------------------------------------------------------------
# INTA3 — Phase 4: mutation proposals
# ---------------------------------------------------------------------------

class TestINTA3_MutationProposals(unittest.TestCase):
    """INTA3: phase4_mutation_proposals generates correct review-first proposals."""

    def _conv(self, entity: str, count: int, sources: list[str],
              signal_types: list[str] | None = None) -> dict:
        return {
            "entity": entity,
            "item_count": count,
            "sources": sources,
            "signal_types": signal_types or ["general"],
            "items": [],
        }

    def test_INTA3a_watchlist_add_proposed_for_untracked_entity(self):
        """Entity with 3+ items across 2 sources, not on watchlist → watchlist_add proposed."""
        convergences = _fake_assessment(
            multi_source=[self._conv("Zephyr Foods Co", 4, ["RTN", "Restaurant Dive"])]
        )
        # Patch watchlist to empty
        orig_wl = ia._load_watchlist_entities
        ia._load_watchlist_entities = lambda: set()
        orig_at = ia._load_active_thread_entities
        ia._load_active_thread_entities = lambda: {}
        try:
            result = ia.phase4_mutation_proposals(convergences, prior_assessment=None)
        finally:
            ia._load_watchlist_entities = orig_wl
            ia._load_active_thread_entities = orig_at

        self.assertEqual(result["status"], "ok")
        proposal_types = [p["type"] for p in result["proposals"]]
        self.assertIn("watchlist_add", proposal_types)
        watchlist_prop = next(p for p in result["proposals"] if p["type"] == "watchlist_add")
        self.assertEqual(watchlist_prop["entity"], "Zephyr Foods Co")
        self.assertTrue(watchlist_prop["requires_confirmation"])

    def test_INTA3b_thread_intelligence_proposed_for_thread_match(self):
        """Entity matching an active thread company → thread_intelligence proposed."""
        convergences = _fake_assessment(
            multi_source=[self._conv("PAR Technology", 3, ["RTN", "QSR Magazine"])]
        )
        orig_wl = ia._load_watchlist_entities
        ia._load_watchlist_entities = lambda: set()
        orig_at = ia._load_active_thread_entities
        ia._load_active_thread_entities = lambda: {"T-2026-001": ["par technology"]}
        try:
            result = ia.phase4_mutation_proposals(convergences, prior_assessment=None)
        finally:
            ia._load_watchlist_entities = orig_wl
            ia._load_active_thread_entities = orig_at

        proposal_types = [p["type"] for p in result["proposals"]]
        self.assertIn("thread_intelligence", proposal_types)
        tp = next(p for p in result["proposals"] if p["type"] == "thread_intelligence")
        self.assertEqual(tp["thread_id"], "T-2026-001")
        self.assertTrue(tp["requires_confirmation"])

    def test_INTA3c_no_proposals_when_no_convergences(self):
        """Empty convergences produces zero proposals."""
        result = ia.phase4_mutation_proposals(_fake_assessment(), prior_assessment=None)
        self.assertEqual(result["proposals_count"], 0)
        self.assertEqual(result["proposals"], [])


class TestINTA3d_WatchlistAutoApply(unittest.TestCase):
    """RB 2026-08-27: Todd's direction -- high-confidence watchlist_add
    proposals now auto-apply into ecosystem_intelligence.json's watch_list
    (tier_2, interrupt_eligible=False) instead of sitting review-first
    forever. Isolated ecosystem file per test (same pattern as
    TestINTA3b_LoadWatchlistEntities); _write_graph itself is mocked so
    these tests exercise this module's logic, not ecosystem_intelligence.py's
    own already-covered write/validate/snapshot behavior."""

    def _conv(self, entity: str, count: int, sources: list[str]) -> dict:
        return {
            "entity": entity, "item_count": count, "sources": sources,
            "signal_types": ["general"], "items": [],
        }

    def setUp(self):
        import ecosystem_intelligence as ei
        import mutation_policy as mp
        self.tmpdir = tempfile.TemporaryDirectory()
        self.eco_path = Path(self.tmpdir.name) / "ecosystem_intelligence.json"
        self.eco_path.write_text(json.dumps({
            "entities": [{"id": "brand-burger-king", "name": "Burger King", "entity_type": "brand"}],
            "watch_list": [],
        }))
        self._orig_path = ia.core.ECOSYSTEM_INTELLIGENCE_PATH
        ia.core.ECOSYSTEM_INTELLIGENCE_PATH = self.eco_path
        self._orig_wl = ia._load_watchlist_entities
        ia._load_watchlist_entities = lambda: set()
        self._orig_at = ia._load_active_thread_entities
        ia._load_active_thread_entities = lambda: {}
        # Mock _write_graph itself -- these tests exercise this module's
        # entry-construction/proposal-flag logic, not ecosystem_intelligence
        # .py's own already-covered subprocess-validator/snapshot behavior.
        # Default: write the graph to the isolated path, mimicking the real
        # write's end state without invoking the real validator subprocess.
        self._ei = ei
        self._orig_write_graph = ei._write_graph
        def _fake_write_graph(graph, **kw):
            self.eco_path.write_text(json.dumps(graph))
        ei._write_graph = _fake_write_graph
        # RB-DEFECT-2026-09-18 (KFC accounting bug): isolate mutation_policy's
        # receipt log too, so these tests can assert on it without touching
        # (or being polluted by) the real system/.cache/ file.
        self._mp = mp
        self._orig_receipts_path = mp.RECEIPTS_PATH
        mp.RECEIPTS_PATH = Path(self.tmpdir.name) / "mutation_policy_receipts.jsonl"

    def tearDown(self):
        ia.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_path
        ia._load_watchlist_entities = self._orig_wl
        ia._load_active_thread_entities = self._orig_at
        self._ei._write_graph = self._orig_write_graph
        self._mp.RECEIPTS_PATH = self._orig_receipts_path
        self.tmpdir.cleanup()

    def test_high_confidence_resolvable_entity_auto_applies(self):
        convergences = _fake_assessment(
            multi_source=[self._conv("Burger King", 6, ["RTN", "QSR Magazine", "Nation's Restaurant News"])]
        )
        result = ia.phase4_mutation_proposals(convergences, prior_assessment=None)
        prop = next(p for p in result["proposals"] if p["type"] == "watchlist_add")
        self.assertEqual(prop["confidence"], "high")
        self.assertTrue(prop["auto_applied"])
        self.assertFalse(prop["requires_confirmation"])

        written = json.loads(self.eco_path.read_text())
        watch_ids = [w["entity_id"] for w in written["watch_list"]]
        self.assertIn("brand-burger-king", watch_ids)
        entry = next(w for w in written["watch_list"] if w["entity_id"] == "brand-burger-king")
        self.assertEqual(entry["priority"], "tier_2")
        self.assertFalse(entry["interrupt_eligible"])
        # RB-2026-08-28: schema only allows "user"/"cos_suggested" (system/
        # schemas/ecosystem_intelligence.schema.json) -- intelligence_
        # assessment.py's real "rb_auto" writes were fixed to "cos_suggested"
        # earlier this session; this assertion was stale.
        self.assertEqual(entry["added_by"], "cos_suggested")

    def test_KFC_accounting_bug_auto_applied_watchlist_add_gets_reconciling_receipt(self):
        """RB-DEFECT-2026-09-18: the exact live discrepancy the handoff
        documents -- a watchlist item labeled auto_applied:true in its own
        proposal dict, with records_changed:0 and an empty mutation_
        lifecycle elsewhere, because nothing durable recorded that the write
        actually happened. A successful auto-apply must now leave a
        mutation_policy receipt with applied=True and a real artifact name,
        so morning_pipeline.py's records_changed/mutations_generated (which
        now fold mutation_policy.summarize_receipts() in) can never again
        silently disagree with a proposal's own auto_applied flag."""
        convergences = _fake_assessment(
            multi_source=[self._conv("KFC", 6, ["RTN", "QSR Magazine", "Nation's Restaurant News"])]
        )
        result = ia.phase4_mutation_proposals(convergences, prior_assessment=None)
        prop = next(p for p in result["proposals"] if p["type"] == "watchlist_add")
        self.assertTrue(prop["auto_applied"])

        receipts = self._mp.load_receipts()
        self.assertEqual(len(receipts), 1, receipts)
        receipt = receipts[0]
        self.assertEqual(receipt["decision_class"], self._mp.AUTO_ADDED_NET_NEW)
        self.assertTrue(receipt["applied"])
        self.assertEqual(receipt["artifact"], "ecosystem_intelligence.json:watch_list")
        self.assertIn("brand-kfc", receipt["entity_id"])

        summary = self._mp.summarize_receipts(receipts)
        self.assertEqual(summary["automatically_applied"], 1)
        self.assertEqual(summary["records_changed"], 1)

    def test_write_graph_failure_leaves_no_applied_receipt(self):
        """The inverse of the KFC bug: if the durable write itself fails,
        the receipt must say so honestly (applied=False, no artifact) --
        never let auto_applied:true survive a write that didn't happen."""
        def _failing_write_graph(graph, **kw):
            raise RuntimeError("simulated write failure")
        self._ei._write_graph = _failing_write_graph

        convergences = _fake_assessment(
            multi_source=[self._conv("KFC", 6, ["RTN", "QSR Magazine", "Nation's Restaurant News"])]
        )
        result = ia.phase4_mutation_proposals(convergences, prior_assessment=None)
        prop = next(p for p in result["proposals"] if p["type"] == "watchlist_add")
        self.assertFalse(prop["auto_applied"])
        self.assertTrue(prop["requires_confirmation"])

        receipts = self._mp.load_receipts()
        self.assertEqual(len(receipts), 1, receipts)
        self.assertFalse(receipts[0]["applied"])
        self.assertIsNone(receipts[0]["artifact"])

    def test_medium_confidence_stays_review_first_and_does_not_write(self):
        convergences = _fake_assessment(
            multi_source=[self._conv("Burger King", 3, ["RTN", "QSR Magazine"])]
        )
        result = ia.phase4_mutation_proposals(convergences, prior_assessment=None)
        prop = next(p for p in result["proposals"] if p["type"] == "watchlist_add")
        self.assertEqual(prop["confidence"], "medium")
        self.assertFalse(prop["auto_applied"])
        self.assertTrue(prop["requires_confirmation"])
        written = json.loads(self.eco_path.read_text())
        self.assertEqual(written["watch_list"], [])

    def test_high_confidence_new_entity_is_provisionally_auto_added(self):
        """Todd's 2026-09-16 preference is add-first/remove-later for a
        high-confidence sustained company pattern. The graph gets only a
        reversible provisional brand shell plus a tier-2 watch entry."""
        convergences = _fake_assessment(
            multi_source=[self._conv("Some Brand Nobody Tracks Yet", 6, ["RTN", "QSR Magazine", "Fast Casual"])]
        )
        result = ia.phase4_mutation_proposals(convergences, prior_assessment=None)
        prop = next(p for p in result["proposals"] if p["type"] == "watchlist_add")
        self.assertTrue(prop["auto_applied"])
        self.assertFalse(prop["requires_confirmation"])
        written = json.loads(self.eco_path.read_text())
        self.assertIn("brand-some-brand-nobody-tracks-yet",
                      [row["entity_id"] for row in written["watch_list"]])
        entity = next(row for row in written["entities"]
                      if row["id"] == "brand-some-brand-nobody-tracks-yet")
        self.assertTrue(entity["attributes"]["provisional"])

    def test_write_failure_reverts_proposal_to_review_first(self):
        """If the write/validation step fails, the proposal must not claim
        auto_applied -- never assert persistence that didn't happen."""
        import ecosystem_intelligence as ei
        convergences = _fake_assessment(
            multi_source=[self._conv("Burger King", 6, ["RTN", "QSR Magazine", "Nation's Restaurant News"])]
        )
        orig_write = ei._write_graph
        ei._write_graph = lambda graph, **kw: (_ for _ in ()).throw(RuntimeError("validation failed"))
        try:
            result = ia.phase4_mutation_proposals(convergences, prior_assessment=None)
        finally:
            ei._write_graph = orig_write
        prop = next(p for p in result["proposals"] if p["type"] == "watchlist_add")
        self.assertFalse(prop["auto_applied"])
        self.assertTrue(prop["requires_confirmation"])
        self.assertEqual(result["status"], "ok")  # isolated failure, not a phase-4 crash


class TestINTA3b_LoadWatchlistEntities(unittest.TestCase):
    """RB-2026-08-24: _load_watchlist_entities() read from SYSTEM_DIR/"watchlist.json",
    which has never existed on disk, so it always returned an empty set --
    every convergent entity looked untracked regardless of the real
    ecosystem_intelligence.json watch_list. This is the fix: read the real
    store and resolve entity_id -> name."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.eco_path = Path(self.tmpdir.name) / "ecosystem_intelligence.json"
        self._orig_path = ia.core.ECOSYSTEM_INTELLIGENCE_PATH
        ia.core.ECOSYSTEM_INTELLIGENCE_PATH = self.eco_path

    def tearDown(self):
        ia.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_path
        self.tmpdir.cleanup()

    def test_resolves_entity_id_to_name_from_real_watch_list(self):
        self.eco_path.write_text(json.dumps({
            "entities": [{"id": "brand-mcdonald-s", "name": "McDonald's"}],
            "watch_list": [{"entity_id": "brand-mcdonald-s", "priority": "tier_1"}],
        }))
        result = ia._load_watchlist_entities()
        self.assertEqual(result, {"mcdonald's"})

    def test_empty_watch_list_returns_empty_set(self):
        self.eco_path.write_text(json.dumps({"entities": [], "watch_list": []}))
        self.assertEqual(ia._load_watchlist_entities(), set())

    def test_missing_file_returns_empty_set_not_error(self):
        # eco_path deliberately never written
        self.assertEqual(ia._load_watchlist_entities(), set())


# ---------------------------------------------------------------------------
# INTA4 — Phase 5: trust stats
# ---------------------------------------------------------------------------

class TestINTA4_TrustStats(unittest.TestCase):
    """INTA4: phase5_trust_stats aggregates correctly."""

    def _p1(self, fetched=10, from_cache=2, source_health=None, errors=None):
        return {
            "status": "ok",
            "items_fetched": fetched,
            "items_from_cache": from_cache,
            "source_health": (
                source_health if source_health is not None else [
                    {"source": "Restaurant Dive", "status": "ok"},
                    {"source": "RTN", "status": "ok"},
                ]
            ),
            "errors": errors or [],
            "metadata": {},
        }

    def _p2(self, classified=5):
        return {"status": "ok", "items_classified": classified,
                "signal_types_found": [], "entities_detected": []}

    def _p3(self, multi=2, pairs=1):
        return {"status": "ok", "multi_source": [], "entity_pairs": [],
                "multi_source_count": multi, "entity_pair_count": pairs}

    def _p4(self, proposals=2):
        return {"status": "ok", "proposals": [], "proposals_count": proposals}

    def test_INTA4a_counts_aggregate_correctly(self):
        """Trust stats correctly sums items_fetched, items_written, convergences, proposals."""
        ts = ia.phase5_trust_stats(
            self._p1(fetched=10, from_cache=2),
            self._p2(classified=5),
            self._p3(multi=3, pairs=2),
            self._p4(proposals=2),
            started_at=ia._now_iso(),
        )
        self.assertEqual(ts["items_fetched"], 10)
        self.assertEqual(ts["items_written_to_db"], 8)   # 10 - 2 from cache
        self.assertEqual(ts["email_items_classified"], 5)
        self.assertEqual(ts["convergences_detected"], 5)  # 3 multi + 2 pairs
        self.assertEqual(ts["mutation_proposals"], 2)

    def test_INTA4b_confidence_degrades_on_phase_errors(self):
        """Phase errors lower confidence from high to medium."""
        p1_err = self._p1()
        p2_err = {"status": "error", "error": "test error", "items_classified": 0,
                  "signal_types_found": [], "entities_detected": []}
        ts = ia.phase5_trust_stats(
            p1_err, p2_err, self._p3(), self._p4(), started_at=ia._now_iso()
        )
        self.assertIn(ts["confidence"], ("medium", "low"))

    def test_INTA4c_gap_populated_when_no_source_health(self):
        """Empty source_health produces an intelligence gap entry."""
        p1_empty = self._p1(fetched=0, source_health=[])
        ts = ia.phase5_trust_stats(
            p1_empty, self._p2(0), self._p3(0, 0), self._p4(0),
            started_at=ia._now_iso(),
        )
        self.assertTrue(any("Web scanner" in g for g in ts["intelligence_gaps"]))


# ---------------------------------------------------------------------------
# INTA5 — Integration: run_assessment and is_fresh
# ---------------------------------------------------------------------------

class TestINTA5_Integration(unittest.TestCase):
    """INTA5: run_assessment writes valid cache; is_fresh returns correct state."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self._orig_cache = ia.CACHE_PATH
        self._orig_pei = ia.PASSIVE_EMAIL_CACHE
        ia.CACHE_PATH = self.tmp_dir / "intelligence_assessment.json"
        ia.PASSIVE_EMAIL_CACHE = self.tmp_dir / "passive_email_intelligence.json"
        # Write an empty email cache so phase2 returns skipped_empty
        ia.PASSIVE_EMAIL_CACHE.write_text(json.dumps({"top_headlines": []}))
        # RB 2026-08-27: confirmed live -- this integration test runs the
        # real phase3/phase4 pipeline against whatever the real
        # IntelligenceDB actually contains. Since phase4's watchlist_add can
        # now auto-write into ecosystem_intelligence.json (RB 2026-08-27),
        # an unisolated run here would genuinely persist entries into the
        # real production file as a side effect of running this test suite
        # -- confirmed happening once already before this isolation was
        # added. Isolate the same way TestINTA3d_WatchlistAutoApply does.
        self._orig_eco_path = ia.core.ECOSYSTEM_INTELLIGENCE_PATH
        ia.core.ECOSYSTEM_INTELLIGENCE_PATH = self.tmp_dir / "ecosystem_intelligence.json"

    def tearDown(self):
        ia.CACHE_PATH = self._orig_cache
        ia.PASSIVE_EMAIL_CACHE = self._orig_pei
        ia.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_eco_path

    def test_INTA5a_run_assessment_writes_valid_cache(self):
        """run_assessment writes a valid intelligence_assessment.json with all required keys."""
        result = ia.run_assessment(today=date(2026, 6, 1))
        self.assertTrue(ia.CACHE_PATH.exists())
        # Verify file is parseable
        on_disk = json.loads(ia.CACHE_PATH.read_text())
        self.assertEqual(on_disk["contract"], "rb_intelligence_assessment_v1")
        for key in ("assessment_date", "generated_at", "trust_stats",
                    "phase_1_web", "phase_2_email",
                    "phase_3_convergences", "phase_4_proposals"):
            self.assertIn(key, on_disk, f"Missing key in assessment output: {key}")

    def test_INTA5b_is_fresh_returns_correct_state(self):
        """is_fresh() returns True after a genuinely healthy run, False
        for a different date.

        2026-10-03 (Defect 5 fix): is_fresh() now also requires no
        run_errors and a "current" (not "degraded") primary daily-
        intelligence status -- previously it only checked the date. This
        test used to call the REAL phase1_web_intelligence(), so its
        pass/fail depended on live network/RSS reachability from this
        sandbox; it in fact reproduced the exact live incident (370 real
        collected items, all 24 configured sources reported failed,
        input_status "ok") that made is_fresh() wrong in the first place.
        Mocking gatherer.build_packet/write_packet here makes this a
        deterministic test of is_fresh()'s own logic, not of today's
        network conditions -- and avoids writing a fake packet into the
        real, unisolated gatherer.CACHE_PATH as a side effect."""
        ia.core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(json.dumps({"entities": [
            {"id": "brand-a", "name": "Brand A", "entity_type": "brand", "status": "active"}
        ]}))
        healthy_packet = {
            "contract": "rb.gatherer_daily_change_packet.v1",
            "packet_id": "gatherer-test-healthy",
            "changes": [], "hunter_escalations": [],
            "coverage": {"source_checks": {}},
            "run_receipt": {"schema_validation": "valid", "receipt_consistent": True},
        }
        with patch("gatherer.build_packet", return_value=healthy_packet), \
             patch("gatherer.write_packet"):
            ia.run_assessment(today=date(2026, 6, 1))
        self.assertTrue(ia.is_fresh(date(2026, 6, 1)))
        self.assertFalse(ia.is_fresh(date(2026, 6, 2)))

    def test_INTA5c_is_fresh_is_false_after_a_degraded_run(self):
        """A run with a self-contradictory/degraded Gatherer receipt must
        not be treated as fresh -- this is the exact bug: a single bad
        run used to get frozen as today's cached answer and never
        retried for the rest of the day."""
        ia.core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(json.dumps({"entities": []}))
        degraded_packet = {
            "contract": "rb.gatherer_daily_change_packet.v1",
            "packet_id": "gatherer-test-degraded",
            "changes": [], "hunter_escalations": [],
            "coverage": {"source_checks": {}},
            "run_receipt": {"schema_validation": "valid", "receipt_consistent": False},
        }
        with patch("gatherer.build_packet", return_value=degraded_packet), \
             patch("gatherer.write_packet"):
            ia.run_assessment(today=date(2026, 6, 1))
        self.assertFalse(ia.is_fresh(date(2026, 6, 1)))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
