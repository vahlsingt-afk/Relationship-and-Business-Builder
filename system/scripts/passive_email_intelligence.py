#!/usr/bin/env python3
"""
passive_email_intelligence.py — harvest weak restaurant-industry signals from email.

Reads normalized Gmail thread caches in system/inbox/email*.json, detects recurring
industry newsletters, extracts headline-shaped subjects/snippets, scores strategic
relevance, classifies weak-signal themes, and writes a safe derived cache. It does
not persist raw email bodies.

For known newsletter senders (Payments Dive, QSR Magazine, NRN, etc.) fetch_google.py
now fetches the full message body on a second pass and attaches it as `body_text`.
When present, article titles and direct article URLs are extracted from the HTML before
falling back to subject/snippet. Deep-dive items now carry real clickable article links
rather than `blocked_no_article_link`.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402


CACHE_PATH = core.SYSTEM_DIR / ".cache" / "passive_email_intelligence.json"
REGISTRY_PATH = core.SYSTEM_DIR / ".cache" / "email_intelligence_sources.json"

INDUSTRY_SOURCE_HINTS = {
    "the rundown ai": "The Rundown AI",
    "therundown.ai": "The Rundown AI",
    "digital transactions": "Digital Transactions",
    "digitaltransactions.net": "Digital Transactions",
    "food on demand": "Food On Demand",
    "foodondemand.com": "Food On Demand",
    "qsr web": "QSR Web",
    "qsrweb.com": "QSR Web",
    "fsr magazine": "FSR Magazine",
    "restaurantbusinessonline.com": "Restaurant Business",
    "restaurantbusiness": "Restaurant Business",
    "nrn.com": "Nation's Restaurant News",
    "nation's restaurant news": "Nation's Restaurant News",
    "qsrmagazine.com": "QSR Magazine",
    "qsr magazine": "QSR Magazine",
    "restaurantdive.com": "Restaurant Dive",
    "restaurant dive": "Restaurant Dive",
    "technomic": "Technomic",
    "hospitalitytech.com": "Hospitality Technology",
    "hospitality technology": "Hospitality Technology",
    "restaurant technology network": "Restaurant Technology Network",
    "fast casual": "Fast Casual",
    "fsr magazine": "FSR Magazine",
    "modern restaurant management": "Modern Restaurant Management",
    "restaurantnews.com": "RestaurantNews.com",
    "the spoon": "The Spoon",
    "foodondemandnews": "Food On Demand",
    "paymentsdive": "Payments Dive",
    "divenewsletter.com": "Payments Dive",
}

INDUSTRY_KEYWORDS = (
    "restaurant", "qsr", "franchise", "franchisee", "operator", "foodservice",
    "drive-thru", "drive thru", "pos", "menu", "kitchen", "labor", "loyalty",
    "delivery", "brand", "unit", "stores", "closures", "automation", "robot",
    "ai", "inventory", "sweetgreen", "toast", "yum", "pizza hut", "mcdonald",
    "taco bell", "technomic", "nrn",
)

STRICT_INDUSTRY_KEYWORDS = (
    "restaurant", "restaurants", "qsr", "franchise", "franchisee", "franchisor",
    "foodservice", "drive-thru", "drive thru", "pos", "menu", "kitchen",
    "operator", "operators", "unit economics", "store closures", "restaurant tech",
    "technomic", "nrn", "qsr magazine",
)

JOB_RELEVANCE_KEYWORDS = (
    "chief revenue", "chief sales", "chief strategy", "cro", "cso",
    "vp sales", "head of sales", "customer success", "enterprise account",
    "key account", "strategic account", "revenue officer", "sales officer",
    "toast", "restaurant", "restaurants", "qsr", "foodservice",
)

SENT_RELEVANCE_KEYWORDS = (
    "interview", "hiring manager", "recruiter", "role", "opportunity",
    "customer success", "enterprise account", "strategic account", "demo",
    "global payments", "qu", "maho", "harri", "toast", "triton", "coates",
    "mcdonald", "restaurant", "restaurants", "foodservice", "qsr",
)

THEME_PATTERNS = [
    ("automation_failure", r"\b(robot|robotic|automation|automated|ai).{0,50}\b(shut|shuts|shutdown|failed|failure|errors|rollback|lawsuit|dumps|halts)\b"),
    ("automation_failure", r"\b(ending|ends|dumps|halts|shuts down|shutdown|rollback).{0,60}\b(ai|robot|robotic|automation|automated|inventory)\b"),
    ("ai_adoption", r"\b(ai|automation|robot|voice).{0,60}\b(launch|rollout|deploy|expands|investment|backbone|introduces)\b"),
    ("executive_movement", r"\b(names|appoints|hires|chief|ceo|cfo|coo|cmo|strategy officer|president)\b"),
    ("vendor_shutdown", r"\b(shuts down|shutdown|closes|bankrupt|insolv|ceases operations|folds)\b"),
    ("margin_pressure", r"\b(margin|profit|ebitda|cost pressure|food cost|labor cost|traffic|value fatigue|inflation)\b"),
    ("restaurant_closure", r"\b(closes|closed|exits|exit|bankruptcy|store closures|units closed)\b"),
    ("franchisee_resistance", r"\b(franchisee|franchisor|lawsuit|mandate|standardization|governance|association)\b"),
    ("saas_consolidation", r"\b(consolidation|acquires|merger|platform|suite|integrat|pos|payments)\b"),
    ("labor_pressure", r"\b(labor|wage|staffing|hiring|retention|scheduler|schedule)\b"),
    ("consumer_value_fatigue", r"\b(value|discount|traffic|consumer spending|guest count|low income|trade down)\b"),
    # Payments industry — added when Todd joined Global Payments (2026-06)
    ("payments_industry", r"\b(fiserv|stripe|adyen|payoneer|nuvei|square|visa|mastercard|apple pay|paypal|acqui|issuer|merchant services|processing fee|interchange|card network)\b"),
    ("payments_industry", r"\b(payment processor|payment platform|fintech|embedded payments|point.of.sale financing|bnpl|buy now pay later)\b"),
]

PAIN_BY_THEME = {
    "automation_failure": "trust_damaged_pain",
    "ai_adoption": "educational_burden",
    "executive_movement": "organizational_friction",
    "vendor_shutdown": "economically_constrained_pain",
    "margin_pressure": "economically_constrained_pain",
    "restaurant_closure": "economically_constrained_pain",
    "franchisee_resistance": "politically_constrained_pain",
    "saas_consolidation": "theoretically_solved_practically_broken_pain",
    "labor_pressure": "known_solvable_pain",
    "consumer_value_fatigue": "economically_constrained_pain",
    "payments_industry": "organizational_friction",
}

WATCH_TERMS = (
    "toast", "par", "global payments", "genius", "xenial", "yum", "pizza hut",
    "mcdonald", "sweetgreen", "technomic", "restaurant365", "revel", "qu",
    "olo", "ncr", "shift4", "doordash", "uber eats", "ghai", "taco bell",
    # Payments ecosystem — relevant to Global Payments role (added 2026-06)
    "fiserv", "stripe", "adyen", "payoneer", "nuvei", "worldpay", "heartland",
    "tsys", "first data", "payments dive", "apple pay", "google pay",
)

DEEP_DIVE_THRESHOLD = 70

SENT_LOOP_PATTERNS = [
    ("post_meeting_followup", r"\b(thanks again|thank you|great to (?:connect|speak|talk)|enjoyed the conversation|following (?:our|the) call)\b"),
    ("direct_followup_sent", r"\b(follow-?up|circle back|checking in|touch base|reconnecting|wanted to reconnect)\b"),
    ("resume_or_material_sent", r"\b(resume|cv|attached|overview|sending over|shared your info|my notes)\b"),
    ("relationship_nurture_sent", r"\b(congrats|congratulations|nice to hear|thinking more about|hope all is well)\b"),
    ("scheduling_or_next_step", r"\b(do either|available|interview|next step|next steps|schedule|calendar|times work)\b"),
]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        if "," in text or re.search(r"\+\d{4}", text):
            dt = parsedate_to_datetime(text)
        else:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sender_text(thread: dict) -> str:
    sender = thread.get("last_message_from") or {}
    return " ".join(str(sender.get(k) or "") for k in ("name", "email")).strip()


def _sender_email(thread: dict) -> str:
    sender = thread.get("last_message_from") or {}
    raw = " ".join(str(sender.get(k) or "") for k in ("email", "name"))
    match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", raw)
    return match.group(0).lower() if match else ""


def _source_name(thread: dict) -> str | None:
    sender = _sender_text(thread).lower()
    subject = str(thread.get("subject") or "").lower()
    snippet = str(thread.get("snippet") or "").lower()
    text = " ".join([sender, subject, snippet])
    for needle, name in INDUSTRY_SOURCE_HINTS.items():
        if needle in text:
            return name
    sender_is_news_digest = any(k in sender for k in (
        "newsletter", "news@", "alerts", "technomic",
    ))
    sender_is_job_digest = any(k in sender for k in (
        "jobalerts", "jobs@", "recruiting", "linkedin", "ladders", "toast",
    ))
    if sender_is_news_digest and _contains_any_phrase(text, STRICT_INDUSTRY_KEYWORDS):
        return "Industry email source"
    if sender_is_job_digest and _contains_any_phrase(text, JOB_RELEVANCE_KEYWORDS):
        return "Industry email source"
    return None


def _contains_any_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    return any(
        re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text, re.I)
        for phrase in phrases
    )


def _clean_text(value: str | None) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"[\u034f\u200b\u200c\u200d]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# URL patterns to skip when pulling article links from newsletter bodies
_SKIP_URL_PATTERNS = re.compile(
    r"(unsubscribe|optout|opt-out|manage.pref|preference|mailto:|#|javascript:|"
    r"tracking|click\.e\.|go\.pardot|mailchimp|mlsend|campaign-archive|view.email|"
    # LinkedIn newsletter emails link their own masthead ("Hospitality
    # Headline") to /comm/newsletters/<id> and the author's byline to
    # /comm/in/<handle> — both are self-referential chrome, not articles,
    # and no title-text heuristic reliably tells them apart from a real
    # headline since the anchor text is just the publication/author name.
    r"linkedin\.com/comm/newsletters/|linkedin\.com/comm/in/)",
    re.I,
)

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(raw: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", raw)).strip()


def _extract_body_articles(body_text: str) -> list[dict]:
    """Parse HTML newsletter body and return list of {title, url} dicts."""
    if not body_text:
        return []
    articles: list[dict] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    # Match <a href="...">...</a> blocks (non-greedy, handles multi-line)
    for m in re.finditer(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', body_text, re.I | re.S):
        # HTML attribute values encode & as &amp; per spec — every extracted
        # href had literal "&amp;" in its query string (e.g. LinkedIn tracking
        # params) until unescaped here. Titles were already unescaped below;
        # URLs were not.
        url = html.unescape(m.group(1).strip())
        anchor = _strip_tags(m.group(2))
        if not url or not anchor:
            continue
        if _SKIP_URL_PATTERNS.search(url):
            continue
        if len(anchor) < 18:
            continue
        anchor_lower = anchor.lower()
        if anchor_lower.startswith(("view ", "click ", "read more", "learn more", "here", "subscribe")):
            continue
        # CTA button labels that don't start with the patterns above but are
        # still content-free: "CONTINUE READING HERE", "FIND OUT HERE",
        # "LISTEN NOW" style shout-caps buttons, and LinkedIn's algorithmic-
        # transparency footer link. These rendered in D+ as if they were
        # real article titles alongside the masthead/author-name links.
        if anchor_lower in ("continue reading here", "find out here", "listen now",
                             "read more", "learn why we included this."):
            continue
        # A real headline is essentially never fully upper-case in these feeds —
        # that's a CTA button style ("READ MORE", "LISTEN NOW"), not a title.
        if anchor.isupper():
            continue
        url_key = url.split("?")[0].rstrip("/").lower()
        if url_key in seen_urls:
            continue
        title_key = anchor_lower[:60]
        if title_key in seen_titles:
            continue
        seen_urls.add(url_key)
        seen_titles.add(title_key)
        articles.append({"title": html.unescape(anchor[:200]), "url": url})
        if len(articles) >= 20:
            break
    return articles


_NEWSLETTER_TAIL_CUTOFF_RE = re.compile(
    r"(this email was intended for|you are receiving (linkedin )?notification|"
    r"unsubscribe|learn why we included this|"
    r"if you are having trouble reading this email|view (the )?online version|"
    r"view this email in your browser)",
    re.I,
)
_NEWSLETTER_LEAD_START_RE = re.compile(r"read on linkedin", re.I)

# RB-DEFECT-2026-07-10: many marketing/newsletter templates include a hidden
# "preheader" element purely to control the inbox preview snippet -- it's
# invisible in a rendered email client but is plain text once tags are
# stripped, and it typically repeats the newsletter's own opening line
# verbatim (e.g. "QSR AM Jolt - ... And Chipotle makes six new food tech
# investments. And Chipotle makes six new food tech investments."). Strip
# these elements before the generic tag-strip removes the wrapper but
# leaves the duplicated text behind.
_HIDDEN_PREHEADER_RE = re.compile(
    r"<(div|span)\b[^>]*(?:display\s*:\s*none|mso-hide\s*:\s*all|"
    r"max-height\s*:\s*0(?:px)?)[^>]*>.*?</\1>",
    re.I | re.S,
)


def _collapse_immediate_sentence_repeat(text: str) -> str:
    """Defensive pass for hidden-preheader duplication that _HIDDEN_PREHEADER_RE's
    display:none/mso-hide detection didn't catch (some templates hide preview
    text via other means). If the same sentence appears twice back-to-back,
    keep only the first occurrence."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    for s in sentences:
        if out and s.strip() == out[-1].strip():
            continue
        out.append(s)
    return " ".join(out)


