# RB restaurant tech-stack research segment 01

**Run:** 2026-09-02T16:11:00Z  
**Scope:** bounded revalidation of an active Tier-1 account plus one vendor/product battle-card update.  
**Disposition:** review-only. No canonical tech-stack value was changed.

## Phase 1 — Five Guys / Olo Engage revalidation

| Field | Review-ready evidence |
|---|---|
| Brand / priority | Five Guys — active account; canonical portfolio material describes it as Tier 1. |
| Vendor / product / category | Olo — Engage suite: Guest Data Platform (GDP) and Marketing; guest-data / marketing automation. |
| Claim | Olo says Five Guys, its first enterprise online-ordering customer in 2009, expanded the relationship to deploy GDP and Marketing. Olo's named-customer case study says a 150-restaurant initial group was followed by rollout to all 1,500 locations. |
| Source URL | https://investors.olo.com/news/news-details/2024/Olo-Announces-Fourth-Quarter-and-Full-Year-2023-Financial-Results/ ; https://www.olo.com/case-studies/five-guys-engage |
| Source type / date | Olo investor press release, 2024-02-29; Olo named-customer case study, live page checked 2026-09-02 (the page does not expose a publication date). |
| Evidence text | The investor release explicitly names Five Guys and says it deployed Engage GDP and Marketing. The case study attributes a $2M revenue lift during the first six months and describes an enterprise rollout after a 150-location initial group. |
| Confidence | 93% — direct vendor/investor evidence, named customer and named products; vendor has commercial incentive. |
| Limitations | The case study's **1,500 locations** is a stated rollout scope, not an independently verified current live count. It supports GDP/Marketing, not a standalone claim that Five Guys currently uses every Olo product or Olo Pay. |
| Field-ready? | **Yes**, for: “Olo publicly identifies Five Guys as a customer of Engage GDP and Marketing, with an enterprise rollout described in its case study.” Use no current-location count without reconfirming it. |

### Proposed canonical review queue item (do not auto-promote)

* Match existing relationship: `rel-brand-five-guys-online-ordering-unknown-reference-only-vendor-olo`.
* Proposed normalization: retain existing relationship history, but add a separate `guest_data_marketing` relationship/product assertion for **Olo Engage GDP + Marketing**. Do **not** relabel this specific evidence as core online ordering and do not imply Olo Pay.
* Suggested posture: `active` / `brand_wide_deployment`; confidence `0.93`; review after `2027-02-28`.

## Phase 2 — Olo battle-card proposal versus Global Payments / Genius

| Field | Review-ready evidence |
|---|---|
| Vendor / category | Olo — digital ordering, delivery orchestration, guest data and marketing, payments. |
| Product scope | Olo publicly lists Order, Pay and Engage suites. Order includes online ordering, Serve front end, Dispatch, Rails, Catering+, loyalty, Order with Google and Switchboard; Engage includes GDP, marketing and sentiment. |
| Features / integrations | Olo says its platform is open and modular with 400+ integration partners. Its Summer 2024 release documents Ordering API, Dispatch API, store-hours API and POS synchronization for Rails tips. |
| Customer proof | Five Guys case study: GDP + Marketing rollout after a 150-location group, plus named operating results. |
| Strength | Broad restaurant-specific digital/guest-data layer, marketplace and POS integration ecosystem, and a strong claimed case-study narrative for measurable marketing use cases. |
| Likely vulnerability | Olo’s proof is vendor-authored and its multi-product catalog creates potential overlap with a merchant's existing POS, payments, loyalty, CRM and ordering tools. Validate active modules, contract ownership, data portability and incremental cost before positioning a replacement. |
| Genius value wedge | Global Payments’ current Genius restaurant/enterprise materials position a unified cloud POS/payments suite spanning POS, kitchen management, back office, drive-thru, kiosk and digital signage; the restaurant page claims 75+ native integrations plus an open API. Position Genius around consolidated operational and payment workflows, then prove that it can preserve or connect the customer’s chosen digital/guest layer. Avoid claiming feature parity with Olo’s GDP/marketing without product confirmation. |
| Source URL | https://www.olo.com/ ; https://www.olo.com/engage ; https://www.olo.com/quarterly-release/summer-2024 ; https://www.globalpayments.com/industries/restaurant ; https://investors.globalpayments.com/news-events/press-releases/detail/486/global-payments-announces-the-launch-of-its-genius-for |
| Source type / evidence date | Vendor product/solution pages checked 2026-09-02; Olo release documentation (Summer 2024); Global Payments enterprise launch (2025-09-10). |
| Confidence / field-ready | 88% scope/features; 82% comparative wedge. **Field-ready only** for source-linked product-scope statements. Comparative positioning remains a discovery hypothesis, not a factual customer-stack assertion. |

## Unresolved gaps and next pointer

1. Confirm Five Guys’ current Olo contract modules, geography and live footprint through account intelligence or a current first-party source; do not infer Olo Pay, ordering or POS use from this evidence.
2. Next active-account brand pointer: **Del Taco** — re-check the existing “aging NCR estate” intelligence against public/current evidence and separately validate whether Presto Voice remains deployed after the 2023 expansion announcement.
3. Next vendor pointer: **PAR / Brink** — capture a current first-party product/API source and an enterprise named-customer case study, then compare its POS/operations scope against Genius without treating franchisee deployments as brand-wide.
