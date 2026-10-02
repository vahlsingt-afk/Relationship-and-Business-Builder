# Technology Lifecycle Research — Kickoff

Use Hunter's `technology_replacement_lifecycle` playbook to start building
RBB's restaurant-technology lifecycle dataset. The goal: a verified,
sourced history of restaurant-technology change events — which brand
replaced what with what, why, and what happened — across POS, payments,
back office, loyalty, ordering, and the rest of the stack. This becomes
shared RBB infrastructure (a Team Portal-visible public intelligence
layer), not private notes in one account.

## What to produce

Prepare the job with:

```bash
python3 system/scripts/hunter_cycle.py prepare technology_replacement_lifecycle --target company:<id> --output /tmp/hunter-lifecycle-job.json
```

Submit its directive to ChatGPT Deep Research with Hunter's system prompt.
Return one Hunter JSON packet whose payload schema is
`rb.technology_lifecycle_research.v1`, then run `hunter_cycle.py finalize`.
The old Markdown-plus-sidecar template is retained only as a downstream legacy
view/import format; it is not the research contract.

## Core discipline: selection is not deployment

Cycles 7–8 found the single most common research error is collapsing
"Brand X selected Vendor Y" into "Brand X is running Vendor Y at N
locations." Keep these separate at all times:

- **Announced / contracted / mandated / committed scope** — what was
  publicly stated, signed, or required. This is intent, not deployment.
- **Pilot / installed / live / verified scope** — what evidence actually
  shows is running, with a real source and date.
- **Governance** — what the franchise agreement/FDD actually requires vs.
  what it merely *permits* the franchisor to require later. A clause
  giving the franchisor authority to mandate a change someday is not
  evidence the change is mandated today.
- **Brand-level vs. operator-level** — one franchisee's rollout (e.g.
  "GPS Hospitality has ~400 Burger King locations on R365") is real
  evidence, but it does not establish the brand-wide figure. Record it at
  the level the evidence actually supports (`entity_level: operator`), and
  let it roll up into brand intelligence rather than standing in for it.
- **Enterprise win vs. installed base** — a vendor winning the corporate
  standard does not mean it has displaced every incumbent yet. Both facts
  can and should be recorded simultaneously (see the Burger King worked
  example in `system/SCHEMAS.md`).

FDDs (Franchise Disclosure Documents) are now a priority source — for
relevant brands, check Items 5, 6, 7, 8, and 11 for required technology,
approved vendors, technology fees, required hardware, replacement
obligations, and franchisor authority to change requirements during the
term (and who pays for it).

## Two hypotheses to actively test, not assume

See `system/technology_lifecycle/README.md` for the full statement. Short
version: (1) mature enterprise brands tend to replace one stack layer at a
time while emerging/growth brands are more likely to adopt a broad
integrated platform at once, and (2) a vendor is more likely to expand
into an account one category at a time after an initial win than to sell
the whole stack up front. Because of this, for **every** event, also
capture:

- **Change scope** — single product, adjacent bundle, platform module
  expansion, or broad stack replacement.
- **Brand maturity** — emerging/growth, established regional, or large
  enterprise.
- **Existing stack retained** — what categories/vendors stayed untouched
  (e.g. new POS, same loyalty platform as before).
- **Phasing** — pilot → regional/franchisee rollout → enterprise rollout →
  any later module adoption.
- **Platform relationship** — did the winning vendor also sell other
  categories (payments, loyalty, back office, etc.) the operator chose
  **not** to buy from them? Name those explicitly — the absence of
  consolidation is a real, recordable data point, not a gap to skip.
- **Follow-on adoption** — did that same vendor pick up an adjacent
  category 12–36+ months later? Only record this with real evidence, never
  as an assumption.

