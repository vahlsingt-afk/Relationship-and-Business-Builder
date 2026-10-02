"""
test_ctd_synthesis.py

RB-2026-07-17: _compute_ctd_db_convergence (daily_brief.py) fell back to
generic boilerplate -- "Review the N gathered intelligence items for X.
Assess whether this sustained activity affects your strategy..." -- for
every convergence item that didn't already have a deterministic
role-context match (an entity in Todd's active opportunity pipeline or
today's escalated watchlist). Most convergence items don't. This adds one
batched LLM synthesis call (brief_synthesis.py) for those items; any
failure must fall back to the exact current deterministic text.

Uses a minimal fake IntelligenceDB rather than the real one -- these tests
exercise daily_brief.py's wiring/fallback behavior, not the DB query layer
itself (see test_ctd_db_convergence.py for that).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402


class _FakeDB:
    def __init__(self, convergences=None, pairs=None):
        self._convergences = convergences or []
        self._pairs = pairs or []

    def find_convergences(self, min_entity_hits, days):
        return self._convergences

    def cross_entity_convergence(self, days, min_shared_items):
        return self._pairs


def _multi_source_conv(entity="&pizza", count=36, sources=None, distinct_days=2):
    sources = sources or ["a", "b", "c", "d", "e", "f"]
    return {
        "entity": entity, "item_count": count, "sources": sources,
        "signal_types": ["press_release"],
        "items": [{"gathered_date": "2026-07-14", "title": f"{entity} news"}],
        "distinct_days": distinct_days,
    }


def _pair_conv(entity_a="Burger King", entity_b="Firehouse Subs", shared=14, distinct_days=2):
    return {
        "entity_a": entity_a, "entity_b": entity_b, "shared_count": shared,
        "shared_items": [{"title": f"{entity_a} and {entity_b} news"}],
        "distinct_days": distinct_days,
    }


class TestCtdSynthesisFallback(unittest.TestCase):
    """RB-2026-08-25: no role-context match AND synthesis unavailable/
    unsuccessful now means the item is DROPPED, not shown with generic
    boilerplate. This is the Dot-Connecting Mandate this module already
    documented ("if you cannot name the connection, the signal does not
    belong") but never actually enforced -- confirmed live: entities with
    no tie to Todd's watchlist/opportunities rendered here with boilerplate
    text ("Review the N gathered intelligence items...") that's also an
    exact duplicate of the Intelligence Brief's own Sustained Patterns
    section. See test_ctd_db_convergence.py for the distinct_days >= 2
    gate this also depends on."""

    def setUp(self):
        # Isolate the same-day synthesis cache from other test runs.
        cache_path = db._ctd_synthesis_cache_path()
        if cache_path.exists():
            cache_path.unlink()

    def test_multi_source_dropped_when_no_connection_and_synthesis_unavailable(self):
        fake_db = _FakeDB(convergences=[_multi_source_conv()])
        with patch("brief_synthesis.synthesize_signals", return_value=None):
            items = db._compute_ctd_db_convergence(fake_db, role_context={"opportunities": {}, "watchlist": {}})
        self.assertEqual(items, [])

    def test_entity_pair_dropped_when_no_connection_and_synthesis_unavailable(self):
        fake_db = _FakeDB(pairs=[_pair_conv()])
        with patch("brief_synthesis.synthesize_signals", return_value=None):
            items = db._compute_ctd_db_convergence(fake_db, role_context={"opportunities": {}, "watchlist": {}})
        self.assertEqual(items, [])


class TestCtdSynthesisSuccess(unittest.TestCase):
    def setUp(self):
        cache_path = db._ctd_synthesis_cache_path()
        if cache_path.exists():
            cache_path.unlink()

    def tearDown(self):
        cache_path = db._ctd_synthesis_cache_path()
        if cache_path.exists():
            cache_path.unlink()

    def test_multi_source_uses_synthesized_text(self):
        fake_db = _FakeDB(convergences=[_multi_source_conv()])

        def fake_synth(items, role_summary):
            return {items[0]["key"]: {
                "why": "Sustained coverage signals unit growth worth a prospecting flag.",
                "action": "Flag for next territory review.",
            }}

        with patch("brief_synthesis.synthesize_signals", side_effect=fake_synth):
            items = db._compute_ctd_db_convergence(fake_db, role_context={"opportunities": {}, "watchlist": {}})
        self.assertEqual(items[0]["why_it_matters"],
                          "Sustained coverage signals unit growth worth a prospecting flag.")
        self.assertEqual(items[0]["recommended_action"], "Flag for next territory review.")
        self.assertNotIn("Review the", items[0]["recommended_action"])

    def test_role_context_match_never_triggers_synthesis(self):
        fake_db = _FakeDB(convergences=[_multi_source_conv(entity="Global Payments")])
        role_context = {"opportunities": {"global payments": "Global Payments Inc."}, "watchlist": {}}
        with patch("brief_synthesis.synthesize_signals") as mock_synth:
            items = db._compute_ctd_db_convergence(fake_db, role_context=role_context)
        mock_synth.assert_not_called()
        self.assertIn("is one of your active opportunities", items[0]["why_it_matters"])

    def test_role_context_match_still_dropped_if_not_actually_multi_day(self):
        """RB-2026-08-25: distinct_days < 2 filters the item out before the
        role_note/Dot-Connecting-Mandate check even runs -- a same-day
        burst isn't "sustained" just because the entity happens to also be
        one of Todd's active opportunities. The two gates are independent;
        role_note doesn't override the day-span requirement."""
        fake_db = _FakeDB(convergences=[_multi_source_conv(entity="Global Payments", distinct_days=1)])
        role_context = {"opportunities": {"global payments": "Global Payments Inc."}, "watchlist": {}}
        with patch("brief_synthesis.synthesize_signals") as mock_synth:
            items = db._compute_ctd_db_convergence(fake_db, role_context=role_context)
        self.assertEqual(items, [])
        mock_synth.assert_not_called()

    def test_synthesis_result_cached_across_calls_same_day(self):
        fake_db = _FakeDB(convergences=[_multi_source_conv()])

        def fake_synth(items, role_summary):
            return {items[0]["key"]: {"why": "Cached text.", "action": "Cached action."}}

        with patch("brief_synthesis.synthesize_signals", side_effect=fake_synth) as mock_synth:
            db._compute_ctd_db_convergence(fake_db, role_context={"opportunities": {}, "watchlist": {}})
            self.assertEqual(mock_synth.call_count, 1)
            items2 = db._compute_ctd_db_convergence(fake_db, role_context={"opportunities": {}, "watchlist": {}})
            self.assertEqual(mock_synth.call_count, 1)  # not called again -- served from cache
        self.assertEqual(items2[0]["why_it_matters"], "Cached text.")
        self.assertEqual(items2[0]["recommended_action"], "Cached action.")


if __name__ == "__main__":
    unittest.main()
