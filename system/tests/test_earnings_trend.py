"""
test_earnings_trend.py — RB-2026-09-05, Predictive Market Intelligence
Phase 1: dimension-mix/frequency trend computation over earnings_monitor.py's
durable earnings-history log.

Uses real production data (system/earnings_history/earnings_calls.jsonl,
804 real records across 39 tracked companies) where possible, rather than
only synthetic fixtures -- consistent with this project's own testing
discipline. Synthetic fixtures cover the honesty/edge-case guarantees a
real file can't reliably exercise on demand (insufficient history, a
genuine first-ever dimension, a company with real 0 records).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import earnings_monitor as em  # noqa: E402
import earnings_trend as et  # noqa: E402

# conftest.py deliberately redirects em.EARNINGS_HISTORY_PATH to an empty
# per-session temp file for every test (RB-DEFECT-066 isolation), so tests
# that want the real production log -- same reasoning and same pattern as
# test_master_account_plan.py's REAL_WORLDPAY_FILE / test_real_..._if_present
# -- must explicitly patch back to the real path and skip if it's not
# present in this environment, rather than relying on the module constant.
REAL_EARNINGS_HISTORY_FILE = ROOT / "system" / "earnings_history" / "earnings_calls.jsonl"


class TestRealProductionData:
    """Real earnings_calls.jsonl -- confirms the module actually works
    against what's really on disk, not just a synthetic fixture."""

    def _skip_if_absent(self):
        if not REAL_EARNINGS_HISTORY_FILE.exists():
            import pytest
            pytest.skip("real earnings_calls.jsonl not present in this environment")

    def test_starbucks_real_data_is_ok_status_with_real_numbers(self):
        self._skip_if_absent()
        with patch.object(em, "EARNINGS_HISTORY_PATH", REAL_EARNINGS_HISTORY_FILE):
            result = et.compute_entity_trend("Starbucks Corp")
        assert result["status"] == "ok"
        assert result["total_events"] > 10
        assert result["event_frequency_recent_vs_prior"] in ("increasing", "decreasing", "stable", "unknown")
        assert result["source"].endswith("earnings_calls.jsonl")

    def test_unknown_company_returns_insufficient_history(self):
        self._skip_if_absent()
        with patch.object(em, "EARNINGS_HISTORY_PATH", REAL_EARNINGS_HISTORY_FILE):
            result = et.compute_entity_trend("Definitely Not A Tracked Company Inc")
        assert result["status"] == "insufficient_history"
        assert result["total_events"] == 0

    def test_real_papa_johns_financial_health_signal_is_detected(self):
        # RB-2026-09-05, Phase 2: real 2026-08-06 excerpt classifies as both
        # growth_confidence ("Accelerate") and traffic_pressure ("Comparable
        # Sales Decreas..."), pinned as a permanent regression.
        self._skip_if_absent()
        with patch.object(em, "EARNINGS_HISTORY_PATH", REAL_EARNINGS_HISTORY_FILE):
            result = et.compute_entity_trend("Papa Johns International Inc")
        assert result["status"] == "ok"
        assert "growth_confidence" in result["financial_health_signals"]
        assert "Growth Confidence" in result["answer"]

    def test_frequency_is_not_tautological_across_real_companies(self):
        """RB-2026-09-05 regression: an earlier version compared event COUNT
        between two equal-sized windows, which is always equal by
        construction and therefore always read 'stable'. Confirmed against
        real data that at least one real tracked company shows a genuine
        non-stable read once frequency is computed from date span instead."""
        self._skip_if_absent()
        companies = ["Starbucks Corp", "Dine Brands", "Portillo's Inc.",
                     "Jack In The Box Inc", "Yum Brands"]
        with patch.object(em, "EARNINGS_HISTORY_PATH", REAL_EARNINGS_HISTORY_FILE):
            labels = {c: et.compute_entity_trend(c)["event_frequency_recent_vs_prior"] for c in companies}
        assert any(v in ("increasing", "decreasing") for v in labels.values()), labels


