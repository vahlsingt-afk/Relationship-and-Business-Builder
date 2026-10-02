#!/usr/bin/env python3
"""
rbb_chat.py — RBB Trusted Chat Client (Responses API orchestrator).

Why this exists
----------------
Custom GPT Actions were proven this week to be unreliable as RBB's
persistence layer: the model can (and repeatedly did) render confident,
well-formatted responses — including plausible-sounding *failure* messages
— with zero corresponding backend call, and the chat UI gives the user no
way to see that a tool call didn't happen. See
system/CODEX_HANDOFF_2026-08-26_GPT_ACTIONS_PERSISTENCE_FABRICATION.md.

This service replaces that surface for trusted, mobile-accessible chat
access to RBB. It uses OpenAI's Responses API with `tool_choice: "required"`
on the first turn of every message, so a tool call is enforced by the
platform rather than left to the model's discretion — and, critically, the
actual HTTP call against RBB's real API is executed by this server's own
code, never asserted by the model. The model only ever sees and narrates
real tool output.

Run:
    uvicorn system.api.rbb_chat:app --host 127.0.0.1 --port 8766

Auth: a single shared passcode (RBB_CHAT_PASSCODE env var) gates the /chat
endpoint — this is a single-user personal tool on a private hostname, not a
product with real user accounts. Calls to RBB's own API use the same
RB_API_KEY every other RBB client already uses.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import base64 as b64
import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from pydantic import BaseModel

try:
    from openai import OpenAI
    import openai
except ImportError:
    sys.stderr.write("openai package required — see system/scripts/rbb_chat_tools.py header.\n")
    raise

SYSTEM_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SYSTEM_DIR / "scripts"))

import audit_log  # noqa: E402
import ecosystem_export  # noqa: E402
import rbb_chat_history as history  # noqa: E402
import rbb_chat_skills  # noqa: E402
import url_content_fetcher  # noqa: E402
import xlsx_safety  # noqa: E402
from rbb_chat_tools import TOOLS, OPERATIONS  # noqa: E402

RB_API_BASE = "http://127.0.0.1:8765"
RB_API_KEY = os.environ.get("RB_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
CHAT_PASSCODE = os.environ.get("RBB_CHAT_PASSCODE")
# RB-2026-08-28: was hardcoded to gpt-4o-mini (a small, cheap, >1-year-old
# model) with no cost/latency rationale recorded anywhere -- confirmed as
# the real root cause of a live quality complaint: RBB chat's summaries of
# rich source content (a detailed internal pricing-call transcript) came
# back as generic, un-specific topic-bucket bullets with no real names or
# numbers, while the same content summarized by Codex (running a current
# flagship model) was detailed and useful. Verified via the account's own
# live /v1/models listing that gpt-5.5 (current stable flagship, released
# 2026-04-23) is available on this key, and tested it directly against
# this file's actual call pattern -- tool_choice="required" still returns
# a function_call, tool_choice="none" still returns plain text, and a
# side-by-side summarization test on the same kind of content produced
# specific, name/number-grounded output gpt-4o-mini could not. This is not
# a cosmetic bump -- it's the fix for "RBB chat needs the same CoS voice
# as the GPT/Codex app," not a truncation/instruction-wording issue.
MODEL = "gpt-5.5"
# RB-2026-09-01: gpt-5.5 is $5/$30 per M input/output tokens -- ~30-50x
# gpt-4o-mini's rate. Confirmed live cost driver (Todd: $15 one day, $5 the
# next, purely from rbb-chat -- Codex bridge usage costs nothing extra since
# it's subscription-billed, not metered per-token like this OpenAI key).
# Reserve MODEL for the tool-orchestration loop and final narrated reply --
# that's the reliability- and voice-critical path this file's model upgrade
# was actually fixing (see MODEL comment above). CHEAP_MODEL is for the two
# calls in this file that are pure perception/relay, not orchestration or
# narration synthesis: image OCR extraction (_extract_text_from_image) and
# the upload-receipt acknowledgment (post_upload) -- gpt-4o-mini's Responses
# API vision was already spike-tested and confirmed accurate for exactly the
# OCR task below; downgrading those two doesn't touch tool-selection
# reliability or brief-summarization quality at all.
CHEAP_MODEL = "gpt-4o-mini"
# RB-2026-08-28: confirmed live -- a real turn needed exactly 4 tool calls to
# recover (a wrong capture id, two failed uploadAndIngestFile attempts from
# formatting issues, then a successful one), which exactly exhausted this
# budget on the response that WAS the successful call's own function_call --
# the loop never got to submit its output and let the model reply in text.
# Raised for headroom; the real fix is the forced-text safety net below,
# which now guarantees a reply regardless of what this number is.
MAX_TOOL_TURNS = 8
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# RB-2026-09-01: gpt-5.5's ~1M-token context means this conversation is very
# unlikely to hit context_length_exceeded the way it could under
# gpt-4o-mini's 128K window (see MAX_TOOL_TURNS-era comment above) -- but the
# COST of reprocessing a growing conversation's full history every turn via
# previous_response_id chaining is real and silent regardless of the ceiling.
# 150K chars (~37K tokens) is a middle-of-the-road point to start nudging
# toward New Chat: well before it's a large fraction of a real turn's cost,
# but not so early that routine multi-turn threads trigger it constantly.
CONVERSATION_SIZE_WARN_CHARS = 150_000
# RB-2026-09-20: Phase 1 (rbb-chat fast-path) of
# RBB_TOKEN_EFFICIENT_ARCHITECTURE_SCOPE_2026-09-19.md. A message matching
# this literal prefix skips the entire premium orchestration loop below
# (MODEL, tools=TOOLS, tool_choice="required") -- no model call at all, not
# even a cheap Tier-0 classification one -- and relays straight to
# queueCaptureText's real HTTP call via _execute_operation, the same
# execution/audit path every normal tool call already takes. Deliberately
# NOT a Tier-0 LLM classifier, even though RBB_MODEL_TIER_POLICY_2026-09-19.md
# frames "is this a drop-off or a live question" as the Tier-0 candidate: a
# literal trigger is strictly cheaper (zero marginal cost, not just low)
# and, more importantly, cannot misroute a live question into the capture
# queue the way a probabilistic classifier could -- the same "deterministic
# sweep, not live model judgment" trust reasoning already established for
# this capture path (see module docstring above). A message that doesn't
# match this prefix falls straight through to the normal premium path,
# unchanged.
CAPTURE_FAST_PATH_PREFIX = "capture:"

# Same persona, RULE 0 anti-fabrication discipline, and receipt formats the
# Custom GPT used — reused as-is (not duplicated/rewritten) so there's one
# source of truth for "how RB talks," and so this file changing updates both
# surfaces. A couple of lines reference GPT-Actions-specific UX (e.g. the
# empty-first-message bootstrap greeting) that simply never fires here since
# every /chat call carries a real user message — harmless, not worth forking
# the file over.
_INSTRUCTIONS_PATH = SYSTEM_DIR / "api" / "custom_gpt_instructions_compact_8k.md"
_INSTRUCTIONS_TEMPLATE = _INSTRUCTIONS_PATH.read_text(encoding="utf-8")


def _current_instructions() -> str:
    """Real gap found live 2026-08-27: INSTRUCTIONS was loaded once at
    process startup with no date anywhere in it, so the model had NO
    reliable source of truth for "today" -- confirmed live, it passed
    since_date="2023-10-09" to a real tool call (matching a suspiciously
    training-cutoff-shaped date, not the real one), which happened to work
    only because a limit= parameter accidentally bounded the result to the
    right items. Computed fresh per call (not baked in once) since this
    process can run for days."""
    from datetime import date as _date
    today_line = f"Today's date is {_date.today().isoformat()}. Use this as the real, authoritative source for any relative date (\"today\", \"this week\", \"recently\") -- never guess or default to a training-data date.\n\n"
    return today_line + _INSTRUCTIONS_TEMPLATE

if not RB_API_KEY:
    sys.stderr.write("WARNING: RB_API_KEY not set; calls to RBB's API will fail auth.\n")
if not OPENAI_API_KEY:
    sys.stderr.write("WARNING: OPENAI_API_KEY not set; /chat will fail.\n")
if not CHAT_PASSCODE:
    sys.stderr.write("WARNING: RBB_CHAT_PASSCODE not set; /chat is running with no auth gate.\n")

# RB-SECURITY-2026-09-03: matches the same fix on system/api/server.py --
# default docs_url/openapi_url exposed this internet-facing service's full
# route map unauthenticated. Gated replacements registered below.
app = FastAPI(title="RBB Trusted Chat Client", docs_url=None, redoc_url=None, openapi_url=None)
# 30s was fine for small tool outputs but real briefs run ~70KB of context
# plus a full markdown reply — that routinely exceeds 30s. 120s comfortably
# covers the largest real payload (getDailyBrief + getDailyBriefPart2
# together) with room to spare.
_openai_client = OpenAI(api_key=OPENAI_API_KEY, timeout=120) if OPENAI_API_KEY else None


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    caller: Optional[str] = None  # e.g. "codex-task" -- distinguishes Todd's own
    # usage from an automated caller in the audit log, since both share the
    # same passcode. Purely informational, not an auth mechanism.


class ChatResponse(BaseModel):
    reply: str
    response_id: str
    conversation_id: str
    tool_calls: list[dict]


class ConversationTurn(BaseModel):
    ts: str
    role: str
    text: str
    tool_calls: Optional[list[dict]] = None


class ConversationHistory(BaseModel):
    conversation_id: str
    turns: list[ConversationTurn]


# RB-SECURITY-2026-09-03: both of these were fail-open ("no passcode
# configured -> pass through") -- the same pattern found and fixed on
# server.py's _auth() during the RBB security audit. A missing
# RBB_CHAT_PASSCODE would have silently opened this internet-exposed
# service (rbb-chat.bridgepointops.org) to unauthenticated read/write
# access with no warning beyond a startup stderr line. Fail closed instead.
def _auth(passcode: Optional[str]) -> None:
    if not CHAT_PASSCODE:
        raise HTTPException(status_code=503, detail="server misconfigured: RBB_CHAT_PASSCODE is not set")
    if passcode != CHAT_PASSCODE:
        raise HTTPException(status_code=401, detail="invalid or missing passcode")


def _auth_flexible(header_passcode: Optional[str], query_passcode: Optional[str]) -> None:
    """For endpoints meant to be opened by a plain browser navigation (a
    clicked/pasted link), which can't attach a custom header the way the
    chat UI's own fetch() calls do. Accepts the passcode via ?passcode=
    as well as the usual header. Same passcode either way -- this is a
    single-user personal tool (see module docstring), not a system where a
    URL-embedded credential crosses a real trust boundary."""
    if not CHAT_PASSCODE:
        raise HTTPException(status_code=503, detail="server misconfigured: RBB_CHAT_PASSCODE is not set")
    if header_passcode == CHAT_PASSCODE or query_passcode == CHAT_PASSCODE:
        return
    raise HTTPException(status_code=401, detail="invalid or missing passcode")


@app.get("/openapi.json", include_in_schema=False)
def get_openapi_json(x_chat_passcode: Optional[str] = Header(None), passcode: Optional[str] = Query(None)):
    _auth_flexible(x_chat_passcode, passcode)
    from fastapi.openapi.utils import get_openapi as _get_openapi
    return _get_openapi(title=app.title, version="0.1.0", routes=app.routes)


@app.get("/docs", include_in_schema=False)
def get_docs(x_chat_passcode: Optional[str] = Header(None), passcode: Optional[str] = Query(None)):
    _auth_flexible(x_chat_passcode, passcode)
    from fastapi.openapi.docs import get_swagger_ui_html
    return get_swagger_ui_html(openapi_url=f"/openapi.json?passcode={passcode or x_chat_passcode or ''}", title=app.title + " - Docs")


# Real gap found 2026-08-27: the model was told to "give a downloadable
# link" for a Background Brief and, with no real download capability
# anywhere in the system, invented one -- it printed the brief's raw
# on-disk relative path (system/account_research/accounts/.../Background_
# Brief.md) as if it were a clickable link. That path means nothing to a
# browser. getBriefDownloadLink is a LOCAL operation (not forwarded to the
# RB API like every other tool) because the real, working link has to
# point at THIS server's own public hostname (rbb-chat.bridgepointops.org,
# confirmed via the cloudflared tunnel config) with the download route
# defined below -- server.py has no reason to know its own external URL.
def _local_get_brief_download_link(arguments: dict) -> tuple[int, Any]:
    slug = str(arguments.get("account_slug", "")).strip()
    if not slug:
        return 400, {"error": "account_slug is required"}
    # Confirm a real brief actually exists before handing back a link that
    # would 404 -- reuses the same real RB API call every other tool uses,
    # not a guess.
    status_code, payload = _execute_operation("getAccountResearch", {"account_slug": slug})
    if status_code != 200:
        return status_code, payload
    brief = (payload or {}).get("latest_background_brief") or {}
    if not brief.get("markdown"):
        return 404, {"error": f"No Background Brief has been generated yet for '{slug}'. Call generateAccountBackgroundBrief first."}
    resolved_slug = payload.get("account", {}).get("account_slug", slug)
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/brief/{resolved_slug}/download{passcode_qs}",
        "note": "This is a real, working link -- relay it verbatim. Do not construct a link yourself from any file path.",
    }


def _local_get_account_plan_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as getBriefDownloadLink, for the Account
    Plan (RB-2026-09-07) -- a distinct document from the Background Brief,
    so reusing getBriefDownloadLink's link here would point at the wrong
    artifact, the exact bug class getBlueSheetDownloadLink's own docstring
    already documents for the Blue Sheet case."""
    slug = str(arguments.get("account_slug", "")).strip()
    if not slug:
        return 400, {"error": "account_slug is required"}
    status_code, payload = _execute_operation("getAccountPlan", {"account_slug": slug})
    if status_code != 200:
        return status_code, payload
    if not (payload or {}).get("markdown"):
        return 404, {"error": f"No Account Plan has been generated yet for '{slug}'. Call createAccountPlan first."}
    resolved_slug = (payload or {}).get("slug", slug)
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/account-plan/{resolved_slug}/download{passcode_qs}",
        "note": "This is a real, working link -- relay it verbatim. Do not construct a link yourself from any file path.",
    }


