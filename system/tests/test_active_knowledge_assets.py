"""
test_active_knowledge_assets.py — RB 9.36 / DEFECT-019
Session-start graph mounting, active_knowledge_assets section,
and Source Priority Order enforcement.

DEFECT-019 Root causes addressed:
  - No session-start context seeding → `active_knowledge_assets` in every brief
  - No source priority hierarchy → Source Priority Order in rendering_rules
  - No context boost window → session_scoped mount directive on each asset

Test groups:
  AKA1 (6):  _load_active_knowledge_assets unit tests — registry parsing, filtering, structure
  AKA2 (6):  build_canonical_brief integration — section populated, top-level key, titles
  AKA3 (6):  rendering_rules contract — Source Priority Order, source declaration, boost window
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import daily_brief as db
import rb_core as core

# ---------------------------------------------------------------------------
# Minimal synthetic brief report (shared across test groups)
# ---------------------------------------------------------------------------

_SYNTHETIC_REPORT: dict = {
    "today": "2026-05-30",
    "relationship_signals": {"signals": [], "stale_sources": []},
    "loops": {},
    "active_threads": [],
    "market_signals": {"signals": [], "freshness_status": "ok"},
    "strategic_memory": {},
    "daily_prep_summary": {
        "totals": {"inner": 0, "broader": 0, "dormant_valuable": 0},
        "source_health": {},
        "signals": [],
        "overdue_loops": [],
        "stale_sources": [],
        "meeting_prep": [],
    },
}

# ---------------------------------------------------------------------------
# Registry fixtures
# ---------------------------------------------------------------------------

_ACTIVE_REGISTRY = {
    "contract": "rb_graph_registry_v1",
    "version": 1,
    "graphs": [
        {
            "graph_id": "micro_ecosystem:mcdonalds_us_ops",
            "graph_slug": "mcdonalds_us_ops",
            "graph_type": "micro_ecosystem",
            "name": "McDonald's US Operations Micro Ecosystem",
            "status": "partial",
            "updated_at": "2026-05-27T09:57:53",
            "source_workbook": "NSN Lookup 2026-05 MAY.xlsx",
            "counts": {"nodes": 19694, "edges": 127385},
            "activation_terms": [
                "mcdonalds", "mcdonald's", "nsn", "storetech", "field office",
                "coop", "co-op", "rfm", "otm", "stim", "fbp", "otp",
            ],
        }
    ],
    "updated_at": "2026-05-27T09:57:53",
}

_REGISTRY_INACTIVE_GRAPH = {
    "contract": "rb_graph_registry_v1",
    "version": 1,
    "graphs": [
        {
            "graph_id": "micro_ecosystem:inactive_corp",
            "graph_slug": "inactive_corp",
            "graph_type": "micro_ecosystem",
            "name": "Inactive Corp Graph",
            "status": "inactive",
            "activation_terms": ["inactive"],
            "counts": {"nodes": 0, "edges": 0},
        }
    ],
}

_REGISTRY_MULTIPLE = {
    "contract": "rb_graph_registry_v1",
    "version": 1,
    "graphs": [
        {
            "graph_id": "micro_ecosystem:alpha",
            "graph_slug": "alpha",
            "name": "Alpha Corp",
            "status": "active",
            "activation_terms": ["alpha", "alphacorp"],
            "counts": {"nodes": 100, "edges": 200},
        },
        {
            "graph_id": "micro_ecosystem:beta",
            "graph_slug": "beta",
            "name": "Beta Inc",
            "status": "partial",
            "activation_terms": ["beta", "betainc"],
            "counts": {"nodes": 50, "edges": 80},
        },
        {
            "graph_id": "micro_ecosystem:retired",
            "graph_slug": "retired",
            "name": "Retired Co",
            "status": "retired",
            "activation_terms": ["retired"],
            "counts": {"nodes": 0, "edges": 0},
        },
    ],
}


def _patch_registry(registry_content: dict | None):
    """Context manager: replace system/graphs/index.json with tmp fixture."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        if registry_content is None:
            # Simulate missing file
            with patch.object(
                Path,
                "exists",
                lambda self: False if str(self).endswith("index.json") and "graphs" in str(self) and "micro" not in str(self) else Path.exists.__wrapped__(self) if hasattr(Path.exists, "__wrapped__") else True,
            ):
                # Simpler: just monkey-patch core.SYSTEM_DIR temporarily
                with tempfile.TemporaryDirectory() as td:
                    fake_dir = Path(td) / "graphs"
                    fake_dir.mkdir()
                    original = core.SYSTEM_DIR
                    core.SYSTEM_DIR = Path(td)
                    try:
                        yield
                    finally:
                        core.SYSTEM_DIR = original
        else:
            with tempfile.TemporaryDirectory() as td:
                graphs_dir = Path(td) / "graphs"
                graphs_dir.mkdir()
                (graphs_dir / "index.json").write_text(
                    json.dumps(registry_content), encoding="utf-8"
                )
                original = core.SYSTEM_DIR
                core.SYSTEM_DIR = Path(td)
                try:
                    yield
                finally:
                    core.SYSTEM_DIR = original

    return _cm()


