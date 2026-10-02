#!/usr/bin/env python3
"""
ri_smoke_test.py — end-to-end smoke test of the RI event intake stack.

Exercises the full pipeline from POST /ri_events/review through
GET /ri_events/recent and POST /ri_events/{id}/confirm via FastAPI's
TestClient. Designed to be run as part of `api_smoke_test.py` once the
RI surface is stable, but lives as a separate script today so it can be
iterated on independently.

Canonical fixtures from the two traces that motivated this sprint:
  - PerfectHire / Fathom transcript (T-2026-05-19-005)
  - Ryan Hildebrand interview "yesterday" (T-2026-05-19-006)
  - Simin Gorgulu / TritonExec / Hari recruiter screen (T-2026-05-19-006)

Plus negative + ancillary fixtures:
  - quiet-chat ordinary message (does not write an event)
  - email_paste with full RFC 5322 headers
  - linkedin_screenshot with visible date

The script uses dry_run=True on every confirm call so canonical state
(baseline / threads / loops / briefs) is never mutated. This is enforced
by the feedback-memory rule on smoke tests touching mutations.py.

Run:

    pip install fastapi httpx --break-system-packages   # if not already
    python3 system/scripts/ri_smoke_test.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import shutil
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR.parent / "api"))

try:
    from fastapi.testclient import TestClient  # noqa: E402
except ImportError:
    sys.stderr.write(
        "fastapi not installed. Install with:\n"
        "  pip install fastapi httpx --break-system-packages\n"
    )
    sys.exit(2)

# Import the running app + the event store so we can isolate writes.
import ri_events  # noqa: E402
import ri_intake  # noqa: E402
from server import app  # noqa: E402

client = TestClient(app)


def main() -> int:
    failures = 0

    def expect(label: str, cond: bool, detail: str = "") -> None:
        nonlocal failures
        if cond:
            print(f"  OK   {label}")
        else:
            failures += 1
            print(f"  FAIL {label}  {detail}")

    # Isolate the event store and pending cache to a tmpdir so this
    # smoke test does not pollute system/ri_events/ or
    # system/.cache/ri_events_pending.json on the operator's machine.
    tmp = Path(tempfile.mkdtemp(prefix="ri_smoke_"))
    orig_events_dir = ri_events.EVENTS_DIR
    orig_index = ri_events.INDEX_PATH
    orig_pending = ri_intake.PENDING_PATH
    ri_events.EVENTS_DIR = tmp / "ri_events"
    ri_events.INDEX_PATH = tmp / "ri_events_index.json"
    ri_intake.PENDING_PATH = tmp / "ri_events_pending.json"

    try:
        # ------------------------------------------------------------
        # Fixture 1 — PerfectHire / Fathom transcript pasted next-day.
        # event_at must anchor to the meeting date 2026-05-19, NOT to
        # the captured_at 2026-05-20.
        # ------------------------------------------------------------
        ph_transcript = (
            "Olivia Nielsen: Thanks for joining today. Let me kick off "
            "with our QSR scheduling problem.\n"
            "Todd: Sure, happy to walk through what I learned.\n"
            "Max Holmes: One thing I have been asking myself is how the "
            "POS integration plays into your retention story.\n"
            "Olivia Nielsen: That is the right question. I think Matt "
            "would love to talk through that with you.\n"
            "Todd: Looking forward to that intro.\n"
        )
        r = client.post("/ri_events/review", json={
            "source_type": "fathom_manual_paste",
            "raw_text": ph_transcript,
            "captured_at": "2026-05-20T09:00:00-05:00",
            "source_ref": {
                "title": "Todd <> PerfectHire - QSR Platform Review - May 19",
                "id": "fathom://share/smoke-test-perfecthire",
            },
            "trace_id": "T-2026-05-19-005",
        })
        expect("PerfectHire: HTTP 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        body = r.json()
        expect("PerfectHire: ri_scan=found", body.get("ri_scan") == "found")
        ph_event = body.get("event") or {}
        expect("PerfectHire: event_at anchored to meeting date 2026-05-19",
               (ph_event.get("event_at") or "")[:10] == "2026-05-19",
               f"got {ph_event.get('event_at')!r}")
        expect("PerfectHire: event_at_confidence=high",
               ph_event.get("event_at_confidence") == "high")
        ph_people = [p.get("raw") for p in (ph_event.get("entities") or {}).get("people") or []]
        expect("PerfectHire: Olivia Nielsen extracted",
               "Olivia Nielsen" in ph_people)
        expect("PerfectHire: Max Holmes extracted",
               "Max Holmes" in ph_people)
        ph_event_id = body.get("event_id")

        # Confirm in dry_run mode — exercises the full confirm path
        # without writing to baseline/threads/loops/briefs.
        confirm_required = body.get("confirmation_required_for") or []
        r2 = client.post(
            f"/ri_events/{ph_event_id}/confirm",
            json={"accept": confirm_required, "reject": [], "dry_run": True},
        )
        expect("PerfectHire confirm: HTTP 200", r2.status_code == 200)
        c_body = r2.json()
        expect("PerfectHire confirm: persistence_status=dry_run",
               c_body.get("persistence_status") == "dry_run")
        expect("PerfectHire confirm: no follow_up_event_id (dry_run)",
               c_body.get("follow_up_event_id") is None)

        # ------------------------------------------------------------
        # Fixture 2 — Ryan Hildebrand interview "yesterday".
        # ------------------------------------------------------------
        r = client.post("/ri_events/review", json={
            "source_type": "recruiting_update",
            "raw_text": (
                "The interview with Ryan Hildebrand from Global Payments "
                "went well yesterday. He was at NRA and wants to have "
                "another conversation either this week or next."
            ),
            "name": "Ryan Hildebrand",
            "organization": "Global Payments",
            "captured_at": "2026-05-19T13:30:00-05:00",
            "trace_id": "T-2026-05-19-006",
        })
        expect("Ryan: HTTP 200", r.status_code == 200)
        body = r.json()
        ev = body.get("event") or {}
        expect("Ryan: event_at=2026-05-18", (ev.get("event_at") or "") == "2026-05-18")
        expect("Ryan: event_at_confidence=medium",
               ev.get("event_at_confidence") == "medium")
        subtypes = (ev.get("signal") or {}).get("subtypes") or []
        expect("Ryan: interview_completed in signal subtypes",
               "interview_completed" in subtypes)
        expect("Ryan: second_conversation_requested in signal subtypes",
               "second_conversation_requested" in subtypes)

        # ------------------------------------------------------------
        # Fixture 3 — Simin / TritonExec / Hari recruiter screen.
        # "Friday" must resolve to 2026-05-15.
        # ------------------------------------------------------------
        r = client.post("/ri_events/review", json={
            "source_type": "recruiting_update",
            "raw_text": (
                "I have not heard back from the headhunter I talked to "
                "on Friday. This was for a job with Hari managing the "
                "McDonalds account. I would be very interested in it."
            ),
            "name": "Simin Gorgulu",
            "organization": "TritonExec",
            "opportunity": "Hari McDonald's account role",
            "captured_at": "2026-05-19T14:00:00-05:00",
            "trace_id": "T-2026-05-19-006",
        })
        expect("Simin: HTTP 200", r.status_code == 200)
        body = r.json()
        ev = body.get("event") or {}
        expect("Simin: event_at=2026-05-15", (ev.get("event_at") or "") == "2026-05-15")
        subtypes = (ev.get("signal") or {}).get("subtypes") or []
        expect("Simin: recruiter_screen_completed in subtypes",
               "recruiter_screen_completed" in subtypes)
        expect("Simin: waiting_on_recruiter in subtypes",
               "waiting_on_recruiter" in subtypes)
        expect("Simin: high_interest_role in subtypes",
               "high_interest_role" in subtypes)

        # ------------------------------------------------------------
        # Fixture 4 — email_paste with explicit Date header.
        # ------------------------------------------------------------
        r = client.post("/ri_events/review", json={
            "source_type": "email_paste",
            "raw_text": (
                "From: Ryan Hildebrand <ryan@globalpayments.com>\n"
                "To: Todd Vahlsing <vahlsingt@gmail.com>\n"
                "Subject: Re: Director conversation next steps\n"
                "Date: Mon, 18 May 2026 14:32:10 -0500\n"
                "Message-ID: <CAH9876xyz@mail.gmail.com>\n"
                "\n"
                "Hi Todd, great talking yesterday. Let me know your "
                "availability next week for another conversation.\n"
                "Ryan\n"
            ),
            "captured_at": "2026-05-19T13:00:00-05:00",
            "trace_id": "T-2026-05-19-006",
        })
        expect("email: HTTP 200", r.status_code == 200)
        body = r.json()
        ev = body.get("event") or {}
        expect("email: event_at anchored to Date header (2026-05-18)",
               (ev.get("event_at") or "").startswith("2026-05-18"))
        src = ev.get("source") or {}
        expect("email: message_id captured",
               src.get("message_id") == "CAH9876xyz@mail.gmail.com")

        # ------------------------------------------------------------
        # Fixture 5 — linkedin_screenshot with visible date.
        # ------------------------------------------------------------
        r = client.post("/ri_events/review", json={
            "source_type": "linkedin_screenshot",
            "raw_text": (
                "Conversation with Matt Chalzi\n"
                "May 17\n"
                "\n"
                "Matt Chalzi · 1st\n"
                "CEO at PerfectHire\n"
                "\n"
                "Matt: Olivia tells me you have strong views on QSR "
                "scheduling. Would love a quick intro chat.\n"
                "Send message  View profile\n"
            ),
            "captured_at": "2026-05-19T13:30:00-05:00",
            "trace_id": "T-2026-05-19-005",
        })
        expect("linkedin: HTTP 200", r.status_code == 200)
        body = r.json()
        ev = body.get("event") or {}
        expect("linkedin: event_at=2026-05-17", (ev.get("event_at") or "")[:10] == "2026-05-17")
        people = [p.get("raw") for p in (ev.get("entities") or {}).get("people") or []]
        expect("linkedin: Matt Chalzi extracted",
               any("matt chalzi" in (p or "").lower() for p in people))

        # ------------------------------------------------------------
        # Fixture 6 — quiet-chat negative paths.
        # ------------------------------------------------------------
        for chat_text in (
            "what is on my plate today?",
            "regenerate today.md",
            "draft me an email to Ryan",
        ):
            r = client.post("/ri_events/review", json={
                "source_type": "manual_text",
                "raw_text": chat_text,
                "captured_at": "2026-05-19T14:00:00-05:00",
            })
            expect(f"quiet-scan: HTTP 200 for {chat_text!r}", r.status_code == 200)
            body = r.json()
            expect(f"quiet-scan: not_persisted for {chat_text!r}",
                   body.get("persistence_status") == "not_persisted"
                   and body.get("event") is None)

        # ------------------------------------------------------------
        # Fixture 7 — GET /ri_events/recent returns the events we wrote.
        # ------------------------------------------------------------
        r = client.get("/ri_events/recent", params={"limit": 50})
        expect("recent: HTTP 200", r.status_code == 200)
        body = r.json()
        expect("recent: list of events returned",
               isinstance(body.get("events"), list) and body.get("count", 0) >= 5,
               f"got count={body.get('count')}")
        recent_types = {(e.get("source") or {}).get("type") for e in body.get("events") or []}
        for t in ("fathom_manual_paste", "recruiting_update", "email_paste", "linkedin_screenshot"):
            expect(f"recent: includes source_type={t}", t in recent_types)

    finally:
        ri_events.EVENTS_DIR = orig_events_dir
        ri_events.INDEX_PATH = orig_index
        ri_intake.PENDING_PATH = orig_pending
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"--- ri_smoke_test complete: {failures} failure(s) ---")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
