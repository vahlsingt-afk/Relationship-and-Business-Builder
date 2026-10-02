"""
test_earnings_monitor_sprint_g.py — Sprint G: Earnings Intelligence Engine tests.

Tests:
  EG1 — build_earnings_intelligence is callable and returns a list
  EG2 — pre-earnings alert fires for a company within 30 days
  EG3 — pre-earnings alert is NOT fired for a company outside 30 days
  EG4 — alert window label is correct (TODAY/7 DAYS/30 DAYS)
  EG5 — pre-earnings alert has required canonical schema fields
  EG6 — _detect_earnings_dimensions identifies correct signal dimensions
  EG7 — _estimate_next_report_date wraps correctly across year boundary
  EG8 — build_earnings_intelligence handles empty/missing calendar silently
"""
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import earnings_monitor as em

# RB-DEFECT-066 backfill (2026-08-10): most tests in this file only patch
# _load_calendar, leaving em.OUTPUT_PATH pointed at the real repo's
# market_signals_earnings.jsonl. That file now has real earnings_release
# rows (the backfill correctly reclassified them), so an unpatched
# build_earnings_intelligence() call would read real production data, hit
# real network fetches via record_earnings_history's exhibit lookup, and
# write to the real system/earnings_history/earnings_calls.jsonl -- slow,
# flaky, and it pollutes production state. Isolate both paths for the whole
# module; individual tests (e.g. EG8a) can still override within their own
# `with patch.object(...)` block.
_module_tmpdir = tempfile.TemporaryDirectory()
_output_patcher = patch.object(em, "OUTPUT_PATH", Path(_module_tmpdir.name) / "market_signals_earnings.jsonl")
_history_patcher = patch.object(em, "EARNINGS_HISTORY_PATH", Path(_module_tmpdir.name) / "earnings_calls.jsonl")


def setUpModule():
    _output_patcher.start()
    _history_patcher.start()


def tearDownModule():
    _output_patcher.stop()
    _history_patcher.stop()
    _module_tmpdir.cleanup()


def _company(name: str, ticker: str, report_months: list[int],
             relevance: str = "high", watch_priority: bool = True) -> dict:
    return {
        "name": name,
        "ticker": ticker,
        "report_months": report_months,
        "strategic_relevance": relevance,
        "watch_priority": watch_priority,
        "side": "vendor_supply",
        "category": "pos",
        "notes": f"Test company {name}",
        "edgar_cik": None,
        "ir_rss_url": None,
    }


class EG1Callable(unittest.TestCase):
    def test_EG1_build_earnings_intelligence_callable(self):
        self.assertTrue(callable(em.build_earnings_intelligence))

    def test_EG1_returns_list(self):
        with patch.object(em, "_load_calendar", return_value=[]):
            result = em.build_earnings_intelligence(today=date.today())
        self.assertIsInstance(result, list)


class EG2PreEarningsAlertFires(unittest.TestCase):
    def test_EG2a_alert_fires_within_30_days(self):
        """A company reporting in 20 days should generate a pre-earnings alert."""
        future = date.today() + timedelta(days=20)
        company = _company("TestCo", "TST", [future.month])
        with patch.object(em, "_load_calendar", return_value=[company]):
            items = em.build_earnings_intelligence(today=date.today())
        pre_alerts = [i for i in items if i["extras"]["earnings_type"] == "pre_earnings_alert"]
        self.assertGreater(len(pre_alerts), 0)

    def test_EG2b_alert_contains_company_name(self):
        future = date.today() + timedelta(days=10)
        company = _company("AlphaVendor", "ALVN", [future.month])
        with patch.object(em, "_load_calendar", return_value=[company]):
            items = em.build_earnings_intelligence(today=date.today())
        pre_alerts = [i for i in items if i["extras"]["earnings_type"] == "pre_earnings_alert"]
        self.assertGreater(len(pre_alerts), 0)
        titles = [i["title"] for i in pre_alerts]
        self.assertTrue(any("AlphaVendor" in t for t in titles))

    def test_EG2c_extras_contain_required_fields(self):
        future = date.today() + timedelta(days=5)
        company = _company("BetaCo", "BETA", [future.month])
        with patch.object(em, "_load_calendar", return_value=[company]):
            items = em.build_earnings_intelligence(today=date.today())
        pre_alerts = [i for i in items if i["extras"]["earnings_type"] == "pre_earnings_alert"]
        self.assertGreater(len(pre_alerts), 0)
        alert = pre_alerts[0]
        for field in ("earnings_type", "company", "ticker", "days_until_report",
                      "estimated_report_date", "alert_window", "strategic_relevance",
                      "watch_dimensions"):
            self.assertIn(field, alert["extras"], f"Missing extras field: {field}")

    def test_EG2d_only_watch_priority_companies_alerted(self):
        """Companies with watch_priority=False should not generate alerts."""
        future = date.today() + timedelta(days=5)
        no_watch = _company("IgnoredCo", "IGN", [future.month], watch_priority=False)
        with patch.object(em, "_load_calendar", return_value=[no_watch]):
            items = em.build_earnings_intelligence(today=date.today())
        pre_alerts = [i for i in items if i["extras"]["earnings_type"] == "pre_earnings_alert"]
        self.assertEqual(len(pre_alerts), 0)


