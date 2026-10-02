#!/usr/bin/env python3
"""earnings_trend.py — read-only trend computation over earnings_monitor.py's
durable per-company earnings-history log (RB-2026-09-05, Predictive Market
Intelligence Phase 1).

Deliberately a separate module from earnings_monitor.py, not a new function
added to it — same domain-split this codebase already relies on elsewhere
(market_signals.py writes/intakes, market_signals_ri.py is a separate read/
assess step over the same rows). earnings_monitor.py owns writing to
system/earnings_history/earnings_calls.jsonl; this module only reads it.

Before this module, the only access path to a company's earnings history
(getCompanyEarningsHistory) returned the raw record list for a human or GPT
to eyeball -- nothing computed a trend from it. This module turns that raw,
already-durable log into real, falsifiable trend facts: which signal
dimensions are becoming more or less common for this company, whether a
dimension has never appeared before, and whether the company is generating
earnings-relevant signals more or less often than its own recent past.

Ground rule carried over from every other synthesis module in this
codebase: no LLM call, no invented claim -- if there isn't enough history
to say something real, say so plainly instead.

Phase 2 (2026-09-05): financial-health/momentum signal classification
(earnings_signal_taxonomy.classify_financial_health) added on top of the
Phase 1 dimension/frequency trend -- scoped down from the original
"predictive-signal vocabulary" ask after checking the real excerpt corpus
and finding it only supports financial-distress/growth-confidence/
traffic-pressure language, not "is this competitor vulnerable"/"is this
customer shopping for a new solution" (that stays in
vulnerability_taxonomy.py, sourced from job postings and news instead --
see earnings_signal_taxonomy.py's own docstring for why). Never an
open-ended judgment call -- purely which of a small, evidence-grounded set
of phrase categories the recent window's own excerpt text actually
contains.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from typing import Any

import earnings_monitor
import earnings_signal_taxonomy

DEFAULT_WINDOW_EVENTS = 4
MIN_EVENTS_FOR_TREND = 2

# RB-2026-09-05: comparing event COUNT between two windows that are each
# defined to hold exactly `window_events` records is tautological -- both
# counts are always equal by construction, so a count comparison can never
# show a real frequency change. Comparing the DATE SPAN each window covers
# is the real, non-tautological signal: the same number of events packed
# into a shorter span means they're happening more often now. A span-ratio
# threshold below this counts as noise, not a real change -- caught by
# hand-verifying this module's own output against real Starbucks data
# before shipping (both windows had exactly 4 events each, so the original
# count-based comparison always read "stable").
_STABLE_SPAN_RATIO_TOLERANCE = 0.20


def _span_days(rows: list[dict]) -> int | None:
    if len(rows) < 2:
        return None
    try:
        first = date.fromisoformat(rows[0].get("event_date") or "")
        last = date.fromisoformat(rows[-1].get("event_date") or "")
    except ValueError:
        return None
    return (last - first).days


def _frequency_label(recent_span: int | None, prior_span: int | None) -> str:
    if recent_span is None or prior_span is None or prior_span == 0:
        return "unknown"
    ratio = recent_span / prior_span
    if ratio <= 1 - _STABLE_SPAN_RATIO_TOLERANCE:
        return "increasing"
    if ratio >= 1 + _STABLE_SPAN_RATIO_TOLERANCE:
        return "decreasing"
    return "stable"


def _build_answer(
    company: str,
    total_events: int,
    new_dimensions: list[str],
    dimension_counts_recent: Counter,
    dimension_counts_prior: Counter,
    recent_count: int,
    prior_count: int,
    frequency_label: str,
    recent_span: int | None,
    prior_span: int | None,
    financial_health_signals: Counter,
) -> str:
    parts = [f"{company}: {total_events} recorded earnings event(s)."]

    if new_dimensions:
        parts.append(
            f"First-ever mention of {', '.join(sorted(new_dimensions))} in this company's recorded history."
        )

    shifting = []
    for dim in sorted(set(dimension_counts_recent) | set(dimension_counts_prior)):
        recent_n = dimension_counts_recent.get(dim, 0)
        prior_n = dimension_counts_prior.get(dim, 0)
        if recent_n != prior_n and prior_count and recent_count:
            direction = "up" if recent_n > prior_n else "down"
            shifting.append(f"{dim} {direction} ({prior_n}→{recent_n} events)")
    if shifting:
        parts.append("Dimension shift: " + "; ".join(shifting) + ".")

    if financial_health_signals:
        labels = [
            earnings_signal_taxonomy.FINANCIAL_HEALTH_CATEGORIES[cat]["label"]
            for cat in sorted(financial_health_signals)
        ]
        parts.append(
            f"Recent earnings language shows: {', '.join(labels)} "
            f"(in {sum(financial_health_signals.values())} of {recent_count} recent event(s))."
        )

    if frequency_label != "unknown" and recent_span is not None and prior_span is not None:
        parts.append(
            f"Earnings-relevant events are {frequency_label} in frequency "
            f"(last {recent_count} span {recent_span} days vs. {prior_span} days for the {prior_count} before that)."
        )

    return " ".join(parts)


def compute_entity_trend(company: str, *, window_events: int = DEFAULT_WINDOW_EVENTS) -> dict[str, Any]:
    """Compute a deterministic trend read over one company's earnings-history
    log. Never fabricates: with fewer than MIN_EVENTS_FOR_TREND records,
    returns status="insufficient_history" rather than a trend from one
    data point.

    Matches getCompanyEarningsHistory's own exact-match convention on the
    `company` field (earnings_monitor.get_company_earnings_history) so a
    trend computed here is always consistent with what that endpoint
    already returns for the same name.
    """
    rows = [r for r in earnings_monitor._load_earnings_history_rows() if r.get("company") == company]
    rows.sort(key=lambda r: r.get("event_date") or "")

    if len(rows) < MIN_EVENTS_FOR_TREND:
        return {
            "status": "insufficient_history",
            "company": company,
            "total_events": len(rows),
            "answer": (
                f"Only {len(rows)} recorded earnings event(s) for {company} -- "
                "not enough history to compute a trend."
            ),
        }

    recent_rows = rows[-window_events:]
    prior_rows = rows[max(0, len(rows) - 2 * window_events):-window_events] if len(rows) > window_events else []

    dimension_counts_recent: Counter = Counter()
    for r in recent_rows:
        dimension_counts_recent.update(r.get("signal_dimensions") or [])

    dimension_counts_prior: Counter = Counter()
    for r in prior_rows:
        dimension_counts_prior.update(r.get("signal_dimensions") or [])

    all_prior_and_earlier_dims: set[str] = set()
    for r in rows[: len(rows) - len(recent_rows)]:
        all_prior_and_earlier_dims.update(r.get("signal_dimensions") or [])
    new_dimensions = sorted(set(dimension_counts_recent) - all_prior_and_earlier_dims)

    recent_count = len(recent_rows)
    prior_count = len(prior_rows)
    recent_span = _span_days(recent_rows)
    prior_span = _span_days(prior_rows)
    frequency_label = _frequency_label(recent_span, prior_span)

    financial_health_signals: Counter = Counter()
    for r in recent_rows:
        for cat in earnings_signal_taxonomy.classify_financial_health(r.get("excerpt") or ""):
            financial_health_signals[cat] += 1

    answer = _build_answer(
        company, len(rows), new_dimensions, dimension_counts_recent, dimension_counts_prior,
        recent_count, prior_count, frequency_label, recent_span, prior_span,
        financial_health_signals,
    )

    return {
        "status": "ok",
        "company": company,
        "total_events": len(rows),
        "date_range": {"first": rows[0].get("event_date"), "last": rows[-1].get("event_date")},
        "dimension_counts_recent": dict(dimension_counts_recent),
        "dimension_counts_prior": dict(dimension_counts_prior),
        "new_dimensions": new_dimensions,
        "event_frequency_recent_vs_prior": frequency_label,
        "financial_health_signals": dict(financial_health_signals),
        "recent_event_count": recent_count,
        "prior_event_count": prior_count,
        "recent_span_days": recent_span,
        "prior_span_days": prior_span,
        "answer": answer,
        "source": str(earnings_monitor.EARNINGS_HISTORY_PATH),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
