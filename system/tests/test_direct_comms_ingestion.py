"""
test_direct_comms_ingestion.py — RB-DEFECT-004 direct comms health and passive RI ingestion tests.

Tests the five-state readiness model, contact matching, urgency signal detection,
completeness caveat generation, and brief integration contract.

Privacy constraints enforced by fixtures:
  - No real message content in test fixtures.
  - Snippet content uses synthetic placeholder text.
  - Phone numbers are fabricated (555-series).
"""
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "system" / "scripts" / "direct_comms_health.py"

spec = importlib.util.spec_from_file_location("direct_comms_health", SCRIPT)
dch = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(dch)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NOW_ISO = datetime.now(tz=timezone.utc).isoformat()
STALE_ISO = (datetime.now(tz=timezone.utc) - timedelta(hours=60)).isoformat()

FRESH_MESSAGES_INBOX = {
    "fetched_at": NOW_ISO,
    "source": "apple_messages",
    "window_days": 7,
    "include_snippets": True,
    "events": [
        {
            "handle": "+15551234567",
            "service": "iMessage",
            "direction": "inbound",
            "date": "2026-05-27T08:00:00",
            "snippet": "Hey, are you free to connect this week?",
        }
    ],
}

STALE_MESSAGES_INBOX = {
    "fetched_at": STALE_ISO,
    "source": "apple_messages",
    "window_days": 7,
    "include_snippets": False,
    "events": [],
}

METADATA_ONLY_MESSAGES_INBOX = {
    "fetched_at": NOW_ISO,
    "source": "apple_messages",
    "window_days": 7,
    "include_snippets": False,
    "events": [
        {
            "handle": "+15551234567",
            "service": "SMS",
            "direction": "inbound",
            "date": "2026-05-27T08:00:00",
            "snippet": None,
        }
    ],
}

FRESH_CALLS_INBOX = {
    "fetched_at": NOW_ISO,
    "source": "apple_calls",
    "window_days": 7,
    "calls": [
        {
            "handle": "+15559876543",
            "direction": "inbound",
            "answered": False,
            "duration": 0,
            "date": "2026-05-27T07:30:00",
        }
    ],
}

SAMPLE_BASELINE = [
    {
        "id": "contact-alice",
        "name": "Alice Smith",
        "phones": ["+15551234567"],
        "email": "alice@example.com",
    },
    {
        "id": "contact-bob",
        "name": "Bob Jones",
        "phones": ["+15559876543"],
        "email": "bob@example.com",
    },
]


# ---------------------------------------------------------------------------
# Helper: write an inbox file to a tmp dir and patch MESSAGES_PATH/CALLS_PATH
# ---------------------------------------------------------------------------
class _TmpInbox:
    """Context manager that writes inbox files to a temp dir and patches paths."""
    def __init__(self, messages_data=None, calls_data=None):
        self._messages_data = messages_data
        self._calls_data = calls_data
        self._tmpdir = None
        self._patches = []

    def __enter__(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        msg_path = tmp / "messages.json"
        calls_path = tmp / "calls.json"
        if self._messages_data is not None:
            msg_path.write_text(json.dumps(self._messages_data))
        if self._calls_data is not None:
            calls_path.write_text(json.dumps(self._calls_data))
        # Patch module-level path constants
        self._patches = [
            patch.object(dch, "MESSAGES_PATH", msg_path),
            patch.object(dch, "CALLS_PATH", calls_path),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *args):
        for p in self._patches:
            p.stop()
        self._tmpdir.cleanup()


# ---------------------------------------------------------------------------
# Readiness state tests
# ---------------------------------------------------------------------------
class MessagesReadinessStateTests(unittest.TestCase):
    def test_unavailable_when_no_inbox_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = Path(tmp) / "messages.json"
            with patch.object(dch, "MESSAGES_PATH", missing_path), \
                 patch.object(dch, "_full_disk_access_available", return_value=False):
                result = dch.check_messages_readiness([])
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["event_count"], 0)

    def test_available_stale_when_inbox_old(self):
        with _TmpInbox(messages_data=STALE_MESSAGES_INBOX):
            with patch.object(dch, "_full_disk_access_available", return_value=True):
                result = dch.check_messages_readiness([])
        self.assertEqual(result["state"], "available_stale")

    def test_available_metadata_only_when_no_snippets(self):
        with _TmpInbox(messages_data=METADATA_ONLY_MESSAGES_INBOX):
            with patch.object(dch, "_full_disk_access_available", return_value=True):
                result = dch.check_messages_readiness([])
        self.assertEqual(result["state"], "available_metadata_only")
        self.assertFalse(result["snippet_available"])

    def test_available_with_snippets_or_fresh_when_recent_and_has_snippets(self):
        with _TmpInbox(messages_data=FRESH_MESSAGES_INBOX):
            with patch.object(dch, "_full_disk_access_available", return_value=True):
                result = dch.check_messages_readiness([])
        self.assertIn(result["state"], ("available_with_snippets", "available_fresh"))
        self.assertTrue(result["snippet_available"])


