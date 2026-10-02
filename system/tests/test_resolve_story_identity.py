"""
test_resolve_story_identity.py

RB-DEFECT-2026-07-20 Phase 2: replaces the patchwork of _story_anchor_nouns /
_corporate_subject_signature / _corporate_story_already_covered's title-regex
matching with one canonical (entity_key, event_type, disambiguator) signature.
Every previous point-fix closed exactly one disguise the same real-world
event could wear; this module is meant to close the class of bug instead.

Test data is the real, live evidence gathered 2026-07-20: all 9 registry
entries for "Wonder" that motivated this fix, spanning three genuinely
different real-world events (an earlier $600M pre-IPO round, a Mighty
Quinn's BBQ acquisition, and the later $650M Series D round). The target:
- the two $600M items match each other (story_keys_match) but not the $650M items
- the four $650M items all match each other
- the acquisition never matches either funding cluster
- the one genuinely unresolvable roundup headline degrades to None gracefully
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import rb_core as core  # noqa: E402


# The real, live titles from system/.cache/rendered_headlines.json (2026-07-20).
_WONDER_600M_1 = "[💰 FUNDING] Wonder looks to raise $600M in a reported pre-IPO funding round"
_WONDER_600M_2 = "[💰 FUNDING] Wonder plans another $600M funding round"
_WONDER_ACQUISITION = "[🏢 ACQUISITION] Wonder acquires Mighty Quinn's BBQ"
_WONDER_ROUNDUP = "[💰 FUNDING] The chicken wars heat up, value menu overload, and Wonder's IPO plans"
_WONDER_650M_1 = "Wonder is valued at more than $9B after latest fundraise"
_WONDER_650M_2 = "[💰 FUNDING] Wonder raises $650M in Series D funding at $9B valuation"
_WONDER_650M_3 = "[💰 FUNDING] Wonder tops $9B valuation, raises $650M"
_WONDER_650M_4 = "[💰 FUNDING] Wonder Raises $650 Million at $9 Billion Valuation"


class TestResolveStoryIdentityWonderRealData(unittest.TestCase):
    def test_600m_round_items_match_each_other(self):
        a = core.resolve_story_identity(_WONDER_600M_1)
        b = core.resolve_story_identity(_WONDER_600M_2)
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertTrue(core.story_keys_match(a, b))

    def test_650m_round_items_all_match_each_other(self):
        keys = [
            core.resolve_story_identity(t)
            for t in (_WONDER_650M_1, _WONDER_650M_2, _WONDER_650M_3, _WONDER_650M_4)
        ]
        self.assertTrue(all(k is not None for k in keys))
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                self.assertTrue(
                    core.story_keys_match(keys[i], keys[j]),
                    f"expected match between {keys[i]} and {keys[j]}",
                )

    def test_600m_and_650m_rounds_do_not_match(self):
        a = core.resolve_story_identity(_WONDER_600M_1)
        b = core.resolve_story_identity(_WONDER_650M_1)
        self.assertFalse(core.story_keys_match(a, b))

    def test_acquisition_never_matches_either_funding_cluster(self):
        acq = core.resolve_story_identity(_WONDER_ACQUISITION)
        f600 = core.resolve_story_identity(_WONDER_600M_1)
        f650 = core.resolve_story_identity(_WONDER_650M_1)
        self.assertIsNotNone(acq)
        self.assertFalse(core.story_keys_match(acq, f600))
        self.assertFalse(core.story_keys_match(acq, f650))

    def test_duplicate_acquisition_title_matches_itself(self):
        """The real registry has this exact title twice under different
        (republished) URLs -- identical titles must resolve identically."""
        a = core.resolve_story_identity(_WONDER_ACQUISITION)
        b = core.resolve_story_identity(_WONDER_ACQUISITION)
        self.assertTrue(core.story_keys_match(a, b))

    def test_unresolvable_roundup_headline_degrades_to_none(self):
        """"The chicken wars heat up..." opens with a stopword, not a proper
        noun -- must not guess a wrong entity_key like "the"."""
        self.assertIsNone(core.resolve_story_identity(_WONDER_ROUNDUP))


class TestResolveStoryIdentityGeneralCases(unittest.TestCase):
    def test_multi_word_company_name_kept_together(self):
        key = core.resolve_story_identity(
            "[👤 EXEC HIRE] Domino's Pizza appoints 2 new directors, "
            "names Corie Barry lead independent director of the board"
        )
        self.assertEqual(key[0], "domino's pizza")
        self.assertEqual(key[1], "exec_change")

    def test_headline_verb_not_swept_into_entity_name(self):
        """"Wonder Raises $650 Million..." must resolve entity_key to just
        "wonder", not "wonder raises" -- Raises is a Title-Case headline
        verb, not part of the company name."""
        key = core.resolve_story_identity(_WONDER_650M_4)
        self.assertEqual(key[0], "wonder")

    def test_curly_apostrophe_normalized_to_straight(self):
        """RB-DEFECT-2026-07-23: entity_key used to keep whichever apostrophe
        glyph the title happened to use -- Restaurant Dive's "Jersey Mike’s"
        (curly) and Fast Casual's "Jersey Mike's" (straight) resolved to two
        literally different entity_key strings for the same IPO story, so
        story_keys_match's entity equality check rejected the match outright.
        Both styles must normalize to the same key."""
        key = core.resolve_story_identity(
            "[👤 EXEC HIRE] McDonald’s hires Nike vet as chief strategy officer"
        )
        self.assertEqual(key[0], "mcdonald's")
        # regression: a naive [A-Z][a-z]{3,} scan matches "Donald" out of the
        # middle of "McDonald" -- must not appear as a spurious disambiguator
        self.assertNotIn("donald", key[2])

    def test_curly_and_straight_apostrophe_same_entity_match(self):
        a = core.resolve_story_identity(
            "[💰 FUNDING] Jersey Mike’s IPO could raise over $1B"
        )
        b = core.resolve_story_identity(
            "[💰 FUNDING] Jersey Mike's launches IPO, plans to list on NYSE"
        )
        self.assertEqual(a[0], b[0])
        self.assertTrue(core.story_keys_match(a, b))

    def test_different_companies_never_match(self):
        a = core.resolve_story_identity("[🏢 ACQUISITION] GoTab Acquires Fishbowl")
        b = core.resolve_story_identity("[🏢 ACQUISITION] Wonder acquires Mighty Quinn's BBQ")
        self.assertFalse(core.story_keys_match(a, b))

    def test_leading_subject_preferred_over_watchlist_entity_match(self):
        """RB-DEFECT-2026-07-20: extras.entities is a watchlist-term-
        mentioned-anywhere match, not necessarily the headline's subject --
        confirmed live, a real Wonder funding headline carried
        entities=["Grubhub"] purely because the body text mentioned Wonder
        would use the funding for "Grubhub growth." The title-derived
        leading subject ("Wonder") must win, not the incidental mention."""
        key = core.resolve_story_identity(
            "[💰 FUNDING] Wonder is valued at more than $9B after latest fundraise",
            extras={"entities": ["Grubhub"]},
        )
        self.assertEqual(key[0], "wonder")

    def test_watchlist_entity_used_as_fallback_when_no_leading_subject(self):
        """When the title has no clean leading proper noun (a roundup
        headline), a watchlist entity match is better than nothing."""
        key = core.resolve_story_identity(
            "[💰 FUNDING] The chicken wars heat up, value menu overload, and Wonder's IPO plans",
            extras={"entities": ["Wonder"]},
        )
        self.assertIsNotNone(key)
        self.assertEqual(key[0], "wonder")

    def test_no_title_no_entities_returns_none(self):
        self.assertIsNone(core.resolve_story_identity("", extras={}))

    def test_extra_disambiguator_detail_still_overlaps(self):
        """One outlet's write-up mentioning an extra detail ("pre-IPO") that
        another's omits must still overlap via the shared core token."""
        a = core.resolve_story_identity(_WONDER_600M_1)  # has "pre-ipo" + "$600m"
        b = core.resolve_story_identity(_WONDER_600M_2)  # has only "$600m"
        self.assertTrue(a[2] & b[2])
        self.assertTrue(core.story_keys_match(a, b))

    def test_exact_key_equality_is_sufficient_without_calling_story_keys_match(self):
        a = core.resolve_story_identity(_WONDER_ACQUISITION)
        b = core.resolve_story_identity(_WONDER_ACQUISITION)
        self.assertEqual(a, b)


