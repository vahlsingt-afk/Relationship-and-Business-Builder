#!/usr/bin/env python3
"""send_brief_email.py — email the pre-rendered Intelligence Brief and Daily Brief.

Runs at 5:05am after render_intelligence_brief.py and render_daily_brief.py.
Reads the pre-rendered markdown files and sends them as a single formatted
HTML email to the configured recipient.

Transport: SMTP via RB_SMTP_HOST / RB_SMTP_PORT / RB_SMTP_USER / RB_SMTP_PASS
Recipient: daily_briefing.delivery.recipient in settings.json, or RB_BRIEF_RECIPIENT env var.
Gate: daily_briefing.email_enabled in settings.json must be true, or every send
function here returns status="disabled" without touching SMTP at all -- RB-DEFECT
(2026-09-15): this file previously attempted SMTP unconditionally regardless of
that flag, so email being deliberately turned off (its documented state -- the
primary delivery path is a ChatGPT Task push, this is the backup channel) still
surfaced as a "no_smtp" pipeline failure every morning.

Usage:
    python3 send_brief_email.py [--date YYYY-MM-DD] [--dry-run]
    python3 send_brief_email.py --part intelligence   # intelligence brief only
    python3 send_brief_email.py --part daily          # daily brief only
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import sys
from datetime import date, datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core

BRIEFS_DIR = core.SYSTEM_DIR / "briefs"

# Same-day delivery idempotency guard (2026-08-01): the morning-pipeline
# LaunchAgent can fire more than once for the same calendar day -- confirmed
# live: a 5:00am run failed mid-send with a transient DNS resolution error
# (network not yet up right after the Mac woke from sleep), and two more
# pipeline runs followed minutes later, each of which re-sent every brief
# email from scratch since nothing tracked "already delivered today". Rather
# than try to fix the launchd/sleep-wake timing (outside this codebase's
# control), each send records a receipt here after a real SMTP success, and
# every send checks it first -- a legitimate retry after a failure still
# goes through (nothing was recorded), but a second successful run for a day
# that's already delivered is a no-op instead of a duplicate email.
_DELIVERY_LOG_PATH = core.CACHE_DIR / "email_delivery_log.json"


def _load_delivery_log() -> dict:
    if not _DELIVERY_LOG_PATH.exists():
        return {}
    try:
        return json.loads(_DELIVERY_LOG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _delivery_key(target_date: date, part: str) -> str:
    return f"{target_date.isoformat()}:{part}"


def _already_delivered_today(target_date: date, part: str) -> bool:
    log = _load_delivery_log()
    return bool(log.get(_delivery_key(target_date, part), {}).get("sent"))


def _record_delivered(target_date: date, part: str, detail: dict) -> None:
    log = _load_delivery_log()
    log[_delivery_key(target_date, part)] = {
        "sent": True,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **{k: v for k, v in detail.items() if k in ("recipient", "subject", "status")},
    }
    _DELIVERY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DELIVERY_LOG_PATH.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Markdown → HTML
# ---------------------------------------------------------------------------

def _md_to_html(md: str) -> str:
    """Minimal markdown → HTML conversion sufficient for brief rendering."""
    import re
    lines = md.split("\n")
    html_lines = []
    in_table = False
    in_code = False

    for line in lines:
        # Code blocks
        if line.startswith("```"):
            if not in_code:
                html_lines.append('<pre style="background:#f4f4f4;padding:12px;border-radius:4px;font-size:13px;">')
                in_code = True
            else:
                html_lines.append("</pre>")
                in_code = False
            continue
        if in_code:
            html_lines.append(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            continue

        # Tables
        if "|" in line and line.strip().startswith("|"):
            if not in_table:
                html_lines.append('<table style="width:100%;border-collapse:collapse;margin:8px 0;">')
                in_table = True
            if re.match(r"^\|[-| :]+\|$", line.strip()):
                continue  # separator row
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            row_html = "".join(
                f'<td style="border:1px solid #ddd;padding:6px 10px;">{_inline_md(c)}</td>'
                for c in cells
            )
            html_lines.append(f"<tr>{row_html}</tr>")
            continue
        elif in_table:
            html_lines.append("</table>")
            in_table = False

        # Headings
        if line.startswith("# "):
            html_lines.append(f'<h1 style="color:#1a1a1a;border-bottom:2px solid #2c5282;padding-bottom:8px;">{_inline_md(line[2:])}</h1>')
        elif line.startswith("## "):
            html_lines.append(f'<h2 style="color:#2c5282;margin-top:24px;margin-bottom:8px;">{_inline_md(line[3:])}</h2>')
        elif line.startswith("### "):
            html_lines.append(f'<h3 style="color:#444;margin-top:16px;margin-bottom:4px;">{_inline_md(line[4:])}</h3>')
        # HR
        elif line.strip() == "---":
            html_lines.append('<hr style="border:none;border-top:1px solid #e2e8f0;margin:16px 0;">')
        # List items
        elif line.startswith("- ") or line.startswith("* "):
            html_lines.append(f'<li style="margin:4px 0;">{_inline_md(line[2:])}</li>')
        elif line.startswith("  - ") or line.startswith("  * "):
            html_lines.append(f'<li style="margin:2px 0;margin-left:20px;">{_inline_md(line[4:])}</li>')
        # Blockquote (used for banners in KBs)
        elif line.startswith("> "):
            html_lines.append(f'<blockquote style="border-left:4px solid #2c5282;padding:4px 12px;color:#555;margin:8px 0;">{_inline_md(line[2:])}</blockquote>')
        # Empty line
        elif line.strip() == "":
            html_lines.append("<br>")
        # Paragraph
        else:
            html_lines.append(f'<p style="margin:4px 0;">{_inline_md(line)}</p>')

    if in_table:
        html_lines.append("</table>")

    return "\n".join(html_lines)


def _inline_md(text: str) -> str:
    """Convert inline markdown (bold, italic, links) to HTML."""
    import re
    # Links: [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                  r'<a href="\2" style="color:#2c5282;">\1</a>', text)
    # Bold
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    # Italic
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)
    # Inline code
    text = re.sub(r"`([^`]+)`", r'<code style="background:#f4f4f4;padding:1px 4px;border-radius:3px;">\1</code>', text)
    return text


def _build_html(intelligence_md: str | None, daily_md: str | None, target_date: date) -> str:
    weekday = target_date.strftime("%A")
    date_str = target_date.strftime("%B %-d, %Y")

    sections = []
    if intelligence_md:
        sections.append(_md_to_html(intelligence_md))
    if daily_md:
        if intelligence_md:
            sections.append('<hr style="border:none;border-top:3px solid #2c5282;margin:32px 0;">')
        sections.append(_md_to_html(daily_md))

    body_html = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RB Brief — {weekday}, {date_str}</title>
</head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:800px;margin:0 auto;padding:24px;color:#1a1a1a;line-height:1.6;">
{body_html}
<hr style="border:none;border-top:1px solid #e2e8f0;margin:32px 0;">
<p style="color:#888;font-size:12px;">RB — {weekday}, {date_str} · Sent {datetime.now(tz=timezone.utc).strftime('%H:%M UTC')}</p>
</body>
</html>"""


