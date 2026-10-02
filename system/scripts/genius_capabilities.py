#!/usr/bin/env python3
"""
genius_capabilities.py — Genius Capability Library (RB-2026-09-25) +
Genius Own-Line Evidence Log (RB-2026-09-26).

Prerequisite for the Value Wedge (value_wedge.py): today "why Genius wins"
only ever exists as freeform text typed in per-competitor
(competitor.json's vs_genius.genius_advantages, via
competitor_intelligence.add_gap_point()) -- there's no structured,
reusable description of what Genius itself actually offers per tech-stack
product line that could be paired against ANY competitor's specific
weaknesses. This module is that reusable baseline.

The capability library itself (add_capability/list_capabilities) is
Todd's own curated product knowledge -- structured, per
competitor_intelligence_common.GENIUS_PRODUCT_LINES category, never
auto-recorded, never confidence-scored (same trust posture as
competitor.json's todds_pov / competitor_intelligence.add_competitive_note()).
Each capability point is discrete and sourced, not a freeform paragraph --
same discipline as add_gap_point().

Single flat JSON store (only 7 product lines total, no need for per-
category files): system/genius_capabilities.json.

Append-only for v1 -- no update/delete function, matching add_gap_point()'s
own no-delete precedent.

RB-2026-09-26: added a second, separate store -- add_evidence()/
list_evidence() -- for everything ABOUT Genius's own product lines that
isn't itself a "capability" claim (financial/market signals, named
customers, weaknesses, C-suite, news, rumors). Found live: the first deep-
research import of Genius's own product lines had real, sourced content in
these dimensions with nowhere to land, since the capability library is
deliberately scoped to "what Genius offers," not general evidence. Same
record shape as competitor_intelligence's evidence.jsonl (evidence_id,
logged_at, category, summary, source, confidence), deliberately NOT
routed through competitor_intelligence itself -- Genius/Global Payments is
explicitly excluded from ever being tracked as a competitor
(is_own_company(), RB-DEFECT-071). Scope is GENIUS_PRODUCT_LINES plus
"parent" (Global Payments corporate-level facts) and "adjacent" (a Genius-
adjacent line not itself one of the 7, e.g. Kitchen Management) --
deliberately not open to arbitrary categories, so this store can't quietly
become a dumping ground.

CLI:
    python3 genius_capabilities.py add pos "Real-time inventory sync across 40k locations" --why "eliminates manual reconciliation"
    python3 genius_capabilities.py list pos
    python3 genius_capabilities.py list-all
    python3 genius_capabilities.py add-evidence pos "Bookings +25% sequentially in Q2 2026" --source "GPN Q2 2026 earnings call" --confidence critical
    python3 genius_capabilities.py list-evidence pos
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import competitor_intelligence_common as cic  # noqa: E402

GENIUS_CAPABILITIES_PATH = SCRIPTS_DIR.parent / "genius_capabilities.json"
GENIUS_EVIDENCE_PATH = SCRIPTS_DIR.parent / "genius_own_evidence.jsonl"

# GENIUS_PRODUCT_LINES plus two scopes the capability library itself
# deliberately excludes (see module docstring): "parent" for Global
# Payments corporate-level facts, "adjacent" for a Genius-adjacent line
# that isn't one of the 7 official product lines.
GENIUS_EVIDENCE_SCOPES = cic.GENIUS_PRODUCT_LINES | {"parent", "adjacent"}

VALID_EVIDENCE_CATEGORIES = {
    "positioning", "strength", "weakness", "pricing", "market_share",
    "reference_customer", "customer_win", "customer_loss", "other",
}


def _load() -> dict:
    if not GENIUS_CAPABILITIES_PATH.exists():
        return {"capabilities": {}, "updated_at": None}
    try:
        data = json.loads(GENIUS_CAPABILITIES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"capabilities": {}, "updated_at": None}
    data.setdefault("capabilities", {})
    return data


def _save(data: dict) -> None:
    GENIUS_CAPABILITIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = cic.now_iso()
    GENIUS_CAPABILITIES_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def add_capability(
    category: str, point: str, *, why_it_matters: str | None = None,
    evidence_id: str | None = None, added_by: str = "Todd Vahlsing",
) -> dict:
    if category not in cic.GENIUS_PRODUCT_LINES:
        raise ValueError(f"unknown product line {category!r}; valid: {sorted(cic.GENIUS_PRODUCT_LINES)}")
    if not (point or "").strip():
        raise ValueError("point must not be empty")
    data = _load()
    entry = {
        "point": point.strip(),
        "why_it_matters": (why_it_matters or "").strip() or None,
        "evidence_id": evidence_id,
        "added_at": cic.now_iso(),
        "added_by": added_by,
    }
    data["capabilities"].setdefault(category, []).append(entry)
    _save(data)
    return {"ok": True, "category": category, "capability": entry}


def list_capabilities(category: str) -> list[dict]:
    if category not in cic.GENIUS_PRODUCT_LINES:
        raise ValueError(f"unknown product line {category!r}; valid: {sorted(cic.GENIUS_PRODUCT_LINES)}")
    return list(_load()["capabilities"].get(category, []))


def list_all_capabilities() -> dict[str, list[dict]]:
    """Honest-blank: always returns all 7 GENIUS_PRODUCT_LINES keys, even
    when a category has nothing on file yet -- a caller can tell "checked,
    nothing there" from "forgot to check" this way."""
    caps = _load()["capabilities"]
    return {cat: list(caps.get(cat, [])) for cat in sorted(cic.GENIUS_PRODUCT_LINES)}


