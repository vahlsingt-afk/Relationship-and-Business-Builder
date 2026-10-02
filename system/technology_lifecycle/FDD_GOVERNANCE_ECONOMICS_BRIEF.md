# RBB FDD Technology Governance & Economics — Implementation Brief

**Status: received 2026-10-01, not yet implemented.** This is a larger,
separate program from the Technology Lifecycle Phase 0 work in this same
folder — recorded here verbatim (lightly reformatted) so it isn't lost,
and so a future session has the full brief plus a compatibility note
without re-deriving it. Do not start building against this until a
dedicated session scopes it properly — it's a significant architecture
commitment (entity resolution, a new FDD source-record type, economics
observations, historical-change detection, and a real importer), not a
quick add.

## Compatibility with what already exists (as of 2026-10-01)

Good news: `system/technology_lifecycle/technology_governance.jsonl`
(built for the Technology Lifecycle schema, see `system/SCHEMAS.md` →
"Technology Lifecycle & Change Events" → `technology_governance.jsonl`)
already models most of what this brief's §2–4 ask for — `governance_state`
enum, `franchisor_change_authority` (with `may_require_change_during_term`
and `expense_borne_by`, matching this brief's "authority ≠ requirement ≠
deployment" principle), and `fdd_sourced_fields` (fdd_year, items
reviewed, required technology/hardware, fees, replacement obligations,
payment/loyalty/online-ordering requirements, differs-by-store-age). This
brief's additions on top of that foundation, not yet built:

- A dedicated **FDD source record** (§2) — one row per FDD document
  reviewed (not per governance fact), with `document_status` lifecycle
  (current/historical/amended/superseded/incomplete/secondary_copy/
  not_located) and prior/superseding FDD linkage. `technology_governance.
  jsonl` currently has no equivalent — governance facts point at evidence
  inline, not at a normalized document registry.
- **Technology-economics observations** (§6) as their own structured
  record (fees, costs, subsidies, caps) — not yet modeled; would need a
  new file, e.g. `technology_economics.jsonl`, sibling to the existing
  five `.jsonl` stores.
- **Historical governance-change detection** (§8) generating Technology
  Lifecycle events automatically (`vendor_removed_from_approved_list`,
  `optional_to_mandated`, etc.) — the lifecycle_state enum in `technology_
  relationship_events.jsonl` doesn't yet include these governance-specific
  transition types; would need either new lifecycle_state values or a
  parallel governance-transition event type.
- **Penetration reconciliation** (§9) as an explicit record type
  (standard_vendor / known_competing_installed_base / grandfathering_
  possible / migration_in_progress / penetration_unknown) distinct from a
  raw `technology_penetration.jsonl` observation — not yet modeled.
