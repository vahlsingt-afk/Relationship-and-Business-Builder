"""Tests for render_intelligence_brief._render_hunter_office_manager_log."""
from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import render_intelligence_brief as rib  # noqa: E402


class TestHunterOfficeManagerLogSection(unittest.TestCase):
    def setUp(self):
        self._orig_path = rib.HUNTER_OFFICE_MANAGER_LOG_PATH

    def tearDown(self):
        rib.HUNTER_OFFICE_MANAGER_LOG_PATH = self._orig_path

    def _set_log(self, tmp_path, rows):
        path = tmp_path / "hunter_office_manager_log.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""), encoding="utf-8")
        rib.HUNTER_OFFICE_MANAGER_LOG_PATH = path

    def test_no_log_file_reports_nothing_deposited(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            rib.HUNTER_OFFICE_MANAGER_LOG_PATH = Path(tmp) / "missing.jsonl"
            out = rib._render_hunter_office_manager_log(date(2026, 10, 9))
        self.assertIn("## Hunter Office Manager", out)
        self.assertIn("No Hunter packets were deposited", out)
        self.assertIn("October 8", out)  # prior day

    def test_lists_each_distinct_company_deposited_the_prior_day(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._set_log(Path(tmp), [
                {"observed_at": "2026-10-08T12:00:00+00:00", "company": "McDonald's", "target_key": "company:brand-mcdonalds", "sms": {"sent": False}},
                {"observed_at": "2026-10-08T13:00:00+00:00", "company": "Subway", "target_key": "company:brand-subway", "sms": {"sent": False}},
            ])
            out = rib._render_hunter_office_manager_log(date(2026, 10, 9))
        self.assertIn("2 companies", out)
        self.assertIn("McDonald's", out)
        self.assertIn("Subway", out)

    def test_rows_from_other_days_are_excluded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._set_log(Path(tmp), [
                {"observed_at": "2026-10-06T12:00:00+00:00", "company": "Wendy's", "target_key": "company:brand-wendys"},
            ])
            out = rib._render_hunter_office_manager_log(date(2026, 10, 9))
        self.assertIn("No Hunter packets were deposited", out)
        self.assertNotIn("Wendy's", out)

    def test_notes_how_many_were_texted(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._set_log(Path(tmp), [
                {"observed_at": "2026-10-08T12:00:00+00:00", "company": "McDonald's", "target_key": "company:brand-mcdonalds", "sms": {"sent": True}},
                {"observed_at": "2026-10-08T13:00:00+00:00", "company": "Subway", "target_key": "company:brand-subway", "sms": {"sent": False}},
            ])
            out = rib._render_hunter_office_manager_log(date(2026, 10, 9))
        self.assertIn("Texted Todd for 1 of 2", out)

    def test_malformed_log_lines_are_skipped_not_fatal(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            path.write_text("not json\n" + json.dumps({"observed_at": "2026-10-08T12:00:00+00:00", "company": "McDonald's"}) + "\n", encoding="utf-8")
            rib.HUNTER_OFFICE_MANAGER_LOG_PATH = path
            out = rib._render_hunter_office_manager_log(date(2026, 10, 9))
        self.assertIn("McDonald's", out)


if __name__ == "__main__":
    unittest.main()
