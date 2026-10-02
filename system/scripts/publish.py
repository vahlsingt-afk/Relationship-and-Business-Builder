#!/usr/bin/env python3
"""
publish.py — publish today's daily brief to the RB operational layer.

Per `settings.json → daily_briefing.delivery.destination_requirement`, the
morning email CTA must open a user-facing RB surface — never Codex, Claude,
local project paths, or implementation scaffolding. This module materializes
that surface deterministically and composes the email body that points
into it.

Three output files under `system/published/daily/<YYYY-MM-DD>/`:

    index.md       — the brief markdown (same content as system/today.md)
    index.html     — self-contained HTML render
    brief.json     — machine-readable canonical_brief

Plus `system/published/daily/latest.html` and
`system/published/daily/latest_brief.json` — copies of the newest artifacts so
the email CTA and read-only API aliases can point at stable paths while the
operator wires a real hosted destination.

Email body is a short summary (top 3 priorities + signal health + CTA URL).
The actual SMTP/Gmail send is host-side: this module composes; the operator
or LaunchAgent wires the transport. See:

    system/CLAUDE_FOLLOWUP_MORNING_DELIVERY_PIPELINE.md

Per the smoke-test-mutations memory, the smoke never writes files and never
reaches the --send branch.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import date, datetime
from html import escape as h
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core


PUBLISHED_DIR = core.SYSTEM_DIR / "published" / "daily"
PUBLIC_URL_BASE_ENV = "RB_PUBLIC_URL_BASE"
CHATGPT_BRIEF_URL_ENV = "RB_CHATGPT_BRIEF_URL"


# -----------------------------------------------------------------------------
# Settings + URL resolution
# -----------------------------------------------------------------------------

def _resolve_url_base() -> Optional[str]:
    """Pick the operational-layer URL base: env override → settings.json → None.

    Returns None when nothing's configured; callers should report
    "pending_destination" in that case rather than fabricating a URL.
    """
    env = os.environ.get(PUBLIC_URL_BASE_ENV)
    if env:
        return env.rstrip("/")
    try:
        s = core.load_settings()
    except Exception:  # noqa: BLE001
        return None
    op = ((s.get("daily_briefing") or {}).get("operational_layer") or {})
    base = op.get("public_url_base")
    if isinstance(base, str) and base:
        return base.rstrip("/")
    return None


def public_url_for(today: date, *, latest: bool = False) -> Optional[str]:
    base = _resolve_url_base()
    if not base:
        return None
    if latest:
        return f"{base}/daily/latest.html"
    return f"{base}/daily/{today.isoformat()}/index.html"


def _resolve_chatgpt_brief_url() -> Optional[str]:
    """Pick the ChatGPT daily-brief destination: env override -> settings -> None."""
    env = os.environ.get(CHATGPT_BRIEF_URL_ENV)
    if env:
        return env.strip()
    try:
        s = core.load_settings()
    except Exception:  # noqa: BLE001
        return None
    delivery = ((s.get("daily_briefing") or {}).get("delivery") or {})
    url = delivery.get("chatgpt_brief_url")
    if isinstance(url, str) and url.strip():
        return url.strip()
    return None


# -----------------------------------------------------------------------------
# HTML rendering
# -----------------------------------------------------------------------------

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_MD_BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.*)$")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)([^*]+?)\*(?!\*)")
_MD_CODE_RE = re.compile(r"`([^`]+?)`")
_MD_LINK_RE = re.compile(r"\[([^\]]+?)\]\(([^)]+?)\)")


def _md_inline(s: str) -> str:
    """Minimal inline markdown → HTML (bold, italic, code, links)."""
    s = h(s)
    s = _MD_LINK_RE.sub(r'<a href="\2">\1</a>', s)
    s = _MD_BOLD_RE.sub(r"<strong>\1</strong>", s)
    s = _MD_ITALIC_RE.sub(r"<em>\1</em>", s)
    s = _MD_CODE_RE.sub(r"<code>\1</code>", s)
    return s


def _md_to_html_body(md: str) -> str:
    """Block-level markdown → HTML. Just enough for the daily brief output."""
    out: list[str] = []
    in_list = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append("")
            continue
        h_match = _MD_HEADING_RE.match(line)
        if h_match:
            if in_list:
                out.append("</ul>")
                in_list = False
            level = len(h_match.group(1))
            level = min(max(level, 1), 6)
            out.append(f"<h{level}>{_md_inline(h_match.group(2))}</h{level}>")
            continue
        b_match = _MD_BULLET_RE.match(line)
        if b_match:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"  <li>{_md_inline(b_match.group(2))}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        out.append(f"<p>{_md_inline(line.strip())}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>RB Daily Brief — {date}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
         max-width: 760px; margin: 2em auto; padding: 0 1em; color: #222; line-height: 1.45; }}
  h1, h2, h3 {{ line-height: 1.2; }}
  h1 {{ font-size: 1.7em; border-bottom: 1px solid #ddd; padding-bottom: 0.3em; }}
  h2 {{ font-size: 1.25em; margin-top: 1.6em; }}
  h3 {{ font-size: 1.05em; }}
  ul {{ padding-left: 1.4em; }}
  li {{ margin: 0.18em 0; }}
  code {{ background: #f4f4f4; padding: 0 0.25em; border-radius: 3px; font-size: 0.95em; }}
  .meta {{ color: #777; font-size: 0.9em; }}
  .footer {{ margin-top: 3em; color: #888; font-size: 0.85em; border-top: 1px solid #eee; padding-top: 1em; }}
</style>
</head>
<body>
<p class="meta">Published {generated_at} · RB operational layer</p>
{body}
<p class="footer">Generated by Relationship Builder. Sources: relationship-graph + connector overlays. The morning brief opens the day; the 16:30 closeout closes it.</p>
</body>
</html>
"""


