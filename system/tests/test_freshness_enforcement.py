"""
test_freshness_enforcement.py — Sprint H: Source Freshness Enforcement tests.

Tests:
  FE1 — _compute_operational_confidence is importable
  FE2 — all Tier 1 sources fresh → operational_confidence = HIGH
  FE3 — one Tier 1 source stale (>1h) → operational_confidence = LOW
  FE4 — two Tier 1 sources stale/blind → operational_confidence = CRITICAL
  FE5 — email + calendar both stale → operational_confidence = CRITICAL regardless of count
  FE6 — Tier 1 all fresh but empty items → operational_confidence = MEDIUM
  FE7 — missing Tier 1 sources (not in dict) counted as blind
  FE8 — source_trust_table item title contains OPERATIONAL CONFIDENCE
  FE9 — source_trust_table extras contain operational_confidence field
  FE10 — canonical brief top-level contains operational_confidence field
  FE11 — _source_age_hours returns None for missing timestamp
  FE12 — _source_age_hours returns correct age for known timestamp
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from daily_brief import (
    _compute_operational_confidence,
    _source_age_hours,
    _TIER1_SOURCES,
    _compute_source_trust_table,
)


def _fresh_source(item_count: int = 10) -> dict:
    """Source refreshed 30 minutes ago with items."""
    ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    return {"status": "refreshed", "last_refreshed_at": ts, "item_count": item_count}


def _stale_source(hours_ago: float = 3.0) -> dict:
    """Source refreshed N hours ago — stale but not blind."""
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {"status": "stale", "last_refreshed_at": ts, "item_count": 0}


def _blind_source() -> dict:
    """Source that is missing or >24h old."""
    ts = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    return {"status": "stale", "last_refreshed_at": ts, "item_count": 0}


def _missing_source() -> dict:
    """Source with no last_refreshed_at (pipeline never ran for it)."""
    return {"status": "skipped_no_raw_input", "item_count": 0}


def _all_fresh_sources() -> dict:
    return {key: _fresh_source() for key in _TIER1_SOURCES}


class FE1Import(unittest.TestCase):
    def test_FE1_compute_operational_confidence_callable(self):
        self.assertTrue(callable(_compute_operational_confidence))

    def test_FE1_source_age_hours_callable(self):
        self.assertTrue(callable(_source_age_hours))

    def test_FE1_tier1_sources_defined(self):
        self.assertIsInstance(_TIER1_SOURCES, (set, frozenset))
        self.assertGreater(len(_TIER1_SOURCES), 0)
        self.assertIn("email:bridgepoint", _TIER1_SOURCES)
        self.assertIn("email:personal", _TIER1_SOURCES)
        self.assertIn("calendar:bridgepoint", _TIER1_SOURCES)


class FE2HighConfidence(unittest.TestCase):
    def test_FE2_all_fresh_returns_high(self):
        result = _compute_operational_confidence(_all_fresh_sources())
        self.assertEqual(result["level"], "HIGH")

    def test_FE2_high_has_full_relationship_confidence(self):
        result = _compute_operational_confidence(_all_fresh_sources())
        self.assertEqual(result["relationship_confidence"], "full")

    def test_FE2_tier1_fresh_list_populated(self):
        result = _compute_operational_confidence(_all_fresh_sources())
        self.assertGreater(len(result["tier1_fresh"]), 0)
        self.assertEqual(len(result["tier1_stale"]), 0)
        self.assertEqual(len(result["tier1_blind"]), 0)


class FE3LowOnOneStale(unittest.TestCase):
    def test_FE3_one_stale_source_returns_low(self):
        sources = _all_fresh_sources()
        sources["email:personal"] = _stale_source(hours_ago=2)
        result = _compute_operational_confidence(sources)
        self.assertEqual(result["level"], "LOW")

    def test_FE3_stale_source_appears_in_tier1_stale(self):
        sources = _all_fresh_sources()
        sources["email:personal"] = _stale_source(hours_ago=2)
        result = _compute_operational_confidence(sources)
        # Gmail (Personal) should be in tier1_stale
        self.assertTrue(any("Personal" in s or "personal" in s.lower()
                            for s in result["tier1_stale"]))

    def test_FE3_low_has_degraded_relationship_confidence(self):
        sources = _all_fresh_sources()
        sources["email:personal"] = _stale_source(hours_ago=2)
        result = _compute_operational_confidence(sources)
        self.assertIn(result["relationship_confidence"], ("degraded", "partial"))


class FE4CriticalOnTwoBlind(unittest.TestCase):
    def test_FE4_two_blind_sources_returns_critical(self):
        sources = _all_fresh_sources()
        sources["email:personal"] = _blind_source()
        sources["email:bridgepoint"] = _blind_source()
        result = _compute_operational_confidence(sources)
        self.assertEqual(result["level"], "CRITICAL")

    def test_FE4_two_missing_sources_returns_critical(self):
        sources = _all_fresh_sources()
        sources["calls"] = _missing_source()
        sources["messages"] = _missing_source()
        result = _compute_operational_confidence(sources)
        self.assertEqual(result["level"], "CRITICAL")

    def test_FE4_critical_has_blind_relationship_confidence(self):
        sources = _all_fresh_sources()
        sources["email:personal"] = _blind_source()
        sources["calendar:bridgepoint"] = _blind_source()
        result = _compute_operational_confidence(sources)
        self.assertEqual(result["relationship_confidence"], "blind")


class FE5CriticalEmailAndCalendar(unittest.TestCase):
    def test_FE5_email_and_calendar_stale_returns_critical(self):
        """Email (any) + Calendar (any) both stale → CRITICAL, even if only 2 sources degraded."""
        sources = _all_fresh_sources()
        sources["email:bridgepoint"] = _stale_source(hours_ago=2)
        sources["calendar:personal"] = _stale_source(hours_ago=2)
        result = _compute_operational_confidence(sources)
        self.assertEqual(result["level"], "CRITICAL")

    def test_FE5_only_email_stale_is_low_not_critical(self):
        """Email stale but calendar still fresh → LOW (not CRITICAL)."""
        sources = _all_fresh_sources()
        sources["email:bridgepoint"] = _stale_source(hours_ago=2)
        # calendar sources remain fresh
        result = _compute_operational_confidence(sources)
        self.assertEqual(result["level"], "LOW")


class FE6MediumOnEmptyItems(unittest.TestCase):
    def test_FE6_fresh_but_zero_items_returns_medium(self):
        sources = {key: _fresh_source(item_count=0) for key in _TIER1_SOURCES}
        result = _compute_operational_confidence(sources)
        self.assertEqual(result["level"], "MEDIUM")

    def test_FE6_tier1_empty_list_populated(self):
        sources = {key: _fresh_source(item_count=0) for key in _TIER1_SOURCES}
        result = _compute_operational_confidence(sources)
        self.assertGreater(len(result["tier1_empty"]), 0)


class FE7MissingKeysBlind(unittest.TestCase):
    def test_FE7_empty_sources_dict_returns_critical(self):
        """All Tier 1 keys missing from dict → all blind → CRITICAL."""
        result = _compute_operational_confidence({})
        self.assertEqual(result["level"], "CRITICAL")
        self.assertEqual(len(result["tier1_blind"]), len(_TIER1_SOURCES))

    def test_FE7_tier1_degraded_count_correct(self):
        result = _compute_operational_confidence({})
        self.assertEqual(
            result["tier1_degraded_count"],
            len(result["tier1_stale"]) + len(result["tier1_blind"])
        )


class FE8TrustTableTitle(unittest.TestCase):
    def _make_report_with_sources(self, sources: dict) -> dict:
        return {
            "daily_prep_summary": {
                "source_health": {"sources": sources}
            }
        }

    def test_FE8_title_contains_operational_confidence(self):
        report = self._make_report_with_sources(_all_fresh_sources())
        items = _compute_source_trust_table(report)
        self.assertGreater(len(items), 0)
        self.assertIn("OPERATIONAL CONFIDENCE", items[0]["title"])

    def test_FE8_title_contains_level(self):
        report = self._make_report_with_sources(_all_fresh_sources())
        items = _compute_source_trust_table(report)
        title = items[0]["title"]
        self.assertTrue(
            any(level in title for level in ("HIGH", "MEDIUM", "LOW", "CRITICAL")),
            f"Expected HIGH/MEDIUM/LOW/CRITICAL in title: {title}"
        )


class FE9TrustTableExtras(unittest.TestCase):
    def _get_extras(self, sources: dict) -> dict:
        report = {"daily_prep_summary": {"source_health": {"sources": sources}}}
        items = _compute_source_trust_table(report)
        return items[0].get("extras", {}) if items else {}

    def test_FE9_extras_has_operational_confidence(self):
        extras = self._get_extras(_all_fresh_sources())
        self.assertIn("operational_confidence", extras)

    def test_FE9_extras_has_relationship_confidence(self):
        extras = self._get_extras(_all_fresh_sources())
        self.assertIn("relationship_confidence", extras)

    def test_FE9_extras_has_tier1_lists(self):
        extras = self._get_extras(_all_fresh_sources())
        for field in ("tier1_fresh", "tier1_stale", "tier1_blind", "tier1_empty"):
            self.assertIn(field, extras, f"Missing extras field: {field}")

    def test_FE9_extras_has_operational_impact(self):
        extras = self._get_extras(_all_fresh_sources())
        self.assertIn("operational_impact", extras)
        self.assertIsInstance(extras["operational_impact"], str)
        self.assertGreater(len(extras["operational_impact"]), 10)

    def test_FE9_low_confidence_disposition_act_today(self):
        sources = _all_fresh_sources()
        sources["email:personal"] = _stale_source(hours_ago=2)
        report = {"daily_prep_summary": {"source_health": {"sources": sources}}}
        items = _compute_source_trust_table(report)
        self.assertEqual(items[0]["disposition"], "act_today")

    def test_FE9_high_confidence_disposition_monitor(self):
        report = {"daily_prep_summary": {"source_health": {"sources": _all_fresh_sources()}}}
        items = _compute_source_trust_table(report)
        self.assertEqual(items[0]["disposition"], "monitor")


class FE10CanonicalBriefTopLevel(unittest.TestCase):
    def test_FE10_build_canonical_brief_has_operational_confidence(self):
        """build_canonical_brief surfaces operational_confidence as a top-level field."""
        from daily_brief import build_canonical_brief
        # Minimal report that won't crash build_canonical_brief
        report = {
            "today": "2026-06-03",
            "relationship_signals": {},
            "loops": {},
            "active_threads": [],
            "daily_prep_summary": {
                "source_health": {"sources": _all_fresh_sources()}
            },
        }
        result = build_canonical_brief(report)
        self.assertIn("operational_confidence", result)
        self.assertIn(result["operational_confidence"],
                      ("HIGH", "MEDIUM", "LOW", "CRITICAL", "UNKNOWN"))

    def test_FE10_top_level_has_relationship_confidence(self):
        from daily_brief import build_canonical_brief
        report = {
            "today": "2026-06-03",
            "relationship_signals": {},
            "loops": {},
            "active_threads": [],
            "daily_prep_summary": {
                "source_health": {"sources": _all_fresh_sources()}
            },
        }
        result = build_canonical_brief(report)
        self.assertIn("relationship_confidence", result)

    def test_FE10_top_level_has_trust_score(self):
        from daily_brief import build_canonical_brief
        report = {
            "today": "2026-06-03",
            "relationship_signals": {},
            "loops": {},
            "active_threads": [],
        }
        result = build_canonical_brief(report)
        self.assertIn("trust_score", result)


class FE11SourceAgeHoursEdgeCases(unittest.TestCase):
    def test_FE11_none_for_missing_timestamp(self):
        self.assertIsNone(_source_age_hours({}))

    def test_FE11_none_for_empty_timestamp(self):
        self.assertIsNone(_source_age_hours({"last_refreshed_at": ""}))

    def test_FE11_zero_for_just_refreshed(self):
        ts = datetime.now(timezone.utc).isoformat()
        age = _source_age_hours({"last_refreshed_at": ts})
        self.assertIsNotNone(age)
        self.assertLess(age, 0.1)


class FE12SourceAgeHoursCorrect(unittest.TestCase):
    def test_FE12_two_hours_ago(self):
        ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        age = _source_age_hours({"last_refreshed_at": ts})
        self.assertIsNotNone(age)
        self.assertAlmostEqual(age, 2.0, delta=0.05)

    def test_FE12_z_suffix_handled(self):
        ts = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        age = _source_age_hours({"last_refreshed_at": ts})
        self.assertIsNotNone(age)
        self.assertAlmostEqual(age, 1.0, delta=0.05)


if __name__ == "__main__":
    unittest.main()
