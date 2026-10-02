#!/usr/bin/env python3
"""
linkedin_messaging.py — parse LinkedIn messaging exports into RI.

Closes the F33 gap: `linkedin_messaging` was surfaced in the daily brief
prep summary but no parser turned the export into usable signal. This
module normalizes LinkedIn export CSVs (or browser-captured / manually
pasted equivalents) into a canonical inbox JSON, then runs an overlay
against baseline contacts to produce:

  * direction-aware message counts (inbound / outbound)
  * matched-contact summaries (per RC/LKI/etc., last interaction time,
    proposed last_touch update)
  * inbound RI candidates (relationship-warming or opportunity signals)
  * outbound evidence for passive_verification of follow_up loops
  * unmatched recurring participants worth promoting to baseline

Canonical inbox schema (`system/inbox/linkedin.messages.json`):

    {
      "fetched_at": "ISO datetime",
      "source": "linkedin_data_export | manual_paste | browser_capture",
      "messages": [
        {
          "conversation_id": "...",
          "conversation_title": "...",
          "from": {"name": "...", "profile_url": "...", "is_self": false},
          "to": [{"name": "...", "profile_url": "..."}],
          "date": "ISO datetime",
          "subject": "...",
          "content": "...",
          "folder": "INBOX | SENT",
          "direction": "inbound | outbound"
        }, ...
      ]
    }

CLI:
    python3 linkedin_messaging.py --ingest <messages.csv>             # writes inbox JSON
    python3 linkedin_messaging.py --overlay                           # read current inbox + summarize
    python3 linkedin_messaging.py --smoke

Per the smoke-test-mutations memory, the smoke never writes the inbox file.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core


INBOX_PATH = core.SYSTEM_DIR / "inbox" / "linkedin.messages.json"


# -----------------------------------------------------------------------------
# CSV → canonical message
# -----------------------------------------------------------------------------

# LinkedIn export header varies slightly by year. We accept both upper-cased
# space-separated and snake_case so reformatted exports keep working.
_CSV_COLUMN_ALIASES = {
    "conversation_id":      {"CONVERSATION ID", "CONVERSATIONID", "conversation_id"},
    "conversation_title":   {"CONVERSATION TITLE", "conversation_title"},
    "from":                 {"FROM", "from"},
    "sender_profile_url":   {"SENDER PROFILE URL", "sender_profile_url"},
    "to":                   {"TO", "to"},
    "recipient_profile_urls": {"RECIPIENT PROFILE URLS", "recipient_profile_urls"},
    "date":                 {"DATE", "date"},
    "subject":              {"SUBJECT", "subject"},
    "content":              {"CONTENT", "content"},
    "folder":               {"FOLDER", "folder"},
}


def _normalize_header(row: dict) -> dict:
    """Map a raw CSV row dict onto canonical keys regardless of header casing."""
    out: dict[str, Optional[str]] = {}
    for canonical, aliases in _CSV_COLUMN_ALIASES.items():
        for k, v in row.items():
            if k is None:
                continue
            if k.strip() in aliases:
                out[canonical] = (v or "").strip()
                break
        out.setdefault(canonical, None)
    return out


def _parse_date(s: Optional[str]) -> Optional[str]:
    """Return an ISO string. Accepts LinkedIn's "YYYY-MM-DD HH:MM:SS UTC" and ISO."""
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    # Strip trailing tz label
    s_clean = re.sub(r"\s+UTC\s*$", "+00:00", s)
    s_clean = s_clean.replace(" ", "T", 1) if "T" not in s_clean and " " in s_clean else s_clean
    try:
        dt = datetime.fromisoformat(s_clean)
        return dt.isoformat()
    except ValueError:
        # Fall back to date-only.
        try:
            return datetime.fromisoformat(s_clean[:10]).date().isoformat()
        except ValueError:
            return None


def _split_multi(s: Optional[str]) -> list[str]:
    """LinkedIn separates multiple values with ';' (sometimes also ','). Robustly split."""
    if not s:
        return []
    parts: list[str] = []
    for chunk in re.split(r"\s*;\s*", s):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts


def _is_self(profile_url: Optional[str], self_urls: set[str]) -> bool:
    if not profile_url:
        return False
    p = profile_url.lower().rstrip("/")
    return any(p.endswith(u.lower().rstrip("/")) or u.lower().rstrip("/").endswith(p)
               for u in self_urls)


