#!/usr/bin/env python3
"""
fetch_via_session.py — write inbox JSON from a Cowork session.

Usage pattern: a session in this Cowork project calls the Google Calendar
and Gmail MCP tools, then pipes the raw JSON outputs into this script.
This script normalizes the shapes into `system/inbox/calendar.json` and
`system/inbox/email.json`.

Examples:

    # From stdin (paste raw Cowork MCP output for calendar list_events):
    cat raw_calendar.json | python3 fetch_via_session.py calendar

    # Or read a file:
    python3 fetch_via_session.py calendar --in raw_calendar.json
    python3 fetch_via_session.py email --in raw_gmail_threads.json

The expected raw shapes are exactly what the Cowork Google Calendar and
Gmail MCP servers return — see the function docstrings.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core


def normalize_calendar(raw: dict) -> dict:
    """Accepts the Cowork `list_events` response shape:

        {
          "accessRole": "...",
          "events": [
            {
              "id": "...",
              "summary": "...",
              "start": {"dateTime": "...", "timeZone": "..."},
              "end": {"dateTime": "...", "timeZone": "..."},
              "location": "...",
              "description": "...",
              "attendees": [{"email": "...", "responseStatus": "...", "self": bool, "organizer": bool}],
              "htmlLink": "...",
              "organizer": {"email": "..."}
            },
            ...
          ]
        }
    """
    events_out = []
    for ev in raw.get("events") or []:
        start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date")
        end = (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date")
        events_out.append({
            "id": ev.get("id"),
            "title": ev.get("summary"),
            "start": start,
            "end": end,
            "location": ev.get("location"),
            "description": ev.get("description"),
            "attendees": [
                {
                    "email": a.get("email"),
                    "response": a.get("responseStatus"),
                    "self": bool(a.get("self")),
                }
                for a in (ev.get("attendees") or [])
            ],
            "organizer_email": (ev.get("organizer") or {}).get("email"),
            "html_link": ev.get("htmlLink"),
            "source": "google_calendar",
        })
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "events": events_out,
    }


def normalize_social(raw: dict) -> dict:
    """Accept a flexible social-feed input. The expected shape is what a
    Claude-in-Chrome session can produce after reading the LinkedIn feed
    DOM, or what a manual paste flow would emit. Required per post:
    `author.name` (string) and `text`. Everything else is optional.
    """
    posts_out = []
    for raw_post in raw.get("posts") or []:
        author = raw_post.get("author") or {}
        # If author was passed as a string, accept that as the name.
        if isinstance(author, str):
            author = {"name": author}
        engagement = raw_post.get("engagement") or {}
        post_id = (
            raw_post.get("id")
            or raw_post.get("post_url")
            or f"{author.get('name','')}::{raw_post.get('posted_at','')}"
        )
        posts_out.append({
            "id": post_id,
            "author": {
                "name": author.get("name"),
                "linkedin_url": author.get("linkedin_url") or author.get("profile_url"),
                "headline": author.get("headline"),
            },
            "posted_at": raw_post.get("posted_at"),
            "text": raw_post.get("text") or raw_post.get("body") or "",
            "post_url": raw_post.get("post_url"),
            "platform": raw_post.get("platform") or "linkedin",
            "engagement": {
                "likes": engagement.get("likes"),
                "comments": engagement.get("comments"),
                "shares": engagement.get("shares"),
            },
            "captured_at": raw_post.get("captured_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "captured_via": raw_post.get("captured_via") or raw.get("captured_via") or "unknown",
        })
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": raw.get("source") or raw.get("captured_via") or "unknown",
        "posts": posts_out,
    }


def normalize_email(raw: dict) -> dict:
    """Accepts the Cowork `search_threads` response shape:

        {
          "threads": [
            {
              "id": "...",
              "messages": [
                {
                  "id": "...",
                  "date": "ISO",
                  "sender": "Name <email@x>" or "email@x",
                  "subject": "...",
                  "labelIds": [...],
                  "snippet": "...",
                  "toRecipients": [...]
                },
                ...
              ]
            },
            ...
          ]
        }
    """
    def _parse_addr(s: str) -> dict:
        if not s:
            return {"email": None, "name": None}
        if "<" in s and ">" in s:
            name = s[: s.find("<")].strip().strip('"')
            email = s[s.find("<") + 1: s.find(">")]
            return {"email": email, "name": name or None}
        return {"email": s.strip(), "name": None}

    def _parse_recipients(raw_to) -> list[dict]:
        """Normalize a toRecipients value into a list of {name, email} dicts.

        The Cowork Gmail MCP returns toRecipients as either a list of
        strings ("Name <email>" or bare email) or a list of dicts. Accept
        both. Empty/missing values yield an empty list.
        """
        out: list[dict] = []
        if not raw_to:
            return out
        if isinstance(raw_to, str):
            # Single comma-separated string ("a@x, b@y").
            raw_to = [s.strip() for s in raw_to.split(",") if s.strip()]
        for r in raw_to:
            if isinstance(r, dict):
                em = r.get("email") or r.get("address")
                name = r.get("name")
                if em:
                    out.append({"email": em, "name": name})
            else:
                parsed = _parse_addr(str(r))
                if parsed["email"]:
                    out.append(parsed)
        return out

    def _sort_key(m: dict):
        # RB-DEFECT-2026-07-14: comparing raw RFC 2822 date header strings
        # ("Mon, 13 Jul 2026 21:30:18 +0000" vs "Mon, 13 Jul 2026 17:39:12
        # -0400") lexicographically only works when every message in the
        # thread uses the same UTC offset. A reply sent from a client on
        # -0400 sorted BEFORE an earlier +0000 message in the same thread
        # purely because "17" < "21" as characters, even though 17:39 -0400
        # is 21:39 UTC -- chronologically the later message. Confirmed live:
        # this picked Todd's own 21:30 UTC reply as "last_message" over
        # Erika Till's genuinely later 17:39 -0400 (21:39 UTC) reply,
        # silently hiding her response from What Changed Today / the
        # Communication Queue. Parse to an actual aware datetime first.
        raw_date = m.get("date") or ""
        try:
            dt = parsedate_to_datetime(raw_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc)

    threads_out = []
    for t in raw.get("threads") or []:
        msgs = t.get("messages") or []
        if not msgs:
            continue
        # Treat the chronologically last message as "last_message_*"
        last = max(msgs, key=_sort_key)
        labels: set[str] = set()
        for m in msgs:
            labels.update(m.get("labelIds") or [])
        threads_out.append({
            "thread_id": t.get("id"),
            "subject": last.get("subject"),
            "last_message_at": last.get("date"),
            "last_message_from": _parse_addr(last.get("sender") or ""),
            # Preserve recipients on the last message so the overlay can
            # classify sent-followups (Step 2a of the canonical CoS sprint).
            # Older normalized files won't have this field; rb_core handles
            # the missing case.
            "last_message_to": _parse_recipients(last.get("toRecipients")),
            "labels": sorted(labels),
            "unread": "UNREAD" in labels,
            "snippet": last.get("snippet"),
            "message_count": len(msgs),
            "source": "gmail",
        })
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "threads": threads_out,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("kind", choices=["calendar", "email", "social"])
    p.add_argument("--in", dest="infile",
                   help="Input file (default: stdin).")
    p.add_argument("--account", dest="account",
                   help="Account id from inbox/accounts.yaml. If omitted, "
                        "writes to the legacy single-file path. "
                        "Not used for 'social' (single shared feed file).")
    p.add_argument("--mailbox", dest="mailbox", default="inbox",
                   choices=["inbox", "sent"],
                   help="For kind=email: which mailbox this raw capture came "
                        "from. 'inbox' writes email.<id>.json; 'sent' writes "
                        "email_sent.<id>.json so the two mailboxes stay "
                        "independent. Ignored for other kinds.")
    p.add_argument("--merge", action="store_true",
                   help="Merge with existing social.feed.json instead of overwriting. "
                        "Dedup by post id. Only applies to kind=social.")
    args = p.parse_args()

    text = Path(args.infile).read_text() if args.infile else sys.stdin.read()
    raw = json.loads(text)

    # If --account was passed (only meaningful for calendar / email), sanity-check.
    if args.account and args.kind in ("calendar", "email"):
        accounts = {a["id"]: a for a in core.load_inbox_accounts()}
        if args.account not in accounts:
            sys.stderr.write(
                f"ERROR: account id '{args.account}' not found in "
                f"{core.INBOX_ACCOUNTS_PATH.relative_to(core.PROJECT_DIR)}. "
                f"Known: {sorted(accounts.keys())}\n"
            )
            return 2
        feeds = accounts[args.account].get("feeds") or []
        if args.kind not in feeds:
            sys.stderr.write(
                f"WARNING: account '{args.account}' does not list '{args.kind}' "
                f"in its `feeds:` list. Writing anyway, but consider updating "
                f"the manifest.\n"
            )

    if args.kind == "calendar":
        out = normalize_calendar(raw)
        target = (core.calendar_path_for(args.account)
                  if args.account else core.CALENDAR_PATH)
        if args.account:
            out["account_id"] = args.account
    elif args.kind == "email":
        out = normalize_email(raw)
        if args.account:
            if args.mailbox == "sent":
                target = core.email_sent_path_for(args.account)
            else:
                target = core.email_path_for(args.account)
            out["account_id"] = args.account
            out["mailbox"] = args.mailbox
        else:
            # Legacy single-file path — no separate sent file in legacy
            # mode. If the user wants per-mailbox isolation they must use
            # --account.
            target = core.EMAIL_PATH
    else:  # social
        out = normalize_social(raw)
        target = core.SOCIAL_FEED_PATH
        if args.merge and target.exists():
            try:
                existing = json.loads(target.read_text())
                by_id = {p["id"]: p for p in (existing.get("posts") or [])}
                for p in out["posts"]:
                    by_id[p["id"]] = p
                out["posts"] = list(by_id.values())
            except Exception:
                pass

    core.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=2, default=str))
    rel = target.relative_to(core.PROJECT_DIR)
    if args.kind == "calendar":
        count = len(out.get("events") or [])
        label = f"account={args.account}" if args.account else "legacy"
    elif args.kind == "email":
        count = len(out.get("threads") or [])
        if args.account:
            label = f"account={args.account},mailbox={args.mailbox}"
        else:
            label = "legacy"
    else:
        count = len(out.get("posts") or [])
        label = f"source={out.get('source')}"
    print(f"Wrote {count} {args.kind} records to {rel} "
          f"({label}, fetched_at={out['fetched_at']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
