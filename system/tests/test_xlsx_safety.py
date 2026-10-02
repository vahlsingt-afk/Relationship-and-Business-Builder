"""
test_xlsx_safety.py

RB-SECURITY-2026-09-05: the 2026-09-04 security audit fixed formula/CSV
injection (CWE-1236) in Blue Sheet exports only -- the one confirmed
customer-facing artifact at the time -- leaving ~20 other xlsx-writing
scripts unreviewed. This module extracts the same guard as a shared
primitive so every other writer gets it too. Covers the primitive itself;
see test_xlsx_safety_writers.py for per-script wiring checks.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import xlsx_safety  # noqa: E402


class TestSanitizeCellValue(unittest.TestCase):
    def test_leaves_normal_string_untouched(self):
        self.assertEqual(xlsx_safety.sanitize_cell_value("Pollo Campero"), "Pollo Campero")

    def test_prefixes_equals_sign(self):
        self.assertEqual(xlsx_safety.sanitize_cell_value("=1+1"), "'=1+1")

    def test_prefixes_plus_sign(self):
        self.assertEqual(xlsx_safety.sanitize_cell_value("+cmd|' /C calc'!A1"), "'+cmd|' /C calc'!A1")

    def test_prefixes_minus_sign(self):
        self.assertEqual(xlsx_safety.sanitize_cell_value("-2+3"), "'-2+3")

    def test_prefixes_at_sign(self):
        self.assertEqual(xlsx_safety.sanitize_cell_value("@SUM(A1:A2)"), "'@SUM(A1:A2)")

    def test_prefixes_leading_tab_and_cr(self):
        self.assertEqual(xlsx_safety.sanitize_cell_value("\t=1+1"), "'\t=1+1")
        self.assertEqual(xlsx_safety.sanitize_cell_value("\r=1+1"), "'\r=1+1")

    def test_non_string_values_pass_through(self):
        self.assertEqual(xlsx_safety.sanitize_cell_value(42), 42)
        self.assertEqual(xlsx_safety.sanitize_cell_value(3.5), 3.5)
        self.assertIsNone(xlsx_safety.sanitize_cell_value(None))
        self.assertEqual(xlsx_safety.sanitize_cell_value(True), True)

    def test_empty_string_untouched(self):
        self.assertEqual(xlsx_safety.sanitize_cell_value(""), "")

    def test_trigger_char_mid_string_not_touched(self):
        """Only a LEADING trigger character makes Excel evaluate a cell as
        a formula -- a normal sentence that happens to contain '=' or '-'
        partway through must render unchanged."""
        self.assertEqual(xlsx_safety.sanitize_cell_value("Revenue = $5M"), "Revenue = $5M")


class TestSanitizeRow(unittest.TestCase):
    def test_sanitizes_every_value_in_row(self):
        row = ["Normal", "=HYPERLINK(\"http://evil\")", 42, None]
        out = xlsx_safety.sanitize_row(row)
        self.assertEqual(out, ["Normal", "'=HYPERLINK(\"http://evil\")", 42, None])

    def test_empty_row(self):
        self.assertEqual(xlsx_safety.sanitize_row([]), [])

    def test_preserves_order_and_length(self):
        row = ["a", "=b", "c", "-d", "e"]
        out = xlsx_safety.sanitize_row(row)
        self.assertEqual(len(out), len(row))
        self.assertEqual(out[0], "a")
        self.assertEqual(out[2], "c")
        self.assertEqual(out[4], "e")


if __name__ == "__main__":
    unittest.main()
