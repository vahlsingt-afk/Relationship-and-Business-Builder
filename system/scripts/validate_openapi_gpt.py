#!/usr/bin/env python3
"""
validate_openapi_gpt.py — build and validate the Custom GPT-facing OpenAPI
schema.

2026-08-28: Todd decided to retire the Custom GPT ("Relationship Bridge
9.0" / ChatGPT Project "RBB") as a live surface — see
project_open_decisions_ledger memory, item 13. **This file/schema is NOT
dead, though** — `rbb_chat_tools.py` (the live Trusted Chat Client's tool
loader) builds its real op set directly from this file's 30-op
`openapi_gpt.yaml` output PLUS its own `_EXTRA_RBB_CHAT_ONLY_TOOLS` list,
so `openapi_gpt.yaml`/`GPT_OPERATIONS` is still load-bearing infrastructure
for the live system, just no longer for a Custom GPT specifically. What
IS now stale: the 30-op cap itself — it exists only because "Custom GPT
Actions reject specs with more than 30 operations" (see below), a
constraint that no longer applies to anything now that the Custom GPT is
retired and rbb_chat_tools.py already proved out an unlimited-tools path
via `_EXTRA_RBB_CHAT_ONLY_TOOLS`. Not yet resolved: whether to keep this
two-layer structure (curated 30 + bolted-on extras) or collapse it into a
single, uncapped tool set built straight from the full `openapi.yaml` --
a real simplification opportunity, not urgent, flagged but not done.
`validate_kb_consistency.py`'s own "live schema" check was repointed to
`rbb_chat_tools.py`'s actual built TOOLS list as of 2026-08-28 (not
`GPT_OPERATIONS` directly) so it reflects the real live surface either way.
If Todd scopes the "unidirectional
read-only GPT query engine" idea logged in ROADMAP.md, that will likely
want its own new, deliberately narrow schema — not a revival of this one.

Background (historical, pre-2026-08-28)
----------------------------------------
Custom GPT Actions reject specs with more than 30 operations. The full
internal API surface in `system/api/openapi.yaml` is larger than that, so
the GPT consumes a curated subset at `system/api/openapi_gpt.yaml`. The
subset must stay in sync with the full schema, prioritize the operations
the GPT actually relies on, and pass a small set of structural checks.

This script does both jobs:

  1. Regenerates `openapi_gpt.yaml` from `openapi.yaml` by filtering to
     the GPT allowlist below. Run with `--write` to overwrite the file.
  2. Validates the existing `openapi_gpt.yaml` against the rules in
     CLAUDE_HANDOFF_2026-05-19.md:
        - OpenAPI parses.
        - <=30 operations.
        - Required RB interface operations present.
        - servers[0].url set (non-empty and not the example placeholder).
        - No duplicate operationIds.
        - Every operationId also exists in the full schema (no drift).

Usage:
    python3 system/scripts/validate_openapi_gpt.py            # validate only
    python3 system/scripts/validate_openapi_gpt.py --write    # regenerate then validate
    python3 system/scripts/validate_openapi_gpt.py --diff     # show ops in/out

Exit codes:
    0 — all checks pass
    1 — at least one check failed
    2 — bad arguments / I/O error
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:
    sys.stderr.write(
        "PyYAML required. Install with: pip install pyyaml --break-system-packages\n"
    )
    sys.exit(2)

SYSTEM_DIR = Path(__file__).resolve().parent.parent
FULL_PATH = SYSTEM_DIR / "api" / "openapi.yaml"
GPT_PATH = SYSTEM_DIR / "api" / "openapi_gpt.yaml"
INSTRUCTIONS_PATH = SYSTEM_DIR / "api" / "custom_gpt_instructions_compact_8k.md"
GPT_SERVER_URL = "https://rb-api.bridgepointops.org"

# Operations the Custom GPT depends on. Curated per RB Sprint 9.17.
# Budget: ≤30 ops for Custom GPT Actions.
#
# Removed from prior list (moved to internal-only):
#   processLinkedInSignal   — LinkedIn data pipeline, not operator surface
#   getSocialOutboundOverlay — redundant with relationship signals
#   getSocialOverlay         — covered by getRelationshipSignals / daily brief
#   getNetworkGap            — covered by getNetworkAnalysis
#   getDrrScore              — useful metric but not critical operator action
#
# Added (RB 9.16-RB 9.25 high-value endpoints):
#   getWatchList, evaluatePassiveIntelligence, getMicroGraphIndex,
#   listArtifacts, getArtifact, getEntitySignals, triageInput
#
# RB 9.42 — getMicroGraphIndex swapped for queryContacts (relationship
#   aggregated query surface — supersedes getMicroGraphIndex which is now
#   covered by active_knowledge_assets in the daily brief bootstrap).
# Re-added:
#   ingestLinkedInExport — known uploaded LinkedIn ZIPs are canonical RI baseline
#   enhancement artifacts and must not dead-end after classification.
# Sprint E-4 — getIntelligence added (gathered intelligence store).
# RB 9.27 query layer — adds module-specific read endpoints so the GPT can
# pull intelligence back out after pushing it in. Retired from GPT subset:
# getRelationshipSignals, getWatchList, findIntro, addContact, saveTestTrace.
# Routes remain internal.
#
# RB 9.55-9.60 (live drift) — getLinkedInProfileBookmarklet, ingestLinkedInProfile,
# getJobIntelligence, toggleJobSearch, getIntelligenceCollection,
# getIntelligenceCollectionAudit, getSmsExemptHandles, addSmsExemptHandle,
# removeSmsExemptHandle were added directly to openapi_gpt.yaml without updating
# this allowlist, pushing the live spec to 29/30.
#
# DEFECT-009/011/012 reconciliation (RB 9.6x) — the live Instructions
# (custom_gpt_instructions_compact_8k.md) reference triageInput,
# manualRelationshipIntake, getThesisConvergence, and queryContacts, none of
# which were present in the GPT spec. To fit these 4 within the 30-op cap,
# retire the 8 ops above that have zero references in any GPT-facing
# instructions/knowledge file and zero/near-zero request.log usage:
#   ingestLinkedInProfile  — bookmarklet POSTs to this directly from the
#     browser (server.py getLinkedInProfileBookmarklet), bypassing GPT Actions.
#   getJobIntelligence, toggleJobSearch, getIntelligenceCollection,
#   getIntelligenceCollectionAudit, getSmsExemptHandles, addSmsExemptHandle,
#   removeSmsExemptHandle — never referenced in any instructions/prompt file.
# getLinkedInProfileBookmarklet itself IS referenced live and is kept.
# Net: 29 - 8 + 4 = 25.
#
# RB 9.90 (RB-DEFECT-046 Slice 3) — added getCompanyIntelligenceFile (29 -> 30,
# at the cap). Enrich-before-comment: the GPT must call this before commenting
# on a company's tech-stack/vendor moves so new signals are correlated against
# the persisted strategic narrative instead of summarized in isolation.
#
# 2026-07-01 reconciliation — this list had drifted from the live
# openapi_gpt.yaml for some time: getCapture, getCapturesPending,
# getRenderedDailyBrief, getRenderedIntelligenceBrief, ingestContent,
# processAllCaptures, and submitCapture were added directly to the live
# file without ever being added here (so a `--write` run would have
# silently dropped them — getRenderedDailyBrief/getRenderedIntelligenceBrief
# are the primary brief-delivery path per custom_gpt_instructions_8k.md, and
# ingestContent is the single most-called op in request.log). triageInput
# is gone from the live 30 — superseded by ingestContent, which folds
# triageInput + getEntitySignals into one call (see test_ingest_api.py).
# This list now matches the live file exactly, so `--write` is a no-op
# going forward until someone deliberately changes it.
#
# 2026-08-25 — RB cockpit architecture gate 4 (live read endpoint). Added
# getCockpitContext (GET /cockpit/context — wraps cockpit_context.build_context(),
# the same generator behind system/cockpit/context.json, called live instead of
# reading a snapshot). Retired getRenderedIntelligenceBrief to hold the 30-op
# cap: checked request.log (0 calls, ~3 months) AND grepped
# custom_gpt_instructions_compact_8k.md (0 references) before cutting — the
# real zero-usage-and-zero-reference bar, not usage alone. That check also
# surfaced something bigger, left unresolved: touchContact,
# manualRelationshipIntake, getCapturesPending, submitCapture, confirmProposal,
# and ingestExecutiveDeclaration are all explicitly instructed with "call X
# when Y" triggers (getCapturesPending's trigger fires on every brief) yet
# every one of them shows zero calls in the same 3-month log — the same
# reported-but-never-fired failure class already documented for closeLoop and
# (ironically) ingestExecutiveDeclaration's original 2026-07-09 fix. Not
# retired here because "load-bearing per instructions but never actually
# called" is a suspected bug to investigate, not evidence of low value.
#
# 2026-08-25 (same day, later pass) — full KB audit against the live schema
# found a second, more serious instance of the SAME class of bug this comment
# already describes above, this time on the READ side: getDailyBriefPart2 —
# the "brief"/Document-2 delivery call, referenced in EVERY KB file including
# the always-on compact_8k instructions — was not in this allowlist or the
# live openapi_gpt.yaml at all, despite a real, working server route
# (GET /daily_brief/part2, with its own HEAD handler built specifically
# because "ChatGPT sends HEAD to verify endpoint before GET" -- meaning this
# was reachable from the GPT at some point in the past). request.log shows
# only 5 calls ever, all at ~05:0x local time matching the pipeline's own
# 5am schedule, not organic daytime chat usage -- strong evidence the live
# GPT itself has never successfully completed the "brief" flow's second call.
# Swapped in for getRenderedDailyBrief (same retirement bar as
# getRenderedIntelligenceBrief above: 0 calls ever, and this session's KB
# sweep rewrote every routing reference away from it too, so it's now
# genuinely zero-usage-and-zero-reference, not just zero-usage). This
# validator's check #8 (RB-DEFECT-061 pattern) does not catch this class of
# bug -- it only verifies every WRITE-tagged op has a documented trigger
# phrase, not that every op an instructions file NAMES actually exists in
# the schema. Worth adding that as a real check #9 at some point.
#
# Same pass — swapped 2 ops to add identity-match confirmation actions
# (RB: baseline contacts with no email on file get matched to inbound
# senders by exact name; the GPT needs to be able to confirm/reject so the
# match isn't silently auto-merged). Retired addLoop and openThread: zero
# calls in request.log AND zero references in any GPT-facing instructions/
# playbook file (unlike e.g. resolveLinkedInProfile, which is also
# zero-call today but is load-bearing per custom_gpt_instructions_8k.md).
GPT_OPERATIONS = [
    "getDailyBrief",
    "getPublicBootstrapStatus",
    "getDraftActions",
    "getLoops",
    "closeLoop",
    "getCard",
    "resolveLinkedInProfile",
    "listActiveThreads",
    # RB 10.x: queryEngine is the unified dispatch surface. getMicroGraphSummary,
    # getEcosystemGraphQuery, queryContacts, getEntitySignals, getThesisConvergence,
    # getCompanyIntelligenceFile retired — all dispatched internally by queryEngine.
    # Net: 30 → 24, opening 6 slots.
    "queryEngine",
    "listArtifacts",
    "getArtifact",
    "uploadAndIngestFile",
    "refreshSources",
    "touchContact",
    "closeThread",
    "getLinkedInProfileBookmarklet",
    "manualRelationshipIntake",
    # DEFECT-011/012 reconciliation (RB 9.6x): originally added so triageInput's
    # processing_order (ri_event -> /relationship/intake, macro_signal ->
    # /macro/signal) wouldn't dead-end. triageInput itself was later superseded
    # by ingestContent (below), but these two routing targets are still live.
    "processRelationshipIntake",
    "processMacroSignal",
    # RB-DEFECT-037 (RB 9.6x): Active Opportunity Pipeline routing targets.
    "processOpportunityUpdate",
    "getOpportunityPipeline",
    # Live-drift ops folded in during the 2026-07-01 reconciliation (see above).
    "getCapture",
    "getCapturesPending",
    "getDailyBriefPart2",
    "getCockpitContext",
    "ingestContent",
    "processAllCaptures",
    "submitCapture",
    # 2026-07-06 — confirmIdentityMatch + rejectIdentityMatch (added 2026-07-01)
    # retired in favor of confirmProposal, a single generic confirm/reject
    # action covering identity matches AND four other "proposed, awaiting
    # operator decision" surfaces that had NO GPT action at all before this:
    # relationship interactions (confirmRelationshipInteraction), strategic
    # insights (confirmInsight), macro behavioral records (confirmMacroRecord),
    # and macro entity risk (confirmMacroEntity). Trigger: Todd tried to
    # reject an identity match from the live GPT and it failed — fixing the
    # rendering (naming the operationId instead of a raw HTTP path) surfaced
    # that the *relationship* confirm/reject flow wasn't reachable from the
    # GPT at all, with no operation cleanly meeting the zero-usage-and-
    # zero-reference retirement bar to free a slot. Net: 30 ops → 29 (one
    # op reclaimed) with five confirm/reject surfaces now covered instead
    # of one. The per-kind endpoints (confirmIdentityMatch, etc.) still
    # exist in openapi.yaml/server.py for direct API callers — only the
    # GPT-facing curated subset changed.
    "confirmProposal",
    # RB-DEFECT-2026-07-09: ingestExecutiveDeclaration was referenced by
    # custom_gpt_instructions_compact_8k.md's CEO-declaration routing rule
    # (RB-DEFECT-062, 2026-07-03) but was never actually added to this
    # allowlist or the curated openapi_gpt.yaml -- the instructions told the
    # model to call an action it was never given as a tool. Confirmed via
    # request.log: zero calls to /ingest/executive_declaration, ever. This
    # is the root cause of Life Lens never updating (0/N "not logged yet"
    # every day) despite the user reporting prayer/devotions/exercise/
    # quality-time regularly in chat -- not a model instruction-following
    # failure, a missing-tool failure. Net: 29 -> 30 (at the cap, no
    # retirement needed).
    "ingestExecutiveDeclaration",
]

# Operations that MUST be present for the GPT to function as the CoS
# operator surface. A subset of GPT_OPERATIONS — used by the validator so a
# typo doesn't silently drop a critical action.
REQUIRED_OPERATIONS = {
    "getDailyBrief",
    "getPublicBootstrapStatus",  # replaced getBriefHealth — public diagnostic, no-auth
    "getDraftActions",
    "getCard",
    "getLoops",
    "closeLoop",
    "touchContact",
    "uploadAndIngestFile",
    "queryEngine",
    "ingestExecutiveDeclaration",  # Life Lens / CEO-declaration write path — see RB-DEFECT-2026-07-09
}

MAX_OPS = 30
MAX_SUMMARY_LEN = 300
MAX_DESCRIPTION_LEN = 300


def _trim_text(value: str, limit: int) -> str:
    """Trim builder-facing text fields without cutting words raggedly."""
    if len(value) <= limit:
        return value
    trimmed = value[: max(0, limit - 1)].rstrip()
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0]
    return trimmed.rstrip(".,;:") + "…"


def _apply_api_key_security(spec: dict) -> None:
    """Declare x-api-key as Action auth, not a model-filled parameter."""
    components = spec.setdefault("components", {})
    security_schemes = components.setdefault("securitySchemes", {})
    security_schemes["ApiKeyAuth"] = {
        "type": "apiKey",
        "in": "header",
        "name": "x-api-key",
    }
    spec["security"] = [{"ApiKeyAuth": []}]

    for _, _, op in _iter_operations(spec):
        params = op.get("parameters")
        if not isinstance(params, list):
            continue
        op["parameters"] = [
            p for p in params
            if not (
                isinstance(p, dict)
                and p.get("name") == "x-api-key"
                and p.get("in") == "header"
            )
        ]


def _prune_unused_components(spec: dict) -> None:
    """Keep only component schemas reachable from the curated action paths."""
    schemas = ((spec.get("components") or {}).get("schemas") or {})
    if not isinstance(schemas, dict):
        return

    prefix = "#/components/schemas/"
    required: set[str] = set()

    def collect(value: object) -> None:
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and ref.startswith(prefix):
                name = ref[len(prefix):]
                if name not in required:
                    required.add(name)
                    collect(schemas.get(name))
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(spec.get("paths") or {})
    spec.setdefault("components", {})["schemas"] = {
        name: schemas[name] for name in schemas if name in required
    }


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _iter_operations(spec: dict):
    """Yield (path, method, op_dict) for every operation in the spec."""
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if not isinstance(op, dict):
                continue
            if "operationId" not in op:
                continue
            yield path, method, op


def build_gpt_spec(full_spec: dict, ops_allowlist: list[str]) -> dict:
    """Return a new spec dict with paths filtered to ops in `ops_allowlist`."""
    keep = set(ops_allowlist)
    new_spec = copy.deepcopy(full_spec)
    new_paths: dict = {}
    for path, item in (full_spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        kept_methods = {}
        for method, op in item.items():
            if isinstance(op, dict) and op.get("operationId") in keep:
                op_copy = copy.deepcopy(op)
                for field, limit in (
                    ("summary", MAX_SUMMARY_LEN),
                    ("description", MAX_DESCRIPTION_LEN),
                ):
                    value = op_copy.get(field)
                    if isinstance(value, str):
                        op_copy[field] = _trim_text(value, limit)
                kept_methods[method] = op_copy
            elif not isinstance(op, dict):
                # Preserve non-operation keys (parameters, summary at path level)
                kept_methods[method] = copy.deepcopy(op)
        if any(isinstance(v, dict) and "operationId" in v for v in kept_methods.values()):
            new_paths[path] = kept_methods
    new_spec["paths"] = new_paths
    new_spec["servers"] = [{
        "url": GPT_SERVER_URL,
        "description": "RB API tunnel",
    }]
    # Mark the spec as the curated variant
    info = new_spec.setdefault("info", {})
    if "Curated Custom GPT subset" not in (info.get("description") or ""):
        info["description"] = (
            (info.get("description") or "").rstrip()
            + "\n\nCurated Custom GPT subset — generated by validate_openapi_gpt.py."
        )
    _apply_api_key_security(new_spec)
    _prune_unused_components(new_spec)
    return new_spec


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------

def validate(
    spec: dict,
    full_spec: dict | None = None,
    instructions_text: str | None = None,
) -> list[str]:
    """Return a list of failure messages. Empty list means OK."""
    failures: list[str] = []
    ops = list(_iter_operations(spec))
    op_ids = [op["operationId"] for _, _, op in ops]
    # 1. Operation count
    if len(ops) > MAX_OPS:
        failures.append(
            f"too many operations: {len(ops)} (max {MAX_OPS} for Custom GPT)"
        )
    # 2. Required operations present
    missing = REQUIRED_OPERATIONS - set(op_ids)
    if missing:
        failures.append(
            f"missing required operations: {sorted(missing)}"
        )
    # 3. Server URL set and not a placeholder
    servers = spec.get("servers") or []
    if not servers:
        failures.append("servers[] is empty")
    else:
        url = (servers[0] or {}).get("url") or ""
        if not url:
            failures.append("servers[0].url is empty")
        elif "example.com" in url or "<set me>" in url or "REPLACE" in url.upper():
            failures.append(f"servers[0].url looks like a placeholder: {url!r}")
    # 4. Duplicate operationIds
    dupes = sorted({oid for oid in op_ids if op_ids.count(oid) > 1})
    if dupes:
        failures.append(f"duplicate operationIds: {dupes}")
    # 5. Drift check against full schema
    if full_spec is not None:
        full_ids = {op["operationId"] for _, _, op in _iter_operations(full_spec)}
        drift = sorted(set(op_ids) - full_ids)
        if drift:
            failures.append(
                f"GPT ops missing from full openapi.yaml (drift): {drift}"
            )
    # 6. Custom GPT Builder enforces 300-character operation text limits.
    for path, method, op in ops:
        summary = op.get("summary")
        if isinstance(summary, str) and len(summary) > MAX_SUMMARY_LEN:
            failures.append(
                f"{op['operationId']} summary too long at {method.upper()} {path}: "
                f"{len(summary)} chars (max {MAX_SUMMARY_LEN})"
            )
        description = op.get("description")
        if isinstance(description, str) and len(description) > MAX_DESCRIPTION_LEN:
            failures.append(
                f"{op['operationId']} description too long at {method.upper()} {path}: "
                f"{len(description)} chars (max {MAX_DESCRIPTION_LEN})"
            )
    # 7. Custom GPT auth must be declared as an apiKey security scheme.
    security_schemes = ((spec.get("components") or {}).get("securitySchemes") or {})
    api_key = security_schemes.get("ApiKeyAuth") or {}
    if api_key.get("type") != "apiKey" or api_key.get("in") != "header" or api_key.get("name") != "x-api-key":
        failures.append("missing ApiKeyAuth security scheme for x-api-key header")
    if {"ApiKeyAuth": []} not in (spec.get("security") or []):
        failures.append("missing global ApiKeyAuth security requirement")
    for path, method, op in ops:
        for param in op.get("parameters") or []:
            if isinstance(param, dict) and param.get("name") == "x-api-key" and param.get("in") == "header":
                failures.append(
                    f"{op['operationId']} exposes x-api-key as a model-filled parameter at "
                    f"{method.upper()} {path}; use ApiKeyAuth security instead"
                )
    # 8. RB-DEFECT-061: every write-tagged operation must have a documented trigger phrase in the
    # GPT instructions. Without one, the model has no instructed path to call the tool and can
    # fabricate a success receipt instead (confirmed live: closeLoop, 3 loops, zero API calls).
    if instructions_text is not None:
        unrouted = sorted(
            op["operationId"] for _, _, op in ops
            if "write" in (op.get("tags") or []) and op["operationId"] not in instructions_text
        )
        if unrouted:
            failures.append(
                f"write operations with no documented GPT routing (RB-DEFECT-061 class): {unrouted} — "
                f"add a trigger phrase + receipt format to {INSTRUCTIONS_PATH.name}"
            )
    return failures


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--write", action="store_true",
        help="Rebuild openapi_gpt.yaml from openapi.yaml + allowlist."
    )
    p.add_argument(
        "--diff", action="store_true",
        help="Print which operationIds are in / out of the GPT subset."
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON report instead of text."
    )
    args = p.parse_args()

    if not FULL_PATH.exists():
        sys.stderr.write(f"ERROR: full schema not found: {FULL_PATH}\n")
        return 2
    full_spec = yaml.safe_load(FULL_PATH.read_text())

    if args.write:
        gpt_spec = build_gpt_spec(full_spec, GPT_OPERATIONS)
        GPT_PATH.write_text(
            yaml.safe_dump(gpt_spec, sort_keys=False, width=120) + ""
        )
        print(f"wrote {GPT_PATH.relative_to(SYSTEM_DIR.parent)}")

    if not GPT_PATH.exists():
        sys.stderr.write(
            f"ERROR: {GPT_PATH} does not exist. Run with --write to build it.\n"
        )
        return 1
    gpt_spec = yaml.safe_load(GPT_PATH.read_text())
    instructions_text = INSTRUCTIONS_PATH.read_text() if INSTRUCTIONS_PATH.exists() else None
    failures = validate(gpt_spec, full_spec=full_spec, instructions_text=instructions_text)

    if args.diff:
        full_ids = {op["operationId"] for _, _, op in _iter_operations(full_spec)}
        gpt_ids = {op["operationId"] for _, _, op in _iter_operations(gpt_spec)}
        in_gpt = sorted(gpt_ids)
        out_gpt = sorted(full_ids - gpt_ids)
        print(f"\nIn GPT subset ({len(in_gpt)}):")
        for o in in_gpt:
            print(f"  + {o}")
        print(f"\nExcluded from GPT subset ({len(out_gpt)}):")
        for o in out_gpt:
            print(f"  - {o}")

    if args.json:
        ops = list(_iter_operations(gpt_spec))
        print(json.dumps({
            "ok": not failures,
            "operation_count": len(ops),
            "operations": [o["operationId"] for _, _, o in ops],
            "failures": failures,
        }, indent=2))
        return 0 if not failures else 1

    if failures:
        print("validate_openapi_gpt: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(
        f"validate_openapi_gpt: OK "
        f"({sum(1 for _ in _iter_operations(gpt_spec))} ops)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
