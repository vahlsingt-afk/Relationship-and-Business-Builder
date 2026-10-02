#!/usr/bin/env python3
"""
create_plan.py — Master Account Plan ingest orchestration.

Two paths in, deliberately different trust tiers:

1. Mechanical upload path (`ingest_workbook`) -- server.py routes a real
   .xlsx upload here once dataset_classifier.py recognizes its shape
   (Ranked Portfolio + RM Portfolio sheets). No `user_authorization_quote`
   required: the document shape is unambiguous by construction (same
   precedent as the McDonald's NSN Lookup workbook), so there's nothing for
   a human/model to authorize -- this is Todd's own real, complete document
   being recognized and stored, not something being fabricated.

2. Curated-JSON programmatic path (`ingest_curated_update`) -- for any
   future caller that wants to write structured content NOT sourced from a
   raw upload. This DOES require `user_authorization_quote`, mirroring
   create_account.py's createBlueSheetAccount guard, since content curated
   from scratch carries the same fabrication risk Blue Sheet creation does
   -- the mechanical-upload path structurally doesn't.

RB-2026-08-28: built after a real incident where a portfolio-level, multi-
account Worldpay document upload was misrouted into createBlueSheetAccount
(single-account, single-objective) and produced an empty, falsely-
authorized Blue Sheet. See CANONICAL_REGISTRY.yaml's master_account_plans
domain entry for the full governance decision.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

ENGINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_DIR))
import mp_common as common  # noqa: E402
import parse_workbook  # noqa: E402

SCRIPTS_DIR = ENGINE_DIR.parent.parent / "system" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import intelligence_index  # noqa: E402
import ecosystem_intelligence as eco  # noqa: E402
import intelligence_triage as itriage  # noqa: E402
import slug_safety  # noqa: E402

BLUE_SHEETS_ENGINE_DIR = ENGINE_DIR.parent.parent / "blue_sheets" / "_engine"
ACCOUNT_RESEARCH_DIR = ENGINE_DIR.parent.parent / "system" / "account_research"


def _load_module_from_path(module_name: str, file_path: Path):
    """Real bug found 2026-08-28: blue_sheets/_engine/common.py and
    master_account_plans/_engine/common.py are both literally named
    'common' -- a plain `import common as bs_common` after this module's
    own `import common` silently returns the ALREADY-CACHED module from
    sys.modules['common'] (this module's own common.py), not blue_sheets'.
    That made every cross-link resolution silently read the wrong (empty)
    registry. Load by explicit file path under a distinct module name to
    guarantee a genuinely separate module object."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bs_common = _load_module_from_path("blue_sheets_engine_common", BLUE_SHEETS_ENGINE_DIR / "common.py")


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def derive_vendor_slug(filename: str, dashboard_title: str = "") -> Optional[str]:
    """Mechanical, never a guess: matches the established
    '<Vendor>_Master_Account_Plan_<date>.xlsx' filename convention Todd's
    own tooling already produces. Cross-checked against the workbook's own
    title text when available; returns None (never a fabricated slug) if
    the filename doesn't match the known convention."""
    stem = Path(filename).stem
    m = re.match(r"^([A-Za-z0-9]+)_Master_Account_Plan", stem, re.IGNORECASE)
    if not m:
        return None
    vendor_name = m.group(1)
    if dashboard_title and vendor_name.lower() not in dashboard_title.lower():
        return None  # filename and title disagree -- don't guess
    return _slugify(vendor_name)


def _account_tokens(name: str) -> set[str]:
    return set(itriage._tokens(re.sub(r"[^\w\s]", " ", name or "")))


def _resolve_cross_link(account_name: str, registry: dict) -> Optional[str]:
    """Best-effort, non-blocking match against an existing Blue Sheet or
    Account Research registry entry -- never required, never a guess when
    ambiguous (multiple candidates or no clear token match -> None)."""
    name_tokens = _account_tokens(account_name)
    if not name_tokens:
        return None
    candidates = []
    for entry in registry.get("registry", []):
        account_id = entry.get("account_id", "")
        slug = account_id[len("acct-"):] if account_id.startswith("acct-") else account_id
        candidate_names = [slug.replace("-", " ")] + list(entry.get("aliases") or [])
        for cand in candidate_names:
            cand_tokens = _account_tokens(cand)
            if cand_tokens and cand_tokens.issubset(name_tokens):
                candidates.append(slug)
                break
    unique = sorted(set(candidates))
    return unique[0] if len(unique) == 1 else None


def _resolve_all_cross_links(ranked_portfolio: list[dict]) -> list[dict]:
    bs_registry = bs_common.load_registry()
    ar_registry_path = ACCOUNT_RESEARCH_DIR / "_portfolio" / "account_research_registry.json"
    ar_registry = common.load_json(ar_registry_path) if ar_registry_path.exists() else {"registry": []}
    out = []
    for row in ranked_portfolio:
        row = dict(row)
        row["linked_blue_sheet_slug"] = _resolve_cross_link(row.get("account_name", ""), bs_registry)
        row["linked_account_research_slug"] = _resolve_cross_link(row.get("account_name", ""), ar_registry)
        out.append(row)
    return out


def ingest_workbook(content: bytes, filename: str, *, dry_run: bool = False) -> dict:
    """The mechanical upload path. Parses, cross-links, versions, and
    registers a Master Account Plan from a real uploaded workbook. Never
    invents a vendor_slug -- derive_vendor_slug() returning None is a hard
    stop, not a fallback guess."""
    parsed = parse_workbook.parse_workbook(content)
    vendor_slug = derive_vendor_slug(filename, parsed.get("executive_summary_text", ""))
    if not vendor_slug:
        return {
            "ok": False,
            "error": (
                f"Could not determine a vendor slug from filename '{filename}'. "
                "Expected the '<Vendor>_Master_Account_Plan_<date>.xlsx' convention. "
                "Use the curated-JSON path with an explicit vendor_slug instead."
            ),
        }
    ranked_portfolio = _resolve_all_cross_links(parsed["ranked_portfolio"])
    display_name = vendor_slug.replace("-", " ").title()

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "vendor_slug": vendor_slug,
            "display_name": display_name,
            "ranked_portfolio_count": len(ranked_portfolio),
            "rm_portfolios_count": len(parsed["rm_portfolios"]),
            "linked_blue_sheet_count": sum(1 for r in ranked_portfolio if r.get("linked_blue_sheet_slug")),
            "linked_account_research_count": sum(1 for r in ranked_portfolio if r.get("linked_account_research_slug")),
        }

    vendor_dir = common.ROOT / "vendors" / vendor_slug
    is_new = not vendor_dir.exists()
    current_dir = vendor_dir / "current"
    history_dir = vendor_dir / "history"
    current_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)

    workbook_name = f"{display_name.replace(' ', '_')}_Master_Account_Plan.xlsx"
    current_path = current_dir / workbook_name
    if current_path.exists():
        archive_name = current_path.stem + f"_{common.today()}" + current_path.suffix
        archive_path = history_dir / archive_name
        n = 1
        while archive_path.exists():
            archive_path = history_dir / (current_path.stem + f"_{common.today()}_{n}" + current_path.suffix)
            n += 1
        shutil.copy2(current_path, archive_path)
    current_path.write_bytes(content)

    plan_json = {
        "vendor_slug": vendor_slug,
        "display_name": display_name,
        "vendor_entity_id": f"vendor-{vendor_slug}",
        "last_evidence_date": common.today(),
        "last_ingested_at": common.now_iso(),
        "dashboard": parsed.get("dashboard", {}),
        "scoring_model": parsed.get("scoring_model", []),
        "source_workbook_filename": filename,
    }
    common.save_json(vendor_dir / "plan.json", plan_json)
    common.save_json(vendor_dir / "ranked_portfolio.json", ranked_portfolio)
    common.save_json(vendor_dir / "rm_portfolios.json", parsed.get("rm_portfolios", []))
    common.save_json(vendor_dir / "stack_intelligence.json", parsed.get("stack_intelligence", []))
    common.save_json(vendor_dir / "source_snapshot.json", parsed.get("source_snapshot", []))
    common.save_json(vendor_dir / "contradictions.json", {"vendor_slug": vendor_slug, "conflicts": parsed.get("conflict_register", [])})

    evidence_path = vendor_dir / "evidence.jsonl"
    if not evidence_path.exists():
        evidence_path.touch()

    common.append_jsonl(vendor_dir / "logs" / "change_log.jsonl", {
        "at": common.now_iso(),
        "event": "ingested" if is_new else "re-ingested",
        "source_filename": filename,
        "ranked_portfolio_count": len(ranked_portfolio),
        "rm_portfolios_count": len(parsed.get("rm_portfolios", [])),
    })

    _register_in_registry(vendor_slug, display_name, workbook_name)

    # RB-DEFECT-2026-08-29: ingestion never touched ecosystem_intelligence.json
    # at all -- every real account in the vendor's own Ranked Portfolio (its
    # actual, current customer roster) should carry a payments relationship
    # to that vendor in RB's macro tech-stack graph, and nothing here ever
    # wrote one. Best-effort: a payments-sync failure must never fail the
    # ingestion itself (the workbook is already safely stored by this point).
    payments_sync_summary: dict = {"attempted": False}
    try:
        payments_sync_summary = sync_payments_to_ecosystem_graph(vendor_slug, dry_run=False)
        payments_sync_summary["attempted"] = True
    except Exception as exc:  # noqa: BLE001
        payments_sync_summary = {"attempted": True, "error": str(exc)}

    if is_new:
        intelligence_index.register_document(
            display_name, "master_account_plan",
            title=f"{display_name} Master Account Plan",
            path=f"master_account_plans/vendors/{vendor_slug}",
            source_system="master_account_plans",
        )
    else:
        try:
            intelligence_index.log_update(
                display_name, f"master_account_plans/vendors/{vendor_slug}",
                resource_type="master_account_plan",
                note=f"Re-ingested from uploaded workbook '{filename}'.",
            )
        except Exception:  # noqa: BLE001
            pass

    return {
        "ok": True,
        "vendor_slug": vendor_slug,
        "display_name": display_name,
        "newly_created": is_new,
        "ranked_portfolio_count": len(ranked_portfolio),
        "rm_portfolios_count": len(parsed.get("rm_portfolios", [])),
        "payments_sync": payments_sync_summary,
        "linked_blue_sheet_count": sum(1 for r in ranked_portfolio if r.get("linked_blue_sheet_slug")),
        "linked_account_research_count": sum(1 for r in ranked_portfolio if r.get("linked_account_research_slug")),
        "workbook_path": str(current_path.relative_to(common.ROOT.parent)),
    }


