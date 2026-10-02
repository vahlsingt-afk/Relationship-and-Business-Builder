# Deep Research Intelligence Packet

> **Legacy rendered view.** Hunter (`system/research/HUNTER.md`) now produces
> one authoritative JSON packet. Calling/ingest functions may render this
> Markdown view from that JSON for human review; Hunter must not separately
> author both formats. Existing pre-Hunter packets remain valid historical
> records.

> Copy this template for each research cycle. Save the completed packet to
> `system/inbox/chatgpt_intelligence_drop/YYYY-MM-DD_HHMM_scope_deep-research.md`.
> Delete instructional text and omit empty optional fields before saving.
>
> **Also save a JSON sidecar with the exact same basename** (e.g.
> `2026-09-20_1030_subway_deep-research.json` next to the `.md` above) --
> this feeds the research-quality feedback ledger AND (RB-DEFECT-073,
> 2026-09-28) the canonical import that turns this packet's competitor/
> Genius platform observations into real, queryable RBB records; the
> markdown stays the human-readable evidence record either way. Schema:
>
> ```json
> {
>   "packet_id": "dr-YYYYMMDD-HHMM-scope",
>   "targets": ["company:<entity_id>", "competitor:<slug>"],
>   "pages_reviewed": 0,
>   "candidate_pages_validated": 0,
>   "conflicts_found": 0,
>   "source_ledger": [
>     {"url": "https://...", "source_type": "case study", "productive": true, "notes": "optional"}
>   ],
>   "schema": "rb.competitor_platform_research.v1",
>   "findings": [
>     {
>       "target": "competitor:<slug>",
>       "field": "vendor_claims | strengths | weaknesses | vulnerabilities | key_customers | product_lineage | marketplace_signals | trends",
>       "value": "one discrete, sourced statement",
>       "finding_type": "vendor_stated | independently_verified | marketplace_reported | inference",
>       "source_url": "https://...",
>       "source_owner": "publisher or site owner",
>       "source_type": "case study | analyst report | review site | press release | ...",
>       "confidence": "low | medium | high | critical",
>       "observed_at": "YYYY-MM-DD",
>       "published_at": "YYYY-MM-DD, if known",
>       "deployment_scope": "enterprise | franchisee | pilot | regional | location-level | historical | unknown, optional",
>       "is_vendor_claim": false,
>       "is_inference": false,
>       "limitations_or_conflicts": "optional"
>     }
>   ]
> }
> ```
>
> `targets` uses the exact same `company:<entity_id>` / `competitor:<slug>`
> keys `deep_research_coverage.py`'s own state file uses -- ask for the
> assigned target's real key if it wasn't given directly, never guess one.
> `source_ledger` should list every page from the "Source ledger" table
> below, with `productive` set to whether it actually supported an
> observation (`true`) or was a negative finding/dead end (`false`) -- this
> is what lets the same unproductive domain stop being re-researched.
>
> `findings` is optional but is the ONLY thing that turns a "Company /
> competitor profile observation" block below into an actual canonical
> mutation -- one `findings` entry per Company profile observation block
> that has a real, sourced value. `target` for Genius's own product lines
> or Global Payments corporate-level facts is `"genius:<scope>"` (one of
> `pos | payments | back_office | kitchen_drive_thru | loyalty_engagement |
> digital_menu_boards | restaurant_os_platform | parent | adjacent`) --
> **never** `"competitor:global-payments"` or any other competitor slug;
> Genius/Global Payments must never be tracked as a competitor.
> `field: "vendor_claims"` is for the vendor's OWN marketing/positioning
> statements -- these are never eligible to become `strengths`, no matter
> how confident the source is; an independently-supported claim (a
> customer story, an analyst report, a third-party review) belongs in
> `strengths` with `finding_type: "independently_verified"` instead. Every
> entry needs a real `source_url` and `observed_at` -- never fabricate
> either to satisfy the schema.

## Cycle metadata

- Packet ID: `dr-YYYYMMDD-HHMM-scope`
- Research system: `Hunter`
- Started at: `YYYY-MM-DD HH:MM TZ`
- Completed at: `YYYY-MM-DD HH:MM TZ`
- Research scope:
- Queue item or vendor:
- Methodology: Candidate-page validation and multi-source competitor/customer research

## Executive result

- Pages reviewed:
- Candidate pages validated:
- Brand-vendor-product observations added or revised:
- Company/competitor profile observations added or revised:
- Conflicts found:
- Material takeaway:

## Evidence observations

### Observation 1

- Brand:
- Vendor:
- Product or category:
- Relationship indication: `customer | pilot | partner | integration | reseller | former customer | unknown`
- Deployment scope: `enterprise | franchisee | pilot | regional | location-level | historical | unknown`
- Evidence type: `case study | customer story | logo wall | press release | integration page | partner page | testimonial | job posting | technical artifact | secondary report | other`
- Confidence: `20-100%`
- Confidence rationale:
- Source title:
- Source URL:
- Publisher or source owner:
- Published date, if known:
- Accessed date: `YYYY-MM-DD`
- Exact supporting excerpt or faithful factual note:
- Product-specific evidence:
- Conflicting or limiting evidence:
- Recommended follow-up:

## Company / competitor profile observations

Broader-than-tech-stack observations about a brand's or competitor's actual
business -- initiatives, health, status, priorities, strengths, weaknesses,
challenges, and overall competitive positioning (Todd's 2026-09-25
instruction: "anything that helps us understand them better should be
collected and recorded"). One block per observation; repeat as needed. Same
non-negotiable discipline as Evidence observations above -- source URL,
accessed date, confidence, exact supporting excerpt. Label inference as
inference; never present it as fact. Feeds the Ecosystem Lookup Tool's
Company Profile (brand target) and Competitor Snapshot (competitor target)
-- see `system/scripts/brand_profile_common.py` / `competitor_intelligence_
common.py` for the exact stored shape each Field maps onto.

### Company profile observation 1

- Target: `company:<entity_id>` or `competitor:<slug>`
- Field: `identity.parent_ownership | identity.hq_city_state | identity.founded_year | synopsis | leadership | strengths | weaknesses | vulnerabilities | key_customers | recent_news | trends | recent_signal`
- Signal type (only if Field is `recent_signal`): `strategic_initiative | financial_health | leadership_change | ownership_change | expansion_or_contraction | challenge_or_headwind | competitive_positioning | other`
- Leadership split (only if Field is `leadership`): `confirmed | reported_unverified`
- Value or observation:
- Confidence: `20-100%`
- Confidence rationale:
- Source title:
- Source URL:
- Publisher or source owner:
- Published date, if known:
- Accessed date: `YYYY-MM-DD`
- Exact supporting excerpt or faithful factual note:
- Conflicting or limiting evidence:

## Negative findings

Record pages checked that did not support the suspected relationship. Negative
evidence prevents the same dead ends from being researched repeatedly.

- Candidate or claim:
- Pages checked:
- Result:

## Source ledger

List every page actually opened, including useful negative results.

| URL | Source type | Accessed | Supports observation(s) | Notes |
|---|---|---|---|---|

## Research notes and inferences

Keep inference separate from sourced observations. Label every inference and
do not present it as a canonical fact.