def _local_get_green_sheet_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as getAccountPlanDownloadLink, for the
    Green Sheet (RB-2026-09-07) -- a distinct, single-call-scoped document,
    not the account-wide Account Plan."""
    slug = str(arguments.get("account_slug", "")).strip()
    if not slug:
        return 400, {"error": "account_slug is required"}
    status_code, payload = _execute_operation("getGreenSheet", {"account_slug": slug})
    if status_code != 200:
        return status_code, payload
    if not (payload or {}).get("markdown"):
        return 404, {"error": f"No Green Sheet has been generated yet for '{slug}'. Call createGreenSheet first."}
    resolved_slug = (payload or {}).get("slug", slug)
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/green-sheet/{resolved_slug}/download{passcode_qs}",
        "note": "This is a real, working link -- relay it verbatim. Do not construct a link yourself from any file path.",
    }


def _local_get_win_plan_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as getAccountPlanDownloadLink, for the
    Win Plan (RB-2026-09-07) -- a distinct, closing-strategy-scoped
    document."""
    slug = str(arguments.get("account_slug", "")).strip()
    if not slug:
        return 400, {"error": "account_slug is required"}
    status_code, payload = _execute_operation("getWinPlan", {"account_slug": slug})
    if status_code != 200:
        return status_code, payload
    if not (payload or {}).get("markdown"):
        return 404, {"error": f"No Win Plan has been generated yet for '{slug}'. Call createWinPlan first."}
    resolved_slug = (payload or {}).get("slug", slug)
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/win-plan/{resolved_slug}/download{passcode_qs}",
        "note": "This is a real, working link -- relay it verbatim. Do not construct a link yourself from any file path.",
    }


def _local_get_win_loss_review_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as getAccountPlanDownloadLink, for one
    specific Win/Loss Review (RB-2026-09-28) -- keyed by BOTH account_slug
    and opportunity_slug, unlike every other single-slug artifact here."""
    account_slug = str(arguments.get("account_slug", "")).strip()
    opportunity_slug = str(arguments.get("opportunity_slug", "")).strip()
    if not account_slug or not opportunity_slug:
        return 400, {"error": "account_slug and opportunity_slug are required"}
    status_code, payload = _execute_operation(
        "getWinLossReview", {"account_slug": account_slug, "opportunity_slug": opportunity_slug},
    )
    if status_code != 200:
        return status_code, payload
    if not (payload or {}).get("markdown"):
        return 404, {"error": f"No Win/Loss Review has been generated yet for '{account_slug}' / '{opportunity_slug}'. Call createWinLossReview first."}
    resolved_account_slug = (payload or {}).get("account_slug", account_slug)
    resolved_opportunity_slug = (payload or {}).get("opportunity_slug", opportunity_slug)
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/win-loss-review/{resolved_account_slug}/{resolved_opportunity_slug}/download{passcode_qs}",
        "note": "This is a real, working link -- relay it verbatim. Do not construct a link yourself from any file path.",
    }


def _local_get_rfp_response_plan_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as getAccountPlanDownloadLink, for the
    RFP Response Plan (RB-2026-09-07) -- a distinct, RFP-specific tracking
    document."""
    slug = str(arguments.get("account_slug", "")).strip()
    if not slug:
        return 400, {"error": "account_slug is required"}
    status_code, payload = _execute_operation("getRfpResponsePlan", {"account_slug": slug})
    if status_code != 200:
        return status_code, payload
    if not (payload or {}).get("markdown"):
        return 404, {"error": f"No RFP Response Plan has been generated yet for '{slug}'. Call createRfpResponsePlan first."}
    resolved_slug = (payload or {}).get("slug", slug)
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/rfp-response-plan/{resolved_slug}/download{passcode_qs}",
        "note": "This is a real, working link -- relay it verbatim. Do not construct a link yourself from any file path.",
    }


def _local_get_vendor_engagement_analysis_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as getRfpResponsePlanDownloadLink, for the
    Vendor Engagement Analysis (RB-2026-09-07, competitive-side) --
    account-scoped, distinct from getBattleCardDownloadLink (category) and
    getCompetitiveBriefDownloadLink (competitor)."""
    slug = str(arguments.get("account_slug", "")).strip()
    if not slug:
        return 400, {"error": "account_slug is required"}
    status_code, payload = _execute_operation("getVendorEngagementAnalysis", {"account_slug": slug})
    if status_code != 200:
        return status_code, payload
    if not (payload or {}).get("markdown"):
        return 404, {"error": f"No Vendor Engagement Analysis has been generated yet for '{slug}'. Call createVendorEngagementAnalysis first."}
    resolved_slug = (payload or {}).get("slug", slug)
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/vendor-engagement-analysis/{resolved_slug}/download{passcode_qs}",
        "note": "This is a real, working link -- relay it verbatim. Do not construct a link yourself from any file path.",
    }


def _local_get_relationship_card_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as getRfpResponsePlanDownloadLink, for the
    PUBLISHED Relationship Card (RB-2026-09-08, relationship-side) --
    distinct from getCard's own raw-file link (there isn't one; getCard is
    a direct JSON read, not a download)."""
    contact_id = str(arguments.get("contact_id", "")).strip()
    if not contact_id:
        return 400, {"error": "contact_id is required"}
    status_code, payload = _execute_operation("getRelationshipCard", {"contact_id": contact_id})
    if status_code != 200:
        return status_code, payload
    if not (payload or {}).get("markdown"):
        return 404, {"error": f"No Relationship Card has been published yet for '{contact_id}'. Call createRelationshipCard first."}
    resolved_id = (payload or {}).get("contact_id", contact_id)
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/relationship-cards/{resolved_id}/download{passcode_qs}",
        "note": "This is a real, working link -- relay it verbatim. Do not construct a link yourself from any file path.",
    }


def _local_get_relationship_plan_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as getRfpResponsePlanDownloadLink, for the
    Relationship Plan (RB-2026-09-08, relationship-side, final artifact)."""
    contact_id = str(arguments.get("contact_id", "")).strip()
    if not contact_id:
        return 400, {"error": "contact_id is required"}
    status_code, payload = _execute_operation("getRelationshipPlan", {"contact_id": contact_id})
    if status_code != 200:
        return status_code, payload
    if not (payload or {}).get("markdown"):
        return 404, {"error": f"No Relationship Plan has been generated yet for '{contact_id}'. Call createRelationshipPlan first."}
    resolved_id = (payload or {}).get("contact_id", contact_id)
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/relationship-plans/{resolved_id}/download{passcode_qs}",
        "note": "This is a real, working link -- relay it verbatim. Do not construct a link yourself from any file path.",
    }


def _local_get_inner_circle_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as getRfpResponsePlanDownloadLink, for the
    Inner Circle roll-up (RB-2026-09-08, relationship-side) -- a
    singleton, no id/slug argument needed."""
    status_code, payload = _execute_operation("getInnerCircle", {})
    if status_code != 200:
        return status_code, payload
    if not (payload or {}).get("markdown"):
        return 404, {"error": "No Inner Circle has been generated yet. Call createInnerCircle first."}
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/inner-circle/download{passcode_qs}",
        "note": "This is a real, working link -- relay it verbatim. Do not construct a link yourself from any file path.",
    }


