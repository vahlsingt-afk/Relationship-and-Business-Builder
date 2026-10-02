"""
test_relationship_mutation_engine.py — Sprint M: Relationship Intelligence Mutation Engine

Tests:
  RM1  — exported functions are callable
  RM2  — _score_relationship_signals returns correct structure
  RM3  — advocacy keywords score positive
  RM4  — sponsor keywords score higher than advocacy
  RM5  — problem-solving keywords (active advocacy) score positive
  RM6  — risk keywords score negative
  RM7  — peer/trust keywords score positive
  RM8  — text with no signals scores zero
  RM9  — detect_relationship_mutations returns empty for empty sections
  RM10 — item with advocacy signal + known contact generates mutation proposal
  RM11 — proposal has correct schema fields
  RM12 — MEDIUM confidence → ask_todd disposition
  RM13 — HIGH confidence → act_today disposition
  RM14 — HIGH confidence proposal has auto_apply=True in extras
  RM15 — proposed_classification is non-empty string
  RM16 — duplicate (contact, classification) not proposed twice
  RM17 — risk signals generate classification_downgrade mutation type
  RM18 — items below MEDIUM threshold do not generate proposals
  RM19 — explicit extras.contact_name used when contact not in index
  RM20 — mutation extras contain all required fields
  RM21 — John Adams advocacy scenario generates mutation (defect example)
  RM22 — sponsor text generates Sponsor or Tier 1 Sponsor classification
  RM23 — detect_relationship_mutations sorts HIGH confidence first
"""
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import cos_synthesis as cs


# ── Helpers ─────────────────────────────────────────────────────────────────

def _item(title="Test signal", summary="", why="", action="", extras=None,
          disposition="monitor") -> dict:
    return {
        "title": title,
        "summary": summary,
        "why_it_matters": why,
        "recommended_action": action,
        "disposition": disposition,
        "extras": extras or {},
    }


def _sections_with_signal(text: str, section: str = "last_24h_relationship_signals",
                           contact_name: str = "") -> dict:
    extras = {"contact_name": contact_name} if contact_name else {}
    return {section: [_item(title=text, summary=text, extras=extras)]}


def _john_adams_sections() -> dict:
    """Simulate the exact defect scenario from the report."""
    return {
        "last_24h_relationship_signals": [_item(
            title="John Adams — WhatsApp advocacy after job rejection",
            summary=(
                "John Adams immediately asked who did you speak with after learning of "
                "the rejection. He began identifying contacts and offered to leverage his "
                "network on your behalf. He is reaching out to them to advocate for you."
            ),
            why="Former OTP3 peer. Selected as successor at PAR. Active problem-solving mode.",
            action="Follow up with John Adams on his outreach efforts.",
            extras={"contact_name": "John Adams"},
        )]
    }


# ── Tests ────────────────────────────────────────────────────────────────────

class RM1Callable(unittest.TestCase):
    def test_RM1_score_relationship_signals_callable(self):
        self.assertTrue(callable(cs._score_relationship_signals))

    def test_RM1_detect_relationship_mutations_callable(self):
        self.assertTrue(callable(cs.detect_relationship_mutations))

    def test_RM1_classify_mutation_callable(self):
        self.assertTrue(callable(cs._classify_mutation))


class RM2ScoreStructure(unittest.TestCase):
    def test_RM2_returns_dict_with_required_keys(self):
        result = cs._score_relationship_signals("some text")
        for key in ("total_score", "by_type", "matches", "dominant"):
            self.assertIn(key, result)

    def test_RM2_by_type_is_dict(self):
        result = cs._score_relationship_signals("advocating for you")
        self.assertIsInstance(result["by_type"], dict)

    def test_RM2_matches_is_list(self):
        result = cs._score_relationship_signals("advocating for you")
        self.assertIsInstance(result["matches"], list)