class CallsReadinessStateTests(unittest.TestCase):
    def test_unavailable_when_no_calls_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = Path(tmp) / "calls.json"
            with patch.object(dch, "CALLS_PATH", missing_path), \
                 patch.object(dch, "_full_disk_access_available", return_value=False):
                result = dch.check_calls_readiness([])
        self.assertEqual(result["state"], "unavailable")

    def test_available_fresh_for_recent_calls(self):
        with _TmpInbox(calls_data=FRESH_CALLS_INBOX):
            with patch.object(dch, "_full_disk_access_available", return_value=True):
                result = dch.check_calls_readiness([])
        self.assertIn(result["state"], ("available_fresh", "available_with_snippets"))


# ---------------------------------------------------------------------------
# Contact matching tests
# ---------------------------------------------------------------------------
class ContactMatchingTests(unittest.TestCase):
    def test_message_event_matches_known_contact(self):
        events = [
            {
                "handle": "+15551234567",
                "service": "iMessage",
                "direction": "inbound",
                "date": "2026-05-27T08:00:00",
                "snippet": "Hello",
            }
        ]
        candidates = dch._match_events_to_contacts(events, SAMPLE_BASELINE)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["contact_id"], "contact-alice")
        self.assertEqual(candidates[0]["channel"], "imessage")
        self.assertEqual(candidates[0]["direction"], "inbound")
        self.assertTrue(candidates[0]["snippet_available"])

    def test_unmatched_message_event_has_null_contact_id_and_block_reason(self):
        events = [
            {
                "handle": "+19990000000",
                "service": "SMS",
                "direction": "inbound",
                "date": "2026-05-27T08:00:00",
                "snippet": None,
            }
        ]
        candidates = dch._match_events_to_contacts(events, SAMPLE_BASELINE)
        self.assertEqual(len(candidates), 1)
        self.assertIsNone(candidates[0]["contact_id"])
        self.assertEqual(candidates[0]["block_reason"], "unmatched_handle")

    def test_exempted_handle_is_not_counted_as_unmatched(self):
        """RB-DEFECT-061 follow-up: sms_exempt_manager.py's exempt_handles
        (family/personal numbers Todd has explicitly registered) must not
        keep surfacing in the SMS Contact Resolution Queue -- the queue's
        own recommended_action tells Todd to resolve gaps that way, so the
        matcher has to actually honor it."""
        with tempfile.TemporaryDirectory() as td:
            exempt_path = Path(td) / "sms_trusted_senders.json"
            exempt_path.write_text(json.dumps({
                "mode": "all", "trusted_handles": [],
                "exempt_handles": ["9165551212"],
            }))
            with patch.object(dch.core, "SMS_EXEMPT_HANDLES_PATH", exempt_path):
                events = [
                    {"handle": "+19165551212", "service": "SMS",
                     "direction": "inbound", "date": "2026-05-27T08:00:00",
                     "snippet": None},
                    {"handle": "+19165551212", "service": "SMS",
                     "direction": "outbound", "date": "2026-05-27T09:00:00",
                     "snippet": None},
                ]
                candidates = dch._match_events_to_contacts(events, SAMPLE_BASELINE)
                self.assertTrue(
                    all(c["block_reason"] != "unmatched_handle" for c in candidates))
                self.assertEqual(candidates[0]["block_reason"], "exempted_personal")
                unmatched_handles = candidates[0].get("_unmatched_handles", [])
                self.assertFalse(
                    any(h["handle"] == "+19165551212" for h in unmatched_handles))

    def test_missed_call_from_known_contact_triggers_urgency(self):
        events = [
            {
                "handle": "+15559876543",
                "direction": "inbound",
                "answered": False,
                "duration": 0,
                "date": "2026-05-27T07:30:00",
            }
        ]
        candidates = dch._match_calls_to_contacts(events, SAMPLE_BASELINE)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["contact_id"], "contact-bob")
        self.assertTrue(candidates[0]["urgency_signal"])
        self.assertTrue(candidates[0]["missed"])
        self.assertEqual(candidates[0]["proposed_mutation"], "log_missed_call_touch")

    def test_answered_call_does_not_trigger_urgency(self):
        events = [
            {
                "handle": "+15559876543",
                "direction": "inbound",
                "answered": True,
                "duration": 300,
                "date": "2026-05-27T07:30:00",
            }
        ]
        candidates = dch._match_calls_to_contacts(events, SAMPLE_BASELINE)
        self.assertFalse(candidates[0]["urgency_signal"])