def _extract_newsletter_summary(body_text: str, max_chars: int = 400) -> str:
    """Best-effort plain-text lead summary of a newsletter's actual write-up.

    _extract_body_articles only captures <a> anchor text, which for LinkedIn
    newsletters is mostly CTA button labels ("READ MORE", "LISTEN NOW",
    "CONTINUE READING HERE") with no content of their own — the real
    substance (the author's actual paragraph) sits as plain text alongside
    those links, not inside them, and was never surfaced anywhere. D+ ended
    up showing nothing but a bare list of link titles for sources like this,
    with no indication of what the newsletter actually said.

    Strips the LinkedIn masthead/footer boilerplate that surrounds the real
    content and returns the lead paragraph(s), truncated to a sentence
    boundary. Falls back to the full cleaned text when the LinkedIn-specific
    "Read on LinkedIn" anchor isn't present (other newsletter platforms).
    """
    if not body_text:
        return ""
    # RB-DEFECT-2026-07-10f: multi-article roundup templates (e.g. QSR AM
    # Jolt) put each article's headline + blurb in its own <p> pair with no
    # separator text between one article and the next -- tag-stripping alone
    # collapses those block boundaries to a single space, so the lead
    # article's summary ran straight into the second article's headline and
    # blurb ("...Partner content with Sunny Sky Products. How a High Volume
    # Jersey Mike's Turns Cleanliness..."). Truncate at the second real
    # article's anchor (already identified by _extract_body_articles) so
    # only the first article's own text is ever considered.
    articles = _extract_body_articles(body_text)
    if len(articles) >= 2:
        second_url = articles[1]["url"]
        anchor_re = re.compile(r'<a\s[^>]*href=["\']' + re.escape(second_url) + r'["\']', re.I)
        m = anchor_re.search(body_text)
        if not m:
            anchor_re = re.compile(
                r'<a\s[^>]*href=["\']' + re.escape(second_url.replace("&", "&amp;")) + r'["\']', re.I,
            )
            m = anchor_re.search(body_text)
        if m and m.start() > 0:
            body_text = body_text[:m.start()]
    text = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", body_text, flags=re.I | re.S)
    text = _HIDDEN_PREHEADER_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"[͏​‌‍]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    tail_cut = _NEWSLETTER_TAIL_CUTOFF_RE.search(text)
    if tail_cut:
        text = text[:tail_cut.start()].strip()

    lead_start = _NEWSLETTER_LEAD_START_RE.search(text)
    if lead_start:
        text = text[lead_start.end():].strip()

    text = _collapse_immediate_sentence_repeat(text)

    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_period = truncated.rfind(". ")
    return truncated[:last_period + 1] if last_period > 100 else truncated + "…"


