#!/usr/bin/env python3
"""team_sales_tools.py — Blue Sheet Center + Green Sheet Center for the Team Portal.

Two "turn RB's account intelligence into a usable document" capabilities,
both built the same way every other Team Portal surface is built (see
team_tech_stack.py's module docstring): reuse the real, existing engine
(blue_sheets/_engine/render.py, system/scripts/green_sheet.py) and put an
explicit ALLOWLIST between it and anything a teammate sees — never a
blocklist, per the 2026-08-29 lesson (a blocklist redaction of the
Background Brief still leaked real sensitive content through free-text
fields a blocklist hadn't anticipated; the fix was allowlisting from
structural sections instead).

Blue Sheet Center
-----------------
render_team_blue_sheet_workbook() is a NEW team-export adapter, not a call
into render.py's own render(slug) entry point:
  - render(slug) writes the canonical workbook to
    blue_sheets/accounts/<slug>/current/*.xlsx and archives the prior
    version — exactly the one file this must never touch (Todd's real
    working document, full of his own judgment fields).
  - Instead, this loads the same dossier (blue_sheets/_engine/common.
    load_account), builds a FILTERED COPY of it (see _build_team_safe_
    dossier below) with every judgment/tactical field replaced by
    "Team input required", then calls render.py's own per-tab render_*
    functions directly against that filtered copy and an in-memory
    workbook — the exact "build fresh, return bytes, never touch disk"
    pattern blue_sheets/_engine/customer_artifact.py already established
    for the (different, customer-facing) reduced-view case.

The judgment/tactical field list below is seeded from blue_sheets/_engine/
impact_review.py's GOVERNANCE_GATED_FIELD_NAMES / GOVERNANCE_GATED_PATH_
PREFIXES (the codebase's own canonical definition of "Miller Heiman
judgment field, never auto-write") plus a few additional free-text tactical
fields render.py reads that aren't in that list (buying_influences[].
current_read/next_step/access, strengths/red_flags[].best_action_plan) —
found by reading exactly which cells render_blue_sheet_tab/render_
presentation_views populate. impact_review's list answers a different
question (what a human must approve before auto-write) than this one (what
a teammate may see at all), so it's a floor, not a ceiling, here.

Green Sheet Center
------------------
Real gap found live 2026-09-29 in manual verification: being scoped to
named attendees does NOT make Green Sheet content team-safe by itself —
its "## On This Call" table reads the same buying_influences[].role_etuc/
current_read/next_step and opportunities[].single_sales_objective fields
Blue Sheet Center masks, straight off account.json, for whichever
attendees match. So this applies the exact same _build_team_safe_dossier
masking, via the same interception technique as Blue Sheet Center's
"filtered copy, never the canonical write path" — except green_sheet.py's
render_green_sheet() has no dossier-injection parameter of its own (it
calls account_background_brief.retrieve_existing_intelligence(slug)
internally), so the interception point here is a scoped patch.object() of
that one call for the duration of a single render, not a parameter. Calls
render_green_sheet() directly, never generate_green_sheet() — the latter
persists a new version into system/artifact_vault/green_sheets/<slug>/,
exactly the shared store Todd's own real Green Sheets live in, so a Team
Portal preview must never write there.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(ROOT / "blue_sheets" / "_engine"))

import customers_prospects_common as cpc  # noqa: E402
import green_sheet  # noqa: E402
import account_background_brief as abb  # noqa: E402
import team_artifact_footer as footer  # noqa: E402
import common as bs_common  # noqa: E402 -- blue_sheets/_engine/common.py
import render as bs_render  # noqa: E402 -- blue_sheets/_engine/render.py


class NotFoundError(Exception):
    """An account slug the caller passed doesn't resolve to an existing account."""


class ValidationError(Exception):
    """The caller's request is missing a required field."""


_MASK = "Team input required"