def render_html(md: str, *, today: date, generated_at: str) -> str:
    return _HTML_TEMPLATE.format(
        date=h(today.isoformat()),
        generated_at=h(generated_at),
        body=_md_to_html_body(md),
    )


# -----------------------------------------------------------------------------
# Email composition
# -----------------------------------------------------------------------------

def _clean_one_line(value: object, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _sections(report: dict) -> dict:
    return (report.get("canonical_brief") or {}).get("sections") or {}


def _top_priority_items(report: dict, n: int = 3) -> list[dict]:
    sections = _sections(report)
    out: list[dict] = []
    for item in (sections.get("top_priorities_today") or []):
        if item.get("disposition") == "act_today":
            out.append(item)
        if len(out) >= n:
            break
    if len(out) < n:
        for item in (sections.get("morning_command_center") or []):
            if item.get("disposition") == "act_today":
                title = item.get("title", "")
                if title and all(title != x.get("title") for x in out):
                    out.append(item)
            if len(out) >= n:
                break
    return out[:n]


def _top_priority_titles(report: dict, n: int = 3) -> list[str]:
    return [item.get("title", "") for item in _top_priority_items(report, n=n)]


def _top_priority_lines(report: dict, n: int = 3) -> list[str]:
    lines: list[str] = []
    for item in _top_priority_items(report, n=n):
        title = _clean_one_line(item.get("title"), limit=90)
        action = _clean_one_line(item.get("recommended_action"), limit=130)
        summary = _clean_one_line(item.get("summary"), limit=150)
        proof = " / ".join(
            x for x in [
                str(item.get("grounding") or ""),
                str(item.get("freshness") or ""),
                f"confidence {item.get('confidence')}" if item.get("confidence") else "",
            ]
            if x
        )
        if summary and action:
            detail = f"{summary} Next: {action}"
        else:
            detail = action or summary
        detail = _clean_one_line(detail, limit=210)
        suffix = f" ({proof})" if proof else ""
        lines.append(f"{title}: {detail}{suffix}" if detail else f"{title}{suffix}")
    return lines


def _ask_todd_lines(report: dict, n: int = 2) -> list[str]:
    sections = (report.get("canonical_brief") or {}).get("sections") or {}
    # Prefer Morning Command Center's act_today items; fall back to top_priorities.
    out: list[str] = []
    for item in (sections.get("morning_command_center") or []):
        if item.get("disposition") == "ask_todd":
            title = _clean_one_line(item.get("title"), limit=80)
            if "publication" in title.lower() or "destination" in title.lower():
                continue
            action = _clean_one_line(item.get("recommended_action"), limit=140)
            out.append(f"{title}: {action}" if action else title)
        if len(out) >= n:
            break
    return out[:n]


def _signal_health_summary(report: dict) -> str:
    prep = report.get("daily_prep_summary") or {}
    totals = prep.get("totals") or {}
    sources = totals.get("sources_scanned", 0)
    fresh_missing = totals.get("stale_or_missing_sources", 0)
    ri = totals.get("relationship_signals_detected", 0)
    if not sources:
        return "Source health: no scan summary."
    if fresh_missing:
        return (
            f"Source health: {sources} sources scanned; {fresh_missing} stale or missing; "
            f"{ri} RI signal(s) detected."
        )
    return f"Source health: {sources} sources scanned; all fresh; {ri} RI signal(s) detected."


def _stale_source_names(report: dict, n: int = 5) -> list[str]:
    prep = report.get("daily_prep_summary") or {}
    rows = prep.get("sources") or prep.get("source_rows") or []
    names: list[str] = []
    for row in rows:
        state = " ".join(str(row.get(k, "")) for k in ("status", "scan_status", "freshness", "result"))
        if "stale" in state or "missing" in state:
            name = row.get("source") or row.get("source_name") or row.get("name")
            if name:
                names.append(str(name))
        if len(names) >= n:
            break
    return names


def compose_email(report: dict, *, today: Optional[date] = None) -> dict:
    """Return {subject, text_body, html_body, cta_url, cta_status}.

    Body is intentionally short — doorstep notice, not the newspaper.
    """
    today = today or date.today()
    weekday = today.strftime("%A")
    iso = today.isoformat()
    pretty_date = today.strftime("%B %-d, %Y") if hasattr(today, "strftime") else iso
    subject = f"RB Daily Brief backup is ready - {weekday}, {iso}"

    top_titles = _top_priority_titles(report)
    top_lines = _top_priority_lines(report)
    ask_lines = _ask_todd_lines(report)
    signal_line = _signal_health_summary(report)
    stale_names = _stale_source_names(report)
    chatgpt_url = _resolve_chatgpt_brief_url()
    url = chatgpt_url or public_url_for(today, latest=True) or public_url_for(today)
    cta_status = "ready" if url else "pending_destination"
    cta_destination = "chatgpt" if chatgpt_url else ("operational_layer" if url else "pending")
    cta_text = url or "Full ChatGPT brief link is not configured yet."

    # Text body
    text_lines: list[str] = []
    text_lines.append(f"RB Daily Brief backup — {weekday}, {pretty_date}")
    text_lines.append("Preferred delivery is the native ChatGPT Task email with a View message button.")
    text_lines.append("")
    if top_titles:
        text_lines.append(f"Start here: {top_titles[0]}.")
        if len(top_titles) > 1:
            text_lines.append("")
            text_lines.append("On deck:")
            for i, t in enumerate(top_titles[1:3], 1):
                text_lines.append(f"  {i}. {t}")
    else:
        text_lines.append("Start here: no act-today item crossed the threshold.")
    if ask_lines:
        text_lines.append("")
        text_lines.append("Needs your call in the full brief.")
    text_lines.append("")
    text_lines.append(signal_line)
    if stale_names:
        text_lines.append(f"Under-instrumented: {', '.join(stale_names)}.")
    text_lines.append("")
    if cta_destination in ("chatgpt", "pending"):
        text_lines.append("Backup link — open Relationship Bridge 9.0:")
    else:
        text_lines.append("Open the operational layer:")
    text_lines.append(f"  {cta_text}")
    if cta_destination == "chatgpt":
        text_lines.append("")
        text_lines.append("Important: this backup link opens the RB cockpit; it cannot auto-run the brief.")
        text_lines.append('Paste/send this exact command: "Show today’s RB Daily Brief."')
    text_lines.append("")
    text_lines.append("Full brief has the action board, evidence, and draft-ready next steps.")
    text_body = "\n".join(text_lines) + "\n"

    # HTML body (mirrors text — short and link-forward)
    if top_titles:
        top_html = f"<p><strong>Start here:</strong> {h(top_titles[0])}.</p>"
        if len(top_titles) > 1:
            top_html += "<p><strong>On deck:</strong> " + h(" | ".join(top_titles[1:3])) + "</p>"
    else:
        top_html = "<p><strong>Start here:</strong> no act-today item crossed the threshold.</p>"
    ask_html = "<p><strong>Needs your call:</strong> see full brief.</p>" if ask_lines else ""
    if url:
        label = "Open Relationship Bridge 9.0" if cta_destination == "chatgpt" else "Open the RB operational layer"
        cta_html = (
            f'<p><a href="{h(url)}" '
            'style="display:inline-block;background:#111;color:#fff;text-decoration:none;'
            'padding:12px 20px;border-radius:999px;font-weight:700">'
            f'{h(label)}</a></p>'
        )
    else:
        cta_html = f"<p><em>{h(cta_text)}</em></p>"
    prompt_html = (
        '<p><strong>Important:</strong> this backup link opens the RB cockpit; it cannot auto-run the brief.</p>'
        '<p><strong>Paste/send this exact command:</strong> "Show today’s RB Daily Brief."</p>'
        if cta_destination == "chatgpt" else ""
    )
    html_body = (
        f"<h1>RB Daily Brief backup — {h(weekday)}, {h(pretty_date)}</h1>"
        '<p style="color:#666">Preferred delivery is the native ChatGPT Task email with a '
        '<strong>View message</strong> button.</p>'
        f"{top_html}"
        f"{ask_html}"
        f"<p>{h(signal_line)}</p>"
        f"{'<p>Under-instrumented: ' + h(', '.join(stale_names)) + '.</p>' if stale_names else ''}"
        f"{cta_html}"
        f"{prompt_html}"
        "<p style=\"color:#888;font-size:0.9em\">Backup doorbell only. Open ChatGPT for the action board.</p>"
    )

    return {
        "subject": subject,
        "text_body": text_body,
        "html_body": html_body,
        "cta_url": url,
        "cta_status": cta_status,
        "cta_destination": cta_destination,
        "top_priorities": _top_priority_titles(report),
        "top_priority_lines": top_lines,
        "ask_todd_lines": ask_lines,
        "signal_health": signal_line,
        "stale_or_missing_sources": stale_names,
        "recipient_settings_key": "daily_briefing.delivery.recipient",
    }


# -----------------------------------------------------------------------------
# Publication (the artifact writer)
# -----------------------------------------------------------------------------

def _today_md_path() -> Path:
    return core.SYSTEM_DIR / "today.md"


def publish_brief(today: Optional[date] = None, *,
                  dry_run: bool = True,
                  report: Optional[dict] = None) -> dict:
    """Write three files for `today` plus refresh latest.html.

    The brief markdown is the canonical `system/today.md` — if it doesn't
    exist yet (e.g., the morning regen hasn't fired), we render from the
    in-memory report dict to keep the publish step self-contained.
    """
    today = today or date.today()
    if report is None:
        import daily_brief  # local to avoid cycle
        report = daily_brief.build_report(today)

    md_path = _today_md_path()
    if md_path.exists():
        md = md_path.read_text(encoding="utf-8")
    else:
        # Fall back to the renderer so publish never blocks on file existence.
        import daily_brief  # local
        md = daily_brief.render_today_md(report)

    generated_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    html = render_html(md, today=today, generated_at=generated_at)
    canonical_json = (report.get("canonical_brief") or {})
    canonical_json_text = json.dumps(
        {
            "today": today.isoformat(),
            "generated_at": generated_at,
            "canonical_brief": canonical_json,
            "counts": {
                "active_threads": len(report.get("active_threads") or []),
                "open_loops_total": sum(
                    len(report.get("loops", {}).get(k) or [])
                    for k in ("overdue", "due_today", "this_week", "future")
                ),
            },
        },
        default=str, indent=2,
    )

    target_dir = PUBLISHED_DIR / today.isoformat()
    md_file = target_dir / "index.md"
    html_file = target_dir / "index.html"
    json_file = target_dir / "brief.json"
    latest_file = PUBLISHED_DIR / "latest.html"
    latest_json_file = PUBLISHED_DIR / "latest_brief.json"

    rel = lambda p: str(p.relative_to(core.PROJECT_DIR))
    sizes = {
        "index.md": len(md.encode("utf-8")),
        "index.html": len(html.encode("utf-8")),
        "brief.json": len(canonical_json_text.encode("utf-8")),
    }

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "target_dir": rel(target_dir),
            "files": {
                "index_md": rel(md_file),
                "index_html": rel(html_file),
                "brief_json": rel(json_file),
                "latest_html": rel(latest_file),
                "latest_brief_json": rel(latest_json_file),
            },
            "bytes": sizes,
            "public_url": public_url_for(today),
            "public_url_latest": public_url_for(today, latest=True),
        }

    target_dir.mkdir(parents=True, exist_ok=True)
    md_file.write_text(md, encoding="utf-8")
    html_file.write_text(html, encoding="utf-8")
    json_file.write_text(canonical_json_text, encoding="utf-8")
    # Snapshot latest.html before overwrite (defense — never lose the prior pointer).
    if latest_file.exists():
        core.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        snap = core.SNAPSHOTS_DIR / (
            "latest.pre-publish-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".html"
        )
        shutil.copy2(latest_file, snap)
    shutil.copy2(html_file, latest_file)
    shutil.copy2(json_file, latest_json_file)
    return {
        "ok": True,
        "dry_run": False,
        "target_dir": rel(target_dir),
        "files": {
            "index_md": rel(md_file),
            "index_html": rel(html_file),
            "brief_json": rel(json_file),
            "latest_html": rel(latest_file),
            "latest_brief_json": rel(latest_json_file),
        },
        "bytes": sizes,
        "public_url": public_url_for(today),
        "public_url_latest": public_url_for(today, latest=True),
    }


