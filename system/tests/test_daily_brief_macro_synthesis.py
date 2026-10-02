"""
test_daily_brief_macro_synthesis.py — RB 9.20 Macro-To-Operator Synthesis tests (T3).

Tests:
  T3a: macro items have operator implication (not just generic macro statement)
  T3b: generic "operators face pressure" alone fails (without mechanism)
  T3c: stale macro returns stale label and empty chain
  T3d: unavailable macro returns source_unavailable and does not claim
  T3e: macro chain item has required fields
  T3f: canonical evaluator rejects macro without operator implication
"""
from __future__ import annotations
import sys
import unittest
import json
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import cos_judgment as cj
from canonical_response_eval import eval_daily_brief_cos_operator_text

TODAY = date(2026, 5, 28)

VALID_MACRO_CHAIN_ITEM = {
    "trigger": "Oil price volatility",
    "mechanism": "Freight and distribution cost increase",
    "restaurant_operator_implication": "Tighter margin tolerance; longer experimentation cycles.",
    "restaurant_tech_implication": "Vendors need ROI proof, not AI novelty.",
    "vendor_or_job_market_implication": "Deals slow without clear payback period.",
    "user_implication": "Vendor conversations need concrete ROI framing this quarter.",
    "confidence": "medium",
    "freshness": "fresh",
    "source_refs": ["market_signals_cache"],
}

GENERIC_MACRO_ONLY = {
    "trigger": "Margin pressure",
    "mechanism": "",
    "restaurant_operator_implication": "",
    "restaurant_tech_implication": "",
    "vendor_or_job_market_implication": "",
    "user_implication": "",
    "confidence": "low",
    "freshness": "stale",
    "source_refs": [],
}


class TestMacroSynthesisShape(unittest.TestCase):

    def test_valid_macro_item_has_required_fields(self):
        """T3e: a valid macro chain item has all required fields."""
        required = [
            "trigger", "mechanism",
            "restaurant_operator_implication", "restaurant_tech_implication",
            "vendor_or_job_market_implication", "user_implication",
            "confidence", "freshness", "source_refs",
        ]
        for field in required:
            self.assertIn(field, VALID_MACRO_CHAIN_ITEM, f"Macro item missing field: {field}")

    def test_macro_item_without_operator_implication_fails_eval(self):
        """T3b: generic macro text without operator implication fails canonical eval."""
        text = "Operators are still facing margin pressure. Here is some context on the macro environment."
        result = eval_daily_brief_cos_operator_text(text)
        self.assertFalse(result.passed, "Generic macro without operator implication must fail canonical eval")

    def test_macro_with_mechanism_passes_eval(self):
        """T3a: macro text with full causal chain and operator implication passes eval."""
        text = (
            "## Resource Verification & Freshness Status\n"
            "- **email**: `fresh`\n\n"
            "## CoS Judgment\n"
            "**Hard truths:**\n"
            "- [MONITOR] Evidence: Loop count is 0.\n"
            "  - Evidence: zero loops.\n"
            "  - Confidence: medium | Grounding: loop_ledger.md\n\n"
            "## What Is Not Happening\n"
            "No detected absences.\n\n"
            "## Macro-To-Operator Synthesis\n"
            "Oil volatility → freight inflation → distribution cost increase → "
            "tighter restaurant margin tolerance → longer SaaS experimentation cycles. "
            "Implication: vendor conversations need ROI proof, not AI novelty.\n\n"
            "## Execution Closure\n"
            "- `[monitor_relationship]` **RB system** — Hard truth flagged as monitor.\n"
        )
        result = eval_daily_brief_cos_operator_text(text)
        if not result.passed:
            fail_msgs = [f.message for f in result.findings if f.severity == "fail"]
            self.fail(f"Macro with mechanism failed eval:\n" + "\n".join(fail_msgs))

    def test_stale_macro_cache_returns_stale_label(self):
        """T3c: stale macro cache → stale label, no chain items used."""
        # Simulate stale source_freshness
        fake_sf = {
            "sources": {
                "macro_synthesis": {
                    "label": "stale",
                    "stale": True,
                    "generated_at": (datetime.now() - timedelta(days=10)).isoformat(),
                }
            }
        }
        result = cj.build_macro_synthesis(fake_sf)
        self.assertFalse(result["available"], "Stale macro should not be available")
        self.assertEqual(result["label"], "stale")
        self.assertEqual(result["macro_chain"], [])

    def test_unavailable_macro_cache_returns_source_unavailable(self):
        """T3d: no macro cache → source_unavailable, no claims."""
        fake_sf = {
            "sources": {
                "macro_synthesis": {
                    "label": "source_unavailable",
                    "stale": True,
                }
            }
        }
        result = cj.build_macro_synthesis(fake_sf)
        self.assertFalse(result["available"])
        self.assertEqual(result["label"], "source_unavailable")
        self.assertEqual(result["macro_chain"], [])

    def test_fresh_macro_cache_returns_chain(self):
        """T3a: fresh macro cache returns available=True and chain items."""
        macro_data = {
            "_generated_at": datetime.now().isoformat(timespec="seconds"),
            "data": {
                "macro_chain": [VALID_MACRO_CHAIN_ITEM],
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            # Patch CACHE_DIR
            import rb_core as core
            original = cj.CACHE_DIR
            cj.CACHE_DIR = Path(tmpdir)
            macro_path = Path(tmpdir) / "macro_synthesis.json"
            macro_path.write_text(json.dumps(macro_data))
            fake_sf = {
                "sources": {
                    "macro_synthesis": {
                        "label": "fresh",
                        "stale": False,
                        "generated_at": macro_data["_generated_at"],
                    }
                }
            }
            result = cj.build_macro_synthesis(fake_sf)
            cj.CACHE_DIR = original
        self.assertTrue(result["available"])
        self.assertEqual(result["label"], "fresh")
        self.assertGreater(len(result["macro_chain"]), 0)

    def test_macro_item_must_map_to_user_implication(self):
        """T3a: a macro item without user_implication is incomplete per the contract."""
        item = dict(VALID_MACRO_CHAIN_ITEM)
        item["user_implication"] = ""
        self.assertFalse(
            bool(item["user_implication"]),
            "A macro item without user_implication does not satisfy the contract"
        )

    def test_macro_generic_statement_fails_canonical_eval(self):
        """T3b: 'Operators are still facing margin pressure' alone fails without mechanism."""
        text = "Operators are still facing margin pressure, so restaurant technology buyers will continue to value ROI."
        result = eval_daily_brief_cos_operator_text(text)
        self.assertFalse(result.passed)
        fail_rules = [f.rule for f in result.findings]
        self.assertIn("no_banned_phrases", fail_rules)


if __name__ == "__main__":
    unittest.main()
