"""
test_import_technology_lifecycle_research.py — Technology Lifecycle Phase 1
Hunter packet importer.
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
import import_technology_lifecycle_research as itl  # noqa: E402

_GRAPH = {
    "entities": [
        {"id": "brand-burger-king", "name": "Burger King", "entity_type": "brand", "aliases": []},
        {"id": "vendor-par-technology", "name": "PAR Technology", "entity_type": "vendor", "aliases": []},
    ],
    "relationships": [],
}


def _rel_key(**overrides) -> dict:
    base = {
        "parent_entity_id": None, "brand_entity_id": "brand-burger-king", "operator_entity_id": None,
        "technology_category": "pos", "vendor_entity_id": "vendor-par-technology", "product": "PAR Brink POS",
    }
    base.update(overrides)
    return base


def _good_relationship_event_item(finding_id="f1", **payload_overrides):
    payload = {
        "event_id": "tre-1", "relationship_key": _rel_key(), "entity_level": "brand",
        "lifecycle_state": "selected", "state_date_or_range": "2024", "evidence": "e",
        "source_url": "https://example.com", "source_type": "press release",
        "confidence": "high", "evidence_type": "independent_evidence",
    }
    payload.update(payload_overrides)
    return {"record_type": "relationship_event", "finding_id": finding_id, "payload": payload}


class _IsolatedPathsMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self._orig_tl = {
            "RELATIONSHIP_EVENTS_PATH": tl.RELATIONSHIP_EVENTS_PATH, "GOVERNANCE_PATH": tl.GOVERNANCE_PATH,
            "PENETRATION_PATH": tl.PENETRATION_PATH, "CHANGE_EVENTS_PATH": tl.CHANGE_EVENTS_PATH,
            "FORCING_SIGNALS_PATH": tl.FORCING_SIGNALS_PATH,
        }
        tl.RELATIONSHIP_EVENTS_PATH = tmp / "rel.jsonl"
        tl.GOVERNANCE_PATH = tmp / "gov.jsonl"
        tl.PENETRATION_PATH = tmp / "pen.jsonl"
        tl.CHANGE_EVENTS_PATH = tmp / "chg.jsonl"
        tl.FORCING_SIGNALS_PATH = tmp / "fs.jsonl"
        self._orig_log_path = itl.IMPORT_LOG_PATH
        itl.IMPORT_LOG_PATH = tmp / "import_log.json"
        self._graph_patch = patch.object(tl, "_load_graph", lambda: _GRAPH)
        self._graph_patch.start()

    def tearDown(self):
        self._graph_patch.stop()
        for name, path in self._orig_tl.items():
            setattr(tl, name, path)
        itl.IMPORT_LOG_PATH = self._orig_log_path
        self._tmpdir.cleanup()


class TestImportFindings(_IsolatedPathsMixin, unittest.TestCase):
    def test_applies_well_formed_item(self):
        packet = {
            "schema": "rb.hunter_research_packet.v1", "packet_id": "p1",
            "payload_schema": "rb.technology_lifecycle_research.v1",
            "payload": {"technology_lifecycle_findings": [_good_relationship_event_item()]},
        }
        result = itl.import_findings(packet, dry_run=False)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["rejected"], 0)
        self.assertEqual(len(tl.load_records(tl.RELATIONSHIP_EVENTS_PATH)), 1)

    def test_malformed_item_rejected_without_blocking_rest(self):
        bad_item = {"record_type": "relationship_event", "finding_id": "bad", "payload": {"event_id": "x"}}  # missing required fields
        packet = {
            "schema": "rb.hunter_research_packet.v1", "packet_id": "p1",
            "payload_schema": "rb.technology_lifecycle_research.v1",
            "payload": {"technology_lifecycle_findings": [bad_item, _good_relationship_event_item(finding_id="f2")]},
        }
        result = itl.import_findings(packet, dry_run=False)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(len(result["rejected_errors"]), 1)
        self.assertEqual(result["rejected_errors"][0]["finding_id"], "bad")

    def test_unknown_record_type_rejected(self):
        item = {"record_type": "not_a_real_type", "finding_id": "f1", "payload": {}}
        packet = {
            "schema": "rb.hunter_research_packet.v1", "packet_id": "p1",
            "payload_schema": "rb.technology_lifecycle_research.v1",
            "payload": {"technology_lifecycle_findings": [item]},
        }
        result = itl.import_findings(packet, dry_run=False)
        self.assertEqual(result["rejected"], 1)

    def test_idempotent_on_rerun(self):
        packet = {
            "schema": "rb.hunter_research_packet.v1", "packet_id": "p1",
            "payload_schema": "rb.technology_lifecycle_research.v1",
            "payload": {"technology_lifecycle_findings": [_good_relationship_event_item()]},
        }
        itl.import_findings(packet, dry_run=False)
        result2 = itl.import_findings(packet, dry_run=False)
        self.assertEqual(result2["applied"], 0)
        self.assertEqual(result2["already_processed"], 1)
        self.assertEqual(len(tl.load_records(tl.RELATIONSHIP_EVENTS_PATH)), 1)

    def test_dry_run_writes_nothing(self):
        packet = {
            "schema": "rb.hunter_research_packet.v1", "packet_id": "p1",
            "payload_schema": "rb.technology_lifecycle_research.v1",
            "payload": {"technology_lifecycle_findings": [_good_relationship_event_item()]},
        }
        result = itl.import_findings(packet, dry_run=True)
        self.assertEqual(result["applied"], 1)  # counted, but...
        self.assertEqual(tl.load_records(tl.RELATIONSHIP_EVENTS_PATH), [])  # ...never written

    def test_multiple_record_types_in_one_packet(self):
        items = [
            _good_relationship_event_item(finding_id="f1"),
            {
                "record_type": "forcing_signal", "finding_id": "f2",
                "payload": {
                    "signal_id": "tfs-1", "brand_entity_id": "brand-burger-king", "entity_level": "brand",
                    "technology_category": "pos_hardware", "forcing_event_type": "os_eol", "detail": "d",
                    "evidence": "e", "source_url": None, "confidence": "medium", "evidence_type": "rbb_inference",
                },
            },
        ]
        packet = {
            "schema": "rb.hunter_research_packet.v1", "packet_id": "p1",
            "payload_schema": "rb.technology_lifecycle_research.v1",
            "payload": {"technology_lifecycle_findings": items},
        }
        result = itl.import_findings(packet, dry_run=False)
        self.assertEqual(result["applied"], 2)
        self.assertEqual(result["by_record_type"]["relationship_event"]["applied"], 1)
        self.assertEqual(result["by_record_type"]["forcing_signal"]["applied"], 1)

    def test_legacy_direct_payload_shape_supported(self):
        """A packet without the rb.hunter_research_packet.v1 envelope --
        the payload itself at top level -- is still accepted."""
        packet = {"packet_id": "p1", "technology_lifecycle_findings": [_good_relationship_event_item()]}
        result = itl.import_findings(packet, dry_run=False)
        self.assertEqual(result["applied"], 1)


if __name__ == "__main__":
    unittest.main()
