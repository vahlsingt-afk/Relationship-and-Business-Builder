#!/usr/bin/env python3
"""
test_refresh_persisted_briefs.py — routine daily regeneration of Canonical
Background Briefs / Competitive Briefs / Battle Cards / Value Wedges
(2026-09-25).

The underlying generate_*() functions are already fully covered by
test_account_background_brief.py / test_competitive_brief.py /
test_battle_card.py / test_value_wedge.py -- this file only covers what
refresh_persisted_briefs.py itself adds: iterating each registry
correctly, classifying generated-vs-unchanged correctly (proving the
no-op guard actually gets exercised through this script, not just
directly), and one bad subject never aborting the rest of a run.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import ecosystem_intelligence as ei  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import genius_capabilities as gc  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index as ix  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402
import refresh_persisted_briefs as rpb  # noqa: E402

TEST_ACCOUNT_SLUG = "test-fixture-refresh-briefs-account"
TEST_CATEGORY = "pos"


def _entity(entity_id, name, entity_type, **attrs):
    return {"id": entity_id, "name": name, "entity_type": entity_type, "aliases": [],
            "attributes": attrs, "sources": [], "confidence": {}, "domains": ["restaurants"]}


def _rel(rid, brand_id, vendor_id, category):
    return {"id": rid, "from_entity_id": brand_id, "to_entity_id": vendor_id,
            "relationship_type": "uses_vendor_for_category", "category": category,
            "status": "active", "deployment_status": "live"}


class _IsolatedFixtureMixin(unittest.TestCase):
    """Same isolation pattern test_battle_card.py/test_competitive_brief.py
    already use -- disposable graph/competitor_intelligence/artifact_vault/
    intelligence_index paths, PLUS a disposable customers_prospects account
    directory (cpc.ROOT has no path-injection mechanism, same constraint
    test_account_background_brief.py documents -- real filesystem I/O
    against a throwaway slug, never the shared portfolio registry)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)

        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_cic_root = cic.ROOT
        self._orig_gc_path = gc.GENIUS_CAPABILITIES_PATH
        self._orig_vault_root = avc.VAULT_ROOT
        self._orig_index_path = ix.INDEX_PATH
        self._orig_log_path = ix.UPDATE_LOG_PATH

        self._graph_path = tmp_root / "ecosystem_intelligence.json"
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path
        cic.ROOT = tmp_root / "competitor_intelligence"
        gc.GENIUS_CAPABILITIES_PATH = tmp_root / "genius_capabilities.json"
        avc.VAULT_ROOT = tmp_root / "artifact_vault"
        ix.INDEX_PATH = tmp_root / "intelligence_index.json"
        ix.UPDATE_LOG_PATH = tmp_root / "intelligence_index_updates.jsonl"

        graph = {
            "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-25",
            "entities": [
                _entity("brand-real-fixture-brand", "Real Fixture Brand", "brand"),
                _entity("vendor-test-rival", "Test Rival", "vendor"),
            ],
            "relationships": [_rel("rel-1", "brand-real-fixture-brand", "vendor-test-rival", TEST_CATEGORY)],
            "signals": [], "sources": [], "assessments": [], "user_relevance": [],
            "strategic_recommendations": [],
        }
        self._graph_path.write_text(json.dumps(graph), encoding="utf-8")

        comp_dir = cic.ROOT / "competitors" / "test-rival"
        comp_dir.mkdir(parents=True)
        (comp_dir / "competitor.json").write_text(json.dumps({
            "competitor_id": "comp-test-rival", "competitor_slug": "test-rival",
            "display_name": "Test Rival", "vendor_entity_id": "vendor-test-rival",
            "competes_on": [TEST_CATEGORY], "positioning_summary": "Real positioning.",
            "todds_pov": "", "vs_genius": {"genius_advantages": [], "competitor_advantages": []},
            "category_battle_cards": {}, "last_evidence_date": None,
        }), encoding="utf-8")
        (comp_dir / "evidence.jsonl").write_text("", encoding="utf-8")
        (cic.ROOT / "_portfolio").mkdir(parents=True, exist_ok=True)
        (cic.ROOT / "_portfolio" / "competitor_registry.json").write_text(
            json.dumps({"registry": [{"competitor_slug": "test-rival", "display_name": "Test Rival"}]}),
            encoding="utf-8",
        )

        self.account_dir_path = cpc.ROOT / "accounts" / TEST_ACCOUNT_SLUG
        if self.account_dir_path.exists():
            shutil.rmtree(self.account_dir_path)
        self.account_dir_path.mkdir(parents=True)
        cpc.save_json(self.account_dir_path / "account.json", {
            "account_id": f"acct-{TEST_ACCOUNT_SLUG}", "account_slug": TEST_ACCOUNT_SLUG,
            "display_name": "Test Fixture Refresh Briefs Account", "aliases": [],
            "portfolio_status": {"value": "research", "status": "confirmed", "evidence_ids": [],
                                  "confidence": "high", "as_of": "2026-09-25", "scope": "account",
                                  "last_reviewed_by": "human:test"},
            "owners": [], "executive_summary": "Test summary.", "current_business_situation": "Test situation.",
            "leadership": {"confirmed": [], "reported_unverified": []}, "technology_stack": [],
            "commercial_models": [], "buying_influences": [], "opportunities": [],
            "qualification": {"criteria": []}, "strategic_position": {}, "bottom_line": "Test bottom line.",
            "latest_review": {}, "template_version": "test", "updated_at": "2026-09-25T00:00:00Z",
        })
        cpc.save_json(self.account_dir_path / "brand_profile.json", {"account_id": f"acct-{TEST_ACCOUNT_SLUG}"})
        cpc.save_json(self.account_dir_path / "contradictions.json", {"account_id": f"acct-{TEST_ACCOUNT_SLUG}", "contradictions": []})
        cpc.save_json(self.account_dir_path / "source_index.json", {"account_id": f"acct-{TEST_ACCOUNT_SLUG}", "sources": []})
        (self.account_dir_path / "evidence.jsonl").write_text("", encoding="utf-8")

        self._orig_cpc_registry = cpc.load_registry()
        cpc.save_registry({"registry": [{"account_id": f"acct-{TEST_ACCOUNT_SLUG}"}]})

    def tearDown(self):
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        cic.ROOT = self._orig_cic_root
        gc.GENIUS_CAPABILITIES_PATH = self._orig_gc_path
        avc.VAULT_ROOT = self._orig_vault_root
        ix.INDEX_PATH = self._orig_index_path
        ix.UPDATE_LOG_PATH = self._orig_log_path
        if self.account_dir_path.exists():
            shutil.rmtree(self.account_dir_path)
        cpc.save_registry(self._orig_cpc_registry)
        self._tmpdir.cleanup()


