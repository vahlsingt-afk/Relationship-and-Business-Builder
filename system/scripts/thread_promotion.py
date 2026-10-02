#!/usr/bin/env python3
"""
thread_promotion.py — RB-DEFECT-038: relationship/thread auto-promotion.

RB has the data model for relationship/opportunity journeys
(`active_threads.yaml`, opportunity dossiers with `opportunity_state` /
`narrative`, cross-channel signal correlation in
`opportunity_signal_correlation.py`) but no step decides when a brand-new
signal — an unknown calendar attendee on a real meeting, or a two-way
email/LinkedIn exchange — is significant enough to *become* a tracked
baseline contact / active thread in the first place. New relationships fall
into a dead zone: not yet a contact, not yet a thread, so none of the
existing journey machinery engages and meeting briefs render empty.

This module implements the "remember vs. dismiss" tiers from
`CLAUDE_DEFECT_RB_038_RELATIONSHIP_THREAD_AUTO_PROMOTION.md`:

  Tier 0 — noise: cold-outreach / template-spam, no meeting context. Dismiss.
  Tier 1 — candidate signal: specific inbound, no reply yet. (handled
           upstream by overlays/triage — not this module's concern.)
  Tier 2 — engaged exchange: two-way reply detected. (also upstream.)
  Tier 3 — calendar confirmation: a real scheduled meeting with an unknown
           attendee. THIS is what this module evaluates: classify the
           thread type, search inbox history for prior context, synthesize
           a narrative, and propose a baseline contact + active thread.
  Tier 4 — existing RC/opportunity promotion. Unchanged.

Tier 3 proposals default to `persistence_status: "pending confirmation"`.
`apply_baseline_entry()` can write the baseline contact once confirmed;
`active_threads.yaml` is hand-curated, so proposals include a ready-to-paste
YAML block rather than being written automatically.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core  # noqa: E402


# -----------------------------------------------------------------------------
# Tier 0 — noise filter
# -----------------------------------------------------------------------------

# Phrases typical of templated cold-outreach (financing pitches, recruiting
# blasts, "let's hop on a 15-minute Zoom" spam). Two-or-more hits with no
# calendar/meeting context = noise, never promoted.
_COLD_TEMPLATE_PHRASES = (
    "15-minute zoom", "15 minute zoom", "hop on a call",
    "lines of credit", "term loans", "equipment financing",
    "funded in as fast as", "secure $10k", "banks decline",
    "let's set up a", "what time works best for you today/tomorrow",
    "does your business have access to",
)


def is_cold_template(text: str) -> bool:
    """True if `text` reads like generic templated cold outreach."""
    t = (text or "").lower()
    return sum(1 for p in _COLD_TEMPLATE_PHRASES if p in t) >= 2


# -----------------------------------------------------------------------------
# Tier 3 — thread-type classification
# -----------------------------------------------------------------------------

# Order matters: first match wins. Extends the spirit of
# opportunity_sensing.classify_opportunity_type but for personal-relationship
# threads rather than career/vendor opportunities.
_TYPE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("media_thought_leadership", ("podcast", "interview", "guest", "media")),
    ("recruiting", ("recruit", "phone screen", "screen", "hiring", "role at")),
    ("business_engagement", ("engagement", "proposal", "scope", "contract",
                              "advisory", "consulting", "sow")),
    ("networking", ("intro", "introduction", "connect", "coffee")),
]

_BOOST_BY_TYPE = {
    "media_thought_leadership": "high",
    "business_engagement": "high",
    "recruiting": "medium",
    "networking": "medium",
    "general_relationship": "low",
}


def classify_thread_type(title: str, description: str) -> str:
    """Best-effort thread type from calendar title/description text."""
    text = f"{title or ''} {description or ''}".lower()
    for ttype, keywords in _TYPE_KEYWORDS:
        if any(k in text for k in keywords):
            return ttype
    return "general_relationship"


# -----------------------------------------------------------------------------
# Cross-inbox history search
# -----------------------------------------------------------------------------

def _iter_email_threads():
    """Yield (account_id, direction, thread_dict) across all configured accounts."""
    accounts = core.load_inbox_accounts() or [{"id": "personal"}, {"id": "bridgepoint"}]
    for acct in accounts:
        acct_id = acct.get("id")
        if not acct_id:
            continue
        for direction, path_fn in (("inbound", core.email_path_for),
                                    ("sent", core.email_sent_path_for)):
            p = path_fn(acct_id)
            if not p.exists():
                continue
            try:
                data = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            for th in data.get("threads") or []:
                yield acct_id, direction, th


def _iter_linkedin_messages():
    p = core.INBOX_DIR / "linkedin.messages.json"
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return
    items = data if isinstance(data, list) else (data.get("messages") or [])
    yield from items


def search_inbox_history(email: Optional[str], name: Optional[str] = None,
                          *, max_hits: int = 5) -> list[dict]:
    """Search email (all accounts, inbound + sent) and LinkedIn messages for
    prior exchanges with `email` / `name`.

    Returns hits oldest-first: [{source, date, subject, from, snippet,
    direction}, ...], capped at `max_hits`.
    """
    em = core._normalize_email(email) if email else ""
    name_low = (name or "").strip().lower()
    hits: list[dict] = []

    if em:
        for acct_id, direction, th in _iter_email_threads():
            frm = core._normalize_email((th.get("last_message_from") or {}).get("email"))
            to_emails = {core._normalize_email(t.get("email"))
                          for t in (th.get("last_message_to") or [])}
            if em == frm or em in to_emails:
                hits.append({
                    "source": f"email.{acct_id}",
                    "date": th.get("last_message_at"),
                    "subject": th.get("subject"),
                    "from": (th.get("last_message_from") or {}).get("name") or frm,
                    "snippet": (th.get("snippet") or "")[:300],
                    "direction": direction,
                })

    if name_low:
        for it in _iter_linkedin_messages():
            frm_name = ((it.get("from") or {}).get("name") or "").lower()
            if name_low and name_low in frm_name:
                hits.append({
                    "source": "linkedin",
                    "date": it.get("date"),
                    "subject": it.get("subject"),
                    "from": (it.get("from") or {}).get("name"),
                    "snippet": (it.get("content") or "")[:300],
                    "direction": it.get("direction"),
                })

    hits.sort(key=lambda h: h.get("date") or "")
    return hits[-max_hits:]


# -----------------------------------------------------------------------------
# Narrative synthesis
# -----------------------------------------------------------------------------

def _format_hit_date(raw: Optional[str]) -> str:
    """Best-effort ISO date from either an ISO string or an RFC 2822 header
    (e.g. "Mon, 08 Jun 2026 11:46:34 +0000")."""
    if not raw:
        return ""
    raw = raw.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", raw):
        return raw[:10]
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        return raw[:16]


def _description_summary(description: str, *, max_len: int = 220) -> str:
    """Collapse a calendar description's non-empty lines into one line."""
    lines = [l.strip() for l in (description or "").splitlines() if l.strip()]
    summary = " / ".join(lines)
    if len(summary) > max_len:
        summary = summary[: max_len - 3] + "..."
    return summary


