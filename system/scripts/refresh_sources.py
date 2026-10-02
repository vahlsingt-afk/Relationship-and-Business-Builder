#!/usr/bin/env python3
"""
refresh_sources.py — single local-only entry point to refresh external
sources (email / calendar / messages / calls / social) before relying on
the daily brief.

EXPLICITLY LOCAL-ONLY. No FastAPI route, no MCP tool, no Custom GPT
exposure. The Cowork MCP / direct Google OAuth path stays manual until
the auth story is solid. Closes the carry-over from
`CLAUDE_HANDOFF_2026-05-19.md` ("Refresh / Stale Feed Operationalization"),
which deferred the unified wrapper.

Usage:

    python3 system/scripts/refresh_sources.py --all
    python3 system/scripts/refresh_sources.py --messages --calls
    python3 system/scripts/refresh_sources.py --email --calendar
    python3 system/scripts/refresh_sources.py --social

Per-source behavior:

    --messages : runs `fetch_apple_messages.py`. Mac-only (Apple SQLite).
                 Requires Full Disk Access. After running, also refreshes
                 the interaction_overlay cache once.
    --calls    : runs `fetch_apple_calls.py`. Mac-only. Same overlay
                 refresh applies (and only happens once even with both
                 --messages and --calls).
    --email    : for every enabled account in `system/inbox/accounts.yaml`
                 whose feeds include "email", looks for one of the
                 raw-input files this script accepts (see RAW_PATTERNS
                 below). If found, normalizes via `fetch_via_session.py
                 email --account <id> --in <raw>`. If not found, prints
                 the exact Cowork MCP command needed to produce that
                 raw file and marks the source skipped_no_raw_input.
    --calendar : same pattern but for "calendar" feeds.
    --social   : refreshes the derived social overlay caches
                 (`social_overlay.py --cache` + `social_outbound.py
                 --cache`). This covers LinkedIn/social public-post
                 feeds, own-post engagement, comments/reactions, and
                 related market/RI evidence when those permissioned
                 inputs are present. The underlying feed files
                 (system/inbox/social.feed.json, social.engagement.json,
                 social.own_posts.json) and LinkedIn message exports or
                 captures come from outside this script — if missing or
                 stale, the overlay caches will simply reflect that and
                 the daily brief must report the limitation.
    --market   : refreshes the market-signals cache from
                 system/inbox/market_signals.json when present. This does
                 not fetch the web itself; scheduled AI runs should scan
                 vertical/primary sources and write reviewed rows there
                 before this cache step. Missing market_signals.json is a
                 skipped_no_raw_input status, not a hard failure.
                 RB 9.28: also merges market_signals_feed.jsonl (trade press)
                 and market_signals_earnings.jsonl (IR/EDGAR) automatically.
    --earnings : RB 9.28. Fetches EDGAR 8-K filings + investor-relations
                 press release RSS for all watch_priority=true companies in
                 system/earnings_calendar.yaml. Writes new rows to
                 system/inbox/market_signals_earnings.jsonl (append-only,
                 deduped). Run before --market so the earnings rows are
                 present when the market cache is rebuilt. Requires network.
    --watch-scan : RB 9.29. Runs the watch-list auto-scan engine after market
                 signals are refreshed. Reads all signal sources and adds
                 companies that appear >= 3 times but are not yet in
                 earnings_calendar.yaml. Adds them with added_by='cos_auto'.
                 Run after --earnings and --market (order matters).
    --all      : equivalent to --messages --calls --email --calendar
                 --social --earnings --market --watch-scan.

Optional follow-up:

    --refresh-signals : after source refresh, also run
                        `relationship_signals.py --cache --json` so the
                        next daily brief picks up the new state without
                        a separate `refresh_all` call. Off by default.

Exit codes:

    0 — every requested source either refreshed successfully or was
        intentionally skipped (with clear operator guidance printed).
    1 — at least one requested source failed.

The output format is per-source structured lines so a future operator
or scheduled job can grep / parse it. Each line includes the source
label, the action taken, and a brief reason.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402

try:
    import openpyxl
except ImportError:
    openpyxl = None


PROJECT_DIR = core.PROJECT_DIR
INBOX_DIR = PROJECT_DIR / "system" / "inbox"
ACCOUNTS_YAML = INBOX_DIR / "accounts.yaml"


# Filename patterns the wrapper will look for when an account needs a
# raw MCP capture as fetch_via_session.py input. The first match wins.
#
# For email, the inbox and sent mailboxes are separate raw files so they
# can be refreshed independently — refreshing the inbox should not
# clobber the sent capture, and vice versa. fetch_via_session.py routes
# each to a distinct normalized file via --mailbox.
RAW_PATTERNS = {
    "email": [
        "raw_gmail_threads.{id}.json",
        "raw_email.{id}.json",
        "raw.{id}.email.json",
    ],
    "email_sent": [
        "raw_gmail_sent_threads.{id}.json",
        "raw_email_sent.{id}.json",
        "raw.{id}.email_sent.json",
    ],
    "calendar": [
        "raw_calendar.{id}.json",
        "raw.{id}.calendar.json",
    ],
}


# Result statuses emitted per source.
STATUS_REFRESHED = "refreshed"
STATUS_SKIPPED_NO_RAW_INPUT = "skipped_no_raw_input"
STATUS_SKIPPED_NOT_REQUESTED = "skipped_not_requested"
STATUS_SKIPPED_DISABLED = "skipped_disabled"
STATUS_NOT_APPLICABLE_ON_PLATFORM = "not_applicable_on_platform"
STATUS_UNAVAILABLE = "unavailable"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_STALE = "stale"
STATUS_FAILED = "failed"

# RB-DEFECT-2026-09-18: consecutive successful-but-empty runs before a source
# is treated as a health signal rather than an ordinary quiet day. 3 was
# chosen to tolerate normal day-to-day variance (a source can legitimately
# return 0 items once or twice) while still catching a genuinely broken
# source within a few days rather than never.
ZERO_STREAK_ALERT_THRESHOLD = 3

SOURCE_HEALTH_PATH = PROJECT_DIR / "system" / ".cache" / "source_health.json"
STALENESS_THRESHOLDS_HOURS = {
    # RB-2026-09-07: a bare 24h threshold exactly matches the refresh
    # cadence itself (a once-daily cron), so ANY normal jitter -- the cron
    # firing a couple minutes late, the health check running slightly after
    # the fetch step -- pushes age_hours a hair past 24 and flips these to
    # "stale". brief_acceptance_check.py then treats "stale" as an outright
    # required-source FAILURE (see REQUIRED_SOURCES there), blocking the
    # entire brief send. Confirmed live 2026-09-07: last refresh was 24.04h
    # old (2.4 minutes of drift) and BOTH the Daily and Intelligence Brief
    # were silently withheld for the whole day over it -- not a real
    # staleness problem, a threshold-equals-cadence design bug. web_scanner
    # below already got the fix for this exact class of source (daily
    # cadence, needs slack) via its own comment; email/calendar never did.
    "email": 26,
    "calendar": 26,
    "messages": 48,
    "calls": 48,
    "social_feed": 48,
    "social_engagement": 48,
    "social_own_posts": 48,
    "linkedin_messaging": 48,
    "market_signals": 72,
    # Governed RB artifact, not a daily external feed. Its age should drive
    # periodic maintenance, not create a daily intelligence blind-spot alarm.
    "restaurant_tech_workbook": 720,
    # RB-DEFECT-038: web_scanner runs daily and generates restaurant/tech headlines.
    # Tracked separately from market_signals inbox (manual) so health dashboard
    # doesn't report "stale" when the scanner ran successfully this cycle.
    "web_scanner": 26,
    # This is an intake ledger, not a source expected to refresh daily.
    "user_artifacts": 8760,
}
SOURCE_TIERS = {
    "email": 1,
    "calendar": 1,
    "messages": 2,
    "calls": 2,
    "linkedin_messaging": 2,
    "social_feed": 3,
    "social_engagement": 3,
    "social_own_posts": 3,
    "market_signals": 3,
    "restaurant_tech_workbook": 4,
    # RB-DEFECT-038: web_scanner is the live restaurant/tech intelligence pipeline.
    # Tier 3 — not a primary source but critical for Section 2/3 brief quality.
    "web_scanner": 3,
    "relationship_signals": 4,
    "strategic_operators": 4,
    "interaction_overlay": 4,
    "social_overlay": 4,
    "social_outbound": 4,
    "linkedin_session_reader": 4,
    "user_artifacts": 4,
}

RECOVERY_COMMANDS = {
    ("email", STATUS_SKIPPED_NO_RAW_INPUT): (
        "From a Cowork Gmail session, run search_threads and save the JSON to "
        "system/inbox/raw_gmail_threads.{account_id}.json. Then: "
        "python3 system/scripts/refresh_sources.py --email --save-health",
        "mcp_capture",
    ),
    ("calendar", STATUS_SKIPPED_NO_RAW_INPUT): (
        "From a Cowork Calendar session, run list_events and save the JSON to "
        "system/inbox/raw_calendar.{account_id}.json. Then: "
        "python3 system/scripts/refresh_sources.py --calendar --save-health",
        "mcp_capture",
    ),
    ("messages", STATUS_UNAVAILABLE): (
        "Run: python3 system/scripts/apple_access_check.py. Then grant Full Disk Access "
        "to the app/process that runs RB and enable iPhone Text Message Forwarding to this Mac. "
        "Retry: python3 system/scripts/fetch_apple_messages.py --days 365",
        "host_setup",
    ),
    ("messages", STATUS_SKIPPED_NO_RAW_INPUT): (
        "Run: python3 system/scripts/apple_access_check.py. Then grant Full Disk Access "
        "to the app/process that runs RB and enable iPhone Text Message Forwarding to this Mac. "
        "Retry: python3 system/scripts/fetch_apple_messages.py --days 365",
        "host_setup",
    ),
    ("calls", STATUS_UNAVAILABLE): (
        "Run: python3 system/scripts/apple_access_check.py. Then grant Full Disk Access "
        "to the app/process that runs RB and enable Calls on Other Devices / Calls From iPhone. "
        "Retry: python3 system/scripts/fetch_apple_calls.py --days 365",
        "host_setup",
    ),
    ("calls", STATUS_SKIPPED_NO_RAW_INPUT): (
        "Run: python3 system/scripts/apple_access_check.py. Then grant Full Disk Access "
        "to the app/process that runs RB and enable Calls on Other Devices / Calls From iPhone. "
        "Retry: python3 system/scripts/fetch_apple_calls.py --days 365",
        "host_setup",
    ),
    ("linkedin_messaging", STATUS_SKIPPED_NO_RAW_INPUT): (
        "Option 1 (export watcher — recommended): drop LinkedIn Messages export at "
        "system/inbox/linkedin_messages_export.csv or system/inbox/linkedin_exports/, "
        "then: python3 system/scripts/linkedin_export_watcher.py --scan; "
        "python3 system/scripts/linkedin_export_watcher.py --ingest-new --confirm. "
        "Option 2 (direct): python3 system/scripts/linkedin_messaging.py --ingest "
        "system/inbox/linkedin_messages_export.csv (preview); add --confirm to write.",
        "manual_export",
    ),
    ("market_signals", STATUS_SKIPPED_NO_RAW_INPUT): (
        "Scan RTN / Restaurant Business / Nation's Restaurant News / Restaurant Dive / QSR Magazine "
        "and write reviewed rows to system/inbox/market_signals.json. Then: "
        "python3 system/scripts/refresh_sources.py --market --save-health",
        "manual_scan",
    ),
    ("restaurant_tech_workbook", STATUS_NOT_CONFIGURED): (
        "Set settings.json:daily_briefing.source_refresh_contract.restaurant_tech_workbook.path "
        "to Todd's current working Restaurant Technology Coverage workbook.",
        "settings",
    ),
    ("restaurant_tech_workbook", STATUS_SKIPPED_NO_RAW_INPUT): (
        "Confirm the workbook path exists, then run: "
        "python3 system/scripts/refresh_sources.py --restaurant-tech-workbook --save-health",
        "local_file",
    ),
    ("interaction_overlay", STATUS_SKIPPED_NO_RAW_INPUT): (
        "Refresh messages/calls after Full Disk Access is granted. Then: "
        "python3 system/scripts/interaction_overlay.py --cache --json",
        "derived_cache",
    ),
    ("relationship_signals", STATUS_SKIPPED_NO_RAW_INPUT): (
        "Run: python3 system/scripts/refresh_sources.py --all --save-health --refresh-signals",
        "derived_cache",
    ),
    ("strategic_operators", STATUS_SKIPPED_NO_RAW_INPUT): (
        "Run: python3 system/scripts/strategic_operators.py --cache --json",
        "derived_cache",
    ),
    ("social_engagement", STATUS_SKIPPED_NO_RAW_INPUT): (
        "Option 1 (official API): python3 system/scripts/linkedin_own_engagement.py --fetch --confirm. "
        "Option 2 (browser capture): python3 system/scripts/linkedin_session_reader.py --capture-own-post-engagement-js, "
        "paste JS in browser on post page, save output to /tmp/linkedin_engagement.json, then: "
        "python3 system/scripts/linkedin_session_reader.py --ingest-own-engagement --in /tmp/linkedin_engagement.json --confirm. "
        "Then: python3 system/scripts/refresh_sources.py --social --save-health",
        "manual_export",
    ),
    ("social_own_posts", STATUS_SKIPPED_NO_RAW_INPUT): (
        "Option 1 (official API): python3 system/scripts/linkedin_own_engagement.py --fetch --confirm. "
        "Option 2 (export watcher): drop LinkedIn ZIP/CSV at system/inbox/linkedin_exports/, "
        "then: python3 system/scripts/linkedin_export_watcher.py --scan; "
        "python3 system/scripts/linkedin_export_watcher.py --ingest-new --confirm. "
        "Option 3 (manual): python3 system/scripts/mutations.py my-post-add. "
        "Then: python3 system/scripts/refresh_sources.py --social --save-health",
        "manual_export",
    ),
    ("social_feed", STATUS_SKIPPED_NO_RAW_INPUT): (
        "python3 system/scripts/linkedin_session_reader.py --capture-js, "
        "paste in browser, save output to /tmp/linkedin_posts.json, then: "
        "python3 system/scripts/linkedin_session_reader.py --ingest --in /tmp/linkedin_posts.json. "
        "Then: python3 system/scripts/refresh_sources.py --social --save-health",
        "manual_export",
    ),
}


# ----------------------------------------------------------------------
# Logging + subprocess helpers (modeled on interaction_fetch_wrapper.py)
# ----------------------------------------------------------------------

def log(msg: str) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {msg}", flush=True)


def _is_operator_confirmed(entry: dict) -> bool:
    """True when a contact's notes carry an explicit operator-confirmation
    marker -- the same notes-text convention linkedin_ingest.py's
    _is_operator_confirmed() already established for employment-state
    (company/role) conflicts. Shared here so every auto-apply path in this
    file that touches a contact field (SMS-derived and cross-source last-
    touch reconciliation alike) honors the same "a human already resolved
    this, don't let a lower-confidence source silently overwrite it" rule,
    rather than each inventing its own divergent check -- or, as
    apply_sms_last_touch_updates() did until RB-DEFECT-2026-09-18, none at
    all despite apply_cross_source_last_touch()'s sibling docstring having
    long promised the same guarantee for both."""
    notes = (entry.get("notes") or "").lower()
    return "confirmed by todd" in notes or "confirmed by operator" in notes or "conflict resolved" in notes


def run(label: str, cmd: list[str]) -> tuple[int, str, str]:
    """Run a subprocess, stream its stdout/stderr through log(), return
    (returncode, stdout, stderr)."""
    log(f"-> {label}: {' '.join(shlex.quote(c) for c in cmd)}")
    rc = subprocess.run(cmd, capture_output=True, text=True)
    if rc.stdout:
        for line in rc.stdout.strip().splitlines():
            log(f"   stdout: {line}")
    if rc.stderr:
        for line in rc.stderr.strip().splitlines():
            log(f"   stderr: {line}")
    log(f"   exit: {rc.returncode}")
    return rc.returncode, rc.stdout, rc.stderr


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalized_inbox_path(source: str) -> Path | None:
    if source.startswith("email:"):
        return INBOX_DIR / f"email.{source.split(':', 1)[1]}.json"
    if source.startswith("calendar:"):
        return INBOX_DIR / f"calendar.{source.split(':', 1)[1]}.json"
    lookup = {
        "messages": INBOX_DIR / "messages.json",
        "calls": INBOX_DIR / "calls.json",
        "linkedin_messaging": INBOX_DIR / "linkedin.messages.json",
        "social_feed": INBOX_DIR / "social.feed.json",
        "social_engagement": INBOX_DIR / "social.engagement.json",
        "social_own_posts": INBOX_DIR / "social.own_posts.json",
        "market_signals": INBOX_DIR / "market_signals.json",
        "user_artifacts": INBOX_DIR / "user_artifacts.json",
        # RB-DEFECT-038: derived/cache sources — resolve via cache not inbox.
        # These are live-generated every cycle; no manual inbox file exists.
        "web_scanner": PROJECT_DIR / "system" / ".cache" / "web_scanner_cache.json",
        "relationship_signals": PROJECT_DIR / "system" / ".cache" / "relationship_signals.json",
    }
    return lookup.get(source)


def _restaurant_tech_workbook_settings() -> dict:
    settings = core.load_settings()
    contract = ((settings.get("daily_briefing") or {}).get("source_refresh_contract") or {})
    block = contract.get("restaurant_tech_workbook") or {}
    return block if isinstance(block, dict) else {}


def _restaurant_tech_workbook_path() -> Path | None:
    block = _restaurant_tech_workbook_settings()
    raw = block.get("path")
    if not raw:
        return None
    return Path(str(raw)).expanduser()


def _last_refreshed_at(source: str) -> str | None:
    if source == "restaurant_tech_workbook":
        path = _restaurant_tech_workbook_path()
        if not path or not path.exists():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    path = _normalized_inbox_path(source)
    if not path or not path.exists():
        return None
    data = _read_json(path)
    # RB-DEFECT-038: web_scanner cache is keyed by source name → find most recent last_fetch.
    if source == "web_scanner" and isinstance(data, dict):
        fetches = [v.get("last_fetch") for v in data.values() if isinstance(v, dict) and v.get("last_fetch")]
        if fetches:
            return max(fetches)
        return None
    # RB-DEFECT-038: relationship_signals cache uses _generated_at (underscore prefix).
    if source == "relationship_signals" and isinstance(data, dict):
        value = data.get("_generated_at") or data.get("generated_at")
        if isinstance(value, str):
            return value
        return None
    value = data.get("fetched_at") or data.get("generated_at")
    if isinstance(value, str):
        return value
    return None


def _item_count_from_inbox(source: str) -> int:
    """Return the number of items in the inbox file for a given source.

    Email files: {"threads": [...]}  → len(threads)
    Calendar files: {"events": [...]} → len(events)
    Calls/Messages: top-level list or {"calls": [...]} / {"messages": [...]}
    LinkedIn messages: {"messages": [...]}
    Market signals: top-level list
    Returns 0 if the file is missing, unreadable, or has no recognized structure.
    """
    if source == "restaurant_tech_workbook":
        path = _restaurant_tech_workbook_path()
        if not path or not path.exists() or openpyxl is None:
            return 0
        try:
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            if "Evidence Ledger" not in wb.sheetnames:
                return 0
            ws = wb["Evidence Ledger"]
            return max(0, ws.max_row - 4)
        except Exception:  # noqa: BLE001
            return 0
    path = _normalized_inbox_path(source)
    if not path or not path.exists():
        return 0
    try:
        data = _read_json(path)
        # RB-DEFECT-038: web_scanner cache — total items across all feed sources.
        if source == "web_scanner" and isinstance(data, dict):
            total = 0
            for v in data.values():
                if isinstance(v, dict):
                    items = v.get("items")
                    if isinstance(items, list):
                        total += len(items)
            return total
        # RB-DEFECT-038: relationship_signals cache — count signals in data.signals.
        if source == "relationship_signals" and isinstance(data, dict):
            inner = data.get("data") or {}
            if isinstance(inner, dict):
                signals = inner.get("signals")
                if isinstance(signals, list):
                    return len(signals)
                threads = inner.get("active_threads")
                if isinstance(threads, list):
                    return len(threads)
            return 0
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            # Ordered by expected key name
            for key in ("threads", "events", "messages", "calls", "items", "signals", "entries"):
                val = data.get(key)
                if isinstance(val, list):
                    return len(val)
            # Fallback: count the first list value in the dict
            for val in data.values():
                if isinstance(val, list):
                    return len(val)
    except Exception:  # noqa: BLE001
        pass
    return 0


def _source_kind(source: str) -> str:
    if source.startswith("email:") or source.startswith("email_sent:"):
        return "email"
    if source.startswith("calendar:"):
        return "calendar"
    if source in {"social_overlay", "social_outbound", "linkedin_session_reader"}:
        return source
    return source


def _recovery(source: str, status: str) -> tuple[str | None, str | None]:
    kind = _source_kind(source)
    if status == STATUS_STALE:
        if kind in {"messages", "calls"}:
            status_key = STATUS_SKIPPED_NO_RAW_INPUT
        elif kind in {"email", "calendar", "linkedin_messaging", "market_signals", "social_feed", "social_engagement", "social_own_posts"}:
            status_key = STATUS_SKIPPED_NO_RAW_INPUT
        else:
            return None, None
    else:
        status_key = status
    command_type = RECOVERY_COMMANDS.get((kind, status_key))
    if not command_type and status == STATUS_FAILED:
        return (
            f"Retry: python3 system/scripts/refresh_sources.py --{kind} --save-health; check error output for the specific failure.",
            "retry",
        )
    if not command_type:
        return None, None
    command, recovery_type = command_type
    account_id = source.split(":", 1)[1] if ":" in source else kind
    return command.format(account_id=account_id), recovery_type


def _health_status(run_status: str | None, source: str, last_refreshed_at: str | None,
                   generated_at: datetime) -> tuple[str, str | None]:
    kind = _source_kind(source)
    status = run_status or STATUS_NOT_CONFIGURED
    if status == STATUS_NOT_APPLICABLE_ON_PLATFORM:
        status = STATUS_UNAVAILABLE
    if status in {STATUS_SKIPPED_NOT_REQUESTED, "email_sent"}:
        status = STATUS_SKIPPED_NO_RAW_INPUT
    if status == STATUS_FAILED:
        return status, None
    threshold = STALENESS_THRESHOLDS_HOURS.get(kind)
    refreshed_at = _parse_dt(last_refreshed_at)
    if threshold is not None and refreshed_at is not None:
        if refreshed_at.tzinfo is None:
            refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)
        age_hours = (generated_at - refreshed_at.astimezone(timezone.utc)).total_seconds() / 3600
        if age_hours > threshold:
            return STATUS_STALE, f"last refresh is {age_hours:.1f}h old; threshold is {threshold}h"
        if status in {STATUS_SKIPPED_NO_RAW_INPUT, STATUS_UNAVAILABLE}:
            return STATUS_REFRESHED, "using existing normalized cache within staleness threshold"
    return status, None


def _configured_health_sources() -> dict[str, dict]:
    sources: dict[str, dict] = {}
    accounts = load_accounts()
    for acct in accounts:
        acct_id = acct.get("id") or "unknown"
        feeds = feeds_of(acct)
        if "email" in feeds:
            sources[f"email:{acct_id}"] = {"tier": 1, "kind": "email"}
        if "calendar" in feeds:
            sources[f"calendar:{acct_id}"] = {"tier": 1, "kind": "calendar"}
    for name, tier in {
        "messages": 2,
        "calls": 2,
        "linkedin_messaging": 2,
        "social_feed": 3,
        "social_engagement": 3,
        "social_own_posts": 3,
        "market_signals": 3,
        # RB-DEFECT-038: web_scanner is the live restaurant/tech intelligence pipeline.
        # Tracked separately from market_signals inbox (manual) to eliminate the
        # contradiction where market_signals=stale but fresh tech headlines are present.
        "web_scanner": 3,
        "relationship_signals": 4,
        "strategic_operators": 4,
        "interaction_overlay": 4,
        "user_artifacts": 4,
    }.items():
        sources.setdefault(name, {"tier": tier, "kind": name})
    if _restaurant_tech_workbook_settings().get("enabled", False):
        sources.setdefault("restaurant_tech_workbook", {"tier": SOURCE_TIERS["restaurant_tech_workbook"], "kind": "restaurant_tech_workbook"})
    return sources


def write_source_health(results: list[dict]) -> dict:
    generated_at = datetime.now(timezone.utc)
    # RB-2026-09-05: this function rebuilds `sources` from scratch every
    # cycle with no memory of prior runs, so a source stuck Stale/Unavailable
    # renders identically forever with no signal of how long it's been
    # broken -- confirmed live: calendar:global-payments and
    # email:global-payments sat at 0% trust for a full week straight with
    # no escalation. Read the outgoing file first so a still-unhealthy
    # source keeps its original unhealthy_since date instead of restarting
    # the clock every cycle.
    prior_sources = (_read_json(SOURCE_HEALTH_PATH) or {}).get("sources") or {}
    by_source = {r.get("source"): r for r in results if r.get("source")}
    sources: dict[str, dict] = {}
    configured = _configured_health_sources()

    # Map derived social refresh rows back to the raw scan surfaces that
    # make the brief trustworthy.
    social_result = next((r for r in results if r.get("source") in {"social_overlay", "social_outbound"}), None)
    if social_result:
        for name in ("social_feed", "social_engagement", "social_own_posts", "linkedin_messaging"):
            configured.setdefault(name, {"tier": SOURCE_TIERS[name], "kind": name})
            if name not in by_source:
                by_source[name] = {
                    "source": name,
                    "status": STATUS_REFRESHED if (_normalized_inbox_path(name) or Path()).exists() else STATUS_SKIPPED_NO_RAW_INPUT,
                    "reason": "derived from existing LinkedIn/social inbox capture",
                }

    for source, meta in configured.items():
        run_result = by_source.get(source) or {}
        kind = meta.get("kind") or _source_kind(source)
        last_at = _last_refreshed_at(source)
        run_status = run_result.get("status")
        if run_status is None and _normalized_inbox_path(source) and _normalized_inbox_path(source).exists():
            run_status = STATUS_REFRESHED
        elif run_status is None:
            run_status = STATUS_NOT_CONFIGURED if kind in {"email", "calendar"} else STATUS_SKIPPED_NO_RAW_INPUT
        status, freshness_reason = _health_status(run_status, source, last_at, generated_at)
        reason = freshness_reason or run_result.get("reason")
        # RB-DEFECT-038: label derived-cache sources explicitly so health dashboard
        # doesn't show a blank reason when these sources are refreshed.
        if not reason and source == "web_scanner" and status == STATUS_REFRESHED:
            reason = "live web scan completed this cycle; restaurant/tech headlines generated"
        elif not reason and source == "relationship_signals" and status == STATUS_REFRESHED:
            reason = "derived from relationship cache built this cycle"
        item_count = _item_count_from_inbox(source)
        row = {
            "status": status,
            "last_refreshed_at": last_at,
            "tier": int(meta.get("tier") or SOURCE_TIERS.get(kind, 3)),
            "item_count": item_count,
        }
        if reason:
            row["reason"] = reason
        # RB-DEFECT-2026-09-18: a source that runs successfully and returns
        # zero items looked identical to a genuinely quiet day -- there was
        # no distinction between "ran fine, nothing new" and "ran, but
        # something upstream is broken." Track a per-source streak so the
        # brief can suppress an ordinary single-day zero and only escalate
        # once the same source has returned zero for ZERO_STREAK_ALERT_
        # THRESHOLD consecutive successful runs (see should_alert_on_zero()
        # and source_health_report.py, which consumes this field).
        prior_row_for_zero = prior_sources.get(source) or {}
        if status == STATUS_REFRESHED and item_count == 0:
            prior_streak = (
                int(prior_row_for_zero.get("consecutive_zero_runs") or 0)
                if prior_row_for_zero.get("status") == STATUS_REFRESHED
                and int(prior_row_for_zero.get("item_count") or 0) == 0
                else 0
            )
            row["consecutive_zero_runs"] = prior_streak + 1
            row["zero_streak_alert"] = row["consecutive_zero_runs"] >= ZERO_STREAK_ALERT_THRESHOLD
        else:
            row["consecutive_zero_runs"] = 0
            row["zero_streak_alert"] = False
        # RB-2026-09-05: track how long a source has been continuously
        # unhealthy so the brief can escalate a chronic failure instead of
        # repeating the same unqualified warning forever. Deliberate no-op
        # states (not applicable on this platform, or a scan that simply
        # wasn't requested/is disabled) don't count as a growing problem.
        if status not in (STATUS_REFRESHED, STATUS_NOT_APPLICABLE_ON_PLATFORM,
                          STATUS_SKIPPED_NOT_REQUESTED, STATUS_SKIPPED_DISABLED):
            prior_row = prior_sources.get(source) or {}
            prior_unhealthy_since = prior_row.get("unhealthy_since")
            still_unhealthy = prior_row.get("status") not in (
                STATUS_REFRESHED, None,
            )
            unhealthy_since = prior_unhealthy_since if (still_unhealthy and prior_unhealthy_since) else generated_at.date().isoformat()
            row["unhealthy_since"] = unhealthy_since
            row["days_unhealthy"] = (generated_at.date() - date.fromisoformat(unhealthy_since)).days
        recovery_command, recovery_type = _recovery(source, status)
        if recovery_command:
            row["recovery_command"] = recovery_command
            row["recovery_type"] = recovery_type
        sources[source] = row

    tiered = [r for r in sources.values() if int(r.get("tier") or 0) in {1, 2}]
    if not sources or not any(r.get("last_refreshed_at") or r.get("status") == STATUS_REFRESHED for r in sources.values()):
        overall = "not_configured"
    elif all(r.get("status") == STATUS_REFRESHED for r in tiered):
        overall = "green"
    elif all(r.get("status") == STATUS_REFRESHED for r in sources.values() if int(r.get("tier") or 0) == 1):
        overall = "partial"
    else:
        overall = "under_instrumented"

    trust = overall
    tier1 = {k: v for k, v in sources.items() if int(v.get("tier") or 0) == 1}
    if any(v.get("status") != STATUS_REFRESHED for v in tier1.values()):
        trust = "under_instrumented"

    health = {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "overall_health": overall,
        "brief_trustworthiness": trust,
        "staleness_thresholds_hours": STALENESS_THRESHOLDS_HOURS,
        "sources": sources,
    }
    SOURCE_HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOURCE_HEALTH_PATH.write_text(json.dumps(health, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log(f"   wrote source health: {SOURCE_HEALTH_PATH}")
    return health


# ----------------------------------------------------------------------
# Accounts manifest
# ----------------------------------------------------------------------

def load_accounts() -> list[dict]:
    """Parse system/inbox/accounts.yaml. Falls back to a hand-rolled
    parser if PyYAML isn't installed so this script never depends on
    a non-stdlib package."""
    if not ACCOUNTS_YAML.exists():
        return []
    text = ACCOUNTS_YAML.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        doc = yaml.safe_load(text) or {}
        return list(doc.get("accounts") or [])
    except ModuleNotFoundError:
        pass

    # Minimal-fidelity fallback: only fields we actually use here.
    # Each account is a top-level list entry under `accounts:` whose
    # children we extract by indentation. This is intentionally narrow
    # and prefers PyYAML when available.
    accounts: list[dict] = []
    in_accounts = False
    current: dict | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not in_accounts:
            if line.strip().startswith("accounts:"):
                in_accounts = True
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # New account row begins with "  - id:" at depth 2.
        if line.startswith("  - "):
            if current is not None:
                accounts.append(current)
            current = {}
            kv = line[len("  - "):]
            if ":" in kv:
                k, v = kv.split(":", 1)
                current[k.strip()] = v.strip().strip('"').strip("'")
            continue
        # Continuation line for the current account: "    key: value".
        if current is not None and line.startswith("    ") and ":" in line and not line.lstrip().startswith("-"):
            kv = line.strip()
            k, v = kv.split(":", 1)
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                inside = v[1:-1].strip()
                current[k.strip()] = [x.strip() for x in inside.split(",") if x.strip()]
            elif v in ("true", "false"):
                current[k.strip()] = (v == "true")
            else:
                current[k.strip()] = v.strip('"').strip("'")
    if current is not None:
        accounts.append(current)
    return accounts


def feeds_of(acct: dict) -> list[str]:
    feeds = acct.get("feeds") or []
    if isinstance(feeds, str):
        # tolerant of "[calendar, email]" appearing as a single string
        feeds = [f.strip(" '\"") for f in feeds.strip("[]").split(",")]
    return [f for f in feeds if f]


# ----------------------------------------------------------------------
# Source-specific refresh routines
# ----------------------------------------------------------------------

def _is_apple_db_missing(stderr: str, needle: str) -> bool:
    """Detect the fetcher's 'database not found' message. The Apple
    fetchers print 'ERROR: chat.db not found at ...' / 'CallHistory...
    not found at ...' when the file simply isn't there (off-Mac or
    Full Disk Access not granted). Either situation is operationally
    'not applicable on this platform' rather than a hard failure for
    refresh purposes."""
    s = stderr or ""
    return needle in s and "not found" in s


def _is_apple_db_permission_denied(stderr: str) -> bool:
    s = (stderr or "").lower()
    return "authorization denied" in s or "operation not permitted" in s


def refresh_messages(py: str) -> dict:
    # DEFECT-016-A / DEFECT-003: --include-snippets required for momentum classification.
    # Without snippets the inbox is in available_metadata_only state, which blocks all
    # relationship momentum scoring via the completeness contract.
    # --trusted-fulltext: capture full message text for contacts on the
    # sms_trusted_senders.json allowlist (RB-DEFECT-032 / sms_content_mutation.py).
    # Gracefully no-ops when the allowlist file does not exist.
    cmd = [py, str(SCRIPTS_DIR / "fetch_apple_messages.py"), "--days", "90", "--include-snippets", "--trusted-fulltext"]
    rc, _, stderr = run("fetch_apple_messages", cmd)
    if rc != 0:
        status = STATUS_FAILED
        # Apple chat.db is Mac-only; signal that distinctly when the OS
        # path simply isn't there.
        if _is_apple_db_missing(stderr, "chat.db"):
            status = STATUS_NOT_APPLICABLE_ON_PLATFORM
        if _is_apple_db_permission_denied(stderr):
            status = STATUS_SKIPPED_NO_RAW_INPUT
            return {
                "source": "messages",
                "status": status,
                "exit_code": rc,
                "reason": "macOS Full Disk Access is not granted to the Python binary used by this job",
            }
        return {"source": "messages", "status": status, "exit_code": rc}
    return {"source": "messages", "status": STATUS_REFRESHED, "exit_code": 0}


def apply_sms_last_touch_updates(min_age_hours: float = 24.0) -> dict:
    """Auto-apply SMS-derived last_touch updates to the baseline.

    DEFECT-003 / Step 3 fix: SMS matching produces proposed last_touch updates
    each brief cycle but they never persist.  This function applies them
    automatically when the SMS event is older than min_age_hours — meaning
    it happened on a prior day, not in an ongoing conversation.

    Only applies updates where:
      - The contact is in the baseline (matched by contact_id)
      - The SMS event_at is more recent than the current baseline last_touch
      - The event_at is at least min_age_hours old (avoids mid-conversation updates)
      - The contact_id resolves to an RC, LKI, or LMI contact

    Returns a summary dict with applied / skipped / error counts.
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    import json as _json

    try:
        import sys as _sys
        _sys.path.insert(0, str(SCRIPTS_DIR))
        import direct_comms_health as _dch
        import rb_core as _core
        from mutations import snapshot as _snapshot
        from pathlib import Path as _Path
        from datetime import date as _date
    except Exception as e:
        return {"source": "sms_last_touch", "status": STATUS_FAILED,
                "reason": f"import failed: {e}"}

    try:
        baseline = _core.load_baseline()
        id_map = {e.get("id"): e for e in baseline if e.get("id")}
    except Exception as e:
        return {"source": "sms_last_touch", "status": STATUS_FAILED,
                "reason": f"baseline load failed: {e}"}

    try:
        msgs = _dch.check_messages_readiness(baseline)
    except Exception as e:
        return {"source": "sms_last_touch", "status": STATUS_SKIPPED_NO_RAW_INPUT,
                "reason": f"messages readiness check failed: {e}"}

    state = msgs.get("state") or ""
    if state not in ("available_fresh", "available_with_snippets"):
        return {"source": "sms_last_touch", "status": STATUS_SKIPPED_NO_RAW_INPUT,
                "reason": f"messages not in fresh/snippets state: {state}"}

    ri_candidates = msgs.get("ri_candidates") or []
    cutoff_dt = _dt.now(tz=_tz.utc) - _td(hours=min_age_hours)
    today = _date.today()

    applied: list[dict] = []
    skipped: list[dict] = []

    # Build contact_id → most recent eligible SMS event
    sms_latest: dict[str, _date] = {}
    for cand in ri_candidates:
        cid = cand.get("contact_id")
        evt = cand.get("event_at")
        if not cid or not evt:
            continue
        try:
            evt_dt = _dt.fromisoformat(evt)
            if evt_dt.tzinfo is None:
                evt_dt = evt_dt.replace(tzinfo=_tz.utc)
            if evt_dt > cutoff_dt:
                continue  # too recent — skip
            evt_date = evt_dt.date()
            if cid not in sms_latest or evt_date > sms_latest[cid]:
                sms_latest[cid] = evt_date
        except (ValueError, TypeError):
            continue

    changed = False
    for cid, sms_date in sms_latest.items():
        entry = id_map.get(cid)
        if not entry:
            continue
        # Only apply to contacts we track actively
        if entry.get("signal_class") not in ("RC", "LKI", "LMI"):
            continue
        # RB-DEFECT-2026-09-18: same gap as apply_cross_source_last_touch()
        # had -- an SMS-derived last_touch could silently overwrite a
        # contact whose last_touch a human had already explicitly confirmed.
        if _is_operator_confirmed(entry):
            skipped.append({"contact_id": cid, "reason": "operator_confirmed"})
            continue
        current_lt = entry.get("last_touch")
        if current_lt:
            try:
                current_date = _date.fromisoformat(str(current_lt))
                if sms_date <= current_date:
                    skipped.append({"contact_id": cid, "reason": "baseline already current"})
                    continue
            except (ValueError, TypeError):
                pass
        # Apply the update
        entry["last_touch"] = sms_date.isoformat()
        existing_notes = (entry.get("notes") or "").rstrip()
        today_tag = f"[{today.isoformat()}]"
        entry["notes"] = (
            f"{existing_notes}\n{today_tag} last_touch updated from SMS reconciliation "
            f"({sms_date.isoformat()}, auto-applied by refresh_sources)."
        ).lstrip()
        applied.append({
            "contact_id": cid,
            "name": entry.get("name"),
            "old_last_touch": current_lt,
            "new_last_touch": sms_date.isoformat(),
        })
        changed = True

    if not changed:
        return {
            "source": "sms_last_touch",
            "status": STATUS_REFRESHED,
            "applied": 0,
            "skipped": len(skipped),
            "detail": "No last_touch updates needed.",
        }

    # Snapshot and write
    try:
        snap = _snapshot(_core.BASELINE_PATH, f"pre-sms-last-touch-{_dt.now().strftime('%Y%m%dT%H%M%S')}")
        _core.BASELINE_PATH.write_text(_json.dumps(baseline, indent=2) + "\n")
    except Exception as e:
        return {"source": "sms_last_touch", "status": STATUS_FAILED,
                "reason": f"baseline write failed: {e}"}

    log(f"sms_last_touch: applied {len(applied)} update(s): "
        + ", ".join(f"{a['name']} ({a['old_last_touch']} → {a['new_last_touch']})" for a in applied[:5]))

    return {
        "source": "sms_last_touch",
        "status": STATUS_REFRESHED,
        "applied": len(applied),
        "skipped": len(skipped),
        "updates": applied,
    }