def _headline_candidates(thread: dict) -> list[str]:
    subject = _clean_text(thread.get("subject"))
    snippet = _clean_text(thread.get("snippet"))
    out: list[str] = []
    # Pull article titles from full body when available
    body_articles = _extract_body_articles(thread.get("body_text") or "")
    body_titles = [_clean_text(a["title"]) for a in body_articles]
    # RB-DEFECT-2026-07-16: a multi-story digest's subject line (and, when
    # there's no HTML body to extract from, its snippet) bundles several
    # unrelated headlines in one string -- e.g. "July 15 - Stripe's $53B bid
    # for PayPal | Does BNPL pump up prices?" or "Chipotle expands into
    # Mexico; Pizza Hut serves up nostalgia...". Scored as a single
    # candidate it packs in keywords from multiple stories at once,
    # out-competing real individual headlines and producing garbled,
    # misattributed evidence in the Watchlist/Earnings sections. Split
    # subject the same way snippet is already split (now also on ";"), so
    # each story becomes its own candidate instead of one bundle. Body-
    # extracted titles are still preferred outright when available -- they
    # carry real per-article URLs that subject/snippet fragments don't.
    _split_re = r"(?<=[.!?])\s+| \| | • | - |; "
    candidates = (
        body_titles if body_titles else re.split(_split_re, subject)
    ) + list(re.split(_split_re, snippet))
    for candidate in candidates:
        c = candidate.strip(" -|•")
        if not c or len(c) < 18:
            continue
        if c.lower().startswith(("view this email", "click here", "unsubscribe", "manage preferences")):
            continue
        if c not in out:
            out.append(c[:180])
        if len(out) >= 12:
            break
    return out


