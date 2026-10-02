"""
test_newsletter_job_alert_filter.py

Regression coverage: broadening fetch_google.py's NEWSLETTER_DOMAINS to
include linkedin.com (so a genuine editorial digest like a contact's
"Hospitality Headline" gets full-body fetch) had a side effect — LinkedIn's
own job-alert bots ("Confidential Careers", generic jobs digests) also send
from linkedin.com and now qualify for full-body fetch too, so they started
appearing in D+ Newsletter Inbox as if they were real newsletters. The
"What Changed Today" email-activity line already mutes these senders
(_MUTED_EMAIL_SENDERS in render_intelligence_brief.py) but that muting never
applied to the newsletter pipeline. _compute_newsletter_intelligence now
excludes job-alert-shaped senders at the source.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db  # noqa: E402
import rb_core as core  # noqa: E402


def _thread(sender_name: str, sender_email: str, articles_html: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "subject": "Digest",
        "last_message_at": now,
        "last_message_from": {"name": sender_name, "email": sender_email},
        "body_text": articles_html,
    }


class TestNewsletterJobAlertFilter(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.inbox_dir = Path(self.tmpdir.name)
        self._patch = patch.object(core, "INBOX_DIR", self.inbox_dir)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmpdir.cleanup()

    def _write_personal_threads(self, threads: list[dict]) -> None:
        (self.inbox_dir / "email.personal.json").write_text(json.dumps({"threads": threads}))

    def test_job_alert_sender_excluded_from_newsletter_pool(self):
        html = '<a href="https://linkedin.com/jobs/1">Multi-Skilled Maintenance Technician role</a>'
        self._write_personal_threads([
            _thread("Confidential Careers via LinkedIn", "jobalerts-noreply@linkedin.com", html),
        ])
        report = {"today": None}
        items = db._compute_newsletter_intelligence(report, {})
        self.assertEqual(items, [])

    def test_genuine_linkedin_newsletter_still_surfaces(self):
        html = '<a href="https://linkedin.com/pulse/1">Mapping the Path from Technical Excellence to Leadership</a>'
        self._write_personal_threads([
            _thread("Michael 'schatzy' Schatzberg via LinkedIn", "newsletters-noreply@linkedin.com", html),
        ])
        report = {"today": None}
        items = db._compute_newsletter_intelligence(report, {})
        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
