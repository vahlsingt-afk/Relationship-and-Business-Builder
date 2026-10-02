"""xlsx_safety.py — shared formula/CSV-injection guard (CWE-1236).

RB-SECURITY-2026-09-03 fixed this in blue_sheets/_engine/render.py alone
(the one confirmed customer-facing artifact at the time). The 2026-09-04
security audit named ~20 other scripts that also write .xlsx via openpyxl
from data that can include externally-influenced strings (captured
LinkedIn names/headlines, vendor claims, watchlist entity names) as a real,
lower-priority-but-real gap, since none of them were reviewed. Extracted
here as a single shared primitive rather than letting each writer carry
its own (and inevitably drifting) copy -- render.py's own original fix is
still local to keep its call sites unchanged, but now delegates to this
module so there is exactly one place the trigger-char list lives.
"""
from __future__ import annotations

FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell_value(value):
    """Prefix a leading apostrophe on any string starting with a
    formula-trigger character, so Excel/Sheets renders it as literal text
    instead of evaluating it as a formula when the workbook is opened.
    Non-string values pass through untouched."""
    if isinstance(value, str) and value.startswith(FORMULA_TRIGGER_CHARS):
        return "'" + value
    return value


def sanitize_row(values):
    """For the common `ws.append(row)` call shape -- sanitize every value
    in the row, preserving order and length."""
    return [sanitize_cell_value(v) for v in values]
