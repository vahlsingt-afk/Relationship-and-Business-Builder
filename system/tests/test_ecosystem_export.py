#!/usr/bin/env python3
"""test_ecosystem_export.py — RB Unified Restaurant-Tech Graph (2026-07-31),
Phase 6: Shareable Workbook Export Generator.

Covers ecosystem_export.py, the new graph-to-workbook exporter. All tests
pass a synthetic graph dict directly into build_workbook(graph=...) rather
than reading the real ecosystem_intelligence.json, and file-writing tests
pass an explicit tmp_path output_path -- export_workbook()'s default output
directory (system/exports/) is never touched by this file.

Test IDs:
  EXP1 — module sources only from the graph, never re-reads a workbook
  EXP2 — internal and shareable projections agree on every public fact
  EXP3 — strategic_note / confidence rationale are present in internal, absent from shareable
  EXP4 — macro view category/classification totals reconcile against the full row count
  EXP5 — no formula strings or Excel error values anywhere in an exported workbook
  EXP6 — repeat export of unchanged graph data is deterministic (ignoring the timestamp)
  EXP7 — is_current_win() matches Phase 2's own historical/working-profile eligibility rule
  EXP8 — export_workbook() writes all six expected tabs, in order
  EXP9 — Evidence Ledger falls back to sources[] when source_assertions[] is absent
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import ecosystem_export as exp  # noqa: E402
import ecosystem_intelligence as eco  # noqa: E402


def _entity(id_: str, name: str, entity_type: str = "brand") -> dict:
    return {"id": id_, "name": name, "entity_type": entity_type}


def _rel(**overrides) -> dict:
    rel = {
        "id": "rel-1",
        "from_entity_id": "brand-checkers",
        "to_entity_id": "vendor-hi-auto",
        "category": "drive_thru_ai",
        "vendor_role": "voice_ai_provider",
        "status": "active",
        "deployment_status": None,
        "deployment": {"stage": "active_rollout", "deployed_units": 120, "penetration_pct": 40.0},
        "confidence": {"level": "high", "rationale": "Internal review: strong primary-source evidence."},
        "relationship_classification": {"level": 4, "level_name": "preferred_vendor"},
        "ai_application": "voice_ai",
        "strategic_note": "Competitive displacement opportunity vs. Presto -- do not share externally.",
        "sources": ["src-hi-auto-1"],
        "updated_at": "2026-07-01T00:00:00Z",
    }
    rel.update(overrides)
    return rel


def _graph(*rels: dict) -> dict:
    return {
        "version": 2,
        "contract": "rb_ecosystem_intelligence_v1",
        "last_updated": "2026-07-31",
        "entities": [
            _entity("brand-checkers", "Checkers & Rally's"),
            _entity("vendor-hi-auto", "Hi Auto", "vendor"),
            _entity("vendor-presto", "Presto", "vendor"),
        ],
        "relationships": list(rels),
    }


class EXP1_GraphOnlySourcing(unittest.TestCase):
    def test_module_has_no_workbook_reading_symbols(self):
        # ecosystem_export.py must never re-read the original research
        # workbook -- the graph is the only source of truth for an export.
        self.assertFalse(hasattr(exp, "load_rows"))
        self.assertFalse(hasattr(exp, "migrate_workbook"))

    def test_build_workbook_only_touches_the_passed_graph(self):
        graph = _graph(_rel())
        wb, metadata = exp.build_workbook(export_type="internal", graph=graph)
        self.assertEqual(metadata["graph_version"], 2)
        ws = wb[exp.TAB_TECH_STACK]
        self.assertEqual(ws.cell(row=2, column=1).value, "Checkers & Rally's")


class EXP2_InternalShareableAgreeOnPublicFacts(unittest.TestCase):
    def test_public_columns_match_between_projections(self):
        graph = _graph(_rel())
        wb_internal, _ = exp.build_workbook(export_type="internal", graph=graph)
        wb_shareable, _ = exp.build_workbook(export_type="shareable", graph=graph)

        internal_headers = [c.value for c in wb_internal[exp.TAB_TECH_STACK][1]]
        shareable_headers = [c.value for c in wb_shareable[exp.TAB_TECH_STACK][1]]
        public_headers = [h for h in internal_headers if h not in ("strategic_note", "confidence_rationale")]
        self.assertEqual(shareable_headers, public_headers)

        def _row_by_header(ws, headers, row_idx):
            return dict(zip(headers, [c.value for c in ws[row_idx]]))

        internal_row = _row_by_header(wb_internal[exp.TAB_TECH_STACK], internal_headers, 2)
        shareable_row = _row_by_header(wb_shareable[exp.TAB_TECH_STACK], shareable_headers, 2)
        for key in shareable_headers:
            self.assertEqual(internal_row[key], shareable_row[key], key)


class EXP3_PrivateFieldsAbsentFromShareable(unittest.TestCase):
    def test_strategic_note_and_rationale_only_in_internal(self):
        graph = _graph(_rel())
        wb_internal, _ = exp.build_workbook(export_type="internal", graph=graph)
        wb_shareable, _ = exp.build_workbook(export_type="shareable", graph=graph)

        internal_headers = [c.value for c in wb_internal[exp.TAB_TECH_STACK][1]]
        shareable_headers = [c.value for c in wb_shareable[exp.TAB_TECH_STACK][1]]
        self.assertIn("strategic_note", internal_headers)
        self.assertIn("confidence_rationale", internal_headers)
        self.assertNotIn("strategic_note", shareable_headers)
        self.assertNotIn("confidence_rationale", shareable_headers)

        # And the actual private text never appears anywhere in the shareable workbook.
        shareable_text = []
        for ws in wb_shareable.worksheets:
            for row in ws.iter_rows(values_only=True):
                shareable_text.extend(str(v) for v in row if v is not None)
        self.assertFalse(any("do not share externally" in t for t in shareable_text))


class EXP4_MacroTotalsReconcile(unittest.TestCase):
    def test_category_and_classification_totals_sum_to_row_count(self):
        graph = _graph(
            _rel(category="drive_thru_ai"),
            _rel(id="rel-2", category="pos", relationship_classification={"level": 5, "level_name": "standardized_platform"}),
            _rel(id="rel-3", category="pos", relationship_classification=None),
        )
        wb, _ = exp.build_workbook(export_type="internal", graph=graph)
        rows = list(wb[exp.TAB_MACRO_VIEWS].iter_rows(values_only=True))
        total_relationships = len(graph["relationships"])

        recon_row = next(r for r in rows if r and r[0] == "Total relationships (reconciliation check)")
        self.assertEqual(recon_row[1], total_relationships)

        # Independently re-derive both breakdowns straight from the source
        # rows (not by parsing sheet layout) and confirm each sums to the
        # same total the sheet itself reports.
        macro = exp._macro_counts([
            exp._relationship_row(rel, exp._index_entities(graph), shareable=False)
            for rel in graph["relationships"]
        ])
        self.assertEqual(sum(macro["by_category"].values()), total_relationships)
        self.assertEqual(sum(macro["by_classification"].values()), total_relationships)


class EXP5_NoFormulaErrors(unittest.TestCase):
    def test_no_formula_strings_or_excel_errors(self):
        graph = _graph(_rel(), _rel(id="rel-2", category="pos"))
        wb, _ = exp.build_workbook(export_type="shareable", graph=graph)
        error_markers = ("#REF!", "#N/A", "#VALUE!", "#DIV/0!", "#NAME?", "#NULL!", "#NUM!")
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                for value in row:
                    if isinstance(value, str):
                        self.assertFalse(value.startswith("="), f"unexpected formula string: {value!r}")
                        self.assertNotIn(value, error_markers)


class EXP6_DeterministicRepeatExport(unittest.TestCase):
    def test_repeat_build_produces_identical_data_rows(self):
        graph = _graph(_rel(), _rel(id="rel-2", category="pos"))
        wb1, meta1 = exp.build_workbook(export_type="internal", graph=graph)
        wb2, meta2 = exp.build_workbook(export_type="internal", graph=graph)

        self.assertEqual(meta1["graph_hash"], meta2["graph_hash"])
        tabs = (exp.TAB_TECH_STACK, exp.TAB_VENDOR_CUSTOMER_LIST, exp.TAB_EVIDENCE_LEDGER, exp.TAB_MACRO_VIEWS)
        for tab in tabs:
            rows1 = list(wb1[tab].iter_rows(values_only=True))
            rows2 = list(wb2[tab].iter_rows(values_only=True))
            self.assertEqual(rows1, rows2, tab)


class EXP7_CurrentWinEligibilityMatchesPhase2(unittest.TestCase):
    def test_historical_deployment_status_not_a_current_win(self):
        historical_status = next(iter(eco._HISTORICAL_DEPLOYMENT_STATUSES))
        rel = _rel(deployment_status=historical_status)
        self.assertFalse(exp.is_current_win(rel))

    def test_working_profile_not_a_current_win(self):
        rel = _rel(deployment_status="working_profile_public_source_required")
        self.assertFalse(exp.is_current_win(rel))

    def test_inactive_status_not_a_current_win(self):
        rel = _rel(status="superseded")
        self.assertFalse(exp.is_current_win(rel))

    def test_active_clean_deployment_status_is_a_current_win(self):
        rel = _rel(status="active", deployment_status="active_rollout")
        self.assertTrue(exp.is_current_win(rel))


class EXP8_AllSevenTabsWritten(unittest.TestCase):
    def test_export_workbook_writes_seven_tabs_in_order(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "export.xlsx"
            graph = _graph(_rel())
            result = exp.export_workbook(export_type="shareable", output_path=output_path, graph=graph)
            self.assertEqual(result["path"], str(output_path))
            self.assertTrue(output_path.exists())

            import openpyxl
            wb = openpyxl.load_workbook(output_path)
            self.assertEqual(
                wb.sheetnames,
                [
                    exp.TAB_EXECUTIVE_SUMMARY, exp.TAB_BRAND_PROFILES, exp.TAB_TECH_STACK,
                    exp.TAB_VENDOR_CUSTOMER_LIST, exp.TAB_EVIDENCE_LEDGER, exp.TAB_MACRO_VIEWS,
                    exp.TAB_UPDATE_PLAYBOOK,
                ],
            )


class EXP10_BrandProfilesTab(unittest.TestCase):
    """Ecosystem Lookup Tool (2026-09-25): the new Brand Profiles tab --
    one row per brand entity, reusing brand_profile_common.get_profile()/
    shareable_view() directly so this can never silently disagree with
    what Team Portal's Company Profile card shows. Isolates bpc.ROOT so
    this never touches real brand_profiles/*.json."""

    def setUp(self):
        import tempfile
        from unittest.mock import patch
        import brand_profile_common as bpc
        self.tmpdir = tempfile.TemporaryDirectory()
        self._patch = patch.object(bpc, "ROOT", Path(self.tmpdir.name))
        self._patch.start()
        self.bpc = bpc

    def tearDown(self):
        self._patch.stop()
        self.tmpdir.cleanup()

    def _graph_with_technomic(self) -> dict:
        return {
            "version": 2, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-25",
            "entities": [
                {"id": "brand-checkers", "name": "Checkers & Rally's", "entity_type": "brand",
                 "attributes": {"unit_delta": 5.0, "technomic_history": {"2025": {"units": 900}}}},
                {"id": "vendor-hi-auto", "name": "Hi Auto", "entity_type": "vendor", "attributes": {}},
            ],
            "relationships": [],
        }

    def test_brand_profiles_tab_has_a_row_per_brand_entity_only(self):
        graph = self._graph_with_technomic()
        wb, _ = exp.build_workbook(export_type="shareable", graph=graph)
        ws = wb[exp.TAB_BRAND_PROFILES]
        rows = list(ws.iter_rows(values_only=True))
        self.assertEqual(len(rows), 2)  # header + 1 brand (vendor excluded)
        self.assertEqual(rows[1][0], "brand-checkers")

    def test_trajectory_computed_live_not_fabricated(self):
        graph = self._graph_with_technomic()
        wb, _ = exp.build_workbook(export_type="shareable", graph=graph)
        ws = wb[exp.TAB_BRAND_PROFILES]
        headers = [c.value for c in ws[1]]
        row = dict(zip(headers, [c.value for c in ws[2]]))
        self.assertEqual(row["trajectory_badge"], "Growing")
        self.assertEqual(row["trajectory_value_pct"], 5.0)

    def test_unresearched_brand_shows_honest_blanks_not_fabricated_content(self):
        graph = self._graph_with_technomic()
        wb, _ = exp.build_workbook(export_type="shareable", graph=graph)
        ws = wb[exp.TAB_BRAND_PROFILES]
        headers = [c.value for c in ws[1]]
        row = dict(zip(headers, [c.value for c in ws[2]]))
        self.assertIsNone(row["parent_ownership"])
        self.assertIsNone(row["synopsis"])
        self.assertEqual(row["leadership_confirmed"], "")

    def test_leadership_reported_unverified_only_in_internal(self):
        graph = self._graph_with_technomic()
        profile = self.bpc.empty_profile("brand-checkers", "Checkers & Rally's")
        profile["leadership"]["confirmed"] = [{"name": "Jane Doe", "title": "CEO"}]
        profile["leadership"]["reported_unverified"] = [{"name": "Rumor Guy"}]
        self.bpc.save_profile("brand-checkers", profile)

        wb_internal, _ = exp.build_workbook(export_type="internal", graph=graph)
        wb_shareable, _ = exp.build_workbook(export_type="shareable", graph=graph)

        internal_headers = [c.value for c in wb_internal[exp.TAB_BRAND_PROFILES][1]]
        shareable_headers = [c.value for c in wb_shareable[exp.TAB_BRAND_PROFILES][1]]
        self.assertIn("leadership_reported_unverified", internal_headers)
        self.assertNotIn("leadership_reported_unverified", shareable_headers)

        internal_row = dict(zip(internal_headers, [c.value for c in wb_internal[exp.TAB_BRAND_PROFILES][2]]))
        self.assertIn("Rumor Guy", internal_row["leadership_reported_unverified"])

        shareable_text = []
        for row in wb_shareable[exp.TAB_BRAND_PROFILES].iter_rows(values_only=True):
            shareable_text.extend(str(v) for v in row if v is not None)
        self.assertFalse(any("Rumor Guy" in t for t in shareable_text))

    def test_recent_signals_rendered_with_type_and_as_of(self):
        graph = self._graph_with_technomic()
        profile = self.bpc.empty_profile("brand-checkers", "Checkers & Rally's")
        self.bpc.add_signal(profile, value="Launching a loyalty relaunch.",
                             signal_type="strategic_initiative", as_of="2026-08-01")
        self.bpc.save_profile("brand-checkers", profile)

        wb, _ = exp.build_workbook(export_type="shareable", graph=graph)
        ws = wb[exp.TAB_BRAND_PROFILES]
        headers = [c.value for c in ws[1]]
        row = dict(zip(headers, [c.value for c in ws[2]]))
        self.assertIn("strategic_initiative", row["recent_signals"])
        self.assertIn("Launching a loyalty relaunch.", row["recent_signals"])
        self.assertIn("2026-08-01", row["recent_signals"])


class EXP9_EvidenceLedgerFallback(unittest.TestCase):
    def test_falls_back_to_sources_when_no_source_assertions(self):
        rel = _rel(sources=["src-a", "src-b"])
        rel.pop("source_assertions", None)
        entities = exp._index_entities(_graph(rel))
        rows = exp._evidence_rows(rel, entities)
        self.assertEqual([r["source_id"] for r in rows], ["src-a", "src-b"])

    def test_uses_source_assertions_when_present(self):
        rel = _rel(source_assertions=[
            {"source_id": "src-a", "url": "https://example.com/a", "title": "T", "publisher": "P",
             "discovered_at": "2026-07-01", "posture": "current"},
        ])
        entities = exp._index_entities(_graph(rel))
        rows = exp._evidence_rows(rel, entities)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["posture"], "current")


if __name__ == "__main__":
    unittest.main(verbosity=2)
