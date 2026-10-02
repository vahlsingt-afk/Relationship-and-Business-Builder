#!/usr/bin/env python3
"""
team_portal_api.py — RBB Team Portal.

The first multi-user-facing surface in RBB. Everything else (rbb_chat.py,
server.py) gates every route behind ONE shared static secret with no
per-user identity — fine for Todd alone, wrong for handing real access to
other people. This is a separate process with its own per-teammate
credential store (see system/scripts/team_portal_admin.py) so a teammate's
access can never reach Todd's own chat passcode, rb-api's other 170+
endpoints, or another teammate's account.

It imports system/scripts/team_tech_stack.py directly (in-process function
calls) rather than calling rb-api over HTTP — this process never needs
RB_API_KEY at all, and can't reach any of rb-api's other ~40 imported
domain modules.

Scope, all explicitly decided by Todd on 2026-08-29:
  - Existing brands/vendors only. No entity-creation endpoint exists here.
  - Todd's editorial layer (notes, assessments, risk, strategic_note, his
    own account_intelligence/*.md excerpts, "Todd's POV") is excluded from
    every response — see team_tech_stack.py for exactly how.
  - Per-teammate bearer tokens, checked fresh on every request (revocation
    is immediate, no restart needed).

Run:
    uvicorn system.api.team_portal_api:app --host 127.0.0.1 --port 8767
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

SYSTEM_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SYSTEM_DIR / "scripts"))

import team_tech_stack as tts  # noqa: E402
import team_portal_email as tpe  # noqa: E402
import team_sales_tools as tsts  # noqa: E402
import team_market_intelligence as tmi  # noqa: E402
import restaurant_tech_trends as rtt  # noqa: E402
import team_portal_admin as tpa  # noqa: E402
import team_portal_usage_log as tpul  # noqa: E402

CREDENTIALS_PATH = (
    Path.home() / "Library" / "Application Support" / "Relationship Builder"
    / "team_portal_credentials.json"
)
MANIFEST_PATH = SYSTEM_DIR / "team" / "manifest.yaml"

app = FastAPI(title="RBB Team Portal")


# ---------------------------------------------------------------------------
# Auth — per-teammate bearer token, no shared-secret fallback.
# ---------------------------------------------------------------------------

def _load_credentials() -> dict:
    if not CREDENTIALS_PATH.exists():
        return {}
    return json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))


def _load_manifest_members() -> dict[str, dict]:
    if not MANIFEST_PATH.exists():
        return {}
    import yaml
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8")) or {}
    return {m["id"]: m for m in manifest.get("members", [])}


def get_current_member(request: Request, authorization: Optional[str] = Header(None)) -> dict:
    """FastAPI dependency: Authorization: Bearer <token> -> {id, name, is_owner, email}.

    Unlike rbb_chat.py's passcode auth, there is deliberately no "not
    configured -> run open" fallback here — a team surface with real other
    people's access must always require a valid, non-revoked credential.

    is_owner (2026-09-25 addition, defaults False): an explicit manifest
    flag, not a magic id/name comparison — set is_owner: true on Todd's own
    manifest entry if/when he registers himself as a Team Portal member.
    Gates get_canonical_background_brief's full-vs-redacted branch; every
    other route in this module ignores it entirely and stays
    identically-redacted for every member regardless of this flag.

    request.state.member_id (2026-09-30 addition): stashed as soon as a
    token hash resolves to a real credential entry, BEFORE checking
    revoked_at/suspended_at -- so _UsageLogger below can still attribute a
    revoked/suspended member's blocked attempt to them (real security
    signal, not just an anonymous 401 in the usage log). Left unset if the
    token never matches anything at all.

    suspended_at (2026-09-30 addition): treated identically to
    revoked_at for auth purposes -- 401 either way. The difference is
    reversibility, handled entirely in team_portal_admin.py (unsuspend_
    member() vs. add_member with a new id); this function doesn't need to
    know which one a caller hit.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing or malformed Authorization header")
    token = authorization[len("Bearer "):].strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing token")

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    creds = _load_credentials()  # read fresh every request — revocation is immediate
    entry = None
    member_id = None
    for mid, rec in creds.items():
        if rec.get("token_hash") == token_hash:
            entry = rec
            member_id = mid
            break
    if member_id:
        request.state.member_id = member_id
    if not entry or entry.get("revoked_at") or entry.get("suspended_at"):
        raise HTTPException(status_code=401, detail="invalid, revoked, or suspended token")

    members = _load_manifest_members()
    member = members.get(member_id)
    if not member or member.get("revoked_at") or member.get("suspended_at"):
        raise HTTPException(status_code=401, detail="invalid, revoked, or suspended token")

    return {
        "id": member_id, "name": member.get("name", member_id), "is_owner": bool(member.get("is_owner")),
        "email": member.get("email"),
    }


