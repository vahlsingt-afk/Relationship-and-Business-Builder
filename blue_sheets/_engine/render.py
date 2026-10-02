"""
Deterministic Blue Sheet workbook renderer (spec Section 19 item 6, partial).

Regenerates an account's current/*.xlsx from its JSON dossier
(account.json + brand_profile.json + actions.json), starting fresh each time
from _standard/Standard_Blue_Sheet.xlsx so the output is a pure function of
the dossier, not an incremental edit of whatever was there before.

Known limits (see _standard/GAP_REPORT_2026-08-21.md):
- Buying influences beyond the template's fixed 10 rows (19-28) now grow the
  "Blue Sheet" tab via _extend_buying_influence_rows() -- RB-2026-09-02, real
  bug found live: pollo-campero's 11th buying influence raised instead of
  rendering, permanently freezing that account's workbook. See that
  function's docstring for why this needed its own row/merge/height-shift
  logic rather than a bare ws.insert_rows() call.
- Strengths / red-flag / tech-stack / action row counts are still only
  handled up to the number of rows the current Standard_Blue_Sheet.xlsx
  template provides. An account with more rows than the template raises,
  rather than silently truncating data - insert-row support for these
  sections isn't built yet (lower risk than buying influences: none has
  come close to its cap in practice).
- Presentation View 1/2 are regenerated as static literals here (matching the
  reference file's own behavior), not linked formulas - they must be
  regenerated on every render call or they will drift, exactly as flagged
  in the gap report.
- No LibreOffice is available in this environment, so formula cells
  (Blue Sheet!N6:N11, Commercial Model!C12:C13) are written but not
  recalculated here - open in Excel/LibreOffice to see live values.

Usage: python3 render.py <account_slug>
"""
import sys
import shutil
import datetime
from copy import copy
from pathlib import Path

import openpyxl
from openpyxl.cell.cell import MergedCell
from openpyxl.worksheet.cell_range import CellRange

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

sys.path.insert(0, str(common.ROOT.parent / "system" / "scripts"))
import xlsx_safety  # noqa: E402

TEMPLATE = common.ROOT / "_standard" / "Standard_Blue_Sheet.xlsx"

# Buying Influences table on the "Blue Sheet" tab: template rows 19-28 (10
# rows), immediately followed by 2 blank spacer rows (29-30) then the
# SUMMARY OF MY POSITION TODAY / STRENGTHS / RED FLAGS sections. Per-row
# data-column merges (verified against _standard/Standard_Blue_Sheet.xlsx):
# every row 19-28 merges K:N and O:P, nothing else.
_INFLUENCE_TEMPLATE_ROWS = 10
_INFLUENCE_FIRST_ROW = 19
_INFLUENCE_INSERT_AT = _INFLUENCE_FIRST_ROW + _INFLUENCE_TEMPLATE_ROWS  # 29
_INFLUENCE_ROW_COLUMNS = "ABCDEFGHIJKO"
_INFLUENCE_MERGE_COLUMN_PAIRS = [("K", "N"), ("O", "P")]