def _thread_recipients(thread: dict) -> list[dict]:
    out = []
    seen = set()
    for raw in thread.get("last_message_to") or []:
        if isinstance(raw, dict):
            email_value = str(raw.get("email") or "").strip().lower()
            name = str(raw.get("name") or "").strip()
        else:
            email_value = str(raw or "").strip().lower()
            name = ""
        if not email_value or email_value in seen or email_value in core.self_emails():
            continue
        seen.add(email_value)
        out.append({"email": email_value, "name": name or email_value})
    return out


def _sent_loop_category(text: str) -> str | None:
    low = text.lower()
    for category, pattern in SENT_LOOP_PATTERNS:
        if re.search(pattern, low, re.I):
            return category
    return None


def _sent_loop_signal(thread: dict, *, path: Path, now: datetime) -> dict | None:
    labels = list(thread.get("labels") or [])
    if "SENT" not in labels and "email_sent" not in path.name:
        return None
    subject = _clean_text(thread.get("subject"))
    snippet = _clean_text(thread.get("snippet"))
    if _is_system_generated_sent(subject, snippet):
        return None
    text = " ".join([subject, snippet])
    category = _sent_loop_category(text)
    sender_email = _sender_email(thread)
    last_sender_self = sender_email in core.self_emails()
    recipients = _thread_recipients(thread)
    dt = _parse_dt(thread.get("last_message_at"))

    if last_sender_self:
        lifecycle_state = "outbound_sent_awaiting_response"
        lifecycle_effect = (
            "Outbound evidence can close a pending send/follow-up loop and open or update an awaiting-response loop."
        )
        recommended_action = (
            "Mark matching send/follow-up obligation complete; monitor response window before recommending another touch."
        )
    else:
        lifecycle_state = "response_received_after_outbound"
        lifecycle_effect = (
            "Inbound activity on a sent thread can close or suppress an awaiting-response loop."
        )
        recommended_action = (
            "Reconcile against open waiting loops and suppress redundant follow-up recommendations."
        )

    if not recipients and last_sender_self:
        return None

    if not category and not _contains_any_phrase(text, SENT_RELEVANCE_KEYWORDS):
        return None
    if not category:
        category = "sent_state_evidence"

    return {
        "thread_id": thread.get("thread_id"),
        "subject": subject,
        "last_activity_at": dt.isoformat() if dt else thread.get("last_message_at"),
        "source_file": str(path.relative_to(core.PROJECT_DIR)),
        "labels": labels,
        "last_sender_self": last_sender_self,
        "sender": _sender_text(thread),
        "recipients": recipients,
        "category": category,
        "lifecycle_state": lifecycle_state,
        "lifecycle_effect": lifecycle_effect,
        "recommended_action": recommended_action,
        "mailbox_flags": {
            "sent": "SENT" in labels or "email_sent" in path.name,
            "inbox": "INBOX" in labels,
            "spam": "SPAM" in labels,
            "trash": "TRASH" in labels,
        },
        "snippet": snippet[:240],
        "grounding": "system_detected",
        "freshness": "fresh",
        "confidence": "medium" if category != "sent_state_evidence" else "low",
    }


