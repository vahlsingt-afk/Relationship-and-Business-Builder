#!/usr/bin/env python3
"""signal_synthesis.py — Entity-level cross-store signal aggregator (RB 9.27).

A CoS doesn't assess one signal at a time — they pull everything known about
an entity from every store and synthesize a connected read. This module does
that: given a company or person name, it queries all RB stores, scores each
signal against a pattern taxonomy, and returns a synthesis hypothesis.

Pattern taxonomy:
    exit_positioning    — M&A-oriented leadership + board pressure + stock decline
    growth_mode         — revenue growth, expansion, new partnerships, hiring
    distress            — layoffs, restructuring, revenue decline, exits
    consolidation       — active M&A acquirer or acquisition target
    competitive_shift   — losing or gaining customers/market share
    transition          — leadership change without clear directional signal
    stable              — no convergent signal
    unknown             — too few signals to classify

Usage:
    python3 signal_synthesis.py --entity "PAR Technology"
    python3 signal_synthesis.py --entity "PAR Technology" --json
    python3 signal_synthesis.py --entity "PAR Technology" --days 60
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

# ---------------------------------------------------------------------------
# Pattern taxonomy
# ---------------------------------------------------------------------------

PATTERN_EXIT = "exit_positioning"
PATTERN_GROWTH = "growth_mode"
PATTERN_DISTRESS = "distress"
PATTERN_CONSOLIDATION = "consolidation"
PATTERN_COMPETITIVE = "competitive_shift"
PATTERN_TRANSITION = "transition"
PATTERN_STABLE = "stable"
PATTERN_UNKNOWN = "unknown"

KNOWN_PATTERNS = (
    PATTERN_EXIT, PATTERN_GROWTH, PATTERN_DISTRESS, PATTERN_CONSOLIDATION,
    PATTERN_COMPETITIVE, PATTERN_TRANSITION, PATTERN_STABLE, PATTERN_UNKNOWN,
)

# Keywords that map signal text to patterns
_PATTERN_KEYWORDS: dict[str, list[str]] = {
    PATTERN_EXIT: [
        "m&a", "mergers and acquisitions", "corporate development", "investment banking",
        "strategic alternatives", "strategic review", "activist investor", "activist",
        "board pressure", "board change", "proxy fight", "sale process",
        "divest", "divestiture", "spin-off", "go private", "take private",
        "undervalued", "stock decline", "stock drop", "shares fall",
        "shareholder pressure", "ceo departure", "ceo replaced", "leadership overhaul",
    ],
    PATTERN_GROWTH: [
        "record revenue", "revenue growth", "revenue up", "new partnership",
        "new customer", "customer win", "expansion", "hiring", "headcount",
        "new market", "launched", "new product", "new contract", "signed",
        "investment", "funding", "series", "raised", "series a", "series b",
        "ipo", "growth", "accelerating", "record quarter",
    ],
    PATTERN_DISTRESS: [
        "layoff", "layoffs", "restructuring", "cost cutting", "cost reduction",
        "revenue decline", "revenue down", "loss", "net loss", "miss", "missed",
        "below expectations", "guidance cut", "shutdown", "closure", "closed",
        "bankruptcy", "chapter 11", "struggling", "headwinds",
    ],
    PATTERN_CONSOLIDATION: [
        "acquisition", "acquired", "acquiring", "merger", "merged",
        "consolidation", "rollup", "combined with", "joining forces",
        "scale through acquisition", "bolt-on", "platform acquisition",
    ],
    PATTERN_COMPETITIVE: [
        "lost customer", "customer loss", "market share loss", "lost to",
        "replaced by", "switching from", "churn", "displacement",
        "customer win", "won against", "beat out", "selected over",
        "competitive win", "competitive loss",
    ],
    PATTERN_TRANSITION: [
        "new ceo", "new president", "new chief", "appointed", "named",
        "joined as", "promoted to", "taking over", "stepping down",
        "stepping up", "leadership change", "leadership transition",
        "executive change", "new leadership",
    ],
}

# Leadership backgrounds that signal exit positioning specifically
_EXIT_BACKGROUND_TERMS = frozenset({
    "m&a", "mergers", "acquisitions", "corporate development", "corp dev",
    "investment banking", "private equity", "pe", "venture capital", "vc",
    "transaction", "deal", "deal-making", "strategic transactions",
})

# ---------------------------------------------------------------------------
# Entity matching
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", " ", text.lower()).strip()


_SHORT_TOKEN_THRESHOLD = 4  # same threshold intelligence_triage._term_in_text() uses


def _entity_matches(entity_name: str, text: str) -> bool:
    """Return True if ALL of entity_name's significant tokens appear in
    text, each matched word-boundary-safe if short.

    RB-DEFECT (2026-09-16): the prior version did a raw substring check
    for the entity's full normalized name, and separately excluded any
    token under 4 chars from its AND-required multi-token fallback
    entirely. Live impact, confirmed: "Qu" -- a real, actively-tracked
    competitor -- matched any text containing "quarter", "acquisition",
    "equity", "require", "unique", anything with the letters q-u
    consecutive, false-positiving into that entity's convergence signals
    daily. "PAR" matched inside "comparable" -- the exact collision class
    already fixed once in intelligence_triage.py's registered-artifact
    matcher, but never here. Separately, a multi-word entity with a short
    first token (e.g. "NCR Voyix") silently dropped "ncr" from the match
    requirement rather than finding a safe way to require it, which
    *also* meant a bare "NCR" mention (no "Voyix") never matched at all --
    weaker recall, not just weaker precision. Now every token, short or
    long, gets a real match: token-set (word-boundary-safe) for tokens
    <= _SHORT_TOKEN_THRESHOLD chars, substring for longer ones -- same
    discipline as intelligence_triage._term_in_text()."""
    if not text:
        return False
    norm_entity = _normalize(entity_name)
    norm_text = _normalize(text)
    text_tokens = set(norm_text.split())

    entity_tokens = norm_entity.split()
    if not entity_tokens:
        return False

    def _token_hit(token: str) -> bool:
        if len(token) <= _SHORT_TOKEN_THRESHOLD:
            return token in text_tokens
        return token in norm_text

    return all(_token_hit(tok) for tok in entity_tokens)


def _score_text_for_patterns(text: str) -> dict[str, int]:
    """Count pattern keyword hits in text. Returns {pattern: hit_count}."""
    norm = _normalize(text)
    scores: dict[str, int] = {p: 0 for p in KNOWN_PATTERNS if p not in (PATTERN_STABLE, PATTERN_UNKNOWN)}
    for pattern, keywords in _PATTERN_KEYWORDS.items():
        for kw in keywords:
            if kw in norm:
                scores[pattern] += 1
    return scores


def _has_exit_background(text: str) -> bool:
    norm = _normalize(text)
    return any(term in norm for term in _EXIT_BACKGROUND_TERMS)

# ---------------------------------------------------------------------------
# Store readers
# ---------------------------------------------------------------------------

def _load_json_safe(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _load_yaml_safe(path: Path, default: Any = None) -> Any:
    """Load a YAML file, falling back to default on any error."""
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8")
    if _YAML_AVAILABLE:
        try:
            return _yaml.safe_load(text)
        except Exception:  # noqa: BLE001
            pass
    # Fallback: try JSON in case the caller passed a JSON file with a .yaml extension
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return default


def _active_thread_signals(entity_name: str, threads_path: Path) -> list[dict]:
    raw = _load_yaml_safe(threads_path)
    if not raw:
        return []
    threads = raw if isinstance(raw, list) else (raw.get("threads") or [])
    signals = []
    for thread in threads:
        companies = thread.get("companies") or []
        title = thread.get("title") or ""
        context = thread.get("context") or ""
        if not any(_entity_matches(entity_name, c) for c in companies) and \
                not _entity_matches(entity_name, title) and \
                not _entity_matches(entity_name, context):
            continue
        signals.append({
            "signal_id": f"thread:{thread.get('id', 'unknown')}",
            "signal_type": "active_thread",
            "description": f"Active thread: '{title}' (status: {thread.get('status','?')}, type: {thread.get('type','?')})",
            "source": "active_threads.yaml",
            "captured_at": thread.get("opened") or "",
            "pattern_tags": ["transition"],  # thread presence = active monitoring
            "confidence": "high",
            "thread_id": thread.get("id"),
        })
    return signals


def _market_signals(entity_name: str, cache_path: Path) -> list[dict]:
    raw = _load_json_safe(cache_path)
    if not raw:
        return []
    data = raw.get("data") or raw
    top = data.get("top") or []
    signals = []
    for sig in top:
        company = sig.get("company") or ""
        title = sig.get("title") or ""
        pain = sig.get("pain_point_or_priority") or ""
        if not any(_entity_matches(entity_name, t) for t in [company, title, pain]):
            continue
        text = f"{title} {pain}"
        pattern_scores = _score_text_for_patterns(text)
        tags = [p for p, s in pattern_scores.items() if s > 0] or ["market_signal"]
        signals.append({
            "signal_id": f"market:{sig.get('url_canonical', title[:40])}",
            "signal_type": "market_signal",
            "description": title,
            "detail": pain[:200] if pain else None,
            "source": sig.get("source_name") or "market_signals",
            "captured_at": sig.get("published_at") or "",
            "pattern_tags": tags,
            "confidence": "medium" if sig.get("strategic_relevance") == "high" else "low",
            "source_quality": sig.get("source_quality") or "unknown",
        })
    return signals


def _ri_event_signals(entity_name: str, ri_dir: Path, lookback_days: int) -> list[dict]:
    """Read persisted RI events from ri_events/*.jsonl."""
    if not ri_dir.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    signals = []
    for jsonl_file in sorted(ri_dir.glob("*.jsonl"), reverse=True)[:3]:
        try:
            for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Check captured_at for recency
                cap = ev.get("captured_at") or ev.get("event_at") or ""
                if cap:
                    try:
                        cap_dt = datetime.fromisoformat(cap.replace("Z", "+00:00"))
                        if cap_dt.tzinfo is None:
                            cap_dt = cap_dt.replace(tzinfo=timezone.utc)
                        if cap_dt < cutoff:
                            continue
                    except ValueError:
                        pass

                # Entity matching: check people entities and source text
                text_fields = [
                    str(ev.get("source", {}).get("title") or ""),
                    str(ev.get("signal", {}).get("reasoning") or ""),
                    json.dumps(ev.get("entities") or {}),
                ]
                if not any(_entity_matches(entity_name, t) for t in text_fields):
                    continue

                sig_type = (ev.get("signal") or {}).get("type") or "ri_event"
                description = (ev.get("source") or {}).get("title") or sig_type
                pattern_scores = _score_text_for_patterns(description)
                tags = [p for p, s in pattern_scores.items() if s > 0] or [PATTERN_TRANSITION]
                signals.append({
                    "signal_id": ev.get("event_id") or f"ri:{description[:30]}",
                    "signal_type": sig_type,
                    "description": description[:200],
                    "source": "ri_events",
                    "captured_at": cap,
                    "pattern_tags": tags,
                    "confidence": "medium",
                })
        except Exception:  # noqa: BLE001
            continue
    return signals


def _passive_ri_signals(entity_name: str, cache_path: Path, lookback_days: int) -> list[dict]:
    """Read passive RI ingest cache for entity signals."""
    raw = _load_json_safe(cache_path)
    if not raw:
        return []
    data = raw.get("data") or raw
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    signals = []

    def _check_events(events: list) -> None:
        for ev in (events or []):
            cap = ev.get("captured_at") or ""
            try:
                cap_dt = datetime.fromisoformat(cap.replace("Z", "+00:00"))
                if cap_dt.tzinfo is None:
                    cap_dt = cap_dt.replace(tzinfo=timezone.utc)
                if cap_dt < cutoff:
                    continue
            except (ValueError, AttributeError):
                pass
            text = json.dumps(ev.get("source_refs") or {}) + str(ev.get("signal") or {})
            if not _entity_matches(entity_name, text):
                continue
            desc = (ev.get("source") or {}).get("title") or str(ev.get("signal", {}).get("type") or "")
            pattern_scores = _score_text_for_patterns(desc)
            tags = [p for p, s in pattern_scores.items() if s > 0] or [PATTERN_TRANSITION]
            signals.append({
                "signal_id": ev.get("event_id") or f"passive:{desc[:30]}",
                "signal_type": (ev.get("signal") or {}).get("type") or "passive_ri",
                "description": desc[:200],
                "source": "passive_ri_ingest",
                "captured_at": cap,
                "pattern_tags": tags,
                "confidence": "low",
            })

    for key in ("events_written", "duplicates_skipped", "blocked_low_confidence"):
        _check_events(data.get(key) or [])
    return signals


def _artifact_signal(entity_name: str, registry_path: Path) -> dict | None:
    raw = _load_json_safe(registry_path)
    if not raw:
        return None
    for art in raw.get("artifacts") or []:
        entity = (art.get("entity") or "").lower()
        name = (art.get("name") or "").lower()
        aliases = [str(a).lower() for a in (art.get("entity_aliases") or [])]
        if any(_entity_matches(entity_name, t) for t in [entity, name] + aliases):
            status = art.get("status") or "unknown"
            return {
                "signal_id": f"artifact:{art.get('artifact_id')}",
                "signal_type": "artifact_record",
                "description": (
                    f"RB has a {status} artifact for {art.get('name')} "
                    f"(id: {art.get('artifact_id')}, enrichments: {art.get('enrichment_count', 0)})"
                ),
                "source": "artifact_registry",
                "captured_at": art.get("freshness_date") or "",
                "pattern_tags": [],
                "confidence": "high",
                "artifact_id": art.get("artifact_id"),
                "artifact_status": status,
            }
    return None


def _ecosystem_signals(entity_name: str, eco_path: Path) -> list[dict]:
    raw = _load_json_safe(eco_path)
    if not raw:
        return []
    signals = []
    # Watch list
    watch_list = raw.get("watch_list") or []
    for item in watch_list:
        name = (item.get("entity") or item.get("name") or "").lower()
        if _entity_matches(entity_name, name):
            signals.append({
                "signal_id": f"watchlist:{name}",
                "signal_type": "watch_list",
                "description": f"Entity is on RB watch list (tier: {item.get('tier','?')}, last signal: {item.get('last_signal_date','?')})",
                "source": "ecosystem_intelligence",
                "captured_at": item.get("last_signal_date") or "",
                "pattern_tags": [],
                "confidence": "high",
            })
    # Entity records
    entities = raw.get("entities") or []
    for ent in entities:
        names = [str(ent.get("name") or ""), str(ent.get("id") or "")]
        names += [str(a) for a in (ent.get("aliases") or [])]
        if ent.get("ticker"):
            names.append(str(ent["ticker"]))
        if not any(_entity_matches(entity_name, n) for n in names):
            continue
        # Signals on this entity
        for sig in (ent.get("signals") or []):
            sig_text = str(sig.get("description") or sig.get("title") or "")
            pattern_scores = _score_text_for_patterns(sig_text)
            tags = [p for p, s in pattern_scores.items() if s > 0]
            signals.append({
                "signal_id": f"ecosystem:{ent.get('id')}:{sig.get('id', sig_text[:20])}",
                "signal_type": sig.get("signal_type") or "ecosystem_signal",
                "description": sig_text[:200],
                "source": "ecosystem_intelligence",
                "captured_at": sig.get("date") or sig.get("captured_at") or "",
                "pattern_tags": tags,
                "confidence": "medium",
            })
    return signals


def _baseline_contacts(entity_name: str, baseline_path: Path) -> list[dict]:
    raw = _load_json_safe(baseline_path)
    if not raw:
        return []
    contacts = []
    for contact in (raw if isinstance(raw, list) else []):
        company = (contact.get("company") or "").lower()
        if not _entity_matches(entity_name, company):
            continue
        name = f"{contact.get('first_name','')} {contact.get('last_name','')}".strip()
        title = contact.get("title") or contact.get("position") or ""
        has_exit_bg = _has_exit_background(str(contact.get("notes") or "") + " " + title)
        contacts.append({
            "name": name,
            "title": title,
            "contact_id": contact.get("id") or contact.get("slug") or "",
            "tier": contact.get("tier") or "unknown",
            "exit_background_signal": has_exit_bg,
        })
    return contacts


def _social_overlay_signals(entity_name: str, cache_path: Path, lookback_days: int) -> list[dict]:
    raw = _load_json_safe(cache_path)
    if not raw:
        return []
    data = raw.get("data") or raw
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    signals = []
    posts = data.get("posts") or data.get("top_posts") or []
    for post in posts:
        author = post.get("author") or {}
        author_name = author.get("name") or ""
        author_headline = author.get("headline") or ""
        text = post.get("text") or ""
        captured = post.get("captured_at") or ""
        full_text = f"{author_name} {author_headline} {text}"
        if not _entity_matches(entity_name, full_text):
            continue
        try:
            cap_dt = datetime.fromisoformat(captured.replace("Z", "+00:00"))
            if cap_dt.tzinfo is None:
                cap_dt = cap_dt.replace(tzinfo=timezone.utc)
            if cap_dt < cutoff:
                continue
        except (ValueError, AttributeError):
            pass
        has_exit_bg = _has_exit_background(author_headline + " " + text)
        pattern_scores = _score_text_for_patterns(text)
        tags = [p for p, s in pattern_scores.items() if s > 0]
        if has_exit_bg:
            tags.append(PATTERN_EXIT)
        if not tags:
            tags = [PATTERN_TRANSITION]
        signals.append({
            "signal_id": f"social:{post.get('id', author_name[:20])}",
            "signal_type": "social_post",
            "description": f"{author_name} ({author_headline[:60]}): {text[:120]}",
            "source": "social_feed",
            "captured_at": captured,
            "pattern_tags": list(set(tags)),
            "confidence": "medium",
            "author_exit_background": has_exit_bg,
        })
    return signals

# ---------------------------------------------------------------------------
# Pattern scoring and synthesis
# ---------------------------------------------------------------------------

def _compute_pattern_scores(signals: list[dict]) -> dict[str, int]:
    scores: dict[str, int] = {p: 0 for p in KNOWN_PATTERNS if p not in (PATTERN_STABLE, PATTERN_UNKNOWN)}
    for sig in signals:
        for tag in (sig.get("pattern_tags") or []):
            if tag in scores:
                # Weight by confidence
                weight = {"high": 3, "medium": 2, "low": 1}.get(sig.get("confidence") or "low", 1)
                scores[tag] += weight
        # Extra weight for explicit exit background
        if sig.get("author_exit_background") or sig.get("exit_background_signal"):
            scores[PATTERN_EXIT] += 2
    return scores


def _dominant_pattern(scores: dict[str, int]) -> tuple[str, str]:
    """Return (dominant_pattern, confidence)."""
    active = {p: s for p, s in scores.items() if s > 0}
    if not active:
        return PATTERN_UNKNOWN, "low"
    best = max(active, key=lambda p: active[p])
    best_score = active[best]
    # If multiple patterns are close (within 2 points), it's ambiguous
    close = [p for p, s in active.items() if s >= best_score - 2 and p != best]
    if best_score >= 6:
        confidence = "high"
    elif best_score >= 3:
        confidence = "medium"
    else:
        confidence = "low"
    # Ambiguity check
    if close and confidence != "low":
        confidence = "medium" if confidence == "high" else "low"
    return best, confidence


_SYNTHESIS_TEMPLATES: dict[str, str] = {
    PATTERN_EXIT: (
        "Combined read: {entity} shows exit-positioning signals. An organization "
        "in exit-positioning mode may deprioritize new vendor commitments and "
        "accelerate decisions on existing partnerships. Watch for timeline compression "
        "on any open conversations."
    ),
    PATTERN_GROWTH: (
        "Combined read: {entity} is in growth mode. Organizations in active growth "
        "are typically more open to new vendor relationships and platform commitments. "
        "This is a better-than-average timing window."
    ),
    PATTERN_DISTRESS: (
        "Combined read: {entity} shows distress signals. Decision-making may slow "
        "or become risk-averse. Buying cycles extend. Relationships with champions "
        "inside the organization become more important as budgets tighten."
    ),
    PATTERN_CONSOLIDATION: (
        "Combined read: {entity} is in consolidation mode — actively acquiring or "
        "being consolidated. M&A activity typically creates vendor uncertainty: "
        "integration reviews, platform rationalization, and champion turnover."
    ),
    PATTERN_COMPETITIVE: (
        "Combined read: {entity} is under competitive pressure — either gaining or "
        "losing ground on key accounts. Competitive dynamics are in motion; "
        "positioning relative to alternatives matters now."
    ),
    PATTERN_TRANSITION: (
        "Combined read: {entity} is in leadership transition. New executives set "
        "new priorities; relationships with the incoming team matter more than "
        "prior executive relationships. Requalify existing access."
    ),
    PATTERN_STABLE: (
        "Combined read: No strong directional signal for {entity}. "
        "Signals are consistent with steady-state operations."
    ),
    PATTERN_UNKNOWN: (
        "Combined read: Insufficient signals for {entity} to establish a pattern. "
        "RB has {signal_count} signal(s); more data needed for synthesis."
    ),
}


def _build_hypothesis(entity: str, pattern: str, confidence: str, signals: list[dict],
                      signal_count: int) -> str:
    template = _SYNTHESIS_TEMPLATES.get(pattern, _SYNTHESIS_TEMPLATES[PATTERN_UNKNOWN])
    base = template.format(entity=entity, signal_count=signal_count)

    # Append top signal descriptions as evidence
    evidence_signals = [
        s for s in signals
        if s.get("signal_type") not in ("artifact_record", "watch_list", "active_thread")
    ][:4]
    if evidence_signals:
        evidence_lines = "; ".join(
            s["description"][:80].replace("\n", " ") for s in evidence_signals
        )
        base += f" Evidence: {evidence_lines}."
    if confidence == "low":
        base += " Confidence is low — treat as emerging hypothesis, not confirmed pattern."
    return base


def _opportunity_or_risk(pattern: str, has_active_thread: bool) -> str:
    """One-line implication for Todd's position."""
    thread_note = " You have an active thread here — timing awareness is critical." if has_active_thread else ""
    mapping = {
        PATTERN_EXIT: f"Risk: conversations may stall or accelerate unexpectedly; get to decision-makers now.{thread_note}",
        PATTERN_GROWTH: f"Opportunity: growth-mode org is more likely to commit to new relationships.{thread_note}",
        PATTERN_DISTRESS: f"Risk: budgets and timelines are uncertain; champion relationships become the moat.{thread_note}",
        PATTERN_CONSOLIDATION: f"Risk/opportunity: consolidation creates platform decisions — position early.{thread_note}",
        PATTERN_COMPETITIVE: f"Competitive window: if they're gaining, get in now; if losing, wait for reset.{thread_note}",
        PATTERN_TRANSITION: f"Action: new leadership = new relationship surface. Requalify and get introduced.{thread_note}",
        PATTERN_STABLE: "Monitor: no urgency trigger. Continue current posture.",
        PATTERN_UNKNOWN: "Insufficient data to assess risk or opportunity.",
    }
    return mapping.get(pattern, "Unknown.")

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def synthesize_entity_signals(
    entity_name: str,
    *,
    lookback_days: int = 90,
    aliases: list[str] | None = None,
    # Injectable paths for testing
    baseline_path: Path | None = None,
    registry_path: Path | None = None,
    market_signals_path: Path | None = None,
    ri_events_dir: Path | None = None,
    ri_cache_path: Path | None = None,
    active_threads_path: Path | None = None,
    ecosystem_path: Path | None = None,
    social_overlay_path: Path | None = None,
) -> dict:
    """Aggregate and synthesize all known signals about a named entity.

    RB-DEFECT (2026-09-16): a signal mentioning an entity only by an
    alternate name (e.g. "NCR" for the tracked competitor "NCR Voyix")
    previously never counted toward that entity's synthesis at all --
    _entity_matches requires every token of the passed name, and no
    caller ever passed alternate names to check. `aliases` lets a caller
    (e.g. entity_convergence_scan.py, which already has each competitor's
    real alias list from its registry) widen the search without touching
    the underlying ecosystem-graph entity-identity model. Signals found
    under any alias are merged into the SAME evidence pool before pattern
    scoring runs once over the combined set -- never scored per-alias and
    combined after, which would double-count a pattern found under two
    names as stronger evidence than it really is.

    Returns a structured synthesis with dominant pattern, hypothesis, and
    opportunity/risk assessment grounded in RB's current state.
    """
    entity_name = entity_name.strip()
    names_to_match = [entity_name] + [
        a.strip() for a in (aliases or []) if a and a.strip() and a.strip().casefold() != entity_name.casefold()
    ]

    # Resolve paths
    _baseline = baseline_path or core.BASELINE_PATH
    _registry = registry_path or (core.SYSTEM_DIR / "artifacts" / "registry.json")
    _market = market_signals_path or (core.CACHE_DIR / "market_signals.json")
    _ri_dir = ri_events_dir or (core.SYSTEM_DIR / "ri_events")
    _ri_cache = ri_cache_path or (core.CACHE_DIR / "passive_ri_ingest.json")
    _threads = active_threads_path or core.ACTIVE_THREADS_PATH
    _ecosystem = ecosystem_path or core.ECOSYSTEM_INTELLIGENCE_PATH
    _social = social_overlay_path or (core.CACHE_DIR / "social_overlay.json")

    # Collect signals from all stores, across the primary name and every
    # alias, deduped by signal_id so a signal matched under two names
    # (e.g. both "NCR" and "NCR Voyix" literally appear in the same
    # article) contributes once, not twice.
    all_signals: list[dict] = []
    _seen_signal_ids: set[str] = set()

    def _extend_deduped(signals: list[dict]) -> None:
        for sig in signals:
            sig_id = sig.get("signal_id")
            if sig_id is not None and sig_id in _seen_signal_ids:
                continue
            if sig_id is not None:
                _seen_signal_ids.add(sig_id)
            all_signals.append(sig)

    artifact_sig = None
    baseline_contacts: list[dict] = []
    _seen_contact_ids: set[str] = set()

    for name in names_to_match:
        _extend_deduped(_active_thread_signals(name, _threads))
        _extend_deduped(_market_signals(name, _market))
        _extend_deduped(_ri_event_signals(name, _ri_dir, lookback_days))
        _extend_deduped(_passive_ri_signals(name, _ri_cache, lookback_days))
        _extend_deduped(_ecosystem_signals(name, _ecosystem))
        _extend_deduped(_social_overlay_signals(name, _social, lookback_days))

        if artifact_sig is None:
            artifact_sig = _artifact_signal(name, _registry)

        for contact in _baseline_contacts(name, _baseline):
            cid = contact.get("contact_id")
            if cid and cid in _seen_contact_ids:
                continue
            if cid:
                _seen_contact_ids.add(cid)
            baseline_contacts.append(contact)

    artifact_id = None
    artifact_status = "none"
    if artifact_sig:
        all_signals.append(artifact_sig)
        artifact_id = artifact_sig.get("artifact_id")
        artifact_status = artifact_sig.get("artifact_status") or "unknown"

    # Score patterns
    pattern_scores = _compute_pattern_scores(all_signals)
    dominant, pat_confidence = _dominant_pattern(pattern_scores)
    if not all_signals:
        dominant, pat_confidence = PATTERN_UNKNOWN, "low"

    # Active thread detection
    active_thread_ids = [
        s["thread_id"] for s in all_signals
        if s.get("signal_type") == "active_thread" and s.get("thread_id")
    ]
    has_active_thread = bool(active_thread_ids)

    # Watch list status
    watch_list_status = "watching" if any(
        s.get("signal_type") == "watch_list" for s in all_signals
    ) else "not_watching"

    # Synthesis
    hypothesis = _build_hypothesis(
        entity_name, dominant, pat_confidence, all_signals, len(all_signals)
    )
    opp_or_risk = _opportunity_or_risk(dominant, has_active_thread)

    # Deduplicate signals by signal_id
    seen: set[str] = set()
    deduped: list[dict] = []
    for s in all_signals:
        sid = s.get("signal_id") or ""
        if sid not in seen:
            seen.add(sid)
            deduped.append(s)

    # Sort: most recent first (signals with dates), then undated
    def _sig_date(s: dict) -> str:
        return s.get("captured_at") or ""

    deduped.sort(key=_sig_date, reverse=True)

    return {
        "entity": entity_name,
        "entity_type": "company",  # default; can be overridden by caller
        "lookback_days": lookback_days,
        "signal_count": len(deduped),
        "signals": deduped,
        "pattern_scores": pattern_scores,
        "dominant_pattern": dominant,
        "pattern_confidence": pat_confidence,
        "synthesis_hypothesis": hypothesis,
        "opportunity_or_risk": opp_or_risk,
        "active_threads": active_thread_ids,
        "watch_list_status": watch_list_status,
        "artifact_id": artifact_id,
        "artifact_status": artifact_status,
        "baseline_contacts": baseline_contacts,
        "staleness_flags": [],  # populated by caller if needed
        "contract": "rb_entity_signal_synthesis_v1",
    }

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--entity", required=True, help="Company or person name to synthesize.")
    p.add_argument("--days", type=int, default=90, help="Lookback window in days (default: 90).")
    p.add_argument("--json", action="store_true", help="Output raw JSON.")
    args = p.parse_args()

    result = synthesize_entity_signals(args.entity, lookback_days=args.days)

    if getattr(args, "json"):
        print(json.dumps(result, indent=2))
        return 0

    print(f"\nEntity: {result['entity']}")
    print(f"Signals found: {result['signal_count']} (lookback: {result['lookback_days']}d)")
    print(f"Pattern: {result['dominant_pattern']} (confidence: {result['pattern_confidence']})")
    print(f"Artifact: {result['artifact_status']} | Watch list: {result['watch_list_status']}")
    print(f"Active threads: {result['active_threads'] or 'none'}")
    print(f"Baseline contacts: {len(result['baseline_contacts'])}")
    print()
    print(f"Synthesis:\n  {result['synthesis_hypothesis']}")
    print()
    print(f"Opportunity/risk:\n  {result['opportunity_or_risk']}")
    print()
    if result["signals"]:
        print("Signals:")
        for sig in result["signals"][:8]:
            date_str = (sig.get("captured_at") or "?")[:10]
            print(f"  [{sig['signal_type']}] ({date_str}) {sig['description'][:90]}")
            if sig.get("pattern_tags"):
                print(f"    → patterns: {', '.join(sig['pattern_tags'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