def synthesize_narrative(event: dict, hits: list[dict], display_name: str) -> str:
    """Build a one-paragraph narrative from calendar context + inbox history.

    Never invents facts (Tenet 1) — only describes evidence actually found.
    """
    bits: list[str] = []

    if hits:
        first = hits[0]
        first_date = _format_hit_date(first.get("date"))
        if first.get("direction") == "inbound":
            verb = "first reached out"
        else:
            verb = "first appeared in correspondence"
        when = f" on {first_date}" if first_date else ""
        source = first.get("source")
        bits.append(f"{display_name} {verb}{when}" + (f" (via {source})." if source else "."))
        if len(hits) > 1:
            bits.append(f"{len(hits)} prior exchange(s) on record.")
    else:
        bits.append(f"No prior email/LinkedIn history found for {display_name}.")

    summary = _description_summary(event.get("description") or "")
    if summary:
        bits.append(f'Calendar context: "{summary}".')

    return " ".join(bits)


# -----------------------------------------------------------------------------
# Tier 3 evaluation
# -----------------------------------------------------------------------------

def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "unknown"


def evaluate_promotion(event: dict, attendee: dict) -> Optional[dict]:
    """Evaluate a Tier-3 (calendar confirmation) promotion for an attendee
    not already in baseline.

    `event` is a calendar overlay row (must carry `title`/`description`).
    `attendee` is an entry from `attendees_matched` with `id is None`
    (i.e. unmatched against baseline).

    Returns None for Tier-0 noise (e.g. noise-domain senders, or templated
    cold outreach with no real meeting context). Otherwise returns a
    promotion proposal dict with `persistence_status: "pending confirmation"`.
    """
    email = attendee.get("email") or ""
    if not email:
        return None

    if any(p in email.lower() for p in core.NOISE_DOMAIN_PATTERNS):
        return None

    raw_name = attendee.get("name")
    display_name = raw_name or email.split("@")[0].replace(".", " ").replace("_", " ").title()
    title = event.get("title") or ""
    description = event.get("description") or ""

    # Only search LinkedIn by name when a real (multi-word) name is known —
    # a single first name derived from an email local-part (e.g. "Daniel"
    # from daniel@growthventure.ai) is too ambiguous and produces false
    # positives against unrelated contacts sharing that first name.
    li_name = raw_name if raw_name and " " in raw_name else None
    hits = search_inbox_history(email, li_name)

    # A real, accepted calendar invite is itself a strong signal — but if the
    # *only* prior history is cold-template outreach and the calendar event
    # carries no descriptive context of its own, treat as noise.
    if not description and hits and all(is_cold_template(h.get("snippet", "")) for h in hits):
        return None

    thread_type = classify_thread_type(title, description)
    narrative = synthesize_narrative(event, hits, display_name)
    contact_id = _slug(display_name)
    today_iso = date.today().isoformat()

    proposed_baseline_entry = {
        "id": contact_id,
        "name": display_name,
        "email": email,
        "status": "prospect",
        "relationship_stage": "new_contact",
        "narrative": narrative,
        "source": "thread_promotion_auto (RB-DEFECT-038)",
        "created_at": today_iso,
        # Required by schemas/baseline.schema.json (id, name, signal_class,
        # sources). Missing these caused 61 auto-promoted entries to fail
        # baseline validation and block every mutations.py write (found
        # 2026-07-05). New auto-promoted contacts start unclassified (VC,
        # no RC state/tier) until Todd promotes them.
        "signal_class": "VC",
        "sources": ["thread_promotion_auto (RB-DEFECT-038)"],
        "rc_state": None,
        "rc_tier": None,
        "last_touch": None,
        "circles": [],
        "tags": [],
    }

    thread_id = f"T-{today_iso}-{contact_id}"
    proposed_thread = {
        "id": thread_id,
        "title": title or f"{display_name} — new relationship",
        "opened": today_iso,
        "status": "open",
        "type": thread_type,
        "people": [contact_id],
        "companies": [],
        "context": narrative,
        "boost_for_brief": _BOOST_BY_TYPE.get(thread_type, "medium"),
    }

    return {
        "tier": 3,
        "email": email,
        "display_name": display_name,
        "thread_type": thread_type,
        "narrative": narrative,
        "evidence": hits,
        "proposed_baseline_entry": proposed_baseline_entry,
        "proposed_thread": proposed_thread,
        "proposed_thread_yaml": render_thread_yaml(proposed_thread),
        "persistence_status": "pending confirmation",
    }


