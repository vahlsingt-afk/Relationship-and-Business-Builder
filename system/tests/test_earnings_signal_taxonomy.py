"""
test_earnings_signal_taxonomy.py — RB-2026-09-05, Predictive Market
Intelligence Phase 2: financial-health signal classification over real
earnings-excerpt text.

Every pattern here was grounded in real hits against the actual
earnings_calls.jsonl corpus before being written (see
earnings_signal_taxonomy.py's own docstring) -- these tests pin the exact
real phrases found (e.g. Papa Johns' real "Accelerate Transformation
Strategy" / "Comparable Sales Decreased" 2026-08-06 excerpt) as permanent
regressions, not just synthetic examples.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import earnings_signal_taxonomy as est  # noqa: E402


class TestClassifyFinancialHealth:
    def test_empty_text_returns_empty_list(self):
        assert est.classify_financial_health("") == []
        assert est.classify_financial_health(None) == []

    def test_no_matching_language_returns_empty_list(self):
        text = "The board of directors declared a quarterly cash dividend of $0.19 per share."
        assert est.classify_financial_health(text) == []

    def test_real_papa_johns_excerpt_classifies_both_categories(self):
        # RB-2026-09-05: real excerpt text, Papa Johns 2026-08-06 filing.
        text = (
            "Diluted EPS of $0.46 Company Shifts Capital Allocation to "
            "Accelerate Transformation Strategy; Suspends Dividend "
            "Comparable Sales Decreased 2.1% in North America"
        )
        result = est.classify_financial_health(text)
        assert "growth_confidence" in result
        assert "traffic_pressure" in result

    def test_financial_distress_language(self):
        text = "The company recorded a goodwill impairment charge and announced a restructuring plan."
        result = est.classify_financial_health(text)
        assert result == ["financial_distress"]

    def test_growth_confidence_language(self):
        text = "We delivered a record quarter with 16% unit growth and 102 net new openings."
        result = est.classify_financial_health(text)
        assert result == ["growth_confidence"]

    def test_traffic_pressure_language(self):
        text = "Comparable store sales declined amid a challenging consumer environment."
        result = est.classify_financial_health(text)
        assert result == ["traffic_pressure"]

    def test_never_invents_vendor_evaluation_category(self):
        # RB-2026-09-05: deliberately scoped out -- confirmed zero real hits
        # in the actual corpus, that signal lives in vulnerability_taxonomy.py
        # instead. This category must never exist here.
        assert "vendor_evaluation" not in est.FINANCIAL_HEALTH_CATEGORIES
        assert "customer_shopping" not in est.FINANCIAL_HEALTH_CATEGORIES

    def test_category_labels_are_human_readable(self):
        for name, cat in est.FINANCIAL_HEALTH_CATEGORIES.items():
            assert cat["label"], f"{name} missing a label"