def _local_get_referral_network_overview_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as getRfpResponsePlanDownloadLink, for the
    PUBLISHED Referral Network Overview (RB-2026-09-08, relationship-side)
    -- a singleton, no id/slug argument needed."""
    status_code, payload = _execute_operation("getReferralNetworkOverview", {})
    if status_code != 200:
        return status_code, payload
    if not (payload or {}).get("markdown"):
        return 404, {"error": "No Referral Network Overview has been published yet. Call createReferralNetworkOverview first."}
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/referral-network/overview/download{passcode_qs}",
        "note": "This is a real, working link -- relay it verbatim. Do not construct a link yourself from any file path.",
    }


def _local_get_referral_network_analysis_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as getRfpResponsePlanDownloadLink, for the
    persisted Referral Network Analysis (RB-2026-09-08, relationship-side)
    -- per-target, distinct from the singleton Overview above."""
    target = str(arguments.get("target", "")).strip()
    if not target:
        return 400, {"error": "target is required"}
    status_code, payload = _execute_operation("getReferralNetworkAnalysis", {"target": target})
    if status_code != 200:
        return status_code, payload
    if not (payload or {}).get("markdown"):
        return 404, {"error": f"No Referral Network Analysis has been generated yet for '{target}'. Call createReferralNetworkAnalysis first."}
    resolved_target = (payload or {}).get("target", target)
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/referral-network/analysis/{resolved_target}/download{passcode_qs}",
        "note": "This is a real, working link -- relay it verbatim. Do not construct a link yourself from any file path.",
    }


def _local_get_battle_card_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as getRfpResponsePlanDownloadLink, for the
    persisted Battle Card (RB-2026-09-07, competitive-side) -- category-
    scoped, not account-scoped."""
    category = str(arguments.get("category", "")).strip()
    if not category:
        return 400, {"error": "category is required"}
    status_code, payload = _execute_operation("getBattleCard", {"category": category})
    if status_code != 200:
        return status_code, payload
    if not (payload or {}).get("markdown"):
        return 404, {"error": f"No Battle Card has been generated yet for category '{category}'. Call createBattleCard first."}
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/battle-cards/{category}/download{passcode_qs}",
        "note": "This is a real, working link -- relay it verbatim. Do not construct a link yourself from any file path.",
    }


def _local_get_competitive_brief_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as getRfpResponsePlanDownloadLink, for the
    persisted Competitive Brief (RB-2026-09-07, competitive-side) --
    competitor-scoped."""
    slug = str(arguments.get("competitor_slug", "")).strip()
    if not slug:
        return 400, {"error": "competitor_slug is required"}
    status_code, payload = _execute_operation("getCompetitiveBrief", {"competitor_slug": slug})
    if status_code != 200:
        return status_code, payload
    if not (payload or {}).get("markdown"):
        return 404, {"error": f"No Competitive Brief has been generated yet for competitor '{slug}'. Call createCompetitiveBrief first."}
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/competitive-briefs/{slug}/download{passcode_qs}",
        "note": "This is a real, working link -- relay it verbatim. Do not construct a link yourself from any file path.",
    }


def _local_get_value_wedge_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as getRfpResponsePlanDownloadLink, for the
    persisted Value Wedge (RB-2026-09-25, competitive-side) --
    competitor-scoped."""
    slug = str(arguments.get("competitor_slug", "")).strip()
    if not slug:
        return 400, {"error": "competitor_slug is required"}
    status_code, payload = _execute_operation("getValueWedge", {"competitor_slug": slug})
    if status_code != 200:
        return status_code, payload
    if not (payload or {}).get("markdown"):
        return 404, {"error": f"No Value Wedge has been generated yet for competitor '{slug}'. Call createValueWedge first."}
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/value-wedges/{slug}/download{passcode_qs}",
        "note": "This is a real, working link -- relay it verbatim. Do not construct a link yourself from any file path.",
    }


def _local_get_blue_sheet_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as getBriefDownloadLink, but for a Blue
    Sheet's actual xlsx workbook -- a real gap found live 2026-08-27: the
    model reused getBriefDownloadLink (which serves an Account Research
    brief's markdown) for a Blue Sheet download request, producing a link
    to the wrong document entirely."""
    slug = str(arguments.get("account_slug", "")).strip()
    if not slug:
        return 400, {"error": "account_slug is required"}
    status_code, payload = _execute_operation("listBlueSheetAccounts", {})
    if status_code != 200:
        return status_code, payload
    accounts = (payload or {}).get("accounts", [])
    entry = next((a for a in accounts if a.get("account_id") == f"acct-{slug}"), None)
    if entry is None:
        return 404, {"error": f"No Blue Sheet account found for slug '{slug}'. Call listBlueSheetAccounts to see valid slugs."}
    if not entry.get("workbook_path"):
        return 404, {"error": f"'{slug}' has a Blue Sheet account shell but no xlsx workbook has been rendered yet."}
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/blue-sheet/{slug}/download{passcode_qs}",
        "note": "This is a real, working link to the actual xlsx workbook -- relay it verbatim.",
    }


def _local_get_blue_sheet_customer_artifact_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as getBlueSheetDownloadLink, for one
    structured customer-facing artifact (a table saved via
    addBlueSheetCustomerArtifact) -- a separate, deliberately smaller
    export than the full internal Blue Sheet workbook. RB-2026-09-02: real
    gap -- there was no way to hand back just a clean pricing-matrix-shaped
    table as its own Excel file, distinct from the internal workbook that
    carries Commercial Model/red-flag/rating data never meant for a
    customer. Unlike getBlueSheetDownloadLink, this confirms the specific
    artifact_id exists (not just the account) before returning a link."""
    slug = str(arguments.get("account_slug", "")).strip()
    artifact_id = str(arguments.get("artifact_id", "")).strip()
    if not slug or not artifact_id:
        return 400, {"error": "account_slug and artifact_id are both required"}
    status_code, payload = _execute_operation("listBlueSheetCustomerArtifacts", {"account_slug": slug})
    if status_code != 200:
        return status_code, payload
    artifacts = (payload or {}).get("artifacts", [])
    entry = next((a for a in artifacts if a.get("artifact_id") == artifact_id), None)
    if entry is None:
        return 404, {"error": f"No customer-facing artifact '{artifact_id}' found for '{slug}'. Call listBlueSheetCustomerArtifacts to see valid ids."}
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/blue-sheet-customer-artifact/{slug}/{artifact_id}/download{passcode_qs}",
        "note": "This is a real, working link to a fresh xlsx export of just this customer-facing table -- relay it verbatim.",
    }


def _local_get_capture_download_link(arguments: dict) -> tuple[int, Any]:
    """RB-2026-08-28: Todd's explicit direction -- "Any document produced by
    RBB needs to be available with a link where I can download a local copy
    to review and revise." A processed capture's real transcript was
    previously only readable inline in chat, never downloadable. Same real-
    link discipline as the other getXDownloadLink tools."""
    file_id = str(arguments.get("file_id", "")).strip()
    if not file_id:
        return 400, {"error": "file_id is required"}
    status_code, payload = _execute_operation("getCaptureProcessed", {"file_id": file_id})
    if status_code != 200:
        return status_code, payload
    if not (payload or {}).get("transcript"):
        return 404, {"error": f"Capture '{file_id}' has no transcript available to download."}
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/capture/{file_id}/download{passcode_qs}",
        "note": "This is a real, working link to the actual transcript -- relay it verbatim.",
    }


def _local_get_master_account_plan_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as getBlueSheetDownloadLink, for a Master
    Account Plan's actual xlsx workbook -- a distinct third document type
    from a Blue Sheet or an Account Research brief."""
    vendor_slug = str(arguments.get("vendor_slug", "")).strip()
    if not vendor_slug:
        return 400, {"error": "vendor_slug is required"}
    status_code, payload = _execute_operation("listMasterAccountPlans", {})
    if status_code != 200:
        return status_code, payload
    plans = (payload or {}).get("plans", [])
    entry = next((p for p in plans if p.get("vendor_slug") == vendor_slug), None)
    if entry is None:
        return 404, {"error": f"No Master Account Plan found for vendor_slug '{vendor_slug}'. Call listMasterAccountPlans to see valid slugs."}
    if not entry.get("workbook_path"):
        return 404, {"error": f"'{vendor_slug}' has a Master Account Plan registry entry but no xlsx workbook is registered."}
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/master-account-plan/{vendor_slug}/download{passcode_qs}",
        "note": "This is a real, working link to the actual xlsx workbook -- relay it verbatim.",
    }


