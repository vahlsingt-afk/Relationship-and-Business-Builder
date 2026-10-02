"""Pre-processor for source_type=manual_text.

Real implementation (replaces the step-2 stub). The big job here is the
**quiet-chat RI screen gate** mandated by RI_EVENT_INTAKE_DESIGN.md
§"Quiet RI screening on chat" and the sprint UX rules:

    Normal chat: quiet RI scan. Do NOT say "no RI found" on ordinary
    conversation. Trigger an RI event capture ONLY when the message
    contains a positive RI signal.

This pre-processor returns `chat_scan_decision = "not_found"` for
ordinary chat (queries, drafting requests, metadata commands), causing
`ri_intake.review` to return immediately without invoking the classifier
or writing anything. For anything plausibly RI-bearing, it falls through
to `manual_relationship_intake.assess()` which has its own signal-
detection layer; if assess finds nothing, the existing not-found path in
`review` handles it.

The bias is **false-positive over false-negative**: a missed quiet-scan
results in a wasted assess() call (cheap, no canonical write); a false
positive on ordinary chat results in unwanted prose telling the user
"no RI found" (loud, breaks the UX rule). So when in doubt, return
not_found.

Resolution rules:

1. **Hard reject** when the message is clearly a query or a command:
   - starts with an interrogative ("what", "when", "where", ...)
   - starts with an imperative ("draft", "write", "regenerate", "summarize", ...)
   - is meta about RB itself ("today.md", "MANIFEST.md", "test trace", ...)
   These categories produce `chat_scan_decision = "not_found"`.

2. **Hard accept** when the message contains a clear RI keyword:
   - any of the recruiting state-transition phrasings (interview, resume,
     recruiter screen, follow-up sent, etc.)
   - any of the relationship-movement phrasings (introducing me, asked
     for the intro, going to circulate, etc.)
   These produce `chat_scan_decision = "found"`.

3. **Default**: pass through with `chat_scan_decision = "found"` and let
   `assess()` make the final call.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


# Interrogative / imperative openers — when a message starts with these,
# it's almost certainly a query or command, not an RI signal. The check
# is anchored to the start of the *stripped* message (we ignore leading
# whitespace + a single "hey" / "hi" pleasantry).
QUERY_OPENERS = (
    "what", "when", "where", "who", "whose", "whom", "which",
    "how", "why",
    "is ", "are ", "do ", "does ", "did ", "should ", "could ",
    "would ", "can ", "may ", "might ", "shall ",
    "tell me", "show me", "give me", "find ", "list ",
    "summarize", "explain", "help me understand", "walk me through",
    "remind me",
)

COMMAND_OPENERS = (
    "draft ", "write ", "compose ",
    "regenerate", "rebuild", "refresh", "rerun",
    "create ", "make ", "build ", "set up", "schedule",
    "open ", "close ", "delete ", "remove ",
    "send ", "post ", "publish",
    "save ", "update ",  # update can be RI in some cases; assess will catch it
    "fix ", "edit ",
)

# Meta — talking about RB itself, not relationships.
META_KEYWORDS_RX = re.compile(
    r"\b(today\.md|manifest\.md|baseline_index|loop_ledger|active_threads"
    r"|test trace|test_trace|daily brief|customGPT|custom gpt"
    r"|api smoke|openapi|rb tenet|tenet|protocol|the schema|the system"
    r"|regenerate|refresh sources|fetch sources)\b",
    re.I,
)

# Hard-accept RI keywords — recruiting + relationship-movement vocabulary.
# Aligns with manual_relationship_intake._detect_signals so this gate's
# positive set never lags the classifier.
RI_POSITIVE_RX = re.compile(
    r"\b("
    # Recruiting state transitions
    r"interview(?:ed|ing)?(?:\s+(?:went|was|with|for|today|yesterday))?"
    r"|recruiter(?:'?s)?(?:\s+(?:screen|call|conversation))?"
    r"|headhunter"
    r"|(?:my|the)\s+resume"
    r"|sent\s+(?:my|the|over\s+my)\s+(?:resume|cv|follow-?up|followup|reply)"
    r"|asked\s+for\s+(?:my|the)\s+(?:resume|cv|background|notes)"
    r"|wants?\s+another\s+(?:conversation|call|chat|meeting)"
    r"|second\s+(?:interview|conversation|round|chat|call)"
    r"|haven'?t\s+heard\s+back|have\s+not\s+heard\s+back"
    r"|email(?:s)?\s+(?:bounced|bounce(?:d)?\s+back|failed|not\s+deliver(?:ed|able))"
    r"|bounce(?:d)?\s+back|undeliver(?:ed|able)|delivery\s+(?:status|failure|failed)"
    r"|couldn'?t\s+(?:reach|email|get\s+through)|tried\s+(?:to\s+)?(?:reach|email|send)"
    r"|dns|nameserver|mx\s+record|email\s+routing|domain\s+(?:issue|outage)"
    r"|via\s+(?:text|sms|imessage)|tracked\s+(?:you|me)\s+down|switch(?:ed|ing)\s+(?:to|channels?)"
    r"|circulat(?:e|ed|ing)\s+(?:my|the|your)\s+(?:resume|info|notes|background)"
    # Relationship movement
    r"|introduc(?:e|ed|ing)\s+(?:me|us)"
    r"|intro(?:'?d)?\s+(?:to|with)"
    r"|making\s+(?:an|the)\s+intro"
    r"|put\s+in\s+(?:a|an)\s+(?:good|kind)?\s*word"
    r"|sponsor(?:ed|ing|ship)"
    r"|vouched?\s+for"
    r"|advocate(?:d)?\s+for"
    r"|reached\s+out\s+to"
    r"|met\s+with\s+[A-Z]"
    r"|spoke\s+(?:to|with)\s+[A-Z]"
    r"|talked\s+(?:to|with)\s+[A-Z]"
    r"|coffee\s+with\s+[A-Z]"
    r"|call\s+with\s+[A-Z]"
    # Opportunity movement
    r"|offer\s+letter|signed\s+the\s+offer|got\s+the\s+offer"
    r"|the\s+role\s+(?:opened|closed|moved|advanced)"
    r")\b",
    re.I,
)


def _normalize_opener(text: str) -> str:
    """Strip leading whitespace, a one-word pleasantry, and return the
    lowercase head of the message for opener-pattern checks."""
    s = (text or "").strip().lower()
    # Strip simple leading pleasantries: "hi", "hey", "hello", "ok", "yo".
    s = re.sub(r"^(hi|hey|hello|ok|okay|yo|so|well|um|uh|btw|fyi)[,\.\s]+", "", s)
    return s


def _classify_chat_scan(text: str) -> tuple[str, str]:
    """Return (decision, reason). decision ∈ {'found', 'not_found'}."""
    if not text or not text.strip():
        return "not_found", "empty_input"

    # Very short messages are almost never structured RI signal.
    stripped = text.strip()
    if len(stripped) < 8:
        return "not_found", "too_short"

    # Hard-accept first — recruiting/movement keywords win even if the
    # message also contains a query word.
    if RI_POSITIVE_RX.search(stripped):
        return "found", "ri_keyword_match"

    opener = _normalize_opener(stripped)

    for q in QUERY_OPENERS:
        if opener.startswith(q):
            return "not_found", f"query_opener:{q.strip()}"

    for c in COMMAND_OPENERS:
        if opener.startswith(c):
            return "not_found", f"command_opener:{c.strip()}"

    if META_KEYWORDS_RX.search(stripped):
        return "not_found", "meta_keyword"

    # Default: pass through to assess. If it finds no signals, ri_intake
    # will return not_found anyway. We say "found" here to mean "worth
    # screening," not "definitely RI."
    return "found", "default_passthrough"


def preprocess(req: dict[str, Any]) -> dict[str, Any]:
    captured_at = req.get("captured_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")
    event_at = req.get("event_at") or captured_at
    text = req.get("raw_text") or req.get("summary") or ""

    decision, reason = _classify_chat_scan(text)

    return {
        "text": text,
        "name": req.get("name"),
        "contact_id": req.get("contact_id"),
        "organization": req.get("organization"),
        "opportunity": req.get("opportunity"),
        "event_at": event_at,
        "event_at_confidence": req.get("event_at_confidence") or "low",
        "event_at_source": req.get("event_at_source") or "operator_provided_or_default",
        "captured_at": captured_at,
        "extracted_people": req.get("people") or [],
        "extracted_companies": req.get("companies") or [],
        "additional_signals": [],
        "source_extras": req.get("source_ref") or {},
        "chat_scan_decision": decision,
        "chat_scan_reason": reason,
    }
