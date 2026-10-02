"""
test_thread_promotion.py — RB-DEFECT-038

Unit tests for thread_promotion.py (relationship/thread auto-promotion) and
an integration test confirming meeting_prep.py renders synthesized narrative
context for not-yet-baseline attendees instead of an empty gap note.

Test groups:
  TP1 (4): Tier-0 noise filtering — is_cold_template, noise-domain dismissal
  TP2 (4): classify_thread_type — keyword-based thread typing
  TP3 (3): synthesize_narrative — evidence-only narrative construction
  TP4 (3): evaluate_promotion — end-to-end proposal shape, dismissal cases,
           and the Daniel von Walzel regression fixture
  TP5 (2): apply_baseline_entry — idempotent baseline writes
  TP6 (1): meeting_prep integration — unknown attendee gets a narrative,
           not just "not yet in baseline"
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import thread_promotion as tp  # noqa: E402
import meeting_prep  # noqa: E402


# ---------------------------------------------------------------------------
# TP1 — Tier-0 noise filtering
# ---------------------------------------------------------------------------

class TestTP1_Noise(unittest.TestCase):
    def test_TP1a_cold_template_two_phrase_hit(self):
        text = (
            "Hi Todd, does BridgePoint Ops have access to all the financing "
            "it needs right now? We help businesses secure $10K to $20M "
            "through lines of credit, term loans, equipment financing, and "
            "more. What time works best for you today/tomorrow?"
        )
        self.assertTrue(tp.is_cold_template(text))

    def test_TP1b_single_phrase_hit_not_flagged(self):
        text = "Let's set up a time to chat about the podcast next week."
        self.assertFalse(tp.is_cold_template(text))

    def test_TP1c_noise_domain_email_dismissed(self):
        event = {"title": "Sync", "description": ""}
        attendee = {"email": "newsletter@no-reply.example", "name": "No Reply"}
        self.assertIsNone(tp.evaluate_promotion(event, attendee))

    def test_TP1d_no_email_dismissed(self):
        event = {"title": "Sync", "description": ""}
        attendee = {"email": "", "name": "Mystery"}
        self.assertIsNone(tp.evaluate_promotion(event, attendee))


# ---------------------------------------------------------------------------
# TP2 — classify_thread_type
# ---------------------------------------------------------------------------

class TestTP2_ThreadType(unittest.TestCase):
    def test_TP2a_podcast_is_media_thought_leadership(self):
        self.assertEqual(
            tp.classify_thread_type("Pre-podcast call", "Virtual Coffee - Let's connect"),
            "media_thought_leadership",
        )

    def test_TP2b_phone_screen_is_recruiting(self):
        self.assertEqual(
            tp.classify_thread_type("Phone screen with Acme", ""),
            "recruiting",
        )

    def test_TP2c_intro_is_networking(self):
        self.assertEqual(
            tp.classify_thread_type("Quick intro call", ""),
            "networking",
        )

    def test_TP2d_unmatched_is_general_relationship(self):
        self.assertEqual(
            tp.classify_thread_type("Weekly status sync", ""),
            "general_relationship",
        )


# ---------------------------------------------------------------------------
# TP3 — synthesize_narrative
# ---------------------------------------------------------------------------

class TestTP3_Narrative(unittest.TestCase):
    def test_TP3a_no_history_states_so(self):
        narrative = tp.synthesize_narrative({"description": ""}, [], "Daniel von Walzel")
        self.assertIn("No prior email/LinkedIn history found for Daniel von Walzel", narrative)

    def test_TP3b_includes_calendar_context(self):
        event = {"description": "Virtual Coffee - Let's connect\n\nPre-podcast call"}
        narrative = tp.synthesize_narrative(event, [], "Daniel von Walzel")
        self.assertIn("Pre-podcast call", narrative)
        self.assertIn("Virtual Coffee - Let's connect", narrative)

    def test_TP3c_includes_first_contact_evidence(self):
        hits = [{"source": "email.personal", "date": "2026-06-05", "direction": "inbound",
                 "subject": "Found you via BridgePoint Ops", "snippet": "..."}]
        narrative = tp.synthesize_narrative({"description": ""}, hits, "Daniel von Walzel")
        self.assertIn("2026-06-05", narrative)
        self.assertIn("first reached out", narrative)


# ---------------------------------------------------------------------------
# TP4 — evaluate_promotion
# ---------------------------------------------------------------------------

class TestTP4_EvaluatePromotion(unittest.TestCase):
    def test_TP4a_proposal_shape(self):
        event = {"title": "Quick intro call", "description": "Excited to connect."}
        attendee = {"email": "new.person@example.com", "name": "New Person"}
        proposal = tp.evaluate_promotion(event, attendee)
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal["tier"], 3)
        self.assertEqual(proposal["thread_type"], "networking")
        self.assertEqual(proposal["persistence_status"], "pending confirmation")
        self.assertIn("id", proposal["proposed_baseline_entry"])
        self.assertIn("id", proposal["proposed_thread"])
        self.assertIn("- id: T-", proposal["proposed_thread_yaml"])

    def test_TP4b_cold_outreach_with_no_calendar_context_dismissed(self):
        # "Anel Paul" / QualiFi sent only templated cold-outreach LinkedIn
        # messages and this event carries no description of its own.
        #
        # This used to read the real system/inbox/linkedin.messages.json,
        # which drifted out from under the test (that file no longer
        # contains any "Anel Paul" message at all — the live inbox re-synced
        # since this test was written), so search_inbox_history() found zero
        # hits and evaluate_promotion() had nothing to classify as cold
        # outreach, silently falling through to a "new contact" proposal
        # instead of dismissing it. Fixed by supplying a controlled
        # LinkedIn-messages fixture instead of depending on live inbox state.
        with tempfile.TemporaryDirectory() as tmp:
            inbox_dir = Path(tmp)
            (inbox_dir / "linkedin.messages.json").write_text(json.dumps([{
                "from": {"name": "Anel Paul"},
                "date": "2026-05-01",
                "subject": "Quick chat",
                "content": (
                    "Hi Todd, does BridgePoint Ops have access to all the financing "
                    "it needs right now? We help businesses secure $10K to $20M "
                    "through lines of credit, term loans, equipment financing, and "
                    "more. What time works best for you today/tomorrow?"
                ),
                "direction": "inbound",
            }]))
            with patch.object(tp.core, "INBOX_DIR", inbox_dir):
                event = {"title": "Quick chat", "description": ""}
                attendee = {"email": "anel.paul@qualifi-llc.com", "name": "Anel Paul"}
                self.assertIsNone(tp.evaluate_promotion(event, attendee))

    def test_TP4c_daniel_von_walzel_regression(self):
        """RB-DEFECT-038 trigger case: 2026-06-11 'Daniel von Walzel and Todd
        Vahlsing' calendar event, description 'Virtual Coffee - Let's
        connect... Pre-podcast call'. Daniel is not in baseline_index.json.
        This must classify as a media/thought-leadership Tier-3 proposal,
        not be dismissed as noise.
        """
        event = {
            "title": "Daniel von Walzel and Todd Vahlsing",
            "description": (
                "Event Name\nVirtual Coffee - Let's connect\n\n"
                "Location: This is a Google Meet web conference.\n"
                "Please share anything that will help prepare for our "
                "meeting.: Pre-podcast call\n"
            ),
        }
        attendee = {"email": "daniel@growthventure.ai", "name": None}
        proposal = tp.evaluate_promotion(event, attendee)
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal["thread_type"], "media_thought_leadership")
        self.assertEqual(proposal["proposed_thread"]["boost_for_brief"], "high")
        self.assertEqual(proposal["display_name"], "Daniel")
        self.assertIn("Pre-podcast call", proposal["narrative"])


# ---------------------------------------------------------------------------
# TP5 — apply_baseline_entry
# ---------------------------------------------------------------------------

class TestTP5_ApplyBaselineEntry(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "baseline_index.json"
        self.path.write_text(json.dumps([
            {"id": "existing-person", "name": "Existing Person", "email": "existing@example.com"},
        ]))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_TP5a_appends_new_entry(self):
        entry = {"id": "new-person", "name": "New Person", "email": "new@example.com"}
        result = tp.apply_baseline_entry(entry, baseline_path=self.path)
        self.assertTrue(result["applied"])
        baseline = json.loads(self.path.read_text())
        self.assertEqual(len(baseline), 2)
        self.assertEqual(baseline[1]["id"], "new-person")

    def test_TP5b_idempotent_on_existing_email(self):
        entry = {"id": "duplicate-person", "name": "Existing Person", "email": "existing@example.com"}
        result = tp.apply_baseline_entry(entry, baseline_path=self.path)
        self.assertFalse(result["applied"])
        baseline = json.loads(self.path.read_text())
        self.assertEqual(len(baseline), 1)


# ---------------------------------------------------------------------------
# TP6 — meeting_prep integration
# ---------------------------------------------------------------------------

class TestTP6_MeetingPrepIntegration(unittest.TestCase):
    def test_TP6a_unknown_attendee_gets_narrative_not_just_gap_note(self):
        from datetime import date
        event = {
            "id": "ev-daniel-test",
            "title": "Daniel von Walzel and Todd Vahlsing",
            "description": "Virtual Coffee - Let's connect\n\nPre-podcast call",
            "start": "2026-06-11T09:00:00-05:00",
            "end": "2026-06-11T09:30:00-05:00",
            "attendees_matched": [
                {"id": None, "name": None, "email": "daniel@growthventure.ai"},
            ],
            "matched_threads": [],
            "active_thread_company_hits": [],
        }
        payload = meeting_prep.build_prep_payload(event, baseline=[], threads=[], today=date(2026, 6, 11))
        unknown = payload["attendees"]["unknown"]
        self.assertEqual(len(unknown), 1)
        promo = unknown[0]["promotion"]
        self.assertIsNotNone(promo)
        self.assertEqual(promo["thread_type"], "media_thought_leadership")

        md = meeting_prep.render_prep_brief_md(payload)
        self.assertIn("## 1a. New relationship — proposed thread", md)
        self.assertIn("Pre-podcast call", md)
        self.assertIn("media_thought_leadership", md)


if __name__ == "__main__":
    unittest.main()
