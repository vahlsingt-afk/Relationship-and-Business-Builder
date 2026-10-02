"""
test_competitor_intelligence_common_aliases.py — RB-2026-09-16.

aliases_by_display_name() and resolve_aliases() are the shared source of
truth for competitor alias lookups, extracted from
entity_convergence_scan.py's original _entity_aliases_by_name() so
query_engine.py's ad-hoc entity lookups don't drift from the daily
convergence scan's copy.

aliases_by_display_name() itself is already covered indirectly via
entity_convergence_scan.py's TestEntityAliasesByName (which now calls
through this same function). This file covers the piece that's new here:
resolve_aliases()'s case-insensitive, match-on-any-known-name lookup --
the fix for query_engine.py's ad-hoc "ask about NCR" gap, where a user
types an alias rather than the canonical display_name.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import competitor_intelligence_common as cic  # noqa: E402


class _IsolatedRootMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_root = cic.ROOT
        cic.ROOT = Path(self._tmpdir.name) / "competitor_intelligence"

    def tearDown(self):
        cic.ROOT = self._orig_root
        self._tmpdir.cleanup()

    def _register(self, slug: str, display_name: str, aliases: list[str]) -> None:
        cic.create_competitor_shell(slug, display_name, f"vendor-{slug}")
        cic.register_competitor(slug, display_name)
        comp_path = cic.competitor_dir(slug) / "competitor.json"
        comp = cic.load_json(comp_path)
        comp["aliases"] = aliases
        cic.save_json(comp_path, comp)


class TestResolveAliasesByCanonicalName(_IsolatedRootMixin, unittest.TestCase):
    def test_canonical_display_name_returns_its_aliases(self):
        self._register("ncr", "NCR Voyix", ["NCR Corporation", "NCR Voyix", "NCR Aloha", "Aloha POS", "NCR"])
        result = cic.resolve_aliases("NCR Voyix")
        self.assertEqual(
            set(result),
            {"NCR Corporation", "NCR Aloha", "Aloha POS", "NCR"},
        )
        # the queried name itself is excluded -- signal_synthesis already
        # searches entity_name separately, so including it here would be
        # redundant, not wrong, but the contract is "the OTHER names."
        self.assertNotIn("NCR Voyix", result)


class TestResolveAliasesByAlternateName(_IsolatedRootMixin, unittest.TestCase):
    """The real gap this closes: query_engine.py's entity comes from raw
    user text, which might be the bare alias "NCR" rather than the
    canonical "NCR Voyix" -- a plain dict.get(entity) on
    aliases_by_display_name() would miss this entirely."""

    def test_bare_alias_resolves_to_every_other_known_name(self):
        self._register("ncr", "NCR Voyix", ["NCR Corporation", "NCR Voyix", "NCR Aloha", "Aloha POS", "NCR"])
        result = cic.resolve_aliases("NCR")
        self.assertIn("NCR Voyix", result)
        self.assertIn("NCR Corporation", result)
        self.assertIn("NCR Aloha", result)
        self.assertIn("Aloha POS", result)
        self.assertNotIn("NCR", result)

    def test_lookup_is_case_insensitive(self):
        self._register("ncr", "NCR Voyix", ["NCR Corporation", "NCR Voyix", "NCR Aloha", "Aloha POS", "NCR"])
        result = cic.resolve_aliases("ncr voyix")
        self.assertIn("NCR Corporation", result)

    def test_no_duplicate_when_aliases_list_redundantly_repeats_display_name(self):
        # Real data shape (system/competitor_intelligence/competitors/ncr/
        # competitor.json, 2026-09-16): the aliases list itself includes
        # "NCR Voyix", the same string as display_name. Querying by any
        # OTHER alias must not surface "NCR Voyix" twice.
        self._register("ncr", "NCR Voyix", ["NCR Corporation", "NCR Voyix", "NCR Aloha", "Aloha POS", "NCR"])
        result = cic.resolve_aliases("NCR")
        self.assertEqual(result.count("NCR Voyix"), 1)
        self.assertEqual(len(result), len(set(n.casefold() for n in result)))


class TestResolveAliasesFailsClosed(_IsolatedRootMixin, unittest.TestCase):
    def test_unknown_name_returns_empty_list_not_error(self):
        self._register("ncr", "NCR Voyix", ["NCR Corporation"])
        # a Blue Sheet customer name, or any other non-competitor text --
        # must fail closed to [], never raise, since query_engine.py calls
        # this for every entity a user types, most of which aren't
        # tracked competitors at all.
        result = cic.resolve_aliases("McDonald's")
        self.assertEqual(result, [])

    def test_empty_string_returns_empty_list(self):
        self.assertEqual(cic.resolve_aliases(""), [])

    def test_competitor_with_no_aliases_is_never_matched(self):
        self._register("toast", "Toast", [])
        result = cic.resolve_aliases("Toast")
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