# RB-DEFECT-2026-08-29: a Master Account Plan ingestion never touched
# ecosystem_intelligence.json at all -- confirmed live, Church's Chicken
# appears in the real Worldpay portfolio (rank 19, "Established" lifecycle)
# and RB's own macro tech-stack graph had no payments relationship to
# Worldpay for it, or for any of the other 29 accounts. Todd: "All the
# accounts in the worldpay master account should have updated the tech
# macro graph to have worldpay be the payments provider." A Master Account
# Plan's Ranked Portfolio IS, by construction, the vendor's own real,
# current customer roster (an RM-managed book of business, not a cold
# prospect list) -- every row is genuine evidence of an active payments
# relationship, safe to write without per-row human confirmation, same
# trust tier as the mechanical upload path itself.
#
# Corporate legal-entity suffixes ("LLC", "INC", "CO", ...) on account
# names routinely defeat _resolve_brand_entity_id's token-subset matching
# (the incoming token set has to be a SUBSET of the existing one -- an
# extra "LLC" token breaks that). Strip them before resolving. A handful of
# names still need a hand-verified override: either the auto-clean still
# doesn't match (a real spelling/format difference), or plain suffix-
# stripping is ambiguous on its own (e.g. "DEL TACO" is a token-subset of
# BOTH "Del Taco" and the unrelated "Taco Del Mar" -- _resolve_brand_
# entity_id correctly refuses to guess between them without continuity
# hints this isolated lookup doesn't have).
_CORP_SUFFIX_RE = re.compile(
    r"\b(LLC|INC|CO|CORP|CORPORATION|GRP|GROUP|ENTERPRISE|ENTERPRISES|"
    r"HOSPITALITY|RESTAURANTS?|USA|HOLDINGS|OLD COUNTRY STORE|MANAGEMENT)\b\.?,?",
    re.I,
)
_PAREN_RE = re.compile(r"\(.*?\)")