def _local_get_vendor_list_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as the other DownloadLink tools -- a real,
    working link to a fresh xlsx export of ecosystem_intelligence.json's
    ~88 tracked vendor entities (2026-09-01, Todd's own request; xlsx per
    his same-day follow-up -- every other DownloadLink tool serves a real
    Excel workbook too, so match that rather than CSV). Unlike the others,
    this has no slug -- one export, generated fresh from the real graph on
    each download, never a stale cached file."""
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/vendor-list/download{passcode_qs}",
        "note": "This is a real, working link to a fresh xlsx export of every tracked vendor -- relay it verbatim.",
    }


def _local_get_ecosystem_workbook_download_link(arguments: dict) -> tuple[int, Any]:
    """Wires up ecosystem_export.py (built 2026-07-31, "Phase 6") -- a
    complete 6-tab restaurant-tech graph workbook exporter that had zero
    chat/API exposure until now (CLI-only). Same real-link discipline as
    the other DownloadLink tools -- generated fresh from the real graph on
    each download, never a stale cached file. export_type is required (no
    default) so nothing silently leaks Todd's own strategic_note/confidence
    rationale text via the "shareable" projection -- see
    ecosystem_export.EXPORT_TYPES."""
    export_type = str(arguments.get("export_type", "")).strip()
    if export_type not in ecosystem_export.EXPORT_TYPES:
        return 400, {"error": f"export_type must be one of {ecosystem_export.EXPORT_TYPES}, got {export_type!r}"}
    passcode_qs = f"&passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/ecosystem-workbook/download?export_type={export_type}{passcode_qs}",
        "note": "This is a real, working link to a fresh xlsx export of the restaurant-tech graph -- relay it verbatim.",
    }


def _local_get_competitive_landscape_download_link(arguments: dict) -> tuple[int, Any]:
    """Same real-link discipline as the other DownloadLink tools -- a real,
    working link to a fresh multi-tab xlsx analysis (2026-09-01, Todd's
    corrected direction: market share by category, real battle cards with
    a computed Genius-vs-competitor delta, a brand x category grid modeled
    on his own canonical Restaurant Tech Coverage workbook, and competitor
    research status). No slug -- one export, generated fresh from the real
    graph + competitor_intelligence on each download, never a stale cached
    file."""
    passcode_qs = f"?passcode={CHAT_PASSCODE}" if CHAT_PASSCODE else ""
    return 200, {
        "download_url": f"https://rbb-chat.bridgepointops.org/competitive-landscape/download{passcode_qs}",
        "note": "This is a real, working link to a fresh competitive tech-stack analysis workbook -- relay it verbatim.",
    }


def _local_fetch_url_content(arguments: dict) -> tuple[int, Any]:
    """RB-2026-09-01: pasting a URL previously did nothing -- neither
    ingestContent nor processMacroSignal has a field that gets
    dereferenced as a URL, so the model only ever saw the literal link
    text. This is the real fix: url_content_fetcher.py does an actual,
    deterministic HTTP fetch + extraction (never something the model
    asserts), same "act for real, then let the model only narrate what
    genuinely happened" discipline as uploadAndIngestFile. A failed fetch
    (login wall, 404, JS-only shell) returns an honest reason here --
    the tool's own description tells the model to relay that reason
    plainly rather than fill the gap with general knowledge."""
    url = str(arguments.get("url", "")).strip()
    if not url:
        return 400, {"error": "url is required"}
    result = url_content_fetcher.fetch_url_content(url)
    if result.get("ok"):
        return 200, result
    return 422, {"error": result.get("reason", "Could not fetch that URL."), "url": url}


_LOCAL_OPERATIONS = {
    "getBriefDownloadLink": _local_get_brief_download_link,
    "getAccountPlanDownloadLink": _local_get_account_plan_download_link,
    "getGreenSheetDownloadLink": _local_get_green_sheet_download_link,
    "getWinPlanDownloadLink": _local_get_win_plan_download_link,
    "getWinLossReviewDownloadLink": _local_get_win_loss_review_download_link,
    "getRfpResponsePlanDownloadLink": _local_get_rfp_response_plan_download_link,
    "getBattleCardDownloadLink": _local_get_battle_card_download_link,
    "getCompetitiveBriefDownloadLink": _local_get_competitive_brief_download_link,
    "getValueWedgeDownloadLink": _local_get_value_wedge_download_link,
    "getVendorEngagementAnalysisDownloadLink": _local_get_vendor_engagement_analysis_download_link,
    "getRelationshipCardDownloadLink": _local_get_relationship_card_download_link,
    "getRelationshipPlanDownloadLink": _local_get_relationship_plan_download_link,
    "getInnerCircleDownloadLink": _local_get_inner_circle_download_link,
    "getReferralNetworkOverviewDownloadLink": _local_get_referral_network_overview_download_link,
    "getReferralNetworkAnalysisDownloadLink": _local_get_referral_network_analysis_download_link,
    "getBlueSheetDownloadLink": _local_get_blue_sheet_download_link,
    "getBlueSheetCustomerArtifactDownloadLink": _local_get_blue_sheet_customer_artifact_download_link,
    "getMasterAccountPlanDownloadLink": _local_get_master_account_plan_download_link,
    "getCaptureDownloadLink": _local_get_capture_download_link,
    "getVendorListDownloadLink": _local_get_vendor_list_download_link,
    "getCompetitiveLandscapeDownloadLink": _local_get_competitive_landscape_download_link,
    "getEcosystemWorkbookDownloadLink": _local_get_ecosystem_workbook_download_link,
    "fetchUrlContent": _local_fetch_url_content,
}


def _execute_operation(operation_id: str, arguments: dict) -> tuple[int, Any]:
    """Execute a real HTTP call against RBB's API. Never trust the model — this
    is the only place a tool call actually happens."""
    if operation_id in _LOCAL_OPERATIONS:
        return _LOCAL_OPERATIONS[operation_id](arguments)
    op = OPERATIONS.get(operation_id)
    if op is None:
        return 404, {"error": f"unknown operation: {operation_id}"}

    path = op["path"]
    for pname in op["path_params"]:
        path = path.replace("{" + pname + "}", str(arguments.get(pname, "")))

    query = {k: arguments[k] for k in op["query_params"] if k in arguments and arguments[k] is not None}
    body = None
    if op["has_body"]:
        body = {k: arguments[k] for k in op["body_param_names"] if k in arguments}
        if operation_id == "ingestContent" and body.get("auto_persist"):
            # Confirmed incident (2026-08-27): instructions alone did not
            # reliably stop the model from calling ingestContent with
            # auto_persist=true on text it generated itself (not anything
            # the user provided), which writes straight to IntelligenceDB
            # with no separate confirmation step -- unlike every other
            # mutation path in this system. Force it off here, at the one
            # place a real HTTP call actually happens, rather than trusting
            # a model-controlled boolean. This makes ingestContent behave
            # like processMacroSignal already does: propose, never
            # auto-write. Real persistence still needs a genuine
            # confirmProposal call using the id ingestContent's own
            # response returns.
            body["auto_persist"] = False

    url = RB_API_BASE + path
    headers = {"x-api-key": RB_API_KEY} if RB_API_KEY else {}

    try:
        # Confirmed live (2026-08-27): a real uploadAndIngestFile/contacts
        # call took ~32s (real downstream work -- brief rebuild, source
        # health refresh, campaign reconciliation, all synchronous). 30s
        # here would 599 a genuinely successful call. 90s covers the
        # heaviest known real pipelines with room to spare, while staying
        # under the OpenAI client's own 120s budget for a full model turn.
        with httpx.Client(timeout=90) as client:
            resp = client.request(op["method"], url, params=query or None, json=body, headers=headers)
        try:
            payload = resp.json()
        except ValueError:
            payload = {"raw": resp.text[:2000]}
        # RB-2026-08-28: confirmed live -- after createBlueSheetAccount /
        # generateAccountBackgroundBrief succeed, the model has repeatedly
        # skipped the follow-up getBlueSheetDownloadLink/getBriefDownloadLink
        # call and instead built its own markdown link straight from the raw
        # workbook_path/slug field (a relative filesystem path, not a URL --
        # not downloadable). Same RULE 0 discipline as those two tools
        # themselves: don't rely on the model remembering a second call:
        # inject the one real, working link into the response it already has
        # in hand, so there is nothing left for it to construct.
        if 200 <= resp.status_code < 300 and isinstance(payload, dict):
            slug = payload.get("slug") or arguments.get("account_slug")
            if operation_id == "createBlueSheetAccount" and slug:
                link_status, link_payload = _local_get_blue_sheet_download_link({"account_slug": slug})
                if link_status == 200:
                    payload["download_url"] = link_payload["download_url"]
                    payload["_download_note"] = "Relay this download_url verbatim -- do not construct a link from workbook_path yourself."
            elif operation_id == "generateAccountBackgroundBrief" and slug:
                link_status, link_payload = _local_get_brief_download_link({"account_slug": slug})
                if link_status == 200:
                    payload["download_url"] = link_payload["download_url"]
                    payload["_download_note"] = "Relay this download_url verbatim -- do not construct a link from any file path yourself."
            elif operation_id == "ingestMasterAccountPlanUpload":
                vendor_slug = payload.get("vendor_slug") or arguments.get("vendor_slug")
                if vendor_slug:
                    link_status, link_payload = _local_get_master_account_plan_download_link({"vendor_slug": vendor_slug})
                    if link_status == 200:
                        payload["download_url"] = link_payload["download_url"]
                        payload["_download_note"] = "Relay this download_url verbatim -- do not construct a link from any file path yourself."
            elif operation_id == "uploadAndIngestFile" and payload.get("pipeline_run") == "master_account_plan":
                # RB-2026-08-28: the primary real path -- a raw .xlsx upload
                # auto-routed by dataset_classifier.py, not the curated-JSON
                # path above. vendor_slug lives inside ingest_result here.
                vendor_slug = (payload.get("ingest_result") or {}).get("vendor_slug")
                if vendor_slug:
                    link_status, link_payload = _local_get_master_account_plan_download_link({"vendor_slug": vendor_slug})
                    if link_status == 200:
                        payload["download_url"] = link_payload["download_url"]
                        payload["_download_note"] = "Relay this download_url verbatim -- do not construct a link from any file path yourself."
        return resp.status_code, payload
    except httpx.RequestError as exc:
        return 599, {"error": f"request failed: {exc}"}


def _audit_tool_call(operation_id: str, arguments: dict, status_code: int, response_id: str, call_id: str, caller: Optional[str] = None, reason: Optional[str] = None) -> None:
    is_write = OPERATIONS.get(operation_id, {}).get("method") in WRITE_METHODS
    ok = 200 <= status_code < 300
    event_type = "mutation_executed" if (is_write and ok) else (
        "mutation_rejected" if is_write else "source_accessed"
    )
    audit_log.append_event(
        event_type=event_type,
        item_summary=f"rbb_chat tool call: {operation_id}",
        reason=reason or ("responses_api_tool_choice_required" if is_write else f"Source read: {operation_id}"),
        outcome="executed" if ok else "failed",
        data_class="memory" if is_write else "raw_source",
        source="rbb_chat",
        extra={
            "operation_id": operation_id,
            "response_id": response_id,
            "call_id": call_id,
            "http_status": status_code,
            "arguments_keys": list(arguments.keys()),
            "caller": caller or "user",
        },
    )


class _TurnResult:
    def __init__(self, reply: str, response_id: str, tool_calls: list[dict], persist_response_id: bool = True):
        self.reply = reply
        self.response_id = response_id
        self.tool_calls = tool_calls
        # False for the capture fast path: response_id there is a local
        # sentinel, never a real OpenAI response id, so it must never be
        # written into conversation history -- rbb_chat_history.get_last_
        # response_id() would otherwise hand it back as previous_response_id
        # on the next real orchestration turn and break the chain (handled
        # gracefully today by the BadRequestError/NotFoundError fallback in
        # _run_chat_turn, but skipping it here is strictly better: the next
        # real turn keeps chaining from the last genuine OpenAI response
        # instead of losing that context for no reason).
        self.persist_response_id = persist_response_id


def _log_turn_error(conversation_id: str, exc: Exception, stage: str, caller: Optional[str] = None) -> None:
    audit_log.append_event(
        event_type="source_accessed",
        item_summary="rbb_chat turn errored",
        reason=f"openai_api_error: {type(exc).__name__} at {stage}: {exc}"[:500],
        outcome="failed",
        data_class="operational",
        source="rbb_chat",
        extra={"conversation_id": conversation_id, "stage": stage, "caller": caller or "user"},
    )


_USAGE_LOG_PATH = SYSTEM_DIR / "api" / "openai_usage.jsonl"
# RB-2026-09-02: confirmed live cost driver was gpt-5.5 ($15 one day, $5
# the next, per the MODEL comment above), but resp.usage was never read
# or logged anywhere in this file -- every cost claim before this was
# anecdotal, with no way to see which conversations/stages were actually
# expensive or whether prompt caching on the repeated instructions+tools
# prefix was landing at all. Rates below are per-million-tokens, USD,
# only for models this file actually calls; an unlisted model still gets
# its raw token counts logged, just with cost_usd_estimate=None.
_PRICING_PER_M_TOKENS = {
    "gpt-5.5": (5.00, 30.00),
    "gpt-4o-mini": (0.15, 0.60),
}


def _log_usage(stage: str, resp: Any, conversation_id: str, caller: Optional[str] = None,
                extra: Optional[dict] = None) -> None:
    """Best-effort: a logging failure must never break a real chat turn."""
    try:
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        input_details = getattr(usage, "input_tokens_details", None)
        cached_tokens = getattr(input_details, "cached_tokens", None) if input_details else None
        model = getattr(resp, "model", None) or "unknown"
        rates = _PRICING_PER_M_TOKENS.get(model)
        cost_usd = None
        if rates and input_tokens is not None and output_tokens is not None:
            in_rate, out_rate = rates
            cached = cached_tokens or 0
            # Cached input tokens are discounted by OpenAI (rate varies by
            # model, not published per-model here) -- approximated at 50%
            # off rather than treated as free or full-price, so this stays
            # a directional estimate, not a claimed-exact invoice match.
            cost_usd = round(
                ((input_tokens - cached) * in_rate + cached * in_rate * 0.5 + output_tokens * out_rate)
                / 1_000_000,
                6,
            )
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "conversation_id": conversation_id,
            "caller": caller or "user",
            "stage": stage,
            "model": model,
            "response_id": getattr(resp, "id", None),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "cost_usd_estimate": cost_usd,
        }
        if extra:
            record.update(extra)
        with _USAGE_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


def _fast_path_capture_text(message: str) -> Optional[str]:
    """Return the text to relay if `message` matches CAPTURE_FAST_PATH_PREFIX,
    else None (meaning: fall through to the normal premium path). Only the
    prefix match is case-insensitive; the captured text itself is relayed
    exactly as typed, never reworded."""
    stripped = message.strip()
    if not stripped.lower().startswith(CAPTURE_FAST_PATH_PREFIX):
        return None
    return stripped[len(CAPTURE_FAST_PATH_PREFIX):].strip()


def _run_capture_fast_path(text: str, caller: Optional[str] = None) -> "_TurnResult":
    """Execute the Phase 1 capture fast path: a real queueCaptureText call
    with zero model tokens spent. See CAPTURE_FAST_PATH_PREFIX for the
    trigger and its rationale. Always capture_type="pasted_content" --
    deep_research packets are richer evidence documents produced by an
    actual research cycle with their own JSON-sidecar contract
    (system/templates/deep_research_intelligence_drop.md), not something
    typed inline into chat, so that override stays out of this path's scope
    on purpose."""
    call_id = f"fast_path_{uuid.uuid4().hex[:12]}"
    if not text:
        return _TurnResult(
            reply=f"Nothing to capture — send `{CAPTURE_FAST_PATH_PREFIX} <text>` with the content to relay.",
            response_id=call_id,
            tool_calls=[],
            persist_response_id=False,
        )
    arguments = {"text": text, "capture_type": "pasted_content"}
    status_code, payload = _execute_operation("queueCaptureText", arguments)
    _audit_tool_call(
        "queueCaptureText", arguments, status_code, call_id, call_id,
        caller=caller, reason="capture_fast_path_deterministic_trigger",
    )
    tool_calls_made = [{"operation_id": "queueCaptureText", "arguments": arguments, "status": status_code}]
    if 200 <= status_code < 300 and isinstance(payload, dict):
        file_id = payload.get("file_id")
        if payload.get("status") == "already_queued":
            reply = f"Already queued (id `{file_id}`) — this exact text was captured before; nothing new was added."
        else:
            reply = (
                f"Captured (id `{file_id}`). No live processing ran — this lands in the pending "
                "queue for the morning pipeline, same as any other capture."
            )
    else:
        detail = payload if isinstance(payload, str) else json.dumps(payload)[:300]
        reply = f"Capture failed ({status_code}): {detail}"
    return _TurnResult(reply=reply, response_id=call_id, tool_calls=tool_calls_made, persist_response_id=False)


def _run_chat_turn(message: str, previous_response_id: Optional[str], conversation_id: str, caller: Optional[str] = None) -> "_TurnResult":
    if _openai_client is None:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured on server")

    tool_calls_made: list[dict] = []

    # RBB_SKILLS_ARCHITECTURE_SCOPE_2026-09-23 spike: computed once per turn
    # from the incoming message, then reused for every responses.create()
    # call this turn (including the mid-turn tool-output follow-up) so a
    # matched skill doesn't disappear partway through a turn that's still
    # using it. matched_skills is logged alongside real token counts below
    # so the spike's actual token delta is measurable from real usage, not
    # estimated.
    turn_instructions, turn_tools, matched_skills = rbb_chat_skills.build_turn_payload(
        message, _current_instructions(), TOOLS,
    )
    usage_extra = {"matched_skills": sorted(matched_skills)}

    try:
        resp = None
        if previous_response_id:
            try:
                resp = _openai_client.responses.create(
                    model=MODEL,
                    previous_response_id=previous_response_id,
                    input=message,
                    instructions=turn_instructions,
                    tools=turn_tools,
                    tool_choice="required",
                )
                _log_usage("initial_chained", resp, conversation_id, caller=caller, extra=usage_extra)
            except (openai.BadRequestError, openai.NotFoundError):
                # Prior response fell out of OpenAI's retention window (stored
                # responses are not kept forever), or the chain itself is no
                # longer valid. Fall back to a fresh turn rather than
                # erroring the whole request — the persisted transcript on
                # our side is unaffected either way.
                resp = None
        if resp is None:
            resp = _openai_client.responses.create(
                model=MODEL,
                input=message,
                instructions=turn_instructions,
                tools=turn_tools,
                tool_choice="required",
            )
            _log_usage("initial_fresh", resp, conversation_id, caller=caller, extra=usage_extra)

        for _ in range(MAX_TOOL_TURNS):
            fn_calls = [item for item in resp.output if getattr(item, "type", None) == "function_call"]
            if not fn_calls:
                break

            outputs = []
            for fn_call in fn_calls:
                try:
                    arguments = json.loads(fn_call.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                status_code, payload = _execute_operation(fn_call.name, arguments)
                _audit_tool_call(fn_call.name, arguments, status_code, resp.id, fn_call.call_id, caller=caller)
                tool_calls_made.append({
                    "operation_id": fn_call.name,
                    "arguments": arguments,
                    "status": status_code,
                })
                # Briefs alone run ~70KB; gpt-4o-mini's context is 128K tokens,
                # so there's real headroom. This cap exists only to stop a
                # runaway payload from blowing the whole context, not to
                # bound normal responses — an 8KB cap here silently
                # truncated real briefs mid-JSON, making
                # pre_rendered_brief.available unreadable and driving a
                # false "brief not available" reply despite a real 200.
                outputs.append({
                    "type": "function_call_output",
                    "call_id": fn_call.call_id,
                    "output": json.dumps({"status": status_code, "body": payload})[:300000],
                })

            resp = _openai_client.responses.create(
                model=MODEL,
                previous_response_id=resp.id,
                input=outputs,
                instructions=turn_instructions,
                tools=turn_tools,
                tool_choice="auto",
            )
            _log_usage("tool_output_followup", resp, conversation_id, caller=caller, extra=usage_extra)

        reply = getattr(resp, "output_text", None)
        if not reply:
            # RB-2026-08-28: confirmed live -- the loop above can exit with
            # `resp` itself holding an unanswered function_call (MAX_TOOL_TURNS
            # exhausted exactly on a round whose follow-up call was ANOTHER
            # tool call, not text), which has no output_text by definition.
            # Real tool calls had already succeeded (mutations_applied=2 in
            # one real incident) but Todd saw nothing at all. Try once, with
            # tool_choice="none" (a real reply is forced, no further tool
            # call is possible), to get the model's own honest wrap-up of
            # what it just did. If that also fails or comes back empty --
            # a dangling unanswered function_call can make the API reject
            # the follow-up outright -- fall back to a deterministic summary
            # built from tool_calls_made (already recorded, real status
            # codes) rather than another LLM call that could ALSO go silent.
            try:
                nudge = _openai_client.responses.create(
                    model=MODEL,
                    previous_response_id=resp.id,
                    input=(
                        "Reply now in plain text, summarizing what the tool calls "
                        "above actually did (or didn't) accomplish. No more tool calls."
                    ),
                    instructions=turn_instructions,
                    tool_choice="none",
                )
                _log_usage("nudge", nudge, conversation_id, caller=caller, extra=usage_extra)
                reply = getattr(nudge, "output_text", None) or None
                if reply:
                    resp = nudge
            except openai.OpenAIError:
                reply = None
        if not reply:
            if tool_calls_made:
                lines = [f"- {tc['operation_id']} ({tc['status']})" for tc in tool_calls_made]
                reply = (
                    "No summary was generated this turn, but these tool calls did run:\n"
                    + "\n".join(lines)
                )
            else:
                reply = "(no response text — check tool call results)"
        return _TurnResult(reply=reply, response_id=resp.id, tool_calls=tool_calls_made)

    except openai.OpenAIError as exc:
        # Any OpenAI-side failure at any point in the turn (initial call,
        # fallback call, or a mid-loop follow-up submitting tool results)
        # lands here instead of propagating as a raw HTTP error status.
        # Two reasons: (1) Cloudflare's edge replaces 502/504-class origin
        # responses with its own branded HTML error page, which broke the
        # client's response.json() parse with zero useful information; (2)
        # even without that, a raw error status gives Todd no visibility
        # into whether any of the tool calls above it actually happened for
        # real. Returning a normal 200 with an honest explanation, plus
        # whatever tool_calls_made already completed, is strictly more
        # informative and never worse than an opaque failure.
        _log_turn_error(conversation_id, exc, stage="responses.create", caller=caller)
        prefix = (
            f"{len(tool_calls_made)} tool call(s) completed for real before this happened.\n\n"
            if tool_calls_made else ""
        )
        # context_length_exceeded is NOT transient -- it means THIS
        # conversation has accumulated too much content for the model's
        # context window (confirmed live 2026-08-27: a long-running thread
        # with several ~11K-character embedded briefs hit this). Every
        # future turn in the same conversation will keep failing the same
        # way via previous_response_id chaining, since it keeps
        # re-including the same oversized history -- "try rephrasing, ask
        # again" was actively wrong advice here. A brand-new conversation
        # has none of that accumulated history and works immediately.
        error_code = getattr(exc, "code", None)
        exc_body = getattr(exc, "body", None)
        if not error_code and isinstance(exc_body, dict):
            error_code = (exc_body.get("error") or {}).get("code")
        is_context_length = error_code == "context_length_exceeded" or "context_length_exceeded" in str(exc)
        if is_context_length:
            reply = (
                f"{prefix}This conversation has grown too large for the model's context "
                "window and can't continue -- this isn't transient, and rephrasing or "
                "retrying here won't help; every turn re-sends this conversation's full "
                "history. Click **New Chat** and ask again there. Nothing above was "
                "fabricated."
            )
        else:
            reply = (
                f"{prefix}RB hit a real error partway through that request "
                f"({type(exc).__name__}) and stopped rather than guess. Nothing above was "
                "fabricated. Try rephrasing, or ask again — this is often transient."
            )
        return _TurnResult(reply=reply, response_id=previous_response_id or "error", tool_calls=tool_calls_made)


@app.post("/chat", response_model=ChatResponse)
def post_chat(body: ChatRequest, x_chat_passcode: Optional[str] = Header(None)):
    _auth(x_chat_passcode)
    conversation_id = body.conversation_id or history.get_current_conversation_id()
    previous_response_id = history.get_last_response_id(conversation_id)

    started = time.monotonic()
    capture_text = _fast_path_capture_text(body.message)
    try:
        if capture_text is not None:
            result = _run_capture_fast_path(capture_text, caller=body.caller)
        else:
            result = _run_chat_turn(body.message, previous_response_id, conversation_id, caller=body.caller)
    except HTTPException:
        raise
    except Exception as exc:
        # Last-resort net for anything that isn't an openai.OpenAIError
        # (those are already caught and turned into an honest in-turn reply
        # inside _run_chat_turn) -- a genuine bug in this code, not a
        # transient API failure. 500 passes through Cloudflare untouched
        # (502/504/521-class codes get edge-intercepted and replaced with a
        # branded HTML page, which is what broke the client's response.json()
        # parse the first time this was hit).
        _log_turn_error(conversation_id, exc, stage="post_chat", caller=body.caller)
        raise HTTPException(status_code=500, detail=f"Unexpected server error: {exc}")
    elapsed = time.monotonic() - started

    reply_text = result.reply
    prior_size = history.estimate_size_chars(conversation_id)
    projected_size = prior_size + len(body.message) + len(reply_text)
    if projected_size > CONVERSATION_SIZE_WARN_CHARS:
        reply_text += (
            f"\n\n— This conversation is now roughly {projected_size // 1000}K characters of "
            "history, and every future turn here reprocesses all of it (real, billed tokens, "
            "not free). If your next question doesn't need this thread's context, consider "
            "**New Chat**."
        )

    history.append_turn(conversation_id, "user", body.message)
    history.append_turn(
        conversation_id, "assistant", reply_text,
        response_id=result.response_id if result.persist_response_id else None,
        tool_calls=result.tool_calls,
    )

    audit_log.append_event(
        event_type="source_accessed",
        item_summary="rbb_chat turn completed",
        reason="chat_turn",
        outcome="completed",
        data_class="operational",
        source="rbb_chat",
        extra={
            "response_id": result.response_id,
            "conversation_id": conversation_id,
            "elapsed_seconds": round(elapsed, 2),
            "tool_call_count": len(result.tool_calls),
            "caller": body.caller or "user",
        },
    )
    return ChatResponse(
        reply=reply_text,
        response_id=result.response_id,
        conversation_id=conversation_id,
        tool_calls=result.tool_calls,
    )


@app.get("/conversations")
def list_conversations(x_chat_passcode: Optional[str] = Header(None)):
    """RB-2026-08-28: backs the UI's conversation sidebar. The storage
    model already supported multiple conversations by id -- this is the
    first endpoint that actually lists them (newest-touched first, each
    with an auto-derived title)."""
    _auth(x_chat_passcode)
    return {"conversations": history.list_conversations()}


@app.get("/conversation", response_model=ConversationHistory)
def get_conversation(conversation_id: Optional[str] = None, x_chat_passcode: Optional[str] = Header(None)):
    _auth(x_chat_passcode)
    cid = conversation_id or history.get_current_conversation_id()
    turns = history.load_conversation(cid)
    return ConversationHistory(conversation_id=cid, turns=turns)


@app.post("/conversation/new", response_model=ConversationHistory)
def post_new_conversation(x_chat_passcode: Optional[str] = Header(None)):
    _auth(x_chat_passcode)
    cid = history.start_new_conversation()
    return ConversationHistory(conversation_id=cid, turns=[])


@app.get("/brief/{account_slug}/download")
def get_brief_download(
    account_slug: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves an Account Background Brief as a real, downloadable .md file
    -- a plain browser navigation (a clicked or pasted link), not a JSON
    API response, so accepts the passcode via ?passcode= as well as the
    header (see _auth_flexible). The model never constructs this URL
    itself -- it only ever relays the exact string getBriefDownloadLink
    returns, which already points here."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("getAccountResearch", {"account_slug": account_slug})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    brief = (payload or {}).get("latest_background_brief") or {}
    markdown = brief.get("markdown")
    if not markdown:
        raise HTTPException(status_code=404, detail=f"No Background Brief has been generated yet for '{account_slug}'.")
    resolved_slug = payload.get("account", {}).get("account_slug", account_slug)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{resolved_slug}-background-brief.md"'},
    )


@app.get("/account-plan/{account_slug}/download")
def get_account_plan_download(
    account_slug: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves an Account Plan as a real, downloadable .md file -- same
    pattern as get_brief_download above, distinct route so the two
    document types never get conflated (see getAccountPlanDownloadLink's
    own docstring)."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("getAccountPlan", {"account_slug": account_slug})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    markdown = (payload or {}).get("markdown")
    if not markdown:
        raise HTTPException(status_code=404, detail=f"No Account Plan has been generated yet for '{account_slug}'.")
    resolved_slug = (payload or {}).get("slug", account_slug)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{resolved_slug}-account-plan.md"'},
    )


@app.get("/green-sheet/{account_slug}/download")
def get_green_sheet_download(
    account_slug: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves a Green Sheet as a real, downloadable .md file -- same
    pattern as get_account_plan_download above."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("getGreenSheet", {"account_slug": account_slug})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    markdown = (payload or {}).get("markdown")
    if not markdown:
        raise HTTPException(status_code=404, detail=f"No Green Sheet has been generated yet for '{account_slug}'.")
    resolved_slug = (payload or {}).get("slug", account_slug)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{resolved_slug}-green-sheet.md"'},
    )


@app.get("/win-plan/{account_slug}/download")
def get_win_plan_download(
    account_slug: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves a Win Plan as a real, downloadable .md file -- same pattern
    as get_account_plan_download above."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("getWinPlan", {"account_slug": account_slug})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    markdown = (payload or {}).get("markdown")
    if not markdown:
        raise HTTPException(status_code=404, detail=f"No Win Plan has been generated yet for '{account_slug}'.")
    resolved_slug = (payload or {}).get("slug", account_slug)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{resolved_slug}-win-plan.md"'},
    )


@app.get("/win-loss-review/{account_slug}/{opportunity_slug}/download")
def get_win_loss_review_download(
    account_slug: str,
    opportunity_slug: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves one specific Win/Loss Review as a real, downloadable .md
    file -- same pattern as get_win_plan_download above, keyed by both
    account_slug and opportunity_slug."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation(
        "getWinLossReview", {"account_slug": account_slug, "opportunity_slug": opportunity_slug},
    )
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    markdown = (payload or {}).get("markdown")
    if not markdown:
        raise HTTPException(status_code=404, detail=f"No Win/Loss Review has been generated yet for '{account_slug}' / '{opportunity_slug}'.")
    resolved_account_slug = (payload or {}).get("account_slug", account_slug)
    resolved_opportunity_slug = (payload or {}).get("opportunity_slug", opportunity_slug)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{resolved_account_slug}-{resolved_opportunity_slug}-win-loss-review.md"'},
    )


@app.get("/rfp-response-plan/{account_slug}/download")
def get_rfp_response_plan_download(
    account_slug: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves an RFP Response Plan as a real, downloadable .md file --
    same pattern as get_account_plan_download above."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("getRfpResponsePlan", {"account_slug": account_slug})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    markdown = (payload or {}).get("markdown")
    if not markdown:
        raise HTTPException(status_code=404, detail=f"No RFP Response Plan has been generated yet for '{account_slug}'.")
    resolved_slug = (payload or {}).get("slug", account_slug)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{resolved_slug}-rfp-response-plan.md"'},
    )


@app.get("/battle-cards/{category}/download")
def get_battle_card_download(
    category: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves a persisted Battle Card as a real, downloadable .md file --
    same pattern as get_rfp_response_plan_download above, but category-
    scoped (RB-2026-09-07, competitive-side) rather than account-scoped."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("getBattleCard", {"category": category})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    markdown = (payload or {}).get("markdown")
    if not markdown:
        raise HTTPException(status_code=404, detail=f"No Battle Card has been generated yet for category '{category}'.")
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{category}-battle-card.md"'},
    )


@app.get("/competitive-briefs/{competitor_slug}/download")
def get_competitive_brief_download(
    competitor_slug: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves a persisted Competitive Brief as a real, downloadable .md
    file -- same pattern as get_rfp_response_plan_download above, but
    competitor-scoped (RB-2026-09-07, competitive-side)."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("getCompetitiveBrief", {"competitor_slug": competitor_slug})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    markdown = (payload or {}).get("markdown")
    if not markdown:
        raise HTTPException(status_code=404, detail=f"No Competitive Brief has been generated yet for competitor '{competitor_slug}'.")
    resolved_slug = (payload or {}).get("competitor_slug", competitor_slug)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{resolved_slug}-competitive-brief.md"'},
    )


@app.get("/value-wedges/{competitor_slug}/download")
def get_value_wedge_download(
    competitor_slug: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves a persisted Value Wedge as a real, downloadable .md file --
    same pattern as get_competitive_brief_download above, but for the
    Value Wedge (RB-2026-09-25, competitive-side)."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("getValueWedge", {"competitor_slug": competitor_slug})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    markdown = (payload or {}).get("markdown")
    if not markdown:
        raise HTTPException(status_code=404, detail=f"No Value Wedge has been generated yet for competitor '{competitor_slug}'.")
    resolved_slug = (payload or {}).get("competitor_slug", competitor_slug)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{resolved_slug}-value-wedge.md"'},
    )


@app.get("/vendor-engagement-analysis/{account_slug}/download")
def get_vendor_engagement_analysis_download(
    account_slug: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves a persisted Vendor Engagement Analysis as a real, downloadable
    .md file -- same pattern as get_competitive_brief_download above, but
    account-scoped (RB-2026-09-07, competitive-side)."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("getVendorEngagementAnalysis", {"account_slug": account_slug})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    markdown = (payload or {}).get("markdown")
    if not markdown:
        raise HTTPException(status_code=404, detail=f"No Vendor Engagement Analysis has been generated yet for '{account_slug}'.")
    resolved_slug = (payload or {}).get("slug", account_slug)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{resolved_slug}-vendor-engagement-analysis.md"'},
    )


@app.get("/relationship-cards/{contact_id}/download")
def get_relationship_card_download(
    contact_id: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves a PUBLISHED Relationship Card as a real, downloadable .md
    file -- same pattern as get_vendor_engagement_analysis_download above
    (RB-2026-09-08, relationship-side)."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("getRelationshipCard", {"contact_id": contact_id})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    markdown = (payload or {}).get("markdown")
    if not markdown:
        raise HTTPException(status_code=404, detail=f"No Relationship Card has been published yet for '{contact_id}'.")
    resolved_id = (payload or {}).get("contact_id", contact_id)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{resolved_id}-relationship-card.md"'},
    )


@app.get("/relationship-plans/{contact_id}/download")
def get_relationship_plan_download(
    contact_id: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves a Relationship Plan as a real, downloadable .md file --
    same pattern as get_relationship_card_download above (RB-2026-09-08,
    relationship-side, final artifact)."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("getRelationshipPlan", {"contact_id": contact_id})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    markdown = (payload or {}).get("markdown")
    if not markdown:
        raise HTTPException(status_code=404, detail=f"No Relationship Plan has been generated yet for '{contact_id}'.")
    resolved_id = (payload or {}).get("contact_id", contact_id)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{resolved_id}-relationship-plan.md"'},
    )


@app.get("/inner-circle/download")
def get_inner_circle_download(
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves the persisted Inner Circle roll-up as a real, downloadable
    .md file -- singleton, no id/slug (RB-2026-09-08, relationship-side)."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("getInnerCircle", {})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    markdown = (payload or {}).get("markdown")
    if not markdown:
        raise HTTPException(status_code=404, detail="No Inner Circle has been generated yet.")
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="inner-circle.md"'},
    )


@app.get("/referral-network/overview/download")
def get_referral_network_overview_download(
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves the PUBLISHED Referral Network Overview as a real,
    downloadable .md file -- singleton, no id/slug (RB-2026-09-08,
    relationship-side)."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("getReferralNetworkOverview", {})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    markdown = (payload or {}).get("markdown")
    if not markdown:
        raise HTTPException(status_code=404, detail="No Referral Network Overview has been published yet.")
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="referral-network-overview.md"'},
    )


@app.get("/referral-network/analysis/{target}/download")
def get_referral_network_analysis_download(
    target: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves a persisted Referral Network Analysis as a real, downloadable
    .md file -- per-target (RB-2026-09-08, relationship-side)."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("getReferralNetworkAnalysis", {"target": target})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    markdown = (payload or {}).get("markdown")
    if not markdown:
        raise HTTPException(status_code=404, detail=f"No Referral Network Analysis has been generated yet for '{target}'.")
    resolved_target = (payload or {}).get("target", target)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{resolved_target}-referral-network-analysis.md"'},
    )


def _regenerate_blue_sheet_workbook(account_slug: str) -> Optional[Path]:
    """Best-effort fallback for get_blue_sheet_download: regenerate the
    real .xlsx from the account's current data. Never raises -- a failed
    regeneration attempt should fall through to the normal 404, not crash
    the download route. See RB-2026-09-02 fix note above."""
    try:
        engine_dir = SYSTEM_DIR.parent / "blue_sheets" / "_engine"
        if str(engine_dir) not in sys.path:
            sys.path.insert(0, str(engine_dir))
        import render as bs_render  # noqa: E402
        return bs_render.render(account_slug)
    except Exception:  # noqa: BLE001
        return None


@app.get("/blue-sheet/{account_slug}/download")
def get_blue_sheet_download(
    account_slug: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves a Blue Sheet's real xlsx workbook -- distinct from
    /brief/{slug}/download, which serves an Account Research brief's
    markdown. Two different document types, two different download
    routes -- conflating them was the real bug found live 2026-08-27."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("listBlueSheetAccounts", {})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    accounts = (payload or {}).get("accounts", [])
    entry = next((a for a in accounts if a.get("account_id") == f"acct-{account_slug}"), None)
    if entry is None or not entry.get("workbook_path"):
        raise HTTPException(status_code=404, detail=f"No Blue Sheet workbook found for '{account_slug}'.")
    file_path = SYSTEM_DIR.parent / entry["workbook_path"]
    if not file_path.exists():
        # RB-2026-09-02: defense in depth -- a registered-but-missing file
        # was live-reproduced against pollo-campero (a stale registry path
        # from before create_account.py computed paths correctly; see
        # blue_sheet_registry.json's fix the same day). Rather than 404
        # straight away, try to regenerate the real workbook from the
        # account's current data before giving up.
        regenerated_path = _regenerate_blue_sheet_workbook(account_slug)
        if regenerated_path is not None and regenerated_path.exists():
            file_path = regenerated_path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Workbook is registered but the file is missing on disk: {entry['workbook_path']}")
    return FileResponse(
        file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=file_path.name,
    )


@app.get("/blue-sheet-customer-artifact/{account_slug}/{artifact_id}/download")
def get_blue_sheet_customer_artifact_download(
    account_slug: str,
    artifact_id: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves one customer-facing artifact (a structured table saved via
    addBlueSheetEvidence's sibling addBlueSheetCustomerArtifact) as a
    fresh, clean single-sheet xlsx -- generated on demand from the stored
    structured data via customer_artifact.render_customer_artifact_workbook(),
    never touching disk. Matches get_ecosystem_workbook_download's in-
    memory pattern rather than get_blue_sheet_download's registry-file
    pattern -- there's no stored file path here to ever drift out of sync,
    the exact bug class fixed the same day for the main Blue Sheet
    workbook (see blue_sheet_registry.json / render.py's row-capacity
    fix)."""
    _auth_flexible(x_chat_passcode, passcode)
    try:
        engine_dir = SYSTEM_DIR.parent / "blue_sheets" / "_engine"
        if str(engine_dir) not in sys.path:
            sys.path.insert(0, str(engine_dir))
        import customer_artifact as bs_customer_artifact  # noqa: E402
        wb, record = bs_customer_artifact.render_customer_artifact_workbook(account_slug, artifact_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    import io
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    # Content-Disposition is a raw latin-1 header -- a title with an em-dash
    # or curly quote (very likely here; see the real one used live,
    # "... — Customer-Facing Draft") 500s the whole download otherwise.
    # Confirmed live 2026-09-02. ascii-fold rather than reject: keep the
    # filename readable instead of just dropping the offending characters.
    ascii_title = (
        record["title"]
        .replace("—", "-").replace("–", "-")
        .replace("‘", "'").replace("’", "'")
        .replace("“", '"').replace("”", '"')
        .encode("ascii", "ignore").decode("ascii")
    )
    filename = f"{account_slug}-{artifact_id}-{ascii_title}.xlsx".replace(" ", "_").replace("/", "-")
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/capture/{file_id}/download")
def get_capture_download(
    file_id: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves a processed capture's real transcript as a downloadable .md
    file. RB-2026-08-28: closes the gap Todd flagged -- captures were only
    readable inline in chat, never downloadable to review/revise locally."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("getCaptureProcessed", {"file_id": file_id})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    transcript = (payload or {}).get("transcript")
    if not transcript:
        raise HTTPException(status_code=404, detail=f"Capture '{file_id}' has no transcript available.")
    title = (payload or {}).get("title_hint") or file_id
    # Same latent bug found and fixed live 2026-09-02 in
    # get_blue_sheet_customer_artifact_download just above: a raw
    # Content-Disposition header is latin-1 only, and a title_hint is
    # free text that can carry an em-dash/curly-quote/etc, which 500s the
    # whole download. ascii-fold rather than reject.
    ascii_title = title.encode("ascii", "ignore").decode("ascii") or file_id
    return Response(
        content=transcript,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{file_id}-{ascii_title}.md"'.replace(" ", "_")},
    )


@app.get("/master-account-plan/{vendor_slug}/download")
def get_master_account_plan_download(
    vendor_slug: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves a Master Account Plan's real xlsx workbook -- a third,
    distinct document type from a Blue Sheet account or an Account
    Research brief."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("listMasterAccountPlans", {})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    plans = (payload or {}).get("plans", [])
    entry = next((p for p in plans if p.get("vendor_slug") == vendor_slug), None)
    if entry is None or not entry.get("workbook_path"):
        raise HTTPException(status_code=404, detail=f"No Master Account Plan workbook found for '{vendor_slug}'.")
    file_path = SYSTEM_DIR.parent / entry["workbook_path"]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Workbook is registered but the file is missing on disk: {entry['workbook_path']}")
    return FileResponse(
        file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=file_path.name,
    )


@app.get("/vendor-list/download")
def get_vendor_list_download(
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves a fresh xlsx export of every tracked vendor entity
    (ecosystem_intelligence.json's real ~88-vendor list, 2026-09-01,
    Todd's own request, xlsx per his follow-up the same day) -- generated
    on demand from the real graph, not a pre-rendered file, so it's always
    current."""
    _auth_flexible(x_chat_passcode, passcode)
    status_code, payload = _execute_operation("listVendors", {})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)
    rows = (payload or {}).get("vendors", [])

    import io
    from openpyxl import Workbook
    fieldnames = [
        "vendor_id", "name", "aliases", "primary_category", "status",
        "brand_relationship_count", "is_tracked_competitor", "created_at", "updated_at",
    ]
    wb = Workbook()
    ws = wb.active
    ws.title = "Vendors"
    ws.append(fieldnames)
    for row in rows:
        # RB-SECURITY-2026-09-05: name/aliases can carry externally-
        # influenced text -- same formula/CSV-injection guard (CWE-1236)
        # already applied to Blue Sheets. This is a real, user-downloaded
        # export, not an internal-only file.
        ws.append(xlsx_safety.sanitize_row([row.get(f) for f in fieldnames]))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    from datetime import date as _date
    today_str = _date.today().isoformat()
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="rb-vendor-list-{today_str}.xlsx"'},
    )


@app.get("/competitive-landscape/download")
def get_competitive_landscape_download(
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves a fresh multi-tab competitive tech-stack analysis workbook
    (2026-09-01, Todd's corrected direction -- see
    competitive_landscape.py's module docstring) -- generated on demand
    directly from the real graph + competitor_intelligence, not a
    pre-rendered file. Imports the analysis/export modules directly
    (same established pattern as this file's own audit_log/rbb_chat_history
    imports) rather than round-tripping through the JSON API, since the
    full analysis (25 categories x market share + battle cards + a
    1,654-row brand grid) is large and the export module already reads the
    graph directly -- no duplication either way."""
    _auth_flexible(x_chat_passcode, passcode)
    import io
    import competitive_landscape_export as cl_export

    buf = io.BytesIO()
    wb = cl_export.build_workbook()
    wb.save(buf)
    buf.seek(0)

    from datetime import date as _date
    today_str = _date.today().isoformat()
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="rb-competitive-landscape-{today_str}.xlsx"'},
    )


@app.get("/ecosystem-workbook/download")
def get_ecosystem_workbook_download(
    export_type: str,
    passcode: Optional[str] = None,
    x_chat_passcode: Optional[str] = Header(None),
):
    """Serves a fresh 6-tab restaurant-tech graph workbook
    (ecosystem_export.py, built 2026-07-31 but never wired up until now --
    see _local_get_ecosystem_workbook_download_link) -- generated on demand
    directly from the real graph via build_workbook(), never touching disk
    (matches get_vendor_list_download's in-memory pattern, not
    get_blue_sheet_download's registry-file pattern, since this is a single
    canonical resource with no per-account registry entry). export_type is
    required -- "internal" includes Todd's own strategic_note/confidence
    rationale, "shareable" strips both for external use."""
    _auth_flexible(x_chat_passcode, passcode)
    if export_type not in ecosystem_export.EXPORT_TYPES:
        raise HTTPException(status_code=400, detail=f"export_type must be one of {ecosystem_export.EXPORT_TYPES}")

    wb, _metadata = ecosystem_export.build_workbook(export_type=export_type)
    import io
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    from datetime import date as _date
    today_str = _date.today().isoformat()
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="rb-ecosystem-{export_type}-{today_str}.xlsx"'},
    )


class UploadResponse(BaseModel):
    filename: str
    status: int
    ok: bool
    error: Optional[str] = None
    reply: str
    receipt: dict
    conversation_id: str


# Purely a sanity ceiling to protect this process, not a workaround for
# RB-DEFECT-068 -- that defect was specific to a model having to generate/
# copy a huge base64 blob as a tool-call argument. This path never does
# that: the browser sends real bytes over multipart/form-data, this code
# reads and re-encodes them itself, and the real /ingest/upload call below
# carries genuinely complete content -- the empty/corrupted/truncated
# failure shapes that endpoint guards against structurally can't happen
# here the way they did from a Custom GPT Action.
_MAX_UPLOAD_BYTES = 40 * 1024 * 1024
_IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _extract_text_from_image(content: bytes, content_type: str, conversation_id: Optional[str] = None) -> Optional[str]:
    """Real, automatic content extraction (verified via a live spike call
    before building this: gpt-4o-mini's Responses API accepts inline
    base64 images and gives an accurate, grounded description). This is
    what a screenshot needed all along -- not a human typing a caption
    first. Confirmed incident (2026-08-27): a manual-caption prompt was
    tried instead, and Todd correctly called it out as violating RBB's
    actual design principle -- the CoS should recognize input
    automatically, the same way it's expected to for every other input
    type. This step only describes/transcribes pixels that genuinely
    exist in the real uploaded file (OCR-equivalent perception, not
    invention) -- the actual persistence decision still goes through
    RBB's real, already-gated ingestContent/uploadAndIngestFile pipeline,
    unchanged. Best-effort: returns None on any failure, so the upload
    still proceeds (file saved, just without automatic text extraction)
    rather than blocking on this step.
    """
    if _openai_client is None:
        return None
    try:
        b64_content = b64.b64encode(content).decode("ascii")
        resp = _openai_client.responses.create(
            model=CHEAP_MODEL,
            input=[{
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Transcribe all visible text in this image verbatim, in reading order "
                            "(this may be a screenshot of an email, message, or document). Then, in "
                            "one sentence, describe what kind of content it is (e.g. 'email from X to "
                            "Y about Z', 'text message thread', 'meeting notes'). Only describe what is "
                            "actually visible -- do not guess at anything not shown."
                        ),
                    },
                    {"type": "input_image", "image_url": f"data:{content_type};base64,{b64_content}", "detail": "high"},
                ],
            }],
        )
        _log_usage("image_ocr", resp, conversation_id or "n/a")
        return getattr(resp, "output_text", None) or None
    except openai.OpenAIError:
        return None