def _build_text(intelligence_md: str | None, daily_md: str | None) -> str:
    parts = []
    if intelligence_md:
        parts.append(intelligence_md)
    if daily_md:
        if intelligence_md:
            parts.append("\n" + "=" * 60 + "\n")
        parts.append(daily_md)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

def _resolve_recipient() -> str | None:
    env = os.environ.get("RB_BRIEF_RECIPIENT", "").strip()
    if env:
        return env
    try:
        settings = core.load_settings()
        r = ((settings.get("daily_briefing") or {}).get("delivery") or {}).get("recipient") or None
        if r:
            return r
    except Exception:
        pass
    # Fall back to LaunchAgent plist
    try:
        import plistlib
        plist_path = Path.home() / "Library/LaunchAgents/com.relationshipbuilder.morning-pipeline.plist"
        if plist_path.exists():
            with open(plist_path, "rb") as f:
                plist = plistlib.load(f)
            return (plist.get("EnvironmentVariables") or {}).get("RB_BRIEF_RECIPIENT", "").strip() or None
    except Exception:
        pass
    return None


def _email_enabled() -> bool:
    """daily_briefing.delivery.email_enabled in settings.json -- explicit
    opt-in, same nesting _resolve_recipient() already reads. publish.py's
    own --send path already checks this correctly (delivery.get(
    "email_enabled", False)); this file did not, which was the actual bug.

    Defaults to False (don't send) if settings.json is unreadable or the
    key is absent -- consistent with email being off unless someone
    deliberately turned it on, never a silent default-on."""
    try:
        settings = core.load_settings()
        delivery = (settings.get("daily_briefing") or {}).get("delivery") or {}
        return bool(delivery.get("email_enabled"))
    except Exception:
        return False