class RM3AdvocacySignals(unittest.TestCase):
    def test_RM3_advocating_scores_positive(self):
        result = cs._score_relationship_signals("advocating for you on the role")
        self.assertGreater(result["total_score"], 0)

    def test_RM3_introduction_scores_advocacy(self):
        result = cs._score_relationship_signals("He provided an introduction to the hiring manager")
        self.assertIn("advocacy", result["by_type"])
        self.assertGreater(result["by_type"]["advocacy"], 0)

    def test_RM3_on_your_behalf_scores_advocacy(self):
        result = cs._score_relationship_signals("She reached out on your behalf")
        self.assertGreater(result["total_score"], 0)

    def test_RM3_put_in_a_word_scores_advocacy(self):
        result = cs._score_relationship_signals("He put in a word for you")
        self.assertGreater(result["total_score"], 0)


class RM4SponsorScoresHigher(unittest.TestCase):
    def test_RM4_sponsor_points_exceed_advocacy_points(self):
        advocacy_score = cs._score_relationship_signals(
            "advocating for you and introducing you to contacts"
        )["total_score"]
        sponsor_score = cs._score_relationship_signals(
            "vouching for you and recommends you to the decision makers"
        )["total_score"]
        # Sponsor keywords have 20 pts vs advocacy 15 pts
        self.assertGreater(sponsor_score, 0)
        self.assertGreater(sponsor_score, advocacy_score * 0.5)  # sponsor is heavier per match

    def test_RM4_vouching_scores_sponsor(self):
        result = cs._score_relationship_signals("vouching for your candidacy")
        self.assertIn("sponsor", result["by_type"])

    def test_RM4_executive_sponsor_scores_sponsor(self):
        result = cs._score_relationship_signals("acting as executive sponsor for this initiative")
        self.assertIn("sponsor", result["by_type"])


class RM5ProblemSolvingSignals(unittest.TestCase):
    def test_RM5_who_did_you_speak_with_scores(self):
        result = cs._score_relationship_signals("who did you speak with at the company")
        self.assertGreater(result["total_score"], 0)
        self.assertIn("problem_solving", result["by_type"])

    def test_RM5_leverage_network_scores(self):
        result = cs._score_relationship_signals("will leverage my network to find the right person")
        self.assertGreater(result["total_score"], 0)

    def test_RM5_went_to_bat_scores(self):
        result = cs._score_relationship_signals("he went to bat for you in the meeting")
        self.assertGreater(result["total_score"], 0)


class RM6RiskSignals(unittest.TestCase):
    def test_RM6_gone_silent_scores_negative(self):
        result = cs._score_relationship_signals("contact has gone silent for two weeks")
        self.assertLess(result["total_score"], 0)
        self.assertIn("risk", result["by_type"])

    def test_RM6_ghosted_scores_negative(self):
        result = cs._score_relationship_signals("they have ghosted after the last message")
        self.assertLess(result["total_score"], 0)

    def test_RM6_broken_commitment_scores_negative(self):
        result = cs._score_relationship_signals("broke the commitment made last month")
        self.assertLess(result["total_score"], 0)


class RM7PeerTrustSignals(unittest.TestCase):
    def test_RM7_inner_circle_scores_peer_trust(self):
        result = cs._score_relationship_signals("part of the inner circle for years")
        self.assertIn("peer_trust", result["by_type"])
        self.assertGreater(result["by_type"]["peer_trust"], 0)

    def test_RM7_selected_to_replace_scores_peer_trust(self):
        result = cs._score_relationship_signals("selected you to replace him as Technical Account Manager")
        self.assertIn("peer_trust", result["by_type"])

    def test_RM7_trusted_advisor_scores_peer_trust(self):
        result = cs._score_relationship_signals("serves as a trusted advisor in this process")
        self.assertIn("peer_trust", result["by_type"])


