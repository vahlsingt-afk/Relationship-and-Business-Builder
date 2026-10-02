"""
test_defect_066_par_earnings.py — RB-DEFECT-066 regression coverage.

PAR Technology released Q2 2026 results and held its earnings call on
2026-08-06. The Aug 7 morning intelligence process missed it entirely: the
daily brief kept a stale "reports this week (~Aug 8)" reminder while the
intelligence brief simultaneously (and falsely) claimed "No earnings...
events." See defects/RB-DEFECT-066_par-earnings-call-missed-by-morning-
intelligence_2026-08-07.md for the full writeup.

Failure chain covered here, one test class per link:

  1. TestConfirmedDateSupersedesEstimate — earnings_calendar.yaml retained
     an estimated date instead of reconciling to the confirmed one.
  2. TestMorningPipelineRaceCondition — the brief rendered before the
     earnings monitor completed.
  3. TestIrRssFallback — PAR's IR RSS endpoint failed with no fallback.
  4. TestGenericEightKClassification — SEC's generic 8-K title alone can't
     be classified as an earnings release.
  5. TestPreEarningsAlertSuppressedByPostEarningsSignal — the renderer must
     replace the pre-earnings reminder with a post-earnings review item.
  6. TestWatchlistWiring — a confirmed post-earnings signal must reach
     Section E via watchlist_intelligence, which it never did before.
  7. TestNoEventsClaimGatedOnScanCompleteness — the renderer must not assert
     zero events when the earnings scan didn't complete before cutoff.
  8. TestParAugust6Fixture — end-to-end fixture modeled on PAR's actual
     August 6 filings: build_earnings_intelligence must produce a Q2
     earnings item and no stale pre-earnings reminder for PAR.
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

import earnings_monitor as em  # noqa: E402
import daily_brief as db  # noqa: E402
import render_intelligence_brief as rib  # noqa: E402


def _par_company(**overrides) -> dict:
    company = {
        "name": "PAR Technology",
        "ticker": "PAR",
        "edgar_cik": "708821",
        "ir_rss_url": "https://investors.partech.com/rss/news-releases.xml",
        "ir_page_url": "https://investors.partech.com/",
        "side": "vendor_supply",
        "category": "pos",
        "report_months": [2, 5, 8, 11],
        "strategic_relevance": "high",
        "watch_priority": True,
        "notes": "Test entry",
    }
    company.update(overrides)
    return company


class TestConfirmedDateSupersedesEstimate(unittest.TestCase):
    """AC: confirmed dates supersede estimated dates."""

    def test_estimate_skips_month_already_reported(self):
        today = date(2026, 8, 7)
        company = _par_company(last_reported_date="2026-08-06")
        next_date = em._estimate_next_report_date(company, today)
        # August is no longer a candidate -- next real month is November.
        self.assertIsNotNone(next_date)
        self.assertNotEqual(next_date.month, 8)
        self.assertEqual(next_date.month, 11)

    def test_estimate_without_last_reported_date_uses_current_month(self):
        """Sanity check: without the fix's field, old behavior is preserved."""
        today = date(2026, 8, 7)
        company = _par_company()
        company.pop("last_reported_date", None)
        next_date = em._estimate_next_report_date(company, today)
        self.assertEqual(next_date, date(2026, 8, 8))

    def test_reconcile_confirmed_dates_writes_calendar(self):
        # RB-2026-08-28: was a hardcoded "2026-08-06" -- _reconcile_confirmed_dates
        # (and get_recent_signals underneath it) filters by a real
        # datetime.now(timezone.utc) lookback window (default 14 days), so a
        # fixed date silently aged out and made this test fail for reasons
        # that had nothing to do with the code under test. Relative to
        # today so it can never drift stale again.
        report_date = date.today().isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            snap_dir = tmp_dir / "_snapshots"
            snap_dir.mkdir()
            calendar_path = tmp_dir / "earnings_calendar.yaml"
            output_path = tmp_dir / "market_signals_earnings.jsonl"
            row = {
                "company": "PAR Technology",
                "signal_type": "earnings_release",
                "published_at": report_date,
                "_source_hash": "abc123",
            }
            output_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

            companies = [_par_company()]
            with patch.object(em, "EARNINGS_CALENDAR_PATH", calendar_path), \
                 patch.object(em, "SNAPSHOTS_DIR", snap_dir), \
                 patch.object(em, "OUTPUT_PATH", output_path):
                updated = em._reconcile_confirmed_dates(companies)

            self.assertIn("PAR Technology", updated)
            self.assertEqual(companies[0]["last_reported_date"], report_date)
            self.assertTrue(calendar_path.exists())

    def test_reconcile_confirmed_dates_noop_when_nothing_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            output_path = tmp_dir / "market_signals_earnings.jsonl"
            output_path.write_text("", encoding="utf-8")
            companies = [_par_company()]
            with patch.object(em, "OUTPUT_PATH", output_path):
                updated = em._reconcile_confirmed_dates(companies)
            self.assertEqual(updated, [])