def _resolve_smtp() -> dict | None:
    host = os.environ.get("RB_SMTP_HOST", "").strip()
    port = int(os.environ.get("RB_SMTP_PORT", "587") or 587)
    user = os.environ.get("RB_SMTP_USER", "").strip()
    pw = os.environ.get("RB_SMTP_PASS", "").strip()
    if not all([host, user, pw]):
        # Fall back to LaunchAgent plist when not running under launchd
        try:
            import plistlib, subprocess
            plist_path = Path.home() / "Library/LaunchAgents/com.relationshipbuilder.morning-pipeline.plist"
            if plist_path.exists():
                with open(plist_path, "rb") as f:
                    plist = plistlib.load(f)
                env = plist.get("EnvironmentVariables") or {}
                host = host or env.get("RB_SMTP_HOST", "").strip()
                port = int(env.get("RB_SMTP_PORT", port) or port)
                user = user or env.get("RB_SMTP_USER", "").strip()
                pw = pw or env.get("RB_SMTP_PASS", "").strip()
        except Exception:
            pass
    if not all([host, user, pw]):
        return None
    return {"host": host, "port": port, "user": user, "password": pw}


def send(target_date: date, part: str = "both", dry_run: bool = False) -> dict:
    """Load pre-rendered briefs and send as email(s). Returns status dict.

    When part == "both", sends two separate emails — one for each brief.
    """
    if not _email_enabled():
        return {"sent": False, "status": "disabled",
                "detail": "daily_briefing.email_enabled is false in settings.json"}

    # "both" mode: send each brief as a separate email and return combined status
    if part == "both":
        results = []
        for p in ("intelligence", "daily"):
            r = send(target_date, part=p, dry_run=dry_run)
            results.append(r)
        sent_count = sum(1 for r in results if r.get("sent"))
        return {
            "sent": sent_count > 0,
            "status": "smtp_sent" if sent_count == 2 else ("partial" if sent_count else "failed"),
            "detail": results,
        }

    intel_md: str | None = None
    daily_md: str | None = None

    if part == "intelligence":
        p = BRIEFS_DIR / f"{target_date.isoformat()}-intelligence-brief.md"
        if p.exists():
            intel_md = p.read_text(encoding="utf-8")

    if part == "daily":
        p = BRIEFS_DIR / f"{target_date.isoformat()}-daily-brief.md"
        if p.exists():
            daily_md = p.read_text(encoding="utf-8")

    if not intel_md and not daily_md:
        return {"sent": False, "status": "no_briefs_found",
                "detail": f"No pre-rendered briefs found for {target_date.isoformat()} ({part})"}

    if not dry_run and _already_delivered_today(target_date, f"brief:{part}"):
        return {"sent": True, "status": "skipped_already_sent_today",
                "detail": f"{part} brief for {target_date.isoformat()} was already delivered today"}

    weekday = target_date.strftime("%A")
    date_str = target_date.strftime('%B %-d, %Y')
    if part == "intelligence":
        subject = f"RB Intelligence Brief — {weekday}, {date_str}"
    else:
        subject = f"RB Daily Brief — {weekday}, {date_str}"
    html_body = _build_html(intel_md, daily_md, target_date)
    text_body = _build_text(intel_md, daily_md)

    recipient = _resolve_recipient()
    if not recipient:
        return {"sent": False, "status": "no_recipient",
                "detail": "Set RB_BRIEF_RECIPIENT env var or daily_briefing.delivery.recipient in settings.json"}

    if dry_run:
        print(f"Subject: {subject}")
        print(f"To: {recipient}")
        print(f"Intelligence brief: {'yes' if intel_md else 'no'} ({len(intel_md or '')} chars)")
        print(f"Daily brief: {'yes' if daily_md else 'no'} ({len(daily_md or '')} chars)")
        print("\n--- TEXT PREVIEW (first 500 chars) ---")
        print(text_body[:500])
        return {"sent": False, "status": "dry_run", "subject": subject, "recipient": recipient}

    smtp = _resolve_smtp()
    if not smtp:
        # Fall back to .eml drop if configured
        eml_dir = os.environ.get("RB_EML_DROP_DIR", "").strip()
        if eml_dir:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = "RB <rb@bridgepointops.org>"
            msg["To"] = recipient
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))
            eml_path = Path(eml_dir) / f"rb-brief-{target_date.isoformat()}.eml"
            eml_path.write_bytes(msg.as_bytes())
            result = {"sent": True, "status": "eml_dropped", "path": str(eml_path)}
            _record_delivered(target_date, f"brief:{part}", result)
            return result
        return {"sent": False, "status": "no_smtp",
                "detail": "Set RB_SMTP_HOST, RB_SMTP_USER, RB_SMTP_PASS env vars"}

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp["user"]
        msg["To"] = recipient
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(smtp["host"], smtp["port"], timeout=30) as srv:
            srv.starttls()
            srv.login(smtp["user"], smtp["password"])
            srv.sendmail(smtp["user"], [recipient], msg.as_bytes())

        result = {"sent": True, "status": "smtp_sent", "recipient": recipient, "subject": subject}
        _record_delivered(target_date, f"brief:{part}", result)
        return result
    except Exception as e:
        return {"sent": False, "status": "smtp_error", "detail": str(e)}


