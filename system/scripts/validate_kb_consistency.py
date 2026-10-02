#!/usr/bin/env python3
"""
validate_kb_consistency.py — automated version of the checks this session
did by hand, repeatedly, to catch KB documentation drift from live reality.

Built 2026-08-25 after a single KB audit session found, all by manual grep:
  - getDailyBriefPart2 missing from the live Actions schema entirely, despite
    every KB file naming it as the required "brief" delivery path.
  - getRenderedIntelligenceBrief/getRenderedDailyBrief mandated by 3 KB files
    for months after being retired from the schema (0 calls, ever).
  - getDraftActions live and working, zero KB routing, for 7+ weeks.
  - what_rb_found_without_you_telling_it computed and shipped in the API
    payload, never implemented by the renderer that produces the actual
    displayed brief.
  - DAILY_BRIEF_CANONICAL_TEMPLATE.md's own header calling itself deprecated
    for 7+ weeks while every other file correctly cited it as authoritative.

None of these were caught by anything automated — only by a human (or an
agent) happening to read carefully. This script is the automated version of
checks 1-3 below. Checks 4-5 are heuristic flags for a human to resolve, not
hard failures — the underlying judgment (is this field genuinely missing, or
intentionally synthesis-only?) isn't mechanically decidable.

CLI:
    python3 validate_kb_consistency.py            # run all checks, text report
    python3 validate_kb_consistency.py --json      # machine-readable
    python3 validate_kb_consistency.py --strict     # non-zero exit on ANY finding, incl. heuristic ones
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import validate_openapi_gpt as vgpt  # noqa: E402
import rbb_chat_tools  # noqa: E402

SYSTEM_DIR = SCRIPTS_DIR.parent
API_DIR = SYSTEM_DIR / "api"


def _live_ops() -> set[str]:
    """The real, currently-callable op set. 2026-08-28: repointed from
    vgpt.GPT_OPERATIONS (the 30-op-capped Custom GPT Actions allowlist) to
    rbb_chat_tools.py's actual built TOOLS list, now that the Custom GPT is
    retired and the Trusted Chat Client (rbb_chat.py) is the sole live
    conversational surface -- see project_open_decisions_ledger memory item
    13. rbb_chat_tools.build_tools_and_operations() already correctly
    combines both live layers (openapi_gpt.yaml's base ops +
    _EXTRA_RBB_CHAT_ONLY_TOOLS), so this reflects the true live set either
    way, not just one layer of it."""
    tools, _operations = rbb_chat_tools.build_tools_and_operations()
    return {t["name"] for t in tools}

KB_FILES = [
    API_DIR / "CANONICAL_RESPONSE_CONTRACT.md",
    API_DIR / "custom_gpt_instructions_8k.md",
    API_DIR / "custom_gpt_prompt.md",
    API_DIR / "custom_gpt_operational_playbook.md",
    API_DIR / "DAILY_BRIEF_CANONICAL_TEMPLATE.md",
    API_DIR / "INTELLIGENCE_BRIEF_CANONICAL.md",
    API_DIR / "custom_gpt_instructions_compact_8k.md",
    # RB-DEFECT-072 follow-on (2026-09-23 skills spike): a tool documented
    # only in a conditionally-loaded skill fragment (rbb_chat_skills.py) is
    # still routed -- it's just not ALWAYS in context. Included here so
    # check_unrouted_live_ops doesn't flag the relationship_family tools as
    # undocumented just because their bullets moved out of the always-on
    # core file.
    API_DIR / "custom_gpt_skill_relationship_family.md",
]

# Ops that are legitimately fine to leave undocumented in prose (diagnostic
# infrastructure, or working purely off their own OpenAPI description field
# with real, confirmed usage) — reviewed by hand 2026-08-25, not a rule to
# grow casually. Add to this list only after checking request.log usage,
# the same way every entry here was checked.
KNOWN_OK_UNDOCUMENTED = {
    "getPublicBootstrapStatus",  # diagnostic-tagged, not conversational
    "listActiveThreads",          # low usage, likely reachable via queryEngine
    "getOpportunityPipeline",     # 756 real calls off its own schema description alone
    # 2026-08-25: added for the Custom GPT/RBB Project consolidation
    # decision, which landed 2026-08-28 (retire the Custom GPT; see
    # project_open_decisions_ledger memory item 13) -- kept here rather
    # than removed, because the decision landing doesn't by itself answer
    # whether getCockpitContext (built specifically for "a Custom GPT
    # session pulling fresh state") still has a live purpose now that the
    # Trusted Chat Client has its own full tool access, or should be
    # retired/routed. Real follow-up, not yet decided -- don't remove this
    # exception as a side effect of an unrelated change.
    "getCockpitContext",
}

# Ops that are legitimately mentioned in KB prose despite not being in the
# live schema — either (a) correctly-written "X is not GPT-callable, say so,
# never fabricate a receipt" guidance (RB-DEFECT-2026-07-09's fix), or (b)
# this session's own correction notes explaining a *removed* instruction
# ("this corrects a previous version that instructed calling X"). Regex
# cannot reliably tell active instruction from historical/negative mention
# apart — "instructed calling `X`" contains the same "calling `X`" substring
# as a real routing rule. Reviewed by hand 2026-08-25; re-verify, don't just
# grow this list, if a NEW mention of one of these names shows up somewhere
# unreviewed.
KNOWN_OK_STALE_MENTIONS = {
    "classifyArtifact", "enrichArtifact", "getMicroGraphSummary",
    "ingestLinkedInCSV", "ingestLinkedInExport", "processInsight",
    "recordStrategicMemory", "triageInput",
    "getRenderedDailyBrief", "getRenderedIntelligenceBrief",
    # RB-2026-09-22 (Phase 2, RBB_TOKEN_EFFICIENT_ARCHITECTURE_SCOPE_2026-09-19.md):
    # pulled from rbb_chat_tools.py's live TOOLS schema (routing in
    # OPERATIONS is untouched, so internal/fast-path callers still work) --
    # category (a), custom_gpt_instructions_compact_8k.md's own mentions are
    # now correctly-written "you don't have this tool, say so" guidance, not
    # routing instructions. closeThread: confirmed broken by self-audit (live
    # traffic, zero real mutations ever). processAllCaptures: the prompt
    # already said "scheduled-pipeline only, never call this here" before
    # this change -- it was never meant to be model-callable. queueCaptureText:
    # superseded for chat by the Phase 1 capture fast path ("capture: <text>"),
    # which calls the same operation directly with zero model tokens.
    "closeThread", "processAllCaptures", "queueCaptureText",
}

_OP_TOKEN_RE = re.compile(r"`([a-z][a-zA-Z0-9]{3,40})`")


def _kb_text() -> str:
    return "".join(f.read_text(encoding="utf-8") for f in KB_FILES if f.exists())


def check_stale_op_mentions() -> list[str]:
    """Ops named in KB prose that no longer exist in the live schema —
    the getRenderedIntelligenceBrief/getRenderedDailyBrief failure class."""
    text = _kb_text()
    live = _live_ops()
    mentioned = {m.group(1) for m in _OP_TOKEN_RE.finditer(text)
                 if re.match(r"^[a-z]+([A-Z][a-z0-9]*)+$", m.group(1))}
    # Only flag tokens that look like real operationIds elsewhere in the full
    # internal schema, or in the live tool set itself, too (filters out
    # unrelated camelCase noise like field names). Unioned with `live`
    # because openapi.yaml is a reference snapshot, not continuously
    # regenerated (see rbb_chat_tools.py) -- a chat-only extra tool could be
    # real and live without yet appearing there.
    try:
        import yaml
        full_ops = {op["operationId"] for _, _, op in vgpt._iter_operations(
            yaml.safe_load(vgpt.FULL_PATH.read_text())
        ) if "operationId" in op}
    except Exception:
        full_ops = mentioned  # fail open — don't block on a parse error here
    full_ops |= live
    stale = sorted((mentioned & full_ops - live) - KNOWN_OK_STALE_MENTIONS)
    return stale


def check_unrouted_live_ops() -> list[str]:
    """Live ops with zero mentions anywhere in the KB — the getDraftActions/
    getDailyBriefPart2 failure class (the model has the tool, nothing tells
    it when to use it)."""
    text = _kb_text()
    missing = [op for op in sorted(_live_ops())
               if op not in text and op not in KNOWN_OK_UNDOCUMENTED]
    return missing


_SELF_DEPRECATED_RE = re.compile(
    r"^#\s.*\b(DEPRECATED|SUPERSEDED)\b|^\*?\*?Status:?\*?\*?\s*:?\s*(DEPRECATED|SUPERSEDED)",
    re.I | re.M,
)


def check_self_declared_deprecated_but_cited() -> list[str]:
    """A file that calls ITSELF deprecated/superseded — in its own title
    line or Status: line specifically, not just mentioning the word
    somewhere in prose about a different file — while still being cited as
    authoritative by other KB files. The DAILY_BRIEF_CANONICAL_TEMPLATE.md
    incident: its title line said 'DEPRECATED', its Status line said
    'SUPERSEDED', and 6 other files cited it as authoritative anyway, for
    7+ weeks. Only checks the title/Status line, not the full header prose,
    to avoid flagging a correction note that happens to describe some OTHER
    file's deprecated status (a real false positive hit during dev)."""
    findings = []
    for f in KB_FILES:
        if not f.exists():
            continue
        header = "\n".join(f.read_text(encoding="utf-8").splitlines()[:10])
        self_deprecated = bool(_SELF_DEPRECATED_RE.search(header))
        if not self_deprecated:
            continue
        cited_elsewhere = 0
        for other in KB_FILES:
            if other == f or not other.exists():
                continue
            if f.name in other.read_text(encoding="utf-8"):
                cited_elsewhere += 1
        if cited_elsewhere >= 2:
            findings.append(
                f"{f.name}: header says deprecated/superseded, but {cited_elsewhere} "
                f"other KB files still cite it — verify which is true, don't assume the header"
            )
    return findings