def _is_system_generated_sent(subject: str, snippet: str) -> bool:
    text = " ".join([subject, snippet]).lower()
    return bool(re.search(r"\brb daily brief is ready\b|\brb daily brief\b", text))


def _themes(text: str) -> list[str]:
    hits = []
    low = text.lower()
    for name, pattern in THEME_PATTERNS:
        if re.search(pattern, low, re.I):
            hits.append(name)
    return hits


def _entities(text: str) -> dict[str, list[str]]:
    companies = []
    people = []
    for term in WATCH_TERMS:
        if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text, re.I):
            companies.append(term.title())
    for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b", text):
        val = match.group(1)
        if val.lower() not in {"restaurant business", "restaurant dive", "nation restaurant news", "qsr magazine"}:
            people.append(val)
    return {
        "companies": sorted(set(companies)),
        "people": sorted(set(people))[:8],
    }


def _score(headline: str, source_name: str | None, labels: list[str], themes: list[str], entities: dict) -> tuple[int, list[str]]:
    score = 0
    reasons = []
    if source_name and source_name != "Industry email source":
        score += 35
        reasons.append("known_industry_source")
    elif source_name:
        score += 10
        reasons.append("industry_keyword_source")
    if themes:
        score += min(45, 15 * len(themes))
        reasons.append("theme_match:" + ",".join(themes[:4]))
    if any(t in {"automation_failure", "vendor_shutdown", "restaurant_closure", "franchisee_resistance"} for t in themes):
        score += 20
        reasons.append("high_value_weak_signal_theme")
    if entities.get("companies"):
        score += min(20, 5 * len(entities["companies"]))
        reasons.append("watchlist_entity_match")
    if any(lbl in {"SPAM", "TRASH", "CATEGORY_PROMOTIONS", "CATEGORY_UPDATES"} for lbl in labels):
        score += 5
        reasons.append("non_primary_mailbox_checked")
    if re.search(r"\b(shuts down|exits|names|chief|lawsuit|rollback|bankrupt|closes|launches|acquires)\b", headline, re.I):
        score += 15
        reasons.append("delta_verb")
    return min(score, 100), reasons


def _category_from_themes(themes: list[str]) -> str:
    if "automation_failure" in themes or "ai_adoption" in themes:
        return "restaurant_ai"
    if "executive_movement" in themes:
        return "executive_movement"
    if "vendor_shutdown" in themes:
        return "vendor_failure"
    if "franchisee_resistance" in themes:
        return "franchise_governance"
    if "labor_pressure" in themes:
        return "labor"
    if "saas_consolidation" in themes:
        return "platform_convergence"
    return "industry_signal"


def _load_email_payloads() -> list[tuple[Path, dict]]:
    paths = sorted(core.INBOX_DIR.glob("email*.json"))
    out = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(payload, dict) and payload.get("threads"):
            out.append((path, payload))
    return out


