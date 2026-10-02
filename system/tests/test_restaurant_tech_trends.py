"""
test_restaurant_tech_trends.py

Regression coverage for system/scripts/restaurant_tech_trends.py (the Top
5 Restaurant Technology Trends computation backing the Market
Intelligence page). Confirms: category ranking/direction math, that
evidence citations prefer substantive content over bare stock-price
noise when both exist, that a category with zero qualitative evidence is
labeled honestly rather than presented as a clean trend, and that
persistence round-trips.
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

import restaurant_tech_trends as rtt  # noqa: E402
import team_market_intelligence as tmi  # noqa: E402


def _item(**overrides) -> dict:
    base = {
        "title": "Fixture: substantive headline", "url": "https://example.com/x",
        "source_name": "Fixture Wire", "source_type": "vertical_trade",
        "published_at": "2026-09-28", "company": "Fixture Co.", "side": "vendor_supply",
        "category": "pos", "signal_type": "vendor_expansion",
        "pain_point_or_priority": "Public summary.", "strategic_relevance": "medium",
        "restaurant_tech_vendor_implication": "Public GP note.",
    }
    base.update(overrides)
    return base


class TestComputeTopTrends(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        tmp = Path(self.tmpdir.name)
        self.signals_path = tmp / "market_signals.json"
        self.earnings_signals_path = tmp / "market_signals_earnings.jsonl"
        self.earnings_signals_path.write_text("", encoding="utf-8")
        self._patches = [
            patch.object(tmi, "MARKET_SIGNALS_PATH", self.signals_path),
            patch.object(tmi, "MARKET_SIGNALS_EARNINGS_PATH", self.earnings_signals_path),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _write_items(self, items):
        self.signals_path.write_text(json.dumps({
            "fetched_at": "2026-09-29T00:00:00Z", "source_note": "fixture", "items": items,
        }), encoding="utf-8")

    def test_ranks_by_recent_volume_and_computes_direction(self):
        items = (
            [_item(category="pos", published_at="2026-09-20") for _ in range(3)]
            + [_item(category="payments", published_at="2026-09-20")]
        )
        self._write_items(items)
        result = rtt.compute_top_trends(window_days=30, top_n=5)
        names = [t["category"] for t in result["trends"]]
        self.assertEqual(names[0], "pos")  # higher recent volume ranks first

    def test_direction_emerging_when_no_prior_evidence(self):
        self._write_items([_item(category="pos", published_at="2026-09-20")])
        result = rtt.compute_top_trends(window_days=30, top_n=5)
        self.assertEqual(result["trends"][0]["direction"], "Emerging")

    def test_direction_accelerating_when_recent_exceeds_prior(self):
        recent = [_item(category="pos", published_at="2026-09-20") for _ in range(10)]
        prior = [_item(category="pos", published_at="2026-08-15") for _ in range(3)]
        self._write_items(recent + prior)
        result = rtt.compute_top_trends(window_days=30, top_n=5)
        self.assertEqual(result["trends"][0]["direction"], "Accelerating")

    def test_direction_fading_when_recent_below_prior(self):
        recent = [_item(category="pos", published_at="2026-09-20") for _ in range(2)]
        prior = [_item(category="pos", published_at="2026-08-15") for _ in range(10)]
        self._write_items(recent + prior)
        result = rtt.compute_top_trends(window_days=30, top_n=5)
        self.assertEqual(result["trends"][0]["direction"], "Fading")

    def test_evidence_prefers_substantive_headline_over_price_noise(self):
        noise = _item(
            category="pos", published_at="2026-09-27", title="[\U0001F4C8 PRICE MOVE] Fixture Co. ↓3.4%",
            signal_type="price_move", strategic_relevance="high",
        )
        real = _item(
            category="pos", published_at="2026-09-20", title="Fixture Co. launches new POS terminal",
            signal_type="vendor_expansion", strategic_relevance="medium",
        )
        self._write_items([noise, real])
        result = rtt.compute_top_trends(window_days=30, top_n=5)
        self.assertEqual(result["trends"][0]["evidence"][0]["headline"], "Fixture Co. launches new POS terminal")
        self.assertEqual(result["trends"][0]["qualitative_evidence_count"], 1)
        self.assertIsNone(result["trends"][0]["note"])

    def test_note_set_when_category_has_zero_qualitative_evidence(self):
        noise_items = [_item(
            category="pos", published_at="2026-09-20",
            title="[\U0001F4C8 PRICE MOVE] Fixture Co. ↓3.4%", signal_type="price_move",
        ) for _ in range(3)]
        self._write_items(noise_items)
        result = rtt.compute_top_trends(window_days=30, top_n=5)
        trend = result["trends"][0]
        self.assertEqual(trend["qualitative_evidence_count"], 0)
        self.assertIsNotNone(trend["note"])
        self.assertIn("no qualitative news", trend["note"])

    def test_evidence_items_use_the_same_allowlist_as_latest_news(self):
        """Never a second, divergent safety pass -- evidence citations
        must go through team_market_intelligence._allowlist_news_item,
        the same function Latest News already uses and tests."""
        self._write_items([_item(
            category="pos", published_at="2026-09-20",
            why_this_matters_to_todd="TODD-PRIVATE", recommended_action="TODD-PRIVATE",
        )])
        result = rtt.compute_top_trends(window_days=30, top_n=5)
        blob = json.dumps(result)
        self.assertNotIn("TODD-PRIVATE", blob)
        self.assertNotIn("why_this_matters_to_todd", blob)

    def test_write_and_read_round_trip(self):
        self._write_items([_item(category="pos", published_at="2026-09-20")])
        with tempfile.TemporaryDirectory() as out_tmp:
            out_path = Path(out_tmp) / "restaurant_tech_trends.json"
            with patch.object(rtt, "OUTPUT_PATH", out_path):
                result = rtt.compute_top_trends(window_days=30, top_n=5)
                rtt.write_trends(result)
                read_back = rtt.get_current_trends()
        self.assertEqual(read_back["trends"][0]["category"], "pos")

    def test_get_current_trends_empty_when_never_generated(self):
        with tempfile.TemporaryDirectory() as out_tmp:
            out_path = Path(out_tmp) / "does_not_exist.json"
            with patch.object(rtt, "OUTPUT_PATH", out_path):
                result = rtt.get_current_trends()
        self.assertEqual(result["trends"], [])


if __name__ == "__main__":
    unittest.main()