def _extend_buying_influence_rows(ws, extra: int) -> None:
    """Grow the Buying Influences table by `extra` rows beyond the
    template's fixed 10 (rows 19-28), inserted right after row 28.

    openpyxl's ws.insert_rows() only shifts cell values/styles -- it does
    NOT shift merged-cell ranges or row heights below the insertion point
    (confirmed empirically against openpyxl 3.1.5: cell values move but a
    header's merge stays anchored at its old row while the header text
    moves to the new one, corrupting every section below the insertion
    point). This does the shift insert_rows leaves undone, then styles the
    new rows to match the existing data rows so the extended table isn't
    blank/unformatted. Closes the "row-insertion isn't built yet" gap
    flagged in this module's docstring and
    _standard/GAP_REPORT_2026-08-21.md -- see RB-2026-09-02.
    """
    if extra <= 0:
        return
    insert_at = _INFLUENCE_INSERT_AT

    # Merged ranges at/after the insertion point need to move with their
    # content. None of this template's merges straddle row 29 (verified
    # directly) -- fail loudly rather than silently mis-shifting if a
    # future template edit ever changes that.
    to_shift = []
    for m in list(ws.merged_cells.ranges):
        if m.min_row >= insert_at:
            to_shift.append(str(m))
        elif m.max_row >= insert_at:
            raise ValueError(f"merged range {m} straddles the insertion row {insert_at}; can't safely extend")
    for coord in to_shift:
        ws.unmerge_cells(coord)

    # Row heights below the insertion point, captured before insert_rows
    # (which leaves row_dimensions untouched -- also confirmed directly).
    heights_to_shift = {
        r: ws.row_dimensions[r].height
        for r in range(insert_at, ws.max_row + 1)
        if r in ws.row_dimensions and ws.row_dimensions[r].height is not None
    }

    ws.insert_rows(insert_at, amount=extra)

    for r in sorted(heights_to_shift, reverse=True):
        ws.row_dimensions[r + extra].height = heights_to_shift[r]
        ws.row_dimensions[r].height = None

    for coord in to_shift:
        cr = CellRange(coord)
        cr.shift(0, extra)
        ws.merge_cells(str(cr))

    # Style + re-merge the newly inserted rows to match the last real data
    # row (28), so the extended table looks like a continuation, not a
    # blank gap.
    style_source_row = _INFLUENCE_FIRST_ROW + _INFLUENCE_TEMPLATE_ROWS - 1  # 28
    for i in range(extra):
        r = insert_at + i
        ws.row_dimensions[r].height = ws.row_dimensions[style_source_row].height
        for col in _INFLUENCE_ROW_COLUMNS:
            src = ws[f"{col}{style_source_row}"]
            dst = ws[f"{col}{r}"]
            if src.has_style:
                dst._style = copy(src._style)
        for start_col, end_col in _INFLUENCE_MERGE_COLUMN_PAIRS:
            ws.merge_cells(f"{start_col}{r}:{end_col}{r}")


def _extend_plain_rows(ws, first_extra_row: int, extra: int, style_source_row: int, columns: str) -> None:
    """Extend a tab's data table by `extra` rows starting at
    `first_extra_row`, for the common case where nothing below the
    template's fixed row count needs preserving (no merged ranges, no
    formulas, no other content -- verify that per-tab before calling this;
    _extend_buying_influence_rows and _extend_commercial_model_rows exist
    because Blue Sheet/Commercial Model don't meet that bar). No
    insert_rows() call needed either: openpyxl grows the sheet naturally
    when a cell beyond its current max row is written, so this only needs
    to give the new rows the same height and per-column style as an
    existing data row."""
    if extra <= 0:
        return
    height = ws.row_dimensions[style_source_row].height
    for i in range(extra):
        r = first_extra_row + i
        ws.row_dimensions[r].height = height
        for col in columns:
            src = ws[f"{col}{style_source_row}"]
            dst = ws[f"{col}{r}"]
            if src.has_style:
                dst._style = copy(src._style)


def _extend_commercial_model_rows(ws, extra: int) -> None:
    """Grow the Commercial Model tab's data table (rows 5-11) by `extra`
    rows, inserted right before the two formula rows (12-13: illustrative
    monthly standalone/participation, '=C11*C8' and '=C11*C9'). Those
    formulas reference cells inside the untouched 5-11 range, so their
    text is still correct after the insertion -- only the formula cells'
    own row position needs to move, which is exactly what plain
    ws.insert_rows() does correctly (values/formula text shift; it's only
    merged ranges and row heights it silently leaves behind -- see
    _extend_buying_influence_rows). No merged ranges exist anywhere in
    this tab below its header (verified directly against
    _standard/Standard_Blue_Sheet.xlsx), so unlike that function this only
    needs the row-height shift."""
    if extra <= 0:
        return
    insert_at = 12
    heights_to_shift = {
        r: ws.row_dimensions[r].height
        for r in range(insert_at, ws.max_row + 1)
        if r in ws.row_dimensions and ws.row_dimensions[r].height is not None
    }
    ws.insert_rows(insert_at, amount=extra)
    for r in sorted(heights_to_shift, reverse=True):
        ws.row_dimensions[r + extra].height = heights_to_shift[r]
        ws.row_dimensions[r].height = None
    _extend_plain_rows(ws, insert_at, extra, style_source_row=11, columns="ABCDEFGH")


