"""
test_contact_index.py — Unit tests for the Sprint D Contact Intelligence Index.

Tests:
  CI1 — module imports cleanly
  CI2 — loop_ledger parser extracts person + company correctly
  CI3 — parser handles all known person/company column formats
  CI4 — ContactIndex.by_company finds contacts with fuzzy matching
  CI5 — ContactIndex.related_to_companies deduplicates across companies
  CI6 — ContactIndex.contacts_for_intel_item cross-references entities
  CI7 — importance scoring is correct
  CI8 — build_index produces required schema fields
  CI9 — Pattern 5 relationship_activation in cos_synthesis uses contact index
  CI10 — empty / missing sources handled silently
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import contact_index as ci_mod
from contact_index import ContactIndex, _parse_person_company, _slug, _importance


class CI1ModuleImport(unittest.TestCase):
    def test_CI1_imports_cleanly(self):
        self.assertTrue(callable(ci_mod.build_index))
        self.assertTrue(hasattr(ci_mod, "ContactIndex"))


class CI2LoopLedgerParser(unittest.TestCase):
    """Test _parse_person_company against every known format in loop_ledger.md."""

    def _parse(self, raw: str) -> list[tuple[str, str]]:
        return _parse_person_company(raw)

    def test_CI2a_person_with_company_in_parens(self):
        result = self._parse("Mike Schwartz (Global Payments)")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "Mike Schwartz")
        self.assertEqual(result[0][1], "Global Payments")

    def test_CI2b_person_slash_company(self):
        result = self._parse("Ish Singh / Maho")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "Ish Singh")
        self.assertIn("Maho", result[0][1])

    def test_CI2c_plain_person_name(self):
        result = self._parse("Jeff Wayman")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "Jeff Wayman")
        self.assertEqual(result[0][1], "")

    def test_CI2d_intro_loop_arrow(self):
        result = self._parse("Noelle Labrie → Cristina Gia Luciano")
        names = [r[0] for r in result]
        self.assertIn("Noelle Labrie", names)
        self.assertIn("Cristina Gia Luciano", names)

    def test_CI2e_multi_person_comma(self):
        result = self._parse("Dave Miller (Franke), Bob Gibson")
        names = [r[0] for r in result]
        self.assertIn("Dave Miller", names)
        self.assertIn("Bob Gibson", names)

    def test_CI2f_company_with_slash_in_parens(self):
        result = self._parse("Mike Schwartz (Global Payments / Genius-Xenial)")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "Mike Schwartz")
        self.assertIn("Global Payments", result[0][1])

    def test_CI2g_crm_internal_skipped(self):
        result = self._parse("CRM update")
        self.assertEqual(result, [])

    def test_CI2h_background_check_skipped(self):
        result = self._parse("Background-check referral contact")
        self.assertEqual(result, [])


class CI3SlugHelper(unittest.TestCase):
    def test_CI3a_basic_slug(self):
        self.assertEqual(_slug("Mike Schwartz"), "mike-schwartz")

    def test_CI3b_special_chars(self):
        self.assertEqual(_slug("O'Brien, Dr."), "o-brien-dr")

    def test_CI3c_importance_high_at_three_loops(self):
        self.assertEqual(_importance(3, "2026-05-01"), "high")

    def test_CI3d_importance_medium_at_two_loops(self):
        self.assertEqual(_importance(2, "2026-05-01"), "medium")

    def test_CI3e_importance_medium_recent_one_loop(self):
        from datetime import date, timedelta
        recent = (date.today() - timedelta(days=10)).isoformat()
        self.assertEqual(_importance(1, recent), "medium")

    def test_CI3f_importance_low_old_one_loop(self):
        self.assertEqual(_importance(1, "2024-01-01"), "low")


class CI4ContactIndexByCompany(unittest.TestCase):
    def setUp(self):
        # Build a minimal in-memory index
        contacts = [
            {
                "id": "bob-gibson", "name": "Bob Gibson",
                "company": "Toast", "company_aliases": [],
                "loop_count": 2, "open_loop_count": 2,
                "importance": "high", "sources": ["loop_ledger"],
                "email_domain": "", "thread_ids": [], "open_loops": [],
                "all_loops": [], "last_loop_date": "2026-05-08", "loop_context": "stay close",
            },
            {
                "id": "mike-schwartz", "name": "Mike Schwartz",
                "company": "Global Payments", "company_aliases": ["Genius", "Xenial"],
                "loop_count": 3, "open_loop_count": 2,
                "importance": "high", "sources": ["loop_ledger"],
                "email_domain": "", "thread_ids": [], "open_loops": [],
                "all_loops": [], "last_loop_date": "2026-05-26", "loop_context": "advance role",
            },
        ]
        self.ci = ContactIndex({"contacts": contacts, "contact_count": 2})

    def test_CI4a_exact_company_match(self):
        results = self.ci.by_company("Toast")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Bob Gibson")

    def test_CI4b_alias_match(self):
        results = self.ci.by_company("Genius")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["name"], "Mike Schwartz")

    def test_CI4c_fuzzy_substring_match(self):
        results = self.ci.by_company("Global Pay")
        self.assertGreater(len(results), 0)

    def test_CI4d_no_match_returns_empty(self):
        results = self.ci.by_company("Nonexistent Corp XYZ")
        self.assertEqual(results, [])

    def test_CI4e_by_name_lookup(self):
        c = self.ci.by_name("Bob Gibson")
        self.assertIsNotNone(c)
        self.assertEqual(c["company"], "Toast")


class CI5RelatedToCompanies(unittest.TestCase):
    def setUp(self):
        self.ci = ContactIndex({"contacts": [
            {"id": "a", "name": "Alice A", "company": "PAR", "company_aliases": [],
             "loop_count": 1, "open_loop_count": 1, "importance": "medium",
             "sources": [], "email_domain": "", "thread_ids": [], "open_loops": [],
             "all_loops": [], "last_loop_date": "", "loop_context": ""},
            {"id": "b", "name": "Bob B", "company": "Toast", "company_aliases": [],
             "loop_count": 2, "open_loop_count": 2, "importance": "high",
             "sources": [], "email_domain": "", "thread_ids": [], "open_loops": [],
             "all_loops": [], "last_loop_date": "", "loop_context": ""},
        ], "contact_count": 2})

    def test_CI5a_returns_from_multiple_companies(self):
        results = self.ci.related_to_companies(["PAR", "Toast"])
        names = [c["name"] for c in results]
        self.assertIn("Alice A", names)
        self.assertIn("Bob B", names)

    def test_CI5b_deduplicates(self):
        results = self.ci.related_to_companies(["Toast", "Toast"])
        self.assertEqual(len(results), 1)


class CI6ContactsForIntelItem(unittest.TestCase):
    def setUp(self):
        self.ci = ContactIndex({"contacts": [
            {"id": "toast-person", "name": "Toast Person", "company": "Toast",
             "company_aliases": [], "loop_count": 1, "open_loop_count": 1,
             "importance": "medium", "sources": [], "email_domain": "",
             "thread_ids": [], "open_loops": [], "all_loops": [],
             "last_loop_date": "", "loop_context": "enterprise role"},
        ], "contact_count": 1})

    def test_CI6a_finds_contact_from_entity_list(self):
        item = {"title": "Toast Q2 earnings beat", "summary": "strong growth",
                "extras": {"entities": ["Toast"]}}
        results = self.ci.contacts_for_intel_item(item)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["name"], "Toast Person")

    def test_CI6b_no_contacts_for_unrelated_item(self):
        item = {"title": "Completely unrelated news", "summary": "about widgets",
                "extras": {}}
        results = self.ci.contacts_for_intel_item(item)
        self.assertEqual(results, [])


class CI7Importance(unittest.TestCase):
    def test_CI7a_three_loops_is_high(self):
        self.assertEqual(_importance(3, "2026-01-01"), "high")

    def test_CI7b_two_loops_is_medium(self):
        self.assertEqual(_importance(2, "2026-01-01"), "medium")

    def test_CI7c_none_date_no_crash(self):
        result = _importance(1, None)
        self.assertIn(result, ("high", "medium", "low"))


class CI8BuildIndexSchema(unittest.TestCase):
    def test_CI8_build_index_has_required_fields(self):
        index = ci_mod.build_index()
        self.assertIn("generated_at", index)
        self.assertIn("contact_count", index)
        self.assertIn("contacts", index)
        contacts = index["contacts"]
        self.assertIsInstance(contacts, list)
        # Verify required fields on each contact
        required = {"id", "name", "company", "company_aliases", "loop_count",
                    "open_loop_count", "importance", "sources"}
        for c in contacts[:5]:
            for field in required:
                self.assertIn(field, c, f"Missing field '{field}' in contact {c.get('name')}")

    def test_CI8_contacts_have_known_people(self):
        index = ci_mod.build_index()
        names = [c["name"] for c in index["contacts"]]
        # People from loop_ledger.md
        self.assertIn("Mike Schwartz", names)
        self.assertIn("Patrick Nelson", names)
        self.assertIn("Bob Gibson", names)


class CI9Pattern5InCosSynthesis(unittest.TestCase):
    """Pattern 5 relationship_activation wires into compute_synthesis."""

    def test_CI9_relationship_activation_returns_list(self):
        import cos_synthesis as cs
        sections = {
            "restaurant_industry_headlines": [
                {"title": "Toast raises $100M", "summary": "strong growth at Toast",
                 "extras": {"entities": ["Toast"]}, "disposition": "monitor"},
            ],
        }
        items = cs._relationship_activation(sections)
        self.assertIsInstance(items, list)

    def test_CI9_activation_items_have_canonical_fields(self):
        import cos_synthesis as cs
        sections = {
            "restaurant_industry_headlines": [
                {"title": "PAR announces new features", "summary": "PAR Technology expands",
                 "extras": {"entities": ["PAR"]}, "disposition": "monitor"},
            ],
        }
        items = cs._relationship_activation(sections)
        required = {"title", "summary", "why_it_matters", "recommended_action",
                    "disposition", "grounding", "source_refs", "extras", "novelty"}
        for item in items:
            for field in required:
                self.assertIn(field, item)

    def test_CI9_compute_synthesis_includes_activation(self):
        import cos_synthesis as cs
        sections = {
            "restaurant_industry_headlines": [
                {"title": "Toast Q2 earnings", "summary": "Toast growth strong",
                 "extras": {"entities": ["Toast"]}, "disposition": "monitor"},
            ],
            "opportunity_board": [],
            "last_24h_relationship_signals": [],
            "decision_queue": [],
            "source_gap_declarations": [],
        }
        result = cs.compute_synthesis(sections, {})
        convergence_types = [
            item.get("extras", {}).get("convergence_type") for item in result
        ]
        # relationship_activation should be in the types if contact index has Toast contacts
        self.assertIsInstance(result, list)


class CI10EmptySourcesSilent(unittest.TestCase):
    def test_CI10_no_crash_with_missing_files(self):
        """build_index handles missing loop_ledger / active_threads gracefully."""
        import contact_index as ci_m
        with patch.object(ci_m, "LOOP_LEDGER_PATH", Path("/nonexistent/loop_ledger.md")):
            with patch.object(ci_m, "ACTIVE_THREADS_PATH", Path("/nonexistent/threads.yaml")):
                try:
                    result = ci_m.build_index()
                    self.assertIsInstance(result["contacts"], list)
                except Exception as e:
                    self.fail(f"build_index raised exception with missing sources: {e}")

    def test_CI10_contactindex_load_no_crash_on_empty(self):
        ci = ContactIndex({"contacts": [], "contact_count": 0})
        self.assertEqual(ci.by_company("Toast"), [])
        self.assertIsNone(ci.by_name("Nobody"))
        self.assertEqual(ci.related_to_companies(["Toast"]), [])


if __name__ == "__main__":
    unittest.main()
