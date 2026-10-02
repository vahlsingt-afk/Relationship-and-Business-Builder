"""
test_import_fdd_research.py — FDD Technology Governance & Economics
importer (system/technology_lifecycle/FDD_GOVERNANCE_ECONOMICS_BRIEF.md,
§21). Includes the brief's own "operational addition": run the sample
JSON contract (system/technology_lifecycle/FDD_SAMPLE_PACKET.json) through
one simulated brand import end-to-end, to catch a schema problem on one
record before the real >90-location batches begin -- entirely against an
ISOLATED graph/store fixture, never the real ecosystem_intelligence.json
or real jsonl stores.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import technology_lifecycle as tl  # noqa: E402
import import_fdd_research as ifr  # noqa: E402

SAMPLE_PACKET_PATH = ROOT / "system" / "technology_lifecycle" / "FDD_SAMPLE_PACKET.json"

_GRAPH = {
    "entities": [
        {"id": "brand-sample-fdd-chain", "name": "Sample FDD Chain", "entity_type": "brand", "aliases": []},
        {"id": "vendor-sample-pos-co", "name": "Sample POS Co", "entity_type": "vendor", "aliases": []},
    ],
    "relationships": [],
}


def _good_governance_item(finding_id="f1", **overrides):
    payload = {
        "governance_id": "gov-1", "brand_entity_id": "brand-sample-fdd-chain", "technology_category": "pos",
        "governance_state": "mandated", "evidence": "e", "source_url": "https://example.com",
        "confidence": "high", "evidence_type": "independent_evidence",
    }
    payload.update(overrides)
    return {"record_type": "governance", "finding_id": finding_id, "payload": payload}


class _IsolatedPathsMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self._orig_tl = {
            name: getattr(tl, name) for name in (
                "RELATIONSHIP_EVENTS_PATH", "GOVERNANCE_PATH", "PENETRATION_PATH", "CHANGE_EVENTS_PATH",
                "FORCING_SIGNALS_PATH", "FDD_SOURCES_PATH", "TECHNOLOGY_ECONOMICS_PATH",
                "GOVERNANCE_CHANGE_EVENTS_PATH", "PENETRATION_RECONCILIATION_PATH", "FDD_RESEARCH_GAPS_PATH",
                "ENTITY_RESOLUTION_REVIEW_PATH",
            )
        }
        tl.RELATIONSHIP_EVENTS_PATH = tmp / "rel.jsonl"
        tl.GOVERNANCE_PATH = tmp / "gov.jsonl"
        tl.PENETRATION_PATH = tmp / "pen.jsonl"
        tl.CHANGE_EVENTS_PATH = tmp / "chg.jsonl"
        tl.FORCING_SIGNALS_PATH = tmp / "fs.jsonl"
        tl.FDD_SOURCES_PATH = tmp / "fdd_sources.jsonl"
        tl.TECHNOLOGY_ECONOMICS_PATH = tmp / "economics.jsonl"
        tl.GOVERNANCE_CHANGE_EVENTS_PATH = tmp / "gov_change.jsonl"
        tl.PENETRATION_RECONCILIATION_PATH = tmp / "pen_reconciliation.jsonl"
        tl.FDD_RESEARCH_GAPS_PATH = tmp / "fdd_gaps.jsonl"
        tl.ENTITY_RESOLUTION_REVIEW_PATH = tmp / "entity_review.json"
        self._orig_log_path = ifr.IMPORT_LOG_PATH
        ifr.IMPORT_LOG_PATH = tmp / "import_log.json"
        self._graph_patch = patch.object(tl, "_load_graph", lambda: _GRAPH)
        self._graph_patch.start()

    def tearDown(self):
        self._graph_patch.stop()
        for name, path in self._orig_tl.items():
            setattr(tl, name, path)
        ifr.IMPORT_LOG_PATH = self._orig_log_path
        self._tmpdir.cleanup()


class TestImportFindings(_IsolatedPathsMixin, unittest.TestCase):
    def test_applies_well_formed_governance_item(self):
        packet = {
            "schema": "rb.hunter_research_packet.v1", "packet_id": "p1",
            "payload_schema": "rb.fdd_governance_economics_research.v1",
            "payload": {"fdd_governance_economics_findings": [_good_governance_item()]},
        }
        result = ifr.import_findings(packet, dry_run=False)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["rejected"], 0)
        self.assertEqual(len(tl.load_records(tl.GOVERNANCE_PATH)), 1)

    def test_malformed_item_rejected_without_blocking_rest(self):
        bad_item = {"record_type": "governance", "finding_id": "bad", "payload": {"governance_id": "x"}}
        packet = {
            "schema": "rb.hunter_research_packet.v1", "packet_id": "p1",
            "payload_schema": "rb.fdd_governance_economics_research.v1",
            "payload": {"fdd_governance_economics_findings": [bad_item, _good_governance_item(finding_id="f2")]},
        }
        result = ifr.import_findings(packet, dry_run=False)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["rejected_errors"][0]["finding_id"], "bad")

    def test_unknown_record_type_rejected(self):
        item = {"record_type": "not_a_real_type", "finding_id": "x", "payload": {}}
        packet = {
            "schema": "rb.hunter_research_packet.v1", "packet_id": "p1",
            "payload_schema": "rb.fdd_governance_economics_research.v1",
            "payload": {"fdd_governance_economics_findings": [item]},
        }
        result = ifr.import_findings(packet, dry_run=False)
        self.assertEqual(result["rejected"], 1)

    def test_idempotent_reimport_does_not_double_apply(self):
        packet = {
            "schema": "rb.hunter_research_packet.v1", "packet_id": "p1",
            "payload_schema": "rb.fdd_governance_economics_research.v1",
            "payload": {"fdd_governance_economics_findings": [_good_governance_item()]},
        }
        ifr.import_findings(packet, dry_run=False)
        result2 = ifr.import_findings(packet, dry_run=False)
        self.assertEqual(result2["applied"], 0)
        self.assertEqual(result2["already_processed"], 1)
        self.assertEqual(len(tl.load_records(tl.GOVERNANCE_PATH)), 1)

    def test_dry_run_writes_nothing(self):
        packet = {
            "schema": "rb.hunter_research_packet.v1", "packet_id": "p1",
            "payload_schema": "rb.fdd_governance_economics_research.v1",
            "payload": {"fdd_governance_economics_findings": [_good_governance_item()]},
        }
        result = ifr.import_findings(packet, dry_run=True)
        self.assertEqual(result["applied"], 1)  # counted, but not written
        self.assertEqual(tl.load_records(tl.GOVERNANCE_PATH), [])

    def test_entity_resolution_review_item_queues_not_applies(self):
        item = {
            "record_type": "entity_resolution_review", "finding_id": "f-review",
            "payload": {
                "review_id": "err-1", "raw_name_or_identifier": "Unknown Vendor Name",
                "context": "FDD mentions an unrecognized vendor.",
            },
        }
        packet = {
            "schema": "rb.hunter_research_packet.v1", "packet_id": "p1",
            "payload_schema": "rb.fdd_governance_economics_research.v1",
            "payload": {"fdd_governance_economics_findings": [item]},
        }
        result = ifr.import_findings(packet, dry_run=False)
        self.assertEqual(result["queued_for_entity_review"], 1)
        self.assertEqual(result["applied"], 0)
        pending = tl.list_entity_resolution_review("pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["raw_name_or_identifier"], "Unknown Vendor Name")

    def test_unresolvable_brand_id_rejected_not_silently_created(self):
        """Brief §1: an entity that can't be resolved must never silently
        create a new one -- a governance/economics finding naming an
        unknown brand_entity_id is a real rejection (the research should
        have used entity_resolution_review instead)."""
        item = _good_governance_item(brand_entity_id="brand-does-not-exist")
        packet = {
            "schema": "rb.hunter_research_packet.v1", "packet_id": "p1",
            "payload_schema": "rb.fdd_governance_economics_research.v1",
            "payload": {"fdd_governance_economics_findings": [item]},
        }
        result = ifr.import_findings(packet, dry_run=False)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(tl.load_records(tl.GOVERNANCE_PATH), [])


class TestSampleContractSimulatedImport(_IsolatedPathsMixin, unittest.TestCase):
    """The brief's own 'operational addition': produce a sample JSON
    contract plus one simulated brand import before real batches begin.
    This runs the real, checked-in sample packet through the real
    importer -- against this test's isolated graph/store fixture only,
    never the real ecosystem_intelligence.json or real jsonl stores."""

    def test_sample_packet_imports_cleanly_with_zero_rejections(self):
        packet = json.loads(SAMPLE_PACKET_PATH.read_text(encoding="utf-8"))
        result = ifr.import_findings(packet, dry_run=False)

        self.assertEqual(result["rejected"], 0, result["rejected_errors"])
        self.assertEqual(result["items_received"], 11)
        self.assertEqual(result["applied"], 10)  # every item except the one entity_resolution_review
        self.assertEqual(result["queued_for_entity_review"], 1)

    def test_sample_packet_writes_land_in_every_store(self):
        packet = json.loads(SAMPLE_PACKET_PATH.read_text(encoding="utf-8"))
        ifr.import_findings(packet, dry_run=False)

        self.assertEqual(len(tl.load_records(tl.FDD_SOURCES_PATH)), 2)  # current + prior
        self.assertEqual(len(tl.load_records(tl.GOVERNANCE_PATH)), 1)
        self.assertEqual(len(tl.load_records(tl.TECHNOLOGY_ECONOMICS_PATH)), 1)
        self.assertEqual(len(tl.load_records(tl.GOVERNANCE_CHANGE_EVENTS_PATH)), 1)
        self.assertEqual(len(tl.load_records(tl.PENETRATION_RECONCILIATION_PATH)), 1)
        self.assertEqual(len(tl.load_records(tl.RELATIONSHIP_EVENTS_PATH)), 1)
        self.assertEqual(len(tl.load_records(tl.PENETRATION_PATH)), 1)
        self.assertEqual(len(tl.load_records(tl.FORCING_SIGNALS_PATH)), 1)
        self.assertEqual(len(tl.load_records(tl.FDD_RESEARCH_GAPS_PATH)), 1)
        self.assertEqual(len(tl.list_entity_resolution_review("pending")), 1)

    def test_sample_packet_profile_reads_back_correctly(self):
        """End-to-end proof, not just per-store counts: the SAME derived
        view the API and Account Background Brief both read
        (get_entity_technology_profile) correctly surfaces everything the
        sample packet wrote, for the brand it was written about."""
        packet = json.loads(SAMPLE_PACKET_PATH.read_text(encoding="utf-8"))
        ifr.import_findings(packet, dry_run=False)

        profile = tl.get_entity_technology_profile("brand-sample-fdd-chain")
        self.assertEqual(len(profile["fdd_sources"]), 2)
        self.assertEqual(len(profile["governance"]), 1)
        self.assertEqual(len(profile["economics"]), 1)
        self.assertEqual(len(profile["governance_change_events"]), 1)
        self.assertEqual(len(profile["penetration_reconciliation"]), 1)
        self.assertEqual(len(profile["open_research_gaps"]), 1)
        self.assertEqual(profile["governance"][0]["fdd_sourced_fields"]["grandfathering_status"], "grandfathering_created")

    def test_sample_packet_reimport_is_idempotent(self):
        packet = json.loads(SAMPLE_PACKET_PATH.read_text(encoding="utf-8"))
        ifr.import_findings(packet, dry_run=False)
        result2 = ifr.import_findings(packet, dry_run=False)
        self.assertEqual(result2["applied"], 0)
        self.assertEqual(result2["queued_for_entity_review"], 0)
        self.assertEqual(result2["already_processed"], 11)
        # confirms nothing double-wrote
        self.assertEqual(len(tl.load_records(tl.FDD_SOURCES_PATH)), 2)


if __name__ == "__main__":
    unittest.main()
