#!/usr/bin/env python3
"""
create_account.py — the mechanical half of "create a new Blue Sheet account."

2026-08-27: real gap found live -- Todd asked rbb-chat to "create a blue
sheet for McDonald's," and there was genuinely no code path for that
anywhere: no POST /accounts creation endpoint, no scaffold script, nothing
wired into chat. Every existing Blue Sheet (Pollo Campero, Five Guys, Del
Taco) was hand-built by a prior session, not produced by a repeatable
feature. This is that feature's mechanical half.

Deliberate split, matching this system's established discipline everywhere
else (Account Background Brief, evidence ledgers, etc.): this module does
NOT extract structured facts from free text itself -- that judgment call
(what's real, what's still unknown, how confident to mark something) stays
with whoever is curating the account from real source material, the same
way Cafe Rio's Account Research and every Blue Sheet before this one were
built. What's genuinely mechanical and safe to make repeatable is: take
already-curated, real, sourced structured content and (1) write it in the
exact schema blue_sheets/_engine/render.py expects, (2) register the
account in the portfolio registry, (3) render the real xlsx, (4) register
it in the unified intelligence index. That's what this module does.

Never call this with invented/guessed field values -- render.py's own
row-capacity limits (raise, not silent truncation) are the only thing
stopping oversized input; nothing here checks for fabricated content.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

ENGINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_DIR))
import common  # noqa: E402
import render as render_module  # noqa: E402

SCRIPTS_DIR = ENGINE_DIR.parent.parent / "system" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import intelligence_index  # noqa: E402
import slug_safety  # noqa: E402


def create_blue_sheet_account(
    slug: str,
    display_name: str,
    *,
    account_data: dict,
    brand_profile_data: dict,
    actions_data: Optional[list] = None,
    evidence_records: Optional[list] = None,
    aliases: Optional[list] = None,
    owner_name: str = "Todd Vahlsing",
    authorized_by: str = "Todd Vahlsing",
    activation_note: str = "",
) -> Path:
    """Creates a brand-new Blue Sheet account folder, registers it in the
    portfolio registry, and renders the real xlsx. Raises FileExistsError
    if the slug already has an account -- this never silently overwrites
    an existing Blue Sheet (same governance as every other creation path
    in this system: explicit, not implicit).

    account_data / brand_profile_data must already be in the exact schema
    render.py's render_*_tab functions read (see that module for the
    field-by-field contract) -- this function does not validate content
    correctness, only that the mechanical write/register/render succeeds.
    """
    # RB-SECURITY-2026-09-05: slug reaches here straight from the API
    # request body (createBlueSheetAccount) with no validation anywhere
    # upstream -- a '..'/'/' -bearing value would otherwise let acct_dir
    # below resolve outside blue_sheets/accounts/ entirely, before the
    # unconditional mkdir(parents=True) + JSON writes that follow.
    slug_safety.assert_safe_slug(slug, label="account_slug")
    acct_dir = common.CUSTOMERS_PROSPECTS_ROOT / "accounts" / slug
    if acct_dir.exists():
        raise FileExistsError(f"Blue Sheet account already exists at {acct_dir} -- use the existing update path, not create.")

    # RB-2026-08-28: real incident -- a "worldpay" Blue Sheet was created
    # with every substantive field empty (opportunities: [], commercial_
    # models: [], buying_influences: [], bottom_line: "") after the caller
    # misread a portfolio-level document upload as a single-account Blue
    # Sheet request. A Blue Sheet with no real content is never valid --
    # reject mechanically here rather than relying on the caller's judgment,
    # the same "guard at the one place the write happens" discipline used
    # for ingestContent's auto_persist fix.
    _substantive = (
        bool(account_data.get("opportunities"))
        or bool(account_data.get("commercial_models"))
        or bool(account_data.get("buying_influences"))
        or bool((account_data.get("qualification") or {}).get("criteria"))
        or bool((account_data.get("bottom_line") or "").strip())
    )
    if not _substantive:
        raise ValueError(
            f"Refusing to create a Blue Sheet for '{slug}' with no substantive content "
            "(opportunities/commercial_models/buying_influences/qualification.criteria/"
            "bottom_line are all empty). A Blue Sheet requires real, sourced content "
            "curated from actual material -- never an empty scaffold."
        )

    acct_dir.mkdir(parents=True)

    account_data = dict(account_data)
    account_data.setdefault("account_id", f"acct-{slug}")
    account_data.setdefault("account_slug", slug)
    account_data.setdefault("display_name", display_name)
    account_data.setdefault("aliases", aliases or [])
    account_data.setdefault("owners", [{"name": owner_name, "role": "account_owner"}])
    account_data.setdefault("brand_profile_ref", "brand_profile.json")
    account_data.setdefault("updated_at", common.now_iso())
    account_data.setdefault("template_version", "blue-sheet-v1")

    brand_profile_data = dict(brand_profile_data)
    brand_profile_data.setdefault("account_id", f"acct-{slug}")

    common.save_json(acct_dir / "account.json", account_data)
    common.save_json(acct_dir / "brand_profile.json", brand_profile_data)
    common.save_json(acct_dir / "actions.json", {"account_id": f"acct-{slug}", "parent_rbb_loop_id": None, "actions": actions_data or []})
    common.save_json(acct_dir / "contradictions.json", {"account_id": f"acct-{slug}", "contradictions": []})
    common.save_json(acct_dir / "source_index.json", {"account_id": f"acct-{slug}", "sources": []})
    for rec in (evidence_records or []):
        common.append_jsonl(acct_dir / "evidence.jsonl", rec)
    if not (evidence_records):
        (acct_dir / "evidence.jsonl").touch()
    (acct_dir / "sources").mkdir(exist_ok=True)

    registry = common.load_registry()
    account_id = f"acct-{slug}"
    if not any(e.get("account_id") == account_id for e in registry.get("registry", [])):
        registry.setdefault("registry", []).append({
            "account_id": account_id,
            "aliases": aliases or [],
            "opportunity_ids": [o.get("opportunity_id") for o in account_data.get("opportunities", [])],
            "owner": owner_name,
            "workbook_path": None,
            "template_version": account_data["template_version"],
            "last_evidence_date": common.today(),
            "last_review_date": common.today(),
            "activation_authorized_by": authorized_by,
            "activation_date": common.today(),
            "activation_note": activation_note,
            "status": "active",
            "engagement_tier": "active_engagement",
        })
        common.save_json(common.registry_path(), registry)

    xlsx_path = render_module.render(slug)

    # Real workbook now exists -- record its path in the registry (was
    # None above) so is_activated()/downstream tooling see a real Blue
    # Sheet, not a placeholder.
    registry = common.load_registry()
    for e in registry.get("registry", []):
        if e.get("account_id") == account_id:
            e["workbook_path"] = str(xlsx_path.relative_to(common.ROOT.parent))
    common.save_json(common.registry_path(), registry)

    try:
        intelligence_index.register_document(
            display_name, "blue_sheet_account", f"Blue Sheet: {slug}",
            f"customers_prospects/accounts/{slug}", source_system="blue_sheets",
            created_at=common.today(),
        )
    except Exception:  # noqa: BLE001 — indexing is best-effort, never blocks account creation
        pass

    return xlsx_path


def activate_existing_research_shell(
    slug: str,
    display_name: str,
    *,
    account_data: dict,
    brand_profile_data: dict,
    actions_data: Optional[list] = None,
    aliases: Optional[list] = None,
    authorized_by: str = "Todd Vahlsing",
    activation_note: str = "",
) -> Path:
    """Populate an existing research-only account without replacing its evidence ledger."""
    slug_safety.assert_safe_slug(slug, label="account_slug")
    acct_dir = common.account_dir(slug)
    current = common.load_json(acct_dir / "account.json")
    if current.get("opportunities") or current.get("engagement_tier") == "active_engagement":
        raise ValueError(f"'{slug}' already has active Blue Sheet content; refusing to overwrite it.")
    if not account_data.get("opportunities") or not (account_data.get("bottom_line") or "").strip():
        raise ValueError("Activation requires a sourced opportunity and substantive bottom_line.")
    registry = common.load_registry()
    entry = next((e for e in registry.get("registry", []) if e.get("account_id") == f"acct-{slug}"), None)
    if entry is None:
        raise ValueError(f"'{slug}' is not in the account registry.")
    if entry.get("workbook_path") and entry.get("engagement_tier") == "active_engagement":
        raise ValueError(f"'{slug}' already has an active canonical workbook; refusing to overwrite it.")

    new_account = dict(current)
    new_account.update(account_data)
    new_account.update({
        "account_id": f"acct-{slug}", "account_slug": slug, "display_name": display_name,
        "aliases": aliases or current.get("aliases", []),
        "engagement_tier": "active_engagement", "updated_at": common.now_iso(),
        "template_version": "blue-sheet-v1",
    })
    ledger = common.load_jsonl(acct_dir / "evidence.jsonl")
    new_account["portfolio_status"] = {
        "value": "active competitive RFP", "status": "confirmed", "confidence": "high",
        "as_of": common.today(), "scope": "account",
        "evidence_ids": [e.get("evidence_id") for e in ledger if e.get("evidence_id")][:5],
        "last_reviewed_by": f"human:{authorized_by}",
    }
    new_brand = dict(common.load_json(acct_dir / "brand_profile.json"))
    new_brand.update(brand_profile_data)
    new_brand["account_id"] = f"acct-{slug}"
    actions_path = acct_dir / "actions.json"
    old_actions = common.load_json(actions_path) if actions_path.exists() else {"account_id": f"acct-{slug}", "actions": []}
    new_actions = dict(old_actions)
    new_actions["actions"] = actions_data or []
    old_brand = common.load_json(acct_dir / "brand_profile.json")
    old_registry = common.load_registry()
    try:
        common.save_json(acct_dir / "account.json", new_account)
        common.save_json(acct_dir / "brand_profile.json", new_brand)
        common.save_json(actions_path, new_actions)
        xlsx_path = render_module.render(slug)
        entry.update({
            "aliases": new_account["aliases"],
            "opportunity_ids": [o.get("opportunity_id") for o in new_account["opportunities"]],
            "owner": authorized_by, "workbook_path": str(xlsx_path.relative_to(common.ROOT.parent)),
            "template_version": "blue-sheet-v1", "last_review_date": common.today(),
            "activation_authorized_by": authorized_by, "activation_date": common.today(),
            "activation_note": activation_note, "status": "active",
            "engagement_tier": "active_engagement",
        })
        common.save_json(common.registry_path(), registry)
    except Exception:
        common.save_json(acct_dir / "account.json", current)
        common.save_json(acct_dir / "brand_profile.json", old_brand)
        common.save_json(actions_path, old_actions)
        common.save_json(common.registry_path(), old_registry)
        raise
    return xlsx_path