def require_owner(member: dict = Depends(get_current_member)) -> dict:
    """FastAPI dependency for the new /api/admin/* namespace only --
    separate from the existing per-route `is_owner` checks on the brief/
    competitive-brief/battle-card/value-wedge generation routes below
    (those stay exactly as they are; this is a new, stricter gate for a
    new surface, not a replacement for those checks)."""
    if not member.get("is_owner"):
        raise HTTPException(status_code=403, detail="owner access required")
    return member


class _UsageLogger(BaseHTTPMiddleware):
    """Real per-request traffic log -- see team_portal_usage_log.py's own
    module docstring for why this is a separate concern from audit_log.py.
    Shaped after system/api/server.py's existing _RequestLogger (same
    time.monotonic()/call_next/elapsed_ms pattern), adapted to pull caller
    identity from request.state.member_id, which get_current_member()
    stashes above -- middleware runs outside the dependency-injection
    system, so it has no other way to know who made the request."""

    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = (time.monotonic() - start) * 1000
        try:
            # request.scope["route"] is only populated once the router
            # has actually matched a route -- by the time call_next()
            # returns, it's set for any real endpoint hit (None for a
            # 404 that never matched anything, which append_request
            # falls back to request.url.path for).
            matched_route = request.scope.get("route")
            tpul.append_request(
                member_id=getattr(request.state, "member_id", None),
                route=request.url.path, method=request.method,
                status_code=response.status_code, latency_ms=elapsed_ms,
                query=str(request.url.query or ""),
                route_template=getattr(matched_route, "path", None),
            )
        except Exception:  # noqa: BLE001 — usage logging must never break a real response
            pass
        return response


app.add_middleware(_UsageLogger)


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class TechStackEntry(BaseModel):
    vendor_id: str
    category: str
    relationship_id: Optional[str] = None
    product: Optional[str] = None
    vendor_role: Optional[str] = None
    deployment: Optional[dict] = None
    note: Optional[str] = None


class ProfileCorrection(BaseModel):
    target_type: str  # "brand" | "competitor"
    target_id: str
    field_path: str
    proposed_value: object
    note: Optional[str] = ""
    source_url: Optional[str] = None


class PainPointRequest(BaseModel):
    value: str
    source_url: Optional[str] = None
    confidence: str = "medium"


class EmailBriefRequest(BaseModel):
    recipient: str


class EmailReportRequest(BaseModel):
    recipient: str
    subject: str
    markdown: str


class GreenSheetRequest(BaseModel):
    account_slug: str
    call_purpose: str
    attendees: list[str]
    talking_points: Optional[str] = ""


class GreenSheetEmailRequest(GreenSheetRequest):
    recipient: str


class AddMemberRequest(BaseModel):
    id: str
    name: str
    email: str
    role: str = "team_member"
    is_owner: bool = False
    plan: str = "internal"