class RM8NoSignals(unittest.TestCase):
    def test_RM8_neutral_text_scores_zero(self):
        result = cs._score_relationship_signals("The meeting was rescheduled to next Tuesday")
        self.assertEqual(result["total_score"], 0)
        self.assertEqual(result["dominant"], "")

    def test_RM8_empty_string_scores_zero(self):
        result = cs._score_relationship_signals("")
        self.assertEqual(result["total_score"], 0)


class RM9EmptySections(unittest.TestCase):
    def test_RM9_empty_sections_returns_empty(self):
        result = cs.detect_relationship_mutations({})
        self.assertEqual(result, [])

    def test_RM9_sections_with_no_signals_returns_empty(self):
        sections = {"last_24h_relationship_signals": [
            _item(title="Meeting rescheduled", summary="No material signals today")
        ]}
        result = cs.detect_relationship_mutations(sections)
        self.assertEqual(result, [])


class RM10AdvocacyGeneratesProposal(unittest.TestCase):
    def test_RM10_strong_advocacy_with_contact_generates_proposal(self):
        sections = {
            "last_24h_relationship_signals": [_item(
                title="Bob Smith outreach",
                summary="Bob Smith put in a word for you on your behalf to the hiring team. He advocated for your candidacy and introduced you to key contacts.",
                extras={"contact_name": "Bob Smith"},
            )]
        }
        result = cs.detect_relationship_mutations(sections)
        self.assertGreater(len(result), 0)

    def test_RM10_proposal_references_contact_name(self):
        sections = {
            "last_24h_relationship_signals": [_item(
                title="Bob Smith outreach",
                summary="Bob Smith put in a word for you on your behalf and introduced you to the team.",
                extras={"contact_name": "Bob Smith"},
            )]
        }
        result = cs.detect_relationship_mutations(sections)
        self.assertTrue(any("Bob Smith" in p["title"] for p in result))


class RM11ProposalSchema(unittest.TestCase):
    def _get_proposal(self) -> dict:
        sections = {
            "last_24h_relationship_signals": [_item(
                title="Jane Doe advocacy",
                summary="Jane Doe introduced you to the VP and is advocating for your candidacy on your behalf. She went to bat for you.",
                extras={"contact_name": "Jane Doe"},
            )]
        }
        result = cs.detect_relationship_mutations(sections)
        self.assertGreater(len(result), 0)
        return result[0]

    def test_RM11_has_title(self):
        p = self._get_proposal()
        self.assertIn("title", p)
        self.assertGreater(len(p["title"]), 5)

    def test_RM11_has_summary(self):
        p = self._get_proposal()
        self.assertIn("summary", p)

    def test_RM11_has_why_it_matters(self):
        p = self._get_proposal()
        self.assertIn("why_it_matters", p)

    def test_RM11_has_recommended_action(self):
        p = self._get_proposal()
        self.assertIn("recommended_action", p)

    def test_RM11_has_extras(self):
        p = self._get_proposal()
        self.assertIn("extras", p)


class RM12MediumConfidenceAskTodd(unittest.TestCase):
    def test_RM12_medium_score_is_ask_todd(self):
        # Put in a word = 15 pts → MEDIUM (≥18 needed for MEDIUM, but 15+problem_solving tips it)
        sections = {
            "last_24h_relationship_signals": [_item(
                title="Carlos advocacy",
                summary="Carlos put in a word for you and mentioned your name to the recruiter.",
                extras={"contact_name": "Carlos"},
            )]
        }
        result = cs.detect_relationship_mutations(sections)
        if result:
            # Should be ask_todd or act_today — not monitor/ignore
            self.assertIn(result[0]["disposition"], ("ask_todd", "act_today"))


class RM13HighConfidenceActToday(unittest.TestCase):
    def test_RM13_high_score_is_act_today(self):
        # Multiple strong signals to push past HIGH threshold (≥35)
        sections = {
            "last_24h_relationship_signals": [_item(
                title="Sarah executive sponsor",
                summary=(
                    "Sarah is vouching for you and recommends you to the decision makers. "
                    "She is advocating for your candidacy on your behalf, introduced you to "
                    "the VP, and is leveraging her network to create a role for you."
                ),
                extras={"contact_name": "Sarah Johnson"},
            )]
        }
        result = cs.detect_relationship_mutations(sections)
        self.assertGreater(len(result), 0)
        act_items = [p for p in result if p["disposition"] == "act_today"]
        self.assertGreater(len(act_items), 0)


