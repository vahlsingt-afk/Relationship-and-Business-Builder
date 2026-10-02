"""
customer_artifact.py — RB-2026-09-02.

Structured customer-facing tables (e.g. a clean pricing matrix for an RFP)
attached to a Blue Sheet account -- deliberately separate from both
evidence.jsonl (free-text excerpts/claims, never rendered anywhere) and
render.py's Standard_Blue_Sheet.xlsx (the internal working document, full
of tabs/formulas -- Commercial Model, red flags, buying-influence ratings
-- never meant to reach a customer).

Real gap found live 2026-09-02: Todd asked RBB to "save this as a
customer-facing artifact" for a Pollo Campero DMB pricing matrix. The only
tool available (addBlueSheetEvidence) stored it as markdown text inside an
evidence excerpt -- nothing renders evidence.jsonl into any spreadsheet, so
there was no path from "save this" to the downloadable Excel file he
actually wanted, and no way to hand a customer a table without the risk of
also handing them Todd's internal pricing/red-flag data. This module and
its two API endpoints (see system/api/server.py's
addBlueSheetCustomerArtifact / listBlueSheetCustomerArtifacts) are the real
fix.

Storage: one JSONL record per account
(blue_sheets/accounts/<slug>/customer_facing_artifacts.jsonl) holding the
STRUCTURED table (title/columns/rows) -- never rendered xlsx bytes. The
actual workbook is built fresh on every download
(render_customer_artifact_workbook, called from rbb_chat.py's download
route), matching _local_get_ecosystem_workbook_download_link's in-memory
pattern rather than get_blue_sheet_download's registry-file pattern. That
sidesteps entirely the "registered but missing/stale on disk" bug class
fixed the same day in render.py / blue_sheet_registry.json -- there is no
stored file path here to ever drift out of sync.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as bs_common  # noqa: E402

sys.path.insert(0, str(bs_common.ROOT.parent / "system" / "scripts"))
import xlsx_safety  # noqa: E402


def _artifacts_path(slug: str) -> Path:
    return bs_common.account_dir(slug) / "customer_facing_artifacts.jsonl"


def _next_artifact_id(slug: str, existing: list) -> str:
    n = 0
    for rec in existing:
        aid = rec.get("artifact_id", "")
        if aid.startswith(f"cfa-{slug}-"):
            try:
                n = max(n, int(aid.rsplit("-", 1)[-1]))
            except ValueError:
                pass
    return f"cfa-{slug}-{n + 1:04d}"


def add_customer_artifact(
    slug: str,
    *,
    title: str,
    columns: list,
    rows: list,
    notes: Optional[str] = None,
    source_evidence_id: Optional[str] = None,
) -> dict:
    if not (title or "").strip():
        raise ValueError("title must not be empty")
    if not columns:
        raise ValueError("columns must not be empty")
    if not rows:
        raise ValueError("rows must not be empty")
    for i, row in enumerate(rows):
        if len(row) != len(columns):
            raise ValueError(f"row {i} has {len(row)} cells, expected {len(columns)} to match columns")

    bs_common.account_dir(slug)  # raises FileNotFoundError if slug unknown
    path = _artifacts_path(slug)
    existing = bs_common.load_jsonl(path) if path.exists() else []
    artifact_id = _next_artifact_id(slug, existing)
    record = {
        "artifact_id": artifact_id,
        "account_id": f"acct-{slug}",
        "title": title.strip(),
        "columns": columns,
        "rows": rows,
        "notes": (notes or "").strip(),
        "source_evidence_id": source_evidence_id,
        "created_date": bs_common.today(),
    }
    bs_common.append_jsonl(path, record)
    return {"ok": True, "account_slug": slug, "artifact_id": artifact_id, "title": record["title"]}


def list_customer_artifacts(slug: str) -> list:
    path = _artifacts_path(slug)
    if not path.exists():
        return []
    return [
        {
            "artifact_id": rec["artifact_id"],
            "title": rec["title"],
            "created_date": rec["created_date"],
            "source_evidence_id": rec.get("source_evidence_id"),
            "column_count": len(rec.get("columns", [])),
            "row_count": len(rec.get("rows", [])),
        }
        for rec in bs_common.load_jsonl(path)
    ]


def get_customer_artifact(slug: str, artifact_id: str) -> Optional[dict]:
    path = _artifacts_path(slug)
    if not path.exists():
        return None
    for rec in bs_common.load_jsonl(path):
        if rec.get("artifact_id") == artifact_id:
            return rec
    return None


def render_customer_artifact_workbook(slug: str, artifact_id: str):
    """Build a fresh, clean single-sheet .xlsx for one customer-facing
    artifact -- never touches disk (caller saves the returned Workbook to
    a BytesIO buffer), matches
    _local_get_ecosystem_workbook_download_link's in-memory pattern.
    Raises FileNotFoundError if the artifact doesn't exist -- same
    exception type common.account_dir() already uses for an unknown slug,
    so callers can handle "not found" uniformly."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    record = get_customer_artifact(slug, artifact_id)
    if record is None:
        raise FileNotFoundError(f"No customer-facing artifact '{artifact_id}' for account '{slug}'")

    columns = record["columns"]
    rows = record["rows"]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Customer Facing"

    # RB-SECURITY-2026-09-05: this is a customer-facing artifact by design
    # (see module docstring) -- title/column names/cell values/notes all
    # come from free-text a chat call supplied, which can carry externally-
    # influenced strings. Same formula/CSV-injection guard (CWE-1236)
    # already applied to Blue Sheets' own render.py.
    ws.cell(row=1, column=1, value=xlsx_safety.sanitize_cell_value(record["title"]))
    ws.cell(row=1, column=1).font = Font(bold=True, size=14)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(1, len(columns)))

    header_row = 3
    header_fill = PatternFill(start_color="FFB8CEE4", end_color="FFB8CEE4", fill_type="solid")
    for col_idx, col_name in enumerate(columns, start=1):
        c = ws.cell(row=header_row, column=col_idx, value=xlsx_safety.sanitize_cell_value(col_name))
        c.font = Font(bold=True)
        c.fill = header_fill
        c.alignment = Alignment(wrap_text=True, vertical="top")

    for row_offset, row_values in enumerate(rows):
        r = header_row + 1 + row_offset
        for col_idx, value in enumerate(row_values, start=1):
            ws.cell(row=r, column=col_idx, value=xlsx_safety.sanitize_cell_value(value))

    if record.get("notes"):
        notes_row = header_row + 1 + len(rows) + 1
        c = ws.cell(row=notes_row, column=1, value=xlsx_safety.sanitize_cell_value(record["notes"]))
        c.font = Font(italic=True)
        ws.merge_cells(start_row=notes_row, start_column=1, end_row=notes_row, end_column=max(1, len(columns)))
        c.alignment = Alignment(wrap_text=True, vertical="top")

    for col_idx in range(1, len(columns) + 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = 22

    return wb, record
