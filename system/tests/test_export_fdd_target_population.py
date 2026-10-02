"""
test_export_fdd_target_population.py — 2026-10-02.

Real gap this closes: a live Codex/ChatGPT-Project Hunter task running the
first FDD Technology Governance & Economics research cycle had no local
source for the brief's ">90-location canonical population" and reached for
the live Trusted Chat API, hitting the same DNS failure already fixed once
for Franchisee Finder's equivalent gap. This export reads only
system/ecosystem_intelligence.json and system/technology_lifecycle/
fdd_sources.jsonl -- no network call.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import technology_lifecycle as tl  # noqa: E402
import export_fdd_target_population as efp  # noqa: E402


def _graph(entities: list[dict]) -> dict:
    return {"version": 1, "contract": "rb_ecosystem_intelligence_v1", "entities": entities, "relationships": [], "sources": []}


def _brand(entity_id: str, name: str, unit_count: float, segment: str = "LSR") -> dict:
    return {
        "id": entity_id, "name": name, "entity_type": "brand", "subtype": "restaurant_brand",
        "status": "active", "domains": ["restaurants"], "aliases": [],
        "attributes": {"unit_count": unit_count, "segment": segment},
    }


class _IsolatedStoreMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self._orig_root = tl.ROOT
        tl.ROOT = tmp
        tl.FDD_SOURCES_PATH = tmp / "fdd_sources.jsonl"
        efp.tl.FDD_SOURCES_PATH = tl.FDD_SOURCES_PATH

    def tearDown(self):
        tl.ROOT = self._orig_root
        self._tmpdir.cleanup()


class TestExportFddTargetPopulation(_IsolatedStoreMixin, unittest.TestCase):
    def test_filters_to_unit_count_above_threshold(self):
        graph = _graph([
            _brand("brand-big", "Big Chain", 500),
            _brand("brand-small", "Small Chain", 10),
            _brand("brand-exactly-90", "Exactly Ninety", 90),
            {"id": "vendor-x", "name": "Vendor X", "entity_type": "vendor", "attributes": {"unit_count": 9999}},
        ])
        with patch.object(tl, "_load_graph", lambda: graph):
            data = efp.export_fdd_target_population()
        ids = {b["id"] for b in data["brands"]}
        self.assertEqual(ids, {"brand-big"})

    def test_custom_unit_threshold(self):
        graph = _graph([_brand("brand-mid", "Mid Chain", 50)])
        with patch.object(tl, "_load_graph", lambda: graph):
            data = efp.export_fdd_target_population(unit_threshold=40)
        self.assertEqual([b["id"] for b in data["brands"]], ["brand-mid"])

    def test_sorted_by_unit_count_descending(self):
        graph = _graph([
            _brand("brand-a", "A", 100),
            _brand("brand-b", "B", 5000),
            _brand("brand-c", "C", 500),
        ])
        with patch.object(tl, "_load_graph", lambda: graph):
            data = efp.export_fdd_target_population()
        self.assertEqual([b["id"] for b in data["brands"]], ["brand-b", "brand-c", "brand-a"])

    def test_batches_of_25(self):
        entities = [_brand(f"brand-{i}", f"Brand {i}", 1000 - i) for i in range(60)]
        graph = _graph(entities)
        with patch.object(tl, "_load_graph", lambda: graph):
            data = efp.export_fdd_target_population()
        self.assertEqual(len(data["brands"]), 60)
        self.assertEqual([len(b) for b in data["batches"]], [25, 25, 10])

    def test_no_fdd_source_on_file_is_coverage_none(self):
        graph = _graph([_brand("brand-x", "X", 200)])
        with patch.object(tl, "_load_graph", lambda: graph):
            data = efp.export_fdd_target_population()
        self.assertEqual(data["brands"][0]["coverage"], "none")
        self.assertEqual(data["brands"][0]["research_gaps"], ["fdd_governance_economics"])
        self.assertEqual(data["coverage_summary"], {"total_brands": 1, "has_fdd_source": 0, "none": 1})

    def test_current_fdd_source_on_file_is_coverage_has_fdd_source(self):
        graph = _graph([_brand("brand-x", "X", 200)])
        with patch.object(tl, "_load_graph", lambda: graph):
            tl.record_fdd_source(
                fdd_id="fdd-brand-x-2026", brand_id="brand-x", fdd_year="2026",
                document_status="current", evidence_type="independent_evidence", confidence="high",
                source_url="https://example.com/fdd",
            )
            data = efp.export_fdd_target_population()
        self.assertEqual(data["brands"][0]["coverage"], "has_fdd_source")
        self.assertEqual(data["brands"][0]["research_gaps"], [])

    def test_not_located_fdd_source_still_counts_as_coverage_none(self):
        """A document_status:'not_located' row is itself a real finding
        (a search was done, nothing was found) -- but it does NOT satisfy
        coverage, since the actual research the brief asks for hasn't
        happened yet. Distinguishes 'no FDD exists' from 'no FDD was
        looked for yet' (see record_fdd_source's own docstring)."""
        graph = _graph([_brand("brand-x", "X", 200)])
        with patch.object(tl, "_load_graph", lambda: graph):
            tl.record_fdd_source(
                fdd_id="fdd-brand-x-2026", brand_id="brand-x", fdd_year="2026",
                document_status="not_located", evidence_type="independent_evidence", confidence="low",
            )
            data = efp.export_fdd_target_population()
        self.assertEqual(data["brands"][0]["coverage"], "none")

    def test_only_gaps_excludes_covered_brands(self):
        graph = _graph([_brand("brand-covered", "Covered", 200), _brand("brand-gappy", "Gappy", 200)])
        with patch.object(tl, "_load_graph", lambda: graph):
            tl.record_fdd_source(
                fdd_id="fdd-covered-2026", brand_id="brand-covered", fdd_year="2026",
                document_status="current", evidence_type="independent_evidence", confidence="high",
            )
            data = efp.export_fdd_target_population(only_gaps=True)
        self.assertEqual([b["id"] for b in data["brands"]], ["brand-gappy"])

    def test_record_fdd_source_rejects_unknown_brand(self):
        graph = _graph([])
        with patch.object(tl, "_load_graph", lambda: graph):
            with self.assertRaises(tl.TechnologyLifecycleError):
                tl.record_fdd_source(
                    fdd_id="fdd-x", brand_id="brand-does-not-exist", fdd_year="2026",
                    document_status="current", evidence_type="independent_evidence", confidence="high",
                )

    def test_record_fdd_source_rejects_bad_document_status(self):
        graph = _graph([_brand("brand-x", "X", 200)])
        with patch.object(tl, "_load_graph", lambda: graph):
            with self.assertRaises(tl.TechnologyLifecycleError):
                tl.record_fdd_source(
                    fdd_id="fdd-x", brand_id="brand-x", fdd_year="2026",
                    document_status="not_a_real_status", evidence_type="independent_evidence", confidence="high",
                )

    def test_list_fdd_sources_for_brand_excludes_superseded(self):
        graph = _graph([_brand("brand-x", "X", 200)])
        with patch.object(tl, "_load_graph", lambda: graph):
            tl.record_fdd_source(
                fdd_id="fdd-x-2024", brand_id="brand-x", fdd_year="2024",
                document_status="current", evidence_type="independent_evidence", confidence="high",
            )
            tl.record_fdd_source(
                fdd_id="fdd-x-2026", brand_id="brand-x", fdd_year="2026",
                document_status="current", evidence_type="independent_evidence", confidence="high",
                supersedes="fdd-x-2024",
            )
            sources = tl.list_fdd_sources_for_brand("brand-x")
        self.assertEqual([s["fdd_id"] for s in sources], ["fdd-x-2026"])


if __name__ == "__main__":
    unittest.main()