def _self_linkedin_urls(baseline: list[dict]) -> set[str]:
    """Read Todd's own LinkedIn URL from settings + baseline, if present.

    Direction detection needs to know which sender URL belongs to Todd. We
    pull this from the operator's profile entry in baseline (if there is
    one with a matching email/name) and/or from settings.json if it grows
    such a field later.
    """
    out: set[str] = set()
    for e in baseline:
        # Heuristic: the operator's own LinkedIn URL is sometimes recorded
        # as the entry with sources containing "self" or as an entry whose
        # email matches the operator's. We avoid hard-coding identity here;
        # the operator can override via the OPERATOR_LINKEDIN_URL env var.
        if (e.get("email") or "").lower().strip() == "vahlsingt@gmail.com":
            url = (e.get("linkedin_url") or "").strip()
            if url:
                out.add(url)
    import os
    env = os.environ.get("OPERATOR_LINKEDIN_URL")
    if env:
        out.add(env.strip())
    # Also a stable manual fallback from the project README's known URL.
    out.add("https://www.linkedin.com/in/toddvahlsing")
    # LinkedIn's historical export uses the older hyphenated public-profile
    # slug. Without this alias, Todd's own messages were classified inbound,
    # making response-state and reactivation recommendations unusable.
    out.add("https://www.linkedin.com/in/todd-vahlsing")
    return out


def parse_export_csv(path: Path, *, baseline: Optional[list[dict]] = None) -> list[dict]:
    """Read a LinkedIn export messages.csv into canonical message dicts."""
    if baseline is None:
        baseline = core.load_baseline()
    self_urls = _self_linkedin_urls(baseline)
    out: list[dict] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            row = _normalize_header(raw)
            sender_url = (row.get("sender_profile_url") or "").strip() or None
            recipient_urls = _split_multi(row.get("recipient_profile_urls"))
            recipients_names = _split_multi(row.get("to"))
            recipients: list[dict] = []
            for i, name in enumerate(recipients_names):
                url = recipient_urls[i] if i < len(recipient_urls) else None
                recipients.append({"name": name, "profile_url": url})
            sender_is_self = _is_self(sender_url, self_urls)
            folder = (row.get("folder") or "").upper().strip() or None
            direction = "outbound" if (sender_is_self or folder == "SENT") else "inbound"
            iso = _parse_date(row.get("date"))
            content = (row.get("content") or "").strip()
            subject = (row.get("subject") or "").strip() or None
            out.append({
                "conversation_id": (row.get("conversation_id") or "").strip() or None,
                "conversation_title": (row.get("conversation_title") or "").strip() or None,
                "from": {
                    "name": (row.get("from") or "").strip() or None,
                    "profile_url": sender_url,
                    "is_self": sender_is_self,
                },
                "to": recipients,
                "date": iso,
                "subject": subject,
                "content": content,
                "folder": folder,
                "direction": direction,
            })
    return out


def write_inbox_json(messages: list[dict], *, source: str = "linkedin_data_export",
                     dry_run: bool = True) -> dict:
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "messages": messages,
    }
    rel = str(INBOX_PATH.relative_to(core.PROJECT_DIR))
    if dry_run:
        return {"ok": True, "dry_run": True, "path": rel,
                "message_count": len(messages), "preview_payload": payload}
    INBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
    if INBOX_PATH.exists():
        core.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        import shutil
        snap = core.SNAPSHOTS_DIR / (
            INBOX_PATH.stem + ".pre-ingest-" + datetime.now().strftime("%Y%m%d-%H%M%S") + INBOX_PATH.suffix
        )
        shutil.copy2(INBOX_PATH, snap)
    INBOX_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"ok": True, "dry_run": False, "path": rel, "message_count": len(messages)}


# -----------------------------------------------------------------------------
# Overlay (baseline-matched RI computation)
# -----------------------------------------------------------------------------

