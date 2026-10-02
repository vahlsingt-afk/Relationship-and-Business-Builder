from unittest.mock import patch

from system.scripts import daily_brief


CONTACTS = [
    {"id": "sms-person", "name": "SMS Person", "signal_class": "RC", "rc_tier": "inner", "last_touch": "2026-01-01"},
    {"id": "email-person", "name": "Email Person", "signal_class": "RC", "rc_tier": "inner", "last_touch": "2026-01-01"},
    {"id": "li-person", "name": "LinkedIn Person", "signal_class": "RC", "rc_tier": "inner", "last_touch": "2026-01-01"},
]


def test_relationship_momentum_uses_all_loaded_communication_channels():
    report = {
        "today": "2026-09-16",
        "interaction": {"matched_contacts": [{"id": "sms-person", "last_interaction_at": "2026-09-15T12:00:00Z"}]},
        "email": {
            "from_baseline": [{"match": {"id": "email-person"}, "last_message_at": "Tue, 15 Sep 2026 09:00:00 -0500"}],
            "sent_followups": [],
        },
        "linkedin_messaging": {"matched_contacts": [{"id": "li-person", "last_interaction_at": "2026-09-14T12:00:00Z"}]},
        "active_threads": [],
    }
    with (
        patch.object(daily_brief.core, "load_baseline", return_value=CONTACTS),
        patch.object(daily_brief.core, "parse_loop_ledger", return_value=[]),
        patch.object(daily_brief, "_HAS_COMPLETENESS_CONTRACT", False),
        patch.object(daily_brief, "_HAS_DIRECT_COMMS_HEALTH", False),
        patch.object(daily_brief.core, "drr_score", return_value={"score": 1.0}),
    ):
        items = daily_brief._compute_relationship_momentum(report)

    by_id = {(i.get("extras") or {}).get("contact_id"): i for i in items}
    assert by_id["sms-person"]["extras"]["days_quiet"] == 1
    assert by_id["sms-person"]["extras"]["observed_sources"] == ["text/call"]
    assert by_id["email-person"]["extras"]["days_quiet"] == 1
    assert by_id["email-person"]["extras"]["observed_sources"] == ["email"]
    assert by_id["li-person"]["extras"]["days_quiet"] == 2
    assert by_id["li-person"]["extras"]["observed_sources"] == ["LinkedIn"]
    assert all((i.get("extras") or {}).get("momentum_tier") == "HOT" for i in by_id.values())