# -----------------------------------------------------------------------------
# Email send (stub — host-side OAuth/SMTP wiring required)
# -----------------------------------------------------------------------------

def send_email(composed: dict, *, transport: Optional[str] = None,
               confirm: bool = False) -> dict:
    """Attempt to send the composed email. Honest about transport status.

    The morning email contract lives in settings.json. The actual SMTP/Gmail
    OAuth wiring is host-side; this function reports `pending_transport`
    when nothing's configured and refuses to fabricate success.

    Supported transport hooks (env-var based — keep secrets out of code):
      * 'smtp'  → uses RB_SMTP_HOST, RB_SMTP_PORT, RB_SMTP_USER, RB_SMTP_PASS
      * 'eml'   → drops a .eml file at $RB_EML_DROP_DIR for a desktop mail
                  client to pick up
      * None    → returns pending_transport without sending

    Per the smoke-test-mutations rule, smoke tests never reach this path
    with confirm=true.
    """
    if not confirm:
        return {"sent": False, "status": "preview", "reason": "confirm=false"}

    try:
        delivery = ((core.load_settings().get("daily_briefing") or {})
                    .get("delivery") or {})
    except Exception:  # noqa: BLE001
        delivery = {}
    if not delivery.get("email_enabled", False):
        return {
            "sent": False,
            "status": "disabled",
            "reason": "daily_briefing.delivery.email_enabled=false",
        }
    if os.environ.get("RB_ALLOW_BACKUP_EMAIL") != "1":
        return {
            "sent": False,
            "status": "disabled",
            "reason": "set RB_ALLOW_BACKUP_EMAIL=1 to send external RB backup email",
        }

    if composed.get("cta_status") != "ready":
        return {
            "sent": False,
            "status": "pending_destination",
            "reason": (
                "ChatGPT daily-brief URL is not configured. Set "
                "daily_briefing.delivery.chatgpt_brief_url in settings.json "
                f"or {CHATGPT_BRIEF_URL_ENV} before sending user-facing email."
            ),
        }

    if transport is None:
        if os.environ.get("RB_SMTP_HOST"):
            transport = "smtp"
        elif os.environ.get("RB_EML_DROP_DIR"):
            transport = "eml"
        else:
            return {
                "sent": False,
                "status": "pending_transport",
                "reason": (
                    "No transport configured. Set RB_SMTP_HOST/PORT/USER/PASS "
                    "for direct send, or RB_EML_DROP_DIR for .eml drop. "
                    "See system/CLAUDE_FOLLOWUP_MORNING_DELIVERY_PIPELINE.md."
                ),
            }

    # Resolve recipient
    try:
        s = core.load_settings()
        recipient = (((s.get("daily_briefing") or {}).get("delivery") or {})
                     .get("recipient"))
    except Exception:  # noqa: BLE001
        recipient = None
    if not recipient:
        return {"sent": False, "status": "no_recipient",
                "reason": "settings.json daily_briefing.delivery.recipient not set"}

    if transport == "eml":
        drop = Path(os.environ["RB_EML_DROP_DIR"]).expanduser()
        try:
            drop.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            return {"sent": False, "status": "eml_drop_dir_error",
                    "reason": f"{type(exc).__name__}: {exc}"}
        eml = _build_eml(composed, recipient=recipient)
        eml_path = drop / f"rb-daily-brief-{date.today().isoformat()}.eml"
        eml_path.write_text(eml, encoding="utf-8")
        return {"sent": True, "status": "eml_dropped", "path": str(eml_path)}

    if transport == "smtp":
        try:
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
        except ImportError as exc:
            return {"sent": False, "status": "smtp_import_failed",
                    "reason": f"{type(exc).__name__}: {exc}"}
        host = os.environ.get("RB_SMTP_HOST")
        port = int(os.environ.get("RB_SMTP_PORT", "587"))
        user = os.environ.get("RB_SMTP_USER")
        pwd = os.environ.get("RB_SMTP_PASS")
        if not (host and user and pwd):
            return {"sent": False, "status": "smtp_env_incomplete",
                    "reason": "set RB_SMTP_HOST/PORT/USER/PASS"}
        msg = MIMEMultipart("alternative")
        msg["Subject"] = composed["subject"]
        msg["From"] = user
        msg["To"] = recipient
        msg.attach(MIMEText(composed["text_body"], "plain", "utf-8"))
        msg.attach(MIMEText(composed["html_body"], "html", "utf-8"))
        try:
            with smtplib.SMTP(host, port, timeout=30) as srv:
                srv.starttls()
                srv.login(user, pwd)
                srv.sendmail(user, [recipient], msg.as_string())
        except Exception as exc:  # noqa: BLE001
            return {"sent": False, "status": "smtp_send_failed",
                    "reason": f"{type(exc).__name__}: {exc}"}
        return {"sent": True, "status": "smtp_sent", "recipient": recipient}

    return {"sent": False, "status": "unknown_transport", "reason": transport}