def set_cell(ws, coord, value):
    # RB-SECURITY-2026-09-03: guards against formula/CSV injection
    # (CWE-1236). Dossier fields can originate from external,
    # attacker-influenceable sources (a captured LinkedIn headline/name, a
    # vendor claim from a press release, etc.). None of the 105 real
    # set_cell() call sites in this file write a literal formula string
    # (verified before adding this), so it is safe to prefix any string
    # value that starts with a formula-trigger character with a leading
    # apostrophe -- Excel/Sheets then render it as literal text instead of
    # evaluating it as a formula when the workbook is opened, including on
    # the customer-facing artifact export path. RB-SECURITY-2026-09-05:
    # moved the actual trigger-char list/logic to xlsx_safety.py, shared
    # with every other xlsx writer in RB -- this call site is unchanged.
    value = xlsx_safety.sanitize_cell_value(value)
    cell = ws[coord]
    if isinstance(cell, MergedCell):
        for rng in ws.merged_cells.ranges:
            if coord in rng:
                ws.cell(row=rng.min_row, column=rng.min_col).value = value
                return
        return
    cell.value = value


def render_blue_sheet_tab(wb, dossier, slug):
    ws = wb["Blue Sheet"]
    acct = dossier["account"]
    opp = acct["opportunities"][0] if acct["opportunities"] else {}
    display_name = acct["display_name"]

    set_cell(ws, "A1", f"{display_name.upper()} — STRATEGIC ANALYSIS")
    set_cell(ws, "A2", f"Executive working account plan | updated {acct['updated_at'][:10]}")
    set_cell(ws, "B5", acct["updated_at"][:10])
    set_cell(ws, "D5", acct["owners"][0]["name"] if acct["owners"] else "")
    set_cell(ws, "E5", opp.get("customer_stated_objective", {}).get("value", ""))
    set_cell(ws, "B6", display_name)
    set_cell(ws, "D6", "U.S. estate")
    set_cell(ws, "B9", opp.get("single_sales_objective", {}).get("value", ""))
    set_cell(ws, "B10", opp.get("commercial_hypothesis", {}).get("value", ""))

    qual = acct.get("qualification", {})
    for i, crit in enumerate(qual.get("criteria", [])):
        r = 6 + i
        set_cell(ws, f"L{r}", crit["answer"])
        set_cell(ws, f"O{r}", crit["current_read"])
        set_cell(ws, f"P{r}", crit["next_step"])

    pos = acct.get("strategic_position", {})
    ep = pos.get("euphoria_panic", {})
    set_cell(ws, "B13", ep.get("current_state", {}).get("value", ""))
    set_cell(ws, "B14", ep.get("reason", {}).get("value", ""))
    set_cell(ws, "B15", ep.get("timing", {}).get("value", ""))
    set_cell(ws, "E13", pos.get("competition", {}).get("value", ""))
    position = pos.get("position", {})
    set_cell(ws, "L13", position.get("place_in_funnel", ""))
    set_cell(ws, "L14", position.get("customer_priority", ""))
    set_cell(ws, "L15", position.get("critical_test", ""))
    set_cell(ws, "O13", position.get("position_vs_competition", {}).get("value", ""))
    set_cell(ws, "O15", position.get("immediate_move", ""))

    influences = acct.get("buying_influences", [])
    extra_influence_rows = max(0, len(influences) - _INFLUENCE_TEMPLATE_ROWS)
    if extra_influence_rows:
        _extend_buying_influence_rows(ws, extra_influence_rows)
    influence_rows = _INFLUENCE_TEMPLATE_ROWS + extra_influence_rows
    for i in range(influence_rows):
        r = _INFLUENCE_FIRST_ROW + i
        if i < len(influences):
            p = influences[i]
            loc = f" — {p['location']}" if p.get("location") and p["location"] != "unverified" else ""
            set_cell(ws, f"A{r}", f"{p['name']} — {p['title']}{loc}")
            set_cell(ws, f"B{r}", p["role_etuc"]["value"])
            set_cell(ws, f"C{r}", p["influence"])
            set_cell(ws, f"D{r}", p["mode"]["value"])
            set_cell(ws, f"E{r}", p["personal_win"]["value"])
            set_cell(ws, f"F{r}", p["business_result"])
            set_cell(ws, f"G{r}", p["competitive_preference"]["value"])
            set_cell(ws, f"H{r}", p["rating"]["value"])
            set_cell(ws, f"I{r}", p["current_read"])
            set_cell(ws, f"J{r}", p["access"])
            set_cell(ws, f"K{r}", p["next_step"])
            set_cell(ws, f"O{r}", p["owner"])
        else:
            for col in "ABCDEFGHIJKO":
                set_cell(ws, f"{col}{r}", None)

    strengths = pos.get("strengths", [])
    for i in range(4):  # rows 33-36, shifted down if buying influences overflowed
        r = 33 + extra_influence_rows + i
        if i < len(strengths):
            s = strengths[i]
            set_cell(ws, f"A{r}", s["value"])
            set_cell(ws, f"E{r}", s["possible_action"])
            set_cell(ws, f"K{r}", s["best_action_plan"])
            set_cell(ws, f"N{r}", s["owner"])
            set_cell(ws, f"P{r}", s["target"])
        else:
            for col in "AEKNP":
                set_cell(ws, f"{col}{r}", None)

    red_flags = pos.get("red_flags", [])
    for i in range(5):  # rows 39-43, shifted down if buying influences overflowed
        r = 39 + extra_influence_rows + i
        if i < len(red_flags):
            rf = red_flags[i]
            set_cell(ws, f"A{r}", rf["value"])
            set_cell(ws, f"E{r}", rf["possible_action"])
            set_cell(ws, f"K{r}", rf["best_action_plan"])
            set_cell(ws, f"N{r}", rf["owner"])
            set_cell(ws, f"P{r}", rf["target"])
        else:
            for col in "AEKNP":
                set_cell(ws, f"{col}{r}", None)

    return influences, pos


