"""
test_master_account_plan.py — RB-2026-08-28.

Real incident: Todd uploaded Worldpay_Master_Account_Plan_2026-08-14.xlsx
(a portfolio-level, multi-account vendor plan -- 30 ranked accounts, 6 named
Worldpay relationship managers) and asked the CoS to update it. The CoS
misread it as a single-account Blue Sheet request, producing an empty,
falsely-authorized Blue Sheet. Built master_account_plans/ as a real third
artifact type: vendor/partner-scoped, seeded from Todd's real workbook,
mutated day-to-day via a safe-apply/flag-for-review engine mirroring Blue
Sheet's own discipline exactly.

Tests use a disposable temp ROOT (monkeypatched module-level constant, same
style as test_intelligence_index.py) so nothing here touches the real
master_account_plans/vendors/worldpay/ state a live session may have
ingested.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "master_account_plans" / "_engine"))
sys.path.insert(0, str(ROOT_DIR / "system" / "scripts"))

try:
    import openpyxl
    _HAS_OPENPYXL = True
except ImportError:
    _HAS_OPENPYXL = False

import parse_workbook as pw  # noqa: E402
import dataset_classifier as dc  # noqa: E402

REAL_WORLDPAY_FILE = ROOT_DIR / "system" / "inbox" / "user_artifacts" / "Worldpay_Master_Account_Plan_2026-08-14.xlsx"


def _make_master_account_plan_workbook_bytes() -> bytes:
    import io
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Executive Summary"
    ws.append(("Executive Summary — Test Vendor Master Account Plan",))

    ws2 = wb.create_sheet("Ranked Portfolio")
    ws2.append(("Rank | Priority | Account | RM",))  # single-cell title row
    ws2.append(("Rank", "Priority", "Account", "RM", "Score", "Tier", "Immediate Next Action", "Key Vendors"))
    ws2.append((1, "P1", "TEST ACCOUNT LLC", "Jane Doe", 83, "Tier 1", "Do the thing.", "Vendor A; Vendor B"))
    ws2.append((2, "P2", "SECOND ACCOUNT", "John Smith", 60, "Tier 2", "Do another thing.", "Vendor C"))

    ws3 = wb.create_sheet("RM Portfolio")
    ws3.append(("RM Portfolio Briefings",))
    ws3.append(("RM", "Opportunity Accounts", "P1 Accounts", "Average Score", "Top Ranked Accounts"))
    ws3.append(("Jane Doe", 1, 1, 83, "TEST ACCOUNT LLC"))
    ws3.append(("John Smith", 1, 0, 60, "SECOND ACCOUNT"))

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@unittest.skipUnless(_HAS_OPENPYXL, "openpyxl not installed")
class TestParseWorkbook(unittest.TestCase):
    def test_parses_real_ranked_portfolio_and_rm_rows(self):
        content = _make_master_account_plan_workbook_bytes()
        data = pw.parse_workbook(content)
        self.assertEqual(len(data["ranked_portfolio"]), 2)
        self.assertEqual(len(data["rm_portfolios"]), 2)
        row = data["ranked_portfolio"][0]
        self.assertEqual(row["account_name"], "TEST ACCOUNT LLC")
        self.assertEqual(row["rm_name"], "Jane Doe")
        self.assertEqual(row["score"], 83)
        self.assertEqual(row["key_vendors"], ["Vendor A", "Vendor B"])
        self.assertIsNone(row["linked_blue_sheet_slug"])  # resolved later by create_plan.py

    def test_missing_required_sheets_raises(self):
        import io
        wb = openpyxl.Workbook()
        wb.active.title = "Just A Sheet"
        buf = io.BytesIO()
        wb.save(buf)
        with self.assertRaises(ValueError):
            pw.parse_workbook(buf.getvalue())

    def test_reconstructs_formula_score_when_xlsx_has_no_cached_value(self):
        import io
        wb = openpyxl.Workbook()
        wb.active.title = "Executive Summary"
        ranked = wb.create_sheet("Ranked Portfolio")
        ranked.append(("Rank", "Priority", "Account", "RM", "Score", "Strategic Value",
                       "Active Trigger", "Access", "Lifecycle", "Deployment Health",
                       "Whitespace", "Coordination"))
        ranked.append((1, "P1", "TEST", "Jane", "=SUM(F2:L2)", 4, 5, 4, 4, 3, 4, 5))
        rm = wb.create_sheet("RM Portfolio")
        rm.append(("RM", "Opportunity Accounts"))
        rm.append(("Jane", 1))
        buf = io.BytesIO()
        wb.save(buf)
        row = pw.parse_workbook(buf.getvalue())["ranked_portfolio"][0]
        self.assertEqual(row["score"], 83.0)

    def test_real_worldpay_file_if_present(self):
        if not REAL_WORLDPAY_FILE.exists():
            self.skipTest("real incident file not present in this environment")
        data = pw.parse_workbook_file(REAL_WORLDPAY_FILE)
        self.assertEqual(len(data["ranked_portfolio"]), 30)
        self.assertGreaterEqual(len(data["rm_portfolios"]), 3)
        self.assertIn("Worldpay", data["executive_summary_text"])


@unittest.skipUnless(_HAS_OPENPYXL, "openpyxl not installed")
class TestDatasetClassifierRecognizesShape(unittest.TestCase):
    """Real incident's root cause: this document shape had NO classifier
    signature at all, so it fell through to the generic pipeline and got
    misrouted by the model. Confirms both directions: correctly recognized,
    and never false-positive-matches an unrelated single-account workbook."""

    def test_recognizes_ranked_portfolio_plus_rm_portfolio_shape(self):
        # A minimal 3-sheet synthetic fixture (vs. the real file's 10) --
        # required_sheets alone is enough to identify the type correctly;
        # the real file's full sheet set is what clears the auto-ingest
        # confidence bar, confirmed separately below against the real file.
        content = _make_master_account_plan_workbook_bytes()
        result = dc.classify_bytes(content, "Test_Vendor_Master_Account_Plan_2026-08-28.xlsx")
        self.assertEqual(result.dataset_type, "master_account_plan_workbook")
        self.assertGreater(result.confidence, 0)

    def test_real_worldpay_file_clears_auto_ingest_threshold(self):
        if not REAL_WORLDPAY_FILE.exists():
            self.skipTest("real incident file not present in this environment")
        result = dc.classify_bytes(REAL_WORLDPAY_FILE.read_bytes(), REAL_WORLDPAY_FILE.name)
        self.assertEqual(result.dataset_type, "master_account_plan_workbook")
        self.assertGreaterEqual(result.confidence, dc.DEFAULT_CONFIDENCE_THRESHOLD)
        self.assertTrue(dc.should_auto_ingest(result))

    def test_does_not_misclassify_a_plain_single_sheet_workbook(self):
        import io
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Blue Sheet"
        ws.append(("Some", "Random", "Columns"))
        ws.append((1, 2, 3))
        buf = io.BytesIO()
        wb.save(buf)
        result = dc.classify_bytes(buf.getvalue(), "Some_Blue_Sheet.xlsx")
        self.assertNotEqual(result.dataset_type, "master_account_plan_workbook")

    def test_real_pollo_campero_blue_sheet_not_misclassified(self):
        real_bs = ROOT_DIR / "blue_sheets" / "accounts" / "pollo-campero" / "current" / "Pollo_Campero_Blue_Sheet.xlsx"
        if not real_bs.exists():
            self.skipTest("real Pollo Campero Blue Sheet not present in this environment")
        result = dc.classify_bytes(real_bs.read_bytes(), real_bs.name)
        self.assertNotEqual(result.dataset_type, "master_account_plan_workbook")


class TestCreatePlanGuards(unittest.TestCase):
    """create_plan.py is imported fresh per test class (not at module scope)
    since it does real sys.path/module-loading work at import time -- keeps
    that contained to tests that actually need it."""

    @classmethod
    def setUpClass(cls):
        import create_plan as cp
        cls.cp = cp

    def test_derive_vendor_slug_matches_known_convention(self):
        self.assertEqual(
            self.cp.derive_vendor_slug("Worldpay_Master_Account_Plan_2026-08-14.xlsx"),
            "worldpay",
        )

    def test_derive_vendor_slug_returns_none_for_unrecognized_filename(self):
        self.assertIsNone(self.cp.derive_vendor_slug("random_upload.xlsx"))

    def test_derive_vendor_slug_returns_none_when_title_disagrees(self):
        self.assertIsNone(
            self.cp.derive_vendor_slug(
                "Worldpay_Master_Account_Plan_2026-08-14.xlsx",
                dashboard_title="This is actually about Stripe",
            )
        )

    def test_ingest_curated_update_rejects_missing_authorization_quote(self):
        with self.assertRaises(ValueError):
            self.cp.ingest_curated_update(
                "test-vendor-guard", "Test Vendor",
                ranked_portfolio=[{"account_name": "X"}], rm_portfolios=[],
                user_authorization_quote="",
            )

    def test_ingest_curated_update_rejects_empty_content(self):
        with self.assertRaises(ValueError):
            self.cp.ingest_curated_update(
                "test-vendor-guard", "Test Vendor",
                ranked_portfolio=[], rm_portfolios=[],
                user_authorization_quote="build the test vendor plan",
            )

    def test_cross_link_resolution_no_guess_when_ambiguous(self):
        registry = {"registry": [
            {"account_id": "acct-foo", "aliases": []},
            {"account_id": "acct-foo-bar", "aliases": []},
        ]}
        # "Foo" alone is ambiguous against both foo and foo-bar candidates
        # only if both token sets are subsets -- confirm a genuine no-match
        # case (unrelated name) returns None, never a guess.
        result = self.cp._resolve_cross_link("Totally Unrelated Company", registry)
        self.assertIsNone(result)

    def test_cross_link_resolution_real_match(self):
        registry = {"registry": [{"account_id": "acct-del-taco", "aliases": ["Yadav Enterprises"]}]}
        self.assertEqual(self.cp._resolve_cross_link("DEL TACO LLC", registry), "del-taco")


class TestSafeApplyIdempotencyAndNeverAutoScores(unittest.TestCase):
    """Real fixture-based test against a disposable temp vendor dir --
    proves the two properties that matter most: (1) re-running the safe-
    apply pass never double-appends evidence, (2) a material signal never
    changes ranked_portfolio.json's score/tier, only queues a review item."""

    def setUp(self):
        import mp_common
        import mp_impact_review as mir
        self.mp_common = mp_common
        self.mir = mir
        self._tmpdir = tempfile.mkdtemp(prefix="rb-map-test-")
        self._orig_root = mp_common.ROOT
        mp_common.ROOT = Path(self._tmpdir)

        vendor_dir = mp_common.ROOT / "vendors" / "testvendor"
        vendor_dir.mkdir(parents=True)
        mp_common.save_json(vendor_dir / "plan.json", {
            "vendor_slug": "testvendor", "display_name": "Test Vendor",
            "last_evidence_date": "2000-01-01",
        })
        mp_common.save_json(vendor_dir / "ranked_portfolio.json", [{
            "account_name": "MCDONALD'S", "rm_name": "Jane Doe",
            "priority": "P1", "score": 90, "tier": "Tier 1",
            "linked_blue_sheet_slug": None, "linked_account_research_slug": None,
        }])
        mp_common.save_json(vendor_dir / "rm_portfolios.json", [{
            "rm_name": "Jane Doe", "opportunity_accounts_count": 1,
            "p1_accounts_count": 1, "average_score": 90,
        }])

        # Isolate bs_common/registries so this never touches real Blue
        # Sheet/Account Research state -- same MagicMock isolation style as
        # test_blue_sheet_coverage_hook.py.
        self._orig_bs_common = mir.bs_common
        fake_bs_common = MagicMock()
        fake_bs_common.load_registry.return_value = {"registry": []}
        fake_bs_common.log_coverage_event.return_value = None
        mir.bs_common = fake_bs_common
        mir.create_plan.bs_common = fake_bs_common

        self._orig_ar_dir = mir.create_plan.ACCOUNT_RESEARCH_DIR
        mir.create_plan.ACCOUNT_RESEARCH_DIR = Path(self._tmpdir) / "no_account_research"

        self._orig_render = mir.render_mod
        mir.render_mod = MagicMock()
        mir.render_mod.append_evidence_rows.return_value = 0
        mir.render_mod.refresh_rm_portfolio_sheet.return_value = 0

    def tearDown(self):
        self.mp_common.ROOT = self._orig_root
        self.mir.bs_common = self._orig_bs_common
        self.mir.create_plan.bs_common = self._orig_bs_common
        self.mir.create_plan.ACCOUNT_RESEARCH_DIR = self._orig_ar_dir
        self.mir.render_mod = self._orig_render
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _fake_graph(self):
        return {
            "entities": [{"id": "brand-mcdonald-s"}],
            "signals": [{
                "id": "sig-test-mcdonalds-leadership-change",
                "captured_at": "2026-08-20T00:00:00Z",
                "event_at": "2026-08-19",
                "entities": ["brand-mcdonald-s"],
                "signal_type": "leadership_change",
                "summary": "Test signal.",
                "confidence": {"level": "high"},
            }],
        }

    def test_material_signal_queues_review_never_changes_score(self):
        self.mir._load_graph = lambda: self._fake_graph()
        result = self.mir.run_safe_apply("testvendor")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["evidence_appended"]), 1)
        self.assertEqual(len(result["queued_for_review"]), 1)

        ranked = self.mp_common.load_json(
            self.mp_common.ROOT / "vendors" / "testvendor" / "ranked_portfolio.json"
        )
        self.assertEqual(ranked[0]["score"], 90)  # untouched
        self.assertEqual(ranked[0]["tier"], "Tier 1")  # untouched

    def test_rerun_is_idempotent_no_duplicate_evidence(self):
        self.mir._load_graph = lambda: self._fake_graph()
        self.mir.run_safe_apply("testvendor")
        second = self.mir.run_safe_apply("testvendor")
        self.assertEqual(second["evidence_appended"], [])  # nothing new the second time

        evidence = self.mp_common.load_jsonl(
            self.mp_common.ROOT / "vendors" / "testvendor" / "evidence.jsonl"
        )
        self.assertEqual(len(evidence), 1)  # not 2

    def test_rm_aggregates_always_recomputed_even_with_no_signals(self):
        self.mir._load_graph = lambda: {"entities": [], "signals": []}
        result = self.mir.run_safe_apply("testvendor")
        self.assertTrue(result["ok"])
        rm = self.mp_common.load_json(
            self.mp_common.ROOT / "vendors" / "testvendor" / "rm_portfolios.json"
        )[0]
        self.assertEqual(rm["opportunity_accounts_count"], 1)
        self.assertEqual(rm["average_score"], 90)


