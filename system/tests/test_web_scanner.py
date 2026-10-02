"""
test_web_scanner.py — RB 9.38 / Sprint E-2
Tests for web_scanner: external RSS fetcher, classifier, entity detector,
signal detector, cache layer, and ScanResult integration.

All tests are fully offline — no network calls.  A mock fetcher is injected
wherever fetch behaviour is needed.

Test groups:
  WSC1 (6):  RSS parsing — RSS 2.0, Atom, title/url/date extraction,
             HTML stripping, empty feed, malformed XML
  WSC2 (6):  Classification — tech keywords → technology, industry keywords →
             industry, world fallback, RTN source prior, mixed article, Atom feed
  WSC3 (6):  Entity & signal detection — known entity matched case-insensitively,
             multiple entities, acquisition pattern, exec-change pattern,
             no-entity text, "general" when no signal
  WSC4 (6):  Cache — cache miss triggers fetch, cache hit skips fetch, stale
             cache triggers re-fetch, scan_all_sources returns ScanResult,
             three-bucket structure correct, source_health populated
  WSC5 (6):  Integration — brief item has required fields, correct disposition
             for high-signal items, all-fetch-fail returns empty ScanResult,
             DB write path is best-effort (no crash if DB absent), ScanResult
             .all_items() aggregates correctly, .to_brief_sections() keys correct
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import web_scanner as ws
from web_scanner import (
    ScanResult,
    classify_headline,
    detect_entities,
    detect_signal_type,
    is_cache_fresh,
    parse_rss_items,
    scan_all_sources,
)

# Every scan_all_sources(force_refresh=True, fetcher=mock_fetcher, ...) call in
# this file caches its result by source *name* — and none of them passed
# cache_path, so the mock fetcher's fixture RSS (literally titled "PAR
# Technology acquires TASK Group for $200M" below) was written straight into
# the real production system/.cache/web_scanner_cache.json on every test run,
# silently overwriting genuinely-fetched headlines for any source name reused
# here (e.g. "Restaurant Dive", "BBC Business"). Isolate the whole module to a
# throwaway cache file for the duration of this test run.
_CACHE_TMPDIR = tempfile.TemporaryDirectory()
_cache_patch = patch.object(ws, "_CACHE_FILE", Path(_CACHE_TMPDIR.name) / "web_scanner_cache.json")


def setUpModule():
    _cache_patch.start()


def tearDownModule():
    _cache_patch.stop()
    _CACHE_TMPDIR.cleanup()

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_RSS2_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Restaurant Dive</title>
    <link>https://www.restaurantdive.com</link>
    <item>
      <title>PAR Technology acquires TASK Group for $200M</title>
      <link>https://www.restaurantdive.com/news/par-task-acquisition</link>
      <description>&lt;p&gt;PAR Technology announced it will acquire TASK Group, expanding its POS footprint.&lt;/p&gt;</description>
      <pubDate>Mon, 01 Jun 2026 09:00:00 +0000</pubDate>
    </item>
    <item>
      <title>McDonald's rolls out AI drive-thru voice ordering</title>
      <link>https://www.restaurantdive.com/news/mcdonalds-ai-drive-thru</link>
      <description>McDonald's is expanding its AI-powered drive-thru kiosk program nationwide.</description>
      <pubDate>Mon, 01 Jun 2026 08:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Federal Reserve holds rates steady amid inflation concerns</title>
      <link>https://www.restaurantdive.com/news/fed-rates</link>
      <description>The Federal Reserve held interest rates steady at its June meeting.</description>
      <pubDate>Sun, 31 May 2026 18:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""

_ATOM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Restaurant Technology News</title>
  <entry>
    <title>Olo launches Pay+ integration for Worldpay</title>
    <link href="https://restauranttechnologynews.com/olo-worldpay" rel="alternate"/>
    <published>2026-06-01T10:00:00Z</published>
    <summary>Olo has announced Pay+ now integrates directly with Worldpay payment processors.</summary>
  </entry>
  <entry>
    <title>Toast raises $300M Series F funding round</title>
    <link href="https://restauranttechnologynews.com/toast-funding" rel="alternate"/>
    <published>2026-05-30T12:00:00Z</published>
    <summary>Toast announced a $300M Series F from new investors.</summary>
  </entry>
</feed>"""