class EG3NoAlertOutside30Days(unittest.TestCase):
    def test_EG3a_horizon_watch_alert_within_90_days(self):
        """Company reporting in ~66 days → "90 DAYS" horizon-watch alert.

        RB 9.82 — RB-DEFECT-044 "Horizon Watch (30-90 Days)": _ALERT_WINDOWS
        was widened to include a 90-day "monitor" tier so reports 31-90 days
        out surface as horizon items instead of being dropped entirely.
        """
        today = date(2026, 6, 3)
        # report_months: pick a month ~66 days out (August = month 8)
        # from June 3, August 8 is ~66 days → within the new 90-day window
        company = _company("FarFutureCo", "FFC", [8])
        alerts = em._build_pre_earnings_alerts([company], today)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["extras"]["alert_window"], "90 DAYS")
        self.assertEqual(alerts[0]["disposition"], "monitor")

    def test_EG3a2_no_alert_beyond_90_days(self):
        """Company reporting in ~158 days → no alert (outside all windows)."""
        today = date(2026, 6, 3)
        # report_months: November (month 11) from June 3 is ~158 days out
        company = _company("VeryFarFutureCo", "VFF", [11])
        alerts = em._build_pre_earnings_alerts([company], today)
        self.assertEqual(len(alerts), 0)

    def test_EG3b_no_alert_when_no_report_months(self):
        company = _company("UnknownCo", "UNK", [])
        today = date.today()
        alerts = em._build_pre_earnings_alerts([company], today)
        self.assertEqual(len(alerts), 0)


class EG4AlertWindowLabel(unittest.TestCase):
    """Use a fixed reference date (June 3) so test dates are deterministic.

    _estimate_next_report_date always returns the 8th of the report month.
    So to get a specific day offset we use a reference today that puts the
    8th at the desired number of days away.
    """

    def test_EG4a_today_window_label(self):
        # report_months=[3], ref_today = March 8 → 0 days out → TODAY
        ref_today = date(2026, 3, 8)
        company = _company("TodayCo", "TDY", [3])
        alerts = em._build_pre_earnings_alerts([company], ref_today)
        self.assertGreater(len(alerts), 0)
        self.assertEqual(alerts[0]["extras"]["alert_window"], "TODAY")
        self.assertEqual(alerts[0]["extras"]["days_until_report"], 0)

    def test_EG4b_seven_day_window_label(self):
        # report_months=[6], ref_today = June 3 → June 8 = 5 days away → 7 DAYS
        ref_today = date(2026, 6, 3)
        company = _company("SoonCo", "SON", [6])
        alerts = em._build_pre_earnings_alerts([company], ref_today)
        self.assertGreater(len(alerts), 0)
        self.assertEqual(alerts[0]["extras"]["alert_window"], "7 DAYS")
        self.assertEqual(alerts[0]["disposition"], "act_today")

    def test_EG4c_thirty_day_window_is_monitor(self):
        """25 days out → 30-day window → monitor disposition."""
        # report_months=[7], ref_today = June 13 → July 8 = 25 days away → 30 DAYS
        ref_today = date(2026, 6, 13)
        company = _company("SoonCo2", "SON2", [7])
        alerts = em._build_pre_earnings_alerts([company], ref_today)
        self.assertGreater(len(alerts), 0)
        self.assertEqual(alerts[0]["extras"]["alert_window"], "30 DAYS")
        self.assertEqual(alerts[0]["disposition"], "monitor")


class EG5CanonicalSchema(unittest.TestCase):
    REQUIRED = {"title", "summary", "why_it_matters", "recommended_action",
                "disposition", "grounding", "freshness", "confidence",
                "source_refs", "extras", "novelty"}

    def test_EG5_pre_earnings_item_has_canonical_fields(self):
        future = date.today() + timedelta(days=5)
        company = _company("SchemaCo", "SCH", [future.month])
        alerts = em._build_pre_earnings_alerts([company], date.today())
        self.assertGreater(len(alerts), 0)
        for field in self.REQUIRED:
            self.assertIn(field, alerts[0], f"Missing canonical field: {field}")

    def test_EG5_disposition_is_valid(self):
        future = date.today() + timedelta(days=5)
        company = _company("DispCo", "DSP", [future.month])
        alerts = em._build_pre_earnings_alerts([company], date.today())
        if alerts:
            self.assertIn(alerts[0]["disposition"], ("act_today", "monitor"))

    def test_EG5_title_has_pre_earnings_tag(self):
        future = date.today() + timedelta(days=5)
        company = _company("TagCo", "TAG", [future.month])
        alerts = em._build_pre_earnings_alerts([company], date.today())
        if alerts:
            self.assertIn("[PRE-EARNINGS]", alerts[0]["title"])