# Buying-influence fields that carry Todd's own live sales judgment (Economic
# Buying Influence / Coach designation, buying mode, personal win, competitive
# preference, champion assessment) -- impact_review.GOVERNANCE_GATED_FIELD_
# NAMES. Rendered by render_blue_sheet_tab (Blue Sheet tab, rows 19-30) and
# render_presentation_views (Presentation View 1/2).
_BI_MASKED_OBJECT_FIELDS = ("role_etuc", "mode", "personal_win", "competitive_preference", "rating")
# Plain-string tactical fields on the same rows -- Todd's specific play for
# this person (who to use as a wedge, how hard they are to reach), not in
# impact_review's list (that list governs auto-write, not team visibility)
# but the same risk class as the free-text content that leaked in the
# Background Brief incident. `owner` is a separate category from the other
# three -- not a judgment call, just an attribution field -- but 2026-09-29
# real-deployment feedback: every "owner" field in this account is Todd's
# own name, so a downloaded team-safe workbook read "Todd Vahlsing" as the
# assigned owner of nearly every row (buying influences, strengths, red
# flags, actions) and as the Account Owner / Blue Sheet Owner / Core
# Contributors on two other tabs -- exactly the "Todd's names" a teammate's
# working copy must not carry. Blanked, not masked to _MASK, since "who
# owns this" is a staffing assignment a teammate makes for themselves, not
# a fact Todd is withholding.
_BI_MASKED_PLAIN_FIELDS = ("current_read", "next_step", "access")
_OWNER_BLANK = ""


def _mask_field_object(obj: Optional[dict]) -> dict:
    obj = dict(obj or {})
    obj["value"] = _MASK
    return obj


def _mask_buying_influence(p: dict) -> dict:
    masked = dict(p)
    for field_name in _BI_MASKED_OBJECT_FIELDS:
        if field_name in masked:
            masked[field_name] = _mask_field_object(masked[field_name])
    for field_name in _BI_MASKED_PLAIN_FIELDS:
        if field_name in masked:
            masked[field_name] = _MASK
    if "owner" in masked:
        masked["owner"] = _OWNER_BLANK
    return masked


def _mask_strategic_position(pos: dict) -> dict:
    """strategic_position.euphoria_panic / .competition / .position are all
    governance-gated path prefixes (Final strategic position, Euphoria/
    Panic -- explicit spec items). strengths/red_flags stay populated
    (spec lists "Verified strengths and risks" as team-visible) except
    best_action_plan, Todd's specific tactical play for that item."""
    masked = dict(pos)

    ep = masked.get("euphoria_panic") or {}
    masked["euphoria_panic"] = {
        "current_state": _mask_field_object(ep.get("current_state")),
        "reason": _mask_field_object(ep.get("reason")),
        "timing": _mask_field_object(ep.get("timing")),
    }
    masked["competition"] = _mask_field_object(masked.get("competition"))

    position = masked.get("position") or {}
    masked["position"] = {
        "place_in_funnel": _MASK,
        "customer_priority": _MASK,
        "critical_test": _MASK,
        "position_vs_competition": _mask_field_object(position.get("position_vs_competition")),
        "immediate_move": _MASK,
    }

    for key in ("strengths", "red_flags"):
        masked[key] = [
            {**item, "best_action_plan": _MASK, "owner": _OWNER_BLANK} for item in masked.get(key, [])
        ]
    return masked


def _mask_qualification(qual: dict) -> dict:
    masked = dict(qual)
    masked["criteria"] = [
        {**c, "current_read": _MASK, "next_step": _MASK} for c in masked.get("criteria", [])
    ]
    return masked


def _mask_latest_review(review: dict) -> dict:
    """Method & Governance tab: blue_sheet_owner/core_contributors are
    person-attribution fields (currently always "Todd Vahlsing" on real
    accounts) -- blanked for the same reason buying-influence/strengths/
    actions owner fields are. review_status/current_critical_test/
    next_formal_review stay -- process/status facts, not an identity."""
    masked = dict(review)
    for field_name in ("blue_sheet_owner", "core_contributors"):
        if field_name in masked:
            masked[field_name] = _OWNER_BLANK
    return masked


def _mask_actions(actions_doc: dict) -> dict:
    """Actions & Decisions tab reads dossier["actions"]["actions"][].owner
    straight through today -- _build_team_safe_dossier previously spread
    the whole unmasked `actions` key from the dossier via **dossier,
    meaning this whole tab bypassed masking entirely. Real gap found in
    the 2026-09-29 production deployment: every action's owner is Todd's
    name."""
    masked = dict(actions_doc)
    masked["actions"] = [{**a, "owner": _OWNER_BLANK} for a in masked.get("actions", [])]
    return masked