# ---------------------------------------------------------------------------
# Brief items and completeness caveat
# ---------------------------------------------------------------------------
class BriefItemsTests(unittest.TestCase):
    def test_build_brief_items_returns_list(self):
        with _TmpInbox(messages_data=FRESH_MESSAGES_INBOX, calls_data=FRESH_CALLS_INBOX):
            with patch.object(dch, "_full_disk_access_available", return_value=True):
                items = dch.build_direct_comms_brief_items(SAMPLE_BASELINE)
        self.assertIsInstance(items, list)
        self.assertGreater(len(items), 0)

    def test_urgency_item_present_for_missed_call(self):
        with _TmpInbox(calls_data=FRESH_CALLS_INBOX):
            with patch.object(dch, "_full_disk_access_available", return_value=True), \
                 patch.object(dch, "MESSAGES_PATH", Path("/nonexistent/messages.json")):
                items = dch.build_direct_comms_brief_items(SAMPLE_BASELINE)
        urgency_items = [i for i in items if i.get("state") == "urgency_signal"]
        self.assertGreater(len(urgency_items), 0, "Expected urgency signal item for missed call from known contact")
        self.assertEqual(urgency_items[0]["contact_id"], "contact-bob")

    def test_completeness_claim_safe_for_fresh_source(self):
        with _TmpInbox(messages_data=FRESH_MESSAGES_INBOX, calls_data=FRESH_CALLS_INBOX):
            with patch.object(dch, "_full_disk_access_available", return_value=True):
                items = dch.build_direct_comms_brief_items(SAMPLE_BASELINE)
        source_items = [i for i in items if i.get("source") in ("messages", "calls")
                        and i.get("state") != "urgency_signal"]
        for item in source_items:
            if item["state"] in ("available_fresh", "available_with_snippets"):
                self.assertTrue(item["completeness_claim_safe"],
                                f"Fresh source should have completeness_claim_safe=True: {item}")


