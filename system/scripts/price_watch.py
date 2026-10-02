#!/usr/bin/env python3
"""price_watch.py — daily stock price signal monitor for RB watchlist companies.

Pulls last 30 days of price/volume data for each public ticker in
earnings_calendar.yaml and flags:
  - Price move ≥ PRICE_THRESHOLD % (default 3%) vs prior close
  - Volume spike ≥ VOLUME_THRESHOLD x 20-day average
  - 52-week high/low proximity (within 3%)

Signals are written to system/inbox/market_signals_earnings.jsonl in the
same format as earnings_monitor.py so they surface in F: Watchlist and
E: Earnings sections without any pipeline changes.

Usage:
    python3 price_watch.py [--dry-run] [--threshold 3.0] [--verbose]
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import date, datetime, timezone
from pathlib import Path

# Suppress urllib3 OpenSSL warning from yfinance on macOS
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*OpenSSL.*")
warnings.filterwarnings("ignore", message=".*NotOpenSSLWarning.*")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core

try:
    import yfinance as yf
except ImportError:
    print("ERROR: yfinance not installed. Run: pip3 install yfinance", file=sys.stderr)
    sys.exit(1)

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip3 install pyyaml", file=sys.stderr)
    sys.exit(1)

EARNINGS_CALENDAR_PATH = core.SYSTEM_DIR / "earnings_calendar.yaml"
SIGNALS_PATH = core.SYSTEM_DIR / "inbox" / "market_signals_earnings.jsonl"
CACHE_PATH = core.SYSTEM_DIR / ".cache" / "price_watch_cache.json"

PRICE_THRESHOLD = 3.0    # % daily move to flag
VOLUME_THRESHOLD = 1.75  # x 20-day average volume to flag
HIGH_LOW_PROXIMITY = 3.0 # % from 52-week high/low to flag
LOOKBACK_DAYS = "30d"
HIST_PERIOD = "1y"       # needed for 52-week high/low


def _load_tickers() -> list[dict]:
    """Load public tickers from earnings_calendar.yaml."""
    if not EARNINGS_CALENDAR_PATH.exists():
        return []
    data = yaml.safe_load(EARNINGS_CALENDAR_PATH.read_text(encoding="utf-8")) or {}
    companies = data.get("companies") or []
    return [
        c for c in companies
        if c.get("ticker") and str(c["ticker"]).lower() not in ("private", "none", "")
    ]


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, default=str), encoding="utf-8")


def _load_existing_hashes() -> set[str]:
    """Load signal hashes already written to avoid duplicates."""
    hashes = set()
    if not SIGNALS_PATH.exists():
        return hashes
    for line in SIGNALS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            h = obj.get("_source_hash")
            if h:
                hashes.add(h)
        except Exception:
            pass
    return hashes


def _signal_hash(ticker: str, signal_type: str, today: date) -> str:
    import hashlib
    return hashlib.md5(f"{ticker}:{signal_type}:{today.isoformat()}".encode()).hexdigest()[:16]


def _pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / b * 100


def analyze_ticker(company: dict, today: date, verbose: bool = False) -> list[dict]:
    """Fetch price data and return list of signal dicts (may be empty)."""
    ticker = str(company["ticker"]).upper()
    name = str(company.get("name") or ticker)
    side = str(company.get("side") or "")
    category = str(company.get("category") or "")

    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=HIST_PERIOD)
        if hist.empty or len(hist) < 5:
            if verbose:
                print(f"  {ticker}: no data")
            return []
    except Exception as e:
        if verbose:
            print(f"  {ticker}: fetch error — {e}")
        return []

    signals = []
    closes = hist["Close"]
    volumes = hist["Volume"]

    today_row = hist.iloc[-1]
    prev_row = hist.iloc[-2] if len(hist) >= 2 else None

    # RB-DEFECT: signals were stamped with the wall-clock run date (`today`,
    # the function argument) rather than the actual trading date of the row
    # yfinance returned. If the feed hasn't posted the latest close yet
    # (observed: a Monday run reusing Friday's row across a long weekend),
    # the exact same price move gets relabeled with today's date and passes
    # the hash-based dedup as if it were a brand-new signal — the same
    # "PAR +3.8%, Shift4 +4.4%, McDonald's +4.2%..." set appeared for both
    # 2026-07-03 and 2026-07-06 in market_signals_earnings.jsonl. Using the
    # row's own date means a stale fetch is honestly dated as stale (and
    # naturally deduped against the prior day's real signal) instead of
    # being laundered into a fake "new" one.
    data_date = today_row.name.date() if hasattr(today_row.name, "date") else today

    today_close = float(today_row["Close"])
    today_vol = float(today_row["Volume"])
    prev_close = float(prev_row["Close"]) if prev_row is not None else today_close

    # 20-day average volume (excluding today)
    avg_vol_20 = float(volumes.iloc[-21:-1].mean()) if len(volumes) > 21 else float(volumes.mean())

    # 52-week high/low
    high_52 = float(closes.max())
    low_52 = float(closes.min())

    daily_pct = _pct(today_close, prev_close)
    vol_ratio = today_vol / avg_vol_20 if avg_vol_20 > 0 else 0.0

    pct_from_52h = _pct(today_close, high_52)
    pct_from_52l = _pct(today_close, low_52)

    if verbose:
        print(f"  {ticker}: ${today_close:.2f} ({daily_pct:+.1f}%) vol {vol_ratio:.1f}x "
              f"52w-H {pct_from_52h:+.1f}% 52w-L {pct_from_52l:+.1f}%")

    def _make_signal(signal_subtype: str, title: str, detail: str, priority: str) -> dict:
        today_str = data_date.isoformat()
        h = _signal_hash(ticker, signal_subtype, data_date)
        source_url = f"https://finance.yahoo.com/quote/{ticker}"
        return {
            "title": title,
            "url": source_url,
            "source_name": f"Price Watch ({ticker})",
            "source_type": "price_watch",
            "source_quality": "medium",
            "published_at": today_str,
            "company": name,
            "ticker": ticker,
            "entity_search_terms": [name, ticker],
            "entity_search_queries": [],
            "side": side,
            "category": category,
            "signal_type": signal_subtype,
            "pain_point_or_priority": detail,
            "strategic_relevance": "high",
            "affected_relationships_or_threads": [],
            "macro_force": "none",
            "restaurant_operator_impact": "",
            "restaurant_tech_vendor_implication": detail,
            "second_order_impact": detail,
            "relationship_opportunity": "",
            "why_this_matters_to_todd": (
                f"{name} is a tracked entity in RB. {detail}"
            ),
            "timing_priority": priority,
            "recommended_action": "act_today" if priority == "today" else "monitor",
            "confidence": "medium",
            "_source_hash": h,
            "_ingested_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "_price_watch": True,
        }

    direction = "↑" if daily_pct > 0 else "↓"

    # 1. Significant daily price move
    if abs(daily_pct) >= PRICE_THRESHOLD:
        detail = (
            f"{name} ({ticker}) moved {daily_pct:+.1f}% today "
            f"(${prev_close:.2f} → ${today_close:.2f}). "
            f"No confirmed news source — may indicate undisclosed event."
        )
        signals.append(_make_signal(
            "price_move",
            f"[📈 PRICE MOVE] {name} ({ticker}) {direction}{abs(daily_pct):.1f}% — possible undisclosed event",
            detail,
            "today",
        ))

    # 2. Volume spike (only flag if also a meaningful price move ≥ 1%)
    if vol_ratio >= VOLUME_THRESHOLD and abs(daily_pct) >= 1.0:
        detail = (
            f"{name} ({ticker}) trading {vol_ratio:.1f}x normal volume today "
            f"(${today_vol:,.0f} shares vs {avg_vol_20:,.0f} 20-day avg) "
            f"with {daily_pct:+.1f}% price move. Elevated activity without confirmed news."
        )
        signals.append(_make_signal(
            "volume_spike",
            f"[📊 VOLUME SPIKE] {name} ({ticker}) {vol_ratio:.1f}x normal volume — elevated activity",
            detail,
            "today",
        ))

    # 3. 52-week high proximity
    if -HIGH_LOW_PROXIMITY <= pct_from_52h <= 0:
        detail = (
            f"{name} ({ticker}) at ${today_close:.2f}, within {abs(pct_from_52h):.1f}% "
            f"of 52-week high (${high_52:.2f}). Momentum or pre-announcement positioning."
        )
        signals.append(_make_signal(
            "52w_high",
            f"[🔺 52W HIGH] {name} ({ticker}) near 52-week high — watch for announcement",
            detail,
            "this_week",
        ))

    # 4. 52-week low proximity (distress signal)
    if 0 <= pct_from_52l <= HIGH_LOW_PROXIMITY:
        detail = (
            f"{name} ({ticker}) at ${today_close:.2f}, within {pct_from_52l:.1f}% "
            f"of 52-week low (${low_52:.2f}). Possible distress, leadership instability, "
            f"or acquisition target positioning."
        )
        signals.append(_make_signal(
            "52w_low",
            f"[🔻 52W LOW] {name} ({ticker}) near 52-week low — distress or M&A signal",
            detail,
            "this_week",
        ))

    return signals


def _is_trading_day(d: date) -> bool:
    """Return True if d is a US market trading day (Mon–Fri, not a major holiday)."""
    if d.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    # Major US market holidays (fixed-date approximation; good enough for daily suppression)
    _MARKET_HOLIDAYS = {
        (1, 1),   # New Year's Day
        (7, 4),   # Independence Day
        (12, 25), # Christmas
        (11, 11), # Veterans Day (markets open, but included for safety)
    }
    if (d.month, d.day) in _MARKET_HOLIDAYS:
        return False
    return True


def run(dry_run: bool = False, threshold: float = PRICE_THRESHOLD,
        verbose: bool = False) -> dict:
    today = date.today()

    if not _is_trading_day(today):
        msg = f"✓ Price watch: skipped — {today.strftime('%A')} is not a trading day"
        print(msg)
        return {"ok": True, "date": today.isoformat(), "skipped": True, "reason": "non_trading_day"}

    companies = _load_tickers()
    if not companies:
        return {"ok": False, "error": "No tickers found in earnings_calendar.yaml"}

    existing_hashes = _load_existing_hashes()
    all_signals: list[dict] = []
    scanned = 0

    for company in companies:
        ticker = str(company.get("ticker") or "").upper()
        if verbose:
            print(f"Scanning {ticker} — {company.get('name', '')}")
        signals = analyze_ticker(company, today, verbose=verbose)
        scanned += 1
        for sig in signals:
            h = sig.get("_source_hash", "")
            if h not in existing_hashes:
                all_signals.append(sig)
                existing_hashes.add(h)

    if dry_run:
        print(f"\nScanned {scanned} tickers — {len(all_signals)} new signals")
        for sig in all_signals:
            print(f"  {sig['title']}")
            print(f"    {sig['pain_point_or_priority'][:120]}")
        return {"ok": True, "dry_run": True, "scanned": scanned, "signals": len(all_signals)}

    if all_signals:
        with SIGNALS_PATH.open("a", encoding="utf-8") as f:
            for sig in all_signals:
                f.write(json.dumps(sig, default=str) + "\n")

    result = {
        "ok": True,
        "date": today.isoformat(),
        "scanned": scanned,
        "signals_written": len(all_signals),
    }
    print(f"✓ Price watch: {scanned} tickers scanned, {len(all_signals)} signals written")
    return result


def main() -> int:
    p = argparse.ArgumentParser(description="RB price watch — watchlist stock signal monitor")
    p.add_argument("--dry-run", action="store_true", help="Print signals, don't write")
    p.add_argument("--threshold", type=float, default=PRICE_THRESHOLD,
                   help=f"Daily % move threshold (default {PRICE_THRESHOLD})")
    p.add_argument("--verbose", "-v", action="store_true", help="Print per-ticker details")
    args = p.parse_args()

    result = run(dry_run=args.dry_run, threshold=args.threshold, verbose=args.verbose)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