class TestMorningPipelineRaceCondition(unittest.TestCase):
    """AC: the morning pipeline enforces earnings-monitor completion before
    brief render. brief_only mode's freshness gate previously only checked
    intelligence_assessment.json, not earnings_monitor's own health cache --
    a 4 AM scan still mid-run at 5 AM left intelligence_assessment fresh
    while earnings_monitor hadn't run at all."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "system" / "scripts"))
        import morning_pipeline as mp
        self.mp = mp

    def test_fresh_health_cache_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            cache_dir = tmp_dir / ".cache"
            cache_dir.mkdir()
            health_path = cache_dir / "earnings_monitor_health.json"
            today = date(2026, 8, 7)
            health_path.write_text(json.dumps({
                "generated_at": "2026-08-07T05:43:00+00:00",
            }), encoding="utf-8")
            with patch.object(self.mp, "SYSTEM_DIR", tmp_dir):
                self.assertTrue(self.mp._earnings_monitor_fresh(today))

    def test_stale_health_cache_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            cache_dir = tmp_dir / ".cache"
            cache_dir.mkdir()
            health_path = cache_dir / "earnings_monitor_health.json"
            today = date(2026, 8, 7)
            health_path.write_text(json.dumps({
                "generated_at": "2026-08-06T05:43:00+00:00",
            }), encoding="utf-8")
            with patch.object(self.mp, "SYSTEM_DIR", tmp_dir):
                self.assertFalse(self.mp._earnings_monitor_fresh(today))

    def test_missing_health_cache_is_not_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            today = date(2026, 8, 7)
            with patch.object(self.mp, "SYSTEM_DIR", tmp_dir):
                self.assertFalse(self.mp._earnings_monitor_fresh(today))


class TestIrRssFallback(unittest.TestCase):
    """AC: a failed IR RSS feed triggers a recorded fallback attempt and a
    visible health warning if all primary-source fallbacks fail."""

    def test_ir_page_fallback_detects_earnings_language(self):
        html = (
            "<html><body><h1>Newsroom</h1>"
            "<p>PAR Technology Reports Second Quarter 2026 Results with "
            "revenue growth across its restaurant technology platform.</p>"
            "</body></html>"
        )

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return html.encode("utf-8")

        with patch("urllib.request.urlopen", return_value=_FakeResp()):
            rows = em._fetch_ir_page_fallback(_par_company(), "https://investors.partech.com/", 14)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["signal_type"], "earnings_release")
        self.assertEqual(rows[0]["source_type"], "public_company_primary_fallback")

    def test_ir_page_fallback_no_match_returns_empty(self):
        html = "<html><body><p>Investor relations contact information.</p></body></html>"

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return html.encode("utf-8")

        with patch("urllib.request.urlopen", return_value=_FakeResp()):
            rows = em._fetch_ir_page_fallback(_par_company(), "https://investors.partech.com/", 14)
        self.assertEqual(rows, [])

    def test_ir_page_fallback_fetch_error_returns_empty(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            rows = em._fetch_ir_page_fallback(_par_company(), "https://investors.partech.com/", 14)
        self.assertEqual(rows, [])

    def test_health_flags_unresolved_ir_failure(self):
        health = em._Health()
        health.record("PAR Technology", "ir_rss", "failed", error="Could not fetch feed")
        health.record("PAR Technology", "ir_page_fallback", "failed", error="No match")
        d = health.to_dict()
        self.assertIn("PAR Technology", d["ir_rss_unresolved_companies"])

    def test_health_does_not_flag_resolved_fallback(self):
        health = em._Health()
        health.record("PAR Technology", "ir_rss", "failed", error="Could not fetch feed")
        health.record("PAR Technology", "ir_page_fallback", "ok", row_count=1)
        d = health.to_dict()
        self.assertNotIn("PAR Technology", d["ir_rss_unresolved_companies"])


class TestGenericEightKClassification(unittest.TestCase):
    """AC: SEC 8-K/10-Q entries for earnings are correctly classified using
    filing items/exhibits, not the generic Atom title alone."""

    def test_classify_via_index_detects_item_202(self):
        html = "<html><body>Item 2.02 Results of Operations and Financial Condition</body></html>"

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return html.encode("utf-8")

        with patch("urllib.request.urlopen", return_value=_FakeResp()):
            self.assertTrue(em._classify_8k_via_index("https://www.sec.gov/fake-index.htm"))

    def test_classify_via_index_ex99_alone_is_not_sufficient(self):
        """RB-DEFECT-2026-09-30: an EX-99.1 exhibit alone used to be treated
        as sufficient on its own -- confirmed live false positive when
        McDonald's filed an Item 7.01 (Reg FD Disclosure) 8-K with an
        EX-99.1 investor-conference exhibit and no Item 2.02, which got
        misclassified as an earnings release and corrupted
        last_reported_date. Item 2.02 is now required."""
        html = "<html><body><table><tr><td>EX-99.1</td><td>Press Release</td></tr></table></body></html>"

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return html.encode("utf-8")

        with patch("urllib.request.urlopen", return_value=_FakeResp()):
            self.assertFalse(em._classify_8k_via_index("https://www.sec.gov/fake-index.htm"))

    def test_classify_via_index_item_701_with_ex99_is_not_earnings(self):
        """The exact McDonald's 2026-09-23 shape: Item 7.01 + Item 9.01 +
        an EX-99.1 exhibit, no Item 2.02 -- must not classify as earnings."""
        html = ("<html><body>Item 7.01: Regulation FD Disclosure<br>"
                "Item 9.01: Financial Statements and Exhibits<br>"
                "<table><tr><td>EX-99.1</td></tr></table></body></html>")

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return html.encode("utf-8")

        with patch("urllib.request.urlopen", return_value=_FakeResp()):
            self.assertFalse(em._classify_8k_via_index("https://www.sec.gov/fake-index.htm"))

    def test_classify_via_index_false_for_unrelated_8k(self):
        html = "<html><body>Item 5.02 Departure of Directors</body></html>"

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return html.encode("utf-8")

        with patch("urllib.request.urlopen", return_value=_FakeResp()):
            self.assertFalse(em._classify_8k_via_index("https://www.sec.gov/fake-index.htm"))

    def test_classify_via_index_returns_false_on_fetch_failure(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            self.assertFalse(em._classify_8k_via_index("https://www.sec.gov/fake-index.htm"))

    def test_parse_edgar_feed_reclassifies_generic_8k(self):
        import xml.etree.ElementTree as ET
        xml_str = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>8-K - Current report</title>
    <link href="https://www.sec.gov/Archives/edgar/data/708821/000070882126000123-index.htm"/>
    <updated>2026-08-06T20:30:00Z</updated>
    <summary>Item 2.02, Item 9.01</summary>
  </entry>
</feed>
"""
        root = ET.fromstring(xml_str)
        with patch.object(em, "_classify_8k_via_index", return_value=True):
            rows = em._parse_edgar_feed(root, _par_company(), 90, source_type="sec_edgar_8k")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["signal_type"], "earnings_release")

    def test_parse_edgar_feed_stays_material_event_when_index_check_fails(self):
        import xml.etree.ElementTree as ET
        xml_str = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>8-K - Current report</title>
    <link href="https://www.sec.gov/Archives/edgar/data/708821/000070882126000123-index.htm"/>
    <updated>2026-08-06T20:30:00Z</updated>
    <summary></summary>
  </entry>
</feed>
"""
        root = ET.fromstring(xml_str)
        with patch.object(em, "_classify_8k_via_index", return_value=False):
            rows = em._parse_edgar_feed(root, _par_company(), 90, source_type="sec_edgar_8k")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["signal_type"], "material_event")


class TestPreEarningsAlertSuppressedByPostEarningsSignal(unittest.TestCase):
    """AC: replace the pre-earnings reminder with a post-earnings review item."""

    def test_exclude_param_drops_company_from_pre_alerts(self):
        today = date(2026, 8, 7)
        companies = [_par_company()]
        items_no_exclude = em._build_pre_earnings_alerts(companies, today)
        items_excluded = em._build_pre_earnings_alerts(companies, today, exclude=frozenset({"PAR Technology"}))
        self.assertTrue(any(
            (i.get("extras") or {}).get("company") == "PAR Technology" for i in items_no_exclude
        ))
        self.assertFalse(any(
            (i.get("extras") or {}).get("company") == "PAR Technology" for i in items_excluded
        ))

    def test_build_earnings_intelligence_drops_stale_pre_alert(self):
        today = date(2026, 8, 7)
        post_signal = em._canonical_earnings_item(
            title="[POST-EARNINGS] PAR Technology: Reports Second Quarter 2026 Results",
            summary="PAR reported Q2 2026 results.",
            why_it_matters="Earnings release.",
            recommended_action="Review.",
            extras={"earnings_type": "post_earnings_signal", "company": "PAR Technology"},
        )
        with patch.object(em, "_load_calendar", return_value=[_par_company()]), \
             patch.object(em, "_build_post_earnings_signals", return_value=[post_signal]):
            items = em.build_earnings_intelligence(today=today)

        pre_alerts_par = [
            i for i in items
            if (i.get("extras") or {}).get("earnings_type") == "pre_earnings_alert"
            and (i.get("extras") or {}).get("company") == "PAR Technology"
        ]
        post_par = [
            i for i in items
            if (i.get("extras") or {}).get("earnings_type") == "post_earnings_signal"
            and (i.get("extras") or {}).get("company") == "PAR Technology"
        ]
        self.assertEqual(pre_alerts_par, [], "stale pre-earnings reminder must be dropped")
        self.assertEqual(len(post_par), 1, "post-earnings review item must replace it")


class TestWatchlistWiring(unittest.TestCase):
    """AC: surface the event in E: Earnings & Corporate, update the
    watchlist state. A confirmed post-earnings signal was never wired into
    _compute_watchlist_intelligence's status computation, so it could never
    reach Section E even when correctly detected."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_post_earnings_signal_escalates_par_watchlist_status(self):
        report = {"relationship_signals": {"signals": []}}
        sections = {
            "earnings_intelligence": [{
                "title": "[POST-EARNINGS] PAR Technology: Reports Second Quarter 2026 Results",
                "summary": "PAR Technology earnings release (2026-08-06).",
                "why_it_matters": "Authoritative executive-level signal.",
                "extras": {
                    "earnings_type": "post_earnings_signal",
                    "company": "PAR Technology",
                    "source_url": "https://investors.partech.com/press-releases/2026/q2-results",
                },
            }],
        }
        with patch.object(db.core, "CACHE_DIR", self.cache_dir):
            items = db._compute_watchlist_intelligence(report, sections)

        par_items = [i for i in items if (i.get("extras") or {}).get("entity_name") == "PAR Technology"]
        self.assertTrue(par_items, "PAR Technology must appear individually, not rolled up into No Change")
        par_item = par_items[0]
        self.assertEqual((par_item.get("extras") or {}).get("watchlist_status"), "Escalation")
        self.assertIn("EARNINGS", (par_item.get("title") or "").upper())
        self.assertEqual(par_item.get("disposition"), "act_today")

    def test_section_e_renders_par_earnings_from_watchlist(self):
        report = {"relationship_signals": {"signals": []}}
        sections = {
            "earnings_intelligence": [{
                "title": "[POST-EARNINGS] PAR Technology: Reports Second Quarter 2026 Results",
                "summary": "PAR Technology earnings release (2026-08-06).",
                "why_it_matters": "Authoritative executive-level signal.",
                "extras": {
                    "earnings_type": "post_earnings_signal",
                    "company": "PAR Technology",
                    "source_url": "https://investors.partech.com/press-releases/2026/q2-results",
                },
            }],
        }
        with patch.object(db.core, "CACHE_DIR", self.cache_dir):
            sections["watchlist_intelligence"] = db._compute_watchlist_intelligence(report, sections)

        out = rib._render_earnings(sections, date(2026, 8, 7))
        self.assertIn("PAR Technology", out)
        self.assertNotIn("No earnings reports or material corporate events this cycle.", out)