def render_brand_tab(wb, dossier, slug, display_name):
    ws = wb["Brand"]
    set_cell(ws, "A1", f"{display_name.upper()} — BRAND PROFILE")
    bp = dossier["brand_profile"]
    rows = []
    label_map = {
        "identity_heritage": "Identity & heritage", "ownership": "Ownership",
        "global_scale": "Global scale", "us_footprint": "U.S. footprint",
        "growth_goal": "Growth goal", "growth_markets": "Growth markets",
        "us_leadership": "U.S. leadership", "global_leadership": "Global leadership",
        "digital_ordering": "Digital ordering", "rewards": "Rewards",
        "brand_ambition": "Brand ambition", "economics_scale": "Economics / scale",
        "relationship_history": "Relationship history", "current_pursuit": "Current pursuit",
    }
    for item in bp.get("identity_ownership_footprint", []):
        rows.append(item)
    for item in bp.get("leadership", []):
        rows.append(item)
    for item in bp.get("technology_payment_landscape", {}).get("highlights", []):
        rows.append(item)
    for item in bp.get("brand_digital_cx_strategy", []):
        rows.append(item)
    for item in bp.get("account_economics_scale", []):
        rows.append(item)
    for item in bp.get("relationship_history", []):
        rows.append(item)

    template_rows = 14  # rows 5-18
    extra_rows = max(0, len(rows) - template_rows)
    if extra_rows:
        _extend_plain_rows(ws, 5 + template_rows, extra_rows, style_source_row=5 + template_rows - 1, columns="ABCDEFGH")
    template_rows += extra_rows
    for i in range(template_rows):
        r = 5 + i
        if i < len(rows):
            item = rows[i]
            set_cell(ws, f"A{r}", label_map.get(item["category"], item["category"]))
            set_cell(ws, f"B{r}", item["value"])
            set_cell(ws, f"C{r}", item["strategic_implication"])
            set_cell(ws, f"D{r}", item["confidence"].replace("_", "/").title() if isinstance(item["confidence"], str) else item["confidence"])
            set_cell(ws, f"E{r}", item["as_of"])
            set_cell(ws, f"F{r}", "")
            set_cell(ws, f"G{r}", "")
            set_cell(ws, f"H{r}", "")
        else:
            for col in "ABCDEFGH":
                set_cell(ws, f"{col}{r}", None)


