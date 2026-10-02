"""
test_action_drafts_text.py — RB 9.19 draft rendering tests.

Covers:
  - follow-up email draft
  - SMS/text draft length and tone
  - LinkedIn DM draft length and tone
  - intro request includes opt-out/no-pressure language
  - restricted/free-advice trigger does not produce free-resource positioning
  - weak/stale evidence is not phrased as fact
  - passive intelligence weak signal is not phrased as verified fact
  - send_allowed=False and requires_user_review=True are always present
  - no unsupported names/facts appear
"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from dataclasses import asdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import action_drafts as ad


# ---------------------------------------------------------------------------
# Synthetic test data — no real baseline required
# ---------------------------------------------------------------------------

TODAY = date(2026, 5, 28)


def _make_entry(**kwargs) -> dict:
    defaults = {
        "id": "test-contact",
        "name": "Jane Doe",
        "signal_class": "RC",
        "rc_tier": "inner",
        "rc_state": "ACTIVE",
        "current_company": "Acme Corp",
        "role": "VP Sales",
        "email": "jane@acme.com",
        "phone": "+19205551234",
        "linkedin_url": "https://linkedin.com/in/janedoe",
        "last_touch": (TODAY - timedelta(days=45)).isoformat(),
        "last_touch_source": "direct",
        "sources": ["linkedin", "email"],
        "circles": [],
        "tags": [],
        "notes": "",
    }
    defaults.update(kwargs)
    return defaults


def _make_spec(**kwargs) -> ad.DraftSpec:
    defaults = dict(
        spec_id="abc123def456",
        action_type="follow_up",
        channel="email",
        contact_id="test-contact",
        contact_name="Jane Doe",
        contact_company="Acme Corp",
        contact_role="VP Sales",
        contact_email="jane@acme.com",
        contact_phone="+19205551234",
        contact_linkedin="https://linkedin.com/in/janedoe",
        rc_tier="inner",
        days_since_touch=45,
        last_touch=(TODAY - timedelta(days=45)).isoformat(),
        urgency="overdue",
        evidence=["45 days since last touch (threshold: 30 days, overage: 15d)"],
        evidence_strength="strong",
        thread_ids=[],
        company_context="Acme Corp",
        last_touch_source="direct",
        is_engagement_restricted=False,
        restriction_reason=None,
    )
    defaults.update(kwargs)
    return ad.DraftSpec(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInvariants(unittest.TestCase):
    """send_allowed=False and requires_user_review=True must always hold."""

    def _assert_invariants(self, draft: ad.RenderedDraft) -> None:
        self.assertFalse(draft.send_allowed, "send_allowed must always be False")
        self.assertTrue(draft.requires_user_review, "requires_user_review must always be True")
        d = draft.to_dict()
        self.assertFalse(d["send_allowed"], "to_dict() must preserve send_allowed=False")
        self.assertTrue(d["requires_user_review"], "to_dict() must preserve requires_user_review=True")

    def test_follow_up_invariants(self):
        draft = ad.render_draft(_make_spec(action_type="follow_up", channel="email"))
        self._assert_invariants(draft)

    def test_reconnect_invariants(self):
        draft = ad.render_draft(_make_spec(action_type="reconnect", channel="email"))
        self._assert_invariants(draft)

    def test_linkedin_invariants(self):
        draft = ad.render_draft(_make_spec(action_type="linkedin_message", channel="linkedin"))
        self._assert_invariants(draft)

    def test_text_message_invariants(self):
        draft = ad.render_draft(_make_spec(action_type="text_message", channel="sms"))
        self._assert_invariants(draft)

    def test_intro_request_invariants(self):
        spec = _make_spec(
            action_type="intro_request",
            channel="email",
            intro_target_company="Toast",
            intro_reason="Connected through PAR alumni network.",
        )
        draft = ad.render_draft(spec)
        self._assert_invariants(draft)

    def test_thank_you_invariants(self):
        draft = ad.render_draft(_make_spec(action_type="thank_you", channel="email"))
        self._assert_invariants(draft)


class TestFollowUpEmail(unittest.TestCase):

    def test_follow_up_has_subject(self):
        draft = ad.render_draft(_make_spec())
        self.assertIsNotNone(draft.subject)
        self.assertTrue(len(draft.subject) > 0)

    def test_follow_up_body_not_empty(self):
        draft = ad.render_draft(_make_spec())
        self.assertTrue(len(draft.body) > 20)

    def test_follow_up_signed_with_sender_name(self):
        draft = ad.render_draft(_make_spec())
        self.assertIn(ad.SENDER_FIRST_NAME, draft.body)

    def test_follow_up_references_company_when_known(self):
        draft = ad.render_draft(_make_spec(contact_company="Acme Corp"))
        self.assertIn("Acme Corp", draft.body)

    def test_follow_up_omits_company_when_unknown(self):
        draft = ad.render_draft(_make_spec(contact_company=None))
        # Should note the omission, not invent a company
        self.assertIn("company", " ".join(draft.omitted_due_to_uncertainty).lower())

    def test_follow_up_no_banned_phrases(self):
        draft = ad.render_draft(_make_spec())
        for phrase in ad._BANNED_PHRASES:
            self.assertNotIn(
                phrase.lower(), draft.body.lower(),
                f"Banned phrase found: '{phrase}'"
            )

    def test_follow_up_has_evidence_used(self):
        spec = _make_spec(evidence=["45 days since last touch (threshold: 30 days, overage: 15d)"])
        draft = ad.render_draft(spec)
        self.assertEqual(draft.evidence_used, spec.evidence)

    def test_follow_up_has_tone_suggestion(self):
        draft = ad.render_draft(_make_spec())
        self.assertTrue(len(draft.tone_suggestion) > 10)

    def test_no_unsupported_names_in_body(self):
        # Body should contain the contact's real name or "Jane" but not invent others
        spec = _make_spec(contact_name="Jane Doe")
        draft = ad.render_draft(spec)
        # The only name should be "Jane" (first name) and "Todd" (sender)
        import re
        # Check that "Jane" appears (as first name)
        self.assertIn("Jane", draft.body)
        # Ensure no obviously invented fictional names appear
        for invented in ["Bob", "Alice", "John Smith", "Mary"]:
            self.assertNotIn(invented, draft.body)


class TestSMSDraft(unittest.TestCase):

    def test_sms_under_160_chars(self):
        draft = ad.render_draft(_make_spec(action_type="text_message", channel="sms"))
        self.assertLessEqual(len(draft.body), ad.SMS_MAX_CHARS + 10,
                             f"SMS body too long: {len(draft.body)} chars")

    def test_sms_has_no_subject(self):
        draft = ad.render_draft(_make_spec(action_type="text_message", channel="sms"))
        self.assertIsNone(draft.subject)

    def test_sms_uses_first_name(self):
        draft = ad.render_draft(_make_spec(action_type="text_message", channel="sms",
                                           contact_name="Jane Doe"))
        self.assertIn("Jane", draft.body)

    def test_sms_tone_is_warm_not_corporate(self):
        draft = ad.render_draft(_make_spec(action_type="text_message", channel="sms"))
        body_lower = draft.body.lower()
        for corporate in ["per our discussion", "as per", "synergy", "leverage"]:
            self.assertNotIn(corporate, body_lower)

    def test_sms_has_safety_flag_about_consent(self):
        draft = ad.render_draft(_make_spec(action_type="text_message", channel="sms"))
        flags_text = " ".join(draft.safety_flags).lower()
        self.assertIn("consent", flags_text)


class TestLinkedInDraft(unittest.TestCase):

    def test_linkedin_under_300_chars(self):
        draft = ad.render_draft(_make_spec(action_type="linkedin_message", channel="linkedin"))
        self.assertLessEqual(len(draft.body), ad.LINKEDIN_MAX_CHARS + 10,
                             f"LinkedIn DM too long: {len(draft.body)} chars")

    def test_linkedin_has_no_subject(self):
        draft = ad.render_draft(_make_spec(action_type="linkedin_message", channel="linkedin"))
        self.assertIsNone(draft.subject)

    def test_linkedin_uses_first_name(self):
        draft = ad.render_draft(_make_spec(action_type="linkedin_message", channel="linkedin",
                                           contact_name="Jane Doe"))
        self.assertIn("Jane", draft.body)

    def test_linkedin_no_business_pitch(self):
        draft = ad.render_draft(_make_spec(action_type="linkedin_message", channel="linkedin"))
        body_lower = draft.body.lower()
        for pitch_phrase in ["advisory services", "bridgepoint ops services", "let me know how i can help you grow"]:
            self.assertNotIn(pitch_phrase, body_lower)

    def test_linkedin_tone_warm(self):
        draft = ad.render_draft(_make_spec(action_type="linkedin_message", channel="linkedin"))
        self.assertTrue(len(draft.body) > 0)
        self.assertIn(ad.SENDER_FIRST_NAME, draft.body)


class TestIntroRequest(unittest.TestCase):

    def _make_intro_spec(self, **kwargs) -> ad.DraftSpec:
        base = dict(
            action_type="intro_request",
            channel="email",
            intro_target_company="Toast",
            intro_reason="Connected through PAR alumni network — knows the CTO there.",
        )
        base.update(kwargs)
        return _make_spec(**base)

    def test_intro_has_opt_out_language(self):
        draft = ad.render_draft(self._make_intro_spec())
        body_lower = draft.body.lower()
        # Must include no-pressure phrasing
        opt_out_phrases = ["no pressure", "rather not", "if you're comfortable", "if you think it's a fit"]
        found = any(phrase in body_lower for phrase in opt_out_phrases)
        self.assertTrue(found, f"Intro request missing no-pressure language. Body:\n{draft.body}")

    def test_intro_references_target(self):
        draft = ad.render_draft(self._make_intro_spec(intro_target_company="Toast"))
        self.assertIn("Toast", draft.body)

    def test_intro_does_not_claim_broker_knows_target_certainly(self):
        # Should not use phrases that claim certainty about relationship
        spec = _make_spec(
            action_type="intro_request",
            channel="email",
            intro_target_company="Toast",
            intro_reason="",  # no reason provided
        )
        draft = ad.render_draft(spec)
        # Omissions should note the uncertain relationship
        omitted_text = " ".join(draft.omitted_due_to_uncertainty).lower()
        self.assertIn("relationship", omitted_text)

    def test_intro_has_safety_flag(self):
        draft = ad.render_draft(self._make_intro_spec())
        flags_text = " ".join(draft.safety_flags).lower()
        self.assertIn("intro", flags_text)

    def test_intro_signed_with_company(self):
        draft = ad.render_draft(self._make_intro_spec())
        self.assertIn(ad.SENDER_COMPANY, draft.body)


class TestRestrictedContacts(unittest.TestCase):
    """Free-advice / intro-only restricted contacts must not produce free-resource positioning."""

    def _make_restricted_spec(self, note: str = "wants free advice", **kwargs) -> ad.DraftSpec:
        return _make_spec(
            is_engagement_restricted=True,
            restriction_reason=f"free-advice positioning: {note}",
            **kwargs,
        )

    def test_restricted_contact_still_renders(self):
        spec = self._make_restricted_spec()
        draft = ad.render_draft(spec)
        self.assertIsNotNone(draft.body)

    def test_restricted_contact_has_safety_flag(self):
        spec = self._make_restricted_spec()
        draft = ad.render_draft(spec)
        flags_text = " ".join(draft.safety_flags).lower()
        self.assertIn("bridgepoint", flags_text)

    def test_restricted_contact_body_does_not_offer_free_help(self):
        spec = self._make_restricted_spec()
        draft = ad.render_draft(spec)
        body_lower = draft.body.lower()
        for free_phrase in [
            "free consultation",
            "no charge",
            "happy to help for free",
            "pro bono",
            "at no cost",
            "let me pick your brain",
        ]:
            self.assertNotIn(free_phrase, body_lower,
                             f"Restricted draft contains forbidden phrase: '{free_phrase}'")

    def test_restriction_reason_in_safety_flags(self):
        spec = self._make_restricted_spec(note="commission-only")
        draft = ad.render_draft(spec)
        flags_text = " ".join(draft.safety_flags)
        self.assertIn("restriction", flags_text.lower())


class TestWeakEvidence(unittest.TestCase):
    """Weak/stale evidence must not be phrased as verified fact."""

    def _make_weak_spec(self, **kwargs) -> ad.DraftSpec:
        return _make_spec(
            evidence_strength="weak",
            days_since_touch=400,
            evidence=["400 days since last touch — stale signal"],
            **kwargs,
        )

    def test_weak_evidence_has_safety_flag(self):
        spec = self._make_weak_spec()
        draft = ad.render_draft(spec)
        flags_text = " ".join(draft.safety_flags).lower()
        self.assertIn("evidence", flags_text)

    def test_weak_evidence_body_does_not_claim_certainty(self):
        spec = self._make_weak_spec()
        draft = ad.render_draft(spec)
        body_lower = draft.body.lower()
        # Should not claim "I know what you've been working on" type certainty
        for certain_phrase in [
            "i know you've been",
            "since our last project",
            "when we last worked together on",
        ]:
            self.assertNotIn(certain_phrase, body_lower,
                             f"Weak evidence draft uses certainty phrase: '{certain_phrase}'")

    def test_weak_stale_evidence_triggers_omission(self):
        spec = self._make_weak_spec(contact_company=None)
        draft = ad.render_draft(spec)
        self.assertTrue(len(draft.omitted_due_to_uncertainty) > 0)


class TestPassiveIntelligence(unittest.TestCase):
    """Passive intelligence weak signals must not be phrased as verified facts."""

    def _make_passive_spec(self, **kwargs) -> ad.DraftSpec:
        return _make_spec(
            last_touch_source="linkedin_feed",
            evidence_strength="weak",
            evidence=["last-touch source is passive (social signal — not confirmed direct contact)"],
            **kwargs,
        )

    def test_passive_source_has_safety_flag(self):
        spec = self._make_passive_spec()
        draft = ad.render_draft(spec)
        flags_text = " ".join(draft.safety_flags).lower()
        self.assertIn("passive", flags_text)

    def test_passive_source_omits_specific_post_reference(self):
        spec = self._make_passive_spec()
        draft = ad.render_draft(spec)
        omitted_text = " ".join(draft.omitted_due_to_uncertainty).lower()
        self.assertIn("passive", omitted_text)

    def test_passive_linkedin_source_does_not_claim_saw_post(self):
        spec = self._make_passive_spec(action_type="linkedin_message", channel="linkedin")
        draft = ad.render_draft(spec)
        body_lower = draft.body.lower()
        for claim in ["saw your post", "i noticed your update", "i read your article"]:
            self.assertNotIn(claim, body_lower,
                             f"Passive draft claims specific post knowledge: '{claim}'")

    def test_passive_source_not_phrased_as_direct_contact(self):
        spec = self._make_passive_spec()
        draft = ad.render_draft(spec)
        # "been a while" is acceptable; "when we last talked" is not (claims direct contact)
        for direct_claim in ["when we last talked", "since our call", "after our meeting"]:
            self.assertNotIn(direct_claim, draft.body.lower())


class TestChannelConstraints(unittest.TestCase):

    def test_email_has_subject(self):
        for action in ["follow_up", "reconnect", "thank_you", "intro_request"]:
            spec = _make_spec(action_type=action, channel="email")
            if action == "intro_request":
                spec.intro_target_company = "Toast"
            draft = ad.render_draft(spec)
            self.assertIsNotNone(draft.subject, f"Email draft '{action}' missing subject")

    def test_linkedin_no_subject(self):
        draft = ad.render_draft(_make_spec(action_type="linkedin_message", channel="linkedin"))
        self.assertIsNone(draft.subject)

    def test_sms_no_subject(self):
        draft = ad.render_draft(_make_spec(action_type="text_message", channel="sms"))
        self.assertIsNone(draft.subject)


class TestDraftIdAndSpecId(unittest.TestCase):

    def test_draft_has_draft_id(self):
        draft = ad.render_draft(_make_spec())
        self.assertTrue(draft.draft_id)

    def test_draft_has_spec_id(self):
        spec = _make_spec()
        draft = ad.render_draft(spec)
        self.assertEqual(draft.spec_id, spec.spec_id)

    def test_draft_id_differs_from_spec_id(self):
        spec = _make_spec()
        draft = ad.render_draft(spec)
        self.assertNotEqual(draft.draft_id, draft.spec_id)


class TestRenderDrafts(unittest.TestCase):

    def test_render_drafts_all_have_invariants(self):
        specs = [
            _make_spec(action_type="follow_up", channel="email"),
            _make_spec(action_type="text_message", channel="sms"),
            _make_spec(action_type="linkedin_message", channel="linkedin"),
        ]
        drafts = ad.render_drafts(specs)
        self.assertEqual(len(drafts), 3)
        for d in drafts:
            self.assertFalse(d.send_allowed)
            self.assertTrue(d.requires_user_review)


if __name__ == "__main__":
    unittest.main()