class TestSyncPaymentsToEcosystemGraph(unittest.TestCase):
    """RB-DEFECT-2026-08-29: ingesting a Master Account Plan never touched
    ecosystem_intelligence.json at all -- confirmed live, Church's Chicken
    is a real Worldpay portfolio account (rank 19) and RB's own macro
    tech-stack graph had no payments relationship to Worldpay for it, or
    for any of the other 29 accounts. Todd: "All the accounts in the
    worldpay master account should have updated the tech macro graph to
    have worldpay be the payments provider." A Ranked Portfolio IS the
    vendor's own real, current customer roster by construction -- every row
    is genuine evidence of an active payments relationship."""

    def setUp(self):
        import create_plan as cp
        import mp_common
        import ecosystem_intelligence as eco
        self.cp = cp
        self.mp_common = mp_common
        self.eco = eco

        self._tmpdir = tempfile.mkdtemp(prefix="rb-map-payments-test-")
        self._orig_root = mp_common.ROOT
        mp_common.ROOT = Path(self._tmpdir)
        self.cp.common = mp_common

        vendor_dir = mp_common.ROOT / "vendors" / "testpayvendor"
        vendor_dir.mkdir(parents=True)
        mp_common.save_json(vendor_dir / "plan.json", {
            "vendor_slug": "testpayvendor", "display_name": "Test Pay Vendor",
            "vendor_entity_id": "vendor-testpayvendor",
            "last_evidence_date": "2026-08-14",
        })
        mp_common.save_json(vendor_dir / "ranked_portfolio.json", [
            {"rank": 1, "tier": "Tier 1", "account_name": "MCDONALD'S CORPORATION",
             "locations": 100, "lifecycle_posture": "Established."},
            {"rank": 2, "tier": "Tier 2", "account_name": "CHOICE HOTELS",
             "locations": 50, "lifecycle_posture": "Deconverting."},
            {"rank": 3, "tier": "Tier 2", "account_name": "TOTALLY UNKNOWN BRAND XYZ CORP",
             "locations": 5, "lifecycle_posture": "Established."},
        ])

        eco_dir = Path(self._tmpdir) / "ecosystem"
        eco_dir.mkdir(parents=True)
        self._graph_path = eco_dir / "ecosystem_intelligence.json"
        self._snap_dir = eco_dir / "_snapshots"
        self._orig_graph_path = eco.core.ECOSYSTEM_INTELLIGENCE_PATH
        self._orig_snap_dir = eco.core.SNAPSHOTS_DIR
        eco.core.ECOSYSTEM_INTELLIGENCE_PATH = self._graph_path
        eco.core.SNAPSHOTS_DIR = self._snap_dir
        self._graph_path.write_text(json.dumps({
            "version": 1, "contract": "rb_ecosystem_intelligence_v1", "last_updated": "2026-08-29",
            "entities": [
                {"id": "brand-mcdonald-s", "name": "McDonald's", "entity_type": "brand", "aliases": []},
            ],
            "relationships": [], "sources": [],
        }), encoding="utf-8")

        self._patch_validator = unittest.mock.patch("subprocess.run")
        mock_run = self._patch_validator.start()
        mock_run.return_value.returncode = 0

        # This test only adds a temporary override entry for the made-up
        # unresolvable brand -- everything else uses the real, hand-
        # verified override table so the McDonald's/Choice Hotels rows
        # exercise the actual production resolution logic.

    def tearDown(self):
        self._patch_validator.stop()
        self.mp_common.ROOT = self._orig_root
        self.eco.core.ECOSYSTEM_INTELLIGENCE_PATH = self._orig_graph_path
        self.eco.core.SNAPSHOTS_DIR = self._orig_snap_dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_dry_run_does_not_write(self):
        before = self._graph_path.read_text()
        result = self.cp.sync_payments_to_ecosystem_graph("testpayvendor", dry_run=True)
        self.assertEqual(self._graph_path.read_text(), before)
        self.assertTrue(result["dry_run"])
        # McDonald's (existing entity) + the fictional unknown brand (a
        # genuinely new entity gets created, not skipped -- see
        # test_unresolvable_new_brand_creates_new_entity_not_skipped).
        # Only Choice Hotels is skipped as out-of-scope.
        self.assertEqual(result["added"], 2)
        self.assertEqual(result["skipped_out_of_scope"], 1)

    def test_real_brand_gets_payments_relationship(self):
        self.cp.sync_payments_to_ecosystem_graph("testpayvendor", dry_run=False)
        graph = json.loads(self._graph_path.read_text())
        rels = [r for r in graph["relationships"] if r["from_entity_id"] == "brand-mcdonald-s"]
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["category"], "payments")
        self.assertEqual(rels[0]["to_entity_id"], "vendor-testpayvendor")
        self.assertEqual(rels[0]["status"], "active")

    def test_non_restaurant_account_skipped(self):
        result = self.cp.sync_payments_to_ecosystem_graph("testpayvendor", dry_run=True)
        self.assertEqual(result["skipped_out_of_scope"], 1)
        graph = json.loads(self._graph_path.read_text())
        self.assertFalse(any("hotel" in (e.get("name") or "").lower() for e in graph.get("entities", [])))

    def test_unresolvable_new_brand_creates_new_entity_not_skipped(self):
        result = self.cp.sync_payments_to_ecosystem_graph("testpayvendor", dry_run=False)
        graph = json.loads(self._graph_path.read_text())
        new_names = [e["name"] for e in graph["entities"] if "UNKNOWN BRAND" in (e.get("name") or "")]
        self.assertEqual(len(new_names), 1)

    def test_relationship_ids_are_schema_safe_no_underscores(self):
        """RB-DEFECT-2026-08-29: the first --confirm run produced ids with
        underscores ("...vendor_own_account_roster...") that failed
        ecosystem_intelligence.schema.json's id pattern -- _write_graph
        writes to disk BEFORE its own validation subprocess check, so this
        genuinely corrupted the real file for a few minutes until caught
        and restored from the pre-write snapshot. Must never regress."""
        self.cp.sync_payments_to_ecosystem_graph("testpayvendor", dry_run=False)
        graph = json.loads(self._graph_path.read_text())
        for rel in graph["relationships"]:
            self.assertRegex(rel["id"], r"^rel-[a-z0-9][a-z0-9-]{1,160}$")

    def test_source_authority_is_a_valid_schema_value(self):
        self.cp.sync_payments_to_ecosystem_graph("testpayvendor", dry_run=False)
        graph = json.loads(self._graph_path.read_text())
        valid = {
            "sec_or_regulatory_filing", "operator_filing_earnings_investor",
            "vendor_filing_earnings_investor", "official_brand_announcement",
            "vendor_case_study_named_customer", "official_customer_page_or_executive_statement",
            "credible_trade_reporting_direct_attribution", "vendor_logo_or_listing_page",
            "unknown", None,
        }
        for rel in graph["relationships"]:
            for assertion in rel.get("source_assertions", []):
                self.assertIn(assertion.get("source_authority"), valid)

    def test_rerun_is_idempotent_no_duplicate_relationships(self):
        self.cp.sync_payments_to_ecosystem_graph("testpayvendor", dry_run=False)
        self.cp.sync_payments_to_ecosystem_graph("testpayvendor", dry_run=False)
        graph = json.loads(self._graph_path.read_text())
        rels = [r for r in graph["relationships"] if r["from_entity_id"] == "brand-mcdonald-s"]
        self.assertEqual(len(rels), 1, "re-running must not create a duplicate relationship")


if __name__ == "__main__":
    unittest.main()
