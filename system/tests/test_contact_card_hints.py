"""
test_contact_card_hints.py

Regression coverage: user feedback on relationship-decay bullets ("I
exchanged texts with Jeff a few days ago -- not sure why this is down")
suggested "maybe we need a link to display the contact card for the
contacts that are being surfaced for relationships." RB has no web UI to
hyperlink to, but getCard is a real, GPT-callable action -- so a plain-
language trigger phrase next to a contact's name lets the user pull up
their card without hunting for the right words.

_contact_card_hint() is applied to: Notable Contact Moves (only when
there's an actual baseline match) and Connect the Dots' relationship-
activation items.

RB 2026-08-27: My Priorities' relationship-decay bullets no longer use
the card hint. Reviewed live -- Todd: "Is telling the user something
they are aware [of] good use of this space? ... better would be - Jeff
is in your inner circle and you haven't communicated with him in 30
days." The card hint told the user to go ask for a fact RB already has
(the real days-quiet/RC-tier data already exists in
relationship_momentum_status, the same source RC Contact Status draws
from); My Priorities now looks that up and renders the real status
inline instead of an instruction to go ask for it.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_daily_brief as rdb  # noqa: E402

_TEST_DATE = date(2026, 7, 16)


class TestContactCardHint(unittest.TestCase):
    def test_hint_names_the_contact(self):
        hint = rdb._contact_card_hint("Jeff Coffland")
        self.assertIn("Jeff Coffland", hint)
        self.assertIn("show me", hint.lower())

    def test_empty_name_yields_empty_hint(self):
        self.assertEqual(rdb._contact_card_hint(""), "")


class TestRelationshipDecayBulletsShowRealStatus(unittest.TestCase):
    def test_decay_bullet_shows_real_days_and_inner_circle_tier(self):
        sections = {
            "my_priorities": [{
                "title": "↘ Relationship decay alert: Jeff Coffland",
                "extras": {},
            }],
            "relationship_momentum_status": [{
                "title": "Jeff Coffland — FROZEN [partial — LinkedIn unreconciled] (315d quiet)",
                "summary": (
                    "Last touch (baseline): 2025-10-15. Days quiet (effective): 315. "
                    "Momentum: FROZEN [partial — LinkedIn unreconciled]. Open loops: 0. "
                    "RC tier: inner."
                ),
            }],
        }
        out = rdb._render_my_priorities(sections)
        self.assertIn("Jeff Coffland", out)
        self.assertIn("315d since last contact", out)
        self.assertIn("inner circle", out)
        self.assertNotIn("show me", out.lower())

    def test_decay_bullet_omits_circle_note_for_non_inner_tier(self):
        sections = {
            "my_priorities": [{
                "title": "↘ Relationship decay alert: Someone Else",
                "extras": {},
            }],
            "relationship_momentum_status": [{
                "title": "Someone Else — COLD (40d quiet)",
                "summary": "Days quiet (effective): 40. RC tier: extended.",
            }],
        }
        out = rdb._render_my_priorities(sections)
        self.assertIn("40d since last contact", out)
        self.assertNotIn("inner circle", out)

    def test_up_arrow_direction_preserved_and_stripped_from_name(self):
        """RB-DEFECT-2026-07-09: only "↘" was stripped from the title and the
        rendered prefix was hardcoded to "↘" regardless of actual direction,
        so an early-warning (↗) alert rendered as "↘ ↗  Jose Torres" -- the
        real arrow leaking into the "name" plus the wrong symbol in front."""
        sections = {
            "my_priorities": [{
                "title": "↗ Relationship decay alert: Jose Torres",
                "extras": {},
            }],
        }
        out = rdb._render_my_priorities(sections)
        self.assertIn("↗ **Jose Torres**", out)
        self.assertNotIn("↘", out)
        self.assertNotIn("↗  Jose Torres", out)  # double-space leak from unstripped arrow


class TestNotableContactMovesCardHint(unittest.TestCase):
    def _item(self, person, baseline_id=None):
        return {
            "title": f"Notable contact move: {person} → Dairy Queen (chief technology officer)",
            "why_it_matters": "Baseline match found.",
            "recommended_action": "Review the relationship card.",
            "extras": {
                "person": person, "new_company": "Dairy Queen",
                "new_role": "chief technology officer", "is_cto": True,
                "baseline_id": baseline_id,
            },
        }

    def test_card_hint_shown_when_baseline_match_exists(self):
        sections = {"notable_contact_moves": [self._item("Phil Crawford", baseline_id="phil-crawford")]}
        out = rdb._render_notable_contact_moves(sections)
        self.assertIn('show me Phil Crawford\'s card', out)

    def test_no_card_hint_without_baseline_match(self):
        sections = {"notable_contact_moves": [self._item("Nobody Known", baseline_id=None)]}
        out = rdb._render_notable_contact_moves(sections)
        self.assertNotIn("show me", out.lower())


class TestConnectTheDotsRelationshipActivationCardHint(unittest.TestCase):
    def test_relationship_activation_item_includes_card_hint(self):
        sections = {"connect_the_dots": [{
            "title": "[RELATIONSHIP ACTIVATION] John Morrison (Qu) — outreach window",
            "why_it_matters": "Timely reason to reach out.",
            "recommended_action": "Contact John Morrison.",
            "disposition": "monitor",
            "confidence": "medium",
            "extras": {"convergence_type": "relationship_activation", "contact": "John Morrison"},
        }]}
        out = rdb._render_connect_the_dots(sections, _TEST_DATE)
        self.assertIn('show me John Morrison\'s card', out)

    def test_non_relationship_item_has_no_card_hint(self):
        sections = {"connect_the_dots": [{
            "title": "[CONVERGENCE] Independent validation of X",
            "why_it_matters": "Cross-company signal.",
            "recommended_action": "Review it.",
            "disposition": "monitor",
            "confidence": "high",
            "extras": {"convergence_type": "cross_company_theme"},
        }]}
        out = rdb._render_connect_the_dots(sections, _TEST_DATE)
        self.assertNotIn("show me", out.lower())


if __name__ == "__main__":
    unittest.main()