- **Permanent brand intelligence fields** (§12 — franchisor legal entity,
  franchise term, remodel cadence, transfer provisions, etc.) — these
  belong on brand-level records (`system/brand_profiles/<slug>.json` per
  `system/DATA_TIER_ARCHITECTURE.md`'s Tier 1), not in this technology-
  specific layer; would need `brand_profile_common.py` schema additions,
  out of this folder's scope.
- **The importer** (§21) — nothing in this repo yet ingests FDD packets.
  Would follow the same pattern as the planned (not yet built, see
  `README.md`) technology-lifecycle importer: schema-versioned JSON
  sidecar in `system/inbox/chatgpt_intelligence_drop/`, resolve-against-
  existing-entities-only, idempotent, receipt-producing.
- The brief's own closing ask — **a sample JSON contract plus one
  simulated brand import** before the real >90-location batches begin —
  has not been produced. That should be the first concrete deliverable
  when this program is actually scoped, specifically so a schema problem
  is caught on one record, not after hundreds.

## `visibility_class` applies here too

The amended §19 (public/private boundary — see `system/SCHEMAS.md` →
"Technology Lifecycle & Change Events" → `visibility_class`) governs this
program identically: FDDs are public records, so verified FDD-sourced
governance/economics observations default to `visibility_class:
"public_shared"`. Any private/firsthand knowledge used only to decide
*which brands or questions to prioritize* stays `private_user` and is
never itself written into a shared record — only independently
public-sourced confirmation is.

## The brief, as received (2026-10-01)

### Objective

Prepare RBB to ingest, normalize, reconcile, store and expose the
upcoming restaurant FDD Technology Governance & Economics research. This
research will cover every canonical RBB restaurant brand with more than
90 locations, processed in batches of 25. This must NOT become a
standalone FDD database or isolated research artifact — the information
belongs in RBB's shared intelligence model and must ultimately be usable
from: account intelligence, brand profiles, technology lifecycle/change-
event intelligence, vendor/competitor intelligence, franchisee/operator
intelligence, opportunity intelligence, Chief-of-Staff analysis, Team
Portal, future account-management and penetration reporting, and future
propensity/buying-window analysis. Principle: ingest once, normalize once,
expose everywhere appropriate.

### 1. Canonical Entity Resolution

All incoming research must resolve against existing RBB entities before
creating anything new. Primary entities: restaurant brand, parent company,
franchisor/legal entity, franchisee/operator, technology vendor,
technology product, technology category. Research packets should preserve
RBB canonical IDs when supplied. If an entity cannot be confidently
resolved, create an entity-resolution review item rather than silently
creating a duplicate. Examples: RTI/RTIconnect → Genius Back Office,
Xenial POS → Genius POS, SICOM DMB → Genius DMB, Como → Genius Loyalty,
SICOM hardware → Genius Hardware. Preserve historical/source product
names for provenance while using RBB canonical normalization.

### 2. FDD Source Record

Create a reusable source-level record for each FDD reviewed: `fdd_id`,
`brand_id`, `franchisor_entity_id`, `fdd_year`, `effective_date`,
`amendment_date`, `source_url`, `source_title`, `accessed_date`,
`document_status`, `document_confidence`, `prior_fdd_id`,
`superseded_by_fdd_id`, `notes`. `document_status` ∈ `current | historical
| amended | superseded | incomplete | secondary_copy | not_located`. Do
not treat the newest document found as current without checking its
effective date where possible.

### 3. Technology Governance Record

Technology governance must exist at the Brand × Technology Category ×
Date level: `brand_id`, `technology_category`, `effective_from`,
`effective_to`, `contractual_authority`, `current_requirement`,
`governance_status`, `named_vendor_id`, `named_product_id`,
`approved_alternatives`, `new_store_requirement`,
`existing_store_requirement`, `grandfathering_status`,
`conversion_deadline`, `franchisee_choice`, `geographic_scope`,
`operator_scope`, `evidence_type`, `confidence`, `source_id`,
`source_item`, `source_page`, `source_excerpt_or_summary`,
`last_verified_date`. Governance states: `mandated |
mandated_category_brand_selected_vendor | approved_vendor_list |
preferred_not_required | franchisee_choice_with_requirements |
grandfathered | new_store_mandate | pilot_optional | corporate_only |
unknown`. Do not force one governance status across an entire technology
stack — a brand may simultaneously mandate POS and loyalty while allowing
franchisees to select among several approved back-office systems.

### 4. Separate Authority, Requirement and Deployment

Three different facts, never collapsed: **contractual authority** (what
the franchise agreement/FDD allows the franchisor to require — e.g.
"franchisor may designate required technology systems"), **current
requirement** (what current evidence says franchisees are actually
required to use — e.g. "franchisees must use Vendor A POS"), and **actual
deployment** (what's actually installed and operational — e.g. "Vendor A
verified live at 1,200 of ~1,800 eligible restaurants"). Therefore
authority ≠ requirement ≠ deployment; each needs its own evidence and
confidence.

### 5. Technology Categories

At minimum: POS software, POS hardware, payments processor, payments
gateway, payment devices, payment orchestration/tokenization, back
office, accounting, inventory, labor/scheduling, workforce management,
loyalty, mobile application, online ordering, delivery, order
aggregation, KDS, digital menu boards, drive-thru technology, networking,
security, kiosks, AI/automation, other restaurant technology. Do not
infer that adoption of one vendor's module means adoption of other
modules from that vendor.

### 6. Technology Economics

Normalized technology-economic observations associated with the
brand/FDD/category: `initial_technology_investment`, `hardware_cost`,
`software_fee`, `recurring_technology_fee`, `digital_fee`, `loyalty_fee`,
`online_ordering_fee`, `payment_related_fee`, `support_fee`,
`maintenance_fee`, `upgrade_cost`, `replacement_cost`, `franchisee_pays`,
`franchisor_subsidy`, `vendor_subsidy`, `supplier_rebate_or_commission`,
`future_spending_cap`, `no_cap_disclosed`, `payment_frequency`,
`cost_range_low`, `cost_range_high`, `cost_unit`, `notes`. Preserve
disclosed ranges rather than inventing point estimates.

### 7. Franchisor Change Authority

Explicit field `franchisor_change_authority` capturing whether the
franchisor may: change required systems, change approved vendors, require
upgrades, require replacement hardware, mandate future technologies,
require additional systems, require compliance during the existing
franchise term. Also, where disclosed: `franchisee_bears_change_cost`,
`spending_limit`, `no_spending_limit`. Potentially important buying-window
and migration intelligence.

### 8. Historical FDD Comparison

Where multiple FDD years are available, do not overwrite previous
governance — create dated changes (e.g. 2024: back office approved
A/B/C; 2025: approved A/B; 2026: B mandated). This should create
Technology Lifecycle events: `vendor_removed_from_approved_list`,
`vendor_added_to_approved_list`, `optional_to_mandated`,
`approved_to_exclusive`, `new_store_mandate_created`,
`grandfathering_created`, `grandfathering_ended`,
`conversion_deadline_created`, `technology_fee_increased`,
`technology_fee_decreased`, `franchisor_authority_changed`. FDD changes
should feed the existing Technology Lifecycle model rather than live in a
separate historical table with no relationship to lifecycle intelligence.

### 9. Penetration Reconciliation

Governance and penetration must remain separate but linked. Example: FDD
says PAR/Data Central is the required future back-office standard;
existing intelligence says R365 is at ~400 GPS Hospitality Burger Kings;
existing intelligence says RTI/Genius Back Office is a historical
installed footprint. These can all be simultaneously true. Do not
overwrite installed-base observations because an FDD identifies a new
mandate — instead create reconciliation intelligence: `standard_vendor`,
`known_competing_installed_base`, `grandfathering_possible`,
`migration_in_progress`, `penetration_unknown`,
`conversion_deadline_unknown`, and generate appropriate research gaps.

### 10. Penetration Time Series

Brand × Category × Vendor × Product × Date: `total_system_locations`,
`eligible_locations`, `contracted_locations`, `mandated_locations`,
`committed_locations`, `pilot_locations`, `installed_locations`,
`live_locations`, `verified_locations`, `estimated_locations_low`,
`estimated_locations_high`, `penetration_pct_low`, `penetration_pct_high`,
`remaining_opportunity`, `rollout_velocity`, `last_verified_date`,
`confidence`, `evidence_type`. Never infer 100% penetration from an FDD
mandate.

### 11. Franchisee/Operator Intelligence

FDD research frequently exposes operator-level implications. Technology
relationships should attach to Parent → Brand → Franchisee/Operator →
Location (e.g. GPS Hospitality → Burger King → Back Office →
Restaurant365 → ~400 locations). This observation should appear in GPS
Hospitality intelligence, Burger King intelligence, Restaurant365
competitive intelligence, and Burger King back-office penetration
analysis — without creating four separate copies of the underlying fact.
Use shared relationships and references.

### 12. Permanent Brand Intelligence

While processing FDDs, capture a bounded set of additional facts:
`franchisor_legal_entity`, `parent_company`, `ownership_type`,
`franchise_term`, `renewal_structure`, `remodel_requirement`,
`remodel_cadence`, `advertising_fund_requirement`, `required_suppliers`,
`company_location_count`, `franchised_location_count`,
`transfer_provisions`, `change_of_control_provisions`. These should
populate canonical account/brand intelligence, not remain embedded only
in the FDD packet.

### 13. Account Intelligence Integration

Every relevant brand account should be able to surface an automatically
generated technology-governance section (POS mandate, payments approval
model, back-office approved vendors + known penetration, loyalty/online-
ordering mandates, technology change authority) generated from shared
structured intelligence, not manually copied into account files.

### 14. Team Portal

Should eventually support queries like: what's mandated at Brand X; which
POS/back-office vendors are required/approved; does the franchisee have
processor choice; what fees does Brand X charge; which brands mandate
Vendor X; which brands changed POS requirements in the last 3 years;
where is Vendor X mandated but not fully penetrated; which brands give
franchisees payment flexibility; which brands have grandfathered legacy
systems; what migrations appear underway. Filterable by category, vendor,
governance status, mandate status, FDD year, penetration, franchise
model, parent company, confidence, last-verified date.

### 15. Competitive Intelligence Integration

Vendor intelligence should automatically benefit from FDD observations
(mandated-relationship count, approved-vendor-relationship count, known
franchisee-selected deployments, known displacement events, brands where
approval was lost, known penetration observations). Do not treat "approved
vendor" as equivalent to "customer." Do not treat "mandated vendor" as
equivalent to "fully deployed."

### 16. Opportunity Intelligence

FDD/governance information should eventually inform sales opportunity
analysis — e.g. low practical displacement opportunity (competitor
mandated, 95% verified penetration, contract recently renewed, franchisees
bear conversion expense) vs. potential opportunity (competitor preferred
not mandated, multiple approved vendors, low incumbent penetration,
franchisee choice permitted) vs. future buying window (current vendor
mandated but franchisor has change authority, hardware approaching EOL,
contract expiration approaching, fee increasing, new CTO, FDD language
changed recently). These remain analytical signals, not unsupported
predictions.

### 17. Research Gaps

Missing information should generate structured research gaps rather than
disappear: `current_vendor_unknown`, `governance_unknown`,
`approved_vendor_list_incomplete`, `penetration_unknown`,
`grandfathering_unknown`, `conversion_deadline_unknown`,
`payment_flexibility_unknown`, `current_FDD_not_located`,
`historical_FDD_missing`, `conflicting_vendor_evidence`. These can feed
future Deep Research cycles.

### 18. Evidence and Provenance

Every factual observation must preserve: `source_url`, `source_title`,
`publisher`, `publication_date`, `FDD_effective_date`, `accessed_date`,
`page`, `item`, `evidence_type`, `confidence`,
`source_excerpt_or_summary`. Evidence types: `operator_statement |
independent_evidence | vendor_claim | rbb_inference |
firsthand_rbb_intelligence | unknown`. Do not silently upgrade evidence.
Conflicting evidence should coexist until resolved.

### 19. No Data Silos — With a Hard Public/Private Boundary (amended)

Two simultaneous principles: (1) public intelligence should not be
siloed; (2) user/private intelligence must remain siloed from Team Portal
and shared-team access. The FDD source itself is public, so its verified
observations default to `visibility_class: "public_shared"` and should
flow into: brand/account intelligence, technology governance, technology
lifecycle/change events, technology penetration, franchisee/operator
intelligence, vendor/competitive intelligence, opportunity context, and
Team Portal search/reporting. Private/user information (notes,
conversations, emails, CRM entries not meant for sharing, personal
assessments, confidential documents, non-public competitive intelligence)
must not automatically propagate into the shared layer. Every material
observation should carry `visibility_class` ∈ `public_shared |
private_user | private_organization | restricted | unknown`; default to
`unknown` when undetermined, and do not expose `unknown` through Team
Portal until resolved. A private observation later independently
corroborated by public evidence gets a **separate** `public_shared`
record citing the public source — the private record and its provenance
are never converted or exposed. Derived intelligence inherits the most
restrictive visibility of what it materially depends on; public-only
inputs may produce `public_shared` derived intelligence. The Team Portal
query layer must enforce this boundary at the data-access level, not rely
on the UI to hide fields after retrieval. Account intelligence is
assembled as permission-aware views: a Shared Account View (public
records only, what Team Portal receives) and a Private User Account View
(may additionally include personal relationships, private meetings,
internal notes, confidential intelligence — what the individual user's
own RBB experience receives). No data silos for public intelligence; hard
silos for private intelligence; the same entity graph may connect both,
but evidence records, visibility, and access controls stay distinct.

### 20. Incoming Research Program

Once the receiving architecture is ready, ChatGPT Deep Research will
process every canonical RBB restaurant brand with >90 system locations.
RBB should provide the canonical population and IDs. Research runs in
fixed batches of 25. Each batch includes: current FDD research, historical
FDD comparison where available, technology governance by category,
technology economics, named technology vendors/products, franchisee
choice/mandate analysis, change authority, grandfathering, permanent
brand intelligence, reconciliation against known technology relationships,
penetration questions, conflicts, research gaps, evidence/confidence/
provenance. Each batch produces a Markdown evidence packet and JSON
sidecar for ingestion.

### 21. Importer Requirement

Before bulk research begins, implement or extend the RBB intelligence
importer so these packets can be processed without losing relationships
or provenance. The importer should: (1) resolve canonical entities, (2)
preserve FDD source records, (3) create/update governance observations,
(4) create technology-economic observations, (5) detect historical
governance changes, (6) generate lifecycle events, (7) reconcile rather
than overwrite penetration data, (8) attach operator-level intelligence
appropriately, (9) update account/brand intelligence, (10) update vendor
intelligence, (11) generate unresolved research gaps, (12) preserve
confidence and evidence type, (13) make resulting intelligence queryable
by Team Portal, (14) produce an import receipt showing what was created,
updated, conflicted, or queued for review. No silent overwrites. No
duplicate entities. No unsupported inference. No FDD data silo.

### Core Architectural Principle

The FDD is a source, not a destination. The research packet is a
transport mechanism, not a database. A fact such as "Brand X requires
Vendor Y POS for all new franchise locations beginning in 2026" should be
stored once with provenance and then be discoverable from: Brand X →
technology governance, Vendor Y → customer/mandate intelligence,
Technology Lifecycle → mandate/change event, Opportunity Intelligence →
incumbent/governance context, Team Portal → user query.

### Operational addition

Before launching hundreds of FDD records: produce a sample JSON contract
plus one simulated brand import, to catch schema problems on one record
rather than repairing 500 later.
