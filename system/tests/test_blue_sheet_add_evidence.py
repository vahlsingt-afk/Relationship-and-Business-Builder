"""
test_blue_sheet_add_evidence.py — RB-2026-08-28.

add_evidence() is the structured, reviewed counterpart to
account_reference_detector.py's automatic reference-linking: a clean
one-call action for recording a REAL commercial term/claim/participant,
mirroring competitor_intelligence.add_competitive_note()'s discipline
(structured fields only, never invented, never a model-generated summary
presented as fact).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "blue_sheets" / "_engine"))

import add_evidence as ae  # noqa: E402
import common as bs_common  # noqa: E402


class TestAddEvidence(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)
        self._orig_root = bs_common.CUSTOMERS_PROSPECTS_ROOT
        bs_common.CUSTOMERS_PROSPECTS_ROOT = tmp_root

        (tmp_root / "_portfolio").mkdir(parents=True)
        (tmp_root / "accounts" / "acme").mkdir(parents=True)
        registry = {"registry": [{"account_id": "acct-acme", "status": "current", "engagement_tier": "active_engagement", "last_evidence_date": "2026-08-01"}]}
        (tmp_root / "_portfolio" / "customers_prospects_registry.json").write_text(json.dumps(registry), encoding="utf-8")
        (tmp_root / "accounts" / "acme" / "evidence.jsonl").write_text("", encoding="utf-8")

    def tearDown(self):
        bs_common.CUSTOMERS_PROSPECTS_ROOT = self._orig_root
        self._tmpdir.cleanup()

    def test_adds_real_evidence_record(self):
        result = ae.add_evidence(
            "acme", excerpt="Project management fee will be held off at the RFP stage.",
            source_type="internal_pricing_call_transcript",
            extracted_claims=["PM fee deferred to post-RFP justification"],
            participants=["Todd Vahlsing", "Ryan Hildebrand"],
            event_date="2026-08-28", confidence="high",
        )
        self.assertTrue(result["ok"])
        evidence = bs_common.load_jsonl(bs_common.account_dir("acme") / "evidence.jsonl")
        self.assertEqual(len(evidence), 1)
        record = evidence[0]
        self.assertEqual(record["excerpt"], "Project management fee will be held off at the RFP stage.")
        self.assertEqual(record["participants"], ["Todd Vahlsing", "Ryan Hildebrand"])
        self.assertEqual(record["confidence"], "high")

    def test_rejects_empty_excerpt(self):
        with self.assertRaises(ValueError):
            ae.add_evidence("acme", excerpt="  ", source_type="note")

    def test_rejects_empty_source_type(self):
        with self.assertRaises(ValueError):
            ae.add_evidence("acme", excerpt="real content", source_type="")

    def test_unknown_account_raises(self):
        with self.assertRaises(FileNotFoundError):
            ae.add_evidence("does-not-exist", excerpt="x", source_type="note")

    def test_evidence_ids_increment(self):
        ae.add_evidence("acme", excerpt="first", source_type="note")
        ae.add_evidence("acme", excerpt="second", source_type="note")
        evidence = bs_common.load_jsonl(bs_common.account_dir("acme") / "evidence.jsonl")
        ids = [e["evidence_id"] for e in evidence]
        self.assertEqual(len(ids), len(set(ids)))

    def test_updates_registry_last_evidence_date(self):
        ae.add_evidence("acme", excerpt="x", source_type="note", event_date="2026-08-28")
        reg = bs_common.load_registry()
        entry = next(e for e in reg["registry"] if e["account_id"] == "acct-acme")
        self.assertEqual(entry["last_evidence_date"], "2026-08-28")

    def test_defaults_are_never_fabricated_facts(self):
        """Unspecified fields default to empty/None, never a guessed value."""
        result = ae.add_evidence("acme", excerpt="minimal record", source_type="note")
        evidence = bs_common.load_jsonl(bs_common.account_dir("acme") / "evidence.jsonl")
        record = next(e for e in evidence if e["evidence_id"] == result["evidence_id"])
        self.assertEqual(record["extracted_claims"], [])
        self.assertEqual(record["participants"], [])
        self.assertIsNone(record["source_author"])
        self.assertIsNone(record["durable_source_id"])


if __name__ == "__main__":
    unittest.main()