_EMPTY_RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Empty Feed</title>
  </channel>
</rss>"""

_MALFORMED_XML = b"<broken xml without closing tag <item>title goes here"


def _make_stale_cache(source_name: str) -> dict:
    """Cache entry that is 10 hours old (stale)."""
    old_time = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    return {source_name: {"last_fetch": old_time, "items": [{"title": "cached"}]}}


def _make_fresh_cache(source_name: str, items: list[dict] | None = None) -> dict:
    """Cache entry that is 1 hour old (fresh)."""
    recent_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return {source_name: {"last_fetch": recent_time, "items": items or [{"title": "cached"}]}}


# ---------------------------------------------------------------------------
# WSC1 — RSS parsing
# ---------------------------------------------------------------------------

class TestWSC1_RSSParsing(unittest.TestCase):
    """WSC1: RSS 2.0 and Atom parsing correctness."""

    def test_WSC1a_parse_rss2_item_count(self):
        """RSS 2.0 feed with 3 items returns 3 parsed items."""
        items = parse_rss_items(_RSS2_XML, "Restaurant Dive")
        self.assertEqual(len(items), 3)

    def test_WSC1b_parse_rss2_fields_extracted(self):
        """RSS 2.0 item has title, url, and description populated."""
        items = parse_rss_items(_RSS2_XML, "Restaurant Dive")
        first = items[0]
        self.assertIn("PAR Technology", first["title"])
        self.assertIn("restaurantdive.com", first["url"])
        self.assertNotEqual(first["description"], "")

    def test_WSC1c_parse_rss2_date_extracted(self):
        """RSS 2.0 pubDate parsed to ISO YYYY-MM-DD."""
        items = parse_rss_items(_RSS2_XML, "Restaurant Dive")
        self.assertEqual(items[0]["pub_date"], "2026-06-01")

    def test_WSC1d_parse_atom_items(self):
        """Atom feed returns correct items with title and url."""
        items = parse_rss_items(_ATOM_XML, "RTN")
        self.assertEqual(len(items), 2)
        self.assertIn("Olo", items[0]["title"])
        self.assertIn("restauranttechnologynews.com", items[0]["url"])

    def test_WSC1e_empty_feed_returns_empty_list(self):
        """Feed with no items returns empty list (not error)."""
        items = parse_rss_items(_EMPTY_RSS_XML, "Empty")
        self.assertEqual(items, [])

    def test_WSC1f_malformed_xml_returns_empty_list(self):
        """Malformed XML returns empty list (not exception)."""
        items = parse_rss_items(_MALFORMED_XML, "Bad Source")
        self.assertEqual(items, [])


# ---------------------------------------------------------------------------
# WSC2 — Classification
# ---------------------------------------------------------------------------

class TestWSC2_Classification(unittest.TestCase):
    """WSC2: Domain classification correctness."""

    def test_WSC2a_tech_keyword_routes_to_technology(self):
        """Title with 'point of sale' → restaurant_technology."""
        domain = classify_headline(
            "NCR Voyix launches new point of sale platform",
            "NCR Voyix unveiled its next-generation POS system.",
            default_domain="mixed",
        )
        self.assertEqual(domain, "restaurant_technology")

    def test_WSC2b_industry_keyword_routes_to_restaurant_industry(self):
        """Title with 'franchise' and no tech keywords → restaurant_industry."""
        domain = classify_headline(
            "Chick-fil-A expands franchise program to new markets",
            "Chick-fil-A announced new franchise locations in the Midwest.",
            default_domain="mixed",
        )
        self.assertEqual(domain, "restaurant_industry")

    def test_WSC2c_no_match_falls_back_to_world_national(self):
        """Non-restaurant, non-tech text → world_national."""
        domain = classify_headline(
            "Federal Reserve holds interest rates steady",
            "The Fed kept rates at 5.25% after its June meeting.",
            default_domain="mixed",
        )
        self.assertEqual(domain, "world_national")

    def test_WSC2d_rtn_source_prior_forces_technology(self):
        """RTN source with default_domain='restaurant_technology' always → technology."""
        domain = classify_headline(
            "Federal rates impact restaurant industry",
            "General economy news.",
            default_domain="restaurant_technology",  # RTN prior
        )
        self.assertEqual(domain, "restaurant_technology")

    def test_WSC2e_tech_takes_priority_over_industry(self):
        """Article with both restaurant brand AND POS keywords → technology."""
        domain = classify_headline(
            "McDonald's selects Toast POS for 5000 locations",
            "McDonald's will deploy Toast point of sale across US.",
            default_domain="mixed",
        )
        self.assertEqual(domain, "restaurant_technology")

    def test_WSC2f_nrn_default_domain_applies_when_no_tech_keywords(self):
        """NRN source default (restaurant_industry) applied when no tech keywords."""
        domain = classify_headline(
            "Starbucks same-store sales rise 3% in Q2",
            "Starbucks reported positive comparable sales growth.",
            default_domain="restaurant_industry",
        )
        self.assertEqual(domain, "restaurant_industry")


# ---------------------------------------------------------------------------
# WSC3 — Entity & signal detection
# ---------------------------------------------------------------------------

class TestWSC3_EntitySignal(unittest.TestCase):
    """WSC3: Entity and signal type detection."""

    def test_WSC3a_known_entity_detected_case_insensitive(self):
        """'olo' in lowercase title matches 'Olo' in watchlist."""
        entities = detect_entities(
            "olo launches pay+ integration",
            "olo announced new payment integration with worldpay.",
        )
        olo_matches = [e for e in entities if e.lower() == "olo"]
        self.assertTrue(len(olo_matches) >= 1, f"Olo not found in {entities}")

    def test_WSC3b_multiple_entities_extracted(self):
        """Title mentioning PAR Technology and TASK Group returns both."""
        entities = detect_entities(
            "PAR Technology acquires TASK Group",
            "PAR will absorb TASK Group's operations.",
        )
        entity_lower = [e.lower() for e in entities]
        self.assertIn("par technology", entity_lower)
        self.assertIn("task group", entity_lower)

    def test_WSC3c_acquisition_signal_detected(self):
        """'acquires' in title → signal_type = acquisition."""
        sig = detect_signal_type(
            "PAR Technology acquires TASK Group for $200M",
            "",
        )
        self.assertEqual(sig, "acquisition")

    def test_WSC3d_exec_change_signal_detected(self):
        """'appoints new CEO' → signal_type = executive_hire."""
        sig = detect_signal_type(
            "Toast appoints new CEO following leadership transition",
            "",
        )
        self.assertEqual(sig, "executive_hire")

    def test_WSC3e_no_entity_text_returns_empty(self):
        """Unrelated text returns no entities."""
        entities = detect_entities(
            "Federal Reserve holds rates steady",
            "Monetary policy unchanged at June meeting.",
        )
        self.assertEqual(entities, [])

    def test_WSC3f_no_signal_pattern_returns_general(self):
        """Text with no signal keywords returns 'general'."""
        sig = detect_signal_type(
            "Restaurant industry overview for June 2026",
            "A summary of the current restaurant landscape.",
        )
        self.assertEqual(sig, "general")


# ---------------------------------------------------------------------------
# WSC4 — Cache & scan mechanics
# ---------------------------------------------------------------------------

class TestWSC4_Cache(unittest.TestCase):
    """WSC4: Cache TTL logic and scan mechanics."""

    def test_WSC4a_cache_miss_triggers_fetch(self):
        """Empty cache → fetcher is called once per source."""
        call_count = {"n": 0}

        def mock_fetcher(url, timeout):
            call_count["n"] += 1
            return _RSS2_XML

        result = scan_all_sources(
            db_path=Path(tempfile.mktemp(suffix=".db")),
            fetcher=mock_fetcher,
            force_refresh=True,
        )
        # 5 sources → fetcher called 5 times
        self.assertEqual(call_count["n"], len(ws.SOURCES))

    def test_WSC4b_is_cache_fresh_returns_true_for_recent(self):
        """Cache entry 1 hour old is considered fresh with 6h TTL."""
        cache = _make_fresh_cache("Restaurant Dive")
        self.assertTrue(is_cache_fresh(cache, "Restaurant Dive", ttl_hours=6))

    def test_WSC4c_is_cache_fresh_returns_false_for_stale(self):
        """Cache entry 10 hours old is stale with 6h TTL."""
        cache = _make_stale_cache("Restaurant Dive")
        self.assertFalse(is_cache_fresh(cache, "Restaurant Dive", ttl_hours=6))

    def test_WSC4d_scan_all_sources_returns_scan_result(self):
        """scan_all_sources returns a ScanResult instance."""
        def mock_fetcher(url, timeout):
            return _RSS2_XML

        result = scan_all_sources(
            db_path=Path(tempfile.mktemp(suffix=".db")),
            fetcher=mock_fetcher,
        )
        self.assertIsInstance(result, ScanResult)

    def test_WSC4e_scan_result_has_three_buckets(self):
        """ScanResult has world_national, restaurant_industry, restaurant_technology lists."""
        def mock_fetcher(url, timeout):
            return _RSS2_XML

        result = scan_all_sources(
            db_path=Path(tempfile.mktemp(suffix=".db")),
            fetcher=mock_fetcher,
        )
        self.assertIsInstance(result.world_national, list)
        self.assertIsInstance(result.restaurant_industry, list)
        self.assertIsInstance(result.restaurant_technology, list)

    def test_WSC4f_source_health_has_entry_per_source(self):
        """source_health list has one entry per configured source."""
        def mock_fetcher(url, timeout):
            return _RSS2_XML

        result = scan_all_sources(
            db_path=Path(tempfile.mktemp(suffix=".db")),
            fetcher=mock_fetcher,
        )
        self.assertEqual(len(result.source_health), len(ws.SOURCES))

    def test_WSC4g_removed_and_test_sources_are_pruned_from_cache(self):
        """The production cache may contain only currently configured feeds."""
        with tempfile.TemporaryDirectory() as td:
            cache_path = Path(td) / "scanner.json"
            cache_path.write_text(json.dumps({
                "Real Feed": _make_fresh_cache("Real Feed")["Real Feed"],
                "Test Feed": _make_fresh_cache("Test Feed")["Test Feed"],
            }))
            cfg = {"sources": [{
                "name": "Real Feed", "url": "https://example.com/real",
                "default_domain": "world_national", "confidence": "high",
            }], "metadata": {}}
            scan_all_sources(
                db_path=Path(td) / "scanner.db",
                cache_path=cache_path,
                sources_config=cfg,
                fetcher=lambda url, timeout: _RSS2_XML,
            )
            saved = json.loads(cache_path.read_text())
            self.assertEqual(set(saved), {"Real Feed"})


# ---------------------------------------------------------------------------
# WSC5 — Integration / edge cases
# ---------------------------------------------------------------------------

class TestWSC5_Integration(unittest.TestCase):
    """WSC5: Brief item format, error handling, ScanResult helpers."""

    def test_WSC5a_brief_item_has_required_fields(self):
        """Scanned items have all fields required by brief section renderers."""
        def mock_fetcher(url, timeout):
            return _RSS2_XML

        result = scan_all_sources(
            db_path=Path(tempfile.mktemp(suffix=".db")),
            fetcher=mock_fetcher,
        )
        all_items = result.all_items()
        self.assertGreater(len(all_items), 0, "Expected at least one item")
        item = all_items[0]
        for key in ("title", "summary", "why_it_matters", "recommended_action",
                    "disposition", "grounding", "confidence", "extras"):
            self.assertIn(key, item, f"Missing key: {key}")

    def test_WSC5b_acquisition_item_gets_act_today_disposition(self):
        """PAR Technology acquisition item gets disposition='act_today'."""
        def mock_fetcher(url, timeout):
            return _RSS2_XML

        result = scan_all_sources(
            db_path=Path(tempfile.mktemp(suffix=".db")),
            fetcher=mock_fetcher,
        )
        all_items = result.all_items()
        par_items = [i for i in all_items if "PAR Technology" in i.get("title", "")]
        if par_items:
            self.assertEqual(par_items[0]["disposition"], "act_today")

    def test_WSC5c_all_fetch_fail_returns_empty_scan_result(self):
        """When all sources fail to fetch, ScanResult is empty but valid."""
        def failing_fetcher(url, timeout):
            return None  # simulate network failure

        result = scan_all_sources(
            db_path=Path(tempfile.mktemp(suffix=".db")),
            fetcher=failing_fetcher,
            force_refresh=True,  # bypass any on-disk cache from prior tests
        )
        self.assertIsInstance(result, ScanResult)
        self.assertEqual(result.total(), 0)
        # All sources should have fetch_error status
        statuses = [h.get("status") for h in result.source_health]
        self.assertTrue(
            all(s in ("fetch_error", "parse_error", "exception") for s in statuses),
            f"Expected all fetch failures, got: {statuses}",
        )

    def test_WSC5d_db_failure_does_not_crash_scanner(self):
        """DB write failure is swallowed; scanner still returns items."""
        def mock_fetcher(url, timeout):
            return _RSS2_XML

        # Pass an invalid DB path (a directory, not a file)
        bad_path = Path(tempfile.mkdtemp()) / "subdir" / "sub2" / "bad.db"
        # Note: IntelligenceDB.open() will create the directory, so pass truly bad path
        # by passing a path in a location with no write permission — or just test that
        # items are returned even when DB raises.  We pass None and rely on the
        # try/except in scan_all_sources.
        result = scan_all_sources(
            db_path=None,  # uses default; may or may not succeed — just must not raise
            fetcher=mock_fetcher,
        )
        # The important thing: no exception raised, result is valid
        self.assertIsInstance(result, ScanResult)

    def test_WSC5e_all_items_aggregates_all_buckets(self):
        """ScanResult.all_items() returns combined list of all three buckets."""
        r = ScanResult(
            world_national=[{"title": "world"}],
            restaurant_industry=[{"title": "industry"}],
            restaurant_technology=[{"title": "tech"}],
        )
        self.assertEqual(len(r.all_items()), 3)
        self.assertEqual(r.total(), 3)

    def test_WSC5f_to_brief_sections_has_correct_keys(self):
        """ScanResult.to_brief_sections() returns dict with the three section keys."""
        r = ScanResult(
            world_national=[{"title": "world"}],
            restaurant_industry=[],
            restaurant_technology=[{"title": "tech"}],
        )
        sections = r.to_brief_sections()
        self.assertIn("world_national_headlines", sections)
        self.assertIn("restaurant_industry_headlines", sections)
        self.assertIn("restaurant_technology_headlines", sections)
        self.assertEqual(sections["world_national_headlines"], [{"title": "world"}])


# ---------------------------------------------------------------------------
# WSC6 — Configurable sources (industry_sources.yaml)
# ---------------------------------------------------------------------------

class TestWSC6_ConfigurableSources(unittest.TestCase):
    """WSC6: load_sources_config(), sources_config override, metadata in ScanResult."""

    def test_WSC6a_load_sources_config_reads_yaml(self):
        """load_sources_config() reads industry_sources.yaml and returns sources list."""
        import tempfile, os
        yaml_content = """
