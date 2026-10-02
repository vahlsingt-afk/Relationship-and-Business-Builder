"""
test_reconcile_deep_research_capture_receipts.py — RB defect 2026-09-30.

Coverage for reconcile_deep_research_capture_receipts.py, the script that
closes the gap documented in system/CLAUDE_HANDOFF_RB_DEEP_RESEARCH_
MUTATION_RECEIPT_DEFECT_2026-09-30.md: a deep-research capture's own
receipt only ever counted executive_declaration mutations, so it reported
"exec_mutations: 0" even when import_competitor_platform_research.py
applied real canonical mutations from the same packet's structured
sidecar moments later. This script reconciles the two by packet_id.

Exercises the handoff's acceptance tests 1-5 directly against the
reconciler (6-8 are covered elsewhere: generic-noise suppression in
server.py, unchanged meeting-capture behavior, and brief/getCapturesProcessed
rendering in render_intelligence_brief.py / capture_ingest.list_processed).
Isolated against disposable PROCESSED_DIR / IMPORT_RECEIPTS_PATH /
RECEIPT_LOG_PATH -- never touches real captures or import receipts.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))

import capture_ingest as ci  # noqa: E402
import import_competitor_platform_research as icpr  # noqa: E402
import reconcile_deep_research_capture_receipts as reconciler  # noqa: E402


class _IsolatedFixtureMixin(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)

        self._orig_processed_dir = ci.PROCESSED_DIR
        self._orig_import_receipts_path = icpr.IMPORT_RECEIPTS_PATH
        self._orig_receipt_log_path = reconciler.RECEIPT_LOG_PATH

        ci.PROCESSED_DIR = tmp / "processed"
        ci.PROCESSED_DIR.mkdir(parents=True)
        icpr.IMPORT_RECEIPTS_PATH = tmp / "import_receipts.json"
        reconciler.RECEIPT_LOG_PATH = tmp / "reconciliation_latest.json"

        self.drop_dir = tmp / "chatgpt_intelligence_drop"
        self.drop_dir.mkdir(parents=True)

    def tearDown(self):
        ci.PROCESSED_DIR = self._orig_processed_dir
        icpr.IMPORT_RECEIPTS_PATH = self._orig_import_receipts_path
        reconciler.RECEIPT_LOG_PATH = self._orig_receipt_log_path
        self._tmpdir.cleanup()

    def _write_capture(self, file_id: str, *, packet_id: str, has_sidecar_findings: bool = True) -> None:
        md_path = self.drop_dir / f"{file_id}.md"
        md_path.write_text("# packet", encoding="utf-8")
        if has_sidecar_findings:
            sidecar_path = self.drop_dir / f"{file_id}.json"
            sidecar_path.write_text(json.dumps({
                "packet_id": packet_id,
                "findings": [{"target": "competitor:toast", "field": "strengths", "value": "x"}],
            }), encoding="utf-8")
        capture = {
            "file_id": file_id,
            "queued_at": "2026-09-30T12:00:00+00:00",
            "processed_at": "2026-09-30T12:05:00+00:00",
            "capture_type": "deep_research",
            "source_file": str(md_path),
            "processing_result": {
                "exec_mutations": 0,
                "executive_declaration_mutations": 0,
                "structured_import_reconciled": False,
                "structured_import_applied": None,
                "canonical_mutation_statement": "pending reconciliation",
            },
        }
        (ci.PROCESSED_DIR / f"{file_id}.json").write_text(json.dumps(capture), encoding="utf-8")

    def _write_import_receipt(self, packet_id: str, **fields) -> None:
        receipts = icpr._load_import_receipts()
        receipts["receipts"][packet_id] = {"packet_id": packet_id, "recorded_at": "2026-09-30T12:10:00+00:00", **fields}
        icpr._save_import_receipts(receipts)

    def _stored_result(self, file_id: str) -> dict:
        data = json.loads((ci.PROCESSED_DIR / f"{file_id}.json").read_text(encoding="utf-8"))
        return data["processing_result"]


class TestAppliedFindingsReconcile(_IsolatedFixtureMixin):
    """Handoff acceptance test 1-2: applied findings surface as a real count
    with the exact canonical target named, not a misleading 0."""

    def test_applied_findings_reported_not_zero(self):
        self._write_capture("cap-1", packet_id="dr-toast-1")
        self._write_import_receipt(
            "dr-toast-1", applied=4, deduped=0, queued_for_review=0,
            findings_rejected=0, canonical_targets_changed=["competitor:toast"],
        )
        report = reconciler.reconcile()
        self.assertEqual(report["reconciled_count"], 1)
        result = self._stored_result("cap-1")
        self.assertEqual(result["structured_import_applied"], 4)
        self.assertEqual(result["canonical_targets_changed"], ["competitor:toast"])
        self.assertIn("competitor:toast", result["canonical_mutation_statement"])
        self.assertTrue(result["structured_import_reconciled"])


class TestQueuedFindingsReconcile(_IsolatedFixtureMixin):
    """Handoff acceptance test 3: a review-required finding reports queued,
    not applied."""

    def test_queued_only_does_not_claim_applied(self):
        self._write_capture("cap-2", packet_id="dr-queued-1")
        self._write_import_receipt(
            "dr-queued-1", applied=0, deduped=0, queued_for_review=2,
            findings_rejected=0, canonical_targets_changed=[],
        )
        reconciler.reconcile()
        result = self._stored_result("cap-2")
        self.assertEqual(result["structured_import_applied"], 0)
        self.assertEqual(result["structured_import_queued"], 2)
        self.assertIn("queued for review", result["canonical_mutation_statement"])


class TestDedupedFindingsReconcile(_IsolatedFixtureMixin):
    """Handoff acceptance test 4: a duplicate reports deduped and does not
    inflate the applied count."""

    def test_deduped_does_not_inflate_applied(self):
        self._write_capture("cap-3", packet_id="dr-dup-1")
        self._write_import_receipt(
            "dr-dup-1", applied=1, deduped=3, queued_for_review=0,
            findings_rejected=0, canonical_targets_changed=["competitor:toast"],
        )
        reconciler.reconcile()
        result = self._stored_result("cap-3")
        self.assertEqual(result["structured_import_applied"], 1)
        self.assertEqual(result["structured_import_deduped"], 3)


class TestRejectedFindingsReconcile(_IsolatedFixtureMixin):
    """Handoff acceptance test 5: a malformed finding reports rejected."""

    def test_rejected_only_reports_rejected_not_applied(self):
        self._write_capture("cap-4", packet_id="dr-bad-1")
        self._write_import_receipt(
            "dr-bad-1", applied=0, deduped=0, queued_for_review=0,
            findings_rejected=2, canonical_targets_changed=[],
        )
        reconciler.reconcile()
        result = self._stored_result("cap-4")
        self.assertEqual(result["structured_import_applied"], 0)
        self.assertEqual(result["structured_import_rejected"], 2)
        self.assertIn("rejected", result["canonical_mutation_statement"])


class TestIdempotencyAndSkips(_IsolatedFixtureMixin):
    def test_rerun_is_a_noop_once_reconciled(self):
        self._write_capture("cap-5", packet_id="dr-toast-2")
        self._write_import_receipt(
            "dr-toast-2", applied=1, deduped=0, queued_for_review=0,
            findings_rejected=0, canonical_targets_changed=["competitor:toast"],
        )
        first = reconciler.reconcile()
        self.assertEqual(first["reconciled_count"], 1)
        second = reconciler.reconcile()
        self.assertEqual(second["reconciled_count"], 0)
        self.assertEqual(second["skipped"][0]["reason"], "already_reconciled")

    def test_no_sidecar_findings_is_skipped_not_failed(self):
        self._write_capture("cap-6", packet_id="dr-none", has_sidecar_findings=False)
        report = reconciler.reconcile()
        self.assertEqual(report["reconciled_count"], 0)
        self.assertEqual(report["skipped"][0]["reason"], "no_structured_sidecar_findings")

    def test_sidecar_findings_but_no_import_receipt_yet_is_skipped_not_failed(self):
        self._write_capture("cap-7", packet_id="dr-not-swept-yet")
        report = reconciler.reconcile()
        self.assertEqual(report["reconciled_count"], 0)
        self.assertEqual(report["skipped"][0]["reason"], "no_import_receipt_yet")

    def test_non_deep_research_captures_are_ignored(self):
        self._write_capture("cap-8", packet_id="dr-ignored")
        data = json.loads((ci.PROCESSED_DIR / "cap-8.json").read_text(encoding="utf-8"))
        data["capture_type"] = "meeting"
        (ci.PROCESSED_DIR / "cap-8.json").write_text(json.dumps(data), encoding="utf-8")
        report = reconciler.reconcile()
        self.assertEqual(report["reconciled_count"], 0)
        self.assertEqual(report["skipped_count"], 0)


if __name__ == "__main__":
    unittest.main()
