#!/usr/bin/env python3
"""
test_import_genius_competitive_research.py — RB-2026-09-26.

Coverage for import_genius_competitive_research.py against a small,
hand-built fixture payload matching the real "rb.competitive_research.v1"
schema (confirmed against the real export this was built to load).
Isolated against disposable competitor_intelligence/genius_capabilities/
import-log paths -- never touches real production data.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import import_genius_competitive_research as igcr  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import genius_capabilities as gc  # noqa: E402
import ecosystem_intelligence as ei  # noqa: E402


def _claim(dimension, claim, confidence, *, is_inference=False, urls=None, source_text=None):
    return {
        "dimension": dimension, "claim": claim, "confidence": confidence,
        "confidence_raw": confidence, "is_inference": is_inference,
        "source_text": source_text, "urls": urls or [], "urls_inherited_from_prior_claim": False,
    }


def _payload():
    return {
        "schema": "rb.competitive_research.v1",
        "generated": "2026-09-26",
        "entities": [
            {
                "entity": "Genius N Software / Genius for Enterprise POS (formerly Xenial Cloud)",
                "category": "POS", "phase": "phase1_genius", "source_file": "01_genius.md",
                "recommended_add": False, "flag_acquired": False,
                "claims": [
                    _claim("Positioning", "Cloud-native enterprise POS", "VERY HIGH", urls=["https://example.com/a"]),
                    _claim("Strengths", "Unified hardware/software stack", "HIGH"),
                    _claim("Strengths", "Inference-only strength, should not become a capability", "HIGH", is_inference=True),
                    _claim("Weaknesses / Vulnerabilities", "Thin third-party analyst coverage", "LOW"),
                    _claim("Financial / Market Health", "Bookings +25% QoQ in Q2 2026", "VERY HIGH"),
                    _claim("C-Suite & Leadership", "Dedicated GM: not found", "NOT_FOUND"),
                ],
            },
            {
                "entity": "Global Payments (parent)", "category": "Parent / Payments",
                "phase": "phase1_genius", "source_file": "01_genius.md",
                "recommended_add": False, "flag_acquired": False,
                "claims": [
                    _claim("Snapshot", "HQ Atlanta, GA; NYSE: GPN", "VERY HIGH"),
                    _claim("Named Customers", "See sub-lines below", "CROSS_REFERENCE"),
                ],
            },
            {
                "entity": "Toast", "category": "POS", "phase": "phase2_competitor",
                "source_file": "02_pos.md", "recommended_add": False, "flag_acquired": False,
                "claims": [
                    _claim("Strengths", "Record net location adds in Q2 2026", "VERY HIGH", urls=["https://example.com/toast"]),
                    _claim("Weaknesses / Vulnerabilities", "Hardware cost pressure flagged for 2027", "MEDIUM"),
                    _claim("Rumors / Unconfirmed", "Anonymous forum claim about internal layoffs", "LOW"),
                ],
            },
            {
                "entity": "Acrelec", "category": "Digital Menu Boards (RECOMMENDED ADD)",
                "phase": "phase2_competitor", "source_file": "06_menu_boards.md",
                "recommended_add": True, "flag_acquired": False,
                "claims": [
                    _claim("Positioning", "Menu-board and kiosk maker", "MEDIUM"),
                ],
            },
            {
                "entity": "Compeat", "category": "Back Office (ACQUIRED; remove as standalone)",
                "phase": "phase2_competitor", "source_file": "07_back_office.md",
                "recommended_add": False, "flag_acquired": True,
                "claims": [
                    _claim("Positioning / Strengths / Weaknesses", "Folded into Restaurant365", "CROSS_REFERENCE"),
                ],
            },
        ],
    }


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)

        self._orig_graph_path = ei.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_cic_root = cic.ROOT
        self._orig_gc_path = gc.GENIUS_CAPABILITIES_PATH
        self._orig_gc_evidence_path = gc.GENIUS_EVIDENCE_PATH
        self._orig_import_log = igcr.IMPORT_LOG_PATH

        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = tmp / "ecosystem_intelligence.json"
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH.write_text(json.dumps({
            "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-09-26",
            "entities": [], "relationships": [], "signals": [], "sources": [],
            "assessments": [], "user_relevance": [], "strategic_recommendations": [],
        }), encoding="utf-8")
        cic.ROOT = tmp / "competitor_intelligence"
        (cic.ROOT / "_portfolio").mkdir(parents=True)
        (cic.ROOT / "_portfolio" / "competitor_registry.json").write_text(
            json.dumps({"registry": [{"competitor_slug": "toast", "display_name": "Toast"}]}), encoding="utf-8",
        )
        comp_dir = cic.ROOT / "competitors" / "toast"
        comp_dir.mkdir(parents=True)
        comp_dir_json = {
            "competitor_id": "comp-toast", "competitor_slug": "toast", "display_name": "Toast",
            "vendor_entity_id": None, "competes_on": [], "positioning_summary": "", "todds_pov": "",
            "vs_genius": {"genius_advantages": [], "competitor_advantages": []},
            "category_battle_cards": {}, "last_evidence_date": None,
        }
        (comp_dir / "competitor.json").write_text(json.dumps(comp_dir_json), encoding="utf-8")
        (comp_dir / "evidence.jsonl").write_text("", encoding="utf-8")

        gc.GENIUS_CAPABILITIES_PATH = tmp / "genius_capabilities.json"
        gc.GENIUS_EVIDENCE_PATH = tmp / "genius_own_evidence.jsonl"
        igcr.IMPORT_LOG_PATH = tmp / "import_log.json"

    def tearDown(self):
        ei.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        cic.ROOT = self._orig_cic_root
        gc.GENIUS_CAPABILITIES_PATH = self._orig_gc_path
        gc.GENIUS_EVIDENCE_PATH = self._orig_gc_evidence_path
        igcr.IMPORT_LOG_PATH = self._orig_import_log
        self._tmpdir.cleanup()


class TestDryRun(_IsolatedFixtureMixin):
    def test_dry_run_writes_nothing(self):
        igcr.import_research(_payload(), dry_run=True)
        self.assertFalse(gc.GENIUS_CAPABILITIES_PATH.exists())
        self.assertFalse(gc.GENIUS_EVIDENCE_PATH.exists())
        self.assertEqual((cic.ROOT / "competitors" / "toast" / "evidence.jsonl").read_text(), "")

    def test_dry_run_counts_match_real_run(self):
        dry = igcr.import_research(_payload(), dry_run=True)
        real = igcr.import_research(_payload(), dry_run=False)
        for key in ("genius_capabilities_added", "genius_evidence_added",
                    "competitor_evidence_added", "gap_points_added"):
            self.assertEqual(dry[key], real[key], key)


class TestRealImport(_IsolatedFixtureMixin):
    def test_not_found_and_cross_reference_are_skipped(self):
        result = igcr.import_research(_payload(), dry_run=False)
        # 1 NOT_FOUND (Genius N Software) + 1 CROSS_REFERENCE (GPN parent's
        # Named Customers pointer) -- Compeat's own CROSS_REFERENCE claim
        # is never reached at all, since flag_acquired skips the whole
        # entity before its claims are iterated.
        self.assertEqual(result["claims_skipped_not_found_or_cross_ref"], 2)

    def test_capability_eligible_claim_becomes_a_capability(self):
        igcr.import_research(_payload(), dry_run=False)
        caps = gc.list_capabilities("pos")
        points = [c["point"] for c in caps]
        self.assertIn("Cloud-native enterprise POS", points)
        self.assertIn("Unified hardware/software stack", points)

    def test_inference_flagged_strength_does_not_become_a_capability(self):
        igcr.import_research(_payload(), dry_run=False)
        caps = gc.list_capabilities("pos")
        points = [c["point"] for c in caps]
        self.assertNotIn("Inference-only strength, should not become a capability", points)

    def test_capability_entries_tagged_with_system_provenance_not_todd(self):
        igcr.import_research(_payload(), dry_run=False)
        caps = gc.list_capabilities("pos")
        self.assertTrue(all(c["added_by"] == igcr.SOURCE_TAG for c in caps))
        self.assertTrue(all(c["added_by"] != "Todd Vahlsing" for c in caps))

    def test_non_capability_genius_claim_becomes_genius_evidence(self):
        igcr.import_research(_payload(), dry_run=False)
        evidence = gc.list_evidence("pos")
        summaries = [e["summary"] for e in evidence]
        self.assertIn("Thin third-party analyst coverage", summaries)
        self.assertIn("Bookings +25% QoQ in Q2 2026", summaries)

    def test_parent_entity_claims_land_in_parent_scope(self):
        igcr.import_research(_payload(), dry_run=False)
        evidence = gc.list_evidence("parent")
        self.assertEqual(len(evidence), 1)
        self.assertIn("HQ Atlanta, GA", evidence[0]["summary"])

    def test_confidence_mapped_to_rb_schema_levels(self):
        igcr.import_research(_payload(), dry_run=False)
        evidence = gc.list_evidence("pos")
        by_summary = {e["summary"]: e for e in evidence}
        self.assertEqual(by_summary["Bookings +25% QoQ in Q2 2026"]["confidence"], "critical")
        self.assertEqual(by_summary["Thin third-party analyst coverage"]["confidence"], "low")

    def test_competitor_evidence_written_for_every_real_claim(self):
        igcr.import_research(_payload(), dry_run=False)
        evidence = cic.load_jsonl(cic.ROOT / "competitors" / "toast" / "evidence.jsonl")
        self.assertEqual(len(evidence), 3)  # Strengths + Weaknesses + Rumors (LOW is still real, not NOT_FOUND)

    def test_strength_promoted_to_competitor_advantage_gap_point(self):
        igcr.import_research(_payload(), dry_run=False)
        comp = cic.load_competitor("toast")["competitor"]
        points = [p["point"] for p in comp["vs_genius"]["competitor_advantages"]]
        self.assertIn("Record net location adds in Q2 2026", points)

    def test_weakness_promoted_to_genius_advantage_gap_point(self):
        igcr.import_research(_payload(), dry_run=False)
        comp = cic.load_competitor("toast")["competitor"]
        points = [p["point"] for p in comp["vs_genius"]["genius_advantages"]]
        self.assertIn("Hardware cost pressure flagged for 2027", points)

    def test_gap_point_linked_to_real_evidence_id(self):
        igcr.import_research(_payload(), dry_run=False)
        comp = cic.load_competitor("toast")["competitor"]
        gap = comp["vs_genius"]["competitor_advantages"][0]
        self.assertIsNotNone(gap["evidence_id"])
        evidence_ids = {e["evidence_id"] for e in cic.load_jsonl(cic.ROOT / "competitors" / "toast" / "evidence.jsonl")}
        self.assertIn(gap["evidence_id"], evidence_ids)

    def test_recommended_add_competitor_gets_created(self):
        igcr.import_research(_payload(), dry_run=False)
        data = cic.load_competitor("acrelec")
        self.assertEqual(data["competitor"]["display_name"], "Acrelec")

    def test_flag_acquired_entity_with_no_real_claims_is_skipped(self):
        """Compeat (round 1): every claim is a pure cross-reference to its
        acquirer (Restaurant365) -- nothing real to preserve."""
        result = igcr.import_research(_payload(), dry_run=False)
        skipped_names = [s["entity"] for s in result["entities_skipped"]]
        self.assertIn("Compeat", skipped_names)
        with self.assertRaises(FileNotFoundError):
            cic.load_competitor("compeat")

    def test_flag_acquired_entity_with_real_claims_is_still_processed(self):
        """RB-2026-09-26, round 2: found live that flag_acquired can carry
        real, sourced content (an acquisition fact, positioning of the
        absorbed product, competitive implications) -- flag_acquired must
        not silently discard that. See Revel Systems/CardFree/etc. in the
        real round-2 import."""
        payload = _payload()
        payload["entities"].append({
            "entity": "Acquired But Real", "category": "POS", "phase": "phase2_competitor",
            "source_file": "02_pos.md", "recommended_add": False, "flag_acquired": True,
            "claims": [
                _claim("Snapshot", "Acquired by BigCo for $50M, closed 2025", "VERY HIGH"),
                _claim("Positioning", "Now folded into BigCo's enterprise suite", "HIGH"),
            ],
        })
        result = igcr.import_research(payload, dry_run=False)
        skipped_names = [s["entity"] for s in result["entities_skipped"]]
        self.assertNotIn("Acquired But Real", skipped_names)
        data = cic.load_competitor("acquired-but-real")
        summaries = [e["summary"] for e in data["evidence"]]
        self.assertIn("Acquired by BigCo for $50M, closed 2025", summaries)

    def test_rerun_is_idempotent_no_duplicate_writes(self):
        igcr.import_research(_payload(), dry_run=False)
        result2 = igcr.import_research(_payload(), dry_run=False)
        self.assertEqual(result2["genius_capabilities_added"], 0)
        self.assertEqual(result2["genius_evidence_added"], 0)
        self.assertEqual(result2["competitor_evidence_added"], 0)
        self.assertGreater(result2["claims_skipped_already_imported"], 0)
        evidence = cic.load_jsonl(cic.ROOT / "competitors" / "toast" / "evidence.jsonl")
        self.assertEqual(len(evidence), 3)  # unchanged, not doubled

    def test_new_claim_in_a_second_payload_still_imports(self):
        """Re-running against an EXPANDED export (the real RTI-footprint-
        addition scenario) imports only the new claim, not the old ones
        again."""
        igcr.import_research(_payload(), dry_run=False)
        payload2 = _payload()
        payload2["entities"][2]["claims"].append(
            _claim("Named Customers", "A brand-new customer claim added later", "HIGH")
        )
        result2 = igcr.import_research(payload2, dry_run=False)
        self.assertEqual(result2["competitor_evidence_added"], 1)
        evidence = cic.load_jsonl(cic.ROOT / "competitors" / "toast" / "evidence.jsonl")
        self.assertEqual(len(evidence), 4)


if __name__ == "__main__":
    unittest.main()
