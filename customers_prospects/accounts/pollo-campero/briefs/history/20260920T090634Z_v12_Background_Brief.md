# Pollo Campero Background Brief

Purpose: Automated 24h SLA refresh — flagged stale on a prior run and not yet addressed.

Current posture: active

## Brand Profile

### Conflicting information requiring reconciliation
- **technology_stack.in_store_acquiring**: Bank of America is the processor for the R016/R138/R173 sample stores. vs. Fiserv is the in-store acquirer (field report). — Both claims may be correct for different entities, stores, channels or roles per the spec's evidence-precedence example. Do not resolve by recency; resolve by mapping acquirer/processor/gateway to entity and store/channel footprint first (see act-pollo-campero-0010).

## Technology Environment

*"Unknown" means reliable evidence was not found. It does not mean the account lacks the system.*

| Layer | Vendor | Status | Confidence | As of |
|---|---|---|---|---|
| DMB / CMS | ZoneIn and Stream among multiple vendors | Active | high | 2026-08-21 |
| POS software | NCR Aloha + Oracle Simphony | Verify | medium | 2026-08-21 |
| POS hardware / workstations | Provider and models not yet confirmed | Open | low | 2026-08-21 |
| Online ordering | Olo | Confirmed | high | 2026-08-21 |
| Above-store digital gateway / payments | Olo Pay using Stripe | Verify | medium | 2026-08-21 |
| In-store acquiring | Fiserv working view; Bank of America appears in sampled statements | Reconcile | medium | 2026-08-21 |
| Payment terminal | Verifone P400 on Simphony side | Verify | medium | 2026-08-21 |
| Back office | Provider and modules not yet confirmed | Open | low | 2026-08-21 |
| AI / analytics | Provider and active use cases not yet confirmed | Open | low | 2026-08-21 |
| Gift cards | Valutec | Verify | medium | 2026-08-21 |
| Loyalty / app | Campero Rewards; platform provider not confirmed | Verify | medium | 2026-08-21 |
| Content operations | Highly manual current process | Active | high | 2026-08-21 |
| Displays / field service | Samsung/LG indoor; LG outdoor; mixed configurations | Active | high | 2026-08-21 |

## Relationship and Access Map
- Todd Vahlsing — account_owner
- Amy McKay — contributor_payments
- Luis Javier Rodas — Managing Director & COO, Campero USA
- Jose Gregorio Baquero — Global CEO, Pollo Campero
- Katherine Celeste Urbina Barillas — Purchasing Specialist, CMI
- Randy Heriberto Mulato Cerrate — Purchasing Specialist, CMI Alimentos
- Diego Haro — Senior Accountant, CMI
- Scott O'Neill — Restaurant Technology Manager, Pollo Campero
- Fernando Perez — Head of Operations (title unverified)
- Karla Patino — VP, U.S. Marketing & Sales
- Jorge de la Parra — title and location unverified, CMI
- Terry Fresquez — title and location unverified, CMI
- Katherine Celeste Urbina Barillas — Purchasing Specialist

## Key Discovery Questions

### Business and Operating Priorities
- What are current leadership's top strategic priorities?
- What operating metrics (comps, traffic, margin) are under the most pressure right now?
- Is there an active turnaround, growth push, or ownership/leadership change underway?
- What is driving any current technology or vendor evaluation, if one exists?

### Loyalty and Digital
- What loyalty platform is currently in use, if any?
- What percentage of transactions come through loyalty or digital channels?
- What are the current app, online-ordering, and delivery platforms?
- Who owns digital engagement and customer data internally?

### Payments
- Who processes in-store and digital payments today?
- Is there a single payments gateway/acquirer, or a fragmented setup across channels?
- When do the current payments agreements renew?
- Are there known pain points (fees, chargebacks, fraud, reliability)?

### Restaurant Technology
- What POS system is currently deployed?
- What hardware is installed, and is there a known refresh or support deadline?
- What systems handle KDS, back office, inventory, and labor management?
- Are there known integration or data-quality issues across systems?

### Decision Process
- Who owns technology strategy and vendor decisions?
- Who owns loyalty, payments, and digital commerce internally?
- Is there an existing Global Payments/Genius relationship or contact?
- What are the next known renewal, budgeting, or vendor-review dates?

## Risks and Cautions
- 3 field(s) have not been reverified within their expected cadence and should be treated with caution.
- 17 field(s) remain unknown — absence of evidence, not evidence of absence.
- 1 open contradiction(s) require reconciliation before external use.

## Research and Internal Intelligence Sources
- Aug 12, 2026 discovery call capture (Just Press Record transcript).
- Full Pollo Campero Blue Sheet as provided by Todd on 2026-08-21; local copy preserved unedited in this folder's sources/.
- Todd's Aug 20 follow-up email (NDA / Valutec asks) - durable Gmail message ID not yet captured.
- Randy's Aug 21 reply confirming approach and statement commitment - durable Gmail message ID not yet captured.
- DMB modernization RFI document - durable Drive/file location not yet captured.
- Nine store-month statement sample - durable file location not yet captured.
- CMI/Pollo Campero public releases (store count, leadership, growth goals) - specific URLs not yet captured.
- LinkedIn profile research for Rodas, Perez, O'Neill - specific profile URLs not yet captured.
- Prior comprehensive account brief; background context, not yet re-cited line-by-line into evidence.jsonl.
- Original Genius lead brief; background context, not yet re-cited line-by-line into evidence.jsonl.
- Global Payments/Genius submitted RFI response dated 2026-08-21.
- Pollo Campero TDR V3 received 2026-08-26.
- Pollo Campero SOW and pricing workbook received 2026-08-26.
- Katherine Urbina RFP transmittal establishing revised-response request and 2026-09-04 deadline.
- Internal account research, updated 2026-08-10
- Internal account research, updated 2026-07-27