class TestResolveStoryIdentityIpoAcronym(unittest.TestCase):
    """RB-DEFECT-2026-07-22: "Jersey Mike's IPO could raise..." and "Jersey
    Mike's launches IPO..." resolved to different entity_keys ("jersey
    mike's ipo" vs "jersey mike's") purely because the verb "launches" broke
    the leading-subject phrase before IPO could be swept in, while the first
    headline had nothing to stop the acronym from being treated as part of
    the company name. The real brief showed the same IPO story 3x across
    outlets as a result."""

    def test_ipo_immediately_after_name_does_not_join_entity_key(self):
        key = core.resolve_story_identity(
            "[💰 FUNDING] Jersey Mike's IPO could raise $8B, per report"
        )
        self.assertEqual(key[0], "jersey mike's")

    def test_ipo_after_verb_and_ipo_immediately_after_name_share_entity_key(self):
        a = core.resolve_story_identity(
            "[💰 FUNDING] Jersey Mike's IPO could raise $8B, per report"
        )
        b = core.resolve_story_identity(
            "[💰 FUNDING] Jersey Mike's launches IPO, eyes $10B valuation"
        )
        self.assertEqual(a[0], b[0])
        self.assertTrue(core.story_keys_match(a, b))

    def test_ipo_disambiguator_token_shared_despite_different_dollar_amounts(self):
        a = core.resolve_story_identity(
            "[💰 FUNDING] Jersey Mike's IPO could raise $8B, per report"
        )
        b = core.resolve_story_identity(
            "[💰 FUNDING] Jersey Mike's launches IPO, eyes $10B valuation"
        )
        self.assertIn("ipo", a[2])
        self.assertIn("ipo", b[2])

    def test_other_event_acronyms_also_excluded_from_entity_key(self):
        key = core.resolve_story_identity(
            "[👤 EXEC HIRE] Wonder CEO steps down amid restructuring"
        )
        self.assertEqual(key[0], "wonder")


