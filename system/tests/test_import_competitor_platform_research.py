#!/usr/bin/env python3
"""
test_import_competitor_platform_research.py — RB-DEFECT-073 (2026-09-28).

Coverage for import_competitor_platform_research.py against small,
hand-built "rb.competitor_platform_research.v1" fixtures. Isolated against
disposable competitor_intelligence/genius_capabilities/import-log/inbox
paths -- never touches real production data. mutation_policy's receipts
path is already redirected globally by conftest.py for the whole test
run, so assertions here filter receipts by entity_id rather than assuming
an empty file.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import import_competitor_platform_research as icpr  # noqa: E402
import competitor_intelligence as compintel  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import genius_capabilities as gc  # noqa: E402
import mutation_policy  # noqa: E402


def _finding(**overrides) -> dict:
    base = {
        "target": "competitor:toast",
        "field": "strengths",
        "value": "Record net new location adds in a published earnings call",
        "finding_type": "independently_verified",
        "source_url": "https://example.com/toast-earnings",
        "source_owner": "Example Analyst Desk",
        "source_type": "analyst report",
        "confidence": "high",
        "observed_at": "2026-09-27",
    }
    base.update(overrides)
    return base


def _sidecar(findings: list[dict], *, packet_id: str = "dr-20260928-0900-competitor-platforms") -> dict:
    return {
        "packet_id": packet_id,
        "targets": sorted({f["target"] for f in findings}),
        "pages_reviewed": 1,
        "candidate_pages_validated": 1,
        "conflicts_found": 0,
        "source_ledger": [{"url": "https://example.com/toast-earnings", "source_type": "analyst report", "productive": True}],
        "schema": "rb.competitor_platform_research.v1",
        "findings": findings,
    }


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)

        self._orig_cic_root = cic.ROOT
        self._orig_gc_caps = gc.GENIUS_CAPABILITIES_PATH
        self._orig_gc_evidence = gc.GENIUS_EVIDENCE_PATH
        self._orig_import_log = icpr.IMPORT_LOG_PATH
        self._orig_drop_dir = icpr.DROP_DIR
        self._orig_manifest = icpr.SIDECAR_MANIFEST_PATH
        self._orig_receipts_path = mutation_policy.RECEIPTS_PATH
        mutation_policy.RECEIPTS_PATH = tmp / "mutation_policy_receipts.jsonl"

        cic.ROOT = tmp / "competitor_intelligence"
        (cic.ROOT / "_portfolio").mkdir(parents=True)
        (cic.ROOT / "_portfolio" / "competitor_registry.json").write_text(
            json.dumps({"registry": [{"competitor_slug": "toast", "display_name": "Toast"}]}), encoding="utf-8",
        )
        comp_dir = cic.ROOT / "competitors" / "toast"
        comp_dir.mkdir(parents=True)
        (comp_dir / "competitor.json").write_text(
            json.dumps(cic._empty_competitor_json("toast", "Toast", None)), encoding="utf-8",
        )
        (comp_dir / "evidence.jsonl").write_text("", encoding="utf-8")

        gc.GENIUS_CAPABILITIES_PATH = tmp / "genius_capabilities.json"
        gc.GENIUS_EVIDENCE_PATH = tmp / "genius_own_evidence.jsonl"
        icpr.IMPORT_LOG_PATH = tmp / "import_log.json"
        icpr.DROP_DIR = tmp / "chatgpt_intelligence_drop"
        icpr.DROP_DIR.mkdir(parents=True)
        icpr.SIDECAR_MANIFEST_PATH = tmp / "sidecar_manifest.json"

    def tearDown(self):
        cic.ROOT = self._orig_cic_root
        gc.GENIUS_CAPABILITIES_PATH = self._orig_gc_caps
        gc.GENIUS_EVIDENCE_PATH = self._orig_gc_evidence
        icpr.IMPORT_LOG_PATH = self._orig_import_log
        icpr.DROP_DIR = self._orig_drop_dir
        icpr.SIDECAR_MANIFEST_PATH = self._orig_manifest
        mutation_policy.RECEIPTS_PATH = self._orig_receipts_path
        self._tmpdir.cleanup()

    def _toast(self) -> dict:
        return cic.load_competitor("toast")["competitor"]

    def _receipts_for(self, entity_id: str) -> list[dict]:
        return [r for r in mutation_policy.load_receipts() if r.get("entity_id") == entity_id]


class TestHunterEnvelope(_IsolatedFixtureMixin):
    def test_import_reads_platform_findings_from_hunter_payload(self):
        legacy_payload = _sidecar([_finding()])
        packet = {
            "schema": "rb.hunter_research_packet.v1",
            "packet_id": "hunter-20261002-toast",
            "payload_schema": "rb.competitor_platform_research.v1",
            "payload": legacy_payload,
        }

        result = icpr.import_findings(packet, dry_run=True)

        self.assertEqual(result["findings_received"], 1)
        self.assertEqual(result["findings_malformed"], 0)

    def test_sweep_rejects_invalid_hunter_packet_before_import(self):
        packet = {
            "schema": "rb.hunter_research_packet.v1",
            "packet_id": "hunter-invalid",
            "payload_schema": "rb.competitor_platform_research.v1",
            "payload": {"findings": [_finding()]},
        }
        (icpr.DROP_DIR / "invalid.json").write_text(json.dumps(packet), encoding="utf-8")

        result = icpr.sweep(dry_run=True)

        self.assertEqual(len(result["packets"]), 1)
        self.assertFalse(result["packets"][0]["ok"])
        self.assertEqual(result["packets"][0]["quality_gate"], "rejected")
        self.assertTrue(result["packets"][0]["quality_errors"])


class TestSchemaValidation(_IsolatedFixtureMixin):
    def test_legacy_platform_baseline_is_recognized_and_translated(self):
        packet = {
            "packet_id": "baseline-1",
            "artifact_type": "rbb_marketplace_intelligence_import",
            "research_access_date": "2026-09-28",
            "competitors": [{
                "key": "competitor:toast",
                "vendor": "Toast",
                "positioning": "Integrated restaurant platform.",
                "verified_product_families": [{"name": "Toast POS", "category": "POS"}],
                "strengths": [{
                    "observation": "Broad integrated footprint.",
                    "evidence_type": "verified portfolio observation",
                    "confidence": 95,
                }],
                "weaknesses_and_risks": [{
                    "observation": "Independent implementation evidence remains thin.",
                    "evidence_type": "negative finding/research gap",
                    "confidence": 90,
                }],
                "research_gaps": ["Support quality"],
            }],
        }
        encoded = json.dumps(packet).encode()
        self.assertTrue(icpr.classify_bytes(encoded))
        result = icpr.import_findings(packet, dry_run=False)
        self.assertEqual(result["findings_malformed"], 0)
        self.assertEqual(result["identity_unresolved"], 0)
        self.assertEqual(result["applied"], 5)
        profile = cic.get_extended_profile(self._toast())
        self.assertEqual(len(profile["products"]), 1)
        self.assertEqual(len(profile["vendor_claims"]), 2)
        self.assertEqual(len(profile["vulnerabilities"]), 1)

    def test_missing_required_key_is_rejected_without_blocking_others(self):
        good = _finding()
        bad = _finding(value="Orphan claim with no source", source_url="")
        result = icpr.import_findings(_sidecar([bad, good]), dry_run=False)
        self.assertEqual(result["findings_malformed"], 1)
        self.assertEqual(result["applied"], 1)
        self.assertIn("missing required key", result["malformed_errors"][0]["error"])

    def test_unknown_field_is_rejected(self):
        result = icpr.import_findings(_sidecar([_finding(field="not_a_real_field")]), dry_run=False)
        self.assertEqual(result["findings_malformed"], 1)

    def test_unknown_confidence_is_rejected(self):
        result = icpr.import_findings(_sidecar([_finding(confidence="super duper high")]), dry_run=False)
        self.assertEqual(result["findings_malformed"], 1)


class TestVendorClaimNeverPromoted(_IsolatedFixtureMixin):
    def test_vendor_stated_finding_lands_in_vendor_claims_not_strengths(self):
        finding = _finding(
            field="vendor_claims", finding_type="vendor_stated",
            value="Toast says its platform is the #1 restaurant POS",
        )
        icpr.import_findings(_sidecar([finding]), dry_run=False)
        comp = self._toast()
        self.assertEqual([e["value"] for e in comp["vendor_claims"]], [finding["value"]])
        self.assertEqual(comp["strengths"], [])

    def test_vendor_claims_field_rejects_non_vendor_stated_finding_type(self):
        finding = _finding(field="vendor_claims", finding_type="independently_verified")
        result = icpr.import_findings(_sidecar([finding]), dry_run=False)
        self.assertEqual(result["findings_malformed"], 1)

    def test_strengths_field_rejects_vendor_stated_finding_type(self):
        finding = _finding(field="strengths", finding_type="vendor_stated")
        result = icpr.import_findings(_sidecar([finding]), dry_run=False)
        self.assertEqual(result["findings_malformed"], 1)


class TestSourcedMarketplaceShortcomings(_IsolatedFixtureMixin):
    def test_sourced_weakness_populates_competitor_weakness(self):
        finding = _finding(
            field="weaknesses", finding_type="marketplace_reported",
            value="Hardware cost pressure flagged for 2027",
            deployment_scope="enterprise",
        )
        icpr.import_findings(_sidecar([finding]), dry_run=False)
        comp = self._toast()
        self.assertEqual(len(comp["weaknesses"]), 1)
        self.assertEqual(comp["weaknesses"][0]["value"], finding["value"])
        self.assertEqual(comp["weaknesses"][0]["deployment_scope"], "enterprise")
        self.assertEqual(comp["weaknesses"][0]["source_url"], finding["source_url"])

    def test_sourced_vulnerability_populates_with_scope(self):
        finding = _finding(
            field="vulnerabilities", finding_type="marketplace_reported",
            value="Thin third-party analyst coverage of uptime SLAs",
            deployment_scope="franchisee",
        )
        icpr.import_findings(_sidecar([finding]), dry_run=False)
        comp = self._toast()
        self.assertEqual(comp["vulnerabilities"][0]["deployment_scope"], "franchisee")


class TestNetNewAutoApply(_IsolatedFixtureMixin):
    def test_net_new_finding_auto_applies_with_receipt(self):
        finding = _finding()
        result = icpr.import_findings(_sidecar([finding]), dry_run=False)
        self.assertEqual(result["applied"], 1)
        receipts = self._receipts_for("toast")
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["decision_class"], mutation_policy.AUTO_ADDED_NET_NEW)
        self.assertTrue(receipts[0]["applied"])

    def test_dry_run_writes_nothing(self):
        icpr.import_findings(_sidecar([_finding()]), dry_run=True)
        comp = self._toast()
        self.assertEqual(comp["strengths"], [])
        self.assertFalse(icpr.IMPORT_LOG_PATH.exists())


class TestConflictRoutesToReview(_IsolatedFixtureMixin):
    def test_conflicting_undated_trends_requires_confirmation_no_overwrite(self):
        compintel.add_extended_profile_finding("toast", "trends", "Expanding into Europe", as_of="2026-09-01")
        finding = _finding(field="trends", value="Pulling back from Europe", observed_at="2026-09-01")
        result = icpr.import_findings(_sidecar([finding]), dry_run=False)
        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["queued_for_review"], 1)
        comp = self._toast()
        self.assertEqual(comp["trends"]["value"], "Expanding into Europe")

    def test_newer_dated_trends_auto_applies_as_successor(self):
        compintel.add_extended_profile_finding("toast", "trends", "Expanding into Europe", as_of="2026-09-01")
        finding = _finding(field="trends", value="Pulling back from Europe", observed_at="2026-09-20")
        result = icpr.import_findings(_sidecar([finding]), dry_run=False)
        self.assertEqual(result["applied"], 1)
        comp = self._toast()
        self.assertEqual(comp["trends"]["value"], "Pulling back from Europe")

    def test_conflicting_undated_value_statement_requires_confirmation_no_overwrite(self):
        """2026-10-02: value_statement must go through the exact same
        generalized scalar dispatch as trends (_apply_competitor_scalar_
        finding) -- this is the regression check that the trends-only
        hardcoding was fully generalized, not just extended for trends
        itself."""
        compintel.add_extended_profile_finding("toast", "value_statement", "The restaurant platform built to grow with you", as_of="2026-09-01")
        finding = _finding(field="value_statement", value="Just works", observed_at="2026-09-01")
        result = icpr.import_findings(_sidecar([finding]), dry_run=False)
        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["queued_for_review"], 1)
        comp = self._toast()
        self.assertEqual(comp["value_statement"]["value"], "The restaurant platform built to grow with you")

    def test_newer_dated_value_statement_auto_applies_as_successor(self):
        compintel.add_extended_profile_finding("toast", "value_statement", "The restaurant platform built to grow with you", as_of="2026-09-01")
        finding = _finding(field="value_statement", value="Just works", observed_at="2026-09-20")
        result = icpr.import_findings(_sidecar([finding]), dry_run=False)
        self.assertEqual(result["applied"], 1)
        comp = self._toast()
        self.assertEqual(comp["value_statement"]["value"], "Just works")


class TestNewListFields(_IsolatedFixtureMixin):
    """2026-10-02: features and customer_feedback_testimonials, the two
    new list-shaped EXTENDED_PROFILE_FIELDS for the top-10-per-category
    competitor Hunter cycle, routed through the pre-existing generic
    _apply_competitor_list_finding path."""

    def test_features_finding_applies_as_net_new(self):
        finding = _finding(field="features", value="Real-time kitchen display routing", finding_type="vendor_stated")
        result = icpr.import_findings(_sidecar([finding]), dry_run=False)
        self.assertEqual(result["applied"], 1)
        comp = self._toast()
        self.assertEqual(comp["features"][0]["value"], "Real-time kitchen display routing")

    def test_customer_feedback_testimonial_finding_applies_as_net_new(self):
        finding = _finding(field="customer_feedback_testimonials", value="G2 reviewer: cut ticket times by 20%")
        result = icpr.import_findings(_sidecar([finding]), dry_run=False)
        self.assertEqual(result["applied"], 1)
        comp = self._toast()
        self.assertEqual(comp["customer_feedback_testimonials"][0]["value"], "G2 reviewer: cut ticket times by 20%")


class TestIdempotency(_IsolatedFixtureMixin):
    def test_sweep_is_idempotent_across_reruns(self):
        sidecar = _sidecar([_finding()])
        (icpr.DROP_DIR / "2026-09-28_0900_toast_deep-research.json").write_text(json.dumps(sidecar), encoding="utf-8")
        first = icpr.sweep(dry_run=False)
        second = icpr.sweep(dry_run=False)
        self.assertEqual(first["packets"][0]["applied"], 1)
        # Second sweep sees the same file hash already in the manifest, so
        # it isn't even re-opened.
        self.assertEqual(second["packets"], [])
        comp = self._toast()
        self.assertEqual(len(comp["strengths"]), 1)

    def test_reimporting_same_finding_by_file_is_not_reapplied(self):
        finding = _finding()
        icpr.import_findings(_sidecar([finding]), dry_run=False)
        result = icpr.import_findings(_sidecar([finding]), dry_run=False)
        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["findings_already_processed"], 1)
        comp = self._toast()
        self.assertEqual(len(comp["strengths"]), 1)


class TestGeniusAndParentRouting(_IsolatedFixtureMixin):
    def test_global_payments_target_routes_to_genius_parent_evidence_not_a_competitor(self):
        finding = _finding(
            target="competitor:global-payments", field="vendor_claims", finding_type="vendor_stated",
            value="HQ Atlanta, GA; NYSE: GPN",
        )
        icpr.import_findings(_sidecar([finding]), dry_run=False)
        evidence = gc.list_evidence("parent")
        self.assertEqual(len(evidence), 1)
        with self.assertRaises(FileNotFoundError):
            cic.competitor_dir("global-payments")

    def test_genius_parent_target_routes_to_parent_evidence(self):
        finding = _finding(
            target="genius:parent", field="vendor_claims", finding_type="vendor_stated",
            value="Global Payments reported Q2 2026 bookings growth",
        )
        icpr.import_findings(_sidecar([finding]), dry_run=False)
        evidence = gc.list_evidence("parent")
        self.assertEqual(len(evidence), 1)

    def test_genius_capability_and_weakness_route_to_distinct_stores(self):
        capability = _finding(
            target="genius:pos", field="strengths", finding_type="independently_verified",
            value="Real-time inventory sync across 40k locations", confidence="critical",
        )
        weakness = _finding(
            target="genius:pos", field="weaknesses", finding_type="marketplace_reported",
            value="Onboarding time flagged by a third-party review", confidence="medium",
        )
        icpr.import_findings(_sidecar([capability, weakness]), dry_run=False)
        caps = gc.list_capabilities("pos")
        evidence = gc.list_evidence("pos")
        self.assertEqual([c["point"] for c in caps], [capability["value"]])
        self.assertEqual([e["summary"] for e in evidence], [weakness["value"]])

    def test_inference_flagged_genius_strength_does_not_become_a_capability(self):
        finding = _finding(
            target="genius:pos", field="strengths", finding_type="independently_verified",
            value="Inferred advantage, not a direct claim", confidence="high", is_inference=True,
        )
        icpr.import_findings(_sidecar([finding]), dry_run=False)
        self.assertEqual(gc.list_capabilities("pos"), [])
        self.assertEqual(len(gc.list_evidence("pos")), 1)


class TestUnresolvableIdentity(_IsolatedFixtureMixin):
    def test_unknown_competitor_slug_is_never_auto_created(self):
        finding = _finding(target="competitor:some-brand-new-vendor-nobody-tracks")
        result = icpr.import_findings(_sidecar([finding]), dry_run=False)
        self.assertEqual(result["identity_unresolved"], 1)
        with self.assertRaises(FileNotFoundError):
            cic.competitor_dir("some-brand-new-vendor-nobody-tracks")
        receipts = [r for r in mutation_policy.load_receipts() if r.get("entity_id") == "competitor:some-brand-new-vendor-nobody-tracks"]
        self.assertEqual(receipts[0]["decision_class"], mutation_policy.REVIEW_REQUIRED_IDENTITY_AMBIGUITY)


class TestMigrateGlobalPayments(_IsolatedFixtureMixin):
    def setUp(self):
        super().setUp()
        gp_dir = cic.ROOT / "competitors" / "global-payments"
        gp_dir.mkdir(parents=True)
        (gp_dir / "competitor.json").write_text(
            json.dumps(cic._empty_competitor_json("global-payments", "Global Payments", None)), encoding="utf-8",
        )
        (gp_dir / "evidence.jsonl").write_text(
            json.dumps({"evidence_id": "note-0001", "summary": "HQ Atlanta, GA", "category": "other",
                        "source": "legacy", "confidence": "high"}) + "\n",
            encoding="utf-8",
        )
        reg = cic.load_registry()
        reg["registry"].append({"competitor_slug": "global-payments", "display_name": "Global Payments"})
        cic.save_registry(reg)

    def test_dry_run_leaves_everything_in_place(self):
        result = icpr.migrate_global_payments_to_parent_evidence(dry_run=True)
        self.assertTrue(result["existed"])
        self.assertTrue(cic.competitor_dir("global-payments"))

    def test_confirm_moves_evidence_and_removes_competitor(self):
        result = icpr.migrate_global_payments_to_parent_evidence(dry_run=False)
        self.assertEqual(result["evidence_migrated"], 1)
        self.assertTrue(result["removed_from_registry"])
        evidence = gc.list_evidence("parent")
        self.assertEqual(len(evidence), 1)
        self.assertIn("HQ Atlanta, GA", evidence[0]["summary"])
        with self.assertRaises(FileNotFoundError):
            cic.competitor_dir("global-payments")
        reg = cic.load_registry()
        self.assertNotIn("global-payments", [e["competitor_slug"] for e in reg["registry"]])

    def test_own_company_can_never_be_recreated_after_migration(self):
        icpr.migrate_global_payments_to_parent_evidence(dry_run=False)
        with self.assertRaises(ValueError):
            compintel.ensure_competitor("Global Payments Inc.")


if __name__ == "__main__":
    unittest.main()
