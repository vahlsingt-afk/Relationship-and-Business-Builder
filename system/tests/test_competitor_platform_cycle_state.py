#!/usr/bin/env python3
"""
test_competitor_platform_cycle_state.py — RB-DEFECT-073 (2026-09-28).

Coverage for the cycle-state status gating fix: a target must reach a real
import outcome (and, for STATUS_COMPLETE, a canonical readback) before it
can be marked complete -- confirmed live before this fix that
toast/par-technology/nory/qu/global-payments were all "complete" with zero
populated canonical fields.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import competitor_platform_cycle_state as cps  # noqa: E402
import competitor_intelligence as compintel  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import genius_capabilities as gc  # noqa: E402
import import_competitor_platform_research as icpr  # noqa: E402


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)

        self._orig_cps_root = cps.ROOT
        self._orig_cic_root = cic.ROOT
        self._orig_gc_caps = gc.GENIUS_CAPABILITIES_PATH
        self._orig_gc_evidence = gc.GENIUS_EVIDENCE_PATH
        self._orig_import_log = icpr.IMPORT_LOG_PATH

        cps.ROOT = tmp
        cic.ROOT = tmp / "system" / "competitor_intelligence"
        (cic.ROOT / "_portfolio").mkdir(parents=True)
        (cic.ROOT / "_portfolio" / "competitor_registry.json").write_text(
            json.dumps({"registry": []}), encoding="utf-8",
        )
        gc.GENIUS_CAPABILITIES_PATH = tmp / "genius_capabilities.json"
        gc.GENIUS_EVIDENCE_PATH = tmp / "genius_own_evidence.jsonl"
        icpr.IMPORT_LOG_PATH = tmp / "import_log.json"

        for slug, name in (("par-technology", "PAR Technology"), ("toast", "Toast"),
                            ("nory", "Nory"), ("qu", "Qu")):
            cic.create_competitor_shell(slug, name, None)
            cic.register_competitor(slug, name)

    def tearDown(self):
        cps.ROOT = self._orig_cps_root
        cic.ROOT = self._orig_cic_root
        gc.GENIUS_CAPABILITIES_PATH = self._orig_gc_caps
        gc.GENIUS_EVIDENCE_PATH = self._orig_gc_evidence
        icpr.IMPORT_LOG_PATH = self._orig_import_log
        self._tmpdir.cleanup()

    def _state_path(self) -> Path:
        return Path(self._tmpdir.name) / "state.json"

    def _write_state(self):
        state = {
            "cycle_id": "test-cycle",
            "brief": "test",
            "created_at": cps.now_utc(),
            "updated_at": None,
            "live_roster_snapshot": {},
            "competitors": [
                {"slug": slug, "display_name": name, "relationship_class": "product_line_competitor",
                 "status": cps.STATUS_PENDING_RESEARCH, "qualifying_packet_path": None,
                 "products_platforms_covered": [], "completed_at": None, "blocker": None}
                for slug, name in (("par-technology", "PAR Technology"), ("toast", "Toast"),
                                    ("nory", "Nory"), ("qu", "Qu"))
            ],
        }
        self._state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")


def _finding(target: str, **overrides) -> dict:
    base = {
        "target": target, "field": "strengths", "value": f"A sourced claim about {target}",
        "finding_type": "independently_verified", "source_url": "https://example.com/a",
        "source_owner": "Example Publisher", "source_type": "analyst report",
        "confidence": "high", "observed_at": "2026-09-28",
    }
    base.update(overrides)
    return base


def _export_text(findings: list[dict]) -> str:
    targets = sorted(cps.EXPECTED_BATCH_TARGETS)
    matrix_rows = "\n".join(f"| `{t}` | **Platform for {t}** |" for t in targets)
    target_mentions = "\n".join(f"- {t}" for t in targets)
    ledger_url = "https://example.com/a"
    sidecar = {
        "packet_id": "dr-20260928-0900-competitor-platforms",
        "targets": targets,
        "pages_reviewed": 1,
        "candidate_pages_validated": 1,
        "conflicts_found": 0,
        "source_ledger": [{"url": ledger_url, "source_type": "analyst report", "productive": True}],
        "schema": "rb.competitor_platform_research.v1",
        "findings": findings,
    }
    markdown = f"""# Competitor platform research

## Per-company and per-platform matrix

{matrix_rows}

Targets covered:
{target_mentions}

## Detailed evidence observations

Vendor-stated positioning noted. Marketplace strength observed.
Marketplace shortcomings noted too.

