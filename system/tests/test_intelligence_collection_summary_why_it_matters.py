"""
test_intelligence_collection_summary_why_it_matters.py

RB-DEFECT-2026-09-18 (handoff §Reporting contract: "Technical scoring and
system statuses appear in user-facing prose without explaining why a signal
matters"). daily_brief._intelligence_collection_summary_items()'s why_text
used to be nothing but raw accounting fields ("Records processed: N | New
records: N | Changed: N | Mutations generated: N") with zero plain-language
framing of why that evidence matters to Todd. The numbers themselves are
correct and intentional (CHARTER.md's "trust is earned by stats" tenet) --
this covers that the framing sentence was added without dropping any of the
underlying evidence.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import daily_brief as db  # noqa: E402


def _execution_report(**overrides) -> dict:
    base = {
        "refresh_status": "Success",
        "trust_score": 92,
        "generated_at": "2026-09-18T09:24:00+00:00",
        "records_processed": 1234,
        "records_added": 12,
        "records_changed": 7,
        "mutations_generated": 4,
        "sources_processed": ["email:personal", "calendar:bridgepoint"],
        "stale_sources": [],
        "processing_errors": [],
        "intelligence_health_dashboard": {
            "sources": [],
            "summary": {"healthy": 20, "stale": 1, "failed_or_partial": 0,
                        "unavailable": 1, "total_sources": 22},
        },
        "intelligence_cycle_statistics": {},
        "date": "2026-09-18",
    }
    base.update(overrides)
    return base


class TestIntelligenceCollectionSummaryWhyItMatters(unittest.TestCase):
    def test_why_text_explains_relevance_before_the_stats(self):
        report = {"execution_report": _execution_report()}
        items = db._intelligence_collection_summary_items(report)
        self.assertEqual(len(items), 1)
        why = items[0]["why_it_matters"]
        # Framing sentence present, in plain language, before the stats.
        self.assertTrue(why.lower().startswith("confirms this cycle's collection actually ran"))
        # The underlying evidence is not lost -- still present, verbatim.
        self.assertIn("Records processed: 1,234", why)
        self.assertIn("New records: 12", why)
        self.assertIn("Changed: 7", why)
        self.assertIn("Mutations generated: 4", why)

    def test_stale_sources_and_errors_still_appended(self):
        report = {"execution_report": _execution_report(
            stale_sources=["market_signals"],
            processing_errors=[{"step": "hubspot_ingest_scan"}],
        )}
        items = db._intelligence_collection_summary_items(report)
        why = items[0]["why_it_matters"]
        self.assertIn("Stale sources: market_signals", why)
        self.assertIn("Errors: hubspot_ingest_scan", why)

    def test_missing_execution_report_still_returns_one_item(self):
        items = db._intelligence_collection_summary_items({})
        self.assertEqual(len(items), 1)
        self.assertIn("Not Available", items[0]["title"])


if __name__ == "__main__":
    unittest.main()