def _build_eml(composed: dict, *, recipient: str) -> str:
    """Minimal multipart/alternative .eml so the desktop mail client renders both."""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    msg = MIMEMultipart("alternative")
    msg["Subject"] = composed["subject"]
    msg["From"] = "rb@local"
    msg["To"] = recipient
    msg.attach(MIMEText(composed["text_body"], "plain", "utf-8"))
    msg.attach(MIMEText(composed["html_body"], "html", "utf-8"))
    return msg.as_string()


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", help="ISO date for publication (default: today).")
    p.add_argument("--write", action="store_true",
                   help="Materialize the publication artifacts. Requires --confirm.")
    p.add_argument("--send", action="store_true",
                   help="Send the morning email. Requires --confirm + configured transport.")
    p.add_argument("--confirm", action="store_true",
                   help="Second-factor flag required with --write and --send.")
    p.add_argument("--transport", choices=["smtp", "eml"], default=None,
                   help="Email transport override (default: auto-detect from env).")
    p.add_argument("--json", action="store_true", help="Emit JSON output.")
    p.add_argument("--smoke", action="store_true",
                   help="In-memory regression — no I/O, no send.")
    args = p.parse_args()

    if args.smoke:
        return _smoke()

    today = date.fromisoformat(args.date) if args.date else date.today()
    import daily_brief  # local
    report = daily_brief.build_report(today)

    if args.write:
        if not args.confirm:
            print("ERROR: --write requires --confirm.", file=sys.stderr)
            return 2
        result = publish_brief(today=today, dry_run=False, report=report)
    else:
        result = publish_brief(today=today, dry_run=True, report=report)

    composed = compose_email(report, today=today)
    out = {"publish": result, "email": composed}

    if args.send:
        if not args.confirm:
            print("ERROR: --send requires --confirm.", file=sys.stderr)
            return 2
        send_result = send_email(composed, transport=args.transport, confirm=True)
        out["send"] = send_result

    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    print(f"publish: {result['target_dir']}{' (dry-run)' if result.get('dry_run') else ''}")
    for label, path in result["files"].items():
        print(f"  {label}: {path}")
    print()
    print(f"email subject: {composed['subject']}")
    print(f"email CTA:    {composed['cta_url'] or '(pending_destination — see --help)'}")
    print(f"email CTA status: {composed['cta_status']}")
    if "send" in out:
        sr = out["send"]
        print()
        print(f"send: status={sr['status']}{(' — ' + sr.get('reason','')) if sr.get('reason') else ''}")
    return 0