def apply_cross_source_last_touch(min_age_hours: float = 1.0) -> dict:
    """DEFECT-007: Cross-source last-touch reconciliation.

    Reconciles last_touch against email participants, calendar attendees,
    and strategic WhatsApp chats in addition to SMS events. Updates baseline
    only when a source date is more recent than the current last_touch.

    Sources checked (in order of recency preference):
      1. Apple Messages (via interaction_overlay / direct_comms_health)
      2. Apple Calls (via calls.json)
      3. Email participants (via email.personal.json + email.bridgepoint.json)
      4. Calendar attendees (via calendar.personal.json + calendar.bridgepoint.json)
      5. Strategic WhatsApp chats (via .cache/whatsapp_chats.json, already
         resolved to baseline contact ids by whatsapp_ingest.py)

    Only updates RC, LKI, LMI contacts. Never overwrites operator-confirmed
    last_touch entries with lower-confidence sources.
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td, date as _date
    import json as _json
    import re as _re

    try:
        import sys as _sys
        _sys.path.insert(0, str(SCRIPTS_DIR))
        import rb_core as _core
    except Exception as e:
        return {"source": "cross_source_last_touch", "status": STATUS_FAILED,
                "reason": f"import failed: {e}"}

    def _norm_email(e: str) -> str:
        return (e or "").lower().strip()

    def _parse_iso(s: str) -> _date | None:
        try:
            dt = _dt.fromisoformat(s)
            return dt.date() if hasattr(dt, "date") else _date.fromisoformat(s[:10])
        except (ValueError, TypeError):
            return None

    # Load baseline
    try:
        baseline = _core.load_baseline()
    except Exception as e:
        return {"source": "cross_source_last_touch", "status": STATUS_FAILED,
                "reason": f"baseline load failed: {e}"}

    ELIGIBLE_CLASSES = {"RC", "LKI", "LMI"}

    # Build email → contact_id index
    email_idx: dict[str, str] = {}
    for c in baseline:
        if c.get("signal_class") not in ELIGIBLE_CLASSES:
            continue
        email = _norm_email(c.get("email") or "")
        if email:
            email_idx[email] = c["id"]

    # Build contact_id → contact for fast lookup
    contact_map = {c["id"]: c for c in baseline if c.get("id")}

    # Accumulate candidate last_touch dates per contact_id
    # {contact_id: [(date, source_label)]}
    candidates: dict[str, list[tuple[_date, str]]] = {}

    def _add(cid: str, dt_str: str, label: str) -> None:
        d = _parse_iso(dt_str)
        if d and cid and cid in contact_map:
            candidates.setdefault(cid, []).append((d, label))

    # --- Email participants ---
    # RB-DEFECT-2026-09-18: this list omitted every GP (global-payments)
    # account file, so a GP Outlook capture -- however fresh -- could never
    # advance baseline last_touch through cross-source reconciliation; only
    # interaction_event_ledger.py (which globs INBOX_DIR for email*.json/
    # calendar*.json rather than hardcoding an account list) picked GP up.
    # See system/CLAUDE_HANDOFF_INTELLIGENCE_CYCLE_REPAIR_2026-09-18.md,
    # "GP Outlook manual scan" evidence section.
    for fname in ["email.personal.json", "email.bridgepoint.json", "email.global-payments.json",
                   "email_sent.personal.json", "email_sent.bridgepoint.json", "email_sent.global-payments.json"]:
        fpath = _core.INBOX_DIR / fname
        if not fpath.exists():
            continue
        try:
            emails = _json.loads(fpath.read_text())
            if isinstance(emails, dict):
                emails = emails.get("emails") or emails.get("messages") or []
            for msg in (emails or []):
                msg_date = msg.get("date") or msg.get("received_at") or msg.get("sent_at") or ""
                # Gather all participants
                participants: list[str] = []
                for field in ("from", "sender", "to", "cc", "reply_to"):
                    val = msg.get(field) or ""
                    if isinstance(val, list):
                        participants.extend(val)
                    elif val:
                        participants.append(str(val))
                for p in participants:
                    em = _norm_email(p)
                    # Strip name portion: "Name <email>" → "email"
                    m = _re.search(r"<([^>]+)>", em)
                    if m:
                        em = m.group(1)
                    cid = email_idx.get(em)
                    if cid and msg_date:
                        _add(cid, msg_date, f"email:{fname}")
        except Exception:
            continue

    # --- Calendar attendees ---
    for fname in ["calendar.personal.json", "calendar.bridgepoint.json", "calendar.global-payments.json"]:
        fpath = _core.INBOX_DIR / fname
        if not fpath.exists():
            continue
        try:
            cal = _json.loads(fpath.read_text())
            events = cal if isinstance(cal, list) else cal.get("events") or []
            for ev in (events or []):
                ev_date = ev.get("start") or ev.get("date") or ev.get("start_at") or ""
                attendees = ev.get("attendees") or []
                for att in attendees:
                    if isinstance(att, dict):
                        em = _norm_email(att.get("email") or "")
                    else:
                        em = _norm_email(str(att))
                    cid = email_idx.get(em)
                    if cid and ev_date:
                        _add(cid, ev_date, f"calendar:{fname}")
        except Exception:
            continue

    # --- WhatsApp strategic chats ---
    # RB-DEFECT-2026-09-18: whatsapp_ingest.py already matches chat
    # participants to baseline contacts, classifies each chat, and computes
    # last_message_at -- but never applied any of it. It even built a
    # `whatsapp_channel_confirm` mutation per matched participant, cached in
    # system/.cache/whatsapp_chats.json, that nothing ever consumed.
    # whatsapp_ingest_scan runs --confirm in the scheduled pipeline, but
    # since apply_cross_source_last_touch()/apply_sms_last_touch_updates()
    # never read WhatsApp data, active WhatsApp communication with a tracked
    # contact could never advance that contact's last_touch -- confirmed
    # while auditing the ingestion paths for the intelligence-cycle repair.
    # Reuses the already-resolved matched_baseline/last_message_at fields
    # from that cache (whatsapp_ingest.py's own name/phone matching), rather
    # than re-implementing chat-participant resolution here.
    whatsapp_path = _core.CACHE_DIR / "whatsapp_chats.json"
    if whatsapp_path.exists():
        try:
            chats = _json.loads(whatsapp_path.read_text()).get("chats") or []
            for chat in chats:
                if chat.get("classification") not in ("strategic_relationship", "strategic_group"):
                    continue
                last_message_at = chat.get("last_message_at")
                if not last_message_at:
                    continue
                for bc in chat.get("matched_baseline") or []:
                    cid = bc.get("id")
                    if cid:
                        _add(cid, last_message_at, f"whatsapp:{chat.get('chat_name')}")
        except Exception:
            pass

    # RB-DEFECT-2026-09-18: this function's own docstring has always claimed
    # "Never overwrites operator-confirmed last_touch entries with lower-
    # confidence sources," but nothing below actually checked for that --
    # confirmed while auditing this file for the intelligence-cycle repair.
    # Uses the module-level _is_operator_confirmed() (defined near log(),
    # shared with apply_sms_last_touch_updates() below).

    # Apply updates
    applied: list[dict] = []
    skipped: list[dict] = []
    today_str = _date.today().isoformat()
    today_tag = f"[{today_str}]"

    for cid, date_source_pairs in candidates.items():
        c = contact_map.get(cid)
        if not c:
            continue
        if _is_operator_confirmed(c):
            skipped.append({"id": cid, "reason": "operator_confirmed",
                             "current": c.get("last_touch")})
            continue
        # Pick most recent candidate
        best_date, best_source = max(date_source_pairs, key=lambda x: x[0])
        best_str = best_date.isoformat()

        # Don't apply future dates
        if best_date > _date.today():
            continue

        current_lt = c.get("last_touch")
        if current_lt:
            try:
                current_d = _date.fromisoformat(current_lt)
                if best_date <= current_d:
                    skipped.append({"id": cid, "reason": "not_more_recent",
                                    "current": current_lt, "candidate": best_str})
                    continue
            except (ValueError, TypeError):
                pass

        # Apply
        old_lt = c.get("last_touch")
        c["last_touch"] = best_str
        existing_notes = (c.get("notes") or "").rstrip()
        c["notes"] = (
            f"{existing_notes}\n{today_tag} last_touch updated from {best_source} reconciliation "
            f"({old_lt} → {best_str})."
        ).strip()
        applied.append({
            "id": cid,
            "name": c.get("name"),
            "old_last_touch": old_lt,
            "new_last_touch": best_str,
            "source": best_source,
        })

    if not applied:
        return {
            "source": "cross_source_last_touch",
            "status": STATUS_REFRESHED,
            "applied": 0,
            "skipped": len(skipped),
            "detail": "No cross-source last_touch updates needed.",
        }

    # Write baseline
    try:
        _core.BASELINE_PATH.write_text(
            _json.dumps(baseline, indent=2) + "\n", encoding="utf-8"
        )
    except Exception as e:
        return {"source": "cross_source_last_touch", "status": STATUS_FAILED,
                "reason": f"baseline write failed: {e}"}

    log(f"cross_source_last_touch: applied {len(applied)} update(s): "
        + ", ".join(f"{a['name']} ({a['old_last_touch']} → {a['new_last_touch']}, {a['source']})"
                    for a in applied[:8]))

    return {
        "source": "cross_source_last_touch",
        "status": STATUS_REFRESHED,
        "applied": len(applied),
        "skipped": len(skipped),
        "updates": applied,
    }


def refresh_calls(py: str) -> dict:
    cmd = [py, str(SCRIPTS_DIR / "fetch_apple_calls.py"), "--days", "30"]
    rc, _, stderr = run("fetch_apple_calls", cmd)
    if rc != 0:
        status = STATUS_FAILED
        if _is_apple_db_missing(stderr, "CallHistory.storedata"):
            status = STATUS_NOT_APPLICABLE_ON_PLATFORM
        if _is_apple_db_permission_denied(stderr):
            status = STATUS_SKIPPED_NO_RAW_INPUT
            return {
                "source": "calls",
                "status": status,
                "exit_code": rc,
                "reason": "macOS Full Disk Access is not granted to the Python binary used by this job",
            }
        return {"source": "calls", "status": status, "exit_code": rc}
    return {"source": "calls", "status": STATUS_REFRESHED, "exit_code": 0}


def refresh_interaction_overlay(py: str) -> dict:
    """Refresh derived interaction overlay cache after messages/calls."""
    cmd = [py, str(SCRIPTS_DIR / "interaction_overlay.py"), "--cache", "--json"]
    rc, _, _ = run("interaction_overlay (cache)", cmd)
    return {
        "source": "interaction_overlay",
        "status": STATUS_REFRESHED if rc == 0 else STATUS_FAILED,
        "exit_code": rc,
    }


def _resolve_raw_input(kind: str, account_id: str) -> Path | None:
    for pattern in RAW_PATTERNS[kind]:
        p = INBOX_DIR / pattern.format(id=account_id)
        if p.exists():
            return p
    return None


def _normalized_cache_status(kind: str, account_id: str) -> tuple[bool, str | None]:
    """Return whether the normalized per-account cache is fresh enough to trust.

    This lets daemon/API refreshes use files written by fetch_google.py directly
    instead of incorrectly reporting "no raw input" merely because there is no
    Cowork connector capture to normalize.
    """
    source = f"{kind}:{account_id}"
    path = _normalized_inbox_path(source)
    if not path or not path.exists():
        return False, None
    last_at = _last_refreshed_at(source)
    refreshed_at = _parse_dt(last_at)
    if refreshed_at is None:
        return False, "normalized cache exists but has no fetched_at timestamp"
    if refreshed_at.tzinfo is None:
        refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - refreshed_at.astimezone(timezone.utc)).total_seconds() / 3600
    threshold = STALENESS_THRESHOLDS_HOURS.get(kind, 24)
    if age_hours <= threshold:
        return True, f"using existing normalized cache from {last_at}"
    return False, f"normalized cache is stale ({age_hours:.1f}h old; threshold {threshold}h)"


def _mcp_hint(kind: str, account_id: str, account_email: str | None) -> str:
    """Print the exact Cowork MCP command needed to produce a raw input
    file, so the operator can copy-paste."""
    pretty = RAW_PATTERNS[kind][0].format(id=account_id)
    target = INBOX_DIR / pretty
    if kind == "calendar":
        return (
            f"Durable path: python3 system/scripts/fetch_google.py both --account {account_id} --mailbox both --days 14\n"
            f"Session path: from a Cowork session pointing at {account_email or account_id}, run\n"
            f"        list_events (Google Calendar MCP)\n"
            f"      and save the raw JSON to:\n"
            f"        {target}\n"
            f"      Then re-run: refresh_sources.py --calendar"
        )
    if kind == "email_sent":
        return (
            f"Durable path: python3 system/scripts/fetch_google.py email --account {account_id} --mailbox sent\n"
            f"Session path: from a Cowork session pointing at {account_email or account_id}, run\n"
            f"        search_threads (Gmail MCP) with query 'in:sent newer_than:30d -in:draft'\n"
            f"      and save the raw JSON to:\n"
            f"        {target}\n"
            f"      Then re-run: refresh_sources.py --email\n"
            f"      (The sent-mailbox capture is optional — without it, self-sent\n"
            f"      threads continue to fall through to self_sent_skipped.)"
        )
    return (
        f"Durable path: python3 system/scripts/fetch_google.py email --account {account_id} --mailbox inbox\n"
        f"Session path: from a Cowork session pointing at {account_email or account_id}, run\n"
        f"        search_threads (Gmail MCP)\n"
        f"      and save the raw JSON to:\n"
        f"        {target}\n"
        f"      Then re-run: refresh_sources.py --email"
    )


def refresh_email_or_calendar(py: str, kind: str) -> list[dict]:
    """Per-account refresh. Returns one result row per enabled account
    that subscribes to this kind of feed.

    For kind="email", also looks for an optional sent-mailbox raw input
    (RAW_PATTERNS["email_sent"]) per account. If present, normalizes it
    into email_sent.<id>.json via `fetch_via_session.py email --mailbox sent`.
    If absent, prints a one-line note and continues — the sent mailbox
    capture is optional and the inbox flow does not depend on it.
    """
    out: list[dict] = []
    accounts = load_accounts()
    if not accounts:
        out.append({
            "source": kind,
            "status": STATUS_FAILED,
            "reason": f"no accounts found in {ACCOUNTS_YAML}",
            "exit_code": 1,
        })
        return out

    # fetch_via_session uses 'email' / 'calendar' as the positional 'kind'
    # argument; RAW_PATTERNS has a separate 'email_sent' entry only as a
    # filename lookup key.
    fvs_kind = "email" if kind == "email_sent" else kind

    found_any = False
    for acct in accounts:
        # Sent-mailbox feed lives alongside the email feed on the same
        # account, so respect the "email" feed flag for either.
        feed_key = "email" if kind == "email_sent" else kind
        if feed_key not in feeds_of(acct):
            continue
        acct_id = acct.get("id") or "unknown"
        acct_email = acct.get("email")
        found_any = True
        if acct.get("enabled") is False:
            out.append({
                "source": f"{kind}:{acct_id}",
                "status": STATUS_SKIPPED_DISABLED,
                "reason": "account is enabled=false in accounts.yaml",
            })
            continue
        raw = _resolve_raw_input(kind, acct_id)
        if raw is None:
            if kind in {"email", "calendar"}:
                fresh, cache_reason = _normalized_cache_status(kind, acct_id)
                if fresh:
                    out.append({
                        "source": f"{kind}:{acct_id}",
                        "status": STATUS_REFRESHED,
                        "reason": cache_reason,
                    })
                    continue
            if kind == "email_sent":
                # Sent-mailbox capture is optional: log a one-line note
                # rather than the full MCP hint so we don't drown the
                # inbox refresh output. The result is still recorded so
                # --json consumers can see what happened.
                log(f"   no sent-mailbox raw for email:{acct_id} "
                    f"(optional — self_sent_skipped will be used instead)")
            else:
                log(f"   needs raw input for {kind}:{acct_id} (no matching raw_*.json found)")
                log("   " + _mcp_hint(kind, acct_id, acct_email).replace("\n", "\n   "))
            out.append({
                "source": f"{kind}:{acct_id}",
                "status": STATUS_SKIPPED_NO_RAW_INPUT,
                "reason": (
                    f"none of {RAW_PATTERNS[kind]} present in {INBOX_DIR}; "
                    +
                    (cache_reason + "; " if kind in {"email", "calendar"} and cache_reason else "")
                    + "durable Google fetch or Cowork MCP capture required"
                ),
            })
            continue
        cmd = [
            py,
            str(SCRIPTS_DIR / "fetch_via_session.py"),
            fvs_kind,
            "--account",
            acct_id,
            "--in",
            str(raw),
        ]
        if kind == "email_sent":
            cmd.extend(["--mailbox", "sent"])
        rc, _, _ = run(f"fetch_via_session {kind}:{acct_id}", cmd)
        out.append({
            "source": f"{kind}:{acct_id}",
            "status": STATUS_REFRESHED if rc == 0 else STATUS_FAILED,
            "exit_code": rc,
            "raw_input": str(raw),
        })
    if not found_any:
        out.append({
            "source": kind,
            "status": STATUS_FAILED,
            "reason": f"no enabled accounts subscribe to {kind} feed",
            "exit_code": 1,
        })
    return out


def refresh_social(py: str) -> list[dict]:
    out: list[dict] = []
    rc, _, _ = run(
        "linkedin_session_reader (purge/materialize)",
        [py, str(SCRIPTS_DIR / "linkedin_session_reader.py"), "--purge"],
    )
    out.append({
        "source": "linkedin_session_reader",
        "status": STATUS_REFRESHED if rc == 0 else STATUS_FAILED,
        "exit_code": rc,
    })
    rc, _, _ = run(
        "social_overlay (cache)",
        [py, str(SCRIPTS_DIR / "social_overlay.py"), "--cache", "--json"],
    )
    out.append({
        "source": "social_overlay",
        "status": STATUS_REFRESHED if rc == 0 else STATUS_FAILED,
        "exit_code": rc,
    })
    rc, _, _ = run(
        "social_outbound (cache)",
        [py, str(SCRIPTS_DIR / "social_outbound.py"), "--cache", "--json"],
    )
    out.append({
        "source": "social_outbound",
        "status": STATUS_REFRESHED if rc == 0 else STATUS_FAILED,
        "exit_code": rc,
    })
    # Heads-up: this script never *fetches* social/LinkedIn data. It
    # refreshes derived caches from permissioned inputs already present
    # in system/inbox. If the feed/message captures are stale, the
    # overlay caches will be too, and Daily Prep Summary should say so.
    feed_path = INBOX_DIR / "social.feed.json"
    if not feed_path.exists():
        log(f"   note: {feed_path} is missing; overlay reflects no posts")
    for path in (
        INBOX_DIR / "social.engagement.json",
        INBOX_DIR / "social.own_posts.json",
        INBOX_DIR / "linkedin.messages.json",
    ):
        if not path.exists():
            if path.name == "linkedin.messages.json":
                log(
                    f"   note: {path} is missing; run: "
                    "python3 system/scripts/linkedin_messaging.py --ingest <path/to/messages.csv>"
                )
            else:
                log(f"   note: {path} is missing; corresponding LinkedIn/social scan surface is unavailable")
    return out


def refresh_market_feeds(py: str) -> dict:
    """RB-2026-08-24: market_source_feeds.py --fetch (RSS trade-press feeder,
    writes system/inbox/market_signals_feed.jsonl) was never actually called
    by anything automated or API-driven -- confirmed via the intelligence
    pipeline map research pass -- even though market_signals.py already
    merges that exact file in (see its INBOX_DIR / "market_signals_feed.jsonl"
    reference) and every downstream consumer of market_signals.py treats it
    as live. Runs before refresh_market() so the feed is populated before
    market_signals.py reads it.
    """
    rc, _, _ = run(
        "market_source_feeds (fetch)",
        [py, str(SCRIPTS_DIR / "market_source_feeds.py"), "--fetch", "--save-health"],
    )
    return {
        "source": "market_source_feeds",
        "status": STATUS_REFRESHED if rc == 0 else STATUS_FAILED,
        "exit_code": rc,
    }


def refresh_market(py: str) -> dict:
    inbox_path = INBOX_DIR / "market_signals.json"
    if not inbox_path.exists():
        return {
            "source": "market_signals",
            "status": STATUS_SKIPPED_NO_RAW_INPUT,
            "reason": (
                "system/inbox/market_signals.json is missing; scan RTN, "
                "Restaurant Business, Nation's Restaurant News, Restaurant "
                "Dive, QSR Magazine, Fast Casual, earnings/filings, and "
                "operator/vendor primary sources, then write reviewed rows."
            ),
        }
    rc, _, _ = run(
        "market_signals (cache)",
        [py, str(SCRIPTS_DIR / "market_signals.py"), "--cache", "--json"],
    )
    return {
        "source": "market_signals",
        "status": STATUS_REFRESHED if rc == 0 else STATUS_FAILED,
        "exit_code": rc,
    }


def refresh_earnings(py: str) -> dict:
    """RB 9.28 — Fetch EDGAR 8-K + IR press release RSS for tracked companies.

    Runs earnings_monitor.py --fetch --save-health against the FULL tracked
    universe (not just watch_priority=true names) and writes new rows to
    system/inbox/market_signals_earnings.jsonl. earnings_monitor.py's own
    tiered fetch (RB Unified Restaurant-Tech Graph, 2026-07-31) keeps cost
    down: watch_priority=true and near-earnings companies get a full EDGAR
    + IR-RSS fetch, everyone else gets a cheap EDGAR-8K-only health check.
    Passing --watch-only here would filter the calendar down to only the
    always-full set before tiering ever runs, silently excluding every
    lower-priority company from the daily pipeline. These rows are
    automatically merged into the market_signals pipeline on the next
    --market refresh.
    """
    rc, _, _ = run(
        "earnings_monitor (fetch)",
        [py, str(SCRIPTS_DIR / "earnings_monitor.py"),
         "--fetch", "--save-health"],
    )
    return {
        "source": "earnings_monitor",
        "status": STATUS_REFRESHED if rc == 0 else STATUS_FAILED,
        "exit_code": rc,
    }


def refresh_watch_scan(py: str) -> dict:
    """RB 9.29 — Auto-scan market signals and add companies above threshold.

    Runs earnings_monitor.py --auto-scan, which reads from all three signal
    sources (market_signals_earnings.jsonl, market_signals_feed.jsonl, and
    .cache/market_signals.json) and auto-adds companies that appear >= 3
    times but are not yet in earnings_calendar.yaml.

    Must run AFTER --earnings and --market so the signal sources are fresh.
    """
    rc, _, _ = run(
        "earnings_monitor (auto-scan)",
        [py, str(SCRIPTS_DIR / "earnings_monitor.py"), "--auto-scan"],
    )
    return {
        "source": "watch_scan",
        "status": STATUS_REFRESHED if rc == 0 else STATUS_FAILED,
        "exit_code": rc,
    }


def refresh_restaurant_tech_workbook(py: str) -> dict:
    block = _restaurant_tech_workbook_settings()
    if not block.get("enabled", False):
        return {
            "source": "restaurant_tech_workbook",
            "status": STATUS_SKIPPED_DISABLED,
            "reason": "settings.json restaurant_tech_workbook.enabled is false",
        }
    path = _restaurant_tech_workbook_path()
    if path is None:
        return {
            "source": "restaurant_tech_workbook",
            "status": STATUS_NOT_CONFIGURED,
            "reason": "settings.json restaurant_tech_workbook.path is not set",
        }
    if not path.exists():
        return {
            "source": "restaurant_tech_workbook",
            "status": STATUS_SKIPPED_NO_RAW_INPUT,
            "reason": f"configured workbook does not exist: {path}",
        }
    rc, _, _ = run(
        "tech_stack_workbook_sync (ingest current working workbook)",
        [py, str(SCRIPTS_DIR / "tech_stack_workbook_sync.py"), "ingest", str(path), "--confirm"],
    )
    return {
        "source": "restaurant_tech_workbook",
        "status": STATUS_REFRESHED if rc == 0 else STATUS_FAILED,
        "exit_code": rc,
        "workbook_path": str(path),
        "reason": "ingested Evidence Ledger into ecosystem_intelligence graph",
    }


def refresh_ecosystem_intelligence(py: str) -> dict:
    """RB Unified Restaurant-Tech Graph (2026-07-31), Phase 5 -- daily
    mutation workflow. Runs intelligence_mutation_engine.py --refresh-ecosystem,
    which reads today's classified earnings/trade-press signal rows
    (market_signals_earnings.jsonl, market_signals_feed.jsonl), converts
    mutation-worthy ones (named wins, renewals/expansions, churn/loss) into
    ecosystem_intelligence.json mutations, applies them with the existing
    conflict-detection + confidence gate, and writes a receipt to
    system/.cache/ecosystem_mutation_receipt.json.

    Must run AFTER --earnings and --market so the signal sources are fresh.
    """
    rc, _, _ = run(
        "intelligence_mutation_engine (refresh-ecosystem)",
        [py, str(SCRIPTS_DIR / "intelligence_mutation_engine.py"), "--refresh-ecosystem"],
    )
    return {
        "source": "ecosystem_intelligence",
        "status": STATUS_REFRESHED if rc == 0 else STATUS_FAILED,
        "exit_code": rc,
    }


def refresh_relationship_signals(py: str) -> dict:
    rc, _, _ = run(
        "relationship_signals (cache)",
        [py, str(SCRIPTS_DIR / "relationship_signals.py"), "--cache", "--json"],
    )
    return {
        "source": "relationship_signals",
        "status": STATUS_REFRESHED if rc == 0 else STATUS_FAILED,
        "exit_code": rc,
    }


def refresh_relationship_health(py: str) -> dict:
    """Persist metadata-derived relationship health (drr_score, outstanding
    follow-ups, communication frequency) onto baseline_index.json. See
    relationship_health_aggregate.py — Metadata-First Connector, RB Phase 1."""
    rc, _, _ = run(
        "relationship_health_aggregate (apply)",
        [py, str(SCRIPTS_DIR / "relationship_health_aggregate.py"), "--apply"],
    )
    return {
        "source": "relationship_health_aggregate",
        "status": STATUS_REFRESHED if rc == 0 else STATUS_FAILED,
        "exit_code": rc,
    }


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="Local-only source refresh. Wraps the per-source fetchers.",
    )
    p.add_argument("--messages", action="store_true", help="Refresh Apple messages.")
    p.add_argument("--calls", action="store_true", help="Refresh Apple calls.")
    p.add_argument("--email", action="store_true",
                   help="Normalize email raw_*.json per enabled account.")
    p.add_argument("--calendar", action="store_true",
                   help="Normalize calendar raw_*.json per enabled account.")
    p.add_argument("--social", action="store_true",
                   help="Refresh derived social overlay caches.")
    p.add_argument("--market", action="store_true",
                   help="Refresh market-signal cache from market_signals.json.")
    p.add_argument("--earnings", action="store_true",
                   help="Fetch EDGAR 8-K + IR RSS for watch-priority companies (RB 9.28).")
    p.add_argument("--watch-scan", action="store_true", dest="watch_scan",
                   help="Auto-scan market signals and add new companies above threshold (RB 9.29).")
    p.add_argument("--ecosystem", action="store_true",
                   help="Daily mutation workflow: convert classified earnings/trade-press signal "
                        "rows into ecosystem_intelligence.json mutations (RB Unified Restaurant-Tech "
                        "Graph, 2026-07-31). Run after --earnings/--market so signals are fresh.")
    p.add_argument("--restaurant-tech-workbook", action="store_true", dest="restaurant_tech_workbook",
                   help="Ingest Todd's current Restaurant Technology Coverage workbook Evidence Ledger "
                        "into ecosystem_intelligence.json using the configured settings.json path.")
    p.add_argument("--all", action="store_true", help="Equivalent to all of the above.")
    p.add_argument("--refresh-signals", action="store_true", dest="refresh_signals",
                   help="After source refresh, also rebuild the relationship_signals cache.")
    p.add_argument("--refresh-relationship-health", action="store_true", dest="refresh_relationship_health",
                   help="After source refresh, persist relationship_health (drr_score, outstanding "
                        "follow-ups, communication frequency) onto baseline_index.json.")
    p.add_argument("--save-health", action="store_true", dest="save_health",
                   help="Write system/.cache/source_health.json after collecting source results.")
    p.add_argument("--health-only", action="store_true", dest="health_only",
                   help="Recompute source health from current normalized files without refreshing sources.")
    p.add_argument("--json", action="store_true", dest="json_out",
                   help="Emit the per-source result list as a final JSON line.")
    args = p.parse_args()

    if args.all:
        args.messages = True
        args.calls = True
        args.email = True
        args.calendar = True
        args.social = True
        args.market = True
        args.earnings = True
        args.watch_scan = True
        args.ecosystem = True
        args.restaurant_tech_workbook = True

    if not args.health_only and not any([args.messages, args.calls, args.email, args.calendar,
                args.social, args.market, args.earnings, args.watch_scan,
                args.ecosystem, args.restaurant_tech_workbook]):
        p.error("specify at least one source flag, or --all")

    if args.health_only:
        args.save_health = True

    py = sys.executable or "/usr/bin/python3"

    log("=== refresh_sources start ===")
    log(f"python={py}")
    log(f"project_dir={PROJECT_DIR}")

    results: list[dict] = []
    need_overlay_refresh = False

    if args.messages:
        results.append(refresh_messages(py))
        if results[-1]["status"] == STATUS_REFRESHED:
            need_overlay_refresh = True
            # DEFECT-003 Step 3: auto-apply SMS-derived last_touch updates (>24h old)
            try:
                sms_lt_result = apply_sms_last_touch_updates(min_age_hours=24.0)
                results.append(sms_lt_result)
            except Exception:  # noqa: BLE001
                pass
            # DEFECT-007: cross-source last-touch reconciliation (email + calendar)
            try:
                results.append(apply_cross_source_last_touch())
            except Exception:  # noqa: BLE001
                pass
    if args.calls:
        results.append(refresh_calls(py))
        if results[-1]["status"] == STATUS_REFRESHED:
            need_overlay_refresh = True
    if need_overlay_refresh:
        results.append(refresh_interaction_overlay(py))

    if args.email:
        results.extend(refresh_email_or_calendar(py, "email"))
        # Also opportunistically refresh the sent mailbox if a raw input
        # is present. The inbox flow does not depend on this; missing raw
        # files report as STATUS_SKIPPED_NO_RAW_INPUT and exit code stays
        # 0 (it's an optional capture).
        results.extend(refresh_email_or_calendar(py, "email_sent"))
    if args.calendar:
        results.extend(refresh_email_or_calendar(py, "calendar"))
    if args.social:
        results.extend(refresh_social(py))
    if args.earnings:
        results.append(refresh_earnings(py))
    if args.market:
        results.append(refresh_market_feeds(py))
        results.append(refresh_market(py))
    if args.watch_scan:
        results.append(refresh_watch_scan(py))
    if args.ecosystem:
        results.append(refresh_ecosystem_intelligence(py))
    if args.restaurant_tech_workbook:
        results.append(refresh_restaurant_tech_workbook(py))

    if args.refresh_signals:
        results.append(refresh_relationship_signals(py))

    if args.refresh_relationship_health:
        results.append(refresh_relationship_health(py))

    # Summary
    refreshed = [r for r in results if r["status"] == STATUS_REFRESHED]
    failed = [r for r in results if r["status"] == STATUS_FAILED]
    skipped = [
        r for r in results
        if r["status"] in {
            STATUS_SKIPPED_NO_RAW_INPUT,
            STATUS_SKIPPED_DISABLED,
            STATUS_NOT_APPLICABLE_ON_PLATFORM,
            STATUS_UNAVAILABLE,
        }
    ]
    log("=== summary ===")
    log(f"   refreshed: {len(refreshed)}  skipped: {len(skipped)}  failed: {len(failed)}")
    for r in results:
        extra = ""
        if "reason" in r:
            extra = f"  ({r['reason']})"
        log(f"   {r['source']:<32} {r['status']}{extra}")
    if args.save_health:
        write_source_health(results)
    if args.json_out:
        # Single final line for downstream parsers.
        print(json.dumps({"results": results}, indent=2))

    log("=== refresh_sources end ===")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