@app.post("/upload", response_model=UploadResponse)
async def post_upload(
    file: UploadFile = File(...),
    conversation_id: Optional[str] = None,
    extracted_text: Optional[str] = Form(None),
    x_chat_passcode: Optional[str] = Header(None),
):
    _auth(x_chat_passcode)
    cid = conversation_id or history.get_current_conversation_id()
    filename = file.filename or "upload.bin"

    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        receipt = {
            "error": (
                f"'{filename}' is {len(content) / 1_048_576:.1f}MB, over this endpoint's "
                f"{_MAX_UPLOAD_BYTES // 1_048_576}MB sanity limit. Save it to the appropriate "
                "watched inbox folder instead (system/inbox/linkedin_exports/, "
                "system/inbox/whatsapp_exports/, etc.) -- the nightly pipeline picks it up "
                "automatically."
            )
        }
        status_code = 413
    else:
        upload_args = {"filename": filename, "content_base64": b64.b64encode(content).decode("ascii")}
        # Images (e.g. a screenshot) have no text of their own -- RBB's real
        # ingest pipeline can save the file but explicitly can't turn it into
        # intelligence without extracted_text. Automatic, not a caption
        # prompt (see _extract_text_from_image's docstring for why that
        # first version was wrong): the CoS is supposed to recognize input
        # on its own, the same as any other input type. An explicit
        # extracted_text from the caller (if ever sent) still wins.
        is_image = (file.content_type in _IMAGE_CONTENT_TYPES) or (Path(filename).suffix.lower() in _IMAGE_EXTENSIONS)
        effective_text = extracted_text
        if is_image and not effective_text:
            effective_text = _extract_text_from_image(content, file.content_type or "image/png", conversation_id=cid)
        if effective_text:
            upload_args["extracted_text"] = effective_text
        status_code, receipt = _execute_operation("uploadAndIngestFile", upload_args)

    # A 200 here only means the HTTP call succeeded -- the real ingest
    # pipeline can still report an inner failure (ok: false) inside a 200
    # body, exactly the image-with-no-text case above. Check both, so the
    # UI doesn't say "Upload receipt above" for something that didn't
    # actually get processed.
    inner_ok = receipt.get("ingest_result", {}).get("ok") if isinstance(receipt.get("ingest_result"), dict) else None
    is_write = 200 <= status_code < 300 and inner_ok is not False
    audit_log.append_event(
        event_type="mutation_executed" if is_write else "mutation_rejected",
        item_summary=f"rbb_chat file upload: {filename}",
        reason="upload_and_ingest_file",
        outcome="executed" if is_write else "failed",
        data_class="memory",
        source="rbb_chat",
        extra={
            "conversation_id": cid,
            "filename": filename,
            "http_status": status_code,
            "size_bytes": len(content),
        },
    )

    # The upload itself is never routed through the model -- deterministic
    # execution stays deterministic, same RB-DEFECT-068 discipline as
    # always. But Todd's live feedback (2026-08-27): a flat mechanical
    # string here "fails to engage the CoS" -- the persona should still
    # react to something that genuinely happened. Reconciled by narrating
    # the ALREADY-COMPLETED real receipt with tool_choice="none" (the model
    # cannot call anything else here -- there is nothing left to call, the
    # real work is done) -- it can only comment on verified facts already
    # in hand, never claim or invent new ones. Best-effort: falls back to
    # the plain receipt line if this call fails for any reason.
    inner_error = None
    if isinstance(receipt.get("ingest_result"), dict) and receipt["ingest_result"].get("error"):
        inner_error = receipt["ingest_result"]["error"]
    fallback_text = (
        "Upload receipt above." if is_write
        else f"Upload failed: {receipt.get('error') or receipt.get('detail') or inner_error or f'HTTP {status_code}'}"
    )
    reply_text = fallback_text
    if _openai_client is not None:
        try:
            narration = _openai_client.responses.create(
                model=CHEAP_MODEL,
                instructions=_current_instructions(),
                tool_choice="none",
                input=(
                    f"A file upload just completed for real, deterministically, before you saw this "
                    f"message -- you did not call any tool for it and should not claim to have. "
                    f"Filename: {filename!r}. Success: {is_write}. "
                    f"Real receipt from RBB's backend: {json.dumps(receipt)[:4000]}\n\n"
                    "In 1-2 sentences, in your normal voice, acknowledge what happened using only "
                    "what's in this receipt -- do not infer or invent content beyond it (e.g. don't "
                    "guess what's in an image)."
                ),
            )
            _log_usage("upload_narration", narration, cid)
            text = getattr(narration, "output_text", None)
            if text:
                reply_text = text
        except openai.OpenAIError:
            pass

    history.append_turn(cid, "user", f"[Uploaded file: {filename}]")
    history.append_turn(
        cid, "assistant", reply_text,
        tool_calls=[{"operation_id": "uploadAndIngestFile", "arguments": {"filename": filename}, "status": status_code, "ok": is_write}],
    )

    return UploadResponse(
        filename=filename, status=status_code, receipt=receipt, conversation_id=cid, ok=is_write,
        error=inner_error or receipt.get("error") or receipt.get("detail"), reply=reply_text,
    )


