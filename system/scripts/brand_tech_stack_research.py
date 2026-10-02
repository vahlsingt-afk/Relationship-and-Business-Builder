#!/usr/bin/env python3
"""
brand_tech_stack_research.py — RB-2026-08-31.

The "external research" leg of tech-stack coverage expansion (see
tech_stack_relationship_promotion.py's module docstring for the full
context). ~1,200 of ~1,654 tracked brands have zero known tech-stack
vendor relationships; researching all of them is a large, paced,
judgment-heavy undertaking, not something to run unattended in one shot.

This is deliberately NOT a server-side web scraper -- RB's API doesn't do
its own independent internet research anywhere in this codebase; the chat
model (rbb-chat/Codex, which has real web search/fetch tools) does the
actual research and reports back what it found, the same way
uploadAndIngestFile accepts already-extracted text rather than fetching a
file itself. This module is the thin recording step for that: one real,
already-gathered finding (a vendor name, the evidence text, ideally a
source URL) about ONE brand at a time, fed into the same review-first
candidate queue tech_stack_relationship_promotion.py's other two scan
paths use. Never a bulk/batch loop across brands -- pacing which brands to
research is Todd's call, made one at a time (from chat or this CLI), not
something to automate blind.

CLI:
    python3 brand_tech_stack_research.py --brand-id brand-five-guys \\
        --vendor "Toast" --evidence "Five Guys press release: ..." \\
        --category pos --source-url https://...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import tech_stack_relationship_promotion as promotion  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--brand-id", required=True, help="e.g. brand-five-guys")
    p.add_argument("--vendor", required=True, help="Vendor name -- need not already be a tracked entity.")
    p.add_argument("--evidence", required=True, help="The real evidence text found -- never a summary/guess.")
    p.add_argument("--category", default=None, help="Optional; inferred from --evidence text if omitted.")
    p.add_argument("--source-url", default=None)
    p.add_argument("--source-title", default=None)
    args = p.parse_args()

    result = promotion.propose_research_finding(
        args.brand_id, args.vendor, args.evidence,
        category=args.category, source_url=args.source_url, source_title=args.source_title,
    )
    print(json.dumps(result, indent=2))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
