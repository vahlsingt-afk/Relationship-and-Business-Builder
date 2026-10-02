# LinkedIn Relationship Intelligence Delta - 2026-05-29

Source file: `Complete_LinkedInDataExport_05-28-2026.zip.zip`. Artifact auto-classified as LinkedIn export ZIP and processed as a longitudinal relationship-intelligence mutation event.

## Baseline Comparison

| Metric | Count |
|---|---:|
| Prior baseline records | 2,696 |
| Current baseline records | 2,715 |
| Connections in export | 2,719 |
| Matched existing relationships | 2,624 |
| Net-new relationships | 19 |
| Lost relationships | 4 |
| Reconnections | 0 |
| Conflicts held | 1 |

## Delta Metrics

| Signal | Count |
|---|---:|
| Title changes | 45 |
| Company changes | 27 |
| Promotions | 2 |
| Recruiter additions | 0 |
| Executive additions | 6 |
| Enterprise buyer additions | 2 |
| Restaurant-tech adjacency expansion | 3 |
| Strategic industry cluster changes | 3 |

## Strategic Relationship Changes

3 strategic relationships materially increased in value.

### Who Matters Now
- **Daran Adair**: Ameri-Can Hospitality Consulting / Lead Advisor — LinkedIn export shows company/title movement against prior baseline.
- **Oliver Ostertag**: PAR Technology / President, Growth + AI — LinkedIn export shows company/title movement against prior baseline.
- **Brett R. Smith**: Smith Creek Co. / Founder & Owner — LinkedIn export shows company/title movement against prior baseline.

### Newly relevant contacts
- **Michael Zammar**: OSP Inspectors Inc / Vice President — New relationship has recruiter, executive, enterprise-buyer, or restaurant-tech adjacency.
- **Elizabeth Jenswold**: Bridgepoint Consulting, LLC. / President — New relationship has recruiter, executive, enterprise-buyer, or restaurant-tech adjacency.
- **Bakhat Ali**: Really Beyond / Chief Marketing Officer — New relationship has recruiter, executive, enterprise-buyer, or restaurant-tech adjacency.
- **Sriram Subramanian**: LinkToAny / CEO | Sales, Product and Partnerships — New relationship has recruiter, executive, enterprise-buyer, or restaurant-tech adjacency.
- **Piyush Jain**: Kubbly / Co-Founder, CEO — New relationship has recruiter, executive, enterprise-buyer, or restaurant-tech adjacency.
- **Matt Schalsey**: PerfectHire / Chief Executive Officer & Co-Founder — New relationship has recruiter, executive, enterprise-buyer, or restaurant-tech adjacency.
- **Darius Green**: Mad Room Hospitality / Director Of Operations — New relationship has recruiter, executive, enterprise-buyer, or restaurant-tech adjacency.
- **Sriram Subramanian**: LinkToAny / CEO | Sales, Product and Partnerships — New relationship has recruiter, executive, enterprise-buyer, or restaurant-tech adjacency.
- **Darius Green**: Mad Room Hospitality / Director Of Operations — New relationship has recruiter, executive, enterprise-buyer, or restaurant-tech adjacency.
- **Ryan Hildebrand**: Global Payments Inc. / Senior Director Account Executives — New relationship has recruiter, executive, enterprise-buyer, or restaurant-tech adjacency.

## Professional Change Detection

### RC moves
- **Daran Adair** (company_change): Ameri-Can Hospitality Consulting / Lead Advisor -> Ameri-Can Hospitality Consulting / Lead Advisor. LinkedIn export shows company/title movement against prior baseline.

### LKI moves
- **Oliver Ostertag** (promotion): PAR Technology / GM, Operator Cloud -> PAR Technology / President, Growth + AI.
- **Spencer Amadon** (company_and_role_change): VenueByte / Business Owner -> Here Here Market / Business Development Consultant.
- **Brett R. Smith** (promotion): City of Annapolis, MD / Assistant Harbormaster -> Smith Creek Co. / Founder & Owner.
- **John Herman** (title_change): Johnson Controls Security Products / Strategic Sales Manager - Northeast Region -> Johnson Controls Security Products / Strategic Sales Manager - Northeast & Mid-Atlantic Regions.