This is why IHOP-style deep single-layer migrations and Big Chicken-style
emerging-brand full-stack adoptions are both exactly what this dataset
needs — they're the two ends of the Stack Replacement Hypothesis, and
recording each one fully (not just "what changed" but "what didn't, and
why") is what will eventually let RBB tell a competitor's layer-win apart
from an account actually closing.

## Start here: 10 concrete research priorities (post-schema-update)

These supersede a generic "sweep everything" approach for now — Mature
Core categories (POS, POS hardware, payments, back office) are still the
right place to focus, and Burger King/RBI specifically is the anchor case
this schema was built around, so reconstructing it well pays off across
every brand that follows the same parent/franchise pattern:

1. Reconstruct Burger King's complete pre-2024 approved back-office vendor
   universe (Restaurant365, PAR Data Central, RTI/RTIconnect, and others).
2. Determine current Burger King back-office penetration by vendor —
   enterprise standard vs. verified installed base, brand-level and
   operator-level.
3. Quantify the actual PAR/Data Central Burger King rollout (contracted
   vs. live vs. verified scope, rollout velocity if determinable).
4. Reconstruct RBI's proprietary rPOS investment and the pivot to PAR
   POS/Brink — total investment, technical/franchisee/economic reasons
   for the pivot, to the extent evidence exists.
5. Determine Popeyes' current U.S. POS architecture.
6. Reconstruct Firehouse Subs' POS/back-office stack and its Toast
   migration.
7. Reconstruct Tim Hortons' technology architecture (remember: common
   RBI ownership does not imply the same stack as Burger King).
8. Begin FDD technology-governance sweeps across top restaurant brands
   (Items 5, 6, 7, 8, 11).
9. Systematically search for announced wins that stalled, failed, were
   abandoned, or were replaced unusually quickly — these are as valuable
   as confirmed successes (see non-switch discipline below).
10. Build penetration time series wherever reliable deployment counts
    become available (`technology_penetration.jsonl`'s shape — don't wait
    for Phase 1's importer to start collecting the raw numbers).

After these, continue sweeping the rest of Mature Core, then Established
Digital (loyalty, mobile apps, online ordering, digital menu boards, KDS,
drive-thru tech) and Emerging (kiosks, voice AI, computer vision, AI
agents) — their shorter histories and different dynamics (pilot/abandon/
expand rather than clean replacement cycles) matter, but Mature Core is
where verifiable multi-year tenure data exists today.

## Research workflow (follow this order — don't skip to statistics)

1. **Vendor discovery** — use vendor press releases, case studies,
   customer lists, and announcements to identify *candidate* events: which
   brand, which incumbent, which replacement, roughly when. Vendor
   marketing is a discovery index, not evidence of outcome.
2. **Brand reconstruction** — research the restaurant/operator directly:
   previous system, tenure, business problem, executive sponsor, timeline.
3. **Independent verification** — pivot to trade press (Nation's
   Restaurant News, Restaurant Business, QSR, Hospitality Technology), SEC
   filings, earnings calls, investor presentations, franchisee
   communications, conference talks, job postings. This is what actually
   turns a vendor claim into a recorded fact.
4. **Pre-change reconstruction** — for each verified event, look back
   24–36 months: leadership changes, business transformation, technology
   pain signals, buying signals (RFI/RFP/pilot/consultant engagements, job
   postings for migration roles).
5. **Outcome research** — look for evidence at 6/12/24/36 months post-
   deployment: ROI, labor/revenue impact, digital mix, uptime, franchisee
   adoption, or — just as importantly — evidence of failure, abandonment,
   or no public evidence at all. Absence of bad news is not success
   evidence; record `no_public_evidence_found` rather than assuming.

**Do not** jump ahead to computing tenure statistics or a propensity
score — that's explicitly a later phase, gated on having the event corpus
be large *and* balanced across switches, renewals, abandoned projects, and
non-switches (minimum 5 per category/segment, and not just 5 confirmed
switches) to be statistically honest.

## Non-negotiable discipline

- **Include non-switches on purpose.** Renewed incumbents, abandoned
  pilots, canceled RFPs, and "new CTO but nothing changed" cases are as
  valuable as confirmed switches — they prevent the dataset from learning
  a biased pattern. Actively look for and record these.
- **Label every claim's evidence type**: `vendor_claim`,
  `operator_statement`, `independent_evidence`, `rbb_inference`, or
  `unknown` (see `system/technology_lifecycle/README.md` for definitions).
  Never silently upgrade a vendor claim or an inference into a stated
  fact.
- **Preserve uncertainty as ranges, not guesses.** If an exact date isn't
  known, give a range and a confidence level rather than picking a single
  plausible-looking date.
- **Every factual entry needs a real source URL and accessed/observed
  date.** No source, no entry.
- **Brand and vendor identity should match RBB's existing records where
  possible** — if you know or can infer the brand is already tracked in
  RBB (most top restaurant chains are), use the same name RBB would use;
  the importer will resolve to existing entity records rather than create
  duplicates.

## Reference

Full field-level schema: `system/SCHEMAS.md` → "Technology Lifecycle &
Change Events". Hunter envelope: `system/schemas/
hunter_research_packet.schema.json`. Payload registration:
`system/research/hunter_payload_registry.json`.
