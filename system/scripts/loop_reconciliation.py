#!/usr/bin/env python3
"""
loop_reconciliation.py — detect possible duplicate intent between the two
open-loop namespaces (system/loop_ledger.md's L- and system/eolms/loops.json's
EL-), per CANONICAL_REGISTRY.yaml's execution_loops.conflict_policy: "one
intent must not exist as active in both namespaces." That policy has existed
since 2026-08-19 with nothing enforcing it -- consolidation_target was left
as "explicit_single_loop_authority_decision," genuinely unresolved.

This is a literal subset of the opportunities domain's consolidation
(current_stores already lists both system/loop_ledger.md and
system/eolms/loops.json), not a standalone side project.

What this deliberately does NOT do: auto-merge, auto-close, or pick a
winner between a flagged pair. There is no shared identifier between the
two namespaces and no safe automated way to decide which namespace's
version of an intent is authoritative -- that is Todd's decision, not
this script's. A pair already cross-linked via an EL- record's
related_loop_ids is treated as KNOWN, not flagged as a conflict needing
adjudication -- only unlinked pairs that look similar are surfaced.

Usage:
    python3 loop_reconciliation.py            # human-readable report
    python3 loop_reconciliation.py --json      # JSON report
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import rb_core as core  # noqa: E402
import eolms  # noqa: E402

# Matches the fuzzy-match threshold convention already established in
# identity_match_review.py (_FUZZY_MATCH_THRESHOLD = 0.82) -- similarity
# scoring here is deliberately conservative for the same reason: a false
# "possible duplicate" flag is much cheaper than a wrong auto-merge would
# be, but a threshold that's too loose just trains Todd to ignore this
# report.
SIMILARITY_THRESHOLD = 0.55

_STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "for", "with", "on", "at", "in",
    "of", "is", "this", "that", "be", "if", "then", "per", "todd", "not",
}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _l_loop_text(loop: core.Loop) -> str:
    return f"{loop.party} {loop.description}"


def _el_loop_text(loop: core.ELoop) -> str:
    return " ".join([
        loop.title,
        " ".join(loop.related_people or []),
        " ".join(loop.related_orgs or []),
    ])


def _similarity(a_text: str, b_text: str) -> float:
    a_tokens, b_tokens = _tokens(a_text), _tokens(b_text)
    if not a_tokens or not b_tokens:
        return 0.0
    jaccard = len(a_tokens & b_tokens) / len(a_tokens | b_tokens)
    sequence = difflib.SequenceMatcher(None, a_text.lower(), b_text.lower()).ratio()
    # Average rather than max: a high sequence-ratio on short strings that
    # share almost no real tokens (or vice versa) shouldn't alone clear the
    # bar -- both signals have to agree at least somewhat.
    return (jaccard + sequence) / 2


def build_report(*, ledger_path: Path | None = None) -> dict:
    """ledger_path overrides system/loop_ledger.md for tests. eolms._load()
    has no such parameter by design (its own comment: tests patch
    core.EOLMS_PATH instead) -- do the same here rather than adding a
    second, inconsistent way to override the EL- source."""
    l_loops = core.parse_loop_ledger(ledger_path) if ledger_path else core.parse_loop_ledger()
    open_l = [loop for loop in l_loops if not loop.closed]

    el_loops = eolms._load()  # noqa: SLF001
    # RB-2026-09-10: this hardcoded set drifted from core.ELOOP_TERMINAL_STATUSES
    # -- it never included "archived", so an EL- loop closed via
    # `eolms.py transition --to archived` (the terminal status used for
    # migrations/stale-fork cleanup) kept counting as open here. Use the
    # core constant directly so the two can't diverge again.
    open_el = [loop for loop in el_loops if (loop.status or "").lower() not in core.ELOOP_TERMINAL_STATUSES]

    known_linked_l_ids: set[str] = set()
    for el in open_el:
        known_linked_l_ids.update(lid for lid in (el.related_loop_ids or []) if lid.startswith("L-"))

    conflicts: list[dict] = []
    for l in open_l:
        for el in open_el:
            is_known_linked = l.id in (el.related_loop_ids or [])
            score = _similarity(_l_loop_text(l), _el_loop_text(el))
            if is_known_linked:
                continue  # already a confirmed, deliberate cross-reference -- not a conflict to surface
            if score >= SIMILARITY_THRESHOLD:
                conflicts.append({
                    "l_id": l.id,
                    "l_party": l.party,
                    "l_description": l.description[:150],
                    "el_id": el.id,
                    "el_title": el.title,
                    "similarity_score": round(score, 3),
                })

    conflicts.sort(key=lambda c: c["similarity_score"], reverse=True)

    return {
        "open_l_count": len(open_l),
        "open_el_count": len(open_el),
        "known_linked_l_ids": sorted(known_linked_l_ids),
        "possible_duplicate_intent": conflicts,
        "distinct_obligation_count": len(open_l) + len(open_el) - len(known_linked_l_ids),
        "raw_combined_count": len(open_l) + len(open_el),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"Open L- loops: {report['open_l_count']}, open EL- loops: {report['open_el_count']}")
        print(f"Raw combined count: {report['raw_combined_count']} "
              f"(distinct, deduping known cross-links: {report['distinct_obligation_count']})")
        if report["possible_duplicate_intent"]:
            print(f"\nPossible duplicate intent ({len(report['possible_duplicate_intent'])}), "
                  "for Todd to adjudicate -- not auto-resolved:")
            for c in report["possible_duplicate_intent"]:
                print(f"  [{c['similarity_score']}] {c['l_id']} ({c['l_party']}) "
                      f"<-> {c['el_id']} ({c['el_title']})")
        else:
            print("\nNo possible duplicate intent detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
