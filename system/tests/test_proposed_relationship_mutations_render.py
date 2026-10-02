"""
test_proposed_relationship_mutations_render.py

RB-2026-08-24: cos_synthesis.detect_relationship_mutations() computes real
contact-reclassification proposals (evidence-scored, confidence-thresholded)
into sections["proposed_relationship_mutations"], but the only rendering
path for that section was a "rendering_rules" GPT-instruction list that
server.py's compact payload hardcodes to [] -- dead since the pre-rendered-
markdown pipeline superseded GPT-side generation. These proposals were
computed daily and never once reached the actual Daily Brief. This covers
the new renderer that closes that gap.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import render_daily_brief as rdb  # noqa: E402


def _mutation_item(contact_name: str, current: str, proposed: str,
                    confidence: str = "high", evidence: str = "Advocacy (+40pts)",
                    tags: list | None = None) -> dict:
    return {
        "title": f"⚡ Proposed Mutation: {contact_name} → {proposed}",
        "extras": {
            "contact_name": contact_name,
            "current_classification": current,
            "proposed_classification": proposed,
            "confidence": confidence,
            "evidence_summary": evidence,
            "proposed_tags": tags or [],
        },
    }


class TestProposedRelationshipMutationsRender(unittest.TestCase):
    def test_empty_sections_renders_nothing(self):
        self.assertEqual(rdb._render_proposed_relationship_mutations({}), "")
        self.assertEqual(
            rdb._render_proposed_relationship_mutations({"proposed_relationship_mutations": []}), "")

    def test_proposal_renders_contact_and_transition(self):
        sections = {"proposed_relationship_mutations": [
            _mutation_item("Jeff Coffland", "cold", "warm", evidence="Sponsor behavior (+45pts)")
        ]}
        out = rdb._render_proposed_relationship_mutations(sections)
        self.assertIn("## Proposed Relationship Mutations", out)
        self.assertIn("Jeff Coffland", out)
        self.assertIn("cold → warm", out)
        self.assertIn("Sponsor behavior", out)

    def test_no_apply_path_disclosure_present(self):
        """This section must be honest that there's no automatic apply path
        yet -- it must not imply the mutation is auto-applied just because
        the underlying data carries an auto_apply flag."""
        sections = {"proposed_relationship_mutations": [
            _mutation_item("Test Contact", "cold", "warm")
        ]}
        out = rdb._render_proposed_relationship_mutations(sections)
        self.assertIn("No automatic apply path", out)

    def test_proposed_tags_rendered_when_present(self):
        sections = {"proposed_relationship_mutations": [
            _mutation_item("Test Contact", "cold", "warm", tags=["advocate", "sponsor"])
        ]}
        out = rdb._render_proposed_relationship_mutations(sections)
        self.assertIn("advocate, sponsor", out)

    def test_caps_at_five_items(self):
        sections = {"proposed_relationship_mutations": [
            _mutation_item(f"Contact {i}", "cold", "warm") for i in range(8)
        ]}
        out = rdb._render_proposed_relationship_mutations(sections)
        for i in range(5):
            self.assertIn(f"Contact {i}", out)
        for i in range(5, 8):
            self.assertNotIn(f"Contact {i}", out)


if __name__ == "__main__":
    unittest.main()
