"""
test_market_signals_ranking.py — RB 9.71 item 4/5: market_signals top-N
selection reserves room for non-SEC items.

`rank()`'s priority_score favors SEC 8-K filings (source_quality + recency
push them to ~400-410) over curated vertical-trade items (~395-400) even
when the curated items are fresher and tied to active threads. With
top_n=7 (the morning_headlines cut), a plain `ranked[:top_n]` slice was
filled entirely with 8-K filings. `_select_top()` reserves up to
`_DOMINANT_RESERVE_SLOTS` slots for non-8-K items when they exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import market_signals as ms  # noqa: E402


def _items(n, source_type, base_score):
    return [
        {"title": f"{source_type} item {i}", "source_type": source_type,
         "priority_score": base_score - i}
        for i in range(n)
    ]


def test_select_top_reserves_slots_for_non_dominant_items():
    ranked = _items(10, "sec_edgar_8k", 410) + _items(3, "vertical_trade", 400)
    top = ms._select_top(ranked, top_n=7)
    assert len(top) == 7
    source_types = [it["source_type"] for it in top]
    assert source_types.count("vertical_trade") == 3
    assert source_types.count("sec_edgar_8k") == 4


def test_select_top_backfills_when_no_non_dominant_items():
    ranked = _items(10, "sec_edgar_8k", 410)
    top = ms._select_top(ranked, top_n=7)
    assert len(top) == 7
    assert all(it["source_type"] == "sec_edgar_8k" for it in top)


def test_select_top_does_not_truncate_below_top_n_when_few_dominant():
    ranked = _items(2, "sec_edgar_8k", 410) + _items(10, "vertical_trade", 400)
    top = ms._select_top(ranked, top_n=7)
    assert len(top) == 7
    source_types = [it["source_type"] for it in top]
    assert source_types.count("sec_edgar_8k") == 2
    assert source_types.count("vertical_trade") == 5


def test_select_top_preserves_order_within_each_group():
    ranked = _items(10, "sec_edgar_8k", 410) + _items(5, "vertical_trade", 400)
    top = ms._select_top(ranked, top_n=7)
    titles = [it["title"] for it in top]
    assert titles[:4] == [f"sec_edgar_8k item {i}" for i in range(4)]
    assert titles[4:] == [f"vertical_trade item {i}" for i in range(3)]


def test_select_top_zero_returns_empty():
    ranked = _items(5, "sec_edgar_8k", 410)
    assert ms._select_top(ranked, top_n=0) == []