def load_inbox() -> dict:
    if not INBOX_PATH.exists():
        return {}
    try:
        payload = json.loads(INBOX_PATH.read_text(encoding="utf-8"))
        # Historical LinkedIn exports used Todd's older hyphenated profile
        # slug. Files ingested before that alias was recognized can contain
        # Todd's own messages labeled inbound. Correct the derived direction
        # at read time so every downstream consumer is repaired immediately
        # without rewriting the preserved source artifact.
        for message in payload.get("messages") or []:
            sender = message.get("from") or {}
            url = str(sender.get("profile_url") or "").lower().rstrip("/")
            sender_self = bool(sender.get("is_self")) or url.endswith((
                "/toddvahlsing", "/todd-vahlsing",
            ))
            if sender_self:
                sender["is_self"] = True
                message["direction"] = "outbound"
                if not message.get("folder"):
                    message["folder"] = "SENT"
        return payload
    except Exception:  # noqa: BLE001
        return {}


def _build_li_index(baseline: list[dict]) -> dict:
    """Index baseline by profile URL slug and lowercased name."""
    by_url: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for e in baseline:
        url = (e.get("linkedin_url") or "").lower().rstrip("/")
        if url:
            # Use trailing slug as the join key.
            slug = url.rsplit("/", 1)[-1]
            if slug:
                by_url[slug] = e
        name = (e.get("name") or "").lower().strip()
        if name:
            by_name[name] = e
    return {"by_url_slug": by_url, "by_name": by_name}


def _match_participant(participant: dict, idx: dict) -> tuple[Optional[dict], str]:
    """Return (baseline_entry, match_quality)."""
    url = (participant.get("profile_url") or "").lower().rstrip("/")
    if url:
        slug = url.rsplit("/", 1)[-1]
        if slug and slug in idx["by_url_slug"]:
            return idx["by_url_slug"][slug], "high"
    name = (participant.get("name") or "").lower().strip()
    if name and name in idx["by_name"]:
        return idx["by_name"][name], "medium"
    return None, "none"


@dataclass
class _ContactSummary:
    id: str
    name: str
    signal_class: Optional[str]
    rc_tier: Optional[str]
    current_last_touch: Optional[str]
    inbound: int = 0
    outbound: int = 0
    last_inbound_at: Optional[str] = None
    last_outbound_at: Optional[str] = None
    last_interaction_at: Optional[str] = None
    sample_subjects: list[str] = field(default_factory=list)
    match_quality: str = "medium"