# account_name (as it literally appears in the Ranked Portfolio) -> real
# brand name, hand-verified against the live ecosystem_intelligence.json
# graph 2026-08-29. Skip entries are non-restaurant/non-brand rows (a
# hotel chain, a ski resort, a casino, a real-estate holding company, a
# named pilot program) genuinely out of RB's restaurant-tech domain scope.
_ACCOUNT_NAME_OVERRIDES: dict[str, str | None] = {
    "DEL TACO LLC": "Del Taco",
    "THE STEAK N SHAKE CO": "Steak 'n Shake",
    "MILLLER'S ALE HOUSE": "Miller's Ale House",
    "CHURCHS CHICKEN 558": "Church's Texas Chicken",
    "GRAETERS": "Graeter's",
    "DOMINO'S PIZZA": "Domino's",
    "LATRELLES MANAGEMENT CORP (Bullritos)": "Bullritos",
    # RB-DEFECT-2026-08-29, found on first dry-run: the parent-holding-
    # company-plus-parenthetical-brand shape defeats plain paren-stripping
    # (which kept "Focus Brands", the parent, and discarded "GoTo Foods",
    # the real current operating brand already tracked in the graph).
    "Focus Brands (GoTo Foods)": "GoTo Foods",
    "CHOICE HOTELS": None,
    "BOYNE USA INC": None,
    # RB-DEFECT-2026-08-29, found on first dry-run: this dict's lookup is
    # case-sensitive and every other skip entry happens to be genuinely
    # all-caps in the source data -- this one isn't, so the all-caps key
    # silently missed it and it fell through to the generic clean+resolve
    # path, which created a brand-new, wrong "Barbara B Mann PAH" brand
    # entity for what is actually a performing-arts venue, not a restaurant.
    "Barbara B Mann PAH": None,
    "STATION CASINO": None,
    "FESTIVA REAL ESTATE HOLDINGS": None,
    "RDS FIVE-UNIT REBRAND PILOT": None,
}