def send_linkedin_reports(target_date: date, dry_run: bool = False) -> dict:
    """Email the three LinkedIn intelligence brief files for target_date.

    Reads:
      - BRIEFS_DIR / YYYY-MM-DD-linkedin-intelligence-report.md
      - BRIEFS_DIR / YYYY-MM-DD-linkedin-contact-rationalization.md
      - BRIEFS_DIR / YYYY-MM-DD-linkedin-mutation-package.md

    Sends them as a single combined email.
    """
    if not _email_enabled():
        return {"sent": False, "status": "disabled",
                "detail": "daily_briefing.email_enabled is false in settings.json"}

    stamp = target_date.isoformat()
    parts = {
        "Intelligence Report": BRIEFS_DIR / f"{stamp}-linkedin-intelligence-report.md",
        "Contact Rationalization": BRIEFS_DIR / f"{stamp}-linkedin-contact-rationalization.md",
        "Mutation Package": BRIEFS_DIR / f"{stamp}-linkedin-mutation-package.md",
    }
    found = {label: path.read_text(encoding="utf-8") for label, path in parts.items() if path.exists()}
    if not found:
        return {"sent": False, "status": "no_linkedin_briefs_found",
                "detail": f"No LinkedIn briefs found for {stamp}"}

    combined_md = ""
    for label, content in found.items():
        combined_md += content + "\n\n---\n\n"

    weekday = target_date.strftime("%A")
    date_str = target_date.strftime("%B %-d, %Y")
    subject = f"RB LinkedIn Intelligence — {weekday}, {date_str} ({len(found)} reports)"
    html_body = _build_html(combined_md, None, target_date)
    text_body = _build_text(combined_md, None)

    recipient = _resolve_recipient()
    if not recipient:
        return {"sent": False, "status": "no_recipient",
                "detail": "Set RB_BRIEF_RECIPIENT env var or daily_briefing.delivery.recipient in settings.json"}

    if dry_run:
        print(f"Subject: {subject}")
        print(f"To: {recipient}")
        print(f"Reports included: {', '.join(found)}")
        return {"sent": False, "status": "dry_run", "subject": subject, "recipient": recipient}

    smtp = _resolve_smtp()
    if not smtp:
        eml_dir = os.environ.get("RB_EML_DROP_DIR", "").strip()
        if eml_dir:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = "RB <rb@bridgepointops.org>"
            msg["To"] = recipient
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))
            eml_path = Path(eml_dir) / f"rb-linkedin-{stamp}.eml"
            eml_path.write_bytes(msg.as_bytes())
            return {"sent": True, "status": "eml_dropped", "path": str(eml_path)}
        return {"sent": False, "status": "no_smtp",
                "detail": "Set RB_SMTP_HOST, RB_SMTP_USER, RB_SMTP_PASS env vars"}

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp["user"]
        msg["To"] = recipient
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        with smtplib.SMTP(smtp["host"], smtp["port"], timeout=30) as srv:
            srv.starttls()
            srv.login(smtp["user"], smtp["password"])
            srv.sendmail(smtp["user"], [recipient], msg.as_bytes())
        return {"sent": True, "status": "smtp_sent", "recipient": recipient, "subject": subject,
                "reports": list(found.keys())}
    except Exception as e:
        return {"sent": False, "status": "smtp_error", "detail": str(e)}