def linkedin_message_overlay(*, baseline: Optional[list[dict]] = None,
                            recent_days: int = 30,
                            today: Optional[date] = None,
                            inbox: Optional[dict] = None) -> dict:
    """Mirror of email_overlay / interaction_overlay for LinkedIn messages."""
    if baseline is None:
        baseline = core.load_baseline()
    if today is None:
        today = date.today()
    if inbox is None:
        inbox = load_inbox()

    messages = inbox.get("messages") or []
    fetched_at = inbox.get("fetched_at")
    source = inbox.get("source") or "unknown"
    out: dict = {
        "fetched_at": fetched_at,
        "stale": fetched_at is None,
        "source": source,
        "totals": {
            "messages_total": len(messages),
            "messages_in_window": 0,
            "inbound_in_window": 0,
            "outbound_in_window": 0,
            "matched_count": 0,
            "unmatched_count": 0,
        },
        "matched_contacts": [],
        "proposed_last_touch_updates": [],
        "unmatched_recurring_participants": [],
        "inbound_ri_candidates": [],
        "outbound_evidence": [],
    }
    if not messages:
        return out

    idx = _build_li_index(baseline)
    cutoff = today - timedelta(days=recent_days)
    summary: dict[str, _ContactSummary] = {}
    unmatched: dict[str, dict] = {}

    def _accept(msg: dict) -> Optional[date]:
        d = msg.get("date")
        if not d:
            return None
        try:
            return date.fromisoformat(d[:10])
        except ValueError:
            return None

    for msg in messages:
        msg_date = _accept(msg)
        if msg_date is None or msg_date < cutoff:
            continue
        out["totals"]["messages_in_window"] += 1
        direction = msg.get("direction") or "inbound"
        out["totals"][f"{direction}_in_window"] += 1

        # Pick the "other party" for each message: for inbound, that's `from`;
        # for outbound, that's the first `to`.
        if direction == "inbound":
            other = msg.get("from") or {}
            other_pool = [other]
        else:
            other_pool = msg.get("to") or []

        for participant in other_pool:
            if not participant:
                continue
            entry, quality = _match_participant(participant, idx)
            if entry:
                slot = summary.get(entry["id"])
                if slot is None:
                    slot = _ContactSummary(
                        id=entry["id"],
                        name=entry.get("name") or "(no name)",
                        signal_class=entry.get("signal_class"),
                        rc_tier=entry.get("rc_tier"),
                        current_last_touch=entry.get("last_touch"),
                        match_quality=quality,
                    )
                    summary[entry["id"]] = slot
                else:
                    # Promote to high if we now find a URL match.
                    if quality == "high":
                        slot.match_quality = "high"
                if direction == "inbound":
                    slot.inbound += 1
                    if not slot.last_inbound_at or (msg.get("date") or "") > slot.last_inbound_at:
                        slot.last_inbound_at = msg.get("date")
                else:
                    slot.outbound += 1
                    if not slot.last_outbound_at or (msg.get("date") or "") > slot.last_outbound_at:
                        slot.last_outbound_at = msg.get("date")
                if not slot.last_interaction_at or (msg.get("date") or "") > slot.last_interaction_at:
                    slot.last_interaction_at = msg.get("date")
                if msg.get("subject") and msg["subject"] not in slot.sample_subjects:
                    if len(slot.sample_subjects) < 3:
                        slot.sample_subjects.append(msg["subject"])
            else:
                key = (participant.get("profile_url") or participant.get("name") or "").lower()
                if not key:
                    continue
                u = unmatched.setdefault(key, {
                    "name": participant.get("name") or "(unknown)",
                    "profile_url": participant.get("profile_url"),
                    "count": 0,
                    "last_seen_at": None,
                })
                u["count"] += 1
                if not u["last_seen_at"] or (msg.get("date") or "") > u["last_seen_at"]:
                    u["last_seen_at"] = msg.get("date")

    # Materialize summary rows
    rows = []
    for slot in summary.values():
        row = {
            "id": slot.id,
            "name": slot.name,
            "signal_class": slot.signal_class,
            "rc_tier": slot.rc_tier,
            "inbound": slot.inbound,
            "outbound": slot.outbound,
            "last_inbound_at": slot.last_inbound_at,
            "last_outbound_at": slot.last_outbound_at,
            "last_interaction_at": slot.last_interaction_at,
            "current_last_touch": slot.current_last_touch,
            "match_quality": slot.match_quality,
            "sample_subjects": slot.sample_subjects,
        }
        rows.append(row)
    rows.sort(
        key=lambda r: r.get("last_interaction_at") or "",
        reverse=True,
    )
    out["matched_contacts"] = rows
    out["totals"]["matched_count"] = len(rows)

    # Proposed last_touch updates: when the most recent LinkedIn interaction
    # is newer than the recorded last_touch.
    for r in rows:
        last_interact = r.get("last_interaction_at")
        if not last_interact:
            continue
        try:
            last_d = date.fromisoformat(last_interact[:10])
        except ValueError:
            continue
        cur = r.get("current_last_touch")
        try:
            cur_d = date.fromisoformat(cur[:10]) if cur else None
        except ValueError:
            cur_d = None
        if cur_d is None or last_d > cur_d:
            out["proposed_last_touch_updates"].append({
                "contact_id": r["id"],
                "name": r["name"],
                "current": cur,
                "proposed": last_d.isoformat(),
                "gap_days": (last_d - cur_d).days if cur_d else None,
                "source": "linkedin_messaging",
                "confidence": "high" if r["match_quality"] == "high" else "medium",
            })

    # Inbound RI candidates: any RC/LKI inbound in the last `recent_days` is
    # worth surfacing for follow-up consideration. The brief decides whether
    # to elevate.
    for r in rows:
        if (r.get("inbound") or 0) <= 0:
            continue
        if r.get("signal_class") not in {"RC", "LKI", "LMI"}:
            continue
        out["inbound_ri_candidates"].append({
            "contact_id": r["id"],
            "name": r["name"],
            "signal_class": r["signal_class"],
            "rc_tier": r["rc_tier"],
            "last_inbound_at": r.get("last_inbound_at"),
            "inbound_count": r["inbound"],
            "sample_subjects": r.get("sample_subjects") or [],
            "match_quality": r["match_quality"],
            "ref": f"linkedin_messaging.matched_contacts.{r['id']}",
        })

    # Outbound evidence for passive_verification consumers
    for r in rows:
        if (r.get("outbound") or 0) <= 0:
            continue
        out["outbound_evidence"].append({
            "contact_id": r["id"],
            "name": r["name"],
            "outbound_count": r["outbound"],
            "last_outbound_at": r.get("last_outbound_at"),
            "ref": f"linkedin_messaging.matched_contacts.{r['id']}",
        })

    # Unmatched recurring participants worth promoting (>= 3 messages)
    unmatched_rows = [
        {**v, "key": k} for k, v in unmatched.items() if v["count"] >= 3
    ]
    unmatched_rows.sort(key=lambda r: r["count"], reverse=True)
    out["unmatched_recurring_participants"] = unmatched_rows[:12]
    out["totals"]["unmatched_count"] = len(unmatched_rows)
    return out


