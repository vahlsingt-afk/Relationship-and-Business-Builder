# LinkedIn Relationship Intelligence Delta - 2026-05-29

Source file: `Basic_LinkedInDataExport_05-09-2026.zip.zip`. Artifact auto-classified as LinkedIn export ZIP and processed as a longitudinal relationship-intelligence mutation event.

## Baseline Comparison

| Metric | Count |
|---|---:|
| Prior baseline records | 2,715 |
| Current baseline records | 2,715 |
| Connections in export | 2,681 |
| Matched existing relationships | 2,607 |
| Net-new relationships | 0 |
| Lost relationships | 41 |
| Reconnections | 5 |
| Conflicts held | 1 |

## Delta Metrics

| Signal | Count |
|---|---:|
| Title changes | 98 |
| Company changes | 55 |
| Promotions | 2 |
| Recruiter additions | 0 |
| Executive additions | 0 |
| Enterprise buyer additions | 0 |
| Restaurant-tech adjacency expansion | 0 |
| Strategic industry cluster changes | 4 |

## Strategic Relationship Changes

4 strategic relationships materially increased in value.

### Who Matters Now
- **Daran Adair**: Ameri-Can Hospitality Consulting / Lead Advisor — LinkedIn export shows company/title movement against prior baseline.
- **Spencer Amadon**: VenueByte / Business Owner — LinkedIn export shows company/title movement against prior baseline.
- **Allie Harrison**: First Light Clothing / Founder — LinkedIn export shows company/title movement against prior baseline.
- **Jason Riggs**: Audivi AI / Chief Commercial and Product Officer — LinkedIn export shows company/title movement against prior baseline.

## Professional Change Detection

### RC moves
- **Daran Adair** (company_change): Ameri-Can Hospitality Consulting / Lead Advisor -> Ameri-Can Hospitality Consulting / Lead Advisor. LinkedIn export shows company/title movement against prior baseline.

### LKI moves
- **Žana Devine** (company_and_role_change): Žana Devine Hospitality / Founder & Executive Hospitality Advisor -> R&R Resilience and Rebirth / Podcaster.
- **Patty Dominguez** (title_change): More Leverage Solutions / Founder & AI Authority Advisor | Creator of The Chosen Brand™ System -> More Leverage Solutions / Founder & Brand Authority Advisor | Creator of The Chosen Brand™ System.
- **Toby Malbec** (company_and_role_change): Qu / Vice President of Partnerships & Channel -> Coming Soon / Looking for What's Next.
- **Oliver Ostertag** (title_change): PAR Technology / President, Growth + AI -> PAR Technology / GM, Operator Cloud.
- **Spencer Amadon** (promotion): Here Here Market / Business Development Consultant -> VenueByte / Business Owner.
- **Allie Harrison** (promotion): Singulate / Founding BDR -> First Light Clothing / Founder.
- **Dan Ewing** (company_and_role_change): Dan The Airport Man / Business Owner -> HopSkipDrive / Independent Driver.
- **Brett R. Smith** (company_and_role_change): Smith Creek Co. / Founder & Owner -> City of Annapolis, MD / Assistant Harbormaster.
- **Jason Riggs** (title_change): Audivi AI / Chief Commercial and Product Officer (CCPO) -> Audivi AI / Chief Commercial and Product Officer.
- **John Herman** (title_change): Johnson Controls Security Products / Strategic Sales Manager - Northeast & Mid-Atlantic Regions -> Johnson Controls Security Products / Strategic Sales Manager - Northeast Region.

## Opportunity detection

- **Spencer Amadon** - Send a concise congratulations note.. _LinkedIn export shows Business Development Consultant -> Business Owner._
- **Allie Harrison** - Send a concise congratulations note.. _LinkedIn export shows Founding BDR -> Founder._
- **Daran Adair** - Review as a warm-path or market-adjacency candidate.. _LinkedIn export shows company/title movement against prior baseline._
- **Spencer Amadon** - Review as a warm-path or market-adjacency candidate.. _LinkedIn export shows company/title movement against prior baseline._
- **Allie Harrison** - Review as a warm-path or market-adjacency candidate.. _LinkedIn export shows company/title movement against prior baseline._
- **Jason Riggs** - Review as a warm-path or market-adjacency candidate.. _LinkedIn export shows company/title movement against prior baseline._

## Recommended actions

- **HIGH - Daran Adair**: Confirm the LinkedIn-visible move and use it as a reconnect pretext. _Why: LinkedIn export shows company/title movement against prior baseline._

## Graph Mutations

- Persistent graph mutated: True
- Baseline entries added: 0
- Baseline entries updated: 199
- Relationship strength mutations: 46
- Strategic importance mutations: 4
- Opportunity graph mutations: 6
- Who Matters Now mutations: 4
- Mutation tag: `linkedin_delta_2026-05-29`
- Updated company/title/source/note/tag fields where LinkedIn created deterministic baseline deltas.
- Did not auto-promote relationship tier or trust score from LinkedIn edge alone.
- Generated Who Matters Now and outreach queues as review-first CoS mutations.

## Daily Brief Mutations

- Cache path: `system/.cache/linkedin_ingest_latest.json`
- Section: LinkedIn Relationship Delta
- Items available for brief: 10
- 4 strategic relationships materially increased in value.
- 2 promotion events detected.
- 6 suggested outreach queue items generated.

## Persistence Verification

- Status: persisted
- Source tag: `linkedin_export_2026-05-29`
- Post-write validation: baseline_json_written_delta_markdown_written_cache_written
- Wrote: `system/_snapshots/baseline_index.pre-linkedin-ingest-2026-05-29-3.json`
- Wrote: `system/baseline_index.json`
- Wrote: `system/deltas/linkedin_export_2026-05-29-3.md`
- Wrote: `system/.cache/linkedin_ingest_latest.json`

## Activity files detected

| File | Rows |
|---|---:|
| messages | 21,038 |
| invitations | 505 |
| comments | 0 |
| shares | 0 |
| reactions | 0 |

## Guardrails

- Enhanced baseline; did not replace operator-owned fields wholesale.
- Did not promote signal_class from LinkedIn connection alone.
- Did not treat LinkedIn export presence as last_touch.
- Held operator-confirmed conflicts instead of overwriting them.

## Open questions

- Daran Adair: confirm current_company (canonical='Ameri-Can Hospitality Consulting', LinkedIn='Franchise Grade').

*Generated by `system/scripts/linkedin_ingest.py` on 2026-05-29.*
