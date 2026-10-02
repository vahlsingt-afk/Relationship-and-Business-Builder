"""
test_competitor_bulk_import.py — RB-DEFECT-071, 2026-09-11.

Coverage for the API-level durable fix: structured errors from
createCompetitor, and the new bulkImportCompetitors endpoint that replaces
"many concurrent createCompetitor calls" -- the exact pattern that
corrupted competitor_registry.json and took listCompetitors down for a
real incident (Todd bulk-adding the FSTEC 2026 Buyers Guide roster, 142
vendors, 8 concurrent workers). Isolated from real production data (own
tmp cic.ROOT), same pattern as test_competitor_intelligence.py.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

import competitor_intelligence_common as cic  # noqa: E402


class _IsolatedRootMixin:
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_root = cic.ROOT
        cic.ROOT = Path(self._tmpdir.name)

    def tearDown(self):
        cic.ROOT = self._orig_root
        self._tmpdir.cleanup()


class TestCreateCompetitorStructuredErrors(_IsolatedRootMixin, unittest.TestCase):
    def test_create_competitor_returns_ok_with_already_tracked_field(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            body = server.CreateCompetitorBody(name="Acme POS")
            result = server.post_create_competitor(body, x_api_key=None)
        self.assertFalse(result["already_tracked"])
        with patch.object(server, "_auth", lambda *a, **k: None):
            result2 = server.post_create_competitor(body, x_api_key=None)
        self.assertTrue(result2["already_tracked"])

    def test_create_competitor_failure_returns_structured_500(self):
        import server
        from fastapi import HTTPException
        with patch.object(server, "_auth", lambda *a, **k: None), \
             patch.object(server.compintel, "sync_from_ecosystem", side_effect=RuntimeError("boom")):
            body = server.CreateCompetitorBody(name="Acme POS")
            with self.assertRaises(HTTPException) as cm:
                server.post_create_competitor(body, x_api_key=None)
        detail = cm.exception.detail
        self.assertEqual(detail["stage"], "sync")
        self.assertTrue(detail["shell_created"])
        self.assertTrue(detail["registered"])
        self.assertTrue(detail["safe_to_retry"])


class TestBulkImportCompetitors(_IsolatedRootMixin, unittest.TestCase):
    def test_mixed_batch_classifies_each_item_correctly(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            first = server.post_create_competitor(
                server.CreateCompetitorBody(name="Acme POS"), x_api_key=None)
            self.assertFalse(first["already_tracked"])

            body = server.BulkImportCompetitorsBody(
                names=["Acme POS", "Beta Payments", "Global Payments Inc."],
            )
            result = server.post_bulk_import_competitors(body, x_api_key=None)

        self.assertEqual(result["requested"], 3)
        by_name = {r["name"]: r for r in result["results"]}
        self.assertEqual(by_name["Acme POS"]["status"], "already_exists")
        self.assertEqual(by_name["Beta Payments"]["status"], "created")
        self.assertEqual(by_name["Global Payments Inc."]["status"], "rejected_with_reason")
        self.assertEqual(result["counts"], {
            "created": 1, "already_exists": 1, "rejected_with_reason": 1, "failed": 0,
        })

    def test_own_company_never_becomes_a_tracked_competitor(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            body = server.BulkImportCompetitorsBody(names=["Genius", "Global Payments"])
            server.post_bulk_import_competitors(body, x_api_key=None)
        reg = cic.load_registry()
        self.assertEqual(reg["registry"], [])

    def test_dry_run_classifies_with_zero_writes(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            body = server.BulkImportCompetitorsBody(names=["Acme POS", "Beta Payments"], dry_run=True)
            result = server.post_bulk_import_competitors(body, x_api_key=None)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["counts"]["created"], 2)
        # Nothing actually written.
        self.assertEqual(cic.load_registry()["registry"], [])
        self.assertFalse((cic.ROOT / "competitors").exists())

    def test_dry_run_recognizes_already_existing_vendor(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            server.post_create_competitor(server.CreateCompetitorBody(name="Acme POS"), x_api_key=None)
            body = server.BulkImportCompetitorsBody(names=["Acme POS"], dry_run=True)
            result = server.post_bulk_import_competitors(body, x_api_key=None)
        self.assertEqual(result["results"][0]["status"], "already_exists")

    def test_one_failure_does_not_abort_the_rest_of_the_batch(self):
        import server
        real_generate_profile = server.compintel.generate_profile

        def flaky(name):
            if name == "Broken Vendor":
                raise server.compintel.CompetitorCreationError(
                    "sync", "broken-vendor", shell_created=True, registered=True,
                    cause=RuntimeError("boom"),
                )
            return real_generate_profile(name)

        with patch.object(server, "_auth", lambda *a, **k: None), \
             patch.object(server.compintel, "generate_profile", side_effect=flaky):
            body = server.BulkImportCompetitorsBody(names=["Broken Vendor", "Good Vendor"])
            result = server.post_bulk_import_competitors(body, x_api_key=None)

        by_name = {r["name"]: r for r in result["results"]}
        self.assertEqual(by_name["Broken Vendor"]["status"], "failed")
        self.assertEqual(by_name["Broken Vendor"]["error"]["stage"], "sync")
        self.assertEqual(by_name["Good Vendor"]["status"], "created")
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertEqual(result["counts"]["created"], 1)

    def test_blank_names_skipped_silently(self):
        import server
        with patch.object(server, "_auth", lambda *a, **k: None):
            body = server.BulkImportCompetitorsBody(names=["Acme POS", "   ", ""])
            result = server.post_bulk_import_competitors(body, x_api_key=None)
        self.assertEqual(len(result["results"]), 1)


if __name__ == "__main__":
    unittest.main()