class CompletenessCaveatTests(unittest.TestCase):
    def test_no_caveat_when_both_sources_fresh(self):
        items = [
            {"source": "messages", "state": "available_fresh", "completeness_claim_safe": True},
            {"source": "calls", "state": "available_fresh", "completeness_claim_safe": True},
        ]
        caveat = dch.get_completeness_caveat(items)
        self.assertIsNone(caveat)

    def test_caveat_when_messages_unavailable(self):
        items = [
            {"source": "messages", "state": "unavailable", "completeness_claim_safe": False},
            {"source": "calls", "state": "available_fresh", "completeness_claim_safe": True},
        ]
        caveat = dch.get_completeness_caveat(items)
        self.assertIsNotNone(caveat)
        self.assertIn("Apple Messages", caveat)
        self.assertIn("unavailable", caveat)

    def test_caveat_when_messages_stale(self):
        items = [
            {"source": "messages", "state": "available_stale",
             "completeness_claim_safe": False, "last_read": "2026-05-25T08:00:00"},
            {"source": "calls", "state": "available_fresh", "completeness_claim_safe": True},
        ]
        caveat = dch.get_completeness_caveat(items)
        self.assertIsNotNone(caveat)
        self.assertIn("stale", caveat)

    def test_caveat_includes_cannot_confirm_language(self):
        items = [
            {"source": "calls", "state": "unavailable", "completeness_claim_safe": False},
        ]
        caveat = dch.get_completeness_caveat(items)
        self.assertIsNotNone(caveat)
        self.assertIn("cannot be confirmed", caveat)


# ---------------------------------------------------------------------------
# Brief language template coverage
# ---------------------------------------------------------------------------
class BriefLanguageTests(unittest.TestCase):
    def test_all_five_states_have_brief_language(self):
        states = dch.READINESS_STATES
        self.assertEqual(len(states), 5)
        for state in states:
            if state in dch.BRIEF_LANGUAGE:
                # Format with dummy values to ensure template is valid
                lang = dch.BRIEF_LANGUAGE[state].format(
                    source_label="Messages",
                    channel_label="SMS/iMessage",
                    last_read="2026-05-25T08:00:00",
                    event_count=5,
                    matched_count=2,
                    urgency_count=1,
                    recovery_command="python3 system/scripts/fetch_apple_messages.py",
                    recovery_command_snippets="python3 system/scripts/fetch_apple_messages.py --include-snippets",
                )
                self.assertIsInstance(lang, str)
                self.assertGreater(len(lang), 10)


# ---------------------------------------------------------------------------
# Channel escalation and communication failure signal classification
# Tests the regex patterns in relationship_signals.py that classify direct
# comms snippets as multi_channel_escalation or communication_failure_risk.
# RB-DEFECT-003 / RB-DEFECT-004 acceptance requirement.
# ---------------------------------------------------------------------------
import importlib.util as _ilu

_RS_SCRIPT = ROOT / "system" / "scripts" / "relationship_signals.py"
_rs_spec = _ilu.spec_from_file_location("relationship_signals", _RS_SCRIPT)
rs = _ilu.module_from_spec(_rs_spec)
assert _rs_spec.loader is not None
_rs_spec.loader.exec_module(rs)


