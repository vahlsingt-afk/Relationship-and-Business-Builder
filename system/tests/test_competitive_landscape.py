"""
test_competitive_landscape.py — RB-2026-09-01.

Coverage for competitive_landscape.py, the market-share/battle-card
analysis engine built after Todd corrected the earlier vendor-list
direction: he wants real category-by-category market share across the
tracked ~1,654 brands, real battle cards with a computed Genius-vs-
competitor delta, and a research-status view of the tracked competitors.
Isolated from real production data throughout (own tmp graph, own tmp
competitor_intelligence root) -- same pattern as
test_tech_stack_relationship_promotion.py's _IsolatedGraphMixin.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "system" / "scripts"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


ei = _load("ecosystem_intelligence", "ecosystem_intelligence.py")

import sys  # noqa: E402
sys.modules["ecosystem_intelligence"] = ei
sys.path.insert(0, str(SCRIPTS_DIR))

eco_export = _load("ecosystem_export", "ecosystem_export.py")
sys.modules["ecosystem_export"] = eco_export

# RB-2026-09-06: deliberately a PLAIN import here, NOT the _load()+
# sys.modules-override pattern used above for ecosystem_intelligence/
# ecosystem_export. earnings_monitor.py is imported (transitively, via
# earnings_trend.py) by many other test files' own plain imports --
# registering a second, separately-loaded copy into sys.modules here would
# silently redirect every OTHER test file's `import earnings_monitor` to
# this copy instead of the real one, breaking their own EARNINGS_HISTORY_PATH
# patches with no visible error (confirmed live: this exact mistake broke
# 12 tests in test_earnings_trend.py, only visible in a full-suite run, not
# in this file's own isolated run). A plain import lets Python's normal
# module cache keep one single, consistent earnings_monitor identity across
# every test file that touches it.
import earnings_monitor as em  # noqa: E402

cl = _load("competitive_landscape", "competitive_landscape.py")


BLAZE = {"id": "brand-blaze-pizza", "name": "Blaze Pizza", "entity_type": "brand", "aliases": [],
         "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]}
DAVES = {"id": "brand-daves-hot-chicken", "name": "Dave's Hot Chicken", "entity_type": "brand", "aliases": [],
         "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]}
MCD = {"id": "brand-mcdonald-s", "name": "McDonald's", "entity_type": "brand", "aliases": [],
       "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]}
QU = {"id": "vendor-qu", "name": "Qu", "entity_type": "vendor", "aliases": [],
      "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]}
GENIUS = {"id": "vendor-genius", "name": "Genius", "entity_type": "vendor", "aliases": [],
          "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]}
ELO = {"id": "vendor-elo", "name": "Elo", "entity_type": "vendor", "aliases": [],
       "attributes": {"primary_category": "pos_hardware"}, "sources": [], "confidence": {}, "domains": ["restaurants"]}


def _rel(rid, brand_id, vendor_id, category, *, status="active", deployment_status=None):
    return {
        "id": rid, "from_entity_id": brand_id, "to_entity_id": vendor_id,
        "relationship_type": "uses_vendor_for_category", "category": category,
        "status": status, "deployment_status": deployment_status,
    }


def _graph(entities, relationships) -> dict:
    return {
        "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-01",
        "entities": entities, "relationships": relationships, "signals": [], "sources": [],
        "assessments": [], "user_relevance": [], "strategic_recommendations": [],
    }


class _IsolatedMixin:
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        tmp = Path(self._tmpdir)
        self._graph_path = tmp / "ecosystem_intelligence.json"
        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path

        sys.path.insert(0, str(SCRIPTS_DIR))
        import competitor_intelligence_common as cic
        self._cic = cic
        self._orig_cic_root = cic.ROOT
        comp_root = tmp / "competitor_intelligence"
        (comp_root / "_portfolio").mkdir(parents=True)
        (comp_root / "_portfolio" / "competitor_registry.json").write_text(
            json.dumps({"registry": []}), encoding="utf-8",
        )
        cic.ROOT = comp_root

    def tearDown(self):
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        self._cic.ROOT = self._orig_cic_root
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_graph(self, graph: dict) -> None:
        self._graph_path.write_text(json.dumps(graph), encoding="utf-8")

    def _register_competitor(self, slug: str, vendor_entity_id: str, **fields) -> None:
        comp_dir = self._cic.ROOT / "competitors" / slug
        comp_dir.mkdir(parents=True)
        comp = {
            "competitor_id": f"comp-{slug}", "competitor_slug": slug,
            "display_name": fields.get("display_name", slug), "vendor_entity_id": vendor_entity_id,
            "competes_on": fields.get("competes_on", []),
            "positioning_summary": fields.get("positioning_summary", ""),
            "todds_pov": fields.get("todds_pov", ""),
            "vs_genius": fields.get("vs_genius", {"genius_advantages": [], "competitor_advantages": []}),
            "category_battle_cards": fields.get("category_battle_cards", {}),
            "last_evidence_date": fields.get("last_evidence_date"),
        }
        (comp_dir / "competitor.json").write_text(json.dumps(comp), encoding="utf-8")
        reg_path = self._cic.ROOT / "_portfolio" / "competitor_registry.json"
        reg = json.loads(reg_path.read_text())
        reg["registry"].append({"competitor_slug": slug, "display_name": comp["display_name"]})
        reg_path.write_text(json.dumps(reg), encoding="utf-8")


class TestCategoryMarketShare(_IsolatedMixin, unittest.TestCase):
    def test_counts_active_relationships_and_ranks_by_brand_count(self):
        graph = _graph(
            [BLAZE, DAVES, MCD, QU, GENIUS],
            [
                _rel("r1", "brand-blaze-pizza", "vendor-qu", "pos"),
                _rel("r2", "brand-daves-hot-chicken", "vendor-qu", "pos"),
                _rel("r3", "brand-mcdonald-s", "vendor-genius", "pos"),
            ],
        )
        self._write_graph(graph)
        share = cl.category_market_share(graph, "pos")
        self.assertEqual(share["total_brands_tracked"], 3)
        self.assertEqual(share["brands_with_known_vendor"], 3)
        self.assertEqual(share["vendors"][0]["vendor_name"], "Qu")
        self.assertEqual(share["vendors"][0]["brand_count"], 2)
        self.assertEqual(share["vendors"][0]["rank"], 1)
        self.assertAlmostEqual(share["vendors"][0]["share_of_known_pct"], 66.7, places=1)

    def test_genius_family_flagged_correctly(self):
        graph = _graph([MCD, GENIUS], [_rel("r1", "brand-mcdonald-s", "vendor-genius", "pos")])
        self._write_graph(graph)
        share = cl.category_market_share(graph, "pos")
        self.assertTrue(share["vendors"][0]["is_genius_family"])

    def test_historical_status_excluded_from_market_share(self):
        """Reuses ecosystem_export.is_current_win -- a historical/superseded
        relationship must not count toward current market share."""
        graph = _graph(
            [BLAZE, QU],
            [_rel("r1", "brand-blaze-pizza", "vendor-qu", "pos", status="historical")],
        )
        self._write_graph(graph)
        share = cl.category_market_share(graph, "pos")
        self.assertEqual(share["vendors"], [])
        self.assertEqual(share["brands_with_known_vendor"], 0)

    def test_empty_category_returns_honest_zero_not_error(self):
        graph = _graph([BLAZE], [])
        self._write_graph(graph)
        share = cl.category_market_share(graph, "loyalty")
        self.assertEqual(share["vendors"], [])
        self.assertEqual(share["brands_with_known_vendor"], 0)
        self.assertEqual(share["coverage_pct"], 0.0)

    def test_no_tracked_brands_returns_none_percentages_not_divide_by_zero(self):
        graph = _graph([], [])
        self._write_graph(graph)
        share = cl.category_market_share(graph, "loyalty")
        self.assertEqual(share["total_brands_tracked"], 0)
        self.assertIsNone(share["coverage_pct"])


_LSR_BRAND = {"id": "brand-lsr-1", "name": "LSR Brand One", "entity_type": "brand", "aliases": [],
              "attributes": {"segment": "LSR", "subsegment": "QSR"}, "sources": [], "confidence": {},
              "domains": ["restaurants"]}
_LSR_BRAND_2 = {"id": "brand-lsr-2", "name": "LSR Brand Two", "entity_type": "brand", "aliases": [],
                "attributes": {"segment": "LSR", "subsegment": "Fast Casual"}, "sources": [], "confidence": {},
                "domains": ["restaurants"]}
_FSR_BRAND = {"id": "brand-fsr-1", "name": "FSR Brand One", "entity_type": "brand", "aliases": [],
              "attributes": {"segment": "FSR", "subsegment": "Casual Dining"}, "sources": [], "confidence": {},
              "domains": ["restaurants"]}
_NO_SEGMENT_BRAND = {"id": "brand-no-segment", "name": "No Segment Brand", "entity_type": "brand", "aliases": [],
                     "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]}


class TestVendorConcentration(_IsolatedMixin, unittest.TestCase):
    """Ecosystem Lookup Tool, Competitors tab (Phase B): which brands a
    vendor is installed at, grouped by segment -- "where they concentrate"."""

    def test_groups_real_relationships_by_segment(self):
        graph = _graph(
            [_LSR_BRAND, _LSR_BRAND_2, _FSR_BRAND, QU],
            [
                _rel("r1", "brand-lsr-1", "vendor-qu", "pos"),
                _rel("r2", "brand-lsr-2", "vendor-qu", "loyalty"),
                _rel("r3", "brand-fsr-1", "vendor-qu", "pos"),
            ],
        )
        self._write_graph(graph)
        result = cl.vendor_concentration(graph, "vendor-qu")
        self.assertEqual(result["total_brand_relationships"], 3)
        by_segment = {row["segment"]: row["count"] for row in result["by_segment"]}
        self.assertEqual(by_segment, {"LSR": 2, "FSR": 1})
        self.assertEqual(len(result["brands"]), 3)

    def test_not_scoped_to_one_category_unlike_market_share(self):
        """The whole point of this function vs. category_market_share: a
        vendor installed under TWO different categories at the same brand
        still counts both relationships."""
        graph = _graph(
            [_LSR_BRAND, QU],
            [
                _rel("r1", "brand-lsr-1", "vendor-qu", "pos"),
                _rel("r2", "brand-lsr-1", "vendor-qu", "loyalty"),
            ],
        )
        self._write_graph(graph)
        result = cl.vendor_concentration(graph, "vendor-qu")
        self.assertEqual(result["total_brand_relationships"], 2)

    def test_historical_relationship_excluded(self):
        graph = _graph(
            [_LSR_BRAND, QU],
            [_rel("r1", "brand-lsr-1", "vendor-qu", "pos", status="historical")],
        )
        self._write_graph(graph)
        result = cl.vendor_concentration(graph, "vendor-qu")
        self.assertEqual(result["total_brand_relationships"], 0)
        self.assertEqual(result["brands"], [])

    def test_brand_with_no_segment_groups_under_unknown_not_dropped(self):
        graph = _graph(
            [_NO_SEGMENT_BRAND, QU],
            [_rel("r1", "brand-no-segment", "vendor-qu", "pos")],
        )
        self._write_graph(graph)
        result = cl.vendor_concentration(graph, "vendor-qu")
        self.assertEqual(result["total_brand_relationships"], 1)
        self.assertEqual(result["by_segment"], [{"segment": "unknown", "count": 1}])

    def test_unknown_vendor_id_raises_value_error_not_silent_empty(self):
        graph = _graph([], [])
        self._write_graph(graph)
        with self.assertRaises(ValueError):
            cl.vendor_concentration(graph, "vendor-does-not-exist")

    def test_brand_id_passed_instead_of_vendor_id_raises(self):
        graph = _graph([_LSR_BRAND], [])
        self._write_graph(graph)
        with self.assertRaises(ValueError):
            cl.vendor_concentration(graph, "brand-lsr-1")

    def test_no_relationships_at_all_returns_honest_empty_not_error(self):
        graph = _graph([QU], [])
        self._write_graph(graph)
        result = cl.vendor_concentration(graph, "vendor-qu")
        self.assertEqual(result["total_brand_relationships"], 0)
        self.assertEqual(result["by_segment"], [])
        self.assertEqual(result["by_subsegment"], [])


class TestBattleCardForCategory(_IsolatedMixin, unittest.TestCase):
    def test_computes_real_delta_against_genius(self):
        graph = _graph(
            [BLAZE, DAVES, MCD, QU, GENIUS],
            [
                _rel("r1", "brand-blaze-pizza", "vendor-qu", "pos"),
                _rel("r2", "brand-daves-hot-chicken", "vendor-qu", "pos"),
                _rel("r3", "brand-mcdonald-s", "vendor-genius", "pos"),
            ],
        )
        self._write_graph(graph)
        self._register_competitor("qu", "vendor-qu", display_name="Qu",
                                   positioning_summary="Real positioning.", todds_pov="Real POV.")
        card = cl.battle_card_for_category(graph, "pos")
        self.assertEqual(card["genius_brand_count"], 1)
        self.assertEqual(len(card["battle_cards"]), 1)
        qu_card = card["battle_cards"][0]
        self.assertEqual(qu_card["vendor_name"], "Qu")
        self.assertEqual(qu_card["positioning_summary"], "Real positioning.")
        self.assertIn("Genius: 1 brands", qu_card["delta_line"])
        self.assertIn("Qu: 2 brands", qu_card["delta_line"])

    def test_competitor_with_no_profile_shows_honest_none(self):
        graph = _graph([BLAZE, QU], [_rel("r1", "brand-blaze-pizza", "vendor-qu", "pos")])
        self._write_graph(graph)
        card = cl.battle_card_for_category(graph, "pos")
        self.assertFalse(card["battle_cards"][0]["has_competitor_profile"])
        self.assertIsNone(card["battle_cards"][0]["positioning_summary"])

    def test_genius_excluded_from_its_own_battle_cards(self):
        graph = _graph([MCD, GENIUS], [_rel("r1", "brand-mcdonald-s", "vendor-genius", "pos")])
        self._write_graph(graph)
        card = cl.battle_card_for_category(graph, "pos")
        self.assertEqual(card["battle_cards"], [])
        self.assertEqual(card["genius_brand_count"], 1)

    def test_prefers_category_specific_battle_card_when_present(self):
        """RB-2026-09-01: a populated category_battle_cards[category] entry
        surfaces its RM-only fields on top of the existing vendor-level
        fields."""
        graph = _graph([BLAZE, QU], [_rel("r1", "brand-blaze-pizza", "vendor-qu", "pos")])
        self._write_graph(graph)
        self._register_competitor(
            "qu", "vendor-qu", display_name="Qu", positioning_summary="Vendor-level positioning.",
            category_battle_cards={"pos": {
                "status": "todd_validated", "confidence_pct": 80,
                "rm_plain_english_posture": "RM talk track for pos.",
                "listen_for": ["renewal noise"], "discovery_questions": ["contract date?"],
                "when_to_bring_todd_in": "if enterprise deal", "red_flags": ["price war"],
                "evidence_ids": ["ev-1"], "source_urls": ["https://example.com"],
                "last_validated": "2026-09-01",
            }},
        )
        card = cl.battle_card_for_category(graph, "pos")
        qu_card = card["battle_cards"][0]
        self.assertTrue(qu_card["has_category_battle_card"])
        self.assertEqual(qu_card["status"], "todd_validated")
        self.assertEqual(qu_card["confidence_pct"], 80)
        self.assertEqual(qu_card["rm_plain_english_posture"], "RM talk track for pos.")
        self.assertEqual(qu_card["discovery_questions"], ["contract date?"])
        self.assertEqual(qu_card["when_to_bring_todd_in"], "if enterprise deal")
        self.assertEqual(qu_card["red_flags"], ["price war"])
        self.assertEqual(qu_card["last_validated"], "2026-09-01")
        # vendor-level fields still populated too, unchanged behavior
        self.assertEqual(qu_card["positioning_summary"], "Vendor-level positioning.")

    def test_falls_back_to_vendor_level_fields_when_no_category_card(self):
        """A competitor with vendor-level content but no category-specific
        card: vendor-level fields still populate (unchanged behavior), and
        the new RM-only fields stay honestly empty rather than falling back
        to anything."""
        graph = _graph([BLAZE, QU], [_rel("r1", "brand-blaze-pizza", "vendor-qu", "pos")])
        self._write_graph(graph)
        self._register_competitor("qu", "vendor-qu", display_name="Qu", positioning_summary="Vendor-level positioning.")
        card = cl.battle_card_for_category(graph, "pos")
        qu_card = card["battle_cards"][0]
        self.assertFalse(qu_card["has_category_battle_card"])
        self.assertEqual(qu_card["positioning_summary"], "Vendor-level positioning.")
        self.assertIsNone(qu_card["status"])
        self.assertIsNone(qu_card["confidence_pct"])
        self.assertIsNone(qu_card["rm_plain_english_posture"])
        self.assertEqual(qu_card["listen_for"], [])
        self.assertEqual(qu_card["discovery_questions"], [])

    def test_no_profile_at_all_new_fields_stay_honestly_empty(self):
        """Extends the existing no-profile invariant: with no competitor
        profile at all, every new RM field stays empty, never fabricated."""
        graph = _graph([BLAZE, QU], [_rel("r1", "brand-blaze-pizza", "vendor-qu", "pos")])
        self._write_graph(graph)
        card = cl.battle_card_for_category(graph, "pos")
        qu_card = card["battle_cards"][0]
        self.assertFalse(qu_card["has_category_battle_card"])
        self.assertIsNone(qu_card["status"])
        self.assertIsNone(qu_card["confidence_pct"])
        self.assertEqual(qu_card["red_flags"], [])
        self.assertEqual(qu_card["battle_card_evidence_ids"], [])

    def test_unmapped_vendor_recent_financial_health_is_none(self):
        """RB-2026-09-06: Qu is privately held, has no public earnings, and
        is not in _VENDOR_TO_EARNINGS_COMPANY -- honestly None, no
        fabricated placeholder."""
        graph = _graph([BLAZE, QU], [_rel("r1", "brand-blaze-pizza", "vendor-qu", "pos")])
        self._write_graph(graph)
        card = cl.battle_card_for_category(graph, "pos")
        self.assertIsNone(card["battle_cards"][0]["recent_financial_health"])

    def test_maintained_category_card_surfaces_without_market_share_row(self):
        graph = _graph([BLAZE], [])
        self._write_graph(graph)
        self._register_competitor(
            "par-technology", "vendor-par-technology", display_name="PAR Technology",
            category_battle_cards={"online_ordering": {
                "status": "source_backed", "confidence_pct": 75,
                "rm_plain_english_posture": "Real ordering posture.",
            }},
        )

        card = cl.battle_card_for_category(graph, "online_ordering")

        self.assertEqual(len(card["battle_cards"]), 1)
        self.assertEqual(card["battle_cards"][0]["vendor_name"], "PAR Technology")
        self.assertIn("No current brand-count relationship", card["battle_cards"][0]["delta_line"])
        self.assertEqual(card["battle_cards"][0]["rm_plain_english_posture"], "Real ordering posture.")

    def test_product_vendor_with_same_ticker_joins_parent_competitor_profile(self):
        par = {"id": "vendor-par-technology", "name": "PAR Technology", "entity_type": "vendor", "ticker": "PAR"}
        punchh = {"id": "vendor-par-punchh", "name": "PAR Punchh", "entity_type": "vendor", "ticker": "PAR"}
        graph = _graph([BLAZE, par, punchh], [_rel("r1", "brand-blaze-pizza", "vendor-par-punchh", "loyalty")])
        self._write_graph(graph)
        self._register_competitor(
            "par-technology", "vendor-par-technology", display_name="PAR Technology",
            category_battle_cards={"loyalty": {
                "status": "source_backed", "rm_plain_english_posture": "Real loyalty posture.",
            }},
        )

        card = cl.battle_card_for_category(graph, "loyalty")

        self.assertTrue(card["battle_cards"][0]["has_competitor_profile"])
        self.assertEqual(card["battle_cards"][0]["rm_plain_english_posture"], "Real loyalty posture.")


class TestRecentFinancialHealthForVendor(_IsolatedMixin, unittest.TestCase):
    """RB-2026-09-06, Predictive Market Intelligence battle-card extension:
    _recent_financial_health_for_vendor() joins a real public vendor's
    earnings-trend read onto its battle card, via the small explicit
    _VENDOR_TO_EARNINGS_COMPANY mapping (not fuzzy name matching -- see
    that mapping's own comment for why)."""

    PAR = {"id": "vendor-par-technology", "name": "PAR Technology", "entity_type": "vendor",
           "aliases": [], "attributes": {}, "sources": [], "confidence": {}, "domains": ["restaurants"]}

    def _write_earnings_rows(self, rows: list[dict]) -> Path:
        tmp = Path(self._tmpdir) / "earnings_calls.jsonl"
        tmp.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return tmp

    def test_mapped_vendor_with_real_earnings_history_returns_real_read(self):
        graph = _graph([BLAZE, self.PAR], [_rel("r1", "brand-blaze-pizza", "vendor-par-technology", "pos")])
        self._write_graph(graph)
        rows = [
            {"company": "PAR Technology", "event_date": "2026-01-01", "signal_dimensions": [], "excerpt": ""},
            {"company": "PAR Technology", "event_date": "2026-04-01", "signal_dimensions": [],
             "excerpt": "We delivered a record quarter with strong unit growth."},
        ]
        earnings_path = self._write_earnings_rows(rows)
        from unittest.mock import patch
        with patch.object(em, "EARNINGS_HISTORY_PATH", earnings_path):
            card = cl.battle_card_for_category(graph, "pos")
        health = card["battle_cards"][0]["recent_financial_health"]
        self.assertIsNotNone(health)
        self.assertEqual(health["earnings_company"], "PAR Technology")
        self.assertEqual(health["financial_health_signals"], {"growth_confidence": 1})

    def test_mapped_vendor_with_no_real_earnings_rows_returns_none(self):
        """The mapping exists, but no rows on file for this company at all
        (e.g. a fresh/empty test environment) -- insufficient_history from
        earnings_trend, so honestly None here too, not a fabricated read."""
        graph = _graph([BLAZE, self.PAR], [_rel("r1", "brand-blaze-pizza", "vendor-par-technology", "pos")])
        self._write_graph(graph)
        earnings_path = self._write_earnings_rows([])
        from unittest.mock import patch
        with patch.object(em, "EARNINGS_HISTORY_PATH", earnings_path):
            card = cl.battle_card_for_category(graph, "pos")
        self.assertIsNone(card["battle_cards"][0]["recent_financial_health"])


class TestBrandCategoryGrid(_IsolatedMixin, unittest.TestCase):
    def test_one_row_per_brand_vendor_name_in_cell(self):
        graph = _graph(
            [BLAZE, MCD, QU, GENIUS],
            [
                _rel("r1", "brand-blaze-pizza", "vendor-qu", "pos"),
                _rel("r2", "brand-mcdonald-s", "vendor-genius", "loyalty"),
            ],
        )
        self._write_graph(graph)
        grid = cl.brand_category_grid(graph)
        self.assertEqual(len(grid), 2)
        blaze_row = next(r for r in grid if r["brand_name"] == "Blaze Pizza")
        self.assertEqual(blaze_row["pos"], "Qu")
        self.assertIsNone(blaze_row["loyalty"])
        mcd_row = next(r for r in grid if r["brand_name"] == "McDonald's")
        self.assertEqual(mcd_row["loyalty"], "Genius")


class TestCompetitorResearchStatus(_IsolatedMixin, unittest.TestCase):
    def test_needs_research_flag_for_empty_positioning(self):
        graph = _graph([], [])
        self._write_graph(graph)
        self._register_competitor("qu", "vendor-qu", display_name="Qu")
        status = cl.competitor_research_status(graph)
        self.assertTrue(status["competitors"][0]["needs_research"])

    def test_needs_research_false_when_filled_and_fresh(self):
        import datetime as _dt
        graph = _graph([], [])
        self._write_graph(graph)
        self._register_competitor(
            "qu", "vendor-qu", display_name="Qu", positioning_summary="Real.",
            last_evidence_date=_dt.date.today().isoformat(),
        )
        status = cl.competitor_research_status(graph)
        self.assertFalse(status["competitors"][0]["needs_research"])

    def test_finds_real_new_competitor_candidate_never_invents_one(self):
        graph = _graph(
            [BLAZE, ELO],
            [_rel("r1", "brand-blaze-pizza", "vendor-elo", "pos_hardware")],
        )
        self._write_graph(graph)
        status = cl.competitor_research_status(graph)
        self.assertEqual(len(status["new_competitor_candidates"]), 1)
        self.assertEqual(status["new_competitor_candidates"][0]["vendor_name"], "Elo")

    def test_already_tracked_vendor_not_a_candidate(self):
        graph = _graph(
            [BLAZE, ELO],
            [_rel("r1", "brand-blaze-pizza", "vendor-elo", "pos_hardware")],
        )
        self._write_graph(graph)
        self._register_competitor("elo", "vendor-elo", display_name="Elo")
        status = cl.competitor_research_status(graph)
        self.assertEqual(status["new_competitor_candidates"], [])

    def test_genius_family_never_a_candidate(self):
        graph = _graph(
            [BLAZE, GENIUS],
            [_rel("r1", "brand-blaze-pizza", "vendor-genius", "pos_hardware")],
        )
        self._write_graph(graph)
        status = cl.competitor_research_status(graph)
        self.assertEqual(status["new_competitor_candidates"], [])


class TestTechStackCategories(unittest.TestCase):
    def test_includes_the_new_workbook_columns(self):
        for cat in ("pos_hardware", "drive_thru_timers", "ai_solution_1", "ai_solution_2",
                    "broadband_network", "in_restaurant_media", "payments_gateway"):
            self.assertIn(cat, cl.TECH_STACK_CATEGORIES)

    def test_categories_are_sorted_and_deduplicated(self):
        self.assertEqual(cl.TECH_STACK_CATEGORIES, sorted(set(cl.TECH_STACK_CATEGORIES)))

    def test_extra_battle_card_categories_kept_separate_from_market_share_categories(self):
        """RB-2026-09-01: platform/delivery_aggregation have no per-brand
        graph data source yet -- must not silently show up in
        TECH_STACK_CATEGORIES (which drives real market-share arithmetic)."""
        self.assertEqual(cl.EXTRA_BATTLE_CARD_CATEGORIES, {"platform", "delivery_aggregation"})
        self.assertFalse(cl.EXTRA_BATTLE_CARD_CATEGORIES & set(cl.TECH_STACK_CATEGORIES))


if __name__ == "__main__":
    unittest.main()