def _mask_opportunity(opp: dict) -> dict:
    masked = dict(opp)
    for field_name in ("single_sales_objective", "commercial_hypothesis"):
        if field_name in masked:
            masked[field_name] = _mask_field_object(masked[field_name])
    return masked


def _mask_brand_profile(bp: Optional[dict]) -> dict:
    """Brand tab rows (brand_profile.json) carry a researched `value` plus
    Todd's own `strategic_implication` (why this fact matters for a Genius
    play) -- the fact stays, the sales interpretation is masked, same split
    as everywhere else in this module. bp is None (not just a missing key)
    for any account customers_prospects_common.load_account() reads that
    has no brand_profile.json on disk -- unlike blue_sheets/_engine/common.
    load_account(), which requires the file to exist."""
    masked = dict(bp or {})
    for list_key in (
        "identity_ownership_footprint", "leadership", "brand_digital_cx_strategy",
        "account_economics_scale", "relationship_history",
    ):
        if list_key in masked:
            masked[list_key] = [{**item, "strategic_implication": _MASK} for item in masked[list_key]]
    tpl = masked.get("technology_payment_landscape")
    if tpl and "highlights" in tpl:
        masked["technology_payment_landscape"] = {
            **tpl,
            "highlights": [{**h, "strategic_implication": _MASK} for h in tpl["highlights"]],
        }
    return masked


# Real 2026-09-29 finding, McDonald's account: several buying_influences
# rows were not masked-but-real people -- they were never verified
# McDonald's stakeholders at all. Their only evidence_id traced to
# "LinkedIn baseline relationship map" / baseline_index.json (Todd's own
# general personal contact index, ~3,000 records, not account-specific
# research), and two of the four (by their own title field) worked for
# other companies entirely (a hardware vendor, Global Payments itself) --
# one was confirmed by Todd to be his own personal contact incorrectly
# carrying a McDonald's title. A masked-but-present row ("Team input
# required" in every judgment cell) still leaks the wrong PERSON to a
# teammate; the fix here is to drop the row, not mask its fields.
_UNVERIFIED_EVIDENCE_DESCRIPTION_MARKERS = ("linkedin baseline",)
_UNVERIFIED_EVIDENCE_EXCERPT_MARKERS = ("baseline_index.json",)


def _unverified_evidence_ids(evidence: list) -> set:
    unverified = set()
    for rec in evidence or []:
        description = (rec.get("description") or "").lower()
        excerpt = (rec.get("excerpt") or "").lower()
        if any(m in description for m in _UNVERIFIED_EVIDENCE_DESCRIPTION_MARKERS) or \
                any(m in excerpt for m in _UNVERIFIED_EVIDENCE_EXCERPT_MARKERS):
            eid = rec.get("evidence_id")
            if eid:
                unverified.add(eid)
    return unverified


def _is_verified_buying_influence(p: dict, unverified_evidence_ids: set) -> bool:
    role = p.get("role_etuc")
    evidence_ids = (role.get("evidence_ids") if isinstance(role, dict) else None) or []
    if not evidence_ids:
        return False  # no evidence backing the role/identity claim at all
    return any(eid not in unverified_evidence_ids for eid in evidence_ids)


def split_verified_buying_influences(dossier: dict) -> tuple:
    """(verified, excluded) buying_influences for this dossier. Shared by
    the team-safe export (_build_team_safe_dossier) and the coverage
    report (get_blue_sheet_coverage) so both agree on who actually counts
    as a known McDonald's-style stakeholder -- real 2026-09-29 finding:
    before this, the coverage report counted an unverified contact both
    as a "known stakeholder" and as satisfying the "Coach designated"
    checklist item, the same wrong-data problem as the export itself,
    just surfaced as a misleading percentage instead of a leaked name."""
    account = dossier["account"]
    unverified_ids = _unverified_evidence_ids(dossier.get("evidence") or [])
    all_influences = account.get("buying_influences", [])
    verified = [p for p in all_influences if _is_verified_buying_influence(p, unverified_ids)]
    verified_names = {p.get("name") for p in verified}
    excluded = [p for p in all_influences if p.get("name") not in verified_names]
    return verified, excluded


