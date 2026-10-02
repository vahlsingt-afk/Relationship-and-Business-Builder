#!/usr/bin/env python3
"""
meeting_prep.py — generate reviewable pre-conversation brief artifacts.

Architecture-mandated structure: ARCHITECTURE.md → "Pre-conversation briefing
(auto-trigger)" defines an 11-section shape that the brief must follow. This
module produces one artifact per qualifying calendar event, populated only
from canonical state — no synthesis, no guessing (Tenet 1). Empty subsections
are omitted, not stubbed.

Two paths:

    python3 meeting_prep.py                      # list qualifying events, no writes
    python3 meeting_prep.py --event-id <id>      # print one prep brief to stdout
    python3 meeting_prep.py --write --confirm    # write briefs for today/tomorrow
    python3 meeting_prep.py --for-today          # write today's qualifying briefs
    python3 meeting_prep.py --smoke              # in-memory regression

Artifacts land in `system/meeting_briefs/YYYY-MM-DD-<slug>-<event_id>.md`.
The directory is created lazily; the writer snapshots an existing file before
overwrite (P-009-style discipline, scaled down — no validator needed because
these are projections, not canonical state).

Voice and discipline (mirrors ARCHITECTURE.md):
  * Direct, Midwestern, peer-level.
  * Every claim cites source files: cards, baseline, loop_ledger, IBs.
  * Section omitted when nothing real to say. Silence is intentional.
  * Brief is forward-looking; the IB gets written after the conversation.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core
import thread_promotion
import audit_log


MEETING_BRIEFS_DIR = core.SYSTEM_DIR / "meeting_briefs"

# Used to score events back into the "this needs a prep brief" bucket. Mirrors
# daily_brief.MEETING_DELIVERABLE_KEYWORDS so the two stay aligned.
DELIVERABLE_KEYWORDS = (
    "prep", "prepare", "review", "proposal", "deck", "presentation",
    "deliverable", "due", "deadline", "interview", "screen", "demo",
    "decision", "finalize", "contract", "scope", "sow",
)


# -----------------------------------------------------------------------------
# Path / naming helpers
# -----------------------------------------------------------------------------

def _slugify(s: str) -> str:
    s = (s or "untitled").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or "untitled"


def artifact_path_for(event: dict) -> Path:
    """Stable path for an event's prep artifact.

    Format: `meeting_briefs/YYYY-MM-DD-<title-slug>-<event_id>.md`
    Pure function; never touches the filesystem.
    """
    start = event.get("start") or ""
    day = start[:10] if len(start) >= 10 else "0000-00-00"
    slug = _slugify(event.get("title") or "untitled")
    ev_id = _slugify(str(event.get("id") or "no-id"))[:32]
    return MEETING_BRIEFS_DIR / f"{day}-{slug}-{ev_id}.md"


# -----------------------------------------------------------------------------
# Card reader
# -----------------------------------------------------------------------------

# Maps the 11-section spec to card heading names we actually find on disk.
CARD_SECTIONS = {
    "why_this_matters": "Why this matters",
    "narrative_arc": "Narrative arc",
    "trust_state": "Trust state",
    "leverage": "Leverage (asymmetric value)",
    "whats_lingering": "What's lingering",
    "unresolved_movement": "Unresolved movement",
    "their_world": "Their world",
    "how_to_engage": "How to engage",
    "risks": "Risks",
    "recent_ibs": "Recent IBs",
    "open_loops": "Open loops",
}


def _split_card_sections(text: str) -> dict[str, str]:
    """Return a dict of section_name -> raw body text (after the `## ...` line).

    The split is purely syntactic on `## ` headings. Frontmatter is stripped.
    """
    # Strip frontmatter
    m = re.match(r"^---\n(.*?\n)---\n", text, flags=re.DOTALL)
    body = text[m.end():] if m else text
    sections: dict[str, str] = {}
    current_key: Optional[str] = None
    buf: list[str] = []
    for line in body.splitlines():
        h = re.match(r"^##\s+(.+?)\s*$", line)
        if h:
            if current_key is not None:
                sections[current_key] = "\n".join(buf).strip()
            current_key = h.group(1).strip()
            buf = []
        else:
            if current_key is not None:
                buf.append(line)
    if current_key is not None:
        sections[current_key] = "\n".join(buf).strip()
    return sections


def _load_card_for(contact_id: str) -> Optional[dict]:
    """Return {frontmatter: dict, sections: dict, path: str} or None."""
    path = core.CARDS_DIR / f"{contact_id}.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    fm: dict = {}
    m = re.match(r"^---\n(.*?\n)---\n", text, flags=re.DOTALL)
    if m:
        for raw in m.group(1).splitlines():
            kv = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", raw)
            if kv:
                fm[kv.group(1)] = kv.group(2).strip()
    sections = _split_card_sections(text)
    return {
        "id": contact_id,
        "frontmatter": fm,
        "sections": sections,
        "path": str(path.relative_to(core.PROJECT_DIR)),
    }


# -----------------------------------------------------------------------------
# Per-attendee context assembly
# -----------------------------------------------------------------------------

@dataclass
class AttendeeContext:
    contact_id: Optional[str]
    name: str
    email: Optional[str]
    signal_class: Optional[str]
    rc_tier: Optional[str]
    last_touch: Optional[str]
    days_since_touch: Optional[int]
    momentum: Optional[str]
    company: Optional[str]
    role: Optional[str]
    card_path: Optional[str]
    card_sections: dict
    in_baseline: bool


def _days_since(today: date, iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        return (today - date.fromisoformat(iso[:10])).days
    except ValueError:
        return None


def _attendee_context(att: dict, baseline_by_id: dict[str, dict], today: date) -> AttendeeContext:
    cid = att.get("id")
    name = att.get("name") or att.get("email") or "Unknown attendee"
    if cid and cid in baseline_by_id:
        entry = baseline_by_id[cid]
        card = _load_card_for(cid)
        return AttendeeContext(
            contact_id=cid,
            name=entry.get("name") or name,
            email=entry.get("email") or att.get("email"),
            signal_class=entry.get("signal_class"),
            rc_tier=entry.get("rc_tier"),
            last_touch=entry.get("last_touch"),
            days_since_touch=_days_since(today, entry.get("last_touch")),
            momentum=(card or {}).get("frontmatter", {}).get("momentum"),
            company=entry.get("current_company"),
            role=entry.get("current_role"),
            card_path=(card or {}).get("path"),
            card_sections=(card or {}).get("sections", {}),
            in_baseline=True,
        )
    return AttendeeContext(
        contact_id=None,
        name=name,
        email=att.get("email"),
        signal_class=None,
        rc_tier=None,
        last_touch=None,
        days_since_touch=None,
        momentum=None,
        company=None,
        role=None,
        card_path=None,
        card_sections={},
        in_baseline=False,
    )


# -----------------------------------------------------------------------------
# Loop / thread / interaction lookups
# -----------------------------------------------------------------------------

def _open_loops_for(party_names: list[str], company_names: list[str]) -> list[dict]:
    """Return open loops whose `party` field references any attendee or company.

    Match is substring + case-insensitive. We err on the side of recall;
    irrelevant matches are rare because the loop ledger is hand-curated.
    """
    out: list[dict] = []
    needles = [n.lower() for n in party_names + company_names if n]
    if not needles:
        return out
    for L in core.parse_loop_ledger():
        if L.closed:
            continue
        party_low = (L.party or "").lower()
        desc_low = (L.description or "").lower()
        for needle in needles:
            if needle and (needle in party_low or needle in desc_low):
                out.append({
                    "id": L.id,
                    "party": L.party,
                    "description": L.description,
                    "target": L.target.isoformat(),
                    "opened": L.opened.isoformat(),
                    "matched_on": needle,
                })
                break
    return out


def _active_threads_for(thread_ids: list[str], people_ids: list[str],
                       company_names: list[str], all_threads: list[dict]) -> list[dict]:
    """Return active threads that touch this meeting (id, attendee, or company)."""
    out: list[dict] = []
    seen: set[str] = set()
    id_set = {t for t in thread_ids if t}
    company_low = {c.lower() for c in company_names if c}
    people_set = {p for p in people_ids if p}

    for t in all_threads:
        tid = t.get("id") or ""
        if t.get("status") and t["status"] != "open":
            continue
        if tid in seen:
            continue
        match_reason = None
        if tid in id_set:
            match_reason = "calendar_thread_match"
        elif any(p in (t.get("people") or []) for p in people_set):
            match_reason = "shared_person"
        elif any(c in company_low for c in [x.lower() for x in (t.get("companies") or [])]):
            match_reason = "shared_company"
        if match_reason:
            out.append({
                "id": tid,
                "title": t.get("title"),
                "current_state": t.get("current_state") or t.get("context") or "",
                "target_close": str(t.get("target_close") or ""),
                "boost_for_brief": t.get("boost_for_brief"),
                "match_reason": match_reason,
            })
            seen.add(tid)
    return out


# -----------------------------------------------------------------------------
# Brief generation
# -----------------------------------------------------------------------------

def event_qualifies(event: dict) -> bool:
    """Return True if an event should produce a prep brief.

    Aligned with daily_brief._meeting_prep_items so the set of meetings the
    Daily Brief surfaces for prep is exactly the set meeting_prep can
    materialize. Qualifying signals:

      * any attendee on the invite (matched or unmatched) — unknown
        attendees are themselves a Tenet 13a gap-surface signal worth
        producing a brief for;
      * an active-thread or active-thread-company tie;
      * a deliverable/prep keyword in the title.
    """
    attendees = event.get("attendees_matched") or []
    if attendees:
        return True
    if event.get("matched_threads") or event.get("active_thread_company_hits"):
        return True
    title_low = (event.get("title") or "").lower()
    if any(k in title_low for k in DELIVERABLE_KEYWORDS):
        return True
    return False


def _expected_outcome(event: dict, attendees: list[AttendeeContext],
                     threads_for: list[dict]) -> Optional[str]:
    """Best-effort labeling of what this meeting is for, from titles + threads.

    Returns None when there's no evidence — never invents an outcome.
    """
    title = (event.get("title") or "").lower()
    if any(k in title for k in ("screen", "interview", "phone screen")):
        return "Recruiting screen — qualify role fit, surface follow-up loops."
    if any(k in title for k in ("intro", "introduction")):
        return "Introduction call — open a new relationship lane; no commitments yet."
    if any(k in title for k in ("review", "proposal", "deck", "presentation")):
        return "Review/decision conversation — confirm next step and owner."
    if any(k in title for k in ("prep", "deliverable", "due", "deadline")):
        return "Prep/deliverable working session — produce a tangible artifact."
    if threads_for:
        return f"Move active thread '{threads_for[0]['title']}' forward."
    if any(a.signal_class == "RC" for a in attendees):
        return "Relationship-cadence conversation — protect trust and surface unresolved movement."
    return None


def build_prep_payload(event: dict, *, baseline: Optional[list[dict]] = None,
                       threads: Optional[list[dict]] = None,
                       today: Optional[date] = None) -> dict:
    """Return a structured prep payload (the API and the markdown renderer share it)."""
    today = today or date.today()
    if baseline is None:
        baseline = core.load_baseline()
    if threads is None:
        try:
            threads = core.load_active_threads()  # type: ignore[attr-defined]
        except AttributeError:
            threads = []
    baseline_by_id = {e["id"]: e for e in baseline if e.get("id")}

    # Attendee resolution
    raw_attendees = event.get("attendees_matched") or []
    attendees = [_attendee_context(a, baseline_by_id, today) for a in raw_attendees]
    known = [a for a in attendees if a.in_baseline]
    unknown = [a for a in attendees if not a.in_baseline]

    # Loops / threads
    party_names = [a.name for a in known]
    company_names = [a.company for a in known if a.company] + (event.get("active_thread_company_hits") or [])
    loops = _open_loops_for(party_names, company_names)
    matched_threads_ids = event.get("matched_threads") or []
    people_ids = [a.contact_id for a in known if a.contact_id]
    threads_for = _active_threads_for(matched_threads_ids, people_ids, company_names, threads)

    expected_outcome = _expected_outcome(event, attendees, threads_for)

    payload = {
        "contract": "meeting_prep_v1",
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "event": {
            "id": event.get("id"),
            "title": event.get("title"),
            "start": event.get("start"),
            "end": event.get("end"),
            "deliverable_signal": any(
                k in (event.get("title") or "").lower() for k in DELIVERABLE_KEYWORDS
            ),
        },
        "attendees": {
            "known": [
                {
                    "contact_id": a.contact_id,
                    "name": a.name,
                    "signal_class": a.signal_class,
                    "rc_tier": a.rc_tier,
                    "last_touch": a.last_touch,
                    "days_since_touch": a.days_since_touch,
                    "momentum": a.momentum,
                    "company": a.company,
                    "role": a.role,
                    "card_path": a.card_path,
                    "has_card": bool(a.card_path),
                }
                for a in known
            ],
            "unknown": [
                {
                    "name": a.name,
                    "email": a.email,
                    "promotion": thread_promotion.evaluate_promotion(
                        event, {"email": a.email, "name": None if a.name == a.email else a.name}
                    ),
                }
                for a in unknown
            ],
            "total": len(attendees),
        },
        "open_loops": loops,
        "active_threads": threads_for,
        "expected_outcome": expected_outcome,
        "artifact_path": str(artifact_path_for(event).relative_to(core.PROJECT_DIR)),
        # Per ARCHITECTURE.md: every claim cites a source file. The renderer
        # picks these up directly so the brief itself stays clean.
        "source_refs": [
            f"calendar.{event.get('id') or event.get('title') or 'unknown'}",
            "baseline_index.json",
            "loop_ledger.md",
            "active_threads.yaml",
        ] + [a["card_path"] for a in [
            {"card_path": a.card_path} for a in known if a.card_path
        ]],
    }

    # Carry the per-attendee card sections separately so the renderer can pull
    # "What's lingering", "Unresolved movement", "How to engage", etc., without
    # the API payload becoming enormous when no one asked for them.
    payload["_card_sections_by_id"] = {
        a.contact_id: a.card_sections for a in known if a.contact_id and a.card_sections
    }
    return payload


# -----------------------------------------------------------------------------
# Markdown rendering
# -----------------------------------------------------------------------------

def _bullet_card_section(name: str, sections: dict, key: str) -> Optional[str]:
    """Return a markdown sub-bullet `- (Attendee name): ...` if the card has a body."""
    body = (sections or {}).get(CARD_SECTIONS.get(key, "")) or ""
    body = body.strip()
    if not body or body.lower().startswith("(none") or body.startswith("*None"):
        return None
    # Collapse the body to a single readable line for the brief.
    one_line = re.sub(r"\s+", " ", body).strip()
    if len(one_line) > 600:
        one_line = one_line[:597] + "..."
    return f"- ({name}): {one_line}"


def render_prep_brief_md(payload: dict) -> str:
    ev = payload["event"]
    title = ev.get("title") or "Untitled meeting"
    start = ev.get("start") or ""
    out: list[str] = []
    out.append(f"# Meeting prep — {title}\n")
    when = f"{start[:10]} {start[11:16]}".strip() if start else "time TBD"
    out.append(f"*{when} — generated {payload.get('generated_at','')}*\n")

    # 1. Stage and context
    out.append("## 1. Stage and context\n")
    bits = []
    if payload["attendees"]["total"]:
        known = payload["attendees"]["known"]
        unknown = payload["attendees"]["unknown"]
        if known:
            tags = ", ".join(
                f"{a['name']} ({a.get('signal_class') or '—'}"
                + (f"/{a['rc_tier']}" if a.get("rc_tier") else "")
                + ")"
                for a in known
            )
            bits.append(f"Known attendees: {tags}.")
        if unknown:
            tags = []
            for a in unknown:
                promo = a.get("promotion")
                if promo:
                    tags.append(f"{promo['display_name']} (new — {promo['thread_type'].replace('_', ' ')})")
                else:
                    tags.append(a["name"])
            bits.append(f"{len(unknown)} attendee(s) not yet in baseline: "
                        + ", ".join(tags) + ".")
    if payload["active_threads"]:
        bits.append("Active-thread tie: "
                    + ", ".join(f"{t['title']} ({t['id']})"
                                for t in payload["active_threads"][:3]) + ".")
    if ev.get("deliverable_signal"):
        bits.append("Title carries deliverable wording — treat as a working session.")
    if not bits:
        bits.append("Calendar event with no detected relationship or deliverable signal.")
    out.append(" ".join(bits) + "\n")

    # 1a. New relationship — proposed thread (RB-DEFECT-038)
    proposals = [a["promotion"] for a in payload["attendees"]["unknown"] if a.get("promotion")]
    if proposals:
        out.append("## 1a. New relationship — proposed thread\n")
        for p in proposals:
            out.append(f"**{p['display_name']}** ({p['email']}) — {p['narrative']}")
            out.append("")
            out.append(f"Proposed thread type: `{p['thread_type']}` "
                        f"(boost_for_brief: {p['proposed_thread']['boost_for_brief']}). "
                        f"Status: {p['persistence_status']}.")
            out.append("")
            out.append("Proposed `active_threads.yaml` entry (paste to confirm):")
            out.append("```yaml")
            out.append(p["proposed_thread_yaml"])
            out.append("```")
            out.append("")
        out.append("")

    # 2. Relationship state
    known = payload["attendees"]["known"]
    if known:
        out.append("## 2. Relationship state\n")
        for a in known:
            line = f"- {a['name']}"
            tag_bits = []
            if a.get("signal_class"):
                tag_bits.append(a["signal_class"])
            if a.get("rc_tier"):
                tag_bits.append(f"tier={a['rc_tier']}")
            if a.get("momentum"):
                tag_bits.append(f"momentum={a['momentum']}")
            if tag_bits:
                line += f" — {', '.join(tag_bits)}."
            if a.get("last_touch"):
                line += (f" Last touch: {a['last_touch']}"
                         + (f" ({a['days_since_touch']}d ago)." if a.get("days_since_touch") is not None else "."))
            else:
                line += " Last touch: not recorded."
            line += f" [baseline_index.json#{a['contact_id']}]"
            out.append(line)
        out.append("")

    # 3. What's lingering / 4. What NOT to bring up / 5+ from card content
    card_sections_by_id = payload.get("_card_sections_by_id") or {}
    name_by_id = {a["contact_id"]: a["name"] for a in known if a.get("contact_id")}

    def _multi_attendee_section(num: int, label: str, card_key: str) -> None:
        lines: list[str] = []
        for cid, sections in card_sections_by_id.items():
            bullet = _bullet_card_section(name_by_id.get(cid, cid), sections, card_key)
            if bullet:
                lines.append(bullet)
        if lines:
            out.append(f"## {num}. {label}\n")
            out.extend(lines)
            out.append("")

    _multi_attendee_section(3, "What's lingering", "whats_lingering")
    _multi_attendee_section(4, "What NOT to bring up", "risks")

    # 5. Open loops involving them
    if payload["open_loops"]:
        out.append("## 5. Open loops involving them\n")
        for L in payload["open_loops"][:8]:
            out.append(f"- **{L['id']}** ({L['party']}, target {L['target']}) — {L['description']}")
        out.append("")

    # 6. Recent IBs
    _multi_attendee_section(6, "Recent IBs", "recent_ibs")

    # 7. Relevant gap-surface (T13a)
    gap_lines: list[str] = []
    for a in known:
        gaps = []
        if not a.get("last_touch"):
            gaps.append("no last_touch recorded")
        if not a.get("has_card"):
            gaps.append("no RC card")
        if gaps:
            gap_lines.append(f"- {a['name']}: " + ", ".join(gaps) + ".")
    if payload["attendees"]["unknown"]:
        gap_lines.append(
            f"- {len(payload['attendees']['unknown'])} attendee(s) not in baseline — "
            "add via /contacts if they have RI."
        )
    if gap_lines:
        out.append("## 7. Relevant gap-surface\n")
        out.extend(gap_lines)
        out.append("")

    # 8. Thesis lens — only populate when we have explicit signal; never invent.
    # Daily brief's market signals layer is where theses are managed; leave
    # blank when no signal exists per ARCHITECTURE.md.

    # 9. Network context — co-attendees by company
    network_lines: list[str] = []
    company_groups: dict[str, list[str]] = {}
    for a in known:
        if a.get("company"):
            company_groups.setdefault(a["company"], []).append(a["name"])
    if len(company_groups) >= 1 and any(len(v) > 1 for v in company_groups.values()):
        for company, names in company_groups.items():
            if len(names) > 1:
                network_lines.append(f"- Multiple attendees from {company}: {', '.join(names)}.")
    if network_lines:
        out.append("## 9. Network context\n")
        out.extend(network_lines)
        out.append("")

    # 10. Three questions / asks — calibrated by expected outcome label.
    questions = _generate_questions(payload)
    if questions:
        out.append("## 10. Three questions / asks\n")
        for q in questions:
            out.append(f"- {q}")
        out.append("")

    # 11. Engagement-boundary check (only when there's an unpaid-thinking risk)
    boundary = _boundary_check(payload)
    if boundary:
        out.append("## 11. Engagement-boundary check\n")
        out.append(boundary)
        out.append("")

    # Expected outcome (forward-look summary)
    if payload.get("expected_outcome"):
        out.append("## Expected outcome\n")
        out.append(payload["expected_outcome"])
        out.append("")

    # Active threads block (subordinate to the 11 sections; kept short)
    if payload["active_threads"]:
        out.append("## Active threads touching this meeting\n")
        for t in payload["active_threads"][:4]:
            tc = t.get("target_close") or "—"
            out.append(f"- **{t['id']}** — {t.get('title')}. State: "
                       f"{t.get('current_state') or '(none recorded)'} "
                       f"Target close: {tc}.")
        out.append("")

    # Sources
    out.append("---")
    out.append(f"*Sources: {', '.join(sorted(set(payload.get('source_refs') or [])))}*")
    return "\n".join(out) + "\n"


def _generate_questions(payload: dict) -> list[str]:
    """Build at most three calibrated questions from canonical context only.

    Never synthesizes. If there's not enough signal, returns fewer than three.
    """
    out: list[str] = []
    threads = payload.get("active_threads") or []
    if threads:
        t = threads[0]
        out.append(
            f"What's the next concrete move on '{t.get('title')}' before "
            f"{t.get('target_close') or 'target close'}?"
        )
    for L in (payload.get("open_loops") or [])[:1]:
        out.append(
            f"Open loop {L['id']} ({L['description'][:80]}{'…' if len(L['description'])>80 else ''}) — "
            "is the recorded target still right?"
        )
    known = payload["attendees"]["known"]
    if known and known[0].get("days_since_touch") is not None and known[0]["days_since_touch"] > 60:
        out.append(
            f"It's been {known[0]['days_since_touch']} days since the last touch with "
            f"{known[0]['name']} — anything from their side that changed since then?"
        )
    return out[:3]


def _boundary_check(payload: dict) -> Optional[str]:
    """Surface a BridgePoint Ops engagement boundary only when there's risk.

    The risk pattern: known RC, deliverable-signal title, no closed loop opened
    for paid scope. This is intentionally narrow — a generic "watch the
    boundary" prompt on every meeting would be noise.
    """
    if not payload["event"].get("deliverable_signal"):
        return None
    rc_attendees = [a for a in payload["attendees"]["known"] if a.get("signal_class") == "RC"]
    if not rc_attendees:
        return None
    return (
        "Deliverable-shaped meeting with an RC attendee. If the conversation "
        "turns into unpaid scoping or free intro-broker work, name the boundary. "
        "BridgePoint Ops engagement model applies."
    )


# -----------------------------------------------------------------------------
# Selection: which events from today's brief warrant prep
# -----------------------------------------------------------------------------

def collect_candidate_events(report: dict) -> list[dict]:
    """Pick events from the daily-brief calendar overlay that qualify for prep.

    Covers today + tomorrow + this_week so this stays in lockstep with
    daily_brief._meeting_prep_items and smart_loops._propose_meeting_prep —
    a meeting on Friday still benefits from prep work scheduled Monday, so
    cutting off after tomorrow created proposals that smart_loops thought
    were valid but meeting_prep couldn't materialize.
    """
    cal = report.get("calendar") or {}
    if not cal.get("fetched_at"):
        return []
    out: list[dict] = []
    for bucket in ("today", "tomorrow", "this_week"):
        for ev in cal.get(bucket) or []:
            if event_qualifies(ev):
                ev_copy = dict(ev)
                ev_copy["_bucket"] = bucket
                out.append(ev_copy)
    return out


# -----------------------------------------------------------------------------
# File I/O — write-back with snapshot
# -----------------------------------------------------------------------------

def write_artifact(payload: dict, *, dry_run: bool = True) -> dict:
    """Write the prep brief markdown to its canonical path.

    Safety contract:
      * dry_run=True (default) returns the rendered markdown without writing.
      * On overwrite, snapshots the prior artifact into _snapshots/.
      * Never validates against schemas (these are projections, not canonical).
      * Returns the result envelope used by the API.
    """
    md = render_prep_brief_md(payload)
    target = MEETING_BRIEFS_DIR / Path(payload["artifact_path"]).name
    rel = str(target.relative_to(core.PROJECT_DIR))
    if dry_run:
        return {"ok": True, "dry_run": True, "path": rel, "bytes": len(md.encode("utf-8")), "preview": md}
    MEETING_BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    if target.exists():
        core.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        snap = core.SNAPSHOTS_DIR / (
            target.stem + f".pre-meeting-prep-{datetime.now().strftime('%Y%m%d-%H%M%S')}" + target.suffix
        )
        shutil.copy2(target, snap)
    target.write_text(md, encoding="utf-8")
    _apply_promotions(payload)
    return {"ok": True, "dry_run": False, "path": rel, "bytes": len(md.encode("utf-8"))}


def _apply_promotions(payload: dict) -> None:
    """RB-DEFECT-038: for each Tier-3 promotion proposal on this event, create
    the baseline contact (additive, low-risk — auto-applied) and audit-log a
    proposed thread for `active_threads.yaml` (hand-curated — Todd confirms).
    """
    for a in payload["attendees"]["unknown"]:
        promo = a.get("promotion")
        if not promo:
            continue
        entry = promo["proposed_baseline_entry"]
        result = thread_promotion.apply_baseline_entry(entry)
        if result.get("applied"):
            audit_log.log_item_persisted(
                item_summary=f"Auto-created baseline contact '{entry['name']}' "
                              f"({entry['email']}) — RB-DEFECT-038 Tier 3 calendar promotion.",
                data_class="relationship",
                retention_class="durable",
                source=f"calendar.{payload['event'].get('id')}",
                reason="thread_promotion_tier3_auto",
            )
        audit_log.log_mutation_proposed(
            item_summary=f"New thread proposed for '{promo['display_name']}' "
                          f"(type={promo['thread_type']}) — review and paste into "
                          f"active_threads.yaml if it should be tracked.",
            source=f"calendar.{payload['event'].get('id')}",
        )


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _load_report(date_str: Optional[str]) -> dict:
    import daily_brief  # local: avoid a circular import at module load
    d = date.fromisoformat(date_str) if date_str else date.today()
    return daily_brief.build_report(d)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", help="ISO date for the brief context (default: today).")
    p.add_argument("--event-id", help="Print prep for a single event id; no writes.")
    p.add_argument("--write", action="store_true",
                   help="Write artifacts for today+tomorrow qualifying meetings.")
    p.add_argument("--for-today", action="store_true",
                   help="Write prep artifacts for today's qualifying events only. "
                        "Equivalent to --write --confirm scoped to today.")
    p.add_argument("--confirm", action="store_true",
                   help="Second-factor flag required with --write.")
    p.add_argument("--list", action="store_true",
                   help="List qualifying events; no rendering, no writes.")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    p.add_argument("--smoke", action="store_true",
                   help="In-memory regression with synthetic event; no I/O.")
    args = p.parse_args()

    if args.smoke:
        return _smoke()

    report = _load_report(args.date)
    today = date.fromisoformat(args.date) if args.date else date.today()
    baseline = core.load_baseline()
    threads = core.load_active_threads()
    events = collect_candidate_events(report)
    if args.for_today:
        today_str = today.isoformat()
        events = [ev for ev in events if (ev.get("start") or "")[:10] == today_str]
        args.write = True
        args.confirm = True

    if args.list:
        for ev in events:
            print(f"  [{ev.get('_bucket')}] {ev.get('start','')[:16]} {ev.get('id','-')}  {ev.get('title','')}")
        if not events:
            print("(no qualifying events today or tomorrow)")
        return 0

    if args.event_id:
        match = next((e for e in (report.get("calendar") or {}).get("today", []) +
                      (report.get("calendar") or {}).get("tomorrow", []) +
                      (report.get("calendar") or {}).get("this_week", [])
                      if e.get("id") == args.event_id), None)
        if not match:
            print(f"ERROR: no event with id={args.event_id!r}", file=sys.stderr)
            return 1
        payload = build_prep_payload(match, baseline=baseline, threads=threads, today=today)
        if args.json:
            print(json.dumps(payload, indent=2, default=str))
        else:
            print(render_prep_brief_md(payload))
        return 0

    if args.write:
        if not args.confirm:
            print("ERROR: --write requires --confirm.", file=sys.stderr)
            return 2
        results = []
        for ev in events:
            payload = build_prep_payload(ev, baseline=baseline, threads=threads, today=today)
            result = write_artifact(payload, dry_run=False)
            results.append({"event_id": ev.get("id"), **{k: v for k, v in result.items() if k != "preview"}})
        if args.json:
            print(json.dumps(results, indent=2, default=str))
        else:
            for r in results:
                print(f"PREP_WRITTEN: {r['path']}")
                print(f"wrote {r['path']} ({r['bytes']} bytes)")
        return 0

    # Default: render all candidates in dry-run mode and print summaries.
    if not events:
        print("(no qualifying events today or tomorrow)")
        return 0
    summaries = []
    for ev in events:
        payload = build_prep_payload(ev, baseline=baseline, threads=threads, today=today)
        summaries.append({
            "event_id": ev.get("id"),
            "title": ev.get("title"),
            "when": ev.get("start", "")[:16],
            "artifact_path": payload["artifact_path"],
            "known_attendees": [a["name"] for a in payload["attendees"]["known"]],
            "open_loops": len(payload["open_loops"]),
            "active_threads": [t["id"] for t in payload["active_threads"]],
            "expected_outcome": payload["expected_outcome"],
        })
    if args.json:
        print(json.dumps(summaries, indent=2, default=str))
    else:
        for s in summaries:
            print(f"  {s['when']} {s['title']}")
            print(f"    -> {s['artifact_path']}")
            if s["known_attendees"]:
                print(f"    attendees: {', '.join(s['known_attendees'])}")
            if s["active_threads"]:
                print(f"    threads:   {', '.join(s['active_threads'])}")
            if s["expected_outcome"]:
                print(f"    outcome:   {s['expected_outcome']}")
        print("(dry-run — pass --write --confirm to materialize artifacts)")
    return 0


# -----------------------------------------------------------------------------
# Smoke
# -----------------------------------------------------------------------------

def _smoke() -> int:
    """In-memory regression. No file I/O, no real baseline read.

    Synthetic baseline/threads passed in so the test stays pure. Per the
    smoke-test-mutations memory: the test never reaches write_artifact in
    non-dry-run mode.
    """
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK" if cond else "FAIL"
        print(f"  {mark}   {msg}")
        if not cond:
            failures.append(msg)

    baseline = [
        {
            "id": "amy-spytko", "name": "Amy Spytko",
            "signal_class": "RC", "rc_tier": "inner",
            "last_touch": "2026-01-24",
            "current_company": "QSRSoft", "current_role": "VP of Sales",
            "email": "amy@example.com",
        },
        {
            "id": "jeff-wayman", "name": "Jeff Wayman",
            "signal_class": "RC", "rc_tier": "inner",
            "last_touch": "2026-05-12",
            "current_company": "QSRSoft", "current_role": "Sales",
            "email": "jeff@example.com",
        },
    ]
    threads = [
        {
            "id": "AT-qsrsoft", "status": "open",
            "title": "QSRSoft partnership exploration",
            "people": ["amy-spytko", "jeff-wayman"],
            "companies": ["QSRSoft"],
            "current_state": "Exploring scope.",
            "target_close": "2026-06-15",
            "boost_for_brief": "high",
        },
    ]
    event = {
        "id": "ev-smoke-1",
        "title": "QSRSoft proposal review",
        "start": "2026-05-22T14:00:00-05:00",
        "end": "2026-05-22T14:45:00-05:00",
        "attendees_matched": [
            {"id": "amy-spytko", "name": "Amy Spytko", "email": "amy@example.com"},
            {"id": "jeff-wayman", "name": "Jeff Wayman", "email": "jeff@example.com"},
            {"name": "Unknown Buyer", "email": "buyer@bigco.example"},
        ],
        "matched_threads": ["AT-qsrsoft"],
        "active_thread_company_hits": ["QSRSoft"],
    }
    today = date(2026, 5, 21)

    # Stub out the loop ledger reader so the smoke is pure.
    real_open_loops_for = _open_loops_for

    def _no_loops(_p, _c):
        return [
            {
                "id": "L-2026-05-08-XXX",
                "party": "Amy Spytko",
                "description": "Send Amy the proposal draft.",
                "target": "2026-05-23",
                "opened": "2026-05-08",
                "matched_on": "amy",
            }
        ]
    globals()["_open_loops_for"] = _no_loops
    try:
        payload = build_prep_payload(event, baseline=baseline, threads=threads, today=today)
    finally:
        globals()["_open_loops_for"] = real_open_loops_for

    ck(payload["contract"] == "meeting_prep_v1", "payload carries contract version")
    ck(payload["event"]["id"] == "ev-smoke-1", "event id passes through")
    ck(payload["attendees"]["total"] == 3, "all attendees counted")
    ck(len(payload["attendees"]["known"]) == 2, "two known attendees")
    ck(len(payload["attendees"]["unknown"]) == 1, "one unknown attendee")
    ck(payload["attendees"]["known"][0]["days_since_touch"] is not None,
       "days_since_touch computed for known attendees")
    ck(payload["active_threads"] and payload["active_threads"][0]["id"] == "AT-qsrsoft",
       "active thread linkage by calendar id present")
    ck(payload["open_loops"] and payload["open_loops"][0]["id"] == "L-2026-05-08-XXX",
       "open loops surfaced from ledger stub")
    ck(payload["expected_outcome"] is not None,
       "expected_outcome label populated when signal exists")
    ck("meeting_briefs/2026-05-22" in payload["artifact_path"],
       f"artifact_path uses date-slug-event scheme (got {payload['artifact_path']})")

    md = render_prep_brief_md(payload)
    ck("# Meeting prep — QSRSoft proposal review" in md, "markdown title present")
    ck("## 1. Stage and context" in md, "section 1 present")
    ck("## 2. Relationship state" in md, "section 2 present")
    ck("## 5. Open loops involving them" in md, "section 5 present (open loops)")
    ck("## 7. Relevant gap-surface" not in md or "no last_touch recorded" not in md.split("## 7", 1)[1].split("##", 1)[0],
       "section 7 omitted for fully-known attendees, OR only flags real gaps")
    ck("Multiple attendees from QSRSoft" in md, "section 9 surfaces network context")
    ck("## 11. Engagement-boundary check" in md,
       "section 11 fires when deliverable-shaped title meets an RC attendee")
    ck("BridgePoint Ops engagement model" in md,
       "section 11 names the BridgePoint Ops boundary explicitly")

    # Confirm the boundary check stays silent on the safe case (no
    # deliverable wording).
    safe_event = dict(event)
    safe_event["title"] = "Catch-up coffee"
    safe_payload = build_prep_payload(safe_event, baseline=baseline, threads=threads, today=today)
    safe_md = render_prep_brief_md(safe_payload)
    ck("## 11. Engagement-boundary check" not in safe_md,
       "section 11 omitted for non-deliverable meeting (no false-positive boundary noise)")
    ck("## Expected outcome" in md, "expected outcome block rendered")
    ck("Amy Spytko" in md and "Jeff Wayman" in md, "both attendees named")

    # write_artifact in dry-run must not touch disk and must include a preview.
    result = write_artifact(payload, dry_run=True)
    ck(result.get("dry_run") is True, "write_artifact dry_run flagged true")
    ck("preview" in result, "dry_run preview included")
    ck(result["path"].startswith("system/meeting_briefs/"),
       "dry_run path under system/meeting_briefs/")

    # event_qualifies branches
    ck(event_qualifies({"attendees_matched": [{"id": "x"}]}) is True,
       "qualifies on known attendee")
    ck(event_qualifies({"matched_threads": ["AT-x"]}) is True,
       "qualifies on active-thread tie")
    ck(event_qualifies({"title": "Lunch with team"}) is False,
       "does not qualify without any signal")
    ck(event_qualifies({"title": "Quarterly review"}) is True,
       "qualifies on deliverable-shaped title")

    print(f"--- meeting_prep smoke complete: {len(failures)} failure(s) ---")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
