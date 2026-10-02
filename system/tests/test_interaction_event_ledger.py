from datetime import datetime, timezone
import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import interaction_event_ledger as iel


BASELINE = [{
    "id": "josh-w", "name": "Josh Wesolowski", "email": "josh@example.com",
    "signal_class": "RC", "rc_tier": "inner", "current_company": "McDonald's",
}]


def test_cross_channel_outbound_suppresses_stale_inbound_state():
    by_name, by_email = iel._contact_index(BASELINE)
    inbound = iel._event(
        channel="linkedin", source="linkedin_notification", source_id="li1",
        at=datetime(2026, 9, 16, 14, tzinfo=timezone.utc), direction="inbound",
        person={"name": "Josh Wesolowski"}, subject="Josh messaged you", preview="Thanks",
        unread=False, by_name=by_name, by_email=by_email,
    )
    outbound = iel._event(
        channel="email", source="outlook_gui_capture", source_id="mail1",
        at=datetime(2026, 9, 16, 15, tzinfo=timezone.utc), direction="outbound",
        person={"name": "Josh Wesolowski"}, subject="Follow-up", preview=None,
        unread=False, by_name=by_name, by_email=by_email,
    )
    states = iel._current_states([inbound, outbound], BASELINE, datetime(2026, 9, 17, tzinfo=timezone.utc))
    assert states[0]["state"] == "awaiting_them"
    assert states[0]["channels_seen"] == ["email", "linkedin"]
    assert states[0]["priority_class"] == "inner_circle"


def test_linkedin_export_recognizes_todds_hyphenated_profile_as_self():
    by_name, by_email = iel._contact_index(BASELINE)
    payload = {"messages": [{
        "conversation_id": "c1", "date": "2026-09-16T15:00:00Z",
        "direction": "inbound",
        "from": {"name": "Todd Vahlsing", "profile_url": "https://www.linkedin.com/in/todd-vahlsing", "is_self": False},
        "to": [{"name": "Josh Wesolowski"}], "content": "Checking in",
    }]}
    events = iel._linkedin_events(payload, by_name, by_email)
    assert len(events) == 1
    assert events[0]["direction"] == "outbound"
    assert events[0]["contact_id"] == "josh-w"


def test_future_calendar_event_sets_scheduled_state():
    by_name, by_email = iel._contact_index(BASELINE)
    inbound = iel._event(
        channel="linkedin", source="test", source_id="li2",
        at=datetime(2026, 9, 16, 14, tzinfo=timezone.utc), direction="inbound",
        person={"name": "Josh Wesolowski"}, subject=None, preview="Meet?", unread=False,
        by_name=by_name, by_email=by_email,
    )
    meeting = iel._event(
        channel="calendar", source="test", source_id="cal1",
        at=datetime(2026, 9, 18, 14, tzinfo=timezone.utc), direction="scheduled",
        person={"email": "josh@example.com"}, subject="Catch-up", preview=None, unread=None,
        by_name=by_name, by_email=by_email,
    )
    states = iel._current_states([inbound, meeting], BASELINE, datetime(2026, 9, 17, tzinfo=timezone.utc))
    assert states[0]["state"] == "scheduled"


def test_outlook_last_first_formal_name_matches_common_baseline_name():
    by_name, by_email = iel._contact_index(BASELINE)
    contact_id, name, confidence = iel._resolve(
        {"name": "Wesolowski, Joshua (Global-US)"}, by_name, by_email
    )
    assert contact_id == "josh-w"
    assert name == "Josh Wesolowski"
    assert confidence == "medium"


def test_repeated_zero_becomes_warning_after_prior_activity(tmp_path, monkeypatch):
    health_path = tmp_path / "health.json"
    monkeypatch.setattr(iel, "HEALTH_PATH", health_path)
    health_path.write_text(json.dumps({"history": [
        {"run_at": "2026-09-13T12:00:00Z", "counts": {"email:gp": 5}},
        {"run_at": "2026-09-14T12:00:00Z", "counts": {"email:gp": 0}},
        {"run_at": "2026-09-15T12:00:00Z", "counts": {"email:gp": 0}},
    ]}))
    result = iel._health(
        {"email:gp": 0}, {"email:gp": "2026-09-16T12:00:00Z"},
        datetime(2026, 9, 16, 13, tzinfo=timezone.utc),
    )
    assert result["sources"]["email:gp"]["status"] == "warning"
    assert result["sources"]["email:gp"]["zero_streak"] == 3