def build_report(*, now: datetime | None = None, days: int = 30, top_n: int = 12) -> dict:
    if now is None:
        now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    payloads = _load_email_payloads()
    sources: dict[str, dict] = {}
    headline_rows: list[dict] = []
    sent_loop_rows: list[dict] = []
    labels_seen = Counter()
    noise_suppressed = 0
    threads_scanned = 0
    newsletters_scanned = 0
    sent_threads_scanned = 0
    # RB-2026-09-05: _load_email_payloads() globs every email*.json in the
    # inbox dir, and overlapping ingestion snapshots can carry the same
    # thread_id in more than one file -- confirmed live: today's Daily
    # Brief showed the same two SENT_LOOP_VERIFICATION subjects twice each,
    # traced to the same thread_id appearing in two payloads. Process each
    # thread_id once per run regardless of which file(s) it appears in.
    seen_thread_ids: set[str] = set()

    for path, payload in payloads:
        for thread in payload.get("threads") or []:
            thread_id = thread.get("thread_id")
            if thread_id and thread_id in seen_thread_ids:
                continue
            if thread_id:
                seen_thread_ids.add(thread_id)
            labels = list(thread.get("labels") or [])
            sender_email = _sender_email(thread)
            dt = _parse_dt(thread.get("last_message_at"))
            if dt and dt < since:
                continue
            threads_scanned += 1
            labels_seen.update(labels)
            sent_signal = _sent_loop_signal(thread, path=path, now=now)
            if sent_signal:
                sent_threads_scanned += 1
                sent_loop_rows.append(sent_signal)
            if sender_email in core.self_emails() or ("SENT" in labels and "INBOX" not in labels):
                continue
            source_name = _source_name(thread)
            if not source_name:
                noise_suppressed += 1
                continue
            newsletters_scanned += 1
            sender = _sender_text(thread)
            source_key = (source_name + "|" + sender).lower()
            slot = sources.setdefault(source_key, {
                "publication": source_name,
                "sender": sender,
                "thread_count": 0,
                "headline_count": 0,
                "labels_seen": [],
                "last_received_at": None,
                "signal_scores": [],
            })
            slot["thread_count"] += 1
            slot["last_received_at"] = max(slot["last_received_at"] or "", dt.isoformat() if dt else "")
            for lbl in labels:
                if lbl not in slot["labels_seen"]:
                    slot["labels_seen"].append(lbl)

            # Build article map once per thread (body_text may not exist)
            _body_articles = _extract_body_articles(thread.get("body_text") or "")
            _body_article_map = {_clean_text(a["title"]).lower(): a["url"] for a in _body_articles}

            for headline in _headline_candidates(thread):
                themes = _themes(headline)
                ents = _entities(headline)
                score, reasons = _score(headline, source_name, labels, themes, ents)
                slot["headline_count"] += 1
                slot["signal_scores"].append(score)
                if score < 20:
                    noise_suppressed += 1
                    continue
                # Prefer a direct article URL matched from body, then fall back to thread link
                link = _body_article_map.get(headline.lower()) or thread.get("link") or thread.get("url") or thread.get("html_link")
                # RB-DEFECT-001: the normalized email cache stores subject/snippet
                # only — newsletter emails rarely surface a "link" field, so
                # `link_target` is almost always None and the GPT had nothing to
                # cite but `email_thread:<id>` (not a clickable URL). Fall back to
                # a Gmail web permalink built from the thread_id so every
                # newsletter-derived item still carries a real, clickable source
                # URL the user can open to read the original email and follow its
                # links. This is distinct from `link_target` (a direct article
                # link, when the cache happens to have one) — `email_permalink`
                # is always populated when a thread_id is available.
                thread_id = thread.get("thread_id")
                email_permalink = (
                    f"https://mail.google.com/mail/u/0/#all/{thread_id}"
                    if thread_id else None
                )
                deep_dive = score >= DEEP_DIVE_THRESHOLD
                headline_rows.append({
                    "headline": headline,
                    "source": source_name,
                    "sender": sender,
                    "received_at": dt.isoformat() if dt else thread.get("last_message_at"),
                    "account_id": thread.get("account_id"),
                    "account_label": thread.get("account_label"),
                    "thread_id": thread.get("thread_id"),
                    "labels": labels,
                    "mailbox_flags": {
                        "inbox": "INBOX" in labels,
                        "spam": "SPAM" in labels,
                        "trash": "TRASH" in labels,
                        "promotions": "CATEGORY_PROMOTIONS" in labels,
                        "updates": "CATEGORY_UPDATES" in labels,
                    },
                    "link_target": link,
                    "email_permalink": email_permalink,
                    "category": _category_from_themes(themes),
                    "themes": themes,
                    "pain_types": sorted({PAIN_BY_THEME[t] for t in themes if t in PAIN_BY_THEME}),
                    "entities": ents,
                    "strategic_relevance_score": score,
                    "score_reasons": reasons,
                    "deep_dive": {
                        "triggered": deep_dive,
                        "status": (
                            "ready_for_retrieval" if deep_dive and link else
                            "blocked_no_article_link" if deep_dive else
                            "not_triggered"
                        ),
                        "required_follow_up": (
                            "Retrieve article, extract failure cause/deltas/named executives, and cross-source validate."
                            if deep_dive else ""
                        ),
                    },
                    "why_it_matters": _why_it_matters(themes),
                    "recommended_action": _recommended_action(themes, deep_dive=deep_dive),
                    "grounding": "system_detected",
                    "freshness": "fresh",
                    "confidence": "medium" if score >= DEEP_DIVE_THRESHOLD else "low",
                })

    for slot in sources.values():
        scores = slot.pop("signal_scores", [])
        useful = sum(1 for s in scores if s >= 20)
        slot["historical_usefulness"] = round(useful / max(1, len(scores)), 2)
        slot["signal_quality"] = (
            "high" if any(s >= DEEP_DIVE_THRESHOLD for s in scores) else
            "medium" if useful else "low"
        )
        slot["noise_rate"] = round(1 - slot["historical_usefulness"], 2)

    headline_rows.sort(key=lambda r: (r["strategic_relevance_score"], r.get("received_at") or ""), reverse=True)
    sent_rank = {"outbound_sent_awaiting_response": 0, "response_received_after_outbound": 1}
    sent_loop_rows.sort(
        key=lambda r: (
            sent_rank.get(r.get("lifecycle_state"), 9),
            r.get("last_activity_at") or "",
        ),
        reverse=True,
    )
    sent_loop_rows.sort(key=lambda r: sent_rank.get(r.get("lifecycle_state"), 9))
    theme_counts = Counter(t for row in headline_rows for t in row.get("themes") or [])
    previous = _load_previous_cache()
    acceleration = _trend_acceleration(theme_counts, previous)
    top = headline_rows[:top_n]

    registry = {
        "generated_at": now.isoformat(timespec="seconds"),
        "sources": sorted(sources.values(), key=lambda r: (r["signal_quality"], r["thread_count"]), reverse=True),
    }
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = {
        "generated_at": now.isoformat(timespec="seconds"),
        "scan_window_days": days,
        "input_files": [str(path.relative_to(core.PROJECT_DIR)) for path, _ in payloads],
        "telemetry": {
            "threads_scanned": threads_scanned,
            "newsletters_scanned": newsletters_scanned,
            "sent_threads_scanned": sent_threads_scanned,
            "sent_loop_signals": len(sent_loop_rows),
            "sent_outbound_awaiting_response": sum(
                1 for r in sent_loop_rows if r["lifecycle_state"] == "outbound_sent_awaiting_response"
            ),
            "sent_response_after_outbound": sum(
                1 for r in sent_loop_rows if r["lifecycle_state"] == "response_received_after_outbound"
            ),
            "recurring_sources_detected": len(sources),
            "relevant_headlines_extracted": len(headline_rows),
            "deep_dives_triggered": sum(1 for r in headline_rows if r["deep_dive"]["triggered"]),
            "deep_dives_blocked_no_link": sum(1 for r in headline_rows if r["deep_dive"]["status"] == "blocked_no_article_link"),
            "net_new_strategic_signals": len([r for r in headline_rows if r["strategic_relevance_score"] >= DEEP_DIVE_THRESHOLD]),
            "noise_suppressed": noise_suppressed,
            "junk_spam_trash_relevant_finds": sum(
                1 for r in headline_rows
                if r["mailbox_flags"]["spam"] or r["mailbox_flags"]["trash"]
            ),
            "sent_trash_relevant_finds": sum(
                1 for r in sent_loop_rows if r["mailbox_flags"]["trash"]
            ),
            "labels_seen": dict(labels_seen),
            "spam_trash_coverage": {
                "spam_labels_seen": labels_seen.get("SPAM", 0),
                "trash_labels_seen": labels_seen.get("TRASH", 0),
                "coverage_status": "covered_if_present_in_fetch" if labels_seen.get("SPAM") or labels_seen.get("TRASH") else "not_proven_by_current_fetch",
            },
        },
        "source_registry": registry["sources"],
        "top_headlines": top,
        "sent_loop_verification": sent_loop_rows[:top_n],
        "theme_counts": dict(theme_counts),
        "trend_acceleration": acceleration,
    }
    return report