def _resolve_worldpay_account_brand(account_name: str, graph: dict) -> tuple[str | None, bool]:
    """Returns (entity_id, is_new) for one Ranked Portfolio account_name,
    or (None, False) if this row is out of restaurant-tech scope or
    couldn't be resolved. Exact-name match is tried first and always wins
    over the ambiguity guard in _resolve_brand_entity_id (the reason "Del
    Taco" needs the override above at all)."""
    # Case-insensitive lookup -- confirmed live 2026-08-29 that relying on
    # every override key matching the source data's exact casing is fragile
    # (every other skip entry happens to be genuinely all-caps in this
    # source; one wasn't, and the exact-case lookup silently missed it).
    override_key = next(
        (k for k in _ACCOUNT_NAME_OVERRIDES if k.casefold() == account_name.casefold()), None,
    )
    if override_key is not None:
        real_name = _ACCOUNT_NAME_OVERRIDES[override_key]
        if real_name is None:
            return None, False
    else:
        cleaned = _PAREN_RE.sub("", account_name)
        cleaned = _CORP_SUFFIX_RE.sub("", cleaned)
        real_name = re.sub(r"\s+", " ", cleaned).strip(" ,.-")

    norm_target = eco._norm_key(real_name)
    for ent in graph.get("entities") or []:
        if ent.get("entity_type") == "brand" and eco._norm_key(ent.get("name") or "") == norm_target:
            return ent["id"], False

    return eco._resolve_brand_entity_id(real_name, graph)


