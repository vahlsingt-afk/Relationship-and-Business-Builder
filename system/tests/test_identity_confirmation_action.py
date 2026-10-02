"""
test_identity_confirmation_action.py

Regression coverage, three rounds:

1. H+ Identity Confirmations named a mismatch ("Taj Penaso" inbound email
   vs. baseline contact) but gave no way to act on it at all.

2. The first fix rendered a raw "POST /identity/{id}/confirm" line — Todd
   pasted that literally into the live Custom GPT and got a generic "here's
   how you'd curl this" explanation back, not an executed action. The GPT
   has no bridge from a bare HTTP path string to its own tools unless the
   operationId is named and the user is given a natural-language phrase to
   say instead of something that reads like a literal command to copy-paste.

3. confirmIdentityMatch/rejectIdentityMatch were retired in favor of
   confirmProposal (kind="identity_match") — a single generic confirm/
   reject action covering identity matches plus four other proposal types
   that had no GPT action at all — freeing a slot within the 30-op cap
   instead of costing one. See test_confirm_proposal_api.py for the
   endpoint itself.

4. RB-DEFECT-2026-09-18 (intelligence-cycle repair, reporting contract
   "suppress internal scoring jargon"): round 2's fix (naming
   confirmProposal/kind="identity_match" explicitly in the rendered text)
   was itself superseded once the Custom GPT was retired in favor of the
   Trusted Chat Client (rbb_chat.py, 2026-08-27) -- that model no longer
   needs a literal operationId bridge in user-facing prose to find the
   right tool call. Now matches the same convention already documented in
   render_daily_brief.py's _render_pending_confirmations() ("Give each
   pending proposal a chat-actionable hint instead of the raw 'call
   confirmProposal (kind=...)' API instruction"): a plain-language
   Yes/No/Not sure decision prompt, no raw operation names in the text
   Todd actually reads.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_intelligence_brief as rib  # noqa: E402


class TestIdentityConfirmationAction(unittest.TestCase):
    def test_renders_confirm_and_reject_commands(self):
        sections = {
            "identity_match_candidates": [{
                "title": 'Identity match: "Taj Penaso" — Taj Penaso?',
                "summary": "Inbound email matches baseline contact by name but no email on file yet.",
                "extras": {"candidate_id": "taj-penaso::taj.penaso@northprogram.website"},
            }]
        }
        out = rib._render_identity_match_candidates(sections)
        # Plain-language decision prompt (no raw operationId in the text Todd
        # reads -- see round 4 in the module docstring above). The title
        # itself (not the internal candidate_id) is the identifying anchor
        # in the rendered text; candidate_id stays in `extras` for whatever
        # downstream resolves the reply, not printed for Todd to read.
        self.assertIn("Decision needed", out)
        self.assertIn("Reply", out)
        self.assertIn('Identity match: "Taj Penaso"', out)

    def test_empty_candidates_renders_nothing(self):
        self.assertEqual(rib._render_identity_match_candidates({"identity_match_candidates": []}), "")


if __name__ == "__main__":
    unittest.main()
