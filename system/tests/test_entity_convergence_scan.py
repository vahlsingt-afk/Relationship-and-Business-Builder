"""
test_entity_convergence_scan.py — RB-2026-08-28.

Priority item #5 from the same-day strategic assessment ("the least-built
capability... seeing what the CEO doesn't see"), scoped as v1: run
signal_synthesis.py's already-tested, mechanical (no free-text generation)
cross-store pattern engine across active Blue Sheet accounts, tracked
competitors, and the FULL watchlist (widened from tier_1-only per Todd's
explicit correction the same day) every morning, surface only genuinely
actionable classifications, and never re-show a persisting finding in full
every single day.

Tests mock signal_synthesis.synthesize_entity_signals and
_watched_entity_names directly -- the underlying engine already has its
own 67-test suite; this file tests entity_convergence_scan.py's own logic
(filtering, new-vs-persisting state tracking, result persistence) in
isolation.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import entity_convergence_scan as ecs  # noqa: E402


def _fake_result(pattern: str, confidence: str, signal_count: int = 5) -> dict:
    return {
        "entity": "placeholder",
        "dominant_pattern": pattern,
        "pattern_confidence": confidence,
        "synthesis_hypothesis": "A test hypothesis.",
        "opportunity_or_risk": "A test opportunity/risk read.",
        "signal_count": signal_count,
    }


class _IsolatedStateMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self._orig_state = ecs.STATE_PATH
        self._orig_result = ecs.RESULT_PATH
        ecs.STATE_PATH = tmp / "state.json"
        ecs.RESULT_PATH = tmp / "result.json"
        # RB-2026-09-16: _entity_aliases_by_name() (run_scan()'s alias-
        # expansion lookup) reads the real competitor registry unless
        # isolated -- without this, every test here would silently touch
        # real production competitor data on every run_scan() call.
        self._orig_cic_root = ecs.cic.ROOT
        ecs.cic.ROOT = tmp / "competitor_intelligence"

    def tearDown(self):
        ecs.cic.ROOT = self._orig_cic_root
        ecs.STATE_PATH = self._orig_state
        ecs.RESULT_PATH = self._orig_result
        self._tmpdir.cleanup()


class TestActionablePatternFiltering(_IsolatedStateMixin, unittest.TestCase):
    def test_stable_pattern_excluded_regardless_of_confidence(self):
        with patch.object(ecs, "_watched_entity_names", return_value=["Acme Co"]), \
             patch.object(ecs.ss, "synthesize_entity_signals",
                           return_value=_fake_result("stable", "high")):
            result = ecs.run_scan()
        self.assertEqual(result["new_findings"], [])
        self.assertEqual(result["persisting_findings"], [])

    def test_unknown_pattern_excluded(self):
        with patch.object(ecs, "_watched_entity_names", return_value=["Acme Co"]), \
             patch.object(ecs.ss, "synthesize_entity_signals",
                           return_value=_fake_result("unknown", "high")):
            result = ecs.run_scan()
        self.assertEqual(result["new_findings"], [])

    def test_low_confidence_actionable_pattern_excluded(self):
        with patch.object(ecs, "_watched_entity_names", return_value=["Acme Co"]), \
             patch.object(ecs.ss, "synthesize_entity_signals",
                           return_value=_fake_result("exit_positioning", "low")):
            result = ecs.run_scan()
        self.assertEqual(result["new_findings"], [])

    def test_medium_confidence_actionable_pattern_included(self):
        with patch.object(ecs, "_watched_entity_names", return_value=["Acme Co"]), \
             patch.object(ecs.ss, "synthesize_entity_signals",
                           return_value=_fake_result("distress", "medium")):
            result = ecs.run_scan()
        self.assertEqual(len(result["new_findings"]), 1)
        self.assertEqual(result["new_findings"][0]["dominant_pattern"], "distress")

    def test_high_confidence_actionable_pattern_included(self):
        with patch.object(ecs, "_watched_entity_names", return_value=["Acme Co"]), \
             patch.object(ecs.ss, "synthesize_entity_signals",
                           return_value=_fake_result("consolidation", "high")):
            result = ecs.run_scan()
        self.assertEqual(len(result["new_findings"]), 1)

    def test_one_entity_failure_does_not_block_others(self):
        def side_effect(name, **kwargs):
            if name == "Broken Co":
                raise RuntimeError("boom")
            return _fake_result("growth_mode", "high")

        with patch.object(ecs, "_watched_entity_names", return_value=["Broken Co", "Good Co"]), \
             patch.object(ecs.ss, "synthesize_entity_signals", side_effect=side_effect):
            result = ecs.run_scan()
        self.assertEqual(len(result["new_findings"]), 1)
        self.assertEqual(result["new_findings"][0]["entity"], "Good Co")


class TestNewVsPersistingState(_IsolatedStateMixin, unittest.TestCase):
    def test_first_ever_finding_is_new(self):
        with patch.object(ecs, "_watched_entity_names", return_value=["Acme Co"]), \
             patch.object(ecs.ss, "synthesize_entity_signals",
                           return_value=_fake_result("distress", "high")):
            result = ecs.run_scan()
        self.assertEqual(len(result["new_findings"]), 1)
        self.assertEqual(result["new_findings"][0]["first_seen"], result["generated_at"][:10])

    def test_same_pattern_next_run_is_persisting_not_new(self):
        with patch.object(ecs, "_watched_entity_names", return_value=["Acme Co"]), \
             patch.object(ecs.ss, "synthesize_entity_signals",
                           return_value=_fake_result("distress", "high")):
            first = ecs.run_scan()
            second = ecs.run_scan()
        self.assertEqual(len(first["new_findings"]), 1)
        self.assertEqual(len(second["new_findings"]), 0)
        self.assertEqual(len(second["persisting_findings"]), 1)

    def test_persisting_finding_keeps_original_first_seen_date(self):
        with patch.object(ecs, "_watched_entity_names", return_value=["Acme Co"]), \
             patch.object(ecs.ss, "synthesize_entity_signals",
                           return_value=_fake_result("distress", "high")):
            first = ecs.run_scan()
            second = ecs.run_scan()
        self.assertEqual(
            second["persisting_findings"][0]["first_seen"],
            first["new_findings"][0]["first_seen"],
        )

    def test_pattern_change_is_new_again_not_persisting(self):
        with patch.object(ecs, "_watched_entity_names", return_value=["Acme Co"]):
            with patch.object(ecs.ss, "synthesize_entity_signals",
                               return_value=_fake_result("distress", "high")):
                ecs.run_scan()
            with patch.object(ecs.ss, "synthesize_entity_signals",
                               return_value=_fake_result("growth_mode", "high")):
                second = ecs.run_scan()
        self.assertEqual(len(second["new_findings"]), 1)
        self.assertEqual(second["new_findings"][0]["dominant_pattern"], "growth_mode")
        self.assertEqual(len(second["persisting_findings"]), 0)

    def test_dropping_below_actionable_then_returning_is_new_again(self):
        """An entity that goes stable for a cycle and then reconverges on
        the same pattern should read as new information again, not a
        continuous persisting streak -- the state file only carries
        forward what was actually actionable last time it ran."""
        with patch.object(ecs, "_watched_entity_names", return_value=["Acme Co"]):
            with patch.object(ecs.ss, "synthesize_entity_signals",
                               return_value=_fake_result("distress", "high")):
                ecs.run_scan()
            with patch.object(ecs.ss, "synthesize_entity_signals",
                               return_value=_fake_result("stable", "high")):
                ecs.run_scan()
            with patch.object(ecs.ss, "synthesize_entity_signals",
                               return_value=_fake_result("distress", "high")):
                third = ecs.run_scan()
        self.assertEqual(len(third["new_findings"]), 1)


class TestResultPersistence(_IsolatedStateMixin, unittest.TestCase):
    def test_result_written_to_result_path(self):
        with patch.object(ecs, "_watched_entity_names", return_value=["Acme Co"]), \
             patch.object(ecs.ss, "synthesize_entity_signals",
                           return_value=_fake_result("distress", "high")):
            ecs.run_scan()
        self.assertTrue(ecs.RESULT_PATH.exists())
        saved = json.loads(ecs.RESULT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["new_findings"]), 1)

    def test_load_last_result_reads_what_was_saved(self):
        with patch.object(ecs, "_watched_entity_names", return_value=["Acme Co"]), \
             patch.object(ecs.ss, "synthesize_entity_signals",
                           return_value=_fake_result("distress", "high")):
            ecs.run_scan()
        loaded = ecs.load_last_result()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["entities_scanned"], 1)

    def test_load_last_result_returns_none_when_never_run(self):
        self.assertIsNone(ecs.load_last_result())

    def test_state_persists_across_process_via_file(self):
        with patch.object(ecs, "_watched_entity_names", return_value=["Acme Co"]), \
             patch.object(ecs.ss, "synthesize_entity_signals",
                           return_value=_fake_result("distress", "high")):
            ecs.run_scan()
        state = ecs._load_state()
        self.assertIn("Acme Co", state)
        self.assertEqual(state["Acme Co"]["dominant_pattern"], "distress")


class TestWatchedEntityNamesScope(unittest.TestCase):
    """RB-2026-08-29: confirms the scan uses the real canonical watchlist
    (entity_alerts.MANDATORY_ALL, Todd's 120-150-company scope from
    2026-08-19) -- not ecosystem_intelligence.json's much smaller,
    stale watch_list array (15 entries), which was a real bug in the
    2026-08-28 v1 build that Todd caught live."""

    def test_includes_mandatory_all_entities(self):
        with patch.object(ecs.ea, "MANDATORY_ALL", ["Brand X", "Vendor Y"]), \
             patch.object(ecs.bs_common, "load_registry", return_value={"registry": []}), \
             patch.object(ecs.cic, "load_registry", return_value={"registry": []}):
            names = ecs._watched_entity_names()
        self.assertIn("Brand X", names)
        self.assertIn("Vendor Y", names)


class TestEntityAliasesByName(_IsolatedStateMixin, unittest.TestCase):
    """RB-DEFECT (2026-09-16): _watched_entity_names() returns only the
    single canonical display_name per competitor, discarding each one's
    real, already-recorded alias list -- a signal mentioning a tracked
    competitor only by an alternate name (e.g. "NCR" for "NCR Voyix")
    never counted toward that entity's convergence pattern. Live incident
    confirmed via signal_synthesis's own _entity_matches() before this fix.
    """

    def _register(self, slug: str, display_name: str, aliases: list[str]) -> None:
        ecs.cic.create_competitor_shell(slug, display_name, f"vendor-{slug}")
        ecs.cic.register_competitor(slug, display_name)
        comp_path = ecs.cic.competitor_dir(slug) / "competitor.json"
        comp = ecs.cic.load_json(comp_path)
        comp["aliases"] = aliases
        ecs.cic.save_json(comp_path, comp)

    def test_returns_real_aliases_keyed_by_display_name(self):
        self._register("ncr", "NCR Voyix", ["NCR Corporation", "NCR Voyix", "NCR Aloha", "Aloha POS"])
        result = ecs._entity_aliases_by_name()
        self.assertEqual(
            result["NCR Voyix"],
            ["NCR Corporation", "NCR Voyix", "NCR Aloha", "Aloha POS"],
        )

    def test_competitor_with_no_aliases_is_omitted_not_empty_list(self):
        self._register("toast", "Toast", [])
        result = ecs._entity_aliases_by_name()
        self.assertNotIn("Toast", result)

    def test_one_unreadable_competitor_does_not_block_the_rest(self):
        self._register("ncr", "NCR Voyix", ["NCR Corporation"])
        # A registry entry pointing at a competitor_slug with no real
        # competitor.json on disk -- simulates a corrupt/partial record.
        reg = ecs.cic.load_registry()
        reg["registry"].append({"competitor_slug": "ghost-vendor"})
        ecs.cic.save_registry(reg)

        result = ecs._entity_aliases_by_name()
        self.assertEqual(result.get("NCR Voyix"), ["NCR Corporation"])

    def test_run_scan_passes_real_aliases_through_to_synthesize(self):
        """Proves the actual wiring, not just the lookup function in
        isolation: run_scan() must call synthesize_entity_signals with
        this entity's real aliases."""
        self._register("ncr", "NCR Voyix", ["NCR Corporation"])
        captured_kwargs = {}

        def _capture(name, **kwargs):
            captured_kwargs.update(kwargs)
            return _fake_result("stable", "high")

        with patch.object(ecs, "_watched_entity_names", return_value=["NCR Voyix"]), \
             patch.object(ecs.ss, "synthesize_entity_signals", side_effect=_capture):
            ecs.run_scan()
        self.assertEqual(captured_kwargs.get("aliases"), ["NCR Corporation"])

    def test_run_scan_passes_none_for_entity_with_no_registered_aliases(self):
        captured_kwargs = {}

        def _capture(name, **kwargs):
            captured_kwargs.update(kwargs)
            return _fake_result("stable", "high")

        with patch.object(ecs, "_watched_entity_names", return_value=["Some Brand"]), \
             patch.object(ecs.ss, "synthesize_entity_signals", side_effect=_capture):
            ecs.run_scan()
        self.assertIsNone(captured_kwargs.get("aliases"))


if __name__ == "__main__":
    unittest.main()