def _not_found_to_404(exc: tts.NotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def get_health():
    return {"status": "ok"}


@app.get("/api/me")
def get_me(member: dict = Depends(get_current_member)):
    """Who the UI is talking to, is_owner included -- lets the UI show
    Todd his real full-canonical-brief language instead of the teammate
    redacted-variant copy, without guessing from the id/name."""
    return member


@app.get("/api/brands/search")
def get_brands_search(q: str = "", member: dict = Depends(get_current_member)):
    return {"brands": tts.search_brands(q)}


@app.get("/api/vendors/search")
def get_vendors_search(
    q: str = "", competes_on: Optional[str] = None, member: dict = Depends(get_current_member),
):
    return {"vendors": tts.search_vendors(q, competes_on_category=competes_on)}


@app.get("/api/partners/search")
def get_partners_search(
    q: str = "", category: Optional[str] = None, member: dict = Depends(get_current_member),
):
    return {"partners": tts.search_partners(q, category=category)}


@app.get("/api/partners/categories")
def get_partners_categories(member: dict = Depends(get_current_member)):
    return {"categories": tts.get_partner_categories()}


@app.get("/api/brands/{brand_id}/tech-stack")
def get_brand_tech_stack_route(brand_id: str, member: dict = Depends(get_current_member)):
    try:
        return tts.get_brand_tech_stack(brand_id)
    except tts.NotFoundError as exc:
        raise _not_found_to_404(exc)


@app.post("/api/brands/{brand_id}/tech-stack")
def post_brand_tech_stack(brand_id: str, body: TechStackEntry, member: dict = Depends(get_current_member)):
    try:
        return tts.submit_tech_stack_entry(
            brand_id,
            member["id"],
            vendor_id=body.vendor_id,
            category=body.category,
            relationship_id=body.relationship_id,
            product=body.product,
            vendor_role=body.vendor_role,
            deployment=body.deployment,
            note=body.note,
        )
    except tts.NotFoundError as exc:
        raise _not_found_to_404(exc)
    except tts.ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/brands/{brand_id}/pain-points")
def post_brand_pain_point(brand_id: str, body: PainPointRequest, member: dict = Depends(get_current_member)):
    """Value Wedge's Circle 2 ("Customer Needs") quick-add -- surfaced
    inline on the Value Wedge panel when that circle is empty (2026-09-28,
    Todd: standalone value in Genius Strengths/Competitor Weaknesses alone
    is real, so the wedge always renders; this just makes it easy to add
    the missing circle in the moment rather than only via a future chat
    tool). See team_tech_stack.add_brand_pain_point."""
    try:
        return tts.add_brand_pain_point(
            brand_id, body.value, member_id=member["id"],
            source_url=body.source_url, confidence=body.confidence,
        )
    except tts.NotFoundError as exc:
        raise _not_found_to_404(exc)


@app.get("/api/brands/{brand_id}/background-brief")
def get_brand_background_brief_route(brand_id: str, member: dict = Depends(get_current_member)):
    """Structural account facts only (Brand Profile, Technology
    Environment, Leadership, Related Artifacts) -- see team_tech_stack.py
    module docstring for why this deliberately never touches Todd's full
    Account Background Brief pipeline. A single GET, no generate/refresh
    step: this is a pure, cheap assembly of already-persisted structural
    data, not a versioned document."""
    try:
        return tts.get_brand_background_brief(brand_id)
    except tts.NotFoundError as exc:
        raise _not_found_to_404(exc)


@app.post("/api/vendors/{vendor_id}/profile")
def post_vendor_profile(vendor_id: str, member: dict = Depends(get_current_member)):
    try:
        return tts.get_vendor_competitor_profile(vendor_id, member_id=member["id"])
    except tts.NotFoundError as exc:
        raise _not_found_to_404(exc)


# --- Ecosystem Lookup Tool (2026-09-25 additions) --------------------------

@app.get("/api/brands/{brand_id}/ecosystem-profile")
def get_brand_ecosystem_profile(brand_id: str, member: dict = Depends(get_current_member)):
    """Identity, synopsis, leadership, footprint, trajectory, recent
    signals -- the new company-profile fields, always the shareable view
    (brand_profile_common.shareable_view). No is_owner branch here: unlike
    the canonical background brief below, this route has no full/redacted
    distinction to make -- these fields are facts either way."""
    try:
        return tts.get_brand_profile(brand_id)
    except tts.NotFoundError as exc:
        raise _not_found_to_404(exc)


@app.get("/api/vendors/{vendor_id}/company-profile")
def get_vendor_company_profile(vendor_id: str, member: dict = Depends(get_current_member)):
    """Vendor-side sibling of the brand ecosystem-profile route above
    (2026-09-28) -- identity, synopsis, leadership, footprint, recent
    signals. Same no-is_owner-branch reasoning: these are facts either
    way."""
    try:
        return tts.get_vendor_profile(vendor_id)
    except tts.NotFoundError as exc:
        raise _not_found_to_404(exc)


@app.get("/api/vendors/{vendor_id}/competitor-extended-profile")
def get_vendor_competitor_extended_profile(vendor_id: str, member: dict = Depends(get_current_member)):
    """Products/strengths/key_customers/recent_news/trends -- weaknesses
    and vulnerabilities excluded (competitor_intelligence_common.
    shareable_extended_view), same reasoning as todds_pov/vs_genius above."""
    try:
        return tts.get_competitor_extended_profile(vendor_id)
    except tts.NotFoundError as exc:
        raise _not_found_to_404(exc)


@app.post("/api/profile-submissions")
def post_profile_submission(body: ProfileCorrection, member: dict = Depends(get_current_member)):
    """A teammate's proposed correction -- queued for Todd's review, never
    applied live. See team_profile_submissions.py."""
    try:
        return tts.submit_profile_correction(
            target_type=body.target_type, target_id=body.target_id, field_path=body.field_path,
            proposed_value=body.proposed_value, member_id=member["id"],
            note=body.note or "", source_url=body.source_url,
        )
    except tts.NotFoundError as exc:
        raise _not_found_to_404(exc)
    except tts.ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/brands/{brand_id}/canonical-background-brief")
def post_canonical_background_brief(brand_id: str, member: dict = Depends(get_current_member)):
    """The "generate a background brief" button. Todd (is_owner=true) gets
    the real, full canonical brief (and it gets versioned, same as any
    other real generation of his); every other teammate gets the same
    allowlisted structural-only version get_brand_background_brief already
    proved safe. See team_tech_stack.get_canonical_background_brief for
    why there's no "redact the full brief" code path."""
    try:
        return tts.get_canonical_background_brief(
            brand_id, is_owner=member["is_owner"], requested_by=member["id"],
        )
    except tts.NotFoundError as exc:
        raise _not_found_to_404(exc)


@app.post("/api/brands/{brand_id}/canonical-background-brief/email")
def post_email_canonical_background_brief(
    brand_id: str, body: EmailBriefRequest, member: dict = Depends(get_current_member),
):
    """Generates the brief (same is_owner branch as above) and emails it
    via Team Portal's own dedicated SMTP credential -- never Todd's."""
    try:
        brief = tts.get_canonical_background_brief(
            brand_id, is_owner=member["is_owner"], requested_by=member["id"],
        )
    except tts.NotFoundError as exc:
        raise _not_found_to_404(exc)

    subject = f"{brief.get('brand_id', brand_id)} — Account Background Brief"
    try:
        outcome = tpe.send_brief(
            recipient=body.recipient, subject=subject, markdown_body=brief["markdown"],
            sent_by=member["id"],
        )
    except tpe.NotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if not outcome.get("sent"):
        raise HTTPException(status_code=502, detail=outcome.get("detail") or outcome.get("status"))
    return {"brand_id": brand_id, "recipient": body.recipient, **outcome}


@app.post("/api/email-report")
def post_email_report(body: EmailReportRequest, member: dict = Depends(get_current_member)):
    """Generic "email this" for any report already rendered in the UI
    (Competitor Profile, Competitive Brief, Battle Card, Value Wedge, ...)
    -- 2026-09-28, Todd: "let's also fix the email so that any of these
    reports can be emailed." Deliberately generic rather than one bespoke
    route per artifact type: team_portal_email.send_brief() was already
    fully generic underneath the one existing caller (canonical-
    background-brief/email above), it just had no other entry point. The
    caller supplies markdown it already has in hand from a prior generate
    call -- this route never re-fetches or re-generates anything itself,
    so it can't be used to exfiltrate content the caller couldn't already
    see."""
    try:
        outcome = tpe.send_brief(
            recipient=body.recipient, subject=body.subject, markdown_body=body.markdown,
            sent_by=member["id"],
        )
    except tpe.NotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if not outcome.get("sent"):
        raise HTTPException(status_code=502, detail=outcome.get("detail") or outcome.get("status"))
    return {"recipient": body.recipient, **outcome}


@app.post("/api/vendors/{vendor_id}/competitive-brief")
def post_competitive_brief(vendor_id: str, member: dict = Depends(get_current_member)):
    """The "generate a competitive brief" button. Todd (is_owner=true)
    generates and persists a new official version of the existing
    competitive_brief.py artifact; every other teammate only reads the
    current persisted version, Todd's POV redacted -- never triggers a new
    version themselves. See team_tech_stack.get_competitive_brief_view."""
    try:
        return tts.get_competitive_brief_view(
            vendor_id, is_owner=member["is_owner"], requested_by=member["id"],
        )
    except tts.NotFoundError as exc:
        raise _not_found_to_404(exc)


@app.post("/api/vendors/{vendor_id}/competitive-brief/request-refresh")
def post_competitive_brief_request_refresh(vendor_id: str, member: dict = Depends(get_current_member)):
    """Todd's explicit "Option B" (2026-09-30) -- any teammate can request
    an earlier Competitive Brief synthesis refresh than the weekly Friday
    EOW pass. Only records the request (no LLM call, no live generation
    here); morning_pipeline.py's competitive_brief_refresh_queue step
    processes it the next morning and emails the requester's own address
    (member["email"] -- never a caller-supplied one). See
    team_tech_stack.request_competitive_brief_refresh."""
    try:
        return tts.request_competitive_brief_refresh(
            vendor_id, member_id=member["id"], member_email=member.get("email") or "",
        )
    except tts.NotFoundError as exc:
        raise _not_found_to_404(exc)


@app.post("/api/vendors/{vendor_id}/battle-card")
def post_battle_card(vendor_id: str, member: dict = Depends(get_current_member)):
    """The "generate a battle card" button -- category-scoped (Genius's
    position plus up to 5 competitors in this vendor's tech-stack
    category), same is_owner split as competitive-brief above. See
    team_tech_stack.get_battle_card_view."""
    try:
        return tts.get_battle_card_view(
            vendor_id, is_owner=member["is_owner"], requested_by=member["id"],
        )
    except tts.NotFoundError as exc:
        raise _not_found_to_404(exc)


@app.post("/api/brands/{brand_id}/value-wedge/{vendor_id}")
def post_value_wedge(
    brand_id: str, vendor_id: str, category: Optional[str] = None, member: dict = Depends(get_current_member),
):
    """The "Generate Value Wedge" action -- redesigned 2026-09-28 to be
    brand+vendor scoped, entry point on the brand page's tech-stack table
    (replaces the old vendor-page-only /api/vendors/{vendor_id}/value-wedge).
    The real three-circle methodology (Genius Strengths / Customer Needs /
    Competitor Weaknesses) is inherently account-specific -- see
    value_wedge.py's module docstring. Same is_owner split as competitive-
    brief/battle-card above; no redaction (nothing here is Todd-private).
    Returns structured `data`, not markdown -- rendered as an actual
    three-circle diagram, not more prose. See
    team_tech_stack.get_value_wedge_view."""
    try:
        return tts.get_value_wedge_view(
            vendor_id, brand_id, is_owner=member["is_owner"], requested_by=member["id"], category=category,
        )
    except tts.NotFoundError as exc:
        raise _not_found_to_404(exc)


# --- Market Intelligence (2026-09-29 additions) -----------------------------

@app.get("/api/market/trends")
def get_market_trends(hide_noise: bool = False, member: dict = Depends(get_current_member)):
    """The persisted weekly snapshot (system/scripts/restaurant_tech_
    trends.py --write) -- never recomputed live on a page load. See that
    module's docstring for the methodology and why direction/confidence
    numbers can legitimately be backed entirely by market-activity
    volume rather than qualitative news. hide_noise strips bare stock-
    price/volume evidence citations -- see get_current_trends."""
    return rtt.get_current_trends(hide_noise=hide_noise)


@app.get("/api/market/news")
def get_market_news(
    days: int = 7, company: Optional[str] = None, category: Optional[str] = None,
    side: Optional[str] = None, hide_noise: bool = False, member: dict = Depends(get_current_member),
):
    return tmi.get_latest_news(days=days, company=company, category=category, side=side, hide_noise=hide_noise)


@app.get("/api/market/news/companies")
def get_market_news_companies(member: dict = Depends(get_current_member)):
    """Backs the company filter dropdown -- see team_market_intelligence.
    get_news_companies for why this is watchlist-derived rather than
    scanning only whatever's in the current news window."""
    return tmi.get_news_companies()


@app.get("/api/market/earnings")
def get_market_earnings(member: dict = Depends(get_current_member)):
    return tmi.get_earnings_center()


@app.get("/api/market/earnings/{company_id}")
def get_market_earnings_company(company_id: str, member: dict = Depends(get_current_member)):
    try:
        return tmi.get_earnings_company_detail(company_id)
    except tmi.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/market/earnings/{company_id}/talking-points")
def post_earnings_talking_points(company_id: str, member: dict = Depends(get_current_member)):
    try:
        return tmi.generate_account_talking_points(company_id)
    except tmi.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# --- Sales Tools: account picker (2026-09-29 additions) --------------------

@app.get("/api/sales-tools/accounts/search")
def get_sales_tools_accounts_search(q: str = "", member: dict = Depends(get_current_member)):
    """Scoped to the customers_prospects registry -- NOT /api/brands/search's
    much broader ecosystem graph. See team_sales_tools.search_accounts."""
    return {"accounts": tsts.search_accounts(q)}


# --- Sales Tools: Blue Sheet Center (2026-09-29 additions) ------------------

@app.get("/api/sales-tools/blue-sheet/{account_id}/template")
def get_sales_tools_blue_sheet_template(account_id: str, member: dict = Depends(get_current_member)):
    """The blank standard template -- account_id in the path only for a
    consistent URL shape with the other two routes; the file itself
    carries no per-account data."""
    path = tsts.get_blue_sheet_template_path()
    return FileResponse(
        path, filename="Blank_Blue_Sheet.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/sales-tools/blue-sheet/{account_id}/coverage")
def get_sales_tools_blue_sheet_coverage(account_id: str, member: dict = Depends(get_current_member)):
    try:
        return tsts.get_blue_sheet_coverage(account_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/sales-tools/blue-sheet/{account_id}/download")
def post_sales_tools_blue_sheet_download(account_id: str, member: dict = Depends(get_current_member)):
    """Builds the team-safe workbook fresh, in memory (team_sales_tools.
    render_team_blue_sheet_workbook) -- never touches the canonical
    blue_sheets/accounts/<slug>/current/*.xlsx."""
    try:
        buf, display_name = tsts.render_team_blue_sheet_workbook(account_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    filename = f"{display_name.replace(' ', '_')}_Team_Blue_Sheet.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Sales Tools: Green Sheet Center (2026-09-29 additions) -----------------

@app.post("/api/sales-tools/green-sheet/preview")
def post_sales_tools_green_sheet_preview(body: GreenSheetRequest, member: dict = Depends(get_current_member)):
    try:
        return tsts.preview_team_green_sheet(
            body.account_slug, call_purpose=body.call_purpose, attendees=body.attendees,
            talking_points=body.talking_points or "", requested_by=member["id"],
        )
    except tsts.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except tsts.ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/sales-tools/green-sheet/download")
def post_sales_tools_green_sheet_download(body: GreenSheetRequest, member: dict = Depends(get_current_member)):
    try:
        result = tsts.preview_team_green_sheet(
            body.account_slug, call_purpose=body.call_purpose, attendees=body.attendees,
            talking_points=body.talking_points or "", requested_by=member["id"],
        )
    except tsts.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except tsts.ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    filename = f"{body.account_slug}_Green_Sheet.md"
    return Response(
        content=result["markdown"], media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/sales-tools/green-sheet/email")
def post_sales_tools_green_sheet_email(body: GreenSheetEmailRequest, member: dict = Depends(get_current_member)):
    """Same generic tpe.send_brief() path as the existing canonical-
    background-brief/email and email-report routes above -- one dedicated
    Team Portal SMTP credential, never Todd's own."""
    try:
        result = tsts.preview_team_green_sheet(
            body.account_slug, call_purpose=body.call_purpose, attendees=body.attendees,
            talking_points=body.talking_points or "", requested_by=member["id"],
        )
    except tsts.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except tsts.ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    subject = f"Green Sheet — {body.account_slug}"
    try:
        outcome = tpe.send_brief(
            recipient=body.recipient, subject=subject, markdown_body=result["markdown"],
            sent_by=member["id"],
        )
    except tpe.NotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if not outcome.get("sent"):
        raise HTTPException(status_code=502, detail=outcome.get("detail") or outcome.get("status"))
    return {"account_slug": body.account_slug, "recipient": body.recipient, **outcome}


# --- Admin (2026-09-30 additions) -------------------------------------------
# All routes here are Depends(require_owner) -- Todd only. Wraps team_
# portal_admin.py's existing add/revoke/suspend/unsuspend/rotate/list
# functions rather than reimplementing manifest/credential I/O; that
# module already has the exact right functions and is exercised by its
# own test suite (test_team_portal_admin.py).

@app.get("/api/admin/members")
def get_admin_members(member: dict = Depends(require_owner)):
    members = tpa.list_members()
    summary = tpul.summarize_by_member()
    for m in members:
        usage = summary.get(m["id"], {"request_count": 0, "last_seen": None})
        m["request_count"] = usage["request_count"]
        m["last_seen"] = usage["last_seen"]
    return {"members": members}


@app.post("/api/admin/members")
def post_admin_member(body: AddMemberRequest, member: dict = Depends(require_owner)):
    try:
        token = tpa.add_member(
            body.id, body.name, body.email, body.role, is_owner=body.is_owner, plan=body.plan,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"id": body.id, "token": token}


@app.post("/api/admin/members/{member_id}/suspend")
def post_admin_member_suspend(member_id: str, member: dict = Depends(require_owner)):
    try:
        tpa.suspend_member(member_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"id": member_id, "suspended": True}


@app.post("/api/admin/members/{member_id}/unsuspend")
def post_admin_member_unsuspend(member_id: str, member: dict = Depends(require_owner)):
    try:
        tpa.unsuspend_member(member_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"id": member_id, "suspended": False}


@app.post("/api/admin/members/{member_id}/revoke")
def post_admin_member_revoke(member_id: str, member: dict = Depends(require_owner)):
    try:
        tpa.revoke_member(member_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"id": member_id, "revoked": True}


@app.post("/api/admin/members/{member_id}/rotate")
def post_admin_member_rotate(member_id: str, member: dict = Depends(require_owner)):
    """The "lost/forgotten individual token" action (Todd, 2026-09-30) --
    already existed; see tpa.rotate_member's own docstring."""
    try:
        token = tpa.rotate_member(member_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"id": member_id, "token": token}


@app.post("/api/admin/members/rotate-all")
def post_admin_members_rotate_all(member: dict = Depends(require_owner)):
    """Group-wide rotation (Todd, 2026-09-30) -- every active member's
    current token stops working immediately; the admin UI is responsible
    for showing every new token so Todd can redistribute them. Placed
    ahead of no {member_id}-shaped path collision risk: this is a
    distinct fixed sub-path, not a dynamic segment."""
    return {"tokens": tpa.rotate_all_members()}


@app.get("/api/admin/usage")
def get_admin_usage(
    member_id: Optional[str] = None, since: Optional[str] = None, until: Optional[str] = None,
    member: dict = Depends(require_owner),
):
    events = tpul.load_events(member_id=member_id, since=since, until=until)
    return {
        "events": events,
        "summary": tpul.summarize_by_member(events),
        # "who is using what" (Todd, 2026-09-30) -- per-member route
        # breakdown, not just a request count. Unaffected by the
        # member_id filter param above still being None/"unknown" (that
        # function only ever sees real member_ids, by design).
        "routes_by_member": tpul.summarize_routes_by_member(events),
    }


_UI_PATH = Path(__file__).resolve().parent / "team_portal_ui.html"
_ADMIN_UI_PATH = Path(__file__).resolve().parent / "admin_ui.html"


@app.get("/", response_class=HTMLResponse)
def get_ui():
    return HTMLResponse(
        content=_UI_PATH.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/admin", response_class=HTMLResponse)
def get_admin_ui():
    """Static shell only -- every real /api/admin/* call from this page
    still requires a valid owner Bearer token, same as team_portal_ui.
    html's own page-vs-data trust split (the HTML itself is not secret;
    the data behind it is gated server-side)."""
    return HTMLResponse(
        content=_ADMIN_UI_PATH.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )
