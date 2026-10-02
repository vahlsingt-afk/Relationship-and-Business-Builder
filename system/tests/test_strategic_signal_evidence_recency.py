"""
test_strategic_signal_evidence_recency.py

Regression coverage: daily_brief.py built a strategic_industry_signals
item's source_refs from event["evidence"][:4] with no sort -- since evidence
accumulates in first-captured (oldest first) order, the "Source:" line
always cited among the OLDEST evidence for a long-running convergence
signal. Confirmed live: a signal reporting "+1 evidence since last
reported" (44 total, genuinely new today) rendered "Source:
https://www.restaurantbusinessonline.com/.../no-surprise-here-beverages-ai-
take-center-stage-restaurant-show" -- an article published 2026-05-20, 7
weeks stale, even though the delta driving today's mention was brand new.

_newest_evidence_first() sorts by published_at descending before the [:4]
slice, so the cited source reflects what's actually new.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402


class TestStrategicSignalEvidenceRecency(unittest.TestCase):
    def test_newest_evidence_sorted_first(self):
        evidence = [
            {"published_at": "2026-05-20", "url": "https://old.example.com/nra-show"},
            {"published_at": "2026-07-07", "url": "https://new.example.com/taco-bell-ai"},
            {"published_at": "2026-06-01", "url": "https://mid.example.com/yum-brands"},
        ]
        out = db._newest_evidence_first(evidence)
        self.assertEqual([e["url"] for e in out],
                          ["https://new.example.com/taco-bell-ai",
                           "https://mid.example.com/yum-brands",
                           "https://old.example.com/nra-show"])

    def test_first_four_after_sort_excludes_the_oldest_evidence_once_more_than_four_exist(self):
        evidence = [
            {"published_at": "2026-05-20", "url": "https://old1.example.com"},
            {"published_at": "2026-05-18", "url": "https://oldest.example.com"},
            {"published_at": "2026-07-08", "url": "https://newest.example.com"},
            {"published_at": "2026-06-15", "url": "https://mid1.example.com"},
            {"published_at": "2026-06-20", "url": "https://mid2.example.com"},
        ]
        top4 = db._newest_evidence_first(evidence)[:4]
        urls = [e["url"] for e in top4]
        self.assertIn("https://newest.example.com", urls)
        # The single oldest item (05-18) is the 5th most recent -- dropped by
        # the [:4] slice; everything less stale than it survives.
        self.assertNotIn("https://oldest.example.com", urls)
        self.assertIn("https://old1.example.com", urls)

    def test_missing_published_at_sorts_last_not_erroring(self):
        evidence = [
            {"url": "https://no-date.example.com"},
            {"published_at": "2026-07-01", "url": "https://has-date.example.com"},
        ]
        out = db._newest_evidence_first(evidence)
        self.assertEqual(out[0]["url"], "https://has-date.example.com")


if __name__ == "__main__":
    unittest.main()