def sync_payments_to_ecosystem_graph(vendor_slug: str, *, dry_run: bool = True) -> dict:
    """Writes a 'payments' category relationship (account brand -> this
    Master Account Plan's vendor) into ecosystem_intelligence.json for
    every real, resolvable restaurant/hospitality account in the Ranked
    Portfolio. Idempotent via resolve_and_upsert_relationship's own
    conflict-detection -- safe to re-run on every future re-ingestion.

    Returns a summary dict; never writes when dry_run=True.
    """
    vendor_dir = common.ROOT / "vendors" / vendor_slug
    plan = common.load_json(vendor_dir / "plan.json")
    ranked_portfolio = common.load_json(vendor_dir / "ranked_portfolio.json")
    vendor_entity_id = plan.get("vendor_entity_id") or f"vendor-{vendor_slug}"
    vendor_display_name = plan.get("display_name") or vendor_slug.replace("-", " ").title()

    graph = eco._read_graph()
    if not dry_run:
        eco._upsert_entity(graph, {
            "id": vendor_entity_id, "name": vendor_display_name,
            "entity_type": "vendor", "subtype": "restaurant_technology_vendor", "status": "active",
            "domains": ["restaurants"], "aliases": [], "attributes": {"primary_category": "payments"},
            "sources": [], "confidence": eco._confidence("high", "Master Account Plan vendor."),
            "notes": "", "created_at": eco._now(), "updated_at": eco._now(), "ticker": None,
        })
        graph = eco._read_graph()

    added = updated = skipped_out_of_scope = skipped_unresolved = 0
    resolved_rows: list[dict] = []
    for row in ranked_portfolio:
        account_name = row.get("account_name") or ""
        entity_id, is_new = _resolve_worldpay_account_brand(account_name, graph)
        is_explicit_skip = any(
            k.casefold() == account_name.casefold() and v is None
            for k, v in _ACCOUNT_NAME_OVERRIDES.items()
        )
        if entity_id is None and is_explicit_skip:
            skipped_out_of_scope += 1
            continue
        if entity_id is None:
            skipped_unresolved += 1
            resolved_rows.append({"account_name": account_name, "resolved": False})
            continue

        source_id = f"src-master-account-plan-{vendor_slug}-{eco._slug(account_name)}"
        assertion = {
            "source_id": source_id,
            "url": None,
            "title": f"{vendor_display_name} Master Account Plan -- {account_name}",
            "publisher": None,
            "published_at": plan.get("last_evidence_date"),
            "discovered_at": eco._today(),
            "source_type": "operator_context",
            # RB-DEFECT-2026-08-29, caught by _write_graph's own pre-write
            # schema validation before anything corrupted: "unknown" is the
            # honest fit here, not an invented enum value -- this is
            # Worldpay's own internal account-management document, not any
            # of the schema's defined *public* source shapes (a filing, a
            # case study, a customer page, trade press, a logo wall).
            "source_authority": "unknown",
            "commercial_incentive_posture": "vendor_self_interested",
            "claim_type": "active_payments_relationship",
            "contracted_locations": row.get("locations"),
            "live_locations": row.get("locations"),
            "rollout_target": None,
            "customer_explicitly_named": True,
            "evidence_origin": "vendor",
            "paraphrase": (
                f"{vendor_display_name}'s own internal Master Account Plan lists {account_name} "
                f"as an active portfolio account ({row.get('tier') or 'unranked'}, rank {row.get('rank')}). "
                f"{row.get('lifecycle_posture') or ''}"
            ).strip(),
            "posture": "current",
        }
        relationship = {
            "id": f"rel-{entity_id}-payments-vendor-own-account-roster-{vendor_entity_id}",
            "from_entity_id": entity_id,
            "to_entity_id": vendor_entity_id,
            "relationship_type": "uses_vendor_for_category",
            "status": "active",
            "domains": ["restaurants"],
            "category": "payments",
            "product": None,
            "vendor_role": "acquirer_or_payments",
            "deployment": {},
            "evidence_posture": "substantiated",
            "interpretation_scope": f"Active Worldpay Master Account Plan account, {row.get('tier') or 'unranked'}.",
            "risk": "unknown",
            "sources": [source_id],
            "confidence": eco._confidence("high", "Vendor's own Master Account Plan roster."),
            "strategic_note": assertion["paraphrase"],
            "created_at": eco._now(), "updated_at": eco._now(),
            "source_assertions": [assertion],
        }

        if not dry_run:
            eco._upsert_entity(graph, {
                "id": entity_id, "name": account_name if is_new else None,
                "entity_type": "brand", "subtype": "restaurant_brand", "status": "active",
                "domains": ["restaurants"], "aliases": [], "attributes": {},
                "sources": [source_id], "confidence": eco._confidence("medium", "Observed in Master Account Plan."),
                "notes": "", "created_at": eco._now(), "updated_at": eco._now(), "ticker": None,
            } if is_new else {"id": entity_id})

        by_id = eco._index_by_id(graph.get("entities") or [])
        outcome = eco.resolve_and_upsert_relationship(graph, relationship, by_id=by_id) if not dry_run else {"added": True}
        if outcome.get("added"):
            added += 1
        else:
            updated += 1
        resolved_rows.append({"account_name": account_name, "resolved": True, "entity_id": entity_id, "is_new": is_new})

    if not dry_run:
        eco._write_graph(graph)

    return {
        "vendor_slug": vendor_slug, "dry_run": dry_run,
        "rows_processed": len(ranked_portfolio),
        "added": added, "updated": updated,
        "skipped_out_of_scope": skipped_out_of_scope,
        "skipped_unresolved": skipped_unresolved,
        "rows": resolved_rows,
    }