class EG6DimensionDetection(unittest.TestCase):
    def test_EG6a_financial_dimension(self):
        text = "Toast reports Q2 revenue of $1.2B, raising full-year guidance"
        dims = em._detect_earnings_dimensions(text)
        self.assertIn("Financial", dims)

    def test_EG6b_customer_dimension(self):
        text = "Same-store sales up 3%, traffic improved with 4M active customers"
        dims = em._detect_earnings_dimensions(text)
        self.assertIn("Customer", dims)

    def test_EG6c_technology_dimension(self):
        text = "PAR expands AI capabilities and point of sale integrations"
        dims = em._detect_earnings_dimensions(text)
        self.assertIn("Technology", dims)

    def test_EG6d_operational_dimension(self):
        text = "Labor costs increased 8%, new unit openings of 200 locations"
        dims = em._detect_earnings_dimensions(text)
        self.assertIn("Operational", dims)

    def test_EG6e_franchisee_dimension(self):
        text = "Franchisee same-store franchise royalty collections strong"
        dims = em._detect_earnings_dimensions(text)
        self.assertIn("Franchisee", dims)

    def test_EG6f_multiple_dimensions(self):
        text = "Strong revenue growth; same-store sales up; AI investment expanding; labor pressure"
        dims = em._detect_earnings_dimensions(text)
        self.assertGreater(len(dims), 1)

    def test_EG6g_empty_text_no_crash(self):
        dims = em._detect_earnings_dimensions("")
        self.assertIsInstance(dims, list)


class EG7DateEstimation(unittest.TestCase):
    def test_EG7a_next_month_in_current_year(self):
        today = date(2026, 6, 3)
        company = _company("YearCo", "YRC", [8])  # August
        result = em._estimate_next_report_date(company, today)
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.month, 8)

    def test_EG7b_wraps_to_next_year(self):
        today = date(2026, 12, 1)
        company = _company("WrapCo", "WRP", [2])  # February — past Dec 1 in current year
        result = em._estimate_next_report_date(company, today)
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2027)
        self.assertEqual(result.month, 2)

    def test_EG7c_same_month_but_future_day(self):
        today = date(2026, 8, 1)
        company = _company("SameCo", "SAM", [8])  # August — estimate is Aug 8
        result = em._estimate_next_report_date(company, today)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result, today)

    def test_EG7d_no_report_months_returns_none(self):
        today = date.today()
        company = _company("NoCo", "NOC", [])
        result = em._estimate_next_report_date(company, today)
        self.assertIsNone(result)


class EG8EmptyCalendarSilent(unittest.TestCase):
    def test_EG8a_empty_calendar_no_crash(self):
        # RB-DEFECT-066 backfill (2026-08-10): _build_post_earnings_signals
        # reads OUTPUT_PATH directly, independent of _load_calendar -- this
        # test only patched the calendar, so it was implicitly relying on
        # the real repo's market_signals_earnings.jsonl having zero recent
        # earnings_release rows to get an empty result. That's no longer
        # true (the backfill correctly reclassified real recent earnings
        # releases), so isolate OUTPUT_PATH too rather than depending on
        # live repo data state.
        with patch.object(em, "_load_calendar", return_value=[]), \
             patch.object(em, "OUTPUT_PATH", Path("/nonexistent/market_signals_earnings.jsonl")):
            result = em.build_earnings_intelligence(today=date.today())
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)

    def test_EG8b_missing_calendar_no_crash(self):
        with patch.object(em, "_load_calendar", side_effect=Exception("no file")):
            result = em.build_earnings_intelligence(today=date.today())
        self.assertIsInstance(result, list)

    def test_EG8c_caps_at_five_pre_alerts(self):
        """Even with many eligible companies, alert output is capped at 5."""
        future = date.today() + timedelta(days=5)
        companies = [_company(f"Co{i}", f"C{i:02d}", [future.month]) for i in range(10)]
        alerts = em._build_pre_earnings_alerts(companies, date.today())
        self.assertLessEqual(len(alerts), 5)


if __name__ == "__main__":
    unittest.main()