## Opportunity detection

- **Oliver Ostertag** - Send a concise congratulations note.. _LinkedIn export shows GM, Operator Cloud -> President, Growth + AI._
- **Brett R. Smith** - Send a concise congratulations note.. _LinkedIn export shows Assistant Harbormaster -> Founder & Owner._
- **Daran Adair** - Review as a warm-path or market-adjacency candidate.. _LinkedIn export shows company/title movement against prior baseline._
- **Oliver Ostertag** - Review as a warm-path or market-adjacency candidate.. _LinkedIn export shows company/title movement against prior baseline._
- **Brett R. Smith** - Review as a warm-path or market-adjacency candidate.. _LinkedIn export shows company/title movement against prior baseline._

## Recommended actions

- **HIGH - Daran Adair**: Confirm the LinkedIn-visible move and use it as a reconnect pretext. _Why: LinkedIn export shows company/title movement against prior baseline._
- **MEDIUM - Oliver Ostertag**: Light-touch reconnect; ask what changed and what they are building now. _Why: Move intersects restaurant, hospitality, AI, payments, or operator terrain._
- **MEDIUM - Michael Zammar**: Review as a warm-entry candidate before the connection goes cold. _Why: Leadership or restaurant/hospitality/AI-adjacent signal in new connection._
- **MEDIUM - Elizabeth Jenswold**: Review as a warm-entry candidate before the connection goes cold. _Why: Leadership or restaurant/hospitality/AI-adjacent signal in new connection._
- **MEDIUM - Darius Green**: Review as a warm-entry candidate before the connection goes cold. _Why: Leadership or restaurant/hospitality/AI-adjacent signal in new connection._
- **MEDIUM - Bakhat Ali**: Review as a warm-entry candidate before the connection goes cold. _Why: Leadership or restaurant/hospitality/AI-adjacent signal in new connection._
- **MEDIUM - Sriram Subramanian**: Review as a warm-entry candidate before the connection goes cold. _Why: Leadership or restaurant/hospitality/AI-adjacent signal in new connection._
- **MEDIUM - Piyush Jain**: Review as a warm-entry candidate before the connection goes cold. _Why: Leadership or restaurant/hospitality/AI-adjacent signal in new connection._

## Graph Mutations

- Persistent graph mutated: True
- Baseline entries added: 19
- Baseline entries updated: 76
- Relationship strength mutations: 4
- Strategic importance mutations: 3
- Opportunity graph mutations: 5
- Who Matters Now mutations: 3
- Mutation tag: `linkedin_delta_2026-05-29`
- Updated company/title/source/note/tag fields where LinkedIn created deterministic baseline deltas.
- Did not auto-promote relationship tier or trust score from LinkedIn edge alone.
- Generated Who Matters Now and outreach queues as review-first CoS mutations.

## Daily Brief Mutations

- Cache path: `system/.cache/linkedin_ingest_latest.json`
- Section: LinkedIn Relationship Delta
- Items available for brief: 8
- 3 strategic relationships materially increased in value.
- 2 promotion events detected.
- 5 suggested outreach queue items generated.

## Persistence Verification

- Status: persisted
- Source tag: `linkedin_export_2026-05-29`
- Post-write validation: baseline_json_written_delta_markdown_written_cache_written
- Wrote: `system/_snapshots/baseline_index.pre-linkedin-ingest-2026-05-29-2.json`
- Wrote: `system/baseline_index.json`
- Wrote: `system/deltas/linkedin_export_2026-05-29-2.md`
- Wrote: `system/.cache/linkedin_ingest_latest.json`

## Activity files detected

| File | Rows |
|---|---:|
| messages | 21,644 |
| invitations | 483 |
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