# ---------------------------------------------------------------------------
# Voice: push-to-talk (RB-2026-08-30)
# ---------------------------------------------------------------------------
# Deliberately not full-duplex live voice (OpenAI's Realtime API) -- these
# two endpoints are a pure input/output wrapper around the exact same
# /chat -> _run_chat_turn pipeline every typed message already goes
# through. Neither touches that pipeline at all: the frontend transcribes
# here, sends the resulting text to /chat exactly as if typed, then sends
# the reply text here to be spoken. tool_choice="required" enforcement
# (this app's whole reason for existing over Custom GPT Actions -- see
# module docstring) is untouched by talking instead of typing.
#
# Models: gpt-4o-mini-transcribe / gpt-4o-mini-tts are the modern
# equivalents of whisper-1/tts-1 (same upgrade rationale as MODEL="gpt-5.5"
# above -- verify live against this account's key; whisper-1/tts-1 are the
# long-proven fallbacks, already used elsewhere in this codebase for
# capture transcription, if either 400s).
TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
SPEECH_MODEL = "gpt-4o-mini-tts"
SPEECH_VOICE = "alloy"


class VoiceTranscribeResponse(BaseModel):
    text: str


class VoiceSpeakRequest(BaseModel):
    text: str


@app.post("/voice/transcribe", response_model=VoiceTranscribeResponse)
async def post_voice_transcribe(
    file: UploadFile = File(...),
    x_chat_passcode: Optional[str] = Header(None),
):
    _auth(x_chat_passcode)
    if _openai_client is None:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured on this server.")

    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="That recording is too long to transcribe.")
    if not content:
        raise HTTPException(status_code=400, detail="Empty recording.")

    # openai's SDK infers audio format from the filename extension on an
    # in-memory buffer the same way it would from a real file's suffix --
    # no temp file needed, matching capture_ingest.py's proven
    # client.audio.transcriptions.create() call shape, just against bytes
    # instead of a path.
    import io
    suffix = Path(file.filename or "recording.webm").suffix or ".webm"
    buf = io.BytesIO(content)
    buf.name = f"recording{suffix}"

    try:
        result = _openai_client.audio.transcriptions.create(
            model=TRANSCRIBE_MODEL, file=buf, response_format="text",
        )
    except openai.OpenAIError as exc:
        raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}")

    text = result.strip() if isinstance(result, str) else getattr(result, "text", "").strip()
    return VoiceTranscribeResponse(text=text)