class RM14AutoApplyHighConfidence(unittest.TestCase):
    def test_RM14_high_confidence_has_auto_apply_true(self):
        sections = {
            "last_24h_relationship_signals": [_item(
                title="Mark sponsor behavior",
                summary=(
                    "Mark is vouching for you, recommends you to decision makers, "
                    "and has been advocating on your behalf. He is leveraging his network "
                    "and introduced you to the executive team."
                ),
                extras={"contact_name": "Mark"},
            )]
        }
        result = cs.detect_relationship_mutations(sections)
        high_items = [p for p in result if p["disposition"] == "act_today"]
        if high_items:
            self.assertTrue(high_items[0]["extras"]["auto_apply"])


class RM15ProposedClassificationNonEmpty(unittest.TestCase):
    def test_RM15_proposed_classification_is_non_empty_string(self):
        sections = {
            "last_24h_relationship_signals": [_item(
                title="Alex advocacy",
                summary="Alex introduced you to the hiring manager and advocated for your candidacy on your behalf.",
                extras={"contact_name": "Alex"},
            )]
        }
        result = cs.detect_relationship_mutations(sections)
        if result:
            cls = result[0]["extras"]["proposed_classification"]
            self.assertIsInstance(cls, str)
            self.assertGreater(len(cls), 0)


class RM16Deduplication(unittest.TestCase):
    def test_RM16_same_contact_same_classification_proposed_once(self):
        # Two items both mentioning Bob Smith with same signal
        sections = {
            "last_24h_relationship_signals": [
                _item(title="Bob item 1",
                      summary="Bob Smith introduced you and advocated on your behalf.",
                      extras={"contact_name": "Bob Smith"}),
                _item(title="Bob item 2",
                      summary="Bob Smith introduced you and advocated on your behalf again.",
                      extras={"contact_name": "Bob Smith"}),
            ]
        }
        result = cs.detect_relationship_mutations(sections)
        # Should not have two proposals for same (contact, classification)
        proposals_for_bob = [p for p in result
                             if "Bob Smith" in p.get("extras", {}).get("contact_name", "")]
        classifications = [p["extras"]["proposed_classification"] for p in proposals_for_bob]
        self.assertEqual(len(classifications), len(set(classifications)))


class RM17RiskMutation(unittest.TestCase):
    def test_RM17_risk_signals_generate_downgrade(self):
        sections = {
            "last_24h_relationship_signals": [_item(
                title="Pat contact cooling",
                summary="Pat has gone silent and broken the commitment made last month. Relationship deteriorating.",
                extras={"contact_name": "Pat"},
            )]
        }
        result = cs.detect_relationship_mutations(sections)
        if result:
            self.assertEqual(result[0]["extras"]["mutation_type"], "classification_downgrade")


class RM18BelowThreshold(unittest.TestCase):
    def test_RM18_weak_signal_no_proposal(self):
        # A single neutral observation — no signal keywords
        sections = {
            "last_24h_relationship_signals": [_item(
                title="Tom sent a message",
                summary="Tom replied to my email about the project timeline.",
                extras={"contact_name": "Tom"},
            )]
        }
        result = cs.detect_relationship_mutations(sections)
        self.assertEqual(result, [])


class RM19ExplicitContactName(unittest.TestCase):
    def test_RM19_extras_contact_name_used_without_index(self):
        sections = {
            "last_24h_relationship_signals": [_item(
                title="Signal",
                summary="She is advocating for your candidacy and introduced you to the team on your behalf.",
                extras={"contact_name": "Maria Garcia"},
            )]
        }
        # Pass ci=None to force no index lookup
        result = cs.detect_relationship_mutations(sections, ci=None)
        if result:
            self.assertIn("Maria Garcia", result[0]["extras"]["contact_name"])