# -----------------------------------------------------------------------------
# Smoke
# -----------------------------------------------------------------------------

def _smoke() -> int:
    """In-memory regression. No file I/O, no email send."""
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK" if cond else "FAIL"
        print(f"  {mark}   {msg}")
        if not cond:
            failures.append(msg)

    today = date(2026, 5, 21)

    # Synthetic report with the shape compose_email + publish need.
    synthetic_report = {
        "today": today.isoformat(),
        "weekday": "Thursday",
        "canonical_brief": {
            "sections": {
                "morning_command_center": [
                    {"title": "Today's operating board", "disposition": "act_today"},
                    {"title": "Meeting prep queue", "disposition": "ask_todd"},
                    {"title": "Waiting-on state", "disposition": "act_today"},
                    {"title": "Loop risk", "disposition": "act_today"},
                ],
                "top_priorities_today": [
                    {
                        "title": "Genius/Global Payments thread",
                        "summary": "Post-screen capture is overdue.",
                        "recommended_action": "Resolve the post-screen capture and unblock the role thread.",
                        "disposition": "act_today",
                        "grounding": "manual_user_provided",
                        "freshness": "manual_context",
                        "confidence": "medium",
                    },
                ],
            },
        },
        "daily_prep_summary": {
            "totals": {
                "sources_scanned": 10,
                "stale_or_missing_sources": 2,
                "relationship_signals_detected": 3,
            },
            "sources": [
                {"source": "email", "status": "scanned", "freshness": "stale_or_missing", "result": "refresh recommended"},
                {"source": "calendar", "status": "scanned", "freshness": "stale_or_missing", "result": "refresh recommended"},
            ],
        },
        "active_threads": [],
        "loops": {"overdue": [], "due_today": [], "this_week": [], "future": []},
    }

    # compose_email — no URL configured, returns pending_destination.
    # Isolate the smoke from live settings.json; production may have a
    # ChatGPT cockpit URL configured, but this fixture needs the missing-CTA
    # branch.
    real_resolve = _resolve_url_base
    real_chatgpt = globals()["_resolve_chatgpt_brief_url"]
    globals()["_resolve_url_base"] = lambda: None  # type: ignore
    globals()["_resolve_chatgpt_brief_url"] = lambda: None  # type: ignore
    try:
        composed = compose_email(synthetic_report, today=today)
    finally:
        globals()["_resolve_url_base"] = real_resolve  # type: ignore
        globals()["_resolve_chatgpt_brief_url"] = real_chatgpt  # type: ignore
    ck(composed["subject"] == "RB Daily Brief backup is ready - Thursday, 2026-05-21",
       f"subject template populated (got {composed['subject']!r})")
    ck(composed["cta_status"] == "pending_destination",
       "no URL configured → cta_status=pending_destination")
    ck("Genius/Global Payments thread" in composed["text_body"],
       "text body lists concrete top-priority titles")
    ck("Start here:" in composed["text_body"],
       "text body uses doorbell start-here framing")
    ck("native ChatGPT Task email" in composed["text_body"],
       "backup email points to native ChatGPT Task as preferred UX")
    ck("2 stale or missing" in composed["text_body"],
       "text body surfaces stale-source health")
    ck("Full ChatGPT brief link is not configured yet" in composed["text_body"],
       "pending CTA is user-readable, not config spew")
    ck("Under-instrumented: email, calendar." in composed["text_body"],
       "text body names under-instrumented sources")
    ck("<h1>" in composed["html_body"] and "Start here:" in composed["html_body"],
       "html body renders heading + start-here frame")

    # compose_email with ChatGPT URL configured
    globals()["_resolve_chatgpt_brief_url"] = lambda: "https://chatgpt.com/g/g-example-rb"  # type: ignore
    try:
        composed_chatgpt = compose_email(synthetic_report, today=today)
    finally:
        globals()["_resolve_chatgpt_brief_url"] = real_chatgpt  # type: ignore
    ck(composed_chatgpt["cta_status"] == "ready", "ChatGPT URL configured → cta_status=ready")
    ck(composed_chatgpt["cta_destination"] == "chatgpt", "ChatGPT URL wins as CTA destination")
    ck("Backup link" in composed_chatgpt["text_body"]
       and "Show today’s RB Daily Brief" in composed_chatgpt["text_body"],
       "text body explains GPT open + daily-brief command")
    ck("cannot auto-run the brief" in composed_chatgpt["text_body"],
       "text body makes the backup deep-link limitation explicit")
    ck("https://chatgpt.com/g/g-example-rb" in composed_chatgpt["html_body"],
       "html body embeds the ChatGPT link")
    ck("View message" in composed_chatgpt["html_body"],
       "html body names the native View message UX")

    # compose_email with URL configured
    globals()["_resolve_url_base"] = lambda: "https://rb.example.com"  # type: ignore
    globals()["_resolve_chatgpt_brief_url"] = lambda: None  # type: ignore
    try:
        composed2 = compose_email(synthetic_report, today=today)
    finally:
        globals()["_resolve_url_base"] = real_resolve  # type: ignore
        globals()["_resolve_chatgpt_brief_url"] = real_chatgpt  # type: ignore
    ck(composed2["cta_status"] == "ready", "URL configured → cta_status=ready")
    ck(composed2["cta_url"] is not None and composed2["cta_url"].startswith("https://rb.example.com"),
       f"cta_url uses configured base (got {composed2['cta_url']!r})")
    ck("https://rb.example.com" in composed2["html_body"],
       "html body embeds the live link")

    # publish_brief dry-run
    real_md_path = _today_md_path
    real_render = None
    # Avoid the daily_brief import in dry_run by passing report and bypassing today.md.
    # Force fallback to renderer by simulating no on-disk today.md.
    real_exists = Path.exists
    Path.exists = lambda self: False if "today.md" in str(self) else real_exists(self)  # type: ignore
    try:
        # Inject a minimal renderer instead of real daily_brief import.
        import daily_brief as _db
        real_render = _db.render_today_md
        _db.render_today_md = lambda r: "# Today — 2026-05-21\n\nBrief body.\n"  # type: ignore
        result = publish_brief(today=today, dry_run=True, report=synthetic_report)
    finally:
        Path.exists = real_exists  # type: ignore
        if real_render is not None:
            _db.render_today_md = real_render  # type: ignore

    ck(result["dry_run"] is True, "publish dry_run flagged")
    ck(result["target_dir"] == "system/published/daily/2026-05-21",
       f"target dir uses date scheme (got {result['target_dir']!r})")
    ck(result["files"]["index_html"].endswith("index.html"), "index.html path present")
    ck(result["files"]["index_md"].endswith("index.md"), "index.md path present")
    ck(result["files"]["brief_json"].endswith("brief.json"), "brief.json path present")
    ck(result["files"]["latest_html"].endswith("latest.html"), "latest.html path present")
    ck(result["bytes"]["index.html"] > 0, "html has non-zero bytes")
    ck(result["bytes"]["index.md"] > 0, "md has non-zero bytes")
    ck(result["bytes"]["brief.json"] > 0, "json has non-zero bytes")

    # send_email confirm=false → preview, never sends
    sr = send_email(composed, confirm=False)
    ck(sr.get("sent") is False, "send_email confirm=false returns sent=false")
    ck(sr.get("status") == "preview", "send_email confirm=false reports status=preview")

    # send_email confirm=true is hard-disabled unless backup email is explicitly enabled.
    sr_pending = send_email(composed, confirm=True)
    ck(sr_pending.get("sent") is False, "backup disabled → sent=false")
    ck(sr_pending.get("status") == "disabled",
       f"backup disabled → status=disabled (got {sr_pending.get('status')!r})")

    # Even with URL/transport hints present, backup email remains disabled by default.
    saved_env = {k: os.environ.pop(k, None) for k in ("RB_SMTP_HOST", "RB_EML_DROP_DIR")}
    try:
        sr2 = send_email(composed_chatgpt, confirm=True)
    finally:
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v
    ck(sr2.get("sent") is False, "backup disabled with URL → sent=false")
    ck(sr2.get("status") == "disabled",
       f"backup disabled with URL → status=disabled (got {sr2.get('status')!r})")
    ck("email_enabled=false" in (sr2.get("reason") or ""),
       "disabled reason names the settings flag")

    # HTML escaping is honest
    html = _md_to_html_body("# Hello <script>alert('x')</script>\n- a & b\n")
    ck("<script>" not in html, "headings escape script tags")
    ck("&amp;" in html or "&" not in html, "ampersand escaped")

    # public_url_for honors env override
    os.environ[PUBLIC_URL_BASE_ENV] = "https://rb-test.example.com/"
    try:
        u = public_url_for(today, latest=True)
    finally:
        del os.environ[PUBLIC_URL_BASE_ENV]
    ck(u == "https://rb-test.example.com/daily/latest.html",
       f"env override produces stable latest URL (got {u!r})")

    print(f"--- publish smoke complete: {len(failures)} failure(s) ---")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
