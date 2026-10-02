#!/usr/bin/env python3
"""transcript_summarizer.py — LLM-based insight extraction for meeting transcripts.

Capture Intelligence's keyword-trigger classifiers (intelligence_triage.py)
count trigger-word hits but never read for actual meaning — a transcript
mentioning "Cool, Well, Yeah, God" as "possible named subjects" is the
output of a capitalized-word regex, not an understanding of who was in the
room or what was decided. This module asks a real LLM (OpenAI) to read the
transcript and extract the four things a Chief of Staff actually needs:
who was discussed, what was covered, what was decided, and what follows.

Best-effort by design: returns None (never raises) when OPENAI_API_KEY
isn't configured, the openai package isn't installed, the transcript is
too short to be worth a call, or the API call fails for any reason.
Callers must fall back to the existing trigger-based rendering when this
returns None.
"""
from __future__ import annotations

import json
import os

MODEL = "gpt-4o-mini"
MIN_WORDS_TO_SUMMARIZE = 30
# gpt-4o-mini's context window is ~128k tokens (~500k chars) -- 12000 was far
# more conservative than the model requires and silently dropped 54% of a
# real 37-minute/~26k-char JPR recording before summarization (found
# 2026-07-20). 60000 chars covers the large majority of real meetings/voice
# notes at negligible added cost; still a hard cutoff, not chunking, for
# anything longer.
MAX_TRANSCRIPT_CHARS = 60000

_SYSTEM_PROMPT_TEMPLATE = (
    "You are a Chief of Staff's assistant. You read a raw {content_label} "
    "(sometimes messy or informal speech-to-text, sometimes clean article "
    "text) and extract only what actually happened or was actually said -- "
    "never invent people, companies, decisions, or facts that are not in "
    "the text. If the content is small talk or has no substantive content, "
    "say so plainly rather than padding the response."
)

_USER_PROMPT_TEMPLATE = """Read this {content_label} and return a JSON object with exactly these fields:

- "people_mentioned": array of real person names actually discussed or present (not filler words like "yeah"/"well"/"cool"). Empty array if none.
- "companies_mentioned": array of real company/organization names mentioned. Empty array if none.
- "topics": array of 1-4 short phrases describing what was actually discussed.
- "decisions": array of concrete decisions or commitments stated in the source. Empty array if none were made.
- "action_items": array of plain strings, each one specific follow-up action naming who owns it if stated (e.g. "Todd: follow up with FSTAC"). Never return an object/dict for an item -- always a single string. Empty array if none.
- "why_it_matters": one sentence on why this is worth a Chief of Staff's attention, or "Casual conversation, no follow-up needed." if it's genuinely just small talk or informational content with no real follow-up.

Return ONLY the JSON object, no other text.

Transcript:
{transcript}
"""


def _flatten_item(v) -> str:
    """RB-2026-08-25: despite the prompt asking for plain strings, gpt-4o-mini
    sometimes returns a structured object instead (seen live: action_items as
    {"owner": "unknown", "task": "..."}). A bare str(v) on a dict renders its
    Python repr verbatim in the brief ("{'owner': 'unknown', 'task': ...}") --
    unmistakably internal data leaking into reader-facing text. Extract the
    meaningful text instead of ever showing raw dict syntax to the reader."""
    if isinstance(v, dict):
        task = str(v.get("task") or v.get("action") or v.get("item") or "").strip()
        owner = str(v.get("owner") or "").strip()
        if not task:
            return ""
        if owner and owner.lower() not in ("unknown", "none", ""):
            return f"{owner}: {task}"
        return task
    return str(v).strip()


def _str_list(data: dict, key: str, limit: int = 10) -> list[str]:
    val = data.get(key)
    if not isinstance(val, list):
        return []
    return [flat for v in val if (flat := _flatten_item(v))][:limit]


# RB-DEFECT-2026-08-13: speech-to-text garbles names phonetically ("Todd
# Volsing" for Todd Vahlsing, "Poil Campero" for Pollo Campero) and gpt-4o-mini
# extracts them verbatim -- it has no grounding in who Todd is or which
# companies are actually in his network, so it can't tell a transcription
# error from a real (if unfamiliar) name. Correct only strong near-misses
# against known entities post-extraction; never invent a correction for a
# name that isn't a close match to something already known.
_OWNER_NAME = "Todd Vahlsing"
_KNOWN_ENTITIES_CACHE: list[str] | None = None