# -----------------------------------------------------------------------------
# RI event proposals
# -----------------------------------------------------------------------------

def ri_event_proposals(overlay: dict, *, today: Optional[date] = None) -> list[dict]:
    """Emit ri_event-shaped dicts (persistence_status=proposed). Operator confirms persistence."""
    today = today or date.today()
    out: list[dict] = []
    for cand in overlay.get("inbound_ri_candidates") or []:
        ts = cand.get("last_inbound_at") or datetime.now(timezone.utc).isoformat()
        out.append({
            "source": "linkedin_messaging",
            "signal_type": "linkedin_message_received",
            "event_at": ts,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "contact_id": cand.get("contact_id"),
            "contact_name": cand.get("name"),
            "summary": (
                f"{cand.get('inbound_count')} inbound LinkedIn message(s) "
                f"from {cand.get('name')}"
                + (f" ({cand.get('rc_tier')})" if cand.get("rc_tier") else "")
                + "."
            ),
            "confidence": "high" if cand.get("match_quality") == "high" else "medium",
            "persistence": {"status": "proposed_write_pending_confirmation"},
            "sample_subjects": cand.get("sample_subjects") or [],
            "source_refs": [cand.get("ref")],
        })
    return out


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ingest", help="Path to a LinkedIn export messages.csv to normalize.")
    p.add_argument("--confirm", action="store_true",
                   help="Write system/inbox/linkedin.messages.json. "
                        "Without this flag --ingest runs in preview/dry-run mode only.")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview ingest without writing (this is the default; use --confirm to write).")
    p.add_argument("--source", default="linkedin_data_export",
                   help="Tag for the inbox JSON source field.")
    p.add_argument("--overlay", action="store_true",
                   help="Print the overlay summary from the current inbox JSON.")
    p.add_argument("--ri-proposals", action="store_true",
                   help="Emit RI event proposals from the current inbox.")
    p.add_argument("--recent-days", type=int, default=30,
                   help="Lookback window for the overlay.")
    p.add_argument("--json", action="store_true", help="Emit JSON output.")
    p.add_argument("--smoke", action="store_true",
                   help="In-memory regression — no I/O.")
    args = p.parse_args()

    if args.smoke:
        return _smoke()

    if args.ingest:
        path = Path(args.ingest)
        if not path.exists():
            print(f"ERROR: no file at {path}", file=sys.stderr)
            return 1
        try:
            messages = parse_export_csv(path)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: failed to parse LinkedIn messages CSV at {path}: {exc}", file=sys.stderr)
            return 1
        # Default is preview/dry-run. --confirm is required to write the inbox JSON.
        # --dry-run is accepted as an explicit alias for the default safe behavior.
        dry_run = not args.confirm
        result = write_inbox_json(messages, source=args.source, dry_run=dry_run)
        overlay = linkedin_message_overlay(
            recent_days=args.recent_days,
            inbox={
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": args.source,
                "messages": messages,
            },
        )
        totals = overlay.get("totals") or {}
        summary = {
            "ok": True,
            "dry_run": result["dry_run"],
            "path": result["path"],
            "total_rows": len(messages),
            "matched_contacts": totals.get("matched_count", 0),
            "unmatched_recurring_participants": totals.get("unmatched_count", 0),
            "last_touch_proposals": len(overlay.get("proposed_last_touch_updates") or []),
            "outbound_evidence_rows": len(overlay.get("outbound_evidence") or []),
        }
        if args.json:
            summary["sample"] = messages[:2]
            print(json.dumps(summary, indent=2, default=str))
        else:
            mode = "preview" if result["dry_run"] else "wrote"
            print(f"{mode} {summary['path']} — {summary['total_rows']} row(s) normalized.")
            print(
                "summary: "
                f"matched_contacts={summary['matched_contacts']}; "
                f"unmatched_recurring_participants={summary['unmatched_recurring_participants']}; "
                f"last_touch_proposals={summary['last_touch_proposals']}; "
                f"outbound_evidence_rows={summary['outbound_evidence_rows']}"
            )
            if result["dry_run"]:
                print("(preview only — add --confirm to write inbox JSON)")
        return 0

    if args.overlay or args.ri_proposals:
        overlay = linkedin_message_overlay(recent_days=args.recent_days)
        if args.ri_proposals:
            proposals = ri_event_proposals(overlay)
            if args.json:
                print(json.dumps(proposals, indent=2, default=str))
            else:
                print(f"{len(proposals)} RI proposal(s) from linkedin_messaging:")
                for p_ in proposals:
                    print(f"  - {p_['contact_name']} (conf={p_['confidence']}): {p_['summary']}")
            return 0
        if args.json:
            print(json.dumps(overlay, indent=2, default=str))
        else:
            t = overlay["totals"]
            print(f"linkedin_messaging overlay (window {args.recent_days}d):")
            print(f"  total messages: {t['messages_total']}; in window: {t['messages_in_window']} "
                  f"(in={t['inbound_in_window']}, out={t['outbound_in_window']})")
            print(f"  matched contacts: {t['matched_count']}; unmatched recurring: {t['unmatched_count']}")
            for r in overlay["matched_contacts"][:6]:
                print(f"   - {r['name']:30s} {r['signal_class'] or '—':6s} "
                      f"in={r['inbound']} out={r['outbound']} last={r['last_interaction_at']}")
            for c in overlay["inbound_ri_candidates"][:6]:
                print(f"  RI: {c['name']} ({c['signal_class']}) — {c['inbound_count']} inbound, "
                      f"conf={c['match_quality']}")
        return 0

    p.print_help()
    return 0


