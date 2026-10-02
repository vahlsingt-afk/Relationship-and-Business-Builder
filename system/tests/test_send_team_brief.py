"""
test_send_team_brief.py

Regression coverage: send_brief_email.send_team_brief() emails the
team-facing Intelligence Brief edition (system/briefs/{date}-team-
intelligence-brief.md, produced by render_intelligence_brief.
render_team_edition) to the same configured recipient as the personal
briefs -- Todd decides whether/how to forward it to his team himself.
No prior test file existed for send_brief_email.py; this covers the pure
branching logic (missing file, missing recipient, dry-run preview)
without touching real SMTP, mirroring the existing send()/send_linkedin_
reports() control flow this function was modeled on.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import send_brief_email as sbe  # noqa: E402


class TestSendTeamBrief(unittest.TestCase):
    def test_missing_file_reports_not_found(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sbe, "BRIEFS_DIR", Path(tmpdir)):
                result = sbe.send_team_brief(date(2026, 7, 10))
        self.assertFalse(result["sent"])
        self.assertEqual(result["status"], "no_briefs_found")

    def test_missing_recipient_reports_no_recipient(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            brief_path = Path(tmpdir) / "2026-07-10-team-intelligence-brief.md"
            brief_path.write_text("# Restaurant & Payments Industry Brief\n\nSome content.",
                                   encoding="utf-8")
            with patch.object(sbe, "BRIEFS_DIR", Path(tmpdir)), \
                 patch.object(sbe, "_resolve_recipient", return_value=None):
                result = sbe.send_team_brief(date(2026, 7, 10))
        self.assertFalse(result["sent"])
        self.assertEqual(result["status"], "no_recipient")

    def test_dry_run_previews_without_sending(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            brief_path = Path(tmpdir) / "2026-07-10-team-intelligence-brief.md"
            brief_path.write_text("# Restaurant & Payments Industry Brief\n\nSome content.",
                                   encoding="utf-8")
            with patch.object(sbe, "BRIEFS_DIR", Path(tmpdir)), \
                 patch.object(sbe, "_resolve_recipient", return_value="vahlsingt@gmail.com"):
                result = sbe.send_team_brief(date(2026, 7, 10), dry_run=True)
        self.assertFalse(result["sent"])
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["recipient"], "vahlsingt@gmail.com")
        self.assertIn("Restaurant & Payments Industry Brief", result["subject"])

    def test_subject_names_the_team_edition(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            brief_path = Path(tmpdir) / "2026-07-10-team-intelligence-brief.md"
            brief_path.write_text("# Restaurant & Payments Industry Brief\n\nSome content.",
                                   encoding="utf-8")
            with patch.object(sbe, "BRIEFS_DIR", Path(tmpdir)), \
                 patch.object(sbe, "_resolve_recipient", return_value="vahlsingt@gmail.com"):
                result = sbe.send_team_brief(date(2026, 7, 10), dry_run=True)
        self.assertIn("Friday, July 10, 2026", result["subject"])


if __name__ == "__main__":
    unittest.main()
