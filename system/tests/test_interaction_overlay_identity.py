"""
test_interaction_overlay_identity.py

RB-DEFECT-061: Amy Spytko had SMS/call activity with Todd (a phone call the
week before 2026-07-27, active texting) but rb_core.interaction_overlay()
never matched it to her baseline record because her `phone` field was null
-- her only identity anchor was `email`, and Messages/Calls handles are
phone numbers, not emails. last_touch, communication_frequency_90d, and the
daily brief all stayed stale/COLD as a result, even though the raw evidence
was sitting in system/inbox/messages.json and calls.json the whole time.

Fixed by populating the missing phone via the existing
`mutations.py contact-update --phone` path (identity-resolution mutation,
not a new matcher). These tests lock in the underlying matching behavior so
a baseline contact with only email on file, plus a recurring raw SMS/call
handle, doesn't silently fail to surface again:

RB-INTOVERLAY-001: email-only contact (no phone) -- recurring raw handle
    stays unmatched, contact absent from matched_contacts, no last_touch
    proposal is generated for them.
RB-INTOVERLAY-002: same contact after a phone is merged onto baseline --
    the same raw handle now resolves, message/call counts land on the
    contact, and a last_touch update is proposed from the newest event.
RB-INTOVERLAY-003: baseline phone and raw handle use different formatting
    (dashed 10-digit vs. E.164) -- normalization still matches them.
RB-INTOVERLAY-004: a handle Todd has explicitly marked personal/private via
    sms_exempt_manager.py (system/sms_trusted_senders.json) does not appear
    in unmatched_recurring_handles, even with no baseline record at all.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import rb_core as core  # noqa: E402

_TODAY = date(2026, 7, 27)
_HANDLE_E164 = "+13153278603"
_HANDLE_DASHED = "315-327-8603"


def _baseline(phone: str | None) -> list[dict]:
    return [{
        "id": "amy-spytko",
        "name": "Amy Spytko",
        "email": "amy.spytko@gmail.com",
        "phone": phone,
        "signal_class": "RC",
        "rc_tier": "inner",
        "rc_state": "ACTIVE",
        "last_touch": "2026-01-24",
    }]


def _write_inbox(tmp_path: Path, handle: str) -> None:
    messages = {
        "fetched_at": "2026-07-27T10:02:25+00:00",
        "events": [
            {"id": "msg-1", "handle": handle, "service": "iMessage",
             "direction": "outbound", "at": "2026-07-20T12:00:00+00:00"},
            {"id": "msg-2", "handle": handle, "service": "iMessage",
             "direction": "inbound", "at": "2026-07-22T09:00:00+00:00"},
            {"id": "msg-3", "handle": handle, "service": "SMS",
             "direction": "outbound", "at": "2026-07-24T19:00:00+00:00"},
        ],
    }
    calls = {
        "fetched_at": "2026-07-27T10:02:26+00:00",
        "events": [
            {"id": "call-1", "handle": handle, "service": "Phone",
             "direction": "outbound", "at": "2026-07-24T19:11:48+00:00",
             "duration_seconds": 400},
        ],
    }
    (tmp_path / "messages.json").write_text(json.dumps(messages))
    (tmp_path / "calls.json").write_text(json.dumps(calls))


def _run_overlay(tmp_path: Path, baseline: list[dict]) -> dict:
    with patch.object(core, "MESSAGES_PATH", tmp_path / "messages.json"), \
         patch.object(core, "CALLS_PATH", tmp_path / "calls.json"):
        return core.interaction_overlay(baseline=baseline, today=_TODAY, recent_days=30)


def test_email_only_contact_leaves_recurring_handle_unmatched(tmp_path):
    """RB-INTOVERLAY-001."""
    _write_inbox(tmp_path, _HANDLE_E164)
    overlay = _run_overlay(tmp_path, _baseline(phone=None))

    assert overlay["matched_contacts"] == []
    assert not any(u["contact_id"] == "amy-spytko"
                   for u in overlay["proposed_last_touch_updates"])
    unmatched_handles = {u["handle"] for u in overlay["unmatched_recurring_handles"]}
    assert _HANDLE_E164 in unmatched_handles


def test_phone_merge_resolves_the_same_recurring_handle(tmp_path):
    """RB-INTOVERLAY-002."""
    _write_inbox(tmp_path, _HANDLE_E164)
    overlay = _run_overlay(tmp_path, _baseline(phone=_HANDLE_E164))

    assert len(overlay["matched_contacts"]) == 1
    contact = overlay["matched_contacts"][0]
    assert contact["id"] == "amy-spytko"
    assert contact["messages_in"] == 1
    assert contact["messages_out"] == 2
    assert contact["calls_out"] == 1
    assert contact["last_interaction_at"] == "2026-07-24T19:11:48+00:00"

    updates = {u["contact_id"]: u for u in overlay["proposed_last_touch_updates"]}
    assert updates["amy-spytko"]["proposed_last_touch"] == "2026-07-24"
    assert updates["amy-spytko"]["current_last_touch"] == "2026-01-24"


def test_differing_phone_formatting_still_matches(tmp_path):
    """RB-INTOVERLAY-003."""
    _write_inbox(tmp_path, _HANDLE_E164)
    overlay = _run_overlay(tmp_path, _baseline(phone=_HANDLE_DASHED))

    assert len(overlay["matched_contacts"]) == 1
    assert overlay["matched_contacts"][0]["id"] == "amy-spytko"


def test_exempt_handle_never_surfaces_as_unmatched(tmp_path):
    """RB-INTOVERLAY-004."""
    exempt_path = tmp_path / "sms_trusted_senders.json"
    exempt_path.write_text(json.dumps({
        "mode": "all", "trusted_handles": [], "exempt_handles": ["3153278603"],
    }))
    _write_inbox(tmp_path, _HANDLE_E164)

    with patch.object(core, "MESSAGES_PATH", tmp_path / "messages.json"), \
         patch.object(core, "CALLS_PATH", tmp_path / "calls.json"), \
         patch.object(core, "SMS_EXEMPT_HANDLES_PATH", exempt_path):
        overlay = core.interaction_overlay(baseline=_baseline(phone=None),
                                            today=_TODAY, recent_days=30)

    assert overlay["matched_contacts"] == []
    unmatched_handles = {u["handle"] for u in overlay["unmatched_recurring_handles"]}
    assert _HANDLE_E164 not in unmatched_handles