# -----------------------------------------------------------------------------
# Smoke
# -----------------------------------------------------------------------------

def _smoke() -> int:
    """In-memory regression. No file I/O, no inbox writes."""
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK" if cond else "FAIL"
        print(f"  {mark}   {msg}")
        if not cond:
            failures.append(msg)

    today = date(2026, 5, 21)
    baseline = [
        {
            "id": "ish-singh",
            "name": "Ish Singh",
            "signal_class": "LKI",
            "linkedin_url": "https://www.linkedin.com/in/ish-singh",
            "last_touch": "2026-04-01",
            "email": "ish@example.com",
        },
        {
            "id": "amy-spytko",
            "name": "Amy Spytko",
            "signal_class": "RC",
            "rc_tier": "inner",
            "linkedin_url": "https://www.linkedin.com/in/amy-spytko279",
            "last_touch": "2026-01-24",
            "email": "amy@example.com",
        },
        {
            "id": "todd-vahlsing",
            "name": "Todd Vahlsing",
            "signal_class": "OPERATOR",
            "linkedin_url": "https://www.linkedin.com/in/toddvahlsing",
            "email": "vahlsingt@gmail.com",
        },
    ]

    # Header-aliasing: upper-case vs snake_case
    raw_rows = [
        {  # Inbound from a known LKI
            "CONVERSATION ID": "c-1", "CONVERSATION TITLE": "Maho intro",
            "FROM": "Ish Singh",
            "SENDER PROFILE URL": "https://www.linkedin.com/in/ish-singh",
            "TO": "Todd Vahlsing",
            "RECIPIENT PROFILE URLS": "https://www.linkedin.com/in/toddvahlsing",
            "DATE": "2026-05-20 18:30:00 UTC",
            "SUBJECT": "Re: Maho deep-dive",
            "CONTENT": "Hey Todd — love the framing. Open to a 30 next week?",
            "FOLDER": "INBOX",
        },
        {  # Outbound to a known RC
            "CONVERSATION ID": "c-2", "CONVERSATION TITLE": "Proposal",
            "FROM": "Todd Vahlsing",
            "SENDER PROFILE URL": "https://www.linkedin.com/in/toddvahlsing",
            "TO": "Amy Spytko",
            "RECIPIENT PROFILE URLS": "https://www.linkedin.com/in/amy-spytko279",
            "DATE": "2026-05-19 10:00:00 UTC",
            "SUBJECT": "QSRSoft draft",
            "CONTENT": "Draft attached.",
            "FOLDER": "SENT",
        },
        {  # Inbound from a stranger (unmatched)
            "CONVERSATION ID": "c-3", "CONVERSATION TITLE": "Cold reach",
            "FROM": "Outside Person",
            "SENDER PROFILE URL": "https://www.linkedin.com/in/outside-person",
            "TO": "Todd Vahlsing",
            "RECIPIENT PROFILE URLS": "https://www.linkedin.com/in/toddvahlsing",
            "DATE": "2026-05-18 09:00:00 UTC",
            "SUBJECT": None,
            "CONTENT": "Interested in restaurant tech.",
            "FOLDER": "INBOX",
        },
        {  # Inbound from the same stranger (3 total = recurring)
            "CONVERSATION ID": "c-3", "CONVERSATION TITLE": "Cold reach",
            "FROM": "Outside Person",
            "SENDER PROFILE URL": "https://www.linkedin.com/in/outside-person",
            "TO": "Todd Vahlsing",
            "RECIPIENT PROFILE URLS": "https://www.linkedin.com/in/toddvahlsing",
            "DATE": "2026-05-15 09:00:00 UTC",
            "SUBJECT": None,
            "CONTENT": "Following up.",
            "FOLDER": "INBOX",
        },
        {  # Third inbound — qualifies as recurring (>=3)
            "CONVERSATION ID": "c-3", "CONVERSATION TITLE": "Cold reach",
            "FROM": "Outside Person",
            "SENDER PROFILE URL": "https://www.linkedin.com/in/outside-person",
            "TO": "Todd Vahlsing",
            "RECIPIENT PROFILE URLS": "https://www.linkedin.com/in/toddvahlsing",
            "DATE": "2026-05-10 09:00:00 UTC",
            "SUBJECT": None,
            "CONTENT": "Bumping this.",
            "FOLDER": "INBOX",
        },
        {  # Out-of-window — should be filtered
            "CONVERSATION ID": "c-9", "CONVERSATION TITLE": "Old",
            "FROM": "Ish Singh",
            "SENDER PROFILE URL": "https://www.linkedin.com/in/ish-singh",
            "TO": "Todd Vahlsing",
            "RECIPIENT PROFILE URLS": "https://www.linkedin.com/in/toddvahlsing",
            "DATE": "2025-01-01 09:00:00 UTC",
            "SUBJECT": "Stale",
            "CONTENT": "old.",
            "FOLDER": "INBOX",
        },
    ]
    normalized = [_normalize_header(r) for r in raw_rows]
    ck(all(n.get("conversation_id") for n in normalized[:3]),
       "header normalization preserves conversation_id across rows")
    ck(_parse_date("2026-05-20 18:30:00 UTC") == "2026-05-20T18:30:00+00:00",
       f"_parse_date handles LinkedIn UTC string (got {_parse_date('2026-05-20 18:30:00 UTC')!r})")
    ck(_parse_date(None) is None and _parse_date("") is None, "empty date returns None")
    ck(_split_multi("a; b ;c") == ["a", "b", "c"], "multi-value split handles spaces and ;")

    # Build a canonical messages list directly to exercise the overlay.
    self_urls = _self_linkedin_urls(baseline)
    canonical = []
    for n in normalized:
        sender_url = n.get("sender_profile_url")
        is_self = _is_self(sender_url, self_urls)
        recipients = [
            {"name": r, "profile_url": url}
            for r, url in zip(_split_multi(n.get("to")), _split_multi(n.get("recipient_profile_urls")))
        ]
        folder = (n.get("folder") or "").upper().strip() or None
        direction = "outbound" if (is_self or folder == "SENT") else "inbound"
        canonical.append({
            "conversation_id": n.get("conversation_id"),
            "conversation_title": n.get("conversation_title"),
            "from": {"name": n.get("from"), "profile_url": sender_url, "is_self": is_self},
            "to": recipients,
            "date": _parse_date(n.get("date")),
            "subject": n.get("subject"),
            "content": n.get("content"),
            "folder": folder,
            "direction": direction,
        })

    ck(canonical[0]["direction"] == "inbound", "Ish→Todd parsed as inbound")
    ck(canonical[1]["direction"] == "outbound", "Todd→Amy parsed as outbound")
    ck(canonical[1]["from"]["is_self"] is True, "Todd's sender is_self=true")

    inbox = {
        "fetched_at": "2026-05-21T18:00:00+00:00",
        "source": "linkedin_data_export",
        "messages": canonical,
    }
    overlay = linkedin_message_overlay(baseline=baseline, today=today, inbox=inbox)

    t = overlay["totals"]
    ck(t["messages_total"] == 6, f"6 messages total (got {t['messages_total']})")
    ck(t["messages_in_window"] == 5,
       f"5 in 30d window (stale 2025 dropped) (got {t['messages_in_window']})")
    ck(t["inbound_in_window"] == 4, "4 inbound in window")
    ck(t["outbound_in_window"] == 1, "1 outbound in window")
    ck(t["matched_count"] == 2,
       f"two matched contacts (Ish + Amy), got {t['matched_count']}")

    ids = {r["id"] for r in overlay["matched_contacts"]}
    ck("ish-singh" in ids and "amy-spytko" in ids, "both expected contacts matched")
    ish_row = next(r for r in overlay["matched_contacts"] if r["id"] == "ish-singh")
    ck(ish_row["inbound"] == 1 and ish_row["outbound"] == 0,
       "Ish has inbound=1 outbound=0 in window")
    ck(ish_row["match_quality"] == "high", "Ish matched via URL → high quality")

    amy_row = next(r for r in overlay["matched_contacts"] if r["id"] == "amy-spytko")
    ck(amy_row["outbound"] == 1, "Amy has outbound=1 in window")

    # Proposed last_touch updates
    updates = {u["contact_id"]: u for u in overlay["proposed_last_touch_updates"]}
    ck("ish-singh" in updates and updates["ish-singh"]["proposed"] == "2026-05-20",
       "Ish last_touch update proposed to 2026-05-20")
    ck("amy-spytko" in updates,
       "Amy last_touch update proposed (current 2026-01-24 < observed 2026-05-19)")

    # Inbound RI candidates: Ish (LKI) qualifies; Amy (RC, but only outbound) does not
    cand_ids = {c["contact_id"] for c in overlay["inbound_ri_candidates"]}
    ck("ish-singh" in cand_ids, "Ish surfaces as inbound RI candidate")
    ck("amy-spytko" not in cand_ids,
       "Amy does NOT surface in inbound RI (no inbound in window)")

    # Outbound evidence: Amy's outbound is available for passive_verification
    out_ids = {e["contact_id"] for e in overlay["outbound_evidence"]}
    ck("amy-spytko" in out_ids, "Amy outbound surfaces as evidence row")

    # Unmatched recurring: stranger with 3 inbound
    rec = overlay["unmatched_recurring_participants"]
    ck(any(p["name"] == "Outside Person" and p["count"] == 3 for p in rec),
       "stranger with 3 messages surfaces as unmatched_recurring")

    # RI proposals
    proposals = ri_event_proposals(overlay)
    ck(len(proposals) == 1 and proposals[0]["contact_id"] == "ish-singh",
       "RI proposals only emit for matched RC/LKI/LMI inbound")
    ck(proposals[0]["persistence"]["status"] == "proposed_write_pending_confirmation",
       "RI proposal carries persistence_status=proposed (not yet persisted)")
    ck(proposals[0]["confidence"] == "high",
       "RI proposal confidence carries URL-match quality up")

    # Empty inbox path returns a non-crashing overlay
    empty = linkedin_message_overlay(baseline=baseline, today=today, inbox={})
    ck(empty["totals"]["messages_total"] == 0,
       "empty inbox returns zero-total overlay (no crash)")
    ck(empty["stale"] is True, "empty inbox flagged stale")

    # write_inbox_json dry-run never touches disk (default contract: dry_run=True)
    result = write_inbox_json(canonical, dry_run=True)
    ck(result["dry_run"] is True, "write dry_run flag set (default safe behavior)")
    ck(result["path"].endswith("inbox/linkedin.messages.json"),
       f"inbox path canonical (got {result['path']!r})")
    ck(result["message_count"] == 6, "dry-run reports correct message count")
    ck("preview_payload" in result, "dry-run includes preview payload")
    # Confirm that not passing dry_run=False also stays dry (defensive check)
    result_explicit_dry = write_inbox_json(canonical, dry_run=True)
    ck(result_explicit_dry["dry_run"] is True,
       "explicit dry_run=True still previews (not written)")

    print(f"--- linkedin_messaging smoke complete: {len(failures)} failure(s) ---")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
