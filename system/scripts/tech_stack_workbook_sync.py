#!/usr/bin/env python3
"""
tech_stack_workbook_sync.py — two-way bridge between RB's own
ecosystem_intelligence.json vendor-relationship graph and Todd's shared
"Restaurant Tech Coverage" workbook (the canonical ~1500-brand tech-stack
tracker he shares with teammates for field intelligence gathering).

RB-2026-08-29. Two directions, kept as separate, explicit commands rather
than one "sync" that guesses which way data should flow:

  backfill   RB graph -> workbook. Fills BLANK cells in the workbook's
             "Canonical Tech Stack" sheet from RB's own already-known
             vendor relationships. Never overwrites a cell that already has
             data -- a human's prior work (or a teammate's field research)
             always wins over RB's own graph. Writes to a NEW file; never
             overwrites Todd's original in place.

  ingest     Workbook -> RB graph. Reads the workbook's "Evidence Ledger"
             sheet (one row per brand/vendor/product relationship, already
             carrying a real source URL, confidence components, and
             verification status) and reconciles each row against RB's
             graph using the exact same conflict-detection/upsert engine
             `migrate-workbook`/`import-phase2-evidence` already use --
             dry-run by default, same as every other graph-mutating command
             in this module.

Usage:
    python3 tech_stack_workbook_sync.py backfill <path.xlsx> [--out <path.xlsx>]
    python3 tech_stack_workbook_sync.py ingest <path.xlsx> [--confirm]
    python3 tech_stack_workbook_sync.py export-updates <path.xlsx> [--out <path.xlsx>]
    python3 tech_stack_workbook_sync.py import-updates <master.xlsx> <updates.xlsx> [--out <path.xlsx>]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import ecosystem_intelligence as eco  # noqa: E402
import xlsx_safety  # noqa: E402

try:
    import openpyxl
except ImportError:
    openpyxl = None

# Workbook "Canonical Tech Stack" column -> RB relationship category.
# RB-2026-09-01: the 6 previously-unmapped columns (POS Hardware,
# Drive-Thru Timers, AI Solution 1/2, Broadband/Network, In-Restaurant
# Media) are now mapped -- Todd's own explicit direction, built for the
# competitive_landscape.py market-share/battle-card analysis, which needed
# every real workbook column represented. `category` is a free-form string
# in the schema (no enum to migrate) and _norm_category() already
# normalizes each of these column names to exactly the slug used below --
# confirmed live, no alias-table change needed in ecosystem_intelligence.py.
_COLUMN_TO_CATEGORY: dict[str, str] = {
    "POS": "pos",
    "POS Hardware": "pos_hardware",
    "Back Office": "back_office_operations",
    "Payments": "payments",
    "Loyalty": "loyalty",
    "Online Ordering": "online_ordering",
    "KDS": "kds_kitchen_ops",
    "Labor / Workforce": "labor_workforce",
    "Inventory / Supply Chain": "inventory",
    "Accounting": "back_office_accounting",
    "Kiosks": "kiosks",
    "Menu Management": "menu_management",
    # RB has no "voice AI" category distinct from drive-thru AI ordering --
    # closest real match, flagged in every backfilled cell's note so a
    # reviewer can judge the approximation themselves rather than trust it
    # silently.
    "Voice AI": "drive_thru_ai",
    "Computer Vision": "computer_vision_robotics",
    "Ops Execution": "ops_execution",
    "Training / LMS": "training_lms",
    "BI / Analytics": "bi_analytics",
    "Unified Commerce": "unified_commerce",
    "Digital Menu Boards": "digital_menu_boards",
    "Drive-Thru Timers": "drive_thru_timers",
    "AI Solution 1": "ai_solution_1",
    "AI Solution 2": "ai_solution_2",
    "Broadband / Network": "broadband_network",
    "In-Restaurant Media": "in_restaurant_media",
}

_HEADER_ROW = 4
_FIRST_DATA_ROW = 5
_SHEET = "Canonical Tech Stack"
_UPDATES_SHEET = "Brand Technology Updates"
_FIELD_MAP_SHEET = "Field Map"
_COMPANY_COL = 1
_LAST_VERIFIED_COL = 52
_EVIDENCE_SOURCES_COL = 53
_RESEARCH_STATE_COL = 55
_NOTES_COL = 56

_CONFIDENCE_LEVEL_TO_SCORE = {"high": 0.9, "medium": 0.7, "low": 0.4}
_UPDATES_HEADER_ROW = 5
_UPDATES_FIRST_DATA_ROW = 6
_UPDATES_TEMPLATE_ROW = 6
_UPDATES_LAST_COL = 14
_USER_EDITABLE_UPDATE_COLS = (2, 3, 6, 7, 8, 9, 10, 11, 12)  # B,C,F:L except formulas D/E


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _build_brand_index(graph: dict) -> dict[str, dict]:
    """norm(name-or-alias) -> brand entity, for every brand entity in the
    graph. A name colliding across two entities keeps the first (rare --
    real duplicate-entity collisions are a known, separately-tracked
    class of defect, not something this read-only backfill should try to
    resolve)."""
    index: dict[str, dict] = {}
    for ent in graph.get("entities") or []:
        if ent.get("entity_type") != "brand":
            continue
        names = [ent.get("name") or ""] + list(ent.get("aliases") or [])
        for n in names:
            key = _norm(n)
            if key and key not in index:
                index[key] = ent
    return index


def _active_relationships_by_category(graph: dict, entity_id: str) -> dict[str, list[dict]]:
    by_cat: dict[str, list[dict]] = {}
    for rel in graph.get("relationships") or []:
        if rel.get("from_entity_id") != entity_id or rel.get("status") != "active":
            continue
        by_cat.setdefault(rel.get("category"), []).append(rel)
    return by_cat


def _relationship_display(rel: dict) -> str:
    """The string to write into a workbook stack cell -- product name if
    known, else the vendor's own name, matching row 5's own convention
    (e.g. "NEWPOS", "Fiserv")."""
    return rel.get("product") or rel.get("to_entity_id", "").removeprefix("vendor-").replace("-", " ").title()


def backfill(workbook_path: Path, out_path: Path) -> dict:
    if openpyxl is None:
        raise RuntimeError("openpyxl is required (pip install openpyxl)")

    graph = eco._read_graph()
    brand_index = _build_brand_index(graph)

    wb = openpyxl.load_workbook(workbook_path, data_only=False)
    ws = wb[_SHEET]
    headers = {ws.cell(_HEADER_ROW, c).value: c for c in range(1, ws.max_column + 1)}

    col_pairs: list[tuple[int, int, str]] = []  # (stack_col, confidence_col, category)
    for wb_col_name, category in _COLUMN_TO_CATEGORY.items():
        if wb_col_name not in headers:
            continue
        stack_col = headers[wb_col_name]
        conf_col = stack_col + 1  # every stack column is immediately followed by its confidence column
        col_pairs.append((stack_col, conf_col, category))

    companies_matched = 0
    companies_unmatched: list[str] = []
    cells_filled = 0
    rows_touched = 0

    for r in range(_FIRST_DATA_ROW, ws.max_row + 1):
        company = ws.cell(r, _COMPANY_COL).value
        if not company:
            continue
        ent = brand_index.get(_norm(company))
        if ent is None:
            companies_unmatched.append(company)
            continue
        companies_matched += 1

        rels_by_cat = _active_relationships_by_category(graph, ent["id"])
        if not rels_by_cat:
            continue

        row_filled = 0
        filled_categories: list[str] = []
        for stack_col, conf_col, category in col_pairs:
            rels = rels_by_cat.get(category)
            if not rels:
                continue
            existing = ws.cell(r, stack_col).value
            if existing not in (None, ""):
                continue  # never overwrite existing work
            rel = rels[0]  # first active relationship for this category
            # RB-SECURITY-2026-09-05: _relationship_display(rel) can carry a
            # vendor/brand name sourced from ecosystem_intelligence.json,
            # itself ultimately sourced from external research -- same
            # formula/CSV-injection guard (CWE-1236) already applied to
            # Blue Sheets. This is a data write, not one of this file's
            # intentional formula cells (see import_updates() below), so it
            # is safe to sanitize.
            ws.cell(r, stack_col).value = xlsx_safety.sanitize_cell_value(_relationship_display(rel))
            level = (rel.get("confidence") or {}).get("level", "medium")
            ws.cell(r, conf_col).value = _CONFIDENCE_LEVEL_TO_SCORE.get(level, 0.7)
            row_filled += 1
            filled_categories.append(category)

        if row_filled:
            cells_filled += row_filled
            rows_touched += 1
            existing_notes = ws.cell(r, _NOTES_COL).value or ""
            backfill_note = (
                f"RB backfill {datetime.now(timezone.utc).date().isoformat()}: "
                f"{row_filled} field(s) from RB's own graph "
                f"({', '.join(filled_categories)}) -- verify before treating as field-confirmed."
            )
            ws.cell(r, _NOTES_COL).value = xlsx_safety.sanitize_cell_value(
                f"{existing_notes} | {backfill_note}" if existing_notes else backfill_note
            )
            if ws.cell(r, _RESEARCH_STATE_COL).value == "Research gap":
                ws.cell(r, _RESEARCH_STATE_COL).value = "RB backfill — verify"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)

    return {
        "workbook": str(workbook_path),
        "output": str(out_path),
        "graph_brands_total": sum(1 for e in graph.get("entities") or [] if e.get("entity_type") == "brand"),
        "workbook_companies_matched_to_graph": companies_matched,
        "workbook_companies_unmatched": len(companies_unmatched),
        "unmatched_sample": companies_unmatched[:15],
        "rows_touched": rows_touched,
        "cells_filled": cells_filled,
        "mapped_categories": sorted(set(_COLUMN_TO_CATEGORY.values())),
        # RB-2026-09-01: all real workbook columns are now mapped (see
        # _COLUMN_TO_CATEGORY's docstring comment) -- kept as an explicit
        # empty list, not removed, so a future new workbook column that
        # genuinely has no mapping yet has an obvious place to be listed
        # again rather than silently falling through unmapped.
        "unmapped_workbook_columns": [],
    }


def _cell_has_content(ws, row: int, cols: tuple[int, ...]) -> bool:
    return any(ws.cell(row, c).value not in (None, "") for c in cols)


def _copy_cell_style(src, dst) -> None:
    from copy import copy

    if src.has_style:
        dst.font = copy(src.font)
        dst.fill = copy(src.fill)
        dst.border = copy(src.border)
        dst.alignment = copy(src.alignment)
        dst.protection = copy(src.protection)
        dst.number_format = src.number_format


def _sorted_brand_names(wb) -> list[str]:
    ws = wb[_SHEET]
    names = [ws.cell(r, _COMPANY_COL).value for r in range(_FIRST_DATA_ROW, ws.max_row + 1)]
    return sorted({str(n).strip() for n in names if str(n or "").strip()}, key=str.casefold)


def _tech_fields(wb) -> list[str]:
    if _FIELD_MAP_SHEET in wb.sheetnames:
        ws = wb[_FIELD_MAP_SHEET]
        fields = [ws.cell(r, 1).value for r in range(2, ws.max_row + 1)]
        return [str(f).strip() for f in fields if str(f or "").strip()]
    ws = wb[_SHEET]
    fields: list[str] = []
    for c in range(1, ws.max_column + 1):
        h = ws.cell(_HEADER_ROW, c).value
        if h and "Confidence" not in str(h) and h not in {"Company", "Units", "Last Verified", "Evidence Sources", "Research State", "Notes"}:
            fields.append(str(h))
    return fields


def _canonical_lookup_maps(wb) -> tuple[dict[str, int], dict[str, int], dict[str, str]]:
    canon = wb[_SHEET]
    headers = {canon.cell(_HEADER_ROW, c).value: c for c in range(1, canon.max_column + 1)}
    brands = {
        str(canon.cell(r, _COMPANY_COL).value).strip(): r
        for r in range(_FIRST_DATA_ROW, canon.max_row + 1)
        if str(canon.cell(r, _COMPANY_COL).value or "").strip()
    }
    confidence_field = {}
    if _FIELD_MAP_SHEET in wb.sheetnames:
        fmap = wb[_FIELD_MAP_SHEET]
        for r in range(2, fmap.max_row + 1):
            field = fmap.cell(r, 1).value
            conf = fmap.cell(r, 2).value
            if field and conf:
                confidence_field[str(field).strip()] = str(conf).strip()
    return headers, brands, confidence_field


def _current_stack_values(wb, brand: str, field: str) -> tuple[object, object]:
    headers, brands, confidence_field = _canonical_lookup_maps(wb)
    row = brands.get(str(brand or "").strip())
    field_col = headers.get(field)
    conf_col = headers.get(confidence_field.get(field))
    if not row:
        return None, None
    current = wb[_SHEET].cell(row, field_col).value if field_col else None
    confidence = wb[_SHEET].cell(row, conf_col).value if conf_col else None
    return current, confidence


def export_updates(workbook_path: Path, out_path: Path, *, max_rows: int = 500) -> dict:
    """Create a lightweight workbook for field/user edits.

    The exported file intentionally contains only the update queue and hidden
    dropdown lists. It is the safe file to send around: users can propose
    corrections without receiving or editing the canonical table.
    """
    if openpyxl is None:
        raise RuntimeError("openpyxl is required (pip install openpyxl)")

    from copy import copy
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Protection
    from openpyxl.worksheet.datavalidation import DataValidation

    src_wb = openpyxl.load_workbook(workbook_path, data_only=False)
    src_ws = src_wb[_UPDATES_SHEET]

    out_wb = Workbook()
    ws = out_wb.active
    ws.title = _UPDATES_SHEET

    # Copy the visible update sheet surface and existing user-entered rows.
    # RB-SECURITY-2026-09-05: deliberately NOT sanitized here, unlike the
    # rest of this file's writes -- column 1 of every data row legitimately
    # holds a live "=IF(...)" BTU-id formula (written by import_updates(),
    # below), copied verbatim by the `else` branch along with the header
    # row. xlsx_safety.sanitize_cell_value() would prefix a leading
    # apostrophe onto that real formula and silently break auto-numbering.
    # `current`/`confidence` (columns 4/5) are excluded from user editing by
    # design and are themselves already-sanitized values re-read from the
    # master (see sync_workbook() above), not fresh external input.
    rows_to_copy = min(src_ws.max_row, max_rows)
    for r in range(1, rows_to_copy + 1):
        ws.row_dimensions[r].height = src_ws.row_dimensions[r].height
        for c in range(1, min(src_ws.max_column, _UPDATES_LAST_COL) + 1):
            src = src_ws.cell(r, c)
            dst = ws.cell(r, c)
            if r >= _UPDATES_FIRST_DATA_ROW and c in (4, 5):
                current, confidence = _current_stack_values(
                    src_wb, src_ws.cell(r, 2).value, src_ws.cell(r, 3).value
                )
                dst.value = current if c == 4 else confidence
            else:
                dst.value = src.value
            _copy_cell_style(src, dst)
    for c in range(1, _UPDATES_LAST_COL + 1):
        letter = openpyxl.utils.get_column_letter(c)
        ws.column_dimensions[letter].width = src_ws.column_dimensions[letter].width or 16
    for merged in src_ws.merged_cells.ranges:
        if merged.max_row <= rows_to_copy and merged.max_col <= _UPDATES_LAST_COL:
            ws.merge_cells(str(merged))

    lists = out_wb.create_sheet("_Lists")
    lists.sheet_state = "hidden"
    lists["A1"] = "Brands"
    for i, brand in enumerate(_sorted_brand_names(src_wb), 2):
        lists.cell(i, 1, xlsx_safety.sanitize_cell_value(brand))
    lists["B1"] = "Technology Fields"
    for i, field in enumerate(_tech_fields(src_wb), 2):
        lists.cell(i, 2, xlsx_safety.sanitize_cell_value(field))
    statuses = ["Draft", "Ready for Review", "Approved", "Rejected", "Processed"]
    lists["C1"] = "Review Status"
    for i, status in enumerate(statuses, 2):
        lists.cell(i, 3, status)

    brand_end = max(2, len(_sorted_brand_names(src_wb)) + 1)
    field_end = max(2, len(_tech_fields(src_wb)) + 1)
    status_end = len(statuses) + 1
    last_input_row = max(max_rows, _UPDATES_FIRST_DATA_ROW + 250)
    validations = [
        (f"=_Lists!$A$2:$A${brand_end}", f"B{_UPDATES_FIRST_DATA_ROW}:B{last_input_row}"),
        (f"=_Lists!$B$2:$B${field_end}", f"C{_UPDATES_FIRST_DATA_ROW}:C{last_input_row}"),
        (f"=_Lists!$C$2:$C${status_end}", f"L{_UPDATES_FIRST_DATA_ROW}:L{last_input_row}"),
    ]
    for formula, target in validations:
        dv = DataValidation(type="list", formula1=formula, allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(target)

    # Keep formula columns locked; leave user-entry columns editable.
    for row in ws.iter_rows():
        for cell in row:
            cell.protection = Protection(locked=True)
    for r in range(_UPDATES_FIRST_DATA_ROW, last_input_row + 1):
        for c in _USER_EDITABLE_UPDATE_COLS:
            ws.cell(r, c).protection = Protection(locked=False)
        ws.cell(r, 7).number_format = "0%"
    ws.protection.sheet = True
    ws.protection.enable()

    ws["A2"] = "Send this lightweight update workbook to contributors. Edit only unlocked cells in columns B, C, and F:L; import it back into the master workbook for review."
    ws["A2"].font = Font(name="Arial", italic=True)
    ws["A2"].fill = PatternFill("solid", fgColor="FFF2CC")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_wb.save(out_path)
    return {
        "source_workbook": str(workbook_path),
        "output": str(out_path),
        "sheet": _UPDATES_SHEET,
        "brand_dropdown_count": len(_sorted_brand_names(src_wb)),
        "field_dropdown_count": len(_tech_fields(src_wb)),
        "editable_columns": ["B", "C", "F", "G", "H", "I", "J", "K", "L"],
    }


def import_updates(master_path: Path, updates_path: Path, out_path: Path, *, max_rows: int = 500) -> dict:
    """Merge a lightweight Brand Technology Updates workbook into master.

    Only user-entry fields are copied back. Formula/current-value columns are
    regenerated from the master template so stale uploaded formulas cannot
    corrupt the queue.
    """
    if openpyxl is None:
        raise RuntimeError("openpyxl is required (pip install openpyxl)")

    from copy import copy
    from openpyxl.styles import Protection

    master_wb = openpyxl.load_workbook(master_path, data_only=False)
    update_wb = openpyxl.load_workbook(updates_path, data_only=False)
    master_ws = master_wb[_UPDATES_SHEET]
    update_ws = update_wb[_UPDATES_SHEET]

    # Capture incoming rows that have a brand/field/proposed value/source/status.
    incoming: list[dict[int, object]] = []
    for r in range(_UPDATES_FIRST_DATA_ROW, min(update_ws.max_row, max_rows) + 1):
        if not _cell_has_content(update_ws, r, _USER_EDITABLE_UPDATE_COLS):
            continue
        incoming.append({c: update_ws.cell(r, c).value for c in _USER_EDITABLE_UPDATE_COLS})

    # Clear old data area and restore template styles/formulas row by row.
    end_row = max(master_ws.max_row, _UPDATES_FIRST_DATA_ROW + len(incoming) + 25)
    for r in range(_UPDATES_FIRST_DATA_ROW, end_row + 1):
        for c in range(1, _UPDATES_LAST_COL + 1):
            src = master_ws.cell(_UPDATES_TEMPLATE_ROW, c)
            dst = master_ws.cell(r, c)
            dst.value = None
            _copy_cell_style(src, dst)
            dst.protection = copy(src.protection)

    for i, row_values in enumerate(incoming, _UPDATES_FIRST_DATA_ROW):
        master_ws.cell(i, 1).value = f'=IF(AND($B{i}<>"",$C{i}<>""),"BTU-"&TEXT(ROW()-5,"0000"),"")'
        master_ws.cell(i, 4).value = (
            f"=IFERROR(INDEX('{_SHEET}'!$A$4:$BF$1504,"
            f"MATCH($B{i},'{_SHEET}'!$A$4:$A$1504,0),"
            f"MATCH($C{i},'{_SHEET}'!$A$4:$BF$4,0)),\"\")"
        )
        master_ws.cell(i, 5).value = (
            f"=IFERROR(INDEX('{_SHEET}'!$A$4:$BF$1504,"
            f"MATCH($B{i},'{_SHEET}'!$A$4:$A$1504,0),"
            f"MATCH(VLOOKUP($C{i},'{_FIELD_MAP_SHEET}'!$A$2:$B$26,2,FALSE),"
            f"'{_SHEET}'!$A$4:$BF$4,0)),\"\")"
        )
        # RB-SECURITY-2026-09-05: the highest-risk write in this file --
        # row_values (built from _USER_EDITABLE_UPDATE_COLS only, never the
        # formula columns 1/4/5 regenerated above) is real contributor
        # free-text flowing back into the canonical master workbook. Same
        # formula/CSV-injection guard (CWE-1236) already applied to Blue
        # Sheets.
        for c, value in row_values.items():
            master_ws.cell(i, c).value = xlsx_safety.sanitize_cell_value(value)
        master_ws.cell(i, 7).number_format = "0%"
        for c in range(1, _UPDATES_LAST_COL + 1):
            master_ws.cell(i, c).protection = Protection(locked=True)
        for c in _USER_EDITABLE_UPDATE_COLS:
            master_ws.cell(i, c).protection = Protection(locked=False)

    master_ws.protection.sheet = True
    master_ws.protection.enable()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    master_wb.save(out_path)
    return {
        "master_workbook": str(master_path),
        "updates_workbook": str(updates_path),
        "output": str(out_path),
        "rows_imported": len(incoming),
        "canonical_touched": False,
        "imported_columns": ["B", "C", "F", "G", "H", "I", "J", "K", "L"],
    }


# The "Evidence Ledger" sheet uses different header text than the
# "Vendor Customer Lists" sheet reconcile_workbook_row/_brand_hints_by_
# customer_name/_workbook_source_assertion were all written for (see their
# own docstrings). Rather than fork that proven conflict-detection/upsert
# engine for a second sheet shape, remap each row's keys once, up front, so
# every downstream call sees the exact shape it already expects.
_EVIDENCE_LEDGER_KEY_MAP = {
    "brand": "customer",
    "confidence_score": "confidence",
    "lifecycle_current_state_flag": "deployment_status",
}


def _remap_evidence_ledger_row(row: dict[str, str]) -> dict[str, str]:
    out = dict(row)
    for src_key, dst_key in _EVIDENCE_LEDGER_KEY_MAP.items():
        if src_key in row and dst_key not in row:
            out[dst_key] = row[src_key]
    # _norm_workbook_row_deployment_status also reads "lifecycle_current_state"
    # (no _flag suffix) as an override signal -- Evidence Ledger only has the
    # one column, so both target keys read the same value.
    if "lifecycle_current_state_flag" in row:
        out.setdefault("lifecycle_current_state", row["lifecycle_current_state_flag"])
    return out


def _existing_live_locations(
    graph: dict, row: dict[str, str],
    brand_hints: dict[str, list[tuple[str, str]]] | None = None,
) -> float | None:
    """RB-DEFECT-2026-08-29, found on the first live --confirm run: the
    Evidence Ledger sheet has no "Units"/location-count column at all (a
    genuine schema gap, not something to infer from other columns). Feeding
    reconcile_workbook_row a row with no units produced a source_assertion
    with live_locations=None whose *content* (correctly) differs from the
    existing assertion for the same source_id (which does carry a real
    count) -- _assertion_key()'s dedup is working exactly as designed (a
    source genuinely correcting itself IS a new assertion, not a
    duplicate), but this wasn't a real correction, just a missing column.
    Confirmed live: 53 of 54 touched relationships picked up a redundant,
    strictly-worse duplicate source_assertion this way; rolled back
    immediately. Fix: when the row has no units of its own, inherit the
    most recent real (non-null) live_locations already on file for the
    same brand+vendor+category, so re-asserting already-known evidence
    doesn't read as a correction."""
    customer = row.get("customer") or ""
    # RB-DEFECT-2026-08-29 (same run, second bug in this one function): a
    # plain exact-name/alias index isn't enough on its own -- the graph has
    # a known, pre-existing duplicate-entity problem ("Checkers" and
    # "Checkers & Rally's" are two separate, undeduped brand entities; see
    # the vendor-first baseline project's entity-dedup notes for the same
    # class of issue on the vendor side). `brand_index.get()` matched
    # "Checkers" exactly -- to the wrong, empty duplicate -- so `ent is
    # None` never even triggered a fallback. Always resolve through
    # _resolve_brand_entity_id with the same whole-sheet continuity hints
    # reconcile_workbook_row itself uses, so this lookup can never disagree
    # with what the real write path will actually resolve the brand to.
    # Only trust a match that already exists (is_new=False) -- never invent
    # an id here just to look something up.
    hints = (brand_hints or {}).get(_norm(customer)) or [(row.get("vendor") or "", eco._norm_category(row.get("tech_category")) or "")]
    entity_id, is_new = eco._resolve_brand_entity_id(customer, graph, vendor_category_hints=hints)
    ent = None
    if entity_id and not is_new:
        ent = next((e for e in graph.get("entities") or [] if e["id"] == entity_id), None)
    if ent is None:
        return None
    vendor_id = f"vendor-{eco._slug(row.get('vendor') or '')}"
    category = eco._norm_category(row.get("tech_category"))
    for rel in graph.get("relationships") or []:
        if rel.get("from_entity_id") != ent["id"] or rel.get("to_entity_id") != vendor_id:
            continue
        if rel.get("category") != category:
            continue
        for assertion in reversed(rel.get("source_assertions") or []):
            if assertion.get("live_locations") is not None:
                return assertion["live_locations"]
    return None


