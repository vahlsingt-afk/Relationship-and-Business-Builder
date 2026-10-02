#!/usr/bin/env python3
"""test_kb_consistency.py — permanent regression coverage for the KB/schema
drift checks added 2026-08-25 (see validate_kb_consistency.py's module
docstring for the incident that motivated it).

Runs in the normal test suite so drift is caught on the next `pytest`, not
only when someone remembers to run validate_kb_consistency.py by hand.

Test IDs:
  KBC1 — no op is mentioned in KB prose (as an active instruction, not a
         reviewed-and-allowlisted historical/negative mention) that doesn't
         exist in the live Actions schema
  KBC2 — no live op is completely unrouted, except the reviewed allowlist
         (diagnostic-only ops, or ops confirmed working off their own
         schema description) plus getCockpitContext, deliberately unrouted
         pending the RBB Project consolidation decision (2026-08-25)
  KBC3 — no KB file's own title/Status line calls itself deprecated while
         still being cited as authoritative elsewhere (the
         DAILY_BRIEF_CANONICAL_TEMPLATE.md incident, generalized)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import validate_kb_consistency as vkc  # noqa: E402

# 2026-08-25: getCockpitContext's "known pending unrouted" exception now
# lives in validate_kb_consistency.KNOWN_OK_UNDOCUMENTED itself (moved
# 2026-08-28 so self_audit_sweep.py shares the same exception list instead
# of duplicating it) -- check_unrouted_live_ops() already excludes it.


class KBC1_NoStaleOpMentions(unittest.TestCase):
    def test_no_unreviewed_stale_op_mentions(self):
        stale = vkc.check_stale_op_mentions()
        self.assertEqual(
            stale, [],
            f"KB files mention operation(s) not in the live schema, and not in "
            f"KNOWN_OK_STALE_MENTIONS: {stale}. Either the op needs to come back, "
            f"the KB text needs fixing, or (if reviewed and genuinely fine — e.g. "
            f"explanatory prose about a retired op, not an active instruction) "
            f"add it to KNOWN_OK_STALE_MENTIONS with a reason.",
        )


class KBC2_NoUnroutedLiveOps(unittest.TestCase):
    def test_no_unexpected_unrouted_live_ops(self):
        unexpected = set(vkc.check_unrouted_live_ops())
        self.assertEqual(
            unexpected, set(),
            f"Live op(s) with zero KB documentation, not already known/pending: "
            f"{unexpected}. The model has the tool but nothing tells it when to "
            f"use it — this is the getDraftActions/getDailyBriefPart2 failure class.",
        )


class KBC3_NoFalseDeprecationClaims(unittest.TestCase):
    def test_no_file_self_deprecates_while_still_cited(self):
        findings = vkc.check_self_declared_deprecated_but_cited()
        self.assertEqual(
            findings, [],
            f"File(s) call themselves deprecated/superseded in their own title/Status "
            f"line while still being cited as authoritative by other KB files: "
            f"{findings}. Verify which is actually true before trusting either claim.",
        )


if __name__ == "__main__":
    unittest.main()
