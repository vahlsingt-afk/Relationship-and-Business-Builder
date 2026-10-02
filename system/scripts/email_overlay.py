#!/usr/bin/env python3
"""
email_overlay.py — match `system/inbox/email.json` against baseline.

Surfaces inbox threads where the sender is a known contact, with priority
given to active threads, unread, and inner-tier RCs. Also surfaces
sent-followup threads where Todd was the most recent sender, with their
response-window classification (Step 2 of the canonical CoS sprint).

Usage:
    python3 email_overlay.py            # text report
    python3 email_overlay.py --json
    python3 email_overlay.py --cache
    python3 email_overlay.py --smoke    # in-memory regression for sent_followups
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rb_core as core


def _render_text(overlay: dict) -> None:
    if not overlay["fetched_at"]:
        print(f"No email data found at {core.EMAIL_PATH.relative_to(core.PROJECT_DIR)}.")
        print("Run a fetcher first (see system/inbox/README.md).")
        return
    accts = overlay.get("accounts_seen") or []
    print(f"Email overlay (fetched {overlay['fetched_at']}, stale={overlay['stale']}, accounts={','.join(accts)})")
    print(f"  noise skipped: {overlay.get('noise_skipped',0)}, "
          f"self-sent skipped: {overlay.get('self_sent_skipped',0)}, "
          f"sent followups: {len(overlay.get('sent_followups', []))}, "
          f"senders not in baseline: {len(overlay.get('senders_not_in_baseline', []))}")
    if overlay.get("active_thread_company_hits"):
        print(f"\n## ACTIVE-THREAD COMPANY HITS ({len(overlay['active_thread_company_hits'])})")
        for h in overlay["active_thread_company_hits"]:
            mark = "•" if h["unread"] else " "
            acct = f"[{h.get('account_id','-')}]"
            print(f"  {mark} {h['last_message_at'][:10]} {acct}  {h['sender_email']}  — {h['subject'][:80]}")
            print(f"      threads: {','.join(h['active_thread_ids'])}")
    if overlay["from_baseline"]:
        print(f"\n## BASELINE-MATCHED EMAILS ({len(overlay['from_baseline'])})")
        for t in overlay["from_baseline"]:
            mark = "•" if t["unread"] else " "
            tier = t["match"]["rc_tier"] or "-"
            acct = f"[{t.get('account_id','-')}]"
            print(f"  {mark} {t['last_message_at'][:10]} {acct}  {t['match']['name']} ({t['match']['signal_class']}/{tier})  — {t['subject'][:80]}")
    if overlay.get("sent_followups"):
        print(f"\n## SENT FOLLOWUPS — awaiting response ({len(overlay['sent_followups'])})")
        for s in overlay["sent_followups"]:
            sent_at = (s.get("sent_at") or "")[:10]
            acct = f"[{s.get('account_id','-')}]"
            status = s.get("response_status", "?")
            action = s.get("recommended_action", "?")
            conf = s.get("confidence", "?")
            cat = s.get("category", "?")
            window = s.get("response_window_business_days") or [None, None]
            bdays = s.get("business_days_since_sent")
            expected = s.get("expected_response_by") or "-"
            to_str = ", ".join((r.get("name") or r.get("email") or "?") for r in (s.get("to") or []))[:60] or "(no recipients)"
            print(f"  {sent_at} {acct} [{status}/{action}/{cat}/{conf}] {bdays}bd since "
                  f"(window {window[0]}-{window[1]}bd, expected by {expected})")
            print(f"      to: {to_str}  — {(s.get('subject') or '')[:80]}")


# ----------------------------------------------------------------------
# Smoke test for sent_followups (Step 2a)
# ----------------------------------------------------------------------

def _smoke() -> int:
    """In-memory regression for the sent_followup classification.

    Phase 1 (overlay classification): patches core.load_email /
    load_baseline / load_active_threads with synthetic inputs covering:
    (a) active-thread match with overdue window, (b) baseline-contact
    match still inside window, (c) untraceable outbound that should fall
    to self_sent_skipped, (d) an inbound thread that should still appear
    in from_baseline.

    Phase 2 (Step 2b two-file load_email): writes a temp inbox file and
    a temp sent file in an isolated tmpdir-INBOX_DIR, verifies that
    load_email's recency merge picks the newer-message version of a
    thread that appears in both, and that the overlay then routes it to
    sent_followups.
    """
    failures: list[str] = []

    def ck(cond: bool, msg: str) -> None:
        mark = "OK" if cond else "FAIL"
        print(f"  {mark}   {msg}")
        if not cond:
            failures.append(msg)

    # Pin "now" so business-day math is deterministic. Pick a Wednesday so
    # neither offset crosses a weekend boundary in awkward ways.
    now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)  # Wed
    todd = "todd@example.com"

    # Sent 10 calendar days ago = ~7 business days — beyond the 5-day
    # active-opportunity window: should classify as response_overdue.
    overdue_sent = (now - timedelta(days=10)).isoformat()
    # Sent 2 calendar days ago = ~2 business days — inside the 5-day
    # active-opportunity window: should classify as awaiting_response/monitor.
    fresh_sent = (now - timedelta(days=2)).isoformat()

    fake_email_payload = {
        "fetched_at": now.isoformat(),
        "per_account_fetched_at": {"primary": now.isoformat()},
        "accounts_seen": ["primary"],
        "threads": [
            {  # (a) sent, active-thread match, overdue
                "thread_id": "T-A-overdue",
                "subject": "Re: Harri intro follow-up",
                "last_message_at": overdue_sent,
                "last_message_from": {"name": "Todd", "email": todd},
                "last_message_to": [{"name": "Simin", "email": "simin@harri.example"}],
                "snippet": "Following up on Harri.",
                "account_id": "primary",
                "account_label": "Primary",
            },
            {  # (b) sent, baseline-contact match (no active thread), fresh
                "thread_id": "T-B-warm",
                "subject": "Catching up",
                "last_message_at": fresh_sent,
                "last_message_from": {"name": "Todd", "email": todd},
                "last_message_to": [{"name": "Jane Doe", "email": "jane@example.org"}],
                "snippet": "Would love to grab time.",
                "account_id": "primary",
                "account_label": "Primary",
            },
            {  # (c) sent, no recipients / no matches — should land in self_sent_skipped
                "thread_id": "T-C-untraceable",
                "subject": "(no subject)",
                "last_message_at": fresh_sent,
                "last_message_from": {"name": "Todd", "email": todd},
                "snippet": "",
                "account_id": "primary",
                "account_label": "Primary",
            },
            {  # (d) inbound from baseline contact — should NOT be a sent_followup
                "thread_id": "T-D-inbound",
                "subject": "Re: Catching up",
                "last_message_at": fresh_sent,
                "last_message_from": {"name": "Jane Doe", "email": "jane@example.org"},
                "snippet": "Sounds great.",
                "account_id": "primary",
                "account_label": "Primary",
            },
        ],
    }

    fake_baseline = [
        {"id": "p-jane", "name": "Jane Doe", "email": "jane@example.org",
         "signal_class": "rc", "rc_tier": "outer"},
        # Simin not in baseline — active-thread match must drive the classification.
    ]
    fake_active_threads = [
        {"id": "AT-harri", "status": "open", "title": "Harri opportunity",
         "companies": ["Harri"], "people": ["Simin"]},
    ]

    # Patch loaders and self_emails just for the smoke.
    orig_load_email = core.load_email
    orig_load_baseline = core.load_baseline
    orig_load_active_threads = core.load_active_threads
    orig_self_emails = core.self_emails
    orig_is_stale = core.is_overlay_stale
    try:
        core.load_email = lambda: fake_email_payload  # type: ignore
        core.load_baseline = lambda: fake_baseline  # type: ignore
        core.load_active_threads = lambda: fake_active_threads  # type: ignore
        core.self_emails = lambda: {todd}  # type: ignore
        core.is_overlay_stale = lambda payload: False  # type: ignore
        overlay = core.email_overlay()
    finally:
        core.load_email = orig_load_email  # type: ignore
        core.load_baseline = orig_load_baseline  # type: ignore
        core.load_active_threads = orig_load_active_threads  # type: ignore
        core.self_emails = orig_self_emails  # type: ignore
        core.is_overlay_stale = orig_is_stale  # type: ignore

    sf = overlay.get("sent_followups") or []
    by_tid = {s["thread_id"]: s for s in sf}

    ck("sent_followups" in overlay, "overlay exposes sent_followups key")
    ck("T-A-overdue" in by_tid, "active-thread overdue sent thread is captured")
    ck("T-B-warm" in by_tid, "warm baseline sent thread is captured")
    ck("T-C-untraceable" not in by_tid, "untraceable sent thread is NOT in sent_followups")
    ck(overlay.get("self_sent_skipped", 0) == 1, "untraceable sent thread increments self_sent_skipped")
    ck(not any(s["thread_id"] == "T-D-inbound" for s in sf),
       "inbound thread is NOT misclassified as sent_followup")

    a = by_tid.get("T-A-overdue", {})
    ck(a.get("category") == "active", "T-A-overdue category=active")
    ck(a.get("response_status") == "response_overdue",
       f"T-A-overdue response_status=response_overdue (got {a.get('response_status')})")
    ck(a.get("recommended_action") == "follow_up", "T-A-overdue recommended_action=follow_up")
    ck(a.get("confidence") == "high", "T-A-overdue confidence=high")
    ck(a.get("matched_threads") == ["AT-harri"],
       f"T-A-overdue matched_threads=[AT-harri] (got {a.get('matched_threads')})")
    ck(a.get("expected_response_by") is not None, "T-A-overdue has expected_response_by")

    b = by_tid.get("T-B-warm", {})
    ck(b.get("category") == "warm", "T-B-warm category=warm")
    ck(b.get("response_status") == "awaiting_response",
       f"T-B-warm response_status=awaiting_response (got {b.get('response_status')})")
    ck(b.get("recommended_action") == "monitor", "T-B-warm recommended_action=monitor")
    ck(b.get("confidence") == "medium", "T-B-warm confidence=medium")
    ck([c["id"] for c in b.get("matched_contacts") or []] == ["p-jane"],
       "T-B-warm matched p-jane via recipient")

    # Sort assertion: overdue should rank above awaiting_response.
    if len(sf) >= 2:
        ck(sf[0]["thread_id"] == "T-A-overdue",
           f"overdue ranks first in sent_followups (got {sf[0]['thread_id']})")

    # ----------------------------------------------------------------
    # Phase 2 — Step 2b two-file load_email recency merge.
    # ----------------------------------------------------------------

    import tempfile

    # Pin "now" again for deterministic classification.
    inbound_at = (now - timedelta(days=6)).isoformat()
    outbound_at = (now - timedelta(days=2)).isoformat()  # newer than inbound

    inbox_thread = {
        "thread_id": "T-X-shared",
        "subject": "Re: Harri intro",
        "last_message_at": inbound_at,
        "last_message_from": {"name": "Simin", "email": "simin@harri.example"},
        "last_message_to": [{"name": "Todd", "email": todd}],
        "snippet": "Following up.",
    }
    sent_thread = {  # same thread_id, Todd is the newer sender
        "thread_id": "T-X-shared",
        "subject": "Re: Harri intro",
        "last_message_at": outbound_at,
        "last_message_from": {"name": "Todd", "email": todd},
        "last_message_to": [{"name": "Simin", "email": "simin@harri.example"}],
        "snippet": "Circling back on the Harri opportunity.",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_inbox = Path(tmpdir)
        # Redirect rb_core's INBOX_DIR for the duration of the test so
        # load_email reads our synthetic files.
        orig_inbox = core.INBOX_DIR
        orig_email_path = core.EMAIL_PATH
        orig_email_sent_path = core.EMAIL_SENT_PATH
        orig_load_baseline = core.load_baseline
        orig_load_active_threads = core.load_active_threads
        orig_self_emails = core.self_emails
        orig_is_stale = core.is_overlay_stale
        orig_load_inbox_accounts = core.load_inbox_accounts
        try:
            core.INBOX_DIR = tmp_inbox  # type: ignore
            core.EMAIL_PATH = tmp_inbox / "email.json"  # type: ignore
            core.EMAIL_SENT_PATH = tmp_inbox / "email_sent.json"  # type: ignore
            core.load_baseline = lambda: fake_baseline  # type: ignore
            core.load_active_threads = lambda: fake_active_threads  # type: ignore
            core.self_emails = lambda: {todd}  # type: ignore
            core.is_overlay_stale = lambda payload: False  # type: ignore
            # No accounts.yaml in tmpdir → load_email falls back to the
            # legacy single-file path, which is what we set above.
            core.load_inbox_accounts = lambda: []  # type: ignore

            # Write inbox-only first; verify the thread shows up as a
            # normal inbound match (from_baseline), NOT as a sent_followup.
            core.EMAIL_PATH.write_text(json.dumps({
                "fetched_at": inbound_at,
                "threads": [inbox_thread],
            }))
            overlay1 = core.email_overlay()
            ck(any(r.get("thread_id") == "T-X-shared"
                   for r in overlay1.get("from_baseline") or []
                   if False)
               # The Simin sender isn't in fake_baseline, so it lands in
               # active_thread_company_hits via the "Harri" company hint
               # instead. Either bucket is fine; what matters is it is NOT
               # in sent_followups.
               or any(r.get("thread_id") == "T-X-shared"
                      for r in overlay1.get("active_thread_company_hits") or []),
               "inbox-only: thread surfaces as inbound (not sent_followup)")
            ck(not any(s.get("thread_id") == "T-X-shared"
                       for s in overlay1.get("sent_followups") or []),
               "inbox-only: thread is NOT in sent_followups")

            # Now also write the sent file with a NEWER outbound message
            # on the same thread. Recency merge should pick the sent
            # version; the overlay should classify as sent_followup.
            core.EMAIL_SENT_PATH.write_text(json.dumps({
                "fetched_at": outbound_at,
                "threads": [sent_thread],
            }))
            overlay2 = core.email_overlay()
            sf2 = overlay2.get("sent_followups") or []
            ck(any(s.get("thread_id") == "T-X-shared" for s in sf2),
               "two-file: thread is in sent_followups when sent message is newer")
            ck(not any(r.get("thread_id") == "T-X-shared"
                       for r in overlay2.get("active_thread_company_hits") or []),
               "two-file: thread is NOT also in active_thread_company_hits")
            matched_followup = next(
                (s for s in sf2 if s.get("thread_id") == "T-X-shared"), {}
            )
            ck(matched_followup.get("sent_at") == outbound_at,
               f"two-file: sent_at uses the outbound timestamp "
               f"(got {matched_followup.get('sent_at')})")
            ck(matched_followup.get("category") == "active",
               f"two-file: classification picks up Harri active thread "
               f"(got category={matched_followup.get('category')})")

            # If the sent file were OLDER than the inbox version, the
            # inbox version should still win — sanity-check the reverse.
            core.EMAIL_PATH.write_text(json.dumps({
                "fetched_at": outbound_at,
                "threads": [{**inbox_thread, "last_message_at": outbound_at + "Z"[:0]}],
            }))
            # Swap the timestamps so inbound is now newer than sent.
            newer_inbound = (now - timedelta(days=1)).isoformat()
            core.EMAIL_PATH.write_text(json.dumps({
                "fetched_at": newer_inbound,
                "threads": [{**inbox_thread, "last_message_at": newer_inbound}],
            }))
            overlay3 = core.email_overlay()
            ck(not any(s.get("thread_id") == "T-X-shared"
                       for s in overlay3.get("sent_followups") or []),
               "two-file: when inbox version is newer, thread is NOT a sent_followup")
        finally:
            core.INBOX_DIR = orig_inbox  # type: ignore
            core.EMAIL_PATH = orig_email_path  # type: ignore
            core.EMAIL_SENT_PATH = orig_email_sent_path  # type: ignore
            core.load_baseline = orig_load_baseline  # type: ignore
            core.load_active_threads = orig_load_active_threads  # type: ignore
            core.self_emails = orig_self_emails  # type: ignore
            core.is_overlay_stale = orig_is_stale  # type: ignore
            core.load_inbox_accounts = orig_load_inbox_accounts  # type: ignore

    print(f"--- email_overlay sent_followups smoke complete: {len(failures)} failure(s) ---")
    return 1 if failures else 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("--cache", action="store_true",
                   help="Write to system/.cache/email_overlay.json")
    p.add_argument("--smoke", action="store_true",
                   help="Run in-memory regression for sent_followups (no inbox I/O).")
    args = p.parse_args()

    if args.smoke:
        return _smoke()

    overlay = core.email_overlay()
    if args.cache:
        core.write_cache("email_overlay", overlay, source="email_overlay.py")
    if args.json:
        print(json.dumps(overlay, indent=2, default=str))
        return 0
    _render_text(overlay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
