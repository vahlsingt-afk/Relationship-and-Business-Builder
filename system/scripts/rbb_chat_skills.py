#!/usr/bin/env python3
"""
rbb_chat_skills.py — contextual skill-bundle loading for rbb-chat's per-turn
tools/instructions payload (RBB_SKILLS_ARCHITECTURE_SCOPE_2026-09-23.md).

Spike scope: proves the mechanism on ONE bundle, relationship_family,
before deciding whether to build the other five named in the scope doc
(competitive_intel, account_plan_family, confirm_review_queues,
blue_sheet_account_status, master_account_plan). relationship_family was
picked because it has zero real tool calls ever (system/audit/*.jsonl,
confirmed during the Phase 2 usage-data analysis) -- misclassifying it away
from a turn that needed it carries zero risk of regressing an in-flight
workflow, the safest possible bundle to pilot this on.

Design constraint that shapes everything here: rbb_chat.py calls
client.responses.create(..., tool_choice="required") -- the model is NEVER
allowed to just reply in plain text, it must call some tool. That means
under-matching (a turn that needed this bundle but didn't get it) forces
the model toward the closest WRONG tool instead of a plain "I can't do
that" -- a real, already-documented failure class in this codebase (the
submitCapture-with-a-guessed-file_id incident, RB-2026-08-28, named
directly in the core instructions). So matching here is deliberately
generous: any plausible signal includes the bundle, never tuned for
precision. The cost of a false positive is a few thousand extra tokens;
the cost of a false negative is a forced, potentially fabricated tool call.

Deterministic, no model call -- same "literal trigger beats a probabilistic
classifier" reasoning the Phase 1 capture fast path already established
(RBB_TOKEN_EFFICIENT_ARCHITECTURE_SCOPE_2026-09-19.md), extended from a
single prefix match to a small per-bundle keyword list.
"""
from __future__ import annotations

from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent / "api"

RELATIONSHIP_FAMILY_TOOLS = frozenset({
    "createRelationshipCard", "getRelationshipCard", "getRelationshipCardDownloadLink",
    "createInnerCircle", "getInnerCircle", "getInnerCircleDownloadLink",
    "findIntro",
    "createReferralNetworkOverview", "getReferralNetworkOverview", "getReferralNetworkOverviewDownloadLink",
    "createReferralNetworkAnalysis", "getReferralNetworkAnalysis", "getReferralNetworkAnalysisDownloadLink",
    "createRelationshipPlan", "getRelationshipPlan", "getRelationshipPlanDownloadLink",
})

# Deliberately generous (see module docstring): plain-language phrasing a
# real user would actually type, not just the tool/artifact names
# themselves -- a real request almost never says "createRelationshipCard"
# verbatim. Reviewed by hand against the 5 real artifact-family bullets
# this bundle covers; re-review this list, don't just grow it silently, if
# a real miss shows up once this is live.
_RELATIONSHIP_FAMILY_TRIGGERS = (
    "relationship card", "publish the card", "publish my card", "version the card",
    "inner circle",
    "referral network", "referral path", "referral overview",
    "find intro", "find an intro", "find me an intro", "intro path", "intro broker", "broker path",
    "relationship plan", "relationship goal",
)

SKILL_BUNDLES = {
    "relationship_family": {
        "tool_names": RELATIONSHIP_FAMILY_TOOLS,
        "instructions_path": API_DIR / "custom_gpt_skill_relationship_family.md",
        "triggers": _RELATIONSHIP_FAMILY_TRIGGERS,
    },
}

# Every tool assigned to a bundle -- the only tools this module can ever
# exclude from a turn's payload. Any tool not in this set is treated as
# core: always included, regardless of match state. A future bundle
# addition is opt-in per tool (add it to a bundle's tool_names), never a
# silent removal of something nobody explicitly bundled.
_ALL_BUNDLED_TOOL_NAMES: frozenset = frozenset().union(
    *(bundle["tool_names"] for bundle in SKILL_BUNDLES.values())
) if SKILL_BUNDLES else frozenset()

_INSTRUCTIONS_CACHE: dict[str, str] = {}


def match_skills(message: str) -> set[str]:
    """Deterministic, no model call. Returns the set of bundle names whose
    trigger phrases appear anywhere in `message` (case-insensitive
    substring match -- simple and fully inspectable on purpose)."""
    text = (message or "").lower()
    return {
        bundle_name for bundle_name, bundle in SKILL_BUNDLES.items()
        if any(trigger in text for trigger in bundle["triggers"])
    }


def _load_bundle_instructions(bundle_name: str) -> str:
    if bundle_name not in _INSTRUCTIONS_CACHE:
        path = SKILL_BUNDLES[bundle_name]["instructions_path"]
        _INSTRUCTIONS_CACHE[bundle_name] = path.read_text(encoding="utf-8") if path.exists() else ""
    return _INSTRUCTIONS_CACHE[bundle_name]


def build_turn_payload(
    message: str, core_instructions: str, all_tools: list[dict],
) -> tuple[str, list[dict], set[str]]:
    """The one function rbb_chat.py calls, once per turn. Returns
    (instructions, tools, matched_skill_names).

    instructions: core_instructions with each matched bundle's fragment
    appended (nothing removed from core -- a bundle can only ADD content).

    tools: all_tools filtered so a tool that belongs to a bundle is
    included only if that bundle matched; every tool NOT assigned to any
    bundle always passes through untouched.
    """
    matched = match_skills(message)

    instructions = core_instructions
    for bundle_name in matched:
        fragment = _load_bundle_instructions(bundle_name)
        if fragment:
            instructions = instructions + "\n\n" + fragment

    matched_tool_names: set[str] = set()
    for bundle_name in matched:
        matched_tool_names |= SKILL_BUNDLES[bundle_name]["tool_names"]

    tools = [
        t for t in all_tools
        if t["name"] not in _ALL_BUNDLED_TOOL_NAMES or t["name"] in matched_tool_names
    ]
    return instructions, tools, matched