def ingest(workbook_path: Path, *, confirm: bool) -> dict:
    graph = eco._read_graph()
    by_id = eco._index_by_id(graph.get("entities") or [])

    raw_rows = eco.load_rows(workbook_path, sheet_name="Evidence Ledger", required_header_keys=("vendor", "brand"))
    rows = [_remap_evidence_ledger_row(r) for r in raw_rows]
    brand_hints = eco._brand_hints_by_customer_name(rows)
    for row in rows:
        if not row.get("units"):
            inherited = _existing_live_locations(graph, row, brand_hints=brand_hints)
            if inherited is not None:
                row["units"] = str(inherited)
    rows.sort(key=lambda r: 0 if eco._norm_workbook_row_deployment_status(r) in eco._HISTORICAL_DEPLOYMENT_STATUSES else 1)

    outcomes: dict[str, list[dict]] = {name: [] for name in eco.RECONCILIATION_OUTCOMES}
    added = updated = conflicts_detected = auto_superseded = 0

    existing_rels_by_id = eco._index_by_id(graph.get("relationships") or [])

    for row in rows:
        outcome, relationship, _assertion = eco.reconcile_workbook_row(row, graph, by_id, brand_hints=brand_hints)
        # RB-DEFECT-2026-08-29, same live --confirm run: Evidence Ledger's
        # "Relationship Scope" text doesn't always carry the specific role
        # language _infer_workbook_vendor_role looks for (e.g. "reseller"),
        # so it correctly falls through to the generic "unknown" default --
        # but _upsert_relationship's shallow merge treats "unknown" as a
        # real, non-null value that overwrites whatever was already known.
        # Confirmed live: 4 relationships lost an already-specific
        # vendor_role (hardware_reseller_service_provider,
        # franchisee_deployment, pilot) this way. "unknown" here means "this
        # row carries no new signal," not "overwrite with unknown" -- inherit
        # the existing value instead when that's the case.
        if relationship is not None and relationship.get("vendor_role") == "unknown":
            existing_rel = existing_rels_by_id.get(relationship["id"])
            if existing_rel and existing_rel.get("vendor_role") not in (None, "unknown"):
                relationship["vendor_role"] = existing_rel["vendor_role"]
        outcomes[outcome].append({
            "vendor": row.get("vendor"), "brand": row.get("customer"),
            "tech_category": row.get("tech_category"),
            "relationship_id": relationship.get("id") if relationship else None,
        })
        if relationship is None:
            continue
        by_id = eco._index_by_id(graph.get("entities") or [])
        if confirm:
            eco._upsert_entity(graph, {
                "id": relationship["from_entity_id"], "name": row.get("customer") or relationship["from_entity_id"],
                "entity_type": "brand", "subtype": "restaurant_brand", "status": "active",
                "domains": ["restaurants"], "aliases": [], "attributes": {}, "sources": relationship["sources"],
                "confidence": eco._confidence("medium", "Observed in tech-stack workbook ingest."),
                "notes": "", "created_at": eco._now(), "updated_at": eco._now(), "ticker": None,
            })
            eco._upsert_entity(graph, {
                "id": relationship["to_entity_id"], "name": row.get("vendor") or relationship["to_entity_id"],
                "entity_type": "vendor", "subtype": "restaurant_technology_vendor", "status": "active",
                "domains": ["restaurants"], "aliases": [], "attributes": {"primary_category": relationship["category"]},
                "sources": relationship["sources"], "confidence": eco._confidence("medium", "Observed in tech-stack workbook ingest."),
                "notes": "", "created_at": eco._now(), "updated_at": eco._now(), "ticker": None,
            })
            by_id = eco._index_by_id(graph.get("entities") or [])
        outcome_result = eco.resolve_and_upsert_relationship(graph, relationship, by_id=by_id)
        if outcome_result["conflict"]["conflict"]:
            conflicts_detected += 1
            if outcome_result["conflict"]["resolution"] == "auto_superseded":
                auto_superseded += 1
        if outcome_result["added"]:
            added += 1
        else:
            updated += 1

    report = {
        "workbook": str(workbook_path),
        "sheet": "Evidence Ledger",
        "rows_processed": len(rows),
        "dry_run": not confirm,
        "added": added,
        "updated": updated,
        "conflicts_detected": conflicts_detected,
        "auto_superseded": auto_superseded,
        "outcome_counts": {name: len(items) for name, items in outcomes.items()},
        "outcomes": outcomes,
    }

    core.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = core.SNAPSHOTS_DIR / f"tech_stack_workbook_ingest_report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    report_path.write_text(json.dumps(report, indent=2))

    if confirm:
        eco._write_graph(graph)

    report["report_path"] = str(report_path)
    return report


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)

    p_backfill = sub.add_parser("backfill")
    p_backfill.add_argument("path")
    p_backfill.add_argument("--out", default=None)

    p_ingest = sub.add_parser("ingest")
    p_ingest.add_argument("path")
    p_ingest.add_argument("--confirm", action="store_true")

    p_export = sub.add_parser("export-updates")
    p_export.add_argument("path")
    p_export.add_argument("--out", default=None)

    p_import = sub.add_parser("import-updates")
    p_import.add_argument("master_path")
    p_import.add_argument("updates_path")
    p_import.add_argument("--out", default=None)

    args = p.parse_args()

    if args.command == "backfill":
        in_path = Path(args.path)
        if args.out:
            out_path = Path(args.out)
        else:
            out_path = in_path.with_name(f"{in_path.stem}_rb-backfilled{in_path.suffix}")
        result = backfill(in_path, out_path)
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "ingest":
        result = ingest(Path(args.path), confirm=args.confirm)
        print(json.dumps({k: v for k, v in result.items() if k != "outcomes"}, indent=2))
        return 0

    if args.command == "export-updates":
        in_path = Path(args.path)
        out_path = Path(args.out) if args.out else in_path.with_name(f"{in_path.stem}_updates-only.xlsx")
        result = export_updates(in_path, out_path)
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "import-updates":
        master_path = Path(args.master_path)
        updates_path = Path(args.updates_path)
        out_path = Path(args.out) if args.out else master_path.with_name(f"{master_path.stem}_with-imported-updates{master_path.suffix}")
        result = import_updates(master_path, updates_path, out_path)
        print(json.dumps(result, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
