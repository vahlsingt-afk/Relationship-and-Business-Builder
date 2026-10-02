"""
test_external_brief_cadence.py

Tests for the /daily_brief/external cadence change: this report (Restaurant
Industry & Technology Intelligence) now publishes only on Tuesdays and
Fridays, accumulating fresh restaurant headlines since the last published
edition, and drops world/national headlines entirely (out of scope for a
restaurant-industry digest shared with teammates).

Covers:
  ECB1: _build_external_brief_payload never includes world_national_headlines
  ECB2: window_start accumulation — items before window_start are excluded,
        items on/after window_start are included
  ECB3: no window_start (bootstrap) falls back to the 7-day trailing window
  ECB4: _next_external_publish_date always returns a Tuesday or Friday
  ECB5: GET /daily_brief/external on a publish day builds + persists a fresh
        edition; on a non-publish day it returns the last persisted edition
        unchanged (no rebuild) with status="cached"
  ECB6: a second call on the same publish day does not shrink the window
        (idempotent — returns the already-persisted edition, doesn't re-run
        with window_start = last_published + 1 == today)
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False


def _headline(title, url, pub_date):
    return {
        "title": title,
        "why_it_matters": None,
        "summary": "A summary.",
        "extras": {"source_url": url, "pub_date": pub_date},
    }


def _fake_report(*, world=None, industry=None, tech=None):
    return {
        "canonical_brief": {
            "sections": {
                "world_national_headlines": world or [],
                "restaurant_industry_headlines": industry or [],
                "restaurant_technology_headlines": tech or [],
                "watchlist_intelligence": [],
                "strategic_industry_signals": [],
            }
        },
        "watchlist_intelligence": [],
    }


class TestECB1WorldHeadlinesExcluded(unittest.TestCase):
    def test_world_headlines_never_in_payload(self):
        import server as srv
        report = _fake_report(
            world=[_headline("World story", "https://reuters.com/a", "2026-08-05")],
            industry=[_headline("NRN story", "https://nrn.com/a", "2026-08-05")],
        )
        payload = srv._build_external_brief_payload(report, date(2026, 8, 10))
        self.assertNotIn("world_national_headlines", payload)
        self.assertEqual(len(payload["restaurant_industry_headlines"]), 1)


class TestECB2WindowAccumulation(unittest.TestCase):
    def test_items_before_window_start_excluded(self):
        import server as srv
        report = _fake_report(
            industry=[
                _headline("Old story", "https://nrn.com/old", "2026-08-04"),
                _headline("New story", "https://nrn.com/new", "2026-08-08"),
            ]
        )
        payload = srv._build_external_brief_payload(
            report, date(2026, 8, 10), window_start=date(2026, 8, 8)
        )
        titles = [i["title"] for i in payload["restaurant_industry_headlines"]]
        self.assertNotIn("Old story", titles)
        self.assertIn("New story", titles)

    def test_no_window_start_falls_back_to_trailing_7_days(self):
        import server as srv
        report = _fake_report(
            industry=[
                _headline("Too old", "https://nrn.com/old", "2026-07-01"),
                _headline("Within window", "https://nrn.com/new", "2026-08-05"),
            ]
        )
        payload = srv._build_external_brief_payload(report, date(2026, 8, 10), window_start=None)
        titles = [i["title"] for i in payload["restaurant_industry_headlines"]]
        self.assertNotIn("Too old", titles)
        self.assertIn("Within window", titles)


class TestECB4NextPublishDate(unittest.TestCase):
    def test_always_returns_tuesday_or_friday(self):
        import server as srv
        for offset in range(14):
            d = date(2026, 8, 1)
            from datetime import timedelta
            d = d + timedelta(days=offset)
            nxt = srv._next_external_publish_date(d)
            self.assertIn(nxt.weekday(), (1, 4))
            self.assertGreater(nxt, d)


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed")
class TestECB5And6EndpointGating(unittest.TestCase):
    def setUp(self):
        import server as srv
        self.srv = srv
        self._orig_dir = srv._EXTERNAL_BRIEF_PUBLISHED_DIR
        self._orig_latest = srv._EXTERNAL_BRIEF_LATEST_PATH
        self._tmp = Path(tempfile.mkdtemp(prefix="ecb_test_"))
        srv._EXTERNAL_BRIEF_PUBLISHED_DIR = self._tmp
        srv._EXTERNAL_BRIEF_LATEST_PATH = self._tmp / "latest.json"

        self._orig_build_report = srv.daily_brief.build_report
        self._orig_read_cache = srv.core.read_cache
        srv.core.read_cache = lambda name: None

        self.client = TestClient(srv.app)

    def tearDown(self):
        self.srv._EXTERNAL_BRIEF_PUBLISHED_DIR = self._orig_dir
        self.srv._EXTERNAL_BRIEF_LATEST_PATH = self._orig_latest
        self.srv.daily_brief.build_report = self._orig_build_report
        self.srv.core.read_cache = self._orig_read_cache

    def _set_report_for(self, industry_titles):
        def _fake_build_report(d):
            return _fake_report(
                industry=[
                    _headline(t, f"https://nrn.com/{i}", d.isoformat())
                    for i, t in enumerate(industry_titles)
                ]
            )
        self.srv.daily_brief.build_report = _fake_build_report

    def test_publish_day_builds_fresh_non_publish_day_returns_cached(self):
        # 2026-08-11 is a Tuesday (publish day)
        self._set_report_for(["Tuesday story"])
        resp = self.client.get(
            "/daily_brief/external", params={"date": "2026-08-11"},
            headers={"x-api-key": "test-key"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "published")
        titles = [i["title"] for i in data["restaurant_industry_headlines"]]
        self.assertIn("Tuesday story", titles)

        # 2026-08-12 is a Wednesday (not a publish day) — must return the
        # Tuesday edition unchanged, not a fresh build.
        self._set_report_for(["Wednesday story — should not appear"])
        resp2 = self.client.get(
            "/daily_brief/external", params={"date": "2026-08-12"},
            headers={"x-api-key": "test-key"},
        )
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertEqual(data2["status"], "cached")
        titles2 = [i["title"] for i in data2["restaurant_industry_headlines"]]
        self.assertIn("Tuesday story", titles2)
        self.assertNotIn("Wednesday story — should not appear", titles2)

    def test_second_call_same_publish_day_is_idempotent(self):
        self._set_report_for(["First call story"])
        resp1 = self.client.get(
            "/daily_brief/external", params={"date": "2026-08-11"},
            headers={"x-api-key": "test-key"},
        )
        self.assertEqual(resp1.json()["status"], "published")

        # If a second same-day call rebuilt with window_start = today+1's
        # predecessor logic, it would produce an empty accumulation window.
        # It must instead just return what's already persisted.
        self._set_report_for([])
        resp2 = self.client.get(
            "/daily_brief/external", params={"date": "2026-08-11"},
            headers={"x-api-key": "test-key"},
        )
        titles2 = [i["title"] for i in resp2.json()["restaurant_industry_headlines"]]
        self.assertIn("First call story", titles2)

    def test_no_prior_edition_on_non_publish_day_returns_not_yet_published(self):
        resp = self.client.get(
            "/daily_brief/external", params={"date": "2026-08-12"},
            headers={"x-api-key": "test-key"},
        )
        data = resp.json()
        self.assertEqual(data["status"], "not_yet_published")
        self.assertEqual(data["next_publish_date"], "2026-08-14")


if __name__ == "__main__":
    unittest.main()