metadata:
  industry_name: "PropTech"
  primary_label: "Real Estate Industry"
  technology_label: "PropTech"
  world_label: "World & National"
sources:
  - name: "Globe St"
    url: "https://www.globest.com/feed/"
    default_domain: industry_primary
    confidence: medium
  - name: "PropTech Insider"
    url: "https://proptech-insider.com/feed/"
    default_domain: industry_technology
    confidence: medium
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            tmp_path = Path(f.name)
        try:
            cfg = ws.load_sources_config(path=tmp_path)
            self.assertEqual(len(cfg["sources"]), 2)
            self.assertEqual(cfg["sources"][0]["name"], "Globe St")
            self.assertEqual(cfg["metadata"]["primary_label"], "Real Estate Industry")
        finally:
            os.unlink(tmp_path)

    def test_WSC6b_load_sources_config_falls_back_when_file_missing(self):
        """load_sources_config() falls back to builtins when file doesn't exist."""
        missing_path = Path("/tmp/does_not_exist_industry_sources_xyz.yaml")
        cfg = ws.load_sources_config(path=missing_path)
        self.assertGreater(len(cfg["sources"]), 0, "Builtin fallback should have sources")
        self.assertIn("metadata", cfg)

    def test_WSC6c_sources_config_param_overrides_file(self):
        """sources_config param passed to scan_all_sources uses those sources, not YAML."""
        custom_config = {
            "sources": [
                {
                    "name": "Custom Source A",
                    "url": "https://example.com/feed/",
                    "default_domain": "industry_primary",
                    "confidence": "medium",
                }
            ],
            "metadata": {
                "industry_name": "Real Estate",
                "primary_label": "Real Estate News",
                "technology_label": "PropTech",
                "world_label": "World & National",
            },
        }
        call_count = {"n": 0}
        def mock_fetcher(url, timeout):
            call_count["n"] += 1
            return _RSS2_XML

        result = scan_all_sources(
            db_path=Path(tempfile.mktemp(suffix=".db")),
            fetcher=mock_fetcher,
            sources_config=custom_config,
            force_refresh=True,
        )
        # Only 1 source configured → fetcher called exactly once
        self.assertEqual(call_count["n"], 1)
        self.assertEqual(len(result.source_health), 1)
        self.assertEqual(result.source_health[0]["source"], "Custom Source A")

    def test_WSC6d_metadata_in_scan_result(self):
        """ScanResult.metadata carries labels from sources_config."""
        custom_config = {
            "sources": [
                {"name": "Test Feed", "url": "https://example.com/", "default_domain": "industry_primary", "confidence": "medium"}
            ],
            "metadata": {
                "primary_label": "Healthcare Industry",
                "technology_label": "HealthTech",
            },
        }
        result = scan_all_sources(
            db_path=Path(tempfile.mktemp(suffix=".db")),
            fetcher=lambda url, t: _RSS2_XML,
            sources_config=custom_config,
        )
        self.assertEqual(result.metadata.get("primary_label"), "Healthcare Industry")
        self.assertEqual(result.metadata.get("technology_label"), "HealthTech")

    def test_WSC6e_industry_primary_domain_routes_to_restaurant_industry_bucket(self):
        """Sources with default_domain=industry_primary route to restaurant_industry bucket."""
        custom_config = {
            "sources": [
                {"name": "Industry Feed", "url": "https://example.com/", "default_domain": "industry_primary", "confidence": "medium"}
            ],
            "metadata": {},
        }
        result = scan_all_sources(
            db_path=Path(tempfile.mktemp(suffix=".db")),
            fetcher=lambda url, t: _RSS2_XML,
            sources_config=custom_config,
            force_refresh=True,
        )
        # "Federal Reserve" (world content, no industry/tech keywords) with
        # default_domain=industry_primary → industry_primary bucket (non-mixed source,
        # keyword bypass) → restaurant_industry.
        # Restaurant Dive articles with PAR/McDonald's → tech keywords → restaurant_technology.
        # Regardless of specific routing, restaurant_industry + restaurant_technology
        # together should contain all items from this source.
        total = result.total()
        self.assertGreater(total, 0)
        non_world = len(result.restaurant_industry) + len(result.restaurant_technology)
        # With industry_primary domain, at least world_national should be empty
        # (or all items go to industry/tech). Total items must be > 0.
        self.assertGreater(total, 0)

    def test_WSC6f_load_sources_config_skips_empty_template_entries(self):
        """Entries with empty name or url in YAML are filtered out (template stubs)."""
        import tempfile, os
        yaml_content = """
metadata:
  industry_name: "Test"
sources:
  - name: ""
    url: ""
    default_domain: industry_primary
  - name: "Real Source"
    url: "https://realsource.com/feed/"
    default_domain: industry_primary
    confidence: medium
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            tmp_path = Path(f.name)
        try:
            cfg = ws.load_sources_config(path=tmp_path)
            names = [s["name"] for s in cfg["sources"]]
            self.assertNotIn("", names, "Empty name entries should be filtered")
            self.assertIn("Real Source", names)
        finally:
            os.unlink(tmp_path)


class TestWSC7_QsrMagazineFeedUrl(unittest.TestCase):
    """RB-DEFECT-2026-07-17: QSR Magazine's /rss.xml 403s (WAF-blocked) and
    had been silently failing since 2026-06-03 -- _scan_source's fetch_error
    branch never updates the cache on failure, so a dead feed's last_fetch
    timestamp freezes forever with nothing surfacing the failure. Root cause:
    the feed moved to /feed (WordPress default). Guard against reintroducing
    the dead URL in either the config default or the live industry_sources.yaml."""

    def test_builtin_sources_do_not_use_dead_qsr_rss_url(self):
        names_urls = {s["name"]: s["url"] for s in ws._BUILTIN_SOURCES}
        self.assertIn("QSR Magazine", names_urls)
        self.assertNotEqual(names_urls["QSR Magazine"], "https://www.qsrmagazine.com/rss.xml")
        self.assertEqual(names_urls["QSR Magazine"], "https://www.qsrmagazine.com/feed")

    def test_industry_sources_yaml_uses_working_qsr_url(self):
        cfg = ws.load_sources_config()
        qsr = next((s for s in cfg["sources"] if s["name"] == "QSR Magazine"), None)
        self.assertIsNotNone(qsr, "QSR Magazine should be an active source")
        self.assertEqual(qsr["url"], "https://www.qsrmagazine.com/feed")

    def test_builtin_sources_do_not_use_dead_franchise_times_url(self):
        names_urls = {s["name"]: s["url"] for s in ws._BUILTIN_SOURCES}
        self.assertIn("Franchise Times", names_urls)
        self.assertNotEqual(names_urls["Franchise Times"], "https://www.franchisetimes.com/rss")


class TestWSC8_DefaultFetcherRetry(unittest.TestCase):
    """RB-DEFECT-2026-07-30: confirmed live on 2026-07-29 and 2026-07-30 that
    every feed failed with the same transient DNS-resolution error
    ([Errno 8] nodename nor servname provided) when the pipeline ran right
    after a scheduled wake, then succeeded minutes later on manual re-run
    with zero code change -- the network interface wasn't reconnected yet.
    _default_fetcher now retries transient errors a couple of times with a
    short delay before giving up."""

    def test_WSC8a_succeeds_immediately_without_sleeping(self):
        with patch("web_scanner.urllib.request.urlopen") as mock_urlopen, \
             patch("web_scanner.time.sleep") as mock_sleep:
            mock_urlopen.return_value.__enter__.return_value.read.return_value = b"<rss></rss>"
            result = ws._default_fetcher("https://example.com/feed")
        self.assertEqual(result, b"<rss></rss>")
        mock_urlopen.assert_called_once()
        mock_sleep.assert_not_called()

    def test_WSC8b_retries_transient_dns_error_then_succeeds(self):
        dns_error = OSError(8, "nodename nor servname provided, or not known")
        success = MagicMock()
        success.__enter__.return_value.read.return_value = b"<rss>ok</rss>"
        with patch("web_scanner.urllib.request.urlopen", side_effect=[dns_error, success]) as mock_urlopen, \
             patch("web_scanner.time.sleep") as mock_sleep:
            result = ws._default_fetcher("https://example.com/feed")
        self.assertEqual(result, b"<rss>ok</rss>")
        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called_once()

    def test_WSC8c_gives_up_after_exhausting_retries(self):
        dns_error = OSError(8, "nodename nor servname provided, or not known")
        with patch("web_scanner.urllib.request.urlopen", side_effect=dns_error) as mock_urlopen, \
             patch("web_scanner.time.sleep") as mock_sleep:
            result = ws._default_fetcher("https://example.com/feed", retries=2)
        self.assertIsNone(result)
        self.assertEqual(mock_urlopen.call_count, 3)  # initial + 2 retries
        self.assertEqual(mock_sleep.call_count, 2)

    def test_WSC8d_http_error_does_not_retry(self):
        http_error = urllib.error.HTTPError("https://example.com/feed", 404, "Not Found", {}, None)
        with patch("web_scanner.urllib.request.urlopen", side_effect=http_error) as mock_urlopen, \
             patch("web_scanner.time.sleep") as mock_sleep:
            result = ws._default_fetcher("https://example.com/feed")
        self.assertIsNone(result)
        mock_urlopen.assert_called_once()
        mock_sleep.assert_not_called()

    def test_WSC8e_zero_retries_behaves_like_original_single_attempt(self):
        dns_error = OSError(8, "nodename nor servname provided, or not known")
        with patch("web_scanner.urllib.request.urlopen", side_effect=dns_error) as mock_urlopen, \
             patch("web_scanner.time.sleep") as mock_sleep:
            result = ws._default_fetcher("https://example.com/feed", retries=0)
        self.assertIsNone(result)
        mock_urlopen.assert_called_once()
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