def _register_in_registry(vendor_slug: str, display_name: str, workbook_name: str) -> None:
    registry = common.load_registry()
    entry_list = registry.setdefault("registry", [])
    existing = next((e for e in entry_list if e.get("vendor_slug") == vendor_slug), None)
    if existing is None:
        entry_list.append({
            "vendor_slug": vendor_slug,
            "display_name": display_name,
            "vendor_entity_id": f"vendor-{vendor_slug}",
            "workbook_path": f"master_account_plans/vendors/{vendor_slug}/current/{workbook_name}",
            "first_ingested_at": common.now_iso(),
            "last_ingested_at": common.now_iso(),
            "status": "active",
        })
    else:
        existing["last_ingested_at"] = common.now_iso()
        existing["workbook_path"] = f"master_account_plans/vendors/{vendor_slug}/current/{workbook_name}"
    common.save_json(common.registry_path(), registry)


def render_digest_markdown(vendor_slug: str) -> str:
    """Chat-readable view of a Master Account Plan -- a chat interface
    can't open the real xlsx. Framed around "where should a rep spend
    their time" per Todd's own description of this artifact's purpose:
    P1/top-ranked accounts first, RM workload, any pending review items
    awaiting Todd's input. Reuses account_background_brief.py's JSON-state
    -> markdown pattern; reads only, never computes new facts."""
    vendor_dir = common.ROOT / "vendors" / vendor_slug
    plan = common.load_json(vendor_dir / "plan.json")
    ranked_portfolio = common.load_json(vendor_dir / "ranked_portfolio.json")
    rm_portfolios = common.load_json(vendor_dir / "rm_portfolios.json")
    review_queue = common.load_review_queue()
    pending = [r for r in review_queue.get("pending_reviews", [])
               if r.get("vendor_slug") == vendor_slug and r.get("status") == "pending"]

    lines = [
        f"# {plan.get('display_name', vendor_slug)} Master Account Plan",
        f"*Last evidence date: {plan.get('last_evidence_date', 'unknown')} | "
        f"{len(ranked_portfolio)} accounts across {len(rm_portfolios)} RM(s)*",
        "",
    ]

    if pending:
        lines.append(f"## ⚠ {len(pending)} item(s) awaiting your input")
        for item in pending:
            lines.append(f"- **{item.get('account_id')}**: {item.get('reason')}")
        lines.append("")

    def _fmt_score(v):
        return round(v, 1) if isinstance(v, (int, float)) else v

    p1_rows = sorted(
        (r for r in ranked_portfolio if r.get("priority") == "P1"),
        key=lambda r: r.get("rank") or 999,
    )
    lines.append("## Where to spend your time (P1 accounts)")
    for row in p1_rows:
        link = ""
        if row.get("linked_blue_sheet_slug"):
            link = f" [Blue Sheet: {row['linked_blue_sheet_slug']}]"
        lines.append(
            f"- **{row.get('account_name')}** (rank {row.get('rank')}, score {_fmt_score(row.get('score'))}, "
            f"RM: {row.get('rm_name')}){link} — {row.get('immediate_next_action', '')}"
        )
    lines.append("")

    lines.append("## RM roster")
    for rm in rm_portfolios:
        lines.append(
            f"- **{rm.get('rm_name')}**: {rm.get('opportunity_accounts_count')} accounts "
            f"({rm.get('p1_accounts_count')} P1), avg score {rm.get('average_score')} — "
            f"{rm.get('workshop_focus', '')}"
        )

    return "\n".join(lines)


