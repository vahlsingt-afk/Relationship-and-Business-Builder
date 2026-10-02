"""
test_rbb_chat_skills.py — RBB_SKILLS_ARCHITECTURE_SCOPE_2026-09-23 spike.

The relationship_family bundle is the pilot: prove that a message NOT
mentioning relationship-side artifacts gets a smaller payload (bundle
tools/instructions excluded), a message that DOES mention them gets the
full payload (bundle included), and every tool NOT assigned to any bundle
is always present either way -- this mechanism can only ever narrow the
bundled slice, never silently drop unrelated capability.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "system" / "scripts"))
sys.path.insert(0, str(ROOT / "system" / "api"))

os.environ.setdefault("OPENAI_API_KEY", "test-key-not-real")

import rbb_chat_skills as sk  # noqa: E402
import rbb_chat_tools as rct  # noqa: E402

ALL_TOOLS, _ = rct.build_tools_and_operations()
ALL_NAMES = {t["name"] for t in ALL_TOOLS}
BUNDLE_NAMES = sk.RELATIONSHIP_FAMILY_TOOLS


def test_bundle_tools_are_real_live_tools():
    """The categorization isn't stale -- every name in the bundle must
    actually exist in the real, current tool schema."""
    missing = BUNDLE_NAMES - ALL_NAMES
    assert not missing, f"bundle references tools no longer in TOOLS: {missing}"


def test_unmatched_message_excludes_bundle_tools_only():
    instructions, tools, matched = sk.build_turn_payload("what's on my calendar today", "CORE", ALL_TOOLS)
    assert matched == set()
    tool_names = {t["name"] for t in tools}
    assert tool_names & BUNDLE_NAMES == set()
    # Everything NOT in any bundle must still be fully present.
    assert (ALL_NAMES - BUNDLE_NAMES) <= tool_names
    assert instructions == "CORE"  # nothing appended


def test_matched_message_includes_full_bundle():
    instructions, tools, matched = sk.build_turn_payload(
        "publish the relationship card for Sal Nazir", "CORE", ALL_TOOLS,
    )
    assert matched == {"relationship_family"}
    tool_names = {t["name"] for t in tools}
    assert BUNDLE_NAMES <= tool_names
    assert tool_names == ALL_NAMES  # full set restored, nothing lost
    assert "CORE" in instructions
    assert "Relationship Card" in instructions  # fragment actually appended


def test_never_drops_a_non_bundled_tool_either_way():
    """The mechanism can only EXCLUDE bundled tools on a miss -- it must
    never touch anything outside the bundle, matched or not."""
    for message in ("random unrelated message", "publish my relationship plan"):
        _, tools, _ = sk.build_turn_payload(message, "CORE", ALL_TOOLS)
        tool_names = {t["name"] for t in tools}
        assert (ALL_NAMES - BUNDLE_NAMES) <= tool_names


def test_generous_matching_covers_plausible_real_phrasings():
    """Deliberately generous per the design constraint (tool_choice=
    'required' means a miss forces a wrong tool call, not a plain refusal)
    -- these are realistic ways Todd might actually ask, not just the
    artifact's own formal name."""
    real_phrasings = [
        "can you publish an updated relationship card for Sal",
        "give me my inner circle roster",
        "build my inner circle",
        "who's a good referral path to the CTO at Toast",
        "find me an intro to someone at Chipotle",
        "set a relationship goal for Ryan Hildebrand",
        "save a relationship plan for this contact",
        "give me a link to download my referral network overview",
    ]
    for message in real_phrasings:
        matched = sk.match_skills(message)
        assert "relationship_family" in matched, f"missed real phrasing: {message!r}"


def test_unrelated_real_phrasings_do_not_false_positive():
    unrelated = [
        "what's the status of the Subway account",
        "close the loop with Mike Schwartz",
        "give me today's intelligence brief",
        "what's Wingstop's POS vendor",
        "log that I heard back from Jeff",
    ]
    for message in unrelated:
        assert sk.match_skills(message) == set(), f"false positive on: {message!r}"


def test_skill_fragment_file_exists_and_mentions_every_bundled_tool():
    """The fragment is the ONLY place these 16 tools are documented once
    they're out of the core file -- if one is missing here, the model has
    zero routing guidance for it even when the bundle correctly loads."""
    path = sk.SKILL_BUNDLES["relationship_family"]["instructions_path"]
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    missing = [name for name in BUNDLE_NAMES if name not in text]
    assert not missing, f"fragment never mentions: {missing}"


def test_core_instructions_no_longer_carry_the_bundled_bullets():
    """Regression guard on the actual point of the spike -- the core file
    must be smaller, not just have a fragment added alongside it."""
    core_path = sk.API_DIR / "custom_gpt_instructions_compact_8k.md"
    core_text = core_path.read_text(encoding="utf-8")
    # These are real, specific strings from the extracted bullets -- if any
    # reappear in core, the split didn't actually happen.
    assert "createRelationshipCard" not in core_text
    assert "createInnerCircle" not in core_text
    assert "createReferralNetworkOverview" not in core_text
    assert "createRelationshipPlan" not in core_text


def test_matched_skills_set_is_reused_not_recomputed_per_call():
    """build_turn_payload is the single source of truth for a turn -- two
    calls with the same message must agree (pure function, no hidden
    per-call state drift)."""
    a = sk.build_turn_payload("publish my relationship card", "CORE", ALL_TOOLS)
    b = sk.build_turn_payload("publish my relationship card", "CORE", ALL_TOOLS)
    assert a[2] == b[2]
    assert {t["name"] for t in a[1]} == {t["name"] for t in b[1]}


def test_matched_skills_flow_into_the_real_usage_log(tmp_path):
    """The spike's whole point is measuring a real token delta -- verify
    _log_usage actually persists matched_skills, not just accepts the
    kwarg silently."""
    import json as _json
    import types
    import unittest.mock as mock

    import rbb_chat as chat

    fake_resp = types.SimpleNamespace(
        usage=types.SimpleNamespace(input_tokens=100, output_tokens=20, input_tokens_details=None),
        model="gpt-5.5", id="resp_test",
    )
    log_path = tmp_path / "usage.jsonl"
    with mock.patch.object(chat, "_USAGE_LOG_PATH", log_path):
        chat._log_usage("initial_fresh", fake_resp, "conv-1", caller="user",
                         extra={"matched_skills": ["relationship_family"]})
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = _json.loads(lines[0])
    assert record["matched_skills"] == ["relationship_family"]
    assert record["input_tokens"] == 100
