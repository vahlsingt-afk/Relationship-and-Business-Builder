#!/usr/bin/env python3
"""
team_portal_email.py — Team Portal's own, isolated SMTP sending.

Ecosystem Lookup Tool (2026-09-25 addition): Todd's explicit decision on
how Team Portal emails a canonical background brief -- a SEPARATE, lower-
privilege credential dedicated to Team Portal, never secrets.env (which
holds RB_API_KEY/OPENAI_API_KEY/GRANOLA_API_KEY/RB_SMTP_*, and which Team
Portal's process has deliberately never touched since it was built --
team_portal_api.py's own docstring: "this process never needs RB_API_KEY
at all"). Reusing send_brief_email.py's RB_SMTP_* credential/env vars here
would mean giving Team Portal read access to the exact file it was built
specifically not to need.

Setup (one-time, outside this repo):
    Create ~/Library/Application Support/Relationship Builder/
    team_portal_secrets.env (chmod 600), containing:
        TEAM_PORTAL_SMTP_HOST=...
        TEAM_PORTAL_SMTP_PORT=587
        TEAM_PORTAL_SMTP_USER=...
        TEAM_PORTAL_SMTP_PASS=...
        TEAM_PORTAL_SMTP_FROM=...   (optional -- see below)
    A distinct mailbox/app-password from RB_SMTP_* is the point -- if this
    credential is ever compromised via a teammate's Team Portal access, it
    can be revoked without touching Todd's own daily-brief delivery.

    TEAM_PORTAL_SMTP_FROM (2026-09-30 addition): real setup, Todd's own --
    rbb@bridgepointops.com is a Gmail/Workspace ALIAS, not a standalone
    login. An alias has no credential of its own; Google requires
    authenticating as the real underlying account (TEAM_PORTAL_SMTP_USER/
    PASS) and only lets the message's From: header show the alias --
    Gmail's standard "Send mail as" pattern (envelope sender stays the
    authenticated account; the visible From is the verified alias).
    Without this, every email would show as sent from the real login, not
    the alias, defeating the entire point of setting the alias up. Set
    TEAM_PORTAL_SMTP_FROM to the alias address; leave it unset and the
    From line falls back to TEAM_PORTAL_SMTP_USER exactly as before (a
    plain non-alias mailbox has no need for this field at all).
"""
from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

SECRETS_PATH = (
    Path.home() / "Library" / "Application Support" / "Relationship Builder"
    / "team_portal_secrets.env"
)

_ENV_KEYS = (
    "TEAM_PORTAL_SMTP_HOST", "TEAM_PORTAL_SMTP_PORT", "TEAM_PORTAL_SMTP_USER",
    "TEAM_PORTAL_SMTP_PASS", "TEAM_PORTAL_SMTP_FROM",
)


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key in _ENV_KEYS:
            values[key] = value
    return values


def resolve_smtp() -> dict | None:
    """Real env vars (if this process happens to have them set) take
    priority; otherwise reads directly from the dedicated secrets file.
    Never falls back to RB_SMTP_* -- an unset Team Portal credential must
    fail closed (returns None), not silently borrow Todd's own."""
    host = os.environ.get("TEAM_PORTAL_SMTP_HOST", "").strip()
    port_raw = os.environ.get("TEAM_PORTAL_SMTP_PORT", "").strip()
    user = os.environ.get("TEAM_PORTAL_SMTP_USER", "").strip()
    pw = os.environ.get("TEAM_PORTAL_SMTP_PASS", "").strip()
    from_addr = os.environ.get("TEAM_PORTAL_SMTP_FROM", "").strip()

    if not all([host, user, pw]):
        file_values = _parse_env_file(SECRETS_PATH)
        host = host or file_values.get("TEAM_PORTAL_SMTP_HOST", "")
        port_raw = port_raw or file_values.get("TEAM_PORTAL_SMTP_PORT", "")
        user = user or file_values.get("TEAM_PORTAL_SMTP_USER", "")
        pw = pw or file_values.get("TEAM_PORTAL_SMTP_PASS", "")
        from_addr = from_addr or file_values.get("TEAM_PORTAL_SMTP_FROM", "")

    if not all([host, user, pw]):
        return None
    try:
        port = int(port_raw) if port_raw else 587
    except ValueError:
        port = 587
    # from_addr: the visible From: header when sending as a verified
    # alias (see TEAM_PORTAL_SMTP_FROM in this module's docstring) --
    # falls back to the authenticated user itself when unset, i.e. no
    # behavior change at all for a plain, non-alias mailbox.
    return {"host": host, "port": port, "user": user, "password": pw, "from_addr": from_addr or user}


class NotConfiguredError(Exception):
    pass


def send_brief(
    *,
    recipient: str,
    subject: str,
    markdown_body: str,
    sent_by: str,
    dry_run: bool = False,
) -> dict:
    """Sends `markdown_body` as a plain-text email. Deliberately plain text,
    not rendered HTML -- a markdown-to-HTML step is real extra surface for
    a first version of this capability; the raw markdown is still fully
    readable as text. Raises NotConfiguredError (never a silent no-op) when
    the dedicated credential isn't set up yet, so a teammate clicking
    "email" gets an honest error instead of nothing happening."""
    smtp = resolve_smtp()
    if smtp is None:
        raise NotConfiguredError(
            f"Team Portal's own SMTP credential is not configured -- "
            f"see {SECRETS_PATH} in this module's docstring for setup."
        )

    if dry_run:
        return {"sent": False, "status": "dry_run", "recipient": recipient, "subject": subject}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    # Gmail's own "Send mail as" pattern: the visible From: header can be
    # a verified alias (from_addr) even though authentication and the
    # SMTP envelope sender below stay the real underlying account -- see
    # TEAM_PORTAL_SMTP_FROM in this module's docstring.
    msg["From"] = smtp["from_addr"]
    msg["To"] = recipient
    footer = f"\n\n---\nSent via RBB Team Portal by {sent_by}."
    msg.attach(MIMEText(markdown_body + footer, "plain", "utf-8"))

    try:
        with smtplib.SMTP(smtp["host"], smtp["port"], timeout=30) as srv:
            srv.starttls()
            srv.login(smtp["user"], smtp["password"])
            srv.sendmail(smtp["user"], [recipient], msg.as_bytes())
        return {"sent": True, "status": "smtp_sent", "recipient": recipient, "subject": subject}
    except Exception as exc:  # noqa: BLE001
        return {"sent": False, "status": "smtp_error", "detail": str(exc)}
