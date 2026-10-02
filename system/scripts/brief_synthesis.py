#!/usr/bin/env python3
"""brief_synthesis.py — LLM-based dot-connecting synthesis for the Daily Brief.

GP/Genius Dot Connections and Connect the Dots each have a deterministic
role-context check first: if a signal names an entity in Todd's active
opportunity pipeline or today's escalated watchlist, a specific "why it
matters" sentence is built directly from that match -- free, instant, and
100% grounded, so it always wins when it applies. But most signals (a
restaurant chain generating 30+ press mentions, a competitor's product
announcement, an unrelated M&A rumor) don't touch anything currently on
Todd's board, and there is no fact in the system's structured data to
template a specific sentence from -- only inference. That's the ceiling of
template matching, and the reason both sections kept falling back to
boilerplate ("Competitive or market signal relevant to your Genius/Worldpay
territory -- assess customer impact.") for the majority of items.

This module asks a real LLM (OpenAI) to reason about *implications* of the
signals that don't have a hard match, grounded strictly in the evidence
handed to it and Todd's stated role -- never inventing new facts, contacts,
or numbers.

Best-effort by design: returns None (never raises) when OPENAI_API_KEY isn't
configured, the openai package isn't installed, there's nothing to
synthesize, or the API call fails for any reason. Callers must fall back to
the existing deterministic boilerplate when this returns None -- this call
must never be able to block or break brief generation.
"""
from __future__ import annotations

import json
import os
import re

MODEL = "gpt-4o-mini"

# RB-2026-09-05: confirmed live, twice, that the system prompt's own
# explicit anti-generic instructions and named-example bans do not reliably
# stop the model from reproducing the same hedge pattern with slightly
# different wording (e.g. "may signal a strategic shift... which could lead
# to new technology needs" for a bare COO-hire announcement -- structurally
# identical to the already-banned Fiserv/Jana Partners sentence). Prompt
# text alone is not a sufficient guard; this is a deterministic backstop so
# a known-generic sentence never reaches the brief even when the model
# ignores the instruction. A "why" that trips this is treated exactly like
# a synthesis miss -- the caller falls back to its own deterministic
# bucket sentence or omits the line, same as if the API call had failed.
_GENERIC_HEDGE_RE = re.compile(
    r"\b(could indicate|may indicate|may suggest|may signal|potentially "
    r"affect|potentially impact|shifts? in strategic (direction|priorit)|"
    r"within the \w+(\s+\w+){0,2} landscape|could lead to new|new "
    r"opportunities? for|operational efficienc|focus on growth|well "
    r"positioned|broader (trend|shift)|competitive dynamics)\b",
    re.IGNORECASE,
)


def _is_generic_hedge(why: str) -> bool:
    return bool(_GENERIC_HEDGE_RE.search(why))
MAX_ITEMS_PER_CALL = 20  # real batches are always well under this (1-8/day)
MAX_EVIDENCE_CHARS = 400  # keep prompt size/cost bounded per item
REQUEST_TIMEOUT_SECONDS = 20  # the render step this runs in normally completes
                               # in under a second with no timeout anywhere in
                               # the chain -- bound this call explicitly so a
                               # slow API response can't stall the 5am pipeline