def _known_entities() -> list[str]:
    """Person + company names worth correcting transcription noise against.
    Best-effort: baseline_index.json is large, so this is loaded once per
    process rather than rescanned per capture.

    RB-DEFECT-2026-08-14: confirmed live -- "Poil Campero" (should be Pollo
    Campero) went uncorrected because the original version of this function
    gated BOTH people and companies to signal_class == "RC" (inner-circle
    relationship-tracked contacts). Pollo Campero's own baseline contacts are
    tagged LMI/VC, not RC, so the company name never made it into the known-
    entity set even though it's one of Todd's most actively worked accounts.
    Company names carry little misattribution risk (correcting a garbled
    company name to a known one doesn't put words in a real person's mouth),
    so those are pulled from every baseline entry regardless of tier. Person
    names stay RC-gated: correcting "John Smith" to a specific real inner-
    circle contact on a loose match is exactly the kind of wrong-identity
    substitution _canonicalize's cutoff is meant to avoid, and that risk is
    only acceptable for people Todd actually has an established relationship
    with, not the broader 3000+ row acquaintance/lead baseline.
    """
    global _KNOWN_ENTITIES_CACHE
    if _KNOWN_ENTITIES_CACHE is not None:
        return _KNOWN_ENTITIES_CACHE
    names: set[str] = {_OWNER_NAME}
    try:
        from pathlib import Path as _Path
        baseline_path = _Path(__file__).resolve().parents[1] / "baseline_index.json"
        entries = json.loads(baseline_path.read_text(encoding="utf-8"))
        for e in entries:
            if not isinstance(e, dict):
                continue
            c = (e.get("current_company") or "").strip()
            if c:
                names.add(c)
            if e.get("signal_class") == "RC":
                n = (e.get("name") or "").strip()
                if n:
                    names.add(n)
    except Exception:
        pass
    _KNOWN_ENTITIES_CACHE = sorted(names)
    return _KNOWN_ENTITIES_CACHE


def _canonicalize(raw_names: list[str]) -> list[str]:
    """Replace each name with a known-entity spelling only on a strong,
    unambiguous near-match. difflib cutoff 0.75 catches phonetic transcription
    garbling (Volsing/Vahlsing, Poil/Pollo Campero) while leaving genuinely
    different names alone -- this must never silently substitute one real
    person for another, only fix misheard spellings of a known one."""
    import difflib
    known = _known_entities()
    if not known:
        return raw_names
    out = []
    for raw in raw_names:
        match = difflib.get_close_matches(raw, known, n=1, cutoff=0.75)
        out.append(match[0] if match else raw)
    return out


def summarize_transcript(
    transcript: str, word_count: int | None = None, *,
    content_label: str = "meeting/call transcript",
) -> dict | None:
    """Best-effort LLM summary of a meeting transcript or other captured text.

    Returns a dict with people_mentioned/companies_mentioned/topics/
    decisions/action_items/why_it_matters, or None if unavailable/
    disabled/failed. Never raises -- callers treat this purely as an
    optional enhancement over the existing trigger-based extraction.

    `content_label` -- RB-2026-09-11: this was meeting/call-only in both
    framing and prompt wording until pasted articles/notes started flowing
    through the same capture pipeline (queueCaptureText). Callers pass a
    label matching what they're actually summarizing (e.g. "pasted article,
    web page, or note") so the model isn't asked to find meeting attendees
    and action items in a news article. Default preserves the original
    meeting-transcript behavior for existing callers.
    """
    if not transcript or not transcript.strip():
        return None
    wc = word_count if word_count is not None else len(transcript.split())
    if wc < MIN_WORDS_TO_SUMMARIZE:
        return None

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        from openai import OpenAI
    except ImportError:
        return None

    text = transcript.strip()
    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = text[:MAX_TRANSCRIPT_CHARS] + "\n[transcript truncated for length]"

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT_TEMPLATE.format(content_label=content_label)},
                {"role": "user", "content": _USER_PROMPT_TEMPLATE.format(content_label=content_label, transcript=text)},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=800,
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    people = _str_list(data, "people_mentioned")
    companies = _str_list(data, "companies_mentioned")
    try:
        people = _canonicalize(people)
        companies = _canonicalize(companies)
    except Exception:
        pass  # best-effort — raw (uncorrected) names are still better than none

    return {
        "people_mentioned": people,
        "companies_mentioned": companies,
        "topics": _str_list(data, "topics", limit=4),
        "decisions": _str_list(data, "decisions"),
        "action_items": _str_list(data, "action_items"),
        "why_it_matters": str(data.get("why_it_matters") or "").strip()[:300],
    }
