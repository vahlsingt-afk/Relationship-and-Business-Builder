"""
test_team_portal_email.py — Ecosystem Lookup Tool, Team Portal email
addition (2026-09-25). Core guarantee: Team Portal's SMTP path never reads
RB_SMTP_* env vars or secrets.env -- only its own dedicated credential.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import team_portal_email as tpe  # noqa: E402


def test_resolve_smtp_reads_dedicated_env_vars(monkeypatch):
    monkeypatch.setenv("TEAM_PORTAL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("TEAM_PORTAL_SMTP_PORT", "465")
    monkeypatch.setenv("TEAM_PORTAL_SMTP_USER", "teamportal@example.com")
    monkeypatch.setenv("TEAM_PORTAL_SMTP_PASS", "hunter2")
    smtp = tpe.resolve_smtp()
    assert smtp == {
        "host": "smtp.example.com", "port": 465, "user": "teamportal@example.com",
        "password": "hunter2", "from_addr": "teamportal@example.com",
    }


def test_resolve_smtp_never_reads_rb_smtp_star(monkeypatch):
    """The core isolation guarantee: even if the MAIN app's RB_SMTP_* vars
    happen to be set in this process's environment, Team Portal must never
    pick them up."""
    monkeypatch.setenv("RB_SMTP_HOST", "main-smtp.example.com")
    monkeypatch.setenv("RB_SMTP_USER", "todd@example.com")
    monkeypatch.setenv("RB_SMTP_PASS", "todds-real-password")
    monkeypatch.delenv("TEAM_PORTAL_SMTP_HOST", raising=False)
    monkeypatch.delenv("TEAM_PORTAL_SMTP_USER", raising=False)
    monkeypatch.delenv("TEAM_PORTAL_SMTP_PASS", raising=False)
    with patch.object(tpe, "SECRETS_PATH", Path("/nonexistent/team_portal_secrets.env")):
        assert tpe.resolve_smtp() is None  # must fail closed, never fall back to RB_SMTP_*


def test_resolve_smtp_reads_dedicated_secrets_file(tmp_path):
    secrets_file = tmp_path / "team_portal_secrets.env"
    secrets_file.write_text(
        "TEAM_PORTAL_SMTP_HOST=smtp.example.com\n"
        "TEAM_PORTAL_SMTP_PORT=587\n"
        'TEAM_PORTAL_SMTP_USER="teamportal@example.com"\n'
        "export TEAM_PORTAL_SMTP_PASS=hunter2\n",
        encoding="utf-8",
    )
    with patch.object(tpe, "SECRETS_PATH", secrets_file):
        with patch.dict("os.environ", {}, clear=False):
            for key in tpe._ENV_KEYS:
                __import__("os").environ.pop(key, None)
            smtp = tpe.resolve_smtp()
    assert smtp["host"] == "smtp.example.com"
    assert smtp["user"] == "teamportal@example.com"
    assert smtp["password"] == "hunter2"


def test_resolve_smtp_returns_none_when_nothing_configured(tmp_path):
    with patch.object(tpe, "SECRETS_PATH", tmp_path / "does_not_exist.env"):
        with patch.dict("os.environ", {}, clear=False):
            for key in tpe._ENV_KEYS:
                __import__("os").environ.pop(key, None)
            assert tpe.resolve_smtp() is None


def test_send_brief_raises_not_configured_when_no_credential(tmp_path):
    with patch.object(tpe, "SECRETS_PATH", tmp_path / "missing.env"):
        with patch.dict("os.environ", {}, clear=False):
            for key in tpe._ENV_KEYS:
                __import__("os").environ.pop(key, None)
            import pytest
            with pytest.raises(tpe.NotConfiguredError):
                tpe.send_brief(recipient="x@example.com", subject="s", markdown_body="b", sent_by="jane")


def test_send_brief_dry_run_never_calls_smtp(monkeypatch):
    monkeypatch.setenv("TEAM_PORTAL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("TEAM_PORTAL_SMTP_USER", "teamportal@example.com")
    monkeypatch.setenv("TEAM_PORTAL_SMTP_PASS", "hunter2")
    with patch("smtplib.SMTP") as mock_smtp:
        result = tpe.send_brief(
            recipient="x@example.com", subject="Test Brief", markdown_body="# Hello",
            sent_by="jane", dry_run=True,
        )
    assert result["status"] == "dry_run"
    mock_smtp.assert_not_called()


def test_send_brief_real_send_uses_dedicated_credential_only(monkeypatch):
    monkeypatch.setenv("TEAM_PORTAL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("TEAM_PORTAL_SMTP_USER", "teamportal@example.com")
    monkeypatch.setenv("TEAM_PORTAL_SMTP_PASS", "hunter2")
    monkeypatch.setenv("RB_SMTP_USER", "todd@example.com")  # must never be used

    mock_server = MagicMock()
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.return_value.__enter__.return_value = mock_server
        result = tpe.send_brief(
            recipient="x@example.com", subject="Test Brief", markdown_body="# Hello",
            sent_by="jane",
        )
    assert result["sent"] is True
    mock_server.login.assert_called_once_with("teamportal@example.com", "hunter2")
    sent_envelope_from = mock_server.sendmail.call_args[0][0]
    assert sent_envelope_from == "teamportal@example.com"


def test_send_brief_uses_alias_from_header_but_authenticates_as_real_account(monkeypatch):
    """Real 2026-09-30 setup (Todd): rbb@bridgepointops.com is a Gmail/
    Workspace alias, not a standalone login -- Google requires
    authenticating as the real account while only the visible From:
    header shows the alias (the standard "Send mail as" pattern).
    TEAM_PORTAL_SMTP_FROM drives the header; login/envelope-sender stay
    the real account."""
    monkeypatch.setenv("TEAM_PORTAL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("TEAM_PORTAL_SMTP_USER", "todd@bridgepointops.com")
    monkeypatch.setenv("TEAM_PORTAL_SMTP_PASS", "hunter2")
    monkeypatch.setenv("TEAM_PORTAL_SMTP_FROM", "rbb@bridgepointops.com")

    mock_server = MagicMock()
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.return_value.__enter__.return_value = mock_server
        result = tpe.send_brief(
            recipient="x@example.com", subject="Test Brief", markdown_body="# Hello",
            sent_by="jane",
        )
    assert result["sent"] is True
    # Authenticates and envelope-sends as the REAL account.
    mock_server.login.assert_called_once_with("todd@bridgepointops.com", "hunter2")
    assert mock_server.sendmail.call_args[0][0] == "todd@bridgepointops.com"
    # But the message the recipient sees is From the alias.
    sent_bytes = mock_server.sendmail.call_args[0][2]
    assert b"From: rbb@bridgepointops.com" in sent_bytes


def test_send_brief_from_header_falls_back_to_user_when_alias_unset(monkeypatch):
    """No TEAM_PORTAL_SMTP_FROM set -- a plain, non-alias mailbox -- must
    behave exactly as before this feature existed."""
    monkeypatch.setenv("TEAM_PORTAL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("TEAM_PORTAL_SMTP_USER", "teamportal@example.com")
    monkeypatch.setenv("TEAM_PORTAL_SMTP_PASS", "hunter2")
    monkeypatch.delenv("TEAM_PORTAL_SMTP_FROM", raising=False)

    mock_server = MagicMock()
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.return_value.__enter__.return_value = mock_server
        tpe.send_brief(
            recipient="x@example.com", subject="Test Brief", markdown_body="# Hello",
            sent_by="jane",
        )
    sent_bytes = mock_server.sendmail.call_args[0][2]
    assert b"From: teamportal@example.com" in sent_bytes


def test_send_brief_smtp_error_reported_not_raised(monkeypatch):
    monkeypatch.setenv("TEAM_PORTAL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("TEAM_PORTAL_SMTP_USER", "teamportal@example.com")
    monkeypatch.setenv("TEAM_PORTAL_SMTP_PASS", "hunter2")
    with patch("smtplib.SMTP", side_effect=OSError("connection refused")):
        result = tpe.send_brief(
            recipient="x@example.com", subject="s", markdown_body="b", sent_by="jane",
        )
    assert result["sent"] is False
    assert result["status"] == "smtp_error"
