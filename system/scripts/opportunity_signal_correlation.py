#!/usr/bin/env python3
"""opportunity_signal_correlation.py — cross-channel opportunity stakeholder
correlation (RB defect: "Failure to Correlate Global Payments Hiring Signals
Across Email, SMS, and Opportunity Intelligence", 2026-06-10).

Existing job_intelligence.py answers "is this a NEW job opportunity?" by
fit-scoring email/LinkedIn against target titles/companies. It has no concept
of an *active* opportunity and cannot recognize a milestone update on one
("I received final feedback from your interviews") because that text scores
zero on title/company/industry fit.

This module answers a different question: "did a known stakeholder on an
ACTIVE opportunity just say something that changes the opportunity's state?"

Mechanism:
1. Load active/building account-dossier artifacts from
   system/artifacts/registry.json (artifact_type == account_dossier). Each
   dossier's key_contacts[] carries an `aliases` map (emails, phones) — the
   entity-resolution layer the defect report asked for.
2. Scan email inbox files AND SMS (messages.json) for inbound messages from
   those aliases, within SCAN_WINDOW_DAYS.
3. Classify milestone language (final feedback, decision/offer, scheduling
   request, internal coordination, reference/background check, onboarding)
   independent of any fit score — message *meaning*, not message existence.
4. Correlate matches for the same opportunity that land within
   CORRELATION_WINDOW_HOURS of each other across different
   channels/contacts — cross-channel corroboration is itself a signal.

Output cache: system/.cache/opportunity_signals.json
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

CACHE_PATH = core.CACHE_DIR / "opportunity_signals.json"
REGISTRY_PATH = core.SYSTEM_DIR / "artifacts" / "registry.json"

#: How far back to scan email/SMS for stakeholder-matched messages.
SCAN_WINDOW_DAYS = 14

#: Matches across channels/contacts within this window are treated as
#: corroborating each other (cross-channel correlation).
CORRELATION_WINDOW_HOURS = 48

# ── Milestone language classification ──────────────────────────────────────
#
# Each entry: (milestone_type, urgency, regex). Order matters — the highest
# urgency match wins for a given message. These describe what a message
# *means* for an opportunity's state, not whether it exists.
MILESTONE_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    (
        "final_feedback",
        "critical",
        re.compile(
            r"\b(final feedback|received (the )?final feedback|"
            r"feedback from your interview|interview feedback)\b", re.I,
        ),
    ),
    (
        "decision_or_offer",
        "critical",
        re.compile(
            r"\b(extend(ing|ed)? (you )?an offer|made a decision|"
            r"moving forward with|next steps?\b|offer letter|"
            r"verbal offer)\b", re.I,
        ),
    ),
    (
        "reference_or_background_check",
        "high",
        re.compile(r"\b(reference check|background check)\b", re.I),
    ),
    (
        "onboarding",
        "high",
        re.compile(r"\b(start date|onboarding|new[- ]hire paperwork)\b", re.I),
    ),
    (
        "scheduling_request",
        "high",
        re.compile(
            r"\b(time for a (quick )?call|do you have time|are you available|"
            r"schedule a (call|time)|call (you )?tomorrow|"
            r"give (me|us) a call)\b", re.I,
        ),
    ),
    (
        "internal_coordination",
        "medium",
        re.compile(
            r"\b(from our team|should be reaching out|reaching out to you|"
            r"please call or text)\b", re.I,
        ),
    ),
]

URGENCY_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}

EMAIL_FILE_GLOB = "email.*.json"


def _classify_milestones(text: str) -> list[dict]:
    out = []
    for milestone_type, urgency, pattern in MILESTONE_PATTERNS:
        m = pattern.search(text)
        if m:
            out.append({
                "milestone_type": milestone_type,
                "urgency": urgency,
                "matched_text": m.group(0),
            })
    return out


def _top_urgency(milestones: list[dict]) -> str:
    if not milestones:
        return "low"
    return max((m["urgency"] for m in milestones), key=lambda u: URGENCY_RANK.get(u, 0))


# ── Dossier / stakeholder loading ───────────────────────────────────────────

def _normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def _normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else digits


def load_active_dossiers() -> list[dict]:
    """Load account-dossier artifacts and build a stakeholder alias index.

    Returns a list of dicts:
        {
          "artifact_id": "account_dossier:global_payments",
          "entity": "Global Payments Inc.",
          "data_path": "...",
          "stakeholders": [
            {"contact_id", "name", "emails": {..}, "phones": {..}},
            ...
          ],
        }
    """
    try:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []

    out = []
    for artifact in registry.get("artifacts") or []:
        if artifact.get("artifact_type") != "account_dossier":
            continue
        if artifact.get("status") not in ("active", "building"):
            continue
        data_path = artifact.get("data_path")
        if not data_path:
            continue
        # data_path is given relative to repo root (e.g. "system/artifacts/data/...")
        full_path = (core.SYSTEM_DIR / ".." / data_path).resolve()
        try:
            dossier = json.loads(full_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue

        stakeholders = []
        for contact in dossier.get("key_contacts") or []:
            aliases = contact.get("aliases") or {}
            emails = {_normalize_email(e) for e in (aliases.get("emails") or []) if e}
            if contact.get("email"):
                emails.add(_normalize_email(contact["email"]))
            phones = {_normalize_phone(p) for p in (aliases.get("phones") or []) if p}
            if not emails and not phones:
                continue
            stakeholders.append({
                "contact_id": contact.get("contact_id") or contact.get("name"),
                "name": contact.get("name"),
                "emails": emails,
                "phones": phones,
            })

        if not stakeholders:
            continue

        out.append({
            "artifact_id": artifact["artifact_id"],
            "entity": artifact.get("entity") or dossier.get("entity"),
            "data_path": data_path,
            "opportunity_state": dossier.get("opportunity_state"),
            "stakeholders": stakeholders,
        })
    return out


# ── Scanners ─────────────────────────────────────────────────────────────────

def _parse_ts(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        ts = None
    if ts is None:
        # Email "last_message_at" is often RFC 2822 (e.g. "Tue, 9 Jun 2026
        # 23:33:44 +0000"), not ISO 8601.
        try:
            from email.utils import parsedate_to_datetime
            ts = parsedate_to_datetime(str(value))
        except (ValueError, TypeError):
            return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _match_stakeholder(stakeholders: list[dict], *, email: str | None = None, phone: str | None = None) -> dict | None:
    if email:
        e = _normalize_email(email)
        for sh in stakeholders:
            if e in sh["emails"]:
                return sh
    if phone:
        p = _normalize_phone(phone)
        for sh in stakeholders:
            if p in sh["phones"]:
                return sh
    return None


def scan_email(dossier: dict, cutoff: datetime) -> list[dict]:
    """Scan email inbox files for messages from this dossier's stakeholders."""
    inbox_dir = core.SYSTEM_DIR / "inbox"
    signals = []
    for fpath in inbox_dir.glob(EMAIL_FILE_GLOB):
        if "_sent" in fpath.stem or "sent." in fpath.stem:
            continue
        try:
            raw = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        threads = raw if isinstance(raw, list) else (raw.get("threads") or raw.get("messages") or [])
        for thread in threads:
            if not isinstance(thread, dict):
                continue
            ts = _parse_ts(thread.get("last_message_at") or thread.get("date"))
            if ts and ts < cutoff:
                continue
            sender = thread.get("last_message_from") or {}
            sender_email = sender.get("email") or ""
            stakeholder = _match_stakeholder(dossier["stakeholders"], email=sender_email)
            if not stakeholder:
                continue
            subject = str(thread.get("subject") or "")
            snippet = str(thread.get("snippet") or "")
            text = f"{subject} {snippet}"
            milestones = _classify_milestones(text)
            signals.append({
                "channel": "email",
                "source_file": fpath.name,
                "thread_id": thread.get("thread_id") or "",
                "at": thread.get("last_message_at") or thread.get("date") or "",
                "stakeholder_contact_id": stakeholder["contact_id"],
                "stakeholder_name": stakeholder["name"],
                "sender_email": sender_email,
                "subject": subject,
                "snippet": snippet[:300],
                "milestones": milestones,
                "urgency": _top_urgency(milestones),
            })
    return signals


