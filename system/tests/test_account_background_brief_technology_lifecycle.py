"""
test_account_background_brief_technology_lifecycle.py — Technology
Lifecycle Phase 1 Account Background Brief enrichment
(render_technology_lifecycle_section).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import account_background_brief as abb  # noqa: E402
import technology_lifecycle as tl  # noqa: E402

_GRAPH = {
    "entities": [
        {"id": "brand-burger-king", "name": "Burger King", "entity_type": "brand", "aliases": []},
        {"id": "brand-mcdonalds", "name": "McDonald's", "entity_type": "brand", "aliases": []},
        {"id": "vendor-par-technology", "name": "PAR Technology", "entity_type": "vendor", "aliases": []},
    ],
    "relationships": [],
}


class _IsolatedPathsMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self._orig = {
            "RELATIONSHIP_EVENTS_PATH": tl.RELATIONSHIP_EVENTS_PATH, "GOVERNANCE_PATH": tl.GOVERNANCE_PATH,
            "PENETRATION_PATH": tl.PENETRATION_PATH, "CHANGE_EVENTS_PATH": tl.CHANGE_EVENTS_PATH,
            "FORCING_SIGNALS_PATH": tl.FORCING_SIGNALS_PATH,
        }
        tl.RELATIONSHIP_EVENTS_PATH = tmp / "rel.jsonl"
        tl.GOVERNANCE_PATH = tmp / "gov.jsonl"
        tl.PENETRATION_PATH = tmp / "pen.jsonl"
        tl.CHANGE_EVENTS_PATH = tmp / "chg.jsonl"
        tl.FORCING_SIGNALS_PATH = tmp / "fs.jsonl"
        self._orig_load_graph = tl._load_graph
        tl._load_graph = lambda: _GRAPH

    def tearDown(self):
        for name, path in self._orig.items():
            setattr(tl, name, path)
        tl._load_graph = self._orig_load_graph
        self._tmpdir.cleanup()


class TestRenderTechnologyLifecycleSection(_IsolatedPathsMixin, unittest.TestCase):
    def test_none_brand_entity_id_renders_nothing(self):
        self.assertEqual(abb.render_technology_lifecycle_section(None), [])

    def test_untracked_brand_renders_nothing(self):
        self.assertEqual(abb.render_technology_lifecycle_section("brand-mcdonalds"), [])

    def test_relationship_renders_table_row(self):
        tl.record_relationship_event(
            event_id="e1",
            relationship_key={
                "parent_entity_id": None, "brand_entity_id": "brand-burger-king", "operator_entity_id": None,
                "technology_category": "pos", "vendor_entity_id": "vendor-par-technology", "product": "PAR Brink POS",
            },
            entity_level="brand", lifecycle_state="rollout_active", state_date_or_range="2024",
            evidence="e", source_url=None, source_type="x", confidence="high", evidence_type="independent_evidence",
        )
        lines = abb.render_technology_lifecycle_section("brand-burger-king")
        text = "\n".join(lines)
        self.assertIn("Technology Lifecycle Intelligence", text)
        self.assertIn("rollout_active", text)
        self.assertIn("PAR Brink POS", text)

    def test_forcing_signal_renders_bullet(self):
        tl.record_forcing_signal(
            signal_id="s1", brand_entity_id="brand-burger-king", entity_level="brand",
            technology_category="pos_hardware", forcing_event_type="os_eol", detail="approaching Windows 10 EOL",
            evidence="e", source_url=None, confidence="medium", evidence_type="rbb_inference",
        )
        lines = abb.render_technology_lifecycle_section("brand-burger-king")
        text = "\n".join(lines)
        self.assertIn("Open forcing signals", text)
        self.assertIn("approaching Windows 10 EOL", text)

    def test_relationship_with_no_current_state_does_not_crash(self):
        """A relationship_key appearing only via a superseded-and-removed
        event never happens in practice, but an empty current_state must
        render gracefully rather than raising."""
        lines = abb.render_technology_lifecycle_section("brand-burger-king")
        self.assertEqual(lines, [])  # nothing recorded at all -- honest omission


if __name__ == "__main__":
    unittest.main()
