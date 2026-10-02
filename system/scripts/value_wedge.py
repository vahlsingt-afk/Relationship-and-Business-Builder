#!/usr/bin/env python3
"""
value_wedge.py — Value Wedge persistence (RB-2026-09-25, redesigned 2026-09-28).

The real sales methodology (Todd, pointing at launchnotes.com's Value
Wedge guide): the intersection of three circles --
    1. Company (Genius) Strengths
    2. Customer Needs (THIS account's, specifically)
    3. Competitor Weaknesses
-- is the "wedge": a real Genius capability that addresses a real need
this account has, which this competitor is verifiably weak on. The
original 2026-09-25 build got this wrong on two counts, both fixed here:

  (a) It was vendor-only, reiterating Battle Card's category text with no
      account-specific "Customer Needs" circle at all -- there wasn't one
      to reiterate, since no such field existed anywhere in this codebase
      (confirmed by search, 2026-09-28). Now brand+vendor scoped: circle 2
      comes from brand_profile_common.py's new `pain_points` field
      (account-specific, evidence-driven, added alongside this fix).
  (b) Circle 3 (competitor weaknesses) read `vs_genius.competitor_
      advantages` -- the competitor's own STRENGTHS (populated via
      competitor_intelligence.add_gap_point(side="competitor")), the
      opposite of what circle 3 needs. Fixed to read `vs_genius.
      genius_advantages` (side="genius" -- "Genius wins here" =
      the competitor's weakness).

Deliberately does NOT attempt to auto-match a strength to a need to a
weakness into "the wedge" for the reader -- that would require inferring
a connection this codebase has no evidence for, the same fabrication risk
every other artifact in this suite is built to avoid (see
intelligence_mutation_engine.py's word-boundary/pronoun-resolution
history). The three circles are rendered as real, separate, sourced
lists; finding the actual wedge between them is the RM's judgment call,
not this module's to invent.

Rendering: this module returns STRUCTURED data (a dict), not markdown --
Team Portal renders it as an actual three-circle diagram (inline SVG),
not more prose. Persisted as JSON via artifact_vault_common.py (same
versioning discipline as every other artifact type in this suite, just a
different file_extension), keyed by `<vendor_slug>--<brand_id>` so each
account gets its own independently-versioned wedge for a given vendor.

`brand_id` is optional, not required, to preserve two existing callers
that only ever had a competitor_slug: server.py's chat-tool routes
(createValueWedge/getValueWedge -- Todd's own rbb-chat interface, out of
scope for this Team Portal redesign) and refresh_persisted_briefs.py's
nightly refresh_value_wedges() batch job, which iterates every tracked
competitor with no brand in the loop. Without a brand_id, circle 2
("Customer Needs") is honestly empty -- there is no generic substitute
for an account-specific need -- and
the persistence key falls back to plain `competitor_slug`, identical to
this module's pre-2026-09-28 behavior, so neither existing caller's
version history is disrupted.

CLI:
    python3 system/scripts/value_wedge.py generate <competitor_slug> [brand_id]
    python3 system/scripts/value_wedge.py get <competitor_slug> [brand_id]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import competitor_intelligence as compintel  # noqa: E402
import competitor_intelligence_common as cic  # noqa: E402
import genius_capabilities  # noqa: E402
import brand_profile_common as bpc  # noqa: E402
import artifact_vault_common as avc  # noqa: E402
import intelligence_index  # noqa: E402

ARTIFACT_TYPE_DIR = "value_wedges"
ARTIFACT_TYPE_TITLE = "Value Wedge"


def _artifact_slug(competitor_slug: str, brand_id: Optional[str]) -> str:
    return competitor_slug if brand_id is None else f"{competitor_slug}--{brand_id}"


def get_current_value_wedge(
    competitor_slug: str, brand_id: Optional[str] = None, *, include_content: bool = False,
) -> Optional[dict]:
    entry = avc.get_current_version(
        ARTIFACT_TYPE_DIR, _artifact_slug(competitor_slug, brand_id), include_content=include_content,
    )
    if entry is not None and include_content and entry.get("content") is not None:
        entry["data"] = json.loads(entry["content"])
    return entry


def build_value_wedge_data(
    competitor_slug: str, brand_id: Optional[str] = None, *, category: Optional[str] = None,
) -> dict:
    """Pure data assembly from already-persisted competitor.json + the
    Genius Capability Library + the brand's pain_points/challenge signals
    (when a brand_id is given) -- same "documents are outputs" discipline
    as every other artifact in this suite. Raises FileNotFoundError if the
    competitor doesn't exist (callers that want auto-create should go
    through generate_value_wedge()).

    `category` (2026-09-28, Todd: pick a specific Genius product line, not
    every category this vendor happens to compete on): when given, scopes
    circle 1 to just that one category's Genius Capability Library
    entries -- a real sales conversation is "Genius digital ordering vs.
    Toast," not every product line at once. Silently ignored if the
    competitor hasn't declared competes_on for that category (nothing to
    scope to; falls back to all declared categories)."""
    comp_data = cic.load_competitor(competitor_slug)
    comp = comp_data["competitor"]
    vendor_display_name = comp.get("display_name", competitor_slug)

    brand_name = None
    circle2_customer_needs: list[dict] = []
    if brand_id is not None:
        brand_profile = bpc.get_profile(brand_id, persist=False)
        brand_name = brand_profile.get("brand_name", brand_id)
        circle2_customer_needs = [
            {"value": p.get("value", ""), "as_of": p.get("as_of"), "confidence": p.get("confidence"), "source": "logged_need"}
            for p in (brand_profile.get("pain_points") or [])
        ]
        # "populate from what we know" -- a brand's already-researched
        # challenge_or_headwind signals (recent_signals) are real, sourced
        # difficulties, the same shape of fact as a manually-logged need,
        # just gathered by the deep-research pipeline instead of a call.
        # Never invented here -- only surfaced if it's already on file.
        circle2_customer_needs += [
            {"value": s.get("value", ""), "as_of": s.get("as_of"), "confidence": s.get("confidence"), "source": "known_challenge"}
            for s in (brand_profile.get("recent_signals") or [])
            if s.get("signal_type") == "challenge_or_headwind"
        ]

    competes_on = sorted(comp.get("competes_on") or [])
    categories_for_circle1 = [category] if category and category in competes_on else competes_on
    all_capabilities = genius_capabilities.list_all_capabilities()
    circle1_genius_strengths = [
        {"category": cic.category_display_name(cat), "point": c["point"], "why_it_matters": c.get("why_it_matters")}
        for cat in categories_for_circle1
        for c in (all_capabilities.get(cat) or [])
    ]

    vs_genius = comp.get("vs_genius") or {}
    selected_category = category if category in competes_on else None
    circle3_competitor_weaknesses = [
        {"point": a.get("point", "")} for a in (vs_genius.get("genius_advantages") or [])
        # A point tagged with a specific product line (competitor_
        # intelligence.add_gap_point's category param) only belongs here
        # when that's the product actually being compared -- otherwise
        # it's off-topic (e.g. a POS/back-office weakness showing up on a
        # Loyalty-scoped wedge). An untagged point is a company-wide fact
        # (financial health, overall strategy) and always applies.
        if not a.get("category") or a.get("category") == selected_category
    ]

    return {
        "competitor_slug": competitor_slug,
        "vendor_display_name": vendor_display_name,
        "brand_id": brand_id,
        "brand_name": brand_name,
        "category": category if category in competes_on else None,
        "competes_on": competes_on,
        "circle1_genius_strengths": circle1_genius_strengths,
        "circle2_customer_needs": circle2_customer_needs,
        "circle3_competitor_weaknesses": circle3_competitor_weaknesses,
    }


def render_value_wedge_markdown(data: dict) -> str:
    """Plain-text rendering of build_value_wedge_data()'s structured
    output -- kept only for server.py's chat-tool routes (createValueWedge/
    getValueWedge, Todd's own rbb-chat interface), which predate and are
    out of scope for the Team Portal SVG redesign and still expect a
    `markdown` string in their response contract. Team Portal never calls
    this -- it renders `data` as an actual diagram instead (see
    team_portal_ui.html's valueWedgeSvg())."""
    display_name = data.get("vendor_display_name", data.get("competitor_slug", ""))
    lines = [f"# {display_name} — Value Wedge", ""]

    lines.append("## Genius Strengths")
    caps = data.get("circle1_genius_strengths") or []
    if caps:
        for c in caps:
            line = f"- ({c.get('category', '')}) {c.get('point', '')}"
            if c.get("why_it_matters"):
                line += f" — {c['why_it_matters']}"
            lines.append(line)
    else:
        lines.append("*No product lines declared yet for this competitor, or no Genius capability content on file.*")

    lines.append("\n## Customer Needs")
    needs = data.get("circle2_customer_needs") or []
    if needs:
        lines.extend(f"- {n.get('value', '')}" for n in needs)
    elif data.get("brand_id"):
        lines.append("*No pain points on file yet for this account.*")
    else:
        lines.append("*No account specified -- this circle is account-specific and only populates when a Value Wedge is generated for a specific brand (Team Portal's brand-page entry point).*")

    lines.append(f"\n## Where {display_name} is weak")
    weaknesses = data.get("circle3_competitor_weaknesses") or []
    if weaknesses:
        lines.extend(f"- {w.get('point', '')}" for w in weaknesses)
    else:
        lines.append('*No competitor-side gap points on file yet -- call addCompetitorGapPoint(side="genius") to add some.*')

    return "\n".join(lines).strip() + "\n"


def generate_value_wedge(
    competitor_slug: str, brand_id: Optional[str] = None, *, category: Optional[str] = None, generated_for: str = "",
) -> dict:
    """The main entry point. Auto-creates the competitor (a mostly-empty
    shell) via ensure_competitor_by_slug() when the slug has no record yet
    -- see module docstring. Raises brand_profile_common.NotFoundError if
    brand_id is given but isn't a real brand entity (unlike the competitor
    side, brands are never auto-created -- existing entities only, same
    discipline as every team_tech_stack.py route).

    `category` is a render-time filter, not a separate persistence
    identity -- regenerating the same (competitor_slug, brand_id) pair
    with a different category still versions under the same slug (the
    latest generation is "current," same as every other artifact type in
    this suite); it isn't a second, independently-tracked wedge."""
    compintel.ensure_competitor_by_slug(competitor_slug)
    data = build_value_wedge_data(competitor_slug, brand_id, category=category)
    content = json.dumps(data, indent=2, ensure_ascii=False)
    slug = _artifact_slug(competitor_slug, brand_id)
    display_name = (
        f"{data['vendor_display_name']} vs. Genius — {data['brand_name']}"
        if brand_id is not None else f"{data['vendor_display_name']} vs. Genius"
    )

    version = avc.register_version(
        ARTIFACT_TYPE_DIR, ARTIFACT_TYPE_TITLE, slug, display_name, content,
        generated_for=generated_for, purpose="value wedge", file_extension="json",
    )

    try:
        if version["version"] == 1:
            intelligence_index.register_document(
                display_name, "value_wedge", f"Value Wedge: {display_name}",
                version["path"], source_system="value_wedge", created_at=avc.today(),
            )
        else:
            intelligence_index.log_update(
                display_name, version["path"], resource_type="value_wedge",
                note=f"regenerated as v{version['version']}" + (f" for {generated_for}" if generated_for else ""),
            )
    except Exception:  # noqa: BLE001 — indexing is best-effort, never blocks a real artifact write
        pass

    return {"competitor_slug": competitor_slug, "brand_id": brand_id, "data": data, "version": version}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_gen = sub.add_parser("generate")
    p_gen.add_argument("competitor_slug")
    p_gen.add_argument("brand_id", nargs="?", default=None)
    p_gen.add_argument("--for", dest="generated_for", default="")
    p_get = sub.add_parser("get")
    p_get.add_argument("competitor_slug")
    p_get.add_argument("brand_id", nargs="?", default=None)
    args = parser.parse_args()

    if args.cmd == "generate":
        result = generate_value_wedge(args.competitor_slug, args.brand_id, generated_for=args.generated_for)
        print(json.dumps(result["data"], indent=2))
        print(f"\n[registered as version {result['version']['version']}]", file=sys.stderr)
    elif args.cmd == "get":
        current = get_current_value_wedge(args.competitor_slug, args.brand_id, include_content=True)
        if current is None:
            print(f"No value wedge persisted for '{args.competitor_slug}'/'{args.brand_id}' yet.", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(current["data"], indent=2))


if __name__ == "__main__":
    main()
