"""external_brief_renderer.py — Layer 2 HTML email renderer for /daily_brief/external.

Produces a styled, mobile-responsive HTML email from the external brief payload.
No Jinja2 — pure Python string building.
"""
from __future__ import annotations

import html
import os
import re
from datetime import date as _date

SENDER_NAME: str = os.environ.get("EXTERNAL_BRIEF_SENDER_NAME", "Restaurant Intelligence")

# ── colour constants ──────────────────────────────────────────────────────────
_NAVY = "#1a2744"
_BLUE = "#2563eb"
_GRAY_BG = "#f3f4f6"
_DIVIDER = "#e5e7eb"
_TEXT = "#111827"
_MUTED = "#6b7280"
_FALLBACK_BG = "#f9fafb"

# ── badge colour map ──────────────────────────────────────────────────────────
_BADGE_COLORS: dict[str, str] = {
    "EARNINGS": "#0f766e",
    "EXEC MOVE": "#7c3aed",
    "FUNDING": "#b45309",
    "M&A": "#be185d",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    return html.escape(str(text or ""), quote=True)


def _trim(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text


def _first_sentence(text: str) -> str:
    text = str(text or "").strip()
    m = re.search(r"[.!?]", text)
    if m:
        return text[: m.start() + 1]
    return text


def _is_non_homepage_url(url: str) -> bool:
    """Return True if url has a path beyond just the domain root."""
    if not url:
        return False
    without_scheme = re.sub(r"^https?://", "", url, flags=re.I)
    path_part = re.sub(r"^[^/]+", "", without_scheme)
    return bool(path_part and path_part.strip("/"))


def _badge_html(badge_text: str) -> str:
    color = _BADGE_COLORS.get(badge_text.upper(), _BLUE)
    return (
        f'<span style="display:inline-block;background:{color};color:#fff;'
        f'font-size:10px;font-weight:700;letter-spacing:.5px;padding:2px 7px;'
        f'border-radius:3px;text-transform:uppercase;margin-right:6px;">'
        f"{_esc(badge_text)}</span>"
    )


def _section_header(title: str) -> str:
    return (
        f'<tr><td style="padding:24px 32px 8px;">'
        f'<h2 style="margin:0;font-size:14px;font-weight:700;letter-spacing:.8px;'
        f'text-transform:uppercase;color:{_BLUE};">{title}</h2>'
        f'<div style="height:2px;background:{_DIVIDER};margin-top:8px;"></div>'
        f"</td></tr>\n"
    )


# ── section renderers ─────────────────────────────────────────────────────────

def _render_headline_rows(items: list, max_count: int = 5) -> str:
    """Render up to max_count headline items as linked blocks."""
    rendered: list[str] = []
    for item in items:
        if len(rendered) >= max_count:
            break
        extras = item.get("extras") or {}
        url = (
            extras.get("source_url")
            or extras.get("url")
            or item.get("source_url")
            or item.get("url")
            or ""
        )
        if not _is_non_homepage_url(url):
            continue

        title = _trim(item.get("title") or "", 120)
        source = str(extras.get("source") or extras.get("publication") or "")
        pub_date = str(extras.get("pub_date") or extras.get("published_date") or "")
        why = _trim(
            item.get("why_it_matters")
            or _first_sentence(item.get("summary") or ""),
            150,
        )

        meta_parts = [p for p in [source, pub_date] if p]
        meta_html = (
            f'<span style="color:{_MUTED};font-size:12px;">'
            + _esc(" | ".join(meta_parts))
            + "</span>"
            if meta_parts else ""
        )

        rendered.append(
            f'<tr><td style="padding:12px 32px;">'
            f'<a href="{_esc(url)}" style="font-size:15px;font-weight:600;color:{_BLUE};'
            f'text-decoration:none;line-height:1.4;">{_esc(title)}</a><br>'
            + (f"{meta_html}<br>" if meta_html else "")
            + (
                f'<span style="font-size:13px;color:{_TEXT};line-height:1.5;">'
                f"{_esc(why)}</span>"
                if why
                else ""
            )
            + f"</td></tr>\n"
        )

    return "".join(rendered)


def _render_rt_fallback(item: dict) -> str:
    summary = _esc(item.get("summary") or "No material tech headlines this cycle.")
    return (
        f'<tr><td style="padding:12px 32px;">'
        f'<div style="background:{_FALLBACK_BG};border:1px solid {_DIVIDER};'
        f'border-radius:6px;padding:14px 18px;">'
        f'<span style="color:{_MUTED};font-size:13px;font-style:italic;">'
        f"No material tech headlines this cycle.</span><br>"
        f'<span style="color:{_MUTED};font-size:12px;">{summary}</span>'
        f"</div></td></tr>\n"
    )


def _render_earnings_rows(items: list, max_count: int = 5) -> str:
    rendered: list[str] = []
    for item in items:
        if len(rendered) >= max_count:
            break
        extras = item.get("extras") or {}
        url = (
            extras.get("source_url")
            or extras.get("url")
            or item.get("source_url")
            or item.get("url")
            or ""
        )
        title = _trim(item.get("title") or "", 120)
        source = str(extras.get("source") or extras.get("publication") or "")
        pub_date = str(extras.get("pub_date") or extras.get("published_date") or "")
        badge_text = str(extras.get("signal_badge") or "").strip()

        meta_parts = [p for p in [source, pub_date] if p]
        meta_html = (
            f'<span style="color:{_MUTED};font-size:12px;">'
            + _esc(" | ".join(meta_parts))
            + "</span>"
            if meta_parts else ""
        )

        title_html = (
            f'<a href="{_esc(url)}" style="font-size:14px;font-weight:600;color:{_BLUE};'
            f'text-decoration:none;">{_esc(title)}</a>'
            if _is_non_homepage_url(url)
            else f'<span style="font-size:14px;font-weight:600;color:{_TEXT};">{_esc(title)}</span>'
        )

        rendered.append(
            f'<tr><td style="padding:10px 32px;">'
            + (_badge_html(badge_text) if badge_text else "")
            + title_html
            + "<br>"
            + (f"{meta_html}" if meta_html else "")
            + f"</td></tr>\n"
        )

    return "".join(rendered)


def _render_signals_rows(items: list, max_count: int = 3) -> str:
    _SKIP_PERSONAL = re.compile(r"\b(you|your|todd)\b", re.I)
    rendered: list[str] = []
    for item in items:
        if len(rendered) >= max_count:
            break
        title = item.get("title") or ""
        summary = item.get("summary") or ""
        if _SKIP_PERSONAL.search(title) or _SKIP_PERSONAL.search(summary):
            continue

        extras = item.get("extras") or {}
        evidence_list: list[str] = []
        if isinstance(extras.get("evidence"), list):
            evidence_list = [str(e) for e in extras["evidence"][:3]]
        elif summary:
            sents = re.split(r"(?<=[.!?])\s+", summary.strip())
            evidence_list = sents[:3]

        bullets = ""
        if evidence_list:
            bullets = (
                '<ul style="margin:6px 0 0 0;padding-left:18px;">'
                + "".join(
                    f'<li style="font-size:13px;color:{_TEXT};line-height:1.5;margin-bottom:4px;">'
                    f"{_esc(_trim(e, 200))}</li>"
                    for e in evidence_list
                )
                + "</ul>"
            )

        rendered.append(
            f'<tr><td style="padding:12px 32px;">'
            f'<div style="background:{_GRAY_BG};border-left:3px solid {_BLUE};'
            f'border-radius:0 6px 6px 0;padding:12px 16px;">'
            f'<p style="margin:0;font-size:14px;font-weight:700;color:{_TEXT};">'
            f"{_esc(_trim(title, 120))}</p>"
            + bullets
            + f"</div></td></tr>\n"
        )

    return "".join(rendered)


# ── main renderer ─────────────────────────────────────────────────────────────

def render_html_email(payload: dict) -> str:
    """Render external brief payload as a styled HTML email string."""
    date_str: str = payload.get("date") or str(_date.today())
    try:
        d = _date.fromisoformat(date_str)
        display_date = d.strftime("%B %-d, %Y")
    except (ValueError, AttributeError):
        display_date = date_str

    next_publish: str = payload.get("next_publish_date") or ""
    try:
        next_publish_display = _date.fromisoformat(next_publish).strftime("%B %-d, %Y")
    except (ValueError, AttributeError):
        next_publish_display = next_publish

    proof: dict = payload.get("proof") or {}
    industry_count: int = proof.get("restaurant_articles_scanned") or 0
    tech_count: int = proof.get("tech_articles_scanned") or 0
    total_scanned: int = industry_count + tech_count
    total_headlines: int = (
        len(payload.get("restaurant_industry_headlines") or [])
        + len(payload.get("restaurant_technology_headlines") or [])
    )

    industry_headlines: list = payload.get("restaurant_industry_headlines") or []
    tech_headlines: list = payload.get("restaurant_technology_headlines") or []
    earnings_items: list = payload.get("earnings_corporate") or []
    strategic_signals: list = payload.get("strategic_signals") or []

    sections_html = ""

    if payload.get("status") == "not_yet_published":
        sections_html += (
            f'<tr><td style="padding:24px 32px;">'
            f'<p style="margin:0;font-size:14px;color:{_TEXT};line-height:1.6;">'
            f"{_esc(payload.get('message') or 'No edition has been published yet.')}"
            + (f" Next edition: {_esc(next_publish_display)}." if next_publish_display else "")
            + "</p></td></tr>\n"
        )
        return _wrap_email(display_date, sections_html, 0, 0)

    if industry_headlines:
        rows = _render_headline_rows(industry_headlines, max_count=5)
        if rows:
            sections_html += _section_header("Restaurant Industry")
            sections_html += rows

    if tech_headlines:
        first = tech_headlines[0]
        if (first.get("extras") or {}).get("fallback") is True:
            sections_html += _section_header("Restaurant Technology")
            sections_html += _render_rt_fallback(first)
        else:
            rows = _render_headline_rows(tech_headlines, max_count=5)
            if rows:
                sections_html += _section_header("Restaurant Technology")
                sections_html += rows

    if earnings_items:
        rows = _render_earnings_rows(earnings_items, max_count=5)
        if rows:
            sections_html += _section_header("Earnings &amp; Corporate")
            sections_html += rows

    if strategic_signals:
        rows = _render_signals_rows(strategic_signals, max_count=3)
        if rows:
            sections_html += _section_header("Strategic Signals")
            sections_html += rows

    return _wrap_email(display_date, sections_html, total_scanned, total_headlines)


def _wrap_email(display_date: str, sections_html: str, total_scanned: int, total_headlines: int) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(SENDER_NAME)} — {_esc(display_date)}</title>
<style>
  body {{ margin:0; padding:0; background:#f0f2f5; font-family:Arial,Helvetica,sans-serif; }}
  a {{ color:{_BLUE}; }}
  @media only screen and (max-width:620px) {{
    .outer {{ padding:0 !important; }}
    .card {{ border-radius:0 !important; }}
  }}
</style>
</head>
<body>
<div class="outer" style="padding:24px 16px;">
<table class="card" width="100%" cellpadding="0" cellspacing="0" border="0"
  style="max-width:600px;margin:0 auto;background:#fff;border-radius:8px;
         overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);">

  <!-- HEADER -->
  <tr>
    <td style="background:{_NAVY};padding:28px 32px;">
      <p style="margin:0;font-size:20px;font-weight:700;color:#fff;letter-spacing:.3px;">
        {_esc(SENDER_NAME)}
      </p>
      <p style="margin:6px 0 0;font-size:13px;color:#94a3b8;">{_esc(display_date)}</p>
    </td>
  </tr>

  {sections_html}

  <!-- FOOTER -->
  <tr>
    <td style="padding:24px 32px;border-top:1px solid {_DIVIDER};">
      <p style="margin:0 0 6px;font-size:12px;color:{_MUTED};">
        Scanned {total_scanned:,} sources &nbsp;|&nbsp;
        {total_headlines:,} headlines reviewed &nbsp;|&nbsp;
        {_esc(display_date)}
      </p>
      <p style="margin:0 0 6px;font-size:12px;color:{_MUTED};">
        This digest is produced by an automated intelligence system. Published Tuesdays and Fridays.
      </p>
      <p style="margin:0;font-size:12px;color:{_MUTED};">
        <a href="#" style="color:{_MUTED};">Unsubscribe</a>
      </p>
    </td>
  </tr>

</table>
</div>
</body>
</html>"""