def send_team_brief(target_date: date, dry_run: bool = False) -> dict:
    """Email the team-facing Intelligence Brief edition (industry news only,
    no personal content) for target_date -- see render_intelligence_brief.
    render_team_edition. Sent to the same configured recipient (Todd); he
    handles further distribution to the team himself."""
    if not _email_enabled():
        return {"sent": False, "status": "disabled",
                "detail": "daily_briefing.email_enabled is false in settings.json"}

    stamp = target_date.isoformat()
    path = BRIEFS_DIR / f"{stamp}-team-intelligence-brief.md"
    if not path.exists():
        return {"sent": False, "status": "no_briefs_found",
                "detail": f"No team intelligence brief found for {stamp}"}

    if not dry_run and _already_delivered_today(target_date, "team_brief"):
        return {"sent": True, "status": "skipped_already_sent_today",
                "detail": f"Team intelligence brief for {stamp} was already delivered today"}

    md = path.read_text(encoding="utf-8")
    weekday = target_date.strftime("%A")
    date_str = target_date.strftime("%B %-d, %Y")
    subject = f"Restaurant & Payments Industry Brief — {weekday}, {date_str}"
    html_body = _build_html(md, None, target_date)
    text_body = _build_text(md, None)

    recipient = _resolve_recipient()
    if not recipient:
        return {"sent": False, "status": "no_recipient",
                "detail": "Set RB_BRIEF_RECIPIENT env var or daily_briefing.delivery.recipient in settings.json"}

    if dry_run:
        print(f"Subject: {subject}")
        print(f"To: {recipient}")
        print("\n--- TEXT PREVIEW (first 500 chars) ---")
        print(text_body[:500])
        return {"sent": False, "status": "dry_run", "subject": subject, "recipient": recipient}

    smtp = _resolve_smtp()
    if not smtp:
        eml_dir = os.environ.get("RB_EML_DROP_DIR", "").strip()
        if eml_dir:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = "RB <rb@bridgepointops.org>"
            msg["To"] = recipient
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))
            eml_path = Path(eml_dir) / f"rb-team-brief-{stamp}.eml"
            eml_path.write_bytes(msg.as_bytes())
            result = {"sent": True, "status": "eml_dropped", "path": str(eml_path)}
            _record_delivered(target_date, "team_brief", result)
            return result
        return {"sent": False, "status": "no_smtp",
                "detail": "Set RB_SMTP_HOST, RB_SMTP_USER, RB_SMTP_PASS env vars"}

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp["user"]
        msg["To"] = recipient
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        with smtplib.SMTP(smtp["host"], smtp["port"], timeout=30) as srv:
            srv.starttls()
            srv.login(smtp["user"], smtp["password"])
            srv.sendmail(smtp["user"], [recipient], msg.as_bytes())
        result = {"sent": True, "status": "smtp_sent", "recipient": recipient, "subject": subject}
        _record_delivered(target_date, "team_brief", result)
        return result
    except Exception as e:
        return {"sent": False, "status": "smtp_error", "detail": str(e)}


def main() -> int:
    p = argparse.ArgumentParser(description="Email the pre-rendered RB briefs.")
    p.add_argument("--date", default=None, help="Date (YYYY-MM-DD). Default: today.")
    p.add_argument("--part", choices=["both", "intelligence", "daily"], default="both")
    p.add_argument("--team", action="store_true",
                   help="Send the team-facing Intelligence Brief edition instead.")
    p.add_argument("--dry-run", action="store_true", help="Print preview, don't send.")
    args = p.parse_args()

    target = date.fromisoformat(args.date) if args.date else date.today()

    if args.team:
        result = send_team_brief(target, dry_run=args.dry_run)
        if result["status"] == "skipped_already_sent_today":
            print(f"– {result['detail']}")
        elif result["sent"]:
            print(f"✓ Team Intelligence Brief emailed to {result['recipient']} ({result['status']})")
        elif result["status"] == "dry_run":
            pass
        else:
            print(f"✗ Not sent: {result['status']} — {result.get('detail', '')}", file=sys.stderr)
            return 1
        return 0

    result = send(target, part=args.part, dry_run=args.dry_run)

    if args.part == "both" and isinstance(result.get("detail"), list):
        # Two separate emails — print status for each
        any_failed = False
        for r in result["detail"]:
            if r.get("status") == "skipped_already_sent_today":
                print(f"– {r['detail']}")
            elif r.get("sent"):
                print(f"✓ {r.get('subject', 'Brief')} → {r.get('recipient')} ({r['status']})")
            elif r.get("status") == "dry_run":
                pass
            else:
                print(f"✗ Not sent: {r.get('status')} — {r.get('detail', '')}", file=sys.stderr)
                any_failed = True
        if any_failed:
            return 1
    elif result["status"] == "skipped_already_sent_today":
        print(f"– {result['detail']}")
    elif result["sent"]:
        print(f"✓ Brief emailed to {result['recipient']} ({result['status']})")
    elif result["status"] == "dry_run":
        pass  # already printed
    else:
        print(f"✗ Not sent: {result['status']} — {result.get('detail', '')}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