@app.post("/voice/speak")
def post_voice_speak(body: VoiceSpeakRequest, x_chat_passcode: Optional[str] = Header(None)):
    _auth(x_chat_passcode)
    if _openai_client is None:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not configured on this server.")
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Nothing to speak.")

    try:
        speech = _openai_client.audio.speech.create(
            model=SPEECH_MODEL, voice=SPEECH_VOICE, input=text, response_format="mp3",
        )
        audio_bytes = speech.read()
    except openai.OpenAIError as exc:
        raise HTTPException(status_code=502, detail=f"Speech synthesis failed: {exc}")

    return Response(content=audio_bytes, media_type="audio/mpeg")


@app.get("/health")
def get_health():
    return {"status": "ok", "tools_loaded": len(TOOLS), "rb_api_key_set": bool(RB_API_KEY), "openai_key_set": bool(OPENAI_API_KEY)}


_UI_PATH = Path(__file__).resolve().parent / "rbb_chat_ui.html"


@app.get("/", response_class=HTMLResponse)
def get_ui():
    # RB-2026-08-27: this page changes multiple times per active development
    # session (unlike a normal static site), and a browser silently serving
    # a stale cached copy after a real server-side fix is deployed is a real,
    # confusing failure mode -- confirmed live (Todd re-tested a genuine fix
    # and still saw the old broken behavior). Cache-Control: no-store forces
    # a fresh fetch on every load; not a meaningful cost for a single-user
    # chat UI that's a few KB of inline HTML/CSS/JS.
    return HTMLResponse(
        content=_UI_PATH.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )
