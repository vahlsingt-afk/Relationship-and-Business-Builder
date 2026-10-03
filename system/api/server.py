#!/usr/bin/env python3
"""
Relationship Builder — HTTP API.

FastAPI wrapper around the same compute functions the MCP server exposes.
This is the surface a Custom GPT (or any AI with HTTP-fetch capability) calls.

Run:
    pip install fastapi uvicorn --break-system-packages
    uvicorn system.api.server:app --host 0.0.0.0 --port 8765

The OpenAPI schema is served at /openapi.json and is what you upload as the
Actions schema when you build the Custom GPT.

Auth is intentionally simple — a single shared API key passed in the
`x-api-key` header. Set the expected value via the `RB_API_KEY` environment
variable. For local dev with no key configured, the server runs open and
warns at startup. Never deploy this without setting RB_API_KEY.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

# Make the scripts/ directory importable.
SYSTEM_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SYSTEM_DIR / "scripts"))

# Blue Sheet account dossiers (blue_sheets/accounts/<slug>/) -- same optional-
# import precedent as ecosystem_intelligence.py's own Blue Sheet hook.
sys.path.insert(0, str(SYSTEM_DIR.parent / "blue_sheets" / "_engine"))
try:
    import common as _blue_sheet_common  # noqa: E402
except Exception:  # noqa: BLE001
    _blue_sheet_common = None
try:
    # RB-2026-08-28: own try/except, same isolation precedent as the split
    # above -- add_evidence.py only depends on common.py, never render.py/
    # openpyxl, so a render.py import failure must never take this down too.
    import add_evidence as _blue_sheet_add_evidence  # noqa: E402
except Exception:  # noqa: BLE001
    _blue_sheet_add_evidence = None
try:
    # RB-2026-09-02: own try/except, same isolation precedent -- this module
    # only depends on common.py + openpyxl (the same dependency add_evidence
    # gained the same day when it started calling render.render()), never
    # render.py's Standard_Blue_Sheet.xlsx template itself.
    import customer_artifact as _blue_sheet_customer_artifact  # noqa: E402
except Exception:  # noqa: BLE001
    _blue_sheet_customer_artifact = None
try:
    # Separate try/except from common above -- real regression found live
    # 2026-08-27: create_account.py transitively imports render.py, which
    # needs openpyxl, not present in this server's actual vendored Python
    # environment (only ever exercised before via a manual `python3
    # render.py <slug>` run in a different environment, never imported from
    # server.py itself). A single shared except here silently disabled
    # _blue_sheet_common too -- breaking listBlueSheetAccounts/
    # getAccountStatus, which have nothing to do with xlsx rendering and
    # worked fine before this file existed.
    import create_account as _blue_sheet_create  # noqa: E402
except Exception:  # noqa: BLE001
    _blue_sheet_create = None

# Master Account Plan (master_account_plans/vendors/<slug>/) -- own try/
# except, same regression precedent as the Blue Sheet split above: a
# failure here must never silently disable unrelated functionality.
sys.path.insert(0, str(SYSTEM_DIR.parent / "master_account_plans" / "_engine"))
try:
    import create_plan as _master_account_plan_create  # noqa: E402
except Exception:  # noqa: BLE001
    _master_account_plan_create = None
try:
    import mp_impact_review as _master_account_plan_review  # noqa: E402
except Exception:  # noqa: BLE001
    _master_account_plan_review = None

import rb_core as core  # noqa: E402
import daily_brief  # noqa: E402
import gap_detection  # noqa: E402
import validate_baseline as vb  # noqa: E402
import mutations  # noqa: E402
import hubspot_ingest  # noqa: E402  # RB-DEFECT-064 — HubSpot CRM export ingest
import test_trace  # noqa: E402
import manual_relationship_intake  # noqa: E402
import opportunity_intake  # noqa: E402
import ri_intake  # noqa: E402
import ri_events  # noqa: E402
import linkedin_ingest  # noqa: E402
import linkedin_freshness_bridge  # noqa: E402
import smart_loops  # noqa: E402
import meeting_prep  # noqa: E402
import passive_verification  # noqa: E402
import closeout  # noqa: E402
import publish  # noqa: E402
import linkedin_messaging  # noqa: E402
import strategic_memory  # noqa: E402
import action_drafts  # noqa: E402
import passive_intelligence  # noqa: E402
import intelligence_triage  # noqa: E402  # RB 9.25A — DEFECT-015
import capture_ingest  # noqa: E402  # ICS Phase 1
import transcript_summarizer  # noqa: E402  # RB-DEFECT-2026-07-15 — real transcript insight
import signal_synthesis  # noqa: E402  # RB 9.27 — connected intelligence
import earnings_monitor  # noqa: E402  # RB 9.29 — watch list mutation
import relationship_intake  # noqa: E402  # RB 9.31 — DEFECT-011
import macro_intelligence  # noqa: E402  # RB 9.31 — DEFECT-012
import insight_intake  # noqa: E402  # RB 9.23 — DEFECT-009
import experiential_intelligence  # noqa: E402  # RB 9.23/9.24 — DEFECT-014
import query_engine  # noqa: E402  # RB 9.42 — Unified Query Engine
import linkedin_ingest_extended  # noqa: E402  # DEFECT-024c — supplemental export sources
import intelligence_mutation_engine  # noqa: E402  # DEFECT-026 — knowledge graph mutations
import dataset_classifier  # noqa: E402  # RB-DEFECT-065 — structured-dataset auto-recognition
import opportunity_pipeline  # noqa: E402  # RB-DEFECT-037 — active opportunity pipeline
import account_background_brief as abb  # noqa: E402  # RB-2026-08-27 — Account Background Brief
import account_plan as acct_plan  # noqa: E402  # RB-2026-09-07 — Account Plan (top-of-funnel, with discovery)
import green_sheet  # noqa: E402  # RB-2026-09-07 — Green Sheet (single-call plan)
import win_plan  # noqa: E402  # RB-2026-09-07 — Win Plan (closing strategy)
import win_loss_review  # noqa: E402  # RB-2026-09-28 — Win/Loss Review (per-opportunity)
import rfp_response_plan  # noqa: E402  # RB-2026-09-07 — RFP Response Plan
import battle_card  # noqa: E402  # RB-2026-09-07 — persisted Battle Card (competitive-side)
import competitive_brief  # noqa: E402  # RB-2026-09-07 — persisted Competitive Brief (competitive-side)
import vendor_engagement_analysis  # noqa: E402  # RB-2026-09-07 — Vendor Engagement Analysis (competitive-side, account-scoped)
import genius_capabilities  # noqa: E402  # RB-2026-09-25 — Genius Capability Library (competitive-side, prerequisite for Value Wedge)
import value_wedge  # noqa: E402  # RB-2026-09-25 — persisted Value Wedge (competitive-side)
import relationship_card  # noqa: E402  # RB-2026-09-08 — Relationship Card publishing (relationship-side; governs system/cards/, does not generate content)
import inner_circle  # noqa: E402  # RB-2026-09-08 — Inner Circle roll-up (relationship-side, singleton artifact)
import referral_network  # noqa: E402  # RB-2026-09-08 — Referral Network: overview publish + per-target analysis persistence (relationship-side)
import relationship_plan  # noqa: E402  # RB-2026-09-08 — Relationship Plan: gated, caller-supplied forward-looking per-person goal (relationship-side, final)
import customers_prospects_common as cpc  # noqa: E402  # RB-2026-09-06 — unified Account Research/Blue Sheet storage (pre-engagement, upstream of active engagement)
import competitor_intelligence as compintel  # noqa: E402  # RB-2026-08-28 — Competitor Intelligence
import competitor_intelligence_common as compintel_common  # noqa: E402
import franchisee_finder_common as ff_common  # noqa: E402  # 2026-10-02 — Franchisee Finder Phase 1 (read-only + seed import)
import technology_lifecycle as tech_lifecycle  # noqa: E402  # 2026-10-02 — Technology Lifecycle Phase 1
import user_pov  # noqa: E402  # 2026-10-02 — User POV Registry Phase 1
import account_reference_detector  # noqa: E402  # RB-2026-08-28 — links uploaded intelligence-pipeline content to known Blue Sheets/competitors by mechanical name match
import intelligence_index  # noqa: E402  # RB-2026-08-27 — unified "what do we have on X, and where" index
import uploaded_document_store  # noqa: E402  # RB-2026-08-31 — retrievable full text for an uploaded document
import tech_stack_relationship_promotion  # noqa: E402  # RB-2026-08-31 — review-first tech-stack coverage expansion
import watchlist_promotion  # noqa: E402  # RB-2026-09-05 — review-first watchlist auto-expansion
import priority_account_publisher_scan  # noqa: E402  # RB-DEFECT-069 (2026-09-10) — review-first priority-account publisher coverage
import ownership_promotion  # noqa: E402  # RB-2026-09-11 — review-first M&A/ownership-change capture
import executive_move_promotion  # noqa: E402  # RB-2026-09-11 — review-first executive-move/contact capture
import job_postings_promotion  # noqa: E402  # RB-2026-09-18, wired 2026-09-25 — job-posting leading-indicator capture
import routine_research_review  # noqa: E402  # review-first baseline research evidence
import downstream_impact_queue  # noqa: E402  # accountable downstream artifact work
import team_profile_submissions as tps  # noqa: E402  # RB-2026-09-25 — Ecosystem Lookup Tool: Todd's review surface for Team Portal-submitted profile/competitor corrections
import intelligence_action_queue as iaq  # noqa: E402  # RB-2026-09-15 — resolvable, calibratable unified action queue
import ecosystem_intelligence  # noqa: E402  # RB-2026-09-01 — listVendors (the canonical tracked-vendor list)
import competitive_landscape  # noqa: E402  # RB-2026-09-01 — market share / battle cards across the tech stack
import weekly_plan_generator  # noqa: E402  # RB-2026-07-15 — weekly plan draft confirmation
import identity_match_review  # noqa: E402  # inbound-email <-> baseline identity confirmations
import eolms  # noqa: E402  # RB-DEFECT-060 — evidence-backed EOLMS loop closure
import personal_log  # noqa: E402  # RB-DEFECT-062 — Life Lens personal-practice logging
import campaign_engine  # noqa: E402  # Conference Campaign Intelligence Engine
import cockpit_context  # noqa: E402  # 2026-08-25 — live cockpit read endpoint (gate 4)
import audit_log as al  # noqa: E402  # 2026-08-25 — mutation-event ledger wiring
from intelligence_observability import build_intelligence_health_dashboard  # noqa: E402
try:
    import job_intelligence as _job_intel  # noqa: E402
    _HAS_JOB_INTEL = True
except Exception:  # noqa: BLE001
    _HAS_JOB_INTEL = False

import logging
import time

try:
    from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from pydantic import BaseModel, Field
except ImportError:
    sys.stderr.write(
        "fastapi not installed. Install with:\n"
        "  pip install fastapi uvicorn --break-system-packages\n"
    )
    sys.exit(2)

# ── Request / response logger ──────────────────────────────────────────────
_REQUEST_LOG = Path(os.environ.get("RB_REQUEST_LOG_PATH", str(SYSTEM_DIR / "api" / "request.log")))
logging.basicConfig(level=logging.WARNING)
_req_logger = logging.getLogger("rb.requests")
_req_logger.setLevel(logging.DEBUG)
_fh = logging.FileHandler(_REQUEST_LOG, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
_req_logger.addHandler(_fh)


API_KEY = os.environ.get("RB_API_KEY")
if not API_KEY:
    sys.stderr.write(
        "WARNING: RB_API_KEY is not set; the API will run without auth.\n"
        "         Set RB_API_KEY in the environment before exposing this service.\n"
    )


# RB-SECURITY-2026-09-03: the default docs_url/openapi_url/redoc_url exposed
# the full internal API surface (all real routes, not just the curated GPT
# schema at /openapi-gpt.yaml) publicly and unauthenticated over the
# Cloudflare tunnel. Disabled here; gated replacements are registered below
# with the same _auth() check every other route uses.
app = FastAPI(
    title="Relationship Builder API",
    version="0.1.0",
    description=(
        "HTTP wrapper around the deterministic Relationship Builder compute "
        "scripts. Designed to be the Actions backend for a Custom GPT. Read-only."
    ),
    servers=[{"url": "http://localhost:8765", "description": "local dev"}],
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# RB-SECURITY-2026-09-03: no request body size was previously enforced
# anywhere (app or uvicorn level) -- an authenticated-or-not caller could
# POST an unbounded body and the server would try to hold/decode all of it.
# 200MB comfortably covers real base64-inflated LinkedIn/WhatsApp exports
# (the largest legitimate uploads) while capping the worst case.
_MAX_REQUEST_BODY_BYTES = int(os.environ.get("RB_MAX_REQUEST_BODY_BYTES", 200 * 1024 * 1024))


class _MaxBodySize(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > _MAX_REQUEST_BODY_BYTES:
                    return Response(status_code=413, content="request body too large")
            except ValueError:
                pass
        return await call_next(request)


app.add_middleware(_MaxBodySize)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten for production
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class _RequestLogger(BaseHTTPMiddleware):
    """Log every inbound request: method, path, query, auth presence, status."""

    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        has_key = bool(
            request.headers.get("x-api-key")
            or request.headers.get("X-Api-Key")
            or request.headers.get("authorization")
        )
        response: Response = await call_next(request)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        _req_logger.info(
            "%s %s?%s auth=%s status=%d %dms",
            request.method,
            request.url.path,
            request.url.query,
            "YES" if has_key else "NO",
            response.status_code,
            elapsed_ms,
        )
        return response


app.add_middleware(_RequestLogger)


def _auth(key_header: Optional[str]) -> None:
    """Authenticate the request against the configured API key.

    RB-DEFECT-033: returns a structured error body so the caller — and any
    agent reasoning about the failure — can self-diagnose without guessing.

    Distinguishes two failure modes with different recovery paths:
      missing_x_api_key  — header was absent entirely (client-side transmission
                           gap: confirm your Action/client attaches the header
                           to *every* call, not just some)
      invalid_x_api_key  — header was present but the value didn't match
                           (configuration issue: check the key value in your
                           Custom GPT Action settings)

    The `auth_present` bool lets a caller immediately distinguish
    "I forgot to send the header" (False) from "I sent the wrong value" (True).
    """
    if not API_KEY:
        # RB-SECURITY-2026-09-03: was fail-open ("no key configured -> pass
        # through"), meaning a missing RB_API_KEY silently opened every route
        # on an internet-exposed server with no warning beyond a log line
        # indistinguishable from normal traffic. Fail closed instead -- an
        # unconfigured key should take the API down, not open it up.
        raise HTTPException(
            status_code=503,
            detail={
                "error": "server_misconfigured",
                "cause": "RB_API_KEY is not set on the server",
                "note": "Refusing all requests until RB_API_KEY is configured. "
                        "This is a server-side configuration issue, not a client fault.",
            },
        )
    header_present = key_header is not None
    if key_header != API_KEY:
        cause = "invalid_x_api_key" if header_present else "missing_x_api_key"
        recovery = (
            "The x-api-key header was present but did not match the configured "
            "RB API key. Check the key value in your Custom GPT Action settings."
            if header_present else
            "The x-api-key header was absent on this request. This is a "
            "per-request header — confirm your client attaches it to every "
            "Action call, not just some. getDailyBrief succeeding while "
            "getLoops fails in the same session is the classic symptom."
        )
        raise HTTPException(
            status_code=401,
            detail={
                "error": "unauthorized",
                "cause": cause,
                "auth_present": header_present,
                "recovery": recovery,
                "note": (
                    "This is a client-side configuration issue, not a server "
                    "or endpoint fault. The endpoint is healthy — retry with "
                    "the correct x-api-key header."
                ),
            },
        )


# RB-SECURITY-2026-09-03: gated replacements for the disabled default
# docs_url/openapi_url/redoc_url (see the FastAPI(...) constructor above) --
# same content, same _auth() check every other route on this server uses.
@app.get("/openapi.json", tags=["meta"], include_in_schema=False)
def get_openapi_json(x_api_key: Optional[str] = Header(None), x_api_key_q: Optional[str] = Query(None, alias="x_api_key")):
    # Swagger UI's own browser-side fetch to this URL can't attach a custom
    # header, so /docs below embeds the key as a query param instead -- a
    # header still works too for any other caller.
    _auth(x_api_key or x_api_key_q)
    from fastapi.openapi.utils import get_openapi as _get_openapi
    return _get_openapi(
        title=app.title, version=app.version, description=app.description, routes=app.routes,
    )


@app.get("/docs", tags=["meta"], include_in_schema=False)
def get_docs(x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    from fastapi.openapi.docs import get_swagger_ui_html
    return get_swagger_ui_html(openapi_url="/openapi.json?x_api_key=" + (x_api_key or ""), title=app.title + " - Docs")


def _public_refresh_result(row: dict) -> dict:
    """Return a GPT/user-safe refresh result without local paths or commands."""
    source = row.get("source")
    status = row.get("status")
    out = {"source": source, "status": status}
    if status == "skipped_no_raw_input":
        out["reason"] = "source input unavailable to RB"
        out["next_step"] = "connect or capture this source, then ask RB to refresh again"
    elif status == "not_applicable_on_platform":
        out["reason"] = "not applicable on this host"
    elif status == "unavailable":
        out["reason"] = "source unavailable"
    elif status == "failed":
        out["reason"] = "refresh failed"
    elif status == "refreshed":
        out["reason"] = "refreshed"
    return out


def _google_oauth_status() -> dict:
    """Report durable Google credential readiness without exposing secrets."""
    cfg_dir = Path.home() / ".config" / "rb"
    client_path = Path(os.environ.get("RB_GOOGLE_CLIENT", str(cfg_dir / "google_client.json")))
    accounts = []
    try:
        configured = core.load_inbox_accounts()
    except Exception:  # noqa: BLE001
        configured = []
    for acct in configured:
        if not acct.get("enabled", True):
            continue
        feeds = acct.get("feeds") or []
        if isinstance(feeds, str):
            feeds = [f.strip(" '\"") for f in feeds.strip("[]").split(",")]
        if "email" not in feeds and "calendar" not in feeds:
            continue
        acct_id = acct.get("id") or "unknown"
        token_path = cfg_dir / f"google_token.{acct_id}.json"
        accounts.append({
            "account_id": acct_id,
            "email": acct.get("email"),
            "feeds": feeds,
            "durable_token_present": token_path.exists(),
        })
    return {
        "oauth_client_present": client_path.exists(),
        "accounts": accounts,
    }


def _public_google_fetch(proc: subprocess.CompletedProcess[str]) -> dict:
    """Sanitized summary of the durable Google fetch attempt."""
    status = "refreshed" if proc.returncode == 0 else "not_ready"
    combined = "\n".join([proc.stderr or "", proc.stdout or ""])
    detail = "\n".join(line for line in combined.splitlines()[-6:] if line.strip())
    safe_detail = None
    if proc.returncode == 0:
        safe_detail = "durable Google fetch completed"
    elif combined:
        if "No durable Google token" in combined:
            safe_detail = "durable Google token missing for at least one configured account"
        elif "No OAuth client" in combined:
            safe_detail = "Google OAuth client is missing from the RB daemon configuration"
        elif "authenticated as" in combined:
            safe_detail = "a durable Google token is authenticated to a different account than accounts.yaml expects"
        elif "Google API dependencies not installed" in combined or "No module named 'googleapiclient'" in combined:
            safe_detail = "Google API dependencies are not installed for the RB daemon Python"
        elif detail:
            safe_detail = "durable Google fetch did not complete"
    return {
        "status": status,
        "exit_code": proc.returncode,
        "reason": safe_detail,
    }


def _published_daily_brief_payload(d: date, part: int = 1) -> Optional[dict]:
    """Return the compact published brief artifact for `d` when available.

    The Custom GPT action path should not have to ingest the full raw
    daily_brief.build_report payload on the common morning path. The published
    artifact is the canonical user-facing brief and is roughly half the size of
    the raw report.
    """
    candidates = [
        SYSTEM_DIR / "published" / "daily" / d.isoformat() / "brief.json",
        SYSTEM_DIR / "published" / "daily" / "latest_brief.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if payload.get("today") == d.isoformat() and payload.get("canonical_brief"):
            action_payload = _daily_brief_action_payload(
                payload.get("canonical_brief") or {},
                d,
                source="published_brief_artifact",
                source_path=str(path.relative_to(SYSTEM_DIR.parent)),
                generated_at=payload.get("generated_at"),
                counts=payload.get("counts"),
                execution_report=_latest_execution_report(d),
                part=part,
            )
            return _attach_pre_rendered_brief(action_payload, d, part=part)
    return None


def _latest_execution_report(d: date) -> Optional[dict]:
    """Return the same-day intelligence refresh receipt for brief delivery."""
    path = SYSTEM_DIR / ".cache" / "morning_pipeline.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    report = payload.get("execution_report")
    if not isinstance(report, dict) or report.get("date") != d.isoformat():
        return None
    keep = (
        "refresh_status",
        "generated_at",
        "date",
        "mode",
        "sources_processed",
        "records_processed",
        "records_added",
        "records_changed",
        "mutations_generated",
        "opportunities_generated",
        "relationship_changes",
        "trust_score",
        "trust_level",
        "stale_sources",
        "processing_errors",
        "brief_rebuilt",
        "intelligence_readiness",
        "intelligence_health_dashboard",
    )
    return {key: report.get(key) for key in keep if key in report}


# RB 9.69 — Two-part brief delivery (DEFECT report 2026-06-10).
#
# A single getDailyBrief response was approaching the ~70KB Action payload
# ceiling, forcing _enforce_action_payload_budget to silently trim whichever
# section happened to be largest — not whichever was least important. That
# made the canonical template's "negative reporting" mandate (every watchlist
# entity must appear, even to say "no signal") structurally impossible, and
# put new sections (day_ahead, weekly_plan_focus) at risk of eviction on busy
# days.
#
# The brief is now split into two calls that mirror the canonical template's
# own two halves:
#   Part 1 (getDailyBrief)      — Proof Line + Section 1 (What Changed /
#                                  Executive Summary) + Section 2 (Market &
#                                  Macro Intelligence). The "front page".
#   Part 2 (getDailyBriefPart2) — Section 3 (Restaurant Technology Radar,
#                                  with room for full watchlist coverage) +
#                                  Section 4 (My Priorities — calendar,
#                                  opportunities, loops, weekly goals, CoS
#                                  recommendations). The "operating console".
#
# Part 1's payload includes a `continuation` block instructing the GPT to
# offer Part 2 once Part 1 has been presented.
_ACTION_BRIEF_SECTIONS_PART1 = [
    # RB 9.62 — Canonical newspaper-style CoS brief (4-section structure).
    # Order matches DAILY_BRIEF_CANONICAL_TEMPLATE.md exactly.
    # brief_status_header EXCLUDED: "DAILY BRIEF STATUS: DEGRADED" triggers
    # ChatGPT false-positive error detection. Trust info lives in trust_score.
    # information_debt_queue EXCLUDED: "INFORMATION DEBT"/"blocked" titles
    # trigger false-positive error detection. IDQ handled in brief_status_header.
    #
    # ── Proof Line source ────────────────────────────────────────────────────
    "intelligence_collection_summary",       # Proof: N sources scanned / mutations / stale
    "resource_verification_and_freshness_status",  # IB-2: email/SMS/calendar/calls health + counts
    "communication_intelligence",            # IB-2: emails awaiting response, calendar changes
    #
    # ── Section 1: Executive Summary ─────────────────────────────────────────
    # "What You Didn't Know Before Opening This" — OBSERVED items only.
    "what_todd_doesnt_know_yet",         # Newspaper front page: top NEW lifecycle items
    "what_changed_since_yesterday",      # Delta only — 2–4 sentences
    "personal_intelligence_delta",       # Email/SMS/calendar/opportunity mutations in last 24h
    "overnight_delta_intelligence",      # Autonomous discovery from pipeline
    "what_rb_found_without_you_telling_it",  # High/medium autonomous discovery evidence
    #
    # ── Section 2: Market Intelligence ───────────────────────────────────────
    "world_national_headlines",          # Global + national news filtered for relevance
    "world_macro_macroeconomic_impact",  # Labor, rates, energy, consumer macro signals
    "macro_pressure_stack",              # Aggregated macro pressure on operators
    "restaurant_industry_headlines",     # Industry news — QSR, casual dining, franchise
    "restaurant_pain_mapping",           # What pains operators are experiencing now
    #
    #
    # ── Section D: Restaurant Technology (Part 1) ────────────────────────────
    "restaurant_technology_headlines",   # Section D — tech headlines, newspaper format
    "newsletter_intelligence",           # Section D+: articles extracted from inbox newsletters
    #
    # ── Section F: Watchlist Scan (Part 1) ───────────────────────────────────
    "watchlist_intelligence",            # Section F — per-entity scan results + no-change rollups
    "competitive_vulnerability_watchlist",  # Section F addendum — RFP early-warning signals
    "downstream_artifact_cascade",       # Section F addendum — proof new intel was assessed against Blue Sheets/Account Research
    #
    # ── Section G: Personal Intelligence (Part 1) ────────────────────────────
    # Factual snapshot only — career status, key loops, relationship signals.
    # CoS synthesis (recommendations, priorities) belongs in Part 2.
    "opportunity_board",                 # Active career/biz opportunities — status + evidence age
    "w2_intelligence",                   # Job tracker — active roles with state + waiting_on
    "last_24h_relationship_signals",     # New relationship activity — named contacts
    "relationship_momentum_status",      # FROZEN/COLD contacts + overdue touchpoints
    #
    # ── Closing (Part 1) ──────────────────────────────────────────────────────
    "trust_metrics",                     # Source summary — quick confidence read
]

_ACTION_BRIEF_SECTIONS_PART2 = [
    # ── Pre-Section: User Active Context (dot-connect reference) ─────────────
    # Loaded FIRST so GPT has explicit role/thesis/expertise before any signal.
    "user_active_context",               # Current role, strategic thesis, domain expertise
    #
    # ── Section 3: Restaurant Technology Radar ───────────────────────────────
    "restaurant_technology_headlines",   # Tech news, signal-classified + badged
    "watchlist_intelligence",            # Per-entity monitoring (100+ entities)
    "competitive_vulnerability_watchlist",  # Competitive Opportunity Watchlist (RFP early warning)
    "downstream_artifact_cascade",       # Proof new intel was assessed against Blue Sheets/Account Research
    "strategic_industry_signals",        # High-confidence strategic signals
    "email_intelligence_harvest",        # Newsletter headlines with strategic implications
    #
    # ── Section 4: My Priorities (action close) ───────────────────────────────
    "my_priorities",                     # Aggregated: calendar/email/loops/opps/goals/CoS
    # Supporting context for Section 4 rendering — kept for GPT depth:
    "day_ahead",                         # Calendar events + prep questions (CALENDAR sub-section)
    "loops_and_obligations",             # Due today + overdue loops
    "opportunity_board",                 # Active/waiting opportunities with evidence dates
    "w2_intelligence",                   # Active job opportunity tracker
    # job_intelligence: gated by opportunity_context.yaml → job_search_active.
    # Present in payload only when gate is on; empty list when off.
    "job_intelligence",                  # Job market scan — matching roles, fit scores
    "weekly_plan_focus",                 # This week's outcomes (WEEKLY GOALS sub-section)
    "cos_today",                         # If I Were Your CoS Today — CoS recommendations
    "this_week_priorities",              # RB 9.79 — This Week: 2-7 day priorities
    "this_month_priorities",             # RB 9.83 — This Month: 8-30 day strategic priorities
    "upcoming_preparation_requirements",  # RB 9.77 — prep-time estimates by horizon
    "learned_patterns",                  # RB 9.84 — recurring prep-time patterns
    "decision_layer",                    # Top-3 highest-ROI decisions
    "strategic_risks",                   # RB 9.78 — Risks: standalone synthesis
    #
    # ── Relationship intelligence (Section 4 supporting context) ─────────────
    "last_24h_relationship_signals",     # New relationship activity — named contacts
    "relationship_momentum_status",      # FROZEN/COLD RC contacts + SMS overrides
    #
    # ── Synthesis & mutations ─────────────────────────────────────────────────
    "horizon_watch",                     # RB 9.82 — Horizon Watch: 30-90 day developments
    "connect_the_dots",                  # Cross-domain convergence signals
    "pending_graph_mutations",           # LinkedIn-detected company/role changes
    "pending_mutations",                 # RB 9.70 — proposed interactions awaiting confirmation >12h
]

# Back-compat alias: anything that needs "all sections" (e.g. tests) can still
# iterate the union in template order.
_ACTION_BRIEF_SECTIONS = _ACTION_BRIEF_SECTIONS_PART1 + _ACTION_BRIEF_SECTIONS_PART2


def _truncate_value(value: object, limit: int = 260) -> object:
    if not isinstance(value, str):
        return value
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _compact_item(item: object) -> object:
    if not isinstance(item, dict):
        return _truncate_value(item)
    keep = [
        "title",
        "summary",
        "why_it_matters",
        "recommended_action",
        "disposition",
        "grounding",
        "freshness",
        "confidence",
        "source_refs",
        # Layer 1 / DEFECT-027: lifecycle state needed for GPT suppression filter
        # and provenance display. Small fields (~80 chars each).
        "intelligence_lifecycle",
        "attribution_type",
    ]
    out: dict = {}
    for key in keep:
        if key not in item:
            continue
        value = item.get(key)
        if key == "source_refs" and isinstance(value, list):
            out[key] = [_truncate_value(v, 120) for v in value[:2]]
        elif key == "title":
            out[key] = _truncate_value(value, 180)
        elif key == "intelligence_lifecycle" and isinstance(value, dict):
            # The full lifecycle receipt can contain suppression/reactivation
            # metadata that is useful internally but expensive in the Action
            # response. The GPT only needs state and first-seen date to apply
            # the NEW/UPDATED suppression contract.
            out[key] = {
                lifecycle_key: value.get(lifecycle_key)
                for lifecycle_key in ("state", "first_seen")
                if value.get(lifecycle_key) is not None
            }
        else:
            out[key] = _truncate_value(value)
    return out


def _compact_item_with_extras(item: object, extras_keys: list[str]) -> object:
    """Like _compact_item but also preserves specified extras keys."""
    out = _compact_item(item)
    if isinstance(item, dict) and isinstance(item.get("extras"), dict):
        extras_out = {}
        for k in extras_keys:
            if k in item["extras"]:
                v = item["extras"][k]
                # Keep scalar values and short lists as-is; truncate strings
                if isinstance(v, (bool, int, float)) or v is None:
                    extras_out[k] = v
                elif isinstance(v, str):
                    extras_out[k] = _truncate_value(v, 200)
                elif isinstance(v, list) and len(v) <= 20:
                    extras_out[k] = v
                elif k == "no_change_entities" and isinstance(v, list):
                    # RB-DEFECT-1 (item 4): the "No Change" rollup carries
                    # every mandatory-coverage entity with no signal — this
                    # list can exceed 50 entries and must NOT be silently
                    # dropped, or the negative-reporting coverage proof is
                    # lost for most of the watchlist. Pass through in full
                    # (entity names are short; ~50 names is still small).
                    extras_out[k] = v
                elif isinstance(v, dict):
                    extras_out[k] = {ek: _truncate_value(ev, 120)
                                     for ek, ev in list(v.items())[:8]}
        if extras_out:
            out["extras"] = extras_out
    return out


# Extras keys to preserve per section (for GPT to read CoS intelligence)
_SECTION_EXTRAS_KEYS: dict[str, list[str]] = {
    # RB 10.x — User Active Context: all fields passed through so GPT has full
    # dot-connect reference without needing to infer from scattered sections.
    "user_active_context": [
        "entering_company", "active_opportunities", "strategic_theses",
        "market_thesis", "domain_expertise", "career_arc",
        "active_priorities", "operating_principles",
    ],
    "brief_status_header": ["brief_status", "trust_score", "operational_confidence",
                             "stale_sources", "recovery_actions", "blocked_count"],
    # intelligence_collection_summary: named source list + key metrics so the GPT
    # can render a verifiable proof block ("Reuters: 12 articles | AP: 8 articles…")
    # instead of generic "major publications scanned."
    "intelligence_collection_summary": ["sources_scanned", "sources_attempted",
                                         "sources_healthy", "sources_stale",
                                         "sources_failed", "freshness_pct",
                                         "trust_score", "collection_health",
                                         "personal_intel_proof",
                                         "web_scanner_world_sources",
                                         "web_scanner_industry_sources",
                                         "web_scanner_tech_sources",
                                         "web_scanner_world_count",
                                         "web_scanner_industry_count",
                                         "web_scanner_tech_count"],
    # resource_verification_and_freshness_status: surface SMS resolution queue so the
    # GPT can present unregistered phone numbers for user action (IB-2 CoS behavior).
    "resource_verification_and_freshness_status": ["direct_comms_state", "event_count",
                                                    "matched_count", "unmatched_count",
                                                    "top_unmatched_handles",
                                                    "unmatched_handle_count",
                                                    "two_way_count"],
    # RB-DEFECT-001: these sections carry a real, clickable `source_url` in
    # `extras` (computed during daily_brief.build_report), but were previously
    # dropped entirely by compaction because they had no _SECTION_EXTRAS_KEYS
    # entry — the GPT instructions mandate `[Source] — [Title](extras.source_url)`
    # but the field never reached the payload. Headline sections also carry
    # `domain`/`signal_badge`/`signal_type`/`implication`/`macro_category` used
    # by the rendering rules in custom_gpt_instructions_compact_8k.md.
    "world_national_headlines":      ["source_url", "pub_date", "domain", "signal_badge",
                                       "signal_type", "implication", "macro_category"],
    "restaurant_industry_headlines": ["source_url", "pub_date", "domain", "signal_badge",
                                       "signal_type", "implication", "macro_category"],
    "restaurant_technology_headlines": ["source_url", "pub_date", "domain", "signal_badge",
                                         "signal_type", "implication", "macro_category"],
    # Newsletter-derived items: `source_url` is the article link when the email
    # cache has one, else a Gmail permalink to the source email
    # (`source_url_type` distinguishes "article" vs "email").
    "email_intelligence_harvest": ["source_publication", "received_at", "category",
                                    "source_url", "source_url_type",
                                    "opportunity_links", "contact_links"],
    "what_todd_doesnt_know_yet":  ["source_publication", "received_at", "category",
                                    "source_url", "source_url_type",
                                    "opportunity_links", "contact_links",
                                    "what_todd_doesnt_know_yet"],
    "cos_today":           ["actions"],
    "day_ahead":           ["event_title", "start_time", "attendees",
                             "network_connections", "prep_questions"],
    "weekly_plan_focus":   ["allocation_pct", "status", "priority_category"],
    "w2_intelligence":     ["opportunity_state", "evidence_age_days", "stall_probability",
                             "next_review", "waiting_on", "probability"],
    "decision_layer":      ["source_section", "opportunity_cost", "decision_rank"],
    "opportunity_board":   ["state", "probability", "evidence_age_days", "waiting_on",
                             "evidence_source", "latest_activity"],
    "five_things_today":   ["source_section", "priority_score", "rank"],
    "loops_and_obligations": ["loop_id", "contact_name", "days_quiet"],
    "relationship_momentum_status": ["momentum_tier", "days_quiet", "open_loop_count",
                                      "sms_touch", "contact_id"],
    "connect_the_dots":    ["convergence_type", "companies", "opportunity_company"],
    # RB-DEFECT-1 (item 4) — per-entity watchlist status + "No Change" rollup.
    # `no_change_entities`/`no_change_count` carry the full mandatory-coverage
    # entity list for the rollup items so all ~100 entities can be rendered
    # ("[ENTITY] — No signals detected this cycle.") without each consuming a
    # compaction slot.
    "watchlist_intelligence": ["entity_name", "entity_type", "watchlist_status",
                                "evidence_headline", "category", "status_changed",
                                "no_change_entities", "no_change_count"],
    # RB-DEFECT-1 (item 3) — Competitive Opportunity Watchlist.
    "competitive_vulnerability_watchlist": [
        "entity_name", "tier", "vulnerability_score", "estimated_horizon_months",
        "rfp_detected", "potential_categories", "signals",
    ],
    # RB-2026-08-28 — Intelligence-gathering cascade proof item.
    "downstream_artifact_cascade": [
        "entities_with_new_intelligence", "relationships_touched",
        "blue_sheets_auto_synced", "blue_sheets_queued_for_review",
        "account_research_flagged_stale", "account_research_auto_processed",
        "master_account_plans_auto_synced", "master_account_plans_queued_for_review",
        "coverage_gaps_today", "coverage_gaps_14d_pattern",
    ],
    # RB 9.81 — RB-DEFECT-044 wiring: these sections were computed and added
    # to brief_display_order in RB 9.70/9.77-9.79 but never reached the GPT
    # payload (missing from _ACTION_BRIEF_SECTIONS_PART2 and here).
    "pending_mutations": ["pending_count", "pending_interaction_ids",
                           "pending_contacts", "oldest_created_at"],
    "upcoming_preparation_requirements": ["horizon", "prep_minutes",
                                           "required_inputs", "event_start",
                                           "event_id", "loop_id"],
    "strategic_risks": ["contact_count", "event_count", "overdue_count"],
    "this_week_priorities": ["event_start", "event_id", "loop_id"],
    # RB 9.83 — RB-DEFECT-044 "This Month" (8-30 day strategic priorities).
    "this_month_priorities": ["loop_id", "target_date", "days_until_report",
                               "estimated_report_date", "company",
                               "evidence_age_days"],
    # RB 9.82 — RB-DEFECT-044 "Horizon Watch (30-90 Days)".
    "horizon_watch": ["horizon_days", "estimated_date", "company",
                      "estimated_horizon_months", "entity_name"],
    # RB 9.84 — RB-DEFECT-044 "Learned Patterns" (recurring prep-time patterns).
    "learned_patterns": ["category", "prep_minutes", "occurrences", "days_tracked"],
}


def _compact_section(value: object, *, limit: int = 5,
                     section_name: str = "") -> object:
    if isinstance(value, list):
        extras_keys = _SECTION_EXTRAS_KEYS.get(section_name, [])
        if extras_keys:
            return [_compact_item_with_extras(item, extras_keys) for item in value[:limit]]
        return [_compact_item(item) for item in value[:limit]]
    if isinstance(value, dict):
        return {k: _compact_section(v, limit=limit) for k, v in value.items()}
    return _truncate_value(value)


def _compact_active_knowledge_asset(item: object) -> object:
    """Preserve micro-graph mount fields required by the Custom GPT bootstrap."""
    if not isinstance(item, dict):
        return _compact_item(item)
    extras = item.get("extras") if isinstance(item.get("extras"), dict) else {}
    out = _compact_item(item)
    for key in (
        "asset_id",
        "asset_type",
        "graph_slug",
        "activation_terms",
        "retrieval_action",
        "retrieval_params",
        "routing_rule",
        "context_boost_window",
        "graceful_failure_mode",
        "status",
        "freshness_date",
        "node_count",
        "edge_count",
    ):
        if key in extras:
            out[key] = _truncate_value(extras.get(key))
        elif key in item:
            out[key] = _truncate_value(item.get(key))
    # RB 9.42 / DEFECT-026: Preserve precomputed_answers — NOT truncated since
    # it's a structured dict containing the Tier 1 operator_count answer.
    pa = extras.get("precomputed_answers") or item.get("precomputed_answers")
    if pa and isinstance(pa, dict):
        out["precomputed_answers"] = pa
    return out


def _compact_active_knowledge_assets(canonical: dict) -> list:
    assets = canonical.get("active_knowledge_assets")
    if assets is None:
        assets = (canonical.get("sections") or {}).get("active_knowledge_assets")
    if not isinstance(assets, list):
        return []
    return [_compact_active_knowledge_asset(asset) for asset in assets]


def _session_bootstrap_status(canonical: dict, active_assets: list) -> dict:
    """Tiny GPT-readable mount summary for session initialization."""
    mounted_graphs = []
    for asset in active_assets:
        if not isinstance(asset, dict):
            continue
        title = asset.get("title") or ""
        entity = title.split(" Micro Graph", 1)[0].replace(" — MOUNTED FOR SESSION", "").strip()
        mounted_graphs.append({
            "entity": entity or asset.get("graph_slug") or asset.get("asset_id"),
            "status": "MOUNTED",
            "retrieval_action": asset.get("retrieval_action"),
            "activation_terms": asset.get("activation_terms") or [],
            "required_for": "entity topology/count/operator/franchisee/store questions",
        })
    return {
        "contract": "rb_session_bootstrap_status_v1",
        "micro_graphs_mounted_count": len(mounted_graphs),
        "micro_graphs_mounted": mounted_graphs,
        "retrieval_hierarchy_active": bool(canonical.get("knowledge_retrieval_hierarchy")),
        "intelligence_pipeline_active": bool(canonical.get("intelligence_pipeline_spec")),
        "session_bootstrap_spec_active": bool(canonical.get("session_bootstrap_spec")),
        "degraded": len(mounted_graphs) == 0,
        "bootstrap_instruction": (
            "Use this object for the Micro Graphs line. If micro_graphs_mounted_count > 0, "
            "list each entity as [MOUNTED]. Do not report 'none mounted'."
        ),
    }


def _inject_web_scanner_feed_counts(ics_item: dict) -> None:
    """RB 10.0: Inject per-publication web scanner feed counts into the first
    intelligence_collection_summary item so the GPT can render a named-source
    proof block instead of generic 'major publications scanned' labels.

    Reads the web_scanner_cache.json (6-hour TTL cache written by daily_brief.py)
    and aggregates item counts by domain bucket (world_national / restaurant_industry
    / restaurant_technology) + lists named sources for each bucket.
    """
    _WS_CACHE = SYSTEM_DIR / ".cache" / "web_scanner_cache.json"
    if not _WS_CACHE.exists():
        return
    try:
        cache = json.loads(_WS_CACHE.read_text())
    except Exception:
        return

    world_sources: list[str] = []
    industry_sources: list[str] = []
    tech_sources: list[str] = []
    world_count = industry_count = tech_count = 0

    for feed_name, feed_data in cache.items():
        if not isinstance(feed_data, dict):
            continue
        items = feed_data.get("items") or []
        if not items:
            continue
        # Determine domain from first item's domain field; fall back to feed name heuristics
        domain = None
        for item in items[:3]:
            if isinstance(item, dict) and item.get("domain"):
                domain = item["domain"]
                break
        if domain is None:
            ln = feed_name.lower()
            if any(w in ln for w in ("restaurant", "qsr", "foodservice", "nrn", "nation")):
                domain = "restaurant_industry"
            elif any(w in ln for w in ("tech", "toast", "pos", "payment")):
                domain = "restaurant_technology"
            else:
                domain = "world_national"
        n = len(items)
        if domain == "restaurant_industry":
            industry_sources.append(f"{feed_name}: {n}")
            industry_count += n
        elif domain == "restaurant_technology":
            tech_sources.append(f"{feed_name}: {n}")
            tech_count += n
        else:
            world_sources.append(f"{feed_name}: {n}")
            world_count += n

    if not (world_count or industry_count or tech_count):
        return

    extras = ics_item.setdefault("extras", {})
    extras["web_scanner_world_sources"] = world_sources[:10]
    extras["web_scanner_industry_sources"] = industry_sources[:10]
    extras["web_scanner_tech_sources"] = tech_sources[:10]
    extras["web_scanner_world_count"] = world_count
    extras["web_scanner_industry_count"] = industry_count
    extras["web_scanner_tech_count"] = tech_count
    extras["web_scanner_total_articles"] = world_count + industry_count + tech_count


_PROOF_SNAPSHOT_PATH = SYSTEM_DIR / ".cache" / "proof_snapshot.json"


def _load_proof_snapshot() -> dict:
    try:
        return json.loads(_PROOF_SNAPSHOT_PATH.read_text())
    except Exception:
        return {}


def _save_proof_snapshot(counts: dict) -> None:
    try:
        _PROOF_SNAPSHOT_PATH.write_text(json.dumps(counts, indent=2))
    except Exception:
        pass


def _delta(today: int, yesterday: int) -> int:
    return today - yesterday


def _build_proof_dashboard(compact_canonical: dict) -> dict:
    """Build a personal-data delta dashboard at the top level of getDailyBrief.

    The user wants proof the personal intelligence system ran and learned —
    not proof the news fetcher ran. Primary section: personal data delta
    (email, SMS, calls, calendar, contacts, mutations). Secondary: news scan totals.
    """
    extras: dict = {}
    ics = (compact_canonical.get("sections") or {}).get("intelligence_collection_summary") or []
    if ics:
        extras = ics[0].get("extras") or {}
    personal = extras.get("personal_intel_proof") or {}

    # Personal counts — today
    email_accts = personal.get("email_accounts") or []
    n_email_accounts = len(email_accts) if isinstance(email_accts, list) else int(email_accts or 0)
    email_threads_today = personal.get("email_threads_total", 0)
    sms_today = personal.get("sms_events_total", 0)
    calls_today = personal.get("calls_events_total", 0)
    cal_today = personal.get("calendar_events_total", 0)
    mutations_today = extras.get("mutations_generated", 0) or 0

    # Contacts from contact index
    contacts_today = 0
    try:
        ci_path = SYSTEM_DIR / ".cache" / "contact_index.json"
        if ci_path.exists():
            ci = json.loads(ci_path.read_text())
            contacts_today = ci.get("contact_count") or len(ci.get("contacts") or [])
    except Exception:
        pass

    # Watchlist entity count from section rollup
    watchlist_scanned = 0
    wl_section = (compact_canonical.get("sections") or {}).get("watchlist_intelligence") or []
    for item in wl_section:
        nc = (item.get("extras") or {}).get("no_change_count", 0)
        watchlist_scanned += nc
    if watchlist_scanned == 0:
        watchlist_scanned = len(wl_section)

    # Delta vs yesterday's snapshot
    snap = _load_proof_snapshot()
    snap_date = snap.get("date", "")
    today_str = date.today().isoformat()
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    # Only use snapshot if it was saved on a prior date (not today's run)
    use_snap = snap_date and snap_date != today_str

    dashboard = {
        # Personal data (primary — proves the intelligence system ran)
        "email_accounts": n_email_accounts,
        "email_threads_today": email_threads_today,
        "email_threads_yesterday": snap.get("email_threads_today", 0) if use_snap else None,
        "email_threads_delta": _delta(email_threads_today, snap.get("email_threads_today", email_threads_today)) if use_snap else None,
        "sms_events_today": sms_today,
        "sms_events_yesterday": snap.get("sms_events_today", 0) if use_snap else None,
        "sms_events_delta": _delta(sms_today, snap.get("sms_events_today", sms_today)) if use_snap else None,
        "calls_today": calls_today,
        "calls_yesterday": snap.get("calls_today", 0) if use_snap else None,
        "calls_delta": _delta(calls_today, snap.get("calls_today", calls_today)) if use_snap else None,
        "calendar_events_today": cal_today,
        "calendar_events_yesterday": snap.get("calendar_events_today", 0) if use_snap else None,
        "calendar_events_delta": _delta(cal_today, snap.get("calendar_events_today", cal_today)) if use_snap else None,
        "contacts_total": contacts_today,
        "contacts_yesterday": snap.get("contacts_total", contacts_today) if use_snap else None,
        "contacts_delta": _delta(contacts_today, snap.get("contacts_total", contacts_today)) if use_snap else None,
        "mutations_today": mutations_today,
        "mutations_yesterday": snap.get("mutations_today", 0) if use_snap else None,
        "mutations_delta": _delta(mutations_today, snap.get("mutations_today", mutations_today)) if use_snap else None,
        "watchlist_entities_scanned": watchlist_scanned,
        "snapshot_date": snap_date or None,
        # News scan totals (secondary — proves news pipeline ran)
        "world_articles_scanned": extras.get("web_scanner_world_count", 0),
        "world_sources": extras.get("web_scanner_world_sources") or [],
        "restaurant_articles_scanned": extras.get("web_scanner_industry_count", 0),
        "restaurant_sources": extras.get("web_scanner_industry_sources") or [],
        "tech_articles_scanned": extras.get("web_scanner_tech_count", 0),
        "tech_sources": extras.get("web_scanner_tech_sources") or [],
        # Stale state
        "email_stale": personal.get("email_stale", False),
        "calendar_stale": personal.get("calendar_stale", False),
        "linkedin_messaging_stale": personal.get("linkedin_messaging_stale", False),
    }

    # Save today's counts as the new snapshot (for tomorrow's delta)
    _save_proof_snapshot({
        "date": today_str,
        "email_threads_today": email_threads_today,
        "sms_events_today": sms_today,
        "calls_today": calls_today,
        "calendar_events_today": cal_today,
        "contacts_total": contacts_today,
        "mutations_today": mutations_today,
    })

    return dashboard


def _compact_canonical_brief(canonical: dict, part: int = 1) -> dict:
    """Build the GPT-facing compact brief for `part` (1 or 2).

    RB 9.69: the brief is split across two Action calls (see
    _ACTION_BRIEF_SECTIONS_PART1/PART2 above). Each part gets its own section
    list and its own per-section item limits — Part 2 carries the bulk of the
    "operating console" content (technology radar + My Priorities) and is no
    longer competing with Part 1's headlines for the same 70KB budget, so its
    limits are higher to support the canonical template's negative-reporting
    coverage requirements.
    """
    sections = canonical.get("sections") or {}
    section_list = _ACTION_BRIEF_SECTIONS_PART1 if part == 1 else _ACTION_BRIEF_SECTIONS_PART2
    compact_sections: dict = {}
    for name in section_list:
        if name not in sections:
            continue
        # RB 9.68: hard Action-payload budget. The platform rejects oversized
        # responses before the GPT can render them, so this endpoint carries
        # only the highest-value subset of each canonical section.
        # Section 1 — Executive Summary: proof + delta + discoveries
        if name in {"intelligence_collection_summary"}:
            limit = 1   # proof line: single summary item
        elif name in {"what_changed_since_yesterday"}:
            limit = 3 if part == 1 else 1  # RB 10.4: lead section needs full delta set
        elif name in {"personal_intelligence_delta"}:
            limit = 5   # email + SMS + calendar + opportunity mutations + quiet
        elif name in {"newsletter_intelligence"}:
            limit = 6   # up to 6 newsletter editions per cycle
        elif name in {"what_todd_doesnt_know_yet"}:
            limit = 6   # newspaper front page: top 6 NEW items
        elif name in {"overnight_delta_intelligence", "what_rb_found_without_you_telling_it"}:
            limit = 3   # discovery: top 3 items
        # Section 2 — Market Intelligence
        elif name in {"world_national_headlines", "restaurant_industry_headlines"}:
            limit = 7   # headlines: min 5 required by canonical spec; 7 gives GPT selection room
        elif name in {"world_macro_macroeconomic_impact", "macro_pressure_stack",
                      "restaurant_pain_mapping"}:
            limit = 3   # macro/pain: top 3
        # Section 3 — Restaurant Technology Radar (Part 2 — more headroom)
        elif name in {"restaurant_technology_headlines"}:
            limit = 8 if part == 2 else 7
        elif name in {"watchlist_intelligence"}:
            # RB-DEFECT-1 (item 4): up to 3 "No Change" rollup items (one per
            # category bucket) lead the list and carry full coverage of the
            # ~85+ no-signal mandatory entities via extras.no_change_entities.
            # 15 leaves headroom for those rollups plus ~12 individual
            # signal items (Escalation/New/Relevant Activity).
            limit = 15 if part == 2 else 4
        elif name in {"strategic_industry_signals", "email_intelligence_harvest"}:
            limit = 4 if part == 2 else 3
        elif name in {"competitive_vulnerability_watchlist"}:
            limit = 6 if part == 2 else 3   # high-risk + emerging + watch tiers
        elif name in {"downstream_artifact_cascade"}:
            limit = 1   # single proof item, same pattern as intelligence_collection_summary
        # Section 4 — My Priorities (Part 2 — more headroom)
        elif name in {"my_priorities"}:
            limit = 12 if part == 2 else 10  # action close: top priority items
        elif name in {"day_ahead"}:
            limit = 5   # calendar prep: today + tomorrow + this-week highlights
        elif name in {"weekly_plan_focus"}:
            limit = 5   # weekly goals: all active outcomes
        elif name in {"loops_and_obligations", "last_24h_relationship_signals",
                      "pending_graph_mutations"}:
            limit = 5 if part == 2 else 3   # execution sections
        elif name in {"opportunity_board", "relationship_momentum_status",
                      "connect_the_dots"}:
            limit = 5 if part == 2 else 3   # intelligence sections
        elif name in {"cos_today", "w2_intelligence", "decision_layer",
                      "job_intelligence"}:
            limit = 4 if part == 2 else 3   # CoS supporting context + job scan
        elif name in {"this_week_priorities", "upcoming_preparation_requirements",
                      "strategic_risks", "pending_mutations"}:
            limit = 5 if part == 2 else 3   # RB 9.81 — newly-wired Daily Brief sections
        elif name in {"horizon_watch"}:
            limit = 5 if part == 2 else 3   # RB 9.82 — Horizon Watch (30-90 days)
        elif name in {"this_month_priorities"}:
            limit = 5 if part == 2 else 3   # RB 9.83 — This Month (8-30 days)
        elif name in {"learned_patterns"}:
            limit = 5 if part == 2 else 3   # RB 9.84 — Learned Patterns (recurring prep-time)
        else:
            limit = 3   # all others: trim to 3
        compact_sections[name] = _compact_section(sections.get(name), limit=limit,
                                                   section_name=name)

    # RB 10.0: Augment intelligence_collection_summary with per-feed web scanner
    # counts from the cache so the GPT can render a verifiable proof block showing
    # named publication sources and article counts instead of generic labels.
    if part == 1 and "intelligence_collection_summary" in compact_sections:
        ics = compact_sections["intelligence_collection_summary"]
        if ics:
            _inject_web_scanner_feed_counts(ics[0])

    # Part 1: enforce single-sentence watchlist — keep only material items + one rollup.
    # Per-entity "No material developments" rows are a persistent GPT failure pattern.
    if part == 1 and "watchlist_intelligence" in compact_sections:
        _wl_items = compact_sections["watchlist_intelligence"]
        _material: list[dict] = []
        _no_change: list[dict] = []
        for _item in _wl_items:
            _extras = _item.get("extras") or {}
            _has_url = bool(_extras.get("source_url"))
            _sig_type = _item.get("signal_type") or ""
            _no_signal_types = {"no_change", "no_signal", "rollup", ""}
            if _has_url or (_sig_type and _sig_type not in _no_signal_types):
                _material.append(_item)
            else:
                _no_change.append(_item)

        # Count total no-change entities (some items carry a list, others are 1 entity each)
        _nc_count = 0
        for _item in _no_change:
            _nc_list = (_item.get("extras") or {}).get("no_change_entities")
            _nc_count += len(_nc_list) if isinstance(_nc_list, list) else 1
        if _nc_count == 0 and _no_change:
            _nc_count = len(_no_change)

        # Build single rollup item replacing all no-change entries
        _rollup_parts = []
        if _nc_count:
            _rollup_parts.append(f"{_nc_count} entities scanned. No material developments.")
        if _rollup_parts:
            _rollup = {
                "title": "Watchlist: no material changes",
                "summary": " ".join(_rollup_parts),
                "signal_type": "rollup",
                "extras": {"no_change_count": _nc_count},
            }
            compact_sections["watchlist_intelligence"] = _material + [_rollup]
        else:
            compact_sections["watchlist_intelligence"] = _material

        # Strip no_change_entities from all remaining extras
        for _item in compact_sections["watchlist_intelligence"]:
            if isinstance(_item.get("extras"), dict):
                _item["extras"].pop("no_change_entities", None)

    active_assets = _compact_active_knowledge_assets(canonical)
    out = {
        "contract": canonical.get("contract") or "rb_canonical_daily_brief_v1",
        "section_order": [name for name in section_list if name in compact_sections],
        "rendering_rules": [],
        "sections": compact_sections,
        # Assets are returned once at the top level. Duplicating them here
        # added 6-8KB to every getDailyBrief response.
        "active_knowledge_assets": [],
        # payload_note removed — telling the GPT that "behavioral specs live in system
        # instructions" caused it to conclude it couldn't render the brief when no
        # knowledge files were loaded. The brief is self-contained; no external specs needed.
    }
    # NOTE: knowledge_retrieval_hierarchy, intelligence_pipeline_spec, and
    # session_bootstrap_spec are intentionally excluded from the compact action
    # payload. They are large JSON blobs (~6KB each) that the GPT already has
    # in its system instructions. Including them was causing the 83KB response
    # to exhaust the GPT's context budget, preventing follow-on action calls
    # (e.g. getMicroGraphSummary). RB 9.42 — DEFECT-026 fix.
    return out


_ACTION_PAYLOAD_MAX_BYTES = 70_000


def _json_size_bytes(value: object) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _enforce_action_payload_budget(payload: dict) -> dict:
    """Keep getDailyBrief below the platform response-size ceiling.

    Static per-section limits are the first line of defense. This final guard
    handles unusually dense days by removing trailing items from the largest
    populated sections while preserving every section's highest-ranked item.
    """
    sections = ((payload.get("canonical_brief") or {}).get("sections") or {})
    while _json_size_bytes(payload) > _ACTION_PAYLOAD_MAX_BYTES:
        candidates = [
            (name, value)
            for name, value in sections.items()
            if isinstance(value, list) and len(value) > 1
        ]
        if not candidates:
            break
        name, value = max(candidates, key=lambda pair: _json_size_bytes(pair[1]))
        value.pop()
        order = (payload.get("canonical_brief") or {}).get("section_order") or []
        if name not in order:
            sections.pop(name, None)
    return payload


_CONTINUATION_PART1 = {
    "part": "1_of_2",
    "next_action": "getDailyBriefPart2",
    "instruction": (
        "This is Part 1 of 2 — the Proof Line, What Changed Since Yesterday / "
        "Executive Summary, and Market & Macro Intelligence (today's "
        "headlines). Present this content first. Then ask the user: "
        "'That's today's headlines — want your full Operating Console next? "
        "(Restaurant Technology Radar with full watchlist coverage, calendar "
        "prep, active opportunities, open loops, and this week's priorities)' "
        "If the user confirms, call getDailyBriefPart2 with the same date and "
        "present it as Part 2 of the same brief — do not re-present Part 1 "
        "content."
    ),
}

_CONTINUATION_PART2 = {
    "part": "2_of_2",
    "next_action": None,
    "instruction": (
        "This is Part 2 of 2 — Restaurant Technology Radar (with watchlist "
        "negative-reporting coverage) and My Priorities (calendar, "
        "opportunities, loops, weekly goals, and CoS recommendations). This "
        "completes today's brief; do not re-present Part 1 content."
    ),
}


def _daily_brief_action_payload(
    canonical: dict,
    d: date,
    *,
    source: str,
    source_path: Optional[str] = None,
    generated_at: Optional[str] = None,
    counts: Optional[dict] = None,
    execution_report: Optional[dict] = None,
    part: int = 1,
) -> dict:
    active_assets = _compact_active_knowledge_assets(canonical)
    compact_canonical = _compact_canonical_brief(canonical, part=part)
    proof_dashboard = _build_proof_dashboard(compact_canonical) if part == 1 else None
    bootstrap_status = _session_bootstrap_status(
        canonical,
        active_assets,
    )
    # Extract brief confidence from canonical_brief top-level trust_score
    # (brief_status_header is excluded from compact payload to avoid false-positive
    # error detection by ChatGPT on the "DEGRADED" title word)
    _trust_score = canonical.get("trust_score")
    _brief_confidence = (
        "high" if _trust_score and _trust_score >= 80 else
        "medium" if _trust_score and _trust_score >= 50 else
        "low"
    )

    resolved_execution_report = execution_report or _latest_execution_report(d)
    # RB 9.69: the intelligence health dashboard and execution report are
    # only included in Part 1 — they describe overall pipeline freshness,
    # which belongs with the Proof Line. Omitting them from Part 2 frees
    # budget for the larger technology-radar / My Priorities sections.
    health_dashboard = None
    if part == 1:
        health_dashboard = (
            (resolved_execution_report or {}).get("intelligence_health_dashboard")
            or build_intelligence_health_dashboard(
                SYSTEM_DIR,
                as_of=d,
                execution_report=resolved_execution_report,
            )
        )
    if isinstance(resolved_execution_report, dict):
        # The full dashboard is returned as a top-level field. Do not duplicate
        # it inside execution_report.
        resolved_execution_report = {
            key: value
            for key, value in resolved_execution_report.items()
            if key != "intelligence_health_dashboard"
        }

    payload = {
        # Explicit success indicator at the TOP LEVEL.
        "status": "ok",
        # Brief confidence at top level — GPT bootstrap line reads these directly
        # without needing to find brief_status_header in sections.
        "brief_confidence": _brief_confidence,
        "trust_score": _trust_score,
        "today": d.isoformat(),
        "generated_at": generated_at,
        "session_bootstrap_status": bootstrap_status,
        "proof_dashboard": proof_dashboard,
        "canonical_brief": compact_canonical,
        # RB 9.69: tells the GPT which half of the brief this is and what to
        # do next. Part 1 points to getDailyBriefPart2; Part 2 has none.
        "continuation": _CONTINUATION_PART1 if part == 1 else _CONTINUATION_PART2,
        # RB 9.41 bootstrap contract — operational data only.
        # The three behavioral specs (knowledge_retrieval_hierarchy,
        # intelligence_pipeline_spec, session_bootstrap_spec) were removed from
        # this top-level position (RB 9.42 / DEFECT-026). They added ~18KB
        # duplicated from inside canonical_brief, pushing the total payload to
        # 83KB and exhausting the GPT's context budget before follow-on action
        # calls (e.g. getMicroGraphSummary) could fire. The GPT already has
        # these specs in its system instructions.
        "active_knowledge_assets": active_assets if part == 1 else [],
        "counts": counts or {},
        "source": source,
        "full_artifact_endpoint": "/brief/latest-json",
        "retrieval_receipt": {
            "verified": True,
            "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "brief_date": d.isoformat(),
            "brief_generated_at": generated_at,
            "source": source,
        },
        "execution_report": resolved_execution_report if part == 1 else None,
        "intelligence_health_dashboard": health_dashboard,
    }
    if source_path:
        payload["source_path"] = source_path
    return _enforce_action_payload_budget(payload)


def _load_pre_rendered_brief(d: date, kind: str) -> str | None:
    """Load the pre-rendered brief markdown for a given date and kind ('intelligence'|'daily')."""
    briefs_dir = SYSTEM_DIR / "briefs"
    path = briefs_dir / f"{d.isoformat()}-{kind}-brief.md"
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            pass
    return None


def _attach_pre_rendered_brief(payload: dict, d: date, *, part: int) -> dict:
    """Attach the RB 10.7 fetch-only artifact on every daily-brief path."""
    kind = "intelligence" if part == 1 else "daily"
    pre_rendered = _load_pre_rendered_brief(d, kind)
    if pre_rendered:
        payload["pre_rendered_brief"] = {
            "available": True,
            "kind": kind,
            "date": d.isoformat(),
            "markdown": pre_rendered,
            "display_instruction": (
                "Display the markdown field verbatim. Do not reword, summarize, or add to it."
            ),
        }
    else:
        payload["pre_rendered_brief"] = {
            "available": False,
            "kind": kind,
            "date": d.isoformat(),
        }
    # The markdown is added after the compact action payload is built. Re-run
    # the final budget guard so an unusually dense brief cannot exceed the
    # Custom GPT Action response ceiling.
    return _enforce_action_payload_budget(payload)


def _canonical_daily_brief_payload(report: dict, d: date, *, source: str, part: int = 1) -> dict:
    """Trim a raw build_report dict to the Custom-GPT-facing contract."""
    payload = _daily_brief_action_payload(
        report.get("canonical_brief") or {},
        d,
        source=source,
        counts={
            "active_threads": len(report.get("active_threads") or []),
            "open_loops": sum(
                len((report.get("loops") or {}).get(k) or [])
                for k in ("overdue", "due_today", "this_week", "future")
            ),
        },
        execution_report=_latest_execution_report(d),
        part=part,
    )
    # RB-10.7: inject pre-rendered markdown so the GPT can display verbatim
    # rather than synthesizing content from the canonical_brief payload.
    return _attach_pre_rendered_brief(payload, d, part=part)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["meta"])
def health(user_agent: Optional[str] = Header(None, alias="user-agent")):
    """Liveness probe. No auth required. Returns user-agent for diagnostics."""
    return {"ok": True, "user_agent": user_agent}


@app.get("/openapi-gpt.yaml", tags=["meta"], include_in_schema=False)
def get_custom_gpt_openapi():
    """Public, curated Action schema for reproducible GPT Builder imports."""
    from fastapi.responses import PlainTextResponse

    path = SYSTEM_DIR / "api" / "openapi_gpt.yaml"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Custom GPT schema not generated")
    return PlainTextResponse(
        path.read_text(encoding="utf-8"),
        media_type="application/yaml",
    )


@app.head("/daily_brief", tags=["compute"], include_in_schema=False)
def head_daily_brief(x_api_key: Optional[str] = Header(None)):
    """HEAD handler for /daily_brief — ChatGPT sends HEAD to verify endpoint before GET."""
    _auth(x_api_key)
    from fastapi.responses import Response
    return Response(status_code=200)


@app.get(
    "/daily_brief",
    tags=["compute"],
    operation_id="getDailyBrief",
    responses={401: {"description": "Missing or invalid x-api-key"}},
)
def get_daily_brief(
    date_str: Optional[str] = Query(None, alias="date",
                                    description="ISO date 'YYYY-MM-DD' for the brief; defaults to today."),
    use_cache: bool = Query(True, description="Use the cached report if fresh."),
    x_api_key: Optional[str] = Header(None),
):
    """Mandatory source-of-truth retrieval for every Custom GPT Daily Brief.

    Prefer the published canonical artifact on the cached path. This keeps the
    action response small and stable enough for ChatGPT while preserving
    `use_cache=false` as the explicit rebuild path. Callers must require
    `status=ok` and `retrieval_receipt.verified=true`; they must never replace
    a failed or skipped retrieval with a brief synthesized from memory.
    `execution_report` is the authoritative same-day refresh receipt.
    """
    _auth(x_api_key)
    d = date.fromisoformat(date_str) if date_str else date.today()
    if use_cache:
        published = _published_daily_brief_payload(d)
        if published:
            return published
        cached = core.read_cache("daily_brief")
        if cached and cached.get("today") == d.isoformat():
            return _canonical_daily_brief_payload(cached, d, source="daily_brief_cache")
    report = daily_brief.build_report(d)
    return _canonical_daily_brief_payload(report, d, source="daily_brief_build")


@app.head("/daily_brief/part2", tags=["compute"], include_in_schema=False)
def head_daily_brief_part2(x_api_key: Optional[str] = Header(None)):
    """HEAD handler for /daily_brief/part2 — ChatGPT sends HEAD to verify endpoint before GET."""
    _auth(x_api_key)
    from fastapi.responses import Response
    return Response(status_code=200)


@app.get(
    "/daily_brief/part2",
    tags=["compute"],
    operation_id="getDailyBriefPart2",
    responses={401: {"description": "Missing or invalid x-api-key"}},
)
def get_daily_brief_part2(
    date_str: Optional[str] = Query(None, alias="date",
                                    description="ISO date 'YYYY-MM-DD' for the brief; defaults to today. Use the SAME date as the matching getDailyBrief (Part 1) call."),
    use_cache: bool = Query(True, description="Use the cached report if fresh."),
    x_api_key: Optional[str] = Header(None),
):
    """Part 2 of 2 of the Daily Brief — call AFTER getDailyBrief (Part 1).

    RB 9.69: a single getDailyBrief response was approaching the ~70KB Action
    payload ceiling, which forced silent, size-based trimming of whichever
    section happened to be largest. The brief is now split across two calls
    so each half has room for the canonical template's full content —
    especially Section 3's mandatory per-entity watchlist coverage and
    Section 4's My Priorities (calendar, opportunities, loops, weekly goals,
    CoS recommendations).

    Always call getDailyBrief first; its `continuation` field tells the GPT
    when and how to offer this call to the user. Use the same `date` as the
    Part 1 call so both halves describe the same brief.
    """
    _auth(x_api_key)
    d = date.fromisoformat(date_str) if date_str else date.today()
    if use_cache:
        published = _published_daily_brief_payload(d, part=2)
        if published:
            return published
        cached = core.read_cache("daily_brief")
        if cached and cached.get("today") == d.isoformat():
            return _canonical_daily_brief_payload(cached, d, source="daily_brief_cache", part=2)
    report = daily_brief.build_report(d)
    return _canonical_daily_brief_payload(report, d, source="daily_brief_build", part=2)


# ---------------------------------------------------------------------------
# External Brief — Layer 1
# ---------------------------------------------------------------------------

_EXTERNAL_BRIEF_EARNINGS_BADGES = frozenset({"EARNINGS", "EXEC MOVE", "FUNDING", "M&A"})


def _build_external_brief_payload(
    report: dict, d: "date", window_start: Optional["date"] = None
) -> dict:
    """Extract and sanitise the externally shareable subset of a build_report dict.

    This is the Restaurant Industry & Technology Intelligence report — a
    teammate-shareable digest scoped to the restaurant industry. World/national
    news is intentionally excluded (out of scope for this report).

    `window_start`, when given, accumulates every fresh headline published on or
    after that date (i.e. everything since the last published edition) instead
    of a fixed trailing window. Pass None to fall back to a 7-day bootstrap
    window (first-ever edition, no prior published date to accumulate from).
    """
    import json as _json
    from datetime import datetime as _dt

    canonical = report.get("canonical_brief") or {}
    sections = canonical.get("sections") or {}

    # ── headline helpers ──────────────────────────────────────────────────────
    _FRESHNESS_DAYS = 7

    def _is_fresh(item: dict) -> bool:
        extras = item.get("extras") or {}
        pub = extras.get("pub_date") or extras.get("published_date") or ""
        if not pub:
            return True
        try:
            pub_date = date.fromisoformat(str(pub)[:10])
        except (ValueError, TypeError):
            return True
        if window_start is not None:
            return pub_date >= window_start
        return (d - pub_date).days <= _FRESHNESS_DAYS

    def _fresh(items: list) -> list:
        return [i for i in (items or []) if _is_fresh(i)]

    industry_headlines = _fresh(sections.get("restaurant_industry_headlines") or [])
    tech_headlines = _fresh(sections.get("restaurant_technology_headlines") or [])

    # ── watchlist: earnings/corporate filter ──────────────────────────────────
    watchlist_raw = sections.get("watchlist_intelligence") or []
    earnings_corporate: list[dict] = []
    for item in watchlist_raw:
        extras = item.get("extras") or {}
        badge = str(extras.get("signal_badge") or "").upper()
        has_badge = any(b in badge for b in _EXTERNAL_BRIEF_EARNINGS_BADGES)
        has_earnings_date = bool(extras.get("earnings_date"))
        if has_badge or has_earnings_date:
            earnings_corporate.append(item)

    strategic_signals = sections.get("strategic_industry_signals") or []

    # ── proof counts ─────────────────────────────────────────────────────────
    _WS_CACHE = SYSTEM_DIR / ".cache" / "web_scanner_cache.json"
    industry_count = tech_count = 0
    if _WS_CACHE.exists():
        try:
            ws = _json.loads(_WS_CACHE.read_text())
            for feed_name, feed_data in ws.items():
                if not isinstance(feed_data, dict):
                    continue
                items_list = feed_data.get("items") or []
                if not items_list:
                    continue
                domain = None
                for it in items_list[:3]:
                    if isinstance(it, dict) and it.get("domain"):
                        domain = it["domain"]
                        break
                if domain is None:
                    ln = feed_name.lower()
                    if any(w in ln for w in ("restaurant", "qsr", "foodservice", "nrn", "nation")):
                        domain = "restaurant_industry"
                    elif any(w in ln for w in ("tech", "toast", "pos", "payment")):
                        domain = "restaurant_technology"
                    else:
                        domain = None
                n = len(items_list)
                if domain == "restaurant_industry":
                    industry_count += n
                elif domain == "restaurant_technology":
                    tech_count += n
        except Exception:
            pass

    watchlist_entity_count = len(report.get("watchlist_intelligence") or [])
    generated_at = _dt.utcnow().isoformat() + "Z"

    return {
        "date": d.isoformat(),
        "generated_at": generated_at,
        "window_start": window_start.isoformat() if window_start else None,
        "restaurant_industry_headlines": industry_headlines,
        "restaurant_technology_headlines": tech_headlines,
        "earnings_corporate": earnings_corporate,
        "strategic_signals": strategic_signals,
        "proof": {
            "restaurant_articles_scanned": industry_count,
            "tech_articles_scanned": tech_count,
            "watchlist_entities_scanned": watchlist_entity_count,
            "generated_at": generated_at,
        },
    }


_EXTERNAL_BRIEF_PUBLISH_WEEKDAYS = frozenset({1, 4})  # Tuesday, Friday (Mon=0)
_EXTERNAL_BRIEF_PUBLISHED_DIR = Path(
    os.environ.get("RB_EXTERNAL_BRIEF_DIR", str(SYSTEM_DIR / "published" / "external_brief"))
)
_EXTERNAL_BRIEF_LATEST_PATH = _EXTERNAL_BRIEF_PUBLISHED_DIR / "latest.json"


def _load_latest_external_brief() -> Optional[dict]:
    if not _EXTERNAL_BRIEF_LATEST_PATH.exists():
        return None
    try:
        return json.loads(_EXTERNAL_BRIEF_LATEST_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _save_latest_external_brief(payload: dict) -> None:
    _EXTERNAL_BRIEF_PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2)
    _EXTERNAL_BRIEF_LATEST_PATH.write_text(text, encoding="utf-8")
    (_EXTERNAL_BRIEF_PUBLISHED_DIR / f"{payload['date']}.json").write_text(text, encoding="utf-8")


def _next_external_publish_date(d: date) -> date:
    for offset in range(1, 8):
        candidate = d + timedelta(days=offset)
        if candidate.weekday() in _EXTERNAL_BRIEF_PUBLISH_WEEKDAYS:
            return candidate
    return d  # unreachable — Tue/Fri occur within any 7-day span


@app.get("/daily_brief/external", tags=["compute"], operation_id="getExternalBrief")
def get_external_brief(
    date_str: Optional[str] = Query(None, alias="date"),
    format: str = Query("json", description="'json' or 'html'"),
    x_api_key: Optional[str] = Header(None),
):
    """Restaurant Industry & Technology Intelligence report — teammate-shareable.

    Publishes Tuesdays and Fridays only, accumulating every fresh restaurant
    industry/technology headline since the last published edition. On any
    other day this returns the most recently published edition unchanged
    (no fresh build). World/national news is out of scope for this report.

    Use format=html to get a styled HTML email ready for distribution.
    """
    _auth(x_api_key)
    d = date.fromisoformat(date_str) if date_str else date.today()

    is_publish_day = d.weekday() in _EXTERNAL_BRIEF_PUBLISH_WEEKDAYS
    latest = _load_latest_external_brief()

    if is_publish_day and (latest is None or latest.get("date") != d.isoformat()):
        window_start: Optional[date] = None
        if latest and latest.get("date"):
            try:
                window_start = date.fromisoformat(latest["date"]) + timedelta(days=1)
            except ValueError:
                window_start = None

        # Reuse cached report when available (same cache logic as get_daily_brief)
        cached = core.read_cache("daily_brief")
        if cached and cached.get("today") == d.isoformat():
            report = cached
        else:
            report = daily_brief.build_report(d)

        payload = _build_external_brief_payload(report, d, window_start=window_start)
        payload["status"] = "published"
        _save_latest_external_brief(payload)
    elif latest is not None:
        payload = dict(latest)
        payload["status"] = "published" if is_publish_day else "cached"
        payload["requested_date"] = d.isoformat()
    else:
        payload = {
            "date": d.isoformat(),
            "status": "not_yet_published",
            "message": "This report publishes Tuesdays and Fridays. No edition has been published yet.",
            "restaurant_industry_headlines": [],
            "restaurant_technology_headlines": [],
            "earnings_corporate": [],
            "strategic_signals": [],
            "proof": {},
        }

    payload["next_publish_date"] = _next_external_publish_date(d).isoformat()

    if format == "html":
        from fastapi.responses import HTMLResponse
        sys.path.insert(0, str(SYSTEM_DIR / "scripts"))
        import external_brief_renderer as _ebr
        return HTMLResponse(content=_ebr.render_html_email(payload))

    return payload


@app.head("/public/bootstrap_status", tags=["diagnostic"], include_in_schema=False)
def head_public_bootstrap_status():
    """HEAD handler — ChatGPT pre-flight check."""
    from fastapi.responses import Response
    return Response(status_code=200)


@app.get("/public/bootstrap_status", tags=["diagnostic"], operation_id="getPublicBootstrapStatus")
def get_public_bootstrap_status():
    """No-auth diagnostic endpoint. Returns API liveness and graph mount count only.

    Use this to isolate auth failures from tunnel/API failures:
    - If this responds: the API and tunnel are alive.
    - If /daily_brief returns 'invalid or missing x-api-key' but this works:
      the Custom GPT Action auth is misconfigured. Fix: GPT Builder → Actions →
      Authentication → API Key → Auth Type: Custom → Custom Header Name: x-api-key
      → paste your RB_API_KEY value → Save.

    No private data is returned. Entity names and brief content are not exposed.
    """
    try:
        registry_path = SYSTEM_DIR / "graphs" / "index.json"
        mounted_count = 0
        if registry_path.exists():
            try:
                registry = json.loads(registry_path.read_text(encoding="utf-8"))
                mounted_count = sum(
                    1 for g in (registry.get("graphs") or [])
                    if (g.get("status") or "").lower() in ("active", "partial")
                )
            except Exception:  # noqa: BLE001
                pass
        return {
            "api_alive": True,
            "date": date.today().isoformat(),
            "micro_graphs_mounted_count": mounted_count,
            "degraded": False,
            "status": "ready",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "api_alive": False,
            "date": date.today().isoformat(),
            "error": str(exc),
        }


@app.get("/brief/health", tags=["compute"], operation_id="getBriefHealth")
def get_brief_health(x_api_key: Optional[str] = Header(None)):
    """Return source_health.json from cache. 404 if not yet generated."""
    _auth(x_api_key)
    path = SYSTEM_DIR / ".cache" / "source_health.json"
    if not path.exists():
        raise HTTPException(404, detail="source_health.json not found; run refresh_sources.py --save-health")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"failed to parse source_health.json: {exc}") from exc


@app.get("/brief/latest-json", tags=["compute"], operation_id="getBriefLatestJson")
def get_brief_latest_json(x_api_key: Optional[str] = Header(None)):
    """Return the most recently published brief.json artifact."""
    _auth(x_api_key)
    latest = SYSTEM_DIR / "published" / "daily" / "latest_brief.json"
    if not latest.exists():
        raise HTTPException(404, detail="latest_brief.json not found; run publish.py")
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"failed to parse latest_brief.json: {exc}") from exc


@app.get("/intelligence/collection", tags=["compute"], operation_id="getIntelligenceCollection")
def get_intelligence_collection(
    date_str: Optional[str] = Query(None, alias="date", description="ISO date (YYYY-MM-DD). Defaults to today."),
    x_api_key: Optional[str] = Header(None),
):
    """Return the intelligence collection state for a given date.

    RB-INTEL-021 — Answers: Did the pre-brief scan run? When? Which sources
    were scanned? How many signals were detected? What is collection health?

    Use this endpoint to answer any user question about whether intelligence
    collection executed, what was scanned, and what was found.

    Returns:
    - collection_window: when collection ran
    - refresh_status: Success | Partial | Failed
    - collection_health: Healthy | Degraded | Failed
    - sources_healthy / sources_failed / sources_unavailable counts
    - sources_scanned: list of source names that processed successfully
    - records_processed, records_added, mutations_generated
    - trust_score, trust_level
    - stale_sources: sources that did not refresh
    - source_detail: per-source health with records, trust, failure_reason
    - processing_errors: step-level errors from pipeline execution
    """
    _auth(x_api_key)
    from intelligence_observability import build_intelligence_health_dashboard, read_collection_scan_log

    target_date = date.fromisoformat(date_str) if date_str else date.today()

    # Primary: check audit log for a scan record on this date
    try:
        scan_records = read_collection_scan_log(SYSTEM_DIR, days=7)
        todays_scan = next(
            (r for r in scan_records if r.get("date") == target_date.isoformat()),
            None,
        )
    except Exception:  # noqa: BLE001
        todays_scan = None

    # Secondary: check morning_pipeline cache
    cache_path = SYSTEM_DIR / ".cache" / "morning_pipeline.json"
    pipeline_cache: dict = {}
    if cache_path.exists():
        try:
            candidate = json.loads(cache_path.read_text(encoding="utf-8"))
            if candidate.get("date") == target_date.isoformat():
                pipeline_cache = candidate
        except Exception:  # noqa: BLE001
            pass

    er: dict = pipeline_cache.get("execution_report") or {}
    dashboard: dict = er.get("intelligence_health_dashboard") or build_intelligence_health_dashboard(
        SYSTEM_DIR, as_of=target_date
    )

    # Merge scan record + live dashboard
    sources_summary = dashboard.get("sources") or []
    summary_counts = dashboard.get("summary") or {}

    if todays_scan:
        collection_window_start = todays_scan.get("collection_window_start") or todays_scan.get("timestamp")
        collection_window_end = todays_scan.get("collection_window_end") or todays_scan.get("timestamp")
        refresh_status = todays_scan.get("refresh_status") or er.get("refresh_status") or "Unknown"
        collection_health = todays_scan.get("collection_health") or "unknown"
        scan_confirmed = True
        scan_record_id = todays_scan.get("event_id")
    elif er:
        collection_window_start = er.get("generated_at") or ""
        collection_window_end = er.get("generated_at") or ""
        refresh_status = er.get("refresh_status") or "Unknown"
        collection_health = (
            "healthy" if refresh_status == "Success"
            else ("degraded" if refresh_status == "Partial" else "failed")
        )
        scan_confirmed = bool(er)
        scan_record_id = None
    else:
        return {
            "date": target_date.isoformat(),
            "scan_confirmed": False,
            "collection_health": "unknown",
            "refresh_status": "Unknown",
            "message": (
                f"No intelligence collection record found for {target_date.isoformat()}. "
                "Run morning_pipeline.py to generate a collection receipt."
            ),
            "dashboard": dashboard,
        }

    return {
        "date": target_date.isoformat(),
        "scan_confirmed": scan_confirmed,
        "scan_record_id": scan_record_id,
        "collection_window_start": collection_window_start,
        "collection_window_end": collection_window_end,
        "refresh_status": refresh_status,
        "collection_health": collection_health,
        "sources_attempted": int(summary_counts.get("total_sources") or len(sources_summary)),
        "sources_healthy": int(summary_counts.get("healthy") or 0),
        "sources_stale": int(summary_counts.get("stale") or 0),
        "sources_failed": int(summary_counts.get("failed_or_partial") or 0),
        "sources_unavailable": int(summary_counts.get("unavailable") or 0),
        "sources_scanned": er.get("sources_processed") or [],
        "records_processed": er.get("records_processed") or 0,
        "records_added": er.get("records_added") or 0,
        "records_changed": er.get("records_changed") or 0,
        "mutations_generated": er.get("mutations_generated") or 0,
        "opportunities_generated": er.get("opportunities_generated") or 0,
        "relationship_changes": er.get("relationship_changes") or 0,
        "trust_score": er.get("trust_score"),
        "trust_level": er.get("trust_level"),
        "stale_sources": er.get("stale_sources") or [],
        "processing_errors": er.get("processing_errors") or [],
        "brief_rebuilt": er.get("brief_rebuilt", False),
        "source_detail": [
            {
                "source": s.get("source"),
                "source_key": s.get("source_key"),
                "status": s.get("status"),
                "records_processed": s.get("records_processed"),
                "new_signals": s.get("new_signals"),
                "mutations_detected": s.get("mutations_detected"),
                "trust_score": s.get("trust_score"),
                "trust_basis": s.get("trust_basis"),
                "last_refresh": s.get("last_refresh"),
                "failure_reason": s.get("failure_reason"),
            }
            for s in sources_summary
        ],
        "proof_rule": (
            "This endpoint returns recorded system activity. "
            "All fields are derived from actual pipeline execution receipts, "
            "not inference. scan_confirmed=True means a pipeline run was recorded."
        ),
    }


@app.get("/intelligence/collection/audit", tags=["compute"], operation_id="getIntelligenceCollectionAudit")
def get_intelligence_collection_audit(
    days: int = Query(7, ge=1, le=90, description="Number of days to look back (1-90)."),
    x_api_key: Optional[str] = Header(None),
):
    """Return the intelligence collection scan audit log.

    RB-INTEL-021 — Returns all collection scan records from the last N days,
    newest-first.  Each record is a durable receipt written by morning_pipeline
    after each run — not synthesized, not inferred.

    Use this endpoint to answer:
    - 'Show me all intelligence scans from the last 7 days'
    - 'Has the scan run every day this week?'
    - 'Were there any collection failures recently?'
    - 'What sources were scanned on Tuesday?'
    """
    _auth(x_api_key)
    from intelligence_observability import read_collection_scan_log

    try:
        records = read_collection_scan_log(SYSTEM_DIR, days=days)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"Failed to read collection audit log: {exc}") from exc

    return {
        "days_requested": days,
        "record_count": len(records),
        "records": records,
        "proof_rule": (
            "Each record is a durable receipt written by morning_pipeline.py after execution. "
            "Records are appended to system/audit/YYYY-MM.jsonl and never modified."
        ),
    }


@app.get("/intelligence/signals", tags=["compute"], operation_id="getIntelligenceSignals")
def get_intelligence_signals(
    days: int = Query(48, ge=1, le=720, description="Lookback window in hours (1–720)."),
    status: Optional[str] = Query(None, description="Filter by status: new | mutated | all. Default: all."),
    signal_type: Optional[str] = Query(None, description="Filter by signal type (e.g. 'relationship_signal', 'market_signal')."),
    limit: int = Query(50, ge=1, le=200),
    x_api_key: Optional[str] = Header(None),
):
    """Query the signal discovery ledger — knowledge mutations tracked by the IME.

    RB-INTEL-021 — Returns signals discovered and mutated by the intelligence
    mutation engine.  Enables queries like:
    - 'Show me all new signals discovered in the last 48 hours'
    - 'What signals were detected today?'
    - 'What intelligence changed since yesterday?'

    Each signal entry includes:
    - signal_id, discovery timestamp, source, confidence, importance
    - mutation history (what changed)
    - current status
    """
    _auth(x_api_key)
    from datetime import timedelta

    mutations_path = SYSTEM_DIR / "knowledge_mutations.json"
    if not mutations_path.exists():
        raise HTTPException(404, detail="knowledge_mutations.json not found.")

    try:
        raw = json.loads(mutations_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"Failed to read knowledge_mutations.json: {exc}") from exc

    mutations_list = raw if isinstance(raw, list) else (raw.get("mutations") or raw.get("items") or [])

    cutoff = datetime.now(timezone.utc) - timedelta(hours=days)

    results: list[dict] = []
    for mut in mutations_list:
        if not isinstance(mut, dict):
            continue

        # Date filter
        ts_str = (
            mut.get("detected_at")
            or mut.get("last_seen_at")
            or mut.get("created_at")
            or mut.get("date")
            or ""
        )
        if ts_str:
            try:
                ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
            except (ValueError, TypeError):
                pass

        # Signal type filter
        if signal_type:
            mut_type = mut.get("signal_type") or mut.get("mutation_type") or mut.get("type") or ""
            if signal_type.lower() not in mut_type.lower():
                continue

        # Status filter
        if status and status != "all":
            mut_status = mut.get("status") or ""
            if status == "new" and mut_status not in ("new", "discovered", ""):
                if not (mut.get("is_new") or mut.get("first_seen_at") == mut.get("detected_at")):
                    continue
            elif status == "mutated" and mut_status not in ("mutated", "changed", "updated"):
                continue

        results.append({
            "signal_id": mut.get("signal_id") or mut.get("id") or mut.get("mutation_id"),
            "discovery_date": mut.get("detected_at") or mut.get("created_at") or mut.get("date"),
            "last_seen": mut.get("last_seen_at") or mut.get("updated_at"),
            "source": mut.get("source") or mut.get("source_key"),
            "signal_type": mut.get("signal_type") or mut.get("mutation_type") or mut.get("type"),
            "title": mut.get("title") or mut.get("summary") or mut.get("description"),
            "confidence": mut.get("confidence") or mut.get("confidence_score"),
            "importance": mut.get("importance") or mut.get("priority"),
            "status": mut.get("status") or "active",
            "entity": mut.get("entity") or mut.get("entity_name") or mut.get("contact_name"),
            "mutation_count": mut.get("mutation_count") or len(mut.get("mutations") or []),
            "inclusion_reason": mut.get("inclusion_reason"),
        })

    results = results[:limit]

    return {
        "lookback_hours": days,
        "status_filter": status or "all",
        "signal_type_filter": signal_type,
        "signal_count": len(results),
        "signals": results,
    }


@app.get("/intelligence/jobs", tags=["compute"], operation_id="getJobIntelligence")
def get_job_intelligence(
    refresh: bool = Query(False, description="Re-run the scanner instead of using cache."),
    x_api_key: Optional[str] = Header(None),
):
    """Return job opportunity signals detected from email and LinkedIn messages.

    Gated by opportunity_context.yaml → job_search_active.
    Returns gate_status='inactive' when job_search_active is false — no signals
    are ever returned when the user is not searching.

    When active, returns roles detected in the last scan_days window, ordered
    by fit_score descending.  Each signal includes:
    - extracted_titles: detected role titles
    - extracted_companies: detected company names
    - posting_urls: direct links to job postings when available
    - fit_score: 0–100 match against user profile
    - fit_reasons: why the score was assigned
    - is_speculative: true when no posting URL found
    - source: email | linkedin
    - recommended_action: what to do next

    Use when the user asks: 'Any job leads today?', 'Did any roles come in?',
    'What opportunities are in my inbox?'
    """
    _auth(x_api_key)
    if not _HAS_JOB_INTEL:
        raise HTTPException(503, detail="job_intelligence module not available.")

    cache_path = SYSTEM_DIR / ".cache" / "job_intelligence.json"
    if not refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            gen_at = str(cached.get("generated_at") or "")
            if gen_at[:10] == date.today().isoformat():
                return cached
        except Exception:  # noqa: BLE001
            pass

    try:
        report = _job_intel.build_report()
        _job_intel.write_cache(report)
        return report
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"Job intelligence scan failed: {exc}") from exc


@app.post("/intelligence/jobs/toggle", tags=["write"], operation_id="toggleJobSearch")
def toggle_job_search(
    active: bool = Query(..., description="true to enable job search scanning, false to disable."),
    x_api_key: Optional[str] = Header(None),
):
    """Enable or disable job search intelligence scanning.

    Writes job_search_active to opportunity_context.yaml.  When set to false,
    job_intelligence section is completely suppressed from the daily brief.

    Use when the user says: 'I'm open to new opportunities', 'Start scanning
    for jobs', 'I'm not looking anymore', 'Turn off job search'.
    """
    _auth(x_api_key)
    ctx_path = SYSTEM_DIR / "profiles" / "todd_vahlsing" / "opportunity_context.yaml"
    if not ctx_path.exists():
        raise HTTPException(404, detail="opportunity_context.yaml not found.")
    try:
        content = ctx_path.read_text(encoding="utf-8")
        import re as _re
        # Replace job_search_active value
        new_content = _re.sub(
            r"^(job_search_active:\s*).*$",
            f"\\g<1>{str(active).lower()}",
            content,
            flags=_re.MULTILINE,
        )
        ctx_path.write_text(new_content, encoding="utf-8")
        # Invalidate cache
        cache_path = SYSTEM_DIR / ".cache" / "job_intelligence.json"
        if cache_path.exists():
            cache_path.unlink()
        return {
            "status": "updated",
            "job_search_active": active,
            "message": (
                "Job intelligence scanning enabled. Email and LinkedIn will be scanned for matching roles."
                if active else
                "Job intelligence scanning disabled. Job section suppressed from daily brief."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"Failed to update opportunity_context.yaml: {exc}") from exc


@app.get("/draft-actions", tags=["compute"], operation_id="getDraftActions")
def get_draft_actions(
    contact_id: Optional[str] = Query(None, description="Filter DraftSpec rows by baseline contact id."),
    action_type: Optional[str] = Query(None, description="Filter DraftSpec rows by action type."),
    x_api_key: Optional[str] = Header(None),
):
    """Return DraftSpec objects from the action_drafts cache."""
    _auth(x_api_key)
    cache = SYSTEM_DIR / ".cache" / "action_drafts.json"
    if not cache.exists():
        report = action_drafts.build_report()
        action_drafts.write_action_drafts_cache(report)
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"failed to parse action_drafts.json: {exc}") from exc
    specs = data.get("specs") or []
    if contact_id:
        specs = [s for s in specs if s.get("contact_id") == contact_id]
    if action_type:
        specs = [s for s in specs if s.get("action_type") == action_type]
    return {
        "specs": specs,
        "generated_at": data.get("generated_at"),
        "source_brief_date": data.get("source_brief_date"),
        "spec_count": len(specs),
    }


@app.get("/validate_baseline", tags=["compute"])
def get_validate_baseline(x_api_key: Optional[str] = Header(None)):
    """Schema + integrity check on baseline_index.json."""
    _auth(x_api_key)
    return {"integrity": vb.integrity_checks(core.load_baseline(), date.today())}


@app.get("/gap_detection", tags=["compute"])
def get_gap_detection(x_api_key: Optional[str] = Header(None)):
    """RCs without cards, orphan cards, RCs without last_touch, contact-field gaps."""
    _auth(x_api_key)
    return gap_detection.build_report()


@app.get("/loops", tags=["compute"], operation_id="getLoops")
def get_loops(
    date_str: Optional[str] = Query(None, alias="date"),
    x_api_key: Optional[str] = Header(None),
):
    """Loop ledger bucketed by status relative to a target date."""
    _auth(x_api_key)
    d = date.fromisoformat(date_str) if date_str else date.today()
    loops = core.parse_loop_ledger()
    buckets = core.loops_by_status(loops, d)
    from dataclasses import asdict
    return {
        "today": d.isoformat(),
        "buckets": {
            k: [{**asdict(L), "opened": L.opened.isoformat(), "target": L.target.isoformat()} for L in v]
            for k, v in buckets.items()
        },
        "totals": {k: len(v) for k, v in buckets.items()},
    }


@app.get("/meeting_prep", tags=["compute"], operation_id="getMeetingPrepCandidates")
def get_meeting_prep_candidates(
    date_str: Optional[str] = Query(None, alias="date",
                                    description="ISO date for the brief context (default: today)."),
    x_api_key: Optional[str] = Header(None),
):
    """Calendar events from today+tomorrow that qualify for a pre-conversation brief.

    Read-only: returns the structured prep payload per qualifying event so a
    Custom GPT can preview the brief before committing the artifact to disk.
    Per ARCHITECTURE.md "Pre-conversation briefing", the artifact follows the
    11-section structure; empty subsections are omitted, not stubbed.
    """
    _auth(x_api_key)
    d = date.fromisoformat(date_str) if date_str else date.today()
    report = daily_brief.build_report(d)
    baseline = core.load_baseline()
    threads = core.load_active_threads()
    events = meeting_prep.collect_candidate_events(report)
    payloads = [
        meeting_prep.build_prep_payload(ev, baseline=baseline, threads=threads, today=d)
        for ev in events
    ]
    return {
        "today": d.isoformat(),
        "count": len(payloads),
        "candidates": payloads,
    }


@app.get("/meeting_prep/{event_id}", tags=["compute"], operation_id="getMeetingPrepForEvent")
def get_meeting_prep_for_event(
    event_id: str,
    date_str: Optional[str] = Query(None, alias="date"),
    x_api_key: Optional[str] = Header(None),
):
    """Generate a single event's prep payload + rendered markdown (read-only).

    The markdown returned here matches what `POST /meeting_prep/write`
    materializes when `confirm=true`. No file is written.
    """
    _auth(x_api_key)
    d = date.fromisoformat(date_str) if date_str else date.today()
    report = daily_brief.build_report(d)
    cal = report.get("calendar") or {}
    match = next(
        (e for e in (cal.get("today") or []) + (cal.get("tomorrow") or []) + (cal.get("this_week") or [])
         if e.get("id") == event_id),
        None,
    )
    if not match:
        raise HTTPException(404, f"no calendar event with id={event_id!r}")
    baseline = core.load_baseline()
    threads = core.load_active_threads()
    payload = meeting_prep.build_prep_payload(match, baseline=baseline, threads=threads, today=d)
    return {
        "payload": payload,
        "markdown": meeting_prep.render_prep_brief_md(payload),
    }


import threading as _threading  # noqa: E402

_capture_scan_state: dict = {"running": False, "started_at": None, "last_result": None, "last_finished_at": None}
_capture_scan_lock = _threading.Lock()


def _run_capture_scan_in_background(source_filter: Optional[str]) -> None:
    try:
        result = capture_ingest.sweep(source_filter=source_filter)
        with _capture_scan_lock:
            _capture_scan_state["last_result"] = result
            _capture_scan_state["last_finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    except Exception as exc:  # noqa: BLE001
        with _capture_scan_lock:
            _capture_scan_state["last_result"] = {"error": str(exc)}
            _capture_scan_state["last_finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    finally:
        with _capture_scan_lock:
            _capture_scan_state["running"] = False


def _run_capture_queue_in_background(path: Path) -> None:
    """Background counterpart to _run_capture_scan_in_background, for a
    single audio file uploaded through chat (uploadAndIngestFile) rather
    than discovered by a folder-watch sweep. Shares the same
    _capture_scan_state/_capture_scan_lock so getCaptureScanStatus already
    surfaces it -- no new chat tool needed."""
    try:
        result = capture_ingest.queue_file(path, source_id="chat_upload", transcription_mode="whisper_local")
        with _capture_scan_lock:
            _capture_scan_state["last_result"] = {"queued_file": result}
            _capture_scan_state["last_finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    except Exception as exc:  # noqa: BLE001
        with _capture_scan_lock:
            _capture_scan_state["last_result"] = {"error": str(exc)}
            _capture_scan_state["last_finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    finally:
        with _capture_scan_lock:
            _capture_scan_state["running"] = False


@app.post("/captures/scan", tags=["write"], operation_id="scanCaptureSources")
def post_scan_capture_sources(
    source: Optional[str] = Query(None, description="Limit to one source id, e.g. 'just_press_record'. Omit to scan all enabled sources."),
    x_api_key: Optional[str] = Header(None),
):
    """Kick off a scan of enabled capture sources (Just Press Record, RB
    Captures Drop Folder, etc.) for files newer than the last scan --
    getCapturesPending has NO path to trigger this itself.

    Real gap found 2026-08-27: getCapturesPending only reads what a PRIOR
    sweep already queued into system/captures/pending/; it never looks at
    the actual source folders. The only thing that called capture_ingest
    .sweep() was the scheduled morning_pipeline.py run, once a day. A real
    recording made any time after that (e.g. mid-afternoon) was genuinely
    invisible to chat -- confirmed live: getCapturesPending reported zero
    pending JPR files while three real .m4a recordings sat in the watched
    iCloud folder, because the last sweep was that morning.

    Runs in the BACKGROUND, not inline -- confirmed live the same day that
    real local Whisper transcription of just 3 short recordings took well
    over two minutes on CPU, which would blow past the chat orchestrator's
    tool-call timeout if this blocked the response. Returns immediately.
    Call getCaptureScanStatus after roughly a minute to see whether it
    finished and what it found, then call getCapturesPending to see the
    newly queued items. Only call this when the user explicitly asks to
    process something newer than what's already pending -- not on every
    routine check.
    """
    _auth(x_api_key)
    with _capture_scan_lock:
        if _capture_scan_state["running"]:
            return {
                "status": "already_running",
                "started_at": _capture_scan_state["started_at"],
                "note": "A scan is already in progress. Call getCaptureScanStatus to check on it.",
            }
        _capture_scan_state["running"] = True
        _capture_scan_state["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    thread = _threading.Thread(target=_run_capture_scan_in_background, args=(source,), daemon=True)
    thread.start()
    return {
        "status": "started",
        "started_at": _capture_scan_state["started_at"],
        "note": "Scan running in the background (real local Whisper transcription for audio sources takes real time). Call getCaptureScanStatus in about a minute, then getCapturesPending.",
    }


@app.get("/captures/scan/status", tags=["compute"], operation_id="getCaptureScanStatus")
def get_capture_scan_status(x_api_key: Optional[str] = Header(None)):
    """Check whether a background scan started by scanCaptureSources has
    finished, and see its result (files_queued, per-file status). If
    'running' is true, the scan is still going -- wait and check again
    rather than starting a second scan."""
    _auth(x_api_key)
    with _capture_scan_lock:
        return dict(_capture_scan_state)


#: RB-2026-09-19: queueCaptureText hardcoded capture_type="pasted_content"
#: unconditionally -- correct default, but the ChatGPT Intelligence Drop
#: capture source (system/inbox/chatgpt_intelligence_drop/, capture_type=
#: "deep_research") documents this endpoint as the hosted-Custom-GPT
#: equivalent for a client with no filesystem access, and a deep-research
#: evidence packet sent through it got mislabeled "pasted_content" with no
#: way to say otherwise. Only these two values are meaningful for text this
#: endpoint accepts -- never "meeting"/"voice_note"/etc., which would
#: misroute _capture_processing_context()'s transcript-shaped framing onto
#: text that was never a call.
QUEUE_CAPTURE_TEXT_TYPES = {"pasted_content", "deep_research"}


class QueueCaptureTextBody(BaseModel):
    text: str = Field(..., description="The real user-provided text to queue -- an article, a paste, an observation. Never text you generated yourself.")
    title_hint: Optional[str] = Field(None, description="A short human-readable label, e.g. 'Restaurant Business article on Del Taco'. Falls back to a generic label if omitted.")
    source_url: Optional[str] = Field(None, description="The real URL this came from, if the user gave one.")
    capture_type: Optional[str] = Field(
        None,
        description=(
            "Optional override, one of 'pasted_content' (default) or "
            "'deep_research' -- use 'deep_research' for an evidence packet "
            "from a deep-research cycle, so it reports in the Intelligence "
            "Brief's Capture Intelligence section the same way a local "
            "client's file dropped in system/inbox/chatgpt_intelligence_drop/ "
            "already does."
        ),
    )


@app.post("/captures/queue-text", tags=["write"], operation_id="queueCaptureText")
def post_queue_capture_text(
    body: QueueCaptureTextBody,
    x_api_key: Optional[str] = Header(None),
):
    """Queue user-provided text (article, paste, observation) for processing
    on tomorrow's automatic morning pass, instead of right now.

    RB-2026-09-11: for content that does NOT need to inform something being
    actively worked on in this conversation right now -- the default for
    most pasted intelligence per Todd. Uses the exact same pending -> processed
    pipeline as meeting captures (process_pending_captures.py, already run
    automatically every morning by morning_pipeline.py): the identical
    intelligence_triage classifier ingestContent uses live, non-noise
    intelligence auto-persisted to IntelligenceDB with no separate confirm
    step (safe here specifically because it's a deterministic scheduled
    script, never a live model decision -- see ingestContent's own
    auto_persist history for why that distinction matters). Reported back
    via the Daily Brief's Captures sections and the Intelligence Brief's
    Capture Intelligence section -- the same places meeting-capture
    processing already reports, not a new surface.

    For content that DOES need to inform this conversation right now, use
    ingestContent instead -- that runs the same triage immediately and
    returns the result inline.

    Local workspace clients may save .txt/.md files directly to
    system/inbox/chatgpt_intelligence_drop/, which the morning capture sweep
    watches. The hosted Custom GPT cannot write local files and must use this
    endpoint instead; it must never claim it wrote the local folder.
    """
    _auth(x_api_key)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text must not be empty.")
    capture_type = body.capture_type or "pasted_content"
    if capture_type not in QUEUE_CAPTURE_TEXT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"capture_type must be one of {sorted(QUEUE_CAPTURE_TEXT_TYPES)}, got {capture_type!r}.",
        )

    # RB-2026-09-11: external_id is queue_text()'s dedup key -- computed here
    # from the real text content, never left to the caller to construct.
    # Same reasoning capture_ingest.list_processed()'s own docstring gives
    # for resolving "today"/"yesterday" server-side rather than trusting the
    # model to build a precise value: a hash of the actual text is exact and
    # free of any risk the model gets it wrong, and it means an accidental
    # duplicate paste of the same content dedupes for free.
    external_id = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    title_hint = body.title_hint or (text[:80] + ("…" if len(text) > 80 else ""))
    source_id, source_label = (
        ("deep_research_hosted", "Deep research (hosted)")
        if capture_type == "deep_research"
        else ("pasted_content", "Pasted content")
    )

    result = capture_ingest.queue_text(
        text,
        source_id=source_id,
        source_label=source_label,
        external_id=external_id,
        title_hint=title_hint,
        capture_type=capture_type,
        extra={"source_url": body.source_url} if body.source_url else None,
    )
    al.log_mutation_executed(
        f"queueCaptureText: file_id={result.get('file_id')} status={result.get('status')}",
        source="POST /captures/queue-text",
    )
    return result


@app.get("/captures/pending", tags=["compute"], operation_id="getCapturesPending")
def get_captures_pending(
    limit: int = Query(20, description="Max captures to return."),
    include_transcript: bool = Query(False, description="Include full transcript text in response. Default false — fetch individually via /captures/{id}."),
    x_api_key: Optional[str] = Header(None),
):
    """List captures queued by the morning sweep awaiting GPT intelligence processing.

    Returns metadata for each pending capture. Set include_transcript=true to
    get the full text in one call, or fetch individually via GET /captures/{id}.

    On-demand trigger phrase: "RB, process my [person] meeting" or
    "RB, process the transcript" — GPT calls this endpoint, finds the relevant
    capture, then submits it to POST /captures/{id}/submit.
    """
    _auth(x_api_key)
    items = capture_ingest.list_pending(limit=limit)
    if include_transcript:
        full = []
        for item in items:
            data = capture_ingest.get_pending(item["file_id"])
            if data:
                full.append(data)
            else:
                full.append(item)
        items = full
    return {
        "pending_count": len(items),
        "captures": items,
    }


@app.get("/captures/processed", tags=["compute"], operation_id="getCapturesProcessed")
def get_captures_processed(
    limit: int = Query(20, description="Max captures to return."),
    since_date: Optional[str] = Query(None, description="'today', 'yesterday', or an explicit YYYY-MM-DD -- only captures recorded on or after this date. Prefer the literal word for relative dates; resolved server-side, not by the caller."),
    x_api_key: Optional[str] = Header(None),
):
    """List captures that have ALREADY been processed -- real gap found live
    2026-08-27: getCapturesPending only shows what's still awaiting
    processing; once a capture is submitted, it moves to processed/ and
    became completely unretrievable from chat. A user asking to "review
    and summarize the three JPR calls today" after they were already
    processed got "no captures pending" -- technically true, but
    misleading, since the real content still exists and is retrievable
    here. Route "review/summarize/what did we cover in [recent
    captures/calls/meetings]" here, not to getCapturesPending, when the
    user is asking to look BACK at something rather than process something
    NEW. Each item includes processing_result (the triage summary/receipt
    already recorded) -- use that plus getCaptureProcessed's full
    transcript, never re-invent a summary from memory.
    """
    _auth(x_api_key)
    items = capture_ingest.list_processed(limit=limit, since_date=since_date)
    return {"processed_count": len(items), "captures": items}


@app.get("/captures/processed/{file_id}", tags=["compute"], operation_id="getCaptureProcessed")
def get_capture_processed(
    file_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """Retrieve a single already-processed capture, including its full
    transcript and the triage/processing_result recorded when it was
    submitted. Use this to review or summarize a past capture -- never
    regenerate its content from memory."""
    _auth(x_api_key)
    data = capture_ingest.get_processed(file_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No processed capture found for file_id '{file_id}'.")
    return data


@app.get("/captures/{file_id}", tags=["compute"], operation_id="getCapture")
def get_capture(
    file_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """Retrieve a single pending capture including full transcript text.

    Use this to fetch the transcript before submitting to /captures/{file_id}/submit.
    """
    _auth(x_api_key)
    data = capture_ingest.get_pending(file_id)
    if data is None:
        from fastapi import HTTPException
        if capture_ingest.get_processed(file_id) is not None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"'{file_id}' is not pending -- it was already processed. "
                    "Call getCaptureProcessed with this same file_id instead."
                ),
            )
        raise HTTPException(status_code=404, detail=f"Capture not found: {file_id}")
    return data


# RB-2026-09-11: queueCaptureText lets pasted articles/notes ride the same
# pending -> processed pipeline as meeting recordings (capture_type
# "pasted_content"). Both processing paths below used to hardcode
# source_type="transcript" for intelligence_triage/IntelligenceDB and never
# told transcript_summarizer it might be reading an article, not a call --
# harmless metadata drift for a real meeting, wrong framing (attendees,
# action items) for an article.
_MEETING_LIKE_CAPTURE_TYPES = {"meeting", "voice_note", "walking", "conference", "phone_call"}


def _capture_processing_context(capture_type: Optional[str]) -> tuple:
    """(source_type for intelligence_triage/IntelligenceDB, content_label for
    transcript_summarizer) for a given capture_type. Missing/unrecognized
    capture_type defaults to the original meeting-shaped behavior."""
    if (capture_type or "meeting") in _MEETING_LIKE_CAPTURE_TYPES:
        return "transcript", "meeting/call transcript"
    return "paste", "pasted article, web page, or note"


@app.post("/captures/{file_id}/submit", tags=["write"], operation_id="submitCapture")
def post_capture_submit(
    file_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """Submit a pending capture through the intelligence triage pipeline.

    Routes the transcript through /intelligence/triage with source_type='transcript',
    writes RI events and mutations, and marks the capture as processed.

    Called by the GPT after retrieving the transcript from GET /captures/{file_id}.
    The GPT should present the triage result to the user and offer follow-up actions
    (draft email, open loops, update relationship card).
    """
    _auth(x_api_key)
    data = capture_ingest.get_pending(file_id)
    if data is None:
        from fastapi import HTTPException
        if capture_ingest.get_processed(file_id) is not None:
            al.log_mutation_rejected(f"submitCapture: file_id={file_id}", reason="already processed")
            raise HTTPException(
                status_code=404,
                detail=(
                    f"'{file_id}' is not pending -- it was already processed and does not need "
                    "resubmitting. Call getCaptureProcessed with this same file_id to retrieve "
                    "its real transcript and triage result."
                ),
            )
        al.log_mutation_rejected(f"submitCapture: file_id={file_id}", reason="capture not found")
        raise HTTPException(status_code=404, detail=f"Capture not found: {file_id}")

    transcript = data.get("transcript", "")
    if not transcript:
        # A transcript that's empty here will never become non-empty later —
        # whisper already ran (or wasn't configured) at ingest time and this
        # is the same static file. Without marking it processed, this capture
        # would surface as "pending" in the daily brief forever regardless of
        # how many times the GPT calls this endpoint, permanently inflating
        # the pending count with an item nobody can actually act on.
        capture_ingest.mark_processed(file_id, result={
            "skipped": True,
            "reason": "no_transcript_available",
        })
        al.log_mutation_executed(
            f"submitCapture: file_id={file_id} status=skipped reason=no_transcript_available",
            source="POST /captures/{file_id}/submit",
        )
        return {
            "status": "skipped",
            "file_id": file_id,
            "reason": "no_transcript_available",
            "note": "Enable JPR transcription or set transcription mode to whisper_local in settings.json.",
        }

    source_type, content_label = _capture_processing_context(data.get("capture_type"))
    triage_result = intelligence_triage.triage_input(
        text=transcript,
        source_type=source_type,
        captured_at=data.get("queued_at"),
    )

    # RB-DEFECT-2026-07-15: intelligence_triage's classifiers are keyword-
    # trigger detection, not comprehension -- their one attempt at "who was
    # this about" produced "Possible named subjects: Cool, Well, Yeah, Yeah,
    # Yeah, God" on a real transcript, forcing render_intelligence_brief.py
    # to strip that clause entirely and leaving only generic trigger counts
    # in the Capture Intelligence section. Best-effort: returns None (no
    # change in behavior) without OPENAI_API_KEY configured.
    llm_summary = transcript_summarizer.summarize_transcript(
        transcript, word_count=data.get("word_count"), content_label=content_label)

    # RB defect 2026-09-30 (secondary finding): a deep-research packet
    # already has a higher-quality structured sidecar ("findings" array,
    # imported separately by import_competitor_platform_research.py) --
    # running it through the generic keyword-trigger classifiers too
    # produced obvious false positives ("Named, Candidate, Material" read
    # as RI subjects from template language). When a valid structured
    # sidecar exists, suppress the generic streams entirely rather than
    # persist noise the structured importer already supersedes.
    sidecar_info = capture_ingest.deep_research_sidecar_info(data.get("source_file"))
    suppress_generic_streams = (
        data.get("capture_type") == "deep_research" and sidecar_info["has_findings"]
    )
    suppressed_stream_count = (
        len([s for s in triage_result.get("identified_types", []) if s.get("intelligence_type") != "noise"])
        if suppress_generic_streams else 0
    )

    # Auto-persist non-noise intelligence streams to IntelligenceDB
    persisted_count = 0
    persisted_items: list = []
    if _intelligence_db_module and not suppress_generic_streams:
        actionable = [s for s in triage_result.get("identified_types", [])
                      if s.get("intelligence_type") != "noise"]
        if actionable:
            try:
                db = _open_idb()
                try:
                    for stream in actionable:
                        intel_type = stream.get("intelligence_type", "unknown")
                        entities = stream.get("extracted_entities", [])
                        entity_label = f" [{', '.join(entities[:3])}]" if entities else ""
                        title = f"{intel_type}{entity_label}"[:120]
                        tags = [{"type": "intelligence_type", "value": intel_type}]
                        # RB-DEFECT-2026-07-13: extracted_entities holds category/
                        # type labels (not real named entities) for every classifier
                        # except _classify_micro_graph -- each stream sets
                        # entity_scoped accordingly. Tagging every stream's
                        # extracted_entities as tag_type="entity" wrote macro-signal
                        # categories like "industry_trend"/"vendor_tech" into the DB
                        # as if they were named companies, which then surfaced as
                        # nonsense "[ENTITY PAIR] industry_trend + vendor_tech"
                        # convergence claims in Connect the Dots.
                        if stream.get("entity_scoped"):
                            tags += [{"type": "entity", "value": e} for e in entities[:10]]
                        item_id = db.add_item(
                            title=title,
                            content=stream.get("extracted_summary", "") or transcript[:500],
                            source_name=data.get("source_label", "capture"),
                            source_type=source_type,
                            gathered_date=data.get("queued_at"),
                            confidence=stream.get("confidence", "medium"),
                            lifecycle_state="new",
                            tags=tags,
                            raw_json={"triage_stream": stream, "capture_id": file_id},
                        )
                        persisted_items.append({
                            "item_id": item_id,
                            "intelligence_type": intel_type,
                            "extracted_summary": (stream.get("extracted_summary") or "")[:280],
                        })
                        persisted_count += 1
                finally:
                    db.close()
            except Exception:
                pass  # best-effort; do not fail the capture submit

    # Auto-execute executive declarations (authoritative — no confirmation needed)
    exec_mutations: list = []
    for stream in triage_result.get("identified_types", []):
        if stream.get("intelligence_type") == "executive_declaration":
            exec_mutations.extend(_execute_executive_declaration(stream, transcript, data.get("queued_at")))

    capture_ingest.mark_processed(file_id, result={
        # RB-DEFECT-2026-07-08: intelligence_triage.triage_input() has never
        # returned a "stream_count" key (only "type_count") -- this read
        # always silently fell back to the 0 default, permanently reporting
        # zero detected streams even when persisted_count showed real
        # intelligence was extracted and written. Every capture with
        # non-noise content was rendered as "no structured intelligence" in
        # the brief because of this key-name mismatch alone.
        "triage_stream_count": triage_result.get("type_count", 0),
        "triage_type_count": triage_result.get("type_count", 0),
        "noise_only": triage_result.get("noise_only", False),
        "persisted_count": persisted_count,
        "persisted_items": persisted_items[:10],
        "generic_streams_suppressed": suppress_generic_streams,
        "generic_streams_suppressed_count": suppressed_stream_count,
        # RB defect 2026-09-30: "exec_mutations" is the executive-declaration
        # mutation count ONLY -- it is not, and has never been, a total
        # canonical-mutation count. It was previously presented (in the
        # brief and here) as if it were, which is exactly how six real
        # deep-research packets reported "0" while a separate structured
        # importer applied ~126 real canonical mutations moments later with
        # no reconciliation between the two. executive_declaration_mutations
        # is the unambiguous name going forward; exec_mutations is kept for
        # existing callers but must not be read as "total mutations."
        "exec_mutations": len(exec_mutations),
        "executive_declaration_mutations": len(exec_mutations),
        # Populated with real counts by
        # reconcile_deep_research_capture_receipts.py once
        # import_competitor_platform_research.py has run for this packet
        # (it runs later in the same pipeline cycle). None here means
        # "not yet reconciled," not "zero" -- see structured_import_reconciled.
        "structured_import_reconciled": False,
        "structured_import_applied": None,
        "structured_import_deduped": None,
        "structured_import_queued": None,
        "structured_import_rejected": None,
        "canonical_targets_changed": None,
        "canonical_mutation_statement": (
            "Structured deep-research sidecar detected; structured-import outcome "
            "pending reconciliation later in this pipeline cycle."
            if sidecar_info["has_findings"] else
            f"executive_declaration_mutations={len(exec_mutations)}; no structured deep-research "
            "sidecar findings to reconcile for this capture."
        ),
        "llm_summary": llm_summary,
    })
    al.log_mutation_executed(
        f"submitCapture: file_id={file_id} status=processed persisted_count={persisted_count} "
        f"executive_declaration_mutations={len(exec_mutations)}",
        source="POST /captures/{file_id}/submit",
    )

    return {
        "status": "processed",
        "file_id": file_id,
        "title_hint": data.get("title_hint"),
        "capture_type": data.get("capture_type"),
        "word_count": data.get("word_count", 0),
        "triage": triage_result,
        "llm_summary": llm_summary,
        "persisted_intelligence_count": persisted_count,
        "persisted_intelligence": persisted_items,
        "executive_mutations": exec_mutations,
        "executive_mutations_count": len(exec_mutations),
        "mutations_proposed": len(triage_result.get("mutation_proposals", [])),
    }


def process_all_pending_captures() -> dict:
    """Submit all pending captures through the triage pipeline in sequence.

    Plain function (no FastAPI/auth dependency) so it can be called directly
    from a script (RB-DEFECT-2026-07-09: this logic previously only ran via
    the processAllCaptures HTTP route, reachable only by a live GPT chat
    command -- captures sat pending across multiple brief cycles because
    nothing ever called it automatically). The route below and
    system/scripts/process_pending_captures.py (invoked by morning_pipeline.py)
    both delegate to this same function so there is one source of truth.

    Returns a summary of what was processed.
    """
    items = capture_ingest.list_pending(limit=50)
    results = []
    for item in items:
        fid = item["file_id"]
        data = capture_ingest.get_pending(fid)
        if not data or not data.get("transcript"):
            # Same reasoning as submitCapture's no-transcript branch: this
            # capture can never gain a transcript later, so leaving it
            # un-marked means it resurfaces as "pending" on every future call.
            capture_ingest.mark_processed(fid, result={
                "skipped": True,
                "reason": "no_transcript_available",
            })
            results.append({"file_id": fid, "status": "skipped", "reason": "no_transcript"})
            continue
        try:
            source_type, content_label = _capture_processing_context(data.get("capture_type"))
            triage_result = intelligence_triage.triage_input(
                text=data["transcript"],
                source_type=source_type,
                captured_at=data.get("queued_at"),
            )
            # RB-DEFECT-2026-07-15: see the identical fix in submitCapture --
            # intelligence_triage's classifiers are keyword-trigger
            # detection, not comprehension. Best-effort: None without
            # OPENAI_API_KEY configured.
            llm_summary = transcript_summarizer.summarize_transcript(
                data["transcript"], word_count=data.get("word_count"), content_label=content_label)
            # RB defect 2026-09-30: see the identical suppression in
            # submitCapture -- a deep-research packet with a valid
            # structured sidecar should not also get generic
            # keyword-trigger false positives persisted as RI/entity noise.
            sidecar_info = capture_ingest.deep_research_sidecar_info(data.get("source_file"))
            suppress_generic_streams = (
                data.get("capture_type") == "deep_research" and sidecar_info["has_findings"]
            )
            suppressed_stream_count = (
                len([s for s in triage_result.get("identified_types", []) if s.get("intelligence_type") != "noise"])
                if suppress_generic_streams else 0
            )
            # Auto-persist non-noise streams
            persisted_count = 0
            persisted_items: list = []
            if _intelligence_db_module and not suppress_generic_streams:
                actionable = [s for s in triage_result.get("identified_types", [])
                              if s.get("intelligence_type") != "noise"]
                if actionable:
                    try:
                        db = _open_idb()
                        try:
                            for stream in actionable:
                                intel_type = stream.get("intelligence_type", "unknown")
                                entities = stream.get("extracted_entities", [])
                                entity_label = f" [{', '.join(entities[:3])}]" if entities else ""
                                title = f"{intel_type}{entity_label}"[:120]
                                tags = [{"type": "intelligence_type", "value": intel_type}]
                                # RB-DEFECT-2026-07-13: see the identical fix above --
                                # extracted_entities is category/type labels except
                                # when entity_scoped is set.
                                if stream.get("entity_scoped"):
                                    tags += [{"type": "entity", "value": e} for e in entities[:10]]
                                item_id = db.add_item(
                                    title=title,
                                    content=stream.get("extracted_summary", "") or data["transcript"][:500],
                                    source_name=data.get("source_label", "capture"),
                                    source_type=source_type,
                                    gathered_date=data.get("queued_at"),
                                    confidence=stream.get("confidence", "medium"),
                                    lifecycle_state="new",
                                    tags=tags,
                                    raw_json={"triage_stream": stream, "capture_id": fid},
                                )
                                persisted_items.append({
                                    "item_id": item_id,
                                    "intelligence_type": intel_type,
                                    "extracted_summary": (stream.get("extracted_summary") or "")[:280],
                                })
                                persisted_count += 1
                        finally:
                            db.close()
                    except Exception:
                        pass
            # Auto-execute executive declarations
            exec_mutations = []
            for stream in triage_result.get("identified_types", []):
                if stream.get("intelligence_type") == "executive_declaration":
                    exec_mutations.extend(_execute_executive_declaration(
                        stream, data["transcript"], data.get("queued_at")))
            # RB-DEFECT-2026-07-08: see submitCapture -- triage_input() never
            # returns "stream_count", only "type_count"; this always silently
            # defaulted to 0 and mismarked every capture as intelligence-free.
            capture_ingest.mark_processed(fid, result={
                "triage_stream_count": triage_result.get("type_count", 0),
                "noise_only": triage_result.get("noise_only", False),
                "persisted_count": persisted_count,
                "persisted_items": persisted_items[:10],
                "generic_streams_suppressed": suppress_generic_streams,
                "generic_streams_suppressed_count": suppressed_stream_count,
                # RB defect 2026-09-30: see submitCapture's identical fields
                # and the comment there -- exec_mutations is executive-
                # declaration mutations only, never a total mutation count.
                "exec_mutations": len(exec_mutations),
                "executive_declaration_mutations": len(exec_mutations),
                "structured_import_reconciled": False,
                "structured_import_applied": None,
                "structured_import_deduped": None,
                "structured_import_queued": None,
                "structured_import_rejected": None,
                "canonical_targets_changed": None,
                "canonical_mutation_statement": (
                    "Structured deep-research sidecar detected; structured-import outcome "
                    "pending reconciliation later in this pipeline cycle."
                    if sidecar_info["has_findings"] else
                    f"executive_declaration_mutations={len(exec_mutations)}; no structured deep-research "
                    "sidecar findings to reconcile for this capture."
                ),
                "llm_summary": llm_summary,
            })
            results.append({
                "file_id": fid,
                "status": "processed",
                "title_hint": data.get("title_hint"),
                "triage_stream_count": triage_result.get("type_count", 0),
                "persisted_count": persisted_count,
                "executive_declaration_mutations_count": len(exec_mutations),
            })
        except Exception as exc:
            results.append({"file_id": fid, "status": "error", "error": str(exc)})

    processed = sum(1 for r in results if r["status"] == "processed")
    return {
        "total": len(items),
        "processed": processed,
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }


@app.post("/captures/process_all", tags=["write"], operation_id="processAllCaptures")
def post_process_all_captures(
    x_api_key: Optional[str] = Header(None),
):
    """Submit all pending captures through the triage pipeline in sequence.

    Called by the GPT at morning brief time when pending captures are surfaced.
    Returns a summary of what was processed. See process_all_pending_captures()
    for the actual logic -- also called directly (no HTTP) by
    system/scripts/process_pending_captures.py from the scheduled pipeline.
    """
    _auth(x_api_key)
    return process_all_pending_captures()


@app.post("/webhooks/fathom", tags=["webhooks"], include_in_schema=False)
async def post_fathom_webhook(request: Request):
    """Receive Fathom call.completed webhook events.

    Fathom POSTs here when a recorded call finishes processing. The endpoint
    verifies the HMAC-SHA256 signature, extracts the transcript, and queues
    the capture in captures/pending/ for GPT processing.

    No x-api-key required — authenticated via Fathom's webhook signature.
    """
    import hmac
    import hashlib
    from fastapi.responses import JSONResponse

    webhook_secret = os.environ.get("FATHOM_WEBHOOK_SECRET", "")
    raw_body = await request.body()

    # Verify Fathom HMAC-SHA256 signature.
    # RB-SECURITY-2026-09-03: was fail-open -- verification only ran when
    # *both* webhook_secret and sig_header were present, so simply omitting
    # the signature header (or FATHOM_WEBHOOK_SECRET going unset) let any
    # POST body through unauthenticated, straight into the capture pipeline.
    # Fail closed on either being missing, matching /webhooks/fathom's own
    # docstring claim that a signature is required.
    sig_header = request.headers.get("X-Fathom-Signature") or request.headers.get("X-Webhook-Signature", "")
    if not webhook_secret or not sig_header:
        return JSONResponse(status_code=401, content={"error": "missing_signature_or_secret"})
    expected = "sha256=" + hmac.new(
        webhook_secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig_header):
        return JSONResponse(status_code=401, content={"error": "invalid_signature"})

    try:
        payload = json.loads(raw_body)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_json"})

    # Log raw payload for the first few events so we can verify field names
    _raw_log_path = SYSTEM_DIR / "captures" / "fathom_webhook_log.jsonl"
    _raw_log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(_raw_log_path, "a") as _f:
        _f.write(json.dumps({"received_at": datetime.utcnow().isoformat(), "payload": payload}) + "\n")

    event_type = (payload.get("event") or payload.get("type") or payload.get("event_type") or "").lower()
    # Fathom fires "Transcript" and "Action items" events separately.
    # We process on Transcript; Action items are extracted from the same payload if present.
    TRANSCRIPT_EVENTS = {"transcript", "recording.transcript", "call.transcript", "call.completed",
                         "call_completed", "recording.completed", "meeting.completed"}
    ACTION_ITEM_EVENTS = {"action_items", "action items", "recording.action_items", "call.action_items"}
    if event_type in ACTION_ITEM_EVENTS:
        # Action items arrive separately — store for later merge but don't re-queue
        return {"status": "noted", "event": event_type, "note": "action_items_logged_only"}
    if event_type not in TRANSCRIPT_EVENTS:
        return {"status": "ignored", "event": event_type}

    # Extract call data — handle Fathom's nested structure
    call = payload.get("call") or payload.get("recording") or payload.get("meeting") or payload
    call_id = str(call.get("id") or call.get("call_id") or "unknown")
    title = call.get("title") or call.get("name") or call.get("meeting_title") or "Fathom Recording"
    started_at = call.get("started_at") or call.get("created_at") or datetime.utcnow().isoformat()

    # Extract transcript — Fathom may nest it differently
    transcript_text = (
        call.get("transcript")
        or call.get("transcript_text")
        or call.get("full_transcript")
        or ""
    )
    # Fathom sometimes returns transcript as a list of utterances
    if isinstance(transcript_text, list):
        transcript_text = "\n".join(
            f"{u.get('speaker', 'Speaker')}: {u.get('text', '')}"
            for u in transcript_text
        )

    # Also capture summary and action items from Fathom's AI analysis
    summary = call.get("summary") or call.get("ai_summary") or ""
    action_items = call.get("action_items") or call.get("highlights") or []
    if action_items and isinstance(action_items, list):
        action_text = "\n".join(
            f"- {a.get('text', a) if isinstance(a, dict) else a}" for a in action_items
        )
        if action_text:
            transcript_text = f"{transcript_text}\n\n[Action Items]\n{action_text}".strip()
    if summary:
        transcript_text = f"[Summary]\n{summary}\n\n{transcript_text}".strip()

    if not transcript_text:
        return {"status": "skipped", "reason": "no_transcript", "call_id": call_id}

    # Write directly to pending queue
    import hashlib as _hl
    _fid = "cap-fathom-" + _hl.sha256(f"fathom:{call_id}".encode()).hexdigest()[:12]
    pending_path = SYSTEM_DIR / "captures" / "pending" / f"{_fid}.json"
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    capture_record = {
        "file_id": _fid,
        "queued_at": datetime.utcnow().isoformat(),
        "source_id": "fathom",
        "source_label": "Fathom",
        "source_file": f"fathom://call/{call_id}",
        "capture_type": "meeting",
        "title_hint": title,
        "transcription_method": "fathom_api",
        "transcript": transcript_text,
        "word_count": len(transcript_text.split()),
        "transcript_available": True,
        "status": "pending",
        "fathom_call_id": call_id,
        "fathom_started_at": started_at,
    }
    with open(pending_path, "w") as _f:
        json.dump(capture_record, _f, indent=2)

    return {"status": "queued", "file_id": _fid, "title": title, "words": capture_record["word_count"]}


@app.get("/smart_loops/proposals", tags=["compute"], operation_id="getSmartLoopProposals")
def get_smart_loop_proposals(
    date_str: Optional[str] = Query(None, alias="date",
                                    description="ISO date for the brief context (default: today)."),
    x_api_key: Optional[str] = Header(None),
):
    """Loop proposals derived from the canonical daily brief.

    Each proposal carries entity, source signal, action type, due date,
    verification method, confidence, and closure criteria — the fields P-009
    needs to actually open a tracked loop. Dedupe runs against the current
    loop ledger; matched items are returned in the `deduped` bucket with the
    existing loop id so the operator never opens the same loop twice.

    Read-only. No mutations. Use POST /smart_loops/apply with confirm=true
    to actually open loops.
    """
    _auth(x_api_key)
    d = date.fromisoformat(date_str) if date_str else date.today()
    report = daily_brief.build_report(d)
    return smart_loops.propose_loops(report, today=d)


@app.get("/network_gap", tags=["compute"], operation_id="getNetworkGap")
def get_network_gap(
    min_cluster: int = Query(5),
    gaps_only: bool = Query(False),
    limit: int = Query(25),
    x_api_key: Optional[str] = Header(None),
):
    """Company clusters scored by inner-tier RC anchor presence."""
    _auth(x_api_key)
    rows = core.cluster_inner_anchor_score(core.load_baseline(), min_cluster=min_cluster)
    if gaps_only:
        rows = [r for r in rows if r["anchor_gap"]]
    return rows[:limit]


@app.get("/drr_score", tags=["compute"], operation_id="getDrrScore")
def get_drr_score(
    contact_id: Optional[str] = Query(None, alias="id"),
    class_filter: Optional[str] = Query(None, alias="class"),
    limit: int = Query(50),
    date_str: Optional[str] = Query(None, alias="date"),
    x_api_key: Optional[str] = Header(None),
):
    """DRR score — single contact (if `id`) or top-N."""
    _auth(x_api_key)
    d = date.fromisoformat(date_str) if date_str else date.today()
    baseline = core.load_baseline()
    if contact_id:
        match = [e for e in baseline if e.get("id") == contact_id]
        if not match:
            raise HTTPException(404, f"no entry with id={contact_id!r}")
        return core.drr_score(match[0], d)
    rows = [core.drr_score(e, d) for e in baseline]
    if class_filter:
        rows = [r for r in rows if r["signal_class"] == class_filter]
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:limit]


@app.get("/status", tags=["docs"])
def get_status(x_api_key: Optional[str] = Header(None)):
    """Return STATUS.md content."""
    _auth(x_api_key)
    return {"content": (core.PROJECT_DIR / "system" / "STATUS.md").read_text()}


@app.get("/manifest", tags=["docs"])
def get_manifest(x_api_key: Optional[str] = Header(None)):
    """Return MANIFEST.md content."""
    _auth(x_api_key)
    return {"content": (core.PROJECT_DIR / "system" / "MANIFEST.md").read_text()}


@app.get("/protocols", tags=["docs"])
def list_protocols(x_api_key: Optional[str] = Header(None)):
    """Return protocols/index.json."""
    _auth(x_api_key)
    import json
    return json.loads((SYSTEM_DIR / "protocols" / "index.json").read_text())


@app.get("/cards/{contact_id}", tags=["docs"], operation_id="getCard")
def get_card(contact_id: str, x_api_key: Optional[str] = Header(None)):
    """Return a single card .md file for an RC contact, plus canonical
    baseline state so clients can detect projection drift.

    The rendered card markdown is a projection of canonical state held in
    baseline_index.json. Historically the two could diverge silently after a
    touch mutation (TOUCHCONTACT-VALIDATION-CONFLICT-001). The response now
    surfaces:

      - `content`              the raw card markdown (unchanged)
      - `canonical.last_touch` what baseline_index.json says
      - `frontmatter.last_touch` what the card YAML says (parsed, may be null)
      - `frontmatter_in_sync`  true when both agree (or both are missing)
      - `projection_status`    one of {in_sync, stale, frontmatter_missing,
                               canonical_missing, no_frontmatter_block}
    """
    _auth(x_api_key)
    import re as _re
    p = SYSTEM_DIR / "cards" / f"{contact_id}.md"
    if not p.exists():
        raise HTTPException(404, f"no card for id={contact_id!r}")
    text = p.read_text()
    # Canonical last_touch from baseline_index.json
    canonical_last_touch = None
    try:
        for e in core.load_baseline():
            if e.get("id") == contact_id:
                canonical_last_touch = e.get("last_touch")
                break
    except Exception:
        canonical_last_touch = None
    # Frontmatter last_touch
    fm_last_touch = None
    has_fm_block = False
    m = _re.match(r"^---\n(.*?\n)---\n", text, flags=_re.DOTALL)
    if m:
        has_fm_block = True
        fm_match = _re.search(
            r"^last_touch:\s*(.*)$", m.group(1), flags=_re.MULTILINE,
        )
        if fm_match:
            fm_last_touch = fm_match.group(1).strip() or None
    if not has_fm_block:
        projection_status = "no_frontmatter_block"
    elif canonical_last_touch is None and fm_last_touch is None:
        projection_status = "in_sync"
    elif canonical_last_touch is None:
        projection_status = "canonical_missing"
    elif fm_last_touch is None:
        projection_status = "frontmatter_missing"
    elif fm_last_touch == canonical_last_touch:
        projection_status = "in_sync"
    else:
        projection_status = "stale"
    return {
        "id": contact_id,
        "content": text,
        "canonical": {"last_touch": canonical_last_touch},
        "frontmatter": {"last_touch": fm_last_touch},
        "frontmatter_in_sync": projection_status == "in_sync",
        "projection_status": projection_status,
    }


class CreateRelationshipCardBody(BaseModel):
    generated_for: str = Field("", description="Who this is being prepared for, if relevant. Optional.")


@app.post("/relationship-cards/{contact_id}", tags=["write"], operation_id="createRelationshipCard")
def post_create_relationship_card(contact_id: str, body: CreateRelationshipCardBody, x_api_key: Optional[str] = Header(None)):
    """Publishes a versioned snapshot of one contact's existing Relationship
    Card (system/cards/{contact_id}.md) into the same governed artifact
    pattern every other artifact type has -- NOT a generator. The card's
    hand-authored content (Why this matters, Trust state, Leverage, What's
    lingering, Risks, How to engage) is Todd's own judgment and is taken
    verbatim; this call never rewrites the card file itself, only versions
    a copy of its current text for discoverability/history. RB-2026-09-08,
    relationship-side. Requires the contact to already be a real
    Relationship Card (signal_class == 'RC' in baseline_index.json) with an
    existing card file. No authorization quote required -- publishing
    content Todd already wrote is not a new AI-generated commitment."""
    _auth(x_api_key)
    try:
        result = relationship_card.publish_relationship_card(contact_id, generated_for=body.generated_for)
    except FileNotFoundError as exc:
        raise HTTPException(404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))
    return {"contact_id": result["contact_id"], "markdown": result["markdown"], "version": result["version"]}


@app.get("/relationship-cards/{contact_id}", tags=["compute"], operation_id="getRelationshipCard")
def get_relationship_card_detail(contact_id: str, x_api_key: Optional[str] = Header(None)):
    """Return the current PUBLISHED (versioned/indexed) Relationship Card
    for one contact, verbatim. Distinct from getCard, which reads the raw
    live file directly and checks for baseline/frontmatter last_touch
    drift -- use getCard for that drift check; use this for the governed,
    versioned, indexed artifact history. Use RULE-0 discipline: display the
    markdown as-is rather than re-summarizing it."""
    _auth(x_api_key)
    current = relationship_card.get_current_relationship_card(contact_id, include_content=True)
    if current is None:
        raise HTTPException(404, detail=f"No Relationship Card published yet for '{contact_id}'. Call createRelationshipCard to publish one.")
    return {"contact_id": contact_id, "markdown": current.get("content"), "version": current}


class CreateRelationshipPlanBody(BaseModel):
    relationship_goal: str = Field(..., min_length=1, description="What Todd wants this relationship to become -- real, specific, his own judgment. Never invented or templated.")
    next_moves: list[str] = Field(..., min_length=1, description="Real, concrete next moves toward the goal. Never invented.")
    status: str = Field(..., description="One of: active, paused, achieved, abandoned.")
    target_cadence_days: Optional[int] = Field(None, description="Target touch cadence in days, if relevant. Optional.")
    generated_for: str = Field("", description="Who this is being prepared for, if relevant. Optional.")
    user_authorization_quote: str = Field(..., min_length=1, description="REQUIRED. A verbatim quote of what the user actually said authorizing this specific Relationship Plan creation — not a paraphrase. Setting a relationship goal is a real commitment, same discipline as createAccountPlan/createWinPlan/createRfpResponsePlan.")


@app.post("/relationship-plans/{contact_id}", tags=["write"], operation_id="createRelationshipPlan")
def post_create_relationship_plan(contact_id: str, body: CreateRelationshipPlanBody, x_api_key: Optional[str] = Header(None)):
    """Creates (or regenerates) a Relationship Plan for one contact -- a
    forward-looking, real, caller-supplied goal for this relationship
    (e.g. 'deepen this over the next quarter,' 'build toward a Relationship
    Card') plus concrete next moves and a status. RB-2026-09-08,
    relationship-side, the final artifact in Todd's original taxonomy.
    Genuinely new -- distinct from getCard's 'How to engage' (communication
    STYLE, not goals) and from a transactional loop (this is an open-ended
    standing intent, not a one-off ask). Works for ANY baseline contact,
    not only existing Relationship Cards -- building an LMI/LKI contact
    toward RC is a real, intended use case. Never writes to
    baseline_index.json or system/cards/ -- vault-only, versioned like
    every other artifact. Only call this when the user has explicitly
    asked for a new or updated Relationship Plan by name, with a real
    user_authorization_quote."""
    _auth(x_api_key)
    try:
        result = relationship_plan.generate_relationship_plan(
            contact_id, relationship_goal=body.relationship_goal, next_moves=body.next_moves,
            status=body.status, target_cadence_days=body.target_cadence_days, generated_for=body.generated_for,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))
    return {"contact_id": result["contact_id"], "markdown": result["markdown"], "version": result["version"]}


@app.get("/relationship-plans/{contact_id}", tags=["compute"], operation_id="getRelationshipPlan")
def get_relationship_plan_detail(contact_id: str, x_api_key: Optional[str] = Header(None)):
    """Return the current Relationship Plan for one contact, verbatim. Use
    RULE-0 discipline: display the markdown as-is rather than
    re-summarizing it."""
    _auth(x_api_key)
    current = relationship_plan.get_current_relationship_plan(contact_id, include_content=True)
    if current is None:
        raise HTTPException(404, detail=f"No Relationship Plan exists yet for '{contact_id}'. Call createRelationshipPlan to make one.")
    return {"contact_id": contact_id, "markdown": current.get("content"), "version": current}


class CreateInnerCircleBody(BaseModel):
    generated_for: str = Field("", description="Who this is being prepared for, if relevant. Optional.")


@app.post("/inner-circle", tags=["write"], operation_id="createInnerCircle")
def post_create_inner_circle(body: CreateInnerCircleBody, x_api_key: Optional[str] = Header(None)):
    """Persists a versioned Inner Circle roll-up -- a single document
    (singleton, no per-contact/account scoping) listing every contact
    currently tagged rc_tier == 'inner' in baseline_index.json, each with
    last touch/DRR score/trust state/momentum and real open loops pulled
    from their card, split into a full Roster and a Needs Attention
    subset (already-computed strained/drifting/broken trust or negative
    momentum only -- never an invented staleness threshold). RB-2026-09-08,
    relationship-side. Distinct from the existing, differently-scoped
    system/circles/*.md concept (goal-anchored network-activation
    circles) -- not touched by this. Fully computed -- no authorization
    quote required, freely regenerable."""
    _auth(x_api_key)
    result = inner_circle.generate_inner_circle(generated_for=body.generated_for)
    return {"markdown": result["markdown"], "version": result["version"]}


@app.get("/inner-circle", tags=["compute"], operation_id="getInnerCircle")
def get_inner_circle_detail(x_api_key: Optional[str] = Header(None)):
    """Return the current persisted Inner Circle roll-up, verbatim. Use
    RULE-0 discipline: display the markdown as-is rather than
    re-summarizing it."""
    _auth(x_api_key)
    current = inner_circle.get_current_inner_circle(include_content=True)
    if current is None:
        raise HTTPException(404, detail="No Inner Circle has been generated yet. Call createInnerCircle to make one.")
    return {"markdown": current.get("content"), "version": current}


class CreateReferralNetworkOverviewBody(BaseModel):
    generated_for: str = Field("", description="Who this is being prepared for, if relevant. Optional.")


@app.post("/referral-network/overview", tags=["write"], operation_id="createReferralNetworkOverview")
def post_create_referral_network_overview(body: CreateReferralNetworkOverviewBody, x_api_key: Optional[str] = Header(None)):
    """Publishes a versioned snapshot of Todd's EXISTING hand-curated
    referral/broker overview (system/intro_brokers.md) into the same
    governed artifact pattern every other artifact type has -- NOT a
    generator. RB-2026-09-08, relationship-side. Does not fix the source
    file's own staleness (it's Todd's strategic narrative to refresh, not
    something this computes) -- publishing at least makes staleness
    visible via each snapshot's own generated_at. No authorization quote
    required -- publishing content Todd already wrote is not a new
    AI-generated commitment. Singleton, no path param."""
    _auth(x_api_key)
    try:
        result = referral_network.publish_referral_network_overview(generated_for=body.generated_for)
    except FileNotFoundError as exc:
        raise HTTPException(404, detail=str(exc))
    return {"markdown": result["markdown"], "version": result["version"]}


@app.get("/referral-network/overview", tags=["compute"], operation_id="getReferralNetworkOverview")
def get_referral_network_overview_detail(x_api_key: Optional[str] = Header(None)):
    """Return the current PUBLISHED Referral Network Overview, verbatim.
    Use RULE-0 discipline: display the markdown as-is rather than
    re-summarizing it."""
    _auth(x_api_key)
    current = referral_network.get_current_referral_network_overview(include_content=True)
    if current is None:
        raise HTTPException(404, detail="No Referral Network Overview published yet. Call createReferralNetworkOverview to publish one.")
    return {"markdown": current.get("content"), "version": current}


class CreateReferralNetworkAnalysisBody(BaseModel):
    generated_for: str = Field("", description="Who this is being prepared for, if relevant. Optional.")


@app.post("/referral-network/analysis/{target}", tags=["write"], operation_id="createReferralNetworkAnalysis")
def post_create_referral_network_analysis(target: str, body: CreateReferralNetworkAnalysisBody, x_api_key: Optional[str] = Header(None)):
    """Persists a versioned Referral Network Analysis for one target
    (person or company) -- a markdown rendering of findIntro/
    find_intro_paths's own already-computed content (insiders at the
    target, ranked candidate brokers with DRR/composite score/recommended
    posture/a drafted ask). RB-2026-09-08, relationship-side. findIntro
    (GET /intro) is unchanged and still computes fresh by design -- this is
    a parallel, additive persisted view, the same relationship
    createBattleCard has to getCategoryMarketShare. Fully computed -- no
    authorization quote required. Raises if the target doesn't resolve to
    a known person or company."""
    _auth(x_api_key)
    try:
        result = referral_network.generate_referral_network_analysis(target, generated_for=body.generated_for)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))
    return {"target": result["target"], "markdown": result["markdown"], "version": result["version"]}


@app.get("/referral-network/analysis/{target}", tags=["compute"], operation_id="getReferralNetworkAnalysis")
def get_referral_network_analysis_detail(target: str, x_api_key: Optional[str] = Header(None)):
    """Return the current persisted Referral Network Analysis for one
    target, verbatim. Use RULE-0 discipline: display the markdown as-is
    rather than re-summarizing it. For a fresh computed view without
    persisting it, use findIntro instead."""
    _auth(x_api_key)
    current = referral_network.get_current_referral_network_analysis(target, include_content=True)
    if current is None:
        raise HTTPException(404, detail=f"No Referral Network Analysis exists yet for '{target}'. Call createReferralNetworkAnalysis to make one.")
    return {"target": target, "markdown": current.get("content"), "version": current}


@app.get("/contacts/resolve_linkedin", tags=["compute"], operation_id="resolveLinkedInProfile")
def resolve_linkedin_profile(
    url: str = Query(..., description="A LinkedIn profile URL, e.g. https://www.linkedin.com/in/jane-doe-12345"),
    x_api_key: Optional[str] = Header(None),
):
    """RB-DEFECT-034 — recognize a supplied LinkedIn profile URL and resolve it
    against the existing contact graph, instead of falling through to a generic
    'please paste the profile content' deflection.

    This is an *identity-resolution* endpoint, not a live-fetch endpoint — RB
    has no LinkedIn scraping/API connector (and shouldn't build one casually;
    see RB-DEFECT-034's scope notes on ToS/fragility). What it *does* have is a
    Full LinkedIn Export already loaded into `baseline_index.json` with a
    `linkedin_url` field per contact — so most supplied URLs can be resolved
    against data already on hand, with zero new retrieval.

    Behavior:
      - **match found** → returns `status: "matched"` plus the contact's id,
        name, company/role, baseline fields, and `card_hint` (call `getCard`
        with this id for full relationship intelligence — career history,
        loop history, circles, trust signals — already in the graph).
      - **no match** → returns `status: "no_match"` with an honest explanation
        and `next_step` guidance (offer to create a stub contact via
        `manualRelationshipIntake`, do NOT silently ask the user to paste
        content as if no resolution were attempted).
      - **unrecognized URL** → `status: "not_a_linkedin_profile_url"`.

    Matching is by profile slug (the `/in/<slug>` segment), case-insensitive,
    with trailing-slash/query-string/locale-prefix normalization — the same
    identifier shape already recorded passively on baseline contacts from
    LinkedIn export ingestion.
    """
    _auth(x_api_key)
    import re as _re
    from urllib.parse import urlparse, unquote as _unquote

    def _slug(u: str) -> Optional[str]:
        try:
            path = urlparse(u.strip()).path or u
        except Exception:
            path = u
        m = _re.search(r"/in/([^/?#]+)", path)
        if not m:
            return None
        return _unquote(m.group(1)).strip().strip("/").lower()

    target_slug = _slug(url)
    if not target_slug:
        return {
            "status": "not_a_linkedin_profile_url",
            "url": url,
            "note": "Could not find an '/in/<slug>' segment — this doesn't look like a LinkedIn profile URL.",
        }

    baseline = core.load_baseline()
    match = None
    for contact in baseline:
        cu = contact.get("linkedin_url")
        if not cu:
            continue
        if _slug(cu) == target_slug:
            match = contact
            break

    if match:
        return {
            "status": "matched",
            "url": url,
            "matched_slug": target_slug,
            "contact": {
                "id": match.get("id"),
                "name": match.get("name"),
                "current_company": match.get("current_company"),
                "current_role": match.get("current_role"),
                "location": match.get("location"),
                "linkedin_url": match.get("linkedin_url"),
                "signal_class": match.get("signal_class"),
                "rc_tier": match.get("rc_tier"),
                "last_touch": match.get("last_touch"),
                "circles": match.get("circles") or [],
                "tags": match.get("tags") or [],
            },
            "card_hint": f"Call getCard with id={match.get('id')!r} for full relationship intelligence "
                         f"(career history, loop history, circles, trust signals) already in the graph — "
                         f"no new retrieval needed.",
            "note": "Resolved against existing contact graph (LinkedIn export data already on hand). "
                    "Render relationship intelligence from this record; do not ask the user to paste profile content.",
        }

    return {
        "status": "no_match",
        "url": url,
        "matched_slug": target_slug,
        "note": "No existing contact record carries this LinkedIn profile slug. RB has no live LinkedIn "
                "fetch/scrape connector (by design — see RB-DEFECT-034), so it cannot retrieve this "
                "profile's content directly.",
        "next_step": "Offer to create a stub contact via manualRelationshipIntake (name + company + role + "
                     "this URL), or ask the user to share what they know about this person so RB can build "
                     "the record — do NOT respond with a bare 'please paste the profile content' with no "
                     "attempt at resolution or alternative offered.",
    }


@app.post("/contacts/ingest_linkedin_profile", tags=["write"], operation_id="ingestLinkedInProfile")
def post_ingest_linkedin_profile(
    body: dict,
    x_api_key: Optional[str] = Header(None),
):
    """RB-DEFECT-034 #2 — ingest a LinkedIn profile captured via browser-session JS snippet.

    Accepts the JSON payload produced by running the profile capture JavaScript snippet
    in the browser console on a LinkedIn profile page (linkedin.com/in/<slug>). The user
    pastes the JS, copies the output, and posts it here.

    This is the same 'browser-session capture' pattern as the LinkedIn feed capture
    (linkedin_session_reader.py) — zero automation, zero login, zero credentials stored.
    The operator navigates to the page in their own browser and manually captures what
    they can already see.

    Behavior (mirrors linkedin_session_reader.ingest_profile):
      - Resolves the profile slug against existing baseline contacts.
      - **Match found** → enriches the existing contact with fresh headline/company/role/
        experience/education from the capture, preserving all curated RB fields.
      - **No match** → creates a stub contact (`_stub: true`) for manual-intake follow-up.
      - `confirm=false` (default) → dry run, returns preview of what would change.
      - `confirm=true` → writes to baseline_index.json.

    To get the capture JS snippet, run:
        python3 system/scripts/linkedin_session_reader.py --capture-profile-js
    """
    _auth(x_api_key)
    import linkedin_session_reader as _lsr
    confirm = bool(body.get("confirm", False))
    profile_data = body.get("profile_data") or body  # accept raw payload or wrapped
    # If wrapped under "profile_data" key, unwrap; otherwise treat the whole body as the profile
    if "profile_data" in body:
        profile_data = body["profile_data"]

    try:
        result = _lsr.ingest_profile(profile_data, dry_run=not confirm)
        return result
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"profile ingest error: {exc}") from exc


@app.get("/identity/candidates", tags=["compute"], operation_id="getIdentityMatchCandidates")
def get_identity_match_candidates(
    x_api_key: Optional[str] = Header(None),
):
    """List pending identity-match candidates awaiting operator confirmation.

    Most baseline contacts have no email on file (LinkedIn imports don't expose
    email addresses). When an inbound email sender's name exactly matches an
    email-less baseline contact, identity_match_review.py proposes the link here
    rather than auto-merging — see the false-positive rule (never merge on name
    alone) in the contact-rationalization architecture notes.
    """
    _auth(x_api_key)
    try:
        candidates = identity_match_review.pending_candidates()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"identity candidate scan error: {exc}") from exc
    return {"pending": candidates, "count": len(candidates)}


@app.post("/identity/{candidate_id}/confirm", tags=["write"], operation_id="confirmIdentityMatch")
def post_confirm_identity_match(
    candidate_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """Confirm an identity-match candidate is the same person.

    Writes the inbound sender's email onto the baseline contact record and
    appends a dated note. This is the only way an email gets attached to a
    baseline contact from this pipeline — never automatic. Once confirmed,
    the pair is never re-surfaced.
    """
    _auth(x_api_key)
    result = identity_match_review.confirm(candidate_id)
    if "error" in result:
        raise HTTPException(404, detail=result["error"])
    return result


@app.post("/identity/{candidate_id}/reject", tags=["write"], operation_id="rejectIdentityMatch")
def post_reject_identity_match(
    candidate_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """Reject an identity-match candidate — not the same person.

    Marks the (baseline contact, sender email) pair as rejected so it is
    never re-surfaced. Does not modify the baseline record or create a new one.
    """
    _auth(x_api_key)
    result = identity_match_review.reject(candidate_id)
    if "error" in result:
        raise HTTPException(404, detail=result["error"])
    return result


@app.get("/active_threads", tags=["docs"], operation_id="listActiveThreads")
def list_active_threads(
    open_only: bool = Query(True),
    x_api_key: Optional[str] = Header(None),
):
    """Return active strategic threads from active_threads.yaml."""
    _auth(x_api_key)
    threads = core.load_active_threads()
    if open_only:
        threads = [t for t in threads if t.get("status") == "open"]
    return threads


@app.get("/cockpit/context", tags=["compute"], operation_id="getCockpitContext")
def get_cockpit_context(x_api_key: Optional[str] = Header(None)):
    """Live-generated cockpit projection — weekly outcomes, active opportunities,
    open loops, active theses, recent material intelligence, pending decisions,
    reconciliation queue. Same content/shape as system/cockpit/context.json
    (RB-2026-08-19's cockpit_projection_contract), built on demand from the
    live repo state rather than read from a snapshot file. Added 2026-08-25
    (RB cockpit architecture gate 4) so a Custom GPT session can pull fresh
    state itself instead of depending on a manually re-uploaded snapshot."""
    _auth(x_api_key)
    return cockpit_context.build_context()


@app.get("/calendar_overlay", tags=["compute"], operation_id="getCalendarOverlay")
def get_calendar_overlay(
    date_str: Optional[str] = Query(None, alias="date"),
    x_api_key: Optional[str] = Header(None),
):
    """Calendar overlay — bucketed today/tomorrow/this_week with baseline matches."""
    _auth(x_api_key)
    d = date.fromisoformat(date_str) if date_str else date.today()
    return core.calendar_overlay(d)


@app.get("/source_readiness", tags=["compute"], operation_id="getSourceReadiness")
def get_source_readiness(
    feed: Optional[str] = Query(None, description="Optional feed name: calendar, email, email_sent"),
    x_api_key: Optional[str] = Header(None),
):
    """Expected-vs-observed connector coverage for calendar/email sources."""
    _auth(x_api_key)
    return core.source_readiness(feed=feed)


@app.get("/email_overlay", tags=["compute"], operation_id="getEmailOverlay")
def get_email_overlay(x_api_key: Optional[str] = Header(None)):
    """Email overlay — inbox threads matched against baseline + active-thread companies."""
    _auth(x_api_key)
    return core.email_overlay()


@app.get("/interaction_overlay", tags=["compute"], operation_id="getInteractionOverlay")
def get_interaction_overlay(
    date_str: Optional[str] = Query(None, alias="date"),
    recent_days: int = Query(30),
    x_api_key: Optional[str] = Header(None),
):
    """Direct interaction signal — phone + text overlay against baseline."""
    _auth(x_api_key)
    d = date.fromisoformat(date_str) if date_str else date.today()
    return core.interaction_overlay(today=d, recent_days=recent_days)


@app.get("/social_outbound_overlay", tags=["compute"], operation_id="getSocialOutboundOverlay")
def get_social_outbound_overlay(
    date_str: Optional[str] = Query(None, alias="date"),
    recent_days: int = Query(30),
    x_api_key: Optional[str] = Header(None),
):
    """Engagement signal on your own posts."""
    _auth(x_api_key)
    d = date.fromisoformat(date_str) if date_str else date.today()
    return core.social_outbound_overlay(today=d, recent_days=recent_days)


@app.get("/post_recommendations", tags=["compute"], operation_id="getPostRecommendations")
def get_post_recommendations(
    date_str: Optional[str] = Query(None, alias="date"),
    x_api_key: Optional[str] = Header(None),
):
    """Ranked post recommendations grounded in active threads + engagement history."""
    _auth(x_api_key)
    d = date.fromisoformat(date_str) if date_str else date.today()
    return core.post_recommendations(today=d)


class MyPostIn(BaseModel):
    text: str
    posted_at: Optional[str] = None
    topics: Optional[list[str]] = None
    url: Optional[str] = None
    platform: str = "linkedin"
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    impressions: Optional[int] = None
    id: Optional[str] = None


@app.post("/my_posts", tags=["write"])
def post_my_post(body: MyPostIn, x_api_key: Optional[str] = Header(None)):
    """Append a single own-post."""
    _auth(x_api_key)
    rc = mutations.cmd_my_post_add(_ns(
        text=body.text, posted_at=body.posted_at, topics=body.topics,
        url=body.url, platform=body.platform, likes=body.likes,
        comments=body.comments, shares=body.shares, impressions=body.impressions,
        id=body.id, dry_run=False,
    ))
    if rc != 0:
        raise HTTPException(400, "my-post-add failed")
    return {"ok": True}


class EngagementIn(BaseModel):
    post_id: str
    type: str
    engager_name: str
    engager_url: Optional[str] = None
    at: Optional[str] = None
    comment_text: Optional[str] = None


@app.post("/engagement", tags=["write"])
def post_engagement(body: EngagementIn, x_api_key: Optional[str] = Header(None)):
    """Append a single engagement event."""
    _auth(x_api_key)
    rc = mutations.cmd_engagement_add(_ns(
        post_id=body.post_id, type=body.type,
        engager_name=body.engager_name, engager_url=body.engager_url,
        at=body.at, comment_text=body.comment_text, dry_run=False,
    ))
    if rc != 0:
        raise HTTPException(400, "engagement-add failed")
    return {"ok": True}


@app.get("/network_analysis", tags=["compute"], operation_id="getNetworkAnalysis")
def get_network_analysis(
    date_str: Optional[str] = Query(None, alias="date"),
    x_api_key: Optional[str] = Header(None),
):
    """Strategic network report card — strengths, weaknesses, bridges, recommendations."""
    _auth(x_api_key)
    d = date.fromisoformat(date_str) if date_str else date.today()
    return core.network_analysis(today=d)


def _load_micro_graph_registry() -> dict:
    path = SYSTEM_DIR / "graphs" / "index.json"
    if not path.exists():
        return {
            "version": 1,
            "contract": "rb_graph_registry_v1",
            "graphs": [],
            "status": "not_indexed",
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "version": 1,
            "contract": "rb_graph_registry_v1",
            "graphs": [],
            "status": "unreadable",
            "reason": str(exc),
        }


def _match_micro_graph(registry: dict, query: str | None) -> dict | None:
    graphs = registry.get("graphs") or []
    if not query:
        return graphs[0] if len(graphs) == 1 else None
    q = query.lower().replace("'", "")
    for graph in graphs:
        haystack = " ".join([
            str(graph.get("graph_slug") or ""),
            str(graph.get("graph_id") or ""),
            str(graph.get("name") or ""),
            str(graph.get("source_workbook") or ""),
            " ".join(graph.get("activation_terms") or []),
        ]).lower().replace("'", "")
        if q in haystack or any(part and part in haystack for part in q.replace("-", " ").replace("_", " ").split()):
            return graph
    return None


@app.get("/graphs/micro_summary", tags=["compute"], operation_id="getMicroGraphSummary")
def get_micro_graph_summary(
    query: Optional[str] = Query(None, description="Graph slug or search term, e.g. mcdonalds_us_ops or McDonald's."),
    query_type: Optional[str] = Query(
        None,
        description=(
            "Structured query type for precision retrieval. One of: operator_count, "
            "largest_operators, state_lookup, coop_lookup, city_lookup, operator_lookup, "
            "threshold_lookup, staff_lookup, coop_detail, relationship_coverage, summary "
            "(default = full index summary). Common params: state= (state_lookup), "
            "city=+state= (city_lookup), name= (operator_lookup/staff_lookup), "
            "min_stores=/max_stores= (threshold_lookup), role=fbp|otm|otp (staff_lookup), "
            "coop= (coop_lookup/coop_detail). relationship_coverage returns baseline "
            "contacts at this company; operator_count/largest_operators need no params."
        ),
    ),
    state: Optional[str] = Query(
        None,
        description="State abbreviation filter (state_lookup, city_lookup, staff_lookup), e.g. WI, TX, CA.",
    ),
    city: Optional[str] = Query(
        None,
        description="City name for city_lookup, e.g. 'Fond du Lac'. Case-insensitive.",
    ),
    name: Optional[str] = Query(
        None,
        description="Operator or person name for operator_lookup or staff_lookup. Partial match, case-insensitive.",
    ),
    role: Optional[str] = Query(
        None,
        description="Role filter for staff_lookup: fbp, otm, otp, operator.",
    ),
    min_stores: Optional[int] = Query(
        None,
        ge=1,
        description="Minimum store count for threshold_lookup.",
    ),
    max_stores: Optional[int] = Query(
        None,
        ge=1,
        description="Maximum store count for threshold_lookup (optional upper bound).",
    ),
    coop: Optional[str] = Query(
        None,
        description="Co-op name for coop_detail, e.g. 'Chicago'. Partial match, case-insensitive.",
    ),
    limit: int = Query(10, ge=1, le=50),
    x_api_key: Optional[str] = Header(None),
):
    """Return a compact summary for indexed micro ecosystem graphs.

    This is the Custom GPT-safe retrieval surface for large dormant micro graphs.
    It returns registry data when no graph matches and compact index data when a
    graph is found.

    Use query_type for structured precision queries:
      - operator_count: how many operators/franchisees and what tier breakdown
      - largest_operators: top operators ranked by store count
      - state_lookup: store counts by state (optionally filtered by state=XX)
      - coop_lookup: store counts by co-op region
      - city_lookup: operators and stores for a specific city (city=, state=)
      - operator_lookup: detail for a named operator (name=)
      - threshold_lookup: operators above/below a store count (min_stores=, max_stores=)
      - staff_lookup: support staff by name or role (name= or role=fbp/otm/otp, state=)
      - coop_detail: operator breakdown within a specific co-op (coop=)
      - relationship_coverage: baseline contacts at this company
      - summary (default): full index summary with all sections
    """
    _auth(x_api_key)
    registry = _load_micro_graph_registry()
    matched = _match_micro_graph(registry, query)
    if not matched:
        return {
            "status": "registry_only",
            "query": query,
            "registry": registry,
            "note": "No matching micro graph index found; use one of registry.graphs[].graph_slug if present.",
        }
    index_rel = matched.get("index_path")
    if not index_rel:
        return {"status": "missing_index_path", "graph": matched}
    index_path = core.PROJECT_DIR / index_rel
    if not index_path.exists():
        return {"status": "missing_index_file", "graph": matched, "index_path": index_rel}
    index = json.loads(index_path.read_text(encoding="utf-8"))
    counts = index.get("counts") or {}
    tiers = counts.get("operator_tiers") or {}

    # ── shared meta fields ────────────────────────────────────────────────────
    meta = {
        "status": "found",
        "graph_id": index.get("graph_id"),
        "graph_slug": index.get("graph_slug"),
        "name": index.get("name"),
        "source_workbook": index.get("source_workbook"),
        "query_type": query_type or "summary",
    }

    # ── operator_count ────────────────────────────────────────────────────────
    if query_type == "operator_count":
        answer_template = (index.get("retrieval_answer_templates") or {}).get("operator_distribution")
        answer = None
        if answer_template:
            answer = answer_template.format(
                operator_entities_with_stores=counts.get("operator_entities_with_stores", 0),
                stores_with_operator_entity=counts.get("stores_with_operator_entity", 0),
                enterprise_25_plus=tiers.get("enterprise_25_plus", 0),
                mid_tier_5_to_24=tiers.get("mid_tier_5_to_24", 0),
                single_digit_1_to_4=tiers.get("single_digit_1_to_4", 0),
            )
        return {
            **meta,
            "answer": answer,
            "counts": counts,
            "operator_tiers": tiers,
        }

    # ── largest_operators ─────────────────────────────────────────────────────
    if query_type == "largest_operators":
        entities = (index.get("top_operator_entities") or [])[:limit]
        return {
            **meta,
            "answer": f"Top {len(entities)} operator/entity nodes by store count (source: {index.get('source_workbook')}).",
            "top_operator_entities": entities,
            "total_operator_entities_with_stores": counts.get("operator_entities_with_stores", 0),
        }

    # ── state_lookup ──────────────────────────────────────────────────────────
    if query_type == "state_lookup":
        state_dist = index.get("state_distribution") or []
        if state:
            state_upper = state.upper()
            filtered = [s for s in state_dist if s.get("state", "").upper() == state_upper]
            answer = (
                f"{filtered[0]['store_count']} stores in {state_upper} (source: {index.get('source_workbook')})."
                if filtered else f"No stores found for state '{state_upper}'."
            )
            return {**meta, "answer": answer, "state_distribution": filtered, "filter_state": state_upper}
        return {
            **meta,
            "answer": f"Store distribution across {len(state_dist)} states (source: {index.get('source_workbook')}).",
            "state_distribution": state_dist[:limit],
            "total_states": len(state_dist),
        }

    # ── coop_lookup ───────────────────────────────────────────────────────────
    if query_type == "coop_lookup":
        coops = (index.get("coop_distribution") or [])[:limit]
        return {
            **meta,
            "answer": f"Top {len(coops)} co-op regions by store count (source: {index.get('source_workbook')}).",
            "coop_distribution": coops,
            "total_coops": len(index.get("coop_distribution") or []),
        }

    # ── city_lookup ───────────────────────────────────────────────────────────
    if query_type == "city_lookup":
        city_filter = (city or "").strip().upper()
        state_filter = (state or "").strip().upper() or None

        if not city_filter:
            return {
                **meta,
                "status": "missing_city",
                "answer": "city_lookup requires a city= parameter, e.g. city=Fond+du+Lac&state=WI.",
            }

        graph_path = core.PROJECT_DIR / (index.get("paths", {}).get("graph") or "")
        if not graph_path.exists():
            return {**meta, "status": "graph_unavailable",
                    "answer": "City lookup requires graph.json which is not available."}

        graph_data = json.loads(graph_path.read_text(encoding="utf-8"))
        gnodes = graph_data.get("nodes") or []
        gedges = graph_data.get("edges") or []
        node_by_id = {n["id"]: n for n in gnodes if isinstance(n, dict) and "id" in n}

        # Find matching store nodes
        matched_stores = []
        for n in gnodes:
            if not isinstance(n, dict) or n.get("type") != "store":
                continue
            attrs = n.get("attributes") or {}
            node_city = (attrs.get("city") or "").upper()
            node_state = (attrs.get("state") or "").upper()
            if city_filter and city_filter not in node_city:
                continue
            if state_filter and node_state != state_filter:
                continue
            matched_stores.append(n)

        if not matched_stores:
            return {
                **meta,
                "answer": f"No stores found for city '{city_filter}'"
                          + (f", {state_filter}" if state_filter else "") + ".",
                "store_count": 0,
                "stores": [],
            }

        store_ids = {s["id"] for s in matched_stores}

        # Resolve operators and support staff via edges
        operators: dict[str, dict] = {}
        support_staff: dict[str, dict] = {}
        for e in gedges:
            if not isinstance(e, dict):
                continue
            frm = e.get("from") or e.get("source")
            to = e.get("to") or e.get("target")
            etype = e.get("type") or ""
            if frm not in store_ids:
                continue
            target_node = node_by_id.get(to, {})
            tname = target_node.get("name") or (target_node.get("attributes") or {}).get("name") or to
            if etype == "operated_by_entity":
                operators.setdefault(to, {"name": tname, "store_count": 0, "stores": []})
                operators[to]["store_count"] += 1
                operators[to]["stores"].append(frm)
            elif etype in ("operator_for_store", "fbp_for_store", "otm_for_store", "otp_for_store"):
                # Reverse edge — from goes to store, so check target->store
                pass
        # Also check reverse person->store edges
        for e in gedges:
            if not isinstance(e, dict):
                continue
            frm = e.get("from") or e.get("source")
            to = e.get("to") or e.get("target")
            etype = e.get("type") or ""
            if to not in store_ids:
                continue
            src_node = node_by_id.get(frm, {})
            sname = src_node.get("name") or (src_node.get("attributes") or {}).get("name") or frm
            role = (src_node.get("attributes") or {}).get("role") or etype.split("_")[0]
            email = (src_node.get("attributes") or {}).get("email") or ""
            if etype in ("operator_for_store", "fbp_for_store", "otm_for_store", "otp_for_store"):
                key = f"{role}:{frm}"
                support_staff.setdefault(key, {"name": sname, "role": role, "email": email, "stores": []})
                support_staff[key]["stores"].append(to)

        city_label = city_filter.title() if city_filter else "city"
        state_label = (", " + state_filter) if state_filter else ""
        store_detail = [
            {
                "nsn": s.get("attributes", {}).get("nsn"),
                "restaurant_name": s.get("attributes", {}).get("restaurant_name"),
                "city": s.get("attributes", {}).get("city"),
                "state": s.get("attributes", {}).get("state"),
                "store_id": s["id"],
            }
            for s in matched_stores
        ]

        op_list = sorted(operators.values(), key=lambda x: -x["store_count"])
        if op_list:
            op_names = ", ".join(f"{o['name']} ({o['store_count']} store{'s' if o['store_count'] != 1 else ''})"
                                 for o in op_list)
            answer = (
                f"{len(matched_stores)} McDonald's store{'s' if len(matched_stores) != 1 else ''}"
                f" in {city_label}{state_label}. "
                f"Operator{'s' if len(op_list) > 1 else ''}: {op_names}."
            )
        else:
            answer = (
                f"{len(matched_stores)} McDonald's store{'s' if len(matched_stores) != 1 else ''}"
                f" in {city_label}{state_label}. Operator data not resolved."
            )
        return {
            **meta,
            "answer": answer,
            "store_count": len(matched_stores),
            "stores": store_detail,
            "operators": op_list,
            "support_staff": list(support_staff.values()),
            "note": (
                "Source: RB Micro Graph (graph.json traversal). "
                "Cite as: tier=1, confidence=verified."
            ),
        }

    # ── operator_lookup ───────────────────────────────────────────────────────
    if query_type == "operator_lookup":
        if not name:
            return {**meta, "status": "missing_name",
                    "answer": "operator_lookup requires a name= parameter, e.g. name=Rause."}
        name_upper = name.strip().upper()

        # Fast path: search top_operator_entities in index (no graph.json load)
        top_ops = index.get("top_operator_entities") or []
        fast_matches = [o for o in top_ops if name_upper in (o.get("name") or "").upper()]
        if fast_matches:
            op = fast_matches[0]
            # states is a dict {state_code: store_count} in the index
            states_raw = op.get("states") or {}
            state_keys = list(states_raw.keys()) if isinstance(states_raw, dict) else list(states_raw)
            state_count = op.get("state_count") or len(state_keys)
            state_str = ", ".join(state_keys[:5])
            answer = (
                f"{op['name']} operates {op.get('store_count', 0)} "
                f"McDonald's store{'s' if op.get('store_count', 0) != 1 else ''} "
                f"across {state_count} state{'s' if state_count != 1 else ''}"
                + (f" ({state_str})" if state_str else "") + "."
            )
            return {**meta, "answer": answer, "operator": op, "source": "index",
                    "note": "Source: index top_operator_entities. For store-level detail, requery with graph.json."}

        # Full path: load graph.json and search operator_entity nodes
        graph_path = core.PROJECT_DIR / (index.get("paths", {}).get("graph") or "")
        if not graph_path.exists():
            return {**meta, "status": "not_found",
                    "answer": f"No operator matching '{name}' found in index and graph.json is unavailable."}

        graph_data = json.loads(graph_path.read_text(encoding="utf-8"))
        gnodes = graph_data.get("nodes") or []
        gedges = graph_data.get("edges") or []
        node_by_id = {n["id"]: n for n in gnodes if isinstance(n, dict) and "id" in n}

        op_nodes = [
            n for n in gnodes
            if isinstance(n, dict) and n.get("type") == "entity"
            and name_upper in (n.get("name") or "").upper()
        ]
        if not op_nodes:
            return {**meta, "status": "not_found",
                    "answer": f"No operator matching '{name}' found in the McDonald's graph."}

        results = []
        for op_node in op_nodes[:5]:
            op_stores = []
            for e in gedges:
                if not isinstance(e, dict):
                    continue
                frm = e.get("from") or e.get("source")
                to = e.get("to") or e.get("target")
                if e.get("type") == "operated_by_entity" and to == op_node["id"]:
                    store_node = node_by_id.get(frm, {})
                    attrs = store_node.get("attributes") or {}
                    op_stores.append({"nsn": attrs.get("nsn"), "city": attrs.get("city"),
                                      "state": attrs.get("state"), "store_id": frm})
            states = sorted({s["state"] for s in op_stores if s.get("state")})
            results.append({
                "name": op_node.get("name"),
                "store_count": len(op_stores),
                "states": states,
                "stores": op_stores[:limit],
            })

        if len(results) == 1:
            op = results[0]
            state_str = ", ".join(op["states"][:5])
            answer = (
                f"{op['name']} operates {op['store_count']} "
                f"McDonald's store{'s' if op['store_count'] != 1 else ''}"
                + (f" in {state_str}" if state_str else "") + "."
            )
        else:
            answer = f"{len(results)} operators matching '{name}' found."
        return {**meta, "answer": answer, "operators": results, "source": "graph"}

    # ── threshold_lookup ──────────────────────────────────────────────────────
    if query_type == "threshold_lookup":
        min_s = min_stores if min_stores is not None else 1
        max_s = max_stores
        top_ops = index.get("top_operator_entities") or []
        matches = [
            o for o in top_ops
            if o.get("store_count", 0) >= min_s
            and (max_s is None or o.get("store_count", 0) <= max_s)
        ]
        threshold_str = f"{min_s}+ stores" if max_s is None else f"{min_s}–{max_s} stores"
        answer = (
            f"{len(matches)} operator{'s' if len(matches) != 1 else ''} with {threshold_str} "
            f"(from top-{len(top_ops)} operator index; operators below index threshold may not appear)."
        )
        return {
            **meta,
            "answer": answer,
            "threshold": {"min_stores": min_s, "max_stores": max_s},
            "operator_count": len(matches),
            "operators": matches[:limit],
            "note": "Drawn from index top_operator_entities. Small operators below index cut may be missing.",
        }

    # ── staff_lookup ──────────────────────────────────────────────────────────
    if query_type == "staff_lookup":
        if not name and not role:
            return {**meta, "status": "missing_params",
                    "answer": "staff_lookup requires name= (person name) or role= (fbp, otm, otp) parameter."}

        graph_path = core.PROJECT_DIR / (index.get("paths", {}).get("graph") or "")
        if not graph_path.exists():
            return {**meta, "status": "graph_unavailable",
                    "answer": "staff_lookup requires graph.json which is not available."}

        graph_data = json.loads(graph_path.read_text(encoding="utf-8"))
        gnodes = graph_data.get("nodes") or []
        gedges = graph_data.get("edges") or []
        node_by_id = {n["id"]: n for n in gnodes if isinstance(n, dict) and "id" in n}

        name_upper = (name or "").strip().upper()
        role_upper = (role or "").strip().upper()
        state_upper = (state or "").strip().upper()

        person_nodes = []
        for n in gnodes:
            if not isinstance(n, dict) or n.get("type") != "person":
                continue
            attrs = n.get("attributes") or {}
            n_role = (attrs.get("role") or "").upper()
            n_name = (n.get("name") or "").upper()
            if name_upper and name_upper not in n_name:
                continue
            if role_upper and role_upper not in n_role:
                continue
            person_nodes.append(n)

        if not person_nodes:
            criteria = []
            if name:
                criteria.append(f"name='{name}'")
            if role:
                criteria.append(f"role='{role}'")
            return {**meta, "status": "not_found",
                    "answer": f"No staff found matching {', '.join(criteria)}."}

        role_to_edge = {
            "FBP": "fbp_for_store",
            "OTM": "otm_for_store",
            "OTP": "otp_for_store",
            "OPERATOR": "operator_for_store",
        }

        results = []
        for pnode in person_nodes[:limit]:
            attrs = pnode.get("attributes") or {}
            p_role = (attrs.get("role") or "").upper()
            edge_type = role_to_edge.get(p_role, "")

            person_stores = []
            for e in gedges:
                if not isinstance(e, dict):
                    continue
                frm = e.get("from") or e.get("source")
                to = e.get("to") or e.get("target")
                etype = e.get("type") or ""
                if frm != pnode["id"]:
                    continue
                if edge_type and etype != edge_type:
                    continue
                store_node = node_by_id.get(to, {})
                s_attrs = store_node.get("attributes") or {}
                if state_upper and (s_attrs.get("state") or "").upper() != state_upper:
                    continue
                person_stores.append({
                    "nsn": s_attrs.get("nsn"),
                    "city": s_attrs.get("city"),
                    "state": s_attrs.get("state"),
                    "store_id": to,
                })

            states = sorted({s["state"] for s in person_stores if s.get("state")})
            results.append({
                "name": pnode.get("name"),
                "role": attrs.get("role") or p_role,
                "email": attrs.get("email") or "",
                "store_count": len(person_stores),
                "states": states,
                "stores": person_stores[:limit],
            })

        if len(results) == 1:
            p = results[0]
            state_filter_str = f" in {state_upper}" if state_upper else ""
            state_str = ", ".join(p["states"][:5])
            answer = (
                f"{p['name']} ({p['role']}) is assigned to {p['store_count']} "
                f"store{'s' if p['store_count'] != 1 else ''}{state_filter_str}"
                + (f" ({state_str})" if state_str and not state_upper else "") + "."
            )
        else:
            answer = f"{len(results)} staff member{'s' if len(results) != 1 else ''} found."
        return {**meta, "answer": answer, "staff_count": len(results), "staff": results}

    # ── coop_detail ───────────────────────────────────────────────────────────
    if query_type == "coop_detail":
        coop_filter = (coop or "").strip().upper()
        if not coop_filter:
            return {**meta, "status": "missing_coop",
                    "answer": "coop_detail requires a coop= parameter, e.g. coop=Chicago."}

        graph_path = core.PROJECT_DIR / (index.get("paths", {}).get("graph") or "")
        if not graph_path.exists():
            return {**meta, "status": "graph_unavailable",
                    "answer": "coop_detail requires graph.json which is not available."}

        graph_data = json.loads(graph_path.read_text(encoding="utf-8"))
        gnodes = graph_data.get("nodes") or []
        gedges = graph_data.get("edges") or []
        node_by_id = {n["id"]: n for n in gnodes if isinstance(n, dict) and "id" in n}

        # Find matching coop nodes by name (partial match)
        coop_nodes_matched = [
            n for n in gnodes
            if isinstance(n, dict) and n.get("type") == "coop"
            and coop_filter in (n.get("name") or "").upper()
        ]
        if not coop_nodes_matched:
            # List available coops to help the user
            available = sorted(
                n.get("name") for n in gnodes
                if isinstance(n, dict) and n.get("type") == "coop" and n.get("name")
            )
            return {
                **meta,
                "status": "not_found",
                "answer": f"No co-op matching '{coop_filter}' found. Available: {', '.join(available[:20])}.",
                "available_coops": available,
            }

        # Collect store IDs for matched coop(s) via belongs_to_coop edges
        matched_coop_ids = {n["id"] for n in coop_nodes_matched}
        coop_store_ids: set[str] = set()
        for e in gedges:
            if not isinstance(e, dict):
                continue
            frm = e.get("from") or e.get("source")
            to = e.get("to") or e.get("target")
            if e.get("type") == "belongs_to_coop" and to in matched_coop_ids:
                coop_store_ids.add(frm)

        # Resolve operators for those stores
        operators: dict[str, dict] = {}
        for e in gedges:
            if not isinstance(e, dict):
                continue
            frm = e.get("from") or e.get("source")
            to = e.get("to") or e.get("target")
            if e.get("type") != "operated_by_entity" or frm not in coop_store_ids:
                continue
            op_node = node_by_id.get(to, {})
            op_name = op_node.get("name") or to
            operators.setdefault(to, {"name": op_name, "store_count": 0})
            operators[to]["store_count"] += 1

        op_list = sorted(operators.values(), key=lambda x: -x["store_count"])
        coop_label = coop_nodes_matched[0].get("name") or coop_filter.title()
        answer = (
            f"{coop_label} co-op: {len(coop_store_ids)} store{'s' if len(coop_store_ids) != 1 else ''}, "
            f"{len(op_list)} operator{'s' if len(op_list) != 1 else ''}."
        )
        return {
            **meta,
            "answer": answer,
            "coop": coop_label,
            "store_count": len(coop_store_ids),
            "operator_count": len(op_list),
            "operators": op_list[:limit],
        }

    # ── relationship_coverage ─────────────────────────────────────────────────
    if query_type == "relationship_coverage":
        baseline = core.load_baseline()
        graph_name_lower = (index.get("name") or "").lower()
        # Match on graph name fragments (e.g. "mcdonald") against current_company
        name_fragments = [f for f in graph_name_lower.replace("'", "").split() if len(f) > 3]
        contacts = []
        for contact in baseline:
            company = (contact.get("current_company") or "").lower().replace("'", "")
            if any(frag in company for frag in name_fragments):
                contacts.append({
                    "id": contact.get("id"),
                    "name": contact.get("name"),
                    "role": contact.get("current_role"),
                    "company": contact.get("current_company"),
                    "signal_class": contact.get("signal_class"),
                    "rc_tier": contact.get("rc_tier"),
                    "last_touch": contact.get("last_touch"),
                    "circles": contact.get("circles") or [],
                })
        rc_contacts = [c for c in contacts if c["signal_class"] == "RC"]
        answer = (
            f"{len(contacts)} baseline contacts at {index.get('name')} found: "
            f"{len(rc_contacts)} are RC (relationship capital) contacts."
        )
        return {
            **meta,
            "answer": answer,
            "total_contacts": len(contacts),
            "rc_contact_count": len(rc_contacts),
            "contacts": contacts[:limit],
        }

    # ── summary (default) ─────────────────────────────────────────────────────
    answer_template = (index.get("retrieval_answer_templates") or {}).get("operator_distribution")
    answer = None
    if answer_template:
        answer = answer_template.format(
            operator_entities_with_stores=counts.get("operator_entities_with_stores", 0),
            stores_with_operator_entity=counts.get("stores_with_operator_entity", 0),
            enterprise_25_plus=tiers.get("enterprise_25_plus", 0),
            mid_tier_5_to_24=tiers.get("mid_tier_5_to_24", 0),
            single_digit_1_to_4=tiers.get("single_digit_1_to_4", 0),
        )
    return {
        **meta,
        "source_sha256": index.get("source_sha256"),
        "answer": answer,
        "counts": counts,
        "activation": index.get("activation"),
        "paths": index.get("paths"),
        "top_operator_entities": (index.get("top_operator_entities") or [])[:limit],
        "top_states": (index.get("state_distribution") or [])[:limit],
        "top_field_offices": (index.get("field_office_distribution") or [])[:limit],
        "notes": index.get("notes") or [],
    }


def _load_ecosystem_graph() -> dict:
    path = core.ECOSYSTEM_INTELLIGENCE_PATH
    if not path.exists():
        return {
            "contract": "rb_ecosystem_intelligence_v1",
            "entities": [],
            "relationships": [],
            "signals": [],
            "assessments": [],
            "sources": [],
            "user_relevance": [],
            "strategic_recommendations": [],
            "status": "missing",
        }
    try:
        graph = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "contract": "rb_ecosystem_intelligence_v1",
            "entities": [],
            "relationships": [],
            "signals": [],
            "assessments": [],
            "sources": [],
            "user_relevance": [],
            "strategic_recommendations": [],
            "status": "unreadable",
            "reason": str(exc),
        }
    graph.setdefault("entities", [])
    graph.setdefault("relationships", [])
    graph.setdefault("signals", [])
    graph.setdefault("assessments", [])
    graph.setdefault("sources", [])
    graph.setdefault("user_relevance", [])
    graph.setdefault("strategic_recommendations", [])
    graph.setdefault("status", "found")
    return graph


def _ecosystem_by_id(graph: dict) -> dict[str, dict]:
    return {
        str(entity.get("id")): entity
        for entity in graph.get("entities", [])
        if entity.get("id")
    }


def _ecosystem_entity_metrics(entity: dict) -> dict:
    attrs = entity.get("attributes") or {}
    latest_year = attrs.get("technomic_latest_year")
    latest_detail = None
    detailed = attrs.get("technomic_detailed_history") or {}
    history = attrs.get("technomic_history") or {}
    if latest_year is not None:
        key = str(int(latest_year)) if isinstance(latest_year, float) else str(latest_year)
        latest_detail = detailed.get(key) or history.get(key)
    return {
        "id": entity.get("id"),
        "name": entity.get("name"),
        "entity_type": entity.get("entity_type"),
        "subtype": entity.get("subtype"),
        "status": entity.get("status"),
        "domains": entity.get("domains") or [],
        "sources": entity.get("sources") or [],
        "confidence": entity.get("confidence"),
        "metrics": {
            "rank": attrs.get("rank"),
            "segment": attrs.get("segment"),
            "subsegment": attrs.get("subsegment"),
            "menu_type": attrs.get("menu_type"),
            "technomic_ignite_id": attrs.get("technomic_ignite_id"),
            "technomic_latest_year": latest_year,
            "system_sales": attrs.get("system_sales"),
            "unit_count": attrs.get("unit_count"),
            "auv": attrs.get("auv"),
            "sales_delta": attrs.get("sales_delta"),
            "unit_delta": attrs.get("unit_delta"),
            "latest_detail": latest_detail,
        },
        "updated_at": entity.get("updated_at"),
    }


def _ecosystem_relationship_projection(rel: dict, by_id: dict[str, dict]) -> dict:
    brand = by_id.get(rel.get("from_entity_id"), {})
    vendor = by_id.get(rel.get("to_entity_id"), {})
    attrs = brand.get("attributes") or {}
    deploy = rel.get("deployment") or {}
    return {
        "relationship_id": rel.get("id"),
        "brand": brand.get("name"),
        "brand_id": rel.get("from_entity_id"),
        "segment": attrs.get("segment"),
        "unit_count": attrs.get("unit_count"),
        "sales": attrs.get("system_sales"),
        "auv": attrs.get("auv"),
        "vendor": vendor.get("name"),
        "vendor_id": rel.get("to_entity_id"),
        "vendor_category": rel.get("category"),
        "vendor_role": rel.get("vendor_role", "unknown"),
        "product": rel.get("product"),
        "deployment_status": deploy.get("stage"),
        "deployment_scope": deploy.get("scope"),
        "geography": rel.get("geography") or deploy.get("geography"),
        "channel": rel.get("channel") or deploy.get("channel"),
        "service_role": rel.get("service_role") or deploy.get("service_role"),
        "deployment_claim_type": rel.get("deployment_claim_type") or deploy.get("deployment_claim_type"),
        "customer_operator": rel.get("customer_operator") or deploy.get("customer_operator"),
        "scope_unit_count": rel.get("scope_unit_count") or deploy.get("scope_unit_count"),
        "deployed_units": deploy.get("deployed_units"),
        "penetration_pct": deploy.get("penetration_pct"),
        "risk": rel.get("risk"),
        "evidence_posture": rel.get("evidence_posture", "unknown"),
        "interpretation_scope": rel.get("interpretation_scope", ""),
        "confidence": (rel.get("confidence") or {}).get("level"),
        "sources": rel.get("sources") or [],
        "strategic_implication": rel.get("strategic_note"),
    }


def _ecosystem_summary(graph: dict) -> dict:
    entity_types: dict[str, int] = {}
    relationship_categories: dict[str, int] = {}
    relationship_postures: dict[str, int] = {}
    for entity in graph.get("entities", []):
        key = entity.get("entity_type") or "unknown"
        entity_types[key] = entity_types.get(key, 0) + 1
    for rel in graph.get("relationships", []):
        category = rel.get("category") or "unknown"
        posture = rel.get("evidence_posture") or "unknown"
        relationship_categories[category] = relationship_categories.get(category, 0) + 1
        relationship_postures[posture] = relationship_postures.get(posture, 0) + 1
    return {
        "entities": len(graph.get("entities", [])),
        "relationships": len(graph.get("relationships", [])),
        "signals": len(graph.get("signals", [])),
        "sources": len(graph.get("sources", [])),
        "entity_types": entity_types,
        "relationship_categories": relationship_categories,
        "relationship_evidence_posture": relationship_postures,
    }


@app.get("/graphs/ecosystem", tags=["compute"], operation_id="getEcosystemGraphQuery")
def get_ecosystem_graph_query(
    query_type: Literal["summary", "brand", "vendor", "brands", "competitive", "signals"] = Query(
        "summary",
        description=(
            "summary — ecosystem overview. "
            "brand — specific restaurant brand lookup (query= required). "
            "vendor — customer brands for a technology vendor (query= required). "
            "brands — filtered brand list (segment=, min_sales=, max_rank=). "
            "competitive — all vendors in a technology category side-by-side (category= required, e.g. pos, kds, loyalty). "
            "signals — recent intelligence signals across the graph (query= optional entity filter)."
        ),
    ),
    query: Optional[str] = Query(None, description="Brand/vendor name or search term."),
    category: Optional[str] = Query(None, description="Optional vendor category filter, e.g. pos."),
    segment: Optional[str] = Query(None, description="Optional brand segment filter, e.g. LSR."),
    subsegment: Optional[str] = Query(None, description="Optional brand subsegment filter."),
    min_sales: Optional[float] = Query(None, description="Minimum system sales for brands query."),
    max_rank: Optional[float] = Query(None, description="Maximum Technomic rank for brands query."),
    limit: int = Query(20, ge=1, le=100),
    x_api_key: Optional[str] = Header(None),
):
    """Query the macro ecosystem intelligence graph for restaurant brands and vendors.

    This is the Custom GPT-safe retrieval surface for the mutable macro graph
    built from Technomic brand data, vendor evidence, passive LinkedIn signals,
    press/trade evidence, and verification posture.
    """
    _auth(x_api_key)
    graph = _load_ecosystem_graph()
    if graph.get("status") not in {None, "found"}:
        return {"status": graph.get("status"), "reason": graph.get("reason")}
    by_id = _ecosystem_by_id(graph)
    if query_type == "summary":
        return {"status": "found", "query_type": query_type, "summary": _ecosystem_summary(graph)}

    q = (query or "").strip().lower()
    if query_type in {"brand", "vendor"} and not q:
        return {"status": "missing_query", "query_type": query_type, "message": "query is required for this query_type"}

    if query_type == "brand":
        matches = [
            entity for entity in graph.get("entities", [])
            if q in str(entity.get("name") or "").lower() or q in str(entity.get("id") or "").lower()
        ]
        exact = [m for m in matches if str(m.get("name") or "").lower() == q]
        if len(matches) > 1 and not exact:
            return {
                "status": "ambiguous",
                "query_type": query_type,
                "query": query,
                "candidate_count": len(matches),
                "candidates": [_ecosystem_entity_metrics(m) for m in matches[:limit]],
            }
        if not matches:
            return {"status": "not_found", "query_type": query_type, "query": query}
        entity = exact[0] if exact else matches[0]
        relationships = [
            _ecosystem_relationship_projection(rel, by_id)
            for rel in graph.get("relationships", [])
            if rel.get("from_entity_id") == entity.get("id")
            and (not category or rel.get("category") == category)
        ]
        signals = [
            signal for signal in graph.get("signals", [])
            if entity.get("id") in (signal.get("entities") or [])
        ][:limit]
        return {
            "status": "found",
            "query_type": query_type,
            "query": query,
            "entity": _ecosystem_entity_metrics(entity),
            "vendor_relationship_count": len(relationships),
            "vendor_relationships": relationships[:limit],
            "signals": signals,
            "summary": _ecosystem_summary(graph),
        }

    if query_type == "vendor":
        rows = []
        for rel in graph.get("relationships", []):
            vendor = by_id.get(rel.get("to_entity_id"), {})
            vendor_haystack = f"{vendor.get('name') or ''} {rel.get('to_entity_id') or ''}".lower()
            if q not in vendor_haystack:
                continue
            if category and rel.get("category") != category:
                continue
            rows.append(_ecosystem_relationship_projection(rel, by_id))
        return {
            "status": "found",
            "query_type": query_type,
            "query": query,
            "category": category,
            "count": len(rows),
            "items": rows[:limit],
            "summary": _ecosystem_summary(graph),
        }

    # ── competitive ──────────────────────────────────────────────────────────
    if query_type == "competitive":
        if not category:
            # List available categories when none provided
            all_cats: dict[str, int] = {}
            for rel in graph.get("relationships", []):
                c = rel.get("category") or "unknown"
                all_cats[c] = all_cats.get(c, 0) + 1
            available = sorted(all_cats, key=lambda k: -all_cats[k])
            return {
                "status": "missing_category",
                "query_type": query_type,
                "answer": f"competitive requires category= parameter. Available: {', '.join(available)}.",
                "available_categories": available,
            }

        cat_lower = category.lower()
        # Build vendor → [brand relationships] map for the requested category
        vendor_map: dict[str, dict] = {}
        for rel in graph.get("relationships", []):
            if (rel.get("category") or "").lower() != cat_lower:
                continue
            vendor_id = rel.get("to_entity_id") or ""
            vendor_entity = by_id.get(vendor_id, {})
            vendor_name = vendor_entity.get("name") or vendor_id
            brand_id = rel.get("from_entity_id") or ""
            brand_entity = by_id.get(brand_id, {})
            brand_name = brand_entity.get("name") or brand_id
            brand_attrs = brand_entity.get("attributes") or {}
            if vendor_id not in vendor_map:
                vendor_map[vendor_id] = {
                    "vendor_id": vendor_id,
                    "vendor": vendor_name,
                    "category": category,
                    "customer_count": 0,
                    "top_customers": [],
                    "evidence_postures": [],
                }
            vendor_map[vendor_id]["customer_count"] += 1
            vendor_map[vendor_id]["top_customers"].append({
                "brand": brand_name,
                "segment": brand_attrs.get("segment"),
                "unit_count": brand_attrs.get("unit_count"),
                "deployment_status": (rel.get("deployment") or {}).get("stage"),
                "evidence_posture": rel.get("evidence_posture"),
            })
            posture = rel.get("evidence_posture") or "unknown"
            if posture not in vendor_map[vendor_id]["evidence_postures"]:
                vendor_map[vendor_id]["evidence_postures"].append(posture)

        vendors_ranked = sorted(vendor_map.values(), key=lambda v: -v["customer_count"])
        for v in vendors_ranked:
            v["top_customers"] = sorted(
                v["top_customers"],
                key=lambda b: (b.get("unit_count") or 0),
                reverse=True,
            )[:limit]

        if vendors_ranked:
            leader = vendors_ranked[0]
            answer = (
                f"{len(vendors_ranked)} vendor{'s' if len(vendors_ranked) != 1 else ''} "
                f"active in '{category}'. "
                f"Leader: {leader['vendor']} ({leader['customer_count']} customer brand{'s' if leader['customer_count'] != 1 else ''})."
            )
        else:
            answer = f"No vendor relationships found for category '{category}'."

        return {
            "status": "found",
            "query_type": query_type,
            "category": category,
            "vendor_count": len(vendors_ranked),
            "answer": answer,
            "vendors": vendors_ranked[:limit],
        }

    # ── signals ───────────────────────────────────────────────────────────────
    if query_type == "signals":
        all_signals = list(graph.get("signals") or [])
        if q:
            # Filter signals that mention the queried entity (by name or ID)
            filtered = []
            for sig in all_signals:
                sig_text = " ".join([
                    sig.get("summary") or "",
                    " ".join(sig.get("entities") or []),
                ]).lower()
                if q in sig_text:
                    filtered.append(sig)
            all_signals = filtered

        # Sort newest first
        all_signals.sort(
            key=lambda s: s.get("event_at") or s.get("captured_at") or "",
            reverse=True,
        )
        count = len(all_signals)
        if count == 0:
            answer = f"No signals found{' for ' + query if query else ''}."
        else:
            latest = all_signals[0]
            answer = (
                f"{count} signal{'s' if count != 1 else ''} found"
                f"{' for ' + query if query else ''}. "
                f"Latest ({latest.get('event_at') or 'unknown date'}): "
                f"{(latest.get('summary') or '')[:120]}."
            )
        return {
            "status": "found",
            "query_type": query_type,
            "query": query,
            "count": count,
            "answer": answer,
            "signals": all_signals[:limit],
        }

    # ── brands (default) ─────────────────────────────────────────────────────
    results = []
    for entity in graph.get("entities", []):
        if entity.get("entity_type") != "brand":
            continue
        attrs = entity.get("attributes") or {}
        if q and q not in str(entity.get("name") or "").lower() and q not in str(entity.get("id") or "").lower():
            continue
        if segment and segment.lower() not in str(attrs.get("segment") or "").lower():
            continue
        if subsegment and subsegment.lower() not in str(attrs.get("subsegment") or "").lower():
            continue
        if min_sales is not None and (attrs.get("system_sales") is None or attrs.get("system_sales") < min_sales):
            continue
        if max_rank is not None and (attrs.get("rank") is None or attrs.get("rank") > max_rank):
            continue
        results.append(_ecosystem_entity_metrics(entity))
    results.sort(key=lambda item: (item.get("metrics") or {}).get("rank") or 999999)
    return {
        "status": "found",
        "query_type": query_type,
        "query": query,
        "count": len(results),
        "brands": results[:limit],
        "summary": _ecosystem_summary(graph),
    }


@app.get("/graphs/ecosystem/watchlist", tags=["compute"], operation_id="getWatchList")
def get_watch_list(
    x_api_key: Optional[str] = Header(None),
):
    """Return the current ecosystem watch list with entity metrics and last signal date.

    Tier definitions:
    - tier_1: User-curated, interrupt-eligible. Any signal triggers immediate consideration.
    - tier_2: CoS-suggested based on relationship coverage or active thread linkage.
    - tier_3: Ambient — in broader ecosystem scan, no special handling.
    """
    _auth(x_api_key)
    graph = _load_ecosystem_graph()
    if graph.get("status") not in {None, "found"}:
        return {"status": graph.get("status"), "reason": graph.get("reason")}
    by_id = _ecosystem_by_id(graph)
    wl = graph.get("watch_list") or []
    items = []
    for entry in wl:
        entity = by_id.get(entry["entity_id"], {})
        items.append({
            **entry,
            "entity_name": entity.get("name"),
            "entity_type": entity.get("entity_type"),
            "metrics": (entity.get("attributes") or {}).get("rank") and {
                "rank": (entity.get("attributes") or {}).get("rank"),
                "segment": (entity.get("attributes") or {}).get("segment"),
                "unit_count": (entity.get("attributes") or {}).get("unit_count"),
            } or None,
        })
    return {"count": len(items), "watch_list": items}


class WatchListUpdateBody(BaseModel):
    action: Literal["add", "remove", "set_priority"]
    entity_id: str
    priority: Optional[Literal["tier_1", "tier_2", "tier_3"]] = None
    reason: Optional[str] = None
    interrupt_eligible: Optional[bool] = None
    confirm: bool = False


@app.post("/graphs/ecosystem/watchlist", tags=["compute"], operation_id="updateWatchList")
def update_watch_list(
    body: WatchListUpdateBody,
    x_api_key: Optional[str] = Header(None),
):
    """Add, remove, or update priority for an entity on the ecosystem watch list.

    confirm=false previews the mutation. confirm=true writes it.
    """
    _auth(x_api_key)
    import subprocess as _sp
    import sys as _sys
    eco_script = str(core.SYSTEM_DIR / "scripts" / "ecosystem_intelligence.py")

    if body.action == "add":
        cmd = [
            _sys.executable, eco_script, "watch-list", "add", body.entity_id,
            "--priority", body.priority or "tier_1",
        ]
        if body.reason:
            cmd += ["--reason", body.reason]
    elif body.action == "remove":
        cmd = [_sys.executable, eco_script, "watch-list", "remove", body.entity_id]
    elif body.action == "set_priority":
        if not body.priority:
            raise HTTPException(400, "priority is required for set_priority action")
        cmd = [
            _sys.executable, eco_script, "watch-list", "set-priority",
            body.entity_id, "--priority", body.priority,
        ]
    else:
        raise HTTPException(400, f"Unknown action: {body.action}")

    if not body.confirm:
        return {
            "proposed": True,
            "action": body.action,
            "entity_id": body.entity_id,
            "priority": body.priority,
            "message": "Set confirm=true to apply this mutation.",
        }

    result = _sp.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(500, result.stderr or result.stdout)
    try:
        return json.loads(result.stdout)
    except Exception:  # noqa: BLE001
        return {"status": "ok", "output": result.stdout.strip()}


_INTERRUPT_TTL_HOURS = 48


def _expire_stale_interrupts(queue_path: Path) -> int:
    """Auto-expire interrupt queue items older than _INTERRUPT_TTL_HOURS.

    Expired items get acknowledged=True, acknowledged_at=<now>, acknowledgment_reason=ttl_expired.
    The audit trail (the record itself) is preserved — only the acknowledged flag changes.
    Returns the count of items expired in this call.

    RB 9.18: called lazily on every getEcosystemInterrupts read so stale items never
    silently accumulate on the operator surface.
    """
    if not queue_path.exists():
        return 0
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")
    lines = queue_path.read_text(encoding="utf-8").splitlines()
    updated = []
    expired_count = 0
    changed = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except Exception:  # noqa: BLE001
            updated.append(line)
            continue
        if not record.get("acknowledged"):
            detected_raw = record.get("detected_at") or record.get("queued_at") or ""
            try:
                detected_at = datetime.fromisoformat(detected_raw.replace("Z", "+00:00"))
                if detected_at.tzinfo is None:
                    detected_at = detected_at.replace(tzinfo=timezone.utc)
                age_hours = (now - detected_at.astimezone(timezone.utc)).total_seconds() / 3600
            except (ValueError, AttributeError):
                age_hours = 0.0
            if age_hours >= _INTERRUPT_TTL_HOURS:
                record["acknowledged"] = True
                record["acknowledged_at"] = now_iso
                record["acknowledgment_reason"] = "ttl_expired"
                expired_count += 1
                changed = True
        updated.append(json.dumps(record, separators=(",", ":")))
    if changed:
        queue_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    return expired_count


@app.get("/graphs/ecosystem/interrupts", tags=["compute"], operation_id="getEcosystemInterrupts")
def get_ecosystem_interrupts(
    x_api_key: Optional[str] = Header(None),
):
    """Return pending interrupt queue items not yet acknowledged.

    Interrupt items are generated by check-interrupt-queue for signals that are:
    - Tied to a tier_1 watch list entity
    - Of a qualifying signal class (leadership_change, rfp_cycle_signal, extreme_pain, vendor_displacement)
    - High confidence
    - Within 48 hours of detection

    These must be rendered before the daily brief when non-empty.

    RB 9.18: items older than 48h are auto-expired on read (acknowledged=true,
    acknowledgment_reason=ttl_expired). Expired records are preserved for audit.
    """
    _auth(x_api_key)
    queue_path = core.SYSTEM_DIR / "inbox" / "ecosystem" / "interrupt_queue.jsonl"
    if not queue_path.exists():
        return {"count": 0, "items": [], "ttl_expired_this_call": 0}
    # RB 9.18: expire stale items before reading live queue.
    ttl_expired = _expire_stale_interrupts(queue_path)
    items = []
    for line in queue_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            if not record.get("acknowledged"):
                items.append(record)
        except Exception:  # noqa: BLE001
            continue
    return {"count": len(items), "items": items, "ttl_expired_this_call": ttl_expired}


@app.post("/graphs/ecosystem/interrupts/{interrupt_id}/acknowledge", tags=["compute"], operation_id="acknowledgeEcosystemInterrupt")
def acknowledge_ecosystem_interrupt(
    interrupt_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """Mark an ecosystem interrupt queue item as acknowledged.

    Acknowledged items are excluded from subsequent getEcosystemInterrupts responses.
    The record is preserved in the queue file for audit purposes with acknowledged=true
    and acknowledged_at timestamp.
    """
    _auth(x_api_key)
    queue_path = core.SYSTEM_DIR / "inbox" / "ecosystem" / "interrupt_queue.jsonl"
    if not queue_path.exists():
        raise HTTPException(404, f"Interrupt queue not found; no item with id={interrupt_id!r}")
    lines = queue_path.read_text(encoding="utf-8").splitlines()
    updated = []
    found = False
    now = __import__("datetime").datetime.utcnow().isoformat(timespec="seconds") + "Z"
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except Exception:  # noqa: BLE001
            updated.append(line)
            continue
        if record.get("interrupt_id") == interrupt_id:
            record["acknowledged"] = True
            record["acknowledged_at"] = now
            found = True
        updated.append(json.dumps(record, separators=(",", ":")))
    if not found:
        raise HTTPException(404, f"No interrupt found with id={interrupt_id!r}")
    queue_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    return {"acknowledged": True, "interrupt_id": interrupt_id, "acknowledged_at": now}


class PassiveIntelligenceBody(BaseModel):
    content: str
    source_type: Optional[str] = None
    platform: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    source_name: Optional[str] = None


class PromoteClaimBody(BaseModel):
    relationship_id: str
    new_posture: str  # provisional | partially_substantiated | substantiated
    source_title: str
    source_type: Optional[str] = None  # primary_operator_statement | credible_trade_reporting | etc.
    source_url: Optional[str] = None
    confirm: bool = False


# ---------------------------------------------------------------------------
# RB 9.18 — Passive intelligence queue persistence helpers
# ---------------------------------------------------------------------------
_CORROBORATION_QUEUE_CACHE_PATH = core.SYSTEM_DIR / ".cache" / "corroboration_search_queue.json"


def _load_corroboration_queue() -> dict:
    if not _CORROBORATION_QUEUE_CACHE_PATH.exists():
        return {"version": 1, "items": [], "generated_at": None}
    try:
        return json.loads(_CORROBORATION_QUEUE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"version": 1, "items": [], "generated_at": None}


def _append_to_corroboration_queue(new_items: list, evaluation_title: Optional[str]) -> None:
    """Persist corroboration search items from an evaluation, deduplicating by claim_id.

    Resolved/dismissed items are never overwritten by a fresh open entry.
    """
    if not new_items:
        return
    from datetime import datetime, timezone
    payload = _load_corroboration_queue()
    existing_by_id: dict = {
        item["claim_id"]: item
        for item in (payload.get("items") or [])
        if item.get("claim_id")
    }
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for item in new_items:
        cid = item.get("claim_id")
        if not cid:
            continue
        existing = existing_by_id.get(cid, {})
        if existing.get("status") in {"resolved", "dismissed"}:
            continue  # Never overwrite a closed item.
        existing_by_id[cid] = {
            **item,
            "evaluation_title": evaluation_title,
            "added_at": existing.get("added_at") or now,
            "updated_at": now,
            "status": existing.get("status") or "open",
        }
    payload["items"] = list(existing_by_id.values())
    payload["generated_at"] = now
    _CORROBORATION_QUEUE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CORROBORATION_QUEUE_CACHE_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


@app.post("/intelligence/passive/evaluate", tags=["compute"], operation_id="evaluatePassiveIntelligence")
def evaluate_passive_intelligence(
    body: PassiveIntelligenceBody,
    x_api_key: Optional[str] = Header(None),
):
    """Evaluate uploaded/passive content before RB treats it as intelligence.

    This endpoint extracts factual claims, classifies source incentives and authority,
    checks existing RB ecosystem evidence for local corroboration/contradiction, assigns
    claim confidence/status, and returns graph mutation eligibility. It does not mutate
    graph truth layers; verified/corroborated claims can be promoted through existing
    graph mutation paths, while weak signals stay explicitly lower-confidence.

    RB 9.18: corroboration_search_queue items are automatically persisted to
    system/.cache/corroboration_search_queue.json and reviewable via
    GET /intelligence/passive/corroboration-queue.
    """
    _auth(x_api_key)
    graph = _load_ecosystem_graph()
    if graph.get("status") not in {None, "found"}:
        graph = {"sources": [], "signals": [], "relationships": []}
    result = passive_intelligence.evaluate_passive_intelligence(
        body.content,
        {
            "source_type": body.source_type,
            "platform": body.platform,
            "title": body.title,
            "url": body.url,
            "source_name": body.source_name,
        },
        graph=graph,
    )
    # RB 9.18: persist corroboration queue items — never let this block the response.
    try:
        corroboration_items = (result.get("summary") or {}).get("corroboration_search_queue") or []
        _append_to_corroboration_queue(corroboration_items, body.title or body.source_name)
    except Exception:  # noqa: BLE001
        pass
    return result


@app.get("/intelligence/passive/verification-queue", tags=["compute"], operation_id="getVendorVerificationQueue")
def get_vendor_verification_queue(
    status: Optional[str] = Query(None, description="Filter by status. Default: open. Pass 'all' for every item."),
    vendor: Optional[str] = Query(None, description="Filter by vendor name (case-insensitive contains)."),
    x_api_key: Optional[str] = Header(None),
):
    """List vendor-claimed relationship records awaiting independent verification.

    Items come from LinkedIn vendor posts and other vendor-claimed sources that entered
    the ecosystem graph as provisional/vendor_claimed but lack primary or trade corroboration.
    Use this queue to prioritise research. Promote posture only after attaching a real source
    via POST /intelligence/passive/promote-claim.

    Promotion invariants (enforced by promote-claim):
    - A real source title and type are required — search plans alone do not raise posture.
    - Vendor claims require primary operator statements or credible trade reporting.
    - Conflicting records must be resolved before promotion.
    """
    _auth(x_api_key)
    payload = _load_verification_queue()
    items = payload.get("items") or []
    effective_status = (status or "open").lower()
    if effective_status != "all":
        items = [i for i in items if (i.get("status") or "open").lower() == effective_status]
    if vendor:
        vendor_lower = vendor.lower()
        items = [i for i in items if vendor_lower in (i.get("vendor") or "").lower()]
    return {
        "contract": "rb_vendor_verification_queue_v1",
        "generated_at": payload.get("updated_at") or payload.get("generated_at"),
        "total_matching": len(items),
        "filter_status": effective_status,
        "filter_vendor": vendor,
        "promotion_path": "POST /intelligence/passive/promote-claim (confirm=false to preview, confirm=true to write)",
        "items": items,
    }


@app.get("/intelligence/passive/corroboration-queue", tags=["compute"], operation_id="getCorroborationQueue")
def get_corroboration_queue(
    status: Optional[str] = Query(None, description="Filter by status. Default: open. Pass 'all' for every item."),
    x_api_key: Optional[str] = Header(None),
):
    """List passive intelligence claims awaiting external corroboration.

    Items are generated by POST /intelligence/passive/evaluate whenever a claim's
    corroboration_search_plan.status is 'needed'. They persist in
    system/.cache/corroboration_search_queue.json and are deduplicated by claim_id.

    Each item includes claim text, minimum sources required, preferred source types,
    and suggested search queries. Once you have located a corroborating source, call
    POST /intelligence/passive/promote-claim (confirm=true) to advance posture.

    CRITICAL: Search queries here are starting points only. Do not promote posture
    without attaching a real source. Vendor claims need independent evidence.
    """
    _auth(x_api_key)
    payload = _load_corroboration_queue()
    items = payload.get("items") or []
    effective_status = (status or "open").lower()
    if effective_status != "all":
        items = [i for i in items if (i.get("status") or "open").lower() == effective_status]
    return {
        "contract": "rb_corroboration_search_queue_v1",
        "generated_at": payload.get("generated_at"),
        "total_matching": len(items),
        "filter_status": effective_status,
        "promotion_path": "POST /intelligence/passive/promote-claim (confirm=false to preview, confirm=true to write)",
        "note": "Search queries are starting points only. Do not promote posture without attaching a real source.",
        "items": items,
    }


@app.post("/intelligence/passive/promote-claim", tags=["write"], operation_id="promoteClaimPosture")
def post_promote_claim(
    body: PromoteClaimBody,
    x_api_key: Optional[str] = Header(None),
):
    """Promote a provisional vendor/ecosystem claim to a higher evidence posture.

    INVARIANTS — strictly enforced:
    - source_title is required. Promotion without attached evidence is rejected.
    - new_posture must be a forward step: provisional → partially_substantiated → substantiated.
    - Search plans and corroboration queue entries are NOT evidence. Attach a real source.
    - Vendor claims require primary_operator_statement or credible_trade_reporting.
    - Conflicting/disputed records must be resolved before promotion.
    - confirm=false (default) returns a dry-run preview with no writes.
    - confirm=true executes and audit-logs the promotion via P-009.
    """
    _auth(x_api_key)
    import subprocess as _sp
    import sys as _sys
    eco_script = str(core.SYSTEM_DIR / "scripts" / "ecosystem_intelligence.py")
    cmd = [
        _sys.executable, eco_script, "promote-confidence",
        "--relationship-id", body.relationship_id,
        "--new-posture", body.new_posture,
        "--source-title", body.source_title,
    ]
    if body.source_type:
        cmd += ["--source-type", body.source_type]
    if body.source_url:
        cmd += ["--source-url", body.source_url]
    if not body.confirm:
        cmd.append("--dry-run")

    proc = _sp.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raw = proc.stdout.strip() or proc.stderr.strip()
        try:
            detail = json.loads(raw).get("error", raw)
        except Exception:  # noqa: BLE001
            detail = raw or "promote-confidence failed"
        raise HTTPException(400, detail)
    try:
        payload = json.loads(proc.stdout)
    except Exception:  # noqa: BLE001
        payload = {"raw": proc.stdout.strip()}
    return {
        "confirmed": body.confirm,
        "relationship_id": body.relationship_id,
        "new_posture": body.new_posture,
        "source_title": body.source_title,
        **payload,
    }


@app.get("/graphs/micro/index", tags=["compute"], operation_id="getMicroGraphIndex")
def get_micro_graph_index(
    x_api_key: Optional[str] = Header(None),
):
    """Return the presence index of available RB micro ecosystem graphs.

    RB-DEFECT-005: Use this endpoint before answering any company-specific factual
    question (counts, structure, personnel, deployments, technology, relationships)
    to determine whether a micro graph exists and should be consulted first.

    Trust hierarchy enforced by this endpoint:
      User Artifact > RB Micro Graph > RB Macro Graph > Verified External Source > General Model Knowledge

    If the returned graph list contains a matching entry for the named company,
    call getMicroGraphSummary with the appropriate query before answering from
    general knowledge.

    Every micro-graph-backed answer must cite: source artifact name, freshness_date,
    and confidence label (verified / inferred / estimated).
    """
    _auth(x_api_key)
    index_path = core.SYSTEM_DIR / "graphs" / "micro" / "index.json"
    if not index_path.exists():
        return {
            "contract": "rb_micro_graph_presence_index_v1",
            "graphs": [],
            "trust_hierarchy": [
                "user_artifact",
                "rb_micro_graph",
                "rb_macro_graph",
                "verified_external_source",
                "general_model_knowledge",
            ],
            "note": "No micro graph presence index found. Falling back to general knowledge for company-specific questions.",
            "generated_at": None,
            "version": 0,
        }
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "graphs": []}
    return data


# ---------------------------------------------------------------------------
# RB 9.25A — Intelligence Artifact Registry (DEFECT-014) +
#             Multi-Type Intelligence Triage (DEFECT-015)
# ---------------------------------------------------------------------------

_ARTIFACTS_DIR = core.SYSTEM_DIR / "artifacts"
_ARTIFACT_REGISTRY_PATH = _ARTIFACTS_DIR / "registry.json"


def _load_artifact_registry() -> dict:
    """Load the general intelligence artifact registry."""
    if _ARTIFACT_REGISTRY_PATH.exists():
        try:
            return json.loads(_ARTIFACT_REGISTRY_PATH.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return {"contract": "rb_intelligence_artifact_registry_v1",
                    "artifacts": [], "error": str(exc)}
    # Fall back to micro graph registry
    return intelligence_triage._load_artifact_registry()


def _get_artifact_by_id(artifact_id: str, registry: dict) -> dict | None:
    for art in registry.get("artifacts") or []:
        if art.get("artifact_id") == artifact_id:
            return art
    return None


@app.get("/artifacts", tags=["compute"], operation_id="listArtifacts")
def get_artifact_list(
    artifact_type: Optional[str] = Query(None, description="Filter by type: micro_graph, account_dossier, etc."),
    status: Optional[str] = Query(None, description="Filter by status: active, stub, building."),
    x_api_key: Optional[str] = Header(None),
):
    """List registered entries in the micro-graph/account-dossier artifact
    registry ONLY -- micro graphs (McDonald's, stub entries for PAR, Toast,
    Global Payments, Foods Connected), account dossiers, and competitive
    intelligence datasets.

    RB-2026-08-28: this registry is NOT a comprehensive inventory of RB's
    intelligence, despite this endpoint's name -- Blue Sheets, Master Account
    Plans, Account Research background briefs, and account_intelligence docs
    each live in their own separate store and are NOT included here. A real
    incident: asked "what artifacts do you have on file," the model answered
    from this endpoint alone ("Total Artifacts: 5"), which is only ever true
    for this one narrow registry -- a wildly incomplete answer to a general
    question. For "what do you have on file / what artifacts exist / what do
    we have on X" in general, use queryIntelligenceIndex instead -- that is
    the actually comprehensive index across every subsystem. Only use this
    endpoint when specifically asked about micro graphs or account dossiers,
    or after queryIntelligenceIndex has already pointed here.

    Use getMicroGraphSummary for McDonald's operational topology questions.

    DEFECT-014 fix: provides a general artifact catalog so the Custom GPT
    does not have to know each artifact's specific retrieval endpoint.
    """
    _auth(x_api_key)
    registry = _load_artifact_registry()
    artifacts = registry.get("artifacts") or []
    if artifact_type:
        artifacts = [a for a in artifacts if a.get("artifact_type") == artifact_type]
    if status:
        artifacts = [a for a in artifacts if a.get("status") == status]
    return {
        "contract": "rb_artifact_list_v1",
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "stub_count": sum(1 for a in artifacts if a.get("status") == "stub"),
        "active_count": sum(1 for a in artifacts if a.get("status") == "active"),
        "enrich_instruction": "Use POST /artifacts/{artifact_id}/enrich to add data to an existing artifact.",
        "build_instruction": "Use POST /artifacts to register and build a new artifact.",
    }


@app.get("/artifacts/{artifact_id:path}", tags=["compute"], operation_id="getArtifact")
def get_artifact_detail(
    artifact_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """Get details for a specific intelligence artifact.

    For micro_graph artifacts, also returns compact topology summary from the
    graph index (same data as getMicroGraphSummary when applicable).

    artifact_id examples:
      micro_graph:mcdonalds_us_ops
      micro_graph:par_technology
      micro_graph:toast_pos

    DEFECT-014 fix: single retrieval surface for all artifact types regardless
    of the underlying storage format.
    """
    _auth(x_api_key)
    registry = _load_artifact_registry()
    art = _get_artifact_by_id(artifact_id, registry)
    if not art:
        return {
            "status": "not_found",
            "artifact_id": artifact_id,
            "note": "No registered artifact with that id. Call GET /artifacts to see available artifacts.",
        }
    result = dict(art)
    result["status_label"] = art.get("status") or "unknown"
    # For active micro_graph artifacts, attach compact index summary
    if art.get("artifact_type") == "micro_graph" and art.get("status") == "active":
        index_path_rel = art.get("index_path")
        if index_path_rel:
            index_path = core.PROJECT_DIR / index_path_rel
            if index_path.exists():
                try:
                    idx = json.loads(index_path.read_text(encoding="utf-8"))
                    counts = idx.get("counts") or {}
                    tiers = counts.get("operator_tiers") or {}
                    tmpl = (idx.get("retrieval_answer_templates") or {}).get("operator_distribution") or ""
                    answer = tmpl.format(
                        operator_entities_with_stores=counts.get("operator_entities_with_stores", 0),
                        stores_with_operator_entity=counts.get("stores_with_operator_entity", 0),
                        enterprise_25_plus=tiers.get("enterprise_25_plus", 0),
                        mid_tier_5_to_24=tiers.get("mid_tier_5_to_24", 0),
                        single_digit_1_to_4=tiers.get("single_digit_1_to_4", 0),
                    ) if tmpl else None
                    result["topology_summary"] = {
                        "answer": answer,
                        "counts": counts,
                        "top_operator_entities": (idx.get("top_operator_entities") or [])[:10],
                        "top_states": (idx.get("state_distribution") or [])[:10],
                    }
                except Exception:  # noqa: BLE001
                    pass
    return result


class IntelligenceTriageIn(BaseModel):
    text: str = Field(..., description="Text to triage — paste, transcript, article, or screenshot content.")
    source_type: str = Field("paste", description="paste | file | screenshot | url | transcript")
    source_name: Optional[str] = Field(None, description="Optional filename, URL, or source label.")
    author_name: Optional[str] = Field(None, description="Optional author/sender name.")
    author_company: Optional[str] = Field(None, description="Optional author/sender company.")
    event_at: Optional[str] = Field(None, description="Optional ISO date when the event occurred.")
    captured_at: Optional[str] = Field(None, description="Optional ISO date when RB captured this.")


@app.post("/intelligence/triage", tags=["compute"], operation_id="triageInput")
def post_intelligence_triage(
    body: IntelligenceTriageIn,
    x_api_key: Optional[str] = Header(None),
):
    """Identify all intelligence types present in user-supplied text.

    DEFECT-015 fix: any input (paste, upload transcript, screenshot, article)
    may contain multiple intelligence types — macro signals, micro graph data,
    RI events, and strategic memory — that belong in different parts of RB.
    This endpoint runs all four classifiers and returns every type found.

    Always call this endpoint FIRST for any unclassified input.  Do NOT call
    processLinkedInSignal, manualRelationshipIntake, or recordStrategicMemory
    directly until you have the triage result.

    This endpoint is ALWAYS read-only.  Every stream in identified_types carries
    requires_confirmation=True.  Call the target_endpoint for each confirmed type
    after the user approves.

    Rendering contract for the Custom GPT:
      - Show type_count and each identified_type with extracted_summary.
      - Ask confirmation for each type independently.
      - Process in processing_order: ri_event → micro_graph → macro_signal → strategic_memory.
      - Report persistence_status per type after each confirmed mutation.
      - If noise_only is true, say so and stop.
    """
    _auth(x_api_key)
    registry = _load_artifact_registry()
    return intelligence_triage.triage_input(
        body.text,
        source_type=body.source_type,
        source_name=body.source_name,
        author_name=body.author_name,
        author_company=body.author_company,
        event_at=body.event_at,
        captured_at=body.captured_at,
        registry=registry,
    )


# ---------------------------------------------------------------------------
# Sprint E-5 / unified pipeline: consolidated ingest endpoint
# Runs the same 5-phase pipeline as intelligence_assessment.py but triggered
# on-demand by user-supplied content rather than the morning schedule.
# ---------------------------------------------------------------------------

class IngestIn(BaseModel):
    text: str = Field(..., description="Content to triage: paste, screenshot, article, transcript, or note.")
    source_type: str = Field("paste", description="paste | file | screenshot | url | transcript")
    source_name: Optional[str] = Field(None, description="Optional filename, URL, or source label.")
    author_name: Optional[str] = Field(None, description="Optional author/sender name.")
    author_company: Optional[str] = Field(None, description="Optional author/sender company.")
    event_at: Optional[str] = Field(None, description="Optional ISO date when the event occurred.")
    captured_at: Optional[str] = Field(None, description="Optional ISO capture date (defaults to now).")
    entity_context_days: int = Field(30, description="Look-back window in days for entity intelligence context.")
    auto_persist: bool = Field(
        False,
        description=(
            "When true, non-noise triage streams are immediately written to IntelligenceDB "
            "without a separate confirmation step. Mutation proposals (watchlist adds, thread "
            "changes) are still returned as proposals and require explicit confirmation. "
            "Use for any user-provided content where intelligence should persist automatically. "
            "The response includes a 'persisted_intelligence' block showing every item written."
        ),
    )


def _ingest_trust_stats(
    triage: dict,
    convergence: dict,
    proposals_count: int,
    entity_context: dict,
) -> dict:
    """Produce the unified trust-stats block for on-demand ingest.

    Same contract as intelligence_assessment.phase5_trust_stats but scoped to a
    single on-demand ingestion session.  Both paths share these keys:
      source, items_classified, convergences_detected, multi_source_entities,
      entity_pairs_detected, mutation_proposals, confidence, intelligence_gaps.
    """
    items_classified = triage.get("type_count") or 0
    noise_only = triage.get("noise_only") or False

    multi_source_count = convergence.get("multi_source_count") or 0
    entity_pair_count = convergence.get("entity_pair_count") or 0
    convergences_detected = multi_source_count + entity_pair_count

    # Confidence: derive from triage stream confidence, degrade on errors
    confidence_by_type: dict = (triage.get("trust_stats") or {}).get("confidence_by_type") or {}
    if noise_only:
        confidence = "low"
    elif convergence.get("status") == "error":
        confidence = "medium"
    elif any(v == "high" for v in confidence_by_type.values()):
        confidence = "high"
    elif confidence_by_type:
        confidence = "medium"
    else:
        confidence = "medium"

    # Intelligence gaps — mirrors the scheduled path's gap logic
    gaps: list[str] = []
    if noise_only:
        gaps.append("Input classified as noise — no actionable intelligence detected.")
    if convergences_detected == 0 and not noise_only:
        gaps.append("No sustained multi-source patterns in 30-day window — DB may be under-populated.")
    if not entity_context and items_classified > 0:
        gaps.append("Detected entities have no existing intelligence history in the DB.")
    if convergence.get("status") == "error":
        gaps.append("Convergence analysis unavailable — DB may be offline.")

    return {
        "source": "on_demand",
        "items_classified": items_classified,
        "convergences_detected": convergences_detected,
        "multi_source_entities": multi_source_count,
        "entity_pairs_detected": entity_pair_count,
        "mutation_proposals": proposals_count,
        "entity_context_hits": len(entity_context),
        "confidence": confidence,
        "intelligence_gaps": gaps,
        "processing_order": triage.get("processing_order") or [],
    }


def _execute_executive_declaration(stream: dict, raw_text: str, event_at: Optional[str] = None) -> list[dict]:
    """Auto-mutate canonical state from a CEO first-person declaration.

    No confirmation required — CEO declarations are the highest-fidelity source.
    Writes to tracked_opportunities.json, active_threads.yaml, and
    interaction_ledger.json as appropriate.

    Returns a list of mutation result dicts describing what was changed.
    """
    import yaml as _yaml
    results: list[dict] = []
    event_type = stream.get("event_type", "")
    declaration_text = raw_text.strip()
    today_str = event_at or date.today().isoformat()
    now_iso = f"{today_str}T00:00:00.000000+00:00"

    # ── opportunity_accepted ──────────────────────────────────────────────────
    if event_type == "opportunity_accepted":
        opp_path = SYSTEM_DIR / "tracked_opportunities.json"
        threads_path = SYSTEM_DIR / "active_threads.yaml"
        mutated = False
        try:
            opp_data = json.loads(opp_path.read_text(encoding="utf-8"))
            for opp in opp_data.get("opportunities", []):
                if opp.get("stage") not in ("offer_accepted", "declined", "closed"):
                    opp["stage"] = "offer_accepted"
                    opp["candidate_position"] = "accepted"
                    opp["last_updated"] = today_str
                    opp["status_narrative"] = (
                        f"CEO declaration {today_str}: offer accepted. {declaration_text}"
                    )
                    opp.setdefault("history", []).append({
                        "date": today_str,
                        "recorded_at": now_iso,
                        "stage": "offer_accepted",
                        "narrative": declaration_text,
                        "candidate_position": "accepted",
                        "source_type": "executive_declaration",
                    })
                    mutated = True
                    results.append({"mutation": "opportunity_accepted", "target": opp["id"],
                                    "previous_stage": "offer_verbal", "new_stage": "offer_accepted"})
            if mutated:
                opp_data["_last_updated"] = now_iso
                opp_path.write_text(json.dumps(opp_data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            results.append({"mutation": "opportunity_accepted", "error": "tracked_opportunities.json write failed"})

        # Close active thread(s) for this opportunity
        try:
            raw = _yaml.safe_load(threads_path.read_text()) or {}
            threads = raw.get("threads", raw) if isinstance(raw, dict) else raw
            for t in threads if isinstance(threads, list) else []:
                if t.get("type") in ("job_opportunity", "recruiter_engagement") and t.get("status") == "open":
                    t["status"] = "closed"
                    t["close_date"] = today_str
                    t["close_reason"] = f"accepted — CEO declaration {today_str}. {declaration_text}"
                    t["current_state"] = (
                        f"ACCEPTED ({today_str}): CEO declaration — offer accepted. "
                        "Onboarding phase active."
                    )
                    results.append({"mutation": "thread_closed", "target": t.get("id"), "reason": "accepted"})
            if isinstance(raw, dict):
                raw["last_updated"] = today_str
            threads_path.write_text(_yaml.dump(raw, allow_unicode=True, default_flow_style=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            results.append({"mutation": "thread_close", "error": "active_threads.yaml write failed"})

    # ── opportunity_declined ──────────────────────────────────────────────────
    elif event_type == "opportunity_declined":
        opp_path = SYSTEM_DIR / "tracked_opportunities.json"
        threads_path = SYSTEM_DIR / "active_threads.yaml"
        try:
            opp_data = json.loads(opp_path.read_text(encoding="utf-8"))
            # Mark any non-accepted, non-closed opportunity as declined
            for opp in opp_data.get("opportunities", []):
                if opp.get("stage") not in ("offer_accepted", "declined"):
                    opp["stage"] = "declined"
                    opp["candidate_position"] = "declined"
                    opp["last_updated"] = today_str
                    opp["status_narrative"] = f"CEO declaration {today_str}: declined. {declaration_text}"
                    opp.setdefault("history", []).append({
                        "date": today_str, "recorded_at": now_iso,
                        "stage": "declined", "narrative": declaration_text,
                        "candidate_position": "declined", "source_type": "executive_declaration",
                    })
                    results.append({"mutation": "opportunity_declined", "target": opp["id"]})
            opp_data["_last_updated"] = now_iso
            opp_path.write_text(json.dumps(opp_data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            results.append({"mutation": "opportunity_declined", "error": "write failed"})

        try:
            raw = _yaml.safe_load(threads_path.read_text()) or {}
            threads = raw.get("threads", raw) if isinstance(raw, dict) else raw
            for t in threads if isinstance(threads, list) else []:
                if t.get("type") in ("job_opportunity", "opportunity", "recruiter_engagement") and t.get("status") == "open":
                    t["status"] = "closed"
                    t["close_date"] = today_str
                    t["close_reason"] = f"declined — CEO declaration {today_str}. {declaration_text}"
                    t["current_state"] = f"DECLINED ({today_str}): CEO declaration. No further pursuit."
                    results.append({"mutation": "thread_closed", "target": t.get("id"), "reason": "declined"})
            if isinstance(raw, dict):
                raw["last_updated"] = today_str
            threads_path.write_text(_yaml.dump(raw, allow_unicode=True, default_flow_style=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            results.append({"mutation": "thread_close", "error": "active_threads.yaml write failed"})

    # ── relationship_touch / relationship_touch_inbound ───────────────────────
    elif event_type in ("relationship_touch", "relationship_touch_inbound"):
        ledger_path = SYSTEM_DIR / "interaction_ledger.json"
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            interactions = ledger.get("interactions", [])
            # Generate a simple interaction record from the declaration
            new_id = f"exec-decl-{today_str}-{len(interactions):04d}"
            interactions.append({
                "id": new_id,
                "contact_id": None,  # entity resolution deferred to next brief cycle
                "entity": {"name": "Unknown — resolve from declaration text", "org": None, "role": None},
                "interaction_date": today_str,
                "source_type": "executive_declaration",
                "signal_type": event_type,
                "declaration_text": declaration_text,
                "trust_delta": 1,
                "relationship_state_proposed": "active",
                "executive_weight": 3,
                "source_text_snippet": declaration_text[:200],
            })
            ledger["interactions"] = interactions
            ledger["_last_updated"] = now_iso
            ledger_path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")
            results.append({"mutation": "interaction_recorded", "event_type": event_type,
                            "interaction_id": new_id, "note": "contact_id requires manual resolution"})
        except Exception:  # noqa: BLE001
            results.append({"mutation": "relationship_touch", "error": "interaction_ledger.json write failed"})

    # ── action_completed / start_date_set / state_resolved / loop_advanced ────
    elif event_type in ("action_completed", "start_date_set", "state_resolved", "loop_advanced"):
        # Record in interaction ledger as a general action completion
        ledger_path = SYSTEM_DIR / "interaction_ledger.json"
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            interactions = ledger.get("interactions", [])
            new_id = f"exec-action-{today_str}-{len(interactions):04d}"
            interactions.append({
                "id": new_id,
                "contact_id": None,
                "entity": {"name": "self", "org": None, "role": None},
                "interaction_date": today_str,
                "source_type": "executive_declaration",
                "signal_type": event_type,
                "declaration_text": declaration_text,
                "trust_delta": 0,
                "executive_weight": 2,
                "source_text_snippet": declaration_text[:200],
            })
            ledger["interactions"] = interactions
            ledger["_last_updated"] = now_iso
            ledger_path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")
            results.append({"mutation": "action_recorded", "event_type": event_type, "interaction_id": new_id})
        except Exception:  # noqa: BLE001
            results.append({"mutation": event_type, "error": "write failed"})

        # RB-DEFECT-060 — EOLMS evidence-backed closure. Only for loop-targeted event
        # types; only auto-applies on an unambiguous single best match (see
        # eolms.match_and_transition's confidence gate) — best-effort, never fails
        # the declaration call if EOLMS itself errors.
        if event_type in ("action_completed", "state_resolved", "loop_advanced"):
            try:
                eolms_result = eolms.match_and_transition(declaration_text, apply=True)
                results.append({"mutation": "eolms_loop_transition", **eolms_result})
            except Exception as exc:  # noqa: BLE001
                results.append({"mutation": "eolms_loop_transition", "status": "error", "detail": str(exc)})

    # ── personal_practice_logged ──────────────────────────────────────────────
    # RB-DEFECT-062 — Life Lens had no write path at all (not a routing gap like
    # RB-DEFECT-061; personal_log.json never existed). Not a relationship interaction,
    # so this doesn't go through the interaction_ledger branch above.
    elif event_type == "personal_practice_logged":
        try:
            log_result = personal_log.record_personal_practice(declaration_text, event_at, apply=True)
            results.append({"mutation": "personal_log_update", **log_result})
        except Exception as exc:  # noqa: BLE001
            results.append({"mutation": "personal_log_update", "status": "error", "detail": str(exc)})
        # Same declaration can carry personal-relationship / life-timeline
        # content the goal tracker above has no concept of ("date night",
        # "supported Mary at her retirement party") — evaluated independently
        # so a single report can produce both kinds of mutation at once.
        try:
            rel_result = personal_log.record_personal_relationship_events(declaration_text, event_at, apply=True)
            if rel_result.get("status") != "no_match":
                results.append({"mutation": "personal_relationship_event", **rel_result})
        except Exception as exc:  # noqa: BLE001
            results.append({"mutation": "personal_relationship_event", "status": "error", "detail": str(exc)})

    return results


class ExecDeclIn(BaseModel):
    text: str = Field(..., description="CEO first-person declaration text.")
    event_at: Optional[str] = Field(None, description="ISO date of the event (defaults to today).")
    entity_name: Optional[str] = Field(None, description="Optional named entity the declaration concerns.")
    opportunity_id: Optional[str] = Field(None, description="Optional opportunity ID to scope mutations.")


@app.post("/ingest/executive_declaration", tags=["compute"], operation_id="ingestExecutiveDeclaration")
def post_exec_declaration(body: ExecDeclIn, x_api_key: Optional[str] = Header(None)):
    """Process a CEO first-person state declaration and auto-mutate canonical state.

    CEO declarations are authoritative — highest-fidelity source in the priority hierarchy.
    No confirmation required for unambiguous declarations.

    Supported event types:
    - opportunity_accepted: "I accepted the offer"
    - opportunity_declined: "I declined / passed on the opportunity"
    - relationship_touch: "I talked to / met with / called [person] today"
    - action_completed: "I sent / submitted / completed [action]"
    - state_resolved: "the well pump is working", "it's fixed", "resolved" (RB-DEFECT-060)
    - loop_advanced: "Ryan responded", "heard back from [person]" (RB-DEFECT-060)
    - start_date_set: "My start date is [date]"
    - personal_practice_logged: "Mary and I prayed and read the bible together" (RB-DEFECT-062)

    Mutations applied immediately to:
    - tracked_opportunities.json (stage, candidate_position, history)
    - active_threads.yaml (status, close_date, close_reason, current_state)
    - interaction_ledger.json (new interaction entry)
    - system/eolms/loops.json — action_completed/state_resolved/loop_advanced additionally
      run eolms.match_and_transition() against the EOLMS register; a confident single match
      is transitioned (e.g. to completed, or waiting -> active) and included in the response
      under a "eolms_loop_transition" mutation entry. Ambiguous or no-match text is reported
      but nothing is auto-closed — see eolms.py's match_and_transition docstring.
    - system/personal_log.json — personal_practice_logged matches every manually-tracked Life
      Lens goal (see life_goals.yaml's log_keywords) mentioned in the text and marks each True
      for the resolved date (explicit "yesterday" in the text wins over event_at), included
      under a "personal_log_update" mutation entry. See personal_log.py's
      record_personal_practice() docstring.
    - system/personal_relationship_log.json / system/personal_timeline.json — the same
      personal_practice_logged declarations are also checked for personal-relationship
      events ("date night", "supported Mary at her retirement party") and significant
      life events, included under a "personal_relationship_event" mutation entry when
      present. Intelligence only — never creates a to-do/loop for content describing
      something that already happened. See personal_log.py's
      record_personal_relationship_events() docstring.
    """
    _auth(x_api_key)
    from intelligence_triage import classify_executive_declaration
    stream = classify_executive_declaration(body.text)
    if stream is None:
        return {
            "status": "no_match",
            "message": "Text did not match any executive declaration pattern. Use ingestContent for general intelligence.",
            "text": body.text[:200],
        }
    mutations = _execute_executive_declaration(stream, body.text, body.event_at)
    mutation_kinds = ",".join(sorted({m.get("mutation", "?") for m in mutations})) or "none"
    al.log_mutation_executed(
        f"ingestExecutiveDeclaration: event_type={stream.get('event_type')} mutations=[{mutation_kinds}] text={body.text[:200]!r}",
        source="POST /ingest/executive_declaration",
    )
    return {
        "status": "ok",
        "event_type": stream.get("event_type"),
        "confidence": stream.get("confidence"),
        "mutations_applied": mutations,
        "mutations_count": len(mutations),
        "source": "CEO",
        "requires_confirmation": False,
    }


@app.post("/ingest", tags=["compute"], operation_id="ingestContent")
def post_ingest(
    body: IngestIn,
    x_api_key: Optional[str] = Header(None),
):
    """Consolidated content ingestion — full 5-phase intelligence pipeline on demand.

    The mandatory first call for ANY incoming content (paste, screenshot, article,
    newsletter, post, transcript, note, earnings release).  Runs the same pipeline
    as the scheduled morning assessment but triggered by user-supplied content.

    Phase 1 — Triage: classify all intelligence types present (macro_signal,
      ri_event, micro_graph_enrichment, micro_graph_build, strategic_memory, noise).

    Phase 2 — Entity Context: looks up 30-day DB history for every detected entity.
      entity_context: {entity_name: entity_summary} — only entities with DB items.

    Phase 3 — Convergence Analysis: queries existing IntelligenceDB for multi-source
      entities and entity pairs.  Shows what sustained patterns already exist and
      where the new content fits.

    Phase 4 — Mutation Proposals: generates watchlist_add and thread_intelligence
      proposals from convergence findings.  All require_confirmation: true.

    Phase 5 — Trust Stats: unified block (same contract as scheduled assessment)
      with source='on_demand'.  Drives the Receipt rendering in the CoS.

    This endpoint is ALWAYS read-only — no DB writes occur.  Every proposed
    mutation in mutation_proposals carries requires_confirmation=True.
    """
    _auth(x_api_key)
    registry = _load_artifact_registry()

    # ── Phase 1: Triage ───────────────────────────────────────────────────────
    triage = intelligence_triage.triage_input(
        body.text,
        source_type=body.source_type,
        source_name=body.source_name,
        author_name=body.author_name,
        author_company=body.author_company,
        event_at=body.event_at,
        captured_at=body.captured_at,
        registry=registry,
    )

    # Collect unique entities from actionable (non-noise) streams
    entities: set[str] = set()
    for stream in triage.get("identified_types", []):
        if stream.get("intelligence_type") != "noise":
            for ent in stream.get("extracted_entities", []):
                if isinstance(ent, str) and ent.strip():
                    entities.add(ent.strip())

    # ── Phase 2: Entity Context ───────────────────────────────────────────────
    entity_context: dict = {}
    if entities and _intelligence_db_module:
        try:
            db = _open_idb()
            try:
                for ent in sorted(entities)[:10]:
                    summary = db.entity_summary(ent, days=body.entity_context_days)
                    if summary.get("item_count", 0) > 0:
                        entity_context[ent] = summary
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            pass  # best-effort; triage result is authoritative

    # ── Phase 3: Convergence Analysis ─────────────────────────────────────────
    convergence: dict = {
        "status": "skipped",
        "multi_source": [], "entity_pairs": [],
        "multi_source_count": 0, "entity_pair_count": 0,
    }
    if _intelligence_db_module:
        try:
            db = _open_idb()
            try:
                all_convs = db.find_convergences(min_entity_hits=2, days=30)
                multi_source = [
                    c for c in all_convs if len(c.get("sources") or []) >= 2
                ][:10]
                pairs = db.cross_entity_convergence(days=30, min_shared_items=2)[:10]
                convergence = {
                    "status": "ok",
                    "multi_source": multi_source,
                    "entity_pairs": pairs,
                    "multi_source_count": len(multi_source),
                    "entity_pair_count": len(pairs),
                }
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            convergence["status"] = "error"

    # ── Phase 4: Mutation Proposals ───────────────────────────────────────────
    proposals: list[dict] = []
    proposals_count = 0
    if _HAS_IA and convergence.get("status") == "ok":
        try:
            p4 = _ia_module.phase4_mutation_proposals(  # type: ignore[union-attr]
                convergence, prior_assessment=None
            )
            proposals = p4.get("proposals") or []
            proposals_count = p4.get("proposals_count") or 0
        except Exception:  # noqa: BLE001
            pass

    # ── Phase 4b: Auto-Persist (RB-INT-017) ──────────────────────────────────
    # When auto_persist=True, non-noise triage streams are immediately written to
    # IntelligenceDB. This is intelligence RECORDING (what was observed), not
    # mutation (watchlist adds, thread changes). Mutations still require confirmation.
    persisted_items: list[dict] = []
    persisted_count = 0
    if body.auto_persist and _intelligence_db_module:
        actionable_streams = [
            s for s in triage.get("identified_types", [])
            if s.get("intelligence_type") != "noise"
        ]
        if actionable_streams:
            try:
                db = _open_idb()
                try:
                    for stream in actionable_streams:
                        intel_type = stream.get("intelligence_type", "unknown")
                        summary = stream.get("extracted_summary", "")
                        entities = stream.get("extracted_entities", [])
                        confidence = stream.get("confidence", "medium")
                        source = body.source_name or body.source_type or "user_input"

                        # Build a meaningful title from the stream
                        entity_label = (
                            f" [{', '.join(entities[:3])}]" if entities else ""
                        )
                        title = f"{intel_type}{entity_label}"
                        if len(title) > 120:
                            title = title[:117] + "..."

                        tags = [{"type": "intelligence_type", "value": intel_type}]
                        # RB-DEFECT-2026-07-13: see the identical fix in the JPR
                        # capture-ingestion path above -- extracted_entities is
                        # category/type labels (not real named entities) except
                        # when entity_scoped is set.
                        if stream.get("entity_scoped"):
                            for ent in entities[:10]:
                                tags.append({"type": "entity", "value": ent})

                        item_id = db.add_item(
                            title=title,
                            content=summary or body.text[:500],
                            source_name=source,
                            source_url=body.source_name if body.source_type == "url" else None,
                            source_type=body.source_type,
                            gathered_date=body.event_at or body.captured_at,
                            confidence=confidence if confidence in ("high", "medium", "low") else "medium",
                            lifecycle_state="new",
                            tags=tags,
                            raw_json={
                                "triage_stream": stream,
                                "auto_persisted": True,
                                "captured_at": body.captured_at,
                            },
                        )
                        persisted_items.append({
                            "item_id": item_id,
                            "intelligence_type": intel_type,
                            "entities": entities,
                            "confidence": confidence,
                            "title": title,
                        })
                        persisted_count += 1
                finally:
                    db.close()
            except Exception:  # noqa: BLE001
                pass  # best-effort; do not fail the full ingest call

    # ── Phase 5: Trust Stats ──────────────────────────────────────────────────
    trust_stats = _ingest_trust_stats(triage, convergence, proposals_count, entity_context)

    # ── Phase 6: Executive Declaration Auto-Mutation (D3/D4) ─────────────────
    # If triage identified an executive_declaration stream, auto-execute mutations
    # immediately — no confirmation required. CEO declarations are authoritative.
    exec_mutations: list[dict] = []
    exec_decl_streams = [
        s for s in triage.get("identified_types", [])
        if s.get("intelligence_type") == "executive_declaration"
    ]
    for stream in exec_decl_streams:
        result = _execute_executive_declaration(stream, body.text, body.event_at)
        exec_mutations.extend(result)

    return {
        **triage,
        # Override triage trust_stats with the full unified block
        "trust_stats": trust_stats,
        # Phase 2
        "entity_context": entity_context,
        "entity_context_days": body.entity_context_days,
        # Phase 3
        "convergence_analysis": convergence,
        # Phase 4
        "mutation_proposals": proposals,
        "mutation_proposals_count": proposals_count,
        # Phase 4b — auto-persist receipt (RB-INT-017)
        "auto_persist": body.auto_persist,
        "persisted_intelligence": persisted_items,
        "persisted_intelligence_count": persisted_count,
        # Phase 6 — executive declaration mutations (D3/D4)
        "executive_mutations": exec_mutations,
        "executive_mutations_count": len(exec_mutations),
    }


class ArtifactEnrichIn(BaseModel):
    text: Optional[str] = Field(None, description="New signal text to enrich the artifact with.")
    source_type: str = Field("paste", description="paste | file | screenshot | url")
    source_name: Optional[str] = Field(None, description="Optional source filename or URL.")
    event_at: Optional[str] = Field(None, description="Optional ISO date of the intelligence.")
    confirm: bool = Field(False, description="confirm=false previews enrichment; confirm=true applies it.")


@app.post("/artifacts/{artifact_id:path}/enrich", tags=["write"], operation_id="enrichArtifact")
def post_artifact_enrich(
    artifact_id: str,
    body: ArtifactEnrichIn,
    x_api_key: Optional[str] = Header(None),
):
    """Enrich an existing intelligence artifact with new signal data.

    confirm=false (default): returns a preview of what would be enriched —
    the triage result for the supplied text scoped to this artifact, plus
    the proposed source_lineage update.

    confirm=true: records the enrichment event in the artifact registry.
    For micro_graph artifacts, this records that new source data is available;
    full graph rebuild requires running the appropriate ETL script separately.

    DEFECT-014 fix: provides a general enrichment endpoint so users can add
    new data to any registered artifact without re-running ad hoc ETL scripts.

    requires_confirmation: true — always confirm before writing.
    """
    _auth(x_api_key)
    registry = _load_artifact_registry()
    art = _get_artifact_by_id(artifact_id, registry)
    if not art:
        return {
            "status": "not_found",
            "artifact_id": artifact_id,
            "note": "Cannot enrich an unregistered artifact. Register it first via POST /artifacts.",
            "requires_confirmation": False,
        }
    from datetime import datetime as _dt
    now_iso = _dt.now().isoformat(timespec="seconds")

    is_stub = art.get("status") == "stub"

    # Stub artifact — preview or first-enrichment promotion
    if is_stub and not body.confirm:
        return {
            "status": "stub_preview",
            "artifact_id": artifact_id,
            "artifact_name": art.get("name"),
            "current_status": "stub",
            "note": (
                f"'{art.get('name')}' is a stub — no source data has been ingested yet. "
                "Providing source data and calling with confirm=true will begin building this artifact "
                "and promote its status from 'stub' to 'building'."
            ),
            "proposed_action": (
                "Supply source text or source_name (file path), then call with confirm=true "
                "to record the first enrichment and promote status to 'building'."
            ),
            "requires_confirmation": True,
        }

    # Triage the supplied text scoped to this artifact
    triage_result = None
    if body.text:
        triage_result = intelligence_triage.triage_input(
            body.text,
            source_type=body.source_type,
            source_name=body.source_name,
            event_at=body.event_at,
            registry=registry,
        )

    if not body.confirm:
        return {
            "status": "preview",
            "artifact_id": artifact_id,
            "artifact_name": art.get("name"),
            "artifact_type": art.get("artifact_type"),
            "current_enrichment_count": art.get("enrichment_count") or 0,
            "current_last_enriched": art.get("last_enriched"),
            "triage_preview": triage_result,
            "proposed_source_lineage_entry": {
                "source_type": body.source_type,
                "source_name": body.source_name,
                "ingested_at": body.event_at or now_iso[:10],
                "captured_at": now_iso,
            },
            "requires_confirmation": True,
            "note": "Call with confirm=true to record this enrichment.",
        }

    # confirm=true: record enrichment event in registry
    new_lineage_entry = {
        "source_type": body.source_type,
        "source_name": body.source_name,
        "ingested_at": body.event_at or now_iso[:10],
        "captured_at": now_iso,
        "enrichment_note": f"Signal enrichment via POST /artifacts/{artifact_id}/enrich",
    }
    # Update in-memory and persist
    new_enrichment_count = 0
    for a in (registry.get("artifacts") or []):
        if a.get("artifact_id") == artifact_id:
            a.setdefault("source_lineage", []).append(new_lineage_entry)
            a["enrichment_count"] = (a.get("enrichment_count") or 0) + 1
            a["last_enriched"] = now_iso[:10]
            # Promote stub → building on first enrichment
            if is_stub:
                a["status"] = "building"
                a.setdefault("notes", []).append(
                    f"Promoted from stub to building on first enrichment {now_iso[:10]}."
                )
            elif art.get("artifact_type") == "micro_graph" and body.source_name:
                a.setdefault("notes", []).append(
                    f"Enrichment {a['enrichment_count']}: new source '{body.source_name}' "
                    f"recorded {now_iso[:10]}. Run micro_graph_builder.py to rebuild the graph."
                )
            new_enrichment_count = a["enrichment_count"]
    try:
        _ARTIFACT_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ARTIFACT_REGISTRY_PATH.write_text(
            json.dumps(registry, indent=2) + "\n", encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "write_failed",
            "artifact_id": artifact_id,
            "error": str(exc),
            "requires_confirmation": False,
        }
    note = (
        f"First enrichment recorded — '{art.get('name')}' promoted from stub to building. "
        "Supply more source data to continue building, or run the appropriate ETL script to build the graph."
        if is_stub else
        "Enrichment event recorded in artifact registry. "
        "For micro_graph artifacts, run the appropriate ETL script to rebuild graph.json from the new source."
    )
    return {
        "status": "enriched",
        "artifact_id": artifact_id,
        "artifact_name": art.get("name"),
        "artifact_status": "building" if is_stub else art.get("status"),
        "enrichment_count": new_enrichment_count,
        "last_enriched": now_iso[:10],
        "source_lineage_entry": new_lineage_entry,
        "persistence_status": "persisted",
        "promoted_from_stub": is_stub,
        "note": note,
    }


@app.get("/intro", tags=["compute"], operation_id="findIntro")
def get_intro(
    target: str = Query(..., description="Person id, name, or company name."),
    limit: int = Query(3),
    date_str: Optional[str] = Query(None, alias="date"),
    include_suppressed: bool = Query(False),
    x_api_key: Optional[str] = Header(None),
):
    """Find broker paths to a target."""
    _auth(x_api_key)
    d = date.fromisoformat(date_str) if date_str else date.today()
    return core.find_intro_paths(
        target, limit=limit, today=d, include_suppressed=include_suppressed,
    )


@app.get("/social_overlay", tags=["compute"], operation_id="getSocialOverlay")
def get_social_overlay(
    recent_days: int = Query(14),
    x_api_key: Optional[str] = Header(None),
):
    """Social overlay — posts from your graph + active-thread company hits."""
    _auth(x_api_key)
    return core.social_overlay(recent_days=recent_days)


@app.get("/sessions/recent", tags=["compute"])
def get_recent_sessions(
    limit: int = Query(3),
    x_api_key: Optional[str] = Header(None),
):
    """Return the last N session memory entries with body included."""
    _auth(x_api_key)
    return core.load_recent_sessions(limit=limit)


class SessionEndIn(BaseModel):
    session_id: Optional[str] = None
    date: Optional[str] = None
    end_time: Optional[str] = None
    duration_estimate: Optional[str] = None
    focus_areas: list[str] = []
    threads_touched: list[str] = []
    contacts_touched: list[str] = []
    mutations: dict = {}
    status: str = "complete"
    next_session_should: Optional[str] = None
    body: dict = {}


@app.post("/sessions", tags=["write"])
def post_session_end(body: SessionEndIn, x_api_key: Optional[str] = Header(None)):
    """Persist a session memory file. Returns the written path and the new index size."""
    _auth(x_api_key)
    import sys as _sys
    _sys.path.insert(0, str(SYSTEM_DIR / "scripts"))
    import session_writer, session_index
    summary = body.model_dump()
    target = session_writer.write_session(summary)
    idx = session_index.build_index()
    session_index.INDEX_PATH.write_text(json.dumps(idx, indent=2) + "\n")
    return {"ok": True, "file": str(target.relative_to(core.PROJECT_DIR)),
            "indexed_count": idx["count"]}


class SocialAddIn(BaseModel):
    author_name: str
    text: str
    posted_at: Optional[str] = None
    author_url: Optional[str] = None
    author_headline: Optional[str] = None
    url: Optional[str] = None
    platform: str = "linkedin"
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None


@app.post("/social", tags=["write"])
def post_social_add(body: SocialAddIn, x_api_key: Optional[str] = Header(None)):
    """Append a single social post to system/inbox/social.feed.json."""
    _auth(x_api_key)
    rc = mutations.cmd_social_add(_ns(
        author_name=body.author_name, text=body.text, posted_at=body.posted_at,
        author_url=body.author_url, author_headline=body.author_headline,
        url=body.url, platform=body.platform,
        likes=body.likes, comments=body.comments, shares=body.shares,
        id=None, dry_run=False,
    ))
    if rc != 0:
        raise HTTPException(400, "social-add failed")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Test traces — RB 8.0-style developer logging restored for RB 9.0.
# ---------------------------------------------------------------------------

class TestTraceStep(BaseModel):
    step: Optional[int] = None
    label: Optional[str] = None
    timestamp: Optional[str] = None
    user_prompt: Optional[str] = None
    assistant_response: Optional[str] = None
    intended_operation: Optional[str] = None
    endpoint: Optional[str] = None
    request_params: Optional[dict] = None
    response_status: Optional[int] = None
    response_summary: Optional[str] = None
    mutation_confirmation: Optional[str] = None
    post_validation: Optional[str] = None
    user_feedback: Optional[str] = None
    observed_issue: Optional[str] = None


class TestTraceDefect(BaseModel):
    id: Optional[str] = None
    title: Optional[str] = None
    severity: Optional[str] = None
    description: Optional[str] = None
    recommendation: Optional[str] = None


class TestTraceIn(BaseModel):
    title: str
    trace_type: Optional[str] = "interface_test"
    source: Optional[str] = None
    operator: Optional[str] = None
    captured_at: Optional[str] = None
    session_id: Optional[str] = None
    summary: Optional[str] = None
    observed_issue: Optional[str] = None
    steps: list[TestTraceStep] = []
    defects: list[TestTraceDefect] = []
    raw_text: Optional[str] = None


@app.post("/test_traces", tags=["trace"], operation_id="saveTestTrace")
def post_test_trace(body: TestTraceIn, x_api_key: Optional[str] = Header(None)):
    """Persist a structured test trace. Used by the Custom GPT to export a
    debugging-grade record of the most recent test session. Secrets in
    request_params, raw_text, and free-form fields are redacted before write.

    Returns the assigned trace_id and the relative paths of the written
    .md / .json files so the caller can reference them in a follow-up answer.
    """
    _auth(x_api_key)
    payload = body.model_dump(exclude_none=False)
    try:
        return test_trace.append_trace(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/test_traces/recent", tags=["trace"], operation_id="getRecentTestTraces")
def get_recent_test_traces(
    limit: int = Query(10, ge=1, le=100),
    x_api_key: Optional[str] = Header(None),
):
    """List the most recent test traces (summary only)."""
    _auth(x_api_key)
    return test_trace.list_recent(limit=limit)


# ---------------------------------------------------------------------------
# Relationship signals — last-24-hour CoS-level signal layer (Phase 9).
# ---------------------------------------------------------------------------

@app.get("/relationship_signals", tags=["compute"], operation_id="getRelationshipSignals")
def get_relationship_signals(
    date_str: Optional[str] = Query(None, alias="date",
                                    description="ISO date 'YYYY-MM-DD' to anchor the 24h window; defaults to today."),
    hours: int = Query(24, ge=1, le=168,
                       description="Look-back window in hours, default 24."),
    use_cache: bool = Query(True),
    x_api_key: Optional[str] = Header(None),
):
    """Last-`hours` relationship signals across email, calendar, messages,
    calls, and social — matched against baseline + active threads. Returns
    classified, ranked, action-tagged signals plus reconciliation prompts
    and stale-source warnings.
    """
    _auth(x_api_key)
    import sys as _sys
    _sys.path.insert(0, str(SYSTEM_DIR / "scripts"))
    import relationship_signals as rs
    d = date.fromisoformat(date_str) if date_str else date.today()
    if use_cache:
        cached = core.read_cache("relationship_signals")
        if cached and cached.get("today") == d.isoformat() and cached.get("window_hours") == hours:
            return cached
    return rs.build_report(today=d, hours=hours)


# ---------------------------------------------------------------------------
# RB 9.42 — Relationship aggregated query (Gap 2)
# ---------------------------------------------------------------------------

@app.get("/contacts/query", tags=["compute"], operation_id="queryContacts")
def query_contacts(
    min_days_quiet: Optional[int] = Query(None, ge=0, description="Return contacts not touched in at least this many days. 0 = any. Omit for no lower bound."),
    max_days_quiet: Optional[int] = Query(None, ge=0, description="Return contacts not touched more recently than this many days (upper bound). Omit for no upper bound."),
    circle: Optional[str] = Query(None, description="Filter by circle membership. Partial match, case-insensitive (e.g. 'hospitality')."),
    company: Optional[str] = Query(None, description="Filter by current_company. Partial match, case-insensitive (e.g. 'applebee')."),
    source_contains: Optional[str] = Query(None, description="Filter by data provenance: return only contacts whose sources[] list contains a string matching this substring, case-insensitive (e.g. 'hubspot' for CRM-sourced contacts, 'linkedin_export' for LinkedIn-sourced contacts)."),
    signal_class: Optional[str] = Query(None, description="Filter by signal_class: RC, VC, LMI, LKI."),
    rc_tier: Optional[str] = Query(None, description="Filter by rc_tier: inner, broader, dormant_valuable."),
    has_open_loop: Optional[bool] = Query(None, description="If true, return only contacts with at least one open loop. If false, return contacts with no open loops. Omit to ignore."),
    sort_by: str = Query("days_quiet_desc", description="Sort order: days_quiet_desc (default), days_quiet_asc, name_asc, last_touch_desc."),
    limit: int = Query(25, ge=1, le=100, description="Max contacts to return (1–100, default 25)."),
    x_api_key: Optional[str] = Header(None),
):
    """Query the relationship baseline for aggregated contact questions.

    Use this endpoint to answer questions like:
      - 'Who haven't I contacted in 60 days?'
      - 'Which RC contacts are overdue?'
      - 'Who is in the hospitality-table circle that I haven't touched in 30 days?'
      - 'Show me all VC contacts quiet for 90+ days'
      - 'Which RC inner-tier contacts have no open loop?'
      - 'Who do I know at Applebee's?'
      - 'Show me my HubSpot CRM contacts'

    Returns a filtered, sorted list of matching contacts with days_quiet computed
    from today's date, plus a formatted `answer` summary string for direct GPT use.

    Filter parameters are combinable (AND logic). Any parameter omitted is not applied.
    The `has_open_loop` filter requires loading the loops ledger — only use it when
    loop context is explicitly part of the question.
    """
    _auth(x_api_key)
    try:
        today_dt = date.today()
        baseline = core.load_baseline()

        # ── Build loop membership index if needed ─────────────────────────────
        loop_contact_names: Optional[set] = None
        if has_open_loop is not None:
            try:
                all_loops = core.parse_loop_ledger()
                open_loops = [lp for lp in all_loops if not lp.closed]
                loop_contact_names = {lp.party.lower() for lp in open_loops}
            except Exception:  # noqa: BLE001
                loop_contact_names = set()

        # ── Filter and annotate ───────────────────────────────────────────────
        results: list[dict] = []
        no_last_touch: list[dict] = []
        circle_lower = circle.lower() if circle else None
        company_lower = company.lower() if company else None
        sc_lower = signal_class.upper() if signal_class else None
        tier_lower = rc_tier.lower() if rc_tier else None

        for contact in baseline:
            # signal_class filter
            if sc_lower and (contact.get("signal_class") or "").upper() != sc_lower:
                continue
            # rc_tier filter
            if tier_lower and (contact.get("rc_tier") or "").lower() != tier_lower:
                continue
            # circle filter (partial match against any circle)
            if circle_lower:
                contact_circles = [(c or "").lower() for c in (contact.get("circles") or [])]
                if not any(circle_lower in c for c in contact_circles):
                    continue
            # company filter (partial match against current_company)
            if company_lower:
                if company_lower not in (contact.get("current_company") or "").lower():
                    continue
            # source-provenance filter (partial match against any sources[] entry)
            if source_contains:
                sc_needle = source_contains.lower()
                if not any(sc_needle in (s or "").lower() for s in (contact.get("sources") or [])):
                    continue

            # Compute days quiet
            lt_raw = contact.get("last_touch")
            if lt_raw:
                try:
                    lt_date = date.fromisoformat(str(lt_raw))
                    days_quiet = (today_dt - lt_date).days
                except ValueError:
                    days_quiet = None
            else:
                days_quiet = None

            # days_quiet bounds filter
            if min_days_quiet is not None:
                if days_quiet is None or days_quiet < min_days_quiet:
                    if days_quiet is None:
                        no_last_touch.append(contact)
                    continue
            if max_days_quiet is not None:
                if days_quiet is None or days_quiet > max_days_quiet:
                    continue

            # loop filter
            if loop_contact_names is not None:
                name_lower = (contact.get("name") or "").lower()
                has_loop = name_lower in loop_contact_names
                if has_open_loop and not has_loop:
                    continue
                if not has_open_loop and has_loop:
                    continue

            entry = {
                "id": contact.get("id"),
                "name": contact.get("name"),
                "signal_class": contact.get("signal_class"),
                "rc_tier": contact.get("rc_tier"),
                "current_company": contact.get("current_company"),
                "circles": contact.get("circles") or [],
                "last_touch": lt_raw,
                "days_quiet": days_quiet,
                "rc_state": contact.get("rc_state"),
                "sources": contact.get("sources") or [],
            }
            results.append(entry)

        # ── Sort ──────────────────────────────────────────────────────────────
        if sort_by == "days_quiet_desc":
            results.sort(key=lambda x: x["days_quiet"] if x["days_quiet"] is not None else -1, reverse=True)
        elif sort_by == "days_quiet_asc":
            results.sort(key=lambda x: x["days_quiet"] if x["days_quiet"] is not None else 999999)
        elif sort_by == "last_touch_desc":
            results.sort(key=lambda x: x["last_touch"] or "", reverse=True)
        else:  # name_asc
            results.sort(key=lambda x: (x["name"] or "").lower())

        total_matched = len(results)
        results = results[:limit]

        # ── Build answer string ───────────────────────────────────────────────
        filter_desc_parts = []
        if sc_lower:
            filter_desc_parts.append(f"signal_class={sc_lower}")
        if tier_lower:
            filter_desc_parts.append(f"rc_tier={tier_lower}")
        if circle_lower:
            filter_desc_parts.append(f"circle contains '{circle}'")
        if company_lower:
            filter_desc_parts.append(f"company contains '{company}'")
        if source_contains:
            filter_desc_parts.append(f"source contains '{source_contains}'")
        if min_days_quiet is not None:
            filter_desc_parts.append(f"quiet ≥{min_days_quiet} days")
        if max_days_quiet is not None:
            filter_desc_parts.append(f"quiet ≤{max_days_quiet} days")
        if has_open_loop is True:
            filter_desc_parts.append("has open loop")
        if has_open_loop is False:
            filter_desc_parts.append("no open loop")
        filter_str = " | ".join(filter_desc_parts) if filter_desc_parts else "no filters"

        if total_matched == 0:
            answer = f"No contacts matched filters: {filter_str}."
        else:
            names = [r["name"] for r in results if r.get("name")]
            answer = (
                f"{total_matched} contact(s) matched [{filter_str}]"
                + (f" — showing top {len(results)}" if total_matched > len(results) else "")
                + ". "
                + ", ".join(names[:10])
                + ("..." if len(names) > 10 else ".")
            )

        out: dict = {
            "contract": "rb_contact_query_v1",
            "filter_applied": {
                "min_days_quiet": min_days_quiet,
                "max_days_quiet": max_days_quiet,
                "circle": circle,
                "company": company,
                "source_contains": source_contains,
                "signal_class": sc_lower,
                "rc_tier": tier_lower,
                "has_open_loop": has_open_loop,
                "sort_by": sort_by,
                "limit": limit,
            },
            "total_matched": total_matched,
            "returned": len(results),
            "contacts": results,
            "answer": answer,
            "generated_at": today_dt.isoformat(),
        }

        # Include no_last_touch contacts if min_days_quiet is set and there are matches
        if min_days_quiet is not None and no_last_touch:
            out["no_last_touch_count"] = len(no_last_touch)
            out["note"] = f"{len(no_last_touch)} additional contact(s) have no last_touch date and were excluded from the days_quiet filter."

        return out

    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"contact query failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Conference Campaign Intelligence Engine — read-only query surface over
# system/campaigns/<id>/{roster.json,company_rollup.json}. These endpoints
# read the artifacts campaign_engine.py already built; they never re-run
# eligibility/scoring/tiering themselves (use campaign_engine.py's CLI or a
# dedicated build step for that, since it can mutate roster.json).
# ---------------------------------------------------------------------------

@app.get("/campaigns", tags=["compute"], operation_id="listCampaigns")
def list_campaigns_endpoint(x_api_key: Optional[str] = Header(None)):
    """List known conference/event campaigns (from system/campaigns/registry.yaml).

    Use this to answer 'what campaigns exist' before querying a specific
    campaign's roster — e.g. to resolve 'the Genius conference' to its
    campaign_id.
    """
    _auth(x_api_key)
    try:
        campaigns = campaign_engine.list_campaigns()
        return {
            "contract": "rb_campaign_list_v1",
            "campaign_count": len(campaigns),
            "campaigns": campaigns,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"campaign list failed: {exc}") from exc


@app.get("/campaigns/{campaign_id}/roster", tags=["compute"], operation_id="getCampaignRoster")
def get_campaign_roster(
    campaign_id: str,
    tier: Optional[str] = Query(None, description="Filter by tier id or tier_label substring, case-insensitive (e.g. 'tier_1_must_invite' or 'must invite')."),
    status: Optional[str] = Query(None, description="Filter by status: not_yet_invited, invited, registered, declined, attended, no_show, follow_up_due, follow_up_complete."),
    company: Optional[str] = Query(None, description="Filter by current_company. Partial match, case-insensitive."),
    min_score: Optional[int] = Query(None, description="Only return prospects with score >= this value."),
    include_excluded: bool = Query(False, description="If true, also return the excluded list (who was filtered out and why)."),
    limit: int = Query(50, ge=1, le=500, description="Max prospects to return (1-500, default 50)."),
    x_api_key: Optional[str] = Header(None),
):
    """Query a campaign's ranked prospect roster — the canonical answer surface
    for 'who should be invited to <conference>', 'who's already registered',
    'show me Tier 1 prospects at <company>', etc. Reads the roster built by
    campaign_engine.py --build-roster; does not recompute it.
    """
    _auth(x_api_key)
    try:
        roster = campaign_engine._load_roster(campaign_id)
        if roster is None:
            raise HTTPException(404, detail=(
                f"No roster exists yet for campaign '{campaign_id}'. Run "
                f"campaign_engine.py --campaign {campaign_id} --build-roster first, "
                f"or check GET /campaigns for known campaign ids."
            ))

        tier_lower = tier.lower() if tier else None
        status_lower = status.lower() if status else None
        company_lower = company.lower() if company else None

        prospects = roster.get("prospects", [])
        filtered = []
        for p in prospects:
            if tier_lower and tier_lower not in (p.get("tier") or "").lower() and tier_lower not in (p.get("tier_label") or "").lower():
                continue
            if status_lower and (p.get("status") or "").lower() != status_lower:
                continue
            if company_lower and company_lower not in (p.get("current_company") or "").lower():
                continue
            if min_score is not None and p.get("score", 0) < min_score:
                continue
            filtered.append(p)

        total_matched = len(filtered)
        out: dict = {
            "contract": "rb_campaign_roster_query_v1",
            "campaign_id": campaign_id,
            "generated_at": roster.get("generated_at"),
            "filter_applied": {
                "tier": tier, "status": status, "company": company,
                "min_score": min_score, "limit": limit,
            },
            "total_matched": total_matched,
            "returned": min(total_matched, limit),
            "prospects": filtered[:limit],
        }
        if include_excluded:
            out["excluded_count"] = len(roster.get("excluded", []))
            out["excluded"] = roster.get("excluded", [])
        return out
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"campaign roster query failed: {exc}") from exc


@app.get("/campaigns/{campaign_id}/company_rollup", tags=["compute"], operation_id="getCampaignCompanyRollup")
def get_campaign_company_rollup(
    campaign_id: str,
    min_contacts: Optional[int] = Query(None, description="Only return companies with at least this many roster contacts (e.g. 3 for 'which companies have 3+ contacts')."),
    x_api_key: Optional[str] = Header(None),
):
    """Company-level coverage view for a campaign — 'which enterprise brands
    have no relationships', 'which companies have 3+ contacts', best contact
    per company, and registration-status coverage."""
    _auth(x_api_key)
    try:
        d = campaign_engine._campaign_dir(campaign_id)
        rollup_path = d / "company_rollup.json"
        if not rollup_path.exists():
            raise HTTPException(404, detail=(
                f"No company rollup exists yet for campaign '{campaign_id}'. "
                f"Run campaign_engine.py --campaign {campaign_id} --build-roster first."
            ))
        rollup = json.loads(rollup_path.read_text(encoding="utf-8"))
        companies = rollup.get("companies", [])
        if min_contacts is not None:
            companies = [c for c in companies if c.get("contact_count", 0) >= min_contacts]
        return {
            "contract": "rb_campaign_company_rollup_query_v1",
            "campaign_id": campaign_id,
            "generated_at": rollup.get("generated_at"),
            "company_count": len(companies),
            "companies": companies,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"campaign company rollup query failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Write endpoints (POST). Each mutates a canonical file; all snapshot first.
# ---------------------------------------------------------------------------

class LoopAddIn(BaseModel):
    party: str
    description: str
    target: str
    opened: Optional[str] = None
    id: Optional[str] = None


class SmartLoopApplyIn(BaseModel):
    """Body for POST /smart_loops/apply.

    confirm must be `true` for the apply to write anything. Per the
    smoke-test-mutations memory and P-009 confirmed-execution discipline,
    this is a second-factor gate beyond auth: the operator (or Custom GPT)
    must state the intent explicitly.
    """
    confirm: bool = False
    date: Optional[str] = None
    proposal_ids: Optional[list[str]] = None


class MeetingPrepWriteIn(BaseModel):
    """Body for POST /meeting_prep/write.

    confirm must be `true` for any file write. event_ids restricts the write
    to specific events; default is all qualifying events for `date`. With
    confirm=false the response includes the rendered markdown preview so the
    caller can show the brief before committing.
    """
    confirm: bool = False
    date: Optional[str] = None
    event_ids: Optional[list[str]] = None


class PassiveVerificationIn(BaseModel):
    """Body for POST /passive_verification.

    Dual-mode (kept as one operation to stay within the 30-op Custom GPT cap):
      * confirm=false (default) — returns the scan result with proposals; no
        mutations.
      * confirm=true — closes the auto_closeable proposals (plus
        possible_resolution proposals when include_possible=true) via
        mutations.cmd_loop_close. Each close snapshots the ledger first.
    """
    confirm: bool = False
    date: Optional[str] = None
    include_possible: bool = False
    loop_ids: Optional[list[str]] = None


class LinkedInMessagingIngestIn(BaseModel):
    """Body for POST /linkedin_messaging/ingest.

    Ingests a LinkedIn export messages.csv (or a directly-supplied messages
    array from a browser-capture flow) into system/inbox/linkedin.messages.json.
    confirm must be true to actually write; confirm=false returns the
    normalized preview shape.
    """
    confirm: bool = False
    path: Optional[str] = None
    messages: Optional[list[dict]] = None
    source: str = "linkedin_data_export"


class PublishIn(BaseModel):
    """Body for POST /publish.

    Dual-mode. confirm=false returns the publish plan + composed email
    (no writes, no sends). confirm=true writes the artifact tree under
    system/published/daily/<date>/ and refreshes latest.html. The email
    send path is intentionally separate — wire transport host-side per
    system/CLAUDE_FOLLOWUP_MORNING_DELIVERY_PIPELINE.md.
    """
    confirm: bool = False
    date: Optional[str] = None


class SourceRefreshIn(BaseModel):
    """Body for POST /sources/refresh.

    Product-level wrapper for the local refresh pipeline. This is intentionally
    narrow and governed: confirm=false returns the exact plan, confirm=true
    refreshes only the requested source families, rebuilds source health and
    relationship-signal cache, and can republish the daily brief.
    """
    confirm: bool = False
    date: Optional[str] = None
    sources: list[str] = ["email", "calendar"]
    publish_brief: bool = True
    full_pipeline: bool = True


class StrategicOperatorMutationIn(BaseModel):
    """Body for POST /strategic_operators/apply.

    Dual-mode per [[rb-dual-mode-post-pattern]]:
      * confirm=false (default) — return a resolved diff (current operator
        state + proposed change) with no writes.
      * confirm=true — call the corresponding mutations.cmd_operator_*
        with snapshot-then-validate-then-rollback discipline (P-009).

    Required: `action` in {add | update | record_movement | close}.
    The `fields` dict carries the action-specific payload (matches the CLI
    flags for that subcommand).
    """
    confirm: bool = False
    action: str
    operator_id: Optional[str] = None
    fields: Optional[dict] = None


class CloseoutIn(BaseModel):
    """Body for POST /closeout.

    Dual-mode (kept as one op within the 30-op Custom GPT cap):
      * confirm=false — return the structured closeout payload; no writes.
      * confirm=true — write the closeout markdown artifact to
        system/closeouts/YYYY-MM-DD.md. The artifact is a projection, not
        canonical state, so no schema validation runs. Existing files are
        snapshotted before overwrite.
    """
    confirm: bool = False
    date: Optional[str] = None


class LoopCloseIn(BaseModel):
    id: str = Field(..., description="A LOOP id from getLoops, e.g. 'L-2026-07-23-003' (format L-YYYY-MM-DD-NNN) -- or an 'EL-' prefixed variant from the same listing; both route automatically. NEVER a contact id (slugified names like 'jeff-coffland', belongs to touchContact) or a thread id (format 'T-YYYY-MM-<slug>', belongs to closeThread) -- touchContact and confirmProposal each had real incidents (18/18 and 5/5 failed calls) from exactly this kind of id mix-up.")
    reason: str


class LoopRedateIn(BaseModel):
    id: str = Field(..., description="A LOOP id from getLoops, e.g. 'L-2026-07-23-003' (format L-YYYY-MM-DD-NNN). Must be an OPEN loop -- redating a closed one fails. Same id-mixup risk as closeLoop: never a contact id or thread id.")
    target: str = Field(..., description="New target date, YYYY-MM-DD.")
    note: Optional[str] = Field(None, description="Optional reason, appended to the loop's description with today's date and the old target -- e.g. 'need to connect for a meeting'.")


class TouchIn(BaseModel):
    id: str = Field(..., description="A CONTACT id from baseline_index.json, e.g. 'jeff-coffland' (a slugified name) -- NEVER a loop id (those look like 'L-2026-08-24-004' and belong to closeLoop, not this). Confirmed live 2026-08-27: 18/18 real touchContact calls failed because a loop id was passed here by mistake. Resolve the correct contact id via getCard first if unsure.")
    date: Optional[str] = None
    source: Optional[str] = None


class ContactAddIn(BaseModel):
    id: str
    name: str
    signal_class: str
    company: Optional[str] = None
    role: Optional[str] = None
    linkedin: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    last_touch: Optional[str] = None
    rc_tier: Optional[str] = None
    source: Optional[str] = None
    notes: Optional[str] = None


class ThreadOpenIn(BaseModel):
    id: str
    title: str
    type: str
    people: list[str] = []
    companies: list[str] = []
    context: str = ""
    current_state: str = ""
    target_close: Optional[str] = None
    boost_for_brief: str = "medium"
    boost_score: float = 1.2


class ThreadCloseIn(BaseModel):
    id: str = Field(..., description="A THREAD id from listActiveThreads, e.g. 'T-2026-05-bridgepoint-ops-engagement' (format T-YYYY-MM-<slug>). NEVER a loop id (format 'L-YYYY-MM-DD-NNN', belongs to closeLoop) or a contact id (belongs to touchContact) -- touchContact and confirmProposal each had real incidents (18/18 and 5/5 failed calls) from exactly this kind of id mix-up.")
    reason: Optional[str] = None


class ManualRelationshipIntakeIn(BaseModel):
    text: str
    contact_id: Optional[str] = None
    name: Optional[str] = None
    event_at: Optional[str] = None
    captured_at: Optional[str] = None
    organization: Optional[str] = None
    opportunity: Optional[str] = None
    apply: bool = False
    business_override: bool = False


class StrategicMemoryRecordIn(BaseModel):
    text: str
    captured_at: Optional[str] = None
    source: Optional[dict] = None


class OpportunityIntakeIn(BaseModel):
    confirm: bool = False
    text: str
    event_at: Optional[str] = None
    captured_at: Optional[str] = None
    contact_id: Optional[str] = None
    contact_name: Optional[str] = None
    contact_role: Optional[str] = None
    referrer_id: Optional[str] = None
    referrer_name: Optional[str] = None
    referral_path: Optional[str] = None
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    company_context: Optional[str] = None
    opportunity_id: Optional[str] = None
    opportunity_title: Optional[str] = None
    opportunity_context: Optional[str] = None
    stage: Optional[str] = None
    materials_received: bool = False
    resume_submitted: bool = False
    prior_food_safety_certification: bool = False
    surecheck_story: bool = False
    strategic_fit: Optional[str] = None
    momentum: Optional[str] = None
    next_expected_action: Optional[str] = None
    domain_tags: Optional[list[str]] = None


class RIEventsReviewIn(BaseModel):
    """Request body for POST /ri_events/review.

    `source_type` is the only strictly-required field. Everything else is
    optional — the per-source pre-processor extracts what's missing from
    `raw_text` when possible. See system/RI_EVENT_INTAKE_DESIGN.md.
    """
    source_type: str  # one of the six known source_types
    raw_text: Optional[str] = None
    summary: Optional[str] = None
    event_at: Optional[str] = None
    event_at_confidence: Optional[str] = None
    event_at_source: Optional[str] = None
    captured_at: Optional[str] = None
    contact_id: Optional[str] = None
    name: Optional[str] = None
    organization: Optional[str] = None
    opportunity: Optional[str] = None
    people: Optional[list] = None
    companies: Optional[list] = None
    signal_type: Optional[str] = None
    proposed_action: Optional[str] = None
    confidence: Optional[float] = None
    trace_id: Optional[str] = None
    source_ref: Optional[dict] = None


class RIEventsConfirmIn(BaseModel):
    """Request body for POST /ri_events/{event_id}/confirm."""
    accept: Optional[list[str]] = None
    reject: Optional[list[str]] = None
    dry_run: bool = False


class FileUploadIn(BaseModel):
    """Universal RB artifact intake contract.

    The interface sends original bytes when available and may also send
    extracted text. RB owns persistence, classification, processing,
    mutation, freshness, and the final receipt.
    """
    filename: str = Field(
        ...,
        description=(
            "Original filename including extension. Used to determine pipeline routing. "
            "Examples: 'Complete_LinkedInDataExport_06-05-2026.zip', "
            "'WhatsApp Chat with Sarah McAngus.txt', 'contacts.vcf'."
        ),
    )
    content_base64: str = Field(
        "",
        description=(
            "Original file bytes encoded as base64. May be empty when "
            "extracted_text is provided."
        ),
    )
    extracted_text: Optional[str] = Field(
        None,
        description="Text extracted from PDFs, Office files, images, links, or other artifacts.",
    )
    source_type: str = Field(
        "file",
        description="file | screenshot | url | transcript | newsletter | article | note",
    )
    source_url: Optional[str] = None
    dry_run: bool = Field(
        False,
        description=(
            "If true, write the file to inbox but do not mutate baseline or ledgers. "
            "Returns a preview of what would be ingested."
        ),
    )
    campaign_id: Optional[str] = Field(
        None,
        description=(
            "Required when the uploaded file is a conference/event registration list "
            "(dataset_type=conference_attendee_list) and more than one campaign exists. "
            "See GET /campaigns for known ids. Ignored for all other file types."
        ),
    )


class ArtifactClassifyIn(BaseModel):
    path: str = Field(
        ...,
        description=(
            "Local path or uploaded-file handle for the artifact. If this is a "
            "LinkedIn export ZIP, classify it and route immediately to "
            "ingestLinkedInExport; do not ask the operator for intended use."
        ),
    )


class LinkedInIngestIn(BaseModel):
    path: str = Field(
        ...,
        description="Local path or uploaded-file handle for the LinkedIn export ZIP.",
    )
    ingest_date: Optional[str] = None
    dry_run: bool = False


class LinkedInSignalIn(BaseModel):
    raw_text: str
    input_type: Optional[str] = None
    source_url: Optional[str] = None
    author_name: Optional[str] = None
    author_role: Optional[str] = None
    author_company: Optional[str] = None
    event_at: Optional[str] = None
    captured_at: Optional[str] = None
    confirm: bool = False


def _ns(**kwargs):
    return type("Ns", (), kwargs)()


@app.post("/loops", tags=["write"], operation_id="addLoop")
def post_loop_add(body: LoopAddIn, x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    for field_name, value in (("target", body.target), ("opened", body.opened)):
        if value is None:
            continue
        try:
            date.fromisoformat(value)
        except ValueError:
            raise HTTPException(400, f"{field_name} must be YYYY-MM-DD, got {value!r}")
    rc = mutations.cmd_loop_add(_ns(
        party=body.party, description=body.description, target=body.target,
        opened=body.opened, id=body.id, dry_run=False,
    ))
    if rc != 0:
        raise HTTPException(400, "loop-add failed")
    return {"ok": True}


@app.get("/linkedin_messaging/overlay", tags=["compute"], operation_id="getLinkedInMessagingOverlay")
def get_linkedin_messaging_overlay(
    recent_days: int = Query(30, description="Lookback window in days."),
    x_api_key: Optional[str] = Header(None),
):
    """Read-only overlay of the LinkedIn messaging inbox.

    Returns direction-aware totals, matched-contact summaries, proposed
    last_touch updates, inbound RI candidates, outbound evidence (used by
    passive_verification), and unmatched recurring participants. When the
    inbox JSON is missing, returns zero-totals — never crashes the brief.
    """
    _auth(x_api_key)
    return linkedin_messaging.linkedin_message_overlay(recent_days=recent_days)


@app.post("/linkedin_messaging/ingest", tags=["write"], operation_id="ingestLinkedInMessaging")
def post_linkedin_messaging_ingest(body: LinkedInMessagingIngestIn,
                                   x_api_key: Optional[str] = Header(None)):
    """Ingest a LinkedIn export messages.csv (or supplied messages array) into the inbox JSON.

    confirm must be true to actually write system/inbox/linkedin.messages.json
    (the prior file is snapshotted before overwrite). With confirm=false the
    response includes a normalized preview. Exactly one of `path` or `messages`
    must be supplied.
    """
    _auth(x_api_key)
    if bool(body.path) == bool(body.messages):
        raise HTTPException(400, "supply exactly one of `path` or `messages`")
    if body.path:
        p = Path(body.path)
        if not p.exists():
            raise HTTPException(400, f"no file at {body.path!r}")
        try:
            normalized = linkedin_messaging.parse_export_csv(p)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"failed to parse CSV: {exc}")
    else:
        normalized = body.messages or []
    result = linkedin_messaging.write_inbox_json(
        normalized, source=body.source, dry_run=not body.confirm,
    )
    # Trim the preview_payload out of the response when not confirmed — it
    # can be very large. Caller can re-request with confirm=true to write.
    if not body.confirm and "preview_payload" in result:
        result["preview_sample"] = (result["preview_payload"].get("messages") or [])[:2]
        del result["preview_payload"]
    return result


@app.post("/publish", tags=["write"], operation_id="publishBrief")
def post_publish(body: PublishIn, x_api_key: Optional[str] = Header(None)):
    """Publish today's brief to system/published/daily/ and compose the email.

    With confirm=false the response is a publish plan + composed email
    preview (no writes, no sends). With confirm=true the artifact tree is
    materialized and latest.html is refreshed. Email send remains separate;
    transport is wired host-side.
    """
    _auth(x_api_key)
    d = date.fromisoformat(body.date) if body.date else date.today()
    report = daily_brief.build_report(d)
    publish_result = publish.publish_brief(today=d, dry_run=not body.confirm, report=report)
    composed = publish.compose_email(report, today=d)
    return {
        "confirmed": bool(body.confirm),
        "publish": publish_result,
        "email": composed,
    }


@app.post("/sources/refresh", tags=["write"], operation_id="refreshSources")
def post_sources_refresh(body: SourceRefreshIn, x_api_key: Optional[str] = Header(None)):
    """Refresh available local sources and optionally regenerate the daily brief.

    Product-level alternative to telling Todd to run shell commands.

    confirm=false returns a plan only. confirm=true writes refreshed caches,
    source-health state, relationship-signals cache, and optionally the
    published daily brief artifacts.
    """
    _auth(x_api_key)
    allowed = {
        "messages": "--messages",
        "calls": "--calls",
        "email": "--email",
        "calendar": "--calendar",
        "social": "--social",
        "market": "--market",
        "all": "--all",
    }
    requested = body.sources or ["email", "calendar"]
    unknown = [s for s in requested if s not in allowed]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown source(s): {', '.join(unknown)}",
        )
    d = date.fromisoformat(body.date) if body.date else date.today()
    flags = [allowed[s] for s in requested]
    if "--all" in flags:
        flags = ["--all"]
    refresh_cmd = [
        sys.executable or "/usr/bin/python3",
        str(SYSTEM_DIR / "scripts" / "refresh_sources.py"),
        *flags,
        "--refresh-signals",
        "--save-health",
        "--json",
    ]
    plan = {
        "sources": requested,
        "full_pipeline": body.full_pipeline,
        "writes": [
            "Google email/calendar inbox files when durable OAuth tokens are present",
            "source caches where local input is available",
            "system/.cache/source_health.json",
            "system/.cache/relationship_signals.json",
        ] + (["system/published/daily artifacts"] if body.publish_brief else []),
        "cannot_do_without_input": [
            "complete first-time Google OAuth consent",
            "capture missing LinkedIn exports or browser-session feeds",
        ],
        "google_oauth": _google_oauth_status(),
    }
    if not body.confirm:
        latest_pipeline = None
        latest_pipeline_path = SYSTEM_DIR / ".cache" / "morning_pipeline.json"
        if latest_pipeline_path.exists():
            try:
                latest_pipeline = json.loads(latest_pipeline_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                latest_pipeline = None
        return {
            "confirmed": False,
            "status": "preview",
            "date": d.isoformat(),
            "plan": plan,
            "last_execution_report": (
                (latest_pipeline or {}).get("execution_report")
                if isinstance(latest_pipeline, dict) else None
            ),
        }

    if body.full_pipeline:
        pipeline_proc = subprocess.run(
            [
                sys.executable or "/usr/bin/python3",
                str(SYSTEM_DIR / "scripts" / "morning_pipeline.py"),
                "--date",
                d.isoformat(),
                "--json",
            ],
            cwd=SYSTEM_DIR.parent,
            capture_output=True,
            text=True,
            timeout=600,
        )
        pipeline_payload = None
        try:
            pipeline_payload = json.loads(pipeline_proc.stdout)
        except Exception:  # noqa: BLE001
            cache_path = SYSTEM_DIR / ".cache" / "morning_pipeline.json"
            if cache_path.exists():
                try:
                    pipeline_payload = json.loads(cache_path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    pipeline_payload = None
        execution_report = (
            (pipeline_payload or {}).get("execution_report")
            if isinstance(pipeline_payload, dict) else None
        )
        refresh_status = (
            (execution_report or {}).get("refresh_status")
            or ("Failed" if pipeline_proc.returncode else "Success")
        )
        summary = f"refreshSources: sources={requested} full_pipeline=True status={refresh_status}"
        if pipeline_proc.returncode == 0:
            al.log_mutation_executed(summary, source="POST /sources/refresh")
        else:
            al.log_mutation_rejected(summary, reason=f"pipeline exit code {pipeline_proc.returncode}")
        return {
            "confirmed": True,
            "status": refresh_status,
            "date": d.isoformat(),
            "execution_report": execution_report,
            "pipeline_exit_code": pipeline_proc.returncode,
        }

    google_fetch_result = None
    needs_google = "--all" in flags or "--email" in flags or "--calendar" in flags
    if needs_google:
        google_proc = subprocess.run(
            [
                sys.executable or "/usr/bin/python3",
                str(SYSTEM_DIR / "scripts" / "fetch_google.py"),
                "both",
                "--account",
                "all",
                "--mailbox",
                "both",
                "--days",
                "14",
                "--no-consent",
            ],
            cwd=SYSTEM_DIR.parent,
            capture_output=True,
            text=True,
            timeout=180,
        )
        google_fetch_result = _public_google_fetch(google_proc)

    proc = subprocess.run(
        refresh_cmd,
        cwd=SYSTEM_DIR.parent,
        capture_output=True,
        text=True,
        timeout=180,
    )
    refresh_payload = None
    try:
        start = proc.stdout.find('{\n  "results":')
        if start >= 0:
            refresh_payload, _ = json.JSONDecoder().raw_decode(proc.stdout[start:])
    except Exception:  # noqa: BLE001
        refresh_payload = None

    publish_result = None
    if body.publish_brief:
        report = daily_brief.build_report(d)
        publish_result = publish.publish_brief(today=d, dry_run=False, report=report)

    source_health = None
    source_health_path = SYSTEM_DIR / ".cache" / "source_health.json"
    if source_health_path.exists():
        try:
            source_health = json.loads(source_health_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            source_health = None
    refresh_status = "completed" if proc.returncode == 0 else "completed_with_refresh_errors"
    summary = f"refreshSources: sources={requested} full_pipeline=False status={refresh_status}"
    if proc.returncode == 0:
        al.log_mutation_executed(summary, source="POST /sources/refresh")
    else:
        al.log_mutation_rejected(summary, reason=f"refresh exit code {proc.returncode}")
    return {
        "confirmed": True,
        "status": refresh_status,
        "date": d.isoformat(),
        "refresh_exit_code": proc.returncode,
        "google_fetch": google_fetch_result,
        "google_oauth": _google_oauth_status(),
        "refreshed_sources": [
            _public_refresh_result(r)
            for r in ((refresh_payload or {}).get("results") or [])
            if isinstance(r, dict)
        ] if isinstance(refresh_payload, dict) else None,
        "source_health_summary": {
            "overall_health": (source_health or {}).get("overall_health"),
            "brief_trustworthiness": (source_health or {}).get("brief_trustworthiness"),
            "stale_or_missing_count": len([
                r for r in ((source_health or {}).get("sources") or {}).values()
                if isinstance(r, dict) and r.get("status") != "refreshed"
            ]),
        },
        "publish": publish_result,
    }


@app.get("/published/{date_str}", tags=["compute"], operation_id="getPublishedBrief")
def get_published_brief(date_str: str, x_api_key: Optional[str] = Header(None)):
    """Return the published brief.json for a given date (if previously written).

    This is the read side of POST /publish: a stable URL for hosted
    deployments. Returns 404 when the artifact tree for `date_str` doesn't
    exist on disk (e.g., publication hasn't run for that day yet).
    """
    _auth(x_api_key)
    try:
        _ = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    p = SYSTEM_DIR / "published" / "daily" / date_str / "brief.json"
    if not p.exists():
        raise HTTPException(404, f"no published brief for {date_str}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"failed to parse published brief: {exc}")


@app.post("/closeout", tags=["write"], operation_id="runCloseout")
def post_closeout(body: CloseoutIn, x_api_key: Optional[str] = Header(None)):
    """End-of-day closeout — bucket counts plus optional artifact write.

    With confirm=false the response carries the structured payload
    (closed_today, slipped, waiting, auto_resolved, carry_forward,
    tomorrow_setup) and a rendered markdown preview. With confirm=true the
    closeout artifact is written to system/closeouts/YYYY-MM-DD.md; an
    existing file at that path is snapshotted before overwrite.
    """
    _auth(x_api_key)
    d = date.fromisoformat(body.date) if body.date else date.today()
    payload = closeout.build_closeout(today=d)
    result = closeout.write_closeout(payload, dry_run=not body.confirm)
    return {
        "confirmed": bool(body.confirm),
        "payload": payload,
        "write_result": result,
    }


@app.post("/strategic_operators/apply", tags=["write"], operation_id="applyOperatorMutation")
def post_strategic_operator_apply(body: StrategicOperatorMutationIn,
                                  x_api_key: Optional[str] = Header(None)):
    """Strategic-operator mutation (add | update | record_movement | close).

    Dual-mode. confirm=false returns a resolved diff with no writes;
    confirm=true runs the corresponding mutations.cmd_operator_* with
    snapshot-then-validate-then-rollback per P-009. Schema validation runs
    against system/schemas/strategic_operators.schema.json on every write;
    failures auto-rollback.
    """
    _auth(x_api_key)
    action = (body.action or "").lower().replace("-", "_")
    if action not in {"add", "update", "record_movement", "close"}:
        raise HTTPException(
            400, f"unknown action {body.action!r}; must be add | update | record_movement | close",
        )
    fields = body.fields or {}

    def _ns(**kw):
        return type("Ns", (), kw)()

    if action == "add":
        ns = _ns(
            id=fields.get("id"),
            name=fields.get("name"),
            entity_type=fields.get("entity_type"),
            watchlist_bucket=fields.get("watchlist_bucket"),
            companies_owned=fields.get("companies_owned"),
            brands=fields.get("brands_in_portfolio") or fields.get("brands"),
            executives=fields.get("executives"),
            relationship_proximity=fields.get("relationship_proximity"),
            notes=fields.get("notes"),
            dry_run=not body.confirm,
        )
        if not ns.id or not ns.name or not ns.entity_type or not ns.watchlist_bucket:
            raise HTTPException(400, "operator-add requires id, name, entity_type, watchlist_bucket")
        rc = mutations.cmd_operator_add(ns)
    elif action == "update":
        ns = _ns(
            id=body.operator_id or fields.get("id"),
            name=fields.get("name"),
            entity_type=fields.get("entity_type"),
            watchlist_bucket=fields.get("watchlist_bucket"),
            relationship_proximity=fields.get("relationship_proximity"),
            add_executive=fields.get("add_executive"),
            add_company=fields.get("add_company"),
            add_brand=fields.get("add_brand"),
            note=fields.get("note"),
            dry_run=not body.confirm,
        )
        if not ns.id:
            raise HTTPException(400, "operator-update requires operator_id or fields.id")
        rc = mutations.cmd_operator_update(ns)
    elif action == "record_movement":
        ns = _ns(
            operator_id=body.operator_id or fields.get("operator_id"),
            id=fields.get("id"),
            event_at=fields.get("event_at"),
            movement_type=fields.get("movement_type"),
            summary=fields.get("summary"),
            source=fields.get("source"),
            source_quality=fields.get("source_quality"),
            source_url=fields.get("source_url"),
            confidence=fields.get("confidence"),
            affected_brands=fields.get("affected_brands"),
            inference=[f"{k}={v}" for k, v in (fields.get("inferences") or {}).items()] or None,
            notes=fields.get("notes"),
            dry_run=not body.confirm,
        )
        if not (ns.operator_id and ns.movement_type and ns.summary and ns.source and ns.confidence):
            raise HTTPException(400,
                "record_movement requires operator_id, movement_type, summary, source, confidence")
        rc = mutations.cmd_operator_record_movement(ns)
    else:  # close
        ns = _ns(
            id=body.operator_id or fields.get("id"),
            reason=fields.get("reason"),
            closed_at=fields.get("closed_at"),
            dry_run=not body.confirm,
        )
        if not ns.id:
            raise HTTPException(400, "operator-close requires operator_id or fields.id")
        rc = mutations.cmd_operator_close(ns)

    if rc != 0:
        raise HTTPException(400, f"operator-{action} failed")
    return {"ok": True, "confirmed": bool(body.confirm), "action": action}


@app.post("/passive_verification", tags=["write"], operation_id="runPassiveVerification")
def post_passive_verification(body: PassiveVerificationIn, x_api_key: Optional[str] = Header(None)):
    """Scan open loops for closure evidence — and (when confirmed) close them.

    `confirm=false` (default) returns the scan result with auto_closeable +
    possible_resolution proposals; no mutations. `confirm=true` closes each
    auto_closeable loop (and possible_resolution too when
    include_possible=true) via mutations.cmd_loop_close, which snapshots the
    ledger first per P-009. `loop_ids` restricts the close set."""
    _auth(x_api_key)
    d = date.fromisoformat(body.date) if body.date else date.today()
    return passive_verification.apply(
        today=d,
        only_ids=body.loop_ids,
        include_possible=bool(body.include_possible),
        confirm=bool(body.confirm),
    )


@app.post("/meeting_prep/write", tags=["write"], operation_id="writeMeetingPrepArtifacts")
def post_meeting_prep_write(body: MeetingPrepWriteIn, x_api_key: Optional[str] = Header(None)):
    """Materialize prep brief artifacts under system/meeting_briefs/.

    `confirm` MUST be true for any file write. With confirm=false the
    response carries the rendered markdown so the caller can preview before
    committing. Each write snapshots any existing file at the target path
    into _snapshots/ before overwrite. Artifacts are projections, not
    canonical state — there is no schema validation step here, by design.
    """
    _auth(x_api_key)
    d = date.fromisoformat(body.date) if body.date else date.today()
    report = daily_brief.build_report(d)
    baseline = core.load_baseline()
    threads = core.load_active_threads()
    events = meeting_prep.collect_candidate_events(report)
    if body.event_ids:
        wanted = set(body.event_ids)
        events = [e for e in events if e.get("id") in wanted]
    results: list[dict] = []
    for ev in events:
        payload = meeting_prep.build_prep_payload(ev, baseline=baseline, threads=threads, today=d)
        result = meeting_prep.write_artifact(payload, dry_run=not body.confirm)
        results.append({
            "event_id": ev.get("id"),
            "title": ev.get("title"),
            **result,
        })
    return {
        "confirmed": bool(body.confirm),
        "count": len(results),
        "results": results,
    }


@app.post("/smart_loops/apply", tags=["write"], operation_id="applySmartLoops")
def post_smart_loops_apply(body: SmartLoopApplyIn, x_api_key: Optional[str] = Header(None)):
    """Open tracked loops for non-deduped smart-loop proposals.

    `confirm` MUST be true for any mutation. With confirm=false the response
    matches the GET shape so the caller can preview exactly what would
    happen. Per P-009, each opened loop goes through mutations.cmd_loop_add,
    which snapshots the ledger before writing. Dedupe runs once during
    proposal generation and again per-proposal at apply time so a loop
    added between calls can't be re-added."""
    _auth(x_api_key)
    d = date.fromisoformat(body.date) if body.date else date.today()
    report = daily_brief.build_report(d)
    return smart_loops.apply_proposals(
        report,
        today=d,
        only_ids=body.proposal_ids,
        confirm=bool(body.confirm),
    )


@app.post("/loops/close", tags=["write"], operation_id="closeLoop")
def post_loop_close(body: LoopCloseIn, x_api_key: Optional[str] = Header(None)):
    """Close one open loop from getLoops -- call this once Todd confirms a
    followed-up-on item is done. RB-2026-08-28: this route had no docstring
    at all (FastAPI fell back to the auto-generated "Post Loop Close"
    summary), leaving the model with zero guidance on the id format -- the
    same undocumented-id-field shape that caused touchContact's 18/18 and
    confirmProposal's 5/5 failed-call incidents. See LoopCloseIn.id's
    description for the id format and what NOT to pass here."""
    _auth(x_api_key)
    if body.id.startswith("EL-"):
        err = eolms.close_by_id(body.id, body.reason)
        if err:
            al.log_mutation_rejected(f"closeLoop: id={body.id}", reason=err)
            raise HTTPException(400, f"loop-close failed: {err}")
        al.log_mutation_executed(f"closeLoop: id={body.id} reason={body.reason}", source="POST /loops/close")
        return {"ok": True, "id": body.id}
    rc = mutations.cmd_loop_close(_ns(id=body.id, reason=body.reason, dry_run=False))
    if rc != 0:
        al.log_mutation_rejected(f"closeLoop: id={body.id}", reason="id not found or already closed")
        raise HTTPException(400, "loop-close failed (id not found or already closed)")
    al.log_mutation_executed(f"closeLoop: id={body.id} reason={body.reason}", source="POST /loops/close")
    return {"ok": True, "id": body.id}


@app.post("/loops/redate", tags=["write"], operation_id="redateLoop")
def post_loop_redate(body: LoopRedateIn, x_api_key: Optional[str] = Header(None)):
    """Move an open loop's target date -- call this once Todd tells you when
    to push a loop to (e.g. "assign to next week", "late September"). RB-
    2026-08-29: mutations.cmd_loop_redate already existed as a working,
    snapshot-before-write CLI command but was never exposed through any API
    route or chat tool -- confirmed live, a real rbb-chat session correctly
    declined to claim it had moved loop target dates it had no actual way
    to persist, rather than fabricate a receipt. This closes that gap."""
    _auth(x_api_key)
    rc = mutations.cmd_loop_redate(_ns(id=body.id, target=body.target, note=body.note, dry_run=False))
    if rc != 0:
        al.log_mutation_rejected(f"redateLoop: id={body.id}", reason="id not found, closed, or invalid target date")
        raise HTTPException(400, "loop-redate failed (id not found, closed, or invalid target date)")
    al.log_mutation_executed(f"redateLoop: id={body.id} target={body.target} note={body.note}", source="POST /loops/redate")
    return {"ok": True, "id": body.id, "target": body.target}


@app.post("/touch", tags=["write"], operation_id="touchContact")
def post_touch(body: TouchIn, x_api_key: Optional[str] = Header(None)):
    """Update last_touch for an existing baseline entry and project the change
    onto the RC card frontmatter when one exists. Returns the full mutation
    result including both canonical and projection outcomes so the caller can
    distinguish "baseline updated, no card" from "baseline updated and card
    re-synced". See P-009 and TOUCHCONTACT-PROJECTION-SYNC-001."""
    _auth(x_api_key)
    try:
        result = mutations.touch_contact(body.id, body.date, body.source)
    except ValueError as exc:
        al.log_mutation_rejected(f"touchContact: id={body.id}", reason=f"touch failed: {exc}")
        raise HTTPException(400, f"touch failed: {exc}")
    except RuntimeError as exc:
        al.log_mutation_rejected(f"touchContact: id={body.id}", reason=f"touch failed: {exc}")
        raise HTTPException(500, f"touch failed: {exc}")
    al.log_mutation_executed(f"touchContact: id={body.id} date={body.date}", source="POST /touch")
    return result


@app.post("/contacts", tags=["write"], operation_id="addContact")
def post_contact_add(body: ContactAddIn, x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    rc = mutations.cmd_contact_add(_ns(
        id=body.id, name=body.name, company=body.company, role=body.role,
        linkedin=body.linkedin, email=body.email, phone=body.phone,
        last_touch=body.last_touch, signal_class=body.signal_class,
        rc_tier=body.rc_tier, source=body.source, notes=body.notes, dry_run=False,
    ))
    if rc != 0:
        raise HTTPException(400, "contact-add failed (id exists or validation error)")
    return {"ok": True, "id": body.id}


@app.post("/threads", tags=["write"], operation_id="openThread")
def post_thread_open(body: ThreadOpenIn, x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    rc = mutations.cmd_thread_open(_ns(
        id=body.id, title=body.title, type=body.type,
        people=body.people, companies=body.companies,
        context=body.context, state=body.current_state,
        target_close=body.target_close, boost_for_brief=body.boost_for_brief,
        boost_score=body.boost_score, dry_run=False,
    ))
    if rc != 0:
        raise HTTPException(400, "thread-open failed (id exists)")
    return {"ok": True, "id": body.id}


@app.post("/threads/close", tags=["write"], operation_id="closeThread")
def post_thread_close(body: ThreadCloseIn, x_api_key: Optional[str] = Header(None)):
    """Close one active strategic thread from listActiveThreads -- call this
    once Todd confirms the thread is resolved. RB-2026-08-28: this route had
    no docstring at all (FastAPI fell back to the auto-generated "Post
    Thread Close" summary), leaving the model with zero guidance on the id
    format -- the same undocumented-id-field shape that caused
    touchContact's 18/18 and confirmProposal's 5/5 failed-call incidents.
    See ThreadCloseIn.id's description for the id format and what NOT to
    pass here."""
    _auth(x_api_key)
    rc = mutations.cmd_thread_close(_ns(id=body.id, reason=body.reason, dry_run=False))
    if rc != 0:
        al.log_mutation_rejected(f"closeThread: id={body.id}", reason="id not found")
        raise HTTPException(400, "thread-close failed (id not found)")
    al.log_mutation_executed(f"closeThread: id={body.id} reason={body.reason}", source="POST /threads/close")
    return {"ok": True, "id": body.id}


@app.post("/manual_relationship_intake", tags=["compute"], operation_id="manualRelationshipIntake")
def post_manual_relationship_intake(
    body: ManualRelationshipIntakeIn,
    x_api_key: Optional[str] = Header(None),
):
    """Classify pasted/screenshot relationship text into canonical RB outputs.

    This is a review-first compute endpoint: it returns structured signals,
    trust/sponsor/advocacy metrics, DRR before/after projection, and proposed
    write operations. It performs zero writes; callers must explicitly execute
    returned safe mutations through the write endpoints after user confirmation.
    """
    _auth(x_api_key)
    return manual_relationship_intake.assess(
        text=body.text,
        contact_id=body.contact_id,
        name=body.name,
        event_at=body.event_at,
        captured_at=body.captured_at,
        organization=body.organization,
        opportunity=body.opportunity,
        apply=body.apply,
        business_override=body.business_override,
    )


@app.post("/strategic_memory", tags=["write"], operation_id="recordStrategicMemory")
def post_strategic_memory(
    body: StrategicMemoryRecordIn,
    x_api_key: Optional[str] = Header(None),
):
    """Detect and persist durable strategic intelligence memory.

    Use when Todd says phrases like "industry intelligence", "note this",
    "this validates my thesis", "watch this", "strategic company", or
    "static memory". Returns the canonical action-language success surface:
    what RB detected, recorded, stored, tagged, and will reuse.
    """
    _auth(x_api_key)
    return strategic_memory.record(
        body.text,
        captured_at=body.captured_at,
        source=body.source,
    )


@app.get("/strategic_memory", tags=["compute"], operation_id="getStrategicMemory")
def get_strategic_memory(
    q: Optional[str] = Query(None, description="Question to answer from persisted strategic memory."),
    limit: int = Query(10, description="Maximum matches to return."),
    x_api_key: Optional[str] = Header(None),
):
    """Retrieve durable strategic intelligence for briefs and assessments."""
    _auth(x_api_key)
    if q:
        return strategic_memory.query(q, limit=limit)
    return strategic_memory.build_report(limit=limit)


@app.post("/opportunity_intake", tags=["write"], operation_id="processOpportunityIntake")
def post_opportunity_intake(
    body: OpportunityIntakeIn,
    x_api_key: Optional[str] = Header(None),
):
    """Process relationship-led opportunity RI with visible artifact mutations.

    `confirm=false` returns the canonical mutation summary without writes.
    `confirm=true` applies safe projections: contact/touch, company artifact,
    opportunity artifact, relationship edge, loops, active thread, and brief.
    """
    _auth(x_api_key)
    return opportunity_intake.process(body.dict(), confirm=bool(body.confirm))


# ----------------------------------------------------------------------
# Known artifact ingestion — LinkedIn ZIP auto-routing.
# ----------------------------------------------------------------------

@app.post(
    "/artifacts/classify",
    tags=["ingest"],
    operation_id="classifyArtifact",
    summary="Classify uploaded artifacts; LinkedIn ZIPs must be ingested",
)
def post_artifact_classify(body: ArtifactClassifyIn, x_api_key: Optional[str] = Header(None)):
    """Classify a local artifact path for automatic routing.

    LinkedIn export ZIPs are known RB baseline-enhancement artifacts. A caller
    receiving `artifact_type=linkedin_export_zip` must immediately invoke
    `/linkedin/ingest` / `ingestLinkedInExport`. Do not ask the operator what
    to do with the file, do not offer examples, and do not produce a generic
    upload menu.
    """
    _auth(x_api_key)
    return linkedin_ingest.classify_archive(body.path)


@app.post(
    "/linkedin/ingest",
    tags=["ingest"],
    operation_id="ingestLinkedInExport",
    summary="Ingest LinkedIn export ZIP as RB baseline enhancement",
)
def post_linkedin_ingest(body: LinkedInIngestIn, x_api_key: Optional[str] = Header(None)):
    """Auto-ingest a LinkedIn export ZIP as RI enhancement input.

    This endpoint is the required next action after `classifyArtifact` returns
    `artifact_type=linkedin_export_zip`. It performs the canonical P-002 route:
    recognize structure,
    parse contents, compare against persisted baseline, write/update the
    baseline and delta report unless `dry_run=true`, and return the
    CoS-level RI enhancement summary with delta evidence, opportunity
    detection, recommended actions, and visualization data.
    """
    _auth(x_api_key)
    try:
        return linkedin_ingest.ingest(
            body.path,
            ingest_date=body.ingest_date,
            dry_run=body.dry_run,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


def _extract_text_from_xlsx(content: bytes) -> str:
    """Flatten an .xlsx/.xlsm workbook into plain text for the generic
    'intelligence' ingest pipeline (unstructured/narrative workbooks, e.g. an
    account plan, that dataset_classifier doesn't recognize as a structured
    dataset type and so don't get routed to micro_graph). Mechanical
    row-by-row extraction via openpyxl only -- never LLM-guessed -- so
    ingestion never fabricates spreadsheet content it can't actually read.
    Returns "" (never raises) on any failure so the caller falls through to
    the existing 'extracted_text is required' error instead of crashing.
    """
    import io as _io
    try:
        import openpyxl
    except ImportError:
        return ""
    try:
        wb = openpyxl.load_workbook(_io.BytesIO(content), data_only=True, read_only=True)
    except Exception:
        return ""
    lines: list[str] = []
    try:
        for ws in wb.worksheets:
            sheet_lines: list[str] = []
            for row in ws.iter_rows(values_only=True):
                cells = [str(c).strip() for c in row if c is not None and str(c).strip() != ""]
                if cells:
                    sheet_lines.append(" | ".join(cells))
            if sheet_lines:
                lines.append(f"## Sheet: {ws.title}")
                lines.extend(sheet_lines)
    except Exception:
        return ""
    return "\n".join(lines).strip()


def _extract_text_from_docx(content: bytes) -> str:
    """Flatten a .docx document (paragraphs + table cells) into plain text for
    the generic 'intelligence' ingest pipeline. Mechanical extraction via
    python-docx only -- never LLM-guessed -- so ingestion never fabricates
    document content it can't actually read. Returns "" (never raises) on any
    failure so the caller falls through to the existing 'extracted_text is
    required' error instead of crashing.
    """
    import io as _io
    try:
        import docx
    except ImportError:
        return ""
    try:
        doc = docx.Document(_io.BytesIO(content))
    except Exception:
        return ""
    lines: list[str] = []
    try:
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                lines.append(text)
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    lines.append(" | ".join(cells))
    except Exception:
        return ""
    return "\n".join(lines).strip()


def _extract_sections_from_docx(content: bytes) -> list[dict]:
    """Split a .docx into (heading, text) sections using python-docx's own
    paragraph style names ("Heading 1".."Heading 9", "Title") -- a real,
    mechanical signal Word itself assigns, not a guessed heuristic. Body
    text before the first heading (if any) is returned under heading=None.
    Table rows are appended to whichever section they follow in document
    order. Returns [] on any failure (mirrors _extract_text_from_docx)."""
    import io as _io
    try:
        import docx
    except ImportError:
        return []
    try:
        doc = docx.Document(_io.BytesIO(content))
    except Exception:
        return []
    sections: list[dict] = []
    current = {"heading": None, "lines": []}
    try:
        for block in doc.element.body:
            if block.tag.endswith("}p"):
                para = next((p for p in doc.paragraphs if p._p is block), None)
                if para is None:
                    continue
                text = para.text.strip()
                if not text:
                    continue
                style = (para.style.name or "") if para.style else ""
                if style.startswith("Heading") or style == "Title":
                    if current["lines"]:
                        sections.append(current)
                    current = {"heading": text, "lines": []}
                else:
                    current["lines"].append(text)
            elif block.tag.endswith("}tbl"):
                table = next((t for t in doc.tables if t._tbl is block), None)
                if table is None:
                    continue
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        current["lines"].append(" | ".join(cells))
        if current["lines"]:
            sections.append(current)
    except Exception:
        return []
    return [{"heading": s["heading"], "text": "\n".join(s["lines"])} for s in sections]


def _extract_pages_from_pdf(content: bytes) -> list[dict]:
    """Per-page text for a .pdf via pypdf -- the one structural signal a PDF
    reliably carries without OCR/layout inference. Returns [] on any
    failure or for a page with no extractable text (mirrors
    _extract_text_from_pdf's page loop)."""
    import io as _io
    try:
        import pypdf
    except ImportError:
        return []
    try:
        reader = pypdf.PdfReader(_io.BytesIO(content))
    except Exception:
        return []
    pages: list[dict] = []
    try:
        for i, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append({"heading": f"Page {i + 1}", "text": text})
    except Exception:
        return []
    return pages


def _extract_text_from_pdf(content: bytes) -> str:
    """Flatten a .pdf document's page text into plain text for the generic
    'intelligence' ingest pipeline. Mechanical extraction via pypdf only --
    never LLM-guessed -- so ingestion never fabricates document content it
    can't actually read. Returns "" (never raises) on any failure, including
    scanned/image-only PDFs with no extractable text layer, so the caller
    falls through to the existing 'extracted_text is required' error instead
    of crashing.
    """
    import io as _io
    try:
        import pypdf
    except ImportError:
        return ""
    try:
        reader = pypdf.PdfReader(_io.BytesIO(content))
    except Exception:
        return ""
    lines: list[str] = []
    try:
        for page in reader.pages:
            text = (page.extract_text() or "").strip()
            if text:
                lines.append(text)
    except Exception:
        return ""
    return "\n".join(lines).strip()


def _diagnose_pdf_extraction_failure(content: bytes) -> str:
    """Best-effort, specific reason _extract_text_from_pdf returned "" --
    called only in that failure case, so a Custom GPT upload gets an
    actionable reason (encrypted / scanned-no-OCR / parsing error) instead
    of one generic 'extracted_text is required' message regardless of
    cause (RB-2026-08-31, Todd's defect report acceptance criteria). Never
    raises; unavailable/undiagnosable falls through to a generic reason."""
    import io as _io
    try:
        import pypdf
    except ImportError:
        return "pypdf not available on this server"
    try:
        reader = pypdf.PdfReader(_io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001
        return f"parsing error -- not a readable PDF ({exc})"
    try:
        if reader.is_encrypted:
            return "encrypted PDF -- cannot extract text without a password"
    except Exception:  # noqa: BLE001
        pass
    try:
        page_count = len(reader.pages)
    except Exception:  # noqa: BLE001
        return "parsing error -- could not enumerate pages"
    if page_count == 0:
        return "PDF has no pages"
    return "scanned image or no extractable text layer (would need OCR)"


@app.post(
    "/ingest/upload",
    tags=["ingest"],
    operation_id="uploadAndIngestFile",
    summary="Accept a file upload from the GPT and route it through the correct ingest pipeline",
)
def post_ingest_upload(body: "FileUploadIn", x_api_key: Optional[str] = Header(None)):
    """Receive a file from the Custom GPT conversation and ingest it.

    DEFECT-024: The user workflow is to upload a file to the Custom GPT.
    This endpoint is the bridge: the GPT reads the uploaded file content,
    encodes it as base64, and POSTs it here. The server writes it to the
    correct inbox directory and runs the appropriate ingest pipeline.

    Structured file routing:
      - LinkedIn export ZIP (.zip)  → inbox/linkedin_exports/ → linkedin_ingest
      - WhatsApp export (.txt/.zip) → inbox/whatsapp_exports/ → whatsapp_ingest
      - Contacts export (.vcf/.csv) → inbox/contacts_exports/ → contacts_ingest
      - HubSpot CRM export (.csv, classified by header vocabulary)
                                    → inbox/crm_exports/ → hubspot_ingest
      - Conference/event registration list (.csv, classified by header
        vocabulary) → inbox/conference_exports/ → campaign_engine reconciliation.
        Requires `campaign_id` in the request if more than one campaign exists
        (see GET /campaigns).
      - Recognized xlsx/xlsm workbook (RB-DEFECT-065: dataset_classifier clears
        the auto-ingest confidence bar, e.g. McDonald's NSN Lookup workbook)
                                    → inbox/micro_graph_sources/ → micro-graph builder
      - Raw audio recording (.m4a/.mp3/.mp4/.wav/.ogg, e.g. a JPR call pasted
        directly into chat instead of picked up by the folder-watch sweep)
                                    → inbox/chat_capture_uploads/ → queued for
        background Whisper transcription via capture_ingest.queue_file, same
        engine scanCaptureSources uses. Returns immediately (real transcription
        takes real time) — call getCaptureScanStatus to check progress, then
        getCapturesPending for the resulting file_id. That file_id (NOT this
        call's ingestion_id — a different ID space) is what getCaptureProcessed/
        submitCapture need.
      - Other artifacts             → inbox/user_artifacts/ → triage + mutations

    Returns a processing receipt with:
      - file_written: path where the file was saved
      - pipeline_run: which ingest pipeline was invoked
      - ingest_result: the full pipeline output (delta counts, mutations, etc.)
      - ingestion_id: deterministic ID for this upload (sha256[:16] of content)

    The receipt can be referenced in the daily brief as proof of processing.
    dry_run=true writes the file but does not mutate baseline — useful for
    previewing what an ingest would produce.

    Raises 413 for a known-large export type (LinkedIn/WhatsApp ZIP) whose
    content never arrived — the payload likely never survived base64-encoding
    into the Action call. The error message names the correct watched inbox
    folder for that file type; relay it verbatim and do not retry, since a
    retry will hit the identical platform-level failure.
    """
    import base64 as _b64
    import hashlib
    from datetime import datetime as _dt, timezone as _tz

    _auth(x_api_key)

    supplied_filename = (body.filename or "upload.bin").strip()
    filename = Path(supplied_filename).name
    if not filename or filename in {".", ".."}:
        raise HTTPException(400, "filename must identify a file.")
    suffix = Path(filename).suffix.lower()

    # RB-DEFECT (2026-08-26): large binary exports (multi-MB LinkedIn/WhatsApp
    # ZIPs) cannot reliably survive base64-encoding into a Custom GPT Action's
    # function-call argument. Confirmed live in two distinct shapes: the
    # payload can arrive completely empty, OR arrive non-empty but corrupted/
    # truncated (fails b64decode) — same underlying platform-level transfer
    # failure either way. Retrying produces the identical failure both times.
    # For known large-export filename patterns, compute the watch-folder
    # fallback up front so both failure shapes below can give the model a
    # grounded, actionable answer to relay verbatim instead of inventing a
    # narrative: fall back to the same watched-folder path the deterministic
    # nightly pipeline (linkedin_export_watcher.py via morning_pipeline.py)
    # already scans and has processed reliably for months.
    watch_dir = None
    if suffix == ".zip" and (
        "linkedin" in filename.lower()
        or "connections" in filename.lower()
        or "linkedindataexport" in filename.lower()
    ):
        watch_dir = "system/inbox/linkedin_exports/"
    elif suffix in (".txt", ".zip") and "whatsapp" in filename.lower():
        watch_dir = "system/inbox/whatsapp_exports/"

    def _large_file_transfer_failure() -> HTTPException:
        return HTTPException(
            413,
            f"'{filename}' did not arrive with usable content — this file is too large "
            "to transfer reliably through a Custom GPT Action (the platform drops or "
            "corrupts the payload before it reaches this endpoint; retrying will fail "
            f"identically). Tell the user: save this file locally to {watch_dir} — the "
            "nightly intelligence-gathering pass (or a manual trigger) will pick it up "
            "and process it automatically the same way it already does. Do not retry "
            "this upload.",
        )

    # Decode content
    content = b""
    if body.content_base64:
        try:
            content = _b64.b64decode(body.content_base64)
        except Exception as exc:
            if watch_dir:
                raise _large_file_transfer_failure()
            raise HTTPException(400, f"content_base64 is not valid base64: {exc}")
    extracted_text = (body.extracted_text or "").strip()
    if not content and not extracted_text:
        if watch_dir:
            raise _large_file_transfer_failure()
        raise HTTPException(400, "Provide content_base64, extracted_text, or both.")

    # Third confirmed shape of the same transfer failure: content decodes
    # successfully but is truncated to a tiny fragment (seen live: 12 bytes
    # for a ~1.9MB real export) — technically valid, non-empty, so it slips
    # past both checks above and would otherwise silently "succeed" with a
    # useless 0-processed/unknown_classification result instead of the
    # actionable watch-folder guidance. A real LinkedIn/WhatsApp export is
    # never anywhere near this small, so treat it the same way.
    _MIN_LARGE_EXPORT_BYTES = 10_240
    if watch_dir and content and len(content) < _MIN_LARGE_EXPORT_BYTES:
        raise _large_file_transfer_failure()

    ingestion_id = hashlib.sha256(
        content or extracted_text.encode("utf-8")
    ).hexdigest()[:16]

    # Route by filename / suffix
    if suffix == ".zip" and (
        "linkedin" in filename.lower()
        or "connections" in filename.lower()
        or "linkedindataexport" in filename.lower()
    ):
        dest_dir = core.INBOX_DIR / "linkedin_exports"
        pipeline = "linkedin"
    elif suffix in (".txt",) and "whatsapp" in filename.lower():
        dest_dir = core.INBOX_DIR / "whatsapp_exports"
        pipeline = "whatsapp"
    elif suffix == ".zip" and "whatsapp" in filename.lower():
        dest_dir = core.INBOX_DIR / "whatsapp_exports"
        pipeline = "whatsapp"
    elif suffix == ".vcf":
        dest_dir = core.INBOX_DIR / "contacts_exports"
        pipeline = "contacts"
    elif suffix == ".json" and content:
        # Structured Top-500 profile research is a record dataset, not one
        # narrative article. Route it before the generic text intelligence
        # parser can collapse hundreds of brands into one false subject.
        import top500_profile_gapfill_ingest as _top500_gapfill
        if _top500_gapfill.classify_bytes(content):
            dest_dir = core.INBOX_DIR / "user_artifacts"
            pipeline = "top500_profile_gapfill"
        else:
            import import_competitor_category_gapfill as _competitor_category
            if _competitor_category.classify_bytes(content):
                dest_dir = core.INBOX_DIR / "user_artifacts"
                pipeline = "competitor_category_gapfill"
            else:
                import import_competitor_platform_research as _competitor_platform
                dest_dir = core.INBOX_DIR / "user_artifacts"
                pipeline = (
                    "competitor_platform_research"
                    if _competitor_platform.classify_bytes(content)
                    else "intelligence"
                )
    elif suffix == ".csv":
        # RB-DEFECT-064/campaign-engine: a plain .csv could be a contacts
        # export, a HubSpot CRM export, or a conference registration list —
        # all look the same at the filename/suffix level. Classify by header
        # vocabulary (dataset_classifier already knows all three signatures)
        # instead of only matching contact/people/address filename keywords,
        # which previously silently misrouted HubSpot/conference CSVs into
        # the generic intelligence/text-triage pipeline.
        structured_classification = dataset_classifier.classify_bytes(content, filename)
        if structured_classification.dataset_type == "hubspot_crm_export" and dataset_classifier.should_auto_ingest(structured_classification):
            dest_dir = core.INBOX_DIR / "crm_exports"
            pipeline = "hubspot"
        elif structured_classification.dataset_type == "conference_attendee_list" and dataset_classifier.should_auto_ingest(structured_classification):
            dest_dir = core.INBOX_DIR / "conference_exports"
            pipeline = "conference"
        elif any(kw in filename.lower() for kw in ("contact", "people", "address")):
            dest_dir = core.INBOX_DIR / "contacts_exports"
            pipeline = "contacts"
        else:
            dest_dir = core.INBOX_DIR / "user_artifacts"
            pipeline = "intelligence"
    elif suffix == ".zip" and any(
        kw in filename.lower() for kw in ("contacts", "abbu", "address book", "addressbook", "vcard")
    ):
        dest_dir = core.INBOX_DIR / "contacts_exports"
        pipeline = "contacts"
    elif suffix == ".zip":
        # Ambiguous ZIP — peek inside to determine type
        import zipfile as _zf, io as _io
        try:
            with _zf.ZipFile(_io.BytesIO(content)) as zf:
                names_lower = [n.lower() for n in zf.namelist()]
                if any("connections" in n or "linkedin" in n for n in names_lower):
                    dest_dir = core.INBOX_DIR / "linkedin_exports"
                    pipeline = "linkedin"
                elif any(n.endswith(".vcf") for n in names_lower) or any(".abbu" in n for n in names_lower):
                    dest_dir = core.INBOX_DIR / "contacts_exports"
                    pipeline = "contacts"
                elif any(n.endswith(".txt") for n in names_lower):
                    dest_dir = core.INBOX_DIR / "whatsapp_exports"
                    pipeline = "whatsapp"
                else:
                    raise HTTPException(
                        400,
                        f"ZIP content not recognized. Found: {names_lower[:10]}. "
                        "Supply a filename that identifies the source (linkedin, whatsapp, contacts)."
                    )
        except _zf.BadZipFile:
            raise HTTPException(400, "File is not a valid ZIP archive.")
    elif suffix in (".xlsx", ".xlsm") and content:
        # RB-DEFECT-065: recognize authoritative structured workbooks (e.g.
        # McDonald's NSN Lookup) instead of always falling through to the
        # generic intelligence/text pipeline, which can't even read xlsx bytes.
        structured_classification = dataset_classifier.classify_bytes(content, filename)
        if structured_classification.dataset_type == "master_account_plan_workbook" and dataset_classifier.should_auto_ingest(structured_classification):
            # RB-2026-08-28: a portfolio-level, multi-account vendor plan
            # (Ranked Portfolio + RM Portfolio sheets) -- recognized and
            # routed here by construction, never depending on the model's
            # judgment. See CANONICAL_REGISTRY.yaml's master_account_plans
            # domain entry for the incident this closes.
            dest_dir = core.INBOX_DIR / "master_account_plan_sources"
            pipeline = "master_account_plan"
        elif dataset_classifier.should_auto_ingest(structured_classification):
            dest_dir = core.INBOX_DIR / "micro_graph_sources"
            pipeline = "micro_graph"
        else:
            dest_dir = core.INBOX_DIR / "user_artifacts"
            pipeline = "intelligence"
    elif suffix in (".m4a", ".mp3", ".mp4", ".wav", ".ogg") and content:
        # RB-2026-08-28: a raw audio recording uploaded through chat (e.g. a
        # JPR call the user pastes in directly instead of it being picked up
        # by the folder-watch sweep) has no text to extract -- it fell
        # through to the generic "intelligence" pipeline below, which always
        # failed with "extracted_text is required" since nothing can supply
        # extracted text for raw audio bytes. Real transcription (whisper_local,
        # via capture_ingest.queue_file) is the only correct path, same
        # engine scanCaptureSources already uses for folder-watched audio.
        dest_dir = core.INBOX_DIR / "chat_capture_uploads"
        pipeline = "capture_audio"
    else:
        dest_dir = core.INBOX_DIR / "user_artifacts"
        pipeline = "intelligence"

    # Write to inbox
    dest_dir.mkdir(parents=True, exist_ok=True)
    # RB-DEFECT (2026-09-13): when the caller supplies only extracted_text
    # (no content_base64 — e.g. the GPT already pulled the text itself and
    # never transferred real bytes), what gets written to disk is plain
    # text, not the original binary file. Keeping the caller-supplied
    # binary extension (.pdf/.docx/etc.) on that plain-text file mislabels
    # it for any downstream code that trusts the extension — a real
    # incident: FSTEC_BuyersGuide_2026 (1).pdf in inbox/user_artifacts/ was
    # plain ASCII text saved with a .pdf extension this way. Save
    # text-only uploads as .txt so the filename tells the truth.
    _TEXT_ONLY_BINARY_SUFFIXES = {
        ".pdf", ".docx", ".xlsx", ".xlsm", ".zip", ".vcf",
        ".m4a", ".mp3", ".mp4", ".wav", ".ogg",
    }
    if not content and extracted_text and suffix in _TEXT_ONLY_BINARY_SUFFIXES:
        filename = f"{Path(filename).stem}.txt"
        suffix = ".txt"
    dest_path = dest_dir / filename
    # If a file with this name already exists and content is identical, skip write
    already_existed = False
    if dest_path.exists():
        existing_hash = hashlib.sha256(dest_path.read_bytes()).hexdigest()[:16]
        if existing_hash == ingestion_id:
            already_existed = True
        else:
            # Different content — suffix the filename to avoid collision
            stem = Path(filename).stem
            dest_path = dest_dir / f"{stem}_{ingestion_id}{suffix}"
    if content and not already_existed:
        dest_path.write_bytes(content)
    elif not content and not already_existed:
        dest_path.write_text(extracted_text, encoding="utf-8")

    file_written = str(dest_path.relative_to(core.PROJECT_DIR))

    # Run the appropriate ingest pipeline
    import sys as _sys
    py = _sys.executable or "python3"
    scripts = SYSTEM_DIR / "scripts"

    ingest_result: dict = {}

    if pipeline == "linkedin":
        # Use linkedin_export_watcher for hash-manifest-aware ingest
        import subprocess as _sub
        cmd = [py, str(scripts / "linkedin_export_watcher.py"), "--ingest-new",
               "--confirm" if not body.dry_run else "--dry-run"]
        proc = _sub.run(cmd, cwd=str(core.PROJECT_DIR), capture_output=True, text=True)
        try:
            ingest_result = json.loads(proc.stdout)
        except Exception:
            ingest_result = {}
        ingest_result["_pipeline_stdout"] = proc.stdout[-800:] if proc.stdout else ""
        ingest_result["_pipeline_returncode"] = proc.returncode
        ingest_result.setdefault("ok", proc.returncode == 0)

    elif pipeline == "top500_profile_gapfill":
        try:
            import top500_profile_gapfill_ingest as _top500_gapfill
            ingest_result = _top500_gapfill.ingest(dest_path, dry_run=body.dry_run)
        except Exception as exc:  # noqa: BLE001
            ingest_result = {"ok": False, "error": str(exc)}

    elif pipeline == "competitor_platform_research":
        try:
            import import_competitor_platform_research as _competitor_platform
            packet = json.loads(content.decode("utf-8-sig"))
            ingest_result = _competitor_platform.import_findings(
                packet, packet_id=packet.get("packet_id") or ingestion_id,
                dry_run=body.dry_run,
            )
            ingest_result["ok"] = True
        except Exception as exc:  # noqa: BLE001
            ingest_result = {"ok": False, "error": str(exc)}

    elif pipeline == "competitor_category_gapfill":
        try:
            import import_competitor_category_gapfill as _competitor_category
            ingest_result = _competitor_category.ingest(dest_path, dry_run=body.dry_run)
        except Exception as exc:  # noqa: BLE001
            ingest_result = {"ok": False, "error": str(exc)}

    elif pipeline == "whatsapp":
        try:
            _sys.path.insert(0, str(scripts))
            import whatsapp_ingest as _wa
            ingest_result = _wa.ingest_file(dest_path, dry_run=body.dry_run)
        except Exception as exc:
            ingest_result = {"ok": False, "error": str(exc)}

    elif pipeline == "contacts":
        try:
            _sys.path.insert(0, str(scripts))
            import contacts_ingest as _ci
            ingest_result = _ci.ingest_file(dest_path, dry_run=body.dry_run)
        except Exception as exc:
            ingest_result = {"ok": False, "error": str(exc)}

    elif pipeline == "hubspot":
        try:
            ingest_result = hubspot_ingest.ingest(dest_path, dry_run=body.dry_run)
        except Exception as exc:
            ingest_result = {"ok": False, "error": str(exc)}

    elif pipeline == "conference":
        try:
            campaign_id = body.campaign_id
            if not campaign_id:
                known = campaign_engine.list_campaigns()
                if len(known) == 1:
                    campaign_id = known[0]["id"]
                else:
                    raise HTTPException(
                        400,
                        "campaign_id is required for a conference registration list "
                        f"when more than one campaign is known ({[c['id'] for c in known]}). "
                        "Call GET /campaigns to see ids, then retry with campaign_id set.",
                    )
            ingest_result = campaign_engine.reconcile_registrations(
                campaign_id, dest_path, dry_run=body.dry_run,
            )
            ingest_result["campaign_id"] = campaign_id
        except HTTPException:
            raise
        except Exception as exc:
            ingest_result = {"ok": False, "error": str(exc)}

    elif pipeline == "micro_graph":
        import subprocess as _sub
        ingest_script_path = core.PROJECT_DIR / structured_classification.ingest_script
        cmd = [py, str(ingest_script_path), str(dest_path), "--json"]
        if not body.dry_run:
            cmd.append("--write")
        proc = _sub.run(cmd, cwd=str(core.PROJECT_DIR), capture_output=True, text=True)
        try:
            ingest_result = json.loads(proc.stdout)
        except Exception:
            ingest_result = {}
        ingest_result["_pipeline_stdout"] = proc.stdout[-800:] if proc.stdout else ""
        ingest_result["_pipeline_stderr"] = proc.stderr[-800:] if proc.stderr else ""
        ingest_result["_pipeline_returncode"] = proc.returncode
        ingest_result.setdefault("ok", proc.returncode == 0)
        ingest_result["dataset_type"] = structured_classification.dataset_type
        ingest_result["classification_confidence"] = structured_classification.confidence

    elif pipeline == "master_account_plan":
        try:
            ingest_result = _master_account_plan_create.ingest_workbook(
                content, filename, dry_run=body.dry_run,
            )
        except Exception as exc:  # noqa: BLE001
            ingest_result = {"ok": False, "error": str(exc)}

    elif pipeline == "capture_audio":
        # Same background-thread discipline as scanCaptureSources -- real
        # local Whisper transcription of even a few short recordings takes
        # well over a minute on CPU, which would blow past the chat
        # orchestrator's tool-call timeout if this blocked the response.
        with _capture_scan_lock:
            if _capture_scan_state["running"]:
                ingest_result = {
                    "ok": True,
                    "status": "scan_already_running",
                    "note": (
                        "A capture scan/transcription is already running. The file has "
                        "been saved and will be picked up on the next scan/queue pass. "
                        "Call getCaptureScanStatus to check on the current one, then "
                        "getCapturesPending once it finishes."
                    ),
                }
            else:
                _capture_scan_state["running"] = True
                _capture_scan_state["started_at"] = _dt.now(_tz.utc).isoformat(timespec="seconds")
                thread = _threading.Thread(
                    target=_run_capture_queue_in_background, args=(dest_path,), daemon=True,
                )
                thread.start()
                ingest_result = {
                    "ok": True,
                    "status": "queued_for_transcription",
                    "note": (
                        "Real local Whisper transcription is running in the background -- "
                        "it is not done yet and there is nothing to summarize from this call. "
                        "Call getCaptureScanStatus in about a minute to check whether it "
                        "finished, then getCapturesPending to find it, then getCaptureProcessed "
                        "or submitCapture using the file_id FROM THAT LIST -- never the "
                        "ingestion_id returned here, which is a different ID space and will "
                        "404 against submitCapture."
                    ),
                }

    elif pipeline == "top500_profile_gapfill":
        counts = ingest_result.get("counts") or {}
        records_added = int(counts.get("profile_fields_added") or 0) + int(counts.get("relationships_added") or 0)
        records_changed = int(counts.get("profile_fields_updated") or 0) + int(counts.get("relationships_updated") or 0)
        mutations_generated = records_added + records_changed
        mutations_applied = 0 if body.dry_run else mutations_generated
    elif pipeline == "competitor_platform_research":
        mutations_generated = int(ingest_result.get("findings_received") or 0)
        mutations_applied = int(ingest_result.get("applied") or 0)
        records_changed = mutations_applied
    elif pipeline == "competitor_category_gapfill":
        counts = ingest_result.get("counts") or {}
        mutations_generated = int(counts.get("category_sets_changed") or 0) + int(counts.get("profile_fields_added") or 0) + int(counts.get("cos_commentary_written") or 0)
        mutations_applied = 0 if body.dry_run else mutations_generated
        records_changed = mutations_applied
    elif pipeline == "intelligence":
        text = extracted_text
        if not text and suffix in {
            ".txt", ".md", ".csv", ".json", ".jsonl", ".html", ".htm",
            ".xml", ".yaml", ".yml",
        }:
            text = content.decode("utf-8-sig", errors="replace").strip()
        # RB-2026-08-28: unstructured/narrative workbooks (e.g. a master
        # account plan) that dataset_classifier doesn't recognize as a known
        # structured type fall through to this generic pipeline, which
        # previously could never read .xlsx bytes at all (RB-DEFECT-065's
        # own comment above already flagged this gap) -- every such upload
        # failed with "extracted_text is required." Real fix: extract it.
        if not text and suffix in {".xlsx", ".xlsm"} and content:
            text = _extract_text_from_xlsx(content)
        # Same gap as the .xlsx case above (RB-2026-08-28): .docx/.pdf never
        # appeared in the suffix-dispatch chain either, so every such upload
        # fell through to this generic pipeline and failed identically.
        if not text and suffix == ".docx" and content:
            text = _extract_text_from_docx(content)
        if not text and suffix == ".pdf" and content:
            text = _extract_text_from_pdf(content)
        if not text:
            extraction_failure_reason = (
                _diagnose_pdf_extraction_failure(content) if suffix == ".pdf" and content else None
            )
            ingest_result = {
                "ok": False,
                "extraction_status": "failed",
                "error": (
                    f"{suffix or 'binary'} artifact retained, but extracted_text "
                    "is required for intelligence processing."
                    + (f" Reason: {extraction_failure_reason}." if extraction_failure_reason else "")
                    # RB-2026-08-31: Todd's defect report acceptance criteria --
                    # the model must not pretend to review document content it
                    # never actually received. Say so explicitly, same directive
                    # pattern as _large_file_transfer_failure above.
                    + " Tell the user extraction failed and why -- do not "
                    "invent, summarize, or guess at this document's content; "
                    "there is no extracted_text to review."
                ),
            }
        else:
            # RB-2026-08-31 (Todd's defect report, Pollo Campero RFP): the
            # full extracted text below was used in-memory for triage/
            # mutations/reference-linking, then discarded -- only a 400-char
            # excerpt ever survived (baked into a Blue Sheet's auto-linked
            # reference evidence). There was no way to retrieve "what does
            # this document actually say" after this one call, so the GPT
            # could never do a section-by-section review of an uploaded
            # response document. Extract real structure where the format
            # mechanically supports it (Word heading styles; PDF pages) --
            # never guessed/LLM-inferred -- and persist the whole thing via
            # getUploadedDocument below.
            if suffix == ".docx" and content:
                doc_sections = _extract_sections_from_docx(content)
            elif suffix == ".pdf" and content:
                doc_sections = _extract_pages_from_pdf(content)
            else:
                doc_sections = []
            _CONTENT_TYPE_BY_SUFFIX = {
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".pdf": "application/pdf",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
            }
            content_type = _CONTENT_TYPE_BY_SUFFIX.get(suffix, "text/plain")
            triage = intelligence_triage.triage_input(
                text,
                source_type=body.source_type,
                source_name=filename,
                captured_at=_dt.now(_tz.utc).isoformat(timespec="seconds"),
                registry=_load_artifact_registry(),
            )
            mutations = intelligence_mutation_engine.run(
                text,
                source_title=filename,
                source_url=body.source_url or "",
                auto_apply=not body.dry_run,
                dry_run=body.dry_run,
            )
            mutation_stats = mutations.get("trust_stats") or {}
            apply_stats = mutations.get("apply_result") or {}

            # RB-2026-08-28: a real internal pricing-call transcript (Pollo
            # Campero, an active Blue Sheet with an RFP due 2026-09-04) went
            # through this pipeline and produced nothing but two generic
            # "vendor mentioned" watchlist blips -- it never connected to the
            # Blue Sheet that's the actual queryable home for that content.
            # Mechanical name/alias match only (never free-text extraction,
            # same fabrication-avoidance discipline as everything else in
            # this pipeline) -- surfaces the connection and appends a
            # fact-free reference pointer so the CoS can proactively flag it
            # rather than staying silent, and Todd/the model can pull the
            # real details in via addBlueSheetEvidence/addCompetitiveNote.
            reference_matches = account_reference_detector.detect_references(text)
            related_artifacts_detected = [
                {"artifact_type": m.artifact_type, "slug": m.slug, "display_name": m.display_name}
                for m in reference_matches
            ]
            if reference_matches and not body.dry_run:
                account_reference_detector.link_references(
                    reference_matches,
                    source_title=filename,
                    source_date=_dt.now(_tz.utc).date().isoformat(),
                    excerpt=text[:400],
                    document_id=ingestion_id,
                )

            uploaded_document_store.save_document(
                ingestion_id,
                filename=filename,
                content_type=content_type,
                extracted_text=text,
                account_links=[m.slug for m in reference_matches],
                sections=doc_sections,
            )
            _today_iso = _dt.now(_tz.utc).date().isoformat()
            intelligence_index.register_document(
                entity=" ".join([Path(filename).stem] + [m.display_name for m in reference_matches]),
                resource_type="uploaded_source_document",
                title=filename,
                path=str(uploaded_document_store.document_path(ingestion_id).relative_to(core.PROJECT_DIR)),
                source_system="uploaded_document_store",
                created_at=_today_iso,
                notes=f"document_id={ingestion_id}; call getUploadedDocument to retrieve the full text.",
            )

            ingest_result = {
                "ok": True,
                "intelligence_detected": not triage.get("noise_only", False),
                "triage": triage,
                "knowledge_mutations": mutations,
                "related_artifacts_detected": related_artifacts_detected,
                "document_id": ingestion_id,
                "extraction_status": "ok",
                "extracted_text_preview": text[:500],
                "retrieval_hint": (
                    f"Call getUploadedDocument(document_id=\"{ingestion_id}\") for the full "
                    "extracted text and, where the format supports it, section/page breakdown."
                ),
                "trust_stats": {
                    "sources_assessed": 1,
                    "items_classified": triage.get("type_count") or 0,
                    "mutations_generated": mutation_stats.get("total_mutations") or 0,
                    "mutations_applied": apply_stats.get("applied") or 0,
                    "confidence": mutation_stats.get("confidence") or "medium",
                    "trust_contract_met": True,
                },
            }

    # Build the processing receipt
    pipeline_ok = bool(ingest_result.get("ok", True))
    records_added = 0
    records_changed = 0
    mutations_generated = 0
    mutations_applied = 0
    opportunities_generated = 0
    if pipeline == "linkedin":
        processed = ingest_result.get("processed") or []
        connection_runs = [
            ((row.get("connections_ingest") or row) if isinstance(row, dict) else {})
            for row in processed
        ]
        latest_connections = connection_runs[-1] if connection_runs else ingest_result
        graph = (
            (latest_connections.get("delta_intelligence") or {}).get("graph_mutations")
            or {}
        )
        opportunities = (
            (latest_connections.get("delta_intelligence") or {}).get("opportunity_detection")
            or {}
        )
        records_added = int(graph.get("baseline_entries_added") or 0)
        records_changed = int(graph.get("baseline_entries_updated") or 0)
        mutations_generated = sum(
            int(graph.get(key) or 0) for key in (
                "relationship_strength_mutations",
                "strategic_importance_mutations",
                "opportunity_graph_mutations",
                "who_matters_now_mutations",
            )
        )
        mutations_applied = mutations_generated
        opportunities_generated = sum(
            len(value) for value in opportunities.values() if isinstance(value, list)
        )
    elif pipeline == "contacts":
        stats = ingest_result.get("trust_stats") or {}
        records_changed = int(stats.get("baseline_mutations") or 0)
        mutations_generated = records_changed
        mutations_applied = 0 if body.dry_run else records_changed
    elif pipeline == "hubspot":
        records_added = int(ingest_result.get("new_people_created") or 0)
        records_changed = int(ingest_result.get("existing_people_updated") or 0)
        mutations_generated = int(ingest_result.get("knowledge_mutations_applied") or 0)
        mutations_applied = 0 if body.dry_run else mutations_generated
    elif pipeline == "conference":
        records_changed = int(ingest_result.get("matched_count") or 0)
        mutations_generated = records_changed
        mutations_applied = 0 if body.dry_run else records_changed
    elif pipeline == "micro_graph":
        counts = ingest_result.get("counts") or {}
        records_added = int(counts.get("nodes") or 0)
        records_changed = int(counts.get("edges") or 0)
        mutations_generated = records_added + records_changed
        mutations_applied = 0 if body.dry_run else mutations_generated
    elif pipeline == "intelligence":
        stats = ingest_result.get("trust_stats") or {}
        mutations_generated = int(stats.get("mutations_generated") or 0)
        mutations_applied = int(stats.get("mutations_applied") or 0)

    receipt = {
        "ingestion_id": ingestion_id,
        "received_at": _dt.now(_tz.utc).isoformat(timespec="seconds"),
        "filename": filename,
        "file_size_bytes": len(content),
        "file_written": file_written,
        "already_existed": already_existed,
        "pipeline_run": pipeline,
        "processing_status": "success" if pipeline_ok else "failed",
        "dry_run": body.dry_run,
        "ingest_result": ingest_result,
        "delta": {
            "records_added": records_added,
            "records_changed": records_changed,
            "mutations_generated": mutations_generated,
            "mutations_applied": mutations_applied,
            "opportunities_generated": opportunities_generated,
        },
        "freshness_recorded": False,
        "brief_rebuilt": False,
        "receipt_note": (
            f"File received and processed via /ingest/upload. "
            f"Pipeline: {pipeline}. "
            f"{'DRY RUN — no baseline mutations written.' if body.dry_run else 'Baseline updated.'} "
            f"Ingestion ID: {ingestion_id}."
        ),
    }

    # Persist receipt to cache for brief reference
    receipt_path = core.CACHE_DIR / "ingest_upload_receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    history_path = core.CACHE_DIR / "ingest_upload_history.jsonl"

    if not body.dry_run:
        artifact_health_path = core.INBOX_DIR / "user_artifacts.json"
        artifact_health = {"generated_at": receipt["received_at"], "items": []}
        if artifact_health_path.exists():
            try:
                prior = json.loads(artifact_health_path.read_text(encoding="utf-8"))
                artifact_health["items"] = list(prior.get("items") or [])
            except Exception:  # noqa: BLE001
                pass
        artifact_health["items"].append({
            "ingestion_id": ingestion_id,
            "filename": filename,
            "pipeline": pipeline,
            "status": receipt["processing_status"],
            "received_at": receipt["received_at"],
        })
        artifact_health["items"] = artifact_health["items"][-200:]
        artifact_health_path.write_text(
            json.dumps(artifact_health, indent=2) + "\n", encoding="utf-8"
        )
        receipt["freshness_recorded"] = True

        refresh_cmd = [py, str(scripts / "refresh_sources.py")]
        if pipeline == "linkedin":
            refresh_cmd.append("--social")
        refresh_cmd.extend(["--save-health", "--json"])
        refresh_proc = subprocess.run(
            refresh_cmd,
            cwd=str(core.PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=180,
        )
        receipt["source_health_refresh_exit_code"] = refresh_proc.returncode

        try:
            report = daily_brief.build_report(date.today())
            publish.publish_brief(today=date.today(), dry_run=False, report=report)
            receipt["brief_rebuilt"] = True
        except Exception as exc:  # noqa: BLE001
            receipt["brief_rebuild_error"] = str(exc)

        # Living Campaign: baseline just changed (new LinkedIn connections,
        # HubSpot contacts, etc.) — refresh every active campaign's roster
        # in place, same best-effort pattern as the daily-brief rebuild
        # above. build_roster() only recomputes non-settled prospects and
        # diffs against the prior roster, so this is an incremental refresh,
        # not a from-scratch regeneration.
        if pipeline in ("linkedin", "hubspot", "contacts"):
            try:
                campaign_results = campaign_engine.refresh_active_campaigns()
                receipt["campaigns_refreshed"] = [
                    {"campaign_id": r.get("campaign_id"), "ok": r.get("ok")}
                    for r in campaign_results
                ]
            except Exception as exc:  # noqa: BLE001
                receipt["campaign_refresh_error"] = str(exc)

    receipt_path.write_text(json.dumps(receipt, indent=2, default=str) + "\n")
    with history_path.open("a", encoding="utf-8") as history:
        history.write(json.dumps(receipt, default=str) + "\n")

    summary = (
        f"uploadAndIngestFile: filename={filename} pipeline={pipeline} "
        f"records_added={records_added} records_changed={records_changed} "
        f"mutations_applied={mutations_applied} dry_run={body.dry_run}"
    )
    if not pipeline_ok:
        al.log_mutation_rejected(summary, reason=str(ingest_result.get("error"))[:300])
    elif body.dry_run:
        al.log_mutation_proposed(summary, source="POST /ingest/upload")
    else:
        al.log_mutation_executed(summary, source="POST /ingest/upload")

    return receipt


class ContactsIngestIn(BaseModel):
    """DEFECT-024d: Contacts file text ingest — VCF or CSV read by GPT Code Interpreter."""
    contacts_text: str = Field(
        ...,
        description=(
            "Full text content of the contacts file. For VCF: read with "
            "open('/mnt/user-data/uploads/contacts.vcf','r',encoding='utf-8').read(). "
            "For CSV: read similarly. Send raw text — do not base64-encode."
        ),
    )
    filename: str = Field(
        "contacts.vcf",
        description="Original filename (.vcf or .csv) — used to select the correct parser.",
    )
    dry_run: bool = Field(False)


class LinkedInExtendedIngestIn(BaseModel):
    """DEFECT-024c: Supplemental LinkedIn export sources — company follows, invitations, endorsements, recommendations."""
    company_follows_csv: Optional[str] = Field(
        None, description="Text of Company Follows.csv from the LinkedIn export ZIP."
    )
    invitations_csv: Optional[str] = Field(
        None, description="Text of Invitations.csv — sent and received connection requests."
    )
    endorsements_csv: Optional[str] = Field(
        None, description="Text of Endorsement_Received_Info.csv."
    )
    recommendations_csv: Optional[str] = Field(
        None, description="Text of Recommendations_Received.csv."
    )
    source_filename: str = Field("linkedin_export", description="Original ZIP filename for source tagging.")
    dry_run: bool = Field(False)


class LinkedInCSVIngestIn(BaseModel):
    """DEFECT-024b: CSV-text ingest body for GPT upload flow."""
    csv_text: str = Field(
        ...,
        description=(
            "Full text content of Connections.csv extracted from the LinkedIn export ZIP. "
            "The GPT should open the ZIP with Code Interpreter, read Connections.csv as text, "
            "and pass the string here. Do not base64-encode — send raw text."
        ),
    )
    source_filename: str = Field(
        "Connections.csv",
        description="Original ZIP filename for source tagging (e.g. Complete_LinkedInDataExport_06-05-2026.zip).",
    )
    ingest_date: Optional[str] = Field(None, description="ISO date override. Defaults to today.")
    dry_run: bool = Field(False, description="If true, compute delta but do not write files.")


@app.post(
    "/contacts/ingest/text",
    tags=["ingest"],
    operation_id="ingestContactsText",
    summary="Ingest Apple Contacts VCF or CSV text (GPT upload flow)",
)
def post_contacts_ingest_text(body: ContactsIngestIn, x_api_key: Optional[str] = Header(None)):
    """Ingest Apple Contacts from raw VCF or CSV text content.

    DEFECT-024d — the correct route when a user uploads a contacts file to the
    Custom GPT. VCF and CSV are plain text, so Code Interpreter can read them
    directly without base64 encoding.

    GPT Code Interpreter steps:
    1. Read the file:
       contacts_text = open('/mnt/user-data/uploads/contacts.vcf', 'r', encoding='utf-8').read()
       (or 'contacts.csv' for CSV exports)
    2. Call ingestContactsText with contacts_text and filename = original filename.

    Returns:
    - total_parsed: contacts found in the file
    - personal_exempt: excluded (family, church, medical, etc.)
    - professional_matched: enriched existing baseline contacts with phone/email
    - phone_enrichments: how many baseline contacts gained a phone number
    - email_enrichments: how many baseline contacts gained an email
    - unresolved: contacts not matched to baseline — need user classification
    - baseline_mutations: number of baseline records updated

    After ingestion, the interaction overlay should be rebuilt to apply the
    new phone numbers to SMS/call matching.
    """
    _auth(x_api_key)
    suffix = Path(body.filename).suffix.lower()
    try:
        import sys as _sys
        scripts = SYSTEM_DIR / "scripts"
        _sys.path.insert(0, str(scripts))
        import contacts_ingest as _ci

        # Write text to inbox so the manifest tracks it
        dest_dir = core.INBOX_DIR / "contacts_exports"
        dest_dir.mkdir(parents=True, exist_ok=True)
        import hashlib as _hash
        content_bytes = body.contacts_text.encode("utf-8")
        ingestion_id = _hash.sha256(content_bytes).hexdigest()[:16]
        dest_path = dest_dir / body.filename
        if dest_path.exists():
            existing_hash = _hash.sha256(dest_path.read_bytes()).hexdigest()[:16]
            if existing_hash != ingestion_id:
                stem = Path(body.filename).stem
                dest_path = dest_dir / f"{stem}_{ingestion_id}{suffix}"
        dest_path.write_bytes(content_bytes)

        result = _ci.ingest_file(dest_path, dry_run=body.dry_run)
        result["ingestion_id"] = ingestion_id
        result["received_at"] = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(timespec="seconds")

        # Trigger interaction overlay rebuild if phone enrichments occurred
        # Check both old-style and new trust_stats structure
        phone_enrich = (
            result.get("phone_enrichments", 0)
            or (result.get("trust_stats") or {}).get("phone_enrichments", 0)
        )
        if not body.dry_run and phone_enrich > 0:
            import subprocess as _sub
            _sub.Popen(
                [_sys.executable, str(scripts / "interaction_overlay.py"), "--cache"],
                cwd=str(core.PROJECT_DIR),
            )
            result["interaction_overlay_rebuild"] = "triggered"

        return result
    except Exception as e:
        raise HTTPException(400, str(e))


class IntelligenceMutateIn(BaseModel):
    """DEFECT-026: Knowledge graph mutation from article/signal text."""
    text: str = Field(..., description="Full article or signal text to extract mutations from.")
    source_title: str = Field("", description="Article headline or source title.")
    source_url: str = Field("", description="Source URL.")
    source_date: str = Field("", description="Publication date (ISO).")
    auto_apply: bool = Field(True, description="Auto-apply high-confidence mutations (≥0.75).")
    dry_run: bool = Field(False, description="Extract mutations but do not write to stores.")


class ContactsXLSXIngestIn(BaseModel):
    """DEFECT-025: XLSX contacts ingest — apple_contacts_baseline_extract.xlsx."""
    content_base64: str = Field(
        ...,
        description=(
            "Base64-encoded XLSX file content. GPT Code Interpreter: "
            "import base64; data = base64.b64encode(open('/mnt/user-data/uploads/FILE.xlsx','rb').read()).decode()"
        ),
    )
    filename: str = Field("apple_contacts_baseline_extract.xlsx")
    dry_run: bool = Field(False)


@app.post(
    "/intelligence/mutate",
    tags=["ingest"],
    operation_id="applyIntelligenceMutations",
    summary="Extract knowledge graph mutations from article text and apply high-confidence ones",
)
def post_intelligence_mutate(body: IntelligenceMutateIn, x_api_key: Optional[str] = Header(None)):
    """Convert article/signal text into durable knowledge graph mutations (DEFECT-026).

    The mutation is the deliverable, not the article.

    Pipeline:
      1. Entity extraction: brands, executives, vendor relationships, executive POVs
      2. Entity resolution: match against ecosystem_intelligence and baseline
      3. Mutation generation: vendor-customer relationships, POVs, thesis validations
      4. Confidence assessment: auto-apply ≥0.75, propose 0.50-0.74
      5. Persistence: ecosystem_intelligence.json, executive_povs.json, mutation log

    Returns:
    - trust_stats: sources_assessed, vendors_detected, exec_mentions, thesis_signals,
      total_mutations, auto_applied, proposed_pending_confirmation
    - mutations: full list with type, description, confidence, requires_confirmation
    - auto_applied_mutations: mutations written to stores
    - proposed_mutations: mutations awaiting user confirmation
    - apply_result: what was actually written

    Call this after ingestContent when the article contains company/executive/vendor
    intelligence. The resulting mutations appear in the next daily brief's
    'Overnight Knowledge Mutations' section.
    """
    _auth(x_api_key)
    try:
        return intelligence_mutation_engine.run(
            body.text,
            body.source_title,
            body.source_url,
            body.source_date,
            auto_apply=body.auto_apply,
            dry_run=body.dry_run,
        )
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post(
    "/contacts/ingest/xlsx",
    tags=["ingest"],
    operation_id="ingestContactsXLSX",
    summary="Ingest Apple Contacts from XLSX spreadsheet (DEFECT-025 identity resolution)",
)
def post_contacts_ingest_xlsx(body: ContactsXLSXIngestIn, x_api_key: Optional[str] = Header(None)):
    """Ingest Apple Contacts from an XLSX spreadsheet (e.g. apple_contacts_baseline_extract.xlsx).

    DEFECT-025 — Recognized artifact type: apple_contacts_export
    Intent: baseline_identity_enrichment

    Runs the full 6-stage identity resolution pipeline:
      Stage 1: Parse XLSX → contacts
      Stage 2: Resolve phone (E.164) → baseline; name → baseline; composite
      Stage 3: Classify: matched_network / probable / unmatched / personal / service
      Stage 4: Reconciliation queue for ambiguous matches
      Stage 5: Persist identity map + exclusion registry + baseline enrichment
      Stage 6: SMS/Messages attribution enabled via identity map

    GPT Code Interpreter:
    import base64
    data = base64.b64encode(open('/mnt/user-data/uploads/apple_contacts_baseline_extract.xlsx','rb').read()).decode()
    Call ingestContactsXLSX with content_base64=data and filename.

    Returns trust stats, classification breakdown, identity map entries,
    reconciliation queue additions, and baseline mutations.
    Automatically triggers interaction overlay rebuild when phones are enriched.
    """
    import base64 as _b64
    import hashlib as _hash
    import subprocess as _sub

    _auth(x_api_key)
    try:
        content = _b64.b64decode(body.content_base64)
    except Exception as exc:
        raise HTTPException(400, f"content_base64 decode error: {exc}")

    try:
        import sys as _sys
        scripts = SYSTEM_DIR / "scripts"
        _sys.path.insert(0, str(scripts))
        import contacts_ingest as _ci

        dest_dir = core.INBOX_DIR / "contacts_exports"
        dest_dir.mkdir(parents=True, exist_ok=True)
        ingestion_id = _hash.sha256(content).hexdigest()[:16]
        dest_path = dest_dir / body.filename
        if dest_path.exists():
            existing = _hash.sha256(dest_path.read_bytes()).hexdigest()[:16]
            if existing != ingestion_id:
                stem = Path(body.filename).stem
                dest_path = dest_dir / f"{stem}_{ingestion_id}.xlsx"
        dest_path.write_bytes(content)

        result = _ci.ingest_file(dest_path, dry_run=body.dry_run)
        result["ingestion_id"] = ingestion_id
        result["artifact_type"] = "apple_contacts_export"
        result["intent"] = "baseline_identity_enrichment"

        ts = result.get("trust_stats") or {}
        if not body.dry_run and ts.get("phone_enrichments", 0) > 0:
            _sub.Popen(
                [_sys.executable, str(scripts / "interaction_overlay.py"), "--cache"],
                cwd=str(core.PROJECT_DIR),
            )
            result["interaction_overlay_rebuild"] = "triggered"

        return result
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post(
    "/linkedin/ingest/extended",
    tags=["ingest"],
    operation_id="ingestLinkedInExtended",
    summary="Ingest supplemental LinkedIn export sources: company follows, invitations, endorsements, recommendations",
)
def post_linkedin_ingest_extended(body: LinkedInExtendedIngestIn, x_api_key: Optional[str] = Header(None)):
    """Process the high-value supplemental datasets from a LinkedIn export.

    DEFECT-024c — these sources are present in every LinkedIn export but were
    previously ignored. Each provides distinct intelligence:

    company_follows_csv   → strategic interest signals; matched against active
                            threads and baseline contacts at followed companies
    invitations_csv       → outbound prospecting (sent) + inbound leads (received);
                            matched against baseline via LinkedIn URL
    endorsements_csv      → relationship warmth signals; recent endorsers from
                            known contacts trigger proposed last_touch mutations
    recommendations_csv   → highest-quality relationship evidence; full text +
                            recommender matched against baseline

    GPT Code Interpreter extraction (run after opening the ZIP):
    ```python
    import zipfile, glob
    zf = zipfile.ZipFile('/mnt/user-data/uploads/Complete_LinkedInDataExport_06-05-2026.zip')
    names = zf.namelist()
    company_follows = zf.read('Company Follows.csv').decode('utf-8-sig')
    invitations = zf.read('Invitations.csv').decode('utf-8-sig')
    endorsements = zf.read([n for n in names if 'Endorsement_Received' in n][0]).decode('utf-8-sig')
    recommendations = zf.read('Recommendations_Received.csv').decode('utf-8-sig')
    ```

    Returns trust stats, per-source intelligence, and proposed baseline mutations.
    Mutations are proposed only — confirm via POST /mutations or mutations.py.
    """
    _auth(x_api_key)
    try:
        return linkedin_ingest_extended.ingest_extended(
            company_follows_csv=body.company_follows_csv,
            invitations_csv=body.invitations_csv,
            endorsements_csv=body.endorsements_csv,
            recommendations_csv=body.recommendations_csv,
            source_filename=body.source_filename,
            dry_run=body.dry_run,
        )
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post(
    "/linkedin/ingest/csv",
    tags=["ingest"],
    operation_id="ingestLinkedInCSV",
    summary="Ingest LinkedIn connections from Connections.csv text (GPT upload flow)",
)
def post_linkedin_ingest_csv(body: LinkedInCSVIngestIn, x_api_key: Optional[str] = Header(None)):
    """Ingest LinkedIn connections from raw Connections.csv text content.

    DEFECT-024b — preferred GPT upload flow. When the user uploads a LinkedIn
    export ZIP to the Custom GPT conversation, the GPT should:

    1. Use Code Interpreter to open the ZIP and read Connections.csv:
       ```python
       import zipfile
       with zipfile.ZipFile('/mnt/user-data/uploads/Complete_LinkedInDataExport_06-05-2026.zip') as zf:
           csv_text = zf.read('Connections.csv').decode('utf-8-sig')
       ```
    2. POST csv_text here with source_filename set to the ZIP's original name.

    This avoids binary encoding of the full ZIP. Returns the same ingest receipt
    as ingestLinkedInExport: baseline delta, new connections, company/role changes,
    conflicts, files written, and recommended actions.
    """
    _auth(x_api_key)
    try:
        return linkedin_ingest.ingest_from_csv_text(
            body.csv_text,
            source_filename=body.source_filename,
            ingest_date=body.ingest_date,
            dry_run=body.dry_run,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/linkedin/signals", tags=["ingest"], operation_id="processLinkedInSignal")
def post_linkedin_signal(body: LinkedInSignalIn, x_api_key: Optional[str] = Header(None)):
    """Classify operator-provided LinkedIn post/screenshot text.

    confirm=false previews extraction, classification, proof stats, and the
    persistence decision. confirm=true records meaningful signals and promotes
    industry items into market_signals for the daily brief.
    """
    _auth(x_api_key)
    return linkedin_freshness_bridge.process(body.dict(), confirm=body.confirm)


# ----------------------------------------------------------------------
# RI event intake — review / recent / confirm.
# These endpoints implement P-021 (RI event sourcing) on top of the
# existing /manual_relationship_intake classifier engine. See
# system/RI_EVENT_INTAKE_DESIGN.md for the full contract.
#
# Intentionally NOT exposed in openapi_gpt.yaml this sprint — the
# auth/confirm story for GPT-driven persistence is still pending.
# Local-only.
# ----------------------------------------------------------------------

@app.post("/ri_events/review", tags=["ri_intake"], operation_id="reviewRIEvent")
def post_ri_events_review(
    body: RIEventsReviewIn,
    x_api_key: Optional[str] = Header(None),
):
    """Review-first RI intake. Captures the input as an immutable RI event
    with persistence_status='proposed_write_pending_confirmation' and
    returns the structured proposed mutation bundle, matched contacts,
    unmatched entities, proposed loops/threads/contacts/briefs, and the
    dedupe decision.

    No canonical state (baseline, threads, loops, briefs) is changed by
    this call. The follow-up `POST /ri_events/{event_id}/confirm` call
    is what actually mutates state once the operator approves the bundle.

    For manual_text inputs that screen as ordinary chat (queries,
    commands, meta talk), this returns ri_scan='not_found' with no
    event written.
    """
    _auth(x_api_key)
    return ri_intake.review(body.model_dump(exclude_none=True))


@app.get("/ri_events/recent", tags=["ri_intake"], operation_id="getRecentRIEvents")
def get_recent_ri_events(
    limit: int = Query(20, ge=1, le=200),
    since: Optional[str] = Query(None, description="ISO timestamp; filters on captured_at >= since"),
    source_type: Optional[str] = Query(None),
    persistence_status: Optional[str] = Query(None),
    x_api_key: Optional[str] = Header(None),
):
    """Read recent RI events from the JSONL stream, newest first.

    The daily brief uses this to surface newly-captured + proposed-
    unconfirmed RI events in the 'RI Events — Last 24 Hours' section.
    """
    _auth(x_api_key)
    events = ri_events.load_events(
        since=since,
        source_type=source_type,
        persistence_status=persistence_status,
        limit=limit,
    )
    return {"events": events, "count": len(events)}


@app.post("/ri_events/{event_id}/confirm", tags=["ri_intake"], operation_id="confirmRIEvent")
def post_ri_events_confirm(
    event_id: str,
    body: RIEventsConfirmIn,
    x_api_key: Optional[str] = Header(None),
):
    """Confirm or reject the proposed mutations attached to an RI event.

    Accept/reject lists carry `op:id` strings drawn from the
    confirmation_required_for list returned by /ri_events/review. Writes
    a follow-up RI event with persistence_status='persisted' (when any
    accepted operation actually wrote) or 'rejected_by_operator' (when
    nothing wrote). When dry_run=True, no canonical state is changed and
    no follow-up event is written.
    """
    _auth(x_api_key)
    try:
        return ri_intake.confirm(
            event_id,
            accept=body.accept or [],
            reject=body.reject or [],
            dry_run=body.dry_run,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))


# ---------------------------------------------------------------------------
# RB 9.27 — Connected Intelligence: Entity Signal Synthesis
# ---------------------------------------------------------------------------

@app.get("/entities/{entity_name}/signals", tags=["compute"], operation_id="getEntitySignals")
def get_entity_signals(
    entity_name: str,
    days: int = Query(default=90, ge=7, le=365, description="Lookback window in days (7–365)."),
    x_api_key: Optional[str] = Header(None),
):
    """Aggregate and synthesize all known RB signals for a named entity.

    Queries active_threads, market_signals, ri_events, passive_ri, artifact
    registry, ecosystem_intelligence, baseline_index, and social_overlay.
    Returns a pattern classification (exit_positioning, growth_mode, distress,
    consolidation, competitive_shift, transition, stable, or unknown) with a
    synthesis hypothesis and opportunity/risk assessment grounded in RB state.

    Call this before generating CoS assessment for any snippet that names a
    company — include the Pattern synthesis section in the CoS assessment
    when signal_count >= 2.
    """
    _auth(x_api_key)
    try:
        result = signal_synthesis.synthesize_entity_signals(
            entity_name,
            lookback_days=days,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"signal synthesis failed: {exc}") from exc


# ---------------------------------------------------------------------------
# RB 9.90 — Company Intelligence File (RB-DEFECT-046 Slice 3)
# ---------------------------------------------------------------------------

@app.get("/entities/{entity_name}/company-intelligence-file", tags=["compute"], operation_id="getCompanyIntelligenceFile")
def get_company_intelligence_file(
    entity_name: str,
    x_api_key: Optional[str] = Header(None),
):
    """Return the persisted Company Intelligence File for a brand entity.

    Renders the brand's tech stack (one entry per tracked category: active
    vendor, sunset with unknown replacement, or unknown) plus any persisted
    `tech_stack_modernization` strategic narrative (confidence,
    supporting_signals, next_expected_signals). Call this before commenting
    on a company's technology/vendor moves so new signals are correlated
    against what RB already knows, not summarized in isolation.
    """
    _auth(x_api_key)
    try:
        return intelligence_mutation_engine.build_company_intelligence_file(entity_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"company intelligence file failed: {exc}") from exc


# ---------------------------------------------------------------------------
# RB 9.29 — Watch List Mutation
# ---------------------------------------------------------------------------

class EarningsWatchlistBody(BaseModel):
    action: Literal["add", "remove", "auto_scan"] = Field(
        ...,
        description=(
            "Mutation action. 'add' adds a company to the earnings calendar watch list. "
            "'remove' removes a company by name or ticker. "
            "'auto_scan' scans market signals for unlisted companies and adds those that "
            "exceed the appearance threshold."
        ),
    )
    name: Optional[str] = Field(
        None,
        description="Company name. Required for action=add or action=remove.",
    )
    ticker: Optional[str] = Field(None, description="Stock ticker symbol (optional for action=add).")
    edgar_cik: Optional[str] = Field(None, description="SEC EDGAR CIK (optional for action=add).")
    ir_rss_url: Optional[str] = Field(None, description="IR press release RSS URL (optional for action=add).")
    side: Optional[str] = Field(
        None,
        description="vendor_supply | operator_demand — which side of the industry graph (default: vendor_supply).",
    )
    strategic_relevance: Optional[str] = Field(
        None,
        description="high | medium | low — signal ranking weight (default: medium).",
    )
    reason: Optional[str] = Field(None, description="Human-readable reason for the mutation.")
    threshold: Optional[int] = Field(
        3,
        description="Minimum appearance count for auto_scan to trigger an add (default: 3).",
    )
    confirm: bool = Field(
        False,
        description=(
            "Set true to apply the mutation. When false (default), returns a preview "
            "with persistence_status=proposed_write_pending_confirmation."
        ),
    )


@app.post("/earnings/watchlist", tags=["write"], operation_id="manageEarningsWatchlist")
def manage_earnings_watchlist(
    body: EarningsWatchlistBody,
    x_api_key: Optional[str] = Header(None),
):
    """Mutate the earnings calendar watch list (RB 9.29).

    Supports three actions:
    - add: Add a named company to watch. name is required. Optional fields:
      ticker, edgar_cik, ir_rss_url, side, strategic_relevance, reason.
    - remove: Remove a company by name or ticker. name is required.
    - auto_scan: Scan all market signal sources for companies that appear
      >= threshold times but are not yet tracked. Adds qualifying companies
      automatically with added_by='cos_auto'.

    Always call with confirm=false first to preview the mutation. Only call
    with confirm=true after the user or pipeline has approved.

    The CoS must call this when:
    - The user says "add [company] to watch list" or "track [company]"
    - The user says "remove [company]" or "stop watching [company]"
    - The daily brief pipeline runs auto-scan (confirm=true, action=auto_scan)
    """
    _auth(x_api_key)

    action = body.action

    if action == "add":
        if not body.name:
            raise HTTPException(status_code=400, detail="name is required for action=add")
        if not body.confirm:
            return {
                "action": "add",
                "company": body.name,
                "proposed": True,
                "persistence_status": "proposed_write_pending_confirmation",
                "message": (
                    f"RB proposes adding {body.name!r} to the earnings calendar watch list. "
                    "Call again with confirm=true to apply."
                ),
            }
        try:
            result = earnings_monitor.add_company(
                body.name,
                ticker=body.ticker,
                edgar_cik=body.edgar_cik,
                ir_rss_url=body.ir_rss_url,
                side=body.side or "vendor_supply",
                strategic_relevance=body.strategic_relevance or "medium",
                reason=body.reason or "",
                added_by="user",
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, detail=f"watch list add failed: {exc}") from exc
        result["persistence_status"] = "persisted" if result.get("ok") else "failed"
        return result

    elif action == "remove":
        if not body.name:
            raise HTTPException(status_code=400, detail="name is required for action=remove")
        if not body.confirm:
            return {
                "action": "remove",
                "company": body.name,
                "proposed": True,
                "persistence_status": "proposed_write_pending_confirmation",
                "message": (
                    f"RB proposes removing {body.name!r} from the earnings calendar watch list. "
                    "Call again with confirm=true to apply."
                ),
            }
        try:
            result = earnings_monitor.remove_company(body.name)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, detail=f"watch list remove failed: {exc}") from exc
        result["persistence_status"] = "persisted" if result.get("ok") else "failed"
        return result

    elif action == "auto_scan":
        threshold = body.threshold if body.threshold is not None else 3
        if not body.confirm:
            try:
                candidates = earnings_monitor.scan_for_watch_candidates(threshold=threshold)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(500, detail=f"watch-scan failed: {exc}") from exc
            return {
                "action": "auto_scan",
                "threshold": threshold,
                "candidates": candidates,
                "candidates_count": len(candidates),
                "proposed": True,
                "persistence_status": "proposed_write_pending_confirmation",
                "message": (
                    f"RB found {len(candidates)} companies above threshold "
                    f"({threshold}+ appearances). Call again with confirm=true to auto-add."
                ),
            }
        try:
            result = earnings_monitor.auto_add_from_signals(threshold=threshold)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, detail=f"auto-add failed: {exc}") from exc
        result["persistence_status"] = "persisted" if result.get("ok") else "failed"
        return result

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action!r}")


# ---------------------------------------------------------------------------
# GET /earnings/watchlist — read the earnings calendar (RB 9.32)
# ---------------------------------------------------------------------------

@app.get("/earnings/watchlist", tags=["compute"], operation_id="getEarningsWatchlist")
def get_earnings_watchlist(
    watch_only: bool = Query(
        default=False,
        description="When true, return only watch_priority=true companies.",
    ),
    x_api_key: Optional[str] = Header(None),
):
    """Return the current earnings calendar watch list.

    Lists every company in system/earnings_calendar.yaml with their
    monitoring configuration (ticker, EDGAR CIK, IR RSS URL, side,
    strategic_relevance, watch_priority).

    Call this when the user asks 'what companies are we tracking?',
    'what is on the earnings watch list?', or to confirm a mutation
    applied correctly.
    """
    _auth(x_api_key)
    try:
        companies = earnings_monitor._load_calendar()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"failed to load earnings calendar: {exc}") from exc
    if watch_only:
        companies = [c for c in companies if c.get("watch_priority")]
    return {
        "companies": companies,
        "total": len(companies),
        "watch_priority_count": sum(1 for c in companies if c.get("watch_priority")),
        "calendar_path": str(earnings_monitor.EARNINGS_CALENDAR_PATH),
        "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# GET /entities/{entity_name}/earnings-history — 2026-08-10 feature request
# ---------------------------------------------------------------------------

@app.get("/entities/{entity_name}/earnings-history", tags=["compute"],
         operation_id="getCompanyEarningsHistory")
def get_company_earnings_history_endpoint(
    entity_name: str,
    limit: int = Query(default=8, ge=1, le=50,
                        description="Max number of past earnings events to return."),
    x_api_key: Optional[str] = Header(None),
):
    """Return this company's durable, cross-quarter earnings-call record.

    The daily/intelligence brief's Section E only ever shows *this cycle's*
    earnings signal. Public earnings calls and reports are among the
    highest-value recurring intelligence RB gathers, so every confirmed
    earnings event (detected via SEC EDGAR 8-K Item 2.02 + EX-99.1, or IR
    RSS) is persisted to system/earnings_history/earnings_calls.jsonl and
    stays queryable indefinitely -- call this whenever a brief item
    references "Full earnings history" for an entity, or when asked to spot
    a trend/storyline across a company's recent quarters (e.g. a signal
    dimension -- Financial/Customer/Technology/Operational/Franchisee --
    that keeps recurring or has gone quiet).

    Each record includes the event date, detected signal dimensions, an
    excerpt from the actual press-release exhibit when retrievable, and
    links to both the SEC filing and the press-release document itself.
    """
    _auth(x_api_key)
    try:
        history = earnings_monitor.get_company_earnings_history(entity_name, limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"failed to load earnings history: {exc}") from exc
    return {
        "entity_name": entity_name,
        "events": history,
        "event_count": len(history),
        "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# RB 9.31 — DEFECT-011: relationship_intake.py API wiring
# ---------------------------------------------------------------------------

class RelationshipIntakeBody(BaseModel):
    text: str = Field(
        ...,
        description=(
            "Raw interaction text — email thread, LinkedIn message, meeting transcript, "
            "recruiter note, or any free-form conversation involving a named person or company."
        ),
    )
    entity_name: Optional[str] = Field(
        None,
        description="Name of the person or company in the interaction. Auto-extracted if omitted.",
    )
    entity_org: Optional[str] = Field(
        None,
        description="Organization of the entity. Auto-extracted if omitted.",
    )
    entity_role: Optional[str] = Field(
        None,
        description="Role/title of the entity. Auto-extracted if omitted.",
    )
    interaction_date: Optional[str] = Field(
        None,
        description="ISO date of the interaction event (event_at). Defaults to today.",
    )
    source_type: Optional[str] = Field(
        "email",
        description=(
            "Source type: email | linkedin_message | linkedin_post | transcript | "
            "sms | call_note | meeting_note | recruiter_email"
        ),
    )
    ecosystem_tags: Optional[list[str]] = Field(
        None,
        description="Explicit ecosystem tag overrides (e.g. ['restaurant_tech', 'pos']).",
    )
    apply: bool = Field(
        False,
        description=(
            "When false (default), returns proposals without writing. "
            "When true, auto-confirms the interaction immediately IF the entity "
            "resolved to an existing baseline contact (exact or high-confidence "
            "fuzzy name match) -- check the response's auto_confirmed field to "
            "see whether that happened. A brand-new, never-seen entity is always "
            "left pending regardless of apply; call confirmRelationshipInteraction "
            "on it after review."
        ),
    )
    business_override: bool = Field(
        False,
        description=(
            "RB-2026-08-28: RB never records personal relationships/content -- "
            "system/personal_relationship_exempt.json lists senders (e.g. Tony Fryer, "
            "Nick Neylon) who are ALWAYS treated as personal unless this specific item is "
            "explicitly flagged otherwise. Set true only when the user has explicitly said "
            "THIS item, despite the sender being on that list, is actually business (e.g. "
            "'that email from Tony Fryer about the conference was business, log it'). Never "
            "set this on your own inference. Has no effect on a topic-based match (fantasy "
            "football, etc.) -- content that is inherently personal doesn't become business "
            "because of who sent it, so the override does not apply there. If the call "
            "returns persistence_status='RB did not persist' with a personal-content note, "
            "that is correct behavior, not an error -- relay it, don't retry with apply=true."
        ),
    )


@app.post(
    "/relationship/intake",
    tags=["compute"],
    operation_id="processRelationshipIntake",
)
def process_relationship_intake(
    body: RelationshipIntakeBody,
    x_api_key: Optional[str] = Header(None),
):
    """Extract and classify relationship intelligence from an interaction thread.

    DEFECT-011 fix — wires relationship_intake.py (RB 9.21) into the API.

    Accepts any raw interaction text (email, LinkedIn message, transcript,
    recruiter note, meeting summary) and returns:
      - interactions:        classified interaction event records
      - mutation_proposals:  review-first contact + ledger mutations
      - cos_surface:         structured CoS output block with trust/DRR signals
      - persistence_status:  always explicit

    Review-first by default (apply=false). When apply=true, safe mutations
    are applied after user review. Always confirm before calling with apply=true.

    Call this when:
    - Todd pastes an email thread, DM, or meeting summary
    - Any input contains a named person + a relationship event
    - triageInput returns ri_event type in its processing order
    """
    _auth(x_api_key)
    try:
        result = relationship_intake.process_relationship_thread(
            text=body.text,
            entity_name=body.entity_name,
            entity_org=body.entity_org,
            entity_role=body.entity_role,
            interaction_date=body.interaction_date,
            source_type=body.source_type or "email",
            ecosystem_tags=body.ecosystem_tags,
            apply=body.apply,
            business_override=body.business_override,
        )
        pstatus = result.get("persistence_status")
        summary = f"processRelationshipIntake: entity={body.entity_name} status={pstatus}"
        if pstatus in ("applied", "RB recorded"):
            al.log_mutation_executed(summary, source="POST /relationship/intake")
        elif pstatus == "pending confirmation":
            al.log_mutation_proposed(summary, source="POST /relationship/intake")
        elif pstatus == "RB did not persist":
            al.log_mutation_rejected(summary, reason="no interaction event detected")
        return result
    except Exception as exc:  # noqa: BLE001
        al.log_mutation_rejected(f"processRelationshipIntake: entity={body.entity_name}", reason=str(exc)[:300])
        raise HTTPException(500, detail=f"relationship intake failed: {exc}") from exc


# ---------------------------------------------------------------------------
# RB 9.31 — DEFECT-012: macro_intelligence.py API wiring
# ---------------------------------------------------------------------------

class MacroSignalBody(BaseModel):
    text: str = Field(
        ...,
        description=(
            "Raw text to classify — LinkedIn post, newsletter excerpt, industry article, "
            "earnings call excerpt, or any market observation text."
        ),
    )
    source_type: Optional[str] = Field(
        "linkedin_post",
        description=(
            "Source type: linkedin_post | newsletter | article | earnings_transcript | "
            "conference_session | social_post | industry_report"
        ),
    )
    author_name: Optional[str] = Field(
        None,
        description="Author name if known — triggers RI mutation path for known contacts.",
    )
    author_org: Optional[str] = Field(
        None,
        description="Author's organization.",
    )
    author_role: Optional[str] = Field(
        None,
        description="Author's role/title.",
    )
    signal_date: Optional[str] = Field(
        None,
        description="ISO date of the signal (defaults to today).",
    )
    ecosystem_tags: Optional[list[str]] = Field(
        None,
        description="Explicit ecosystem tag overrides.",
    )


@app.post(
    "/macro/signal",
    tags=["compute"],
    operation_id="processMacroSignal",
)
def process_macro_signal(
    body: MacroSignalBody,
    x_api_key: Optional[str] = Header(None),
):
    """Classify macro behavioral intelligence signals from industry content.

    DEFECT-012 fix — wires macro_intelligence.py (RB 9.22) into the API.

    Accepts LinkedIn posts, newsletter excerpts, earnings call transcripts,
    or any industry observation text and returns:
      - behavioral_signals:    classified behavioral signal types with confidence
        (consumer_hesitation, affordability_stress, trade_down_behavior,
         emotional_friction, value_perception_shift, operational_pain)
      - behavioral_artifacts:  named behavioral concepts (Parking Lot Hesitation,
         Value Migration Behavior, etc.)
      - entity_risk_mutations:  per-brand risk profile updates (proposed)
      - tech_implications:      downstream restaurant tech intelligence cascade
      - ri_mutation:            RI mutation proposal when author is a known contact
      - daily_brief_layers:     which daily brief sections this signal should enter
      - mutation_proposals:     flat review-first list of all proposed mutations
      - cos_surface:            canonical CoS output block
      - persistence_status:     always explicit

    Call this when:
    - Todd pastes a LinkedIn post or newsletter with market observations
    - triageInput returns macro_signal type in its processing order
    - Any input describes consumer behavior, restaurant traffic, or
      operator/vendor strategic moves
    """
    _auth(x_api_key)
    try:
        result = macro_intelligence.process_macro_signal(
            text=body.text,
            source_type=body.source_type or "linkedin_post",
            author_name=body.author_name,
            author_org=body.author_org,
            author_role=body.author_role,
            signal_date=body.signal_date,
            ecosystem_tags=body.ecosystem_tags,
        )
        pstatus = result.get("persistence_status")
        summary = f"processMacroSignal: source_type={body.source_type} status={pstatus}"
        if pstatus == "RB did not persist":
            al.log_mutation_rejected(summary, reason="no behavioral signal detected")
        else:
            # macro_intelligence's top-level call is proposal-only by design —
            # confirmProposal(kind="macro_record"/"macro_entity") is the actual
            # execution step, logged separately there.
            al.log_mutation_proposed(summary, source="POST /macro/signal")
        return result
    except Exception as exc:  # noqa: BLE001
        al.log_mutation_rejected(f"processMacroSignal: source_type={body.source_type}", reason=str(exc)[:300])
        raise HTTPException(500, detail=f"macro signal processing failed: {exc}") from exc


# ---------------------------------------------------------------------------
# RB-DEFECT-037 — Active Opportunity Pipeline
# ---------------------------------------------------------------------------

class OpportunityUpdateBody(BaseModel):
    text: str = Field(
        ...,
        description=(
            "Free-form text describing a status update on a job, consulting, "
            "advisory, or business opportunity the user is personally pursuing — "
            "e.g. 'I received a verbal offer from Acme', 'Acme confirmed I'm one "
            "of the final two candidates, interviews June 23-24', 'I declined "
            "the offer from Acme'."
        ),
    )
    company: Optional[str] = Field(
        None,
        description=(
            "Company/organization name, if not reliably extractable from text. "
            "Strongly recommended — without it, RB may not persist the update."
        ),
    )
    role: Optional[str] = Field(
        None,
        description="Role/title under discussion, if known.",
    )
    stage: Optional[str] = Field(
        None,
        description=(
            "Explicit pipeline stage override. One of: target_identified, applied, "
            "screening, interviewing, final_round, offer_verbal, offer_written, "
            "negotiating, accepted, declined, rejected, closed. If omitted, RB "
            "infers the stage from the text."
        ),
    )
    source_type: str = Field(
        "conversation",
        description="Source type: conversation | email | linkedin_message | meeting_note",
    )
    apply: bool = Field(
        False,
        description=(
            "When false (default), returns a review-first proposal without writing. "
            "When true, persists immediately to tracked_opportunities.json. "
            "Confirm with the user before calling with apply=true unless the user "
            "has explicitly stated this update as fact (e.g. 'I just accepted the offer')."
        ),
    )


@app.post(
    "/opportunity/update",
    tags=["compute"],
    operation_id="processOpportunityUpdate",
)
def process_opportunity_update(
    body: OpportunityUpdateBody,
    x_api_key: Optional[str] = Header(None),
):
    """Record a status update to the user's active opportunity pipeline.

    RB-DEFECT-037 fix — closes the gap where job-search/career-pipeline status
    updates (verbal offers, candidate ranking, interview timelines, deal stage)
    had nowhere to persist. relationship_intake captures generic contact
    touches; insight_intake captures market thesis; job_intelligence is a
    read-only posting scanner. None of these capture "my own pipeline status
    changed" — this endpoint does.

    Returns:
      - detected:            whether a pipeline-stage/company signal was found
      - opportunity:         the proposed/updated opportunity record
      - mutation_proposals:  review-first upsert against tracked_opportunities.json
      - persistence_status:  "applied" | "pending confirmation" | "RB did not persist"
      - what_changed:        human-readable diff for "what changed since yesterday" framing

    Always pass `company` explicitly when it can be identified — auto-extraction
    is best-effort only. If `company` cannot be resolved, RB will not persist.

    Call this when:
    - Todd reports a change in status on a job, consulting, or advisory
      opportunity he is personally pursuing (offer received, interview
      scheduled, candidate ranking, accepted/declined/rejected)
    - triageInput returns career_pipeline_update type in its processing order
    """
    _auth(x_api_key)
    try:
        ctx = _job_intel._load_opportunity_context() if _HAS_JOB_INTEL else {}
        known_companies = (
            (ctx.get("job_search_context") or {}).get("target_companies") or []
        )
        result = opportunity_pipeline.process_opportunity_update(
            text=body.text,
            company=body.company,
            role=body.role,
            stage_override=body.stage,
            source_type=body.source_type or "conversation",
            apply=body.apply,
            known_companies=known_companies,
        )
        pstatus = result.get("persistence_status")
        summary = f"processOpportunityUpdate: company={body.company} stage={body.stage} status={pstatus}"
        if pstatus == "applied":
            al.log_mutation_executed(summary, source="POST /opportunity/update")
        elif pstatus == "pending confirmation":
            al.log_mutation_proposed(summary, source="POST /opportunity/update")
        elif pstatus == "RB did not persist":
            al.log_mutation_rejected(summary, reason="company could not be resolved")
        return result
    except Exception as exc:  # noqa: BLE001
        al.log_mutation_rejected(f"processOpportunityUpdate: company={body.company}", reason=str(exc)[:300])
        raise HTTPException(500, detail=f"opportunity update failed: {exc}") from exc


@app.get(
    "/opportunity/pipeline",
    tags=["compute"],
    operation_id="getOpportunityPipeline",
)
def get_opportunity_pipeline(
    active_only: bool = Query(True, description="Exclude accepted/declined/rejected/closed opportunities."),
    x_api_key: Optional[str] = Header(None),
):
    """Return the user's tracked active opportunity pipeline.

    RB-DEFECT-037 — query surface for tracked_opportunities.json. Each
    opportunity includes company, role, stage, candidate_position, key_dates,
    status_narrative, and a full history of recorded updates.

    Call this when:
    - Todd asks "what's the status of my Global Payments / Foods Connected
      process?" or "where do things stand with my job search?"
    - Building the daily brief's career pipeline section
    """
    _auth(x_api_key)
    try:
        return opportunity_pipeline.query_pipeline(active_only=active_only)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"opportunity pipeline query failed: {exc}") from exc


@app.get(
    "/accounts",
    tags=["compute"],
    operation_id="listBlueSheetAccounts",
)
def list_blue_sheet_accounts(x_api_key: Optional[str] = Header(None)):
    """List every Blue Sheet account dossier (restaurant-brand accounts under
    active pursuit, e.g. Pollo Campero, Five Guys, Del Taco).

    RB-2026-08-27 — closes a real gap: getOpportunityPipeline is Todd's
    personal job-search pipeline, unrelated to sales/account opportunities
    like Pollo Campero's RFP. There was no way to even discover which
    accounts have a Blue Sheet dossier, let alone query one.

    Call this when Todd asks about the status of a restaurant-brand account,
    deal, or opportunity that isn't a job-search process — e.g. "what's
    going on with Pollo Campero" — to find the right account_slug for
    getAccountStatus, or when he asks what accounts RB is tracking.
    """
    _auth(x_api_key)
    if _blue_sheet_common is None:
        raise HTTPException(500, detail="Blue Sheet engine not available on this server.")
    try:
        registry = _blue_sheet_common.load_registry()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"blue sheet registry read failed: {exc}") from exc
    return {"accounts": registry.get("registry", [])}


@app.get(
    "/accounts/{account_slug}",
    tags=["compute"],
    operation_id="getAccountStatus",
)
def get_account_status(account_slug: str, x_api_key: Optional[str] = Header(None)):
    """Return the full Blue Sheet dossier for one restaurant-brand account —
    opportunity stage and key dates, buying influences, open/overdue
    actions, technology stack, and the latest strategic review.

    Call this for "what's the status of [account]" / "where do things stand
    with [deal]" questions about a restaurant-brand account under active
    pursuit (e.g. Pollo Campero) — not for Todd's own job-search pipeline
    (that's getOpportunityPipeline). Use listBlueSheetAccounts first if you
    don't already know the exact account_slug (e.g. "pollo-campero").
    """
    _auth(x_api_key)
    if _blue_sheet_common is None:
        raise HTTPException(500, detail="Blue Sheet engine not available on this server.")
    try:
        _blue_sheet_common.account_dir(account_slug)
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No Blue Sheet account found for slug '{account_slug}'. Call listBlueSheetAccounts to see valid slugs.")
    try:
        dossier = _blue_sheet_common.load_account(account_slug)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"blue sheet account read failed: {exc}") from exc
    return dossier


@app.get(
    "/master-account-plans",
    tags=["compute"],
    operation_id="listMasterAccountPlans",
)
def list_master_account_plans(x_api_key: Optional[str] = Header(None)):
    """List every Master Account Plan RB is tracking -- a VENDOR/PARTNER-
    scoped, multi-account portfolio plan (e.g. Worldpay's own internal
    RM-organized view across 30 of their customer accounts), NOT one of
    Todd's own single-account Blue Sheets or Account Research records.

    RB-2026-08-28: built after a real incident where a document exactly
    like this (Ranked Portfolio + RM Portfolio sheets, organized by a
    vendor's own relationship managers across many accounts) was
    misclassified and routed into createBlueSheetAccount, producing an
    empty, falsely-authorized Blue Sheet. If the user's document or
    question is about a vendor's/partner's OWN account portfolio and their
    RM structure (not Todd's target account plan), this is the right
    domain -- never createBlueSheetAccount or generateAccountBackgroundBrief.
    """
    _auth(x_api_key)
    if _master_account_plan_create is None:
        raise HTTPException(500, detail="Master Account Plan engine not available on this server.")
    try:
        registry = _master_account_plan_create.common.load_registry()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"master account plan registry read failed: {exc}") from exc
    return {"plans": registry.get("registry", [])}


@app.get(
    "/master-account-plans/{vendor_slug}",
    tags=["compute"],
    operation_id="getMasterAccountPlan",
)
def get_master_account_plan(vendor_slug: str, x_api_key: Optional[str] = Header(None)):
    """Return a Master Account Plan: the vendor's RM roster, ranked
    portfolio (framed as "where should a rep spend their time"), any
    review items awaiting Todd's input, and a chat-readable digest.

    Use listMasterAccountPlans first if you don't already know the exact
    vendor_slug (e.g. 'worldpay'). Not the same as getAccountStatus (a
    single Blue Sheet account) or getAccountResearch (pre-engagement
    research on one brand) -- this is one vendor's portfolio across many.
    """
    _auth(x_api_key)
    if _master_account_plan_create is None:
        raise HTTPException(500, detail="Master Account Plan engine not available on this server.")
    common_mod = _master_account_plan_create.common
    vendor_dir = common_mod.ROOT / "vendors" / vendor_slug
    if not vendor_dir.exists():
        raise HTTPException(404, detail=f"No Master Account Plan found for vendor slug '{vendor_slug}'. Call listMasterAccountPlans to see valid slugs.")
    try:
        plan = common_mod.load_json(vendor_dir / "plan.json")
        ranked_portfolio = common_mod.load_json(vendor_dir / "ranked_portfolio.json")
        rm_portfolios = common_mod.load_json(vendor_dir / "rm_portfolios.json")
        review_queue = common_mod.load_review_queue()
        pending_reviews = [
            r for r in review_queue.get("pending_reviews", [])
            if r.get("vendor_slug") == vendor_slug and r.get("status") == "pending"
        ]
        digest_markdown = _master_account_plan_create.render_digest_markdown(vendor_slug)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"master account plan read failed: {exc}") from exc
    return {
        "plan": plan,
        "ranked_portfolio": ranked_portfolio,
        "rm_portfolios": rm_portfolios,
        "pending_reviews": pending_reviews,
        "digest_markdown": digest_markdown,
    }


class MasterAccountPlanIngestBody(BaseModel):
    display_name: str = Field(..., description="e.g. 'Worldpay'")
    ranked_portfolio: list = Field(..., description="Curated ranked-account rows -- see master_account_plans/_engine/parse_workbook.py's shape.")
    rm_portfolios: list = Field(default_factory=list, description="Curated RM roster rows.")
    user_authorization_quote: str = Field(
        ..., min_length=1,
        description=(
            "REQUIRED. A verbatim quote of what the user actually said "
            "authorizing this curated (not-from-a-raw-upload) content. "
            "This path is for RBB-curated content only -- if you have a "
            "real .xlsx workbook, use uploadAndIngestFile instead, which "
            "routes automatically and needs no authorization quote."
        ),
    )


@app.post(
    "/master-account-plans/{vendor_slug}/ingest",
    tags=["write"],
    operation_id="ingestMasterAccountPlanUpload",
)
def post_ingest_master_account_plan(
    vendor_slug: str, body: MasterAccountPlanIngestBody,
    x_api_key: Optional[str] = Header(None),
):
    """Programmatic, curated-content path for a Master Account Plan --
    NOT for a real uploaded workbook (use uploadAndIngestFile for that; it
    recognizes and routes a Ranked-Portfolio+RM-Portfolio-shaped .xlsx
    automatically). This path requires explicit authorization because
    curated-from-scratch content carries the same fabrication risk
    createBlueSheetAccount's own guard exists for.
    """
    _auth(x_api_key)
    if _master_account_plan_create is None:
        raise HTTPException(500, detail="Master Account Plan engine not available on this server.")
    try:
        result = _master_account_plan_create.ingest_curated_update(
            vendor_slug, body.display_name,
            ranked_portfolio=body.ranked_portfolio, rm_portfolios=body.rm_portfolios,
            user_authorization_quote=body.user_authorization_quote,
        )
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"master account plan ingest failed: {exc}") from exc
    return result


@app.post(
    "/master-account-plans/{vendor_slug}/review/{evidence_id}/resolve",
    tags=["write"],
    operation_id="resolveMasterAccountPlanReviewItem",
)
def post_resolve_master_account_plan_review_item(
    vendor_slug: str, evidence_id: str, resolution: str,
    x_api_key: Optional[str] = Header(None),
):
    """Todd resolves one flagged review item (a suggested Score/Tier
    change from a material signal) -- marks it resolved so it stops
    appearing as pending. This does NOT itself change ranked_portfolio.json
    scores/tiers; that stays a human edit Todd makes directly, same
    principle as Blue Sheet's buying-influence ratings staying human-only.
    `resolution` should be a short note of what Todd decided (e.g.
    'reviewed, no change needed' or 'bumped to Tier 1, see chat').
    """
    _auth(x_api_key)
    if _master_account_plan_create is None:
        raise HTTPException(500, detail="Master Account Plan engine not available on this server.")
    common_mod = _master_account_plan_create.common
    review_queue = common_mod.load_review_queue()
    matched = False
    for item in review_queue.get("pending_reviews", []):
        if item.get("vendor_slug") == vendor_slug and item.get("evidence_id") == evidence_id and item.get("status") == "pending":
            item["status"] = "resolved"
            item["resolution"] = resolution
            item["resolved_at"] = common_mod.now_iso()
            matched = True
    if not matched:
        raise HTTPException(404, detail=f"No pending review item found for vendor_slug='{vendor_slug}', evidence_id='{evidence_id}'.")
    common_mod.save_json(common_mod.review_queue_path(), review_queue)
    return {"ok": True, "vendor_slug": vendor_slug, "evidence_id": evidence_id, "resolution": resolution}


@app.get(
    "/intelligence/downstream-impacts",
    tags=["compute"],
    operation_id="listDownstreamIntelligenceImpacts",
)
def list_downstream_intelligence_impacts(status: Optional[str] = "pending_review",
                                         x_api_key: Optional[str] = Header(None)):
    """List accountable downstream work created from material intelligence ramifications."""
    _auth(x_api_key)
    return {"impacts": downstream_impact_queue.list_items(status=status)}


class ResolveDownstreamImpactBody(BaseModel):
    decision: Literal["approve_manual_action", "reject"]
    resolution: str = Field(..., min_length=1)


@app.post(
    "/intelligence/downstream-impacts/{impact_id}/resolve",
    tags=["write"],
    operation_id="resolveDownstreamIntelligenceImpact",
)
def resolve_downstream_intelligence_impact(impact_id: str, body: ResolveDownstreamImpactBody,
                                           x_api_key: Optional[str] = Header(None)):
    """Resolve a judgment-heavy downstream recommendation. Approval records
    the decision but does not silently perform the separate artifact edit."""
    _auth(x_api_key)
    try:
        return downstream_impact_queue.resolve(
            impact_id, decision=body.decision, resolution=body.resolution)
    except KeyError:
        raise HTTPException(404, detail=f"No pending downstream impact found for '{impact_id}'.")
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))


@app.get(
    "/routine-research/reviews",
    tags=["compute"],
    operation_id="listRoutineResearchReviews",
)
def list_routine_research_reviews(status: str = "pending_review", x_api_key: Optional[str] = Header(None)):
    """List routine-research evidence awaiting review. These are sourced
    evidence records, not canonical field changes."""
    _auth(x_api_key)
    return {"reviews": routine_research_review.list_reviews(status=status)}


class ResolveRoutineResearchReviewBody(BaseModel):
    decision: Literal["approve_evidence", "reject"]
    resolution: str = Field(..., min_length=1, description="Todd's actual review decision or rationale.")


@app.post(
    "/routine-research/reviews/{evidence_id}/resolve",
    tags=["write"],
    operation_id="resolveRoutineResearchReview",
)
def resolve_routine_research_review(evidence_id: str, body: ResolveRoutineResearchReviewBody,
                                    x_api_key: Optional[str] = Header(None)):
    """Approve sourced research evidence into the entity's canonical evidence
    ledger, or reject it. Approval does not silently rewrite a leadership,
    ownership, financial, or strategy field; it flags the account brief for
    evidence-backed regeneration and records the mutation lifecycle receipt."""
    _auth(x_api_key)
    try:
        return routine_research_review.resolve(
            evidence_id, decision=body.decision, resolution=body.resolution)
    except KeyError:
        raise HTTPException(404, detail=f"No pending routine-research review found for '{evidence_id}'.")
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))


@app.get(
    "/team-profile-submissions",
    tags=["compute"],
    operation_id="listTeamProfileSubmissions",
)
def get_team_profile_submissions(
    status: str = "pending",
    target_type: Optional[str] = None,
    x_api_key: Optional[str] = Header(None),
):
    """List Team Portal-submitted corrections to brand/competitor profiles
    (see team_profile_submissions.py) -- Todd's own review surface,
    deliberately outside Team Portal itself (only he holds RB_API_KEY).
    status defaults to "pending", the actionable queue; pass "confirmed",
    "rejected", or "all" to see history. A submission is never applied to
    the live brand/competitor record until resolveTeamProfileSubmission
    confirms it."""
    _auth(x_api_key)
    status_filter = status if status and status != "all" else None
    return {"submissions": tps.list_submissions(status=status_filter, target_type=target_type)}


class ResolveTeamProfileSubmissionBody(BaseModel):
    decision: Literal["confirm", "reject"]
    review_note: str = Field("", description="Optional note on why this was confirmed or rejected.")


@app.post(
    "/team-profile-submissions/{submission_id}/resolve",
    tags=["write"],
    operation_id="resolveTeamProfileSubmission",
)
def post_resolve_team_profile_submission(
    submission_id: str, body: ResolveTeamProfileSubmissionBody, x_api_key: Optional[str] = Header(None),
):
    """Confirm writes the proposed value into the live brand or competitor
    profile record (last_reviewed_by stamped "human:todd" -- indistinguishable
    in storage afterward from any other human-confirmed fact, per
    brand_profile_common.is_human_reviewed()). Reject leaves the live
    record untouched. Either way the submission stays in the queue,
    status updated, never deleted -- call listTeamProfileSubmissions with
    status="rejected" or "confirmed" to see the audit trail."""
    _auth(x_api_key)
    try:
        if body.decision == "confirm":
            return tps.confirm(submission_id, reviewed_by="todd", review_note=body.review_note)
        return tps.reject(submission_id, reviewed_by="todd", review_note=body.review_note)
    except tps.NotFoundError as exc:
        raise HTTPException(404, detail=str(exc))
    except tps.AlreadyReviewedError as exc:
        raise HTTPException(409, detail=str(exc))


@app.get(
    "/account-research",
    tags=["compute"],
    operation_id="listAccountResearch",
)
def list_account_research(x_api_key: Optional[str] = Header(None)):
    """List every Account Background Brief / pre-engagement research record
    RB has started on a brand.

    NOT the same thing as listBlueSheetAccounts. A Blue Sheet is the plan
    used DURING an active engagement. An Account Background Brief is
    upstream of that -- digging into a brand from public information and
    RBB's own account research, to build a plan BEFORE discovery starts.
    It can feed facts into a Blue Sheet once an engagement begins, but
    never requires one to exist. Call this to find the right slug for
    getAccountResearch, or when asked what accounts RB has researched.
    """
    _auth(x_api_key)
    registry = cpc.load_registry()
    return {"accounts": registry.get("registry", [])}


@app.get(
    "/account-research/{account_slug}",
    tags=["compute"],
    operation_id="getAccountResearch",
)
def get_account_research(account_slug: str, x_api_key: Optional[str] = Header(None)):
    """Return the full pre-engagement Account Research record for one
    brand -- brand profile, leadership, technology environment, opportunity
    hypotheses, discovery questions, and the latest generated Background
    Brief markdown.

    Call this for "what do we know about [brand]" / "give me the [brand]
    background brief" when you just want to see it, not regenerate it. Use
    RULE-0-style discipline: display latest_background_brief.markdown
    verbatim rather than re-summarizing it.
    """
    _auth(x_api_key)
    try:
        dossier = cpc.load_account(account_slug)
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No Account Research record for slug '{account_slug}'. Call listAccountResearch to see valid slugs, or call generateAccountBackgroundBrief to start one.")
    try:
        dossier["opportunity_hypotheses"] = abb.load_hypotheses(account_slug)
        dossier["discovery_questions"] = abb.load_discovery_questions(account_slug)
        dossier["latest_background_brief"] = abb.get_current_brief_version(account_slug, include_markdown=True)
    except Exception:  # noqa: BLE001
        pass
    return dossier


@app.post(
    "/account-research/{account_slug}/background-brief",
    tags=["compute"],
    operation_id="generateAccountBackgroundBrief",
)
def post_generate_account_background_brief(
    account_slug: str,
    generated_for: str = "",
    x_api_key: Optional[str] = Header(None),
):
    """Generate (or regenerate) the canonical Account Background Brief for
    one brand -- a PRE-ENGAGEMENT document (public information + RBB's own
    already-persisted account research), prepared before discovery starts.
    Renders only from RBB's persisted intelligence, never re-researched
    from scratch by the model itself. Registers a new version; the prior
    version is archived, never deleted.

    If no Account Research record exists yet for this name, creates a new,
    empty one first (every field explicitly "unknown", real Discovery
    Questions surfaced instead of guessed content) -- deliberately cheap to
    start, unlike a Blue Sheet, which requires explicit authorization.
    account_slug may be a known slug (e.g. "cafe-rio") or a brand name
    (e.g. "McDonald's") -- resolved the same way either way. Response's
    newly_created tells you whether this was a brand-new account.
    """
    _auth(x_api_key)
    slug, exists = abb.resolve_account(account_slug)
    if not exists:
        slug = abb.create_new_account(account_slug)
    try:
        result = abb.generate_brief(slug, generated_for=generated_for)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"background brief generation failed: {exc}") from exc
    return {"slug": slug, "markdown": result["markdown"], "version": result["version"], "newly_created": not exists}


class CreateAccountPlanBody(BaseModel):
    account_strategy: str = Field(..., min_length=1, description="Real account strategy narrative — Todd's own judgment on how to approach this account. Never a generic template; must reflect this specific account's real situation.")
    go_no_go: str = Field(..., description="'go' | 'no-go' | 'pending' — the qualification call for this account right now.")
    generated_for: str = Field("", description="Who this plan is being prepared for, if relevant (e.g. an internal audience or a specific meeting).")
    user_authorization_quote: str = Field(..., min_length=1, description="REQUIRED. A verbatim quote of what the user actually said authorizing this specific Account Plan creation — not a paraphrase. Creating an Account Plan is a real 'we are now formally planning to pursue this account' commitment, same discipline as createBlueSheetAccount.")


@app.post(
    "/customers-prospects/{account_slug}/account-plan",
    tags=["write"],
    operation_id="createAccountPlan",
)
def post_create_account_plan(
    account_slug: str,
    body: CreateAccountPlanBody,
    x_api_key: Optional[str] = Header(None),
):
    """Creates (or regenerates) the Account Plan for one account -- the
    top-of-funnel, post-discovery planning document that sits between the
    Background Brief (pure pre-engagement research) and the Blue Sheet
    (active engagement). Formalizes the qualification scorecard, discovery
    status, and buying influences already on file, plus records Todd's own
    account strategy and go/no-go call.

    Only call this when the user has explicitly asked for a new or updated
    Account Plan for a specific account -- creating one is a real
    commitment moment, same authorization discipline as
    createBlueSheetAccount. Requires the account to already exist (call
    generateAccountBackgroundBrief first if it doesn't -- an Account Plan
    is never the first thing created for a brand-new brand).
    """
    _auth(x_api_key)
    try:
        result = acct_plan.generate_account_plan(
            account_slug, account_strategy=body.account_strategy, go_no_go=body.go_no_go,
            generated_for=body.generated_for,
        )
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No account found for slug '{account_slug}'. Call generateAccountBackgroundBrief first to create one.")
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))
    return {"slug": result["slug"], "markdown": result["markdown"], "version": result["version"]}


@app.get(
    "/customers-prospects/{account_slug}/account-plan",
    tags=["compute"],
    operation_id="getAccountPlan",
)
def get_account_plan_detail(account_slug: str, x_api_key: Optional[str] = Header(None)):
    """Return the current Account Plan for one account, verbatim. Use RULE-0
    discipline: display the markdown as-is rather than re-summarizing it."""
    _auth(x_api_key)
    current = acct_plan.get_current_account_plan(account_slug, include_content=True)
    if current is None:
        raise HTTPException(404, detail=f"No Account Plan exists yet for '{account_slug}'. Call createAccountPlan to make one.")
    return {"slug": account_slug, "markdown": current.get("content"), "version": current}


class CreateGreenSheetBody(BaseModel):
    call_purpose: str = Field(..., min_length=1, description="This specific call's real objective — distinct from the account-level Single Sales Objective. Never a generic template.")
    attendees: list[str] = Field(..., min_length=1, description="Real names of people on this call. Used to filter buying-influence and evidence records to only what's relevant to this call.")
    talking_points: str = Field("", description="Optional real talking points for this call.")
    generated_for: str = Field("", description="Who this Green Sheet is being prepared for, if relevant. Optional.")
    buying_influence_concepts: list[dict] = Field(default_factory=list, description="Optional. Each: {name, concept} — what this Buying Influence is trying to Accomplish, Fix, or Avoid on this call. Merges into the On This Call table; call-specific, never persisted to account.json.")
    valid_business_reason: str = Field("", description="Optional. This meeting's purpose, from the Buying Influence's own point of view.")
    credibility_if_established: str = Field("", description="Optional. If you already have credibility with this Buying Influence, how you'll check or enhance it this call.")
    credibility_if_not_established: str = Field("", description="Optional. If you don't yet have credibility, how you'll establish it this call.")
    perspective_to_share: str = Field("", description="Optional. The perspective or insight you plan to share this meeting.")
    unique_strengths: list[dict] = Field(default_factory=list, description="Optional. Each: {so_what, prove_it} — a unique strength relevant to this meeting and its proof point.")
    action_commitment_best: str = Field("", description="Optional. The best realistic action this Buying Influence could commit to as a result of this call.")
    action_commitment_minimum: str = Field("", description="Optional. The minimum acceptable action commitment for this call to be worth having.")
    basic_issues: list[str] = Field(default_factory=list, description="Optional. Free-form basic issues / personal-win concerns to keep in view for this Buying Influence.")


@app.post(
    "/customers-prospects/{account_slug}/green-sheet",
    tags=["write"],
    operation_id="createGreenSheet",
)
def post_create_green_sheet(
    account_slug: str,
    body: CreateGreenSheetBody,
    x_api_key: Optional[str] = Header(None),
):
    """Creates (or regenerates) a Green Sheet for one account -- a single-
    call prep document scoped to named attendees, distinct from the
    account-wide Account Plan and the full-engagement Blue Sheet.
    Deliberately ungated (no authorization quote required, unlike
    createAccountPlan/createBlueSheetAccount) -- cheap to create before
    any call, safe to regenerate freely, never mutates account.json.
    Requires the account to already exist.
    """
    _auth(x_api_key)
    try:
        result = green_sheet.generate_green_sheet(
            account_slug, call_purpose=body.call_purpose, attendees=body.attendees,
            talking_points=body.talking_points, generated_for=body.generated_for,
            buying_influence_concepts=body.buying_influence_concepts,
            valid_business_reason=body.valid_business_reason,
            credibility_if_established=body.credibility_if_established,
            credibility_if_not_established=body.credibility_if_not_established,
            perspective_to_share=body.perspective_to_share,
            unique_strengths=body.unique_strengths,
            action_commitment_best=body.action_commitment_best,
            action_commitment_minimum=body.action_commitment_minimum,
            basic_issues=body.basic_issues,
        )
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No account found for slug '{account_slug}'. Call generateAccountBackgroundBrief first to create one.")
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))
    return {"slug": result["slug"], "markdown": result["markdown"], "version": result["version"]}


@app.get(
    "/customers-prospects/{account_slug}/green-sheet",
    tags=["compute"],
    operation_id="getGreenSheet",
)
def get_green_sheet_detail(account_slug: str, x_api_key: Optional[str] = Header(None)):
    """Return the current Green Sheet for one account, verbatim. Use
    RULE-0 discipline: display the markdown as-is rather than
    re-summarizing it."""
    _auth(x_api_key)
    current = green_sheet.get_current_green_sheet(account_slug, include_content=True)
    if current is None:
        raise HTTPException(404, detail=f"No Green Sheet exists yet for '{account_slug}'. Call createGreenSheet to make one.")
    return {"slug": account_slug, "markdown": current.get("content"), "version": current}


class CreateWinPlanBody(BaseModel):
    close_plan: str = Field(..., min_length=1, description="Real, specific closing strategy for this opportunity — never a generic template.")
    generated_for: str = Field("", description="Who this is being prepared for, if relevant. Optional.")
    user_authorization_quote: str = Field(..., min_length=1, description="REQUIRED. A verbatim quote of what the user actually said authorizing this specific Win Plan creation — not a paraphrase. Creating a Win Plan represents committing to a specific closing strategy, a real decision moment, same discipline as createAccountPlan/createBlueSheetAccount.")


@app.post(
    "/customers-prospects/{account_slug}/win-plan",
    tags=["write"],
    operation_id="createWinPlan",
)
def post_create_win_plan(
    account_slug: str,
    body: CreateWinPlanBody,
    x_api_key: Optional[str] = Header(None),
):
    """Creates (or regenerates) the Win Plan for one account -- the
    closing-strategy document that pulls together qualification status,
    buying-influence ratings, and competitive position into one plan for
    how this specific opportunity actually gets won.

    Only call this when the user has explicitly asked for a new or
    updated Win Plan for a specific account -- creating one represents a
    real commitment to a closing strategy, same authorization discipline
    as createAccountPlan/createBlueSheetAccount. Requires the account to
    already exist.
    """
    _auth(x_api_key)
    try:
        result = win_plan.generate_win_plan(
            account_slug, close_plan=body.close_plan, generated_for=body.generated_for,
        )
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No account found for slug '{account_slug}'. Call generateAccountBackgroundBrief first to create one.")
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))
    return {"slug": result["slug"], "markdown": result["markdown"], "version": result["version"]}


@app.get(
    "/customers-prospects/{account_slug}/win-plan",
    tags=["compute"],
    operation_id="getWinPlan",
)
def get_win_plan_detail(account_slug: str, x_api_key: Optional[str] = Header(None)):
    """Return the current Win Plan for one account, verbatim. Use RULE-0
    discipline: display the markdown as-is rather than re-summarizing it."""
    _auth(x_api_key)
    current = win_plan.get_current_win_plan(account_slug, include_content=True)
    if current is None:
        raise HTTPException(404, detail=f"No Win Plan exists yet for '{account_slug}'. Call createWinPlan to make one.")
    return {"slug": account_slug, "markdown": current.get("content"), "version": current}


class CreateWinLossReviewBody(BaseModel):
    outcome: str = Field(..., description="'win' or 'loss'.")
    deal_size: str = Field("", description="Real deal size, e.g. '$250k ARR'. Optional.")
    close_date: str = Field("", description="Real close date. Optional.")
    decision_criteria: str = Field(..., min_length=1, description="What the customer said mattered most, in their words if possible -- and how it compared to the assumed criteria at gold sheet/green sheet stage.")
    competitive_dynamics: str = Field("", description="Who we competed against and how they were positioned, where the battle card held up or didn't, pricing dynamics.")
    what_we_did_well: str = Field("", description="Specific actions, messaging, or relationships that worked.")
    what_we_would_change: str = Field("", description="Specific missteps, timing issues, or coverage gaps.")
    root_cause_or_key_driver: str = Field(..., min_length=1, description="REQUIRED. The single biggest factor behind the outcome -- must be specific, not generic ('price' or 'relationship' alone isn't enough). Drives a real structured competitor gap point, so it must be genuinely specific.")
    action_items: list[dict] = Field(default_factory=list, description="Each: {action, owner, due_date, where_it_updates}. Optional.")
    customer_quote: str = Field("", description="Only used when outcome='win'. A real quote or reference, if one exists. Optional.")
    competitors_in_deal: list[str] = Field(default_factory=list, description="Competitor slugs named in this deal. The FIRST slug is treated as the primary competitor and gets the root_cause_or_key_driver promoted to a structured gap point; every named competitor gets the full competitive_dynamics text logged as firsthand evidence.")
    generated_for: str = Field("", description="Who this is being prepared for, if relevant. Optional.")
    user_authorization_quote: str = Field(..., min_length=1, description="REQUIRED. A verbatim quote of what the user actually said authorizing this specific Win/Loss Review creation — not a paraphrase. Creating one is Todd committing a real account of why a deal was won or lost, same discipline as createAccountPlan/createWinPlan.")


@app.post(
    "/customers-prospects/{account_slug}/win-loss-reviews/{opportunity_slug}",
    tags=["write"],
    operation_id="createWinLossReview",
)
def post_create_win_loss_review(
    account_slug: str,
    opportunity_slug: str,
    body: CreateWinLossReviewBody,
    x_api_key: Optional[str] = Header(None),
):
    """Creates (or regenerates) the Win/Loss Review for one specific
    opportunity at one account -- closes the loop after a deal decision:
    deal summary, decision criteria, competitive dynamics, what worked,
    what to change, the root cause/key driver, and action items for the
    playbook.

    Unlike every other customer-side artifact, this one is keyed by BOTH
    account_slug and opportunity_slug -- one account can have many Win/Loss
    Reviews over time, one per opportunity. Only call this when the user
    has explicitly asked for a new or updated Win/Loss Review for a named
    opportunity -- creating one is a real account of why a deal was won or
    lost, same authorization discipline as createAccountPlan/createWinPlan.
    Requires the account to already exist. root_cause_or_key_driver must
    be real, specific text -- never generic.
    """
    _auth(x_api_key)
    try:
        result = win_loss_review.generate_win_loss_review(
            account_slug, opportunity_slug, outcome=body.outcome, deal_size=body.deal_size,
            close_date=body.close_date, decision_criteria=body.decision_criteria,
            competitive_dynamics=body.competitive_dynamics, what_we_did_well=body.what_we_did_well,
            what_we_would_change=body.what_we_would_change,
            root_cause_or_key_driver=body.root_cause_or_key_driver, action_items=body.action_items,
            customer_quote=body.customer_quote, competitors_in_deal=body.competitors_in_deal,
            generated_for=body.generated_for,
        )
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No account found for slug '{account_slug}'. Call generateAccountBackgroundBrief first to create one.")
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))
    return {
        "account_slug": result["account_slug"], "opportunity_slug": result["opportunity_slug"],
        "markdown": result["markdown"], "version": result["version"],
    }


@app.get(
    "/customers-prospects/{account_slug}/win-loss-reviews/{opportunity_slug}",
    tags=["compute"],
    operation_id="getWinLossReview",
)
def get_win_loss_review_detail(account_slug: str, opportunity_slug: str, x_api_key: Optional[str] = Header(None)):
    """Return one specific Win/Loss Review, verbatim. Use RULE-0
    discipline: display the markdown as-is rather than re-summarizing it."""
    _auth(x_api_key)
    current = win_loss_review.get_current_win_loss_review(account_slug, opportunity_slug, include_content=True)
    if current is None:
        raise HTTPException(404, detail=f"No Win/Loss Review exists yet for '{account_slug}' / '{opportunity_slug}'. Call createWinLossReview to make one.")
    return {"account_slug": account_slug, "opportunity_slug": opportunity_slug, "markdown": current.get("content"), "version": current}


@app.get(
    "/customers-prospects/{account_slug}/win-loss-reviews",
    tags=["compute"],
    operation_id="listWinLossReviews",
)
def get_win_loss_reviews_list(account_slug: str, x_api_key: Optional[str] = Header(None)):
    """List every Win/Loss Review on file for one account -- opportunity
    slug, outcome, deal size, close date, and named competitors for each.
    Honest-blank: an empty reviews list means none exist yet for this
    account, not an error. Use this for 'what have we learned from past
    deals at [account]' or 'how have we done against [competitor]' style
    questions before calling getWinLossReview on a specific one."""
    _auth(x_api_key)
    reviews = win_loss_review.list_win_loss_reviews(account_slug)
    return {"account_slug": account_slug, "reviews": reviews}


class CreateRfpResponsePlanBody(BaseModel):
    deadline: str = Field(..., min_length=1, description="The real RFP response deadline (date and/or description) — e.g. 'Friday, 2026-09-04'.")
    open_loops: list[dict] = Field(..., min_length=1, description="Real open items still needed before the response can be submitted. Each: {priority: 'P0'|'P1'|'P2', loop: str, owner: str, completion_evidence: str, status: str}. Never invented — only real, currently-open work.")
    clarification_questions: list[str] = Field(..., min_length=1, description="Real questions actually owed back to the customer before the response can be finalized.")
    generated_for: str = Field("", description="Who this is being prepared for, if relevant. Optional.")
    user_authorization_quote: str = Field(..., min_length=1, description="REQUIRED. A verbatim quote of what the user actually said authorizing this specific RFP Response Plan creation — not a paraphrase. An RFP response is customer-facing and commitment-bearing, same discipline as createAccountPlan/createWinPlan/createBlueSheetAccount.")


@app.post(
    "/customers-prospects/{account_slug}/rfp-response-plan",
    tags=["write"],
    operation_id="createRfpResponsePlan",
)
def post_create_rfp_response_plan(
    account_slug: str,
    body: CreateRfpResponsePlanBody,
    x_api_key: Optional[str] = Header(None),
):
    """Creates (or regenerates) the RFP Response Plan for one account --
    formal RFP response tracking: a deadline, prioritized open loops still
    needed before submission, customer clarification questions owed back,
    and a fixed submission-gate checklist (Todd's own real methodology,
    rendered the same every time).

    Only call this when the user has explicitly asked for a new or
    updated RFP Response Plan for a specific account -- an RFP response is
    customer-facing and commitment-bearing, same authorization discipline
    as createAccountPlan/createWinPlan/createBlueSheetAccount. open_loops
    and clarification_questions must be real, sourced content -- never
    invented or templated. Requires the account to already exist.
    """
    _auth(x_api_key)
    try:
        result = rfp_response_plan.generate_rfp_response_plan(
            account_slug, deadline=body.deadline, open_loops=body.open_loops,
            clarification_questions=body.clarification_questions, generated_for=body.generated_for,
        )
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No account found for slug '{account_slug}'. Call generateAccountBackgroundBrief first to create one.")
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))
    return {"slug": result["slug"], "markdown": result["markdown"], "version": result["version"]}


@app.get(
    "/customers-prospects/{account_slug}/rfp-response-plan",
    tags=["compute"],
    operation_id="getRfpResponsePlan",
)
def get_rfp_response_plan_detail(account_slug: str, x_api_key: Optional[str] = Header(None)):
    """Return the current RFP Response Plan for one account, verbatim. Use
    RULE-0 discipline: display the markdown as-is rather than
    re-summarizing it."""
    _auth(x_api_key)
    current = rfp_response_plan.get_current_rfp_response_plan(account_slug, include_content=True)
    if current is None:
        raise HTTPException(404, detail=f"No RFP Response Plan exists yet for '{account_slug}'. Call createRfpResponsePlan to make one.")
    return {"slug": account_slug, "markdown": current.get("content"), "version": current}


class CreateVendorEngagementAnalysisBody(BaseModel):
    generated_for: str = Field("", description="Who this is being prepared for, if relevant. Optional.")


@app.post(
    "/customers-prospects/{account_slug}/vendor-engagement-analysis",
    tags=["write"],
    operation_id="createVendorEngagementAnalysis",
)
def post_create_vendor_engagement_analysis(account_slug: str, body: CreateVendorEngagementAnalysisBody, x_api_key: Optional[str] = Header(None)):
    """Persists a versioned Vendor Engagement Analysis for one account --
    which vendor(s) this account actually uses, how entrenched each
    engagement is (status, deployment, risk, real strategic notes from
    ecosystem_intelligence.json), and RBB's competitive angle on each vendor
    that's also a tracked competitor. RB-2026-09-07, competitive-side --
    distinct from Battle Card (category-wide) and Competitive Brief
    (competitor-wide): this is the one account-specific view. Fully
    computed from already-persisted data -- no authorization quote
    required, unlike the customer-facing artifacts. Requires the account to
    already exist."""
    _auth(x_api_key)
    try:
        result = vendor_engagement_analysis.generate_vendor_engagement_analysis(account_slug, generated_for=body.generated_for)
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No account found for slug '{account_slug}'. Call generateAccountBackgroundBrief first to create one.")
    return {"slug": result["slug"], "markdown": result["markdown"], "version": result["version"]}


@app.get(
    "/customers-prospects/{account_slug}/vendor-engagement-analysis",
    tags=["compute"],
    operation_id="getVendorEngagementAnalysis",
)
def get_vendor_engagement_analysis_detail(account_slug: str, x_api_key: Optional[str] = Header(None)):
    """Return the current Vendor Engagement Analysis for one account,
    verbatim. Use RULE-0 discipline: display the markdown as-is rather than
    re-summarizing it."""
    _auth(x_api_key)
    current = vendor_engagement_analysis.get_current_vendor_engagement_analysis(account_slug, include_content=True)
    if current is None:
        raise HTTPException(404, detail=f"No Vendor Engagement Analysis exists yet for '{account_slug}'. Call createVendorEngagementAnalysis to make one.")
    return {"slug": account_slug, "markdown": current.get("content"), "version": current}


@app.get("/vendors", tags=["compute"], operation_id="listVendors")
def get_vendors_list(x_api_key: Optional[str] = Header(None)):
    """List every vendor entity tracked in ecosystem_intelligence.json --
    the canonical vendor list (2026-09-01, Todd's own request) -- ALL ~88
    restaurant-tech vendors across the whole tracked brand universe (POS,
    payments, loyalty, back-office, kitchen ops, ...), not just the 9
    Genius directly competes with (that narrower list is listCompetitors).
    is_tracked_competitor flags which of these also have a competitor
    intelligence profile. For a downloadable file, call
    getVendorListDownloadLink instead."""
    _auth(x_api_key)
    rows = ecosystem_intelligence.vendor_export_rows()
    return {"contract": "rb_vendor_list_v1", "vendor_count": len(rows), "vendors": rows}


@app.get("/competitive-landscape/categories", tags=["compute"], operation_id="listTechStackCategories")
def get_tech_stack_categories(x_api_key: Optional[str] = Header(None)):
    """List every real tech-stack category the competitive-landscape
    analysis covers (POS, POS Hardware, Payments, Payments Gateway,
    Loyalty, Online Ordering, Kiosks, AI Solution 1/2, Drive-Thru Timers,
    ...) -- the exact category names getCategoryMarketShare accepts."""
    _auth(x_api_key)
    return {"categories": competitive_landscape.TECH_STACK_CATEGORIES}


@app.get("/competitive-landscape/category/{category}", tags=["compute"], operation_id="getCategoryMarketShare")
def get_category_market_share(category: str, x_api_key: Optional[str] = Header(None)):
    """Market share + real battle cards (positioning, Todd's POV, sourced
    vs_genius advantages on both sides, and a computed brand-count delta)
    for one tech-stack category -- e.g. 'pos', 'drive_thru_timers',
    'ai_solution_1'. Call listTechStackCategories first if you don't know
    the exact category name. For the full analysis across every category
    as a downloadable workbook, call getCompetitiveLandscapeDownloadLink
    instead."""
    _auth(x_api_key)
    if category not in competitive_landscape.TECH_STACK_CATEGORIES:
        raise HTTPException(
            400,
            detail=f"unknown category '{category}'. Call listTechStackCategories to see valid names.",
        )
    graph = ecosystem_intelligence._read_graph()
    return competitive_landscape.battle_card_for_category(graph, category)


class CreateBattleCardBody(BaseModel):
    generated_for: str = Field("", description="Who this is being prepared for, if relevant. Optional.")


@app.post("/battle-cards/{category}", tags=["write"], operation_id="createBattleCard")
def post_create_battle_card(category: str, body: CreateBattleCardBody, x_api_key: Optional[str] = Header(None)):
    """Persists a versioned Battle Card document for one tech-stack category
    -- a markdown rendering of getCategoryMarketShare's own computed content
    (Genius's share, up to 5 competitors with real positioning/Todd's
    POV/advantages/financial health), written to system/artifact_vault/ and
    indexed. Fully computed from already-persisted data -- no authorization
    quote required, unlike the customer-facing artifacts (Account Plan, Win
    Plan, RFP Response Plan). Call listTechStackCategories first if you
    don't know the exact category name. Regenerating supersedes the prior
    version rather than replacing it in place."""
    _auth(x_api_key)
    try:
        result = battle_card.generate_battle_card(category, generated_for=body.generated_for)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))
    return {"category": result["category"], "markdown": result["markdown"], "version": result["version"]}


@app.get("/battle-cards/{category}", tags=["compute"], operation_id="getBattleCard")
def get_battle_card_detail(category: str, x_api_key: Optional[str] = Header(None)):
    """Return the current persisted Battle Card for one tech-stack
    category, verbatim. Use RULE-0 discipline: display the markdown as-is
    rather than re-summarizing it. For a fresh computed view without
    persisting it, use getCategoryMarketShare instead."""
    _auth(x_api_key)
    current = battle_card.get_current_battle_card(category, include_content=True)
    if current is None:
        raise HTTPException(404, detail=f"No Battle Card persisted yet for category '{category}'. Call createBattleCard to make one.")
    return {"category": category, "markdown": current.get("content"), "version": current}


@app.get("/competitors", tags=["compute"], operation_id="listCompetitors")
def get_competitors_list(x_api_key: Optional[str] = Header(None)):
    """List every competitor RB is tracking intelligence on -- RB's real
    actionable tracked-vendor/competitor universe (not narrowly limited to
    vendors that directly compete with Genius today -- Todd's own real
    usage, e.g. bulk-adding an entire trade-show vendor roster, is broader
    than that; see the FSTEC 2026 batch, RB-DEFECT-071), as distinct from
    restaurant BRANDS (which are potential/existing customers, tracked via
    listAccountResearch / listBlueSheetAccounts / the macro brand graph
    instead). Call this to find the right competitor_slug for
    getCompetitorProfile."""
    _auth(x_api_key)
    reg = compintel_common.load_registry()
    competitors = []
    class_counts: dict[str, int] = {}
    for row in reg.get("registry", []):
        enriched = dict(row)
        try:
            profile = compintel_common.load_competitor(row.get("competitor_slug") or "")
            relationship_class = compintel_common.competitive_relationship_class(profile)
        except (FileNotFoundError, ValueError):
            relationship_class = "unclassified_vendor"
        enriched["relationship_class"] = relationship_class
        class_counts[relationship_class] = class_counts.get(relationship_class, 0) + 1
        competitors.append(enriched)
    return {
        "contract": "rb_competitor_list_v2",
        "competitor_count": len(competitors),
        "relationship_class_counts": class_counts,
        "competitors": competitors,
    }


@app.get("/sales-opportunity-radar", tags=["compute"], operation_id="getSalesOpportunityRadar")
def get_sales_opportunity_radar(x_api_key: Optional[str] = Header(None)):
    """Return the latest analytical Radar + Pursuit report: incumbent vendor
    exposure, review-first buying-window hypotheses, checked/no-signal records,
    and outcome-calibration counts. This never creates an opportunity or turns
    a hypothesis into a canonical claim."""
    _auth(x_api_key)
    path = core.CACHE_DIR / "sales_opportunity_radar.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(404, detail="Sales Opportunity Radar has not run yet.")
    except (OSError, ValueError) as exc:
        raise HTTPException(500, detail=f"Sales Opportunity Radar cache is unreadable: {exc}")
    return report


@app.get("/intelligence-action-queue", tags=["compute"], operation_id="getIntelligenceActionQueue")
def get_intelligence_action_queue(
    action_class: Optional[str] = Query(
        None, description="Filter to one action class: monitor, research_further, "
        "contact_account, build_pursuit, or competitive_displacement_opportunity."),
    status: Optional[str] = Query(
        None, description="Filter to 'pending_review' (not yet resolved) or 'resolved' "
        "(see resolveIntelligenceActionQueueItem). Omit for both."),
    limit: int = Query(50, ge=1, le=200, description="Max items to return (already rank-sorted)."),
    x_api_key: Optional[str] = Header(None),
):
    """Return the full ranked, review-first intelligence action queue --
    consolidated buying-window hypotheses, first-party page changes,
    baseline-research gaps, downstream ramifications, and grouped competitor-
    review items, one queue instead of dispersed cache files. The Daily
    Brief's Part 2 only surfaces the top 8 pending items by rank; call this
    for the rest, or to filter to one action_class (e.g. everything
    currently at research_further) or resolution status. Every item is a
    recommendation, not proof of an executed action -- a queue entry never
    means RB contacted an account, created an opportunity, changed a
    pursuit stage, or wrote a canonical claim. RB-DEFECT (2026-09-15):
    action classes above research_further now require a resolved entity_id
    and (for build_pursuit and competitive_displacement_opportunity) a
    verified incumbent -- a high score alone no longer escalates an
    unidentified account. Resolved items (via resolveIntelligenceActionQueueItem)
    persist their disposition across every rebuild -- see resolution_counts."""
    _auth(x_api_key)
    path = core.CACHE_DIR / "intelligence_action_queue.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(404, detail="Intelligence Action Queue has not run yet.")
    except (OSError, ValueError) as exc:
        raise HTTPException(500, detail=f"Intelligence Action Queue cache is unreadable: {exc}")
    items = report.get("items") or []
    if action_class:
        items = [item for item in items if item.get("action_class") == action_class]
    if status:
        items = [item for item in items if item.get("status") == status]
    report = dict(report)
    report["items"] = items[:limit]
    report["returned"] = len(report["items"])
    return report


@app.post(
    "/intelligence-action-queue/{queue_id}/resolve",
    tags=["write"],
    operation_id="resolveIntelligenceActionQueueItem",
)
def post_resolve_intelligence_action_queue_item(
    queue_id: str, disposition: str, note: str = "", x_api_key: Optional[str] = Header(None),
):
    """Record Todd's disposition on one intelligence action queue item --
    'accepted' (he acted on it), 'rejected' (not relevant / false positive),
    or 'deferred' (correct signal, not right now). This is the durable
    record outcome calibration reads; it does NOT itself contact an
    account, create an opportunity, or change a pursuit stage -- those stay
    on their own existing authorization paths. Snapshots this item's
    item_type/entity/action_class/priority_score as they were at
    resolution time, since the live item disappears or re-scores on the
    next pipeline cycle. Call getIntelligenceActionQueue first to get a
    real queue_id -- ids can change when the underlying evidence changes."""
    _auth(x_api_key)
    if disposition not in iaq.VALID_DISPOSITIONS:
        raise HTTPException(422, detail=f"disposition must be one of {sorted(iaq.VALID_DISPOSITIONS)}, got {disposition!r}.")
    try:
        entry = iaq.resolve(queue_id, disposition, note=note)
    except KeyError as exc:
        raise HTTPException(404, detail=str(exc))
    al.log_mutation_executed(
        f"resolveIntelligenceActionQueueItem: queue_id={queue_id} disposition={disposition}",
        source="POST /intelligence-action-queue/{queue_id}/resolve",
    )
    return {"ok": True, "queue_id": queue_id, "disposition": disposition, "entry": entry}


@app.get("/intelligence-calibration", tags=["compute"], operation_id="getIntelligenceCalibration")
def get_intelligence_calibration(x_api_key: Optional[str] = Header(None)):
    """Return how RB's intelligence action queue is actually doing: real
    accept/reject/defer disposition counts by item_type, and -- for
    buying_window_hypothesis items only -- whether an accepted hypothesis's
    entity later shows a real, independently-tracked active_pursuit outcome
    (from sales_opportunity_radar_state.json, itself driven by the Blue
    Sheet registry, not inferred here). Every other item_type has no
    equivalent outcome source yet and is never claimed to be validated by
    one. This will read as mostly empty for a while after 2026-09-15 --
    that reflects real accumulated dispositions, not a bug; do not draw
    conclusions from a small total_resolutions count."""
    _auth(x_api_key)
    path = core.CACHE_DIR / "intelligence_calibration.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(404, detail="Intelligence Calibration has not run yet.")
    except (OSError, ValueError) as exc:
        raise HTTPException(500, detail=f"Intelligence Calibration cache is unreadable: {exc}")


@app.get("/competitors/{competitor_slug}", tags=["compute"], operation_id="getCompetitorProfile")
def get_competitor_profile(competitor_slug: str, x_api_key: Optional[str] = Header(None)):
    """Return one competitor's full intelligence profile: positioning,
    Todd's own POV, which Genius product line(s) it competes on, gap
    analysis (where Genius wins / where the competitor wins), and every
    evidence record on file -- both mechanically synced from
    ecosystem_intelligence.json signals and account_intelligence/ docs (the
    intelligence-gathering process), and human-supplied notes (see
    addCompetitiveNote). Never re-researched live; only what RB already
    has persisted. Call listCompetitors first if you don't know the slug."""
    _auth(x_api_key)
    try:
        data = compintel_common.load_competitor(competitor_slug)
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No competitor intelligence record for slug '{competitor_slug}'. Call listCompetitors to see valid slugs.")
    review_queue = compintel_common.load_review_queue()
    pending_reviews = [
        item for item in review_queue.get("pending_reviews", [])
        if item.get("competitor_slug") == competitor_slug and item.get("status") == "pending"
    ]
    return {
        "competitor": data["competitor"],
        "evidence": data["evidence"],
        "pending_reviews": pending_reviews,
        "markdown": compintel.render_competitor_profile(competitor_slug),
    }


# ---------------------------------------------------------------------------
# Franchisee Finder (Phase 1, 2026-10-02) — read-only + seed import only.
# system/design/FRANCHISEE_FINDER_SPEC.md sections 16/19: Phase 1 is schema +
# storage + a basic search surface; the review-first submission workflow
# (submitFranchiseeCorrection / reviewFranchiseeSubmission) and the
# continuous research-refresh cycle are Phase 2+, not built here. Every
# organization record was seeded by migrate_franchisee_hierarchy.py from two
# real public sources (Franchise Times 2026 Restaurant 200 + the existing
# multi_brand_franchisee_operator entities in ecosystem_intelligence.json) --
# nothing here is live-researched on call.
# ---------------------------------------------------------------------------

@app.get("/franchisee-organizations", tags=["compute"], operation_id="listFranchiseeOrganizations")
def get_franchisee_organizations_list(
    min_units: Optional[int] = Query(None, ge=0, description="Only organizations whose total_identified_units is at least this."),
    multi_brand_only: bool = Query(False, description="Only organizations operating 2+ distinct brands."),
    x_api_key: Optional[str] = Header(None),
):
    """List every franchisee organization Franchisee Finder has on file --
    multi-brand restaurant franchisee groups (e.g. Flynn Group, Sun
    Holdings) and large foodservice contractors (e.g. Sodexo, Aramark) with
    at least one identified restaurant-brand relationship. Call this to find
    the right org_slug for getFranchiseeProfile, or use queryFranchiseesByBrand
    to search by brand instead of browsing the full list. Each row's
    brand_count/total_identified_units is a real, already-computed summary,
    not re-derived on this call."""
    _auth(x_api_key)
    orgs = ff_common.list_organizations(min_units=min_units, multi_brand_only=multi_brand_only)
    return {"contract": "rb_franchisee_organization_list_v1", "organization_count": len(orgs), "organizations": orgs}


@app.get("/franchisee-organizations/{org_slug}", tags=["compute"], operation_id="getFranchiseeProfile")
def get_franchisee_profile(org_slug: str, x_api_key: Optional[str] = Header(None)):
    """Return one franchisee organization's full profile: headquarters,
    ownership, legal entities, every brand relationship with its own
    unit-count assertion and history, leadership/people, and the complete
    evidence ledger every assertion's evidence_ids point into. Every
    assertion carries its own confidence_pct and status (confirmed /
    inferred / unresolved / contradicted) -- never present a value from
    this record as settled fact without surfacing that distinction. Never
    re-researched live; only what Franchisee Finder already has persisted.
    Call listFranchiseeOrganizations or queryFranchiseesByBrand first if you
    don't know the org_slug."""
    _auth(x_api_key)
    try:
        data = ff_common.load_organization(org_slug)
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No Franchisee Finder record for org_slug '{org_slug}'. Call listFranchiseeOrganizations to see valid slugs.")
    return {"organization": data["organization"], "evidence": data["evidence"]}


@app.get("/franchisee-organizations/by-brand/{brand_name}", tags=["compute"], operation_id="queryFranchiseesByBrand")
def get_franchisees_by_brand(brand_name: str, x_api_key: Optional[str] = Header(None)):
    """Answer 'who are the franchisees of brand X' -- every organization
    with a brand_relationships entry matching brand_name (case-insensitive
    exact match against the brand's name as recorded, e.g. 'Taco Bell'),
    each with that specific relationship's unit count, confidence, and
    status. Returns an empty organizations list (not a 404) when the brand
    is tracked but no franchisee relationship is on file yet -- that is a
    real, honest answer, not an error. Call listFranchiseeOrganizations
    first if you want to browse by organization instead of by brand."""
    _auth(x_api_key)
    matches = ff_common.find_organizations_by_brand(brand_name)
    return {"contract": "rb_franchisee_by_brand_v1", "brand_name": brand_name, "match_count": len(matches), "organizations": matches}


# ---------------------------------------------------------------------------
# Technology Lifecycle (Phase 1, 2026-10-02) — read + one cheap/ungated
# write (forcing signals only). system/technology_lifecycle/README.md,
# "What exists vs. what's Phase 1": the richer structured record types
# (relationship events, governance, penetration, change events) are
# written only through import_technology_lifecycle_research.py's Hunter-
# packet importer, not a chat-callable endpoint with dozens of nested
# parameters -- same reasoning competitor-platform research's structured
# findings go through its own importer rather than createCompetitor.
# ---------------------------------------------------------------------------

def _resolve_brand_entity_id_or_404(brand_name: str) -> str:
    graph = ecosystem_intelligence._read_graph()
    entity_id = ecosystem_intelligence._resolve_entity_id_any_type(brand_name, graph)
    if not entity_id:
        raise HTTPException(404, detail=f"'{brand_name}' does not resolve to a known entity in ecosystem_intelligence.json (no exact name/alias match, or ambiguous).")
    return entity_id


@app.get("/technology-lifecycle/profile/{brand_name}", tags=["compute"], operation_id="getTechnologyLifecycleProfile")
def get_technology_lifecycle_profile(brand_name: str, x_api_key: Optional[str] = Header(None)):
    """Return everything Technology Lifecycle has on one brand: every
    tracked technology relationship with its current lifecycle state
    (selected/contracted/rollout_active/deployed/displaced/etc. -- derived
    from the most recent non-superseded event, never assumed from the
    oldest announcement), governance records, penetration observations,
    reconstructed change-event narratives, and open forcing signals
    (approaching EOL, leadership change, etc. that haven't yet led to a
    completed switch). An empty profile (every list empty) is a real,
    honest answer -- this brand has no technology-lifecycle research on
    file yet, not an error. brand_name is resolved against
    ecosystem_intelligence.json's real brand/vendor entities (exact name
    or alias only, never guessed) -- a 404 means no such entity is
    tracked at all, which is different from a tracked brand with zero
    lifecycle evidence."""
    _auth(x_api_key)
    brand_entity_id = _resolve_brand_entity_id_or_404(brand_name)
    return tech_lifecycle.get_entity_technology_profile(brand_entity_id)


@app.get("/technology-lifecycle/forcing-signals", tags=["compute"], operation_id="listTechnologyForcingSignals")
def get_technology_forcing_signals(
    brand_name: Optional[str] = Query(None, description="Filter to one brand (resolved against ecosystem_intelligence.json)."),
    technology_category: Optional[str] = Query(None, description="Filter to one category, e.g. 'pos_hardware'."),
    x_api_key: Optional[str] = Header(None),
):
    """List standalone pre-change signals (approaching OS/hardware EOL, a
    new CTO, a transformation announcement) that haven't yet led to a
    completed technology switch -- the raw material for a future
    change-propensity read, not itself a confirmed change. See
    getTechnologyLifecycleProfile for a specific brand's full picture
    including any completed change events."""
    _auth(x_api_key)
    brand_entity_id = _resolve_brand_entity_id_or_404(brand_name) if brand_name else None
    if technology_category and technology_category not in tech_lifecycle.TECHNOLOGY_CATEGORIES:
        raise HTTPException(422, detail=f"technology_category must be one of {sorted(tech_lifecycle.TECHNOLOGY_CATEGORIES)}, got {technology_category!r}.")
    signals = tech_lifecycle.list_forcing_signals(brand_entity_id=brand_entity_id, technology_category=technology_category)
    return {"contract": "rb_technology_forcing_signal_list_v1", "signal_count": len(signals), "signals": signals}


class CreateTechnologyForcingSignalBody(BaseModel):
    brand_name: str = Field(..., description="Resolved against ecosystem_intelligence.json -- must already be a tracked brand/vendor entity.")
    technology_category: str = Field(..., description=f"One of {sorted(tech_lifecycle.TECHNOLOGY_CATEGORIES)}.")
    forcing_event_type: str = Field(..., description=f"One of {sorted(tech_lifecycle.FORCING_EVENT_TYPES)}.")
    detail: str = Field(..., description="What was actually observed -- real, specific content, never a generic placeholder.")
    evidence: str = Field(..., description="The real evidence/excerpt supporting this, never invented.")
    source_url: Optional[str] = Field(None, description="Omit only when there is genuinely no URL (e.g. firsthand RBB observation).")
    confidence: str = Field(..., description="One of high/medium/low.")
    evidence_type: str = Field(..., description=f"One of {sorted(tech_lifecycle.EVIDENCE_TYPES)}. Use 'rbb_inference' for a judgment call, never upgrade it to a stronger type later without new sourcing.")


@app.post("/technology-lifecycle/forcing-signals", tags=["write"], operation_id="createTechnologyForcingSignal")
def post_create_technology_forcing_signal(body: CreateTechnologyForcingSignalBody, x_api_key: Optional[str] = Header(None)):
    """Record one standalone pre-change signal about a brand's CURRENT
    stack -- deliberately cheap and ungated (same philosophy as
    createCompetitor/addCompetitiveNote), since this is observational
    evidence capture, not a canonical commitment the way createBlueSheetAccount
    is. Never call this with a signal you inferred without saying so --
    evidence_type:'rbb_inference' exists exactly for that case. For a
    brand with no existing ecosystem_intelligence.json entity at all,
    this returns 404 -- Technology Lifecycle never invents a new entity
    id scheme; the brand/vendor must be tracked there first."""
    _auth(x_api_key)
    brand_entity_id = _resolve_brand_entity_id_or_404(body.brand_name)
    import uuid as _uuid
    signal_id = f"tfs-{brand_entity_id}-{_uuid.uuid4().hex[:8]}"
    try:
        record = tech_lifecycle.record_forcing_signal(
            signal_id=signal_id, brand_entity_id=brand_entity_id, entity_level="brand",
            technology_category=body.technology_category, forcing_event_type=body.forcing_event_type,
            detail=body.detail, evidence=body.evidence, source_url=body.source_url,
            confidence=body.confidence, evidence_type=body.evidence_type,
        )
    except tech_lifecycle.TechnologyLifecycleError as exc:
        raise HTTPException(422, detail=str(exc))
    al.log_mutation_executed(
        f"createTechnologyForcingSignal: signal_id={signal_id} brand={body.brand_name}",
        source="POST /technology-lifecycle/forcing-signals",
    )
    return {"ok": True, "signal": record}


# ---------------------------------------------------------------------------
# FDD Technology Governance & Economics (2026-10-02) — most reads live on
# getTechnologyLifecycleProfile above (get_entity_technology_profile was
# extended with fdd_sources/economics/governance_change_events/
# penetration_reconciliation/open_research_gaps, same "ingest once, expose
# everywhere" principle the brief's own Core Architectural Principle
# states). These two routes cover what that profile read can't: the
# cross-brand research-gap queue and the entity-resolution review queue
# (brief §1/§17), both of which Hunter's import_fdd_research.py writes to.
# ---------------------------------------------------------------------------

@app.get("/technology-lifecycle/fdd-research-gaps", tags=["compute"], operation_id="listFddResearchGaps")
def get_fdd_research_gaps(
    brand_name: Optional[str] = Query(None, description="Filter to one brand (resolved against ecosystem_intelligence.json)."),
    x_api_key: Optional[str] = Header(None),
):
    """Open FDD research gaps (brief §17) -- current_vendor_unknown,
    governance_unknown, penetration_unknown, grandfathering_unknown,
    conversion_deadline_unknown, etc. Cross-brand by default so a review
    pass can work the whole open queue; pass brand_name to scope to one
    brand's gaps (same gaps also surface on that brand's
    getTechnologyLifecycleProfile under open_research_gaps)."""
    _auth(x_api_key)
    brand_entity_id = _resolve_brand_entity_id_or_404(brand_name) if brand_name else None
    gaps = tech_lifecycle.list_fdd_research_gaps(brand_id=brand_entity_id)
    return {"contract": "rb_fdd_research_gap_list_v1", "gap_count": len(gaps), "gaps": gaps}


@app.get("/technology-lifecycle/entity-resolution-review", tags=["compute"], operation_id="listEntityResolutionReviewQueue")
def get_entity_resolution_review_queue(
    status: Optional[str] = Query("pending", description=f"One of {sorted(tech_lifecycle.ENTITY_RESOLUTION_REVIEW_STATUSES)}, or omit for every item regardless of status."),
    x_api_key: Optional[str] = Header(None),
):
    """FDD research (or any Technology Lifecycle research) that named an
    entity it couldn't confidently resolve against ecosystem_
    intelligence.json -- brief §1's safety valve against silently creating
    a duplicate entity. Defaults to status='pending' (the actual review
    queue); pass status=None to see resolved/rejected history too."""
    _auth(x_api_key)
    if status and status not in tech_lifecycle.ENTITY_RESOLUTION_REVIEW_STATUSES:
        raise HTTPException(422, detail=f"status must be one of {sorted(tech_lifecycle.ENTITY_RESOLUTION_REVIEW_STATUSES)}, got {status!r}.")
    items = tech_lifecycle.list_entity_resolution_review(status=status)
    return {"contract": "rb_entity_resolution_review_list_v1", "item_count": len(items), "items": items}


class ResolveEntityResolutionReviewBody(BaseModel):
    resolved_entity_name: str = Field(..., description="The real entity's name/alias, resolved against ecosystem_intelligence.json -- must already be a tracked entity. This never creates a new entity.")


@app.post("/technology-lifecycle/entity-resolution-review/{review_id}/resolve", tags=["write"], operation_id="resolveEntityResolutionReview")
def post_resolve_entity_resolution_review(review_id: str, body: ResolveEntityResolutionReviewBody, x_api_key: Optional[str] = Header(None)):
    """Confirms which real, existing entity an unresolved research mention
    actually refers to. 404 if review_id doesn't exist or
    resolved_entity_name doesn't resolve to a real entity -- this never
    invents a new entity id scheme, same invariant every Technology
    Lifecycle write enforces."""
    _auth(x_api_key)
    resolved_entity_id = _resolve_brand_entity_id_or_404(body.resolved_entity_name)
    try:
        record = tech_lifecycle.resolve_entity_resolution_review(review_id, resolved_entity_id=resolved_entity_id)
    except tech_lifecycle.TechnologyLifecycleError as exc:
        raise HTTPException(404, detail=str(exc))
    al.log_mutation_executed(
        f"resolveEntityResolutionReview: review_id={review_id} resolved_entity_id={resolved_entity_id}",
        source="POST /technology-lifecycle/entity-resolution-review/{review_id}/resolve",
    )
    return {"ok": True, "item": record}


class RejectEntityResolutionReviewBody(BaseModel):
    reason: str = Field(..., description="Why this mention isn't a real resolvable entity (e.g. a typo, a non-entity term, a duplicate of an already-queued item).")


@app.post("/technology-lifecycle/entity-resolution-review/{review_id}/reject", tags=["write"], operation_id="rejectEntityResolutionReview")
def post_reject_entity_resolution_review(review_id: str, body: RejectEntityResolutionReviewBody, x_api_key: Optional[str] = Header(None)):
    """Dismisses a queued review item without resolving it to any entity --
    use when the mention isn't a real new entity at all (never use this to
    silently approve an uncertain match; call resolve with the real entity
    instead)."""
    _auth(x_api_key)
    try:
        record = tech_lifecycle.reject_entity_resolution_review(review_id, reason=body.reason)
    except tech_lifecycle.TechnologyLifecycleError as exc:
        raise HTTPException(404, detail=str(exc))
    al.log_mutation_executed(
        f"rejectEntityResolutionReview: review_id={review_id} reason={body.reason}",
        source="POST /technology-lifecycle/entity-resolution-review/{review_id}/reject",
    )
    return {"ok": True, "item": record}


# ---------------------------------------------------------------------------
# User POV Registry (Phase 1, 2026-10-02) — atomic, governed user beliefs
# and operating principles, distinct from objective intelligence
# (ecosystem graph) and system doctrine (ARCHITECTURE.md/SCHEMAS.md).
# See system/POV_REGISTRY_FEATURE_BRIEF_2026-10-01.md. No framework-import
# pipeline yet (getPOVFramework/importPOVFramework) -- Phase 1 is the
# atomic registry only.
# ---------------------------------------------------------------------------

@app.get("/pov/entries", tags=["compute"], operation_id="listPOVEntries")
def get_pov_entries_list(
    scope: Optional[str] = Query(None, description="Filter to one scope, e.g. 'restaurant_technology', 'enterprise_sales'."),
    type: Optional[str] = Query(None, description=f"Filter to one type: {sorted(user_pov.VALID_TYPES)}."),
    status: Optional[str] = Query(None, description=f"Filter to one status: {sorted(user_pov.VALID_STATUSES)}. Omit to see every entry including superseded/retired."),
    x_api_key: Optional[str] = Header(None),
):
    """List every atomic POV entry on file -- the user's own beliefs,
    hypotheses, evaluative lenses, and hard operating boundaries (NOT
    objective facts about the world -- see getEntitySignals/
    queryIntelligenceIndex for those). Omit `status` to see the full
    history including superseded/retired entries; pass status='active'
    for just what's currently in force. Call this to find a pov_id for
    getPOVEntry, revisePOVEntry, retirePOVEntry, or attachPOVEvidence."""
    _auth(x_api_key)
    if type is not None and type not in user_pov.VALID_TYPES:
        raise HTTPException(422, detail=f"type must be one of {sorted(user_pov.VALID_TYPES)}, got {type!r}.")
    if status is not None and status not in user_pov.VALID_STATUSES:
        raise HTTPException(422, detail=f"status must be one of {sorted(user_pov.VALID_STATUSES)}, got {status!r}.")
    entries = user_pov.list_pov_entries(scope=scope, entry_type=type, status=status)
    return {"contract": "rb_pov_entry_list_v1", "entry_count": len(entries), "entries": entries}


@app.get("/pov/entries/{pov_id}", tags=["compute"], operation_id="getPOVEntry")
def get_pov_entry_detail(pov_id: str, x_api_key: Optional[str] = Header(None)):
    """Return one POV entry plus every evidence record attached to it
    (supporting/challenging/qualifying). Call listPOVEntries first if you
    don't have the pov_id."""
    _auth(x_api_key)
    try:
        entry = user_pov.get_pov_entry(pov_id)
    except user_pov.UserPovError:
        raise HTTPException(404, detail=f"No POV entry with pov_id '{pov_id}'. Call listPOVEntries to see valid ids.")
    return {"entry": entry, "evidence": user_pov.list_evidence_for(pov_id)}


class AddPOVEntryBody(BaseModel):
    statement: str = Field(..., description="The exact statement, in the user's own words wherever possible -- never a paraphrase that changes meaning.")
    type: str = Field(..., description=f"One of {sorted(user_pov.VALID_TYPES)}.")
    scope: str = Field(..., description="e.g. 'restaurant_technology', 'enterprise_sales', 'ai', 'global'.")
    conviction: str = Field("informed_belief", description=f"One of {sorted(user_pov.VALID_CONVICTIONS)}.")
    authorship: str = Field("user_authored", description=f"One of {sorted(user_pov.VALID_AUTHORSHIPS)}. 'rbb_inferred' entries start needs_review:true -- never claim the user said something they didn't.")
    source_document: Optional[str] = Field(None, description="Where this came from, if applicable.")
    source_section: Optional[str] = Field(None, description="Section/heading within source_document, if applicable.")
    applies_to_surfaces: list[str] = Field(default_factory=list, description="Which RBB surfaces should apply this lens, e.g. ['opportunity_scoring', 'account_research'].")


@app.post("/pov/entries", tags=["write"], operation_id="addPOVEntry")
def post_add_pov_entry(body: AddPOVEntryBody, x_api_key: Optional[str] = Header(None)):
    """Record a new atomic POV entry -- deliberately cheap and ungated for
    authorship='user_authored' (the user's own direct, verbatim
    declaration), same philosophy as createCompetitor. Never call this
    with a belief YOU inferred and present it as authorship='user_authored'
    -- use 'rbb_inferred' for your own judgment calls, which this starts
    as needs_review:true rather than immediately authoritative."""
    _auth(x_api_key)
    try:
        entry = user_pov.add_pov_entry(
            body.statement, body.type, body.scope, conviction=body.conviction, authorship=body.authorship,
            source_document=body.source_document, source_section=body.source_section,
            applies_to_surfaces=body.applies_to_surfaces,
        )
    except user_pov.UserPovError as exc:
        raise HTTPException(422, detail=str(exc))
    return {"ok": True, "entry": entry}


class RevisePOVEntryBody(BaseModel):
    new_statement: str = Field(..., description="The revised statement -- never a silent edit; this creates a new entry and marks the original superseded.")
    conviction: Optional[str] = Field(None, description=f"One of {sorted(user_pov.VALID_CONVICTIONS)}. Omit to keep the original entry's conviction.")
    reason: Optional[str] = Field(None, description="Why this is changing, if the user said so.")


@app.post("/pov/entries/{pov_id}/revise", tags=["write"], operation_id="revisePOVEntry")
def post_revise_pov_entry(pov_id: str, body: RevisePOVEntryBody, x_api_key: Optional[str] = Header(None)):
    """Revise an existing POV entry -- NEVER edits it in place. Creates a
    new entry carrying the revised statement and marks the original
    status:'superseded' with superseded_by set, preserving full history
    (additive-by-default -- see the feature brief's write discipline).
    Use this only for the user revising their OWN belief directly, not
    for applying external evidence that merely challenges it -- that's
    attachPOVEvidence instead, which never changes the entry's statement."""
    _auth(x_api_key)
    try:
        entry = user_pov.revise_pov_entry(pov_id, body.new_statement, conviction=body.conviction, reason=body.reason)
    except user_pov.UserPovError as exc:
        raise HTTPException(404 if "No POV entry" in str(exc) else 422, detail=str(exc))
    return {"ok": True, "entry": entry}


class RetirePOVEntryBody(BaseModel):
    reason: str = Field(..., description="Why this entry no longer applies -- required, never silent.")


@app.post("/pov/entries/{pov_id}/retire", tags=["write"], operation_id="retirePOVEntry")
def post_retire_pov_entry(pov_id: str, body: RetirePOVEntryBody, x_api_key: Optional[str] = Header(None)):
    """Mark a POV entry retired -- the user no longer holds this belief or
    applies this rule. The entry and its full evidence history stay on
    file (never deleted), just excluded from an active-only view."""
    _auth(x_api_key)
    try:
        entry = user_pov.retire_pov_entry(pov_id, reason=body.reason)
    except user_pov.UserPovError as exc:
        raise HTTPException(404, detail=str(exc))
    return {"ok": True, "entry": entry}


class AttachPOVEvidenceBody(BaseModel):
    relation: str = Field(..., description=f"One of {sorted(user_pov.VALID_EVIDENCE_RELATIONS)}.")
    evidence: str = Field(..., description="The real evidence/observation -- never invented.")
    source_url: Optional[str] = Field(None, description="Omit only when there is genuinely no URL.")
    confidence: str = Field("medium", description="high, medium, or low.")


@app.post("/pov/entries/{pov_id}/evidence", tags=["write"], operation_id="attachPOVEvidence")
def post_attach_pov_evidence(pov_id: str, body: AttachPOVEvidenceBody, x_api_key: Optional[str] = Header(None)):
    """Attach one evidence record to an existing POV entry -- supports,
    challenges, or qualifies it. Deliberately cheap and ungated, same as
    addCompetitiveNote. NEVER overwrites or changes the entry's statement
    -- evidence is independent fact, the belief stays the user's own
    (feature brief's 'critical separation'). Call revisePOVEntry instead
    if the user is directly changing their own stated belief."""
    _auth(x_api_key)
    try:
        record = user_pov.attach_pov_evidence(pov_id, body.relation, body.evidence, source_url=body.source_url, confidence=body.confidence)
    except user_pov.UserPovError as exc:
        raise HTTPException(404 if "No POV entry" in str(exc) else 422, detail=str(exc))
    return {"ok": True, "evidence": record}


class CreateCompetitiveBriefBody(BaseModel):
    generated_for: str = Field("", description="Who this is being prepared for, if relevant. Optional.")


@app.post("/competitive-briefs/{competitor_slug}", tags=["write"], operation_id="createCompetitiveBrief")
def post_create_competitive_brief(competitor_slug: str, body: CreateCompetitiveBriefBody, x_api_key: Optional[str] = Header(None)):
    """Persists a versioned Competitive Brief document for one competitor --
    a markdown rendering of getCompetitorProfile's own computed content
    (positioning, Todd's POV, gap analysis, category battle cards, evidence
    log), written to system/artifact_vault/ and indexed. Fully computed
    from already-persisted data -- no authorization quote required, unlike
    the customer-facing artifacts. Regenerating supersedes the prior
    version rather than replacing it in place.

    RB-2026-09-25: auto-creates a competitor shell if `competitor_slug`
    doesn't match one yet (Todd's explicit direction -- accepts that a
    typo'd slug now creates a new, mostly-empty shell instead of erroring,
    same tolerance bulkImportCompetitors already has for a batch). Prefer
    listCompetitors/createCompetitor first when you have a real name to
    resolve against, since that seeds real aliases/category data this
    fallback can't."""
    _auth(x_api_key)
    result = competitive_brief.generate_competitive_brief(competitor_slug, generated_for=body.generated_for)
    return {"competitor_slug": result["competitor_slug"], "markdown": result["markdown"], "version": result["version"]}


@app.get("/competitive-briefs/{competitor_slug}", tags=["compute"], operation_id="getCompetitiveBrief")
def get_competitive_brief_detail(competitor_slug: str, x_api_key: Optional[str] = Header(None)):
    """Return the current persisted Competitive Brief for one competitor,
    verbatim. Use RULE-0 discipline: display the markdown as-is rather than
    re-summarizing it. For a fresh computed view without persisting it, use
    getCompetitorProfile instead."""
    _auth(x_api_key)
    current = competitive_brief.get_current_competitive_brief(competitor_slug, include_content=True)
    if current is None:
        raise HTTPException(404, detail=f"No Competitive Brief persisted yet for competitor '{competitor_slug}'. Call createCompetitiveBrief to make one.")
    return {"competitor_slug": competitor_slug, "markdown": current.get("content"), "version": current}


class CreateValueWedgeBody(BaseModel):
    generated_for: str = Field("", description="Who this is being prepared for, if relevant. Optional.")


@app.post("/value-wedges/{competitor_slug}", tags=["write"], operation_id="createValueWedge")
def post_create_value_wedge(competitor_slug: str, body: CreateValueWedgeBody, x_api_key: Optional[str] = Header(None)):
    """Persists a versioned Value Wedge document for one competitor -- a
    sales-enablement document distinct from Competitive Brief (a general
    profile) and Battle Card (category-wide, multi-competitor): for every
    product line this competitor competes on, pairs the Genius Capability
    Library's content for that category with that category's battle card
    and the competitor's own vs_genius.competitor_advantages ("where they
    push back"), written to system/artifact_vault/ and indexed. Fully
    computed from already-persisted data -- no authorization quote
    required. Regenerating supersedes the prior version rather than
    replacing it in place.

    RB-2026-09-25: auto-creates a competitor shell if `competitor_slug`
    doesn't match one yet, same as createCompetitiveBrief -- prefer
    listCompetitors/createCompetitor first when you have a real name to
    resolve against, since that seeds real aliases/category data this
    fallback can't. Honest-blank sections point at
    setCompetitorProductLines/createGeniusCapability/setCategoryBattleCard
    rather than fabricating content when something's missing."""
    _auth(x_api_key)
    result = value_wedge.generate_value_wedge(competitor_slug, generated_for=body.generated_for)
    markdown = value_wedge.render_value_wedge_markdown(result["data"])
    return {"competitor_slug": result["competitor_slug"], "markdown": markdown, "version": result["version"]}


@app.get("/value-wedges/{competitor_slug}", tags=["compute"], operation_id="getValueWedge")
def get_value_wedge_detail(competitor_slug: str, x_api_key: Optional[str] = Header(None)):
    """Return the current persisted Value Wedge for one competitor,
    verbatim. Use RULE-0 discipline: display the markdown as-is rather
    than re-summarizing it."""
    _auth(x_api_key)
    current = value_wedge.get_current_value_wedge(competitor_slug, include_content=True)
    if current is None:
        raise HTTPException(404, detail=f"No Value Wedge persisted yet for competitor '{competitor_slug}'. Call createValueWedge to make one.")
    markdown = value_wedge.render_value_wedge_markdown(current["data"])
    return {"competitor_slug": competitor_slug, "markdown": markdown, "version": current}


@app.post("/competitors/{competitor_slug}/sync", tags=["write"], operation_id="syncCompetitorIntelligence")
def post_sync_competitor_intelligence(competitor_slug: str, x_api_key: Optional[str] = Header(None)):
    """Mechanically pull any new evidence for this competitor from sources
    RB already gathers -- ecosystem_intelligence.json signals tagged to
    this vendor, and account_intelligence/ docs mentioning it by name or
    alias. Idempotent (never re-appends evidence already on file). This is
    the 'intelligence gathering process' half of competitor tracking; for
    something Todd tells you directly, use addCompetitiveNote instead."""
    _auth(x_api_key)
    try:
        result = compintel.sync_from_ecosystem(competitor_slug)
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No competitor intelligence record for slug '{competitor_slug}'. Call listCompetitors to see valid slugs, or POST /competitors to start tracking a new one.")
    return result


class CreateCompetitorBody(BaseModel):
    name: str = Field(..., description="Competitor/vendor name, e.g. 'PAR Technology'. Matched against ecosystem_intelligence.json's vendor entities when possible; creates a new tracked competitor if none exists yet. Safe to call again for a name that already exists -- returns already_tracked:true rather than duplicating or erroring.")


def _competitor_creation_error_detail(exc: "compintel.CompetitorCreationError") -> dict:
    return {
        "error": "competitor_creation_failed",
        "stage": exc.stage,
        "competitor_slug": exc.slug,
        "shell_created": exc.shell_created,
        "registered": exc.registered,
        "cause": str(exc.cause),
        "safe_to_retry": True,
        "note": (
            "register_competitor() is idempotent and ensure_competitor() always "
            "retries registration regardless of whether the shell already exists "
            "(RB-DEFECT-071 fix) -- retrying this exact call is always safe and "
            "will not create a duplicate or leave an ambiguous record."
        ),
    }


@app.post("/competitors", tags=["write"], operation_id="createCompetitor")
def post_create_competitor(body: CreateCompetitorBody, x_api_key: Optional[str] = Header(None)):
    """Start tracking a new competitor. Deliberately cheap and ungated --
    same philosophy as generateAccountBackgroundBrief for a new brand:
    tracking competitive intelligence should never require the kind of
    explicit authorization createBlueSheetAccount requires. Also runs an
    initial sync automatically. For adding many vendors at once (e.g. a
    trade-show buyers guide roster), call bulkImportCompetitors instead --
    it runs the same pipeline with bounded, safe sequencing and per-item
    receipts rather than many concurrent calls to this endpoint (see
    RB-DEFECT-071)."""
    _auth(x_api_key)
    try:
        result = compintel.generate_profile(body.name)
    except compintel.CompetitorCreationError as exc:
        raise HTTPException(status_code=500, detail=_competitor_creation_error_detail(exc)) from exc
    return result


class BulkImportCompetitorsBody(BaseModel):
    names: list[str] = Field(..., description="Vendor/competitor names to add, e.g. an entire trade-show buyers-guide roster. Real names only -- never invented.")
    dry_run: bool = Field(False, description="When true, classify every name (created/already_exists/rejected_with_reason) with zero writes -- a preview. When false, actually create/register/sync each one.")


@app.post("/competitors/bulk-import", tags=["write"], operation_id="bulkImportCompetitors")
def post_bulk_import_competitors(body: BulkImportCompetitorsBody, x_api_key: Optional[str] = Header(None)):
    """Add many competitors at once -- the right tool for a trade-show
    vendor roster (e.g. the FSTEC Buyers Guide) instead of many individual
    createCompetitor calls. RB-DEFECT-071 (2026-09-11): 142 concurrent
    createCompetitor calls from chat is exactly what corrupted the shared
    competitor registry and took listCompetitors down -- this endpoint
    replaces that pattern with safe, sequential, per-item processing (the
    registry write is now locked and atomic, so there's no concurrency
    benefit to parallelizing client-side, and doing it here means the
    lock's own serialization is the only synchronization needed).

    Each name is classified:
      created -- newly tracked, sync attempted.
      already_exists -- resolved to a competitor already tracked (exact
        case-insensitive name/alias match); already_tracked in its own
        result reflects this even in a real (non-dry-run) run.
      rejected_with_reason -- e.g. matches Genius/Global Payments (RB's own
        company) -- never tracked as a competitor of itself.
      failed -- a real per-item error; includes the same structured detail
        createCompetitor's own failure would (stage, what already landed,
        safe-retry guidance) -- one failure never aborts the rest of the
        batch.

    dry_run:true classifies every name with zero writes -- call this first
    to preview a large roster before committing it."""
    _auth(x_api_key)
    results = []
    counts = {"created": 0, "already_exists": 0, "rejected_with_reason": 0, "failed": 0}

    for raw_name in body.names:
        name = (raw_name or "").strip()
        if not name:
            continue

        if compintel_common.is_own_company(name):
            results.append({
                "name": name, "status": "rejected_with_reason",
                "reason": "Matches RB's own company (Genius/Global Payments) -- never tracked as a competitor of itself.",
            })
            counts["rejected_with_reason"] += 1
            continue

        slug, vendor_entity_id, is_new_shell = compintel.resolve_competitor(name)

        if body.dry_run:
            results.append({
                "name": name, "competitor_slug": slug,
                "status": "created" if is_new_shell else "already_exists",
            })
            counts["created" if is_new_shell else "already_exists"] += 1
            continue

        try:
            profile = compintel.generate_profile(name)
        except compintel.CompetitorCreationError as exc:
            results.append({
                "name": name, "competitor_slug": exc.slug, "status": "failed",
                "error": _competitor_creation_error_detail(exc),
            })
            counts["failed"] += 1
            continue

        status = "already_exists" if profile.get("already_tracked") else "created"
        results.append({
            "name": name, "competitor_slug": profile["competitor_slug"], "status": status,
        })
        counts[status] += 1

    al.log_mutation_executed(
        f"bulkImportCompetitors: {len(body.names)} requested, "
        f"created={counts['created']} already_exists={counts['already_exists']} "
        f"rejected={counts['rejected_with_reason']} failed={counts['failed']} dry_run={body.dry_run}",
        source="POST /competitors/bulk-import",
    )
    return {
        "ok": True,
        "dry_run": body.dry_run,
        "requested": len(body.names),
        "counts": counts,
        "results": results,
    }


class AddCompetitiveNoteBody(BaseModel):
    note: str = Field(..., description="The competitive fact/observation itself. Verbatim from what the user actually said or from a real, already-persisted source -- never invented or inferred.")
    category: str = Field("other", description=f"One of: {sorted(compintel_common.VALID_EVIDENCE_CATEGORIES)}")
    source: str = Field("Todd Vahlsing", description="Who/where this came from.")
    confidence: str = Field("high", description="high | medium | low")


@app.post("/competitors/{competitor_slug}/note", tags=["write"], operation_id="addCompetitiveNote")
def post_add_competitive_note(competitor_slug: str, body: AddCompetitiveNoteBody, x_api_key: Optional[str] = Header(None)):
    """Add one human-supplied competitive intelligence note -- the 'user
    input' half of competitor tracking, alongside syncCompetitorIntelligence's
    automated feed. Never call this with a summary or interpretation you
    generated yourself; only with what the user actually told you or a real
    source they gave you."""
    _auth(x_api_key)
    try:
        result = compintel.add_competitive_note(
            competitor_slug, body.note, category=body.category,
            source=body.source, confidence=body.confidence,
        )
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No competitor intelligence record for slug '{competitor_slug}'. Call listCompetitors to see valid slugs, or POST /competitors to start tracking a new one.")
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    return result


class SetCompetitorProductLinesBody(BaseModel):
    product_lines: list[str] = Field(
        ..., description=f"Complete replacement set (not additive) of which Genius product line(s) this competitor directly competes on. Valid values: {sorted(compintel_common.GENIUS_PRODUCT_LINES)}",
    )


@app.post("/competitors/{competitor_slug}/product-lines", tags=["write"], operation_id="setCompetitorProductLines")
def post_set_competitor_product_lines(competitor_slug: str, body: SetCompetitorProductLinesBody, x_api_key: Optional[str] = Header(None)):
    """Declare which Genius product line(s) this competitor directly
    competes on -- RB-2026-09-01: competitor_intelligence.set_competes_on()
    existed but was never wired to any endpoint, so 6 of 9 tracked
    competitors had an empty battle card with no way to fill it in except a
    raw Python call. Replaces the full set every call (not additive) --
    pass the complete current list."""
    _auth(x_api_key)
    try:
        result = compintel.set_competes_on(competitor_slug, body.product_lines)
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No competitor intelligence record for slug '{competitor_slug}'. Call listCompetitors to see valid slugs.")
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    return result


class AddCompetitorGapPointBody(BaseModel):
    side: str = Field(..., description="'genius' (Genius wins/leads here) or 'competitor' (the competitor wins/leads here).")
    point: str = Field(..., description="One discrete, sourced claim -- never a freeform paragraph and never invented. Verbatim from what the user said or a real, already-persisted source.")
    evidence_id: Optional[str] = Field(None, description="Optional pointer to the real source this point comes from.")


@app.post("/competitors/{competitor_slug}/gap-points", tags=["write"], operation_id="addCompetitorGapPoint")
def post_add_competitor_gap_point(competitor_slug: str, body: AddCompetitorGapPointBody, x_api_key: Optional[str] = Header(None)):
    """Add one real, sourced 'vs. Genius' battle-card point -- RB-2026-09-01:
    competitor_intelligence.add_gap_point() existed but was never wired to
    any endpoint. Only extract what's actually stated in the source or what
    the user actually told you -- never a claim you inferred or summarized
    yourself."""
    _auth(x_api_key)
    try:
        result = compintel.add_gap_point(competitor_slug, body.side, body.point, evidence_id=body.evidence_id)
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No competitor intelligence record for slug '{competitor_slug}'. Call listCompetitors to see valid slugs.")
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    return result


class SetCategoryBattleCardBody(BaseModel):
    status: Optional[str] = Field(None, description=f"One of: {sorted(compintel_common.CATEGORY_BATTLE_CARD_STATUSES)}")
    confidence_pct: Optional[int] = Field(None, description="0-100.")
    rm_plain_english_posture: Optional[str] = Field(None, description="Plain-English RM talk track for this category. Verbatim from what the user actually said or a real, already-persisted source -- never invented.")
    when_to_bring_todd_in: Optional[str] = Field(None, description="RM escalation cue for this category.")
    evidence_id: Optional[str] = Field(None, description="Optional pointer to the real source backing this update.")


@app.post("/competitors/{competitor_slug}/battle-card/{category}", tags=["write"], operation_id="setCategoryBattleCard")
def post_set_category_battle_card(competitor_slug: str, category: str, body: SetCategoryBattleCardBody, x_api_key: Optional[str] = Header(None)):
    """Create-or-update the RM-facing battle card for this competitor in
    ONE tech-stack category -- distinct from the vendor-level
    positioning_summary/todds_pov (setCompetitorProductLines/
    addCompetitiveNote), since one competitor can compete differently
    across categories (e.g. PAR on platform vs. loyalty). Only overwrites
    fields explicitly passed; omit a field to leave it unchanged. Never
    call this with a summary or interpretation you generated yourself --
    only with what the user actually told you or a real source they gave
    you. Call listTechStackCategories for the real category names, plus
    'platform'/'delivery_aggregation' (RM-only categories with no
    per-brand market-share data yet)."""
    _auth(x_api_key)
    try:
        result = compintel.upsert_category_battle_card(
            competitor_slug, category, status=body.status, confidence_pct=body.confidence_pct,
            rm_plain_english_posture=body.rm_plain_english_posture,
            when_to_bring_todd_in=body.when_to_bring_todd_in, evidence_id=body.evidence_id,
        )
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No competitor intelligence record for slug '{competitor_slug}'. Call listCompetitors to see valid slugs.")
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    return result


class AddCategoryBattleCardPointBody(BaseModel):
    field: str = Field(..., description="One of: 'listen_for', 'discovery_questions', 'red_flags'.")
    point: str = Field(..., description="One discrete, sourced point -- never a freeform paragraph and never invented. Verbatim from what the user said or a real, already-persisted source.")
    evidence_id: Optional[str] = Field(None, description="Optional pointer to the real source this point comes from.")


@app.post("/competitors/{competitor_slug}/battle-card/{category}/points", tags=["write"], operation_id="addCategoryBattleCardPoint")
def post_add_category_battle_card_point(competitor_slug: str, category: str, body: AddCategoryBattleCardPointBody, x_api_key: Optional[str] = Header(None)):
    """Append one real, sourced point to a category battle card's
    listen_for/discovery_questions/red_flags list. The battle card for
    this (competitor_slug, category) must already exist -- call
    setCategoryBattleCard first if it doesn't. Only extract what's
    actually stated in the source or what the user actually told you --
    never a claim you inferred or summarized yourself."""
    _auth(x_api_key)
    try:
        result = compintel.add_category_battle_card_point(
            competitor_slug, category, body.field, body.point, evidence_id=body.evidence_id,
        )
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No competitor intelligence record for slug '{competitor_slug}'. Call listCompetitors to see valid slugs.")
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    return result


class AddGeniusCapabilityBody(BaseModel):
    point: str = Field(..., description="One discrete capability Genius actually offers -- never a freeform paragraph, never a claim you inferred or embellished. Verbatim from what Todd actually said.")
    why_it_matters: Optional[str] = Field(None, description="Optional: why this capability matters to a customer. Also verbatim from what Todd said, not your own elaboration.")
    evidence_id: Optional[str] = Field(None, description="Optional pointer to a real source backing this claim.")


@app.post("/genius-capabilities/{category}", tags=["write"], operation_id="addGeniusCapability")
def post_add_genius_capability(category: str, body: AddGeniusCapabilityBody, x_api_key: Optional[str] = Header(None)):
    """Add one real, discrete Genius product capability for ONE tech-stack
    category -- the Genius Capability Library, the structured "what Genius
    offers" baseline the Value Wedge (createValueWedge) draws from.
    RB-2026-09-25. This is Todd's own curated product knowledge, same
    trust posture as todds_pov -- only ever record what Todd actually
    said, never a claim you inferred, summarized, or generated yourself.
    Append-only; no update/delete."""
    _auth(x_api_key)
    try:
        result = genius_capabilities.add_capability(
            category, body.point, why_it_matters=body.why_it_matters, evidence_id=body.evidence_id,
        )
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    return result


@app.get("/genius-capabilities/{category}", tags=["read"], operation_id="getGeniusCapabilities")
def get_genius_capabilities(category: str, x_api_key: Optional[str] = Header(None)):
    """List every Genius capability point on file for one tech-stack
    category."""
    _auth(x_api_key)
    try:
        return {"category": category, "capabilities": genius_capabilities.list_capabilities(category)}
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))


@app.get("/genius-capabilities", tags=["read"], operation_id="listAllGeniusCapabilities")
def get_all_genius_capabilities(x_api_key: Optional[str] = Header(None)):
    """List every Genius capability point on file, across every product
    line -- always returns all product-line keys, even ones with nothing
    filled in yet (honest-blank, not omitted)."""
    _auth(x_api_key)
    return {"capabilities": genius_capabilities.list_all_capabilities()}


@app.post("/competitors/{competitor_slug}/review/{review_id}/resolve", tags=["write"], operation_id="resolveCompetitorReviewItem")
def post_resolve_competitor_review_item(competitor_slug: str, review_id: str, resolution: str, x_api_key: Optional[str] = Header(None)):
    """Todd resolves one flagged review item (a new material signal or a
    stale battle card flagged by the daily competitor_intelligence_review_scan)
    -- marks it resolved so it stops appearing as pending. This does NOT
    itself change the underlying category battle card; that stays a direct
    edit via setCategoryBattleCard, same principle as
    resolveMasterAccountPlanReviewItem and Blue Sheet's buying-influence
    ratings staying human-only. `resolution` should be a short note of what
    Todd decided (e.g. 'reviewed, no change needed' or 'updated battle
    card, see chat')."""
    _auth(x_api_key)
    queue = compintel_common.load_review_queue()
    matched = False
    for item in queue.get("pending_reviews", []):
        if item.get("competitor_slug") == competitor_slug and item.get("review_id") == review_id and item.get("status") == "pending":
            item["status"] = "resolved"
            item["resolution"] = resolution
            item["resolved_at"] = compintel_common.now_iso()
            matched = True
    if not matched:
        raise HTTPException(404, detail=f"No pending review item found for competitor_slug='{competitor_slug}', review_id='{review_id}'.")
    compintel_common.save_review_queue(queue)
    return {"ok": True, "competitor_slug": competitor_slug, "review_id": review_id, "resolution": resolution}


class CreateBlueSheetAccountBody(BaseModel):
    account_slug: str = Field(..., description="e.g. 'mcdonalds'")
    display_name: str = Field(..., description="e.g. \"McDonald's\"")
    account_data: dict = Field(..., description="opportunities[], qualification, strategic_position, buying_influences[], technology_stack[], commercial_models[], latest_review, bottom_line -- see blue_sheets/_engine/render.py for the exact field-by-field schema each tab reads. Must contain REAL, substantive content -- an all-empty account_data is rejected.")
    brand_profile_data: dict = Field(..., description="identity_ownership_footprint[] etc. -- see render_brand_tab in render.py.")
    actions_data: list = Field(default_factory=list, description="actions[] -- see render_actions_tab in render.py.")
    evidence_records: list = Field(default_factory=list, description="Real evidence.jsonl entries citing actual sources for the content above.")
    aliases: list = Field(default_factory=list)
    user_authorization_quote: str = Field(
        ..., min_length=1,
        description=(
            "REQUIRED. A verbatim quote of what the user actually said that "
            "constitutes explicit, unambiguous authorization to create a NEW "
            "Blue Sheet for this specific account -- e.g. 'create a blue "
            "sheet for McDonald's'. Do not paraphrase or invent this -- copy "
            "the user's own words from this conversation. A request to "
            "'update the account plan' for an account with no existing Blue "
            "Sheet is NOT authorization to create one -- if you are not "
            "looking at an explicit Blue Sheet creation request in the "
            "transcript, do not call this endpoint; ask the user first. "
            "RB-2026-08-28: a prior call to this endpoint silently stamped "
            "every creation as Todd-authorized regardless of what was "
            "actually said, producing an empty, unauthorized 'worldpay' "
            "Blue Sheet from a portfolio-level document upload. This field "
            "exists so that never happens invisibly again."
        ),
    )


@app.post(
    "/blue-sheets",
    tags=["write"],
    operation_id="createBlueSheetAccount",
)
def post_create_blue_sheet_account(
    body: CreateBlueSheetAccountBody,
    x_api_key: Optional[str] = Header(None),
):
    """Creates a brand-new Blue Sheet account -- a plan for an account under
    ACTIVE engagement -- and renders the real xlsx workbook, the same way
    Pollo Campero's/Five Guys'/Del Taco's were built.

    Real gap closed 2026-08-27: there was previously NO way to create a new
    Blue Sheet at all, from chat or otherwise -- every existing one was
    hand-built by a prior session. This is the first real, repeatable path.

    Deliberately requires the caller to supply already-curated, real,
    sourced structured content in the exact schema render.py's tabs read
    (account_data/brand_profile_data/actions_data) -- this endpoint does
    NOT extract facts from free text itself. That judgment call (what's
    real vs. still unknown, how confident to mark something) has to come
    from whoever is curating the account from real source material, the
    same discipline used everywhere else in this system. Never call this
    with invented/guessed field values. Blue Sheets require Todd's explicit
    direction to create (unlike Account Research, which is cheap to start)
    -- only call this when the user has explicitly asked for a new Blue
    Sheet, not as a substitute for an Account Background Brief request.

    RB-2026-08-28: two structural guards added after a real incident (an
    empty "worldpay" Blue Sheet created from a portfolio-level, multi-
    account document upload -- the user had NOT asked for a Blue Sheet, and
    every substantive field was blank). (1) account_data with no real
    content in opportunities/commercial_models/buying_influences/
    qualification.criteria/bottom_line is rejected (422). (2)
    user_authorization_quote is now required -- a verbatim quote of the
    user's own words authorizing this specific creation, not a paraphrase.
    A generic "update the account plan" request is NOT sufficient grounds
    to call this endpoint for an account that doesn't already have a Blue
    Sheet -- ask the user first.
    """
    _auth(x_api_key)
    if _blue_sheet_create is None:
        raise HTTPException(500, detail="Blue Sheet creation engine not available on this server.")
    try:
        xlsx_path = _blue_sheet_create.create_blue_sheet_account(
            body.account_slug, body.display_name,
            account_data=body.account_data, brand_profile_data=body.brand_profile_data,
            actions_data=body.actions_data, evidence_records=body.evidence_records,
            aliases=body.aliases, authorized_by="Todd Vahlsing",
            activation_note=(
                f"Created via createBlueSheetAccount, "
                f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}. "
                f"User authorization quote: \"{body.user_authorization_quote}\""
            ),
        )
    except FileExistsError as exc:
        raise HTTPException(409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"Blue Sheet creation failed: {exc}") from exc
    return {"slug": body.account_slug, "workbook_path": str(xlsx_path.relative_to(core.PROJECT_DIR))}


@app.post("/blue-sheets/{account_slug}/activate-research-shell", tags=["write"], operation_id="activateBlueSheetResearchShell")
def post_activate_blue_sheet_research_shell(account_slug: str, body: CreateBlueSheetAccountBody, x_api_key: Optional[str] = Header(None)):
    """Populate a research-only shell after explicit Blue Sheet authorization.

    This preserves its existing evidence ledger and refuses to overwrite an
    account with active opportunities or an already active Blue Sheet.
    """
    _auth(x_api_key)
    if body.account_slug != account_slug:
        raise HTTPException(422, detail="Body account_slug must match path account_slug.")
    if _blue_sheet_create is None:
        raise HTTPException(500, detail="Blue Sheet creation engine not available on this server.")
    try:
        xlsx_path = _blue_sheet_create.activate_existing_research_shell(
            account_slug, body.display_name,
            account_data=body.account_data, brand_profile_data=body.brand_profile_data,
            actions_data=body.actions_data, aliases=body.aliases,
            authorized_by="Todd Vahlsing",
            activation_note=(
                f"Activated research shell via activateBlueSheetResearchShell, "
                f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}. "
                f"User authorization quote: \"{body.user_authorization_quote}\""
            ),
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"Blue Sheet shell activation failed: {exc}") from exc
    return {"slug": account_slug, "workbook_path": str(xlsx_path.relative_to(core.PROJECT_DIR))}


class AddBlueSheetEvidenceBody(BaseModel):
    excerpt: str = Field(..., description="The real evidence text -- verbatim or a close paraphrase of what was actually said/written in a real source. Never a claim you inferred or generalized.")
    source_type: str = Field(..., description="e.g. 'internal_pricing_call_transcript', 'meeting_notes', 'email'. Describes what kind of real source this came from.")
    extracted_claims: list = Field(default_factory=list, description="Specific factual claims this evidence supports, each a short verbatim-grounded statement -- e.g. 'Project management fee will be justified after RFP stage, not included upfront'. Never invent a claim not actually present in the source.")
    event_date: Optional[str] = Field(None, description="YYYY-MM-DD the underlying event/call/email actually happened. Defaults to today if not given.")
    participants: list = Field(default_factory=list, description="Real named people actually present/party to this evidence, if known.")
    opportunity_ids: list = Field(default_factory=list, description="Real opportunity_id(s) from this account's account.json this evidence relates to, if known.")
    source_author: Optional[str] = Field(None, description="Who authored/reported this evidence, if known.")
    confidence: str = Field("medium", description="high | medium | low")
    scope: Optional[str] = Field(None, description="e.g. 'opportunity:opp-...' -- defaults to account-level scope if not given.")
    evidence_class: str = Field("user_reported", description="e.g. 'seller_authored_meeting_recap', 'user_reported', 'internal_pricing_call_transcript'.")
    limitations: str = Field("", description="Any caveat on how much this evidence can be trusted/generalized, if relevant.")
    durable_source_id: Optional[str] = Field(None, description="Path/id of the real underlying source document, if one exists (e.g. a capture file_id or ingestion_id).")


@app.post("/blue-sheets/{account_slug}/evidence", tags=["write"], operation_id="addBlueSheetEvidence")
def post_add_blue_sheet_evidence(account_slug: str, body: AddBlueSheetEvidenceBody, x_api_key: Optional[str] = Header(None)):
    """Add one real, structured evidence record to an existing Blue Sheet --
    the reviewed, one-call counterpart to uploadAndIngestFile's automatic
    reference-linking (which only ever appends a fact-free 'this document
    mentions this account' pointer, never a specific claim).

    RB-2026-08-28: real gap -- a genuine internal pricing-call transcript
    for Pollo Campero (an active RFP, specific fee structures, specific
    hardware scenarios) produced no queryable path into the Blue Sheet at
    all; the only recovery was a manual JSON edit. This endpoint is that
    path made repeatable. Only call this with evidence actually present in
    a real source you were given or that the user actually told you --
    never with your own summary, inference, or generalization presented as
    fact. If you're not sure a specific claim is real vs. your own
    interpretation, leave extracted_claims narrower rather than broader."""
    _auth(x_api_key)
    if _blue_sheet_add_evidence is None:
        raise HTTPException(500, detail="Blue Sheet evidence engine not available on this server.")
    try:
        result = _blue_sheet_add_evidence.add_evidence(
            account_slug,
            excerpt=body.excerpt, source_type=body.source_type,
            extracted_claims=body.extracted_claims, event_date=body.event_date,
            participants=body.participants, opportunity_ids=body.opportunity_ids,
            source_author=body.source_author, confidence=body.confidence,
            scope=body.scope, evidence_class=body.evidence_class,
            limitations=body.limitations, durable_source_id=body.durable_source_id,
        )
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No Blue Sheet account for slug '{account_slug}'. Call listBlueSheetAccounts to see valid slugs.")
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    return result


class AddBlueSheetCustomerArtifactBody(BaseModel):
    title: str = Field(..., description="e.g. 'Pollo Campero DMB Pricing Matrix — Customer-Facing Draft'.")
    columns: list = Field(..., description="Column headers, in order, e.g. ['Pricing Structure', 'Best Fit', 'Site Count', ...]. Real headers only -- never invent a column that wasn't actually in the source table.")
    rows: list = Field(..., description="Table rows -- each a list of cell values, in the same order as `columns` and the same length. Use 'TBD' (not '$0' or a made-up number) for a cell that is genuinely not yet decided.")
    notes: Optional[str] = Field(None, description="Optional footer note, e.g. what was deliberately excluded and why.")
    source_evidence_id: Optional[str] = Field(None, description="The addBlueSheetEvidence evidence_id this table came from, if one exists (links the clean export back to its real source).")


@app.post("/blue-sheets/{account_slug}/customer-artifacts", tags=["write"], operation_id="addBlueSheetCustomerArtifact")
def post_add_blue_sheet_customer_artifact(account_slug: str, body: AddBlueSheetCustomerArtifactBody, x_api_key: Optional[str] = Header(None)):
    """Save a structured, customer-facing table (e.g. a pricing matrix for
    an RFP) on a Blue Sheet account, so it can actually become the
    downloadable Excel file a customer-facing request asks for --
    getBlueSheetCustomerArtifactDownloadLink serves it.

    RB-2026-09-02: real gap -- Todd asked to save a customer-facing DMB
    pricing matrix, and the only tool available (addBlueSheetEvidence)
    stored it as markdown text inside an evidence excerpt. Nothing renders
    evidence.jsonl into any spreadsheet, so that table could never become
    an Excel file. This is deliberately a SEPARATE store from evidence and
    from the internal Blue Sheet workbook (render.py's
    Standard_Blue_Sheet.xlsx, which has Commercial Model/red-flag/rating
    tabs never meant for a customer) -- use this specifically for content
    meant to be handed to the customer as-is, and addBlueSheetEvidence for
    everything else. Only ever with real column headers and real row
    values actually present in a real source or stated by the user -- a
    cell that's genuinely undecided should be 'TBD', never invented."""
    _auth(x_api_key)
    if _blue_sheet_customer_artifact is None:
        raise HTTPException(500, detail="Blue Sheet customer-artifact engine not available on this server.")
    try:
        result = _blue_sheet_customer_artifact.add_customer_artifact(
            account_slug,
            title=body.title, columns=body.columns, rows=body.rows,
            notes=body.notes, source_evidence_id=body.source_evidence_id,
        )
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No Blue Sheet account for slug '{account_slug}'. Call listBlueSheetAccounts to see valid slugs.")
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    return result


@app.get("/blue-sheets/{account_slug}/customer-artifacts", tags=["read"], operation_id="listBlueSheetCustomerArtifacts")
def get_list_blue_sheet_customer_artifacts(account_slug: str, x_api_key: Optional[str] = Header(None)):
    """List the customer-facing artifacts saved on one Blue Sheet account
    (title, artifact_id, created_date) -- use to find the right
    artifact_id for getBlueSheetCustomerArtifactDownloadLink, or to answer
    'what customer-facing exports exist for [account]'. Distinct from the
    account's evidence.jsonl (free-text) and from its main Blue Sheet
    workbook (getBlueSheetDownloadLink)."""
    _auth(x_api_key)
    if _blue_sheet_customer_artifact is None:
        raise HTTPException(500, detail="Blue Sheet customer-artifact engine not available on this server.")
    try:
        artifacts = _blue_sheet_customer_artifact.list_customer_artifacts(account_slug)
    except FileNotFoundError:
        raise HTTPException(404, detail=f"No Blue Sheet account for slug '{account_slug}'. Call listBlueSheetAccounts to see valid slugs.")
    return {"account_slug": account_slug, "artifacts": artifacts}


@app.get(
    "/intelligence-index",
    tags=["compute"],
    operation_id="queryIntelligenceIndex",
)
def get_intelligence_index(
    query: str = "",
    x_api_key: Optional[str] = Header(None),
):
    """Query the unified 'what do we have on X, and where' index across
    EVERY intelligence-bearing subsystem: system/account_intelligence/
    (Todd's own hand-authored account research), system/artifacts/registry
    .json (Micro Graphs, account dossiers), blue_sheets/ (active-engagement
    accounts), system/account_research/ (pre-engagement background briefs),
    and the canonical Top 1500 restaurant-brand universe in
    system/ecosystem_intelligence.json. Returns pointers (path,
    resource_type, title, date) -- not the content itself. Ecosystem-brand
    hits include the canonical brand ID, rank, aliases, and relationship
    count so callers can route to the ecosystem query for full detail.

    Built 2026-08-27 after a real same-day failure: a McDonald's Background
    Brief request silently missed 74KB of Todd's own real prior account
    research because nothing scanned system/account_intelligence/ at all --
    found only by a brute-force grep. Call this whenever asked 'what do we
    have on [brand/topic]' generally, or before assuming RBB has no prior
    work on something -- check here first, across every subsystem, rather
    than only checking the one tool that happens to come to mind.

    No query -> returns the full index (useful for 'what has RB researched
    so far').
    """
    _auth(x_api_key)
    if not query:
        idx = intelligence_index._load_index()
        return {"entry_count": len(idx.get("entries", [])), "entries": idx.get("entries", [])}
    matches = intelligence_index.find(query)
    return {"query": query, "match_count": len(matches), "entries": matches}


# RB-2026-08-31: safety cap on the flat extracted_text convenience field --
# real per-section/per-page text is never truncated (see below), only this
# single-string rollup, so a huge document stays fully retrievable via
# section_index without ever forcing one oversized tool response.
_UPLOADED_DOCUMENT_INLINE_TEXT_CAP = 40_000


@app.get(
    "/documents/{document_id}",
    tags=["read"],
    operation_id="getUploadedDocument",
)
def get_uploaded_document(
    document_id: str,
    section_index: Optional[int] = None,
    x_api_key: Optional[str] = Header(None),
):
    """Retrieve the full extracted text of a document previously uploaded
    through uploadAndIngestFile, by the document_id that call returned.

    RB-2026-08-31 (Todd's defect report, Pollo Campero RFP): uploadAndIngestFile
    extracted a DOCX/PDF's full text mechanically, used it in-memory for
    triage/mutations/reference-linking, then discarded it -- only a 400-char
    excerpt ever survived. There was no way to retrieve "what does this
    document actually say" after that one call, so the GPT could not do a
    section-by-section review of an uploaded response document against
    known account requirements. This endpoint is that retrieval path.

    With no section_index: returns the full record, including every
    section/page (never truncated) and a flat extracted_text convenience
    field that IS truncated past ~40,000 characters (set truncated=true when
    so) -- use section_index against the real, untruncated sections list for
    a large document instead of relying on the flat field.

    With section_index: returns just that one section/page (0-based index
    into the sections list from a prior call) -- useful once you know which
    part of a large document you need.

    sections is [] when the format carries no mechanically-derivable
    structure (e.g. most non-.docx/.pdf uploads, or a scanned/image PDF with
    no text layer) -- extracted_text is still the real, full extraction in
    that case, just without a section/page breakdown.
    """
    _auth(x_api_key)
    record = uploaded_document_store.load_document(document_id)
    if record is None:
        raise HTTPException(
            404,
            detail=(
                f"No uploaded document found for document_id '{document_id}'. "
                "This is the id uploadAndIngestFile returned when the document was "
                "uploaded -- call queryIntelligenceIndex to search by filename/account "
                "if you don't have it."
            ),
        )
    sections = record.get("sections") or []
    if section_index is not None:
        if section_index < 0 or section_index >= len(sections):
            raise HTTPException(
                400,
                detail=f"section_index {section_index} out of range -- this document has {len(sections)} section(s).",
            )
        return {
            "document_id": document_id,
            "filename": record.get("filename"),
            "section_index": section_index,
            "section_count": len(sections),
            "section": sections[section_index],
        }
    text = record.get("extracted_text") or ""
    truncated = len(text) > _UPLOADED_DOCUMENT_INLINE_TEXT_CAP
    return {
        "document_id": record.get("document_id"),
        "filename": record.get("filename"),
        "content_type": record.get("content_type"),
        "extraction_status": record.get("extraction_status"),
        "account_links": record.get("account_links") or [],
        "extracted_text": text[:_UPLOADED_DOCUMENT_INLINE_TEXT_CAP],
        "truncated": truncated,
        "char_count": record.get("char_count") or len(text),
        "sections": [{"index": i, "heading": s.get("heading")} for i, s in enumerate(sections)],
        "note": (
            f"extracted_text truncated at {_UPLOADED_DOCUMENT_INLINE_TEXT_CAP} characters -- "
            "call again with section_index against the sections list above for the full, "
            "untruncated text of any part of this document."
        ) if truncated else None,
        "warnings": record.get("warnings") or [],
        "created_at": record.get("created_at"),
    }


@app.get(
    "/tech-stack/proposals",
    tags=["read"],
    operation_id="getTechStackRelationshipProposals",
)
def get_tech_stack_relationship_proposals(x_api_key: Optional[str] = Header(None)):
    """List pending candidate tech-stack vendor relationships awaiting
    review -- confirm or reject each via confirmProposal(kind=
    "tech_stack_relationship", id=candidate_id).

    RB-2026-08-31: ecosystem_intelligence.json's real tech-stack coverage
    is thin (confirmed ~17% of tracked brands), too thin to build a
    competitive-landscape/battle-card artifact on directly. This surfaces
    candidates tech_stack_relationship_promotion.py found by scanning
    already-captured signals and account_intelligence notes for a real
    brand+vendor relationship that was never structured into the graph --
    every candidate carries its real source evidence and is never written
    to the graph until explicitly confirmed. category may be null
    ("category_confidence": "unknown") when the source named a vendor
    relationship but not which product category -- that must be resolved
    (or the candidate rejected) before it can be confirmed.
    """
    _auth(x_api_key)
    return {"proposals": tech_stack_relationship_promotion.pending_candidates()}


@app.get(
    "/priority-accounts/publisher-matches",
    tags=["read"],
    operation_id="getPriorityAccountPublisherMatches",
)
def get_priority_account_publisher_matches(x_api_key: Optional[str] = Header(None)):
    """List pending trade-publisher article matches for priority accounts
    (every customers_prospects account + every vendor's Master Account
    Plan) awaiting review -- confirm or reject each via
    confirmProposal(kind="priority_account_publisher_match", id=candidate_id).

    RB-DEFECT-069 (2026-09-10): a real, material Del Taco article was
    invisible to RB for 8 days despite RestaurantNews.com being an
    already-"monitored" source -- monitored only ever meant "feeds the
    general daily-brief news cap," not "every article about a specific
    priority account is captured." priority_account_publisher_scan.py
    searches trade publishers per priority account (name + real aliases)
    instead of relying on the general cap; every candidate carries its
    real source article.

    Auto-apply added 2026-09-25 (Confidence-Based Auto-Recording Phase 5):
    confirming never claims a fact -- only links a source and bumps
    last_evidence_date -- so the daily scan now auto-confirms every
    material match immediately. This list is the rare residual still
    genuinely awaiting review, not the normal flow.
    """
    _auth(x_api_key)
    return {"proposals": priority_account_publisher_scan.pending_candidates()}


@app.get(
    "/ownership/proposals",
    tags=["read"],
    operation_id="getOwnershipChangeProposals",
)
def get_ownership_change_proposals(x_api_key: Optional[str] = Header(None)):
    """List pending M&A/ownership-change candidates awaiting review --
    confirm or reject each via confirmProposal(kind="ownership_change",
    id=candidate_id).

    RB-2026-09-11: M&A signals are already detected daily (entity_alerts.py/
    technomic_watchlist_scan.py/priority_account_publisher_scan.py all feed
    ecosystem_brief.py's funding_event class), but no entity ever had a
    structured owner/parent-company field to promote a verified acquisition
    into -- even one Todd had already fully researched by hand. Every
    candidate carries its real source evidence and a best-effort extracted
    owner name (proposed_owner_confidence: "extracted_from_text" when a
    clear pattern matched the text, "unknown" when nothing did). A null/
    uncertain proposed_owner_name means confirming requires supplying the
    real owner name yourself (confirmProposal's owner_name field) -- never
    guess one, and never assume a prefilled name is correct without reading
    the evidence_excerpt yourself first (acquisition-direction extraction
    is best-effort, not guaranteed).

    Auto-apply added 2026-09-25 (Confidence-Based Auto-Recording Phase 6):
    a candidate with a real extracted owner name (proposed_owner_confidence
    != "unknown") auto-applies during the daily scan -- as a pure add when
    the entity has no owner on file yet, or as an overwrite only when its
    source-derived confidence clears a real margin over what's already
    recorded (otherwise it's appended to the entity's own
    reported_alternates list, not overwritten). What you see here is
    genuinely "unknown"-confidence candidates still needing a human-
    supplied name, not the normal flow of every M&A signal.
    """
    _auth(x_api_key)
    return {"proposals": ownership_promotion.pending_candidates()}


class OwnershipResearchBody(BaseModel):
    entity_id: str = Field(..., description="Real brand or vendor entity id, e.g. 'brand-del-taco' -- resolve via queryIntelligenceIndex if unknown.")
    evidence_text: str = Field(..., description="The real evidence you found -- never a summary or guess. Required.")
    owner_name: Optional[str] = Field(None, description="The real owner/parent-company name, if you already know it -- almost always the case for manual research. Omit only to let RB attempt best-effort extraction from evidence_text.")
    source_url: Optional[str] = None
    source_title: Optional[str] = None
    evidence_date: Optional[str] = Field(
        None,
        description=(
            "YYYY-MM-DD -- the real-world date the acquisition/evidence was published/"
            "discovered (press release dateline, filing date), not today's date. Omit "
            "if genuinely unknown; never guess one."
        ),
    )


@app.post(
    "/ownership/research",
    tags=["ingest"],
    operation_id="reportOwnershipFinding",
)
def post_report_ownership_finding(body: OwnershipResearchBody, x_api_key: Optional[str] = Header(None)):
    """Record one real ownership/M&A finding YOU already researched (real
    web search/fetch, real source) about one brand or vendor -- queues it
    as a candidate via getOwnershipChangeProposals/confirmProposal, same
    review-first discipline as everywhere else in this codebase. This
    endpoint does NOT do its own web research -- do the research first
    (real search/fetch tools), then call this with what you actually
    found. Supply owner_name directly when you already know it (the
    normal case for manual research); never invent one.
    """
    _auth(x_api_key)
    result = ownership_promotion.propose_ownership_finding(
        body.entity_id, evidence_text=body.evidence_text, owner_name=body.owner_name,
        source_url=body.source_url, source_title=body.source_title, evidence_date=body.evidence_date,
    )
    if "error" in result:
        raise HTTPException(400, detail=result["error"])
    return result


@app.get(
    "/executive-moves/proposals",
    tags=["read"],
    operation_id="getExecutiveMoveProposals",
)
def get_executive_move_proposals(x_api_key: Optional[str] = Header(None)):
    """List pending executive-move candidates awaiting review -- confirm
    or reject each via confirmProposal(kind="executive_move",
    id=candidate_id).

    RB-2026-09-11: leadership_change is RBB's most common real material
    signal type, but the named executive was discarded at classification
    time -- never checked against baseline_index.json (RBB's real
    contact-tracking system). Every candidate's `action` field tells you
    which write confirming will do: "update_existing_contact" (someone
    Todd already knows just moved -- a real warm-intro/opportunity signal,
    surfaced via an exact name match against baseline_index.json) or
    "create_new_contact". Read the candidate's evidence_excerpt yourself
    before trusting a prefilled proposed_name/proposed_title -- name/title
    extraction is best-effort, not guaranteed, and proposed_confidence
    "unknown" means nothing was extracted at all.

    Auto-apply added 2026-09-25 (Confidence-Based Auto-Recording Phase 6):
    a candidate with a real extracted name (proposed_confidence !=
    "unknown") auto-applies during the daily scan -- as a pure add (new
    contact, or an existing contact with no current_company on file), or
    as an overwrite only when its source-derived confidence clears a real
    margin over what's already recorded (otherwise it's appended to the
    contact's own reported_alternates list, not overwritten). What you see
    here is genuinely "unknown"-confidence candidates still needing a
    human-supplied name, not the normal flow of every leadership signal.
    """
    _auth(x_api_key)
    return {"proposals": executive_move_promotion.pending_candidates()}


@app.get(
    "/job-postings/proposals",
    tags=["read"],
    operation_id="getJobPostingCandidates",
)
def get_job_posting_candidates(x_api_key: Optional[str] = Header(None)):
    """List pending job-posting candidates awaiting review -- confirm or
    reject each via confirmProposal(kind="job_posting", id=candidate_id).

    RB-2026-09-18, wired 2026-09-25: a customers_prospects account posting a
    "Director of Restaurant Technology"/"POS Program Manager"-shaped role is
    a real pre-signal of an impending tech-stack evaluation, before any
    press release or executive move confirms it. Scoped to customers_
    prospects accounts only (v1) -- a real sales-timing signal there, a
    different (competitor-roadmap) story for vendors, out of scope here.
    Confirming never claims a fact -- only links the posting as a fact-free
    evidence reference (extracted_claims is always []) and bumps the
    account's last_evidence_date, same shape as
    getPriorityAccountPublisherMatches. Because of that, scan() auto-
    confirms every material match immediately -- most candidates here will
    already show status "confirmed" rather than sitting pending; this list
    is what's still awaiting review (a rare residual, not the normal case).
    """
    _auth(x_api_key)
    return {"proposals": job_postings_promotion.pending_candidates()}


@app.get(
    "/watchlist/promotion-candidates",
    tags=["read"],
    operation_id="getWatchlistPromotionCandidates",
)
def get_watchlist_promotion_candidates(x_api_key: Optional[str] = Header(None)):
    """List pending candidates for permanent promotion onto the canonical
    watchlist (system/watchlist_registry.json) -- confirm or reject each via
    confirmProposal(kind="watchlist_promotion", id=candidate_id).

    RB-2026-09-05, auto-apply added 2026-09-25 (Confidence-Based Auto-
    Recording Phase 5): two categories -- confirming either is purely
    additive (just appends a name to the registry, nothing to overwrite),
    so the daily scan now auto-confirms every qualifying candidate
    immediately. Most candidates here will already show up applied in the
    registry rather than sitting pending; this list is the rare residual
    still genuinely awaiting review, not the normal flow.
      - category="brand": a brand that's shown up repeatedly in
        technomic_watchlist_scan.py's daily/weekly scan (real appearance
        count + dates in evidence.appearance_dates) but was never
        permanently added.
      - category="vendor": a real vendor entity already sitting in
        ecosystem_intelligence.json (real active brand-relationship count
        in evidence.active_relationship_count) that isn't on the watchlist
        yet -- mined from data RB already collects, not a new external
        source.
    """
    _auth(x_api_key)
    return {"candidates": watchlist_promotion.pending_candidates()}


class TechStackResearchBody(BaseModel):
    brand_id: str = Field(..., description="Real brand entity id, e.g. 'brand-five-guys' -- resolve via queryIntelligenceIndex if unknown.")
    vendor_name: str = Field(..., description="Vendor name found via your own research. Need not already be a tracked entity.")
    evidence_text: str = Field(..., description="The real evidence you found -- never a summary or guess. Required.")
    category: Optional[str] = Field(None, description="Optional; inferred from evidence_text if omitted -- never guessed from the vendor's general market position.")
    source_url: Optional[str] = None
    source_title: Optional[str] = None
    evidence_date: Optional[str] = Field(
        None,
        description=(
            "YYYY-MM-DD -- the real-world date the evidence was published/discovered "
            "(press release dateline, filing date, post timestamp), not today's date. "
            "Omit if genuinely unknown; never guess one. Threaded through to the "
            "eventual graph source record's published_at once confirmed."
        ),
    )


@app.post(
    "/tech-stack/research",
    tags=["ingest"],
    operation_id="researchBrandTechStack",
)
def post_research_brand_tech_stack(body: TechStackResearchBody, x_api_key: Optional[str] = Header(None)):
    """Record one real tech-stack finding YOU already researched (real web
    search/fetch, real source) about one brand -- queues it as a candidate
    via getTechStackRelationshipProposals/confirmProposal, same review-first
    discipline as everywhere else in this codebase. This endpoint does NOT
    do its own web research -- do the research first (real search/fetch
    tools), then call this with what you actually found. One brand at a
    time; never loop this across many brands unattended -- pacing which
    brands to research is Todd's call.
    """
    _auth(x_api_key)
    result = tech_stack_relationship_promotion.propose_research_finding(
        body.brand_id, body.vendor_name, body.evidence_text,
        category=body.category, source_url=body.source_url, source_title=body.source_title,
        evidence_date=body.evidence_date,
    )
    if "error" in result:
        raise HTTPException(400, detail=result["error"])
    return result


# ---------------------------------------------------------------------------
# RB 9.23 — DEFECT-009: insight_intake.py API wiring
# ---------------------------------------------------------------------------

class InsightIntakeBody(BaseModel):
    text: str = Field(
        ...,
        description=(
            "Free-form text containing a user belief, market thesis, competitive "
            "positioning, strategic observation, or any durable insight to capture."
        ),
    )
    source_type: str = Field(
        "conversation",
        description=(
            "Source type: conversation | paste | email | linkedin_post | "
            "article | meeting_note"
        ),
    )
    context: Optional[dict] = Field(
        None,
        description="Optional context dict (entity, date, tags) to augment classification.",
    )


@app.post(
    "/insight/intake",
    tags=["compute"],
    operation_id="processInsight",
)
def process_insight_intake(
    body: InsightIntakeBody,
    x_api_key: Optional[str] = Header(None),
):
    """Extract and classify strategic insights from free-form text.

    DEFECT-009 fix — wires insight_intake.py (RB 9.23) into the API.

    Accepts any text containing user beliefs, competitive positioning, market
    thesis, or strategic observations and returns classified insight records
    with review-first mutation proposals.

    Returns:
      - insights:             classified insight records (claim, insight_type, confidence,
                              future_use_tags, proposed_mutations)
      - mutation_proposals:   flat list of all review-first strategic memory writes
      - retrieval_tags:       all unique tags for future retrieval
      - persistence_status:   always explicit

    Two-step write flow:
      1. Call processInsight — returns proposals with requires_confirmation=True.
      2. User reviews each insight.
      3. Confirmed insights → recordStrategicMemory (POST /strategic_memory).

    Call this when:
      - ingestContent returns strategic_memory type in processing_order
      - Todd states a belief, thesis, positioning view, or competitive opinion
      - Any input contains phrases like "I think", "my view is", "we believe",
        "the real opportunity is", "the risk I see"
    """
    _auth(x_api_key)
    try:
        result = insight_intake.process_text(
            body.text,
            source_type=body.source_type,
            context=body.context,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"insight intake failed: {exc}") from exc


# ---------------------------------------------------------------------------
# RB 9.24/9.27 — Claude workspace ingest aliases and experience query surface
# ---------------------------------------------------------------------------

class ExperienceIngestBody(BaseModel):
    text: str = Field(..., description="Experience, observation, or lesson text.")
    source_type: str = Field("conversation", description="Source type for the experience.")
    employer_names: Optional[list[str]] = Field(
        None,
        description="Known employer names that should trigger externalization safeguards.",
    )
    context: Optional[dict] = Field(None, description="Optional context metadata.")


class ExperienceConfirmBody(BaseModel):
    experience_id: str = Field(..., description="Experience record id to confirm/reject.")
    confirmed: bool = Field(True, description="True records the experience; false rejects it.")


class MacroConfirmBody(BaseModel):
    record_id: str = Field(..., description="Behavioral signal or artifact record id.")
    confirmed: bool = Field(True, description="True records the item; false rejects it.")


class MacroEntityConfirmBody(BaseModel):
    entity_id: str = Field(..., description="Entity risk profile id.")
    confirmed: bool = Field(True, description="True records the entity risk; false rejects it.")


class RelationshipConfirmBody(BaseModel):
    interaction_id: str = Field(..., description="Relationship interaction id.")
    confirmed: bool = Field(True, description="True records the interaction; false rejects it.")


class InsightConfirmBody(BaseModel):
    insight_id: str = Field(..., description="Strategic insight id.")
    confirmed: bool = Field(True, description="True records the insight; false rejects it.")


@app.post("/ingest/experience", tags=["ingest"], operation_id="ingestExperience")
def ingest_experience(body: ExperienceIngestBody, x_api_key: Optional[str] = Header(None)):
    """Transform firsthand experience into reusable institutional intelligence."""
    _auth(x_api_key)
    return experiential_intelligence.process_experiential_signal(
        text=body.text,
        source_type=body.source_type,
        employer_names=body.employer_names,
        context=body.context,
    )


@app.post("/ingest/experience/confirm", tags=["ingest"], operation_id="confirmExperience")
def confirm_experience(body: ExperienceConfirmBody, x_api_key: Optional[str] = Header(None)):
    """Confirm or reject a pending experience record."""
    _auth(x_api_key)
    result = experiential_intelligence.record_experience(
        body.experience_id,
        confirmed=body.confirmed,
    )
    if "error" in result:
        raise HTTPException(404, detail=result["error"])
    return result


@app.get("/ingest/experience/retrieve", tags=["ingest"], operation_id="retrieveExperienceHooks")
def retrieve_experience_hooks(
    domain: str = Query(..., description="Retrieval domain, e.g. restaurant_tech or voice_ai."),
    claim_status: str = Query("confirmed", description="proposed | confirmed | rejected"),
    x_api_key: Optional[str] = Header(None),
):
    """Return experience records whose retrieval hooks match a topic domain."""
    _auth(x_api_key)
    return experiential_intelligence.query_retrieval_hooks(
        domain=domain,
        claim_status=claim_status,
    )


@app.get(
    "/ingest/experience/externalize/{experience_id}",
    tags=["ingest"],
    operation_id="externalizeExperience",
)
def externalize_experience(
    experience_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """Return the reputation-safe external version of an experience record."""
    _auth(x_api_key)
    result = experiential_intelligence.externalize(experience_id)
    if "error" in result:
        raise HTTPException(404, detail=result["error"])
    return result


@app.post("/ingest/macro", tags=["ingest"], operation_id="ingestMacro")
def ingest_macro(body: MacroSignalBody, x_api_key: Optional[str] = Header(None)):
    """Alias for macro behavioral intelligence ingestion."""
    return process_macro_signal(body=body, x_api_key=x_api_key)


@app.post("/ingest/macro/confirm", tags=["ingest"], operation_id="confirmMacroRecord")
def confirm_macro_record(body: MacroConfirmBody, x_api_key: Optional[str] = Header(None)):
    """Confirm or reject a pending behavioral signal or artifact."""
    _auth(x_api_key)
    result = macro_intelligence.record_behavioral_record(
        body.record_id,
        confirmed=body.confirmed,
    )
    if "error" in result:
        raise HTTPException(404, detail=result["error"])
    return result


@app.post("/ingest/macro/entity-confirm", tags=["ingest"], operation_id="confirmMacroEntity")
def confirm_macro_entity(body: MacroEntityConfirmBody, x_api_key: Optional[str] = Header(None)):
    """Confirm or reject a pending entity risk profile."""
    _auth(x_api_key)
    result = macro_intelligence.record_entity_risk(
        body.entity_id,
        confirmed=body.confirmed,
    )
    if "error" in result:
        raise HTTPException(404, detail=result["error"])
    return result


@app.post("/ingest/relationship", tags=["ingest"], operation_id="ingestRelationship")
def ingest_relationship(body: RelationshipIntakeBody, x_api_key: Optional[str] = Header(None)):
    """Alias for relationship-intelligence ingestion."""
    return process_relationship_intake(body=body, x_api_key=x_api_key)


@app.post(
    "/ingest/relationship/confirm",
    tags=["ingest"],
    operation_id="confirmRelationshipInteraction",
)
def confirm_relationship_interaction(
    body: RelationshipConfirmBody,
    x_api_key: Optional[str] = Header(None),
):
    """Confirm or reject a pending relationship interaction."""
    _auth(x_api_key)
    result = relationship_intake.record_interaction(
        body.interaction_id,
        confirmed=body.confirmed,
    )
    if "error" in result:
        raise HTTPException(404, detail=result["error"])
    return result


@app.post("/ingest/insight", tags=["ingest"], operation_id="ingestInsight")
def ingest_insight(body: InsightIntakeBody, x_api_key: Optional[str] = Header(None)):
    """Alias for strategic insight ingestion."""
    return process_insight_intake(body=body, x_api_key=x_api_key)


@app.post("/ingest/insight/confirm", tags=["ingest"], operation_id="confirmInsight")
def confirm_insight(body: InsightConfirmBody, x_api_key: Optional[str] = Header(None)):
    """Confirm or reject a pending strategic insight."""
    _auth(x_api_key)
    result = insight_intake.record_insight(body.insight_id, confirmed=body.confirmed)
    if "error" in result:
        raise HTTPException(404, detail=result["error"])
    return result


class ConfirmProposalBody(BaseModel):
    kind: Literal[
        "identity_match", "relationship", "insight", "macro_record",
        "macro_entity", "experience", "weekly_plan", "tech_stack_relationship",
        "watchlist_promotion", "priority_account_publisher_match", "ownership_change",
        "executive_move", "job_posting",
    ] = Field(..., description="Which proposal type `id` refers to.")
    id: str = Field(..., description="The SPECIFIC proposal/mutation's own id, taken from within the tool response that proposed it (e.g. a mutation_proposals[] entry's own id) -- NEVER a triage_id (format 'TRG-YYYY-MM-DD-NNN', that's intelligence_triage's own tracking id for the input as a whole, not any one proposal inside it). Confirmed live 2026-08-27: 5/5 real calls failed, all reusing a triage_id here for kind=macro_record and kind=relationship, neither of which use that id space. Also: if the tool that generated the proposal reported nothing was persisted/proposed (e.g. persistence_status=\"RB did not persist\"), there is nothing to confirm -- do not call this at all.")
    confirmed: bool = Field(True, description="True records/confirms the proposal; false rejects it.")
    write_email: bool = Field(True, description="identity_match only, when confirmed=true: whether to write the inbound sender address onto the baseline record as their email. Set false when the identity link is real but the address itself isn't theirs — e.g. invitations@linkedin.com or another platform/notification relay.")
    reason: Optional[str] = Field(None, description="identity_match only: optional note explaining why write_email was set false.")
    owner_name: Optional[str] = Field(None, description="ownership_change only, when confirmed=true: the real, confirmed owner/parent-company name. Required unless the candidate already has a proposed_owner_name (from best-effort text extraction) -- supplying this always overrides that prefilled value, e.g. to correct a wrong-direction extraction. Read the candidate's evidence_excerpt yourself before trusting either one; never guess.")
    owner_entity_id: Optional[str] = Field(None, description="ownership_change only: the owner's real entity_id, when the owner is itself an already-tracked brand/vendor entity. Omit to let RB resolve it automatically from owner_name; only set this to override that resolution.")
    name: Optional[str] = Field(None, description="executive_move only, when confirmed=true: the real, confirmed executive name. Required unless the candidate already has a proposed_name (from best-effort text extraction) -- supplying this always overrides that prefilled value. Read the candidate's evidence_excerpt yourself before trusting either one; never guess.")
    title: Optional[str] = Field(None, description="executive_move only: the executive's real title/role, if known. Overrides the candidate's own proposed_title when supplied; either may be omitted if genuinely unknown.")
    contact_id: Optional[str] = Field(None, description="executive_move only: the real baseline_index.json contact id to update, when the candidate's action is \"update_existing_contact\". Overrides the candidate's own matched_contact_id -- use this to correct a wrong name match, or to supply one the scanner didn't find. Leave unset for a genuinely new contact (action \"create_new_contact\").")


@app.post("/confirm", tags=["ingest"], operation_id="confirmProposal")
def confirm_proposal(body: ConfirmProposalBody, x_api_key: Optional[str] = Header(None)):
    """Confirm or reject any pending review-first proposal (RB-DEFECT-2026-07-06).

    Single GPT-facing action covering every "proposed, awaiting operator
    decision" surface in RB — identity matches, relationship interactions,
    strategic insights, macro behavioral records, macro entity risk
    profiles, and experience records. Consolidates what used to be up to
    six separate operations (of which only confirmIdentityMatch/
    rejectIdentityMatch were ever reachable from the Custom GPT — the other
    four had no GPT action at all) into one, so the GPT's 30-op action cap
    doesn't force a choice between them. The per-kind endpoints below
    (`/identity/{id}/confirm`, `/ingest/relationship/confirm`, etc.) still
    exist unchanged for direct API callers; this wraps them.
    """
    _auth(x_api_key)
    if body.kind == "identity_match":
        if not body.confirmed:
            result = identity_match_review.reject(body.id)
        elif body.write_email:
            result = identity_match_review.confirm(body.id)
        else:
            result = identity_match_review.confirm_without_email(body.id, reason=body.reason or "")
    elif body.kind == "relationship":
        result = relationship_intake.record_interaction(body.id, confirmed=body.confirmed)
    elif body.kind == "insight":
        result = insight_intake.record_insight(body.id, confirmed=body.confirmed)
    elif body.kind == "macro_record":
        result = macro_intelligence.record_behavioral_record(body.id, confirmed=body.confirmed)
    elif body.kind == "macro_entity":
        result = macro_intelligence.record_entity_risk(body.id, confirmed=body.confirmed)
    elif body.kind == "experience":
        result = experiential_intelligence.record_experience(body.id, confirmed=body.confirmed)
    elif body.kind == "weekly_plan":
        # RB-2026-07-15: promotion of weekly_plan_draft.json -> weekly_plan.json
        # previously had no API path at all — only a CLI command
        # (`weekly_plan_generator.py --confirm`) run by hand. A GPT-side "yes,
        # confirm this week's plan" could never actually persist, so the
        # draft alert kept firing days after the user believed it was
        # confirmed. There's a single pending draft file, not an id-addressed
        # list, so `body.id` isn't used to look anything up.
        result = (
            weekly_plan_generator.confirm_draft()
            if body.confirmed
            else weekly_plan_generator.reject_draft()
        )
    elif body.kind == "tech_stack_relationship":
        result = tech_stack_relationship_promotion.record_proposal(body.id, confirmed=body.confirmed)
    elif body.kind == "watchlist_promotion":
        result = watchlist_promotion.record_proposal(body.id, confirmed=body.confirmed)
    elif body.kind == "priority_account_publisher_match":
        result = priority_account_publisher_scan.record_proposal(body.id, confirmed=body.confirmed)
    elif body.kind == "ownership_change":
        result = ownership_promotion.record_proposal(
            body.id, confirmed=body.confirmed, owner_name=body.owner_name, owner_entity_id=body.owner_entity_id,
        )
    elif body.kind == "executive_move":
        result = executive_move_promotion.record_proposal(
            body.id, confirmed=body.confirmed, name=body.name, title=body.title, contact_id=body.contact_id,
        )
    elif body.kind == "job_posting":
        result = job_postings_promotion.record_proposal(body.id, confirmed=body.confirmed)
    else:  # unreachable given Literal, but keep the surface honest
        raise HTTPException(400, detail=f"unknown kind: {body.kind}")
    if "error" in result:
        al.log_mutation_rejected(f"confirmProposal: kind={body.kind} id={body.id}", reason=str(result["error"])[:300])
        raise HTTPException(404, detail=result["error"])
    al.log_mutation_executed(
        f"confirmProposal: kind={body.kind} id={body.id} confirmed={body.confirmed}",
        source="POST /confirm",
    )
    return result


# ---------------------------------------------------------------------------
# RB 9.27 — Intelligence query endpoints
# ---------------------------------------------------------------------------

@app.get(
    "/query/experiences",
    tags=["compute"],
    operation_id="queryExperiences",
)
def query_experiences(
    intel_type: Optional[str] = Query(None, description="Filter by intelligence type."),
    claim_status: Optional[str] = Query(None, description="Filter by claim status."),
    employer_sensitive: Optional[bool] = Query(None, description="Filter by employer sensitivity."),
    tags: Optional[str] = Query(None, description="Comma-separated retrieval hook domains."),
    x_api_key: Optional[str] = Header(None),
):
    """Query persisted experiential intelligence records."""
    _auth(x_api_key)
    try:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        items = experiential_intelligence.query_experiences(
            tags=tag_list,
            intel_type=intel_type,
            claim_status=claim_status,
            employer_sensitive=employer_sensitive,
        )
        return {
            "contract": "rb_experience_query_v1",
            "filters": {
                "tags": tag_list,
                "intel_type": intel_type,
                "claim_status": claim_status,
                "employer_sensitive": employer_sensitive,
            },
            "count": len(items),
            "items": items,
            "experiences": items,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"experience query failed: {exc}") from exc


@app.get(
    "/query/experiences/hooks",
    tags=["compute"],
    operation_id="queryExperienceHooks",
)
def query_experience_hooks(
    domain: str = Query(..., description="Retrieval hook domain."),
    claim_status: str = Query("confirmed", description="proposed | confirmed | rejected"),
    x_api_key: Optional[str] = Header(None),
):
    """Return experience lessons for a retrieval hook domain."""
    _auth(x_api_key)
    try:
        return experiential_intelligence.query_retrieval_hooks(
            domain=domain,
            claim_status=claim_status,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"experience hook query failed: {exc}") from exc

@app.get(
    "/query/relationships",
    tags=["compute"],
    operation_id="queryRelationships",
)
def query_relationships(
    contact_id: Optional[str] = Query(None, description="Filter by canonical contact id."),
    signal_type: Optional[str] = Query(None, description="Filter by interaction signal type."),
    claim_status: Optional[str] = Query(None, description="Filter by claim status."),
    x_api_key: Optional[str] = Header(None),
):
    """Query persisted relationship interaction intelligence."""
    _auth(x_api_key)
    try:
        items = relationship_intake.query_interactions(
            contact_id=contact_id,
            signal_type=signal_type,
            claim_status=claim_status,
        )
        return {
            "contract": "rb_relationship_query_v1",
            "filters": {
                "contact_id": contact_id,
                "signal_type": signal_type,
                "claim_status": claim_status,
            },
            "count": len(items),
            "items": items,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"relationship query failed: {exc}") from exc


@app.get(
    "/query/relationships/who-matters-now",
    tags=["compute"],
    operation_id="queryWhoMattersNow",
)
def query_who_matters_now(
    top_n: int = Query(10, ge=1, le=50, description="Maximum ranked contacts to return."),
    min_score: int = Query(0, ge=0, description="Minimum Who Matters Now score."),
    x_api_key: Optional[str] = Header(None),
):
    """Return the ranked Who Matters Now relationship leaderboard."""
    _auth(x_api_key)
    try:
        items = relationship_intake.query_who_matters_now(
            top_n=top_n,
            min_score=min_score,
        )
        return {
            "contract": "rb_who_matters_now_v1",
            "filters": {"top_n": top_n, "min_score": min_score},
            "count": len(items),
            "items": items,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"who matters now query failed: {exc}") from exc


@app.get(
    "/query/macro/signals",
    tags=["compute"],
    operation_id="queryMacroSignals",
)
def query_macro_signals(
    signal_type: Optional[str] = Query(None, description="Filter by behavioral signal type."),
    claim_status: Optional[str] = Query(None, description="Filter by claim status."),
    x_api_key: Optional[str] = Header(None),
):
    """Query persisted macro behavioral signals."""
    _auth(x_api_key)
    try:
        items = macro_intelligence.query_behavioral_signals(
            signal_type=signal_type,
            claim_status=claim_status,
        )
        return {
            "contract": "rb_macro_signal_query_v1",
            "filters": {"signal_type": signal_type, "claim_status": claim_status},
            "count": len(items),
            "items": items,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"macro signal query failed: {exc}") from exc


@app.get(
    "/query/macro/artifacts",
    tags=["compute"],
    operation_id="queryMacroArtifacts",
)
def query_macro_artifacts(
    claim_status: Optional[str] = Query(None, description="Filter by claim status."),
    x_api_key: Optional[str] = Header(None),
):
    """Query persisted macro behavioral artifacts."""
    _auth(x_api_key)
    try:
        items = macro_intelligence.query_behavioral_artifacts(
            claim_status=claim_status,
        )
        return {
            "contract": "rb_macro_artifact_query_v1",
            "filters": {"claim_status": claim_status},
            "count": len(items),
            "items": items,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"macro artifact query failed: {exc}") from exc


@app.get(
    "/query/thesis_convergence",
    tags=["compute"],
    operation_id="getThesisConvergence",
)
def get_thesis_convergence(
    theme: str = Query(..., description=(
        "Strategic theme to check. Known themes: operational_realism, affordability_stress, "
        "consumer_hesitation, trade_down_behavior, restaurant_ai_skepticism, "
        "retention_economics, vendor_trust_erosion, value_perception. "
        "Free-text is also accepted."
    )),
    days: int = Query(30, description="Lookback window in days (default 30)."),
    min_convergence: int = Query(3, description="Minimum signals to declare convergence (default 3)."),
    x_api_key: Optional[str] = Header(None),
):
    """Check if a strategic thesis is receiving repeated independent validation.

    DEFECT-009/010 fix: provides the 'Nth independent validation' signal that the
    live CoS layer was missing.  Call this after triageInput returns a
    strategic_memory or macro_signal stream to detect convergence patterns.

    Use case: Todd pastes a LinkedIn post about operational AI realism. triageInput
    returns macro_signal + strategic_memory streams. The GPT then calls
    getThesisConvergence(theme='operational_realism') to check: 'Is this the 3rd
    signal this week?' If is_converging=true, the GPT surfaces the convergence count
    in the CoS assessment instead of treating each signal as isolated.

    Returns convergence_statement ready for GPT rendering.
    """
    _auth(x_api_key)
    try:
        result = macro_intelligence.thesis_convergence(
            theme,
            days=days,
            min_convergence=min_convergence,
        )
        return {
            "contract": "rb_thesis_convergence_v1",
            **result,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"thesis convergence query failed: {exc}") from exc


@app.get(
    "/query/macro/entities",
    tags=["compute"],
    operation_id="queryMacroEntities",
)
def query_macro_entities(
    entity_id: Optional[str] = Query(None, description="Filter by entity id."),
    claim_status: Optional[str] = Query(None, description="Filter by claim status."),
    x_api_key: Optional[str] = Header(None),
):
    """Query persisted macro entity risk profiles."""
    _auth(x_api_key)
    try:
        items = macro_intelligence.query_entity_risks(
            entity_id=entity_id,
            claim_status=claim_status,
        )
        return {
            "contract": "rb_macro_entity_query_v1",
            "filters": {"entity_id": entity_id, "claim_status": claim_status},
            "count": len(items),
            "items": items,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"macro entity query failed: {exc}") from exc


@app.get(
    "/query/insights",
    tags=["compute"],
    operation_id="queryInsights",
)
def query_insights(
    tags: Optional[str] = Query(
        None,
        description="Comma-separated future_use_tags filter.",
    ),
    insight_type: Optional[str] = Query(None, description="Filter by insight type."),
    claim_status: Optional[str] = Query(None, description="Filter by claim status."),
    x_api_key: Optional[str] = Header(None),
):
    """Query persisted strategic insights."""
    _auth(x_api_key)
    try:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
        items = insight_intake.query_insights(
            tags=tag_list,
            insight_type=insight_type,
            claim_status=claim_status,
        )
        return {
            "contract": "rb_insight_query_v1",
            "filters": {
                "tags": tag_list,
                "insight_type": insight_type,
                "claim_status": claim_status,
            },
            "count": len(items),
            "items": items,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"insight query failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Intelligence DB — Sprint E-4
# GET  /intelligence  — convenience entity lookup (internal alias)
# POST /intelligence  — CoS research query surface (GPT-exposed as getIntelligence)
# ---------------------------------------------------------------------------

try:
    import intelligence_db as _intelligence_db_module
    _HAS_IDBM = True
except Exception:  # noqa: BLE001
    _HAS_IDBM = False

try:
    import intelligence_assessment as _ia_module  # phases 3–5 shared with on-demand ingest
    _HAS_IA = True
except Exception:  # noqa: BLE001
    _ia_module = None  # type: ignore[assignment]
    _HAS_IA = False


class IntelligenceQueryIn(BaseModel):
    query_type: str = Field(
        ...,
        description=(
            "Type of intelligence query. One of: "
            "'entity_summary' — what do we know about an entity in the last N days; "
            "'search' — find items matching entity tags, lifecycle, or domain; "
            "'convergences' — entities/pairs appearing across multiple sources in 30d; "
            "'add' — manually add a gathered intelligence item to the DB."
        ),
    )
    entity: Optional[str] = Field(
        None,
        description="Entity name for entity_summary queries (e.g. 'PAR Technology').",
    )
    days: Optional[int] = Field(
        90,
        description="Look-back window in days. Defaults to 90.",
    )
    entities: Optional[list[str]] = Field(
        None,
        description="Entity names to filter by for search queries.",
    )
    lifecycle: Optional[str] = Field(
        None,
        description=(
            "Lifecycle state filter for search. One of: "
            "new, active, monitoring, dormant, resolved, suppressed."
        ),
    )
    min_hits: Optional[int] = Field(
        2,
        description="Minimum hit count for convergences query (default 2).",
    )
    # Fields for 'add' query_type
    title: Optional[str] = Field(None, description="Title for add query.")
    content: Optional[str] = Field(None, description="Full content for add query.")
    source_name: Optional[str] = Field(None, description="Source publication/origin for add query.")
    source_type: Optional[str] = Field(
        None,
        description=(
            "Source type for add. One of: web_scan, email_harvest, manual, "
            "linkedin, brief_harvest, lifecycle, macro, ecosystem, earnings."
        ),
    )
    confidence: Optional[str] = Field(
        None,
        description="Confidence level for add: high, medium, low, unverified.",
    )
    tags: Optional[list[dict]] = Field(
        None,
        description=(
            "Tags for add query. Each tag: {'type': TAG_TYPE, 'value': str}. "
            "TAG_TYPE one of: entity, contact, product, industry, signal_type, "
            "thread_ref, domain, brand, source_entity, keyword."
        ),
    )


def _open_idb():
    """Open IntelligenceDB or raise HTTPException 503."""
    if not _HAS_IDBM:
        raise HTTPException(
            status_code=503,
            detail="intelligence_db module not available on this server.",
        )
    db = _intelligence_db_module.IntelligenceDB()
    db.open()
    return db


@app.get(
    "/intelligence",
    tags=["compute"],
    operation_id="getIntelligenceEntity",
)
def get_intelligence_entity(
    entity: str = Query(..., description="Entity name to look up."),
    days: int = Query(90, description="Look-back window in days."),
    x_api_key: Optional[str] = Header(None),
):
    """Convenience entity lookup — returns entity_summary for a single entity.

    Equivalent to POST /intelligence with query_type='entity_summary'.
    Not exposed in the Custom GPT schema — use POST /intelligence there.
    """
    _auth(x_api_key)
    db = _open_idb()
    try:
        summary = db.entity_summary(entity, days=days)
        return {"query_type": "entity_summary", "entity": entity, "days": days, "summary": summary}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=str(exc)) from exc
    finally:
        db.close()


@app.post(
    "/intelligence",
    tags=["compute"],
    operation_id="getIntelligence",
)
def post_intelligence(
    body: IntelligenceQueryIn,
    x_api_key: Optional[str] = Header(None),
):
    """CoS research query surface for the gathered intelligence store.

    Answers questions like "what do we know about PAR Technology in 90 days?"
    or "show me all acquisition signals this month" or "what entity pairs are
    converging?" Supports four query types:

    entity_summary — full picture of one entity: item count, signal types,
      date range, most recent items, related entities, lifecycle distribution.
      Required: entity. Optional: days (default 90).

    search — filtered item list. Optional: entities (tag filter), lifecycle
      (state filter), days (look-back window, default 90).

    convergences — entities and entity pairs that appear across multiple
      intelligence items in the look-back window. Surfaces sustained patterns
      that span multiple brief cycles. Optional: days (30), min_hits (2).

    add — manually add a gathered intelligence item. Required: title,
      source_name. Optional: content, source_type, confidence, tags.
    """
    _auth(x_api_key)

    qt = (body.query_type or "").strip().lower()

    # ── entity_summary ────────────────────────────────────────────────────────
    if qt == "entity_summary":
        if not body.entity:
            raise HTTPException(400, detail="entity is required for query_type='entity_summary'.")
        db = _open_idb()
        try:
            summary = db.entity_summary(body.entity, days=body.days or 90)
            return {
                "query_type": "entity_summary",
                "entity": body.entity,
                "days": body.days or 90,
                "summary": summary,
            }
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, detail=str(exc)) from exc
        finally:
            db.close()

    # ── search ────────────────────────────────────────────────────────────────
    if qt == "search":
        db = _open_idb()
        try:
            days = body.days or 90
            lifecycle = body.lifecycle or None
            entity_list = body.entities or []

            if entity_list:
                # Multiple entities: call search once per entity, merge by item ID
                seen_ids: set[str] = set()
                merged: list[dict] = []
                for ent in entity_list:
                    for item in db.search(
                        entity=ent,
                        days=days,
                        lifecycle_state=lifecycle,
                    ):
                        if item["id"] not in seen_ids:
                            seen_ids.add(item["id"])
                            merged.append(item)
                items = merged
            else:
                items = db.search(days=days, lifecycle_state=lifecycle)

            return {
                "query_type": "search",
                "filters": {
                    "entities": body.entities,
                    "lifecycle": body.lifecycle,
                    "days": days,
                },
                "count": len(items),
                "items": items,
            }
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, detail=str(exc)) from exc
        finally:
            db.close()

    # ── convergences ──────────────────────────────────────────────────────────
    if qt == "convergences":
        db = _open_idb()
        try:
            min_hits = body.min_hits if body.min_hits is not None else 2
            days = body.days if body.days is not None else 30
            multi_source = [
                c for c in db.find_convergences(min_entity_hits=min_hits, days=days)
                if len(c.get("sources") or []) >= 2
            ]
            pairs = db.cross_entity_convergence(days=days, min_shared_items=min_hits)
            return {
                "query_type": "convergences",
                "days": days,
                "min_hits": min_hits,
                "multi_source_count": len(multi_source),
                "multi_source": multi_source[:10],
                "entity_pair_count": len(pairs),
                "entity_pairs": pairs[:10],
            }
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, detail=str(exc)) from exc
        finally:
            db.close()

    # ── add ───────────────────────────────────────────────────────────────────
    if qt == "add":
        if not body.title:
            raise HTTPException(400, detail="title is required for query_type='add'.")
        if not body.source_name:
            raise HTTPException(400, detail="source_name is required for query_type='add'.")
        db = _open_idb()
        try:
            item_id = db.add_item(
                title=body.title,
                content=body.content or "",
                source_name=body.source_name,
                source_type=body.source_type or "manual",
                confidence=body.confidence or "medium",
                tags=body.tags or [],
            )
            return {
                "query_type": "add",
                "status": "added",
                "id": item_id,
                "title": body.title,
                "source_name": body.source_name,
            }
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, detail=str(exc)) from exc
        finally:
            db.close()

    raise HTTPException(
        400,
        detail=(
            f"Unknown query_type: '{body.query_type}'. "
            "Use: entity_summary, search, convergences, or add."
        ),
    )


# ---------------------------------------------------------------------------
# POST /query — Unified Query Engine  (RB 9.42 — queryEngine)
# ---------------------------------------------------------------------------

class UnifiedQueryIn(BaseModel):
    entity: Optional[str] = Field(
        None,
        description=(
            "Entity name to query about — a company (e.g. 'PAR Technology', 'McDonald\\'s') "
            "or person. Optional: omit for general questions like 'who matters most right now?'"
        ),
    )
    question: str = Field(
        ...,
        description=(
            "Natural language question. The engine parses intent and dispatches to whichever "
            "of the seven modules are relevant: micro graph (operator/store structure), macro "
            "(behavioral signals, brand risk), relationship (interactions, Who Matters Now), "
            "intelligence (gathered research, signal convergences), ecosystem (restaurant/vendor "
            "tech-stack relationships, incumbent POS/payments/loyalty), artifact (registered "
            "account dossiers — Global Payments, Foods Connected, PAR Technology, etc.), and "
            "campaign (conference invitation roster, registration status, coverage gaps). "
            "Examples: "
            "'How many McDonald\\'s operators are in the US?', "
            "'What is PAR Technology\\'s exit positioning?', "
            "'Who should I reach out to at Toast?', "
            "'What behavioral signals have we captured about consumer hesitation?', "
            "'What POS does Chipotle run?', "
            "'What's the status of the Global Payments opportunity?', "
            "'Who hasn't registered for the Genius conference yet?'"
        ),
    )
    modules: Optional[str] = Field(
        None,
        description=(
            "Comma-separated module override. Normally omit — the engine routes automatically. "
            "Valid values: micro, macro, relationship, intelligence, ecosystem, artifact, "
            "campaign, signals. Example: 'micro,intelligence' to force only those two modules."
        ),
    )


@app.post(
    "/query",
    tags=["compute"],
    operation_id="queryEngine",
)
def unified_query(
    body: UnifiedQueryIn,
    x_api_key: Optional[str] = Header(None),
):
    """Single query across RB's original seven query modules — auto-dispatches
    to: micro (operator/store graph), macro (behavioral signals), relationship
    (contacts, WMN), intelligence (research), ecosystem (tech stack/vendors),
    artifact (account dossiers), campaign (conference invites), signals
    (exit/growth/distress).

    RB-2026-08-28: despite the name, this does NOT cover every RB module --
    Blue Sheets, Master Account Plans, and Account Research background briefs
    were all built after this endpoint and are NOT among the modules it
    dispatches to (same gap listArtifacts had, closed the same day). For
    those, use listBlueSheetAccounts/getAccountStatus,
    listMasterAccountPlans/getMasterAccountPlan, or
    listAccountResearch/getAccountResearch directly, or queryIntelligenceIndex
    for a cross-subsystem pointer search that DOES include them.

    Accepts a natural-language question + optional entity name. Parses intent
    deterministically and dispatches to whichever modules are relevant:

    - micro        — Micro graph structure (operators, stores, co-ops, states)
    - macro        — Behavioral signals, brand risk, entity risk profiles
    - relationship — Interaction history, Who Matters Now leaderboard
    - intelligence — Gathered research, signal convergences, entity summaries
    - ecosystem    — Restaurant/vendor tech-stack relationships (POS, payments,
                     loyalty, KDS, digital menu boards), incumbent + candidate research
    - artifact     — Registered account dossiers (Global Payments, Foods Connected,
                     PAR Technology, Xenial, etc.) — status, key contacts, next actions
    - campaign     — Conference invitation roster, registration status, coverage
                     gaps, priority invite list (dispatches to campaign_engine.py)
    - signals      — Entity signal pattern (exit/growth/distress) from signal_synthesis

    Returns a synthesized answer string + per-module structured results +
    source attribution. Multiple modules may activate for a single question.

    This endpoint replaces the seven individual query endpoints
    (queryRelationships, queryWhoMattersNow, queryMacroSignals, queryMacroArtifacts,
    queryMacroEntities, queryInsights, getIntelligence) in the Custom GPT schema.
    Those endpoints remain available in the full API for internal use.
    """
    _auth(x_api_key)
    module_list = (
        [m.strip() for m in body.modules.split(",") if m.strip()]
        if body.modules else None
    )
    try:
        result = query_engine.query(
            entity=body.entity,
            question=body.question,
            modules=module_list,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, detail=f"query engine error: {exc}") from exc


# ---------------------------------------------------------------------------
# LinkedIn profile capture bookmarklet
# ---------------------------------------------------------------------------

@app.get("/contacts/linkedin_profile_bookmarklet", tags=["compute"],
         operation_id="getLinkedInProfileBookmarklet")
def get_linkedin_profile_bookmarklet(
    server_url: Optional[str] = Query(
        None,
        description="RB server base URL, e.g. http://localhost:8765. "
                    "Defaults to the request's own origin.",
    ),
    x_api_key: Optional[str] = Header(None),
):
    """Return a browser bookmarklet that captures a LinkedIn profile page and POSTs
    it directly to the RB server — no terminal, no browser console, no JSON copy/paste.

    One-time setup (30 seconds):
      1. Call this endpoint to get the bookmarklet.
      2. Create a new browser bookmark, paste the returned `bookmarklet_href` as the URL.
      3. Name it something like 'RB: Capture LinkedIn'.

    Per-profile workflow (5 seconds):
      1. Navigate to any LinkedIn profile page (linkedin.com/in/<slug>).
      2. Click the bookmark.
      3. A small overlay appears showing capture status. Done — the profile and
         any visible posts are ingested into RB baseline and social feed.

    The bookmarklet embeds the API key and server URL, so this endpoint
    itself requires the same x-api-key auth as everything else RB exposes.

    RB-SECURITY-2026-09-03: this route accepted x_api_key as a parameter but
    never checked it, and unconditionally embedded the real API_KEY into the
    unauthenticated JSON response -- a full auth-bypass credential leak,
    reachable over the public rb-api.bridgepointops.org tunnel with no
    credentials at all. Confirmed live during the RBB security audit.
    """
    _auth(x_api_key)
    from urllib.parse import urlencode as _ue
    import json as _json

    # Determine the server URL to embed
    if not server_url:
        server_url = "http://localhost:8765"
    server_url = server_url.rstrip("/")

    # The API key to embed (read at call time so it's always current)
    embedded_key = API_KEY or ""

    # The ingest endpoint the bookmarklet will POST to
    ingest_url = f"{server_url}/contacts/ingest_linkedin_profile"

    # Build the bookmarklet JS — minified inline function
    # Uses the same DOM-capture logic as PROFILE_CAPTURE_JS but POSTs directly
    # rather than writing to console. Shows a small injected status banner.
    bookmarklet_js = r"""(function(){
var now=new Date().toISOString();
var pageUrl=window.location.href;
var profileUrl=pageUrl.replace(/\?.*/,'').replace(/\/$/,'');
var slugM=profileUrl.match(/linkedin\.com\/in\/([^\/\?#]+)/);
if(!slugM){alert('RB: Not a LinkedIn profile page.');return;}
var slug=slugM[1];
var isActivity=/\/recent-activity\//.test(pageUrl);
function q(sel){return document.querySelector(sel);}
function qq(el,sel){return el?[...el.querySelectorAll(sel)]:[]}
function tx(el){return el&&el.innerText?el.innerText.trim():null;}
function ariaT(el){return qq(el,'span[aria-hidden="true"]').map(s=>tx(s)).filter(Boolean);}
function secItems(id){var a=q('#'+id);if(!a)return[];var c=a.closest('section')||a.parentElement&&a.parentElement.parentElement;return qq(c,'li.artdeco-list__item,li[class*="pvs-list__item"]');}
var nameEl=q('h1.text-heading-xlarge,h1[class*="heading"],.pv-text-details__left-panel h1')||(!isActivity?q('h1'):null);
var name=tx(nameEl);
var headlineEl=q('.text-body-medium.break-words,.pv-text-details__left-panel .text-body-medium,.artdeco-entity-lockup__subtitle');
var headline=tx(headlineEl);
var locEl=q('.text-body-small.inline.t-black--light.break-words,.pv-text-details__left-panel .t-black--light.t-normal.inline-block');
var location=tx(locEl);
var aboutA=q('#about');
var about=null;
if(aboutA){var sect=aboutA.closest('section')||aboutA.parentElement&&aboutA.parentElement.parentElement;var tels=qq(sect,'span[aria-hidden="true"]');var lng=tels.reduce(function(a,b){return(b.innerText||'').length>(a.innerText||'').length?b:a},{innerText:''});about=tx(lng);}
var experience=secItems('experience').map(function(item){var t=ariaT(item);var d=q.call(item,'.t-black--light.t-normal span[aria-hidden="true"]');return t[0]?{title:t[0],company:t[1]||null,dates:tx(d)||t[2]||null,description:t.slice(3).join(' ')||null}:null;}).filter(Boolean);
var education=secItems('education').map(function(item){var t=ariaT(item);var d=q.call(item,'.t-black--light.t-normal span[aria-hidden="true"]');return t[0]?{school:t[0],degree:t[1]||null,dates:tx(d)||t[2]||null}:null;}).filter(Boolean);
var skills=secItems('skills').slice(0,20).map(function(item){return ariaT(item)[0]||null;}).filter(Boolean);
var contactLinks=[...document.querySelectorAll('.pv-contact-info__contact-type a,.ci-email a,.ci-phone a,[class*="contact-info"] a[href^="mailto:"],[class*="contact-info"] a[href^="tel:"]')].map(function(a){return{href:a.href.split('?')[0],text:tx(a)};}).filter(function(c){return c.href&&!c.href.includes('linkedin.com/in/');});
var posts=[...document.querySelectorAll('[data-urn*="activity"],.feed-shared-update-v2,[class*="occludable-update"]')].slice(0,20).map(function(node,idx){var textEl=node.querySelector('.feed-shared-update-v2__description,.update-components-text,[dir="ltr"]');var rawText=(textEl&&textEl.innerText||'').trim();if(!rawText)return null;var aLink=node.querySelector('a[href*="/in/"],a[href*="/company/"]');var aN=node.querySelector('.update-components-actor__name span[aria-hidden="true"],.update-components-actor__name');var pl=node.querySelector('a[href*="/feed/update/"],a[href*="activity-"]');return{id:node.getAttribute('data-urn')||idx+'::'+now,author:{name:(tx(aN)||name||'').replace(/\s+/g,' ').trim()||null,linkedin_url:aLink?aLink.href.split('?')[0]:'https://www.linkedin.com/in/'+slug,headline:headline},text:rawText,post_url:pl?pl.href.split('?')[0]:null,platform:'linkedin',captured_at:now,captured_via:'linkedin_bookmarklet'};}).filter(Boolean);
var payload={source:'linkedin_bookmarklet',captured_via:'linkedin_bookmarklet',captured_at:now,profile_url:profileUrl,slug:slug,is_activity_page:isActivity,name:name,headline:headline,location:location,about:about,experience:experience,education:education,skills:skills,contact_links:contactLinks,posts:posts,posts_captured:posts.length};
var banner=document.createElement('div');
banner.id='rb-capture-banner';
banner.style.cssText='position:fixed;top:16px;right:16px;z-index:99999;padding:12px 18px;border-radius:8px;font-family:sans-serif;font-size:13px;font-weight:600;background:#0a66c2;color:#fff;box-shadow:0 4px 12px rgba(0,0,0,.3);';
banner.textContent='RB: Capturing profile…';
document.body.appendChild(banner);
function done(msg,ok){banner.style.background=ok?'#057642':'#cc1016';banner.textContent=msg;setTimeout(function(){banner.remove();},4000);}
fetch(""" + _json.dumps(ingest_url) + r""",{method:'POST',headers:{'Content-Type':'application/json','x-api-key':""" + _json.dumps(embedded_key) + r"""},body:JSON.stringify({confirm:true,profile_data:payload})}).then(function(r){return r.json().then(function(d){return{ok:r.ok,data:d};});}).then(function(res){if(res.ok){var d=res.data;var msg='RB: '+( d.status==='matched_and_enriched'?'Updated '+d.contact_name:d.status==='stub_created'?'New: '+d.contact_name:'Done')+( d.posts_ingested?' + '+d.posts_ingested+' posts':'');done(msg,true);}else{done('RB: Error — '+( res.data&&res.data.detail||'unknown'),false);}}).catch(function(e){done('RB: Network error — is the server running?',false);});
})();"""

    # URL-encode for bookmarklet href
    bookmarklet_href = "javascript:" + bookmarklet_js

    return {
        "bookmarklet_href": bookmarklet_href,
        "server_url": server_url,
        "ingest_endpoint": ingest_url,
        "setup_instructions": [
            "1. Copy the 'bookmarklet_href' value.",
            "2. Create a new browser bookmark (any page, right-click bookmarks bar → Add page).",
            "3. Replace the URL with the copied 'bookmarklet_href' value.",
            "4. Name it 'RB: Capture LinkedIn' (or anything memorable).",
            "5. Done — click it on any linkedin.com/in/<slug> page to capture.",
        ],
        "per_profile_usage": [
            "1. Navigate to the target LinkedIn profile page.",
            "2. Click the 'RB: Capture LinkedIn' bookmark.",
            "3. A blue banner appears while capturing, turns green on success.",
            "4. Profile and visible posts are now in RB. Call getCard or resolveLinkedInProfile to confirm.",
        ],
        "note": "The bookmarklet embeds your API key and server URL. Safe for local use — credentials never leave your machine.",
    }


# ---------------------------------------------------------------------------
# SMS exempt-handles management
# ---------------------------------------------------------------------------

SMS_CONFIG_PATH = core.SYSTEM_DIR / "sms_trusted_senders.json"


def _sms_load_config() -> dict:
    if not SMS_CONFIG_PATH.exists():
        return {"mode": "all", "trusted_handles": [], "exempt_handles": []}
    try:
        data = json.loads(SMS_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"mode": "all", "trusted_handles": [], "exempt_handles": []}
        data.setdefault("mode", "all")
        data.setdefault("trusted_handles", [])
        data.setdefault("exempt_handles", [])
        return data
    except (OSError, json.JSONDecodeError):
        return {"mode": "all", "trusted_handles": [], "exempt_handles": []}


def _sms_save_config(cfg: dict) -> None:
    cfg["_comment"] = (
        "SMS intelligence config. mode='all': all inbound messages processed "
        "except exempt_handles. exempt_handles: phone numbers (10-digit, no "
        "dashes) or email addresses for family/friends to exclude."
    )
    SMS_CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def _sms_normalize_phone(handle: str) -> str:
    digits = "".join(ch for ch in handle if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _sms_label_for_handle(handle: str, baseline: list[dict]) -> str:
    for c in baseline:
        phones = []
        for field in ("phone", "mobile", "phone_mobile", "phone_work"):
            val = c.get(field)
            if val:
                n = _sms_normalize_phone(str(val))
                if len(n) == 10:
                    phones.append(n)
        emails = [(c.get("email") or "").lower()]
        if handle in phones or handle in emails:
            name = c.get("name") or handle
            company = c.get("current_company") or ""
            suffix = f" @ {company}" if company else ""
            return f"{name}{suffix}"
    return handle


@app.get("/sms/exempt_handles", tags=["write"], operation_id="getSmsExemptHandles")
def get_sms_exempt_handles(x_api_key: Optional[str] = Header(None)):
    """Return the current SMS intelligence exempt-handles list with resolved names.

    Exempt handles are contacts (family/friends) whose inbound SMS messages
    are skipped by the intelligence processing pipeline. All other inbound
    messages are processed under the opt-out model (mode='all').
    """
    _auth(x_api_key)
    cfg = _sms_load_config()
    exempt = cfg.get("exempt_handles") or []
    baseline = core.load_baseline()
    items = [
        {"handle": h, "label": _sms_label_for_handle(h, baseline)}
        for h in sorted(exempt)
    ]
    return {
        "mode": cfg.get("mode", "all"),
        "exempt_count": len(items),
        "exempt_handles": items,
    }


class SmsExemptBody(BaseModel):
    handle: str  # normalized phone (10-digit) or email address


@app.post("/sms/exempt_handles", tags=["write"], operation_id="addSmsExemptHandle")
def post_sms_exempt_handle(
    body: SmsExemptBody,
    x_api_key: Optional[str] = Header(None),
):
    """Add a phone number or email address to the SMS intelligence exempt list.

    Pass a 10-digit US phone number (digits only, e.g. '2145551234') or an
    email address. The handle is normalized before storing.

    Use this when the user says things like 'exempt my wife', 'don't monitor
    texts from my sister', or 'add 2145551234 to the exempt list'.
    Resolve the contact's phone via /contacts/resolve or ask the user for
    the number if unknown.
    """
    _auth(x_api_key)
    h = body.handle.strip()
    if "@" in h:
        normalized = h.lower()
    else:
        normalized = _sms_normalize_phone(h)
        if len(normalized) != 10:
            raise HTTPException(400, detail=f"Could not normalize '{h}' to a 10-digit phone number.")

    cfg = _sms_load_config()
    exempt = list(cfg.get("exempt_handles") or [])
    if normalized in exempt:
        return {"status": "already_exempt", "handle": normalized}
    exempt.append(normalized)
    cfg["exempt_handles"] = sorted(exempt)
    _sms_save_config(cfg)
    baseline = core.load_baseline()
    return {
        "status": "added",
        "handle": normalized,
        "label": _sms_label_for_handle(normalized, baseline),
    }


@app.delete("/sms/exempt_handles/{handle}", tags=["write"], operation_id="removeSmsExemptHandle")
def delete_sms_exempt_handle(
    handle: str,
    x_api_key: Optional[str] = Header(None),
):
    """Remove a phone number or email from the SMS intelligence exempt list.

    Pass the handle exactly as stored (10-digit phone or email). After removal
    that contact's inbound messages will be processed for intelligence signals.

    Use when the user says 'stop exempting X', 'monitor texts from Y again', etc.
    """
    _auth(x_api_key)
    h = handle.strip()
    if "@" not in h:
        h = _sms_normalize_phone(h)

    cfg = _sms_load_config()
    exempt = list(cfg.get("exempt_handles") or [])
    if h not in exempt:
        raise HTTPException(404, detail=f"Handle '{h}' is not in the exempt list.")
    exempt.remove(h)
    cfg["exempt_handles"] = sorted(exempt)
    _sms_save_config(cfg)
    return {"status": "removed", "handle": h}


# ---------------------------------------------------------------------------
# Pre-rendered Brief endpoints (RB 10.7 pre-render architecture)
# ---------------------------------------------------------------------------

_BRIEFS_DIR = core.SYSTEM_DIR / "briefs"


def _brief_file(target_date: date, kind: str) -> Path:
    return _BRIEFS_DIR / f"{target_date.isoformat()}-{kind}-brief.md"


@app.get("/briefs/intelligence", tags=["read"], operation_id="getRenderedIntelligenceBrief")
def get_rendered_intelligence_brief(
    target_date: Optional[str] = None,
    x_api_key: Optional[str] = Header(None),
):
    """Return the pre-rendered Intelligence Brief (Part 1) for a given date.

    The brief is generated at 5am by render_intelligence_brief.py. If not yet
    rendered for today, renders on-demand from the current daily_brief cache.

    Returns:
      - date: ISO date string
      - rendered_at: when the file was written (from metadata JSON if available)
      - markdown: the full pre-rendered markdown string
      - source: "pre_rendered" | "on_demand"
    """
    _auth(x_api_key)
    import render_intelligence_brief as rib

    if target_date:
        try:
            d = date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(400, detail=f"Invalid date format: {target_date}. Use YYYY-MM-DD.")
    else:
        d = date.today()

    brief_path = _brief_file(d, "intelligence")
    source = "pre_rendered" if brief_path.exists() else "on_demand"
    markdown = rib.render(d, dry_run=False, force=False)

    meta_path = _BRIEFS_DIR / f"{d.isoformat()}-intelligence-brief.json"
    rendered_at = None
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            rendered_at = meta.get("rendered_at")
        except Exception:
            pass

    return {
        "date": d.isoformat(),
        "rendered_at": rendered_at,
        "source": source,
        "markdown": markdown,
    }


@app.get("/briefs/daily", tags=["read"], operation_id="getRenderedDailyBrief")
def get_rendered_daily_brief(
    target_date: Optional[str] = None,
    x_api_key: Optional[str] = Header(None),
):
    """Return the pre-rendered Daily Brief (Part 2) for a given date.

    The brief is generated at 5am by render_daily_brief.py. If not yet
    rendered for today, renders on-demand from the current daily_brief cache.

    Returns:
      - date: ISO date string
      - rendered_at: when the file was written
      - markdown: the full pre-rendered markdown string
      - source: "pre_rendered" | "on_demand"
    """
    _auth(x_api_key)
    import render_daily_brief as rdb

    if target_date:
        try:
            d = date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(400, detail=f"Invalid date format: {target_date}. Use YYYY-MM-DD.")
    else:
        d = date.today()

    brief_path = _brief_file(d, "daily")
    source = "pre_rendered" if brief_path.exists() else "on_demand"
    markdown = rdb.render(d, dry_run=False, force=False)

    meta_path = _BRIEFS_DIR / f"{d.isoformat()}-daily-brief.json"
    rendered_at = None
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            rendered_at = meta.get("rendered_at")
        except Exception:
            pass

    return {
        "date": d.isoformat(),
        "rendered_at": rendered_at,
        "source": source,
        "markdown": markdown,
    }


@app.get("/briefs/list", tags=["read"], operation_id="listRenderedBriefs")
def list_rendered_briefs(
    days: int = 30,
    x_api_key: Optional[str] = Header(None),
):
    """List all pre-rendered briefs available on disk.

    Returns a list of available briefs (most recent first) with:
      - date: ISO date string
      - intelligence_brief: true/false
      - daily_brief: true/false
      - rendered_at_intelligence: timestamp or null
      - rendered_at_daily: timestamp or null

    Used by the GPT for historical context ("what did the brief say on X?").
    Pass days to limit the window (default: 30).
    """
    _auth(x_api_key)

    _BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = date.today().isoformat()

    results = {}
    for f in sorted(_BRIEFS_DIR.glob("*.md"), reverse=True):
        name = f.stem  # e.g. 2026-06-26-intelligence-brief
        parts = name.split("-", 3)
        if len(parts) < 4:
            continue
        d_str = f"{parts[0]}-{parts[1]}-{parts[2]}"
        kind = parts[3]  # "intelligence-brief" or "daily-brief"

        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue

        delta = (date.today() - d).days
        if delta > days:
            continue

        if d_str not in results:
            results[d_str] = {
                "date": d_str,
                "intelligence_brief": False,
                "daily_brief": False,
                "rendered_at_intelligence": None,
                "rendered_at_daily": None,
            }

        meta_key = "intelligence" if "intelligence" in kind else "daily"
        results[d_str][f"{meta_key}_brief"] = True

        meta_path = _BRIEFS_DIR / f"{d_str}-{meta_key}-brief.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                results[d_str][f"rendered_at_{meta_key}"] = meta.get("rendered_at")
            except Exception:
                pass

    return {
        "briefs": sorted(results.values(), key=lambda x: x["date"], reverse=True),
        "total": len(results),
    }