# ---------------------------------------------------------------------------
# AKA1 — _load_active_knowledge_assets unit tests
# ---------------------------------------------------------------------------

class AKA1LoadActiveKnowledgeAssetsTests(unittest.TestCase):
    """Unit tests for _load_active_knowledge_assets()."""

    def test_AKA1a_returns_list_for_active_registry(self):
        """Returns a non-empty list when registry has active/partial graphs."""
        with _patch_registry(_ACTIVE_REGISTRY):
            result = db._load_active_knowledge_assets()
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_AKA1b_skips_inactive_graphs(self):
        """Graphs with status not in (active, partial) are excluded."""
        with _patch_registry(_REGISTRY_INACTIVE_GRAPH):
            result = db._load_active_knowledge_assets()
        self.assertEqual(result, [])

    def test_AKA1c_returns_empty_when_registry_missing(self):
        """Returns empty list when graphs/index.json does not exist."""
        with _patch_registry(None):
            result = db._load_active_knowledge_assets()
        self.assertEqual(result, [])

    def test_AKA1d_required_keys_present(self):
        """Every asset dict has required structural keys."""
        required_keys = {
            "asset_id", "entity", "activation_terms",
            "retrieval_action", "routing_rule",
            "context_boost_window", "graceful_failure_mode",
        }
        with _patch_registry(_ACTIVE_REGISTRY):
            result = db._load_active_knowledge_assets()
        self.assertGreater(len(result), 0)
        for asset in result:
            for key in required_keys:
                self.assertIn(key, asset, f"Missing key '{key}' in asset {asset.get('entity')}")

    def test_AKA1e_routing_rule_contains_mounted_for_session(self):
        """routing_rule must contain 'MOUNTED FOR SESSION' to enforce session-scoped mount."""
        with _patch_registry(_ACTIVE_REGISTRY):
            result = db._load_active_knowledge_assets()
        for asset in result:
            self.assertIn(
                "MOUNTED FOR SESSION",
                asset["routing_rule"],
                f"routing_rule missing 'MOUNTED FOR SESSION' for {asset.get('entity')}",
            )

    def test_AKA1f_multiple_graphs_active_and_partial_included(self):
        """Both 'active' and 'partial' status graphs are included; 'retired' is excluded."""
        with _patch_registry(_REGISTRY_MULTIPLE):
            result = db._load_active_knowledge_assets()
        ids = [a["asset_id"] for a in result]
        self.assertIn("micro_ecosystem:alpha", ids)
        self.assertIn("micro_ecosystem:beta", ids)
        self.assertNotIn("micro_ecosystem:retired", ids)


# ---------------------------------------------------------------------------
# AKA2 — build_canonical_brief integration tests
# ---------------------------------------------------------------------------

class AKA2CanonicalBriefIntegrationTests(unittest.TestCase):
    """Tests that build_canonical_brief populates active_knowledge_assets correctly."""

    def _build(self):
        with _patch_registry(_ACTIVE_REGISTRY):
            brief = db.build_canonical_brief(_SYNTHETIC_REPORT)
        return brief

    def test_AKA2a_section_active_knowledge_assets_present(self):
        """sections['active_knowledge_assets'] exists and is a list."""
        brief = self._build()
        sections = brief.get("sections") or {}
        self.assertIn("active_knowledge_assets", sections)
        self.assertIsInstance(sections["active_knowledge_assets"], list)

    def test_AKA2b_section_populated_from_registry(self):
        """sections['active_knowledge_assets'] is non-empty when registry has active graphs."""
        brief = self._build()
        assets = (brief.get("sections") or {}).get("active_knowledge_assets") or []
        self.assertGreater(len(assets), 0)

    def test_AKA2c_asset_title_format(self):
        """Each asset title follows the '... — MOUNTED FOR SESSION' format."""
        brief = self._build()
        assets = (brief.get("sections") or {}).get("active_knowledge_assets") or []
        for asset in assets:
            title = asset.get("title") or ""
            self.assertIn("MOUNTED FOR SESSION", title,
                          f"Asset title missing 'MOUNTED FOR SESSION': {title!r}")

    def test_AKA2d_asset_extras_have_activation_terms_and_routing_rule(self):
        """Each asset carries activation_terms and routing_rule in extras."""
        brief = self._build()
        assets = (brief.get("sections") or {}).get("active_knowledge_assets") or []
        self.assertGreater(len(assets), 0)
        for asset in assets:
            extras = asset.get("extras") or {}
            self.assertIn("activation_terms", extras,
                          "extras missing 'activation_terms'")
            self.assertIn("routing_rule", extras,
                          "extras missing 'routing_rule'")
            self.assertEqual(extras.get("context_boost_window"), "session_scoped",
                             "context_boost_window must be 'session_scoped'")

    def test_AKA2e_top_level_active_knowledge_assets_matches_section(self):
        """Top-level active_knowledge_assets key mirrors sections['active_knowledge_assets']."""
        brief = self._build()
        top_level = brief.get("active_knowledge_assets")
        section_level = (brief.get("sections") or {}).get("active_knowledge_assets")
        self.assertIsNotNone(top_level,
                             "brief must have top-level 'active_knowledge_assets' key")
        self.assertEqual(len(top_level), len(section_level),
                         "top-level and section-level active_knowledge_assets must match")

    def test_AKA2f_section_order_includes_active_knowledge_assets(self):
        """section_order must include 'active_knowledge_assets'."""
        brief = self._build()
        section_order = brief.get("section_order") or []
        self.assertIn("active_knowledge_assets", section_order)


# ---------------------------------------------------------------------------
# AKA3 — rendering_rules contract tests
# ---------------------------------------------------------------------------

class AKA3RenderingRulesTests(unittest.TestCase):
    """Tests that rendering_rules encode the full DEFECT-019 contract."""

    def _get_rules_text(self):
        with _patch_registry(_ACTIVE_REGISTRY):
            brief = db.build_canonical_brief(_SYNTHETIC_REPORT)
        return " ".join(brief.get("rendering_rules") or []).lower()

    def test_AKA3a_rules_mention_mounted_for_session(self):
        """rendering_rules must state that active_knowledge_assets entries are MOUNTED FOR SESSION."""
        rules = self._get_rules_text()
        self.assertIn("mounted for session", rules)

    def test_AKA3b_rules_state_source_priority_order(self):
        """rendering_rules must encode a Source Priority Order from micro-graph to base model."""
        rules = self._get_rules_text()
        self.assertIn("source priority order", rules)

    def test_AKA3c_rules_require_source_declaration(self):
        """rendering_rules must require source declaration before answering factual questions."""
        rules = self._get_rules_text()
        # Either 'source declaration' or 'declare' / 'declaring' must appear
        self.assertTrue(
            "source" in rules and ("declar" in rules or "declare" in rules),
            "rendering_rules must require source declaration for factual entity questions",
        )

    def test_AKA3d_rules_mention_context_boost_window(self):
        """rendering_rules must mention the context boost window / session-scoped mount."""
        rules = self._get_rules_text()
        self.assertTrue(
            "context_boost_window" in rules or "session_scoped" in rules or "session-scoped" in rules,
            "rendering_rules must describe the session-scoped context boost window",
        )

    def test_AKA3e_rules_specify_graceful_failure_no_base_model_fill(self):
        """rendering_rules must state that unavailable graphs must NOT be filled from base model."""
        rules = self._get_rules_text()
        self.assertIn("graceful_failure_mode", rules,
                      "rendering_rules must reference graceful_failure_mode for unavailable graphs")

    def test_AKA3f_base_model_labeled_as_fallback_only(self):
        """rendering_rules must label base model knowledge as fallback only."""
        rules = self._get_rules_text()
        self.assertIn("fallback", rules,
                      "rendering_rules must describe base model as fallback only")


if __name__ == "__main__":
    unittest.main(verbosity=2)