class TestStoryKeysMatchSingularPerWindowTypes(unittest.TestCase):
    """RB-2026-08-25: live in the 2026-08-25 Intelligence Brief -- the same
    exec-hire event told twice in Section C, once per outlet, because the two
    headlines' extra-capitalized-word disambiguators didn't overlap:
    Restaurant Dive named the predecessor employer ("McDonald's"), NRN named
    the new hire ("Tariq Hassan"). Same real-world event, zero shared token,
    rendered as two separate stories."""

    def test_exec_hire_same_entity_matches_even_with_disjoint_disambiguators(self):
        a = core.resolve_story_identity(
            "[👤 EXEC HIRE] Wendy's hires ex-McDonald's CMO as chief marketing, customer growth officer"
        )
        b = core.resolve_story_identity(
            "[👤 EXEC HIRE] Wendy's names Tariq Hassan chief marketing and customer growth officer"
        )
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertEqual(a[0], b[0])
        self.assertNotEqual(a[2], b[2])  # disambiguators genuinely don't overlap
        self.assertTrue(core.story_keys_match(a, b))

    def test_exec_hire_co_ceo_announcement_matches_across_outlets(self):
        a = core.resolve_story_identity(
            "[👤 EXEC HIRE] Levain Bakery names co-CEOs"
        )
        b = core.resolve_story_identity(
            "[👤 EXEC HIRE] Levain Bakery Names Lorna Sommerville and Taya Stenson Co-CEOs"
        )
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertTrue(core.story_keys_match(a, b))

    def test_exec_hire_different_entity_still_never_matches(self):
        a = core.resolve_story_identity(
            "[👤 EXEC HIRE] Wendy's names Tariq Hassan chief marketing officer"
        )
        b = core.resolve_story_identity(
            "[👤 EXEC HIRE] Levain Bakery names co-CEOs"
        )
        self.assertFalse(core.story_keys_match(a, b))

    def test_acquisition_vs_funding_round_same_entity_still_distinct(self):
        """Guards the fix's scoping: singular-per-window collapse must
        require BOTH sides to share the same type, not just either side --
        an acquisition and a funding round for the same company are still
        different stories even though acquisition alone is in the singular
        set."""
        acq = core.resolve_story_identity(_WONDER_ACQUISITION)
        f600 = core.resolve_story_identity(_WONDER_600M_1)
        self.assertFalse(core.story_keys_match(acq, f600))


if __name__ == "__main__":
    unittest.main()