class TestSyntheticEdgeCases:
    def setUp_rows(self, rows: list[dict]) -> Path:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8")
        for r in rows:
            tmp.write(json.dumps(r) + "\n")
        tmp.close()
        return Path(tmp.name)

    def _row(self, date_str: str, dims: list[str], excerpt: str = "") -> dict:
        return {"company": "Synth Co", "event_date": date_str, "signal_dimensions": dims, "excerpt": excerpt}

    def test_single_event_is_insufficient_history(self):
        path = self.setUp_rows([self._row("2026-01-01", ["Financial"])])
        with patch.object(em, "EARNINGS_HISTORY_PATH", path):
            result = et.compute_entity_trend("Synth Co")
        assert result["status"] == "insufficient_history"
        assert result["total_events"] == 1

    def test_zero_events_is_insufficient_history(self):
        path = self.setUp_rows([self._row("2026-01-01", ["Financial"])])
        with patch.object(em, "EARNINGS_HISTORY_PATH", path):
            result = et.compute_entity_trend("A Company With No Records At All")
        assert result["status"] == "insufficient_history"
        assert result["total_events"] == 0

    def test_new_dimension_never_seen_before_is_detected(self):
        rows = [
            self._row("2025-01-01", ["Financial"]),
            self._row("2025-04-01", ["Financial"]),
            self._row("2025-07-01", ["Financial", "Technology"]),
            self._row("2025-10-01", ["Financial"]),
        ]
        path = self.setUp_rows(rows)
        with patch.object(em, "EARNINGS_HISTORY_PATH", path):
            result = et.compute_entity_trend("Synth Co", window_events=2)
        assert result["status"] == "ok"
        assert "Technology" in result["new_dimensions"]
        assert "Financial" not in result["new_dimensions"]

    def test_dimension_that_recurred_is_not_flagged_as_new(self):
        rows = [
            self._row("2025-01-01", ["Technology"]),
            self._row("2025-04-01", ["Financial"]),
            self._row("2025-07-01", ["Financial"]),
            self._row("2025-10-01", ["Technology"]),
        ]
        path = self.setUp_rows(rows)
        with patch.object(em, "EARNINGS_HISTORY_PATH", path):
            result = et.compute_entity_trend("Synth Co", window_events=2)
        assert result["new_dimensions"] == []

    def test_events_getting_more_frequent_reads_increasing(self):
        """Recent window's date span is much shorter than the prior window's
        for the same event count -- events are happening more often now."""
        rows = [
            self._row("2024-01-01", ["Financial"]),
            self._row("2024-07-01", ["Financial"]),  # prior window spans ~6mo
            self._row("2025-01-01", ["Financial"]),
            self._row("2026-08-01", ["Financial"]),
            self._row("2026-08-15", ["Financial"]),  # recent window spans ~2 weeks
            self._row("2026-08-30", ["Financial"]),
        ]
        path = self.setUp_rows(rows)
        with patch.object(em, "EARNINGS_HISTORY_PATH", path):
            result = et.compute_entity_trend("Synth Co", window_events=3)
        assert result["event_frequency_recent_vs_prior"] == "increasing"

    def test_events_getting_less_frequent_reads_decreasing(self):
        rows = [
            self._row("2026-01-01", ["Financial"]),
            self._row("2026-01-05", ["Financial"]),
            self._row("2026-01-10", ["Financial"]),
            self._row("2026-03-01", ["Financial"]),
            self._row("2026-06-01", ["Financial"]),
            self._row("2026-09-01", ["Financial"]),
        ]
        path = self.setUp_rows(rows)
        with patch.object(em, "EARNINGS_HISTORY_PATH", path):
            result = et.compute_entity_trend("Synth Co", window_events=3)
        assert result["event_frequency_recent_vs_prior"] == "decreasing"

    def test_no_prior_window_reads_frequency_unknown(self):
        """Fewer than 2x window_events total -- there's no prior window to
        compare against, so frequency must say so honestly, not guess."""
        rows = [
            self._row("2026-01-01", ["Financial"]),
            self._row("2026-04-01", ["Financial"]),
            self._row("2026-07-01", ["Financial"]),
        ]
        path = self.setUp_rows(rows)
        with patch.object(em, "EARNINGS_HISTORY_PATH", path):
            result = et.compute_entity_trend("Synth Co", window_events=4)
        assert result["event_frequency_recent_vs_prior"] == "unknown"

    def test_answer_never_mentions_frequency_when_unknown(self):
        rows = [
            self._row("2026-01-01", ["Financial"]),
            self._row("2026-04-01", ["Financial"]),
        ]
        path = self.setUp_rows(rows)
        with patch.object(em, "EARNINGS_HISTORY_PATH", path):
            result = et.compute_entity_trend("Synth Co", window_events=4)
        assert "frequency" not in result["answer"].lower()

    def test_no_financial_health_language_is_empty_dict_not_omitted(self):
        rows = [
            self._row("2026-01-01", ["Financial"], "Quarterly dividend declared."),
            self._row("2026-04-01", ["Financial"], "Quarterly dividend declared."),
        ]
        path = self.setUp_rows(rows)
        with patch.object(em, "EARNINGS_HISTORY_PATH", path):
            result = et.compute_entity_trend("Synth Co")
        assert result["financial_health_signals"] == {}
        assert "financial_health_signals" in result

    def test_financial_health_language_in_recent_window_surfaces_in_answer(self):
        rows = [
            self._row("2026-01-01", ["Financial"], "Ordinary results."),
            self._row("2026-04-01", ["Financial"], "We delivered a record quarter with strong unit growth."),
        ]
        path = self.setUp_rows(rows)
        with patch.object(em, "EARNINGS_HISTORY_PATH", path):
            result = et.compute_entity_trend("Synth Co")
        assert result["financial_health_signals"] == {"growth_confidence": 1}
        assert "Growth Confidence" in result["answer"]