## Company/competitor profile observations

See findings JSON.

## Negative findings

None.

## Source ledger

{ledger_url}

## Research notes and inferences

None.
"""
    return f"```markdown\n{markdown}\n```\n```json\n{json.dumps(sidecar, indent=2)}\n```\n"


class TestCanonicalReadback(_IsolatedFixtureMixin):
    def test_empty_competitor_is_not_populated(self):
        self.assertFalse(cps._canonical_readback_populated("toast"))

    def test_competitor_with_a_real_finding_is_populated(self):
        compintel.add_extended_profile_finding("toast", "strengths", "Real, sourced strength")
        self.assertTrue(cps._canonical_readback_populated("toast"))

    def test_unknown_slug_is_not_populated(self):
        self.assertFalse(cps._canonical_readback_populated("does-not-exist"))


class TestDeriveLegacyStatus(_IsolatedFixtureMixin):
    def test_populated_canonical_data_derives_complete(self):
        compintel.add_extended_profile_finding("toast", "strengths", "Real, sourced strength")
        status = cps._derive_status_for_legacy_complete({"slug": "toast", "qualifying_packet_path": "some/packet.md"})
        self.assertEqual(status, cps.STATUS_COMPLETE)

    def test_packet_without_canonical_data_derives_partial_review_required(self):
        status = cps._derive_status_for_legacy_complete({"slug": "toast", "qualifying_packet_path": "some/packet.md"})
        self.assertEqual(status, cps.STATUS_IMPORT_PARTIAL_REVIEW_REQUIRED)

    def test_no_packet_and_no_data_derives_pending_research(self):
        status = cps._derive_status_for_legacy_complete({"slug": "toast", "qualifying_packet_path": None})
        self.assertEqual(status, cps.STATUS_PENDING_RESEARCH)


class TestIngestExportStatusGating(_IsolatedFixtureMixin):
    def test_target_with_applied_findings_and_readback_becomes_complete(self):
        self._write_state()
        findings = [_finding("competitor:toast")]
        export_path = Path(self._tmpdir.name) / "export.txt"
        export_path.write_text(_export_text(findings), encoding="utf-8")

        cps.ingest_export(self._state_path(), export_path)

        state = json.loads(self._state_path().read_text())
        by_slug = {c["slug"]: c for c in state["competitors"]}
        self.assertEqual(by_slug["toast"]["status"], cps.STATUS_COMPLETE)
        self.assertIsNone(by_slug["toast"]["blocker"])

    def test_target_with_no_findings_becomes_packet_validated_not_complete(self):
        self._write_state()
        findings = [_finding("competitor:toast")]  # only toast has findings
        export_path = Path(self._tmpdir.name) / "export.txt"
        export_path.write_text(_export_text(findings), encoding="utf-8")

        cps.ingest_export(self._state_path(), export_path)

        state = json.loads(self._state_path().read_text())
        by_slug = {c["slug"]: c for c in state["competitors"]}
        self.assertEqual(by_slug["qu"]["status"], cps.STATUS_PACKET_VALIDATED)
        self.assertNotEqual(by_slug["qu"]["status"], cps.STATUS_COMPLETE)

    def test_conflicting_finding_becomes_import_partial_review_required(self):
        self._write_state()
        compintel.add_extended_profile_finding("nory", "trends", "Existing trend", as_of="2026-09-01")
        findings = [
            _finding("competitor:toast"),
            _finding("competitor:nory", field="trends", value="Conflicting trend", observed_at="2026-09-01"),
        ]
        export_path = Path(self._tmpdir.name) / "export.txt"
        export_path.write_text(_export_text(findings), encoding="utf-8")

        cps.ingest_export(self._state_path(), export_path)

        state = json.loads(self._state_path().read_text())
        by_slug = {c["slug"]: c for c in state["competitors"]}
        self.assertEqual(by_slug["nory"]["status"], cps.STATUS_IMPORT_PARTIAL_REVIEW_REQUIRED)
        self.assertIsNotNone(by_slug["nory"]["blocker"])
        # The conflict must not have overwritten the existing canonical value.
        comp = cic.load_competitor("nory")["competitor"]
        self.assertEqual(comp["trends"]["value"], "Existing trend")

    def test_genius_parent_target_is_imported_but_not_tracked_in_competitor_state(self):
        self._write_state()
        findings = [
            _finding("competitor:toast"),
            _finding("genius:parent", field="vendor_claims", finding_type="vendor_stated",
                      value="Global Payments reported Q2 2026 bookings growth"),
        ]
        export_path = Path(self._tmpdir.name) / "export.txt"
        export_path.write_text(_export_text(findings), encoding="utf-8")

        cps.ingest_export(self._state_path(), export_path)

        state = json.loads(self._state_path().read_text())
        slugs = [c["slug"] for c in state["competitors"]]
        self.assertNotIn("genius:parent", slugs)
        self.assertNotIn("parent", slugs)
        evidence = gc.list_evidence("parent")
        self.assertEqual(len(evidence), 1)

    def test_cannot_reach_complete_without_a_real_import_disposition(self):
        """Direct regression for the confirmed live bug: structural
        Markdown/ledger validation alone must never be enough to mark a
        target complete."""
        self._write_state()
        export_path = Path(self._tmpdir.name) / "export.txt"
        export_path.write_text(_export_text([]), encoding="utf-8")  # no findings at all

        cps.ingest_export(self._state_path(), export_path)

        state = json.loads(self._state_path().read_text())
        statuses = {c["status"] for c in state["competitors"]}
        self.assertNotIn(cps.STATUS_COMPLETE, statuses)


class TestMergeReDerivesStaleComplete(_IsolatedFixtureMixin):
    """Regression for a bug caught while running this fix against real
    production data: "complete" is a valid status string under BOTH the
    old (Markdown-only) and new (canonical-readback) semantics, so a
    merge() that only re-derives status strings NOT in VALID_STATUSES
    silently keeps a stale, falsely-complete legacy entry forever -- it
    never falls into the "not in VALID_STATUSES" branch at all. merge()
    must always re-verify a stored "complete" against real canonical
    data."""

    def _patched_roster(self):
        return {
            "competitor_count": 1,
            "relationship_class_counts": {"product_line_competitor": 1},
            "competitors": [
                {"competitor_slug": "toast", "display_name": "Toast", "relationship_class": "product_line_competitor"},
            ],
        }

    def test_stale_complete_with_no_canonical_data_is_downgraded(self):
        state_path = self._state_path()
        state_path.write_text(json.dumps({
            "cycle_id": "test-cycle", "brief": "test", "created_at": cps.now_utc(), "updated_at": None,
            "live_roster_snapshot": {},
            "competitors": [{
                "slug": "toast", "display_name": "Toast", "relationship_class": "product_line_competitor",
                "status": cps.STATUS_COMPLETE, "qualifying_packet_path": "some/packet.md",
                "products_platforms_covered": ["Toast POS"], "completed_at": "2026-09-28T00:00:00Z", "blocker": None,
            }],
        }), encoding="utf-8")

        orig_live_roster = cps.live_roster
        cps.live_roster = self._patched_roster
        try:
            state = cps.merge(state_path)
        finally:
            cps.live_roster = orig_live_roster

        by_slug = {c["slug"]: c for c in state["competitors"]}
        self.assertEqual(by_slug["toast"]["status"], cps.STATUS_IMPORT_PARTIAL_REVIEW_REQUIRED)

    def test_complete_with_real_canonical_data_stays_complete(self):
        compintel.add_extended_profile_finding("toast", "strengths", "Real, sourced strength")
        state_path = self._state_path()
        state_path.write_text(json.dumps({
            "cycle_id": "test-cycle", "brief": "test", "created_at": cps.now_utc(), "updated_at": None,
            "live_roster_snapshot": {},
            "competitors": [{
                "slug": "toast", "display_name": "Toast", "relationship_class": "product_line_competitor",
                "status": cps.STATUS_COMPLETE, "qualifying_packet_path": "some/packet.md",
                "products_platforms_covered": ["Toast POS"], "completed_at": "2026-09-28T00:00:00Z", "blocker": None,
            }],
        }), encoding="utf-8")

        orig_live_roster = cps.live_roster
        cps.live_roster = self._patched_roster
        try:
            state = cps.merge(state_path)
        finally:
            cps.live_roster = orig_live_roster

        by_slug = {c["slug"]: c for c in state["competitors"]}
        self.assertEqual(by_slug["toast"]["status"], cps.STATUS_COMPLETE)


class TestStatusVocabulary(_IsolatedFixtureMixin):
    def test_valid_statuses_is_the_six_value_enum(self):
        self.assertEqual(cps.VALID_STATUSES, {
            "pending_research", "packet_validated", "import_applied",
            "import_partial_review_required", "blocked", "complete",
        })


if __name__ == "__main__":
    unittest.main()
