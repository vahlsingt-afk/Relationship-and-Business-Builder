# LinkedIn Relationship Intelligence Delta - 2026-05-29

Source file: `Complete_LinkedInDataExport_01-03-2026 copy.zip.zip`. Artifact auto-classified as LinkedIn export ZIP and processed as a longitudinal relationship-intelligence mutation event.

## Baseline Comparison

| Metric | Count |
|---|---:|
| Prior baseline records | 2,725 |
| Current baseline records | 2,725 |
| Connections in export | 2,412 |
| Matched existing relationships | 2,352 |
| Net-new relationships | 0 |
| Lost relationships | 10 |
| Reconnections | 107 |
| Conflicts held | 1 |

## Delta Metrics

| Signal | Count |
|---|---:|
| Title changes | 133 |
| Company changes | 76 |
| Promotions | 3 |
| Recruiter additions | 0 |
| Executive additions | 0 |
| Enterprise buyer additions | 0 |
| Restaurant-tech adjacency expansion | 1 |
| Strategic industry cluster changes | 5 |

## Strategic Relationship Changes

5 strategic relationships materially increased in value.

### Who Matters Now
- **Daran Adair**: Ameri-Can Hospitality Consulting / Lead Advisor — LinkedIn export shows company/title movement against prior baseline.
- **Amy Spytko**: BridgePoint Ops / Managing Partner — LinkedIn export shows company/title movement against prior baseline.
- **Patty Dominguez**: More Leverage Solutions / Founder & Authority Advisor | Creator of The Chosen Brand™ System — LinkedIn export shows company/title movement against prior baseline.
- **Sheryar Kayani**: Clara AI / Founder — LinkedIn export shows company/title movement against prior baseline.
- **Ami Austin**: Elevate Impact Designs / Founder & Principal Consultant — LinkedIn export shows company/title movement against prior baseline.

## Professional Change Detection

### RC moves
- **Daran Adair** (company_change): Ameri-Can Hospitality Consulting / Lead Advisor -> Ameri-Can Hospitality Consulting / Lead Advisor. LinkedIn export shows company/title movement against prior baseline.
- **Amy Spytko** (promotion): NCR Voyix / Director North America Sales -> BridgePoint Ops / Managing Partner. LinkedIn export shows company/title movement against prior baseline.

### LKI moves
- **Chris Szewczyk** (company_and_role_change): Espresso AI / Developer Relations -> Xenia / Enterprise Business Development Representative.
- **Patty Dominguez** (promotion): More Leverage Solutions / AI Visibility Strategist & Creator of The Chosen Brand™ System -> More Leverage Solutions / Founder & Authority Advisor | Creator of The Chosen Brand™ System.
- **Kimberley Modeste** (company_change): S'more AI / Co-Founder -> Mault / Co-Founder.
- **Anna Ludwinowski** (company_and_role_change): Anna Ludwinowski - Business Coaching / Business Foundation Coach -> Anna Ludwinowski - Business Foundation Strategist / Business Foundation Strategist.
- **Sheryar Kayani** (company_and_role_change): Trendtial Tech / Vice President -> Clara AI / Founder.
- **Spencer Amadon** (company_change): Focus CPG / Creator -> Discover Snacks / Creator.
- **Ed Herrera** (company_and_role_change): Partech/Brink POS / Account Manager -> Retired / Retired.
- **Nicole Blackman** (company_and_role_change): PAR Technology / Head of Business Operations -> Coates Group / Senior Director, Global.
- **Jeff Caplan** (company_and_role_change): Hooters of America / Senior Vice President / Chief Information Officer -> Church's Texas Chicken / Vice President, Enterprise Technology.
- **Ami Austin** (promotion): Inc Tank GTM / VP of Revenue Operations -> Elevate Impact Designs / Founder & Principal Consultant.
- **John Van Clieaf** (title_change): PAR Technology / Vice President, PAR Sales -> PAR Technology / Vice President of Sales.
- **Julio Cesar Lozano** (title_change): Bank of America / IAM Cybersecurity Analyst -> Bank of America / IAM Analyst (Strategic IAM & IGA Initiatives).

