"""
test_blue_sheet_customer_artifact.py

customer_artifact.py had zero test coverage before this -- it's the module
explicitly built to hand structured tables (pricing matrices, etc.) to a
real customer (RB-2026-09-02), and its render function had no formula/
CSV-injection guard (CWE-1236) until RB-SECURITY-2026-09-05, despite being
the single most explicitly customer-facing xlsx writer in RB (more so than
Blue Sheets itself, whose workbook is Todd's internal working document with
a separate, deliberate customer-artifact concept for exactly this reason).
Same tmp-ROOT isolation pattern as test_blue_sheet_add_evidence.py.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "blue_sheets" / "_engine"))

import customer_artifact as ca  # noqa: E402
import common as bs_common  # noqa: E402


class TestCustomerArtifact(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_root = Path(self._tmpdir.name)
        self._orig_root = bs_common.CUSTOMERS_PROSPECTS_ROOT
        bs_common.CUSTOMERS_PROSPECTS_ROOT = tmp_root

        (tmp_root / "_portfolio").mkdir(parents=True)
        (tmp_root / "accounts" / "acme").mkdir(parents=True)
        registry = {"registry": [{"account_id": "acct-acme", "status": "current", "engagement_tier": "active_engagement", "last_evidence_date": "2026-08-01"}]}
        (tmp_root / "_portfolio" / "customers_prospects_registry.json").write_text(json.dumps(registry), encoding="utf-8")

    def tearDown(self):
        bs_common.CUSTOMERS_PROSPECTS_ROOT = self._orig_root
        self._tmpdir.cleanup()

    def test_add_and_render_real_pricing_matrix(self):
        result = ca.add_customer_artifact(
            "acme", title="DMB Pricing", columns=["Item", "Price"],
            rows=[["Menu Board", "$1,200"], ["Install", "$400"]],
            notes="Pricing valid through Q4.",
        )
        self.assertTrue(result["ok"])
        wb, _rec = ca.render_customer_artifact_workbook("acme", result["artifact_id"])
        ws = wb.active
        self.assertEqual(ws.cell(row=1, column=1).value, "DMB Pricing")
        self.assertEqual(ws.cell(row=3, column=1).value, "Item")
        self.assertEqual(ws.cell(row=4, column=2).value, "$1,200")

    def test_missing_artifact_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            ca.render_customer_artifact_workbook("acme", "cfa-acme-9999")

    def test_render_sanitizes_formula_injection_in_title_columns_rows_and_notes(self):
        """RB-SECURITY-2026-09-05: title/columns/row values/notes all come
        from free-text a chat call supplied -- externally-influenceable,
        and this is the one artifact type explicitly designed to be handed
        to a real customer. A formula-trigger-prefixed value anywhere in
        the record must render as literal text, never a live formula."""
        result = ca.add_customer_artifact(
            "acme", title="=1+1", columns=["Item", "-2+2"],
            rows=[["=HYPERLINK(\"http://evil\")", "@SUM(A1:A2)"]],
            notes="+3 free consulting hours",
        )
        wb, _rec = ca.render_customer_artifact_workbook("acme", result["artifact_id"])
        ws = wb.active
        all_values = [cell.value for row in ws.iter_rows() for cell in row if cell.value is not None]
        self.assertIn("'=1+1", all_values)
        self.assertIn("'-2+2", all_values)
        self.assertIn("'=HYPERLINK(\"http://evil\")", all_values)
        self.assertIn("'@SUM(A1:A2)", all_values)
        self.assertIn("'+3 free consulting hours", all_values)
        for raw in ("=1+1", "-2+2", "=HYPERLINK(\"http://evil\")", "@SUM(A1:A2)", "+3 free consulting hours"):
            self.assertNotIn(raw, all_values, f"unsanitized value leaked through: {raw!r}")


if __name__ == "__main__":
    unittest.main()
