"""
test_proof_dashboard_delta_table.py

Regression coverage: canonical Intelligence Brief spec (INTELLIGENCE_BRIEF_
CANONICAL.md) requires Section J (Proof Dashboard, always last) to render a
personal-data Today/Yesterday/Delta table, a news-scan row, and per-source
health — "counts prove work was done." The prior implementation
("What RB Did Overnight") was three flat prose bullets with no yesterday
comparison and no per-source breakdown at all — a reader had no way to
verify anything against a prior day, which is the whole point of a trust
anchor.

Fixed: _render_proof_dashboard now reads collection_scan_completed audit
events (via audit_log.load_events) for today and yesterday, builds a real
delta table for emails/SMS/calls/calendar/mutations, a news-scan row from
the actual rendered headline-section counts, and a source-health block from
the scan's per-source status/trust_score.
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402
import audit_log  # noqa: E402


def _scan_event(event_date: str, source_detail: list[dict], mutations: int = 5, trust_score: int = 90) -> dict:
    return {
        "event_type": "collection_scan_completed",
        "timestamp": f"{event_date}T10:00:00+00:00",
        "date": event_date,
        "mutations_generated": mutations,
        "trust_score": trust_score,
        "source_detail": source_detail,
    }


def _sources(email_p=10, email_b=5, messages=100, calls=3, cal_p=2, cal_b=1) -> list[dict]:
    return [
        {"source_key": "email:personal", "records_processed": email_p, "status": "Healthy", "trust_score": 100},
        {"source_key": "email:bridgepoint", "records_processed": email_b, "status": "Healthy", "trust_score": 100},
        {"source_key": "messages", "records_processed": messages, "status": "Healthy", "trust_score": 100},
        {"source_key": "calls", "records_processed": calls, "status": "Healthy", "trust_score": 100},
        {"source_key": "calendar:personal", "records_processed": cal_p, "status": "Healthy", "trust_score": 100},
        {"source_key": "calendar:bridgepoint", "records_processed": cal_b, "status": "Healthy", "trust_score": 100},
        {"source_key": "market_signals", "records_processed": 1, "status": "Stale", "trust_score": 25},
    ]


class TestProofDashboardDeltaTable(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        self.audit_dir = Path(self.tmpdir.name)
        self._patch_dir = patch.object(audit_log, "AUDIT_DIR", self.audit_dir)
        self._patch_dir.start()
        self._patch_index = patch.object(audit_log, "INDEX_PATH", self.audit_dir / "index.json")
        self._patch_index.start()

    def tearDown(self):
        self._patch_index.stop()
        self._patch_dir.stop()
        self.tmpdir.cleanup()

    def _write_events(self, events: list[dict]) -> None:
        by_month: dict[str, list[dict]] = {}
        for e in events:
            month = e["date"][:7]
            by_month.setdefault(month, []).append(e)
        for month, evs in by_month.items():
            path = self.audit_dir / f"{month}.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in evs) + "\n", encoding="utf-8")

    def test_delta_table_shows_real_deltas_from_scan_events(self):
        self._write_events([
            _scan_event("2026-07-06", _sources(email_p=10, email_b=5, messages=100, calls=3), mutations=8),
            _scan_event("2026-07-07", _sources(email_p=12, email_b=6, messages=110, calls=4), mutations=10),
        ])
        with patch.object(rib.core, "BASELINE_PATH", Path("/nonexistent/baseline.json")):
            out = rib._render_proof_dashboard({}, None, target_date=date(2026, 7, 7))
        self.assertIn("## J: Proof Dashboard", out)
        self.assertIn("Emails (threads)             18       15           +3", out)
        self.assertIn("SMS (conversations)          110      100          +10", out)
        self.assertIn("Mutations declared           10       8            +2", out)

    def test_missing_yesterday_scan_renders_em_dash_not_zero(self):
        self._write_events([
            _scan_event("2026-07-07", _sources(), mutations=5),
        ])
        with patch.object(rib.core, "BASELINE_PATH", Path("/nonexistent/baseline.json")):
            out = rib._render_proof_dashboard({}, None, target_date=date(2026, 7, 7))
        # No 2026-07-06 event exists, so yesterday columns must show "—", not 0
        self.assertIn("—", out)
        self.assertNotIn("Emails (threads)             15       0", out)

    def test_source_health_lists_unhealthy_sources_with_trust_score(self):
        self._write_events([_scan_event("2026-07-07", _sources(), mutations=5, trust_score=83)])
        with patch.object(rib.core, "BASELINE_PATH", Path("/nonexistent/baseline.json")):
            out = rib._render_proof_dashboard({}, None, target_date=date(2026, 7, 7))
        self.assertIn("Source Health", out)
        self.assertIn("market_signals", out)
        self.assertIn("Trust: 83%", out)

    def test_news_scan_row_reflects_actual_section_contents(self):
        self._write_events([_scan_event("2026-07-07", _sources())])
        sections = {
            "world_national_headlines": [
                {"extras": {"source_name": "BBC World News"}},
                {"extras": {"source_name": "NPR News"}},
            ],
        }
        with patch.object(rib.core, "BASELINE_PATH", Path("/nonexistent/baseline.json")):
            out = rib._render_proof_dashboard(sections, None, target_date=date(2026, 7, 7))
        self.assertIn("World: 2 articles · BBC World News, NPR News", out)

    def test_no_scan_record_at_all_shows_explicit_warning_not_silence(self):
        with patch.object(rib.core, "BASELINE_PATH", Path("/nonexistent/baseline.json")):
            out = rib._render_proof_dashboard({}, None, target_date=date(2026, 7, 7))
        self.assertIn("No collection scan record found for today", out)


if __name__ == "__main__":
    unittest.main()