def ingest_curated_update(
    vendor_slug: str, display_name: str, *,
    ranked_portfolio: list, rm_portfolios: list,
    user_authorization_quote: str,
) -> dict:
    """Programmatic path for RBB-curated content NOT sourced from a raw
    upload. Requires explicit authorization, same discipline as
    create_account.py's createBlueSheetAccount guard -- curation-from-
    scratch carries fabrication risk the mechanical-upload path doesn't."""
    if not (user_authorization_quote or "").strip():
        raise ValueError("user_authorization_quote is required for the curated-content ingest path.")
    if not ranked_portfolio and not rm_portfolios:
        raise ValueError("Refusing to create a Master Account Plan with no substantive content.")
    # RB-SECURITY-2026-09-05: vendor_slug reaches here straight from the
    # URL path parameter (ingestMasterAccountPlanUpload) with no validation
    # anywhere upstream -- a '..'/'/' -bearing value would otherwise let
    # vendor_dir below resolve outside master_account_plans/vendors/
    # entirely, before the unconditional mkdir(parents=True) + JSON writes
    # that follow.
    slug_safety.assert_safe_slug(vendor_slug, label="vendor_slug")
    vendor_dir = common.ROOT / "vendors" / vendor_slug
    is_new = not vendor_dir.exists()
    (vendor_dir / "current").mkdir(parents=True, exist_ok=True)
    (vendor_dir / "history").mkdir(parents=True, exist_ok=True)
    common.save_json(vendor_dir / "plan.json", {
        "vendor_slug": vendor_slug, "display_name": display_name,
        "vendor_entity_id": f"vendor-{vendor_slug}",
        "last_evidence_date": common.today(), "last_ingested_at": common.now_iso(),
        "authorization_quote": user_authorization_quote,
    })
    common.save_json(vendor_dir / "ranked_portfolio.json", _resolve_all_cross_links(ranked_portfolio))
    common.save_json(vendor_dir / "rm_portfolios.json", rm_portfolios)
    if is_new:
        intelligence_index.register_document(
            display_name, "master_account_plan", title=f"{display_name} Master Account Plan",
            path=f"master_account_plans/vendors/{vendor_slug}", source_system="master_account_plans",
        )
    return {"ok": True, "vendor_slug": vendor_slug, "newly_created": is_new}


def main() -> int:
    p = argparse.ArgumentParser(description="Ingest a Master Account Plan workbook.")
    p.add_argument("workbook", help="Path to the .xlsx workbook")
    p.add_argument("--write", action="store_true", help="Actually write (default: dry-run)")
    p.add_argument("--json", action="store_true", help="Print JSON summary")
    args = p.parse_args()

    content = Path(args.workbook).read_bytes()
    result = ingest_workbook(content, Path(args.workbook).name, dry_run=not args.write)
    if args.json:
        import json as _json
        print(_json.dumps(result, indent=2, default=str))
    else:
        print(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
