#!/usr/bin/env python3
"""llm_assist.py — Phase 3 (optional) LLM-assisted refinements.

Two narrow, low-volume uses on top of the mechanical Phase 1/2 fixes (see
~/.claude/plans/binary-dreaming-yeti.md):

1. same_story_tiebreak() -- resolve_story_identity() (rb_core.py) can't
   confidently key every corporate headline (a roundup headline with no
   clean leading proper noun resolves to None), and even when it does key
   two headlines, their disambiguators can fail to overlap despite
   describing the same event (one outlet states a dollar amount, another
   describes it purely qualitatively). This is called only for the small
   number of genuinely ambiguous titles the mechanical check can't resolve
   on its own -- not per item.
2. is_low_signal() -- relevance filtering for "What Changed Today"/D+
   items the Phase 1 keyword blocklists (_MUTED_EMAIL_SENDERS,
   _LOW_SIGNAL_NEWSLETTER_PATTERNS in render_intelligence_brief.py) don't
   catch. Layered on top of those lists, not a replacement for them.

Best-effort by design, exactly like transcript_summarizer.py: returns None
(never raises) when OPENAI_API_KEY isn't configured, the openai package
isn't installed, or the API call fails for any reason. Callers must treat
None as "no signal" and fall back to whatever they'd otherwise do.
"""
from __future__ import annotations

import json
import os

MODEL = "gpt-4o-mini"

_TIEBREAK_SYSTEM_PROMPT = (
    "You judge whether two news headlines describe the exact same real-world "
    "event -- the same funding round, the same acquisition, the same "
    "executive hire -- not merely the same company or topic area. Two "
    "headlines about the same company's two different funding rounds are "
    "NOT the same event. Respond with a JSON object: "
    '{"same_event": true or false}.'
)

_RELEVANCE_SYSTEM_PROMPT = (
    "You judge whether an email or newsletter subject line (and optional "
    "snippet) is worth a Chief of Staff's attention -- genuine business, "
    "industry, or professional-network content -- or pure noise: sports "
    "scores/highlights, marketing or webinar pitches, personal social "
    "content, or automated notifications with no substantive information. "
    'Respond with a JSON object: {"low_signal": true or false}.'
)


def _client():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    return OpenAI(api_key=api_key)


def same_story_tiebreak(title_a: str, title_b: str) -> bool | None:
    """Best-effort judgment of whether two headlines describe the same
    real-world event. Returns None if unavailable/failed -- callers must
    treat None as "no signal," not as a negative answer."""
    if not title_a or not title_b:
        return None
    client = _client()
    if client is None:
        return None
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _TIEBREAK_SYSTEM_PROMPT},
                {"role": "user", "content": f"Headline A: {title_a}\nHeadline B: {title_b}"},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=20,
        )
        data = json.loads(response.choices[0].message.content)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    val = data.get("same_event")
    return val if isinstance(val, bool) else None


def is_low_signal(title: str, snippet: str = "") -> bool | None:
    """Best-effort judgment of whether this item is CoS-irrelevant noise.
    Returns None if unavailable/failed -- callers must treat None as "no
    signal" (keep the item) rather than as a negative answer."""
    if not title:
        return None
    client = _client()
    if client is None:
        return None
    try:
        combined = f"Subject: {title}\nSnippet: {snippet}"[:2000]
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _RELEVANCE_SYSTEM_PROMPT},
                {"role": "user", "content": combined},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=20,
        )
        data = json.loads(response.choices[0].message.content)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    val = data.get("low_signal")
    return val if isinstance(val, bool) else None