def add_evidence(
    scope: str, note: str, *, category: str = "other", source: str = "Todd Vahlsing",
    confidence: str = "high",
) -> dict:
    """The general "everything else about a Genius product line" log --
    same shape/discipline as competitor_intelligence.add_competitive_note(),
    deliberately not routed through that module (see module docstring)."""
    if scope not in GENIUS_EVIDENCE_SCOPES:
        raise ValueError(f"unknown scope {scope!r}; valid: {sorted(GENIUS_EVIDENCE_SCOPES)}")
    if category not in VALID_EVIDENCE_CATEGORIES:
        raise ValueError(f"category must be one of {sorted(VALID_EVIDENCE_CATEGORIES)}, got {category!r}")
    if not (note or "").strip():
        raise ValueError("note must not be empty")
    existing = cic.load_jsonl(GENIUS_EVIDENCE_PATH)
    n = sum(1 for e in existing if e.get("scope") == scope)
    evidence_id = f"genius-{scope}-{n + 1:04d}"
    record = {
        "evidence_id": evidence_id, "scope": scope, "logged_at": cic.now_iso(),
        "category": category, "summary": note.strip(), "source": source, "confidence": confidence,
    }
    cic.append_jsonl(GENIUS_EVIDENCE_PATH, record)
    return {"ok": True, "scope": scope, "evidence_id": evidence_id}


def list_evidence(scope: str) -> list[dict]:
    if scope not in GENIUS_EVIDENCE_SCOPES:
        raise ValueError(f"unknown scope {scope!r}; valid: {sorted(GENIUS_EVIDENCE_SCOPES)}")
    return [e for e in cic.load_jsonl(GENIUS_EVIDENCE_PATH) if e.get("scope") == scope]


def list_all_evidence() -> dict[str, list[dict]]:
    """Honest-blank, same convention as list_all_capabilities(): always
    returns every valid scope's key, empty list when nothing is on file."""
    all_records = cic.load_jsonl(GENIUS_EVIDENCE_PATH)
    by_scope: dict[str, list[dict]] = {s: [] for s in sorted(GENIUS_EVIDENCE_SCOPES)}
    for record in all_records:
        scope = record.get("scope")
        if scope in by_scope:
            by_scope[scope].append(record)
    return by_scope


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add")
    p_add.add_argument("category")
    p_add.add_argument("point")
    p_add.add_argument("--why", dest="why_it_matters", default=None)
    p_add.add_argument("--evidence-id", dest="evidence_id", default=None)

    p_list = sub.add_parser("list")
    p_list.add_argument("category")

    sub.add_parser("list-all")

    p_add_ev = sub.add_parser("add-evidence")
    p_add_ev.add_argument("scope")
    p_add_ev.add_argument("note")
    p_add_ev.add_argument("--category", default="other")
    p_add_ev.add_argument("--source", default="Todd Vahlsing")
    p_add_ev.add_argument("--confidence", default="high")

    p_list_ev = sub.add_parser("list-evidence")
    p_list_ev.add_argument("scope")

    sub.add_parser("list-all-evidence")

    args = p.parse_args()

    if args.cmd == "add":
        result = add_capability(
            args.category, args.point, why_it_matters=args.why_it_matters, evidence_id=args.evidence_id,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.cmd == "list":
        print(json.dumps(list_capabilities(args.category), indent=2, ensure_ascii=False))
    elif args.cmd == "list-all":
        print(json.dumps(list_all_capabilities(), indent=2, ensure_ascii=False))
    elif args.cmd == "add-evidence":
        result = add_evidence(
            args.scope, args.note, category=args.category, source=args.source, confidence=args.confidence,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.cmd == "list-evidence":
        print(json.dumps(list_evidence(args.scope), indent=2, ensure_ascii=False))
    elif args.cmd == "list-all-evidence":
        print(json.dumps(list_all_evidence(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