_SCRUB_PLACEHOLDER = "[name withheld]"


def _scrub_names(obj, names: list):
    """Recursively blank every literal occurrence of a name in `names`
    out of any string value in obj. Real 2026-09-29 finding: excluding an
    unverified person from buying_influences was NOT enough -- their name
    still surfaced verbatim in three separate free-text fields the
    field-by-field allowlist hadn't anticipated (qualification.criteria[]
    .next_step/current_read, strategic_position.position.immediate_move,
    actions[].issue/description, latest_review.next_formal_review) --
    the exact same lesson the Background Brief allowlist rework already
    taught this codebase (2026-08-29 incident), but for free text that
    embeds a specific wrong PERSON rather than a category of sensitive
    content. This is deliberately a narrow, explicitly-known-name
    blocklist -- never a general sensitive-content blocklist, which
    already failed once -- applied as a last line of defense on top of,
    not instead of, the field-by-field masking above."""
    if not names:
        return obj
    if isinstance(obj, str):
        result = obj
        for name in names:
            if name and name in result:
                result = result.replace(name, _SCRUB_PLACEHOLDER)
        return result
    if isinstance(obj, dict):
        return {k: _scrub_names(v, names) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_names(v, names) for v in obj]
    return obj


def _build_team_safe_dossier(dossier: dict) -> dict:
    account = dict(dossier["account"])
    verified_influences, excluded_influences = split_verified_buying_influences(dossier)
    excluded_names = [p.get("name") for p in excluded_influences if p.get("name")]

    account["buying_influences"] = [_mask_buying_influence(p) for p in verified_influences]
    account["strategic_position"] = _mask_strategic_position(account.get("strategic_position", {}))
    account["qualification"] = _mask_qualification(account.get("qualification", {}))
    account["opportunities"] = [_mask_opportunity(o) for o in account.get("opportunities", [])]
    account.pop("bottom_line", None)  # not read by any render_*_tab today; dropped defensively anyway
    # render_blue_sheet_tab's D5 cell ("Account owner") reads owners[0].
    # name -- currently always "Todd Vahlsing" on real accounts, exactly
    # the "Todd's names" a team-safe workbook must not carry.
    account["owners"] = []
    if "latest_review" in account:
        account["latest_review"] = _mask_latest_review(account["latest_review"])

    # Defense-in-depth: scrub excluded/unverified names, plus Todd's own
    # name, out of every remaining free-text field across the account,
    # actions, and brand-profile documents -- not just the fields already
    # masked above by path.
    scrub_names = excluded_names + ["Todd Vahlsing"]
    account = _scrub_names(account, scrub_names)
    actions_doc = _scrub_names(_mask_actions(dossier.get("actions") or {}), scrub_names)
    brand_profile = _scrub_names(_mask_brand_profile(dossier.get("brand_profile", {})), scrub_names)

    return {
        **dossier,
        "account": account,
        "brand_profile": brand_profile,
        "actions": actions_doc,
        # evidence.jsonl is raw, unvetted free text (call-transcript
        # excerpts, extracted_claims) -- blue_sheets/_engine/customer_
        # artifact.py's own docstring already characterizes it as "never
        # rendered anywhere" outside Todd's own tools, and render.py's
        # canonical Blue Sheet never touches it. green_sheet.py's Green
        # Sheet Center's "Relevant Prior Evidence" section does render it
        # verbatim, though -- real 2026-09-29 finding -- and unlike the
        # structured account.json fields above, arbitrary excerpt text
        # can't be reliably field-masked. Dropped entirely for the
        # team-safe view rather than partially/unreliably sanitized.
        "evidence": [],
    }


