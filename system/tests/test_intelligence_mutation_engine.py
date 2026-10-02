#!/usr/bin/env python3
"""test_intelligence_mutation_engine.py — RB Unified Restaurant-Tech Graph
(2026-07-31), Phase 4 (scoped down).

intelligence_mutation_engine.py (1400+ lines) previously had no dedicated test
file -- coverage was fragmented across caller-named test files. This file
anchors coverage added for Phase 4: win/renewal/churn language detected in
article text is now tagged onto vendor_customer_relationship mutations as a
`reconciliation_outcome`, using the same vocabulary
(ecosystem_intelligence.RECONCILIATION_OUTCOMES) as Phase 2's workbook
migration tool, so a daily-signal mutation and a workbook-migration row speak
the same language downstream.

Test IDs:
  IME1  — generate_mutations returns the expected top-level shape
  IME2  — plain deployment language -> reconciliation_outcome=new_relationship
  IME3  — renewal/expansion language -> reconciliation_outcome=lifecycle_update
  IME4  — churn/replacement language -> reconciliation_outcome=supersedes_existing_relationship
  IME5  — reconciliation_outcome is always a member of eco.RECONCILIATION_OUTCOMES
  IME6  — churn mutations always require confirmation, regardless of confidence
  IME7  — non-churn confidence-gated auto-apply/propose triage is unchanged
  IME8  — apply_mutations() dry_run path is unaffected by the new tagging
  IME9  — category_lifecycle_signal (sunset) mutations are unaffected by the
          RELATIONSHIP_VERBS extension
  IME10 — Presto/Hi Auto-style churn sentence produces two mutations, both
          correctly tagged and both routed to human review
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import intelligence_mutation_engine as ime  # noqa: E402
import ecosystem_intelligence as eco  # noqa: E402


def _empty_ecosystem() -> dict:
    return {"entities": [], "relationships": []}


class IME1_Shape(unittest.TestCase):
    def test_generate_mutations_returns_expected_shape(self):
        result = ime.generate_mutations(
            "Checkers deploys Hi Auto for voice AI drive-thru ordering.",
            source_title="t",
            ecosystem=_empty_ecosystem(),
            baseline=[],
            strategic_memory={"signals": []},
        )
        for key in ("mutations", "auto_applied_mutations", "proposed_mutations"):
            self.assertIn(key, result)
        self.assertIsInstance(result["mutations"], list)


class IME2_WinLanguage(unittest.TestCase):
    def test_plain_deployment_tagged_new_relationship(self):
        result = ime.generate_mutations(
            "Checkers deploys Hi Auto for voice AI drive-thru ordering.",
            source_title="t",
            ecosystem=_empty_ecosystem(),
            baseline=[],
            strategic_memory={"signals": []},
        )
        vc = [m for m in result["mutations"] if m["type"] == "vendor_customer_relationship"]
        self.assertEqual(len(vc), 1)
        self.assertEqual(vc[0]["to_entity_id"], "vendor-hi-auto")
        self.assertEqual(vc[0]["reconciliation_outcome"], "new_relationship")


class IME3_RenewalLanguage(unittest.TestCase):
    def test_renewal_tagged_lifecycle_update(self):
        result = ime.generate_mutations(
            "Wendy's renews its agreement with Toast for POS across all locations.",
            source_title="t",
            ecosystem=_empty_ecosystem(),
            baseline=[],
            strategic_memory={"signals": []},
        )
        vc = [m for m in result["mutations"] if m["type"] == "vendor_customer_relationship"]
        self.assertEqual(len(vc), 1)
        self.assertEqual(vc[0]["to_entity_id"], "vendor-toast")
        self.assertEqual(vc[0]["reconciliation_outcome"], "lifecycle_update")

    def test_expansion_tagged_lifecycle_update(self):
        result = ime.generate_mutations(
            "Wingstop expands its rollout of Olo across additional locations.",
            source_title="t",
            ecosystem=_empty_ecosystem(),
            baseline=[],
            strategic_memory={"signals": []},
        )
        vc = [m for m in result["mutations"] if m["type"] == "vendor_customer_relationship"]
        self.assertEqual(len(vc), 1)
        self.assertEqual(vc[0]["reconciliation_outcome"], "lifecycle_update")


class IME4_ChurnLanguage(unittest.TestCase):
    def test_replacement_tagged_supersedes(self):
        result = ime.generate_mutations(
            "Checkers replaces Presto with Hi Auto for drive-thru voice AI.",
            source_title="t",
            ecosystem=_empty_ecosystem(),
            baseline=[],
            strategic_memory={"signals": []},
        )
        vc = [m for m in result["mutations"] if m["type"] == "vendor_customer_relationship"]
        self.assertEqual(len(vc), 2)
        for m in vc:
            self.assertEqual(m["reconciliation_outcome"], "supersedes_existing_relationship")


class IME5_VocabularyConsistency(unittest.TestCase):
    def test_all_reconciliation_outcomes_are_valid_vocabulary(self):
        texts = [
            "Checkers deploys Hi Auto for voice AI drive-thru ordering.",
            "Wendy's renews its agreement with Toast for POS across all locations.",
            "Checkers replaces Presto with Hi Auto for drive-thru voice AI.",
            "Wingstop also uses Olo along with Toast for online ordering.",
        ]
        for text in texts:
            result = ime.generate_mutations(
                text, source_title="t", ecosystem=_empty_ecosystem(),
                baseline=[], strategic_memory={"signals": []},
            )
            for m in result["mutations"]:
                if m["type"] != "vendor_customer_relationship":
                    continue
                self.assertIn(
                    m["reconciliation_outcome"], eco.RECONCILIATION_OUTCOMES,
                    f"{m['reconciliation_outcome']!r} not in canonical vocabulary for: {text}",
                )


class IME6_ChurnAlwaysRequiresConfirmation(unittest.TestCase):
    def test_churn_mutation_requires_confirmation_even_at_high_confidence(self):
        result = ime.generate_mutations(
            "Checkers replaces Presto with Hi Auto for drive-thru voice AI.",
            source_title="t",
            ecosystem=_empty_ecosystem(),
            baseline=[],
            strategic_memory={"signals": []},
        )
        vc = [m for m in result["mutations"] if m["type"] == "vendor_customer_relationship"]
        for m in vc:
            self.assertGreaterEqual(m["confidence"], 0.75)  # would auto-apply if not for the churn override
            self.assertTrue(m["requires_confirmation"])
        # A churn-tagged mutation must never appear in auto_applied_mutations.
        auto_ids = {m["id"] for m in result["auto_applied_mutations"]}
        for m in vc:
            self.assertNotIn(m["id"], auto_ids)


class IME7_NonChurnTriageUnchanged(unittest.TestCase):
    """The existing confidence-gated auto-apply/propose split (Stage 4) must
    not be reinvented -- only churn mutations get a special override."""

    def test_high_confidence_win_is_auto_applied(self):
        result = ime.generate_mutations(
            "Checkers deploys Hi Auto for voice AI drive-thru ordering.",
            source_title="t",
            ecosystem=_empty_ecosystem(),
            baseline=[],
            strategic_memory={"signals": []},
        )
        vc = [m for m in result["mutations"] if m["type"] == "vendor_customer_relationship"][0]
        self.assertFalse(vc["requires_confirmation"])
        auto_ids = {m["id"] for m in result["auto_applied_mutations"]}
        self.assertIn(vc["id"], auto_ids)


class IME8_ApplyMutationsDryRun(unittest.TestCase):
    def test_dry_run_reports_would_apply_without_writing(self):
        result = ime.generate_mutations(
            "Checkers deploys Hi Auto for voice AI drive-thru ordering.",
            source_title="t",
            ecosystem=_empty_ecosystem(),
            baseline=[],
            strategic_memory={"signals": []},
        )
        applied = ime.apply_mutations(result, dry_run=True)
        self.assertTrue(applied["dry_run"])
        self.assertEqual(applied["would_apply"], len(result["auto_applied_mutations"]))


class IME9_CategoryLifecycleUnaffected(unittest.TestCase):
    def test_sunset_language_still_detected(self):
        result = ime.generate_mutations(
            "Hungry Howie's franchise system is sunsetting its loyalty program in favor of a new platform.",
            source_title="t",
            ecosystem=_empty_ecosystem(),
            baseline=[],
            strategic_memory={"signals": []},
        )
        lifecycle = [m for m in result["mutations"] if m["type"] == "category_lifecycle_signal"]
        self.assertTrue(lifecycle, "expected a category_lifecycle_signal mutation")
        self.assertEqual(lifecycle[0]["category"], "loyalty")


class IME10_PrestoHiAutoChurnScenario(unittest.TestCase):
    """Mirrors the real Checkers & Rally's / Presto / Hi Auto correction that
    motivated Phase 2's workbook migration tool -- here on the live-signal
    (article text) path rather than the workbook path."""

    def test_both_vendors_identified_and_routed_to_review(self):
        result = ime.generate_mutations(
            "Checkers & Rally's replaces Presto with Hi Auto for drive-thru voice AI ordering.",
            source_title="Trade press item",
            ecosystem=_empty_ecosystem(),
            baseline=[],
            strategic_memory={"signals": []},
        )
        vc = {m["to_entity_id"]: m for m in result["mutations"] if m["type"] == "vendor_customer_relationship"}
        self.assertIn("vendor-presto", vc)
        self.assertIn("vendor-hi-auto", vc)
        for m in vc.values():
            self.assertEqual(m["reconciliation_outcome"], "supersedes_existing_relationship")
            self.assertTrue(m["requires_confirmation"])


class IME11_ExecTitlePatternCaseSensitivityDefect(unittest.TestCase):
    """RB-DEFECT-2026-08-29: EXEC_TITLE_PATTERN/QUOTE_PATTERN/BLOCK_QUOTE_PATTERN
    used to compile with a blanket re.I, which made [A-Z] and [a-z] equivalent
    and silently defeated the Title-Case shape the (?P<name>...) groups were
    written to enforce. Confirmed live: a real LinkedIn screenshot ("...was at
    the centre of CEO concerns...") produced a persisted executive_pov mutation
    naming "at the centre of" as a fabricated "CEO". Fixed by scoping
    case-insensitivity to only the keyword alternations."""

    def test_lowercase_phrase_before_title_keyword_is_not_a_fabricated_exec(self):
        text = "Analysts said this was at the centre of CEO concerns about the rollout timeline."
        matches = list(ime.EXEC_TITLE_PATTERN.finditer(text))
        self.assertEqual(matches, [])

    def test_real_proper_case_exec_mention_still_matches(self):
        text = "Brian Niccol, CEO at Starbucks, announced the rollback today."
        matches = list(ime.EXEC_TITLE_PATTERN.finditer(text))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].group("name"), "Brian Niccol")
        self.assertEqual(matches[0].group("company"), "Starbucks")

    def test_lowercase_title_keyword_still_tolerated(self):
        """The scoped (?i:...) around the keyword alternation should still
        accept a lowercase role word -- only the name/company shape
        constraint needed to stay strict."""
        text = "Brian Niccol, ceo at Starbucks, announced the rollback today."
        matches = list(ime.EXEC_TITLE_PATTERN.finditer(text))
        self.assertEqual(len(matches), 1)

    def test_quote_pattern_rejects_lowercase_phrase_as_speaker(self):
        text = '"this is fine" said the rollout was fine'
        self.assertEqual(list(ime.QUOTE_PATTERN.finditer(text)), [])

    def test_quote_pattern_still_matches_real_attribution(self):
        text = '"This is a big shift for the industry," said Brian Niccol.'
        matches = list(ime.QUOTE_PATTERN.finditer(text))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].group("name"), "Brian Niccol")

    def test_end_to_end_no_fabricated_executive_from_real_incident_text(self):
        """The actual production sentence shape that produced the bad
        mutation, run through the full extraction path."""
        result = ime.generate_mutations(
            "Starbucks scrapped its AI inventory tool. Analysts said this "
            "rollback was at the centre of CEO concerns about the vendor.",
            source_title="pasted-screenshot.png",
            ecosystem=_empty_ecosystem(),
            baseline=[],
            strategic_memory={"signals": []},
        )
        exec_mutations = [m for m in result["mutations"] if m["type"] == "executive_pov"]
        for m in exec_mutations:
            self.assertNotEqual(m["executive"]["name"], "at the centre of")


if __name__ == "__main__":
    unittest.main(verbosity=2)
