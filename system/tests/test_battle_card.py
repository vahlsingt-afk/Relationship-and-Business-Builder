#!/usr/bin/env python3
"""
test_battle_card.py — RB-2026-09-07.

Isolated against a disposable ecosystem_intelligence.json path (patched via
ei.core.ECOSYSTEM_INTELLIGENCE_PATH -- ecosystem_intelligence.py's own
_read_graph() re-resolves this at call time, per its documented fix for a
real prior incident where a stale, import-time-bound default caused an
unmocked write into the real graph during a test), a disposable
competitor_intelligence_common.ROOT, and a disposable
artifact_vault_common.VAULT_ROOT / intelligence_index paths. Never touches
real project state.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import battle_card as bc  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index as ix  # noqa: E402

TEST_CATEGORY = "pos"


def _entity(entity_id, name, entity_type, **attrs):
    return {"id": entity_id, "name": name, "entity_type": entity_type, "aliases": [],
            "attributes": attrs, "sources": [], "confidence": {}, "domains": ["restaurants"]}


def _rel(rid, brand_id, vendor_id, category):
    return {"id": rid, "from_entity_id": brand_id, "to_entity_id": vendor_id,
            "relationship_type": "uses_vendor_for_category", "category": category,
            "status": "active", "deployment_status": "live"}


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)

        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_cic_root = cic.ROOT
        self._orig_vault_root = avc.VAULT_ROOT
        self._orig_index_path = ix.INDEX_PATH
        self._orig_log_path = ix.UPDATE_LOG_PATH

        self._graph_path = tmp_root / "ecosystem_intelligence.json"
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path
        cic.ROOT = tmp_root / "competitor_intelligence"
        avc.VAULT_ROOT = tmp_root / "artifact_vault"
        ix.INDEX_PATH = tmp_root / "intelligence_index.json"
        ix.UPDATE_LOG_PATH = tmp_root / "intelligence_index_updates.jsonl"

        (cic.ROOT / "_portfolio").mkdir(parents=True)
        (cic.ROOT / "_portfolio" / "competitor_registry.json").write_text(
            json.dumps({"registry": []}), encoding="utf-8",
        )

        brand1 = _entity("brand-genius-customer", "Genius Customer", "brand")
        brand2 = _entity("brand-rival-customer-1", "Rival Customer 1", "brand")
        brand3 = _entity("brand-rival-customer-2", "Rival Customer 2", "brand")
        genius = _entity("vendor-genius", "Genius", "vendor")
        rival = _entity("vendor-test-rival", "Test Rival", "vendor")
        graph = {
            "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-07",
            "entities": [brand1, brand2, brand3, genius, rival],
            "relationships": [
                _rel("rel-1", "brand-genius-customer", "vendor-genius", TEST_CATEGORY),
                _rel("rel-2", "brand-rival-customer-1", "vendor-test-rival", TEST_CATEGORY),
                _rel("rel-3", "brand-rival-customer-2", "vendor-test-rival", TEST_CATEGORY),
            ],
            "signals": [], "sources": [], "assessments": [], "user_relevance": [],
            "strategic_recommendations": [],
        }
        self._graph_path.write_text(json.dumps(graph), encoding="utf-8")

        comp_dir = cic.ROOT / "competitors" / "test-rival"
        comp_dir.mkdir(parents=True)
        comp = {
            "competitor_id": "comp-test-rival", "competitor_slug": "test-rival",
            "display_name": "Test Rival", "vendor_entity_id": "vendor-test-rival",
            "competes_on": [TEST_CATEGORY],
            "positioning_summary": "Real positioning summary for Test Rival.",
            "todds_pov": "Real Todd's POV on Test Rival.",
            "vs_genius": {
                "genius_advantages": [{"point": "Genius wins on X.", "evidence_id": "ev-1", "added_at": "2026-09-01T00:00:00Z"}],
                "competitor_advantages": [{"point": "Test Rival wins on Y.", "evidence_id": "ev-2", "added_at": "2026-09-01T00:00:00Z"}],
            },
            "category_battle_cards": {}, "last_evidence_date": "2026-09-01",
        }
        (comp_dir / "competitor.json").write_text(json.dumps(comp), encoding="utf-8")
        (comp_dir / "evidence.jsonl").write_text("", encoding="utf-8")
        reg_path = cic.ROOT / "_portfolio" / "competitor_registry.json"
        reg = json.loads(reg_path.read_text())
        reg["registry"].append({"competitor_slug": "test-rival", "display_name": "Test Rival"})
        reg_path.write_text(json.dumps(reg), encoding="utf-8")

    def tearDown(self):
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        cic.ROOT = self._orig_cic_root
        avc.VAULT_ROOT = self._orig_vault_root
        ix.INDEX_PATH = self._orig_index_path
        ix.UPDATE_LOG_PATH = self._orig_log_path
        self._tmpdir.cleanup()


class TestRenderBattleCard(_IsolatedFixtureMixin):
    def test_unknown_category_raises(self):
        with self.assertRaises(ValueError):
            bc.render_battle_card("not-a-real-category")

    def test_renders_real_positioning_and_pov(self):
        md = bc.render_battle_card(TEST_CATEGORY)
        self.assertIn("Test Rival", md)
        self.assertIn("Real positioning summary for Test Rival.", md)
        self.assertIn("Real Todd's POV on Test Rival.", md)

    def test_renders_advantage_point_text_not_dict_repr(self):
        """vs_genius advantages are {point, evidence_id, added_at} dicts in
        the real data -- confirms the render extracts .point and never
        leaks the raw dict repr into the document."""
        md = bc.render_battle_card(TEST_CATEGORY)
        self.assertIn("Genius wins on X.", md)
        self.assertIn("Test Rival wins on Y.", md)
        self.assertNotIn("'point':", md)
        self.assertNotIn("evidence_id", md)

    def test_genius_share_reflects_graph(self):
        md = bc.render_battle_card(TEST_CATEGORY)
        self.assertIn("Genius: 1 brands", md)

    def test_category_display_name_handles_acronyms(self):
        self.assertEqual(bc._category_display_name("pos"), "POS")
        self.assertEqual(bc._category_display_name("pos_hardware"), "POS Hardware")
        self.assertEqual(bc._category_display_name("payments_gateway"), "Payments Gateway")
        self.assertEqual(bc._category_display_name("restaurant_os_platform"), "Restaurant OS Platform")


class TestGenerateBattleCard(_IsolatedFixtureMixin):
    def test_generates_and_persists(self):
        result = bc.generate_battle_card(TEST_CATEGORY)
        self.assertEqual(result["version"]["version"], 1)
        current = bc.get_current_battle_card(TEST_CATEGORY, include_content=True)
        self.assertIn("Test Rival", current["content"])

    def test_regenerating_unchanged_content_does_not_create_new_version(self):
        """artifact_vault_common.register_version()'s no-op guard
        (2026-09-25) -- unchanged content stays version 1 rather than
        bumping every time the daily refresh re-renders it. A real content
        change still versions correctly; see
        test_artifact_vault_common.py for that coverage directly."""
        r1 = bc.generate_battle_card(TEST_CATEGORY)
        r2 = bc.generate_battle_card(TEST_CATEGORY, generated_for="test-run")
        self.assertEqual(r1["version"]["version"], 1)
        self.assertEqual(r2["version"]["version"], 1)

    def test_registers_in_intelligence_index_on_first_version(self):
        bc.generate_battle_card(TEST_CATEGORY)
        matches = ix.find("POS")
        bc_matches = [m for m in matches if m.get("resource_type") == "battle_card"]
        self.assertEqual(len(bc_matches), 1)

    def test_unknown_category_raises_without_persisting(self):
        with self.assertRaises(ValueError):
            bc.generate_battle_card("not-a-real-category")
        self.assertIsNone(bc.get_current_battle_card("not-a-real-category"))


if __name__ == "__main__":
    unittest.main()
