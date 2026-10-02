"""One-time migration: Account Research + Blue Sheets -> customers_prospects/.

Phase 2, step 1 of the approved 3-store unification plan. READ-ONLY against
both predecessor systems -- copies real files into a brand-new
customers_prospects/ tree; never modifies or deletes anything under
system/account_research/ or blue_sheets/. Safe to re-run (overwrites the
new tree only).
"""
import json
import shutil
import sys
from pathlib import Path

# Relative to repo root -- run with cwd = repo root, matching every other
# one-off migration script in this codebase.
AR_ROOT = Path("system/account_research")
BS_ROOT = Path("blue_sheets")
CP_ROOT = Path("customers_prospects")

sys.path.insert(0, "system/scripts")
import customers_prospects_common as cpc  # noqa: E402

ACTIVE_ENGAGEMENT_SLUGS = {"del-taco", "five-guys", "mcdonalds", "pollo-campero"}
PRE_ENGAGEMENT_SLUGS = {"cafe-rio", "churchs-texas-chicken", "little-caesars", "red-robin", "worldpay"}

def _ignore_empty_tmp_files(directory: str, names: list[str]) -> set[str]:
    # Leftover 0-byte temp files from an interrupted atomic write
    # (write-to-.tmp-then-rename) -- confirmed empty on every one found
    # (evidence.jsonl.tmp, change_log.jsonl.tmp) before this migration;
    # never real data, never copied forward. shutil.copytree's own ignore
    # hook is required here, not a top-level-only check, since these can
    # live inside a subdirectory (e.g. pollo-campero/logs/) that gets
    # copied wholesale via copytree below.
    skip = set()
    for name in names:
        p = Path(directory) / name
        if name.endswith(".tmp") and p.is_file() and p.stat().st_size == 0:
            skip.add(name)
    return skip


def _copy_tree_skip_empty_reviews(src: Path, dst: Path) -> None:
    for item in sorted(src.iterdir()):
        if item.name.endswith(".tmp") and item.is_file() and item.stat().st_size == 0:
            continue  # see _ignore_empty_tmp_files -- same rule, top level
        if item.name == "reviews" and item.is_dir() and not any(item.iterdir()):
            continue  # confirmed empty scaffold dir, nothing real to carry
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True, ignore=_ignore_empty_tmp_files)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def migrate_active_engagement(slug: str) -> dict:
    bs_dir = BS_ROOT / "accounts" / slug
    ar_dir = AR_ROOT / "accounts" / slug
    cp_dir = CP_ROOT / "accounts" / slug
    cp_dir.mkdir(parents=True, exist_ok=True)

    # Base: Blue Sheet's real, richer content for every shared file/dir.
    _copy_tree_skip_empty_reviews(bs_dir, cp_dir)

    # Unified account.json: BS's version + engagement_tier + AR's
    # unique, non-empty fields folded in (checked live: executive_summary/
    # current_business_situation are empty stubs for all 4 real overlapping
    # accounts today, but this stays field-driven, not a hardcoded skip, so
    # a future real value would still be honored).
    account = cpc.load_json(cp_dir / "account.json")
    account["engagement_tier"] = "active_engagement"
    if ar_dir.exists():
        ar_account = cpc.load_json(ar_dir / "account.json")
        for field in ("executive_summary", "current_business_situation"):
            val = ar_account.get(field)
            if val:
                account[field] = val
    cpc.save_json(cp_dir / "account.json", account)

    # AR-only concepts, carried forward if present.
    merged_ar_fields = []
    if ar_dir.exists():
        for name in ("opportunity_hypotheses.json", "discovery_questions.json"):
            src = ar_dir / name
            if src.exists():
                shutil.copy2(src, cp_dir / name)
                merged_ar_fields.append(name)

    return {"slug": slug, "tier": "active_engagement", "base": "blue_sheets",
            "merged_from_account_research": merged_ar_fields}


def migrate_pre_engagement(slug: str) -> dict:
    ar_dir = AR_ROOT / "accounts" / slug
    cp_dir = CP_ROOT / "accounts" / slug
    cp_dir.mkdir(parents=True, exist_ok=True)

    _copy_tree_skip_empty_reviews(ar_dir, cp_dir)

    account = cpc.load_json(cp_dir / "account.json")
    account["engagement_tier"] = "pre_engagement"
    cpc.save_json(cp_dir / "account.json", account)

    return {"slug": slug, "tier": "pre_engagement", "base": "account_research",
            "merged_from_account_research": []}


def merge_registries() -> dict:
    ar_reg = json.loads((AR_ROOT / "_portfolio" / "account_research_registry.json").read_text())
    bs_reg = json.loads((BS_ROOT / "_portfolio" / "blue_sheet_registry.json").read_text())

    ar_by_id = {e["account_id"]: e for e in ar_reg.get("registry", [])}
    bs_by_id = {e["account_id"]: e for e in bs_reg.get("registry", [])}

    merged = []
    for account_id in sorted(set(ar_by_id) | set(bs_by_id)):
        if account_id in bs_by_id:
            entry = dict(bs_by_id[account_id])
            entry["engagement_tier"] = "active_engagement"
        else:
            entry = dict(ar_by_id[account_id])
            entry["engagement_tier"] = "pre_engagement"
        merged.append(entry)

    reg = {"registry": merged}
    cpc.save_registry(reg)
    return {"entries": len(merged)}


def main() -> None:
    results = []
    for slug in sorted(ACTIVE_ENGAGEMENT_SLUGS):
        results.append(migrate_active_engagement(slug))
    for slug in sorted(PRE_ENGAGEMENT_SLUGS):
        results.append(migrate_pre_engagement(slug))
    registry_result = merge_registries()

    print(json.dumps({"accounts": results, "registry": registry_result}, indent=2))


if __name__ == "__main__":
    main()