def render_tech_stack_tab(wb, dossier, slug, display_name):
    ws = wb["Technology Stack"]
    set_cell(ws, "A1", f"{display_name.upper()} — CURRENT TECHNOLOGY & VENDOR LANDSCAPE")
    stack = dossier["account"].get("technology_stack", [])
    template_rows = 13  # rows 5-17
    extra_rows = max(0, len(stack) - template_rows)
    if extra_rows:
        _extend_plain_rows(ws, 5 + template_rows, extra_rows, style_source_row=5 + template_rows - 1, columns="ABCDEFGHIJK")
    template_rows += extra_rows

    layer_to_row = {}
    for r in range(5, 5 + template_rows):
        layer = ws.cell(row=r, column=1).value
        if layer:
            layer_to_row[layer] = r

    used_rows = set()
    for item in stack:
        r = layer_to_row.get(item["layer"])
        if r is None:
            for candidate in range(5, 5 + template_rows):
                if candidate not in used_rows:
                    r = candidate
                    break
        used_rows.add(r)
        set_cell(ws, f"A{r}", item["layer"])
        set_cell(ws, f"B{r}", item["vendor"])
        set_cell(ws, f"C{r}", item["current_state"])
        set_cell(ws, f"D{r}", item["confidence"])
        set_cell(ws, f"K{r}", item["status"])
    for r in range(5, 5 + template_rows):
        if r not in used_rows:
            for col in "ABCDEFGHIJK":
                set_cell(ws, f"{col}{r}", None)


def render_actions_tab(wb, dossier, slug, display_name):
    ws = wb["Actions & Decisions"]
    set_cell(ws, "A1", f"{display_name.upper()} — ACTION & DECISION REGISTER")
    actions = dossier["actions"].get("actions", [])
    template_rows = 14  # rows 5-18
    extra_rows = max(0, len(actions) - template_rows)
    if extra_rows:
        _extend_plain_rows(ws, 5 + template_rows, extra_rows, style_source_row=5 + template_rows - 1, columns="ABCDEFGHIJKL")
    template_rows += extra_rows
    for i in range(template_rows):
        r = 5 + i
        if i < len(actions):
            a = actions[i]
            set_cell(ws, f"A{r}", a["type"].replace("_", " ").title())
            set_cell(ws, f"B{r}", a["issue"])
            set_cell(ws, f"H{r}", a["description"])
            set_cell(ws, f"I{r}", a["owner"])
            set_cell(ws, f"J{r}", a["target"])
            set_cell(ws, f"K{r}", a["status"].replace("_", " ").title())
            set_cell(ws, f"L{r}", a["blocker"])
        else:
            for col in "ABCDEFGHIJKL":
                set_cell(ws, f"{col}{r}", None)


def render_commercial_tab(wb, dossier, slug, display_name):
    ws = wb["Commercial Model"]
    set_cell(ws, "A1", f"{display_name.upper()} — COMMERCIAL MODEL")
    items = dossier["account"].get("commercial_models", [])
    template_rows = 7  # rows 5-11
    extra_rows = max(0, len(items) - template_rows)
    if extra_rows:
        _extend_commercial_model_rows(ws, extra_rows)
    template_rows += extra_rows
    for i in range(template_rows):
        r = 5 + i
        if i < len(items):
            m = items[i]
            # RB-2026-09-02: real gap found live -- 4 of pollo-campero's
            # commercial_models entries (added later than the other 7) have
            # no "unit" value, which crashed this whole tab via a bare
            # m["unit"]. Rendering blank for a genuinely missing field is
            # correct here; guessing a real unit value is not -- that's
            # Todd's actual RFP pricing data, not something to invent.
            set_cell(ws, f"A{r}", m.get("item", ""))
            set_cell(ws, f"C{r}", m.get("value", ""))
            set_cell(ws, f"D{r}", m.get("unit", ""))
            set_cell(ws, f"E{r}", m.get("confidence", ""))
            set_cell(ws, f"H{r}", m.get("status", ""))
        else:
            for col in "ABCDEFGH":
                set_cell(ws, f"{col}{r}", None)
    # Formula cells (illustrative monthly standalone/participation) now
    # live at C{5+template_rows}/C{6+template_rows} -- shifted down by
    # _extend_commercial_model_rows if items overflowed, but still correct
    # since they reference C8/C9/C11, all inside the untouched 5-11 range.


