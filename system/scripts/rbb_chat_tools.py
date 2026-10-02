#!/usr/bin/env python3
"""
rbb_chat_tools.py — convert openapi_gpt.yaml into OpenAI Responses API tools.

Reads the same curated 30-op schema the Custom GPT already uses
(system/api/openapi_gpt.yaml) and produces two things the orchestrator
(rbb_chat.py) needs:

  1. TOOLS — a list of {"type": "function", "name", "description",
     "parameters"} dicts in the exact shape client.responses.create(tools=...)
     expects.
  2. OPERATIONS — a dict keyed by operationId with enough routing info to
     execute the real HTTP call: {method, path, path_params, query_params,
     has_body}. This is what lets the orchestrator turn a model's
     function_call back into a real request against rb-api, instead of
     trusting the model to have made the call itself.

Regenerate by re-running this module (or just re-import it — it builds the
tool list fresh from openapi_gpt.yaml on every import, same trigger
validate_openapi_gpt.py already watches: the file changing).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:
    sys.stderr.write(
        "PyYAML required. Install with: pip install pyyaml --break-system-packages\n"
    )
    sys.exit(2)

SYSTEM_DIR = Path(__file__).resolve().parent.parent
GPT_SPEC_PATH = SYSTEM_DIR / "api" / "openapi_gpt.yaml"


def _resolve_ref(ref: str, spec: dict) -> dict:
    assert ref.startswith("#/"), f"only in-document $ref supported: {ref}"
    node: Any = spec
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _resolve_schema(schema: dict, spec: dict) -> dict:
    if "$ref" in schema:
        return _resolve_schema(_resolve_ref(schema["$ref"], spec), spec)
    resolved = dict(schema)
    if "properties" in resolved:
        resolved["properties"] = {
            k: _resolve_schema(v, spec) for k, v in resolved["properties"].items()
        }
    return resolved


def _param_schema(param: dict, spec: dict) -> dict:
    schema = _resolve_schema(param.get("schema", {"type": "string"}), spec)
    if param.get("description") and "description" not in schema:
        schema["description"] = param["description"]
    return schema


def build_tools_and_operations(spec_path: Path = GPT_SPEC_PATH):
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = yaml.safe_load(f)

    tools: list[dict] = []
    operations: dict[str, dict] = {}

    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            operation_id = op.get("operationId")
            if not operation_id:
                continue

            properties: dict[str, Any] = {}
            required: list[str] = []
            path_params: list[str] = []
            query_params: list[str] = []

            for param in op.get("parameters", []) or []:
                pname = param["name"]
                properties[pname] = _param_schema(param, spec)
                if param.get("required"):
                    required.append(pname)
                if param.get("in") == "path":
                    path_params.append(pname)
                elif param.get("in") == "query":
                    query_params.append(pname)

            has_body = False
            request_body = op.get("requestBody")
            if request_body:
                body_schema_raw = (
                    request_body.get("content", {})
                    .get("application/json", {})
                    .get("schema")
                )
                if body_schema_raw:
                    body_schema = _resolve_schema(body_schema_raw, spec)
                    if body_schema.get("type") == "object" and body_schema.get("properties"):
                        has_body = True
                        for bname, bschema in body_schema["properties"].items():
                            properties[bname] = bschema
                        for bname in body_schema.get("required", []) or []:
                            if bname not in required:
                                required.append(bname)

            description = (op.get("description") or op.get("summary") or operation_id).strip()

            tools.append({
                "type": "function",
                "name": operation_id,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            })

            operations[operation_id] = {
                "method": method.upper(),
                "path": path,
                "path_params": path_params,
                "query_params": query_params,
                "has_body": has_body,
                "body_param_names": [
                    k for k in properties if k not in path_params and k not in query_params
                ] if has_body else [],
            }

    # rbb-chat calls OpenAI's Responses API directly with tool_choice, which
    # has no 30-tool ceiling — that cap is a Custom GPT Actions platform
    # limit, not a Responses API one. openapi_gpt.yaml is already at that
    # cap (30/30), so ops added here for rbb-chat specifically don't need to
    # displace anything from the Custom GPT's curated set. Kept as an
    # explicit, small list (not auto-pulled from the full openapi.yaml,
    # which is a stale reference snapshot, not continuously regenerated)
    # rather than introducing a live-server dependency at import time.
    for tool, op in _EXTRA_RBB_CHAT_ONLY_TOOLS:
        tools.append(tool)
        operations[tool["name"]] = op

    # RB-2026-09-22 (Phase 2, RBB_TOKEN_EFFICIENT_ARCHITECTURE_SCOPE_2026-09-19.md):
    # narrow what's actually resent to the model every turn, without losing
    # any routing capability the fast path / rb_cli / internal callers still
    # need. `operations` (the HTTP routing table) is deliberately left with
    # every op, including the excluded ones below — _execute_operation()
    # still resolves them for direct/internal callers (e.g. rbb_chat.py's
    # capture fast path calls "queueCaptureText" via _execute_operation
    # without ever going through the model's tool list). Only `tools` (the
    # schema actually sent to the LLM) is trimmed here.
    #
    # Checked against real audit-log call counts before cutting anything
    # (system/audit/*.jsonl, "rbb_chat tool call: <op>") — the vast majority
    # of zero-call tools right now are the competitive-intel/document-suite
    # ops shipped 2026-09-05 through 09-19, which are new and unproven, not
    # dead; cutting those would silently remove capability Todd just asked
    # for. Excluded here only the ops with an actual justification, not bare
    # zero-usage:
    #   - closeThread: self-audit (loop L-2026-09-12-001, recurring 3x)
    #     confirmed it takes live chat traffic and produces zero real
    #     mutations ever — offering a broken tool risks a fabricated receipt.
    #   - processAllCaptures: this file's own live prompt already tells the
    #     model "scheduled-pipeline only, never call this here" — it was
    #     never supposed to be a model-callable tool in the first place.
    #   - queueCaptureText: genuinely superseded for the chat surface by the
    #     Phase 1 capture fast path (a literal "capture: <text>" prefix that
    #     calls this same operation directly, zero model tokens); the model
    #     no longer needs its own copy to make that same call.
    _CHAT_LOOP_EXCLUDED_FROM_TOOLS = {"closeThread", "processAllCaptures", "queueCaptureText"}
    tools = [t for t in tools if t["name"] not in _CHAT_LOOP_EXCLUDED_FROM_TOOLS]

    return tools, operations


_EXTRA_RBB_CHAT_ONLY_TOOLS: list[tuple[dict, dict]] = [
    (
        {
            "type": "function", "name": "activateBlueSheetResearchShell",
            "description": (
                "Populate an existing research-only restaurant account as a canonical active Blue Sheet, "
                "preserving its evidence ledger. Use only after explicit user authorization for this "
                "specific Blue Sheet and with sourced structured content. Refuses to overwrite an "
                "account with active opportunities or an active Blue Sheet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_slug": {"type": "string"}, "display_name": {"type": "string"},
                    "account_data": {"type": "object"}, "brand_profile_data": {"type": "object"},
                    "actions_data": {"type": "array", "items": {"type": "object"}},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "user_authorization_quote": {"type": "string"},
                },
                "required": ["account_slug", "display_name", "account_data", "brand_profile_data", "user_authorization_quote"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/blue-sheets/{account_slug}/activate-research-shell",
            "path_params": ["account_slug"], "query_params": [], "has_body": True,
            "body_param_names": ["account_slug", "display_name", "account_data", "brand_profile_data", "actions_data", "aliases", "user_authorization_quote"],
        },
    ),
    (
        {
            "type": "function",
            "name": "redateLoop",
            "description": (
                "Move an OPEN loop's target date -- call this the moment Todd tells you when to "
                "push a loop to (e.g. 'assign to next week', 'late September', a specific date). "
                "RB-2026-08-29: getLoops/closeLoop existed, but there was no way to actually "
                "persist a re-date -- confirmed live, a real session correctly told Todd it could "
                "not claim target dates had changed rather than fabricate a receipt. Use this "
                "instead of just acknowledging the new date in chat. Get the real id from getLoops "
                "first if you don't already have it (format L-YYYY-MM-DD-NNN) -- same id-mixup risk "
                "as closeLoop, never a contact or thread id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "e.g. 'L-2026-07-23-004'"},
                    "target": {"type": "string", "description": "New target date, YYYY-MM-DD."},
                    "note": {"type": "string", "description": "Optional reason, e.g. 'need to connect for a meeting'."},
                },
                "required": ["id", "target"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/loops/redate", "path_params": [], "query_params": [], "has_body": True,
            "body_param_names": ["id", "target", "note"],
        },
    ),
    (
        {
            "type": "function",
            "name": "listBlueSheetAccounts",
            "description": (
                "List every Blue Sheet account dossier (restaurant-brand accounts under "
                "active sales pursuit, e.g. Pollo Campero, Five Guys, Del Taco). Call this "
                "when Todd asks about the status of a restaurant-brand account, deal, or "
                "opportunity that is NOT his own job-search pipeline (that's "
                "getOpportunityPipeline) -- e.g. 'what's going on with Pollo Campero' -- to "
                "find the right account_slug for getAccountStatus, or when he asks what "
                "accounts RB is tracking."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/accounts", "path_params": [], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getAccountStatus",
            "description": (
                "Return the full Blue Sheet dossier for one restaurant-brand account under "
                "ACTIVE engagement -- opportunity stage and key dates, buying influences, "
                "open/overdue actions, technology stack, the latest strategic review. Call "
                "this for 'what's the status of [account]' / 'where do things stand with "
                "[deal]' about an account under active pursuit -- NOT for Todd's own "
                "job-search pipeline, and NOT for pre-engagement research/background "
                "questions about a brand (that's getAccountResearch -- a Blue Sheet is the "
                "plan used DURING an engagement; it does not exist for every brand). Use "
                "listBlueSheetAccounts first if you don't already know the exact "
                "account_slug (e.g. 'pollo-campero')."
            ),
            "parameters": {
                "type": "object",
                "properties": {"account_slug": {"type": "string", "description": "e.g. 'pollo-campero'"}},
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/accounts/{account_slug}", "path_params": ["account_slug"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "listMasterAccountPlans",
            "description": (
                "List every Master Account Plan RB is tracking -- a VENDOR/PARTNER-scoped, "
                "multi-account portfolio plan (e.g. Worldpay's own internal view across 30 "
                "of THEIR customer accounts, organized by Worldpay's own relationship "
                "managers). This is completely different from listBlueSheetAccounts (Todd's "
                "own single-account sales plans) and listAccountResearch (pre-engagement "
                "research on one brand). RB-2026-08-28: built after a real incident where a "
                "document exactly like this -- organized by a vendor's own RMs across many "
                "accounts, with a scored ranked-account table -- was misrouted into "
                "createBlueSheetAccount, producing an empty, falsely-authorized Blue Sheet. "
                "If the user's question or uploaded document is about a vendor's/partner's "
                "OWN portfolio of accounts and their RM structure (not one of Todd's own "
                "target accounts), this is the right tool -- call this first if you don't "
                "know the exact vendor_slug."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/master-account-plans", "path_params": [], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getMasterAccountPlan",
            "description": (
                "Return a Master Account Plan: the vendor's RM roster, ranked portfolio "
                "(framed as 'where should a rep spend their time'), any review items "
                "awaiting Todd's input, and a chat-readable digest. Use "
                "listMasterAccountPlans first if you don't already know the exact "
                "vendor_slug. Not the same as getAccountStatus (a single Blue Sheet "
                "account) or getAccountResearch (pre-engagement research on one brand)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"vendor_slug": {"type": "string", "description": "e.g. 'worldpay'"}},
                "required": ["vendor_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/master-account-plans/{vendor_slug}", "path_params": ["vendor_slug"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "ingestMasterAccountPlanUpload",
            "description": (
                "Programmatic, curated-content path for a Master Account Plan -- NOT for a "
                "real uploaded workbook (use uploadAndIngestFile for that; a real "
                "Ranked-Portfolio+RM-Portfolio-shaped .xlsx is recognized and routed "
                "automatically, no separate call needed). Use this only when Todd gives you "
                "curated ranked-portfolio/RM-roster content directly (not from a real file) "
                "to record for a vendor's Master Account Plan -- e.g. updating Worldpay's "
                "tracked portfolio from something Todd tells you. Requires explicit "
                "authorization because curated-from-scratch content carries the same "
                "fabrication risk createBlueSheetAccount's own guard exists for -- never call "
                "with invented rows."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "vendor_slug": {"type": "string", "description": "e.g. 'worldpay'. Call listMasterAccountPlans first if unsure."},
                    "display_name": {"type": "string", "description": "e.g. 'Worldpay'"},
                    "ranked_portfolio": {"type": "array", "items": {"type": "object"}, "description": "Curated ranked-account rows -- real, sourced content only, never invented."},
                    "rm_portfolios": {"type": "array", "items": {"type": "object"}, "description": "Curated RM roster rows, if any."},
                    "user_authorization_quote": {"type": "string", "description": "REQUIRED. A verbatim quote of what Todd actually said authorizing this curated update -- copy his own words, don't paraphrase."},
                },
                "required": ["vendor_slug", "display_name", "ranked_portfolio", "user_authorization_quote"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/master-account-plans/{vendor_slug}/ingest",
            "path_params": ["vendor_slug"], "query_params": [], "has_body": True,
            "body_param_names": ["display_name", "ranked_portfolio", "rm_portfolios", "user_authorization_quote"],
        },
    ),
    (
        {
            "type": "function",
            "name": "resolveMasterAccountPlanReviewItem",
            "description": (
                "Mark one flagged Master Account Plan review item resolved -- a suggested "
                "Score/Tier change from a new material signal that RB never auto-applies "
                "(that stays a human judgment call, same principle as Blue Sheet buying-"
                "influence ratings). Call this after Todd tells you what he decided about a "
                "pending item shown by getMasterAccountPlan. This does NOT itself change any "
                "score -- if Todd wants the score/tier actually changed, that's a direct edit "
                "he makes, not something this tool does."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "vendor_slug": {"type": "string", "description": "e.g. 'worldpay'"},
                    "evidence_id": {"type": "string", "description": "From the pending_reviews item's evidence_id."},
                    "resolution": {"type": "string", "description": "Short note of what Todd decided, e.g. 'reviewed, no change needed'."},
                },
                "required": ["vendor_slug", "evidence_id", "resolution"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/master-account-plans/{vendor_slug}/review/{evidence_id}/resolve",
            "path_params": ["vendor_slug", "evidence_id"], "query_params": ["resolution"],
            "has_body": False, "body_param_names": [],
        },
    ),
    (
        {
            "type": "function",
            "name": "getMasterAccountPlanDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for a Master Account Plan's actual "
                "xlsx workbook. Call this after getMasterAccountPlan if the user wants to "
                "download/view the real file -- never construct a link yourself from any "
                "file path."
            ),
            "parameters": {
                "type": "object",
                "properties": {"vendor_slug": {"type": "string", "description": "e.g. 'worldpay'"}},
                "required": ["vendor_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "getVendorListDownloadLink",
            "description": (
                "Get a REAL, working downloadable link to an xlsx export of the canonical vendor "
                "list -- every vendor entity tracked in ecosystem_intelligence.json (~88, "
                "2026-09-01, Todd's own request), generated fresh from the real graph on each "
                "download. Never construct a link yourself, and never build/describe a file's "
                "content yourself -- this is the only correct way to answer 'give me a link to "
                "download the vendor list.'"
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "listTechStackCategories",
            "description": (
                "List every real tech-stack category the competitive-landscape analysis covers "
                "(pos, pos_hardware, payments, payments_gateway, loyalty, online_ordering, kiosks, "
                "ai_solution_1, ai_solution_2, drive_thru_timers, ...) -- the exact category names "
                "getCategoryMarketShare accepts. Call this first if you don't know the exact "
                "category name for what the user asked about."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/competitive-landscape/categories", "path_params": [], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getCategoryMarketShare",
            "description": (
                "Market share + real battle cards (positioning, Todd's own POV, sourced vs_genius "
                "advantages on both sides, and a computed brand-count delta) for ONE tech-stack "
                "category, e.g. 'pos', 'drive_thru_timers', 'ai_solution_1'. Call this whenever "
                "asked about market share, competition, or Genius's position in a specific piece "
                "of the tech stack -- call listTechStackCategories first if you don't know the "
                "exact category name. For the full analysis across every category as one "
                "downloadable workbook, call getCompetitiveLandscapeDownloadLink instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {"category": {"type": "string", "description": "e.g. 'pos', 'drive_thru_timers'"}},
                "required": ["category"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/competitive-landscape/category/{category}", "path_params": ["category"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getCompetitiveLandscapeDownloadLink",
            "description": (
                "Get a REAL, working downloadable link to the full competitive tech-stack analysis "
                "workbook (2026-09-01, Todd's own request, modeled on his canonical Restaurant Tech "
                "Coverage workbook) -- 4 tabs: Market Share by Category (every category, ranked by "
                "brand count, real coverage numbers), Battle Cards (real, sourced positioning + a "
                "computed Genius-vs-competitor delta for every category), Brand x Category Grid "
                "(one row per tracked brand, one column per category -- the actual matrix), and "
                "Competitor Research Status (which tracked competitors still need real research, "
                "plus real new-competitor candidates already found in the graph). Generated fresh "
                "from the real graph on each download. Never construct a link yourself, and never "
                "build/describe this analysis yourself -- this is the only correct way to answer "
                "'give me the competitive analysis / market share / battle cards.'"
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "getEcosystemWorkbookDownloadLink",
            "description": (
                "Get a REAL, working downloadable link to the full restaurant-tech graph workbook "
                "(ecosystem_intelligence.json's complete relationship set across every tracked "
                "brand/vendor -- Executive Summary, full tech stack, evidence ledger, macro views), "
                "generated fresh on each download. REQUIRES export_type: use 'internal' (includes "
                "Todd's own strategic notes and confidence rationale) for his own review; use "
                "'shareable' (public evidence only, private notes stripped) ONLY when Todd says he "
                "wants to send/share this outside RB -- never guess, default to 'internal' unless "
                "he says so explicitly. Never construct a link yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "export_type": {
                        "type": "string",
                        "enum": ["internal", "shareable"],
                        "description": "'internal' includes private strategic notes; 'shareable' strips them for external use.",
                    },
                },
                "required": ["export_type"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "listVendors",
            "description": (
                "List every vendor entity tracked in ecosystem_intelligence.json -- the canonical "
                "vendor list (2026-09-01, Todd's own request) -- ALL ~88 restaurant-tech vendors "
                "across the whole tracked brand universe (POS, payments, loyalty, back-office, "
                "kitchen ops, ...), not just the 9 Genius directly competes with (that narrower "
                "list is listCompetitors -- use that instead when asked specifically about Genius's "
                "competition). is_tracked_competitor flags which of these also have a competitor "
                "intelligence profile. For a downloadable file of this list, call "
                "getVendorListDownloadLink instead."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/vendors", "path_params": [], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "listCompetitors",
            "description": (
                "List every competitor RB is tracking intelligence on -- vendors that compete "
                "with Genius (PAR Technology, Toast, Oracle Food & Beverage, NCR Voyix, Qu, Nory, "
                "Restaurant365, Revel Systems, ...). NOT the same as brands: a restaurant BRAND "
                "(McDonald's, Chipotle, ...) is a potential/existing Genius customer, tracked via "
                "listAccountResearch/listBlueSheetAccounts/the macro brand graph instead. If a "
                "watchlisted entity turns out to be a vendor competing with Genius rather than a "
                "restaurant brand, its intelligence belongs here, not in an account_intelligence "
                "brand doc. Call this to find the right competitor_slug for getCompetitorProfile."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/competitors", "path_params": [], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getCompetitorProfile",
            "description": (
                "Return one competitor's full intelligence profile: positioning summary, Todd's "
                "own POV, which Genius product line(s) it directly competes on (pos, payments, "
                "back_office, kitchen_drive_thru, loyalty_engagement, digital_menu_boards, "
                "restaurant_os_platform), gap analysis (where Genius wins vs. where the competitor "
                "wins, each point sourced), and every evidence record on file. Call "
                "listCompetitors first if you don't know the slug."
            ),
            "parameters": {
                "type": "object",
                "properties": {"competitor_slug": {"type": "string", "description": "e.g. 'par-technology'"}},
                "required": ["competitor_slug"],
                "additionalProperties": False,
            },
        },
        {
            "method": "GET", "path": "/competitors/{competitor_slug}",
            "path_params": ["competitor_slug"], "query_params": [], "has_body": False, "body_param_names": [],
        },
    ),
    (
        {
            "type": "function",
            "name": "getSalesOpportunityRadar",
            "description": (
                "Return the latest Radar + Pursuit opportunity-discovery report: account-to-vendor "
                "incumbent exposure, review-first buying-window hypotheses, evidence gaps and "
                "disconfirming questions, checked/no-signal records, and outcome-calibration counts. "
                "These are hypotheses, not confirmed opportunities or canonical buying events."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/sales-opportunity-radar", "path_params": [], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "createCompetitor",
            "description": (
                "Start tracking a new competitor by name -- deliberately cheap and ungated, same "
                "philosophy as generateAccountBackgroundBrief for a new brand (unlike "
                "createBlueSheetAccount, which requires explicit authorization). Matches against "
                "ecosystem_intelligence.json's vendor entities when possible and runs an initial "
                "sync automatically. Only call this for a vendor RB competes against -- if the name "
                "is a restaurant brand instead, use generateAccountBackgroundBrief, not this. Safe "
                "to call again for a name that already exists -- returns already_tracked:true rather "
                "than duplicating or erroring. For adding MANY vendors at once (a trade-show buyers "
                "guide, a vendor roster), use bulkImportCompetitors instead of many calls to this "
                "one -- RB-DEFECT-071 (2026-09-11) confirmed live that many concurrent calls here "
                "corrupted the shared competitor registry."
            ),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "e.g. 'PAR Technology'"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        {"method": "POST", "path": "/competitors", "path_params": [], "query_params": [], "has_body": True, "body_param_names": ["name"]},
    ),
    (
        {
            "type": "function",
            "name": "bulkImportCompetitors",
            "description": (
                "Add many competitors at once -- the right tool for a trade-show vendor roster "
                "(e.g. the FSTEC Buyers Guide) instead of many individual createCompetitor calls. "
                "RB-DEFECT-071 (2026-09-11): 142 concurrent createCompetitor calls from chat "
                "corrupted the shared competitor registry and took listCompetitors down for a real "
                "incident -- this endpoint processes the batch safely instead. Each name comes back "
                "classified: created, already_exists (resolved to a competitor already tracked), "
                "rejected_with_reason (e.g. matches Genius/Global Payments -- RB's own company, "
                "never tracked as its own competitor), or failed (a real per-item error with "
                "safe-retry detail -- one failure never aborts the rest of the batch). Call with "
                "dry_run:true first to preview a large roster (zero writes) before committing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "names": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Real vendor/competitor names to add -- never invented.",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "true = classify every name with zero writes (preview). false (default) = actually create/register/sync each one.",
                    },
                },
                "required": ["names"],
                "additionalProperties": False,
            },
        },
        {"method": "POST", "path": "/competitors/bulk-import", "path_params": [], "query_params": [], "has_body": True, "body_param_names": ["names", "dry_run"]},
    ),
    (
        {
            "type": "function",
            "name": "syncCompetitorIntelligence",
            "description": (
                "Mechanically pull any new evidence for a tracked competitor from sources RB "
                "already gathers -- ecosystem_intelligence.json signals tagged to this vendor, and "
                "account_intelligence/ docs mentioning it by name or alias. Idempotent. This is the "
                "'intelligence gathering process' half of competitor tracking; for something Todd "
                "tells you directly, use addCompetitiveNote instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {"competitor_slug": {"type": "string", "description": "e.g. 'par-technology'"}},
                "required": ["competitor_slug"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/competitors/{competitor_slug}/sync",
            "path_params": ["competitor_slug"], "query_params": [], "has_body": False, "body_param_names": [],
        },
    ),
    (
        {
            "type": "function",
            "name": "addCompetitiveNote",
            "description": (
                "Add one human-supplied competitive intelligence note -- the 'user input' half of "
                "competitor tracking, alongside syncCompetitorIntelligence's automated feed. NEVER "
                "call this with a summary or interpretation you generated yourself; only with what "
                "the user actually told you, verbatim or near-verbatim, or a real source they gave "
                "you. category must be one of: positioning, strength, weakness, pricing, "
                "market_share, reference_customer, customer_win, customer_loss, pov, other."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "competitor_slug": {"type": "string", "description": "e.g. 'par-technology'"},
                    "note": {"type": "string", "description": "The competitive fact/observation itself."},
                    "category": {"type": "string", "description": "positioning | strength | weakness | pricing | market_share | reference_customer | customer_win | customer_loss | pov | other"},
                    "source": {"type": "string", "description": "Who/where this came from. Defaults to Todd Vahlsing."},
                },
                "required": ["competitor_slug", "note"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/competitors/{competitor_slug}/note",
            "path_params": ["competitor_slug"], "query_params": [], "has_body": True,
            "body_param_names": ["note", "category", "source"],
        },
    ),
    (
        {
            "type": "function",
            "name": "setCompetitorProductLines",
            "description": (
                "Declare which Genius product line(s) this competitor directly competes on -- "
                "RB-2026-09-01: this existed in RB's backend but was never callable, so 6 of 9 "
                "tracked competitors had an empty battle card with no way to fill it in. Valid "
                "values: pos, payments, back_office, kitchen_drive_thru, loyalty_engagement, "
                "digital_menu_boards, restaurant_os_platform. Replaces the FULL set every call (not "
                "additive) -- pass the complete current list, not just what's new."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "competitor_slug": {"type": "string", "description": "e.g. 'par-technology'"},
                    "product_lines": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Complete list, e.g. ['pos', 'payments'].",
                    },
                },
                "required": ["competitor_slug", "product_lines"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/competitors/{competitor_slug}/product-lines",
            "path_params": ["competitor_slug"], "query_params": [], "has_body": True,
            "body_param_names": ["product_lines"],
        },
    ),
    (
        {
            "type": "function",
            "name": "addCompetitorGapPoint",
            "description": (
                "Add one real, sourced 'vs. Genius' battle-card point -- RB-2026-09-01: this "
                "existed in RB's backend but was never callable. side='genius' means Genius wins/"
                "leads here; side='competitor' means the competitor does. Only extract what's "
                "actually stated in a real source or what the user actually told you -- never a "
                "claim you inferred, generalized, or summarized yourself. Each point is one "
                "discrete claim, not a paragraph."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "competitor_slug": {"type": "string", "description": "e.g. 'par-technology'"},
                    "side": {"type": "string", "description": "'genius' or 'competitor'"},
                    "point": {"type": "string", "description": "One discrete, sourced claim."},
                    "evidence_id": {"type": "string", "description": "Optional pointer to the real source."},
                },
                "required": ["competitor_slug", "side", "point"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/competitors/{competitor_slug}/gap-points",
            "path_params": ["competitor_slug"], "query_params": [], "has_body": True,
            "body_param_names": ["side", "point", "evidence_id"],
        },
    ),
    (
        {
            "type": "function",
            "name": "setCategoryBattleCard",
            "description": (
                "Create-or-update the RM-facing battle card for one competitor in ONE "
                "tech-stack category (2026-09-01) -- distinct from setCompetitorProductLines/"
                "addCompetitiveNote, which are vendor-level. A competitor can compete "
                "differently across categories (e.g. PAR on platform vs. loyalty each need "
                "their own posture/wedge). Only overwrites fields you actually pass -- omit a "
                "field to leave it unchanged. Never call this with a summary or interpretation "
                "you generated yourself -- only with what the user actually told you or a real "
                "source they gave you. Call listTechStackCategories for real category names, "
                "plus 'platform'/'delivery_aggregation' (RM-only, no market-share data source "
                "yet). status must be one of: draft, needs_research, source_backed, "
                "todd_review_needed, todd_validated, stale, contradicted, do_not_use."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "competitor_slug": {"type": "string", "description": "e.g. 'par-technology'"},
                    "category": {"type": "string", "description": "e.g. 'pos', 'platform', 'loyalty'"},
                    "status": {"type": "string", "description": "One of the 8 governed status values."},
                    "confidence_pct": {"type": "integer", "description": "0-100."},
                    "rm_plain_english_posture": {"type": "string", "description": "The RM talk track for this category."},
                    "when_to_bring_todd_in": {"type": "string", "description": "RM escalation cue for this category."},
                    "evidence_id": {"type": "string", "description": "Optional pointer to the real source backing this update."},
                },
                "required": ["competitor_slug", "category"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/competitors/{competitor_slug}/battle-card/{category}",
            "path_params": ["competitor_slug", "category"], "query_params": [], "has_body": True,
            "body_param_names": ["status", "confidence_pct", "rm_plain_english_posture", "when_to_bring_todd_in", "evidence_id"],
        },
    ),
    (
        {
            "type": "function",
            "name": "addCategoryBattleCardPoint",
            "description": (
                "Append one real, sourced point to a category battle card's listen_for / "
                "discovery_questions / red_flags list (2026-09-01). The card for this "
                "(competitor_slug, category) pair must already exist -- call "
                "setCategoryBattleCard first if it doesn't. Only extract what's actually "
                "stated in a real source or what the user actually told you -- never a claim "
                "you inferred, generalized, or summarized yourself. One discrete point per "
                "call, not a paragraph."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "competitor_slug": {"type": "string", "description": "e.g. 'par-technology'"},
                    "category": {"type": "string", "description": "e.g. 'pos', 'platform'"},
                    "field": {"type": "string", "description": "'listen_for', 'discovery_questions', or 'red_flags'."},
                    "point": {"type": "string", "description": "One discrete, sourced point."},
                    "evidence_id": {"type": "string", "description": "Optional pointer to the real source."},
                },
                "required": ["competitor_slug", "category", "field", "point"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/competitors/{competitor_slug}/battle-card/{category}/points",
            "path_params": ["competitor_slug", "category"], "query_params": [], "has_body": True,
            "body_param_names": ["field", "point", "evidence_id"],
        },
    ),
    (
        {
            "type": "function",
            "name": "resolveCompetitorReviewItem",
            "description": (
                "Mark one flagged Competitor Intelligence review item resolved -- a new "
                "material signal or a stale battle card flagged by the daily competitor "
                "review scan (2026-09-01), surfaced via getCompetitorProfile's "
                "pending_reviews or the daily brief's Competitor Battle Card Review section. "
                "Call this after Todd tells you what he decided. This does NOT itself change "
                "the underlying battle card -- if Todd wants the content actually updated, "
                "call setCategoryBattleCard separately; that stays a direct edit, same "
                "principle as resolveMasterAccountPlanReviewItem."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "competitor_slug": {"type": "string", "description": "e.g. 'par-technology'"},
                    "review_id": {"type": "string", "description": "From the pending_reviews item's review_id."},
                    "resolution": {"type": "string", "description": "Short note of what Todd decided, e.g. 'reviewed, no change needed'."},
                },
                "required": ["competitor_slug", "review_id", "resolution"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/competitors/{competitor_slug}/review/{review_id}/resolve",
            "path_params": ["competitor_slug", "review_id"], "query_params": ["resolution"],
            "has_body": False, "body_param_names": [],
        },
    ),
    (
        {
            "type": "function",
            "name": "addBlueSheetEvidence",
            "description": (
                "Add one real, structured evidence record to an existing Blue Sheet -- the "
                "reviewed, one-call counterpart to uploadAndIngestFile's automatic reference-"
                "linking (which only appends a fact-free 'this document mentions this account' "
                "pointer, never a specific claim). Use this when a real source (a call "
                "transcript, an email, meeting notes, something the user told you directly) "
                "contains specific commercial terms, decisions, or participants relevant to an "
                "account with an active Blue Sheet -- e.g. a pricing structure discussed on a "
                "call, a fee the customer pushed back on, a named stakeholder who needs a "
                "follow-up meeting. ONLY call this with evidence and claims actually present in "
                "a real source you were given or that the user actually told you -- never with "
                "your own summary, inference, or generalization presented as fact. If unsure "
                "whether a specific claim is real vs. your own interpretation, leave "
                "extracted_claims narrower rather than broader."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_slug": {"type": "string", "description": "e.g. 'pollo-campero'. Call listBlueSheetAccounts if unsure."},
                    "excerpt": {"type": "string", "description": "The real evidence text -- verbatim or a close paraphrase of what was actually said/written."},
                    "source_type": {"type": "string", "description": "e.g. 'internal_pricing_call_transcript', 'meeting_notes', 'email'."},
                    "extracted_claims": {"type": "array", "items": {"type": "string"}, "description": "Specific factual claims this evidence supports, each verbatim-grounded. Never invent a claim not actually present in the source."},
                    "event_date": {"type": "string", "description": "YYYY-MM-DD the underlying event/call/email actually happened. Defaults to today."},
                    "participants": {"type": "array", "items": {"type": "string"}, "description": "Real named people actually present/party to this evidence, if known."},
                    "opportunity_ids": {"type": "array", "items": {"type": "string"}, "description": "Real opportunity_id(s) this relates to, if known."},
                    "source_author": {"type": "string", "description": "Who authored/reported this evidence, if known."},
                    "confidence": {"type": "string", "description": "high | medium | low"},
                    "scope": {"type": "string", "description": "e.g. 'opportunity:opp-...' -- defaults to account-level scope."},
                    "evidence_class": {"type": "string", "description": "e.g. 'seller_authored_meeting_recap', 'user_reported', 'internal_pricing_call_transcript'."},
                    "limitations": {"type": "string", "description": "Any caveat on how much this evidence can be trusted/generalized, if relevant."},
                },
                "required": ["account_slug", "excerpt", "source_type"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/blue-sheets/{account_slug}/evidence",
            "path_params": ["account_slug"], "query_params": [], "has_body": True,
            "body_param_names": [
                "excerpt", "source_type", "extracted_claims", "event_date", "participants",
                "opportunity_ids", "source_author", "confidence", "scope", "evidence_class",
                "limitations",
            ],
        },
    ),
    (
        {
            "type": "function", "name": "listDownstreamIntelligenceImpacts",
            "description": "List downstream artifact work created from evidence-backed intelligence ramifications, including pending review and execution state.",
            "parameters": {"type": "object", "properties": {
                "status": {"type": "string", "description": "Defaults to pending_review; omit or pass an empty value only when all states are needed."}},
                "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/intelligence/downstream-impacts", "path_params": [],
         "query_params": ["status"], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function", "name": "resolveDownstreamIntelligenceImpact",
            "description": "Approve a judgment-heavy downstream recommendation for separate manual action, or reject it. Approval records the decision but does not silently edit the artifact.",
            "parameters": {"type": "object", "properties": {
                "impact_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["approve_manual_action", "reject"]},
                "resolution": {"type": "string"}},
                "required": ["impact_id", "decision", "resolution"], "additionalProperties": False},
        },
        {"method": "POST", "path": "/intelligence/downstream-impacts/{impact_id}/resolve",
         "path_params": ["impact_id"], "query_params": [], "has_body": True,
         "body_param_names": ["decision", "resolution"]},
    ),
    (
        {
            "type": "function", "name": "listRoutineResearchReviews",
            "description": "List sourced routine-research evidence awaiting Todd's review. These are proposals, not canonical field changes.",
            "parameters": {"type": "object", "properties": {
                "status": {"type": "string", "description": "Defaults to pending_review."}},
                "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/routine-research/reviews", "path_params": [],
         "query_params": ["status"], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function", "name": "resolveRoutineResearchReview",
            "description": "Approve one sourced routine-research item into the account's canonical evidence ledger, or reject it. Approval records a mutation receipt and flags the background brief for regeneration; it does not silently rewrite a structured field.",
            "parameters": {"type": "object", "properties": {
                "evidence_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["approve_evidence", "reject"]},
                "resolution": {"type": "string", "description": "Todd's actual decision or rationale."}},
                "required": ["evidence_id", "decision", "resolution"], "additionalProperties": False},
        },
        {"method": "POST", "path": "/routine-research/reviews/{evidence_id}/resolve",
         "path_params": ["evidence_id"], "query_params": [], "has_body": True,
         "body_param_names": ["decision", "resolution"]},
    ),
    (
        {
            "type": "function", "name": "listTeamProfileSubmissions",
            "description": "List Team Portal-submitted corrections to brand/competitor profiles awaiting Todd's review. Defaults to the pending queue; pass status='confirmed'/'rejected'/'all' to see history. Nothing here is live until resolveTeamProfileSubmission confirms it.",
            "parameters": {"type": "object", "properties": {
                "status": {"type": "string", "description": "Defaults to 'pending'."},
                "target_type": {"type": "string", "enum": ["brand", "competitor"], "description": "Optional filter."}},
                "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/team-profile-submissions", "path_params": [],
         "query_params": ["status", "target_type"], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function", "name": "resolveTeamProfileSubmission",
            "description": "Confirm applies a teammate's proposed brand/competitor profile correction to the live record (stamped as Todd-reviewed); reject leaves the live record untouched. Either way the submission stays in the queue for audit.",
            "parameters": {"type": "object", "properties": {
                "submission_id": {"type": "string"},
                "decision": {"type": "string", "enum": ["confirm", "reject"]},
                "review_note": {"type": "string", "description": "Optional note on why."}},
                "required": ["submission_id", "decision"], "additionalProperties": False},
        },
        {"method": "POST", "path": "/team-profile-submissions/{submission_id}/resolve",
         "path_params": ["submission_id"], "query_params": [], "has_body": True,
         "body_param_names": ["decision", "review_note"]},
    ),
    (
        {
            "type": "function",
            "name": "listAccountResearch",
            "description": (
                "List every Account Background Brief / pre-engagement research record RB "
                "has started on a brand. NOT the same thing as listBlueSheetAccounts -- a "
                "Blue Sheet is the plan used DURING an active engagement; an Account "
                "Background Brief is upstream of that, built from public information and "
                "RBB's own research BEFORE discovery starts, and can exist for a brand with "
                "no Blue Sheet at all. Call this to find the right account_slug for "
                "getAccountResearch, or when asked what brands RB has researched."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/account-research", "path_params": [], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getAccountResearch",
            "description": (
                "Return the full pre-engagement Account Research record for one brand -- "
                "brand profile, leadership, technology environment, opportunity hypotheses "
                "(unconfirmed potential fits), open discovery questions, and the latest "
                "registered Background Brief version if one exists (with the actual "
                "markdown text). Call this for 'what do we know about [brand]' / 'give me "
                "the [brand] background brief' when you just want to see the existing one, "
                "not regenerate it. RULE-0-style discipline: display "
                "latest_background_brief.markdown verbatim rather than re-summarizing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"}},
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/account-research/{account_slug}", "path_params": ["account_slug"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "generateAccountBackgroundBrief",
            "description": (
                "Generate (or regenerate) the canonical Account Background Brief document "
                "for one brand -- a PRE-ENGAGEMENT document (public information + RBB's own "
                "already-persisted account research), rendered from RBB's persisted "
                "intelligence only -- never re-researched from scratch by you, never "
                "invented. Registers a new version; the prior version is preserved, never "
                "deleted. Call this for 'give me the [brand] background brief' / 'build a "
                "background brief for [brand]' / 'give [name] an updated [brand] brief'. "
                "Works for a brand-new brand with no existing record -- creates an empty "
                "one first (every field explicitly 'unknown', real Discovery Questions "
                "instead of guessed content); this is deliberately cheap to start, unlike a "
                "Blue Sheet. Automatically pulls in and embeds any of Todd's own real, "
                "already-authored account research from system/account_intelligence/ that "
                "matches the brand (strategic account plans, executive summaries, call "
                "intelligence) -- this is real prior work, not something you need to ask for "
                "separately or re-create. account_slug may be a known slug or a brand name -- resolved "
                "either way. Do NOT answer a background-brief request from your own general "
                "knowledge instead of calling this -- an empty, honestly-labeled brief is "
                "correct behavior for a brand RBB knows little about; inventing company or "
                "personal facts to fill it in is not. This is for ONE brand. If the user's "
                "request or an uploaded document is organized by a vendor's own relationship "
                "managers across MANY accounts (a scored ranked-account table, an RM roster) "
                "-- that is a Master Account Plan, not a background brief on one brand. Use "
                "listMasterAccountPlans/getMasterAccountPlan instead; do not call this."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_slug": {"type": "string", "description": "e.g. 'cafe-rio' or \"McDonald's\""},
                    "generated_for": {"type": "string", "description": "Who the brief is being prepared for, e.g. a person's name. Optional."},
                },
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/account-research/{account_slug}/background-brief",
            "path_params": ["account_slug"], "query_params": ["generated_for"],
            "has_body": False, "body_param_names": [],
        },
    ),
    (
        {
            "type": "function",
            "name": "createAccountPlan",
            "description": (
                "Creates (or regenerates) the Account Plan for one account -- the top-of-"
                "funnel, post-discovery planning document that sits between the Background "
                "Brief (pure pre-engagement research) and the Blue Sheet (active engagement). "
                "RB-2026-09-07: formalizes the qualification scorecard, discovery status, and "
                "buying influences already on file, plus records the user's own account "
                "strategy and go/no-go call -- it does not invent or re-derive any of that "
                "underlying data, only the strategy narrative and go/no-go call are new "
                "judgment content. Only call this when the user has explicitly asked for a "
                "new or updated Account Plan for a specific account -- creating one is a real "
                "'we are now formally planning to pursue this account' commitment, same "
                "authorization discipline as createBlueSheetAccount. Requires the account to "
                "already exist -- call generateAccountBackgroundBrief first if it doesn't."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_slug": {"type": "string", "description": "e.g. 'cafe-rio' or \"McDonald's\""},
                    "account_strategy": {"type": "string", "description": "Real account strategy narrative -- the user's own judgment on how to approach this specific account. Never a generic template."},
                    "go_no_go": {"type": "string", "enum": ["go", "no-go", "pending"], "description": "The qualification call for this account right now."},
                    "generated_for": {"type": "string", "description": "Who this plan is being prepared for, if relevant. Optional."},
                    "user_authorization_quote": {
                        "type": "string",
                        "description": (
                            "REQUIRED. A verbatim quote of what the user actually said "
                            "authorizing this specific Account Plan creation -- not a "
                            "paraphrase. Copy their own words from this conversation."
                        ),
                    },
                },
                "required": ["account_slug", "account_strategy", "go_no_go", "user_authorization_quote"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/customers-prospects/{account_slug}/account-plan",
            "path_params": ["account_slug"], "query_params": [],
            "has_body": True, "body_param_names": ["account_strategy", "go_no_go", "generated_for", "user_authorization_quote"],
        },
    ),
    (
        {
            "type": "function",
            "name": "getAccountPlan",
            "description": (
                "Return the current Account Plan for one account, verbatim. Call this for "
                "'what's the account plan for [brand]' / 'show me the [brand] account plan' "
                "when you just want to see the existing one, not regenerate it. RULE-0-style "
                "discipline: display the markdown verbatim rather than re-summarizing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"}},
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/customers-prospects/{account_slug}/account-plan", "path_params": ["account_slug"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getAccountPlanDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for an Account Plan -- the only "
                "correct way to answer 'give me a link to download/view the account plan for "
                "[brand]'. Distinct from getBriefDownloadLink (Background Brief) and "
                "getBlueSheetDownloadLink (Blue Sheet xlsx) -- a different document each time; "
                "using the wrong one produces a link to the wrong file. Confirms a real "
                "Account Plan actually exists first, then returns the real URL to relay "
                "verbatim. NEVER construct a link yourself from a file path."
            ),
            "parameters": {
                "type": "object",
                "properties": {"account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"}},
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "createGreenSheet",
            "description": (
                "Creates (or regenerates) a Green Sheet -- a single-call prep document "
                "scoped to named attendees, distinct from the account-wide Account Plan and "
                "the full-engagement Blue Sheet. RB-2026-09-07: shows only the buying-"
                "influence records and prior evidence relevant to the named attendees (not "
                "the whole account roster/history), plus this call's own real objective. "
                "Deliberately UNGATED (no user_authorization_quote needed, unlike "
                "createAccountPlan/createBlueSheetAccount) -- cheap to create before any "
                "call, safe to regenerate freely, never mutates account.json. Call this when "
                "the user is prepping for a specific upcoming call ('help me prep for my call "
                "with [name]', 'build a green sheet for the [date] call with [attendees]'). "
                "Requires the account to already exist."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"},
                    "call_purpose": {"type": "string", "description": "This specific call's real objective -- never a generic template."},
                    "attendees": {"type": "array", "items": {"type": "string"}, "description": "Real names of people on this call."},
                    "talking_points": {"type": "string", "description": "Optional real talking points for this call."},
                    "generated_for": {"type": "string", "description": "Who this is being prepared for, if relevant. Optional."},
                    "buying_influence_concepts": {
                        "type": "array",
                        "description": "Optional. Each: {name, concept} -- what this Buying Influence is trying to Accomplish, Fix, or Avoid on this call. name is matched loosely against attendees/on-file buying influences. Merges into the On This Call table as a Concept column; call-specific, never persisted to account.json.",
                        "items": {"type": "object", "properties": {"name": {"type": "string"}, "concept": {"type": "string"}}},
                    },
                    "valid_business_reason": {"type": "string", "description": "Optional. This meeting's purpose, from the Buying Influence's own point of view."},
                    "credibility_if_established": {"type": "string", "description": "Optional. If you already have credibility with this Buying Influence, how you'll check or enhance it this call."},
                    "credibility_if_not_established": {"type": "string", "description": "Optional. If you don't yet have credibility, how you'll establish it this call."},
                    "perspective_to_share": {"type": "string", "description": "Optional. The perspective or insight you plan to share this meeting."},
                    "unique_strengths": {
                        "type": "array",
                        "description": "Optional. Each: {so_what, prove_it} -- a unique strength relevant to this meeting and its proof point.",
                        "items": {"type": "object", "properties": {"so_what": {"type": "string"}, "prove_it": {"type": "string"}}},
                    },
                    "action_commitment_best": {"type": "string", "description": "Optional. The best realistic action this Buying Influence could commit to as a result of this call."},
                    "action_commitment_minimum": {"type": "string", "description": "Optional. The minimum acceptable action commitment for this call to be worth having."},
                    "basic_issues": {"type": "array", "items": {"type": "string"}, "description": "Optional. Free-form basic issues / personal-win concerns to keep in view for this Buying Influence."},
                },
                "required": ["account_slug", "call_purpose", "attendees"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/customers-prospects/{account_slug}/green-sheet",
            "path_params": ["account_slug"], "query_params": [],
            "has_body": True, "body_param_names": [
                "call_purpose", "attendees", "talking_points", "generated_for",
                "buying_influence_concepts", "valid_business_reason",
                "credibility_if_established", "credibility_if_not_established",
                "perspective_to_share", "unique_strengths",
                "action_commitment_best", "action_commitment_minimum", "basic_issues",
            ],
        },
    ),
    (
        {
            "type": "function",
            "name": "getGreenSheet",
            "description": (
                "Return the current Green Sheet for one account, verbatim. Call this for "
                "'what's the green sheet/call plan for [brand]' when you just want to see the "
                "existing one, not regenerate it. RULE-0-style discipline: display the "
                "markdown verbatim rather than re-summarizing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"}},
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/customers-prospects/{account_slug}/green-sheet", "path_params": ["account_slug"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getGreenSheetDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for a Green Sheet -- the only correct "
                "way to answer 'give me a link to download/view the green sheet/call plan for "
                "[brand]'. Distinct from getAccountPlanDownloadLink/getBriefDownloadLink/"
                "getBlueSheetDownloadLink -- a different document each time; using the wrong "
                "one produces a link to the wrong file. Confirms a real Green Sheet actually "
                "exists first, then returns the real URL to relay verbatim. NEVER construct a "
                "link yourself from a file path."
            ),
            "parameters": {
                "type": "object",
                "properties": {"account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"}},
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "createWinPlan",
            "description": (
                "Creates (or regenerates) the Win Plan for one account -- the closing-"
                "strategy document that pulls together qualification status, buying-influence "
                "ratings, and competitive position into one plan for how this specific "
                "opportunity actually gets won. RB-2026-09-07. Same authorization discipline "
                "as createAccountPlan/createBlueSheetAccount: only call this when the user has "
                "explicitly asked for a new or updated Win Plan by name, with a real "
                "user_authorization_quote (verbatim, not paraphrased) -- creating one is a "
                "real commitment to a closing strategy. The account must already exist. "
                "close_plan must be real, specific text -- never a generic template."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"},
                    "close_plan": {"type": "string", "description": "Real, specific closing strategy for this opportunity."},
                    "generated_for": {"type": "string", "description": "Who this is being prepared for, if relevant. Optional."},
                    "user_authorization_quote": {
                        "type": "string",
                        "description": (
                            "REQUIRED. A verbatim quote of what the user actually said "
                            "authorizing this specific Win Plan creation -- not a paraphrase. "
                            "Copy their own words from this conversation."
                        ),
                    },
                },
                "required": ["account_slug", "close_plan", "user_authorization_quote"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/customers-prospects/{account_slug}/win-plan",
            "path_params": ["account_slug"], "query_params": [],
            "has_body": True, "body_param_names": ["close_plan", "generated_for", "user_authorization_quote"],
        },
    ),
    (
        {
            "type": "function",
            "name": "getWinPlan",
            "description": (
                "Return the current Win Plan for one account, verbatim. Call this for "
                "'what's the win plan/closing strategy for [brand]' when you just want to see "
                "the existing one, not regenerate it. RULE-0-style discipline: display the "
                "markdown verbatim rather than re-summarizing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"}},
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/customers-prospects/{account_slug}/win-plan", "path_params": ["account_slug"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getWinPlanDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for a Win Plan -- the only correct way "
                "to answer 'give me a link to download/view the win plan/closing strategy for "
                "[brand]'. Distinct from getAccountPlanDownloadLink/getGreenSheetDownloadLink/"
                "getBriefDownloadLink/getBlueSheetDownloadLink -- a different document each "
                "time; using the wrong one produces a link to the wrong file. Confirms a real "
                "Win Plan actually exists first, then returns the real URL to relay verbatim. "
                "NEVER construct a link yourself from a file path."
            ),
            "parameters": {
                "type": "object",
                "properties": {"account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"}},
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "createWinLossReview",
            "description": (
                "Creates (or regenerates) the Win/Loss Review for one specific opportunity at "
                "one account -- closes the loop after a deal decision: deal summary, decision "
                "criteria, competitive dynamics, what worked, what to change, the root cause/key "
                "driver, and action items for the playbook. RB-2026-09-28. Unlike every other "
                "customer-side artifact, this is keyed by BOTH account_slug and "
                "opportunity_slug -- one account can have many Win/Loss Reviews over time, one "
                "per opportunity; opportunity_slug is a short kebab-case identifier you choose "
                "(e.g. 'pos-refresh-2026-09'), not something looked up. Same authorization "
                "discipline as createAccountPlan/createWinPlan: only call this when the user has "
                "explicitly asked for a new or updated Win/Loss Review for a named opportunity, "
                "with a real user_authorization_quote (verbatim, not paraphrased). The account "
                "must already exist. root_cause_or_key_driver must be real, specific text -- "
                "'price' or 'relationship' alone isn't enough -- because it drives a real "
                "structured gap point on the primary named competitor's profile."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"},
                    "opportunity_slug": {"type": "string", "description": "Short kebab-case identifier for this opportunity, e.g. 'pos-refresh-2026-09'."},
                    "outcome": {"type": "string", "enum": ["win", "loss"]},
                    "deal_size": {"type": "string", "description": "e.g. '$250k ARR'. Optional."},
                    "close_date": {"type": "string", "description": "Real close date. Optional."},
                    "decision_criteria": {"type": "string", "description": "What the customer said mattered most, in their words if possible, and how it compared to the assumed criteria at gold sheet/green sheet stage."},
                    "competitive_dynamics": {"type": "string", "description": "Who we competed against and how they were positioned, where the battle card held up or didn't, pricing dynamics."},
                    "what_we_did_well": {"type": "string", "description": "Specific actions, messaging, or relationships that worked."},
                    "what_we_would_change": {"type": "string", "description": "Specific missteps, timing issues, or coverage gaps."},
                    "root_cause_or_key_driver": {"type": "string", "description": "The single biggest factor behind the outcome -- specific, not generic."},
                    "action_items": {
                        "type": "array",
                        "description": "Optional. Each: {action, owner, due_date, where_it_updates}.",
                        "items": {"type": "object", "properties": {
                            "action": {"type": "string"}, "owner": {"type": "string"},
                            "due_date": {"type": "string"}, "where_it_updates": {"type": "string"},
                        }},
                    },
                    "customer_quote": {"type": "string", "description": "Only used when outcome='win'. Optional."},
                    "competitors_in_deal": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Competitor slugs named in this deal. The FIRST slug is treated as primary and gets root_cause_or_key_driver promoted to a structured gap point; every named competitor gets the full competitive_dynamics text logged as firsthand evidence.",
                    },
                    "generated_for": {"type": "string", "description": "Who this is being prepared for, if relevant. Optional."},
                    "user_authorization_quote": {
                        "type": "string",
                        "description": (
                            "REQUIRED. A verbatim quote of what the user actually said "
                            "authorizing this specific Win/Loss Review creation -- not a "
                            "paraphrase. Copy their own words from this conversation."
                        ),
                    },
                },
                "required": ["account_slug", "opportunity_slug", "outcome", "decision_criteria", "root_cause_or_key_driver", "user_authorization_quote"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/customers-prospects/{account_slug}/win-loss-reviews/{opportunity_slug}",
            "path_params": ["account_slug", "opportunity_slug"], "query_params": [],
            "has_body": True, "body_param_names": [
                "outcome", "deal_size", "close_date", "decision_criteria", "competitive_dynamics",
                "what_we_did_well", "what_we_would_change", "root_cause_or_key_driver",
                "action_items", "customer_quote", "competitors_in_deal", "generated_for",
                "user_authorization_quote",
            ],
        },
    ),
    (
        {
            "type": "function",
            "name": "getWinLossReview",
            "description": (
                "Return one specific Win/Loss Review, verbatim. Call this for 'what happened "
                "on the [opportunity] deal at [account]' when you already know the opportunity "
                "slug. Use listWinLossReviews first if you only know the account, not the "
                "specific opportunity_slug. RULE-0-style discipline: display the markdown "
                "verbatim rather than re-summarizing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"},
                    "opportunity_slug": {"type": "string", "description": "e.g. 'pos-refresh-2026-09'"},
                },
                "required": ["account_slug", "opportunity_slug"],
                "additionalProperties": False,
            },
        },
        {
            "method": "GET", "path": "/customers-prospects/{account_slug}/win-loss-reviews/{opportunity_slug}",
            "path_params": ["account_slug", "opportunity_slug"], "query_params": [], "has_body": False, "body_param_names": [],
        },
    ),
    (
        {
            "type": "function",
            "name": "listWinLossReviews",
            "description": (
                "List every Win/Loss Review on file for one account -- opportunity slug, "
                "outcome, deal size, close date, and named competitors for each. Call this for "
                "'what have we learned from past deals at [account]' or 'how have we done "
                "against [competitor]' style questions, before calling getWinLossReview on a "
                "specific one. Honest-blank: an empty reviews list means none exist yet, not an "
                "error."
            ),
            "parameters": {
                "type": "object",
                "properties": {"account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"}},
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {
            "method": "GET", "path": "/customers-prospects/{account_slug}/win-loss-reviews",
            "path_params": ["account_slug"], "query_params": [], "has_body": False, "body_param_names": [],
        },
    ),
    (
        {
            "type": "function",
            "name": "getWinLossReviewDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for one specific Win/Loss Review -- the "
                "only correct way to answer 'give me a link to download/view the win/loss "
                "review for [opportunity] at [account]'. Distinct from every other "
                "*DownloadLink tool -- a different document each time; using the wrong one "
                "produces a link to the wrong file. Confirms the review actually exists first, "
                "then returns the real URL to relay verbatim. NEVER construct a link yourself "
                "from a file path."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"},
                    "opportunity_slug": {"type": "string", "description": "e.g. 'pos-refresh-2026-09'"},
                },
                "required": ["account_slug", "opportunity_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "createRfpResponsePlan",
            "description": (
                "Creates (or regenerates) the RFP Response Plan for one account -- formal RFP "
                "response tracking: a deadline, prioritized open loops still needed before "
                "submission, customer clarification questions owed back, and a fixed "
                "submission-gate checklist (Todd's own real methodology). RB-2026-09-07. Same "
                "authorization discipline as createAccountPlan/createWinPlan/"
                "createBlueSheetAccount: only call this when the user has explicitly asked for "
                "a new or updated RFP Response Plan by name, with a real user_authorization_"
                "quote (verbatim, not paraphrased) -- an RFP response is customer-facing and "
                "commitment-bearing. open_loops and clarification_questions must be real, "
                "sourced content actually in the RFP or actually owed to the customer -- NEVER "
                "invented or templated. The account must already exist."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"},
                    "deadline": {"type": "string", "description": "The real RFP response deadline, e.g. 'Friday, 2026-09-04'."},
                    "open_loops": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "priority": {"type": "string", "enum": ["P0", "P1", "P2"]},
                                "loop": {"type": "string"},
                                "owner": {"type": "string"},
                                "completion_evidence": {"type": "string"},
                                "status": {"type": "string"},
                            },
                        },
                        "description": "Real open items still needed before the response can be submitted. Never invented.",
                    },
                    "clarification_questions": {"type": "array", "items": {"type": "string"}, "description": "Real questions actually owed back to the customer."},
                    "generated_for": {"type": "string", "description": "Who this is being prepared for, if relevant. Optional."},
                    "user_authorization_quote": {
                        "type": "string",
                        "description": (
                            "REQUIRED. A verbatim quote of what the user actually said "
                            "authorizing this specific RFP Response Plan creation -- not a "
                            "paraphrase. Copy their own words from this conversation."
                        ),
                    },
                },
                "required": ["account_slug", "deadline", "open_loops", "clarification_questions", "user_authorization_quote"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/customers-prospects/{account_slug}/rfp-response-plan",
            "path_params": ["account_slug"], "query_params": [],
            "has_body": True, "body_param_names": ["deadline", "open_loops", "clarification_questions", "generated_for", "user_authorization_quote"],
        },
    ),
    (
        {
            "type": "function",
            "name": "getRfpResponsePlan",
            "description": (
                "Return the current RFP Response Plan for one account, verbatim. Call this for "
                "'what's the RFP response plan for [brand]' when you just want to see the "
                "existing one, not regenerate it. RULE-0-style discipline: display the markdown "
                "verbatim rather than re-summarizing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"}},
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/customers-prospects/{account_slug}/rfp-response-plan", "path_params": ["account_slug"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getRfpResponsePlanDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for an RFP Response Plan -- the only "
                "correct way to answer 'give me a link to download/view the RFP response plan "
                "for [brand]'. Distinct from getWinPlanDownloadLink/getAccountPlanDownloadLink/"
                "getGreenSheetDownloadLink/getBriefDownloadLink/getBlueSheetDownloadLink -- a "
                "different document each time; using the wrong one produces a link to the "
                "wrong file. Confirms a real RFP Response Plan actually exists first, then "
                "returns the real URL to relay verbatim. NEVER construct a link yourself from "
                "a file path."
            ),
            "parameters": {
                "type": "object",
                "properties": {"account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"}},
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "createBattleCard",
            "description": (
                "Persists a versioned Battle Card document for one tech-stack category -- a "
                "markdown rendering of getCategoryMarketShare's own computed content (Genius's "
                "share, up to 5 competitors with real positioning/Todd's POV/advantages/"
                "financial health). RB-2026-09-07, competitive-side. Fully computed from "
                "already-persisted data -- unlike createAccountPlan/createWinPlan/"
                "createRfpResponsePlan, no user_authorization_quote is required; freely "
                "regenerable. Call listTechStackCategories first if you don't know the exact "
                "category name. Use this when the user asks to save/persist/generate a battle "
                "card for a category -- for a fresh computed view without persisting, use "
                "getCategoryMarketShare instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "e.g. 'pos', 'payments_gateway'. Call listTechStackCategories first if unsure."},
                    "generated_for": {"type": "string", "description": "Who this is being prepared for, if relevant. Optional."},
                },
                "required": ["category"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/battle-cards/{category}",
            "path_params": ["category"], "query_params": [],
            "has_body": True, "body_param_names": ["generated_for"],
        },
    ),
    (
        {
            "type": "function",
            "name": "getBattleCard",
            "description": (
                "Return the current persisted Battle Card for one tech-stack category, "
                "verbatim. Call this for 'what's the battle card for [category]' when you just "
                "want to see the existing one, not regenerate it. RULE-0-style discipline: "
                "display the markdown verbatim rather than re-summarizing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"category": {"type": "string", "description": "e.g. 'pos', 'payments_gateway'."}},
                "required": ["category"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/battle-cards/{category}", "path_params": ["category"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getBattleCardDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for a persisted Battle Card -- the only "
                "correct way to answer 'give me a link to download/view the battle card for "
                "[category]'. Distinct from getCompetitiveBriefDownloadLink (competitor-scoped, "
                "not category-scoped) -- using the wrong one produces a link to the wrong file. "
                "Confirms a real Battle Card actually exists first, then returns the real URL "
                "to relay verbatim. NEVER construct a link yourself from a file path."
            ),
            "parameters": {
                "type": "object",
                "properties": {"category": {"type": "string", "description": "e.g. 'pos', 'payments_gateway'."}},
                "required": ["category"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "createCompetitiveBrief",
            "description": (
                "Persists a versioned Competitive Brief document for one competitor -- a "
                "markdown rendering of getCompetitorProfile's own computed content "
                "(positioning, Todd's POV, gap analysis, category battle cards, evidence "
                "log). RB-2026-09-07, competitive-side. Fully computed from already-"
                "persisted data -- no user_authorization_quote is required; freely "
                "regenerable. RB-2026-09-25: auto-creates a mostly-empty competitor shell "
                "if competitor_slug doesn't match one yet -- prefer listCompetitors/"
                "createCompetitor first when you have a real name to resolve against, since "
                "that seeds real aliases/category data this fallback can't; only rely on the "
                "auto-create when you already have a slug you're confident is right. Use "
                "this when the user asks to save/persist/generate a competitive brief for a "
                "competitor -- for a fresh computed view without persisting, use "
                "getCompetitorProfile instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "competitor_slug": {"type": "string", "description": "e.g. 'par-technology'. Call listCompetitors first if unsure."},
                    "generated_for": {"type": "string", "description": "Who this is being prepared for, if relevant. Optional."},
                },
                "required": ["competitor_slug"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/competitive-briefs/{competitor_slug}",
            "path_params": ["competitor_slug"], "query_params": [],
            "has_body": True, "body_param_names": ["generated_for"],
        },
    ),
    (
        {
            "type": "function",
            "name": "getCompetitiveBrief",
            "description": (
                "Return the current persisted Competitive Brief for one competitor, verbatim. "
                "Call this for 'what's the competitive brief for [competitor]' when you just "
                "want to see the existing one, not regenerate it. RULE-0-style discipline: "
                "display the markdown verbatim rather than re-summarizing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"competitor_slug": {"type": "string", "description": "e.g. 'par-technology'."}},
                "required": ["competitor_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/competitive-briefs/{competitor_slug}", "path_params": ["competitor_slug"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getCompetitiveBriefDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for a persisted Competitive Brief -- "
                "the only correct way to answer 'give me a link to download/view the "
                "competitive brief for [competitor]'. Distinct from getBattleCardDownloadLink "
                "(category-scoped, not competitor-scoped) -- using the wrong one produces a "
                "link to the wrong file. Confirms a real Competitive Brief actually exists "
                "first, then returns the real URL to relay verbatim. NEVER construct a link "
                "yourself from a file path."
            ),
            "parameters": {
                "type": "object",
                "properties": {"competitor_slug": {"type": "string", "description": "e.g. 'par-technology'."}},
                "required": ["competitor_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "createGeniusCapability",
            "description": (
                "Add one real, discrete Genius product capability for ONE tech-stack "
                "category -- the Genius Capability Library, the structured 'what Genius "
                "offers' baseline createValueWedge draws from. RB-2026-09-25. This is "
                "Todd's own curated product knowledge, same trust posture as todds_pov -- "
                "only ever record what Todd actually said, never a claim you inferred, "
                "summarized, or generated yourself. Append-only; there is no update/delete."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "One of: pos, payments, back_office, kitchen_drive_thru, loyalty_engagement, digital_menu_boards, restaurant_os_platform."},
                    "point": {"type": "string", "description": "One discrete capability Genius actually offers -- verbatim from what Todd said, never a paraphrase."},
                    "why_it_matters": {"type": "string", "description": "Optional: why this matters to a customer, also verbatim from Todd, not your own elaboration."},
                    "evidence_id": {"type": "string", "description": "Optional pointer to a real source backing this claim."},
                },
                "required": ["category", "point"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/genius-capabilities/{category}",
            "path_params": ["category"], "query_params": [],
            "has_body": True, "body_param_names": ["point", "why_it_matters", "evidence_id"],
        },
    ),
    (
        {
            "type": "function",
            "name": "getGeniusCapabilities",
            "description": "List every Genius capability point on file for one tech-stack category.",
            "parameters": {
                "type": "object",
                "properties": {"category": {"type": "string", "description": "e.g. 'pos', 'payments'."}},
                "required": ["category"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/genius-capabilities/{category}", "path_params": ["category"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "listAllGeniusCapabilities",
            "description": (
                "List every Genius capability point on file, across every product line -- "
                "always returns all product-line keys, even ones with nothing filled in yet "
                "(honest-blank, not omitted). Use this to see the whole library at once "
                "rather than checking one category at a time."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/genius-capabilities", "path_params": [], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "createValueWedge",
            "description": (
                "Persists a versioned Value Wedge document for one competitor -- a "
                "sales-enablement document distinct from Competitive Brief (a general "
                "profile) and Battle Card (category-wide, multi-competitor): for every "
                "product line this competitor competes on, pairs the Genius Capability "
                "Library's content for that category with that category's battle card and "
                "the competitor's own vs_genius.competitor_advantages ('where they push "
                "back'). RB-2026-09-25, competitive-side. Fully computed from already-"
                "persisted data -- no user_authorization_quote is required; freely "
                "regenerable. Auto-creates a mostly-empty competitor shell if "
                "competitor_slug doesn't resolve yet (same as createCompetitiveBrief) -- "
                "prefer listCompetitors/createCompetitor first when you have a real name to "
                "resolve against. Honest-blank sections point at setCompetitorProductLines/"
                "createGeniusCapability/setCategoryBattleCard rather than fabricating "
                "content when something's missing -- never invent a capability or "
                "advantage that isn't on file. Use this when the user asks to save/persist/"
                "generate a value wedge for a competitor."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "competitor_slug": {"type": "string", "description": "e.g. 'par-technology'. Call listCompetitors first if unsure."},
                    "generated_for": {"type": "string", "description": "Who this is being prepared for, if relevant. Optional."},
                },
                "required": ["competitor_slug"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/value-wedges/{competitor_slug}",
            "path_params": ["competitor_slug"], "query_params": [],
            "has_body": True, "body_param_names": ["generated_for"],
        },
    ),
    (
        {
            "type": "function",
            "name": "getValueWedge",
            "description": (
                "Return the current persisted Value Wedge for one competitor, verbatim. "
                "Call this for 'what's the value wedge for [competitor]' / 'why do we win "
                "against [competitor]' when you just want to see the existing one, not "
                "regenerate it. RULE-0-style discipline: display the markdown verbatim "
                "rather than re-summarizing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"competitor_slug": {"type": "string", "description": "e.g. 'par-technology'."}},
                "required": ["competitor_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/value-wedges/{competitor_slug}", "path_params": ["competitor_slug"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getValueWedgeDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for a persisted Value Wedge -- the "
                "only correct way to answer 'give me a link to download/view the value "
                "wedge for [competitor]'. Distinct from getCompetitiveBriefDownloadLink/"
                "getBattleCardDownloadLink -- using the wrong one produces a link to the "
                "wrong file. Confirms a real Value Wedge actually exists first, then "
                "returns the real URL to relay verbatim. NEVER construct a link yourself "
                "from a file path."
            ),
            "parameters": {
                "type": "object",
                "properties": {"competitor_slug": {"type": "string", "description": "e.g. 'par-technology'."}},
                "required": ["competitor_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "createVendorEngagementAnalysis",
            "description": (
                "Persists a versioned Vendor Engagement Analysis for one account -- which "
                "vendor(s) this account actually uses, how entrenched each engagement is "
                "(status, deployment, risk, real strategic notes from ecosystem_intelligence.json), "
                "and RBB's competitive angle on each vendor that's also a tracked competitor. "
                "RB-2026-09-07, competitive-side -- distinct from createBattleCard "
                "(category-wide) and createCompetitiveBrief (competitor-wide): this is the one "
                "account-specific view. Fully computed from already-persisted data -- no "
                "user_authorization_quote is required; freely regenerable. Requires the account "
                "to already exist (generateAccountBackgroundBrief first if not). Use this when "
                "the user asks to save/persist/generate a vendor engagement analysis for an "
                "account."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"},
                    "generated_for": {"type": "string", "description": "Who this is being prepared for, if relevant. Optional."},
                },
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/customers-prospects/{account_slug}/vendor-engagement-analysis",
            "path_params": ["account_slug"], "query_params": [],
            "has_body": True, "body_param_names": ["generated_for"],
        },
    ),
    (
        {
            "type": "function",
            "name": "getVendorEngagementAnalysis",
            "description": (
                "Return the current persisted Vendor Engagement Analysis for one account, "
                "verbatim. Call this for 'what vendors does [brand] use and how do we compete "
                "for them' when you just want to see the existing one, not regenerate it. "
                "RULE-0-style discipline: display the markdown verbatim rather than "
                "re-summarizing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"}},
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/customers-prospects/{account_slug}/vendor-engagement-analysis", "path_params": ["account_slug"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getVendorEngagementAnalysisDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for a persisted Vendor Engagement "
                "Analysis -- the only correct way to answer 'give me a link to download/view "
                "the vendor engagement analysis for [account]'. Distinct from "
                "getBattleCardDownloadLink (category-scoped) and getCompetitiveBriefDownloadLink "
                "(competitor-scoped) -- using the wrong one produces a link to the wrong file. "
                "Confirms a real Vendor Engagement Analysis actually exists first, then returns "
                "the real URL to relay verbatim. NEVER construct a link yourself from a file "
                "path."
            ),
            "parameters": {
                "type": "object",
                "properties": {"account_slug": {"type": "string", "description": "e.g. 'cafe-rio'"}},
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "createRelationshipCard",
            "description": (
                "Publishes a versioned snapshot of one contact's EXISTING Relationship Card "
                "(system/cards/{contact_id}.md) into the same governed artifact pattern every "
                "other artifact type has -- this does NOT generate or rewrite card content. "
                "The card's hand-authored sections (Why this matters, Trust state, Leverage, "
                "What's lingering, Risks, How to engage) are Todd's own judgment and are taken "
                "verbatim. RB-2026-09-08, relationship-side. Requires the contact to already be "
                "a real Relationship Card (signal_class == 'RC') with an existing card file -- "
                "call getCard or listCards-style lookups first if unsure. No "
                "user_authorization_quote is required; freely republishable whenever the "
                "underlying card file changes. Use this when the user asks to save/publish/"
                "version a relationship card for someone."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_id": {"type": "string", "description": "e.g. 'jeff-coffland'"},
                    "generated_for": {"type": "string", "description": "Who this is being prepared for, if relevant. Optional."},
                },
                "required": ["contact_id"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/relationship-cards/{contact_id}",
            "path_params": ["contact_id"], "query_params": [],
            "has_body": True, "body_param_names": ["generated_for"],
        },
    ),
    (
        {
            "type": "function",
            "name": "getRelationshipCard",
            "description": (
                "Return the current PUBLISHED (versioned/indexed) Relationship Card for one "
                "contact, verbatim. Distinct from getCard, which reads the raw live file "
                "directly and checks for baseline/frontmatter last_touch drift -- use getCard "
                "for that drift check; use this for the governed, versioned artifact. "
                "RULE-0-style discipline: display the markdown verbatim rather than "
                "re-summarizing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"contact_id": {"type": "string", "description": "e.g. 'jeff-coffland'"}},
                "required": ["contact_id"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/relationship-cards/{contact_id}", "path_params": ["contact_id"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getRelationshipCardDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for a PUBLISHED Relationship Card -- "
                "the only correct way to answer 'give me a link to download/view the "
                "relationship card for [contact]'. Confirms a real published Relationship Card "
                "actually exists first (call createRelationshipCard if not), then returns the "
                "real URL to relay verbatim. NEVER construct a link yourself from a file path."
            ),
            "parameters": {
                "type": "object",
                "properties": {"contact_id": {"type": "string", "description": "e.g. 'jeff-coffland'"}},
                "required": ["contact_id"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "createRelationshipPlan",
            "description": (
                "Creates (or regenerates) a Relationship Plan for one contact -- a "
                "forward-looking, real, caller-supplied goal for this relationship (e.g. "
                "'deepen this over the next quarter,' 'build toward a Relationship Card') plus "
                "concrete next moves and a status (active/paused/achieved/abandoned). "
                "RB-2026-09-08, relationship-side, the final artifact in Todd's original "
                "taxonomy. Genuinely different from getCard's 'How to engage' (communication "
                "STYLE, not goals) and from a transactional loop (this is open-ended standing "
                "intent, not a one-off ask). Works for ANY baseline contact, not only existing "
                "Relationship Cards -- building an LMI/LKI contact toward RC is a real, intended "
                "use case. Never writes to baseline_index.json or system/cards/ -- vault-only. "
                "Same authorization discipline as createAccountPlan/createWinPlan/"
                "createRfpResponsePlan: only call this when the user has explicitly asked for a "
                "new or updated Relationship Plan by name, with a real user_authorization_quote "
                "(verbatim, not paraphrased). relationship_goal/next_moves must be real, "
                "sourced content -- never invented or templated."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_id": {"type": "string", "description": "e.g. 'jeff-coffland'"},
                    "relationship_goal": {"type": "string", "description": "What Todd wants this relationship to become. Real, specific, his own judgment."},
                    "next_moves": {"type": "array", "items": {"type": "string"}, "description": "Real, concrete next moves toward the goal."},
                    "status": {"type": "string", "enum": ["active", "paused", "achieved", "abandoned"]},
                    "target_cadence_days": {"type": "integer", "description": "Target touch cadence in days, if relevant. Optional."},
                    "generated_for": {"type": "string", "description": "Who this is being prepared for, if relevant. Optional."},
                    "user_authorization_quote": {
                        "type": "string",
                        "description": (
                            "REQUIRED. A verbatim quote of what the user actually said "
                            "authorizing this specific Relationship Plan creation -- not a "
                            "paraphrase. Copy their own words from this conversation."
                        ),
                    },
                },
                "required": ["contact_id", "relationship_goal", "next_moves", "status", "user_authorization_quote"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/relationship-plans/{contact_id}",
            "path_params": ["contact_id"], "query_params": [],
            "has_body": True, "body_param_names": ["relationship_goal", "next_moves", "status", "target_cadence_days", "generated_for", "user_authorization_quote"],
        },
    ),
    (
        {
            "type": "function",
            "name": "getRelationshipPlan",
            "description": (
                "Return the current Relationship Plan for one contact, verbatim. Call this for "
                "'what's the relationship plan for [contact]' when you just want to see the "
                "existing one, not regenerate it. RULE-0-style discipline: display the markdown "
                "verbatim rather than re-summarizing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"contact_id": {"type": "string", "description": "e.g. 'jeff-coffland'"}},
                "required": ["contact_id"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/relationship-plans/{contact_id}", "path_params": ["contact_id"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getRelationshipPlanDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for a Relationship Plan -- the only "
                "correct way to answer 'give me a link to download/view the relationship plan "
                "for [contact]'. Confirms a real Relationship Plan actually exists first (call "
                "createRelationshipPlan if not), then returns the real URL to relay verbatim. "
                "NEVER construct a link yourself from a file path."
            ),
            "parameters": {
                "type": "object",
                "properties": {"contact_id": {"type": "string", "description": "e.g. 'jeff-coffland'"}},
                "required": ["contact_id"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "createInnerCircle",
            "description": (
                "Persists a versioned Inner Circle roll-up -- a SINGLE document (not "
                "per-contact) listing every contact currently tagged rc_tier == 'inner' in "
                "baseline_index.json, each with last touch/DRR score/trust state/momentum and "
                "real open loops pulled from their card, split into a full Roster and a Needs "
                "Attention subset (already-computed strained/drifting/broken trust or negative "
                "momentum only). RB-2026-09-08, relationship-side. Distinct from the existing, "
                "differently-scoped 'circles' concept (goal-anchored network-activation files "
                "like hospitality-table.md) -- this is specifically the innermost-tier roll-up. "
                "Fully computed -- no user_authorization_quote required, freely regenerable. "
                "Takes no id/slug argument."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "generated_for": {"type": "string", "description": "Who this is being prepared for, if relevant. Optional."},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/inner-circle",
            "path_params": [], "query_params": [],
            "has_body": True, "body_param_names": ["generated_for"],
        },
    ),
    (
        {
            "type": "function",
            "name": "getInnerCircle",
            "description": (
                "Return the current persisted Inner Circle roll-up, verbatim. Call this for "
                "'who's in my inner circle' / 'show me my closest relationships' when you just "
                "want to see the existing one, not regenerate it. RULE-0-style discipline: "
                "display the markdown verbatim rather than re-summarizing it."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/inner-circle", "path_params": [], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getInnerCircleDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for the persisted Inner Circle roll-up "
                "-- the only correct way to answer 'give me a link to download/view my inner "
                "circle'. Singleton, no id/slug argument. Confirms it actually exists first "
                "(call createInnerCircle if not), then returns the real URL to relay verbatim. "
                "NEVER construct a link yourself from a file path."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "findIntro",
            "description": (
                "Find real candidate broker paths to a target (person or company) -- ranked "
                "insiders/brokers with DRR score, composite score, recommended posture, and a "
                "drafted outreach message, computed fresh from baseline_index.json every call. "
                "RB-2026-09-08: this existed as a real, tested REST endpoint before but was "
                "never exposed as a chat tool -- use this for 'who can introduce me to "
                "[person/company]' / 'find me a path to [target]'. For a saved, linkable, "
                "versioned copy of one target's analysis, call createReferralNetworkAnalysis "
                "instead (this tool never persists anything)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Person id, name, or company name, e.g. 'Toast'."},
                    "limit": {"type": "integer", "description": "Max number of broker paths to return. Optional, default 3."},
                    "include_suppressed": {"type": "boolean", "description": "Include heuristic-suppressed candidates (info-only). Optional, default false."},
                },
                "required": ["target"],
                "additionalProperties": False,
            },
        },
        {
            "method": "GET", "path": "/intro",
            "path_params": [], "query_params": ["target", "limit", "include_suppressed"],
            "has_body": False, "body_param_names": [],
        },
    ),
    (
        {
            "type": "function",
            "name": "createReferralNetworkOverview",
            "description": (
                "Publishes a versioned snapshot of Todd's EXISTING hand-curated referral/broker "
                "overview (system/intro_brokers.md) into the same governed artifact pattern "
                "every other artifact type has -- this does NOT generate or rewrite the "
                "overview's content. RB-2026-09-08, relationship-side. Does not fix the source "
                "file's own staleness (it's Todd's strategic narrative to refresh, not "
                "something computed here). No user_authorization_quote required. Singleton, no "
                "id/slug argument. Use this when the user asks to save/publish/version the "
                "referral network overview."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "generated_for": {"type": "string", "description": "Who this is being prepared for, if relevant. Optional."},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/referral-network/overview",
            "path_params": [], "query_params": [],
            "has_body": True, "body_param_names": ["generated_for"],
        },
    ),
    (
        {
            "type": "function",
            "name": "getReferralNetworkOverview",
            "description": (
                "Return the current PUBLISHED Referral Network Overview, verbatim. Call this "
                "for 'who are my referral brokers / what's my intro network' when you just want "
                "to see the existing one, not regenerate it. RULE-0-style discipline: display "
                "the markdown verbatim rather than re-summarizing it."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/referral-network/overview", "path_params": [], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getReferralNetworkOverviewDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for the PUBLISHED Referral Network "
                "Overview -- the only correct way to answer 'give me a link to download/view my "
                "referral network overview'. Singleton, no id/slug argument. Confirms it "
                "actually exists first (call createReferralNetworkOverview if not), then "
                "returns the real URL to relay verbatim. NEVER construct a link yourself from a "
                "file path."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "createReferralNetworkAnalysis",
            "description": (
                "Persists a versioned Referral Network Analysis for one target (person or "
                "company) -- a markdown rendering of findIntro's own already-computed content "
                "(insiders at the target, ranked candidate brokers with DRR/composite score/"
                "recommended posture/a drafted ask). RB-2026-09-08, relationship-side. "
                "findIntro (GET /intro) is unchanged and still computes fresh by design -- this "
                "is a parallel, additive persisted view, same relationship createBattleCard has "
                "to getCategoryMarketShare. Fully computed -- no user_authorization_quote "
                "required. Raises if the target doesn't resolve to a known person or company -- "
                "call findIntro first to check resolution if unsure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Person id, name, or company name, e.g. 'Toast'."},
                    "generated_for": {"type": "string", "description": "Who this is being prepared for, if relevant. Optional."},
                },
                "required": ["target"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/referral-network/analysis/{target}",
            "path_params": ["target"], "query_params": [],
            "has_body": True, "body_param_names": ["generated_for"],
        },
    ),
    (
        {
            "type": "function",
            "name": "getReferralNetworkAnalysis",
            "description": (
                "Return the current persisted Referral Network Analysis for one target, "
                "verbatim. Call this for 'what's the referral network analysis for [target]' "
                "when you just want to see the existing one, not regenerate it. For a fresh "
                "computed view without persisting it, use findIntro instead. RULE-0-style "
                "discipline: display the markdown verbatim rather than re-summarizing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"target": {"type": "string", "description": "Person id, name, or company name, e.g. 'Toast'."}},
                "required": ["target"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/referral-network/analysis/{target}", "path_params": ["target"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getReferralNetworkAnalysisDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for a persisted Referral Network "
                "Analysis -- the only correct way to answer 'give me a link to download/view "
                "the referral network analysis for [target]'. Distinct from "
                "getReferralNetworkOverviewDownloadLink (singleton, whole-network) -- using the "
                "wrong one produces a link to the wrong file. Confirms a real Referral Network "
                "Analysis actually exists first, then returns the real URL to relay verbatim. "
                "NEVER construct a link yourself from a file path."
            ),
            "parameters": {
                "type": "object",
                "properties": {"target": {"type": "string", "description": "Person id, name, or company name, e.g. 'Toast'."}},
                "required": ["target"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "queryIntelligenceIndex",
            "description": (
                "Query the unified 'what do we have on X, and where' index across EVERY "
                "intelligence-bearing subsystem: Todd's own hand-authored account research "
                "(system/account_intelligence/), Micro Graphs and account dossiers, Blue "
                "Sheets, and Account Research background briefs. Returns pointers (path, "
                "resource_type, title, date), not the content itself. Call this whenever "
                "asked 'what do we have on [brand/topic]' generally, or before assuming RBB "
                "has no prior work on something -- this is the single place that spans every "
                "subsystem, so check it rather than relying on only one tool coming to mind. "
                "Omit query to see everything indexed so far. RB-2026-08-28: this is also the "
                "right tool for a generic 'what artifacts do you have on file' / 'what do you "
                "have in the system' question -- listArtifacts sounds comprehensive but only "
                "covers ONE narrow registry (micro graphs + account dossiers); answering a "
                "general inventory question from listArtifacts alone undercounts badly (a real "
                "incident: it reported 5 total when dozens of real records existed elsewhere)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "e.g. \"McDonald's\". Omit for the full index."}},
                "required": [],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/intelligence-index", "path_params": [], "query_params": ["query"], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "scanCaptureSources",
            "description": (
                "Kick off a scan of enabled capture sources (Just Press Record, RB Captures "
                "Drop Folder, etc.) for files newer than the last scan. getCapturesPending has "
                "NO way to trigger this itself -- it only reads what a PRIOR sweep already "
                "queued, and the only automatic sweep runs once a day in the morning. A "
                "recording made any time after that (e.g. mid-afternoon) is invisible to "
                "getCapturesPending until this is called. Runs in the BACKGROUND and returns "
                "immediately -- real local Whisper transcription of even a few recordings can "
                "take several minutes on CPU (confirmed live: three real recordings took over "
                "two minutes). Call getCaptureScanStatus after about a minute to check on it, "
                "then getCapturesPending for the real results. Only call this when the user "
                "explicitly asks to process something recent/today's, not on every routine "
                "check -- and never call it again while one is already running (check "
                "getCaptureScanStatus first)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"source": {"type": "string", "description": "Optional: limit to one source id, e.g. 'just_press_record'. Omit to scan all enabled sources."}},
                "required": [],
                "additionalProperties": False,
            },
        },
        {"method": "POST", "path": "/captures/scan", "path_params": [], "query_params": ["source"], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getCaptureScanStatus",
            "description": (
                "Check whether a background scan started by scanCaptureSources has finished, "
                "and see its result (files_queued, per-file status). If running is true, the "
                "scan is still going -- wait and check again rather than starting a second "
                "scan or telling the user nothing was found."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/captures/scan/status", "path_params": [], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getBriefDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for an Account Background Brief -- "
                "the only correct way to answer 'give me a link to download/view the brief "
                "for [brand]'. Confirms a brief actually exists first, then returns the real "
                "URL to relay verbatim. NEVER construct a link yourself from a file path "
                "(e.g. 'system/account_research/accounts/.../Background_Brief.md') -- that is "
                "a local server path, not a URL, and does nothing in a browser. If this tool "
                "returns an error because no brief exists yet, say so plainly and offer to "
                "call generateAccountBackgroundBrief instead of inventing a link. Also: an "
                "Account Background Brief is NOT a Blue Sheet -- never call it one in your "
                "reply, even if the user says 'blue sheet' when they mean this."
            ),
            "parameters": {
                "type": "object",
                "properties": {"account_slug": {"type": "string", "description": "e.g. 'cafe-rio' or \"McDonald's\""}},
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "getCapturesProcessed",
            "description": (
                "List captures that have ALREADY been processed -- distinct from "
                "getCapturesPending, which only shows what's still awaiting processing. Route "
                "'review/summarize what did we cover in [today's/recent] calls/meetings/"
                "captures' here, not to getCapturesPending, when the user wants to look BACK "
                "at something already handled rather than process something new. "
                "getCapturesPending correctly reporting zero pending does NOT mean nothing "
                "happened -- it may already be processed and retrievable here. Each item "
                "includes processing_result (the real triage summary already recorded)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max captures to return. Default 20."},
                    "since_date": {"type": "string", "description": "For \"today's calls\" pass the literal word 'today' (or 'yesterday') -- the server resolves the real date. Do NOT compute or guess a YYYY-MM-DD string yourself; confirmed live 2026-08-27 that this model cannot reliably do that date arithmetic. An explicit YYYY-MM-DD is only safe when the user stated that exact date themselves."},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/captures/processed", "path_params": [], "query_params": ["limit", "since_date"], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getCaptureProcessed",
            "description": (
                "Retrieve a single already-processed capture, including its full transcript "
                "and the triage/processing_result recorded when it was submitted. Use this to "
                "actually review or summarize a past capture -- read the real transcript and "
                "processing_result, never regenerate its content from memory or from the "
                "listing alone."
            ),
            "parameters": {
                "type": "object",
                "properties": {"file_id": {"type": "string", "description": "e.g. 'cap-a7a4517bf80eca1d'"}},
                "required": ["file_id"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/captures/processed/{file_id}", "path_params": ["file_id"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getCaptureDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for a processed capture's actual "
                "transcript (as a .md file) -- call this if the user wants to download/save "
                "a capture to review or revise locally, not just read it in chat. Never "
                "construct a link yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {"file_id": {"type": "string", "description": "e.g. 'cap-a7a4517bf80eca1d'"}},
                "required": ["file_id"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "createBlueSheetAccount",
            "description": (
                "Creates a brand-new Blue Sheet account -- a plan for an account under ACTIVE "
                "engagement -- and renders the real xlsx, the same way Pollo Campero's/Five "
                "Guys'/Del Taco's were built. Real gap closed 2026-08-27: there was previously "
                "no way to create a new Blue Sheet at all. Requires already-curated, real, "
                "sourced structured content in the exact schema each xlsx tab reads "
                "(account_data: opportunities[] with single_sales_objective/customer_stated_"
                "objective/commercial_hypothesis, qualification.criteria[], strategic_position "
                "with euphoria_panic/competition/position/strengths[]/red_flags[], buying_"
                "influences[], technology_stack[], commercial_models[], latest_review, "
                "bottom_line; brand_profile_data: identity_ownership_footprint[] and related "
                "category arrays; actions_data: actions[]). NEVER call this with invented or "
                "guessed field values -- build it from real, cited RBB intelligence (Account "
                "Research, account_intelligence docs, evidence already on file), leaving "
                "genuinely unknown fields marked unknown rather than filled in. Blue Sheets "
                "require the user's explicit direction to create -- only call this when they "
                "have clearly asked for a new Blue Sheet BY NAME, never as a silent substitute "
                "for an Account Background Brief request, and never call Account Research "
                "content a 'Blue Sheet' instead of actually building one. RB-2026-08-28: a real "
                "incident produced an empty 'worldpay' Blue Sheet from a portfolio-level, multi-"
                "account document upload the user never asked to become a Blue Sheet -- "
                "account_data with no real content is now rejected (422), and "
                "user_authorization_quote is required. A generic 'update the account plan' "
                "request is NOT authorization to create a Blue Sheet for an account that "
                "doesn't have one yet -- if the transcript doesn't contain an explicit Blue "
                "Sheet creation request, do not call this; ask the user first. A Blue Sheet is "
                "for ONE account and ONE sales objective. If the request or uploaded document "
                "is a multi-account portfolio organized by a vendor's own relationship "
                "managers (scored ranked-account table, RM roster) -- that is a Master Account "
                "Plan, a completely different artifact type. Use uploadAndIngestFile (it "
                "auto-recognizes and routes that document shape) or listMasterAccountPlans; "
                "never this tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_slug": {"type": "string", "description": "e.g. 'mcdonalds'"},
                    "display_name": {"type": "string", "description": "e.g. \"McDonald's\""},
                    "account_data": {"type": "object", "description": "opportunities[]/qualification/strategic_position/buying_influences[]/technology_stack[]/commercial_models[]/latest_review/bottom_line. Must contain real, substantive content -- an all-empty object is rejected."},
                    "brand_profile_data": {"type": "object", "description": "identity_ownership_footprint[] and related category arrays"},
                    "actions_data": {"type": "array", "items": {"type": "object"}, "description": "actions[]"},
                    "evidence_records": {"type": "array", "items": {"type": "object"}, "description": "Real evidence.jsonl entries citing actual sources"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "user_authorization_quote": {
                        "type": "string",
                        "description": (
                            "REQUIRED. A verbatim quote of what the user actually said that "
                            "constitutes explicit, unambiguous authorization to create a NEW "
                            "Blue Sheet for this specific account. Copy their own words from "
                            "this conversation -- do not paraphrase or invent one."
                        ),
                    },
                },
                "required": ["account_slug", "display_name", "account_data", "brand_profile_data", "user_authorization_quote"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/blue-sheets", "path_params": [], "query_params": [],
            "has_body": True, "body_param_names": ["account_slug", "display_name", "account_data", "brand_profile_data", "actions_data", "evidence_records", "aliases", "user_authorization_quote"],
        },
    ),
    (
        {
            "type": "function",
            "name": "getBlueSheetDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for a Blue Sheet's actual xlsx "
                "workbook -- the only correct way to answer 'give me a link to download/view "
                "the BLUE SHEET for [brand]'. Distinct from getBriefDownloadLink, which "
                "serves an Account Background Brief's markdown -- a different document. "
                "Confirmed live 2026-08-27: calling the wrong one produces a link to the "
                "wrong file entirely. Confirms a real xlsx workbook exists first, then "
                "returns the real URL to relay verbatim. Never construct a link yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {"account_slug": {"type": "string", "description": "e.g. 'pollo-campero' or 'mcdonalds'"}},
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "addBlueSheetCustomerArtifact",
            "description": (
                "Save a structured, customer-facing table (e.g. a pricing matrix for an RFP) "
                "on a Blue Sheet account, so it can become a real downloadable Excel file via "
                "getBlueSheetCustomerArtifactDownloadLink. RB-2026-09-02: real gap -- a request "
                "to 'save this as a customer-facing artifact' was previously only answerable "
                "with addBlueSheetEvidence, which stores markdown text in an evidence excerpt "
                "that nothing ever renders into a spreadsheet. Use THIS tool specifically when "
                "the user wants a clean table handed to them (or eventually the customer) as "
                "its own Excel file -- e.g. 'give me a downloadable excel file of the pricing "
                "matrix', 'save the customer-facing version as an artifact'. Deliberately "
                "separate from the internal Blue Sheet workbook (which has Commercial "
                "Model/red-flag/buying-influence-rating tabs never meant for a customer) -- "
                "never put internal-only content in columns/rows here. Use addBlueSheetEvidence "
                "instead for free-text notes, claims, or anything not shaped as a clean table. "
                "Only real column headers and real row values actually present in a real "
                "source or stated by the user -- a genuinely undecided cell should be 'TBD', "
                "never an invented number."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_slug": {"type": "string", "description": "e.g. 'pollo-campero'. Call listBlueSheetAccounts if unsure."},
                    "title": {"type": "string", "description": "e.g. 'Pollo Campero DMB Pricing Matrix — Customer-Facing Draft'."},
                    "columns": {"type": "array", "items": {"type": "string"}, "description": "Column headers, in order. Real headers only."},
                    "rows": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "string"}},
                        "description": "Table rows -- each a list of cell values, same order and length as columns. Use 'TBD' for a genuinely undecided cell, never a made-up number.",
                    },
                    "notes": {"type": "string", "description": "Optional footer note, e.g. what was deliberately excluded and why."},
                    "source_evidence_id": {"type": "string", "description": "The addBlueSheetEvidence evidence_id this table came from, if one exists."},
                },
                "required": ["account_slug", "title", "columns", "rows"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/blue-sheets/{account_slug}/customer-artifacts",
            "path_params": ["account_slug"], "query_params": [], "has_body": True,
            "body_param_names": ["title", "columns", "rows", "notes", "source_evidence_id"],
        },
    ),
    (
        {
            "type": "function",
            "name": "listBlueSheetCustomerArtifacts",
            "description": (
                "List the customer-facing artifacts (structured tables saved via "
                "addBlueSheetCustomerArtifact) on one Blue Sheet account -- title, "
                "artifact_id, created_date. Use to find the right artifact_id for "
                "getBlueSheetCustomerArtifactDownloadLink, or to answer 'what customer-facing "
                "exports exist for [account]'. Distinct from the account's evidence.jsonl "
                "(free-text) and from its main internal workbook (getBlueSheetDownloadLink)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"account_slug": {"type": "string", "description": "e.g. 'pollo-campero'. Call listBlueSheetAccounts if unsure."}},
                "required": ["account_slug"],
                "additionalProperties": False,
            },
        },
        {"method": "GET", "path": "/blue-sheets/{account_slug}/customer-artifacts", "path_params": ["account_slug"], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getBlueSheetCustomerArtifactDownloadLink",
            "description": (
                "Get a REAL, working downloadable link for one customer-facing artifact's "
                "actual xlsx export -- a clean, single-sheet table file, distinct from "
                "getBlueSheetDownloadLink (the full internal Blue Sheet workbook, which "
                "carries Commercial Model/red-flag/rating data never meant for a customer). "
                "Use this the moment the user asks for 'a downloadable excel file' of a table "
                "you saved with addBlueSheetCustomerArtifact. Call listBlueSheetCustomerArtifacts "
                "first if you don't already have the exact artifact_id. Confirms the specific "
                "artifact exists first, then returns the real URL to relay verbatim -- never "
                "construct a link yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_slug": {"type": "string", "description": "e.g. 'pollo-campero'"},
                    "artifact_id": {"type": "string", "description": "e.g. 'cfa-pollo-campero-0001', from addBlueSheetCustomerArtifact or listBlueSheetCustomerArtifacts."},
                },
                "required": ["account_slug", "artifact_id"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "getUploadedDocument",
            "description": (
                "Retrieve the full extracted text of a document previously uploaded through "
                "uploadAndIngestFile, by the document_id that call returned (also shown as "
                "ingest_result.document_id / the top-level ingestion_id on the same receipt). "
                "RB-2026-08-31 (Todd's defect report, Pollo Campero RFP response): "
                "uploadAndIngestFile extracts a DOCX/PDF's full text mechanically, but that "
                "text previously lived only in memory for the one call that ingested it and "
                "was then discarded -- there was no way to retrieve 'what does this document "
                "actually say' afterward, so a requested section-by-section review of an "
                "uploaded response document (e.g. against a known TDR/SOW) was impossible. "
                "Call this whenever asked to review, compare, or quote from an uploaded "
                "document's actual content -- never guess or reconstruct its text from the "
                "short extracted_text_preview in the upload receipt alone. With no "
                "section_index: returns the full text (with sections/pages, when the format "
                "supports them -- Word headings for .docx, per-page for .pdf) -- for a large "
                "document the flat extracted_text field truncates at ~40,000 characters "
                "(truncated=true) while the real per-section text never does; use "
                "section_index against the returned sections list for the untruncated text of "
                "any one part. If extraction_status was 'failed' on upload, there is no text "
                "to retrieve -- tell the user why (see uploadAndIngestFile's error) rather than "
                "inventing document content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "From uploadAndIngestFile's receipt (ingestion_id / ingest_result.document_id)."},
                    "section_index": {"type": "integer", "description": "Optional 0-based index into a prior call's sections list, to retrieve just that one section/page's untruncated text."},
                },
                "required": ["document_id"],
                "additionalProperties": False,
            },
        },
        {
            "method": "GET", "path": "/documents/{document_id}", "path_params": ["document_id"],
            "query_params": ["section_index"], "has_body": False, "body_param_names": [],
        },
    ),
    (
        {
            "type": "function",
            "name": "fetchUrlContent",
            "description": (
                "Actually fetch a URL and extract its real, readable article text -- call this "
                "whenever Todd pastes or references a URL (a news article, a blog post, a "
                "LinkedIn link, etc.) and wants its content read or summarized. Pass the URL "
                "exactly as given, character for character -- never a URL you construct or "
                "guess. This does a real server-side HTTP fetch; you have not read the page "
                "and cannot know what it says until this returns. "
                "If it returns ok: false: say so plainly, using its real reason (e.g. the site "
                "requires login, the link is broken, the page returned too little readable "
                "text), and suggest Todd paste the text or a screenshot instead -- NEVER answer "
                "as if you read the page anyway, and never fill the gap with general knowledge "
                "about that domain/topic/company. RB-2026-09-01 (Todd's own request, after a "
                "pasted LinkedIn URL correctly got 'RB only received the link, not the article "
                "body' because no fetch tool existed at all): LinkedIn specifically will still "
                "return ok: false every time -- it blocks anonymous fetches and RBB has no "
                "authenticated session -- this is expected, not a bug, say so honestly rather "
                "than retrying or apologizing as if it should have worked. "
                "If it returns ok: true: the text field is the real, actually-fetched page "
                "content -- summarize or discuss THAT text, and if passing it on to "
                "ingestContent or processMacroSignal, use that real text verbatim, never a "
                "paraphrase from memory."
            ),
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "The exact URL to fetch, e.g. 'https://example.com/article'."}},
                "required": ["url"],
                "additionalProperties": False,
            },
        },
        {"method": "GET"},
    ),
    (
        {
            "type": "function",
            "name": "getTechStackRelationshipProposals",
            "description": (
                "List pending candidate tech-stack vendor relationships awaiting review -- "
                "confirm or reject each via confirmProposal(kind=\"tech_stack_relationship\", "
                "id=candidate_id). RB-2026-08-31: ecosystem_intelligence.json's real tech-stack "
                "coverage is thin (confirmed ~17% of tracked brands) -- these candidates were "
                "found by scanning already-captured signals and account_intelligence notes for "
                "a real brand+vendor relationship that was never structured into the graph. "
                "Every candidate carries its real source evidence and is never written to the "
                "graph until explicitly confirmed. A null category (category_confidence="
                "\"unknown\") means the source named a vendor relationship but not which "
                "product category -- pass category when calling researchBrandTechStack for a "
                "related finding, or reject the candidate; never guess a category yourself."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/tech-stack/proposals", "path_params": [], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "researchBrandTechStack",
            "description": (
                "Record one real tech-stack finding YOU already researched (real web search/"
                "fetch tools, a real source) about one brand -- queues it as a candidate via "
                "getTechStackRelationshipProposals/confirmProposal, same review-first "
                "discipline as everywhere else in RB. This does NOT do its own web research -- "
                "do the research first, then call this with what you actually found; never "
                "invent a vendor name or evidence text. Call this one brand at a time when Todd "
                "asks you to research a specific brand's tech stack -- never loop this across "
                "many brands unattended; pacing which brands to research is his call. Resolve "
                "brand_id via queryIntelligenceIndex first if you don't already have it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "brand_id": {"type": "string", "description": "e.g. 'brand-five-guys'"},
                    "vendor_name": {"type": "string", "description": "Need not already be a tracked entity."},
                    "evidence_text": {"type": "string", "description": "The real evidence found -- never a summary or guess."},
                    "category": {"type": "string", "description": "Optional; inferred from evidence_text if omitted -- never guessed from the vendor's general market position."},
                    "source_url": {"type": "string"},
                    "source_title": {"type": "string"},
                    "evidence_date": {
                        "type": "string",
                        "description": (
                            "YYYY-MM-DD -- the real-world date the evidence was published/"
                            "discovered (press release dateline, filing date, post timestamp), "
                            "NOT today's date. Omit if genuinely unknown; never guess one."
                        ),
                    },
                },
                "required": ["brand_id", "vendor_name", "evidence_text"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/tech-stack/research", "path_params": [], "query_params": [], "has_body": True,
            "body_param_names": ["brand_id", "vendor_name", "evidence_text", "category", "source_url", "source_title", "evidence_date"],
        },
    ),
    (
        {
            "type": "function",
            "name": "getPriorityAccountPublisherMatches",
            "description": (
                "List pending trade-publisher article matches for priority accounts (every "
                "customers_prospects account + every vendor's Master Account Plan) awaiting "
                "review -- confirm or reject each via confirmProposal(kind="
                "\"priority_account_publisher_match\", id=candidate_id). RB-DEFECT-069 "
                "(2026-09-10): a real, material Del Taco article was invisible to RB for 8 days "
                "despite RestaurantNews.com being an already-monitored source -- monitored only "
                "ever meant \"feeds the general daily-brief news cap,\" not \"every article about "
                "a specific priority account is captured.\" This scans trade publishers per "
                "priority account (name + real aliases) instead of relying on that general cap; "
                "every candidate carries its real source article and is never written to an "
                "account's evidence until explicitly confirmed."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/priority-accounts/publisher-matches", "path_params": [], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getOwnershipChangeProposals",
            "description": (
                "List pending M&A/ownership-change candidates awaiting review -- confirm or "
                "reject each via confirmProposal(kind=\"ownership_change\", id=candidate_id, "
                "owner_name=... when needed). RB-2026-09-11: M&A signals are already detected "
                "daily by other scanners, but no entity ever had a structured owner/parent-"
                "company field to promote a verified acquisition into -- even one Todd had "
                "already fully researched by hand. Every candidate carries its real source "
                "evidence and a best-effort extracted owner name "
                "(proposed_owner_confidence: \"extracted_from_text\" or \"unknown\"). Read the "
                "candidate's evidence_excerpt yourself before trusting a prefilled name -- "
                "acquisition-direction extraction is best-effort, not guaranteed -- and always "
                "supply the real owner_name yourself when proposed_owner_name is null."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/ownership/proposals", "path_params": [], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "reportOwnershipFinding",
            "description": (
                "Record one real ownership/M&A finding YOU already researched (real web "
                "search/fetch tools, a real source) about one brand or vendor -- queues it as "
                "a candidate via getOwnershipChangeProposals/confirmProposal, same review-first "
                "discipline as everywhere else in RB. This does NOT do its own web research -- "
                "do the research first, then call this with what you actually found; never "
                "invent an owner name or evidence text. Supply owner_name directly when you "
                "already know it (the normal case for manual research). Resolve entity_id via "
                "queryIntelligenceIndex first if you don't already have it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string", "description": "e.g. 'brand-del-taco'"},
                    "evidence_text": {"type": "string", "description": "The real evidence found -- never a summary or guess."},
                    "owner_name": {"type": "string", "description": "The real owner/parent-company name, if already known. Omit only to let RB attempt best-effort extraction."},
                    "source_url": {"type": "string"},
                    "source_title": {"type": "string"},
                    "evidence_date": {
                        "type": "string",
                        "description": (
                            "YYYY-MM-DD -- the real-world date the acquisition/evidence was "
                            "published/discovered, NOT today's date. Omit if genuinely unknown; "
                            "never guess one."
                        ),
                    },
                },
                "required": ["entity_id", "evidence_text"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/ownership/research", "path_params": [], "query_params": [], "has_body": True,
            "body_param_names": ["entity_id", "evidence_text", "owner_name", "source_url", "source_title", "evidence_date"],
        },
    ),
    (
        {
            "type": "function",
            "name": "getExecutiveMoveProposals",
            "description": (
                "List pending executive-move candidates awaiting review -- confirm or reject "
                "each via confirmProposal(kind=\"executive_move\", id=candidate_id, name=..., "
                "title=..., contact_id=... when needed). RB-2026-09-11: leadership_change is "
                "RBB's most common real material signal type, but the named executive was "
                "discarded at classification time -- never checked against "
                "baseline_index.json (RBB's real contact-tracking system). Each candidate's "
                "action field tells you which write confirming will do: "
                "\"update_existing_contact\" (someone Todd already knows just moved -- a real "
                "warm-intro/opportunity signal) or \"create_new_contact\". Read the candidate's "
                "evidence_excerpt yourself before trusting a prefilled proposed_name/"
                "proposed_title -- extraction is best-effort, not guaranteed, and "
                "proposed_confidence \"unknown\" means nothing was extracted at all."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/executive-moves/proposals", "path_params": [], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "getJobPostingCandidates",
            "description": (
                "List pending job-posting candidates awaiting review -- confirm or reject each "
                "via confirmProposal(kind=\"job_posting\", id=candidate_id). RB-2026-09-18, wired "
                "2026-09-25: a customers_prospects account posting a \"Director of Restaurant "
                "Technology\"/\"POS Program Manager\"-shaped role is a real pre-signal of an "
                "impending tech-stack evaluation, before any press release or executive move "
                "confirms it. Scoped to customers_prospects accounts only (v1). Confirming never "
                "claims a fact -- only links the posting as a fact-free evidence reference "
                "(extracted_claims is always []) and bumps the account's last_evidence_date, same "
                "shape as getPriorityAccountPublisherMatches. Because of that, the daily scan "
                "auto-confirms every material match immediately -- most candidates will already "
                "show status \"confirmed\" rather than sitting pending; this list is what's still "
                "genuinely awaiting review (a rare residual, not the normal case)."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {"method": "GET", "path": "/job-postings/proposals", "path_params": [], "query_params": [], "has_body": False, "body_param_names": []},
    ),
    (
        {
            "type": "function",
            "name": "queueCaptureText",
            "description": (
                "Queue real user-provided text (an article, a paste, an observation) for "
                "processing on tomorrow's automatic morning pass instead of right now -- the "
                "default for most pasted intelligence, per Todd. Runs through the identical "
                "triage pipeline ingestContent uses, just on a delay; non-noise intelligence is "
                "auto-persisted with no separate confirm step (safe here specifically because "
                "it's a deterministic scheduled script, never a live model decision), and the "
                "result is reported back via the Daily Brief's Captures sections and the "
                "Intelligence Brief's Capture Intelligence section -- the same places meeting-"
                "capture processing already reports, not a new place to check. Use this when the "
                "content does NOT need to inform anything being actively worked on in THIS "
                "conversation right now (building an account plan, a proposal, answering a live "
                "question) -- that case still needs ingestContent, called now. Only for genuine "
                "user-provided text, never text you generated yourself -- same non-negotiable "
                "ingestContent already carries. Local workspace clients may instead save .txt/.md "
                "files to system/inbox/chatgpt_intelligence_drop/, an enabled daily watched folder; "
                "the hosted Custom GPT has no filesystem access and must use this action, never claim "
                "it wrote the local folder. Don't ask 'now or later?' when context already "
                "makes the answer obvious; default to queueing when genuinely ambiguous."
            ),
            "parameters": {
                "type": "object",
                "properties": {
    "text": {"type": "string", "description": "The real user-provided text to queue -- verbatim, never a summary or paraphrase."},
                    "title_hint": {"type": "string", "description": "Optional short human-readable label, e.g. 'Restaurant Business article on Del Taco'."},
                    "source_url": {"type": "string", "description": "The real URL this came from, if the user gave one."},
                    "capture_type": {
                        "type": "string",
                        "enum": ["pasted_content", "deep_research"],
                        "description": (
                            "Defaults to 'pasted_content'. Use 'deep_research' specifically for "
                            "an evidence packet from a deep-research cycle -- it then reports in "
                            "the Intelligence Brief's Capture Intelligence section the same way a "
                            "local client's file dropped in system/inbox/chatgpt_intelligence_drop/ "
                            "already does, instead of being labeled a generic paste."
                        ),
                    },
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
        {"method": "POST", "path": "/captures/queue-text", "path_params": [], "query_params": [], "has_body": True, "body_param_names": ["text", "title_hint", "source_url", "capture_type"]},
    ),
    (
        {
            "type": "function",
            "name": "getIntelligenceActionQueue",
            "description": (
                "Return the full ranked, review-first intelligence action queue -- "
                "consolidated buying-window hypotheses, first-party page changes, "
                "baseline-research gaps, downstream ramifications, and grouped competitor-"
                "review items, one queue instead of dispersed cache files. The daily brief's "
                "Part 2 only surfaces the top 8 pending-review items by rank; call this for "
                "the rest, or to filter to one action_class (e.g. everything currently at "
                "research_further) or resolution status. Every item is a recommendation, "
                "never proof of an executed action -- a queue entry never means RB contacted "
                "an account, created an opportunity, changed a pursuit stage, or wrote a "
                "canonical claim. RB-2026-09-15: added alongside resolveIntelligenceActionQueueItem "
                "so this queue -- previously daily-brief-only -- is directly reachable from chat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action_class": {"type": "string", "description": "Optional filter: monitor, research_further, contact_account, build_pursuit, or competitive_displacement_opportunity."},
                    "status": {"type": "string", "description": "Optional filter: 'pending_review' or 'resolved'. Omit for both."},
                    "limit": {"type": "integer", "description": "Max items to return, already rank-sorted. Optional, default 50."},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "method": "GET", "path": "/intelligence-action-queue",
            "path_params": [], "query_params": ["action_class", "status", "limit"],
            "has_body": False, "body_param_names": [],
        },
    ),
    (
        {
            "type": "function",
            "name": "resolveIntelligenceActionQueueItem",
            "description": (
                "Record Todd's disposition on one intelligence action queue item -- "
                "'accepted' (he acted on it), 'rejected' (not relevant / false positive), "
                "or 'deferred' (correct signal, not right now). This is the durable record "
                "outcome calibration reads later; it does NOT itself contact an account, "
                "create an opportunity, or change a pursuit stage -- those stay on their own "
                "existing authorization paths. Call getIntelligenceActionQueue first to get a "
                "real queue_id -- ids can change when the underlying evidence changes, same "
                "id-mixup risk as closeLoop/redateLoop."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "queue_id": {"type": "string", "description": "From getIntelligenceActionQueue's items[].queue_id."},
                    "disposition": {"type": "string", "description": "One of: accepted, rejected, deferred."},
                    "note": {"type": "string", "description": "Optional short note of what Todd decided."},
                },
                "required": ["queue_id", "disposition"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/intelligence-action-queue/{queue_id}/resolve",
            "path_params": ["queue_id"], "query_params": ["disposition", "note"],
            "has_body": False, "body_param_names": [],
        },
    ),
    (
        {
            "type": "function",
            "name": "getIntelligenceCalibration",
            "description": (
                "Return how RB's intelligence action queue is actually doing -- real "
                "accept/reject/defer disposition counts by item_type, and, for "
                "buying_window_hypothesis items only, whether an accepted hypothesis's "
                "entity later shows a real active_pursuit outcome (from RB's Blue Sheet "
                "registry, not inferred). 'Is RB's judgment actually good', 'how many of "
                "my accepted calls turned into something real', 'how is the intelligence "
                "queue calibrating' -> this. Every other item_type has no equivalent "
                "outcome source yet and is never claimed to be validated. This reads as "
                "mostly empty for a while after 2026-09-15 -- that reflects real "
                "accumulated dispositions, not a malfunction; never draw a conclusion "
                "from a small total_resolutions count."
            ),
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {
            "method": "GET", "path": "/intelligence-calibration",
            "path_params": [], "query_params": [], "has_body": False, "body_param_names": [],
        },
    ),
    (
        {
            "type": "function",
            "name": "listFranchiseeOrganizations",
            "description": (
                "List every franchisee organization Franchisee Finder has on file -- "
                "multi-brand restaurant franchisee groups (e.g. Flynn Group, Sun Holdings) "
                "and large foodservice contractors (e.g. Sodexo, Aramark) with at least one "
                "identified restaurant-brand relationship. Call this for 'who are our "
                "biggest franchisee operators' / 'which franchisees operate more than N "
                "restaurants' or to find the right org_slug for getFranchiseeProfile. Use "
                "queryFranchiseesByBrand instead when the question is about one specific "
                "brand (e.g. 'who are Taco Bell's franchisees')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "min_units": {"type": "integer", "description": "Only organizations with at least this many total identified units."},
                    "multi_brand_only": {"type": "boolean", "description": "Only organizations operating 2+ distinct brands."},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "method": "GET", "path": "/franchisee-organizations",
            "path_params": [], "query_params": ["min_units", "multi_brand_only"], "has_body": False, "body_param_names": [],
        },
    ),
    (
        {
            "type": "function",
            "name": "getFranchiseeProfile",
            "description": (
                "Return one franchisee organization's full profile: headquarters, "
                "ownership, legal entities, every brand relationship with its own "
                "unit-count assertion and history, leadership/people, and the complete "
                "evidence ledger. Every assertion carries its own confidence_pct and "
                "status (confirmed/inferred/unresolved/contradicted) -- relay that "
                "distinction, never present a value here as settled fact without it. "
                "Call listFranchiseeOrganizations or queryFranchiseesByBrand first if you "
                "don't already have the org_slug."
            ),
            "parameters": {
                "type": "object",
                "properties": {"org_slug": {"type": "string"}},
                "required": ["org_slug"],
                "additionalProperties": False,
            },
        },
        {
            "method": "GET", "path": "/franchisee-organizations/{org_slug}",
            "path_params": ["org_slug"], "query_params": [], "has_body": False, "body_param_names": [],
        },
    ),
    (
        {
            "type": "function",
            "name": "queryFranchiseesByBrand",
            "description": (
                "Answer 'who are the franchisees of brand X' -- every tracked organization "
                "with a brand relationship matching that brand, each with that "
                "relationship's own unit count, confidence, and status. An empty result "
                "means no franchisee relationship is on file yet for that brand (a real, "
                "honest answer, not an error) -- never fill the gap with general "
                "knowledge."
            ),
            "parameters": {
                "type": "object",
                "properties": {"brand_name": {"type": "string", "description": "e.g. 'Taco Bell'."}},
                "required": ["brand_name"],
                "additionalProperties": False,
            },
        },
        {
            "method": "GET", "path": "/franchisee-organizations/by-brand/{brand_name}",
            "path_params": ["brand_name"], "query_params": [], "has_body": False, "body_param_names": [],
        },
    ),
    (
        {
            "type": "function",
            "name": "getTechnologyLifecycleProfile",
            "description": (
                "Return everything Technology Lifecycle has on one brand: every tracked "
                "technology relationship with its current lifecycle state (selected/"
                "contracted/rollout_active/deployed/displaced/etc.), governance records, "
                "penetration observations, reconstructed change-event narratives, and open "
                "forcing signals (approaching EOL, leadership change, etc. that haven't yet "
                "led to a completed switch). An empty profile is a real, honest answer -- "
                "this brand has no technology-lifecycle research on file yet, not an error. "
                "brand_name is resolved against ecosystem_intelligence.json's real brand/vendor "
                "entities -- a 404 means no such entity is tracked at all."
            ),
            "parameters": {
                "type": "object",
                "properties": {"brand_name": {"type": "string", "description": "e.g. 'Burger King'."}},
                "required": ["brand_name"],
                "additionalProperties": False,
            },
        },
        {
            "method": "GET", "path": "/technology-lifecycle/profile/{brand_name}",
            "path_params": ["brand_name"], "query_params": [], "has_body": False, "body_param_names": [],
        },
    ),
    (
        {
            "type": "function",
            "name": "listTechnologyForcingSignals",
            "description": (
                "List standalone pre-change signals (approaching OS/hardware EOL, a new "
                "CTO, a transformation announcement) that haven't yet led to a completed "
                "technology switch -- the raw material for a future change-propensity read, "
                "not itself a confirmed change. Optionally filter to one brand and/or one "
                "technology_category."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "brand_name": {"type": "string"},
                    "technology_category": {"type": "string"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "method": "GET", "path": "/technology-lifecycle/forcing-signals",
            "path_params": [], "query_params": ["brand_name", "technology_category"], "has_body": False, "body_param_names": [],
        },
    ),
    (
        {
            "type": "function",
            "name": "createTechnologyForcingSignal",
            "description": (
                "Record one standalone pre-change signal about a brand's CURRENT technology "
                "stack -- deliberately cheap and ungated, same philosophy as createCompetitor/"
                "addCompetitiveNote. Never call this with a signal you inferred without "
                "saying so -- evidence_type:'rbb_inference' exists exactly for that case. "
                "404s if brand_name doesn't resolve to an existing ecosystem_intelligence.json "
                "entity -- this never invents a new entity id scheme."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "brand_name": {"type": "string"},
                    "technology_category": {"type": "string"},
                    "forcing_event_type": {"type": "string", "description": "e.g. os_eol, hardware_eol, vendor_support_sunset, leadership_change, other."},
                    "detail": {"type": "string"},
                    "evidence": {"type": "string"},
                    "source_url": {"type": "string"},
                    "confidence": {"type": "string", "description": "high, medium, or low."},
                    "evidence_type": {"type": "string", "description": "vendor_claim, operator_statement, independent_evidence, rbb_inference, or unknown."},
                },
                "required": ["brand_name", "technology_category", "forcing_event_type", "detail", "evidence", "confidence", "evidence_type"],
                "additionalProperties": False,
            },
        },
        {
            "method": "POST", "path": "/technology-lifecycle/forcing-signals",
            "path_params": [], "query_params": [], "has_body": True,
            "body_param_names": ["brand_name", "technology_category", "forcing_event_type", "detail", "evidence", "source_url", "confidence", "evidence_type"],
        },
    ),
]


TOOLS, OPERATIONS = build_tools_and_operations()


if __name__ == "__main__":
    import json

    print(f"{len(TOOLS)} tools built from {GPT_SPEC_PATH.name}")
    print(json.dumps(TOOLS[:2], indent=2))
