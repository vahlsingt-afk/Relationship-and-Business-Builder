"""
test_xlsx_safety_writers.py

RB-SECURITY-2026-09-05: the 2026-09-04 audit fixed formula/CSV injection
(CWE-1236) in Blue Sheet exports only. Roadmap follow-up: review and fix
the ~20 other scripts that write .xlsx via openpyxl. Narrowed to the real
writers (most of the ~20 only ever call openpyxl.load_workbook(read_only=
True) -- no write risk at all). Each of those writers got its own local
`_safe_append`/`_set_cell` wrapper around the shared xlsx_safety.py
primitive (already unit-tested in test_xlsx_safety.py) instead of writing
cells directly. This file locks in that each wrapper is actually wired --
calls the real shared sanitizer, not a no-op copy -- without needing a full
fixture for every script's real data pipeline.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import competitive_landscape_export as cle  # noqa: E402
import ecosystem_export as eco  # noqa: E402


class FakeWorksheet:
    """Bare-minimum stand-in for an openpyxl Worksheet -- just records what
    it was appended, so each script's wrapper can be tested without needing
    a real Workbook or that script's full data pipeline."""
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(list(row))


class TestPerScriptSafeAppendWrappers(unittest.TestCase):
    """campaign_engine.py's own wrapper is a local closure inside
    export_campaign_workbook() (the only function in that file that writes
    xlsx), so it has no module-level name to unit-test directly here --
    see test_campaign_engine.py::test_export_campaign_workbook_sanitizes_
    formula_injection for its full end-to-end coverage instead."""

    def test_competitive_landscape_export_safe_append_sanitizes(self):
        ws = FakeWorksheet()
        cle._safe_append(ws, ["Normal", "=1+1", 42])
        self.assertEqual(ws.rows, [["Normal", "'=1+1", 42]])

    def test_ecosystem_export_safe_append_sanitizes(self):
        ws = FakeWorksheet()
        eco._safe_append(ws, ["Normal", "-2+2", None])
        self.assertEqual(ws.rows, [["Normal", "'-2+2", None]])


if __name__ == "__main__":
    unittest.main()