def render_method_governance_tab(wb, dossier, display_name):
    ws = wb["Method & Governance"]
    review = dossier["account"].get("latest_review", {})
    set_cell(ws, "B18", review.get("blue_sheet_owner", ""))
    if review.get("last_reviewed"):
        set_cell(ws, "E18", datetime.datetime.strptime(review["last_reviewed"], "%Y-%m-%d"))
    set_cell(ws, "B19", review.get("core_contributors", ""))
    set_cell(ws, "E19", review.get("review_status", ""))
    set_cell(ws, "H19", review.get("current_critical_test", ""))
    set_cell(ws, "H18", review.get("next_formal_review", ""))


def render_presentation_views(wb, influences, pos, display_name):
    ws1 = wb["Presentation View 1"]
    set_cell(ws1, "A1", f"{display_name.upper()} — PRESENTATION VIEW 1")
    for i, p in enumerate(influences[:16]):  # template has room for 16 rows from row 10
        r = 10 + i
        set_cell(ws1, f"A{r}", f"{p['name']} — {p['title']}")
        set_cell(ws1, f"B{r}", p["role_etuc"]["value"])
        set_cell(ws1, f"C{r}", p["influence"])
        set_cell(ws1, f"D{r}", p["mode"]["value"])
        set_cell(ws1, f"E{r}", p["personal_win"]["value"])
        set_cell(ws1, f"H{r}", p["business_result"])
        set_cell(ws1, f"K{r}", p["access"])
        set_cell(ws1, f"L{r}", p["next_step"])

    ws2 = wb["Presentation View 2"]
    set_cell(ws2, "A1", f"{display_name.upper()} — PRESENTATION VIEW 2")
    set_cell(ws2, "G5", pos.get("competition", {}).get("value", ""))
    for i, p in enumerate(influences[:16]):
        r = 10 + i
        set_cell(ws2, f"A{r}", f"{p['name']} — {p['title']}")
        set_cell(ws2, f"B{r}", p["role_etuc"]["value"])
        set_cell(ws2, f"C{r}", p["influence"])
        set_cell(ws2, f"D{r}", p["mode"]["value"])
        set_cell(ws2, f"E{r}", p["competitive_preference"]["value"])
        set_cell(ws2, f"G{r}", p["rating"]["value"])
        set_cell(ws2, f"H{r}", p["current_read"])
        set_cell(ws2, f"K{r}", p["next_step"])


def render(slug: str, archive_previous: bool = True) -> Path:
    dossier = common.load_account(slug)
    display_name = dossier["account"]["display_name"]
    acct_dir = common.account_dir(slug)
    current_dir = acct_dir / "current"
    out_path = current_dir / f"{display_name.replace(' ', '_')}_Blue_Sheet.xlsx"

    if archive_previous and out_path.exists():
        history_dir = acct_dir / "history"
        history_dir.mkdir(exist_ok=True)
        stamp = common.now_iso().replace(":", "")
        shutil.copy2(out_path, history_dir / f"{stamp}_{display_name.replace(' ', '_')}_Blue_Sheet.xlsx")

    wb = openpyxl.load_workbook(TEMPLATE, data_only=False)
    influences, pos = render_blue_sheet_tab(wb, dossier, slug)
    render_brand_tab(wb, dossier, slug, display_name)
    render_tech_stack_tab(wb, dossier, slug, display_name)
    render_actions_tab(wb, dossier, slug, display_name)
    render_commercial_tab(wb, dossier, slug, display_name)
    render_method_governance_tab(wb, dossier, display_name)
    render_presentation_views(wb, influences, pos, display_name)

    current_dir.mkdir(exist_ok=True)
    wb.save(out_path)
    return out_path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: render.py <account_slug>", file=sys.stderr)
        sys.exit(2)
    result = render(sys.argv[1])
    print(f"rendered {result}")