class TestNoEventsClaimGatedOnScanCompleteness(unittest.TestCase):
    """AC: the renderer cannot emit "No earnings...events" unless the
    required earnings scan completed successfully before the brief's data
    cutoff."""

    def test_incomplete_scan_blocks_no_events_claim(self):
        sections = {
            "watchlist_intelligence": [],
            "earnings_intelligence": [{
                "title": "[SCAN-HEALTH] Earnings monitor status",
                "extras": {"earnings_type": "scan_health", "scan_complete": False,
                           "generated_at": None, "unresolved_ir_failures": []},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 8, 7))
        self.assertNotIn("No earnings reports or material corporate events this cycle.", out)
        self.assertIn("incomplete", out)

    def test_complete_scan_allows_no_events_claim(self):
        sections = {
            "watchlist_intelligence": [],
            "earnings_intelligence": [{
                "title": "[SCAN-HEALTH] Earnings monitor status",
                "extras": {"earnings_type": "scan_health", "scan_complete": True,
                           "generated_at": "2026-08-07T05:43:00+00:00", "unresolved_ir_failures": []},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 8, 7))
        self.assertIn("No earnings reports or material corporate events this cycle.", out)

    def test_missing_scan_health_falls_back_to_standard_no_events_text(self):
        """Sections without a scan_health item (e.g. older cached briefs, or
        earnings_monitor import unavailable) must still render normally."""
        sections = {"watchlist_intelligence": []}
        out = rib._render_earnings(sections, date(2026, 8, 7))
        self.assertIn("No earnings reports or material corporate events this cycle.", out)

    def test_unresolved_ir_failures_produce_provisional_message(self):
        sections = {
            "watchlist_intelligence": [],
            "earnings_intelligence": [{
                "title": "[SCAN-HEALTH] Earnings monitor status",
                "extras": {"earnings_type": "scan_health", "scan_complete": True,
                           "generated_at": "2026-08-07T05:43:00+00:00",
                           "unresolved_ir_failures": ["PAR Technology"]},
            }],
        }
        out = rib._render_earnings(sections, date(2026, 8, 7))
        self.assertNotIn("No earnings reports or material corporate events this cycle.", out)
        self.assertIn("PAR Technology", out)

    def test_scan_health_summary_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            health_path = Path(tmp) / "health.json"
            health_path.write_text(json.dumps({
                "generated_at": "2026-08-07T05:43:00+00:00",
                "ir_rss_unresolved_companies": [],
                "failed_count": 0,
            }), encoding="utf-8")
            with patch.object(em, "HEALTH_PATH", health_path):
                summary = em.scan_health_summary(today=date(2026, 8, 7))
        self.assertTrue(summary["scan_complete"])

    def test_scan_health_summary_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            health_path = Path(tmp) / "health.json"
            health_path.write_text(json.dumps({
                "generated_at": "2026-08-06T05:43:00+00:00",
            }), encoding="utf-8")
            with patch.object(em, "HEALTH_PATH", health_path):
                summary = em.scan_health_summary(today=date(2026, 8, 7))
        self.assertFalse(summary["scan_complete"])

    def test_scan_health_summary_missing_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            health_path = Path(tmp) / "does_not_exist.json"
            with patch.object(em, "HEALTH_PATH", health_path):
                summary = em.scan_health_summary(today=date(2026, 8, 7))
        self.assertFalse(summary["scan_complete"])


class TestParAugust6Fixture(unittest.TestCase):
    """End-to-end regression fixture modeled on PAR's actual August 6
    filings: a generically-titled SEC 8-K (classified via index-page
    fallback) and a confirmed report date. Verifies build_earnings_
    intelligence produces a Q2 earnings item for PAR and does not describe
    the event as merely upcoming."""

    def test_par_post_earnings_item_present_no_stale_pre_alert(self):
        # RB-2026-08-28: today and the row's published_at/_ingested_at were
        # hardcoded to 2026-08-06/07. _build_post_earnings_signals() (which
        # build_earnings_intelligence() calls internally) filters by a real
        # datetime.now(timezone.utc) lookback window -- it does not honor
        # the injected `today` param at all -- so as real time passed, the
        # row silently aged past the lookback and post_items came back
        # empty for reasons unrelated to the code under test. Relative to
        # today so this can never drift stale again.
        today = date.today()
        row = {
            "title": "PAR Technology Reports Second Quarter 2026 Financial Results",
            "url": "https://investors.partech.com/press-releases/2026/q2-results",
            "source_name": "PAR Technology IR",
            "source_type": "public_company_primary",
            "source_quality": "strong",
            "published_at": today.isoformat(),
            "company": "PAR Technology",
            "side": "vendor_supply",
            "category": "pos",
            "signal_type": "earnings_release",
            "pain_point_or_priority": "Quarterly earnings release.",
            "strategic_relevance": "high",
            "second_order_impact": "Revenue and margin trends for Q2 2026.",
            "_source_hash": "par-q2-2026",
            "_ingested_at": f"{today.isoformat()}T20:30:00+00:00",
            "_earnings_monitor": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "market_signals_earnings.jsonl"
            output_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            history_path = Path(tmp) / "earnings_calls.jsonl"

            company = _par_company()
            company.pop("last_reported_date", None)  # not yet reconciled at fixture time

            with patch.object(em, "_load_calendar", return_value=[company]), \
                 patch.object(em, "OUTPUT_PATH", output_path), \
                 patch.object(em, "EARNINGS_HISTORY_PATH", history_path):
                items = em.build_earnings_intelligence(today=today)

        post_items = [
            i for i in items
            if (i.get("extras") or {}).get("earnings_type") == "post_earnings_signal"
            and (i.get("extras") or {}).get("company") == "PAR Technology"
        ]
        pre_items = [
            i for i in items
            if (i.get("extras") or {}).get("earnings_type") == "pre_earnings_alert"
            and (i.get("extras") or {}).get("company") == "PAR Technology"
        ]
        self.assertEqual(len(post_items), 1, "PAR's Q2 earnings release must appear as a post-earnings item")
        self.assertIn("PAR Technology", post_items[0]["title"])
        self.assertIn("Second Quarter", post_items[0]["title"])
        self.assertEqual(pre_items, [], "must not describe an already-completed event as merely upcoming")


class TestReclassifyStaleMaterialEvents(unittest.TestCase):
    """Backfill coverage: investigating this defect found Starbucks
    (2026-07-29) and Domino's (2026-07-20) earnings 8-Ks already sitting in
    market_signals_earnings.jsonl misclassified as material_event -- ingested
    *before* _classify_8k_via_index existed, so they're permanently stuck
    with the old classification (append-only + dedup-by-hash means nothing
    ever reprocesses them). reclassify_stale_material_events() re-checks
    already-ingested rows and rewrites the ones that are actually earnings
    releases."""

    def _row(self, company, url, signal_type="material_event", source_type="sec_edgar_8k", **extra):
        row = {
            "title": "8-K  - Current report",
            "url": url,
            "source_name": f"SEC EDGAR ({company})",
            "source_type": source_type,
            "source_quality": "strong",
            "published_at": "2026-07-20",
            "company": company,
            "side": "vendor_supply",
            "category": "pos",
            "signal_type": signal_type,
            "pain_point_or_priority": "Material event filed with SEC.",
            "strategic_relevance": "high",
            "_source_hash": em._url_hash(url),
            "_ingested_at": "2026-08-01T09:34:39+00:00",
        }
        row.update(extra)
        return row

    def _fake_urlopen(self, matching_urls):
        html_match = "<html>Item 2.02: Results of Operations and Financial Condition. EX-99.1</html>"
        html_no_match = "<html>Item 5.02 Departure of Directors</html>"

        class _FakeResp:
            def __init__(self, html):
                self._html = html

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return self._html.encode("utf-8")

        def _opener(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else req
            return _FakeResp(html_match if url in matching_urls else html_no_match)

        return _opener

    def test_reclassifies_matching_rows_and_leaves_others_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            output_path = tmp_dir / "market_signals_earnings.jsonl"
            snap_dir = tmp_dir / "_snapshots"

            sbux_url = "https://www.sec.gov/Archives/edgar/data/829224/x/sbux-index.htm"
            unrelated_url = "https://www.sec.gov/Archives/edgar/data/1/x/unrelated-index.htm"
            ir_row_url = "https://investors.example.com/press-release"  # not sec_edgar_8k

            rows = [
                self._row("Starbucks Corp", sbux_url),
                self._row("Starbucks Corp", unrelated_url),
                self._row("Starbucks Corp", ir_row_url, source_type="public_company_primary"),
                self._row("PAR Technology", "https://www.sec.gov/x/par-already-earnings",
                          signal_type="earnings_release"),
            ]
            output_path.write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
            )

            with patch.object(em, "OUTPUT_PATH", output_path), \
                 patch.object(em, "SNAPSHOTS_DIR", snap_dir), \
                 patch.object(em, "_load_calendar", return_value=[]), \
                 patch("urllib.request.urlopen", side_effect=self._fake_urlopen({sbux_url})):
                result = em.reclassify_stale_material_events()

            self.assertEqual(result["reclassified_count"], 1)
            self.assertEqual(result["reclassified"][0]["company"], "Starbucks Corp")

            final_rows = [json.loads(l) for l in output_path.read_text().splitlines() if l.strip()]
            by_url = {r["url"]: r for r in final_rows}
            self.assertEqual(by_url[sbux_url]["signal_type"], "earnings_release")
            self.assertEqual(by_url[unrelated_url]["signal_type"], "material_event")
            self.assertEqual(by_url[ir_row_url]["signal_type"], "material_event")
            self.assertTrue(list(snap_dir.glob("market_signals_earnings.pre-reclassify-*.jsonl")))

    def test_scoped_to_company_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            output_path = tmp_dir / "market_signals_earnings.jsonl"
            snap_dir = tmp_dir / "_snapshots"

            sbux_url = "https://www.sec.gov/x/sbux-index.htm"
            dominos_url = "https://www.sec.gov/x/dominos-index.htm"
            rows = [
                self._row("Starbucks Corp", sbux_url),
                self._row("Domino's Pizza Inc", dominos_url),
            ]
            output_path.write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
            )

            with patch.object(em, "OUTPUT_PATH", output_path), \
                 patch.object(em, "SNAPSHOTS_DIR", snap_dir), \
                 patch.object(em, "_load_calendar", return_value=[]), \
                 patch("urllib.request.urlopen", side_effect=self._fake_urlopen({sbux_url, dominos_url})):
                result = em.reclassify_stale_material_events(companies=["Starbucks Corp"])

            self.assertEqual(result["reclassified_count"], 1)
            self.assertEqual(result["reclassified"][0]["company"], "Starbucks Corp")

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            output_path = tmp_dir / "market_signals_earnings.jsonl"
            snap_dir = tmp_dir / "_snapshots"
            url = "https://www.sec.gov/x/sbux-index.htm"
            output_path.write_text(json.dumps(self._row("Starbucks Corp", url)) + "\n", encoding="utf-8")
            original = output_path.read_text()

            with patch.object(em, "OUTPUT_PATH", output_path), \
                 patch.object(em, "SNAPSHOTS_DIR", snap_dir), \
                 patch("urllib.request.urlopen", side_effect=self._fake_urlopen({url})):
                result = em.reclassify_stale_material_events(dry_run=True)

            self.assertEqual(result["reclassified_count"], 1)
            self.assertEqual(output_path.read_text(), original)
            self.assertFalse(snap_dir.exists())

    def test_no_output_file_returns_empty_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does_not_exist.jsonl"
            with patch.object(em, "OUTPUT_PATH", missing):
                result = em.reclassify_stale_material_events()
            self.assertEqual(result["checked"], 0)
            self.assertEqual(result["reclassified"], [])


class _FakeHtmlResp:
    def __init__(self, html):
        self._html = html

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._html.encode("utf-8")


class TestEarningsHistory(unittest.TestCase):
    """2026-08-10 feature request: every earnings report should produce an
    executive summary in the daily brief, link to the full intelligence
    gathering, and be persisted to a durable historic record so the CoS can
    spot cross-quarter trends and storylines."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history_path = Path(self.tmp.name) / "earnings_calls.jsonl"
        self._patcher = patch.object(em, "EARNINGS_HISTORY_PATH", self.history_path)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.tmp.cleanup()

    def _row(self, company="Starbucks Corp", url="https://www.sec.gov/x/sbux-index.htm",
             published_at="2026-07-29"):
        return {
            "company": company,
            "url": url,
            "published_at": published_at,
            "title": "8-K  - Current report",
            "source_type": "sec_edgar_8k",
            "_source_hash": em._url_hash(url),
        }

    def test_record_earnings_history_appends_and_is_idempotent(self):
        row = self._row()
        with patch("urllib.request.urlopen", return_value=_FakeHtmlResp("<html>no exhibit here</html>")):
            rec1 = em.record_earnings_history(row, ["Financial"])
            rec2 = em.record_earnings_history(row, ["Financial"])
        self.assertEqual(rec1["_source_hash"], rec2["_source_hash"])
        self.assertEqual(rec1["recorded_at"], rec2["recorded_at"], "second call must not re-append/re-fetch")
        rows = [json.loads(l) for l in self.history_path.read_text().splitlines() if l.strip()]
        self.assertEqual(len(rows), 1)

    def test_get_company_earnings_history_filters_and_sorts(self):
        with patch("urllib.request.urlopen", return_value=_FakeHtmlResp("<html></html>")):
            em.record_earnings_history(self._row(company="Starbucks Corp", url="https://x/1", published_at="2026-05-20"), [])
            em.record_earnings_history(self._row(company="Starbucks Corp", url="https://x/2", published_at="2026-07-29"), [])
            em.record_earnings_history(self._row(company="Domino's Pizza Inc", url="https://x/3", published_at="2026-07-20"), [])

        history = em.get_company_earnings_history("Starbucks Corp")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["event_date"], "2026-07-29", "most recent first")
        self.assertTrue(all(r["company"] == "Starbucks Corp" for r in history))

    def test_get_company_earnings_history_respects_limit(self):
        with patch("urllib.request.urlopen", return_value=_FakeHtmlResp("<html></html>")):
            for i in range(5):
                em.record_earnings_history(
                    self._row(url=f"https://x/{i}", published_at=f"2026-0{i+1}-01"), [])
        self.assertEqual(len(em.get_company_earnings_history("Starbucks Corp", limit=2)), 2)

    def test_trend_note_first_event_has_no_baseline(self):
        # RB-DEFECT-2026-08-14 (see _build_earnings_trend_note's own
        # docstring): a first-quarter company deliberately gets an empty
        # note now, not "First earnings event on record..." -- that's a
        # statement about RB's own data coverage, not the company, and it
        # used to crowd out the one substantive thing (the real excerpt) in
        # a 200-char-truncated summary field. This test's name/intent
        # (first event has no trend baseline) is still valid; only the
        # expected representation of "no baseline" changed, from a
        # placeholder sentence to an empty string callers can drop.
        note = em._build_earnings_trend_note("Brand New Co", ["Financial"])
        self.assertEqual(note, "")

    def test_trend_note_detects_recurring_dimension(self):
        with patch("urllib.request.urlopen", return_value=_FakeHtmlResp("<html></html>")):
            em.record_earnings_history(self._row(url="https://x/1", published_at="2026-02-01"), ["Technology"])
            em.record_earnings_history(self._row(url="https://x/2", published_at="2026-05-01"), ["Technology"])
            em.record_earnings_history(self._row(url="https://x/3", published_at="2026-08-01"), ["Technology"])
        note = em._build_earnings_trend_note("Starbucks Corp", ["Technology"])
        self.assertIn("sustained theme", note)
        self.assertIn("3 of the last 3", note)

    def test_trend_note_flags_new_theme_when_not_recurring(self):
        with patch("urllib.request.urlopen", return_value=_FakeHtmlResp("<html></html>")):
            em.record_earnings_history(self._row(url="https://x/1", published_at="2026-05-01"), ["Financial"])
            em.record_earnings_history(self._row(url="https://x/2", published_at="2026-08-01"), ["Franchisee"])
        note = em._build_earnings_trend_note("Starbucks Corp", ["Franchisee"])
        self.assertIn("new theme", note)
        self.assertNotIn("sustained", note)

    def test_fetch_earnings_release_excerpt_extracts_exhibit_and_lead_text(self):
        index_html = (
            '<table><tr><td>EX-99.1</td>'
            '<td><a href="/Archives/edgar/data/1/x/pr.htm">pr.htm</a></td></tr></table>'
        )
        doc_html = (
            "<html><body>Contact info here. For Immediate Release "
            "Starbucks Reports Third Quarter 2026 Results. Revenue grew.</body></html>"
        )
        responses = iter([_FakeHtmlResp(index_html), _FakeHtmlResp(doc_html)])
        with patch("urllib.request.urlopen", side_effect=lambda *a, **kw: next(responses)):
            result = em._fetch_earnings_release_excerpt("https://www.sec.gov/x/sbux-index.htm")
        self.assertEqual(result["exhibit_url"], "https://www.sec.gov/Archives/edgar/data/1/x/pr.htm")
        self.assertIn("Starbucks Reports Third Quarter 2026 Results", result["excerpt"])
        self.assertNotIn("Contact info here", result["excerpt"])

    def test_fetch_earnings_release_excerpt_no_exhibit_found(self):
        with patch("urllib.request.urlopen", return_value=_FakeHtmlResp("<html>no exhibit table</html>")):
            result = em._fetch_earnings_release_excerpt("https://www.sec.gov/x/index.htm")
        self.assertEqual(result["excerpt"], "")
        self.assertIsNone(result["exhibit_url"])

    def test_fetch_earnings_release_excerpt_fetch_failure_returns_empty(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            result = em._fetch_earnings_release_excerpt("https://www.sec.gov/x/index.htm")
        self.assertEqual(result, {"excerpt": "", "exhibit_url": None})

    def test_post_earnings_signal_item_carries_history_pointer(self):
        """End-to-end: _build_post_earnings_signals must attach the
        history_api/history_count extras Section E's renderer depends on for
        the 'link to full intelligence gathering' callout."""
        row = self._row(company="Starbucks Corp", url="https://www.sec.gov/x/sbux-index.htm",
                         published_at=date.today().isoformat())
        with tempfile.TemporaryDirectory() as tmp2:
            output_path = Path(tmp2) / "market_signals_earnings.jsonl"
            full_row = {
                **row,
                "signal_type": "earnings_release",
                "second_order_impact": "",
                "pain_point_or_priority": "Quarterly earnings release.",
                "strategic_relevance": "high",
            }
            output_path.write_text(json.dumps(full_row) + "\n", encoding="utf-8")
            with patch.object(em, "OUTPUT_PATH", output_path), \
                 patch("urllib.request.urlopen", return_value=_FakeHtmlResp("<html>no exhibit</html>")):
                items = em._build_post_earnings_signals()

        self.assertEqual(len(items), 1)
        extras = items[0]["extras"]
        self.assertEqual(extras["history_api"], "getCompanyEarningsHistory")
        self.assertEqual(extras["history_count"], 1)
        # RB-DEFECT-2026-08-14: a first-quarter trend note is now "" (see
        # test_trend_note_first_event_has_no_baseline) -- it must not crowd
        # out the real summary content with a placeholder about RB's own
        # data coverage.
        self.assertNotIn("First earnings event", items[0]["summary"])
        self.assertIn("Starbucks Corp earnings release", items[0]["summary"])

    def test_dimensions_merge_with_excerpt_detected_dimensions(self):
        """RB-DEFECT-2026-08-29: the caller's `dimensions` param (computed
        from title+summary alone) used to be persisted verbatim, even when
        the excerpt fetched inside this same call contains real financial
        prose the caller never saw. Confirmed live: a real Starbucks Q3
        release with genuine comp-sales/EPS/revenue content in its excerpt
        was persisted with signal_dimensions: []. Fixed to merge in whatever
        the excerpt itself supports."""
        index_html = (
            '<table><tr><td>EX-99.1</td>'
            '<td><a href="/Archives/edgar/data/1/x/pr.htm">pr.htm</a></td></tr></table>'
        )
        doc_html = (
            "<html><body>For Immediate Release Starbucks reports Q3 results. "
            "Comparable store sales and revenue both grew this quarter, with "
            "franchise unit growth continuing.</body></html>"
        )
        responses = iter([_FakeHtmlResp(index_html), _FakeHtmlResp(doc_html)])
        with patch("urllib.request.urlopen", side_effect=lambda *a, **kw: next(responses)), \
             patch.object(em, "_fetch_fool_transcript_excerpt", return_value={"excerpt": "", "source_url": None}):
            rec = em.record_earnings_history(self._row(url="https://x/dims"), [])
        self.assertIn("Financial", rec["signal_dimensions"])
        self.assertIn("Customer", rec["signal_dimensions"])
        self.assertIn("Franchisee", rec["signal_dimensions"])

    def test_dimensions_from_caller_preserved_when_excerpt_empty(self):
        with patch("urllib.request.urlopen", return_value=_FakeHtmlResp("<html>no exhibit</html>")):
            rec = em.record_earnings_history(self._row(url="https://x/nodims"), ["Technology"])
        self.assertEqual(rec["signal_dimensions"], ["Technology"])


class TestMdaExcerpt(unittest.TestCase):
    """RB-2026-08-29: 10-Q/10-K MD&A section extraction -- the free,
    EDGAR-archived stand-in for a call transcript (real transcripts aren't
    filed with the SEC at all). Live-verified separately against a real
    McDonald's 10-Q; these tests cover the parsing logic in isolation."""

    def test_extracts_mda_section_skipping_toc_entry(self):
        # A realistic shape: "Item 2." appears once in the table of
        # contents (immediately followed by another Item heading, so no
        # real prose follows it) and again at the real section start.
        index_html = (
            '<table><tr><td scope="row">10-Q</td>'
            '<td scope="row"><a href="/ix?doc=/Archives/edgar/data/1/x/co-20260630.htm">'
            'co-20260630.htm</a></td>'
            '<td scope="row">10-Q</td></tr></table>'
        )
        doc_html = (
            "<html><body>"
            "Table of Contents Item 1. Financial Statements Item 2. Management's Discussion "
            "and Analysis of Financial Condition and Results of Operations Item 3. Quantitative "
            "Disclosures "
            "Item 2. Management's Discussion and Analysis of Financial Condition and Results of "
            "Operations Revenue grew this quarter driven by comparable sales strength. "
            "Item 3. Quantitative and Qualitative Disclosures About Market Risk Interest rate "
            "exposure is limited."
            "</body></html>"
        )
        responses = iter([_FakeHtmlResp(index_html), _FakeHtmlResp(doc_html)])
        with patch("urllib.request.urlopen", side_effect=lambda *a, **kw: next(responses)):
            result = em._fetch_mda_excerpt("https://www.sec.gov/x/co-index.htm")
        self.assertIn("Revenue grew this quarter", result["excerpt"])
        self.assertNotIn("Quantitative and Qualitative Disclosures", result["excerpt"])
        self.assertEqual(result["exhibit_url"], "https://www.sec.gov/Archives/edgar/data/1/x/co-20260630.htm")

    def test_no_primary_document_row_returns_empty(self):
        with patch("urllib.request.urlopen", return_value=_FakeHtmlResp("<html>no table here</html>")):
            result = em._fetch_mda_excerpt("https://www.sec.gov/x/co-index.htm")
        self.assertEqual(result, {"excerpt": "", "exhibit_url": None})

    def test_no_mda_heading_in_document_returns_empty_excerpt_but_keeps_url(self):
        index_html = (
            '<table><tr><td scope="row">10-K</td>'
            '<td scope="row"><a href="/Archives/edgar/data/1/x/co-20260630.htm">'
            'co-20260630.htm</a></td>'
            '<td scope="row">10-K</td></tr></table>'
        )
        responses = iter([_FakeHtmlResp(index_html), _FakeHtmlResp("<html><body>Cover page only.</body></html>")])
        with patch("urllib.request.urlopen", side_effect=lambda *a, **kw: next(responses)):
            result = em._fetch_mda_excerpt("https://www.sec.gov/x/co-index.htm")
        self.assertEqual(result["excerpt"], "")
        self.assertIsNotNone(result["exhibit_url"])

    def test_fetch_failure_returns_empty(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            result = em._fetch_mda_excerpt("https://www.sec.gov/x/co-index.htm")
        self.assertEqual(result, {"excerpt": "", "exhibit_url": None})

    def test_record_earnings_history_routes_10q_to_mda_fetch(self):
        index_html = (
            '<table><tr><td scope="row">10-Q</td>'
            '<td scope="row"><a href="/Archives/edgar/data/1/x/co-20260630.htm">'
            'co-20260630.htm</a></td>'
            '<td scope="row">10-Q</td></tr></table>'
        )
        doc_html = "<html><body>Item 2. Management's Discussion and Analysis Revenue grew.</body></html>"
        responses = iter([_FakeHtmlResp(index_html), _FakeHtmlResp(doc_html)])
        row = {
            "company": "Test Co", "url": "https://www.sec.gov/x/co-index.htm",
            "published_at": "2026-08-01", "title": "10-Q  - Quarterly report",
            "source_type": "sec_edgar_10q", "_source_hash": em._url_hash("https://x/10q"),
        }
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(em, "EARNINGS_HISTORY_PATH", Path(tmp) / "earnings_calls.jsonl"), \
             patch("urllib.request.urlopen", side_effect=lambda *a, **kw: next(responses)), \
             patch.object(em, "_fetch_fool_transcript_excerpt", return_value={"excerpt": "", "source_url": None}):
            rec = em.record_earnings_history(row, [])
        self.assertIn("Revenue grew", rec["excerpt"])


class TestExcerptReachingQuote(unittest.TestCase):
    """RB-2026-09-06, Predictive Market Intelligence differentiation-read
    need: a company's genuine strategic framing usually lives in a quoted
    CEO/executive statement further into the release than the standard
    600-char lead excerpt reaches. Confirmed live against 3 real 2026 press
    releases before picking the 3,500-char cap (quote position varies:
    Wingstop 271 chars, Papa Johns ~1,900, Starbucks ~3,200) -- these tests
    pin real excerpts from those three real bodies as permanent regressions,
    not just synthetic examples."""

    def test_quote_within_default_600_chars_does_not_extend(self):
        # Real shape: Wingstop's quote starts at char 271, already inside
        # the 600-char default -- no extension needed or performed.
        body = (
            "WINGSTOP REPORTS FISCAL SECOND QUARTER 2026 FINANCIAL RESULTS " +
            ("Filler metrics text. " * 5) +
            '"This quarter reflects strong execution on the next phase of growth for Wingstop," '
            'said Michael Skipworth, President and Chief Executive Officer. ' +
            ("More filler text after the quote. " * 20)
        )
        excerpt = em._extract_excerpt_reaching_quote(body)
        self.assertEqual(len(excerpt), 600)
        self.assertIn("Michael Skipworth", excerpt)

    def test_quote_beyond_600_chars_extends_to_reach_it(self):
        # Real shape: Papa Johns' quote doesn't start until ~1,600 chars in.
        body = (
            "PAPA JOHNS ANNOUNCES SECOND QUARTER 2026 RESULTS " +
            ("Metrics paragraph with no quote yet. " * 45) +
            '"Second quarter results reflected continued momentum," '
            'said Todd Penegor, President and CEO. ' +
            ("Trailing commentary after the quote. " * 30)
        )
        self.assertGreater(len(body[:600]), 0)
        self.assertNotIn("Todd Penegor", body[:600])  # confirm the quote genuinely isn't in the default window
        excerpt = em._extract_excerpt_reaching_quote(body)
        self.assertGreater(len(excerpt), 600)
        self.assertIn("Todd Penegor", excerpt)

    def test_no_quote_anywhere_falls_back_to_default_600(self):
        body = "PLAIN CORPORATE ANNOUNCEMENT, NO QUOTED SPEECH ANYWHERE. " + ("Filler text. " * 100)
        excerpt = em._extract_excerpt_reaching_quote(body)
        self.assertEqual(excerpt, body[:600])

    def test_quote_beyond_max_len_is_not_reached(self):
        # A quote starting past _QUOTE_SEEK_MAX_LEN must not extend the
        # excerpt indefinitely -- falls back to the default 600 chars
        # rather than searching (and potentially capturing) unbounded text.
        body = ("Filler with no quote. " * 200) + '"A quote too far out to reach," said Someone.'
        self.assertGreater(len(body), em._QUOTE_SEEK_MAX_LEN)
        excerpt = em._extract_excerpt_reaching_quote(body)
        self.assertEqual(excerpt, body[:600])
        self.assertNotIn("Someone", excerpt)

    def test_extended_excerpt_never_exceeds_the_cap(self):
        body = ("Filler. " * 500) + '"' + ("x" * 380) + '" said Someone Important.'
        excerpt = em._extract_excerpt_reaching_quote(body)
        self.assertLessEqual(len(excerpt), em._QUOTE_SEEK_MAX_LEN)


class _FakeDdgResp:
    """Minimal urlopen-response stand-in carrying raw bytes (DDG/Fool pages
    aren't always simple ASCII HTML), unlike _FakeHtmlResp above which
    always encodes as utf-8 -- kept separate so these tests can also cover
    the errors=\"replace\" decode-failure path if ever needed."""

    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


class TestFoolTranscriptSource(unittest.TestCase):
    """RB-2026-09-05, 'expand every free resource we can find': Motley Fool
    publishes free, unpaywalled, speaker-attributed earnings-call
    transcripts for many (not all -- confirmed no PAR Technology coverage)
    tracked companies. Live-verified against real McDonald's/Apple
    transcript pages before writing this. Fool's own listing/index page
    404s via plain urllib (confirmed separately), so discovery goes through
    a DuckDuckGo HTML search instead of scraping Fool's index directly."""

    def _ddg_result_html(self, real_url: str) -> str:
        wrapped = "https://duckduckgo.com/l/?uddg=" + __import__("urllib.parse", fromlist=["quote"]).quote(real_url) + "&rut=abc"
        return f'<a class="result__a" href="{wrapped}">some result</a>'

    def test_search_extracts_real_url_from_ddg_redirect_wrapper(self):
        html = self._ddg_result_html("https://www.fool.com/earnings/call-transcripts/2026/07/22/mcdonalds-mcd-q2-2026-earnings-call-transcript/")
        with patch("urllib.request.urlopen", return_value=_FakeDdgResp(html)):
            url = em._search_fool_transcript_url("McDonald's", date(2026, 8, 1))
        self.assertEqual(url, "https://www.fool.com/earnings/call-transcripts/2026/07/22/mcdonalds-mcd-q2-2026-earnings-call-transcript/")

    def test_search_ignores_non_fool_results(self):
        html = self._ddg_result_html("https://www.example.com/some-other-transcript/")
        with patch("urllib.request.urlopen", return_value=_FakeDdgResp(html)):
            url = em._search_fool_transcript_url("McDonald's", date(2026, 8, 1))
        self.assertIsNone(url)

    def test_search_no_results_returns_none(self):
        with patch("urllib.request.urlopen", return_value=_FakeDdgResp("<html><body>no results</body></html>")):
            url = em._search_fool_transcript_url("McDonald's", date(2026, 8, 1))
        self.assertIsNone(url)

    def test_search_fetch_failure_returns_none(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            url = em._search_fool_transcript_url("McDonald's", date(2026, 8, 1))
        self.assertIsNone(url)

    def test_fetch_excerpt_extracts_text_after_marker(self):
        transcript_url = "https://www.fool.com/earnings/call-transcripts/2026/07/22/mcdonalds-mcd-q2-2026-earnings-call-transcript/"
        ddg_html = self._ddg_result_html(transcript_url)
        doc_html = (
            "<html><body><script>var x = 1;</script>Prepared Remarks "
            "Full Conference Call Transcript Operator: Good morning and "
            "welcome to the McDonald's earnings call. Comparable sales "
            "grew this quarter.</body></html>"
        )
        responses = iter([_FakeDdgResp(ddg_html), _FakeDdgResp(doc_html)])
        with patch("urllib.request.urlopen", side_effect=lambda *a, **kw: next(responses)):
            result = em._fetch_fool_transcript_excerpt("McDonald's", date(2026, 8, 1))
        self.assertEqual(result["source_url"], transcript_url)
        self.assertIn("Comparable sales grew this quarter", result["excerpt"])
        self.assertNotIn("Prepared Remarks", result["excerpt"])
        self.assertNotIn("var x = 1", result["excerpt"])

    def test_fetch_excerpt_no_search_result_returns_empty(self):
        with patch("urllib.request.urlopen", return_value=_FakeDdgResp("<html>no results</html>")):
            result = em._fetch_fool_transcript_excerpt("Some Obscure Co", date(2026, 8, 1))
        self.assertEqual(result, {"excerpt": "", "source_url": None})

    def test_fetch_excerpt_no_marker_in_document_returns_empty_but_keeps_url(self):
        transcript_url = "https://www.fool.com/earnings/call-transcripts/2026/07/22/mcdonalds-mcd-q2-2026-earnings-call-transcript/"
        ddg_html = self._ddg_result_html(transcript_url)
        responses = iter([_FakeDdgResp(ddg_html), _FakeDdgResp("<html><body>Cover page only.</body></html>")])
        with patch("urllib.request.urlopen", side_effect=lambda *a, **kw: next(responses)):
            result = em._fetch_fool_transcript_excerpt("McDonald's", date(2026, 8, 1))
        self.assertEqual(result["excerpt"], "")
        self.assertEqual(result["source_url"], transcript_url)

    def test_fetch_excerpt_article_fetch_failure_keeps_url_but_empty_excerpt(self):
        import urllib.error
        transcript_url = "https://www.fool.com/earnings/call-transcripts/2026/07/22/mcdonalds-mcd-q2-2026-earnings-call-transcript/"
        ddg_html = self._ddg_result_html(transcript_url)
        responses = iter([_FakeDdgResp(ddg_html)])

        def _side_effect(*a, **kw):
            try:
                return next(responses)
            except StopIteration:
                raise urllib.error.URLError("boom")

        with patch("urllib.request.urlopen", side_effect=_side_effect):
            result = em._fetch_fool_transcript_excerpt("McDonald's", date(2026, 8, 1))
        self.assertEqual(result, {"excerpt": "", "source_url": transcript_url})

    def test_record_earnings_history_persists_transcript_fields_and_merges_dimensions(self):
        """End-to-end through record_earnings_history: the transcript
        excerpt's own detected dimensions must merge alongside the
        press-release excerpt's, and both transcript fields must land on
        the persisted record."""
        transcript_url = "https://www.fool.com/earnings/call-transcripts/2026/07/22/mcdonalds-mcd-q2-2026-earnings-call-transcript/"
        ddg_html = self._ddg_result_html(transcript_url)
        index_html = (
            '<table><tr><td>EX-99.1</td>'
            '<td><a href="/Archives/edgar/data/1/x/pr.htm">pr.htm</a></td></tr></table>'
        )
        pr_html = "<html><body>For Immediate Release Revenue grew this quarter.</body></html>"
        transcript_html = (
            "<html><body>Full Conference Call Transcript Operator: welcome. "
            "Comparable store sales and franchise unit growth were strong, "
            "and we're increasing our technology investment this year.</body></html>"
        )
        responses = iter([
            _FakeDdgResp(index_html), _FakeDdgResp(pr_html),
            _FakeDdgResp(ddg_html), _FakeDdgResp(transcript_html),
        ])
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(em, "EARNINGS_HISTORY_PATH", Path(tmp) / "earnings_calls.jsonl"), \
             patch("urllib.request.urlopen", side_effect=lambda *a, **kw: next(responses)):
            row = {
                "company": "McDonald's", "url": "https://x/mcd-8k",
                "published_at": "2026-08-01", "title": "8-K - Current report",
                "source_type": "sec_edgar_8k", "_source_hash": em._url_hash("https://x/mcd-8k"),
            }
            rec = em.record_earnings_history(row, [])
        self.assertEqual(rec["transcript_source_url"], transcript_url)
        self.assertIn("Comparable store sales", rec["transcript_excerpt"])
        self.assertIn("Customer", rec["signal_dimensions"])
        self.assertIn("Franchisee", rec["signal_dimensions"])
        self.assertIn("Technology", rec["signal_dimensions"])

    def test_record_earnings_history_no_transcript_found_leaves_fields_empty(self):
        with patch.object(em, "_fetch_fool_transcript_excerpt", return_value={"excerpt": "", "source_url": None}), \
             patch("urllib.request.urlopen", return_value=_FakeHtmlResp("<html>no exhibit</html>")):
            row = {
                "company": "Some Obscure Co", "url": "https://x/obscure-8k",
                "published_at": "2026-08-01", "title": "8-K - Current report",
                "source_type": "sec_edgar_8k", "_source_hash": em._url_hash("https://x/obscure-8k"),
            }
            with tempfile.TemporaryDirectory() as tmp:
                with patch.object(em, "EARNINGS_HISTORY_PATH", Path(tmp) / "earnings_calls.jsonl"):
                    rec = em.record_earnings_history(row, [])
        self.assertEqual(rec["transcript_excerpt"], "")
        self.assertIsNone(rec["transcript_source_url"])


class TestBackfillEarningsHistory(unittest.TestCase):
    """RB-2026-08-29: the free-source half of the predictive-market-
    intelligence design -- 24-month historical backfill across every
    tracked company. Live-verified separately against real McDonald's/Toast
    EDGAR data (35 real records, ~22 months of real coverage); these tests
    cover the function's own logic (company iteration, 8-K materiality
    filter, no-CIK skip, idempotency, coverage reporting) with a fully
    mocked EDGAR feed."""

    def _atom_feed(self, entries: list[tuple[str, str, str]]) -> str:
        """entries: list of (title, url, updated_iso_date)."""
        items = "".join(
            f'<entry><title>{title}</title><link href="{url}"/>'
            f"<updated>{updated}T00:00:00Z</updated></entry>"
            for title, url, updated in entries
        )
        return f'<feed xmlns="http://www.w3.org/2005/Atom">{items}</feed>'

    def _company(self, name="Test Co", ticker="TST", cik="1"):
        return {"name": name, "ticker": ticker, "edgar_cik": cik,
                "side": "vendor_supply", "category": "pos", "strategic_relevance": "medium"}

    def test_skips_company_with_no_cik(self):
        company = self._company(cik=None)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(em, "EARNINGS_HISTORY_PATH", Path(tmp) / "h.jsonl"):
                result = em.backfill_earnings_history(companies=[company], live=True)
        self.assertIn("Test Co", result["companies_skipped_no_cik"])
        self.assertEqual(result["companies_processed"], 0)

    def test_10q_filings_recorded_unconditionally(self):
        """Every 10-Q found should be recorded -- no materiality gate, since
        the quarterly report itself is the disclosure this design wants."""
        company = self._company()
        feed_10q = self._atom_feed([
            ("10-Q  - Quarterly report", "https://www.sec.gov/x/10q-index.htm", "2026-05-01"),
        ])
        empty_feed = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'

        def fake_fetch_xml(url, **kw):
            import xml.etree.ElementTree as ET
            if "type=10-Q" in url:
                return ET.fromstring(feed_10q)
            return ET.fromstring(empty_feed)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(em, "EARNINGS_HISTORY_PATH", Path(tmp) / "h.jsonl"), \
                 patch.object(em, "_fetch_xml", side_effect=fake_fetch_xml), \
                 patch("urllib.request.urlopen", return_value=_FakeHtmlResp("<html>no primary doc row</html>")):
                result = em.backfill_earnings_history(companies=[company], live=True)
        self.assertEqual(result["records_written"], 1)
        self.assertEqual(result["per_company"][0]["written"], 1)

    def test_8k_non_earnings_release_is_not_recorded(self):
        """An 8-K classified as something other than earnings_release
        (e.g. a routine officer-change item) must not flood the history --
        matches the daily pipeline's own existing filter."""
        company = self._company()
        # A generic 8-K title with no earnings-shaped language classifies
        # as material_event, not earnings_release (see _classify_signal).
        feed_8k = self._atom_feed([
            ("8-K  - Current report", "https://www.sec.gov/x/8k-index.htm", "2026-05-01"),
        ])
        empty_feed = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'

        def fake_fetch_xml(url, **kw):
            import xml.etree.ElementTree as ET
            if "type=8-K" in url:
                return ET.fromstring(feed_8k)
            return ET.fromstring(empty_feed)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(em, "EARNINGS_HISTORY_PATH", Path(tmp) / "h.jsonl"), \
                 patch.object(em, "_fetch_xml", side_effect=fake_fetch_xml), \
                 patch.object(em, "_classify_8k_via_index", return_value=False), \
                 patch("urllib.request.urlopen", return_value=_FakeHtmlResp("<html>index page</html>")):
                result = em.backfill_earnings_history(companies=[company], live=True)
        self.assertEqual(result["records_written"], 0)

    def test_idempotent_rerun_reports_already_present(self):
        company = self._company()
        feed_10k = self._atom_feed([
            ("10-K  - Annual report", "https://www.sec.gov/x/10k-index.htm", "2026-01-01"),
        ])
        empty_feed = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'

        def fake_fetch_xml(url, **kw):
            import xml.etree.ElementTree as ET
            if "type=10-K" in url:
                return ET.fromstring(feed_10k)
            return ET.fromstring(empty_feed)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(em, "EARNINGS_HISTORY_PATH", Path(tmp) / "h.jsonl"), \
                 patch.object(em, "_fetch_xml", side_effect=fake_fetch_xml), \
                 patch("urllib.request.urlopen", return_value=_FakeHtmlResp("<html>no doc</html>")):
                first = em.backfill_earnings_history(companies=[company], live=True)
                second = em.backfill_earnings_history(companies=[company], live=True)
        self.assertEqual(first["records_written"], 1)
        self.assertEqual(second["records_written"], 0)
        self.assertEqual(second["records_already_present"], 1)

    def test_reports_oldest_and_newest_event_date_per_company(self):
        company = self._company()
        feed_10q = self._atom_feed([
            ("10-Q  - Quarterly report", "https://www.sec.gov/x/q1.htm", "2024-11-01"),
            ("10-Q  - Quarterly report", "https://www.sec.gov/x/q2.htm", "2026-05-01"),
        ])
        empty_feed = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'

        def fake_fetch_xml(url, **kw):
            import xml.etree.ElementTree as ET
            if "type=10-Q" in url:
                return ET.fromstring(feed_10q)
            return ET.fromstring(empty_feed)

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(em, "EARNINGS_HISTORY_PATH", Path(tmp) / "h.jsonl"), \
                 patch.object(em, "_fetch_xml", side_effect=fake_fetch_xml), \
                 patch("urllib.request.urlopen", return_value=_FakeHtmlResp("<html>no doc</html>")):
                result = em.backfill_earnings_history(companies=[company], live=True, lookback_days=800)
        pc = result["per_company"][0]
        self.assertEqual(pc["oldest_event_date"], "2024-11-01")
        self.assertEqual(pc["newest_event_date"], "2026-05-01")


if __name__ == "__main__":
    unittest.main()
