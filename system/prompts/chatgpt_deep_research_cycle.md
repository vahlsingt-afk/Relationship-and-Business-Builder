# Legacy Deep Research Entry Point — Redirect to Hunter

This filename remains only for compatibility with older schedulers and links.
It is not a separate research process. Read and follow
`system/research/HUNTER.md` and `system/prompts/hunter_research_bot.md` first.
The caller must prepare the assignment with `system/scripts/hunter_cycle.py
prepare`; do not construct a legacy assignment from this file. Hunter is the
research method and the authoritative output is JSON conforming to
`system/schemas/hunter_research_packet.schema.json`, with the existing
`rb.competitor_platform_research.v1` sidecar contract nested in `payload`.

Research the assigned restaurant-technology candidate pages and competitor
customer evidence. Prioritize direct competitor case studies, customer stories,
logo pages, press releases, integration and partner pages, testimonials, and
product-specific customer pages.

The goal is marketplace coverage, not false certainty. Preserve useful evidence
at 20–40% confidence when that is all the sources support. Never call a logo or
candidate page a confirmed deployment. Seek independent corroboration, retain
conflicts, and distinguish enterprise, franchisee, pilot, regional,
location-level, historical, and unknown scope.

Produce exactly one Hunter JSON packet and finalize it with
`system/scripts/hunter_cycle.py finalize`. A downstream function may render the
legacy Markdown evidence view described in
`system/templates/deep_research_intelligence_drop.md`. Every observation must
include brand, vendor, product/category, relationship indication, deployment
scope, evidence type, confidence percentage and rationale, exact source URL,
publisher, access date, a faithful supporting excerpt or factual note,
limitations/conflicts, and recommended follow-up. Include negative findings and
a complete ledger of pages actually opened.

Save the completed packet as
`system/inbox/chatgpt_intelligence_drop/YYYY-MM-DD_HHMM_scope_deep-research.json`.
Do not independently author a parallel Markdown narrative; avoiding two
divergent records is part of Hunter's contract.
The abbreviated legacy shape below is illustrative only and is not a valid
replacement for Hunter's complete envelope:

```json
{
  "packet_id": "dr-YYYYMMDD-HHMM-scope",
  "targets": ["company:<entity_id>", "competitor:<slug>"],
  "pages_reviewed": 0,
  "candidate_pages_validated": 0,
  "conflicts_found": 0,
  "source_ledger": [
    {"url": "https://...", "source_type": "case study", "productive": true, "notes": "optional"}
  ]
}
```

`targets` must use the assigned target's real `company:<entity_id>` or
`competitor:<slug>` key -- ask for it if not given, never guess one.
`source_ledger` should cover every page from the packet's own "Source
ledger" table, with `productive` set to whether it actually supported an
observation, so a domain that repeatedly produces nothing stops being
re-researched.

Add the existing `"findings"` array inside `payload` (payload schema
`"rb.competitor_platform_research.v1"`, full field contract in
`system/templates/deep_research_intelligence_drop.md`) -- one entry per
Company/competitor profile observation that has a real, sourced value.
This is what actually turns a research packet into a canonical RBB
mutation; without it, the packet is captured but nothing changes in
competitor_intelligence or genius_capabilities. Label the vendor's own
marketing claims as `field: "vendor_claims"` -- never as `strengths`, no
matter how confident the source. For Genius's own product lines or Global
Payments corporate-level facts, `target` is `"genius:<scope>"` (`pos |
payments | back_office | kitchen_drive_thru | loyalty_engagement |
digital_menu_boards | restaurant_os_platform | parent | adjacent`) --
Genius/Global Payments must never be researched as a competitor.

Do not edit canonical RB records. Hunter's finalizer and governed dispatcher
own validation and review routing; the next morning intelligence cycle owns
downstream triage and reporting.
