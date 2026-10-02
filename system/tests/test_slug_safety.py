"""
test_slug_safety.py

RB-SECURITY-2026-09-05, full ingest-route audit: found that Blue Sheets'
createBlueSheetAccount (body.account_slug) and Master Account Plans'
ingestMasterAccountPlanUpload ({vendor_slug} path param) both built a
filesystem path directly from a caller-supplied slug with NO validation --
`ROOT / "accounts" / slug`, `ROOT / "vendors" / vendor_slug` -- then
unconditionally mkdir(parents=True) and wrote JSON content into it. Both
routes are gated only by the same shared x-api-key every other write route
uses, not a stronger boundary. Confirmed exploitable path traversal
(CWE-22): a slug like "../../../../tmp/evil" would resolve OUTSIDE the
intended portfolio directory before either write happened.

Competitor Intelligence's equivalent creation path was already safe
end-to-end (always derives the slug via _slugify() first) -- hardened its
low-level competitor_dir() choke point anyway, for the same reason the
other two engines' low-level dir() functions were hardened: so a FUTURE
caller can't reintroduce this by skipping validation.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "blue_sheets" / "_engine"))
sys.path.insert(0, str(ROOT / "master_account_plans" / "_engine"))

import slug_safety  # noqa: E402


class TestAssertSafeSlug(unittest.TestCase):
    def test_real_slugs_pass(self):
        for slug in ("mcdonalds", "pollo-campero", "worldpay", "a", "a-b-c-123"):
            slug_safety.assert_safe_slug(slug)  # must not raise

    def test_path_traversal_rejected(self):
        for evil in (
            "../../../../tmp/evil", "..", "../etc", "foo/../../bar",
            "/etc/passwd", "foo/bar", "..\\..\\windows",
        ):
            with self.assertRaises(ValueError, msg=f"should have rejected {evil!r}"):
                slug_safety.assert_safe_slug(evil)

    def test_empty_and_non_string_rejected(self):
        for bad in ("", None, 123, ["a"]):
            with self.assertRaises(ValueError):
                slug_safety.assert_safe_slug(bad)  # type: ignore[arg-type]

    def test_uppercase_and_underscore_rejected(self):
        """Every real slug this system generates (_slugify()) is already
        lowercase-hyphen-only -- reject anything that doesn't match that
        shape rather than silently normalizing it."""
        for bad in ("McDonalds", "pollo_campero", "pollo campero", "pollo.campero"):
            with self.assertRaises(ValueError):
                slug_safety.assert_safe_slug(bad)


class TestBlueSheetAccountDirRejectsTraversal(unittest.TestCase):
    def setUp(self):
        import common as bs_common
        self._orig_root = bs_common.CUSTOMERS_PROSPECTS_ROOT
        self._tmpdir = tempfile.TemporaryDirectory()
        bs_common.CUSTOMERS_PROSPECTS_ROOT = Path(self._tmpdir.name)
        (bs_common.CUSTOMERS_PROSPECTS_ROOT / "accounts" / "acme").mkdir(parents=True)
        self.bs_common = bs_common

    def tearDown(self):
        self.bs_common.CUSTOMERS_PROSPECTS_ROOT = self._orig_root
        self._tmpdir.cleanup()

    def test_valid_slug_still_works(self):
        d = self.bs_common.account_dir("acme")
        self.assertTrue(d.is_dir())

    def test_traversal_slug_rejected_before_any_lookup(self):
        with self.assertRaises(ValueError):
            self.bs_common.account_dir("../../../../tmp/evil")


class TestCreateBlueSheetAccountRejectsTraversal(unittest.TestCase):
    def setUp(self):
        import common as bs_common
        import create_account
        self.bs_common = bs_common
        self.create_account = create_account
        self._orig_root = bs_common.CUSTOMERS_PROSPECTS_ROOT
        self._tmpdir = tempfile.TemporaryDirectory()
        bs_common.CUSTOMERS_PROSPECTS_ROOT = Path(self._tmpdir.name)
        (bs_common.CUSTOMERS_PROSPECTS_ROOT / "accounts").mkdir(parents=True)

    def tearDown(self):
        self.bs_common.CUSTOMERS_PROSPECTS_ROOT = self._orig_root
        self._tmpdir.cleanup()

    def test_traversal_slug_rejected_and_nothing_written_outside_root(self):
        outside = Path(self._tmpdir.name).parent / "outside_marker"
        evil_slug = "../outside_marker"
        with self.assertRaises(ValueError):
            self.create_account.create_blue_sheet_account(
                evil_slug, "Evil Co",
                account_data={"opportunities": [{"x": 1}]},
                brand_profile_data={},
            )
        self.assertFalse(outside.exists(), "path traversal must never reach the filesystem")


class TestMasterAccountPlanVendorDirRejectsTraversal(unittest.TestCase):
    def setUp(self):
        import mp_common as common
        self.common = common
        self._orig_root = common.ROOT
        self._tmpdir = tempfile.TemporaryDirectory()
        common.ROOT = Path(self._tmpdir.name)
        (common.ROOT / "vendors" / "worldpay").mkdir(parents=True)

    def tearDown(self):
        self.common.ROOT = self._orig_root
        self._tmpdir.cleanup()

    def test_valid_slug_still_works(self):
        d = self.common.vendor_dir("worldpay")
        self.assertTrue(d.is_dir())

    def test_traversal_slug_rejected(self):
        with self.assertRaises(ValueError):
            self.common.vendor_dir("../../../../tmp/evil")


class TestIngestCuratedUpdateRejectsTraversal(unittest.TestCase):
    def setUp(self):
        import mp_common as common
        import create_plan
        self.common = common
        self.create_plan = create_plan
        self._orig_root = common.ROOT
        self._tmpdir = tempfile.TemporaryDirectory()
        common.ROOT = Path(self._tmpdir.name)
        (common.ROOT / "vendors").mkdir(parents=True)

    def tearDown(self):
        self.common.ROOT = self._orig_root
        self._tmpdir.cleanup()

    def test_traversal_vendor_slug_rejected_and_nothing_written_outside_root(self):
        outside = Path(self._tmpdir.name).parent / "outside_marker"
        evil_slug = "../outside_marker"
        with self.assertRaises(ValueError):
            self.create_plan.ingest_curated_update(
                evil_slug, "Evil Vendor",
                ranked_portfolio=[{"account": "x"}], rm_portfolios=[],
                user_authorization_quote="Todd said create it",
            )
        self.assertFalse(outside.exists(), "path traversal must never reach the filesystem")


class TestCompetitorDirRejectsTraversal(unittest.TestCase):
    def test_traversal_slug_rejected(self):
        import competitor_intelligence_common as cic
        with self.assertRaises(ValueError):
            cic.competitor_dir("../../../../tmp/evil", create=True)


if __name__ == "__main__":
    unittest.main()