def search_accounts(query: str, *, limit: int = 25) -> list[dict]:
    """Accounts a Blue Sheet or Green Sheet can actually be built for --
    the customers_prospects registry (~15 real accounts), NOT the much
    broader ecosystem_intelligence brand graph (1,600+ brands, most with
    no customers_prospects/accounts/<slug>/ folder at all). Reusing team_
    tech_stack.search_brands here would let a teammate pick a brand with
    no account folder and hit a 404 on every Sales Tools action -- these
    are two different id spaces in this codebase (ecosystem entity ids
    like 'brand-mcdonalds' vs. account slugs like 'mcdonalds';
    blue_sheets/_engine/common.entity_id_to_slug is the crosswalk between
    them, not used here since this reads the registry directly)."""
    reg = cpc.load_registry()
    q = (query or "").strip().lower()
    results = []
    for entry in reg.get("registry", []):
        account_id = entry.get("account_id", "")
        slug = account_id[len("acct-"):] if account_id.startswith("acct-") else account_id
        aliases = entry.get("aliases") or []
        display_name = aliases[0] if aliases else slug.replace("-", " ").title()
        haystack = " ".join([slug, display_name] + aliases).lower()
        if q and q not in haystack:
            continue
        results.append({
            "slug": slug, "display_name": display_name,
            "engagement_tier": entry.get("engagement_tier"),
            "status": entry.get("status"),
        })
        if len(results) >= limit:
            break
    return results


# ---------------------------------------------------------------------------
# Blue Sheet Center
# ---------------------------------------------------------------------------

def get_blue_sheet_template_path() -> Path:
    """The blank standard template -- no per-account data, safe to hand out
    to anyone with portal access."""
    return bs_render.TEMPLATE


def render_team_blue_sheet_workbook(slug: str) -> tuple[io.BytesIO, str]:
    """Build a team-safe Blue Sheet workbook fully in memory. Raises
    FileNotFoundError (bs_common.account_dir's own exception) for an
    unknown slug. Never writes to blue_sheets/accounts/<slug>/current/ or
    history/ -- the canonical render.render(slug) entry point is not
    called."""
    dossier = bs_common.load_account(slug)
    display_name = dossier["account"]["display_name"]
    team_dossier = _build_team_safe_dossier(dossier)

    import openpyxl
    wb = openpyxl.load_workbook(bs_render.TEMPLATE, data_only=False)
    influences, pos = bs_render.render_blue_sheet_tab(wb, team_dossier, slug)
    bs_render.render_brand_tab(wb, team_dossier, slug, display_name)
    bs_render.render_tech_stack_tab(wb, team_dossier, slug, display_name)
    bs_render.render_actions_tab(wb, team_dossier, slug, display_name)
    bs_render.render_commercial_tab(wb, team_dossier, slug, display_name)
    bs_render.render_method_governance_tab(wb, team_dossier, display_name)
    bs_render.render_presentation_views(wb, influences, pos, display_name)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf, display_name


_JUDGMENT_CHECKS = (
    ("Single Sales Objective", lambda acct: any(
        (o.get("single_sales_objective") or {}).get("status") not in (None, "unknown")
        for o in acct.get("opportunities", [])
    )),
    ("Economic Buying Influence", lambda acct: any(
        "economic" in ((p.get("role_etuc") or {}).get("value") or "").lower()
        for p in acct.get("buying_influences", [])
    )),
    ("Coach", lambda acct: any(
        "coach" in ((p.get("role_etuc") or {}).get("value") or "").lower()
        for p in acct.get("buying_influences", [])
    )),
    ("Personal wins", lambda acct: any(
        (p.get("personal_win") or {}).get("status") not in (None, "unknown")
        for p in acct.get("buying_influences", [])
    )),
    ("Competitive preference", lambda acct: any(
        (p.get("competitive_preference") or {}).get("status") not in (None, "unknown")
        for p in acct.get("buying_influences", [])
    )),
    ("Euphoria/Panic", lambda acct: (
        ((acct.get("strategic_position") or {}).get("euphoria_panic") or {})
        .get("current_state", {}).get("status") not in (None, "unknown")
    )),
    ("Final strategic position", lambda acct: (
        bool(((acct.get("strategic_position") or {}).get("position") or {}).get("immediate_move"))
    )),
)


