"""
test_personal_relationship_guard.py — RB-2026-08-28.

Todd's explicit rule: RB is a business/professional relationship-capital
tool. Personal relationships and personal content must never be recorded
or reported -- only business (and "business personal") should be. Same
principle as the SMS exempt_handles rule (2026-07-27), extended to email/
calendar with two signal types: exempt senders (always personal unless the
specific item is explicitly flagged otherwise) and exempt topics (always
personal regardless of sender, never overridable).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import personal_relationship_guard as prg  # noqa: E402


TEST_CONFIG = {
    "exempt_senders": [
        {"name": "Tony Fryer", "email": None},
        {"name": "Nick Neylon", "email": "nick@example.com"},
    ],
    "exempt_topics": ["fantasy football"],
}


class TestSenderClassification(unittest.TestCase):
    def test_exempt_sender_by_name_is_personal(self):
        result = prg.classify(sender_name="Tony Fryer", text="want to grab lunch", config=TEST_CONFIG)
        self.assertTrue(result.is_personal)
        self.assertIn("Tony Fryer", result.reason)

    def test_exempt_sender_by_email_is_personal(self):
        result = prg.classify(sender_email="nick@example.com", text="hey", config=TEST_CONFIG)
        self.assertTrue(result.is_personal)

    def test_partial_name_match(self):
        result = prg.classify(sender_name="Tony Fryer Jr.", text="", config=TEST_CONFIG)
        self.assertTrue(result.is_personal)

    def test_non_exempt_sender_is_business_by_default(self):
        result = prg.classify(sender_name="Jeff Caplin", text="confirming the renewal", config=TEST_CONFIG)
        self.assertFalse(result.is_personal)
        self.assertIsNone(result.reason)

    def test_business_override_clears_sender_match(self):
        result = prg.classify(
            sender_name="Tony Fryer", text="quote for the conference booth",
            explicit_business_flag=True, config=TEST_CONFIG,
        )
        self.assertFalse(result.is_personal)


class TestTopicClassification(unittest.TestCase):
    def test_exempt_topic_is_personal(self):
        result = prg.classify(sender_name="Maggie Lenhart", text="who's in fantasy football this year", config=TEST_CONFIG)
        self.assertTrue(result.is_personal)
        self.assertIn("fantasy football", result.reason)

    def test_topic_match_not_overridable_by_business_flag(self):
        """Content that is inherently personal doesn't become business
        because of who sent it or an override flag -- only sender matches
        are overridable."""
        result = prg.classify(
            sender_name="Jeff Caplin", text="fantasy football draft is Saturday",
            explicit_business_flag=True, config=TEST_CONFIG,
        )
        self.assertTrue(result.is_personal)

    def test_topic_match_case_insensitive(self):
        result = prg.classify(text="Fantasy Football league dues due", config=TEST_CONFIG)
        self.assertTrue(result.is_personal)


class TestDefaultBehavior(unittest.TestCase):
    def test_empty_config_never_flags_personal(self):
        result = prg.classify(sender_name="Anyone", text="anything at all", config={"exempt_senders": [], "exempt_topics": []})
        self.assertFalse(result.is_personal)

    def test_no_match_is_business_default(self):
        """Opt-out model: business is the default, nothing is excluded
        unless it actually matches a configured signal."""
        result = prg.classify(sender_name="A Random Vendor Contact", text="quarterly business review", config=TEST_CONFIG)
        self.assertFalse(result.is_personal)


class TestConfigIO(unittest.TestCase):
    def test_load_config_missing_file_returns_defaults(self):
        original = prg.CONFIG_PATH
        try:
            prg.CONFIG_PATH = Path("/tmp/does-not-exist-personal-exempt.json")
            cfg = prg.load_config()
            self.assertEqual(cfg["exempt_senders"], [])
            self.assertEqual(cfg["exempt_topics"], [])
        finally:
            prg.CONFIG_PATH = original

    def test_real_config_file_has_seeded_entries(self):
        """Confirms the actual production config (not a test fixture) has
        the two senders and one topic Todd specified live."""
        cfg = prg.load_config()
        sender_names = {e.get("name") for e in cfg.get("exempt_senders", [])}
        self.assertIn("Tony Fryer", sender_names)
        self.assertIn("Nick Neylon", sender_names)
        self.assertIn("fantasy football", cfg.get("exempt_topics", []))


if __name__ == "__main__":
    unittest.main()