def scan_sms(dossier: dict, cutoff: datetime) -> list[dict]:
    """Scan SMS/iMessage events for messages from this dossier's stakeholders."""
    msgs_path = core.SYSTEM_DIR / "inbox" / "messages.json"
    if not msgs_path.exists():
        return []
    try:
        raw = json.loads(msgs_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    events = raw.get("events") if isinstance(raw, dict) else raw
    signals = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        if ev.get("direction") != "inbound":
            continue
        if not ev.get("has_text"):
            continue
        ts = _parse_ts(ev.get("at"))
        if ts and ts < cutoff:
            continue
        stakeholder = _match_stakeholder(dossier["stakeholders"], phone=ev.get("handle"))
        if not stakeholder:
            continue
        text = str(ev.get("full_text") or ev.get("snippet") or "")
        milestones = _classify_milestones(text)
        signals.append({
            "channel": "sms",
            "source_file": "messages.json",
            "message_id": ev.get("id") or "",
            "at": ev.get("at") or "",
            "stakeholder_contact_id": stakeholder["contact_id"],
            "stakeholder_name": stakeholder["name"],
            "sender_handle": ev.get("handle") or "",
            "subject": "",
            "snippet": text[:300],
            "milestones": milestones,
            "urgency": _top_urgency(milestones),
        })
    return signals


# ── Correlation ──────────────────────────────────────────────────────────────

def _correlate(signals: list[dict]) -> list[dict]:
    """Group signals into correlation groups: matches across different
    channels and/or stakeholders within CORRELATION_WINDOW_HOURS reinforce
    each other (cross-channel corroboration)."""
    enriched = []
    window = timedelta(hours=CORRELATION_WINDOW_HOURS)
    parsed = [(_parse_ts(s["at"]), s) for s in signals]
    parsed = [(ts, s) for ts, s in parsed if ts is not None]
    parsed.sort(key=lambda pair: pair[0])

    for i, (ts, sig) in enumerate(parsed):
        related = []
        for j, (other_ts, other_sig) in enumerate(parsed):
            if i == j:
                continue
            if abs((other_ts - ts).total_seconds()) > window.total_seconds():
                continue
            if other_sig["channel"] == sig["channel"] and other_sig["stakeholder_contact_id"] == sig["stakeholder_contact_id"]:
                continue
            related.append({
                "channel": other_sig["channel"],
                "stakeholder_name": other_sig["stakeholder_name"],
                "at": other_sig["at"],
                "snippet": other_sig["snippet"],
            })
        sig = dict(sig)
        sig["cross_channel_correlated"] = bool(related)
        sig["correlated_with"] = related
        enriched.append(sig)
    return enriched


# ── Report build ─────────────────────────────────────────────────────────────

def _recommended_action(opportunity_signals: list[dict]) -> str:
    has_final_feedback = any(
        m["milestone_type"] == "final_feedback"
        for sig in opportunity_signals for m in sig["milestones"]
    )
    has_offer = any(
        m["milestone_type"] == "decision_or_offer"
        for sig in opportunity_signals for m in sig["milestones"]
    )
    has_scheduling = any(
        m["milestone_type"] == "scheduling_request"
        for sig in opportunity_signals for m in sig["milestones"]
    )
    if has_final_feedback or has_offer:
        return (
            "Interview process appears complete. Be available for the recruiter's "
            "call and prepare for an offer / final-decision conversation."
        )
    if has_scheduling:
        return "A stakeholder is requesting a call/meeting — respond promptly."
    return "Review correlated stakeholder activity for opportunity-state changes."


def build_report(*, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=SCAN_WINDOW_DAYS)

    dossiers = load_active_dossiers()
    opportunities = []

    for dossier in dossiers:
        signals = scan_email(dossier, cutoff) + scan_sms(dossier, cutoff)
        if not signals:
            continue
        signals = _correlate(signals)
        signals.sort(key=lambda s: s["at"], reverse=True)

        top_urgency = max((s["urgency"] for s in signals), key=lambda u: URGENCY_RANK.get(u, 0))
        cross_channel = any(s["cross_channel_correlated"] for s in signals)

        opportunities.append({
            "artifact_id": dossier["artifact_id"],
            "entity": dossier["entity"],
            "data_path": dossier["data_path"],
            "opportunity_state": dossier.get("opportunity_state"),
            "top_urgency": top_urgency,
            "cross_channel_correlated": cross_channel,
            "signal_count": len(signals),
            "signals": signals,
            "recommended_action": _recommended_action(signals),
        })

    opportunities.sort(key=lambda o: (URGENCY_RANK.get(o["top_urgency"], 0), o["cross_channel_correlated"]), reverse=True)

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "scan_window_days": SCAN_WINDOW_DAYS,
        "correlation_window_hours": CORRELATION_WINDOW_HOURS,
        "dossiers_scanned": len(dossiers),
        "opportunity_count": len(opportunities),
        "opportunities": opportunities,
    }


def write_cache(report: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")


def load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", action="store_true", help="Emit JSON output")
    p.add_argument("--write-cache", action="store_true", help="Write report to cache")
    args = p.parse_args()

    report = build_report()

    if args.write_cache:
        write_cache(report)
        print(f"Cache written to {CACHE_PATH}")

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"Opportunity signal correlation: {report['opportunity_count']} opportunity(ies) with new signals "
              f"(scanned {report['dossiers_scanned']} dossiers, last {report['scan_window_days']}d)")
        for opp in report["opportunities"]:
            flag = " [CROSS-CHANNEL]" if opp["cross_channel_correlated"] else ""
            print(f"  [{opp['top_urgency'].upper()}]{flag} {opp['entity']} — {opp['signal_count']} signal(s)")
            print(f"    -> {opp['recommended_action']}")
            for sig in opp["signals"]:
                ms = ", ".join(m["milestone_type"] for m in sig["milestones"]) or "no milestone language"
                print(f"    [{sig['channel']}] {sig['at']} {sig['stakeholder_name']}: {ms}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