def check_authority_citation_conflicts() -> list[str]:
    """Two files both claim to be *the* authority for the same Part —
    the CANONICAL_RESPONSE_CONTRACT.md-vs-TEMPLATE.md conflict, generalized.
    Heuristic: flags when 'authoritative'/'authority' language near a
    filename doesn't match what the majority of other files cite."""
    findings = []
    citation_counts: dict[str, int] = {}
    for f in KB_FILES:
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")
        for other in KB_FILES:
            if other == f or not other.exists():
                continue
            if other.name in text:
                citation_counts[other.name] = citation_counts.get(other.name, 0) + 1
    # Flag any *.md file referenced by exactly one other KB file while a
    # same-topic sibling (heuristic: shares a "DAILY_BRIEF"/"INTELLIGENCE_BRIEF"
    # prefix) is referenced by many more — likely the losing side of an old fork.
    by_prefix: dict[str, list[tuple[str, int]]] = {}
    for name, count in citation_counts.items():
        prefix = name.split("_CANONICAL")[0] if "_CANONICAL" in name else name
        by_prefix.setdefault(prefix, []).append((name, count))
    for prefix, entries in by_prefix.items():
        if len(entries) < 2:
            continue
        entries.sort(key=lambda e: -e[1])
        top, rest = entries[0], entries[1:]
        for name, count in rest:
            if top[1] >= 3 and count <= 1:
                findings.append(
                    f"{name} cited by only {count} KB file(s) while {top[0]} (same topic) "
                    f"is cited by {top[1]} — likely the abandoned side of a fork, verify before trusting either"
                )
    return findings


