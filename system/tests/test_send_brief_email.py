#!/usr/bin/env python3
"""test_send_brief_email.py — same-day delivery idempotency guard (2026-08-01).

Confirmed live: the morning-pipeline LaunchAgent fired more than once for the
same calendar day (a 5:00am run failed mid-send with a transient network
error right after the Mac woke from sleep, then two more runs followed
minutes later), and every run re-sent every brief email from scratch since
nothing tracked "already delivered today" -- Todd's inbox got 3 copies each
of the Daily Brief, Intelligence Brief, and team-facing Restaurant & Payments
brief. This file tests the fix: a delivery receipt keyed by (date, part) that
a legitimate retry-after-failure still bypasses (nothing was recorded), but
a second successful run for an already-delivered day short-circuits before
ever touching the network.

Every test that could reach smtplib is either using dry_run=True or mocking
smtplib.SMTP -- no test in this file may attempt a real network connection.

Test IDs:
  SBE1 — _already_delivered_today / _record_delivered round-trip
  SBE2 — send() skips (no SMTP call) when already delivered today
  SBE3 — send() sends and records when not yet delivered today
  SBE4 — a failed send does not record delivery (so a later retry still attempts)
  SBE5 — send_team_brief() has its own independent delivery key
  SBE6 — different dates and different parts are tracked independently
  SBE7 — dry_run never checks or writes the delivery log
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import send_brief_email as sbe  # noqa: E402


def _patch_log_path(test: unittest.TestCase, tmp_path: Path) -> None:
    original = sbe._DELIVERY_LOG_PATH
    sbe._DELIVERY_LOG_PATH = tmp_path / "email_delivery_log.json"
    test.addCleanup(lambda: setattr(sbe, "_DELIVERY_LOG_PATH", original))


class SBE1_LogRoundTrip(unittest.TestCase):
    def test_record_then_check(self, tmp_path: Path = None):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            _patch_log_path(self, Path(tmp))
            d = date(2026, 8, 1)
            self.assertFalse(sbe._already_delivered_today(d, "brief:daily"))
            sbe._record_delivered(d, "brief:daily", {"sent": True, "status": "smtp_sent"})
            self.assertTrue(sbe._already_delivered_today(d, "brief:daily"))


class SBE2_SkipsWhenAlreadyDelivered(unittest.TestCase):
    def test_send_skips_and_never_calls_smtp(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            _patch_log_path(self, Path(tmp))
            today = date(2026, 8, 1)
            sbe._record_delivered(today, "brief:daily", {"sent": True, "status": "smtp_sent"})

            with patch.object(sbe, "_email_enabled", return_value=True), \
                 patch.object(sbe, "_resolve_smtp") as mock_smtp, \
                 patch("smtplib.SMTP") as mock_smtp_cls:
                result = sbe.send(today, part="daily", dry_run=False)

            self.assertEqual(result["status"], "skipped_already_sent_today")
            self.assertTrue(result["sent"])
            mock_smtp.assert_not_called()
            mock_smtp_cls.assert_not_called()

    def test_send_team_brief_skips_and_never_calls_smtp(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            _patch_log_path(self, Path(tmp))
            today = date(2026, 8, 1)
            sbe._record_delivered(today, "team_brief", {"sent": True, "status": "smtp_sent"})

            with patch.object(sbe, "_email_enabled", return_value=True), \
                 patch.object(sbe, "_resolve_smtp") as mock_smtp:
                result = sbe.send_team_brief(today, dry_run=False)

            self.assertEqual(result["status"], "skipped_already_sent_today")
            mock_smtp.assert_not_called()


class SBE3_SendsAndRecordsWhenNotYetDelivered(unittest.TestCase):
    def test_successful_send_is_recorded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _patch_log_path(self, tmp_path)
            briefs_dir = tmp_path / "briefs"
            briefs_dir.mkdir()
            original_briefs_dir = sbe.BRIEFS_DIR
            sbe.BRIEFS_DIR = briefs_dir
            self.addCleanup(lambda: setattr(sbe, "BRIEFS_DIR", original_briefs_dir))

            today = date(2026, 8, 1)
            (briefs_dir / f"{today.isoformat()}-daily-brief.md").write_text("# Test Brief\n")

            mock_srv = MagicMock()
            mock_srv.__enter__.return_value = mock_srv
            with patch.object(sbe, "_email_enabled", return_value=True), \
                 patch.object(sbe, "_resolve_smtp", return_value={
                "host": "smtp.example.com", "port": 587, "user": "test@example.com", "password": "x",
            }), patch.object(sbe, "_resolve_recipient", return_value="todd@example.com"), \
                 patch("smtplib.SMTP", return_value=mock_srv):
                result = sbe.send(today, part="daily", dry_run=False)

            self.assertTrue(result["sent"])
            self.assertEqual(result["status"], "smtp_sent")
            mock_srv.sendmail.assert_called_once()
            self.assertTrue(sbe._already_delivered_today(today, "brief:daily"))

            # A second call for the same day must now skip -- no second sendmail.
            mock_srv.sendmail.reset_mock()
            with patch.object(sbe, "_email_enabled", return_value=True), \
                 patch.object(sbe, "_resolve_smtp", return_value={
                "host": "smtp.example.com", "port": 587, "user": "test@example.com", "password": "x",
            }), patch.object(sbe, "_resolve_recipient", return_value="todd@example.com"), \
                 patch("smtplib.SMTP", return_value=mock_srv):
                second = sbe.send(today, part="daily", dry_run=False)
            self.assertEqual(second["status"], "skipped_already_sent_today")
            mock_srv.sendmail.assert_not_called()


class SBE4_FailedSendNotRecorded(unittest.TestCase):
    def test_smtp_error_does_not_record_delivery_so_retry_still_attempts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _patch_log_path(self, tmp_path)
            briefs_dir = tmp_path / "briefs"
            briefs_dir.mkdir()
            original_briefs_dir = sbe.BRIEFS_DIR
            sbe.BRIEFS_DIR = briefs_dir
            self.addCleanup(lambda: setattr(sbe, "BRIEFS_DIR", original_briefs_dir))

            today = date(2026, 8, 1)
            (briefs_dir / f"{today.isoformat()}-daily-brief.md").write_text("# Test Brief\n")

            with patch.object(sbe, "_email_enabled", return_value=True), \
                 patch.object(sbe, "_resolve_smtp", return_value={
                "host": "smtp.example.com", "port": 587, "user": "test@example.com", "password": "x",
            }), patch.object(sbe, "_resolve_recipient", return_value="todd@example.com"), \
                 patch("smtplib.SMTP", side_effect=OSError("nodename nor servname provided, or not known")):
                result = sbe.send(today, part="daily", dry_run=False)

            self.assertFalse(result["sent"])
            self.assertEqual(result["status"], "smtp_error")
            self.assertFalse(
                sbe._already_delivered_today(today, "brief:daily"),
                "a failed send must not be recorded as delivered -- a later retry needs to still attempt",
            )


class SBE5_TeamBriefIndependentKey(unittest.TestCase):
    def test_daily_delivery_does_not_suppress_team_brief(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            _patch_log_path(self, Path(tmp))
            today = date(2026, 8, 1)
            sbe._record_delivered(today, "brief:daily", {"sent": True, "status": "smtp_sent"})
            self.assertFalse(sbe._already_delivered_today(today, "team_brief"))
            self.assertFalse(sbe._already_delivered_today(today, "brief:intelligence"))


class SBE6_IndependentByDateAndPart(unittest.TestCase):
    def test_different_dates_and_parts_tracked_independently(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            _patch_log_path(self, Path(tmp))
            sbe._record_delivered(date(2026, 8, 1), "brief:daily", {"sent": True, "status": "smtp_sent"})

            self.assertTrue(sbe._already_delivered_today(date(2026, 8, 1), "brief:daily"))
            self.assertFalse(sbe._already_delivered_today(date(2026, 8, 2), "brief:daily"))
            self.assertFalse(sbe._already_delivered_today(date(2026, 8, 1), "brief:intelligence"))


class SBE7_DryRunNeverTouchesLog(unittest.TestCase):
    def test_dry_run_does_not_check_or_write_log(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _patch_log_path(self, tmp_path)
            briefs_dir = tmp_path / "briefs"
            briefs_dir.mkdir()
            original_briefs_dir = sbe.BRIEFS_DIR
            sbe.BRIEFS_DIR = briefs_dir
            self.addCleanup(lambda: setattr(sbe, "BRIEFS_DIR", original_briefs_dir))

            today = date(2026, 8, 1)
            (briefs_dir / f"{today.isoformat()}-daily-brief.md").write_text("# Test Brief\n")

            with patch.object(sbe, "_email_enabled", return_value=True), \
                 patch.object(sbe, "_already_delivered_today") as mock_check:
                result = sbe.send(today, part="daily", dry_run=True)
            self.assertEqual(result["status"], "dry_run")
            mock_check.assert_not_called()
            self.assertFalse(sbe._DELIVERY_LOG_PATH.exists())


class TestEmailEnabledGate(unittest.TestCase):
    """RB-DEFECT (2026-09-15): send(), send_linkedin_reports(), and
    send_team_brief() previously attempted SMTP unconditionally, regardless
    of daily_briefing.delivery.email_enabled in settings.json -- a
    deliberately-disabled backup channel (its documented state: the primary
    delivery path is a ChatGPT Task push) surfaced as a "no_smtp" pipeline
    failure every morning instead of being silently skipped. These prove
    the gate itself, isolated from real settings.json via _email_enabled."""

    def test_send_disabled_never_touches_smtp_or_delivery_log(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _patch_log_path(self, tmp_path)
            briefs_dir = tmp_path / "briefs"
            briefs_dir.mkdir()
            original_briefs_dir = sbe.BRIEFS_DIR
            sbe.BRIEFS_DIR = briefs_dir
            self.addCleanup(lambda: setattr(sbe, "BRIEFS_DIR", original_briefs_dir))

            today = date(2026, 8, 1)
            (briefs_dir / f"{today.isoformat()}-daily-brief.md").write_text("# Test Brief\n")

            with patch.object(sbe, "_email_enabled", return_value=False), \
                 patch.object(sbe, "_resolve_smtp") as mock_smtp, \
                 patch("smtplib.SMTP") as mock_smtp_cls, \
                 patch.object(sbe, "_already_delivered_today") as mock_check:
                result = sbe.send(today, part="daily", dry_run=False)

            self.assertFalse(result["sent"])
            self.assertEqual(result["status"], "disabled")
            mock_smtp.assert_not_called()
            mock_smtp_cls.assert_not_called()
            mock_check.assert_not_called()
            self.assertFalse(sbe._DELIVERY_LOG_PATH.exists())

    def test_send_disabled_even_for_dry_run(self):
        """Simplicity/predictability over convenience: while disabled, this
        channel is fully hands-off, preview included -- flip the setting to
        preview it, don't special-case dry_run around the gate."""
        with patch.object(sbe, "_email_enabled", return_value=False):
            result = sbe.send(date(2026, 8, 1), part="daily", dry_run=True)
        self.assertEqual(result["status"], "disabled")

    def test_send_team_brief_disabled_never_touches_smtp(self):
        with patch.object(sbe, "_email_enabled", return_value=False), \
             patch.object(sbe, "_resolve_smtp") as mock_smtp:
            result = sbe.send_team_brief(date(2026, 8, 1), dry_run=False)
        self.assertEqual(result["status"], "disabled")
        mock_smtp.assert_not_called()

    def test_send_linkedin_reports_disabled_never_touches_smtp(self):
        with patch.object(sbe, "_email_enabled", return_value=False), \
             patch.object(sbe, "_resolve_smtp") as mock_smtp:
            result = sbe.send_linkedin_reports(date(2026, 8, 1), dry_run=False)
        self.assertEqual(result["status"], "disabled")
        mock_smtp.assert_not_called()

    def test_email_enabled_reads_correct_settings_path(self):
        """RB-DEFECT (2026-09-15): the first draft of _email_enabled() read
        daily_briefing.email_enabled -- the real key is nested one level
        deeper, daily_briefing.delivery.email_enabled (same path
        _resolve_recipient() already reads). Proves the fix reads the
        correct path rather than always defaulting to disabled."""
        with patch.object(sbe.core, "load_settings", return_value={
            "daily_briefing": {"delivery": {"email_enabled": True}}
        }):
            self.assertTrue(sbe._email_enabled())
        with patch.object(sbe.core, "load_settings", return_value={
            "daily_briefing": {"delivery": {"email_enabled": False}}
        }):
            self.assertFalse(sbe._email_enabled())
        with patch.object(sbe.core, "load_settings", return_value={}):
            self.assertFalse(sbe._email_enabled())
        with patch.object(sbe.core, "load_settings", side_effect=OSError("no file")):
            self.assertFalse(sbe._email_enabled())


if __name__ == "__main__":
    unittest.main(verbosity=2)