class TestRefreshBackgroundBriefs(_IsolatedFixtureMixin):
    def test_first_run_classifies_as_generated(self):
        result = rpb.refresh_background_briefs()
        self.assertEqual(result["generated"], [TEST_ACCOUNT_SLUG])
        self.assertEqual(result["unchanged"], [])
        self.assertEqual(result["failed"], [])

    def test_second_run_with_no_changes_classifies_as_unchanged(self):
        rpb.refresh_background_briefs()
        result = rpb.refresh_background_briefs()
        self.assertEqual(result["generated"], [])
        self.assertEqual(result["unchanged"], [TEST_ACCOUNT_SLUG])

    def test_bad_account_lands_in_failed_not_aborted(self):
        """A registry entry with no corresponding account folder on disk
        (a real inconsistency, not a fabricated test case) makes
        generate_brief() raise FileNotFoundError -- must land in
        `failed`, never abort the rest of the run."""
        cpc.save_registry({"registry": [{"account_id": "acct-does-not-exist-on-disk"},
                                         {"account_id": f"acct-{TEST_ACCOUNT_SLUG}"}]})
        result = rpb.refresh_background_briefs()
        self.assertEqual(result["generated"], [TEST_ACCOUNT_SLUG])
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(result["failed"][0]["slug"], "does-not-exist-on-disk")