## Opportunity detection

- **Amy Spytko** - Send a concise congratulations note.. _LinkedIn export shows Director North America Sales -> Managing Partner._
- **Patty Dominguez** - Send a concise congratulations note.. _LinkedIn export shows AI Visibility Strategist & Creator of The Chosen Brand™ System -> Founder & Authority Advisor | Creator of The Chosen Brand™ System._
- **Ami Austin** - Send a concise congratulations note.. _LinkedIn export shows VP of Revenue Operations -> Founder & Principal Consultant._
- **Daran Adair** - Review as a warm-path or market-adjacency candidate.. _LinkedIn export shows company/title movement against prior baseline._
- **Amy Spytko** - Review as a warm-path or market-adjacency candidate.. _LinkedIn export shows company/title movement against prior baseline._
- **Patty Dominguez** - Review as a warm-path or market-adjacency candidate.. _LinkedIn export shows company/title movement against prior baseline._
- **Sheryar Kayani** - Review as a warm-path or market-adjacency candidate.. _LinkedIn export shows company/title movement against prior baseline._
- **Ami Austin** - Review as a warm-path or market-adjacency candidate.. _LinkedIn export shows company/title movement against prior baseline._

## Recommended actions

- **HIGH - Daran Adair**: Confirm the LinkedIn-visible move and use it as a reconnect pretext. _Why: LinkedIn export shows company/title movement against prior baseline._
- **HIGH - Amy Spytko**: Send a congratulations note; leadership movement is a timing signal. _Why: LinkedIn export shows company/title movement against prior baseline._
- **MEDIUM - Sheryar Kayani**: Light-touch reconnect; ask what changed and what they are building now. _Why: Move intersects restaurant, hospitality, AI, payments, or operator terrain._

## Graph Mutations

- Persistent graph mutated: True
- Baseline entries added: 0
- Baseline entries updated: 326
- Relationship strength mutations: 117
- Strategic importance mutations: 5
- Opportunity graph mutations: 8
- Who Matters Now mutations: 5
- Mutation tag: `linkedin_delta_2026-05-29`
- Updated company/title/source/note/tag fields where LinkedIn created deterministic baseline deltas.
- Did not auto-promote relationship tier or trust score from LinkedIn edge alone.
- Generated Who Matters Now and outreach queues as review-first CoS mutations.

## Daily Brief Mutations

- Cache path: `system/.cache/linkedin_ingest_latest.json`
- Section: LinkedIn Relationship Delta
- Items available for brief: 13
- 5 strategic relationships materially increased in value.
- 3 promotion events detected.
- 8 suggested outreach queue items generated.

## Persistence Verification

- Status: persisted
- Source tag: `linkedin_export_2026-05-29`
- Post-write validation: baseline_json_written_delta_markdown_written_cache_written
- Wrote: `system/_snapshots/baseline_index.pre-linkedin-ingest-2026-05-29-5.json`
- Wrote: `system/baseline_index.json`
- Wrote: `system/deltas/linkedin_export_2026-05-29-5.md`
- Wrote: `system/.cache/linkedin_ingest_latest.json`

## Activity files detected

| File | Rows |
|---|---:|
| messages | 7,267 |
| invitations | 654 |
| comments | 879 |
| shares | 7,851 |
| reactions | 8,683 |

## Guardrails

- Enhanced baseline; did not replace operator-owned fields wholesale.
- Did not promote signal_class from LinkedIn connection alone.
- Did not treat LinkedIn export presence as last_touch.
- Held operator-confirmed conflicts instead of overwriting them.

## Open questions

- Daran Adair: confirm current_company (canonical='Ameri-Can Hospitality Consulting', LinkedIn='Franchise Grade').

*Generated by `system/scripts/linkedin_ingest.py` on 2026-05-29.*
