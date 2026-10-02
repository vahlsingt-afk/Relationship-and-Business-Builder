"""
test_account_reference_detector.py — RB-2026-08-28.

Real gap: a real internal Pollo Campero pricing-call transcript went
through uploadAndIngestFile's generic "intelligence" pipeline and produced
only two generic "vendor mentioned" watchlist blips -- nothing connected it
to the actual Pollo Campero Blue Sheet, the real queryable home for that
content (an active RFP, due 2026-09-04). This module closes that gap
mechanically: name/alias matching against known Blue Sheet accounts and
tracked competitors, never free-text fact extraction (that's the same
fabrication class already confirmed and fixed elsewhere this session --
intelligence_mutation_engine.py's pronoun/brand-name bugs).

Tests run against an isolated tmp-dir "root" for both the Blue Sheet and
Competitor Intelligence engines -- same isolation pattern as
test_competitor_intelligence.py's _IsolatedRootMixin -- so nothing here
touches real production data.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import account_reference_detector as ard  # noqa: E402
import customers_prospects_common as cpc  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402


class _IsolatedRootMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)

        self._orig_cpc_root = cpc.ROOT
        self._orig_cic_root = cic.ROOT
        self._orig_ard_cpc_root = ard.CUSTOMERS_PROSPECTS_ROOT
        self._orig_ard_cic_root = ard.COMPETITOR_INTEL_ROOT

        cpc_root = tmp_root / "customers_prospects"
        cic_root = tmp_root / "competitor_intelligence"
        cpc.ROOT = cpc_root
        cic.ROOT = cic_root
        ard.CUSTOMERS_PROSPECTS_ROOT = cpc_root
        ard.COMPETITOR_INTEL_ROOT = cic_root

        (cpc_root / "_portfolio").mkdir(parents=True)
        (cpc_root / "accounts" / "pollo-campero").mkdir(parents=True)
        (cic_root / "_portfolio").mkdir(parents=True)

        cp_registry = {
            "registry": [
                {
                    "account_id": "acct-pollo-campero",
                    "aliases": ["Campero", "Campero USA"],
                    "status": "current",
                    "engagement_tier": "active_engagement",
                    "last_evidence_date": "2026-08-21",
                },
                {
                    "account_id": "acct-five-guys",
                    "aliases": [],
                    "status": "missing_blue_sheet",
                    "engagement_tier": "active_engagement",
                    "last_evidence_date": None,
                },
            ]
        }
        (cpc_root / "_portfolio" / "customers_prospects_registry.json").write_text(json.dumps(cp_registry), encoding="utf-8")
        (cpc_root / "accounts" / "pollo-campero" / "account.json").write_text(
            json.dumps({"display_name": "Pollo Campero"}), encoding="utf-8"
        )
        (cpc_root / "accounts" / "pollo-campero" / "evidence.jsonl").write_text("", encoding="utf-8")

        cic.create_competitor_shell("toast", "Toast", "vendor-toast")
        cic.register_competitor("toast", "Toast")

    def tearDown(self):
        cpc.ROOT = self._orig_cpc_root
        cic.ROOT = self._orig_cic_root
        ard.CUSTOMERS_PROSPECTS_ROOT = self._orig_ard_cpc_root
        ard.COMPETITOR_INTEL_ROOT = self._orig_ard_cic_root
        self._tmpdir.cleanup()


class TestDetectReferences(_IsolatedRootMixin, unittest.TestCase):
    def test_matches_blue_sheet_by_display_name(self):
        matches = ard.detect_references("Pricing call about Pollo Campero DMB installation.")
        types = {(m.artifact_type, m.slug) for m in matches}
        self.assertIn(("blue_sheet", "pollo-campero"), types)

    def test_matches_blue_sheet_by_alias(self):
        matches = ard.detect_references("Campero USA confirmed the rollout timeline.")
        types = {(m.artifact_type, m.slug) for m in matches}
        self.assertIn(("blue_sheet", "pollo-campero"), types)

    def test_matches_competitor_by_display_name(self):
        matches = ard.detect_references("They compared pricing against Toast directly.")
        types = {(m.artifact_type, m.slug) for m in matches}
        self.assertIn(("competitor", "toast"), types)

    def test_skips_missing_blue_sheet_status(self):
        """Five Guys has no real Blue Sheet/evidence.jsonl to append to --
        status != active/current must not be detected as a linkable match."""
        matches = ard.detect_references("Talked about Five Guys today.")
        slugs = {m.slug for m in matches}
        self.assertNotIn("five-guys", slugs)

    def test_no_match_on_unrelated_text(self):
        matches = ard.detect_references("Just a note about the weather and lunch plans.")
        self.assertEqual(matches, [])

    def test_short_alias_does_not_cause_noise_match(self):
        """Guards against a 1-2 char alias matching almost any text."""
        self.assertFalse(ard._name_in_text("Co", "company text here"))

    def test_short_alias_does_not_substring_match_inside_a_longer_word(self):
        """RB-2026-09-08 regression: a real bug misfiled an uploaded business
        calendar into PAR Technology's competitor evidence because "PAR" (a
        real alias) substring-matched inside "Part" ("Pollo Campero Pricing
        Part 2"). Same word-boundary bug class as
        intelligence_mutation_engine.py's 2026-08-25 fix."""
        self.assertFalse(ard._name_in_text("PAR", "pollo campero pricing part 2"))
        self.assertFalse(ard._name_in_text("PAR", "department, apartment, comparison"))

    def test_short_alias_still_matches_as_a_real_standalone_word(self):
        """The word-boundary fix must not overcorrect into false negatives --
        a short alias mentioned as its own word is still a real match."""
        self.assertTrue(ard._name_in_text("PAR", "genius vs par comparison call notes"))
        self.assertTrue(ard._name_in_text("PAR", "par announced a new release."))

    def test_empty_text_returns_no_matches(self):
        self.assertEqual(ard.detect_references(""), [])
        self.assertEqual(ard.detect_references(None), [])


class TestLinkReferences(_IsolatedRootMixin, unittest.TestCase):
    def test_links_blue_sheet_evidence_without_inventing_claims(self):
        matches = ard.detect_references("Pollo Campero pricing discussion.")
        result = ard.link_references(matches, source_title="test-doc-1", source_date="2026-08-28", excerpt="raw excerpt")
        self.assertEqual(result["linked"][0]["artifact_type"], "blue_sheet")

        evidence = cpc.load_jsonl(cpc.account_dir("pollo-campero") / "evidence.jsonl")
        self.assertEqual(len(evidence), 1)
        record = evidence[0]
        self.assertEqual(record["source_type"], "uploaded_content_reference")
        self.assertEqual(record["extracted_claims"], [])  # never invents specific facts
        self.assertIn("no specific fact extracted", record["limitations"])

    def test_links_competitor_evidence(self):
        matches = ard.detect_references("Toast came up as a comparison point.")
        ard.link_references(matches, source_title="test-doc-2", source_date="2026-08-28")
        data = cic.load_competitor("toast")
        self.assertEqual(len(data["evidence"]), 1)
        self.assertEqual(data["evidence"][0]["category"], "other")

    def test_idempotent_on_same_source_title(self):
        matches = ard.detect_references("Pollo Campero pricing discussion with Toast comparison.")
        r1 = ard.link_references(matches, source_title="test-doc-3", source_date="2026-08-28")
        r2 = ard.link_references(matches, source_title="test-doc-3", source_date="2026-08-28")
        self.assertEqual(len(r1["linked"]), 2)
        self.assertEqual(len(r2["linked"]), 0)  # already linked, no duplicate

    def test_different_source_title_creates_new_entry(self):
        matches = ard.detect_references("Pollo Campero update.")
        ard.link_references(matches, source_title="doc-a", source_date="2026-08-28")
        ard.link_references(matches, source_title="doc-b", source_date="2026-08-28")
        evidence = cpc.load_jsonl(cpc.account_dir("pollo-campero") / "evidence.jsonl")
        self.assertEqual(len(evidence), 2)

    def test_updates_registry_last_evidence_date(self):
        matches = ard.detect_references("Pollo Campero note.")
        ard.link_references(matches, source_title="doc-c", source_date="2026-08-28")
        reg = cpc.load_registry()
        entry = next(e for e in reg["registry"] if e["account_id"] == "acct-pollo-campero")
        self.assertEqual(entry["last_evidence_date"], "2026-08-28")

    def test_no_matches_links_nothing(self):
        result = ard.link_references([], source_title="doc-d", source_date="2026-08-28")
        self.assertEqual(result["linked"], [])
        self.assertEqual(result["match_count"], 0)

    def test_document_id_becomes_durable_source_id(self):
        """RB-2026-08-31: previously hardcoded to None -- an uploaded
        document's evidence pointer must be a real, callable retrieval id
        (getUploadedDocument) whenever the caller (uploadAndIngestFile) has
        one, not just a 400-char excerpt with no way back to the source."""
        matches = ard.detect_references("Pollo Campero RFP response received.")
        ard.link_references(
            matches, source_title="doc-e", source_date="2026-08-28",
            excerpt="raw excerpt", document_id="abc123def456",
        )
        evidence = cpc.load_jsonl(cpc.account_dir("pollo-campero") / "evidence.jsonl")
        self.assertEqual(evidence[0]["durable_source_id"], "abc123def456")

    def test_document_id_defaults_to_none(self):
        matches = ard.detect_references("Pollo Campero note without a document id.")
        ard.link_references(matches, source_title="doc-f", source_date="2026-08-28")
        evidence = cpc.load_jsonl(cpc.account_dir("pollo-campero") / "evidence.jsonl")
        self.assertIsNone(evidence[0]["durable_source_id"])

    def test_document_id_recorded_on_competitor_evidence(self):
        matches = ard.detect_references("Toast came up in the RFP comparison.")
        ard.link_references(
            matches, source_title="doc-g", source_date="2026-08-28", document_id="xyz789",
        )
        data = cic.load_competitor("toast")
        self.assertEqual(data["evidence"][0]["document_id"], "xyz789")


if __name__ == "__main__":
    unittest.main()