class TestRefreshCompetitiveBriefs(_IsolatedFixtureMixin):
    def test_first_run_classifies_as_generated(self):
        result = rpb.refresh_competitive_briefs()
        self.assertEqual(result["generated"], ["test-rival"])
        self.assertEqual(result["unchanged"], [])
        self.assertEqual(result["failed"], [])

    def test_second_run_with_no_changes_classifies_as_unchanged(self):
        rpb.refresh_competitive_briefs()
        result = rpb.refresh_competitive_briefs()
        self.assertEqual(result["generated"], [])
        self.assertEqual(result["unchanged"], ["test-rival"])

    def test_unregistered_slug_now_auto_creates_instead_of_failing(self):
        """RB-2026-09-25, Todd's explicit direction: competitive_brief.py's
        generate_competitive_brief() now auto-creates a shell for a slug
        with no competitor record (competitor_intelligence.
        ensure_competitor_by_slug()) instead of raising -- a registry
        entry with no backing competitor.json (the old "ghost competitor"
        scenario this test used to cover) now lands in "generated", not
        "failed"."""
        reg_path = cic.ROOT / "_portfolio" / "competitor_registry.json"
        reg = json.loads(reg_path.read_text())
        reg["registry"].append({"competitor_slug": "ghost-competitor", "display_name": "Ghost"})
        reg_path.write_text(json.dumps(reg), encoding="utf-8")

        result = rpb.refresh_competitive_briefs()
        self.assertEqual(sorted(result["generated"]), ["ghost-competitor", "test-rival"])
        self.assertEqual(result["failed"], [])

    def test_genuinely_corrupt_competitor_lands_in_failed_not_aborted(self):
        """The real remaining "one bad entry doesn't abort the batch"
        case: a competitor.json that exists but fails to parse. Auto-
        create only covers a MISSING record (FileNotFoundError) -- a
        malformed one still surfaces as a real failure, same as before."""
        reg_path = cic.ROOT / "_portfolio" / "competitor_registry.json"
        reg = json.loads(reg_path.read_text())
        reg["registry"].append({"competitor_slug": "corrupt-competitor", "display_name": "Corrupt"})
        reg_path.write_text(json.dumps(reg), encoding="utf-8")
        corrupt_dir = cic.ROOT / "competitors" / "corrupt-competitor"
        corrupt_dir.mkdir(parents=True)
        (corrupt_dir / "competitor.json").write_text("{not valid json", encoding="utf-8")
        (corrupt_dir / "evidence.jsonl").write_text("", encoding="utf-8")

        result = rpb.refresh_competitive_briefs()
        self.assertEqual(result["generated"], ["test-rival"])
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(result["failed"][0]["slug"], "corrupt-competitor")


class TestRefreshBattleCards(_IsolatedFixtureMixin):
    def test_generates_a_card_for_every_valid_category(self):
        import competitive_landscape as cland
        result = rpb.refresh_battle_cards()
        self.assertEqual(len(result["generated"]) + len(result["unchanged"]) + len(result["failed"]),
                          len(cland.TECH_STACK_CATEGORIES))
        self.assertEqual(result["failed"], [])

    def test_second_run_with_no_changes_classifies_as_unchanged(self):
        rpb.refresh_battle_cards()
        result = rpb.refresh_battle_cards()
        self.assertEqual(result["generated"], [])
        self.assertIn(TEST_CATEGORY, result["unchanged"])


class TestRefreshValueWedges(_IsolatedFixtureMixin):
    def test_first_run_classifies_as_generated(self):
        result = rpb.refresh_value_wedges()
        self.assertEqual(result["generated"], ["test-rival"])
        self.assertEqual(result["unchanged"], [])
        self.assertEqual(result["failed"], [])

    def test_second_run_with_no_changes_classifies_as_unchanged(self):
        rpb.refresh_value_wedges()
        result = rpb.refresh_value_wedges()
        self.assertEqual(result["generated"], [])
        self.assertEqual(result["unchanged"], ["test-rival"])

    def test_unregistered_slug_now_auto_creates_instead_of_failing(self):
        reg_path = cic.ROOT / "_portfolio" / "competitor_registry.json"
        reg = json.loads(reg_path.read_text())
        reg["registry"].append({"competitor_slug": "ghost-competitor", "display_name": "Ghost"})
        reg_path.write_text(json.dumps(reg), encoding="utf-8")

        result = rpb.refresh_value_wedges()
        self.assertEqual(sorted(result["generated"]), ["ghost-competitor", "test-rival"])
        self.assertEqual(result["failed"], [])


class TestRefreshAll(_IsolatedFixtureMixin):
    def test_returns_all_four_sections(self):
        result = rpb.refresh_all()
        self.assertEqual(
            set(result.keys()),
            {"background_briefs", "competitive_briefs", "battle_cards", "value_wedges"},
        )


if __name__ == "__main__":
    unittest.main()