def _why_it_matters(themes: list[str]) -> str:
    if "automation_failure" in themes:
        return "Potential weak signal that restaurant automation is still struggling with operational survivability, adoption economics, trust, or funding."
    if "executive_movement" in themes:
        return "Executive movement can reveal growth pressure, transformation, M&A readiness, or operating-model change."
    if "restaurant_closure" in themes:
        return "Closures and exits reveal unit economics, real estate, labor, brand-fit, or expansion risk."
    if "franchisee_resistance" in themes:
        return "Franchisee resistance can widen the franchisor/franchisee alignment gap and weaken rollout readiness."
    if "ai_adoption" in themes:
        return "AI adoption is contradictory evidence against a simple skepticism thesis; RB should test whether deployment is durable or hype."
    return "Headline may indicate a restaurant operating, vendor, macro, relationship, or job-search signal worth monitoring."


def _recommended_action(themes: list[str], *, deep_dive: bool) -> str:
    if deep_dive:
        return "Trigger article retrieval and cross-source validation before turning this headline into a market conclusion."
    if themes:
        return "Monitor theme frequency and correlate with active opportunities, watchlists, and restaurant reality model."
    return "Suppress unless repeated by additional sources."


def _load_previous_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _trend_acceleration(current: Counter, previous: dict) -> list[dict]:
    prev_counts = Counter((previous.get("theme_counts") or {}))
    rows = []
    for theme, count in current.items():
        prev = int(prev_counts.get(theme) or 0)
        if count >= 2 and count > prev:
            rows.append({
                "theme": theme,
                "current_count": count,
                "previous_count": prev,
                "delta": count - prev,
                "interpretation": f"Trend acceleration candidate: {theme} frequency increased in passive email intelligence.",
            })
    rows.sort(key=lambda r: (r["delta"], r["current_count"]), reverse=True)
    return rows[:8]


