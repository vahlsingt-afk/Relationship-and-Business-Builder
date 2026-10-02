"""
test_web_scanner_cache_isolation.py

Regression coverage: scan_all_sources() accepted db_path and sources_config
params specifically so tests wouldn't touch production files, but had no
equivalent for the fetch cache — every scan_all_sources(force_refresh=True,
fetcher=mock_fetcher, ...) call in test_web_scanner.py wrote its mock RSS
fixture (literally titled "PAR Technology acquires TASK Group for $200M")
straight into the real system/.cache/web_scanner_cache.json, keyed by source
name. Any test that reused a real source name (e.g. "BBC Business",
"Restaurant Dive") silently overwrote genuinely-fetched headlines for that
source with fixture content — which is exactly why the 2026-07-06 brief
regeneration in this session unexpectedly went from real BBC World/NPR
content back to a duplicated "PAR Technology acquires TASK Group" / "Federal
Reserve holds rates steady" fixture set after the full test suite ran.

Fixed: scan_all_sources() now accepts cache_path (threaded through
_load_cache/_save_cache), and test_web_scanner.py's setUpModule patches
web_scanner._CACHE_FILE to a temp path for its entire run.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import web_scanner as ws  # noqa: E402


_RSS_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Fixture Source</title>
    <link>https://example.com</link>
    <item>
      <title>Totally Fake Fixture Headline</title>
      <link>https://example.com/fake-fixture-headline</link>
      <pubDate>Mon, 06 Jul 2026 00:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


class TestWebScannerCacheIsolation(unittest.TestCase):
    def test_scan_all_sources_respects_cache_path_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "isolated_cache.json"
            real_cache_path = ws._CACHE_FILE

            def mock_fetcher(url, timeout):
                return _RSS_FIXTURE

            ws.scan_all_sources(
                db_path=Path(tmpdir) / "intel.db",
                fetcher=mock_fetcher,
                force_refresh=True,
                sources_config={
                    "sources": [{
                        "name": "Fixture Source",
                        "url": "https://example.com/rss",
                        "default_domain": "world_national",
                    }],
                    "metadata": {},
                },
                cache_path=cache_path,
            )

            # The isolated path was written to...
            self.assertTrue(cache_path.exists())
            # ...and the real production cache was never touched by this call.
            if real_cache_path.exists():
                real_content = real_cache_path.read_text(encoding="utf-8")
                self.assertNotIn("Totally Fake Fixture Headline", real_content)

    def test_default_cache_path_is_still_production_file_when_unspecified(self):
        # No cache_path passed -> _load_cache/_save_cache fall back to
        # ws._CACHE_FILE, preserving default behavior for real (non-test) callers.
        import inspect
        sig = inspect.signature(ws.scan_all_sources)
        self.assertIn("cache_path", sig.parameters)
        self.assertIsNone(sig.parameters["cache_path"].default)


if __name__ == "__main__":
    unittest.main()