def build_report() -> dict:
    return {
        "stale_op_mentions": check_stale_op_mentions(),
        "unrouted_live_ops": check_unrouted_live_ops(),
        "self_declared_deprecated_but_cited": check_self_declared_deprecated_but_cited(),
        "authority_citation_conflicts": check_authority_citation_conflicts(),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true",
                    help="non-zero exit on any finding, including heuristic ones")
    args = p.parse_args()

    report = build_report()
    hard_failures = report["stale_op_mentions"] or report["unrouted_live_ops"]
    heuristic_findings = (
        report["self_declared_deprecated_but_cited"] or report["authority_citation_conflicts"]
    )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("validate_kb_consistency:")
        print(f"  stale_op_mentions ({len(report['stale_op_mentions'])}): {report['stale_op_mentions']}")
        print(f"  unrouted_live_ops ({len(report['unrouted_live_ops'])}): {report['unrouted_live_ops']}")
        print("  self_declared_deprecated_but_cited:")
        for line in report["self_declared_deprecated_but_cited"]:
            print(f"    - {line}")
        print("  authority_citation_conflicts:")
        for line in report["authority_citation_conflicts"]:
            print(f"    - {line}")
        status = "FAIL" if hard_failures else ("WARN" if heuristic_findings else "OK")
        print(f"  status: {status}")

    if hard_failures:
        return 1
    if args.strict and heuristic_findings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