def render_thread_yaml(thread: dict) -> str:
    """Render a proposed thread as a ready-to-paste `active_threads.yaml`
    block. `active_threads.yaml` is hand-curated — RB proposes, Todd pastes.
    """
    lines = [
        f"- id: {thread['id']}",
        f"  title: {thread['title']}",
        f"  opened: {thread['opened']}",
        f"  status: {thread['status']}",
        f"  type: {thread['type']}",
        "  people:",
    ]
    for p in thread.get("people") or []:
        lines.append(f"  - {p}")
    if not thread.get("people"):
        lines.append("  []")
    lines.append("  companies: []")
    lines.append(f"  context: '{thread['context']}'")
    lines.append(f"  boost_for_brief: {thread['boost_for_brief']}")
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Apply (baseline only — active_threads.yaml stays hand-curated)
# -----------------------------------------------------------------------------

def apply_baseline_entry(entry: dict, *, baseline_path: Path = core.BASELINE_PATH) -> dict:
    """Append `entry` to baseline_index.json if no entry with the same id or
    email already exists. Idempotent.
    """
    baseline = json.loads(baseline_path.read_text())
    target_email = core._normalize_email(entry.get("email"))
    for existing in baseline:
        if existing.get("id") == entry.get("id"):
            return {"applied": False, "reason": "id already exists", "id": entry.get("id")}
        if target_email and core._normalize_email(existing.get("email")) == target_email:
            return {"applied": False, "reason": "email already exists", "id": existing.get("id")}
    baseline.append(entry)
    baseline_path.write_text(json.dumps(baseline, indent=2) + "\n")
    return {"applied": True, "id": entry.get("id")}


# -----------------------------------------------------------------------------
# CLI smoke
# -----------------------------------------------------------------------------

def _smoke() -> int:
    event = {
        "title": "Daniel von Walzel and Todd Vahlsing",
        "description": "Virtual Coffee - Let's connect\n\nPre-podcast call",
    }
    attendee = {"email": "daniel@growthventure.ai", "name": None, "id": None}
    proposal = evaluate_promotion(event, attendee)
    assert proposal is not None
    assert proposal["thread_type"] == "media_thought_leadership"
    assert proposal["tier"] == 3
    assert "Pre-podcast call" in proposal["narrative"]

    # "Anel Paul" / QualiFi only ever sent templated cold-outreach LinkedIn
    # messages (see system/inbox/linkedin.messages.json) and has no
    # calendar-event description of its own -> Tier 0, dismissed.
    spam_event = {"title": "Quick chat", "description": ""}
    spam_attendee = {"email": "anel.paul@qualifi-llc.com", "name": "Anel Paul", "id": None}
    spam_proposal = evaluate_promotion(spam_event, spam_attendee)
    assert spam_proposal is None

    print("OK: thread_promotion smoke test passed")
    return 0


def main() -> int:
    return _smoke()


if __name__ == "__main__":
    raise SystemExit(main())