def write_cache(report: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _print(report: dict) -> None:
    t = report.get("telemetry") or {}
    print("Email Intelligence Harvest")
    print(f"  Newsletters scanned: {t.get('newsletters_scanned', 0)}")
    print(f"  Relevant headlines extracted: {t.get('relevant_headlines_extracted', 0)}")
    print(f"  Deep dives triggered: {t.get('deep_dives_triggered', 0)}")
    print(f"  Sent loop signals: {t.get('sent_loop_signals', 0)}")
    print(f"  Noise suppressed: {t.get('noise_suppressed', 0)}")
    for row in report.get("top_headlines") or []:
        print(f"- [{row['strategic_relevance_score']}] {row['headline']} ({row['source']})")


def _smoke() -> int:
    sample = {
        "fetched_at": "2026-05-27T12:00:00+00:00",
        "threads": [
            {
                "thread_id": "t1",
                "subject": "NRN a.m.: Pizza robot company Picnic shuts down",
                "last_message_at": "2026-05-27T11:00:00+00:00",
                "last_message_from": {"email": "news@nrn.com", "name": "Nation's Restaurant News"},
                "labels": ["INBOX", "CATEGORY_UPDATES"],
                "snippet": "Sweetgreen names first chief strategy officer. Guzman y Gomez has exited the U.S. market.",
            },
            {
                "thread_id": "t2",
                "subject": "Receipt from store",
                "last_message_at": "2026-05-27T10:00:00+00:00",
                "last_message_from": {"email": "receipt@example.com", "name": "Receipt"},
                "labels": ["INBOX"],
                "snippet": "Thanks for shopping.",
            },
            {
                "thread_id": "t3",
                "subject": "Re: Ryan Hildebrand follow-up",
                "last_message_at": "2026-05-27T09:00:00+00:00",
                "last_message_from": {"email": "vahlsingt@gmail.com", "name": "Todd"},
                "last_message_to": [{"email": "ryan@example.com", "name": "Ryan Hildebrand"}],
                "labels": ["SENT"],
                "snippet": "Thanks again for taking the time to speak. Wanted to follow up on next steps.",
            },
        ],
    }
    old_cache = CACHE_PATH.read_text(encoding="utf-8") if CACHE_PATH.exists() else None
    failures = []

    def ck(cond: bool, msg: str) -> None:
        print(("  OK   " if cond else "  FAIL ") + msg)
        if not cond:
            failures.append(msg)

    original_loader = globals()["_load_email_payloads"]
    try:
        globals()["_load_email_payloads"] = lambda: [(core.INBOX_DIR / "_smoke_email_intelligence.json", sample)]
        report = build_report(now=datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc), days=1)
        top = report["top_headlines"]
        ck(report["telemetry"]["newsletters_scanned"] >= 1, "newsletter source detected")
        ck(any("Pizza robot company Picnic shuts down" in r["headline"] for r in top), "headline extracted")
        pic = next(r for r in top if "Pizza robot company Picnic shuts down" in r["headline"])
        ck("automation_failure" in pic["themes"], "automation failure theme detected")
        ck(pic["deep_dive"]["triggered"], "deep dive triggered for high relevance headline")
        ck(pic["deep_dive"]["status"] == "blocked_no_article_link", "deep dive blocked honestly when no link exists")
        ck(report["telemetry"]["sent_loop_signals"] == 1, "sent folder loop signal detected")
        sent = report["sent_loop_verification"][0]
        ck(sent["lifecycle_state"] == "outbound_sent_awaiting_response", "sent loop marks outbound awaiting response")
        ck(report["telemetry"]["noise_suppressed"] >= 1, "noise suppressed")
    finally:
        globals()["_load_email_payloads"] = original_loader
        if old_cache is not None:
            CACHE_PATH.write_text(old_cache, encoding="utf-8")
    print(f"--- passive_email_intelligence smoke: {len(failures)} failure(s) ---")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--cache", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.smoke:
        return _smoke()
    report = build_report(days=args.days, top_n=args.top)
    if args.cache:
        write_cache(report)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