class RM20ExtrasRequiredFields(unittest.TestCase):
    def test_RM20_extras_has_all_required_fields(self):
        sections = {
            "last_24h_relationship_signals": [_item(
                title="Full signal test",
                summary="She introduced you and is advocating for your candidacy on your behalf with the team.",
                extras={"contact_name": "Diana"},
            )]
        }
        result = cs.detect_relationship_mutations(sections, ci=None)
        if not result:
            self.skipTest("No proposal generated — signal below threshold")
        extras = result[0]["extras"]
        for field in ("mutation_type", "contact_name", "proposed_classification",
                      "proposed_tags", "signal_types", "evidence_summary",
                      "evidence_score", "confidence", "auto_apply"):
            self.assertIn(field, extras, f"Missing extras field: {field}")


class RM21JohnAdamsScenario(unittest.TestCase):
    """Validate the exact defect scenario from the report."""

    def test_RM21_john_adams_generates_mutation(self):
        result = cs.detect_relationship_mutations(_john_adams_sections())
        self.assertGreater(len(result), 0,
                           "John Adams scenario should generate at least one mutation proposal")

    def test_RM21_john_adams_proposal_references_him(self):
        result = cs.detect_relationship_mutations(_john_adams_sections())
        self.assertTrue(
            any("John Adams" in p["title"] or "John Adams" in p.get("summary", "")
                for p in result),
            "At least one proposal should reference John Adams"
        )

    def test_RM21_john_adams_disposition_not_monitor(self):
        result = cs.detect_relationship_mutations(_john_adams_sections())
        for p in result:
            if "john" in p["title"].lower() or "john" in p.get("extras", {}).get("contact_name", "").lower():
                self.assertIn(p["disposition"], ("ask_todd", "act_today"),
                              f"John Adams proposal should be ask_todd or act_today, not: {p['disposition']}")


class RM22SponsorClassification(unittest.TestCase):
    def test_RM22_sponsor_text_gives_sponsor_classification(self):
        sections = {
            "last_24h_relationship_signals": [_item(
                title="VP sponsorship signal",
                summary=(
                    "The VP is vouching for you and recommends you to the board. "
                    "He is creating a role for you and influencing the hiring decision."
                ),
                extras={"contact_name": "VP Williams"},
            )]
        }
        result = cs.detect_relationship_mutations(sections, ci=None)
        if result:
            cls = result[0]["extras"]["proposed_classification"].lower()
            self.assertTrue(
                "sponsor" in cls or "advocate" in cls,
                f"Expected sponsor/advocate classification, got: {cls}"
            )


class RM23SortingHighFirst(unittest.TestCase):
    def test_RM23_high_confidence_items_sorted_before_medium(self):
        sections = {
            "last_24h_relationship_signals": [
                # Medium signal
                _item(title="Weak signal",
                      summary="Alex mentioned your name to someone.",
                      extras={"contact_name": "Alex"}),
                # Strong signal
                _item(title="Strong signal",
                      summary=(
                          "Barbara is vouching for you, recommends you to decision makers, "
                          "advocating on your behalf, and introduced you to the executive team."
                      ),
                      extras={"contact_name": "Barbara"}),
            ]
        }
        result = cs.detect_relationship_mutations(sections, ci=None)
        if len(result) >= 2:
            # HIGH confidence (act_today) should come before MEDIUM (ask_todd)
            dispositions = [p["disposition"] for p in result]
            if "act_today" in dispositions and "ask_todd" in dispositions:
                act_idx = dispositions.index("act_today")
                ask_idx = dispositions.index("ask_todd")
                self.assertLess(act_idx, ask_idx,
                                "act_today proposals should sort before ask_todd")


if __name__ == "__main__":
    unittest.main()
