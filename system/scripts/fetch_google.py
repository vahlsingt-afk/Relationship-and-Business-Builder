#!/usr/bin/env python3
"""
fetch_google.py — production fetcher for Google Calendar + Gmail.

For deployments where there's no AI session in the loop: a scheduled task
(or cron) runs this directly against Google APIs and writes
`system/inbox/calendar.<account>.json` and `system/inbox/email.<account>.json`
when `--account` is passed, or the legacy single-account files otherwise.

This is the path the productized multi-tenant SaaS will use. The Cowork
session path (fetch_via_session.py) is the founder/dev path.

Status: SCAFFOLDED, NOT WIRED. To make this live you need:

    1. A GCP project with Calendar + Gmail APIs enabled.
    2. An OAuth 2.0 Client ID (Desktop or Web Application).
    3. Run the consent flow once locally to mint a refresh token.
    4. Store the token securely (default: `~/.config/rb/google_token.<account>.json`
       when `--account` is passed, else `~/.config/rb/google_token.json`).
    5. Install deps:
           pip install google-auth google-auth-oauthlib google-api-python-client \\
               --break-system-packages

Usage (once it's wired):
    python3 fetch_google.py both --account all --mailbox both --days 14
    python3 fetch_google.py both --account personal --mailbox both --days 7
    python3 fetch_google.py calendar
    python3 fetch_google.py email --query "in:inbox newer_than:14d"
    python3 fetch_google.py email --mailbox sent
    python3 fetch_google.py email --mailbox both

Until the deps and credentials are in place, this script prints a
descriptive error and exits non-zero. The shape it writes is identical
to what `fetch_via_session.py` writes, so downstream is unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core

DEFAULT_TOKEN_PATH = Path(os.environ.get(
    "RB_GOOGLE_TOKEN", str(Path.home() / ".config/rb/google_token.json")
))
DEFAULT_CLIENT_PATH = Path(os.environ.get(
    "RB_GOOGLE_CLIENT", str(Path.home() / ".config/rb/google_client.json")
))

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def _token_path(account_id: str | None) -> Path:
    if os.environ.get("RB_GOOGLE_TOKEN"):
        return DEFAULT_TOKEN_PATH
    if account_id:
        return Path.home() / ".config" / "rb" / f"google_token.{account_id}.json"
    return DEFAULT_TOKEN_PATH


def _feeds_of(acct: dict) -> list[str]:
    feeds = acct.get("feeds") or []
    if isinstance(feeds, str):
        feeds = [f.strip(" '\"") for f in feeds.strip("[]").split(",")]
    return [f for f in feeds if f]


def _accounts_for(account_arg: str | None) -> list[dict]:
    if not account_arg:
        return [{"id": None, "email": None, "feeds": ["calendar", "email"], "legacy": True}]

    accounts = [
        a for a in core.load_inbox_accounts()
        if a.get("enabled", True)
        and a.get("automated_fetch", True)
        and a.get("provider", "google") == "google"
    ]
    if account_arg == "all":
        return accounts

    for acct in accounts:
        if acct.get("id") == account_arg:
            return [acct]

    known = sorted(a.get("id") for a in accounts if a.get("id"))
    sys.stderr.write(
        f"ERROR: account id '{account_arg}' not found in "
        f"{core.INBOX_ACCOUNTS_PATH.relative_to(core.PROJECT_DIR)}. "
        f"Known enabled accounts: {known}\n"
    )
    sys.exit(2)


def _ensure_creds(account_id: str | None = None, *, allow_consent: bool = True):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        sys.stderr.write(
            "Google API dependencies not installed. Run:\n"
            "  pip install google-auth google-auth-oauthlib google-api-python-client "
            "--break-system-packages\n"
        )
        sys.exit(2)

    creds = None
    token_path = _token_path(account_id)
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as _refresh_err:  # noqa: BLE001
                # invalid_grant or similar — token revoked by Google.
                # Fall through to the consent flow so the user can re-authorize
                # without having to manually delete the token file.
                sys.stderr.write(
                    f"Google token refresh failed ({_refresh_err}). "
                    "Falling through to OAuth consent flow.\n"
                )
                creds = None  # force consent branch below
        if not creds:
            if not allow_consent:
                if not token_path.exists():
                    sys.stderr.write(
                        f"No durable Google token at {token_path}. Run the OAuth setup "
                        f"interactively once for account '{account_id or 'default'}'.\n"
                    )
                else:
                    sys.stderr.write(
                        f"Google token at {token_path} is invalid and cannot be refreshed "
                        "without an interactive consent flow.\n"
                    )
                sys.exit(2)
            if not DEFAULT_CLIENT_PATH.exists():
                sys.stderr.write(
                    f"No OAuth client at {DEFAULT_CLIENT_PATH}. Set up a GCP project, "
                    "download the OAuth client JSON, and place it there (or override "
                    "via RB_GOOGLE_CLIENT env var). See file docstring for full setup.\n"
                )
                sys.exit(2)
            flow = InstalledAppFlow.from_client_secrets_file(
                str(DEFAULT_CLIENT_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
    return creds


def _assert_gmail_account(service, expected_email: str | None, account_id: str | None) -> None:
    if not expected_email:
        return
    profile = service.users().getProfile(userId="me").execute()
    actual = (profile.get("emailAddress") or "").lower()
    expected = expected_email.lower()
    if actual and actual != expected:
        raise RuntimeError(
            f"Google token for account '{account_id}' is authenticated as {actual}, "
            f"but accounts.yaml expects {expected}. Remove {_token_path(account_id)} "
            "and rerun the fetch, choosing the expected Google account."
        )


def _tag_events_with_source(normalized: dict, *, calendar_id: str, label: str) -> list[dict]:
    """Attach source_calendar_id/source_calendar_label to every event in a
    normalized calendar payload's events list. Used to distinguish events
    from a secondary/subscribed calendar (e.g. an employer's Outlook
    calendar published as an ICS feed and added to Google Calendar as a
    secondary calendar) from the account's own primary calendar."""
    out = []
    for ev in normalized.get("events") or []:
        ev = dict(ev)
        ev["source_calendar_id"] = calendar_id
        ev["source_calendar_label"] = label
        out.append(ev)
    return out