class ChannelEscalationSignalTests(unittest.TestCase):
    """Verify regex patterns and signal-type derivation for escalation language."""

    # --- COMMUNICATION_FAILURE_RX ---

    def test_email_bounced_matches_communication_failure(self):
        phrases = [
            "email bounced",
            "emails bounced back",
            "delivery failure",
            "undeliverable",
            "DNS issue on your domain",
            "MX record problem",
            "couldn't reach you by email",
            "tried to email but delivery status shows failed",
        ]
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertTrue(
                    bool(rs.COMMUNICATION_FAILURE_RX.search(phrase)),
                    f"Expected COMMUNICATION_FAILURE_RX to match: {phrase!r}",
                )

    def test_neutral_message_does_not_match_communication_failure(self):
        phrases = [
            "Just following up on our last conversation",
            "Looking forward to connecting",
            "Great meeting you at the conference",
        ]
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertFalse(
                    bool(rs.COMMUNICATION_FAILURE_RX.search(phrase)),
                    f"Expected COMMUNICATION_FAILURE_RX NOT to match: {phrase!r}",
                )

    # --- CHANNEL_ESCALATION_RX ---

    def test_channel_switch_phrases_match_escalation(self):
        phrases = [
            "texted you since email wasn't working",
            "reaching out via text",
            "tracked you down on iMessage",
            "switched channels to SMS",
            "reached out by phone",
        ]
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertTrue(
                    bool(rs.CHANNEL_ESCALATION_RX.search(phrase)),
                    f"Expected CHANNEL_ESCALATION_RX to match: {phrase!r}",
                )

    def test_neutral_message_does_not_match_escalation(self):
        phrases = [
            "Hey, quick update on the project",
            "Can we get on a call this week?",
            "Thanks for the introduction",
        ]
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertFalse(
                    bool(rs.CHANNEL_ESCALATION_RX.search(phrase)),
                    f"Expected CHANNEL_ESCALATION_RX NOT to match: {phrase!r}",
                )

    # --- Signal-type derivation logic ---

    def test_communication_failure_signal_type_priority_over_escalation(self):
        """communication_failure_risk takes priority over multi_channel_escalation
        when both patterns match — mirrors the if/elif in _interaction_signals."""
        snippet = "email bounced, texted you instead"
        comm_fail = bool(rs.COMMUNICATION_FAILURE_RX.search(snippet))
        channel_escal = bool(rs.CHANNEL_ESCALATION_RX.search(snippet))
        signal_type = (
            "communication_failure_risk" if comm_fail else
            "multi_channel_escalation" if channel_escal else
            "direct_interaction"
        )
        self.assertEqual(signal_type, "communication_failure_risk")

    def test_escalation_only_snippet_produces_multi_channel_escalation(self):
        """Pure channel-switch language (no email failure) → multi_channel_escalation."""
        snippet = "switched channels, texting you here"
        comm_fail = bool(rs.COMMUNICATION_FAILURE_RX.search(snippet))
        channel_escal = bool(rs.CHANNEL_ESCALATION_RX.search(snippet))
        signal_type = (
            "communication_failure_risk" if comm_fail else
            "multi_channel_escalation" if channel_escal else
            "direct_interaction"
        )
        self.assertEqual(signal_type, "multi_channel_escalation")

    def test_neutral_snippet_produces_direct_interaction(self):
        snippet = "Looking forward to our call"
        comm_fail = bool(rs.COMMUNICATION_FAILURE_RX.search(snippet))
        channel_escal = bool(rs.CHANNEL_ESCALATION_RX.search(snippet))
        signal_type = (
            "communication_failure_risk" if comm_fail else
            "multi_channel_escalation" if channel_escal else
            "direct_interaction"
        )
        self.assertEqual(signal_type, "direct_interaction")

    def test_ryan_hildebrand_scenario_produces_communication_failure_risk(self):
        """Generalized shape of the Global Payments / Ryan Hildebrand scenario:
        email bounce + channel escalation to text → communication_failure_risk."""
        snippet = (
            "Hey Todd — tried to email you but it bounced back. "
            "Texted you here to make sure this gets through."
        )
        comm_fail = bool(rs.COMMUNICATION_FAILURE_RX.search(snippet))
        channel_escal = bool(rs.CHANNEL_ESCALATION_RX.search(snippet))
        signal_type = (
            "communication_failure_risk" if comm_fail else
            "multi_channel_escalation" if channel_escal else
            "direct_interaction"
        )
        self.assertEqual(signal_type, "communication_failure_risk")
        # Escalation is also present — both patterns should fire.
        self.assertTrue(channel_escal, "Channel escalation should also be detected in this scenario")


if __name__ == "__main__":
    unittest.main()