def get_blue_sheet_coverage(slug: str) -> dict:
    """The coverage report from the spec: what's populated vs. what still
    needs a human judgment call, computed from the REAL (unmasked) dossier
    read server-side -- never returned to the client, only used to decide
    which checklist items are satisfied.

    Stakeholder count and the buying-influence-derived judgment checks
    (Economic Buying Influence, Coach, Personal wins, Competitive
    preference) run against VERIFIED buying_influences only -- same
    split_verified_buying_influences() the export uses. Real 2026-09-29
    finding: before this, an unverified contact (evidence tracing only to
    Todd's own unvetted LinkedIn baseline index, not account-specific
    research) counted toward "N known stakeholders" and could satisfy
    "Coach designated" purely because its fabricated role_etuc happened
    to say "Coach" -- a misleading percentage, the same wrong-data
    problem as the export, just surfaced as a number instead of a name."""
    dossier = bs_common.load_account(slug)  # raises FileNotFoundError for unknown slug
    account = dossier["account"]
    verified_influences, _excluded = split_verified_buying_influences(dossier)
    account_for_checks = {**account, "buying_influences": verified_influences}

    populated = []
    if account.get("display_name") and account.get("opportunities"):
        populated.append("Brand and opportunity identity")
    tech_count = len(account.get("technology_stack", []))
    if tech_count:
        populated.append(f"{tech_count} technology relationships")
    if verified_influences:
        populated.append(f"{len(verified_influences)} known stakeholders")
    action_count = len(dossier["actions"].get("actions", []))
    if action_count:
        populated.append(f"{action_count} documented actions")

    needs_team_input = [label for label, check in _JUDGMENT_CHECKS if not check(account_for_checks)]

    total = 4 + len(_JUDGMENT_CHECKS)
    done = len(populated) + (len(_JUDGMENT_CHECKS) - len(needs_team_input))
    coverage_pct = round(100 * done / total) if total else 0

    return {
        "account_slug": slug,
        "display_name": account.get("display_name", slug),
        "coverage_pct": coverage_pct,
        "populated": populated,
        "needs_team_input": needs_team_input,
        **footer.footer_fields(
            generated_by="RBB Team Portal", data_as_of=account.get("updated_at", "")[:10] or None,
        ),
    }


# ---------------------------------------------------------------------------
# Green Sheet Center
# ---------------------------------------------------------------------------

def preview_team_green_sheet(
    slug: str, *, call_purpose: str, attendees: list[str], talking_points: str = "",
    requested_by: str = "",
) -> dict:
    """Renders a team-safe Green Sheet fully in memory. Calls green_sheet.
    render_green_sheet() directly -- the pure renderer -- rather than
    generate_green_sheet(), which persists a new version into system/
    artifact_vault/green_sheets/<slug>/ via artifact_vault_common.
    Persisting there is exactly the shared store Todd's own real Green
    Sheets live in, so a Team Portal call must never write to it (same
    "never touch the canonical artifact" rule Blue Sheet Center follows
    for blue_sheets/accounts/<slug>/current/).

    render_green_sheet's own "## On This Call" table reads buying_
    influences[].role_etuc/current_read/next_step and opportunities[].
    single_sales_objective straight off account.json for the matched
    attendees -- real gap found live 2026-09-29 in manual verification:
    Green Sheet content is scoped to named attendees, but was NOT
    filtered of the same judgment/tactical fields Blue Sheet Center
    masks. render_green_sheet has no dossier-injection parameter (it
    calls account_background_brief.retrieve_existing_intelligence(slug)
    itself), so this intercepts that one call for the duration of this
    render only and hands back the same team-safe dossier _build_team_
    safe_dossier already produces for Blue Sheet Center -- one masking
    function, reused, not a second copy of the field list."""
    if not (call_purpose or "").strip():
        raise ValidationError("call_purpose must be real, non-empty text")
    if not attendees:
        raise ValidationError("attendees must name at least one real person on the call")

    real_retrieve = abb.retrieve_existing_intelligence

    def _team_safe_retrieve(slug_: str) -> dict:
        intel = real_retrieve(slug_)
        return {**intel, "dossier": _build_team_safe_dossier(intel["dossier"])}

    try:
        with patch.object(abb, "retrieve_existing_intelligence", side_effect=_team_safe_retrieve):
            markdown = green_sheet.render_green_sheet(
                slug, call_purpose=call_purpose, attendees=attendees, talking_points=talking_points,
                prepared_for=requested_by,
            )
    except FileNotFoundError as exc:
        raise NotFoundError(str(exc)) from exc

    markdown = footer.stamp_markdown(
        markdown, generated_by=f"RBB Team Portal (requested by {requested_by})" if requested_by
        else "RBB Team Portal",
    )
    return {"slug": slug, "markdown": markdown}
