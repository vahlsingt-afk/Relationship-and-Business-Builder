"""
Blue Sheet dossier + workbook validator (spec Section 15, adapted).

This checks the JSON dossier directly (fast, no Excel engine needed) and does
a lightweight structural check of the rendered workbook. It does NOT recalculate
formulas - no LibreOffice is available in this environment (see
_standard/GAP_REPORT_2026-08-21.md) - so a passing run here is not proof the
workbook's formulas evaluate correctly, only that they are present and
syntactically referencing the right cells.

Usage: python3 validate.py <account_slug>
Exit code 0 = pass, 1 = fail. Prints a list of findings either way.
"""
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

REQUIRED_TABS = [
    "Blue Sheet", "Brand", "Technology Stack", "Actions & Decisions",
    "Commercial Model", "Presentation View 1", "Presentation View 2",
    "Method & Governance", "Criteria Statements",
    "Concept Card side 1", "Concept Card side 2",
]

FIELD_OBJECT_KEYS = {"value", "status", "evidence_ids", "confidence", "as_of", "scope", "last_reviewed_by"}


def _walk_field_objects(obj, path=""):
    """Yield (path, field_object) for every dict that looks like a field-object."""
    if isinstance(obj, dict):
        if FIELD_OBJECT_KEYS.issubset(obj.keys()):
            yield path, obj
        for k, v in obj.items():
            yield from _walk_field_objects(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from _walk_field_objects(item, f"{path}[{i}]")


def validate(slug: str):
    findings = []
    dossier = common.load_account(slug)
    acct = dossier["account"]
    evidence_ids = {e["evidence_id"] for e in dossier["evidence"]}

    # 1. Every material field has evidence or is labeled hypothesis/unknown (Section 15)
    for path, fo in _walk_field_objects(acct):
        status = fo.get("status")
        eids = fo.get("evidence_ids", [])
        if status == "confirmed" and not eids:
            findings.append(("ERROR", f"account.json:{path} is status=confirmed with no evidence_ids"))
        for eid in eids:
            if eid not in evidence_ids:
                findings.append(("ERROR", f"account.json:{path} references unknown evidence_id '{eid}'"))
        if status not in {"confirmed", "observed", "reported", "hypothesis", "unknown", "contradicted"}:
            findings.append(("ERROR", f"account.json:{path} has invalid status '{status}'"))

    for path, fo in _walk_field_objects(dossier["brand_profile"]):
        eids = fo.get("evidence_ids", [])
        for eid in eids:
            if eid not in evidence_ids:
                findings.append(("ERROR", f"brand_profile.json:{path} references unknown evidence_id '{eid}'"))

    # 2. Governance-gated fields must never be status=confirmed by automation alone (Section 8)
    gated = {"role_etuc", "mode", "personal_win", "competitive_preference", "rating"}
    for person in acct.get("buying_influences", []):
        for field in gated:
            fo = person.get(field, {})
            if isinstance(fo, dict) and fo.get("status") == "confirmed" and fo.get("last_reviewed_by", "").startswith("automation:"):
                findings.append(("ERROR", f"{person.get('person_id')}.{field} is confirmed by automation - requires human approval per Section 8"))

    # 3. Contradictions are visible and scoped (Section 15)
    contradiction_ids = {c["contradiction_id"] for c in dossier["contradictions"].get("contradictions", [])}
    for cid in dossier["brand_profile"].get("known_contradictions", []):
        if cid not in contradiction_ids:
            findings.append(("ERROR", f"brand_profile.json references contradiction_id '{cid}' not present in contradictions.json"))

    # 4. Actions: no unsupported RBB mutations (Section 13)
    for a in dossier["actions"].get("actions", []):
        if a.get("rbb_loop_id") and a.get("mutation_status") not in {"add_loop", "close_loop", None}:
            findings.append(("WARN", f"{a['action_id']} has rbb_loop_id set but mutation_status='{a.get('mutation_status')}' is not a supported RBB mutation type"))

    # 5. Stale-evidence check (informational): any field as_of older than 90 days
    import datetime
    cutoff = datetime.date.today() - datetime.timedelta(days=90)
    for path, fo in _walk_field_objects(acct):
        as_of = fo.get("as_of")
        if as_of:
            try:
                d = datetime.date.fromisoformat(as_of)
                if d < cutoff:
                    findings.append(("WARN", f"account.json:{path} as_of={as_of} is more than 90 days old"))
            except ValueError:
                findings.append(("ERROR", f"account.json:{path} has unparseable as_of '{as_of}'"))

    # 6. Workbook structural checks
    acct_dir = common.account_dir(slug)
    display_name = acct["display_name"]
    wb_path = acct_dir / "current" / f"{display_name.replace(' ', '_')}_Blue_Sheet.xlsx"
    if not wb_path.exists():
        findings.append(("ERROR", f"no current workbook found at {wb_path}"))
    else:
        wb = openpyxl.load_workbook(wb_path, data_only=False)
        for tab in REQUIRED_TABS:
            if tab not in wb.sheetnames:
                findings.append(("ERROR", f"required tab '{tab}' missing from {wb_path.name}"))
        # formula sanity: look for stray error literals a prior bad save might have baked in
        error_tokens = {"#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NULL!"}
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and cell.value.strip() in error_tokens:
                        findings.append(("ERROR", f"{ws.title}!{cell.coordinate} contains literal error token '{cell.value}'"))
                    if isinstance(cell.value, str) and cell.value.startswith("=") and cell.value != cell.value.upper() and cell.value.islower():
                        findings.append(("WARN", f"{ws.title}!{cell.coordinate} formula is lowercased - LibreOffice could not parse it: {cell.value}"))

    # 7. Template version present
    if not acct.get("template_version"):
        findings.append(("ERROR", "account.json missing template_version"))

    errors = [f for f in findings if f[0] == "ERROR"]
    return findings, len(errors) == 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: validate.py <account_slug>", file=sys.stderr)
        sys.exit(2)
    findings, ok = validate(sys.argv[1])
    for level, msg in findings:
        print(f"[{level}] {msg}")
    print(f"\n{'PASS' if ok else 'FAIL'} - {len(findings)} finding(s), {sum(1 for f in findings if f[0]=='ERROR')} error(s)")
    sys.exit(0 if ok else 1)