_SYSTEM_PROMPT = (
    "You are the dot-connecting synthesis layer for RB, a Chief of Staff "
    "system. You are given a short description of the user's role and a "
    "list of market/competitive signals that do NOT already connect to "
    "anything on the user's active board -- if they did, a simpler "
    "deterministic rule would have already written the sentence and you "
    "would not be called. Your job: for each signal, produce two things -- "
    "'why' (1-2 sentences on why a person in this specific role should "
    "care and what it might imply for them) and 'action' (a specific, "
    "concrete next step if one plausibly exists, or an honest admission "
    "that none is warranted). Ground both ONLY in the evidence text given "
    "for that signal. Never invent facts, numbers, contacts, companies, or "
    "events not present in the evidence -- and never invent a specific "
    "action (a name, a meeting, an outreach) that the evidence doesn't "
    "actually support. If a signal genuinely has no plausible implication "
    "for someone in this role, say so plainly in 'why' ('No clear "
    "implication for this role beyond general market awareness.') and set "
    "'action' to 'No specific action warranted; awareness only.' rather "
    "than padding either field with generic filler. Never use the phrases "
    "'assess customer impact', 'review the gathered intelligence items', "
    "'monitor for now', or similar deflections that just tell the user to "
    "go look at the data themselves -- that is the failure mode you exist "
    "to fix.\n\n"
    "RB-DEFECT-2026-08-18: confirmed live -- 'As a restaurant-technology "
    "executive, changes in major stakeholders like Jana Partners in Fiserv "
    "could indicate shifts in strategic direction or investment priorities "
    "within the payments landscape, potentially affecting competitive "
    "dynamics' was generated for a Fiserv stake-reduction filing. That "
    "sentence names no specific implication and would read as true for "
    "almost any stakeholder-change story about almost any company -- it "
    "passes the letter of the banned-phrases list above without meeting "
    "the actual bar. Avoid hedge words that let a sentence sound analytical "
    "while committing to nothing: 'could indicate', 'may suggest', "
    "'potentially affecting', 'shifts in strategic direction/priorities', "
    "'within the X landscape'. If the evidence supports a specific, "
    "falsifiable claim (a concrete effect on a named account, deal, or "
    "competitive position), state that claim directly. If it genuinely "
    "doesn't, use the honest 'No clear implication...' fallback above "
    "instead of dressing up vagueness as insight -- a plain admission of "
    "no implication is more useful to the reader than a sentence that "
    "sounds like analysis but says nothing falsifiable.\n\n"
    "RB-2026-09-05: confirmed live -- even after the fix above, a second, "
    "subtler generic failure mode kept slipping through, this time naming "
    "the real company but not the real substance: 'Caribou Coffee's "
    "appointment of a new COO indicates a focus on operational efficiency "
    "and growth, which could lead to new opportunities for technology "
    "solutions' was generated for a COO-hire story. Swap in any chain and "
    "any executive title and the sentence still reads as true -- it names "
    "the company but says nothing that depends on THIS evidence "
    "specifically. Before you write 'why', run this test on your own draft: "
    "if you replaced the company name and role with a different company and "
    "a different but same-category executive (a new CFO instead of a new "
    "COO, a different QSR chain), would the sentence still hold up word for "
    "word except for the swapped names? If yes, the sentence has failed --"
    " rewrite it so it depends on some concrete detail actually present in "
    "the evidence (the person's stated background or mandate, the specific "
    "market segment, a number, a competitor named in the text, a stated "
    "priority) or fall back to the honest 'No clear implication...' "
    "sentence. A hire/appointment story with only a name, a title, and an "
    "effective date in the evidence -- no stated mandate, background, or "
    "strategic rationale -- almost always has no specific implication yet; "
    "say so rather than inventing 'operational efficiency', 'growth', "
    "'new opportunities', 'well positioned', or 'a focus on' out of a bare "
    "personnel announcement. Reserve a real claim for when the evidence "
    "actually states a mandate, a number, or a named strategic priority."
)

_USER_PROMPT_TEMPLATE = """User's role and context:
{role_summary}

Signals needing "why it matters" + "what to do about it" (JSON array, each with a stable "key"):
{items_json}

Return ONLY a JSON object mapping each signal's "key" to an object with "why" and "action" fields. Example shape:
{{"signal-key-1": {{"why": "...", "action": "..."}}, "signal-key-2": {{"why": "...", "action": "..."}}}}
Every key in the input array must appear in your response.
"""


def synthesize_signals(items: list[dict], role_summary: str) -> dict[str, dict[str, str]] | None:
    """Best-effort LLM synthesis of "why it matters" + "what to do about it"
    text for signals that have no deterministic role-context match.

    items: [{"key": <stable dedup key>, "title": <signal headline/label>,
             "evidence": <short fact string already known by the system>}, ...]
    role_summary: static paragraph describing the user's role/context.

    Returns {key: {"why": ..., "action": ...}}, or None if unavailable/
    disabled/failed. Never raises -- callers treat this purely as an
    optional enhancement over the existing deterministic boilerplate.
    """
    if not items:
        return None

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        from openai import OpenAI
    except ImportError:
        return None

    trimmed = []
    for it in items[:MAX_ITEMS_PER_CALL]:
        key = str(it.get("key") or "").strip()
        title = str(it.get("title") or "").strip()
        evidence = str(it.get("evidence") or "").strip()[:MAX_EVIDENCE_CHARS]
        if not key or not title:
            continue
        trimmed.append({"key": key, "title": title, "evidence": evidence})
    if not trimmed:
        return None

    try:
        client = OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS)
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _USER_PROMPT_TEMPLATE.format(
                    role_summary=role_summary.strip(),
                    items_json=json.dumps(trimmed, indent=2),
                )},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=1500,
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    result: dict[str, dict[str, str]] = {}
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        why = str(v.get("why") or "").strip()[:400]
        action = str(v.get("action") or "").strip()[:300]
        if why and not _is_generic_hedge(why):
            result[str(k)] = {"why": why, "action": action or "No specific action warranted; awareness only."}
    return result or None
