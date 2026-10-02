"""
test_price_watch_stale_data_date.py

Regression coverage: price_watch.py stamped every signal with the wall-clock
run date (the `today` argument) instead of the actual trading date of the
row yfinance returned. Observed live: market_signals_earnings.jsonl had the
identical PAR/Shift4/McDonald's/Yum/QSR price-move set recorded twice —
once correctly dated 2026-07-03 (Friday, the last trading day before the
July 4th weekend) and again dated 2026-07-06 (Monday) — because a Monday
run picked up a data feed that hadn't posted fresh closes yet, reused
Friday's row, and relabeled it as today's signal. The hash-based dedup
(ticker + signal_type + date) couldn't catch this because the wrong date
was baked into the hash itself.

analyze_ticker() now derives published_at/hash from the fetched row's own
date, not the wall-clock date — a stale fetch is honestly dated as stale
and naturally deduped against the prior day's real signal instead of being
laundered into a fake "new" one.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import price_watch as pw  # noqa: E402

try:
    import pandas as pd
    _HAS_PANDAS = True
except ImportError:
    _HAS_PANDAS = False


def _make_history(dates: list[str], closes: list[float], volumes: list[float]):
    idx = pd.to_datetime(dates)
    return pd.DataFrame({"Close": closes, "Volume": volumes}, index=idx)


@unittest.skipUnless(_HAS_PANDAS, "pandas not installed")
class TestPriceWatchStaleDataDate(unittest.TestCase):
    def test_signal_dated_by_actual_row_not_wall_clock(self):
        """The exact bug: script runs on a Monday (today=2026-07-06) but the
        feed's last row is still Friday's (2026-07-03) — the emitted signal
        must be dated 2026-07-03, not 2026-07-06."""
        # 30 days of flat history ending Friday 2026-07-03, then a >=3% jump
        # on the last (stale) row so a price_move signal actually fires.
        dates = [f"2026-06-{d:02d}" for d in range(4, 31)] + ["2026-07-01", "2026-07-02", "2026-07-03"]
        closes = [100.0] * (len(dates) - 1) + [104.0]  # +4% on the last row
        volumes = [1_000_000.0] * len(dates)
        hist = _make_history(dates, closes, volumes)

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist

        company = {"ticker": "TEST", "name": "Test Co", "side": "vendor", "category": "restaurant_tech"}

        with patch.object(pw.yf, "Ticker", return_value=mock_ticker):
            signals = pw.analyze_ticker(company, today=date(2026, 7, 6), verbose=False)

        self.assertTrue(signals, "expected at least one price_move signal")
        for sig in signals:
            self.assertEqual(sig["published_at"], "2026-07-03")

    def test_fresh_data_still_dated_today(self):
        dates = [f"2026-06-{d:02d}" for d in range(4, 31)] + ["2026-07-01", "2026-07-02", "2026-07-06"]
        closes = [100.0] * (len(dates) - 1) + [104.0]
        volumes = [1_000_000.0] * len(dates)
        hist = _make_history(dates, closes, volumes)

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = hist
        company = {"ticker": "TEST", "name": "Test Co", "side": "vendor", "category": "restaurant_tech"}

        with patch.object(pw.yf, "Ticker", return_value=mock_ticker):
            signals = pw.analyze_ticker(company, today=date(2026, 7, 6), verbose=False)

        self.assertTrue(signals)
        for sig in signals:
            self.assertEqual(sig["published_at"], "2026-07-06")


if __name__ == "__main__":
    unittest.main()