def fetch_calendar(days: int, *, account_id: str | None = None,
                   allow_consent: bool = True,
                   secondary_calendars: list[dict] | None = None) -> dict:
    """`secondary_calendars` — additional non-primary calendar IDs to pull
    events from for this account (e.g. an employer's Outlook calendar
    published via ICS and subscribed to in Google Calendar as a secondary
    calendar — no separate OAuth identity needed since it's just another
    calendar under the same Google account). Each entry is
    `{"id": <calendar_id>, "label": <display label>}`. Events from these
    calendars are tagged with `source_calendar_id`/`source_calendar_label`
    and merged into the same event list as the primary calendar."""
    try:
        from googleapiclient.discovery import build
    except ImportError:
        sys.stderr.write(
            "Google API dependencies not installed. Run:\n"
            "  pip install google-auth google-auth-oauthlib google-api-python-client "
            "--break-system-packages\n"
        )
        sys.exit(2)
    creds = _ensure_creds(account_id, allow_consent=allow_consent)
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days)).isoformat()

    def _fetch_raw_events(calendar_id: str) -> list[dict]:
        page_token = None
        events_raw: list[dict] = []
        while True:
            resp = service.events().list(
                calendarId=calendar_id, timeMin=time_min, timeMax=time_max,
                singleEvents=True, orderBy="startTime", pageToken=page_token,
                maxResults=250,
            ).execute()
            events_raw.extend(resp.get("items") or [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return events_raw

    # Reuse the same normalizer used by fetch_via_session
    import fetch_via_session as fvs
    result = fvs.normalize_calendar({"events": _fetch_raw_events("primary")})

    for sc in secondary_calendars or []:
        sc_id = sc.get("id")
        if not sc_id:
            continue
        # RB-2026-09-09: a removed/unsubscribed secondary calendar (e.g. an
        # expired ICS subscription) makes Google return 404 for that id
        # alone. That must not take down the primary calendar -- or, since
        # this whole function runs before email fetch in
        # _fetch_for_account(), the account's email fetch too. Isolate each
        # secondary calendar so one dead entry degrades to a warning.
        try:
            sc_events = _fetch_raw_events(sc_id)
        except Exception as exc:
            sys.stderr.write(
                f"WARNING: secondary calendar {sc_id!r} "
                f"({sc.get('label') or 'no label'}) failed to fetch, "
                f"skipping: {exc}\n"
            )
            continue
        sc_normalized = fvs.normalize_calendar({"events": sc_events})
        result["events"].extend(_tag_events_with_source(
            sc_normalized, calendar_id=sc_id, label=sc.get("label") or sc_id,
        ))
    return result


# Newsletter sender domains that get a full-body fetch on the second pass.
# Body text enables article title + URL extraction beyond snippet length.
NEWSLETTER_DOMAINS = {
    "divenewsletter.com",       # Payments Dive
    "inform.wtwhmedia.com",     # QSR / WTWH Media
    "newsletter.nrn.com",       # Nation's Restaurant News
    "qsrmagazine.com",          # QSR Magazine
    "restaurantdive.com",       # Restaurant Dive
    "foodondemandnews.com",     # Food On Demand
    "foodondemand.com",         # Food On Demand (current domain)
    "digitaltransactions.net",  # Digital Transactions
    "qsrweb.com",               # QSR Web
    "fsrmagazine.com",          # FSR Magazine
    "restaurantnews.com",       # RestaurantNews.com
    "thespoon.tech",            # The Spoon
    "hospitalitytech.com",      # Hospitality Technology
    "smartbrief.com",           # Restaurant / Restaurant Innovation SmartBrief
    "franchisetimes.com",       # Franchise Times
    "paymentsjournal.com",      # PaymentsJournal
    "pymnts.com",               # PYMNTS
    "restauranttechnologynetwork.com",  # Restaurant Technology Network
    "thepourover.org",          # The Pour Over
    "linkedin.com",             # LinkedIn newsletter digests (e.g. "Hospitality Headline"
                                 # from Michael Schatzberg) — sent from newsletters-noreply@
    # RB-2026-08-29: Restaurant Business (Informa) -- weekly since at least
    # 2026-07-02 in Todd's personal inbox, real hyperlinked article content
    # (restaurantbusinessonline.com), same Informa family as newsletter.nrn.com
    # already above. Its real From display name is "Restaurant Business"
    # (confirmed against the cached inbox) -- daily_brief.py's
    # _resolve_source() falls back to that name when passive_email_
    # intelligence's INDUSTRY_SOURCE_HINTS dict doesn't match (its needles
    # are unspaced "restaurantbusiness"; real subject/snippet text reads
    # "Restaurant Business" with a space), so the source still ends up
    # containing "restaurant" -- satisfying D+'s relevance filter -- rather
    # than falling all the way to the domain-derived last-resort guess.
    "go.informafoodservicemedia.com",  # Restaurant Business
}

# Exact sender addresses, for newsletters hosted on a multi-tenant ESP
# domain (Substack, beehiiv, etc.) where whitelisting the whole domain
# would grant a full-body fetch to every unrelated newsletter on that same
# platform, not just the one Todd actually reads.
NEWSLETTER_SENDER_ADDRESSES = {
    # RB-2026-08-29: Todd subscribed and asked for this to be tracked given
    # his AI-in-restaurants focus. mail.beehiiv.com is shared ESP
    # infrastructure for many unrelated newsletters -- whitelist this one
    # sender address, not the domain.
    "theaireport@mail.beehiiv.com",  # The AI Report
}

# Display-name matches for explicitly requested newsletters whose delivery
# infrastructure may change (or may use a shared ESP domain).  Matching the
# publication's full name keeps this narrow without granting body access to
# every sender on a multi-tenant newsletter platform.
NEWSLETTER_SENDER_NAME_HINTS = {
    "the rundown ai",
}


def _is_newsletter_sender(sender: str) -> bool:
    """Return True if the From header matches a known newsletter domain or
    an explicitly whitelisted sender address (see NEWSLETTER_SENDER_ADDRESSES
    for multi-tenant ESP domains where only specific senders are legitimate)."""
    import re as _re
    sender_lower = sender.lower()
    if any(hint in sender_lower for hint in NEWSLETTER_SENDER_NAME_HINTS):
        return True
    m = _re.search(r"([\w.+-]+)@([\w.-]+)", sender_lower)
    if not m:
        return False
    local, domain = m.group(1), m.group(2)
    if f"{local}@{domain}" in NEWSLETTER_SENDER_ADDRESSES:
        return True
    return any(domain == nd or domain.endswith("." + nd) for nd in NEWSLETTER_DOMAINS)


def _extract_body_text(payload: dict) -> str:
    """Walk a Gmail message payload tree and return decoded text/html body."""
    import base64

    mime = payload.get("mimeType", "")
    if mime == "text/html":
        data = (payload.get("body") or {}).get("data", "")
        if data:
            try:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            except Exception:
                pass
        return ""
    # Prefer text/html over text/plain when both exist
    parts = payload.get("parts") or []
    html_body = ""
    for part in parts:
        result = _extract_body_text(part)
        if result:
            if part.get("mimeType", "") == "text/html":
                return result
            if not html_body:
                html_body = result
    return html_body


def fetch_email(
    query: str,
    limit: int = 50,
    *,
    account_id: str | None = None,
    expected_email: str | None = None,
    allow_consent: bool = True,
    include_spam_trash: bool = False,
) -> dict:
    try:
        from googleapiclient.discovery import build
    except ImportError:
        sys.stderr.write(
            "Google API dependencies not installed. Run:\n"
            "  pip install google-auth google-auth-oauthlib google-api-python-client "
            "--break-system-packages\n"
        )
        sys.exit(2)
    creds = _ensure_creds(account_id, allow_consent=allow_consent)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    _assert_gmail_account(service, expected_email, account_id)
    resp = service.users().threads().list(
        userId="me", q=query, maxResults=limit,
        includeSpamTrash=include_spam_trash,
    ).execute()
    threads_in = resp.get("threads") or []
    threads_out = []
    for t in threads_in:
        full = service.users().threads().get(userId="me", id=t["id"], format="metadata").execute()
        # Map gmail messages to the Cowork-style shape so the normalizer applies
        messages = []
        is_newsletter = False
        for m in full.get("messages") or []:
            headers = {h["name"]: h["value"] for h in (m.get("payload", {}).get("headers") or [])}
            sender = headers.get("From") or ""
            if _is_newsletter_sender(sender):
                is_newsletter = True
            messages.append({
                "id": m["id"],
                "date": headers.get("Date"),
                "sender": sender,
                "subject": headers.get("Subject"),
                "labelIds": m.get("labelIds") or [],
                "snippet": m.get("snippet", ""),
                "toRecipients": [headers.get("To")] if headers.get("To") else [],
            })
        thread_dict: dict = {"id": t["id"], "messages": messages}
        # Second pass: fetch full body for newsletter threads
        if is_newsletter:
            try:
                full_body = service.users().threads().get(
                    userId="me", id=t["id"], format="full"
                ).execute()
                # Use only the last (most recent) message body
                body_msgs = full_body.get("messages") or []
                if body_msgs:
                    body_text = _extract_body_text(body_msgs[-1].get("payload") or {})
                    if body_text:
                        thread_dict["body_text"] = body_text
            except Exception:
                pass
        threads_out.append(thread_dict)
    import fetch_via_session as fvs
    normalized = fvs.normalize_email({"threads": threads_out})
    # Preserve body_text through normalization: re-attach by thread id
    body_map = {t["id"]: t["body_text"] for t in threads_out if "body_text" in t}
    if body_map:
        for thread in normalized.get("threads") or []:
            tid = thread.get("thread_id") or thread.get("id")
            if tid and tid in body_map:
                thread["body_text"] = body_map[tid]
    return normalized


# Default Gmail queries per mailbox. The inbox default mirrors the
# original behavior; the sent default matches the Step 2 handoff.
DEFAULT_QUERIES = {
    "inbox": "in:inbox newer_than:14d -in:draft",
    "sent":  "in:sent newer_than:30d -in:draft",
}


def _target_path(kind: str, account_id: str | None, mailbox: str | None = None) -> Path:
    if account_id:
        if kind == "calendar":
            return core.calendar_path_for(account_id)
        if mailbox == "sent":
            return core.email_sent_path_for(account_id)
        return core.email_path_for(account_id)
    if kind == "calendar":
        return core.CALENDAR_PATH
    return core.EMAIL_SENT_PATH if mailbox == "sent" else core.EMAIL_PATH


def _fetch_one_mailbox(
    mailbox: str,
    query: str | None,
    account_id: str | None,
    expected_email: str | None = None,
    allow_consent: bool = True,
) -> None:
    q = query or DEFAULT_QUERIES[mailbox]
    out = fetch_email(
        q,
        account_id=account_id,
        expected_email=expected_email,
        allow_consent=allow_consent,
    )
    target = _target_path("email", account_id, mailbox)
    target.write_text(json.dumps(out, indent=2, default=str))
    print(f"Wrote {len(out['threads'])} threads to "
          f"{target.relative_to(core.PROJECT_DIR)} (mailbox={mailbox}, query={q!r}).")


def _fetch_for_account(args: argparse.Namespace, acct: dict) -> int:
    account_id = acct.get("id")
    expected_email = acct.get("email")
    feeds = _feeds_of(acct)
    label = acct.get("label") or account_id or "legacy"
    did_work = False
    print(f"\n== {label} ==")

    if args.kind in ("calendar", "both") and "calendar" in feeds:
        out = fetch_calendar(
            args.days, account_id=account_id, allow_consent=not args.no_consent,
            secondary_calendars=acct.get("secondary_calendars"),
        )
        target = _target_path("calendar", account_id)
        target.write_text(json.dumps(out, indent=2, default=str))
        print(f"Wrote {len(out['events'])} events to {target.relative_to(core.PROJECT_DIR)}.")
        did_work = True

    if args.kind in ("email", "both") and "email" in feeds:
        mailboxes = ["inbox", "sent"] if args.mailbox == "both" else [args.mailbox]
        for mb in mailboxes:
            _fetch_one_mailbox(
                mb,
                args.query,
                account_id,
                expected_email,
                allow_consent=not args.no_consent,
            )
        did_work = True

    if not did_work:
        print(f"Skipped: account has feeds={feeds!r}, requested kind={args.kind!r}.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("kind", choices=["calendar", "email", "both"])
    p.add_argument("--days", type=int, default=7,
                   help="Calendar lookahead window in days.")
    p.add_argument("--query", default=None,
                   help="Gmail search query. If omitted, defaults are: "
                        f"inbox={DEFAULT_QUERIES['inbox']!r}, "
                        f"sent={DEFAULT_QUERIES['sent']!r}. "
                        "When --mailbox=both, --query overrides BOTH mailboxes "
                        "to the same query (rare).")
    p.add_argument("--mailbox", choices=["inbox", "sent", "both"], default="inbox",
                   help="Which Gmail mailbox(es) to fetch. 'sent' writes to "
                        "email_sent.json so the inbox capture is not "
                        "overwritten. 'both' fetches each in turn.")
    p.add_argument("--account", default=None,
                   help="Account id from system/inbox/accounts.yaml. When set, "
                        "uses ~/.config/rb/google_token.<account>.json and "
                        "writes per-account inbox files. Use 'all' to refresh "
                        "every enabled account/feed in the manifest.")
    p.add_argument("--no-consent", action="store_true",
                   help="Daemon/API mode: refresh only existing durable tokens; never start a browser OAuth flow.")
    args = p.parse_args()

    core.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    for acct in _accounts_for(args.account):
        _fetch_for_account(args, acct)
    return 0


if __name__ == "__main__":
    sys.exit(main())
